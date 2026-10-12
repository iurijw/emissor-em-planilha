"""Tributações predefinidas, abas da tabela e nota avulsa."""

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import func, select

from emissor import auth
from emissor.api.deps import AdminAtual, UsuarioAtual
from emissor.api.routes_emissoes import obter_lote
from emissor.db import Aba, Cliente, Emissao, LinhaTabela, Tributacao, agora, obter_config, sessao
from emissor.nfse.models import aplicar_tributacao, normalizar_documento, tributacao_de
from emissor.services import tabela as tabela_service
from emissor.services import tributacao as trib_service
from emissor.services.emissao import (
    NotaAvulsa,
    aliquotas_iss,
    criar_avulsa,
    linha_avulsa,
    validar_avulsa,
    verificar_prontidao,
)

log = logging.getLogger(__name__)
router = APIRouter()


# --- tributações predefinidas ----------------------------------------------------------------------


def _tributacao_dict(t: Tributacao, abas: list[Aba]) -> dict[str, Any]:
    dados = t.dados()
    return {
        "id": t.id,
        "nome": t.nome,
        "dados": dados,
        "resumo": trib_service.resumo(dados),
        "abas": [a.nome for a in abas if a.tributacao_id == t.id],
    }


@router.get("/tributacoes")
def listar_tributacoes() -> dict[str, Any]:
    """Tributações predefinidas e os padrões das configurações (ponto de partida de uma tributação nova)."""
    with sessao() as s:
        fiscal = obter_config(s).config_fiscal()
        abas = trib_service.garantir_abas(s)
        itens = list(s.exec(select(Tributacao).order_by(Tributacao.nome)))
    padrao = tributacao_de(fiscal) if fiscal else None
    return {
        "padrao": padrao,
        "padrao_resumo": trib_service.resumo(padrao) if padrao else None,
        # O regime decide quais alíquotas a tela mostra (ex.: a do ISS retido só para ISS apurado pelo Simples).
        "regime": {"op_simp_nac": fiscal.op_simp_nac, "reg_ap_trib_sn": fiscal.reg_ap_trib_sn} if fiscal else None,
        "itens": [_tributacao_dict(t, abas) for t in itens],
    }


class TributacaoEntrada(BaseModel):
    nome: str
    dados: dict[str, Any]


def _nome_tributacao(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not nome:
        raise HTTPException(400, "Dê um nome à tributação.")
    if len(nome) > 60:
        raise HTTPException(400, "Nome da tributação com mais de 60 caracteres.")
    return nome


@router.post("/tributacoes")
def criar_tributacao(entrada: TributacaoEntrada, admin: AdminAtual) -> dict[str, Any]:
    nome = _nome_tributacao(entrada.nome)
    with sessao() as s:
        dados = trib_service.validar_completa(entrada.dados, trib_service.config_base(s))
        if s.exec(select(Tributacao).where(Tributacao.nome == nome)).first():
            raise HTTPException(400, f"Já existe uma tributação chamada “{nome}”.")
        t = Tributacao(nome=nome, dados_json=trib_service.dumps(dados))
        s.add(t)
        s.commit()
        s.refresh(t)
        abas = trib_service.garantir_abas(s)
    auth.auditar("tributacao_criada", f"{nome}: {trib_service.resumo(dados)}", admin)
    return _tributacao_dict(t, abas)


@router.put("/tributacoes/{tributacao_id}")
def alterar_tributacao(tributacao_id: int, entrada: TributacaoEntrada, admin: AdminAtual) -> dict[str, Any]:
    nome = _nome_tributacao(entrada.nome)
    with sessao() as s:
        t = s.get(Tributacao, tributacao_id)
        if t is None:
            raise HTTPException(404, "Tributação não encontrada.")
        dados = trib_service.validar_completa(entrada.dados, trib_service.config_base(s))
        outra = s.exec(select(Tributacao).where(Tributacao.nome == nome, Tributacao.id != tributacao_id)).first()
        if outra:
            raise HTTPException(400, f"Já existe uma tributação chamada “{nome}”.")
        t.nome = nome
        t.dados_json = trib_service.dumps(dados)
        t.atualizado_em = agora()
        s.add(t)
        s.commit()
        abas = trib_service.garantir_abas(s)
    auth.auditar("tributacao_alterada", f"{nome}: {trib_service.resumo(dados)}", admin)
    return _tributacao_dict(t, abas)


class AliquotaIssEntrada(BaseModel):
    tributacao_id: int | None = None  # None = padrões das configurações
    p_aliq_iss: str


@router.post("/tributacoes/aliquota-iss")
def alterar_aliquota_iss(entrada: AliquotaIssEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    """Alíquota do ISS retido de uma tributação (ou dos padrões), pelo aviso da emissão.

    É o único campo de tributação que o operador pode mudar: a alíquota efetiva do Simples muda todo mês.
    """
    hoje = agora().date().isoformat()
    with sessao() as s:
        base = trib_service.config_base(s)
        if entrada.tributacao_id is not None:
            t = s.get(Tributacao, entrada.tributacao_id)
            if t is None:
                raise HTTPException(404, "Tributação não encontrada. Recarregue a página.")
            dados = t.dados()
            nome = t.nome
        else:
            t, dados, nome = None, tributacao_de(base), "padrão"
        anterior = dados.get("p_aliq_iss")
        dados = trib_service.validar_completa(
            {**dados, "p_aliq_iss": entrada.p_aliq_iss, "p_aliq_iss_atualizada_em": hoje}, base
        )
        if not dados.get("p_aliq_iss"):
            raise HTTPException(400, "Informe a alíquota do ISS retido.")
        if t is not None:
            t.dados_json = trib_service.dumps(dados)
            t.atualizado_em = agora()
            s.add(t)
        else:
            cfg = obter_config(s)
            cfg.set_config_fiscal(aplicar_tributacao(base, dados))
            cfg.atualizado_em = agora()
            s.add(cfg)
        s.commit()
    auth.auditar("aliquota_iss_alterada", f"tributação {nome}: {anterior or '-'}% → {dados['p_aliq_iss']}%", acesso)
    return {"tributacao_id": entrada.tributacao_id, "p_aliq_iss": dados["p_aliq_iss"], "atualizada_em": hoje}


@router.delete("/tributacoes/{tributacao_id}")
def excluir_tributacao(tributacao_id: int, admin: AdminAtual) -> dict[str, bool]:
    with sessao() as s:
        t = s.get(Tributacao, tributacao_id)
        if t is None:
            return {"ok": True}
        usando = [a.nome for a in trib_service.garantir_abas(s) if a.tributacao_id == tributacao_id]
        if usando:
            raise HTTPException(
                400,
                {
                    "mensagem": "Esta tributação está em uso. Troque a tributação destas abas antes de excluir.",
                    "detalhes": [f"Aba “{n}”" for n in usando],
                },
            )
        s.delete(t)
        s.commit()
    auth.auditar("tributacao_excluida", t.nome, admin)
    return {"ok": True}


# --- abas ----------------------------------------------------------------------------------------------


def _aba_dict(a: Aba, linhas: int, tributacoes: dict[int, Tributacao], nomes: dict[str, str]) -> dict[str, Any]:
    t = tributacoes.get(a.tributacao_id) if a.tributacao_id is not None else None
    return {
        "id": a.id,
        "nome": a.nome,
        "ordem": a.ordem,
        "tributacao_id": a.tributacao_id,
        "tributacao_nome": t.nome if t else None,
        "tributacao_excluida": a.tributacao_id is not None and t is None,
        "clientes": [{"documento": d, "nome": nomes.get(d, "")} for d in a.clientes()],
        "linhas": linhas,
    }


@router.get("/abas")
def listar_abas() -> list[dict[str, Any]]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        contagem = dict(
            s.exec(select(LinhaTabela.aba_id, func.count()).group_by(LinhaTabela.aba_id)).all()  # type: ignore[arg-type]
        )
        tributacoes = {t.id: t for t in s.exec(select(Tributacao))}
        docs = {d for a in abas for d in a.clientes()}
        nomes = (
            {c.documento: c.nome for c in s.exec(select(Cliente).where(Cliente.documento.in_(docs)))} if docs else {}
        )
    return [_aba_dict(a, contagem.get(a.id, 0), tributacoes, nomes) for a in abas]


class AbaEntrada(BaseModel):
    nome: str | None = None
    tributacao_id: int | None = None
    clientes: list[str] | None = None
    ordem: int | None = None


def _validar_aba(s, entrada: AbaEntrada, aba: Aba) -> list[str]:
    """Aplica a entrada na aba; devolve o que mudou (para a auditoria)."""
    mudou = []
    if entrada.nome is not None:
        nome = " ".join(entrada.nome.split())
        if not nome:
            raise HTTPException(400, "Dê um nome à aba.")
        if len(nome) > 40:
            raise HTTPException(400, "Nome da aba com mais de 40 caracteres.")
        if s.exec(select(Aba).where(Aba.nome == nome, Aba.id != aba.id)).first():
            raise HTTPException(400, f"Já existe uma aba chamada “{nome}”.")
        if nome != aba.nome:
            mudou.append(f"nome “{nome}”")
        aba.nome = nome
    # tributacao_id ausente do corpo = não mexe; null explícito = padrões das configurações.
    if "tributacao_id" in entrada.model_fields_set and entrada.tributacao_id != aba.tributacao_id:
        t = s.get(Tributacao, entrada.tributacao_id) if entrada.tributacao_id is not None else None
        if entrada.tributacao_id is not None and t is None:
            raise HTTPException(400, "Tributação não encontrada.")
        aba.tributacao_id = entrada.tributacao_id
        mudou.append(f"tributação “{t.nome}”" if t else "tributação padrão")
    if entrada.clientes is not None:
        docs = []
        for d in entrada.clientes:
            n = normalizar_documento(d) or ""
            if len(n) not in (11, 14):
                raise HTTPException(400, f"CNPJ/CPF inválido na lista de clientes da aba: {d!r}.")
            if n not in docs:
                docs.append(n)
        if docs != aba.clientes():
            mudou.append(f"{len(docs)} cliente(s) exclusivo(s)" if docs else "sem clientes exclusivos")
        aba.clientes_json = json.dumps(docs)
    if entrada.ordem is not None:
        aba.ordem = entrada.ordem
    return mudou


@router.post("/abas")
def criar_aba(entrada: AbaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        aba = Aba(nome="", ordem=max((a.ordem for a in abas), default=0) + 1)
        if entrada.nome is None:
            raise HTTPException(400, "Dê um nome à aba.")
        mudou = _validar_aba(s, entrada, aba)
        s.add(aba)
        s.commit()
        s.refresh(aba)
    auth.auditar("aba_criada", f"“{aba.nome}”: " + ", ".join(mudou), acesso)
    return next(a for a in listar_abas() if a["id"] == aba.id)


@router.put("/abas/{aba_id}")
def alterar_aba(aba_id: int, entrada: AbaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        aba = s.get(Aba, aba_id)
        if aba is None:
            raise HTTPException(404, "Aba não encontrada.")
        mudou = _validar_aba(s, entrada, aba)
        s.add(aba)
        s.commit()
    if mudou:
        auth.auditar("aba_alterada", f"“{aba.nome}”: " + ", ".join(mudou), acesso)
    return next(a for a in listar_abas() if a["id"] == aba_id)


@router.delete("/abas/{aba_id}")
def excluir_aba(aba_id: int, acesso: UsuarioAtual) -> dict[str, bool]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        aba = next((a for a in abas if a.id == aba_id), None)
        if aba is None:
            return {"ok": True}
        if len(abas) == 1:
            raise HTTPException(400, "A tabela precisa de pelo menos uma aba.")
        linhas = list(s.exec(select(LinhaTabela).where(LinhaTabela.aba_id == aba_id)))
        ids = [ln.id for ln in linhas]
        if (
            ids
            and s.exec(
                select(Emissao).where(Emissao.linha_id.in_(ids), Emissao.status.in_(["pendente", "processando"]))
            ).first()
        ):
            raise HTTPException(400, "Há notas desta aba sendo emitidas agora. Espere a emissão terminar.")
        for ln in linhas:
            s.delete(ln)
        s.delete(aba)
        s.commit()
    auth.auditar("aba_excluida", f"“{aba.nome}” ({len(linhas)} linha(s))", acesso)
    return {"ok": True}


# --- nota avulsa -----------------------------------------------------------------------------------------


class AvulsaEntrada(BaseModel):
    nome: str = ""
    documento: str = ""
    valor: str | float | None = ""
    descricao: str = ""
    tributacao_id: int | None = None  # tributação predefinida
    tributacao: dict[str, Any] | None = None  # tributação só desta nota (completa, partindo dos padrões)


def _nota(s, entrada: AvulsaEntrada) -> NotaAvulsa:
    try:
        valor = tabela_service.parse_valor(entrada.valor)
    except ValueError:
        valor = str(entrada.valor or "").strip()
    nome_trib, dados_trib = None, None
    if entrada.tributacao_id is not None and entrada.tributacao is not None:
        raise HTTPException(400, "Escolha uma tributação predefinida ou uma tributação só para esta nota, não as duas.")
    if entrada.tributacao_id is not None:
        t = s.get(Tributacao, entrada.tributacao_id)
        if t is None:
            raise HTTPException(400, "Tributação não encontrada. Recarregue a página.")
        nome_trib, dados_trib = t.nome, t.dados()
    elif entrada.tributacao is not None:
        dados_trib = trib_service.validar_completa(entrada.tributacao, trib_service.config_base(s))
    return NotaAvulsa(
        nome=entrada.nome.strip(),
        documento=entrada.documento,
        valor=valor,
        descricao=entrada.descricao.strip(),
        tributacao_nome=nome_trib,
        tributacao=dados_trib,
        tributacao_id=entrada.tributacao_id,
    )


def _total(valor: str) -> str:
    try:
        return f"{Decimal(valor):.2f}"
    except (InvalidOperation, TypeError):
        return "0.00"


@router.post("/avulsa/validar")
def validar_nota_avulsa(entrada: AvulsaEntrada) -> dict[str, Any]:
    """Mesma conferência da tabela, no formato de ``POST /tabela/validar`` (uma linha, ``linha_id`` 0)."""
    with sessao() as s:
        cfg = obter_config(s)
        nota = _nota(s, entrada)
        v = validar_avulsa(s, nota, cfg.amb)
        aliquotas = aliquotas_iss(s, [linha_avulsa(nota)], nota.tributacao, nota.tributacao_id)
        problemas = verificar_prontidao(s)
        if nota.tributacao is not None:
            problemas += [
                f"Tributação: {e}" for e in trib_service.coerencia(nota.tributacao, trib_service.config_base(s))
            ]
    return {
        "ambiente": cfg.ambiente,
        "ambiente_descricao": cfg.amb.descricao,
        "problemas": problemas,
        "linhas": [{"linha_id": 0, "erros": v.erros, "avisos": v.avisos}],
        "quantidade": 1,
        "total": _total(nota.valor),
        "tributacao_nome": nota.tributacao_nome or ("Só desta nota" if nota.tributacao else None),
        "aliquotas_iss": aliquotas,
    }


@router.post("/avulsa")
def emitir_nota_avulsa(entrada: AvulsaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        nota = _nota(s, entrada)
    lote = criar_avulsa(nota)
    trib = nota.tributacao_nome or (
        "só desta nota: " + trib_service.resumo(nota.tributacao) if nota.tributacao else "padrão"
    )
    auth.auditar("nota_avulsa", f"lote {lote.id}, ambiente {lote.ambiente}, tributação {trib}", acesso)
    return obter_lote(lote.id)
