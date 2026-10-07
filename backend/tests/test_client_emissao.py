import json

import httpx
import pytest

from emissor import config
from emissor.logging_setup import bind_context
from emissor.nfse.client import SefinClient, gzip_b64, ungzip_b64
from emissor.nfse.constants import Ambiente
from emissor.nfse.emissao import emitir, preparar_dps
from emissor.nfse.errors import SefinError, TipoErro, extrair_mensagens

CHAVE = "4205407" + "2" + "11444777000161" + "0" * 28


class FakeSefin:
    """Servidor falso: cada rota recebe uma lista de respostas/exceções em ordem."""

    def __init__(self, nfse_xml: bytes):
        self.nfse_xml = nfse_xml
        self.chamadas: list[tuple[str, str]] = []
        self.post: list = []
        self.get_dps: list = []

    def ok_emissao(self, status=201):
        return httpx.Response(
            status,
            json={
                "tipoAmbiente": 2,
                "idDps": "x",
                "chaveAcesso": CHAVE,
                "dataHoraProcessamento": "2026-09-23T10:31:00-03:00",
                "nfseXmlGZipB64": gzip_b64(self.nfse_xml),
                "alertas": [{"Codigo": "A001", "Descricao": "alerta de teste"}],
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.chamadas.append((request.method, request.url.path))
        path = request.url.path
        if request.method == "POST" and path.endswith("/nfse"):
            corpo = json.loads(request.content)
            assert ungzip_b64(corpo["dpsXmlGZipB64"]).startswith(b"<?xml")
            item = self.post.pop(0)
        elif "/dps/" in path:
            item = self.get_dps.pop(0) if self.get_dps else httpx.Response(404)
        elif "/nfse/" in path:
            item = self.ok_emissao(200)
        else:
            item = httpx.Response(500)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fake(nfse_exemplo_bytes):
    return FakeSefin(nfse_exemplo_bytes)


@pytest.fixture
def client(fake, certificado):
    c = SefinClient(Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(fake.handler))
    yield c
    c.close()


def _emitir(client, certificado, dps_input):
    return emitir(client, certificado, dps_input, dormir=lambda s: None)


def test_emissao_ok_e_arquivos_de_log(fake, client, certificado, dps_input):
    fake.post = [fake.ok_emissao()]
    with bind_context(emissao_id="e1"):
        r = _emitir(client, certificado, dps_input)
    assert r.chave_acesso == CHAVE and not r.recuperada
    assert r.nfse_xml == fake.nfse_xml
    assert r.alertas[0].codigo == "A001"
    arquivos = sorted(p.name for p in (config.SEFIN_LOG_DIR / "e1").iterdir())
    assert any(n.endswith("_emitir_req.xml") for n in arquivos)
    assert any(n.endswith("_emitir_resp.json") for n in arquivos)
    assert any(n.endswith("_emitir_nfse.xml") for n in arquivos)
    assert fake.chamadas[0][1] == "/SefinNacional/nfse"


def test_rejeicao_normalizada_sem_retentativa(fake, client, certificado, dps_input):
    fake.post = [
        httpx.Response(
            400,
            json={
                "erros": [
                    {"Codigo": "E0010", "Descricao": "A série informada não pertence à faixa", "Complemento": None}
                ]
            },
        )
    ]
    with pytest.raises(SefinError) as exc:
        _emitir(client, certificado, dps_input)
    err = exc.value
    assert err.tipo == TipoErro.REJEICAO and err.http_status == 400
    assert err.mensagens[0].codigo == "E0010"
    assert "49999" in err.mensagens[0].dica
    assert [m for m, _ in fake.chamadas] == ["POST"]


def test_certificado_recusado(fake, client, certificado, dps_input):
    fake.post = [httpx.Response(403, text="Forbidden")]
    with pytest.raises(SefinError) as exc:
        _emitir(client, certificado, dps_input)
    assert exc.value.tipo == TipoErro.AUTENTICACAO


def test_timeout_mas_nota_foi_gerada_recupera(fake, client, certificado, dps_input):
    fake.post = [httpx.ReadTimeout("timeout")]
    fake.get_dps = [httpx.Response(200, json={"chaveAcesso": CHAVE})]
    r = _emitir(client, certificado, dps_input)
    assert r.recuperada and r.chave_acesso == CHAVE
    assert [m for m, _ in fake.chamadas] == ["POST", "GET", "GET"]


def test_timeout_e_nota_nao_existe_reenvia_mesma_dps(fake, client, certificado, dps_input):
    fake.post = [httpx.ReadTimeout("timeout"), fake.ok_emissao()]
    r = _emitir(client, certificado, dps_input)
    assert not r.recuperada and r.tentativas == 2
    assert [m for m, _ in fake.chamadas] == ["POST", "GET", "POST"]


def test_erro_500_no_post_verifica_antes_de_reenviar(fake, client, certificado, dps_input):
    fake.post = [httpx.Response(503, text="Service Unavailable"), fake.ok_emissao()]
    r = _emitir(client, certificado, dps_input)
    assert r.tentativas == 2
    assert [m for m, _ in fake.chamadas] == ["POST", "GET", "POST"]


def test_rejeicao_por_duplicidade_recupera(fake, client, certificado, dps_input):
    fake.post = [httpx.Response(400, json=[{"codigo": "E9999", "descricao": "DPS já existe para este prestador"}])]
    fake.get_dps = [httpx.Response(200, json={"chaveAcesso": CHAVE})]
    r = _emitir(client, certificado, dps_input)
    assert r.recuperada


def test_falha_de_conexao_esgota_tentativas(fake, client, certificado, dps_input):
    fake.post = [httpx.ConnectError("sem rede")] * 3
    with pytest.raises(SefinError) as exc:
        _emitir(client, certificado, dps_input)
    assert exc.value.tipo == TipoErro.INDISPONIVEL
    assert [m for m, _ in fake.chamadas] == ["POST", "POST", "POST"]


def test_mesma_dps_em_todas_as_tentativas(fake, client, certificado, dps_input):
    enviados = []
    original = fake.handler

    def espiao(req):
        if req.method == "POST":
            enviados.append(req.content)
        return original(req)

    fake.handler = espiao
    c = SefinClient(Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(espiao))
    fake.post = [httpx.ConnectError("x"), fake.ok_emissao()]
    emitir(c, certificado, dps_input, dormir=lambda s: None)
    assert len(enviados) == 2 and enviados[0] == enviados[1]


def test_validacao_local_bloqueia_envio(fake, client, certificado, dps_input):
    from dataclasses import replace

    cfg = replace(dps_input.config, c_trib_nac="12")  # fora do padrão [0-9]{6}
    with pytest.raises(SefinError) as exc:
        preparar_dps(replace(dps_input, config=cfg), certificado)
    assert exc.value.tipo == TipoErro.VALIDACAO_LOCAL
    assert exc.value.mensagens[0].descricao.startswith("cTribNac")
    assert fake.chamadas == []


@pytest.mark.parametrize(
    "corpo,esperado",
    [
        ({"erros": [{"codigo": "E1", "descricao": "d1"}]}, [("E1", "d1")]),
        ({"Erro": {"Codigo": "E2", "Descricao": "d2"}}, [("E2", "d2")]),
        ([{"Codigo": "E3", "Mensagem": "d3"}], [("E3", "d3")]),
        ({"codigo": "E4", "mensagem": "d4"}, [("E4", "d4")]),
        ("texto puro", [(None, "texto puro")]),
        (None, []),
    ],
)
def test_formatos_de_erro(corpo, esperado):
    assert [(m.codigo, m.descricao) for m in extrair_mensagens(corpo)] == esperado


def test_pagina_html_403_real_da_sefin_vira_texto_com_dica(fake, client, certificado, dps_input):
    """Resposta real da produção restrita com certificado não aceito (servidor IIS devolve HTML)."""
    from .conftest import FIXTURES

    html = (FIXTURES / "sefin_403_iis.html").read_bytes()
    fake.post = [httpx.Response(403, content=html, headers={"content-type": "text/html"})]
    with pytest.raises(SefinError) as exc:
        _emitir(client, certificado, dps_input)
    err = exc.value
    assert err.tipo == TipoErro.AUTENTICACAO
    msg = err.mensagens[0]
    assert msg.descricao.startswith("403 - Forbidden: Access is denied.")
    assert "<" not in msg.descricao and "font-family" not in msg.descricao
    assert "ICP-Brasil" in msg.dica
