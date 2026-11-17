"""Notas já emitidas como ponto de partida da nota avulsa: copiar uma nota e o histórico de descrições/tributações.

A tributação de cada emissão é a cópia gravada ao criar o lote (``Emissao.tributacao_json``; nula = a padrão). Para a
nota avulsa ela volta como uma das três escolhas da tela:

- ``padrao``: a emissão saiu com a tributação padrão;
- ``predefinida``: existe uma tributação com o mesmo nome e os mesmos campos (a alíquota do ISS, que muda todo mês
  e é perguntada na emissão, não conta);
- ``propria``: a tributação mudou ou foi excluída depois, ou era "só desta nota": volta como "só para esta nota",
  com os campos exatamente como foram na nota.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from emissor.db import Emissao, Tributacao
from emissor.nfse.models import CAMPOS_TRIBUTACAO, normalizar_documento
from emissor.services import tributacao as trib_service

# Notas que a Sefin autorizou (as recusadas não servem de modelo).
EMITIDAS = ("autorizada", "cancelada", "substituida")
# Campos que mudam sem mudar "qual" tributação é (a alíquota do ISS do Simples muda todo mês).
_IGNORAR = {"p_aliq_iss", "p_aliq_iss_atualizada_em"}
LIMITE_NOTAS = 600  # notas mais recentes lidas para o histórico
LIMITE_ITENS = 40


def _comparavel(d: dict[str, Any]) -> dict[str, Any]:
    return {k: d.get(k) for k in CAMPOS_TRIBUTACAO if k not in _IGNORAR}


def escolha_tributacao(e: Emissao, predefinidas: list[Tributacao]) -> dict[str, Any]:
    dados = e.tributacao()
    if not dados:
        return {"tipo": "padrao", "nome": "Tributação padrão"}
    if e.tributacao_nome:
        for t in predefinidas:
            if t.nome == e.tributacao_nome and _comparavel(t.dados()) == _comparavel(dados):
                return {
                    "tipo": "predefinida",
                    "tributacao_id": t.id,
                    "nome": t.nome,
                    "resumo": trib_service.resumo(dados),
                }
    return {
        "tipo": "propria",
        # De onde veio: a tributação com esse nome mudou ou foi excluída depois da nota.
        "nome": e.tributacao_nome or "Só desta nota",
        "dados": dados,
        "resumo": trib_service.resumo(dados),
    }


def copia(s: Session, e: Emissao) -> dict[str, Any]:
    """O que a nota avulsa precisa para começar igual à nota ``e`` (o usuário confere e emite uma nota nova)."""
    nota = {k: v for k, v in (e.nota() or {}).items() if v not in (None, "", [])}
    nota.pop("ref_nfse", None)  # aponta para notas de uma operação específica: não se repete
    return {
        "emissao_id": e.id,
        "numero_nfse": e.numero_nfse,
        "nome": e.tomador_nome,
        "documento": "" if nota.get("tomador_exterior") else e.tomador_documento,
        "valor": e.valor,
        "descricao": e.descricao,
        "tributacao": escolha_tributacao(e, list(s.exec(select(Tributacao)))),
        "nota": nota,
    }


def historico(s: Session, documento: str | None = None) -> dict[str, Any]:
    """Descrições e tributações das notas emitidas, da mais recente para a mais antiga, sem repetição.

    Com ``documento``, também diz quais itens já foram usados para esse tomador (a tela filtra por eles).
    """
    doc = normalizar_documento(documento or "") or ""
    emissoes = list(
        s.exec(
            select(Emissao)
            .where(Emissao.status.in_(EMITIDAS))  # type: ignore[attr-defined]
            .order_by(Emissao.id.desc())  # type: ignore[union-attr]
            .limit(LIMITE_NOTAS)
        )
    )
    predefinidas = list(s.exec(select(Tributacao)))
    descricoes: dict[str, dict[str, Any]] = {}
    tributacoes: dict[str, dict[str, Any]] = {}
    for e in emissoes:
        quando = (e.dh_emissao or e.criado_em).isoformat()
        do_tomador = bool(doc) and e.tomador_documento == doc
        chave = " ".join((e.descricao or "").split()).casefold()
        if chave:
            item = descricoes.setdefault(
                chave,
                {
                    "texto": e.descricao,
                    "vezes": 0,
                    "ultima_em": quando,
                    "ultimo_tomador": e.tomador_nome,
                    "do_tomador": False,
                },
            )
            item["vezes"] += 1
            item["do_tomador"] = item["do_tomador"] or do_tomador
        escolha = escolha_tributacao(e, predefinidas)
        chave_t = (
            escolha["tipo"]
            if escolha["tipo"] == "padrao"
            else f"id:{escolha['tributacao_id']}"
            if escolha["tipo"] == "predefinida"
            else trib_service.dumps(_comparavel(escolha["dados"]))
        )
        item_t = tributacoes.setdefault(chave_t, {**escolha, "vezes": 0, "ultima_em": quando, "do_tomador": False})
        item_t["vezes"] += 1
        item_t["do_tomador"] = item_t["do_tomador"] or do_tomador
    return {
        "documento": doc or None,
        "descricoes": _recortar(list(descricoes.values())),
        "tributacoes": _recortar(list(tributacoes.values())),
    }


def _recortar(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Os mais recentes, sem perder nenhum do tomador escolhido."""
    return [x for i, x in enumerate(itens) if i < LIMITE_ITENS or x["do_tomador"]]
