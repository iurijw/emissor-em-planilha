import io

import pytest
from pypdf import PdfReader

from emissor.nfse import codes
from emissor.nfse.danfse.render import cod_nbs, cod_trib_nac, gerar_danfse, moeda, pct

from .conftest import xmls_reais


def _texto(pdf: bytes) -> tuple[int, str]:
    r = PdfReader(io.BytesIO(pdf))
    return len(r.pages), " ".join(p.extract_text() for p in r.pages)


def test_danfse_pagina_unica_com_campos_obrigatorios(nfse_exemplo_bytes):
    paginas, t = _texto(gerar_danfse(nfse_exemplo_bytes))
    assert paginas == 1
    for esperado in [
        "DANFSe v2.0",
        "Documento Auxiliar da NFS-e",
        "CHAVE DE ACESSO DA NFS-E",
        "42054072211444777000161000000000000626085712554750",
        "11.444.777/0001-61",
        "11.222.333/0001-81",
        "CLIENTE EXEMPLO LTDA",
        "Florianópolis / SC",
        "17.19.01",
        "1.1302.21.00",
        "honorarios 07/2026",
        "R$ 490,00",
        "3,00%",
        "R$ 14,70",
        "Não Retido",
        "NFS-e Gerada",
        "Tipo de Ambiente: Produção",
        "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
        "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
        "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012",
        "Simples Nacional: 11,08%",
    ]:
        assert esperado in t, esperado
    assert "SEM VALIDADE" not in t
    assert "CANCELADA" not in t


def test_homologacao_e_cancelada(nfse_exemplo_bytes):
    xml = nfse_exemplo_bytes.replace(b"<tpAmb>1</tpAmb>", b"<tpAmb>2</tpAmb>")
    _, t = _texto(gerar_danfse(xml, cancelada=True))
    assert "NFS-e SEM VALIDADE JURÍDICA" in t
    assert "Tipo de Ambiente: Homologação" in t
    assert "CANCELADA" in t


def test_descricao_longa_continua_em_uma_pagina(nfse_exemplo_bytes):
    longa = ("Serviço de contabilidade mensal " * 70).strip().encode()
    xml = nfse_exemplo_bytes.replace(b"honorarios 07/2026", longa)
    paginas, t = _texto(gerar_danfse(xml))
    assert paginas == 1 and "..." in t


def test_formatadores():
    assert moeda("1720.00") == "R$ 1.720,00"
    assert moeda(None) == "-"
    assert pct("3.00") == "3,00%"
    assert cod_trib_nac("171901") == "17.19.01"
    assert cod_nbs("113022100") == "1.1302.21.00"
    assert codes.municipio_uf("4205407") == "Florianópolis / SC"
    assert codes.buscar_municipios("florianopolis")[0]["codigo"] == "4205407"


@pytest.mark.skipif(not xmls_reais(), reason="temp/ com XMLs reais não disponível")
def test_danfse_dos_xmls_reais():
    for p in xmls_reais():
        paginas, t = _texto(gerar_danfse(p.read_bytes()))
        assert paginas == 1, p.name
        assert "DANFSe v2.0" in t
