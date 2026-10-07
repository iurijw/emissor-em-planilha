"""Fluxo completo pela API: onboarding → tabela → lote → emissões → downloads."""

import io
import json
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from emissor.api.app import create_app
from emissor.nfse.client import SefinClient, gzip_b64
from emissor.services import emissao as emissao_service

from .conftest import SENHA_PFX


class SefinFalsa:
    """Autoriza toda DPS, devolvendo a NFS-e de exemplo; comportamento ajustável por CNPJ do tomador."""

    def __init__(self, nfse_xml: bytes):
        self.nfse_xml = nfse_xml
        self.rejeitar: set[str] = set()
        self.cair: set[str] = set()
        self.enviadas: list[bytes] = []
        self.numero = 100

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            import base64
            import gzip

            dps = gzip.decompress(base64.b64decode(json.loads(req.content)["dpsXmlGZipB64"]))
            self.enviadas.append(dps)
            for doc in self.cair:
                if doc.encode() in dps:
                    return httpx.Response(403, text="Forbidden")
            for doc in self.rejeitar:
                if doc.encode() in dps:
                    return httpx.Response(400, json={"erros": [{"Codigo": "E0999", "Descricao": "Tomador rejeitado"}]})
            self.numero += 1
            nfse = self.nfse_xml.replace(b"<nNFSe>6</nNFSe>", f"<nNFSe>{self.numero}</nNFSe>".encode())
            return httpx.Response(201, json={"chaveAcesso": "4" * 50, "nfseXmlGZipB64": gzip_b64(nfse)})
        return httpx.Response(404)


@pytest.fixture
def sefin(nfse_exemplo_bytes):
    return SefinFalsa(nfse_exemplo_bytes)


@pytest.fixture
def api(sefin):
    w = emissao_service.EmissionWorker(
        client_factory=lambda amb, cert: SefinClient(amb, cert, transport=httpx.MockTransport(sefin.handler)),
        dormir=lambda s: None,
    )
    emissao_service.definir_worker(w)
    with TestClient(create_app(iniciar_worker=False)) as c:
        c.worker = w
        yield c


def _onboarding(api, pfx_bytes, nfse_exemplo_bytes):
    r = api.post("/api/certificado", files={"arquivo": ("c.pfx", pfx_bytes)}, data={"senha": SENHA_PFX})
    assert r.status_code == 200, r.text
    assert r.json()["cnpj"] == "11444777000161"
    r = api.post("/api/onboarding/xmls", files=[("arquivos", ("n.xml", nfse_exemplo_bytes))])
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["fiscal"]["c_trib_nac"] == "171901"
    assert dados["linhas_sugeridas"][0]["documento"] == "11222333000181"
    r = api.post(
        "/api/onboarding/concluir",
        json={"fiscal": dados["fiscal"], "ambiente": "2", "serie": 1, "linhas": dados["linhas_sugeridas"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pronto_para_emitir"] is True
    return dados


def test_status_inicial(api):
    r = api.get("/api/status").json()
    assert r["onboarding_concluido"] is False
    assert r["pronto_para_emitir"] is False
    assert "Nenhum certificado digital carregado." in r["problemas"]


def test_certificado_senha_errada(api, pfx_bytes):
    r = api.post("/api/certificado", files={"arquivo": ("c.pfx", pfx_bytes)}, data={"senha": "x"})
    assert r.status_code == 400
    assert r.json()["mensagem"] == "Senha do certificado incorreta."


def test_fluxo_completo(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    tabela = api.get("/api/tabela").json()["linhas"]
    assert len(tabela) == 1 and tabela[0]["valor"] == "490.00"

    # Edita em massa: adiciona duas linhas (uma com CPF) e salva a tabela inteira
    linhas = tabela + [
        {
            "nome": "OUTRO CLIENTE LTDA",
            "documento": "11.444.777/0001-61",
            "valor": "1.234,50",
            "descricao": "honorarios",
        },
        {"nome": "PESSOA FISICA", "documento": "529.982.247-25", "valor": 100, "descricao": "consultoria"},
    ]
    tabela = api.put("/api/tabela", json={"linhas": linhas}).json()["linhas"]
    assert [ln["valor"] for ln in tabela] == ["490.00", "1234.50", "100.00"]
    ids = [ln["id"] for ln in tabela]

    val = api.post("/api/tabela/validar", json={"linha_ids": ids}).json()
    assert val["problemas"] == [] and val["total"] == "1824.50"
    assert all(not v["erros"] for v in val["linhas"])
    assert any("sem o endereço" in a for v in val["linhas"] for a in v["avisos"])

    lote = api.post("/api/lotes", json={"linha_ids": ids}).json()
    assert lote["status"] == "em_andamento" and lote["total"] == 3
    api.worker.processar_lote(lote["id"])

    lote = api.get(f"/api/lotes/{lote['id']}").json()
    assert lote["status"] == "concluido"
    assert lote["contagem"] == {"autorizada": 3}
    assert [e["n_dps"] for e in lote["emissoes"]] == [1, 2, 3]
    assert len(sefin.enviadas) == 3
    assert b"<CPF>52998224725</CPF>" in sefin.enviadas[2]
    assert b"<xLgr>RUA EXEMPLO</xLgr>" in sefin.enviadas[0]  # endereço vindo do cadastro importado

    cfg = api.get("/api/config").json()
    assert cfg["proximo_ndps_homologacao"] == 4 and cfg["proximo_ndps_producao"] == 1

    tabela = api.get("/api/tabela").json()["linhas"]
    assert all(ln["ultima_emissao"]["status"] == "autorizada" for ln in tabela)

    lista = api.get("/api/emissoes", params={"status": "autorizada"}).json()
    assert lista["total"] == 3 and lista["valor_autorizado"] == "1824.50"
    eid = lista["itens"][0]["id"]
    r = api.get(f"/api/emissoes/{eid}/xml")
    assert r.status_code == 200 and b"<NFSe" in r.content
    assert "NFSe-0103" in r.headers["content-disposition"]
    r = api.get(f"/api/emissoes/{eid}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")

    r = api.post("/api/emissoes/zip", json={"ids": [e["id"] for e in lista["itens"]], "conteudo": "ambos"})
    assert r.status_code == 200
    nomes = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len([n for n in nomes if n.startswith("xml/")]) == 3
    assert len([n for n in nomes if n.startswith("pdf/")]) == 3

    # Segunda emissão para o mesmo cliente/valor no mês gera aviso de duplicidade
    val = api.post("/api/tabela/validar", json={"linha_ids": [ids[0]]}).json()
    assert any("Já existe NFS-e" in a for a in val["linhas"][0]["avisos"])


def test_linhas_invalidas_bloqueiam_lote(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    tabela = api.put(
        "/api/tabela",
        json={
            "linhas": [
                {"nome": "X", "documento": "11111111111111", "valor": "10", "descricao": "a"},
                {"nome": "", "documento": "", "valor": "abc", "descricao": ""},
            ]
        },
    ).json()["linhas"]
    r = api.post("/api/lotes", json={"linha_ids": [ln["id"] for ln in tabela]})
    assert r.status_code == 400
    corpo = r.json()
    assert "2 linha(s) com erro" in corpo["mensagem"]
    erros = [e for d in corpo["detalhes"] for e in d["erros"]]
    assert any("CNPJ/CPF inválido" in e for e in erros)
    assert any("Valor inválido" in e for e in erros)
    assert any("Descrição" in e for e in erros)


def test_rejeicao_nao_interrompe_e_certificado_recusado_interrompe(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    linhas = [
        {"nome": "A", "documento": "11222333000181", "valor": "10", "descricao": "a"},
        {"nome": "B", "documento": "11444777000161", "valor": "20", "descricao": "b"},
        {"nome": "C", "documento": "52998224725", "valor": "30", "descricao": "c"},
        {"nome": "D", "documento": "11222333000181", "valor": "40", "descricao": "d"},
    ]
    ids = [ln["id"] for ln in api.put("/api/tabela", json={"linhas": linhas}).json()["linhas"]]
    sefin.rejeitar.add("11444777000161</CNPJ><xNome>B")
    sefin.cair.add("52998224725")
    lote = api.post("/api/lotes", json={"linha_ids": ids}).json()
    api.worker.processar_lote(lote["id"])
    lote = api.get(f"/api/lotes/{lote['id']}").json()
    assert [e["status"] for e in lote["emissoes"]] == ["autorizada", "rejeitada", "erro", "nao_enviada"]
    assert lote["status"] == "interrompido"
    assert "Certificado recusado" in lote["motivo_interrupcao"]
    rej = lote["emissoes"][1]["erro"]
    assert rej["tipo"] == "rejeicao" and rej["mensagens"][0]["codigo"] == "E0999"
    assert lote["emissoes"][3]["erro"]["resumo"].startswith("Não enviada")


def test_retomada_apos_queda_do_servidor(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    """Emissão ficou 'processando' (servidor caiu após gravar a DPS): retoma sem duplicar."""
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    ids = [ln["id"] for ln in api.get("/api/tabela").json()["linhas"]]
    lote = api.post("/api/lotes", json={"linha_ids": ids}).json()

    w = api.worker
    original = w._processar

    def cai_apos_preparar(e, client, cert, fiscal, amb):
        w._preparar(e, cert, fiscal, amb)  # grava DPS e reserva número, mas "cai" antes de enviar
        raise KeyboardInterrupt

    w._processar = cai_apos_preparar
    with pytest.raises(KeyboardInterrupt):
        w.processar_lote(lote["id"])
    w._processar = original

    e = api.get(f"/api/lotes/{lote['id']}").json()["emissoes"][0]
    assert e["status"] == "processando" and e["n_dps"] == 1

    from emissor.db import Lote, sessao

    with sessao() as s:  # o finally marcou o lote; simula estado real pós-queda
        lt = s.get(Lote, lote["id"])
        lt.status = "em_andamento"
        s.add(lt)
        s.commit()
    w.processar_lote(lote["id"])
    e = api.get(f"/api/lotes/{lote['id']}").json()["emissoes"][0]
    assert e["status"] == "autorizada" and e["n_dps"] == 1
    assert len(sefin.enviadas) == 1  # consultou /dps (404) e enviou a mesma DPS uma vez
    assert api.get("/api/config").json()["proximo_ndps_homologacao"] == 2


def test_importar_exportar_tabela(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    csv_bytes = "Nome;CNPJ;Valor;Descrição\nEMPRESA A;11.222.333/0001-81;R$ 1.500,00;honorários 09/2026\n".encode(
        "cp1252"
    )
    r = api.post("/api/tabela/importar", params={"modo": "acrescentar"}, files={"arquivo": ("t.csv", csv_bytes)})
    assert r.status_code == 200, r.text
    linhas = r.json()["linhas"]
    assert len(linhas) == 2 and linhas[1]["valor"] == "1500.00" and linhas[1]["descricao"] == "honorários 09/2026"

    r = api.get("/api/tabela/exportar", params={"formato": "xlsx"})
    assert r.status_code == 200
    r2 = api.post("/api/tabela/importar", files={"arquivo": ("t.xlsx", r.content)})
    assert [ln["documento"] for ln in r2.json()["linhas"]] == ["11222333000181", "11222333000181"]

    r = api.post("/api/tabela/importar", files={"arquivo": ("t.pdf", b"x")})
    assert r.status_code == 400 and "Formato não suportado" in r.json()["mensagem"]


def test_config_invalida(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    r = api.put("/api/config", json={"serie": 70000})
    assert r.status_code == 400 and "Série" in r.json()["mensagem"]
    fiscal = api.get("/api/config").json()["fiscal"]
    r = api.put("/api/config", json={"fiscal": {**fiscal, "c_trib_nac": "12", "c_mun_emissor": "9999999"}})
    assert r.status_code == 400
    assert len(r.json()["detalhes"]) == 2


def test_diagnostico_sem_segredos(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    r = api.get("/api/diagnostico")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    resumo = json.loads(z.read("resumo.json"))
    assert resumo["certificado"]["cnpj"] == "11444777000161"
    todo = b"".join(z.read(n) for n in z.namelist())
    assert SENHA_PFX.encode() not in todo


def test_rota_api_inexistente_retorna_json(api):
    r = api.get("/api/nao-existe")
    assert r.status_code == 404
