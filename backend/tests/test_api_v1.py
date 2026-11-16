"""Tokens da API e webhooks (regras que o teste ponta a ponta não exercita)."""

from __future__ import annotations

import httpx
import pytest
from sqlmodel import select

from emissor import tokens_api
from emissor.db import EntregaWebhook, Webhook, agora, sessao
from emissor.services import webhooks


def test_token_guarda_so_o_hash_e_pode_ser_revogado():
    token, dados = tokens_api.criar(1, "ERP", ["notas:ler"], "homologacao", None)
    assert token.startswith("emp_") and dados["prefixo"].endswith("_…")
    from emissor.db import TokenApi

    with sessao() as s:
        reg = s.exec(select(TokenApi)).one()
    assert token not in reg.hash and reg.prefixo in token
    acesso = tokens_api.resolver(token, "1.2.3.4")
    assert acesso.empresa_id == 1 and acesso.escopos == ("notas:ler",) and acesso.ambiente_token == "2"
    assert tokens_api.resolver(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    tokens_api.revogar(1, dados["id"], None)
    assert tokens_api.resolver(token) is None


def test_token_recusa_escopo_desconhecido_e_sem_nome():
    with pytest.raises(tokens_api.TokenError):
        tokens_api.criar(1, "ERP", ["notas:tudo"], "homologacao", None)
    with pytest.raises(tokens_api.TokenError):
        tokens_api.criar(1, " ", ["notas:ler"], "homologacao", None)
    with pytest.raises(tokens_api.TokenError):
        tokens_api.criar(1, "ERP", ["notas:ler"], "teste", None)


def test_limites_por_minuto_e_por_dia(monkeypatch):
    monkeypatch.setattr(tokens_api, "LIMITE_POR_MINUTO", 3)
    monkeypatch.setattr(tokens_api, "LIMITE_NOTAS_DIA", 5)
    assert [tokens_api.excedeu_limite(9) for _ in range(3)] == [None, None, None]
    assert tokens_api.excedeu_limite(9) > 0
    tokens_api.reservar_notas(9, 5)
    with pytest.raises(tokens_api.TokenError) as exc:
        tokens_api.reservar_notas(9, 1)
    assert exc.value.status == 429
    tokens_api.devolver_notas(9, 2)
    tokens_api.reservar_notas(9, 2)


def test_hospedagem_so_aceita_https_fora_da_rede_interna(monkeypatch):
    assert webhooks.conferir_url("http://192.168.0.10/hook") == "http://192.168.0.10/hook"  # instalação própria
    monkeypatch.setenv("FLY_APP_NAME", "emp-x")
    with pytest.raises(webhooks.WebhookError, match="https"):
        webhooks.conferir_url("http://exemplo.com.br/hook")
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.5", 443))])
    with pytest.raises(webhooks.WebhookError, match="rede interna"):
        webhooks.conferir_url("https://interno.exemplo.com.br/hook")
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("169.254.169.254", 443))])
    with pytest.raises(webhooks.WebhookError):
        webhooks.conferir_url("https://metadados.exemplo.com.br/")
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("200.160.2.3", 443))])
    assert webhooks.conferir_url("https://erp.exemplo.com.br/hook")
    with pytest.raises(webhooks.WebhookError, match="usuário"):
        webhooks.conferir_url("https://u:s@erp.exemplo.com.br/hook")


def test_servidor_publico_fora_da_hospedagem_tambem_protege_o_webhook(monkeypatch):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setenv("EMISSOR_PUBLICO", "1")
    with pytest.raises(webhooks.WebhookError, match="https"):
        webhooks.conferir_url("http://192.168.0.10/hook")
    monkeypatch.setattr(webhooks.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.0.0.5", 443))])
    with pytest.raises(webhooks.WebhookError, match="rede interna"):
        webhooks.conferir_url("https://interno.exemplo.com.br/hook")
    monkeypatch.setenv("EMISSOR_PUBLICO", "0")
    assert webhooks.conferir_url("http://192.168.0.10/hook") == "http://192.168.0.10/hook"


def test_entrega_que_falha_tenta_de_novo_e_desiste():
    respostas = iter([httpx.Response(500, text="fora do ar")] * 10)
    ent = webhooks.Entregador(transport=httpx.MockTransport(lambda r: next(respostas)))
    webhooks.definir_entregador(ent)
    _, w = webhooks.criar(1, "https://erp.exemplo.com.br/hook", ["lote.concluido"], "2")
    assert webhooks._registrar(1, "2", "lote.concluido", {"id": "x"}) == 1
    assert webhooks._registrar(1, "1", "lote.concluido", {"id": "x"}) == 0  # outro ambiente
    assert webhooks._registrar(1, "2", "nota.autorizada", {"id": "x"}) == 0  # evento não assinado
    for tentativa in range(1, len(webhooks.ESPERAS) + 2):
        assert ent.processar_pendentes() == 1
        with sessao() as s:
            en = s.exec(select(EntregaWebhook)).one()
            if en.status == "pendente":
                assert en.tentativas == tentativa and en.proxima_em > agora()
                en.proxima_em = agora()  # adianta o relógio
                s.add(en)
                s.commit()
    assert en.status == "falhou" and en.ultimo_status == 500 and "fora do ar" in en.ultimo_erro
    with sessao() as s:
        assert s.get(Webhook, w["id"]).falhas_seguidas == len(webhooks.ESPERAS) + 1
    assert ent.processar_pendentes() == 0
    webhooks.reenviar(1, en.id)
    with sessao() as s:
        assert s.get(EntregaWebhook, en.id).status == "pendente"
