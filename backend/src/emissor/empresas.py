"""Várias empresas (emitentes) na mesma instalação.

Cada empresa é uma linha de ``configuracao`` (a empresa 1 é a configuração de antes): CNPJ e dados fiscais,
ambiente, série, numeração da DPS e o certificado usado para falar com a Sefin. O certificado pode ser o A1 da
própria empresa ou o de um procurador (escritório de contabilidade) com procuração eletrônica para emitir por ela.

**Isolamento:** abas, linhas, tributações, lotes, emissões, eventos e clientes têm ``empresa_id``. Toda consulta
do ORM a essas tabelas recebe o filtro da empresa do contexto (``with_loader_criteria`` no ``do_orm_execute``) e
todo registro novo recebe a empresa do contexto antes de ir para o banco (``before_flush``). Esquecer um filtro
numa rota, portanto, não mostra dados de outra empresa. Quem precisa ver todas (o worker procurando lotes para
retomar, a atualização do sistema conferindo se há lote em andamento) pede com
``.execution_options(todas_empresas=True)``.

A empresa do contexto vem do cabeçalho ``X-Empresa`` (ou ``?empresa=`` nos downloads por link), conferida contra
as permissões do usuário no middleware; o worker e a sincronização usam ``usar(empresa_id)``.

Na hospedagem a opção fica desligada até ser habilitada pelo painel (``EMISSOR_LIMITE_EMPRESAS``).
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, func
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.sql import util as sql_util

# Empresa 0 = cadastro de clientes compartilhado entre todas as empresas.
CLIENTES_COMPARTILHADOS = 0

_atual: ContextVar[int | None] = ContextVar("empresa_atual", default=None)
_lock = threading.Lock()
_varias: bool | None = None  # cache de "há mais de uma empresa"
_compartilhar: bool | None = None  # cache da opção de clientes compartilhados


class SemEmpresa(RuntimeError):
    """Código que lê dados de empresa sem dizer qual (só acontece com mais de uma empresa)."""


@contextmanager
def usar(empresa_id: int) -> Iterator[None]:
    tok = _atual.set(int(empresa_id))
    try:
        yield
    finally:
        _atual.reset(tok)


def definir(empresa_id: int | None) -> None:
    """Para o middleware: vale até o fim da requisição (cada requisição tem o seu contexto)."""
    _atual.set(empresa_id)


def atual() -> int:
    v = _atual.get()
    if v is not None:
        return v
    if varias():
        raise SemEmpresa("Operação sem empresa definida numa instalação com várias empresas.")
    return 1


def no_contexto() -> int | None:
    return _atual.get()


def varias() -> bool:
    global _varias
    if _varias is None:
        from sqlmodel import select

        from emissor.db import Configuracao, sessao

        with sessao() as s:
            n = s.exec(select(func.count()).select_from(Configuracao).where(Configuracao.ativa.is_not(False))).one()
        _varias = (n or 0) > 1
    return _varias


def compartilhar_clientes() -> bool:
    global _compartilhar
    if _compartilhar is None:
        from emissor.db import obter_instancia, sessao

        with sessao() as s:
            _compartilhar = bool(obter_instancia(s).compartilhar_clientes)
    return _compartilhar


def escopo_clientes() -> int:
    """Empresa dona do cadastro de clientes que vale agora (0 quando o cadastro é compartilhado)."""
    return CLIENTES_COMPARTILHADOS if compartilhar_clientes() else atual()


def limpar_cache() -> None:
    global _varias, _compartilhar
    with _lock:
        _varias = None
        _compartilhar = None


def limite() -> int | None:
    """Quantas empresas a instalação pode ter. None = sem limite (instalação própria).

    Na hospedagem (``FLY_APP_NAME``) a opção fica desligada (1) até o painel definir ``EMISSOR_LIMITE_EMPRESAS``.
    """
    bruto = os.environ.get("EMISSOR_LIMITE_EMPRESAS", "").strip()
    if bruto:
        try:
            return max(1, int(bruto))
        except ValueError:
            return 1
    return 1 if os.environ.get("FLY_APP_NAME") else None


# --- permissões ------------------------------------------------------------------------------------


class EmpresaNegada(Exception):
    def __init__(self, mensagem: str, status: int = 404):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def permitidas(usuario_id: int, admin: bool) -> dict[int, str]:
    """Empresas ativas que o usuário acessa → papel nelas (administrador da instalação: todas, como admin)."""
    from sqlmodel import select

    from emissor.db import Configuracao, UsuarioEmpresa, sessao

    with sessao() as s:
        ativas = [
            c.id for c in s.exec(select(Configuracao).where(Configuracao.ativa.is_not(False)).order_by(Configuracao.id))
        ]
        if not ativas:
            ativas = [1]  # instalação nova: a empresa 1 nasce com o onboarding
        if admin:
            return {eid: "admin" for eid in ativas}
        linhas = s.exec(select(UsuarioEmpresa).where(UsuarioEmpresa.usuario_id == usuario_id)).all()
    papeis = {r.empresa_id: r.papel for r in linhas}
    return {eid: papeis[eid] for eid in ativas if eid in papeis}


def resolver(usuario_id: int, admin: bool, pedida: str | None) -> tuple[int, str]:
    """Empresa da requisição: a pedida (cabeçalho ``X-Empresa`` ou ``?empresa=``) ou a primeira que o usuário acessa."""
    perm = permitidas(usuario_id, admin)
    if not perm:
        raise EmpresaNegada("Seu usuário não tem acesso a nenhuma empresa. Peça ao administrador.", 403)
    if pedida in (None, ""):
        eid = min(perm)
        return eid, perm[eid]
    try:
        eid = int(pedida)
    except ValueError:
        raise EmpresaNegada("Empresa inválida.") from None
    if eid not in perm:
        raise EmpresaNegada("Empresa não encontrada ou sem acesso para o seu usuário.")
    return eid, perm[eid]


# --- filtro automático por empresa ---------------------------------------------------------------


def _modelos():
    from emissor.db import Aba, Emissao, EventoNFSe, LinhaTabela, Lote, Tributacao

    return (Aba, Emissao, EventoNFSe, LinhaTabela, Lote, Tributacao)


def _filtrar(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.execution_options.get("todas_empresas") or state.is_column_load or state.is_relationship_load:
        return
    from emissor.db import Cliente

    envolvidos = {m.class_ for m in state.all_mappers}
    # ``select(func.count()).select_from(Emissao)`` não tem entidade: confere também as tabelas do FROM.
    if state.is_select and hasattr(state.statement, "get_final_froms"):
        tabelas = set()
        for f in state.statement.get_final_froms():
            tabelas.update(t.name for t in sql_util.find_tables(f, include_joins=True))
        envolvidos |= {m for m in (*_modelos(), Cliente) if m.__table__.name in tabelas}
    modelos = [m for m in _modelos() if m in envolvidos]
    opcoes = []
    if modelos:
        emp = atual()
        opcoes = [with_loader_criteria(m, m.empresa_id == emp, include_aliases=True) for m in modelos]
    if Cliente in envolvidos:
        opcoes.append(with_loader_criteria(Cliente, Cliente.empresa_id == escopo_clientes(), include_aliases=True))
    if opcoes:
        state.statement = state.statement.options(*opcoes)


def _carimbar(session: Session, _flush_context, _instances) -> None:
    from emissor.db import Cliente

    modelos = _modelos()
    for obj in session.new:
        if isinstance(obj, Cliente):
            if obj.empresa_id is None:
                obj.empresa_id = escopo_clientes()
        elif isinstance(obj, modelos) and obj.empresa_id is None:
            obj.empresa_id = atual()


_instalado = False


def instalar() -> None:
    global _instalado
    if _instalado:
        return
    event.listen(Session, "do_orm_execute", _filtrar)
    event.listen(Session, "before_flush", _carimbar)
    _instalado = True
