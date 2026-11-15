"""Conectores de entrada: um sistema de cobrança avisa um pagamento e o emissor faz a nota sozinho.

Conectores: **Asaas** e **Stripe** (ver ``_preparar_stripe`` mais abaixo). No Asaas:

- O administrador cadastra a chave da API do Asaas (para ler o cliente da cobrança) e escolhe o ambiente da NFS-e,
  a tributação, a descrição e quando emitir. O emissor devolve o endereço do webhook
  (``/api/v1/entrada/asaas/<caminho>``) e um token, mostrado uma vez, que vai no campo "Token de autenticação" do
  webhook no Asaas (ele o manda no cabeçalho ``asaas-access-token``; o Asaas não assina o corpo).
- A rota de entrada confere o token, **grava o aviso e responde 200 na hora** (o Asaas pausa a fila depois de 15
  falhas seguidas e entrega o mesmo evento mais de uma vez). Uma thread processa os avisos gravados.
- ``PAYMENT_RECEIVED`` (gatilho "recebido": dinheiro disponível) ou também ``PAYMENT_CONFIRMED`` (gatilho
  "confirmado": pago, ainda não disponível; cartão de crédito confirma no dia e só fica disponível ~32 dias depois).
  Pix e dinheiro só têm ``PAYMENT_RECEIVED``. Estornos e outros eventos ficam registrados, sem nota e sem cancelar
  nada (quem decide cancelar é a pessoa).
- Contra duplicidade: o aviso tem id próprio (``evt_...``) e a nota leva ``referencia_externa = id da cobrança``
  (``pay_...``): confirmado e depois recebido da mesma cobrança fazem uma nota só.
- O cliente vem de ``GET /v3/customers/{id}``: CPF/CNPJ, nome, e-mail e endereço vão para o cadastro (cliente que já
  existe só tem os campos vazios completados: o cadastro do emissor prevalece). Cliente sem CPF/CNPJ ou estrangeiro
  vira erro no aviso (pode ser emitido pela nota avulsa).
- Chave do sandbox do Asaas só com NFS-e em homologação (cobrança de teste nunca vira nota de verdade).

Na Stripe:

- O administrador cola uma chave da API (de preferência restrita: leitura de Customers, Invoices e Checkout
  Sessions, escrita de Webhook Endpoints). Com ``EMISSOR_URL_PUBLICA`` o emissor cria o endpoint na Stripe
  (``POST /v1/webhook_endpoints``) e guarda o segredo de assinatura; sem ela, o administrador cria o endpoint no
  painel da Stripe e cola o segredo (``whsec_...``). Remover o conector apaga o endpoint criado pelo emissor.
- O corpo vem assinado (``Stripe-Signature: t=<unix>,v1=HMAC-SHA256(segredo, "<t>.<corpo>")``, 5 min de
  tolerância). A rota grava e responde 200 na hora, como no Asaas.
- Uma nota por **fatura paga** (``invoice.paid``: assinaturas e faturas avulsas, referência ``in_...``) ou por
  **Checkout pago sem fatura** (``checkout.session.completed`` com ``payment_status=paid`` ou
  ``checkout.session.async_payment_succeeded`` para boleto; referência ``cs_...``). Checkout de assinatura ou com
  fatura fica com a fatura (senão sairiam duas notas). Fatura de valor zero (período de teste) e moeda que não seja
  real não emitem.
- O CPF/CNPJ vem dos Tax IDs (``br_cpf``/``br_cnpj``) da fatura, do Checkout ou do cliente, ou do metadata
  ``cpf_cnpj`` (``cpf``, ``cnpj``, ``documento``). A Stripe não tem bairro: CNPJ sem endereço completo é completado
  pela Receita (BrasilAPI), sem trocar o que veio.
- Chave de teste (``sk_test_``/``rk_test_``) só com NFS-e em homologação, e evento de teste nunca entra num
  conector com chave real (``livemode``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlmodel import select

from emissor import auth, config, empresas, security
from emissor.db import (
    Cliente,
    ConectorEntrada,
    EventoEntrada,
    Tributacao,
    agora,
    obter_cliente,
    obter_config,
    sessao,
)
from emissor.nfse import codes
from emissor.nfse.constants import Ambiente
from emissor.nfse.models import normalizar_documento, somente_digitos
from emissor.services import clientes as clientes_service

log = logging.getLogger(__name__)

TIPOS = {"asaas": "Asaas", "stripe": "Stripe"}
URL_ASAAS = {"producao": "https://api.asaas.com/v3", "sandbox": "https://api-sandbox.asaas.com/v3"}
URL_STRIPE = "https://api.stripe.com/v1"
EVENTOS_STRIPE = ("invoice.paid", "checkout.session.completed", "checkout.session.async_payment_succeeded")
GATILHOS = {
    "asaas": {
        "recebido": ("PAYMENT_RECEIVED",),
        "confirmado": ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"),
    },
    "stripe": {"pago": EVENTOS_STRIPE},
}
GATILHO_PADRAO = {"asaas": "recebido", "stripe": "pago"}
ORIGENS = {"asaas": ("producao", "sandbox"), "stripe": ("producao", "teste")}
TOLERANCIA_ASSINATURA = 300  # segundos (o mesmo das bibliotecas da Stripe)
CHAVES_DOCUMENTO = ("cpf_cnpj", "cpfCnpj", "cnpj_cpf", "cnpj", "cpf", "documento")
DESCRICAO_PADRAO = "{cobranca}"
NOME_AMBIENTE = {Ambiente.PRODUCAO.value: "producao", Ambiente.HOMOLOGACAO.value: "homologacao"}
AMBIENTES = {v: k for k, v in NOME_AMBIENTE.items()}
ESPERAS = [60, 300, 1800, 7200]  # origem fora do ar: novas tentativas de ler o cliente
MAX_CONECTORES_POR_EMPRESA = 5
MAX_CORPO = 256 * 1024
GUARDAR_EVENTOS = timedelta(days=90)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_transporte: httpx.BaseTransport | None = None


class ConectorError(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


class _Temporario(Exception):
    """Falha que vale tentar de novo depois (Asaas ou Stripe fora do ar, tempo esgotado)."""


def definir_transporte(t: httpx.BaseTransport | None) -> None:
    """Injeção para testes (Asaas simulado)."""
    global _transporte
    _transporte = t


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


# --- API do Asaas ----------------------------------------------------------------------------------------------


def _ambiente_da_chave(chave: str) -> str | None:
    if chave.startswith("$aact_prod_"):
        return "producao"
    if chave.startswith("$aact_hmlg_"):
        return "sandbox"
    return None


def _asaas(chave: str, origem: str, caminho: str) -> dict[str, Any]:
    """GET na API do Asaas. 401 vira ``ConectorError``; rede/5xx/429 viram ``_Temporario``."""
    url = URL_ASAAS[origem] + caminho
    cab = {"access_token": chave, "User-Agent": f"EmissorEmPlanilha/{config.APP_VERSION}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transporte, follow_redirects=False) as http:
            r = http.get(url, headers=cab)
    except httpx.HTTPError as exc:
        raise _Temporario(f"Sem resposta do Asaas ({exc.__class__.__name__}).") from exc
    if r.status_code == 401:
        raise ConectorError(
            "O Asaas recusou a chave da API (inválida, revogada ou de outro ambiente). Cadastre a chave de novo."
        )
    if r.status_code == 429 or r.status_code >= 500:
        raise _Temporario(f"Asaas respondeu {r.status_code}.")
    try:
        dados = r.json()
    except ValueError:
        dados = {}
    if r.status_code == 404:
        raise ConectorError("O Asaas não encontrou o cliente da cobrança (excluído?).")
    if r.status_code >= 400:
        erros = "; ".join(e.get("description", "") for e in dados.get("errors", []) if isinstance(e, dict))
        raise ConectorError(f"O Asaas recusou a consulta ({r.status_code}): {erros or r.text[:200]}")
    return dados if isinstance(dados, dict) else {}


def conferir_chave(tipo: str, chave: str, origem: str) -> None:
    try:
        if tipo == "stripe":
            _stripe(chave, "GET", "/customers?limit=1")
        else:
            _asaas(chave, origem, "/customers?limit=1")
    except _Temporario as exc:
        raise ConectorError(f"Não foi possível falar com {TIPOS[tipo]} para conferir a chave: {exc}", 502) from exc


# --- API da Stripe -----------------------------------------------------------------------------------------------


def _ambiente_da_chave_stripe(chave: str) -> str | None:
    if chave.startswith(("sk_live_", "rk_live_")):
        return "producao"
    if chave.startswith(("sk_test_", "rk_test_")):
        return "teste"
    return None


def _stripe(chave: str, metodo: str, caminho: str, dados: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """Chamada à API da Stripe. 401/403/4xx viram ``ConectorError``; rede/5xx/429 viram ``_Temporario``."""
    cab = {
        "Authorization": f"Bearer {chave}",
        "User-Agent": f"EmissorEmPlanilha/{config.APP_VERSION}",
        "Accept": "application/json",
    }
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transporte, follow_redirects=False) as http:
            if dados is not None:
                cab["Content-Type"] = "application/x-www-form-urlencoded"
            corpo_envio = urlencode(dados).encode() if dados is not None else None
            r = http.request(metodo, URL_STRIPE + caminho, headers=cab, content=corpo_envio)
    except httpx.HTTPError as exc:
        raise _Temporario(f"Sem resposta da Stripe ({exc.__class__.__name__}).") from exc
    if r.status_code == 429 or r.status_code >= 500:
        raise _Temporario(f"Stripe respondeu {r.status_code}.")
    try:
        corpo = r.json()
    except ValueError:
        corpo = {}
    erro = corpo.get("error") if isinstance(corpo, dict) and isinstance(corpo.get("error"), dict) else {}
    texto = str(erro.get("message") or r.text[:200])
    if r.status_code == 401:
        raise ConectorError("A Stripe recusou a chave da API (inválida ou revogada). Cadastre a chave de novo.")
    if r.status_code == 403:
        raise ConectorError(f"A chave da Stripe não tem permissão para isso: {texto}")
    if r.status_code == 404:
        raise ConectorError(f"A Stripe não encontrou o registro: {texto}")
    if r.status_code >= 400:
        raise ConectorError(f"A Stripe recusou o pedido ({r.status_code}): {texto}")
    return corpo if isinstance(corpo, dict) else {}


def url_publica() -> str:
    return os.environ.get("EMISSOR_URL_PUBLICA", "").strip().rstrip("/")


def _criar_endpoint_stripe(chave: str, caminho: str) -> tuple[str, str]:
    """Cria o endpoint do webhook na Stripe e devolve ``(id, segredo)``."""
    base = url_publica()
    dados = [("url", f"{base}/api/v1/entrada/stripe/{caminho}"), ("description", "Emissor em Planilha: NFS-e")]
    dados += [("enabled_events[]", e) for e in EVENTOS_STRIPE]
    try:
        r = _stripe(chave, "POST", "/webhook_endpoints", dados)
    except _Temporario as exc:
        raise ConectorError(f"Não foi possível falar com a Stripe para criar o webhook: {exc}", 502) from exc
    if not r.get("id") or not r.get("secret"):
        raise ConectorError("A Stripe não devolveu o segredo do webhook. Crie o endpoint no painel e cole o segredo.")
    return str(r["id"]), str(r["secret"])


def _assinatura_stripe_ok(segredo: str, cabecalho: str | None, corpo: bytes, agora_ts: float | None = None) -> bool:
    if not cabecalho:
        return False
    partes = [p.strip().split("=", 1) for p in cabecalho.split(",") if "=" in p]
    t = next((v for k, v in partes if k == "t"), "")
    assinaturas = [v for k, v in partes if k == "v1"]
    if not t.isdigit() or not assinaturas:
        return False
    if abs((agora_ts if agora_ts is not None else time.time()) - int(t)) > TOLERANCIA_ASSINATURA:
        return False
    esperado = hmac.new(segredo.encode(), t.encode() + b"." + corpo, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(esperado, a) for a in assinaturas)


# --- cadastro ----------------------------------------------------------------------------------------------------


def conector_dict(c: ConectorEntrada) -> dict[str, Any]:
    with sessao() as s:
        t = s.get(Tributacao, c.tributacao_id) if c.tributacao_id else None
        q = select(EventoEntrada).where(EventoEntrada.conector_id == c.id, EventoEntrada.status == "erro")
        erros = len(s.exec(q).all())
    return {
        "id": c.id,
        "tipo": c.tipo,
        "nome": c.nome,
        "ambiente": NOME_AMBIENTE.get(c.ambiente, c.ambiente),
        "ambiente_origem": c.ambiente_origem,
        "caminho": f"/api/v1/entrada/{c.tipo}/{c.caminho}",
        "webhook_criado": bool(c.webhook_origem_id),
        "segredo_pendente": c.tipo == "stripe" and not c.segredo_enc,
        "eventos_origem": list(EVENTOS_STRIPE) if c.tipo == "stripe" else list(GATILHOS["asaas"].get(c.gatilho, ())),
        "gatilho": c.gatilho,
        "tributacao_id": c.tributacao_id,
        "tributacao_nome": t.nome if t else None,
        "descricao": c.descricao,
        "ativo": c.ativo,
        "criado_por": c.criado_por,
        "criado_em": c.criado_em.isoformat() if c.criado_em else None,
        "ultimo_evento_em": c.ultimo_evento_em.isoformat() if c.ultimo_evento_em else None,
        "eventos_com_erro": erros,
    }


def evento_dict(e: EventoEntrada) -> dict[str, Any]:
    return {
        "id": e.id,
        "evento": e.evento,
        "pagamento_id": e.pagamento_id,
        "valor": e.valor,
        "status": e.status,
        "mensagem": e.mensagem,
        "emissao_id": e.emissao_id,
        "tentativas": e.tentativas,
        "recebido_em": e.recebido_em.isoformat() if e.recebido_em else None,
        "processado_em": e.processado_em.isoformat() if e.processado_em else None,
    }


def _conferir_opcoes(s, tipo: str, gatilho: str, tributacao_id: int | None, descricao: str) -> str:
    if gatilho not in GATILHOS[tipo]:
        raise ConectorError(f"Escolha quando emitir: {' ou '.join(GATILHOS[tipo])}.")
    if tributacao_id is not None and s.get(Tributacao, tributacao_id) is None:
        raise ConectorError("Tributação não encontrada.")
    descricao = (descricao or "").strip()
    if len(descricao) > 1000:
        raise ConectorError("A descrição deve ter até 1.000 caracteres.")
    return descricao


def _origem_da_chave(tipo: str, chave: str) -> str | None:
    return _ambiente_da_chave_stripe(chave) if tipo == "stripe" else _ambiente_da_chave(chave)


def criar(
    empresa_id: int,
    *,
    chave: str,
    ambiente: str,
    tipo: str = "asaas",
    nome: str = "",
    ambiente_origem: str | None = None,
    gatilho: str | None = None,
    tributacao_id: int | None = None,
    descricao: str = "",
    segredo_webhook: str | None = None,
    criado_por: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Cadastra o conector (conferindo a chave na origem) e devolve ``(token, dados)``.

    O token do Asaas só aparece agora; a Stripe não usa token (``None``): o corpo vem assinado.
    """
    if tipo not in TIPOS:
        raise ConectorError("Conector desconhecido.")
    gatilho = gatilho or GATILHO_PADRAO[tipo]
    chave = (chave or "").strip()
    if not chave or len(chave) > 300 or " " in chave:
        if tipo == "stripe":
            raise ConectorError("Cole a chave da API da Stripe (Desenvolvedores → Chaves de API).")
        raise ConectorError("Cole a chave da API do Asaas (Integrações → Chaves de API, no Asaas).")
    if ambiente not in AMBIENTES:
        raise ConectorError("Ambiente da NFS-e deve ser producao ou homologacao.")
    pela_chave = _origem_da_chave(tipo, chave)
    origem = ambiente_origem or pela_chave
    if tipo == "stripe":
        if pela_chave is None:
            raise ConectorError(
                "Use uma chave secreta ou restrita da Stripe (sk_live_, rk_live_, sk_test_ ou rk_test_)."
            )
        if origem != pela_chave:
            raise ConectorError(f"Esta chave é do modo {'de teste' if pela_chave == 'teste' else 'real'} da Stripe.")
        if origem == "teste" and ambiente != "homologacao":
            raise ConectorError(
                "Chave de teste da Stripe só emite em homologação: pagamento de teste não vira nota real."
            )
    else:
        if origem not in URL_ASAAS:
            raise ConectorError("Diga se a chave é da conta de produção ou do sandbox do Asaas.")
        if pela_chave and origem != pela_chave:
            raise ConectorError(f"Esta chave é do {'sandbox' if pela_chave == 'sandbox' else 'Asaas de produção'}.")
        if origem == "sandbox" and ambiente != "homologacao":
            raise ConectorError(
                "Chave do sandbox do Asaas só emite em homologação: cobrança de teste não vira nota real."
            )
    segredo = (segredo_webhook or "").strip() or None
    if segredo and (tipo != "stripe" or not segredo.startswith("whsec_") or len(segredo) > 200):
        raise ConectorError("O segredo de assinatura da Stripe começa com whsec_.")
    with sessao() as s:
        descricao = _conferir_opcoes(s, tipo, gatilho, tributacao_id, descricao)
        n = len(s.exec(select(ConectorEntrada).where(ConectorEntrada.empresa_id == empresa_id)).all())
        if n >= MAX_CONECTORES_POR_EMPRESA:
            raise ConectorError(f"Esta empresa já tem {MAX_CONECTORES_POR_EMPRESA} conectores. Remova os que não usa.")
    conferir_chave(tipo, chave, origem)
    caminho = secrets.token_urlsafe(18)
    token = secrets.token_urlsafe(32)
    webhook_id = None
    if tipo == "stripe" and not segredo and url_publica().startswith("https://"):
        webhook_id, segredo = _criar_endpoint_stripe(chave, caminho)
    # Sem endereço público e sem segredo: o administrador cria o endpoint no painel da Stripe com o endereço do
    # conector e cola o segredo depois (``segredo_pendente``); até lá os avisos são recusados.
    with sessao() as s:
        c = ConectorEntrada(
            empresa_id=empresa_id,
            tipo=tipo,
            nome=(nome or "").strip()[:80] or TIPOS[tipo],
            ambiente=AMBIENTES[ambiente],
            caminho=caminho,
            token_hash=_sha256(token),
            chave_enc=security.encrypt(chave.encode()),
            segredo_enc=security.encrypt(segredo.encode()) if segredo else None,
            webhook_origem_id=webhook_id,
            ambiente_origem=origem,
            gatilho=gatilho,
            tributacao_id=tributacao_id,
            descricao=descricao,
            criado_por=criado_por,
        )
        s.add(c)
        s.commit()
        s.refresh(c)
    return (token if tipo == "asaas" else None), conector_dict(c)


def _conector(s, empresa_id: int, conector_id: int) -> ConectorEntrada:
    c = s.get(ConectorEntrada, conector_id)
    if c is None or c.empresa_id != empresa_id:
        raise ConectorError("Conector não encontrado.", 404)
    return c


def listar(empresa_id: int) -> list[dict[str, Any]]:
    with sessao() as s:
        itens = s.exec(
            select(ConectorEntrada).where(ConectorEntrada.empresa_id == empresa_id).order_by(ConectorEntrada.id)
        ).all()
    return [conector_dict(c) for c in itens]


def alterar(empresa_id: int, conector_id: int, mudancas: dict[str, Any]) -> dict[str, Any]:
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        gatilho = mudancas.get("gatilho") or c.gatilho
        trib = mudancas["tributacao_id"] if "tributacao_id" in mudancas else c.tributacao_id
        valores = {
            "gatilho": gatilho,
            "tributacao_id": trib,
            "descricao": _conferir_opcoes(s, c.tipo, gatilho, trib, mudancas.get("descricao", c.descricao)),
        }
        tipo = c.tipo
        if mudancas.get("ativo") is not None:
            valores["ativo"] = bool(mudancas["ativo"])
        if mudancas.get("nome"):
            valores["nome"] = str(mudancas["nome"]).strip()[:80] or c.nome
        origem = c.ambiente_origem
    chave = (mudancas.get("chave") or "").strip()
    if chave:
        pela_chave = _origem_da_chave(tipo, chave)
        if (pela_chave and pela_chave != origem) or (tipo == "stripe" and pela_chave is None):
            raise ConectorError(f"A chave nova é de outro ambiente da conta {TIPOS[tipo]}. Cadastre outro conector.")
        conferir_chave(tipo, chave, origem)
        valores["chave_enc"] = security.encrypt(chave.encode())
    segredo = (mudancas.get("segredo_webhook") or "").strip()
    if segredo:
        if tipo != "stripe" or not segredo.startswith("whsec_") or len(segredo) > 200:
            raise ConectorError("O segredo de assinatura da Stripe começa com whsec_.")
        valores["segredo_enc"] = security.encrypt(segredo.encode())
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        for campo, valor in valores.items():
            setattr(c, campo, valor)
        s.add(c)
        s.commit()
        s.refresh(c)
    return conector_dict(c)


def novo_token(empresa_id: int, conector_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        if c.tipo != "asaas":
            raise ConectorError("Este conector não usa token: a Stripe assina cada aviso com o segredo do webhook.")
        c.token_hash = _sha256(token)
        s.add(c)
        s.commit()
    return token


def remover(empresa_id: int, conector_id: int) -> ConectorEntrada:
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
    if c.tipo == "stripe" and c.webhook_origem_id:
        try:  # o endpoint que o emissor criou na Stripe sai junto (sem ele, a Stripe tentaria entregar por dias)
            _stripe(security.decrypt(c.chave_enc).decode(), "DELETE", f"/webhook_endpoints/{c.webhook_origem_id}")
        except (ConectorError, _Temporario) as exc:
            log.warning("endpoint da Stripe não removido: %s", exc, extra={"conector_id": c.id})
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        for e in s.exec(select(EventoEntrada).where(EventoEntrada.conector_id == c.id)):
            s.delete(e)
        s.delete(c)
        s.commit()
    return c


def eventos(empresa_id: int, conector_id: int, limite: int = 50) -> list[dict[str, Any]]:
    with sessao() as s:
        _conector(s, empresa_id, conector_id)
        itens = s.exec(
            select(EventoEntrada)
            .where(EventoEntrada.conector_id == conector_id)
            .order_by(EventoEntrada.recebido_em.desc())  # type: ignore[union-attr]
            .limit(limite)
        ).all()
    return [evento_dict(e) for e in itens]


def reprocessar(empresa_id: int, evento_id: str) -> dict[str, Any]:
    with sessao() as s:
        e = s.get(EventoEntrada, evento_id)
        if e is None or e.empresa_id != empresa_id:
            raise ConectorError("Aviso não encontrado.", 404)
        if e.status not in ("erro", "ignorado"):
            raise ConectorError("Só um aviso com erro (ou ignorado) pode ser processado de novo.")
        e.status, e.mensagem, e.tentativas, e.proxima_em = "pendente", None, 0, agora()
        s.add(e)
        s.commit()
        s.refresh(e)
    processador().acordar()
    return evento_dict(e)


# --- entrada (rota pública) ---------------------------------------------------------------------------------------


def receber(tipo: str, caminho: str, cabecalhos: Mapping[str, str], corpo: bytes) -> dict[str, Any]:
    """Grava o aviso (Asaas ou Stripe) e devolve na hora (o processamento é em segundo plano)."""
    if tipo not in TIPOS:
        raise ConectorError("Conector não encontrado.", 404)
    with sessao() as s:
        c = s.exec(
            select(ConectorEntrada).where(ConectorEntrada.caminho == caminho, ConectorEntrada.tipo == tipo)
        ).first()
    if c is None:
        raise ConectorError("Conector não encontrado.", 404)
    if len(corpo) > MAX_CORPO:
        raise ConectorError("Aviso grande demais.", 413)
    if tipo == "stripe":
        segredo = security.decrypt(c.segredo_enc).decode() if c.segredo_enc else ""
        if not segredo or not _assinatura_stripe_ok(segredo, cabecalhos.get("stripe-signature"), corpo):
            log.warning("aviso da Stripe com assinatura errada", extra={"conector_id": c.id})
            raise ConectorError("Assinatura inválida (confira o segredo do webhook no conector).", 400)
    else:
        token = cabecalhos.get("asaas-access-token")
        if not token or not hmac.compare_digest(c.token_hash, _sha256(token)):
            log.warning("aviso do conector com token errado", extra={"conector_id": c.id})
            raise ConectorError("Token de autenticação inválido (confira o campo no webhook do Asaas).", 401)
    try:
        dados = json.loads(corpo)
    except ValueError as exc:
        raise ConectorError("O corpo do aviso não é JSON.") from exc
    if tipo == "stripe":
        evento, evt, pid, valor = _resumo_stripe(dados)
    else:
        if not isinstance(dados, dict) or not isinstance(dados.get("event"), str):
            raise ConectorError("Aviso sem o campo event.")
        pagamento = dados.get("payment") if isinstance(dados.get("payment"), dict) else {}
        pid = str(pagamento.get("id") or "")[:60] or None
        evento = dados["event"]
        evt = str(dados.get("id") or "")[:80] or f"{evento}:{pid or secrets.token_hex(8)}"
        valor = str(pagamento.get("value"))[:20] if pagamento.get("value") is not None else None
    eid = f"{c.id}:{evt}"
    with sessao() as s:
        if s.get(EventoEntrada, eid) is not None:
            return {"ok": True, "repetido": True}
        ativo = c.ativo
        s.add(
            EventoEntrada(
                id=eid,
                conector_id=c.id,
                empresa_id=c.empresa_id,
                evento=evento[:60],
                pagamento_id=pid,
                valor=valor,
                corpo=corpo.decode("utf-8", "replace"),
                status="pendente" if ativo else "ignorado",
                mensagem=None if ativo else "Conector desativado quando o aviso chegou.",
            )
        )
        conector = s.get(ConectorEntrada, c.id)
        conector.ultimo_evento_em = agora()
        s.add(conector)
        s.commit()
    if ativo:
        processador().acordar()
    return {"ok": True, "repetido": False}


def _resumo_stripe(dados: Any) -> tuple[str, str, str | None, str | None]:
    """``(evento, id do evento, id da fatura ou do Checkout, valor)`` de um evento da Stripe."""
    if not isinstance(dados, dict) or not isinstance(dados.get("type"), str):
        raise ConectorError("Evento da Stripe sem o campo type.")
    obj = (dados.get("data") or {}).get("object") if isinstance(dados.get("data"), dict) else None
    obj = obj if isinstance(obj, dict) else {}
    pid = str(obj.get("id") or "")[:60] or None
    evt = str(dados.get("id") or "")[:80] or f"{dados['type']}:{pid or secrets.token_hex(8)}"
    centavos = obj.get("amount_paid") if dados["type"] == "invoice.paid" else obj.get("amount_total")
    valor = None
    if isinstance(centavos, int):
        valor = f"{Decimal(centavos) / 100:.2f}"
    return dados["type"], evt, pid, valor


# --- processamento -----------------------------------------------------------------------------------------------


@dataclass
class _Cobranca:
    """O que a nota precisa de um pagamento. ``completar`` lê o que falta na origem (cliente, descrição) e só é
    chamado depois de conferir que a cobrança ainda não tem nota."""

    referencia: str
    valor: str
    completar: Callable[[], tuple[Cliente, str]]
    numero: str | None = None


def _texto(v: Any) -> str | None:
    return re.sub(r"\s+", " ", str(v)).strip() or None if v not in (None, "") else None


def _valor_decimal(v: Any, centavos: bool = False) -> str:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, TypeError) as exc:
        raise ConectorError("Cobrança sem valor.") from exc
    if centavos:
        d = d / 100
    if d <= 0:
        raise ConectorError("Cobrança com valor zero.")
    return f"{d.quantize(Decimal('0.01'))}"


def _gravar_cliente(s, novo: Cliente) -> tuple[str, str]:
    """Grava o cliente da cobrança (cliente que já existe só tem os campos vazios completados) e devolve
    ``(documento, nome)``. CNPJ sem endereço completo é completado pela Receita, sem trocar o que veio."""
    doc = novo.documento
    atual = obter_cliente(s, doc)
    base = atual or novo
    if len(doc) == 14 and not base.endereco_completo:
        r = clientes_service.consultar_cnpj(doc)
        if r.encontrado and r.cliente is not None:
            clientes_service.completar_vazios(novo, r.cliente)
    if atual is None:
        if not novo.nome:
            raise ConectorError("O cliente da cobrança não tem nome. Complete na origem e processe de novo.")
        novo.atualizado_em = agora()
        s.add(novo)
    elif clientes_service.completar_vazios(atual, novo):
        s.add(atual)
    s.commit()
    return doc, (atual.nome if atual is not None and atual.nome else novo.nome)


def _documento(valor: Any) -> str:
    doc = normalizar_documento(str(valor or "")) or ""
    return doc if doc and clientes_service.documento_valido(doc) else ""


# --- Asaas ---------------------------------------------------------------------------------------------------------


def _endereco(cli: dict[str, Any]) -> dict[str, Any]:
    cep = somente_digitos(str(cli.get("postalCode") or "")) or ""
    return {
        "cep": cep if len(cep) == 8 else None,
        "logradouro": (cli.get("address") or "").strip() or None,
        "numero": (str(cli.get("addressNumber") or "")).strip() or None,
        "complemento": (cli.get("complement") or "").strip() or None,
        "bairro": (cli.get("province") or "").strip() or None,
        "c_mun": codes.codigo_municipio(cli.get("cityName"), cli.get("state")),
    }


def _cliente_asaas(cli: dict[str, Any]) -> Cliente:
    if cli.get("foreignCustomer"):
        raise ConectorError("Cliente estrangeiro no Asaas: emita pela nota avulsa (tomador no exterior).")
    doc = _documento(cli.get("cpfCnpj"))
    if not doc:
        raise ConectorError("O cliente da cobrança não tem CPF/CNPJ válido no Asaas. Complete lá e processe de novo.")
    return Cliente(
        documento=doc,
        nome=(cli.get("name") or "").strip(),
        email=(cli.get("email") or "").strip() or None,
        fone=somente_digitos(str(cli.get("mobilePhone") or cli.get("phone") or "")) or None,
        inscricao_municipal=(cli.get("municipalInscription") or "").strip() or None,
        origem="asaas",
        **_endereco(cli),
    )


def _preparar_asaas(c: ConectorEntrada, ev: EventoEntrada, dados: dict[str, Any]) -> _Cobranca:
    if ev.evento not in GATILHOS["asaas"].get(c.gatilho, ()):
        if ev.evento in ("PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED"):
            raise _Ignorar("Estorno no Asaas: a nota não foi cancelada sozinha. Cancele-a se for o caso.")
        raise _Ignorar(f"Evento {ev.evento} não emite nota.")
    pagamento = dados.get("payment") or {}
    if pagamento.get("deleted"):
        raise _Ignorar("Cobrança excluída no Asaas.")
    if not ev.pagamento_id or not pagamento.get("customer"):
        raise ConectorError("Aviso sem a cobrança ou o cliente.")

    def completar() -> tuple[Cliente, str]:
        chave = security.decrypt(c.chave_enc).decode()
        cli = _asaas(chave, c.ambiente_origem, f"/customers/{pagamento['customer']}")
        return _cliente_asaas(cli), _texto(pagamento.get("description")) or ""

    return _Cobranca(
        ev.pagamento_id,
        _valor_decimal(pagamento.get("value")),
        completar,
        str(pagamento.get("invoiceNumber") or "") or None,
    )


# --- Stripe --------------------------------------------------------------------------------------------------------


def _documento_stripe(tax_ids: Any, metadados: list[Any]) -> str:
    for t in tax_ids if isinstance(tax_ids, list) else []:
        if isinstance(t, dict) and t.get("type") in ("br_cpf", "br_cnpj") and _documento(t.get("value")):
            return _documento(t.get("value"))
    for m in metadados:
        if isinstance(m, dict):
            for k in CHAVES_DOCUMENTO:
                if _documento(m.get(k)):
                    return _documento(m.get(k))
    return ""


def _endereco_stripe(e: dict[str, Any]) -> dict[str, Any]:
    """Endereço da Stripe (line1, line2, city, state, postal_code): a Stripe não tem número nem bairro separados;
    "Rua X, 100" vira logradouro + número."""
    linha = _texto(e.get("line1")) or ""
    numero = None
    m = re.match(r"^(.+?),\s*(\d[\w/-]{0,9}|s/?n)\b(.*)$", linha, re.IGNORECASE)
    if m:
        linha, numero = m.group(1).strip(), m.group(2).upper() if m.group(2).lower().startswith("s") else m.group(2)
        resto = m.group(3).strip(" ,-")
    else:
        resto = ""
    cep = somente_digitos(str(e.get("postal_code") or "")) or ""
    complemento = " ".join(x for x in (resto, _texto(e.get("line2")) or "") if x) or None
    return {
        "cep": cep if len(cep) == 8 else None,
        "logradouro": linha or None,
        "numero": numero,
        "complemento": complemento,
        "c_mun": codes.codigo_municipio(e.get("city"), e.get("state")),
    }


def _cliente_stripe(chave: str, obj: dict[str, Any], fatura: bool) -> Cliente:
    if fatura:
        det = {
            "name": obj.get("customer_name"),
            "email": obj.get("customer_email"),
            "phone": obj.get("customer_phone"),
            "address": obj.get("customer_address"),
            "tax_ids": obj.get("customer_tax_ids"),
        }
    else:
        det = obj.get("customer_details") if isinstance(obj.get("customer_details"), dict) else {}
    doc = _documento_stripe(det.get("tax_ids"), [obj.get("metadata")])
    cus: dict[str, Any] = {}
    cus_id = obj.get("customer") if isinstance(obj.get("customer"), str) else None
    if cus_id and (not doc or not det.get("name") or not det.get("address")):
        cus = _stripe(chave, "GET", f"/customers/{cus_id}?expand[]=tax_ids")
        tax = cus.get("tax_ids") if isinstance(cus.get("tax_ids"), dict) else {}
        doc = doc or _documento_stripe(tax.get("data"), [cus.get("metadata")])
    endereco = det.get("address") or cus.get("address") or {}
    endereco = endereco if isinstance(endereco, dict) else {}
    if endereco.get("country") and endereco["country"] != "BR":
        raise ConectorError(
            "Cliente com endereço fora do Brasil na Stripe: emita pela nota avulsa (tomador no exterior)."
        )
    if not doc:
        raise ConectorError(
            "O cliente do pagamento não tem CPF/CNPJ na Stripe. Peça o documento no Checkout (coleta de Tax ID) ou "
            "guarde no cliente (Tax ID br_cpf/br_cnpj ou metadata cpf_cnpj) e processe de novo."
        )
    return Cliente(
        documento=doc,
        nome=_texto(det.get("name") or cus.get("name")) or "",
        email=_texto(det.get("email") or cus.get("email")),
        fone=somente_digitos(str(det.get("phone") or cus.get("phone") or "")) or None,
        origem="stripe",
        **_endereco_stripe(endereco),
    )


def _preparar_stripe(c: ConectorEntrada, ev: EventoEntrada, dados: dict[str, Any]) -> _Cobranca:
    if ev.evento not in EVENTOS_STRIPE:
        raise _Ignorar(f"Evento {ev.evento} não emite nota.")
    if bool(dados.get("livemode")) != (c.ambiente_origem == "producao"):
        raise _Ignorar("Evento de outro modo da Stripe (teste × real) que o da chave do conector.")
    obj = (dados.get("data") or {}).get("object") or {}
    fatura = ev.evento == "invoice.paid"
    if fatura:
        centavos = obj.get("amount_paid")
        if not centavos:
            raise _Ignorar("Fatura paga sem valor (período de teste ou cupom de 100%).")
    else:
        if obj.get("mode") != "payment":
            raise _Ignorar("Checkout de assinatura: a nota sai pela fatura paga (invoice.paid).")
        if obj.get("invoice"):
            raise _Ignorar("Checkout com fatura: a nota sai pela fatura paga (invoice.paid).")
        if obj.get("payment_status") != "paid":
            raise _Ignorar("Pagamento ainda não compensado (boleto?): a nota sai quando a Stripe confirmar.")
        centavos = obj.get("amount_total")
    moeda = str(obj.get("currency") or "").lower()
    if moeda != "brl":
        raise ConectorError(
            f"Pagamento em {moeda.upper() or 'moeda desconhecida'}: emita pela nota avulsa (exportação)."
        )
    if not ev.pagamento_id:
        raise ConectorError("Evento sem o id da fatura ou do Checkout.")

    def completar() -> tuple[Cliente, str]:
        chave = security.decrypt(c.chave_enc).decode()
        cliente = _cliente_stripe(chave, obj, fatura)
        if fatura:
            linhas = (obj.get("lines") or {}).get("data") or []
            texto = _texto(obj.get("description")) or next(
                (_texto(x.get("description")) for x in linhas if isinstance(x, dict) and x.get("description")), None
            )
        else:
            itens = _stripe(chave, "GET", f"/checkout/sessions/{ev.pagamento_id}/line_items?limit=10").get("data") or []
            texto = "; ".join(t for t in (_texto(x.get("description")) for x in itens if isinstance(x, dict)) if t)
        return cliente, texto or ""

    numero = str(obj.get("number") or "") or None if fatura else None
    return _Cobranca(ev.pagamento_id, _valor_decimal(centavos, centavos=True), completar, numero)


# --- processamento comum -------------------------------------------------------------------------------------------


def _descricao(c: ConectorEntrada, texto: str, numero: str | None, referencia: str) -> str:
    # Chaves no texto da origem seriam lidas como variáveis (e a nota recusada): ficam como parênteses.
    cobranca = (texto or "").replace("{", "(").replace("}", ")")
    modelo = c.descricao or DESCRICAO_PADRAO
    texto = modelo.replace("{cobranca}", cobranca).strip()
    if not texto:
        texto = f"Serviço referente à cobrança {numero or referencia}".strip()
    return texto


def _processar(evento_id: str) -> None:
    from emissor.db import Emissao
    from emissor.services.emissao import LoteError, NotaAvulsa, ReferenciaEmUso, criar_avulsas, notas_da_referencia

    with sessao() as s:
        ev = s.get(EventoEntrada, evento_id)
        c = s.get(ConectorEntrada, ev.conector_id) if ev else None
    if ev is None or ev.status != "pendente":
        return
    status, mensagem, emissao_id, temporario = "erro", None, None, False
    try:
        if c is None or not c.ativo:
            raise _Ignorar("Conector removido ou desativado.")
        if auth.suspenso():
            raise ConectorError("Assinatura suspensa: a nota não foi emitida. Reative e processe o aviso de novo.")
        dados = json.loads(ev.corpo)
        cob = (_preparar_stripe if c.tipo == "stripe" else _preparar_asaas)(c, ev, dados)
        with empresas.usar(c.empresa_id), sessao() as s:
            cfg = obter_config(s)
            if cfg.ambiente != c.ambiente:
                raise ConectorError(
                    f"O conector emite em {NOME_AMBIENTE.get(c.ambiente)}, mas a empresa está em "
                    f"{NOME_AMBIENTE.get(cfg.ambiente)}. Troque o ambiente e processe de novo."
                )
            existentes = notas_da_referencia(s, [cob.referencia], cfg.ambiente)
            if existentes:
                raise ReferenciaEmUso(existentes)
            trib = s.get(Tributacao, c.tributacao_id) if c.tributacao_id else None
            if c.tributacao_id and trib is None:
                raise ConectorError("A tributação do conector foi excluída. Escolha outra no conector.")
            trib_nome, trib_dados = (trib.nome, trib.dados()) if trib else (None, None)
        novo, texto = cob.completar()
        with empresas.usar(c.empresa_id):
            with sessao() as s:
                doc, nome = _gravar_cliente(s, novo)
            nota = NotaAvulsa(
                nome=nome,
                documento=doc,
                valor=cob.valor,
                descricao=_descricao(c, texto, cob.numero, cob.referencia),
                tributacao_nome=trib_nome,
                tributacao=trib_dados,
                tributacao_id=c.tributacao_id,
            )
            lote = criar_avulsas([nota], referencias=[cob.referencia], origem=f"{c.tipo}:{c.id}")
            with sessao() as s:
                e = s.exec(select(Emissao).where(Emissao.lote_id == lote.id)).first()
                emissao_id = e.id if e else None
            auth.auditar("lote_criado", f"{TIPOS[c.tipo]} ({c.nome}): cobrança {cob.referencia}, lote {lote.id}")
        status = "emitida"
    except ReferenciaEmUso as exc:
        status, emissao_id = "repetida", next(iter(exc.existentes.values()), None)
        mensagem = "A cobrança já tem nota."
    except _Ignorar as exc:
        status, mensagem = "ignorado", str(exc)
    except _Temporario as exc:
        mensagem, temporario = str(exc), True
    except ConectorError as exc:
        mensagem = exc.mensagem
    except LoteError as exc:
        detalhes = [d if isinstance(d, str) else "; ".join(d.get("erros", [])) for d in exc.detalhes]
        mensagem = " ".join([exc.mensagem, *[d for d in detalhes if d]])[:1000]
    except Exception as exc:  # não pode derrubar a thread
        log.exception("falha ao processar aviso do conector", extra={"evento": evento_id})
        mensagem = f"Erro interno ({exc.__class__.__name__}); veja o log."
    with sessao() as s:
        ev = s.get(EventoEntrada, evento_id)
        ev.tentativas += 1
        ev.mensagem = mensagem
        ev.emissao_id = emissao_id
        if temporario and ev.tentativas <= len(ESPERAS):
            ev.proxima_em = agora() + timedelta(seconds=ESPERAS[ev.tentativas - 1])
        else:
            ev.status = status
            ev.processado_em = agora()
        s.add(ev)
        s.commit()
    log.log(
        logging.WARNING if status == "erro" else logging.INFO,
        "aviso do conector: %s",
        ev.status,
        extra={"evento": evento_id, "pagamento": ev.pagamento_id, "mensagem": mensagem, "emissao_id": emissao_id},
    )


class _Ignorar(Exception):
    pass


class Processador:
    """Thread que processa os avisos pendentes (um por vez, na ordem de chegada)."""

    def __init__(self) -> None:
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        self._ultima_limpeza = 0.0

    def iniciar(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="conectores", daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        self._acordar.set()

    def _loop(self) -> None:
        while True:
            self._acordar.wait(timeout=30)
            self._acordar.clear()
            try:
                self.processar_pendentes()
                if time.monotonic() - self._ultima_limpeza > 6 * 3600:
                    self._ultima_limpeza = time.monotonic()
                    limpar_antigos()
            except Exception:
                log.exception("falha no processamento dos conectores")
                time.sleep(5)

    def processar_pendentes(self, limite: int = 50) -> int:
        with sessao() as s:
            q = (
                select(EventoEntrada)
                .where(EventoEntrada.status == "pendente", EventoEntrada.proxima_em <= agora())
                .order_by(EventoEntrada.recebido_em)  # type: ignore[arg-type]
                .limit(limite)
            )
            ids = [e.id for e in s.exec(q)]
        for eid in ids:
            _processar(eid)
        return len(ids)


def limpar_antigos() -> int:
    limite = agora() - GUARDAR_EVENTOS
    with sessao() as s:
        antigos = s.exec(
            select(EventoEntrada).where(EventoEntrada.status != "pendente", EventoEntrada.recebido_em < limite)
        ).all()
        for e in antigos:
            s.delete(e)
        s.commit()
    return len(antigos)


_processador: Processador | None = None


def processador() -> Processador:
    global _processador
    if _processador is None:
        _processador = Processador()
    return _processador


def definir_processador(p: Processador | None) -> None:
    """Injeção para testes."""
    global _processador
    _processador = p
