"""Tokens da API de integração (``/api/v1``).

- Formato: ``emp_<prefixo>_<segredo>`` (prefixo de 8 caracteres, segredo de 32 bytes aleatórios). O banco guarda o
  prefixo (para achar o token e mostrá-lo na tela) e o SHA-256 do token inteiro; o token só é mostrado ao ser criado.
- Cada token é de **uma empresa** e de **um ambiente** (produção ou homologação): emite, consulta e cancela só ali. O
  cabeçalho ``X-Empresa`` é ignorado.
- Escopos (``ESCOPOS``) dizem o que o token pode fazer. Só o administrador da empresa cria e revoga tokens.
- Limites em memória: ``LIMITE_POR_MINUTO`` requisições por token e ``LIMITE_NOTAS_DIA`` notas criadas por token por
  dia (protege a numeração e a Sefin de um sistema integrado com defeito).
- Vai só no cabeçalho ``Authorization: Bearer``; token na URL não é aceito (ficaria em logs e no histórico).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import date

from sqlmodel import select

from emissor import auth
from emissor.db import Configuracao, TokenApi, agora, sessao
from emissor.nfse.constants import Ambiente

ESCOPOS = {
    "notas:ler": "Consultar notas e lotes, baixar XML e PDF",
    "notas:emitir": "Emitir notas (uma ou em lote)",
    "notas:cancelar": "Cancelar notas autorizadas",
    "clientes:ler": "Consultar o cadastro de clientes",
    "clientes:escrever": "Cadastrar e alterar clientes",
    "webhooks": "Cadastrar e remover webhooks",
}
AMBIENTES = {"producao": Ambiente.PRODUCAO.value, "homologacao": Ambiente.HOMOLOGACAO.value}
NOME_AMBIENTE = {v: k for k, v in AMBIENTES.items()}
MAX_TOKENS_POR_EMPRESA = 20


def _limite(nome: str, padrao: int) -> int:
    try:
        return max(1, int(os.environ.get(nome, "") or padrao))
    except ValueError:
        return padrao


LIMITE_POR_MINUTO = _limite("EMISSOR_API_LIMITE_MINUTO", 60)
LIMITE_NOTAS_DIA = _limite("EMISSOR_API_LIMITE_NOTAS_DIA", 1000)

_lock = threading.Lock()
_requisicoes: dict[int, deque[float]] = defaultdict(deque)
_notas_dia: dict[tuple[int, date], int] = defaultdict(int)


class TokenError(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def token_dict(t: TokenApi) -> dict:
    return {
        "id": t.id,
        "nome": t.nome,
        "prefixo": f"emp_{t.prefixo}_…",
        "escopos": t.escopos.split(),
        "ambiente": NOME_AMBIENTE.get(t.ambiente, t.ambiente),
        "criado_em": t.criado_em.isoformat() if t.criado_em else None,
        "ultimo_uso": t.ultimo_uso.isoformat() if t.ultimo_uso else None,
        "ultimo_ip": t.ultimo_ip,
        "revogado_em": t.revogado_em.isoformat() if t.revogado_em else None,
        "ativo": t.revogado_em is None,
    }


def criar(
    empresa_id: int, nome: str, escopos: list[str], ambiente: str, acesso: auth.Acesso | None
) -> tuple[str, dict]:
    """Cria o token e devolve ``(token, dados)``. O token não pode ser visto de novo."""
    nome = (nome or "").strip()
    if not nome or len(nome) > 80:
        raise TokenError("Dê um nome ao token (até 80 caracteres), por exemplo o sistema que vai usá-lo.")
    desconhecidos = [e for e in escopos if e not in ESCOPOS]
    if desconhecidos:
        raise TokenError(f"Permissão desconhecida: {', '.join(desconhecidos)}.")
    if not escopos:
        raise TokenError("Escolha ao menos uma permissão.")
    if ambiente not in AMBIENTES:
        raise TokenError("Ambiente deve ser producao ou homologacao.")
    with sessao() as s:
        ativos = s.exec(
            select(TokenApi).where(TokenApi.empresa_id == empresa_id, TokenApi.revogado_em.is_(None))  # type: ignore[union-attr]
        ).all()
        if len(ativos) >= MAX_TOKENS_POR_EMPRESA:
            raise TokenError(f"Esta empresa já tem {MAX_TOKENS_POR_EMPRESA} tokens ativos. Revogue os que não usa.")
        prefixo = secrets.token_hex(4)
        while s.exec(select(TokenApi).where(TokenApi.prefixo == prefixo)).first() is not None:
            prefixo = secrets.token_hex(4)
        token = f"emp_{prefixo}_{secrets.token_urlsafe(32)}"
        t = TokenApi(
            empresa_id=empresa_id,
            nome=nome,
            prefixo=prefixo,
            hash=_sha256(token),
            escopos=" ".join(e for e in ESCOPOS if e in escopos),
            ambiente=AMBIENTES[ambiente],
            criado_por=acesso.usuario_id if acesso else None,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        dados = token_dict(t)
    auth.auditar("token_api_criado", f"“{nome}” ({ambiente}; {' '.join(escopos)})", acesso)
    return token, dados


def listar(empresa_id: int) -> list[dict]:
    with sessao() as s:
        itens = s.exec(select(TokenApi).where(TokenApi.empresa_id == empresa_id).order_by(TokenApi.id.desc())).all()  # type: ignore[union-attr]
        return [token_dict(t) for t in itens]


def revogar(empresa_id: int, token_id: int, acesso: auth.Acesso | None) -> None:
    with sessao() as s:
        t = s.get(TokenApi, token_id)
        if t is None or t.empresa_id != empresa_id:
            raise TokenError("Token não encontrado.", 404)
        if t.revogado_em is None:
            t.revogado_em = agora()
            s.add(t)
            s.commit()
    auth.auditar("token_api_revogado", f"“{t.nome}” (emp_{t.prefixo})", acesso)


def excluir(empresa_id: int, token_id: int, acesso: auth.Acesso | None) -> None:
    """Apaga o token da lista (ativo ou já revogado): deixa de funcionar na hora, como na revogação."""
    with sessao() as s:
        t = s.get(TokenApi, token_id)
        if t is None or t.empresa_id != empresa_id:
            raise TokenError("Token não encontrado.", 404)
        s.delete(t)
        s.commit()
    auth.auditar("token_api_excluido", f"“{t.nome}” (emp_{t.prefixo})", acesso)


def resolver(token: str | None, ip: str | None = None) -> auth.Acesso | None:
    """Acesso do token (com a empresa e o ambiente dele) ou None se não existe, foi revogado ou a empresa está inativa."""
    if not token or len(token) > 200 or not token.startswith("emp_"):
        return None
    partes = token.split("_", 2)
    if len(partes) != 3:
        return None
    with sessao() as s:
        t = s.exec(select(TokenApi).where(TokenApi.prefixo == partes[1])).first()
        if t is None or t.revogado_em is not None or not hmac.compare_digest(t.hash, _sha256(token)):
            return None
        emp = s.get(Configuracao, t.empresa_id)
        if (emp is None and t.empresa_id != 1) or (emp is not None and emp.ativa is False):
            return None  # empresa desativada (a 1 ainda sem configuração é a instalação nova)
        agora_ = agora()
        if t.ultimo_uso is None or (agora_ - t.ultimo_uso).total_seconds() > 60 or t.ultimo_ip != ip:
            t.ultimo_uso = agora_
            t.ultimo_ip = ip
            s.add(t)
            s.commit()
        return auth.Acesso(
            usuario_id=t.criado_por or 0,
            email=f"token:{t.nome}",
            nome=t.nome,
            papel="operador",
            totp_ativo=False,
            sessao_id=f"token:{t.id}",
            ip=ip,
            empresa_id=t.empresa_id,
            papel_empresa="operador",
            token_id=t.id,
            escopos=tuple(t.escopos.split()),
            ambiente_token=t.ambiente,
        )


def excedeu_limite(token_id: int) -> float | None:
    """Conta a requisição. Devolve os segundos a esperar quando o token passou de ``LIMITE_POR_MINUTO``."""
    agora_ = time.monotonic()
    with _lock:
        fila = _requisicoes[token_id]
        while fila and agora_ - fila[0] > 60:
            fila.popleft()
        if len(fila) >= LIMITE_POR_MINUTO:
            return max(1.0, 60 - (agora_ - fila[0]))
        fila.append(agora_)
    return None


def reservar_notas(token_id: int, quantidade: int) -> None:
    """Conta notas criadas pelo token hoje; recusa acima de ``LIMITE_NOTAS_DIA``."""
    hoje = date.today()
    with _lock:
        for chave in [k for k in _notas_dia if k[1] != hoje]:
            del _notas_dia[chave]
        usadas = _notas_dia[(token_id, hoje)]
        if usadas + quantidade > LIMITE_NOTAS_DIA:
            raise TokenError(
                f"Limite de {LIMITE_NOTAS_DIA} notas por dia deste token atingido ({usadas} hoje). "
                "Se o volume é esse mesmo, ajuste EMISSOR_API_LIMITE_NOTAS_DIA.",
                429,
            )
        _notas_dia[(token_id, hoje)] = usadas + quantidade


def devolver_notas(token_id: int, quantidade: int) -> None:
    with _lock:
        chave = (token_id, date.today())
        _notas_dia[chave] = max(0, _notas_dia[chave] - quantidade)


def limpar_limites() -> None:
    with _lock:
        _requisicoes.clear()
        _notas_dia.clear()
