"""Códigos de serviço: lista nacional, código municipal e conferência na parametrização do município (ADN)."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from emissor.nfse import codes
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.errors import SefinError, TipoErro, dica_para
from emissor.services import tributacao as trib_service

COMPETENCIA = date(2026, 10, 27)


# --- lista nacional de serviços -------------------------------------------------------------------------


def test_busca_de_servicos_por_codigo_palavras_e_nbs():
    assert [s["codigo"] for s in codes.buscar_servicos("17.19")] == ["171901"]
    assert [s["codigo"] for s in codes.buscar_servicos("080101")] == ["080101"]
    assert codes.buscar_servicos("contabil")[0]["codigo"] == "171901"
    # sem acento e sem caixa; as palavras podem estar na descrição do serviço ou de um NBS dele
    assert "080101" in [s["codigo"] for s in codes.buscar_servicos("ENSINO MEDIO")]
    assert [s["codigo"] for s in codes.buscar_servicos("1.1302.21.00")] == ["171901"]
    assert codes.buscar_servicos("x") == []
    assert len(codes.buscar_servicos("servicos", limite=5)) == 5


def test_servico_com_nbs_e_sugestoes_de_classificacao():
    s = codes.servico("17.19.01")
    assert s["descricao"].startswith("Contabilidade")
    assert [n["codigo"] for n in s["nbs"]] == ["113022100", "113022200", "113022300"]
    assert s["nbs"][0]["c_ind_op"] == ["100301"]
    assert s["c_class_trib"][0]["codigo"] == "200052" and s["c_class_trib"][0]["reducao_ibs"] == 30
    assert codes.servico("999999") is None
    assert codes.formatar_c_trib_nac("171901") == "17.19.01"
    assert codes.formatar_nbs("113022100") == "1.1302.21.00"


def test_dados_dos_servicos_bem_formados():
    """O JSON gerado pelo script tem códigos nos formatos da DPS (cTribNac 6, NBS 9, cClassTrib 6 dígitos)."""
    dados = codes._servicos_nacionais()
    assert len(dados["servicos"]) > 300 and dados["fontes"]
    for s in dados["servicos"]:
        assert len(s["codigo"]) == 6 and s["codigo"].isdigit() and s["descricao"]
        assert all(len(n["codigo"]) == 9 and n["codigo"].isdigit() for n in s["nbs"])
        assert all(c in dados["classes"] for c in s["c_class_trib"])
    assert "000001" in dados["classes"]


# --- código de tributação municipal --------------------------------------------------------------------


def test_codigo_municipal_com_o_nacional_junto_vira_so_os_3_digitos():
    dados, erros = trib_service.normalizar({"c_trib_nac": "08.01.01", "c_trib_mun": "080101003"})
    assert erros == [] and dados["c_trib_nac"] == "080101" and dados["c_trib_mun"] == "003"
    assert trib_service.normalizar({"c_trib_nac": "080101", "c_trib_mun": "08.01.01.003"})[0]["c_trib_mun"] == "003"
    assert trib_service.normalizar({"c_trib_nac": "080101", "c_trib_mun": " "})[0]["c_trib_mun"] is None


@pytest.mark.parametrize(
    ("mun", "trecho"),
    [
        ("12", "deve ter 3 dígitos"),
        ("A01", "deve ter 3 dígitos"),
        ("171901003", "parece o código nacional 171901 seguido do municipal 003"),
    ],
)
def test_codigo_municipal_invalido(config_fiscal, mun, trecho):
    t = {**trib_service.tributacao_de(config_fiscal), "c_trib_nac": "080101", "c_trib_mun": mun}
    assert any(trecho in e for e in trib_service.coerencia(t)), trib_service.coerencia(t)
    assert trib_service.coerencia({**t, "c_trib_mun": "003"}) == []


def test_dica_da_e0312():
    assert "Conferir na prefeitura" in dica_para("E0312", "")


# --- parametrização do município (ADN) -----------------------------------------------------------------


def _client(certificado, respostas: list[httpx.Response], urls: list[str]) -> SefinClient:
    fila = list(respostas)

    def handler(req: httpx.Request) -> httpx.Response:
        urls.append(str(req.url))
        return fila.pop(0)

    return SefinClient(Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(handler))


def test_aliquota_municipal_encontrada(certificado):
    corpo = {
        "mensagem": None,
        "aliquotas": {
            "171901": [
                {"Incidencia": "M", "Aliq": 5, "DtIni": "2020-01-01T00:00:00", "DtFim": "2025-12-31T00:00:00"},
                {"Incidencia": "M", "Aliq": "2,5", "DtIni": "2026-01-01T00:00:00", "DtFim": None},
            ]
        },
    }
    urls: list[str] = []
    with _client(certificado, [httpx.Response(200, json=corpo)], urls) as c:
        r = c.aliquota_municipal("4205407", "171901", COMPETENCIA)
    assert r.encontrado is True and r.aliquota == "2.50"  # a vigente na competência
    assert urls == ["https://adn.producaorestrita.nfse.gov.br/parametrizacao/4205407/171901/2026-10-27/aliquota"]


def test_aliquota_municipal_nao_encontrada_tenta_o_formato_alternativo(certificado):
    urls: list[str] = []
    respostas = [
        httpx.Response(404, json={"mensagem": "Nenhuma alíquota encontrada.", "aliquotas": None}),
        httpx.Response(400, json={"mensagem": "Parâmetros inválidos."}),
    ]
    with _client(certificado, respostas, urls) as c:
        r = c.aliquota_municipal("4205407", "080101", COMPETENCIA)
    assert r.encontrado is False and r.mensagem == "Nenhuma alíquota encontrada."
    assert urls[1].endswith("/parametrizacao/4205407/08.01.01.000/10-27-2026/aliquota")
    # o alternativo achou: vale o que achou
    achou = {"aliquotas": {"080101000": [{"Aliq": 2.0, "DtIni": "2026-01-01"}]}}
    respostas = [httpx.Response(400, json={"mensagem": "x"}), httpx.Response(200, json=achou)]
    with _client(certificado, respostas, []) as c:
        assert c.aliquota_municipal("4205407", "080101", COMPETENCIA).aliquota == "2.00"


@pytest.mark.parametrize(
    "resposta",
    [
        httpx.Response(404, text="<html>Not Found</html>"),  # rota que não existe: não é "sem cadastro"
        httpx.Response(404, json={"title": "Not Found", "status": 404}),
        httpx.Response(400, json={"mensagem": "Município não conveniado."}),
    ],
)
def test_aliquota_municipal_sem_resposta_conclusiva(certificado, resposta):
    copia = httpx.Response(resposta.status_code, content=resposta.content, headers=resposta.headers)
    with _client(certificado, [resposta, copia], []) as c:
        assert c.aliquota_municipal("4205407", "171901", COMPETENCIA).encontrado is None


def test_aliquota_municipal_certificado_recusado(certificado):
    with _client(certificado, [httpx.Response(403, text="Forbidden")], []) as c, pytest.raises(SefinError) as exc:
        c.aliquota_municipal("4205407", "171901", COMPETENCIA)
    assert exc.value.tipo is TipoErro.AUTENTICACAO


def test_conferencia_por_municipio_e_cache(monkeypatch, certificado):
    from emissor.services import parametros

    monkeypatch.setattr(parametros, "ATIVA", True)
    monkeypatch.setattr(parametros.cert_service, "carregar", lambda: certificado)
    urls: list[str] = []
    corpo = json.dumps({"aliquotas": {"171901": [{"Aliq": 3, "DtIni": "2026-01-01"}]}}).encode()

    def fabrica(amb, cert):
        return _client(cert, [httpx.Response(200, content=corpo)] * 2, urls)

    c = parametros.conferir(Ambiente.PRODUCAO, "4205407", "171901", "4205407", COMPETENCIA, client_factory=fabrica)
    assert c.situacao == "encontrado" and "3,00%" in c.mensagem and "Florianópolis / SC" in c.mensagem
    assert "Produção Restrita" not in c.mensagem
    again = parametros.conferir(Ambiente.PRODUCAO, "4205407", "171901", None, COMPETENCIA, client_factory=fabrica)
    assert again == c and len(urls) == 1  # guardada por município, serviço e mês
    # ISS no domicílio do tomador (4.22) e prestação em outro município: não há como conferir
    assert parametros.conferir(Ambiente.PRODUCAO, "4205407", "042201", None, COMPETENCIA).situacao == "nao_conferido"
    outro = parametros.conferir(Ambiente.PRODUCAO, "4205407", "171901", "3550308", COMPETENCIA)
    assert outro.situacao == "nao_conferido" and len(urls) == 1
