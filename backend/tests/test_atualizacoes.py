"""Versão, novidades e atualização da instalação própria (sem internet: GitHub e download simulados)."""

from __future__ import annotations

import io
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from emissor import atualizacoes, config
from emissor.api.app import create_app

from .conftest import entrar_como_admin

RELEASES = [
    {
        "versao": "9.1.0",
        "data": "2030-01-02",
        "itens": ["Coisa nova."],
    },
    {"versao": "9.0.0", "data": "2030-01-01", "itens": []},
    {"versao": config.APP_VERSION, "data": "2026-10-31", "itens": ["Texto da release, não usado."]},
]


@pytest.fixture
def com_releases(monkeypatch):
    monkeypatch.setattr(atualizacoes, "ATIVA", True)
    monkeypatch.setattr(atualizacoes, "_buscar_releases", lambda: [dict(r) for r in RELEASES])


@pytest.fixture
def cliente():
    with TestClient(create_app(iniciar_worker=False)) as c:
        entrar_como_admin(c)
        yield c


def test_novidades_tem_a_versao_atual_e_estao_em_ordem():
    versoes = atualizacoes.historico_local()
    assert versoes[0]["versao"] == config.APP_VERSION, "acrescente a seção da versão nova em data/novidades.md"
    assert all(v["itens"] and v["data"] for v in versoes)
    numeros = [atualizacoes.versao_tupla(v["versao"]) for v in versoes]
    assert numeros == sorted(numeros, reverse=True)


def test_ler_novidades_junta_itens_de_varias_linhas():
    texto = (
        "# T\n<!-- ## 9.9.9 (01/01/2030)\n- não -->\n## 1.2.3 (05/06/2026)\n\n- um\n  continua\n- dois\n## 1.2.2\n- x\n"
    )
    assert atualizacoes.ler_novidades(texto) == [
        {"versao": "1.2.3", "data": "2026-06-05", "itens": ["um continua", "dois"]},
        {"versao": "1.2.2", "data": None, "itens": ["x"]},
    ]


def test_itens_da_release_so_da_secao_novidades():
    corpo = "## Novidades\r\n- A\r\n- B\r\n  mais\r\n\r\n## What's Changed\r\n* PR by @x\r\n"
    assert atualizacoes._itens_da_release(corpo) == ["A", "B mais"]
    assert atualizacoes._itens_da_release("## What's Changed\n- y") == []


def test_situacao_junta_historico_local_e_versoes_novas(com_releases, monkeypatch):
    monkeypatch.setenv("EMISSOR_INSTALACAO", "iniciar")
    sit = atualizacoes.situacao(admin=True)
    assert sit["novas"] == 2 and sit["ultima"] == "9.1.0" and sit["pode_atualizar"]
    assert [v["versao"] for v in sit["versoes"][:3]] == ["9.1.0", "9.0.0", config.APP_VERSION]
    atual = sit["versoes"][2]
    assert atual["atual"] and not atual["nova"]
    assert atual["itens"] == atualizacoes.historico_local()[0]["itens"]  # o texto local vale para a instalada
    assert not atualizacoes.situacao(admin=False)["pode_atualizar"]


def test_hospedagem_nao_consulta_o_github(com_releases, monkeypatch):
    monkeypatch.setenv("FLY_APP_NAME", "emp-teste")
    sit = atualizacoes.situacao(admin=True)
    assert sit["instalacao"] == "hospedagem" and sit["novas"] == 0 and not sit["pode_atualizar"]
    assert sit["versoes"][0]["versao"] == config.APP_VERSION


def test_github_fora_do_ar_mostra_so_o_historico(monkeypatch):
    import httpx

    def falha():
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(atualizacoes, "ATIVA", True)
    monkeypatch.setattr(atualizacoes, "_buscar_releases", falha)
    sit = atualizacoes.situacao(admin=True)
    assert sit["erro"] and sit["novas"] == 0 and sit["versoes"]


def test_rotas_de_atualizacao_no_iniciar(cliente, com_releases, monkeypatch):
    monkeypatch.setenv("EMISSOR_INSTALACAO", "iniciar")
    encerrou = []
    monkeypatch.setattr(atualizacoes, "encerrar_para_atualizar", lambda: encerrou.append(True))
    monkeypatch.setattr("threading.Timer", lambda _t, f: type("T", (), {"start": lambda self: f()})())
    r = cliente.put("/api/atualizacoes/automatica", json={"automatica": True})
    assert r.status_code == 200 and atualizacoes.automatica()
    assert cliente.get("/api/atualizacoes").json()["automatica"] is True
    r = cliente.post("/api/atualizacoes/aplicar")
    assert r.status_code == 200, r.text
    assert r.json() == {"de": config.APP_VERSION, "para": "9.1.0"}
    assert encerrou == [True]
    assert (config.DATA_DIR / "atualizar-agora").read_text() == "9.1.0"
    assert cliente.get("/api/atualizacoes").json()["atualizando"] is True


def test_aplicar_sem_versao_nova(cliente, monkeypatch):
    monkeypatch.setenv("EMISSOR_INSTALACAO", "iniciar")
    assert cliente.post("/api/atualizacoes/aplicar").status_code == 409


# --- troca do código --------------------------------------------------------------------------


def _zip_da_versao(versao: str, arquivos: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("emissor-em-planilha-x/iniciar.bat", "@echo off\r\n")
        zf.writestr("emissor-em-planilha-x/backend/pyproject.toml", f'[project]\nversion = "{versao}"\n')
        for nome, conteudo in arquivos.items():
            zf.writestr(f"emissor-em-planilha-x/{nome}", conteudo)
    return buf.getvalue()


@pytest.fixture
def instalacao(tmp_path):
    raiz = tmp_path / "inst"
    for nome, conteudo in {
        "iniciar.bat": "velho",
        "backend/pyproject.toml": 'version = "0.1.0"\n',
        "backend/src/emissor/velho.py": "x",
        "backend/src/emissor/igual.py": "igual",
        "backend/.venv/lib.py": "venv",
        "frontend/node_modules/a.js": "nm",
        "frontend/dist/index.html": "dist",
        "meu-script-local.txt": "do usuário",
    }.items():
        p = raiz / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(conteudo)
    dados = raiz / "data"
    dados.mkdir()
    with sqlite3.connect(dados / "emissor.db") as c:
        c.execute("create table t (x)")
        c.execute("insert into t values (42)")
    (dados / "secret.key").write_text("segredo")
    return atualizacoes.Instalacao(raiz=raiz, dados=dados)


def test_atualizar_troca_o_codigo_e_preserva_dados(instalacao):
    novo = _zip_da_versao(
        "9.1.0", {"backend/src/emissor/igual.py": "igual", "backend/src/emissor/novo.py": "n", "data/x": "nunca"}
    )
    atualizacoes.atualizar(instalacao, "9.1.0", baixar=lambda v: novo)
    raiz = instalacao.raiz
    assert (raiz / "iniciar.bat").read_bytes() == b"@echo off\r\n"
    assert (raiz / "backend/src/emissor/novo.py").exists()
    assert not (raiz / "backend/src/emissor/velho.py").exists()  # saiu da versão nova
    assert (raiz / "meu-script-local.txt").exists()  # fora das pastas do sistema nada é apagado
    for protegido in ("backend/.venv/lib.py", "frontend/node_modules/a.js", "frontend/dist/index.html"):
        assert (raiz / protegido).exists()
    assert not (instalacao.dados / "x").exists()
    assert (instalacao.dados / "secret.key").read_text() == "segredo"
    backups = instalacao.dados / "backups"
    banco = next(backups.glob("emissor-*.db"))
    with sqlite3.connect(banco) as c:
        assert c.execute("select x from t").fetchone() == (42,)
    with zipfile.ZipFile(next(backups.glob("codigo-*.zip"))) as zf:
        nomes = set(zf.namelist())
    assert "backend/src/emissor/velho.py" in nomes and not any(n.startswith(("data/", "backend/.venv")) for n in nomes)


def test_zip_de_outra_versao_e_recusado(instalacao):
    with pytest.raises(atualizacoes.AtualizacaoError, match="versão 9.0.0"):
        atualizacoes.atualizar(instalacao, "9.1.0", baixar=lambda v: _zip_da_versao("9.0.0", {}))
    assert (instalacao.raiz / "iniciar.bat").read_text() == "velho"


def test_zip_com_caminho_para_fora_e_recusado(instalacao):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x/../../fora.txt", "x")
    with pytest.raises(atualizacoes.AtualizacaoError):
        atualizacoes.atualizar(instalacao, "9.1.0", baixar=lambda v: buf.getvalue())


def test_falha_ao_gravar_volta_o_codigo_anterior(instalacao, monkeypatch):
    original = atualizacoes.aplicar_arquivos
    chamadas = []

    def quebra(inst, novos):
        chamadas.append(1)
        if len(chamadas) == 1:
            (inst.raiz / "iniciar.bat").write_text("meio gravado")
            raise OSError("disco cheio")
        return original(inst, novos)

    monkeypatch.setattr(atualizacoes, "aplicar_arquivos", quebra)
    with pytest.raises(OSError):
        atualizacoes.atualizar(instalacao, "9.1.0", baixar=lambda v: _zip_da_versao("9.1.0", {}))
    assert (instalacao.raiz / "iniciar.bat").read_text() == "velho"


def test_main_so_atualiza_quando_pedido_ou_automatico(monkeypatch):
    chamado = []
    monkeypatch.setattr(atualizacoes, "ultima_publicada", lambda: "9.1.0")
    monkeypatch.setattr(atualizacoes, "atualizar", lambda inst, v: chamado.append(v))
    assert atualizacoes.main() == 0 and chamado == []
    atualizacoes.pedir_atualizacao("9.1.0")
    assert atualizacoes.main() == atualizacoes.CODIGO_ATUALIZADO and chamado == ["9.1.0"]
    assert not (config.DATA_DIR / "atualizar-agora").exists()
    assert atualizacoes.ultimo_resultado()["ok"] is True
    atualizacoes.definir_automatica(True)
    assert atualizacoes.main() == atualizacoes.CODIGO_ATUALIZADO


def test_main_com_falha_abre_a_versao_de_antes(monkeypatch):
    def falha(inst, v):
        raise atualizacoes.AtualizacaoError("sem espaço")

    monkeypatch.setattr(atualizacoes, "ultima_publicada", lambda: "9.1.0")
    monkeypatch.setattr(atualizacoes, "atualizar", falha)
    atualizacoes.pedir_atualizacao("9.1.0")
    assert atualizacoes.main() == 0
    res = atualizacoes.ultimo_resultado()
    assert res["ok"] is False and res["para"] == "9.1.0" and "sem espaço" in res["mensagem"]
    assert not (config.DATA_DIR / "atualizar-agora").exists()
