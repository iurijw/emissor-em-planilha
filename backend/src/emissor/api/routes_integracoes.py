"""Configurações → Integrações: tokens da API e webhooks da empresa (só o administrador da empresa)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from emissor import auth, tokens_api
from emissor.api.deps import AdminEmpresa
from emissor.db import obter_config, sessao
from emissor.services import webhooks

router = APIRouter(prefix="/integracoes")


@router.get("")
def ver(admin: AdminEmpresa) -> dict[str, Any]:
    with sessao() as s:
        ambiente = tokens_api.NOME_AMBIENTE.get(obter_config(s).ambiente)
    return {
        "ambiente_atual": ambiente,
        "tokens": tokens_api.listar(admin.empresa_id),
        "webhooks": webhooks.listar(admin.empresa_id),
        "escopos": [{"id": k, "descricao": v} for k, v in tokens_api.ESCOPOS.items()],
        "eventos": [{"id": k, "descricao": v} for k, v in webhooks.EVENTOS.items()],
        "limites": {"por_minuto": tokens_api.LIMITE_POR_MINUTO, "notas_por_dia": tokens_api.LIMITE_NOTAS_DIA},
    }


class TokenEntrada(BaseModel):
    nome: str
    escopos: list[str]
    ambiente: str


@router.post("/tokens")
def criar_token(entrada: TokenEntrada, admin: AdminEmpresa) -> dict[str, Any]:
    token, dados = tokens_api.criar(admin.empresa_id, entrada.nome, entrada.escopos, entrada.ambiente, admin)
    return dados | {"token": token}


@router.delete("/tokens/{token_id}")
def revogar_token(token_id: int, admin: AdminEmpresa) -> dict[str, bool]:
    tokens_api.revogar(admin.empresa_id, token_id, admin)
    return {"ok": True}


class WebhookEntrada(BaseModel):
    url: str
    eventos: list[str]
    ambiente: str
    descricao: str = ""


@router.post("/webhooks")
def criar_webhook(entrada: WebhookEntrada, admin: AdminEmpresa) -> dict[str, Any]:
    amb = tokens_api.AMBIENTES.get(entrada.ambiente)
    if amb is None:
        raise webhooks.WebhookError("Ambiente deve ser producao ou homologacao.")
    segredo, w = webhooks.criar(admin.empresa_id, entrada.url, entrada.eventos, amb, entrada.descricao, admin.email)
    auth.auditar("webhook_criado", f"{w['url']} ({entrada.ambiente}; {' '.join(w['eventos'])})", admin)
    return w | {"segredo": segredo}


class WebhookAlteracao(BaseModel):
    ativo: bool


@router.put("/webhooks/{webhook_id}")
def alterar_webhook(webhook_id: int, entrada: WebhookAlteracao, admin: AdminEmpresa) -> dict[str, Any]:
    w = webhooks.alterar(admin.empresa_id, webhook_id, ativo=entrada.ativo)
    auth.auditar("webhook_alterado", f"{w['url']}: {'ativado' if entrada.ativo else 'desativado'}", admin)
    return w


@router.delete("/webhooks/{webhook_id}")
def remover_webhook(webhook_id: int, admin: AdminEmpresa) -> dict[str, bool]:
    w = webhooks.remover(admin.empresa_id, webhook_id)
    auth.auditar("webhook_removido", w.url, admin)
    return {"ok": True}


@router.post("/webhooks/{webhook_id}/testar")
def testar_webhook(webhook_id: int, admin: AdminEmpresa) -> dict[str, Any]:
    return webhooks.testar(admin.empresa_id, webhook_id)


@router.get("/webhooks/{webhook_id}/entregas")
def entregas(webhook_id: int, admin: AdminEmpresa, limite: int = 50) -> list[dict[str, Any]]:
    return webhooks.entregas(admin.empresa_id, webhook_id, limite)


@router.post("/entregas/{entrega_id}/reenviar")
def reenviar(entrega_id: str, admin: AdminEmpresa) -> dict[str, Any]:
    return webhooks.reenviar(admin.empresa_id, entrega_id)
