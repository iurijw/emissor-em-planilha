"""Aceite da cobrança no fim do teste grátis (só na hospedagem).

O painel da hospedagem marca a assinatura nova para acabar no fim do teste; o emissor pergunta ao administrador se ele
quer continuar. Aceitar mantém a assinatura: a primeira cobrança acontece no fim do teste e as próximas são
automáticas; sem aceite, no fim do teste
a assinatura acaba e a instância fica suspensa (só consulta e download). "Continuar e assinar" funciona a qualquer
momento e com a instância suspensa por qualquer motivo: o painel paga a mensalidade em aberto ou cria outra assinatura
com o mesmo cartão e reativa a instância (ela reinicia); com o cartão recusado, devolve ``pagar_url`` (página do
Stripe para pagar com outro cartão).

Variáveis definidas pelo painel: ``EMISSOR_ASSINATURA_API`` (endereço desta instância no painel) e o mesmo
``EMISSOR_EMAIL_TOKEN`` do envio de e-mails. Sem elas (instalação própria), nada disso aparece.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import httpx

from emissor import config

log = logging.getLogger(__name__)

TIMEOUT = 15.0
# Situação guardada por pouco tempo: a tela consulta a cada abertura e o painel fala com o Stripe.
CACHE_SEGUNDOS = 120

_cache: tuple[float, dict[str, Any]] | None = None
_trava = threading.Lock()


class AssinaturaError(Exception):
    def __init__(self, mensagem: str, status: int = 502, cartao_recusado: bool = False, pagar_url: str | None = None):
        super().__init__(mensagem)
        self.status = status
        self.cartao_recusado = cartao_recusado
        # Cartão recusado: página do Stripe para pagar com outro cartão (fatura em aberto ou assinatura nova).
        self.pagar_url = pagar_url


def _env(nome: str) -> str:
    return os.environ.get(nome, "").strip()


def ligada() -> bool:
    return bool(_env("EMISSOR_ASSINATURA_API") and _env("EMISSOR_EMAIL_TOKEN"))


def _pedir(metodo: str, caminho: str = "") -> dict[str, Any]:
    try:
        r = httpx.request(
            metodo,
            _env("EMISSOR_ASSINATURA_API") + caminho,
            headers={"Authorization": f"Bearer {_env('EMISSOR_EMAIL_TOKEN')}", "User-Agent": config.VER_APLIC},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        log.warning("painel da assinatura fora do ar: %s", e)
        raise AssinaturaError("Não foi possível falar com o painel da assinatura. Tente de novo em instantes.") from e
    try:
        dados = r.json()
    except ValueError:
        dados = {}
    if r.status_code != 200:
        log.warning("painel da assinatura: %s %s → HTTP %s %s", metodo, caminho or "/", r.status_code, r.text[:300])
        mensagem = dados.get("erro") if isinstance(dados, dict) else None
        raise AssinaturaError(
            mensagem or f"O painel da assinatura respondeu com erro (HTTP {r.status_code}).",
            status=400 if r.status_code in (402, 409) else 502,
            cartao_recusado=bool(isinstance(dados, dict) and dados.get("cartao_recusado")),
            pagar_url=_link_stripe(dados.get("pagar_url") if isinstance(dados, dict) else None),
        )
    return dados if isinstance(dados, dict) else {}


def _link_stripe(url: Any) -> str | None:
    """Só links HTTPS do Stripe vão para a tela (o painel é confiável, mas o link abre fora do sistema)."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return None
    host = url[8:].split("/", 1)[0]
    return url if host.endswith(".stripe.com") or host == "stripe.com" else None


def limpar_cache() -> None:
    global _cache
    with _trava:
        _cache = None


def situacao(forcar: bool = False) -> dict[str, Any]:
    """Situação da assinatura no painel: ``{situacao, perguntar, teste_ate, valor, cartao, ...}``."""
    global _cache
    if not ligada():
        return {"disponivel": False}
    with _trava:
        if not forcar and _cache and time.monotonic() - _cache[0] < CACHE_SEGUNDOS:
            return _cache[1]
    try:
        dados = {"disponivel": True, **_pedir("GET")}
    except AssinaturaError:
        # Sem o painel, a tela simplesmente não pergunta nada agora.
        return {"disponivel": False}
    with _trava:
        _cache = (time.monotonic(), dados)
    return dados


def confirmar() -> dict[str, Any]:
    """Aceita a cobrança. No teste, ela fica para o fim dele (``cobranca_em``); depois do fim, cobra agora e
    ``reativar`` = a instância vai reiniciar sem a suspensão."""
    try:
        return _pedir("POST", "/confirmar")
    finally:
        limpar_cache()


def recusar() -> dict[str, Any]:
    try:
        return _pedir("POST", "/recusar")
    finally:
        limpar_cache()
