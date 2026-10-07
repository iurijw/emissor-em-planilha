import io

import httpx
import pytest
from openpyxl import Workbook

from emissor.services import clientes, tabela

BRASILAPI = {
    "razao_social": "EMPRESA EXEMPLO LTDA",
    "descricao_tipo_de_logradouro": "RUA",
    "logradouro": "DAS FLORES",
    "numero": "200",
    "complemento": "SALA 12",
    "bairro": "CENTRO",
    "cep": "88010400",
    "codigo_municipio_ibge": 4205407,
    "ddd_telefone_1": "4830000000",
    "email": None,
    "descricao_situacao_cadastral": "ATIVA",
    "qsa": [{"nome_socio": "NÃO DEVE SER GUARDADO"}],
}
CNPJA = {
    "razao_social": "EMPRESA EXEMPLO LTDA",
    "estabelecimento": {
        "tipo_logradouro": "RUA",
        "logradouro": "DAS FLORES",
        "numero": "200",
        "complemento": None,
        "bairro": "CENTRO",
        "cep": "88010400",
        "ddd1": "48",
        "telefone1": "30000000",
        "email": "x@y.com",
        "situacao_cadastral": "Ativa",
        "cidade": {"ibge_id": 4205407},
    },
}


def _http(rotas):
    def handler(req: httpx.Request):
        for trecho, resp in rotas.items():
            if trecho in str(req.url):
                return resp() if callable(resp) else resp
        return httpx.Response(500)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_consulta_brasilapi():
    r = clientes.consultar_cnpj("11.222.333/0001-81", _http({"brasilapi": httpx.Response(200, json=BRASILAPI)}))
    assert r.encontrado and r.fonte == "brasilapi"
    c = r.cliente
    assert (c.nome, c.logradouro, c.c_mun, c.cep, c.fone) == (
        "EMPRESA EXEMPLO LTDA",
        "RUA DAS FLORES",
        "4205407",
        "88010400",
        "4830000000",
    )
    assert c.endereco_completo
    assert "NÃO DEVE" not in str(vars(c))


def test_fallback_para_cnpja_quando_brasilapi_cai():
    def cai():
        raise httpx.ConnectError("fora do ar")

    r = clientes.consultar_cnpj("11222333000181", _http({"brasilapi": cai, "cnpj.ws": httpx.Response(200, json=CNPJA)}))
    assert r.encontrado and r.fonte == "cnpja" and r.cliente.email == "x@y.com"


def test_cnpj_inexistente_e_servicos_fora():
    r = clientes.consultar_cnpj("11222333000181", _http({"brasilapi": httpx.Response(404)}))
    assert not r.encontrado and "não encontrado" in r.erro
    r = clientes.consultar_cnpj("11222333000181", _http({}))
    assert not r.encontrado and "indisponíveis" in r.erro
    r = clientes.consultar_cnpj("11222333000180")
    assert not r.encontrado and "inválido" in r.erro


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("1.234,56", "1234.56"),
        ("R$ 450", "450.00"),
        (1720, "1720.00"),
        (99.9, "99.90"),
        ("1234.5", "1234.50"),
        ("", ""),
    ],
)
def test_parse_valor(entrada, esperado):
    assert tabela.parse_valor(entrada) == esperado


def test_importar_xlsx_com_cnpj_numerico_e_cabecalho_alternativo():
    wb = Workbook()
    ws = wb.active
    ws.append(["Razão Social", "CPF/CNPJ", "Honorários", "Serviço"])
    ws.append(["EMPRESA", 3017190000148, 1720, "HONORARIOS"])  # Excel perdeu o zero à esquerda
    ws.append(["PESSOA", "529.982.247-25", "100,00", "consultoria"])
    buf = io.BytesIO()
    wb.save(buf)
    res = tabela.importar("planilha.xlsx", buf.getvalue())
    assert [(ln.documento, ln.valor) for ln in res.linhas] == [
        ("03017190000148", "1720.00"),
        ("52998224725", "100.00"),
    ]


def test_importar_csv_sem_cabecalho_e_valor_invalido():
    res = tabela.importar("t.csv", b"EMPRESA;11222333000181;abc;desc\n")
    assert res.linhas[0].valor == "" and "não reconhecido" in res.avisos[0]


def test_importar_sem_colunas_obrigatorias():
    with pytest.raises(tabela.ImportacaoError, match="CNPJ/CPF"):
        tabela.importar("t.csv", "Nome;Valor;Descrição;Outro\nA;1;b;c\n".encode())
