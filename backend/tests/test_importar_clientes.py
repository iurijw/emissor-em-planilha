import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from emissor.api.app import create_app

from .conftest import SENHA_PFX


@pytest.fixture
def api():
    with TestClient(create_app(iniciar_worker=False)) as c:
        yield c


def _nota(base: bytes, cnpj: str, nome: str, dh: str, bairro: str = "CENTRO") -> bytes:
    return (
        base.replace(b"11222333000181", cnpj.encode())
        .replace(b"CLIENTE EXEMPLO LTDA", nome.encode())
        .replace(b"<dhProc>2026-08-07T17:24:23-03:00</dhProc>", f"<dhProc>{dh}</dhProc>".encode())
        .replace(b"<xBairro>CENTRO</xBairro></end>", f"<xBairro>{bairro}</xBairro></end>".encode())
    )


def test_cadastro_novo_zip_e_completar_sem_sobrescrever(api, nfse_exemplo_bytes):
    antiga = _nota(nfse_exemplo_bytes, "12345678000195", "NOME ANTIGO", "2026-01-10T10:00:00-03:00", "BAIRRO ANTIGO")
    nova = _nota(nfse_exemplo_bytes, "12345678000195", "NOME NOVO", "2026-08-10T10:00:00-03:00", "BAIRRO NOVO")
    outra = _nota(nfse_exemplo_bytes, "98765432000198", "OUTRO CLIENTE", "2026-08-01T10:00:00-03:00")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pasta/antiga.xml", antiga)
        z.writestr("pasta/outra.xml", outra)
        z.writestr("pasta/leia-me.txt", "x")
    r = api.post(
        "/api/clientes/importar-xmls",
        files=[
            ("arquivos", ("nova.xml", nova)),
            ("arquivos", ("notas.zip", buf.getvalue())),
            ("arquivos", ("x.pdf", b"%PDF")),
        ],
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["notas_lidas"] == 3
    assert {c["documento"] for c in d["novos"]} == {"12345678000195", "98765432000198"}
    assert any(i["arquivo"] == "x.pdf" for i in d["ignorados"])
    c = api.get("/api/clientes/12345678000195").json()
    assert (c["nome"], c["bairro"]) == ("NOME NOVO", "BAIRRO NOVO")  # nota mais recente prevalece

    # Edição manual (sem e-mail) + nova importação: nada editado é sobrescrito; vazios são completados.
    api.put("/api/clientes/12345678000195", json={**c, "nome": "NOME EDITADO", "email": None, "fone": None})
    com_contato = nova.replace(b"</xNome><end>", b"</xNome><end>", 1).replace(
        b"<xBairro>BAIRRO NOVO</xBairro></end>", b"<xBairro>OUTRO</xBairro></end><fone>4830000001</fone>"
    )
    d = api.post("/api/clientes/importar-xmls", files=[("arquivos", ("n.xml", com_contato))]).json()
    assert [c["documento"] for c in d["completados"]] == ["12345678000195"]
    c = api.get("/api/clientes/12345678000195").json()
    assert c["nome"] == "NOME EDITADO" and c["bairro"] == "BAIRRO NOVO" and c["fone"] == "4830000001"


def test_nota_recebida_pelo_proprio_prestador_e_ignorada(api, nfse_exemplo_bytes, pfx_bytes):
    api.post("/api/certificado", files={"arquivo": ("c.pfx", pfx_bytes)}, data={"senha": SENHA_PFX})
    xmls = api.post("/api/onboarding/xmls", files=[("arquivos", ("n.xml", nfse_exemplo_bytes))]).json()
    api.post("/api/onboarding/concluir", json={"fiscal": xmls["fiscal"], "ambiente": "2", "serie": 1})
    propria = nfse_exemplo_bytes.replace(b"<toma><CNPJ>11222333000181", b"<toma><CNPJ>11444777000161")
    r = api.post("/api/clientes/importar-xmls", files=[("arquivos", ("p.xml", propria))])
    d = r.json()
    assert d["novos"] == [] and "próprio prestador" in d["ignorados"][0]["motivo"]


def test_nenhum_xml_valido(api):
    r = api.post("/api/clientes/importar-xmls", files=[("arquivos", ("a.xml", b"<outro/>"))])
    assert r.status_code == 400
    assert "Nenhuma NFS-e válida" in r.json()["mensagem"]
    assert "a.xml" in r.json()["detalhes"][0]
