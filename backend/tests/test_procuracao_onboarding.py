"""Começar a instalação como procurador: certificado de outro CNPJ ou e-CPF, com a empresa pelo CNPJ dela."""

from __future__ import annotations

import pytest

from .conftest import SENHA_PFX, _gerar_pfx
from .test_api import api, sefin  # noqa: F401 (fixtures)

CNPJ_EMPRESA = "11444777000161"  # a empresa das notas de exemplo
CNPJ_ESCRITORIO = "33444555000181"
CPF_PROCURADOR = "52998224725"


@pytest.fixture(scope="module")
def pfx_escritorio() -> bytes:
    return _gerar_pfx(CNPJ_ESCRITORIO, SENHA_PFX)


@pytest.fixture(scope="module")
def pfx_ecpf() -> bytes:
    return _gerar_pfx("", SENHA_PFX, cpf=CPF_PROCURADOR)


def _enviar(api, pfx):  # noqa: F811
    r = api.post("/api/certificado", files={"arquivo": ("c.pfx", pfx)}, data={"senha": SENHA_PFX})
    assert r.status_code == 200, r.text
    return r.json()


def _fiscal(api, nfse_exemplo_bytes):  # noqa: F811
    r = api.post("/api/onboarding/xmls", files=[("arquivos", ("n.xml", nfse_exemplo_bytes))])
    assert r.status_code == 200, r.text
    fiscal = r.json()["fiscal"]
    assert fiscal["cnpj"] == CNPJ_EMPRESA
    return fiscal


def _concluir(api, fiscal, **extra):  # noqa: F811
    return api.post("/api/onboarding/concluir", json={"fiscal": fiscal, "ambiente": "2", "serie": 1, **extra})


def test_onboarding_por_procuracao_com_certificado_de_outro_cnpj(api, pfx_escritorio, nfse_exemplo_bytes):  # noqa: F811
    assert api.get("/api/status").json()["procuracao_permitida"] is True
    assert _enviar(api, pfx_escritorio)["cnpj"] == CNPJ_ESCRITORIO
    fiscal = _fiscal(api, nfse_exemplo_bytes)

    # Dizendo que o certificado é da própria empresa: recusa com o caminho certo
    r = _concluir(api, fiscal, acesso="certificado")
    assert r.status_code == 400 and "sou procurador" in r.json()["mensagem"], r.text

    r = _concluir(api, fiscal, acesso="procuracao")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["pronto_para_emitir"] is True, st["problemas"]
    assert st["empresa"]["acesso"] == "procuracao" and st["empresa"]["cnpj"] == CNPJ_EMPRESA

    # Emite com o CNPJ da empresa no prestador
    r = api.put(
        "/api/tabela",
        json={"linhas": [{"nome": "Cliente", "documento": "11222333000181", "valor": "10.00", "descricao": "Serviço"}]},
    )
    lote = api.post("/api/lotes", json={"linha_ids": [r.json()["linhas"][0]["id"]]})
    assert lote.status_code == 200, lote.text
    api.worker.processar_lote(lote.json()["id"])
    emissao = api.get(f"/api/lotes/{lote.json()['id']}").json()["emissoes"][0]
    assert emissao["status"] == "autorizada", emissao

    # Em Configurações: voltar para "certificado da empresa" é recusado (o certificado é de outro CNPJ)
    r = api.put("/api/config", json={"acesso": "certificado"})
    assert r.status_code == 400, r.text


def test_sem_procuracao_o_certificado_de_outro_cnpj_bloqueia(api, pfx_escritorio, nfse_exemplo_bytes):  # noqa: F811
    _enviar(api, pfx_escritorio)
    fiscal = _fiscal(api, nfse_exemplo_bytes)
    r = _concluir(api, fiscal)  # tela antiga: sem escolher a forma de acesso
    assert r.status_code == 200, r.text
    assert r.json()["pronto_para_emitir"] is False
    assert any("sou procurador" in p for p in r.json()["problemas"])
    # Marcar depois, em Configurações → Certificado digital, libera a emissão
    r = api.put("/api/config", json={"acesso": "procuracao"})
    assert r.status_code == 200, r.text
    assert api.get("/api/status").json()["pronto_para_emitir"] is True


def test_ecpf_vira_procuracao_sozinho(api, pfx_ecpf, nfse_exemplo_bytes):  # noqa: F811
    info = _enviar(api, pfx_ecpf)
    assert info["cnpj"] is None and info["cpf"] == CPF_PROCURADOR and info["acesso"] == "procuracao"
    fiscal = _fiscal(api, nfse_exemplo_bytes)
    r = _concluir(api, fiscal, acesso="certificado")
    assert r.status_code == 400 and "pessoa física" in r.json()["mensagem"], r.text
    r = _concluir(api, fiscal, acesso="procuracao")
    assert r.status_code == 200 and r.json()["pronto_para_emitir"] is True, r.text


def test_procuracao_com_o_certificado_da_propria_empresa_e_recusada(api, pfx_bytes, nfse_exemplo_bytes):  # noqa: F811
    _enviar(api, pfx_bytes)
    fiscal = _fiscal(api, nfse_exemplo_bytes)
    r = _concluir(api, fiscal, acesso="procuracao")
    assert r.status_code == 400 and "própria empresa" in r.json()["mensagem"], r.text
    assert _concluir(api, fiscal, acesso="certificado").status_code == 200


def test_procuracao_desligada_na_hospedagem(api, pfx_escritorio, nfse_exemplo_bytes, monkeypatch):  # noqa: F811
    monkeypatch.setenv("EMISSOR_PROCURACAO", "0")
    assert api.get("/api/status").json()["procuracao_permitida"] is False
    _enviar(api, pfx_escritorio)
    fiscal = _fiscal(api, nfse_exemplo_bytes)
    r = _concluir(api, fiscal, acesso="procuracao")
    assert r.status_code == 400 and "não está disponível" in r.json()["mensagem"], r.text
    r = _concluir(api, fiscal, acesso="certificado")
    assert r.status_code == 400 and "e-CNPJ" in r.json()["mensagem"], r.text
    monkeypatch.setenv("EMISSOR_PROCURACAO", "1")
    assert _concluir(api, fiscal, acesso="procuracao").status_code == 200
