"""IBS/CBS nas configurações, tributações predefinidas, abas (com clientes exclusivos) e nota avulsa."""
# ruff: noqa: F811  (fixtures api/sefin importadas de test_api)

import sqlite3

from fastapi.testclient import TestClient

from emissor import config, db
from emissor.api.app import create_app

from .test_api import _onboarding, api, sefin  # noqa: F401 (fixtures)

LINHA_PADRAO = {
    "nome": "CLIENTE EXEMPLO LTDA",
    "documento": "11222333000181",
    "valor": "100",
    "descricao": "honorarios",
}


def _tributacao(api, nome="ISS retido", **dados):
    corpo = {"nome": nome, "dados": {"c_trib_nac": "010101", "tp_ret_issqn": "2", **dados}}
    r = api.post("/api/tributacoes", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


def _emitir(api, linha_ids):
    lote = api.post("/api/lotes", json={"linha_ids": linha_ids})
    assert lote.status_code == 200, lote.text
    api.worker.processar_lote(lote.json()["id"])
    return api.get(f"/api/lotes/{lote.json()['id']}").json()


def test_ibscbs_nas_configuracoes(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    dados = _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    fiscal = dados["fiscal"] | {"ibscbs": True, "ibscbs_c_ind_op": "999999"}
    r = api.put("/api/config", json={"fiscal": fiscal})
    assert r.status_code == 400 and any("Indicador da operação" in d for d in r.json()["detalhes"])
    r = api.put("/api/config", json={"fiscal": fiscal | {"ibscbs_c_ind_op": "100301", "c_nbs": ""}})
    assert r.status_code == 400 and any("NBS" in d for d in r.json()["detalhes"])
    r = api.put("/api/config", json={"fiscal": fiscal | {"ibscbs_c_ind_op": "100301"}})
    assert r.status_code == 200, r.text
    assert r.json()["fiscal"]["ibscbs"] is True
    opcoes = api.get("/api/config/opcoes").json()
    assert {"valor": "100301"}.items() <= opcoes["ibscbs_c_ind_op"][0].items()

    ids = [ln["id"] for ln in api.get("/api/tabela").json()["linhas"]]
    lote = _emitir(api, ids)
    assert lote["contagem"] == {"autorizada": 1}
    assert b"<cIndOp>100301</cIndOp>" in sefin.enviadas[0] and b"<CST>000</CST>" in sefin.enviadas[0]


def test_abas_com_tributacao_propria(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    abas = api.get("/api/abas").json()
    assert [a["nome"] for a in abas] == ["Notas"] and abas[0]["linhas"] == 1  # linha do onboarding adotada
    principal = abas[0]["id"]

    t = _tributacao(api, ibscbs=True, ibscbs_c_class_trib="200052")
    assert t["dados"]["c_trib_nac"] == "010101" and t["dados"]["c_nbs"] == "113022100"  # resto vem dos padrões
    r = api.post("/api/tributacoes", json={"nome": "ISS retido", "dados": {}})
    assert r.status_code == 400 and "Já existe" in r.json()["mensagem"]

    r = api.post("/api/abas", json={"nome": "Retenção", "tributacao_id": t["id"]})
    assert r.status_code == 200, r.text
    aba = r.json()
    assert aba["tributacao_nome"] == "ISS retido" and aba["linhas"] == 0

    # Cada aba tem a sua tabela: salvar uma não apaga a outra.
    r = api.put("/api/tabela", params={"aba_id": aba["id"]}, json={"linhas": [LINHA_PADRAO]})
    assert r.status_code == 200 and r.json()["aba_id"] == aba["id"]
    linha_aba = r.json()["linhas"][0]["id"]
    linha_principal = api.get("/api/tabela", params={"aba_id": principal}).json()["linhas"][0]["id"]
    assert api.get("/api/tabela").json()["aba_id"] == principal  # sem aba_id = primeira aba

    lote = _emitir(api, [linha_principal, linha_aba])
    assert lote["contagem"] == {"autorizada": 2}
    sem, com = sefin.enviadas
    assert b"<cTribNac>171901</cTribNac>" in sem and b"<IBSCBS>" not in sem
    assert b"<cTribNac>010101</cTribNac>" in com and b"<tpRetISSQN>2</tpRetISSQN>" in com
    assert b"<cClassTrib>200052</cClassTrib>" in com and b"<CST>200</CST>" in com
    assert [e["tributacao_nome"] for e in lote["emissoes"]] == [None, "ISS retido"]

    # Tributação em uso não pode ser excluída; a aba pode voltar aos padrões.
    r = api.delete(f"/api/tributacoes/{t['id']}")
    assert r.status_code == 400 and r.json()["detalhes"] == ["Aba “Retenção”"]
    r = api.put(f"/api/abas/{aba['id']}", json={"tributacao_id": None})
    assert r.status_code == 200 and r.json()["tributacao_nome"] is None
    assert api.delete(f"/api/tributacoes/{t['id']}").status_code == 200

    # Excluir a aba apaga as linhas dela; a última aba não pode ser excluída.
    assert api.delete(f"/api/abas/{aba['id']}").status_code == 200
    assert api.get("/api/tabela", params={"aba_id": aba["id"]}).status_code == 400
    assert api.delete(f"/api/abas/{principal}").status_code == 400


def test_aba_de_clientes_exclusivos(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    r = api.post("/api/abas", json={"nome": "Cliente Exemplo", "clientes": ["11.222.333/0001-81"]})
    assert r.status_code == 200, r.text
    aba = r.json()
    assert aba["clientes"] == [{"documento": "11222333000181", "nome": "CLIENTE EXEMPLO LTDA"}]
    outro = {"nome": "PESSOA", "documento": "529.982.247-25", "valor": "50", "descricao": "consultoria"}
    linhas = api.put("/api/tabela", params={"aba_id": aba["id"]}, json={"linhas": [LINHA_PADRAO, outro]}).json()
    ids = [ln["id"] for ln in linhas["linhas"]]
    val = api.post("/api/tabela/validar", json={"linha_ids": ids}).json()
    assert val["linhas"][0]["erros"] == []
    assert "só dos clientes" in val["linhas"][1]["erros"][0]
    # Na aba principal, o mesmo cliente gera só um aviso.
    principal = api.get("/api/tabela").json()["linhas"][0]["id"]
    val = api.post("/api/tabela/validar", json={"linha_ids": [principal]}).json()
    assert val["linhas"][0]["erros"] == [] and any("aba própria" in a for a in val["linhas"][0]["avisos"])


def test_nota_avulsa(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    t = _tributacao(api)
    nota = {"nome": "", "documento": "11.222.333/0001-81", "valor": "1.500,00", "descricao": "parecer"}

    val = api.post("/api/avulsa/validar", json=nota | {"tributacao_id": t["id"]}).json()
    assert val["problemas"] == [] and val["linhas"][0]["erros"] == [] and val["total"] == "1500.00"
    assert val["tributacao_nome"] == "ISS retido"
    ruim = api.post("/api/avulsa/validar", json=nota | {"documento": "123", "valor": "0"}).json()
    assert len(ruim["linhas"][0]["erros"]) == 3  # documento, nome e valor
    r = api.post("/api/avulsa", json=nota | {"valor": ""})
    assert r.status_code == 400 and r.json()["detalhes"]

    lotes = []
    for extra in ({"tributacao_id": t["id"]}, {"tributacao": {"c_trib_nac": "020101"}}, {}):
        r = api.post("/api/avulsa", json=nota | extra)
        assert r.status_code == 200, r.text
        api.worker.processar_lote(r.json()["id"])
        lotes.append(api.get(f"/api/lotes/{r.json()['id']}").json())
    assert all(lt["contagem"] == {"autorizada": 1} for lt in lotes)
    e = [lt["emissoes"][0] for lt in lotes]
    assert e[0]["tomador_nome"] == "CLIENTE EXEMPLO LTDA" and e[0]["linha_id"] is None
    assert [x["tributacao_nome"] for x in e] == ["ISS retido", "Só desta nota", None]
    assert b"<cTribNac>010101</cTribNac>" in sefin.enviadas[0]
    assert b"<cTribNac>020101</cTribNac>" in sefin.enviadas[1]
    assert b"<cTribNac>171901</cTribNac>" in sefin.enviadas[2]
    assert [n for n in (1, 2, 3)] == [x["n_dps"] for x in e]  # mesma numeração da tabela
    r = api.post("/api/avulsa", json=nota | {"tributacao": {"c_trib_nac": "12"}})
    assert r.status_code == 400 and "6 dígitos" in r.json()["detalhes"][0]
    acoes = [a["acao"] for a in api.get("/api/auditoria").json()["itens"]]
    assert acoes.count("nota_avulsa") == 3 and "tributacao_criada" in acoes


def test_operador_usa_mas_nao_cria_tributacao(api, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    t = _tributacao(api)
    link = api.post("/api/usuarios", json={"email": "op@exemplo.com.br", "nome": "Op", "papel": "operador"}).json()
    with TestClient(create_app(iniciar_worker=False)) as op:
        r = op.post("/api/auth/convite", json={"token": link["link"].split("token=")[1], "senha": "operador-senha-1"})
        assert r.status_code == 200
        assert op.post("/api/tributacoes", json={"nome": "X", "dados": {}}).status_code == 403
        assert op.delete(f"/api/tributacoes/{t['id']}").status_code == 403
        assert op.get("/api/tributacoes").json()["itens"][0]["nome"] == "ISS retido"
        r = op.post("/api/abas", json={"nome": "Mensais", "tributacao_id": t["id"]})
        assert r.status_code == 200 and r.json()["tributacao_nome"] == "ISS retido"


def test_migracao_acrescenta_colunas_em_banco_antigo():
    db.reset_engine()
    config.ensure_dirs()
    con = sqlite3.connect(config.DB_FILE)
    con.execute(
        "CREATE TABLE linha_tabela (id INTEGER PRIMARY KEY, ordem INTEGER, nome VARCHAR, documento VARCHAR, "
        "valor VARCHAR, descricao VARCHAR, ultima_emissao_id INTEGER, atualizado_em DATETIME)"
    )
    con.execute("INSERT INTO linha_tabela VALUES (1, 0, 'A', '11222333000181', '10.00', 'x', NULL, '2026-10-01')")
    con.commit()
    con.close()
    with db.sessao() as s:
        from emissor.services.tributacao import garantir_abas

        abas = garantir_abas(s)
        ln = s.get(db.LinhaTabela, 1)
        assert ln.aba_id == abas[0].id
