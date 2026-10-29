"""Aceite da cobrança no fim do teste (hospedagem): o emissor conversa com o painel e registra na auditoria."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from emissor import assinatura
from emissor.api.app import create_app

from .conftest import entrar_como_admin

API = "https://painel.exemplo.com.br/api/instancias/escritorio/assinatura"


class PainelFalso:
    def __init__(self):
        self.chamadas: list[tuple[str, str, str]] = []
        self.respostas: dict[str, tuple[int, dict]] = {
            "GET": (
                200,
                {
                    "situacao": "teste",
                    "perguntar": True,
                    "teste_ate": "2026-11-02T20:39:00.000Z",
                    "cartao": "Visa final 4242",
                },
            ),
            "POST /confirmar": (200, {"ok": True, "reativar": False}),
            "POST /recusar": (200, {"ok": True}),
        }

    def __call__(self, metodo, url, headers, timeout):
        caminho = url.removeprefix(API)
        self.chamadas.append((metodo, caminho, headers["Authorization"]))
        status, corpo = self.respostas[metodo if not caminho else f"{metodo} {caminho}"]
        return httpx.Response(status, json=corpo, request=httpx.Request(metodo, url))


@pytest.fixture
def painel(monkeypatch):
    p = PainelFalso()
    monkeypatch.setattr(assinatura.httpx, "request", p)
    monkeypatch.setenv("EMISSOR_ASSINATURA_API", API)
    monkeypatch.setenv("EMISSOR_EMAIL_TOKEN", "token-da-instancia")
    return p


@pytest.fixture
def admin():
    with TestClient(create_app(iniciar_worker=False)) as c:
        entrar_como_admin(c)
        yield c


def _acoes(admin):
    return [i["acao"] for i in admin.get("/api/auditoria").json()["itens"]]


def test_situacao_e_cache(admin, painel):
    s = admin.get("/api/assinatura").json()
    assert s["disponivel"] is True and s["situacao"] == "teste" and s["pode_confirmar"] is True
    admin.get("/api/assinatura")
    assert len(painel.chamadas) == 1 and painel.chamadas[0][2] == "Bearer token-da-instancia"
    admin.get("/api/assinatura", params={"atualizar": True})
    assert len(painel.chamadas) == 2


def test_painel_fora_do_ar_nao_mostra_nada(admin, monkeypatch, painel):
    def falha(*a, **k):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(assinatura.httpx, "request", falha)
    assert admin.get("/api/assinatura").json()["disponivel"] is False


def test_confirmar_e_recusar(admin, painel):
    r = admin.post("/api/assinatura/recusar")
    assert r.status_code == 200, r.text
    r = admin.post("/api/assinatura/confirmar")
    assert r.status_code == 200 and r.json()["reativar"] is False
    assert [c[:2] for c in painel.chamadas] == [("POST", "/recusar"), ("POST", "/confirmar")]
    assert _acoes(admin)[:2] == ["assinatura_confirmada", "assinatura_nao_continuar"]


def test_cartao_recusado(admin, painel):
    painel.respostas["POST /confirmar"] = (
        402,
        {"erro": "O cartão foi recusado: Seu cartão foi recusado.", "cartao_recusado": True},
    )
    r = admin.post("/api/assinatura/confirmar")
    assert r.status_code == 400
    assert r.json()["cartao_recusado"] is True and "recusado" in r.json()["mensagem"]
    assert _acoes(admin)[0] == "assinatura_recusada"


def test_cartao_recusado_com_link_para_pagar(admin, painel):
    """Cartão recusado na retomada: a tela recebe a página do Stripe para pagar com outro cartão (só do Stripe)."""
    painel.respostas["POST /confirmar"] = (
        402,
        {
            "erro": "O cartão foi recusado.",
            "cartao_recusado": True,
            "pagar_url": "https://checkout.stripe.com/c/pay/cs_1",
        },
    )
    assert admin.post("/api/assinatura/confirmar").json()["pagar_url"] == "https://checkout.stripe.com/c/pay/cs_1"
    painel.respostas["POST /confirmar"] = (
        402,
        {"erro": "x", "cartao_recusado": True, "pagar_url": "https://golpe.exemplo/stripe.com"},
    )
    assert admin.post("/api/assinatura/confirmar").json()["pagar_url"] is None


def test_confirmar_com_a_instancia_suspensa(admin, painel, monkeypatch):
    """Depois do fim do teste a instância está suspensa, mas "Continuar e assinar" precisa funcionar."""
    monkeypatch.setenv("EMISSOR_SUSPENSO", "1")
    painel.respostas["POST /confirmar"] = (200, {"ok": True, "reativar": True})
    r = admin.post("/api/assinatura/confirmar")
    assert r.status_code == 200 and r.json()["reativar"] is True
    assert admin.post("/api/assinatura/recusar").status_code == 403


def test_operador_nao_confirma(admin, painel):
    r = admin.post("/api/usuarios", json={"email": "op@exemplo.com.br", "nome": "Op", "papel": "operador"})
    token = r.json()["link"].split("token=")[1]
    with TestClient(admin.app) as op:
        assert op.post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"}).status_code == 200
        assert op.get("/api/assinatura").json()["pode_confirmar"] is False
        assert op.post("/api/assinatura/confirmar").status_code == 403
    assert not any(c[1] == "/confirmar" for c in painel.chamadas)
