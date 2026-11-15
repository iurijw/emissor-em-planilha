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


# Relatório NF/RPA da Shopee (cabeçalho como o descrito nos guias: título antes, uma linha por venda, total no fim).
RELATORIO_SHOPEE = (
    "Relatório mensal NF/RPA - Comissão Extra;;;;;;;;\n"
    "Período: 01/10/2026 a 31/10/2026;;;;;;;;\n"
    "Nome da loja;CNPJ do vendedor;E-mail;CEP;Logradouro;Número;Bairro;Cidade;UF;Valor da venda;Comissão extra (R$)\n"
    "LOJA A;11.222.333/0001-81;contato@lojaa.com.br;88015-100;Rua das Flores;10;Centro;Florianópolis;SC;"
    "1.000,00;100,50\n"
    "LOJA B;03.017.190/0001-48;financeiro@lojab.com.br;;;;;;;500,00;20,00\n"
    "LOJA A;11.222.333/0001-81;;;;;;;;300,00;30,00\n"
    "Total;;;;;;;;;1.800,00;150,50\n"
)


def test_importar_relatorio_da_shopee_soma_por_marca():
    res = tabela.importar("relatorio.csv", RELATORIO_SHOPEE.encode("cp1252"))
    assert res.reconhecido == "shopee"
    assert [(ln.nome, ln.documento, ln.valor) for ln in res.linhas] == [
        ("LOJA A", "11222333000181", "130.50"),
        ("LOJA B", "03017190000148", "20.00"),
    ]
    assert res.linhas[0].descricao == tabela.DESCRICAO_SHOPEE
    assert res.linhas[0].cadastro == {
        "email": "contato@lojaa.com.br",
        "cep": "88015-100",
        "logradouro": "Rua das Flores",
        "numero": "10",
        "bairro": "Centro",
        "cidade": "Florianópolis",
        "uf": "SC",
    }
    assert "2 marca(s), total R$ 150,50" in res.informacoes[0]
    assert "1 linha(s) do mesmo CNPJ" in res.informacoes[1]
    assert not res.avisos


def test_planilha_com_coluna_valor_e_comissao_nao_vira_shopee():
    res = tabela.importar(
        "t.csv", "Cliente;CNPJ;Valor;Comissão\nA;11222333000181;100;10\nA;11222333000181;50;5\n".encode()
    )
    assert res.reconhecido is None
    assert [ln.valor for ln in res.linhas] == ["100.00", "50.00"]  # nada é somado fora do relatório da Shopee


def test_cliente_da_planilha():
    c = clientes.de_planilha("11222333000181", "LOJA A", {"cep": "88015-100", "cidade": "Florianópolis", "uf": "sc"})
    assert (c.cep, c.c_mun, c.origem) == ("88015100", "4205407", "planilha")
    assert clientes.pendente_receita(c) and clientes.pendente_receita(None)


# --- CEP -------------------------------------------------------------------------------------------------

CEP_BRASILAPI = {
    "cep": "88010400",
    "state": "SC",
    "city": "Florianópolis",
    "neighborhood": "Centro",
    "street": "Rua Felipe Schmidt",
    "service": "viacep",
}
CEP_VIACEP = {
    "cep": "88010-400",
    "logradouro": "Rua Felipe Schmidt",
    "bairro": "Centro",
    "localidade": "Florianópolis",
    "uf": "SC",
    "ibge": "4205407",
}


@pytest.fixture(autouse=True)
def _sem_cache_cep():
    clientes._CACHE_CEP.clear()
    yield
    clientes._CACHE_CEP.clear()


def test_cep_pela_brasilapi_com_codigo_ibge():
    r = clientes.consultar_cep("88010-400", _http({"brasilapi": httpx.Response(200, json=CEP_BRASILAPI)}))
    assert r.encontrado and r.fonte == "brasilapi"
    assert (r.logradouro, r.bairro, r.c_mun) == ("Rua Felipe Schmidt", "Centro", "4205407")


def test_cep_cai_para_viacep():
    def cai():
        raise httpx.ConnectError("fora do ar")

    r = clientes.consultar_cep("88010400", _http({"brasilapi": cai, "viacep": httpx.Response(200, json=CEP_VIACEP)}))
    assert r.encontrado and r.fonte == "viacep" and r.c_mun == "4205407"


def test_cep_nao_encontrado_e_indisponivel():
    r = clientes.consultar_cep(
        "99999999", _http({"brasilapi": httpx.Response(404), "viacep": httpx.Response(200, json={"erro": "true"})})
    )
    assert not r.encontrado and r.fonte and "não encontrado" in r.erro
    r = clientes.consultar_cep("88010401", _http({}))
    assert not r.encontrado and r.fonte is None and "indisponíveis" in r.erro
    assert not clientes.consultar_cep("8801").encontrado


def test_codigo_municipio_pelo_nome():
    from emissor.nfse import codes

    assert codes.codigo_municipio("FLORIANOPOLIS", "sc") == "4205407"
    assert codes.codigo_municipio("Herval d'Oeste", "SC") == codes.codigo_municipio("HERVAL DOESTE", "SC") is not None
    assert codes.codigo_municipio("Florianópolis", "SP") is None
