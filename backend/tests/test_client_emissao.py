import json

import httpx
import pytest
from lxml import etree

from emissor import config
from emissor.logging_setup import bind_context
from emissor.nfse.client import SefinClient, gzip_b64, ungzip_b64
from emissor.nfse.constants import Ambiente
from emissor.nfse.emissao import NumeracaoEmUso, emitir, preparar_dps
from emissor.nfse.errors import SefinError, TipoErro, extrair_mensagens

NS = "http://www.sped.fazenda.gov.br/nfse"
CHAVE = "4205407" + "2" + "11444777000161" + "0" * 28


class FakeSefin:
    """Servidor falso: cada rota recebe uma lista de respostas/exceções em ordem."""

    def __init__(self, nfse_xml: bytes):
        self.nfse_xml = nfse_xml
        self.chamadas: list[tuple[str, str]] = []
        self.post: list = []
        self.get_dps: list = []
        self.ultima_dps: bytes | None = None

    def nfse_da_ultima_dps(self) -> bytes:
        """A NFS-e de exemplo com a DPS que foi enviada (como a Sefin devolve a nota gerada por ela)."""
        nfse = etree.fromstring(self.nfse_xml)
        inf = nfse.find(f"{{{NS}}}infNFSe")
        antiga = inf.find(f"{{{NS}}}DPS")
        inf.replace(antiga, etree.fromstring(self.ultima_dps))
        return etree.tostring(nfse, xml_declaration=True, encoding="UTF-8")

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
            self.ultima_dps = ungzip_b64(corpo["dpsXmlGZipB64"])
            assert self.ultima_dps.startswith(b"<?xml")
            item = self.post.pop(0)
        elif "/dps/" in path:
            item = self.get_dps.pop(0) if self.get_dps else httpx.Response(404)
        elif "/nfse/" in path:
            item = httpx.Response(
                200, json={"chaveAcesso": CHAVE, "nfseXmlGZipB64": gzip_b64(self.nfse_da_ultima_dps())}
            )
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
    assert "Produção Restrita" in err.as_dict()["aviso"]  # o cliente é da homologação


def test_aviso_de_cadastro_so_em_recusa_da_homologacao(fake, certificado, dps_input):
    corpo = {"erros": [{"Codigo": "E0120", "Descricao": "Inscrição municipal do prestador não cadastrada no CNC"}]}
    for ambiente, tem_aviso in ((Ambiente.HOMOLOGACAO, True), (Ambiente.PRODUCAO, False)):
        fake.post = [httpx.Response(400, json=corpo)]
        with SefinClient(ambiente, certificado, transport=httpx.MockTransport(fake.handler)) as c:
            with pytest.raises(SefinError) as exc:
                _emitir(c, certificado, dps_input)
        d = exc.value.as_dict()
        assert ("aviso" in d) is tem_aviso
        assert "Dados da empresa" in d["mensagens"][0]["dica"]


def test_aviso_nas_recusas_ja_gravadas():
    from emissor.db import Emissao

    recusa = {"tipo": "rejeicao", "resumo": "Recusada", "mensagens": []}
    e = Emissao(lote_id="l", ambiente="2", tomador_documento="1", tomador_nome="x", valor="1", descricao="d")
    e.erro_json = json.dumps(recusa)
    assert "aviso" in e.erro()
    e.ambiente = "1"
    assert "aviso" not in e.erro()
    e.ambiente, e.erro_json = "2", json.dumps(recusa | {"tipo": "indisponivel"})
    assert "aviso" not in e.erro()
    # dica antiga gravada (genérica) é trocada pela atual do código
    antiga = {"codigo": "E0120", "descricao": "IM do prestador...", "dica": "Revise o código IBGE do município"}
    e.erro_json = json.dumps(recusa | {"mensagens": [antiga, {"codigo": "X9", "descricao": "y", "dica": "z"}]})
    msgs = e.erro()["mensagens"]
    assert "Dados da empresa" in msgs[0]["dica"] and msgs[1]["dica"] == "z"


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


def test_duplicidade_com_nota_de_outro_sistema_nao_e_adotada(fake, client, certificado, dps_input):
    """E0014 porque outro sistema já usou o número: a NFS-e dele não pode virar a nossa (bug da 0.1.0)."""
    fake.post = [
        httpx.Response(
            400,
            json={"erros": [{"Codigo": "E0014", "Descricao": "Conjunto de Série, Número ... já existe em uma NFS-e"}]},
        )
    ]
    fake.get_dps = [httpx.Response(200, json={"chaveAcesso": CHAVE})]
    original = fake.nfse_da_ultima_dps
    # mesma série e número, outro dia, outro valor
    fake.nfse_da_ultima_dps = lambda: _trocar(original())
    with pytest.raises(NumeracaoEmUso) as exc:
        _emitir(client, certificado, dps_input)
    m = exc.value.mensagens[0]
    assert m.codigo == "E0014" and "02/01/2020" in m.descricao and "série" in m.dica
    assert [m for m, _ in fake.chamadas] == ["POST", "GET", "GET"]  # não reenvia


def _trocar(nfse: bytes) -> bytes:
    root = etree.fromstring(nfse)
    root.find(f".//{{{NS}}}infDPS/{{{NS}}}dhEmi").text = "2020-01-02T10:00:00-03:00"
    root.find(f".//{{{NS}}}vServ").text = "999.00"
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


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


def test_dicas_de_inscricao_municipal_e_do_leiaute():
    from emissor.nfse.errors import dica_para, dica_xsd

    e0116 = "A IM deve ser informada para o emitente prestador do serviço na DPS"
    assert "Dados da empresa" in dica_para("E0116", e0116)
    assert "inscrição municipal" in dica_para(None, e0116)  # sem código: não cai na dica de município
    assert "3 dígitos" in dica_xsd("cTribMun: [facet 'pattern'] The value '080101003' is not accepted")
    assert "suporte" in dica_xsd("Element 'x': erro sem campo conhecido")
