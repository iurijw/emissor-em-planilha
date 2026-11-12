"""IBS/CBS nas configurações, tributações predefinidas, abas (com clientes exclusivos) e nota avulsa."""
# ruff: noqa: F811  (fixtures api/sefin importadas de test_api)

import sqlite3
from dataclasses import replace

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


def test_ibscbs_com_servico_que_exige_tipo_de_operacao(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    """17.12 (administração de bens e negócios de terceiros) exige tpOper com o IBS/CBS (E0903)."""
    dados = _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    fiscal = dados["fiscal"] | {"ibscbs": True, "c_trib_nac": "171201", "c_nbs": "114012100", "c_trib_mun": None}
    r = api.put("/api/config", json={"fiscal": fiscal})
    assert r.status_code == 400 and any("tipo de operação" in d for d in r.json()["detalhes"])
    r = api.put("/api/config", json={"fiscal": fiscal | {"ibscbs_tp_oper": "2"}})
    assert r.status_code == 200  # 2 e 3 pedem as NFS-e referenciadas, que vêm de cada nota avulsa
    r = api.put("/api/config", json={"fiscal": fiscal | {"ibscbs_tp_oper": "5"}})
    assert r.status_code == 200, r.text
    assert r.json()["fiscal"]["ibscbs_tp_oper"] == "5"
    assert [o["valor"] for o in api.get("/api/config/opcoes").json()["ibscbs_tp_oper"]] == ["5", "1", "4", "2", "3"]

    ids = [ln["id"] for ln in api.get("/api/tabela").json()["linhas"]]
    lote = _emitir(api, ids)
    assert lote["contagem"] == {"autorizada": 1}, lote
    assert b"<cIndOp>100301</cIndOp><tpOper>5</tpOper>" in sefin.enviadas[0]


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


def test_coerencia_de_aliquotas_e_retencoes(config_fiscal):
    from dataclasses import replace

    from emissor.services.tributacao import coerencia, normalizar

    def erros(base=config_fiscal, **t):
        return coerencia(normalizar(t)[0] | {"c_trib_nac": "171901"}, base)

    assert erros(tp_ret_issqn="2") == []
    assert any("MEI" in e for e in erros(replace(config_fiscal, op_simp_nac="2"), tp_ret_issqn="2"))
    simples_iss = replace(config_fiscal, reg_ap_trib_sn="1")
    assert any("1.80%" in e for e in erros(simples_iss, tp_ret_issqn="2", p_aliq_iss="1,5"))
    assert erros(simples_iss, tp_ret_issqn="2", p_aliq_iss="2,5") == []
    assert any("CST" in e for e in erros(cst_pis_cofins=None, p_aliq_pis="0.65"))
    assert any("alíquota do PIS" in e for e in erros(cst_pis_cofins="01", tp_ret_pis_cofins="5"))
    assert any("CSLL retida" in e for e in erros(cst_pis_cofins="01", tp_ret_pis_cofins="8"))
    assert any("E0720" in e for e in erros(cst_pis_cofins="01", tp_ret_pis_cofins="0", p_ret_csll="1"))
    assert any("E0712" in e for e in erros(replace(config_fiscal, p_tot_trib_sn=None)))
    assert normalizar({"p_ret_irrf": "abc"})[1]


def test_dps_com_aliquotas_e_retencoes(dps_input):
    from dataclasses import replace

    import pytest

    from emissor.nfse.dps_builder import DpsBuildError, build_dps
    from emissor.nfse.xsd import validar_dps

    cfg = replace(
        dps_input.config,
        reg_ap_trib_sn="1",
        tp_ret_issqn="2",
        cst_pis_cofins="01",
        p_aliq_pis="0.65",
        p_aliq_cofins="3.00",
        tp_ret_pis_cofins="2",
        p_ret_irrf="1.50",
        p_ret_cp="11.00",
    )
    with pytest.raises(DpsBuildError, match="alíquota do ISS"):
        build_dps(replace(dps_input, config=cfg))
    dps = build_dps(replace(dps_input, config=replace(cfg, p_aliq_iss="2.01")))
    assert validar_dps(dps) == []
    ns = {"n": "http://www.sped.fazenda.gov.br/nfse"}
    val = lambda c: dps.findtext(f".//n:{c}", namespaces=ns)  # noqa: E731
    assert (val("pAliq"), val("vBCPisCofins"), val("vPis"), val("vCofins")) == ("2.01", "450.00", "2.93", "13.50")
    assert (val("vRetCP"), val("vRetIRRF"), val("vRetCSLL"), val("tpRetPisCofins")) == ("49.50", "6.75", None, "2")
    # Sem retenção, a alíquota guardada não vai (o município define; E0625).
    dps = build_dps(replace(dps_input, config=replace(cfg, tp_ret_issqn="1", p_aliq_iss="2.01")))
    assert dps.findtext(".//n:pAliq", namespaces=ns) is None and validar_dps(dps) == []
    mei = build_dps(replace(dps_input, config=replace(dps_input.config, op_simp_nac="2", reg_ap_trib_sn=None)))
    assert mei.findtext(".//n:indTotTrib", namespaces=ns) == "0" and validar_dps(mei) == []


def test_cst_pis_cofins_tem_todos_os_codigos_do_xsd(dps_input):
    import re
    from dataclasses import replace

    from emissor.config import PACKAGE_DIR
    from emissor.nfse import codes
    from emissor.nfse.dps_builder import build_dps
    from emissor.nfse.xsd import validar_dps

    xsd = (PACKAGE_DIR / "nfse/xsd/v1_01/tiposSimples_v1.01.xsd").read_text(encoding="utf-8")
    tipo = xsd[xsd.index('name="TSTipoCST"') :]
    oficiais = re.findall(r'value="(\d{2})"', tipo[: tipo.index("</xs:simpleType>")])
    assert list(codes.CST_PIS_COFINS) == oficiais and "99" in oficiais
    dps = build_dps(replace(dps_input, config=replace(dps_input.config, cst_pis_cofins="99")))
    assert validar_dps(dps) == []


# Listas de códigos que a tela oferece x tipo enumerado do XSD 1.01. Uma lista digitada à mão que fique incompleta
# (como a do CST do PIS/COFINS até 2026-10-12) ou com código a mais falha aqui. C_IND_OP fica de fora: é de propósito
# só uma parte do Anexo C (ver codes.py).
LISTAS_DO_XSD = {
    "TP_AMB": "TSTipoAmbiente",
    "AMB_GER": "TSAmbGeradorNFSe",
    "TP_EMIT": "TSEmitenteDPS",
    "C_STAT": "TStat",
    "FIN_NFSE": "TSRTCFinNFSe",
    "OP_SIMP_NAC": "TSOpSimpNac",
    "REG_AP_TRIB_SN": "TSRegimeApuracaoSimpNac",
    "REG_ESP_TRIB": "TSRegEspTrib",
    "TRIB_ISSQN": "TSTribISSQN",
    "TP_RET_ISSQN": "TSTipoRetISSQN",
    "TP_IMUNIDADE": "TSTipoImunidadeISSQN",
    "TP_SUSP": "TSOpExigSuspensa",
    "TP_BM": "TBMISSQN",
    "TP_RET_PIS_COFINS": "TSTipoRetPISCofins",
    "CST_PIS_COFINS": "TSTipoCST",
    "IND_FINAL": "TSRTCIndFinal",
}


def test_listas_de_codigos_iguais_ao_xsd():
    import re

    from emissor.config import PACKAGE_DIR
    from emissor.nfse import codes

    xsd = "".join(p.read_text(encoding="utf-8") for p in (PACKAGE_DIR / "nfse/xsd/v1_01").glob("*.xsd"))
    for lista, tipo in LISTAS_DO_XSD.items():
        m = re.search(rf'<xs:simpleType name="{tipo}">(.*?)</xs:simpleType>', xsd, re.S)
        assert m, f"{tipo} não está no XSD"
        oficiais = re.findall(r'<xs:enumeration value="([^"]*)"', m.group(1))
        assert list(getattr(codes, lista)) == oficiais, lista


def test_coerencia_de_imunidade_suspensao_beneficio_e_grupos_do_ibscbs(config_fiscal):
    from emissor.nfse.models import tributacao_de
    from emissor.services.tributacao import coerencia

    base = tributacao_de(config_fiscal)

    def erros(**kw):
        return coerencia(base | kw, config_fiscal)

    assert any("E0592" in e for e in erros(trib_issqn="2"))
    assert erros(trib_issqn="2", tp_imunidade="3") == []
    assert any("Exportação" in e for e in erros(trib_issqn="3"))
    assert any("E0585" in e for e in erros(trib_issqn="4", tp_susp="1", n_processo="1" * 30))
    assert any("30 dígitos" in e for e in erros(tp_susp="1", n_processo="123"))
    assert erros(tp_susp="2", n_processo="1" * 30) == []
    # ME/EPP com ISS pelo Simples não tem benefício municipal (E0536), salvo 07.02/07.05
    simples = coerencia(base | {"n_bm": "1" * 14}, replace(config_fiscal, reg_ap_trib_sn="1"))
    assert any("E0536" in e for e in simples)
    assert erros(n_bm="1" * 14, p_red_bcbm="10.00") == []
    assert any("% ou em R$" in e for e in erros(n_bm="1" * 14, p_red_bcbm="10.00", v_red_bcbm="5.00"))
    ibs = {"ibscbs": True, "c_nbs": "113022100"}
    assert any("E0972" in e for e in erros(**ibs, ibscbs_c_class_trib="515001"))
    assert any("E0971" in e for e in erros(**ibs, ibscbs_p_dif_uf="1.00"))
    assert any("E0965" in e for e in erros(**ibs, ibscbs_c_class_trib="550016"))
    assert erros(**ibs, ibscbs_c_class_trib="550016", ibscbs_c_class_trib_reg="000001") == []


def test_nota_avulsa_com_desconto_e_informacoes_complementares(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    nota = {
        "documento": "11.222.333/0001-81",
        "valor": "1.000,00",
        "descricao": "parecer",
        "x_inf_comp": "Contrato 12/2026",
        "x_ped": "PED-1",
        "itens_pedido": ["10", " "],
        "v_desc_incond": "100,00",
        "v_desc_cond": "",
    }
    val = api.post("/api/avulsa/validar", json=nota).json()
    assert val["linhas"][0]["erros"] == [], val
    ruim = api.post("/api/avulsa/validar", json=nota | {"v_desc_incond": "1.000,00"}).json()
    assert any("E0431" in e for e in ruim["linhas"][0]["erros"])
    r = api.post("/api/avulsa", json=nota)
    assert r.status_code == 200, r.text
    api.worker.processar_lote(r.json()["id"])
    assert api.get(f"/api/lotes/{r.json()['id']}").json()["contagem"] == {"autorizada": 1}
    dps = sefin.enviadas[-1]
    assert b"<vDescIncond>100.00</vDescIncond>" in dps and b"<vDescCond>" not in dps
    assert b"<xPed>PED-1</xPed>" in dps and b"<xItemPed>10</xItemPed>" in dps
    assert b"<xInfComp>Contrato 12/2026</xInfComp>" in dps


def test_nota_avulsa_de_exportacao(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    comex = {
        "comex_md_prestacao": "1",
        "comex_vinc_prest": "0",
        "comex_tp_moeda": "220",
        "comex_mec_af_p": "01",
        "comex_mec_af_t": "01",
        "comex_mov_temp_bens": "1",
        "comex_mdic": "0",
    }
    externo = {
        "nif": "98-7654321",
        "nome": "EXAMPLE CONSULTING LLC",
        "c_pais": "us",
        "c_end_post": "10001",
        "cidade": "New York",
        "estado": "NY",
        "logradouro": "5th Avenue",
        "numero": "100",
        "bairro": "Manhattan",
    }
    nota = {
        "valor": "500,00",
        "descricao": "consultoria",
        "tomador_exterior": externo,
        "v_serv_moeda": "90,00",
        "tributacao": {"trib_issqn": "3", "c_pais_result": "US", "cst_pis_cofins": None, **comex},
    }
    val = api.post("/api/avulsa/validar", json=nota).json()
    assert val["problemas"] == [] and val["linhas"][0]["erros"] == [], val
    sem_moeda = api.post("/api/avulsa/validar", json=nota | {"v_serv_moeda": ""}).json()
    assert any("moeda estrangeira" in e for e in sem_moeda["linhas"][0]["erros"])
    incompleto = api.post("/api/avulsa/validar", json=nota | {"tomador_exterior": externo | {"cidade": ""}}).json()
    assert any("cidade" in e for e in incompleto["linhas"][0]["erros"])
    r = api.post("/api/avulsa", json=nota)
    assert r.status_code == 200, r.text
    api.worker.processar_lote(r.json()["id"])
    lote = api.get(f"/api/lotes/{r.json()['id']}").json()
    assert lote["contagem"] == {"autorizada": 1}, lote
    assert lote["emissoes"][0]["tomador_nome"] == "EXAMPLE CONSULTING LLC"
    dps = sefin.enviadas[-1]
    assert b"<NIF>98-7654321</NIF>" in dps and b"<cPais>US</cPais>" in dps and b"<comExt>" in dps
    # uma aba com tributação de exportação não emite pela tabela (falta o valor na moeda de cada nota)
    t = _tributacao(api, "Exportação", tp_ret_issqn="1", trib_issqn="3", c_pais_result="US", **comex)
    aba = api.post("/api/abas", json={"nome": "Exterior", "tributacao_id": t["id"]}).json()
    api.put(f"/api/tabela?aba_id={aba['id']}", json={"linhas": [LINHA_PADRAO]})
    linha = api.get(f"/api/tabela?aba_id={aba['id']}").json()["linhas"][0]
    val = api.post("/api/tabela/validar", json={"linha_ids": [linha["id"]]}).json()
    assert any("nota avulsa" in e for e in val["linhas"][0]["erros"])


def test_nota_avulsa_de_obra_com_deducao(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    obra = {"cep": "88010400", "logradouro": "Rua das Obras", "numero": "10", "bairro": "Centro"}
    trib = {"c_trib_nac": "070201", "c_nbs": "101011100"}
    nota = {**LINHA_PADRAO, "tributacao": trib, "obra": obra, "ajuste_bc_p": "40"}
    val = api.post("/api/avulsa/validar", json=nota).json()
    erros = val["linhas"][0]["erros"]
    # o prestador do exemplo é ME/EPP: com ISS pelo Simples só alguns serviços deduzem (070201 está entre eles)
    assert erros == [], val
    sem_obra = api.post("/api/avulsa/validar", json=nota | {"obra": None}).json()
    assert any("E0370" in e for e in sem_obra["linhas"][0]["erros"])
    r = api.post("/api/avulsa", json=nota)
    assert r.status_code == 200, r.text
    api.worker.processar_lote(r.json()["id"])
    lote = api.get(f"/api/lotes/{r.json()['id']}").json()
    assert lote["contagem"] == {"autorizada": 1}, lote
    assert b"<obra>" in sefin.enviadas[-1]
    # na tabela, o serviço de obra pede a nota avulsa
    t = _tributacao(api, "Obras", tp_ret_issqn="1", **trib)
    aba = api.post("/api/abas", json={"nome": "Obras", "tributacao_id": t["id"]}).json()
    api.put(f"/api/tabela?aba_id={aba['id']}", json={"linhas": [LINHA_PADRAO]})
    linha = api.get(f"/api/tabela?aba_id={aba['id']}").json()["linhas"][0]
    val = api.post("/api/tabela/validar", json={"linha_ids": [linha["id"]]}).json()
    assert any("nota avulsa" in e for e in val["linhas"][0]["erros"])


def test_nota_avulsa_com_intermediario(api, sefin, pfx_bytes, nfse_exemplo_bytes):
    _onboarding(api, pfx_bytes, nfse_exemplo_bytes)
    interm = {
        "documento": "11.444.777/0001-61",
        "nome": "INTERMEDIARIA EXEMPLO LTDA",
        "c_mun": "4205407",
        "cep": "88010-400",
        "logradouro": "Rua Exemplo",
        "numero": "1",
        "bairro": "Centro",
    }
    api.put(
        "/api/clientes/11222333000181",
        json={
            "nome": "CLIENTE EXEMPLO LTDA",
            "c_mun": "4205407",
            "cep": "88010400",
            "logradouro": "Rua A",
            "numero": "1",
            "bairro": "Centro",
        },
    )
    nota = {**LINHA_PADRAO, "tributacao": {"tp_ret_issqn": "3"}, "intermediario": interm}
    sem = api.post("/api/avulsa/validar", json=nota | {"intermediario": None}).json()
    assert any("intermediário" in e for e in sem["linhas"][0]["erros"])
    val = api.post("/api/avulsa/validar", json=nota).json()
    assert val["linhas"][0]["erros"] == [], val
    r = api.post("/api/avulsa", json=nota)
    assert r.status_code == 200, r.text
    api.worker.processar_lote(r.json()["id"])
    assert api.get(f"/api/lotes/{r.json()['id']}").json()["contagem"] == {"autorizada": 1}
    assert b"<interm><CNPJ>11444777000161</CNPJ>" in sefin.enviadas[-1]
    t = _tributacao(api, "Intermediada", tp_ret_issqn="3")
    aba = api.post("/api/abas", json={"nome": "Interm", "tributacao_id": t["id"]}).json()
    api.put(f"/api/tabela?aba_id={aba['id']}", json={"linhas": [LINHA_PADRAO]})
    linha = api.get(f"/api/tabela?aba_id={aba['id']}").json()["linhas"][0]
    val = api.post("/api/tabela/validar", json={"linha_ids": [linha["id"]]}).json()
    assert any("nota avulsa" in e for e in val["linhas"][0]["erros"])
