"""Configurações → Integrações: tokens da API, webhooks e conectores de entrada (só o administrador da empresa)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from emissor import auth, tokens_api
from emissor.api.deps import AdminEmpresa
from emissor.db import obter_config, sessao
from emissor.services import conectores, webhooks

router = APIRouter(prefix="/integracoes")


@router.get("")
def ver(admin: AdminEmpresa) -> dict[str, Any]:
    with sessao() as s:
        ambiente = tokens_api.NOME_AMBIENTE.get(obter_config(s).ambiente)
    return {
        "ambiente_atual": ambiente,
        "tokens": tokens_api.listar(admin.empresa_id),
        "webhooks": webhooks.listar(admin.empresa_id),
        "conectores": conectores.listar(admin.empresa_id),
        "gatilhos": [
            {"id": "recebido", "descricao": "Quando o dinheiro fica disponível (PAYMENT_RECEIVED)"},
            {"id": "confirmado", "descricao": "Quando o pagamento é confirmado (PAYMENT_CONFIRMED; Pix na hora)"},
        ],
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


class ConectorEntrada(BaseModel):
    chave: str
    ambiente: str
    nome: str = ""
    ambiente_origem: str | None = None  # producao | sandbox (do Asaas); deduzido da chave quando dá
    gatilho: str = "recebido"
    tributacao_id: int | None = None
    descricao: str = ""


@router.post("/conectores")
def criar_conector(entrada: ConectorEntrada, admin: AdminEmpresa) -> dict[str, Any]:
    token, c = conectores.criar(
        admin.empresa_id,
        chave=entrada.chave,
        ambiente=entrada.ambiente,
        nome=entrada.nome,
        ambiente_origem=entrada.ambiente_origem,
        gatilho=entrada.gatilho,
        tributacao_id=entrada.tributacao_id,
        descricao=entrada.descricao,
        criado_por=admin.email,
    )
    auth.auditar("conector_criado", f"Asaas “{c['nome']}” ({c['ambiente_origem']} → NFS-e {c['ambiente']})", admin)
    return c | {"token": token}


class ConectorAlteracao(BaseModel):
    nome: str | None = None
    ativo: bool | None = None
    gatilho: str | None = None
    tributacao_id: int | None = None
    descricao: str | None = None
    chave: str | None = None


@router.put("/conectores/{conector_id}")
def alterar_conector(conector_id: int, entrada: ConectorAlteracao, admin: AdminEmpresa) -> dict[str, Any]:
    mudancas = entrada.model_dump(exclude_unset=True)
    c = conectores.alterar(admin.empresa_id, conector_id, mudancas)
    campos = ", ".join(k if k != "chave" else "chave da API" for k in mudancas)
    auth.auditar("conector_alterado", f"Asaas “{c['nome']}”: {campos}", admin)
    return c


@router.post("/conectores/{conector_id}/token")
def novo_token_conector(conector_id: int, admin: AdminEmpresa) -> dict[str, str]:
    token = conectores.novo_token(admin.empresa_id, conector_id)
    auth.auditar("conector_alterado", f"conector {conector_id}: token novo", admin)
    return {"token": token}


@router.delete("/conectores/{conector_id}")
def remover_conector(conector_id: int, admin: AdminEmpresa) -> dict[str, bool]:
    c = conectores.remover(admin.empresa_id, conector_id)
    auth.auditar("conector_removido", f"Asaas “{c.nome}”", admin)
    return {"ok": True}


@router.get("/conectores/{conector_id}/eventos")
def eventos_conector(conector_id: int, admin: AdminEmpresa, limite: int = 50) -> list[dict[str, Any]]:
    return conectores.eventos(admin.empresa_id, conector_id, limite)


@router.post("/eventos-entrada/{evento_id}/reprocessar")
def reprocessar_evento(evento_id: str, admin: AdminEmpresa) -> dict[str, Any]:
    r = conectores.reprocessar(admin.empresa_id, evento_id)
    auth.auditar("conector_reprocessar", f"aviso {evento_id}", admin)
    return r
