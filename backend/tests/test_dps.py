from dataclasses import replace
from decimal import Decimal

import pytest
from lxml import etree

from emissor.nfse.constants import NS_NFSE, Ambiente
from emissor.nfse.dps_builder import DpsBuildError, build_dps, id_dps, to_bytes
from emissor.nfse.emissao import preparar_dps
from emissor.nfse.signer import verificar
from emissor.nfse.xsd import validar_dps, validar_nfse

from .conftest import xmls_reais

N = f"{{{NS_NFSE}}}"


def _estrutura(el):
    """Sequência de caminhos de tags (sem valores) para comparar leiautes."""
    caminhos = []
    for e in el.iter():
        if not isinstance(e.tag, str):
            continue
        partes = []
        cur = e
        while cur is not None and cur is not el:
            partes.append(etree.QName(cur).localname)
            cur = cur.getparent()
        caminhos.append("/".join(reversed(partes)))
    return caminhos


def test_id_dps_segue_regra():
    assert id_dps("4205407", "11444777000161", 70000, 1) == "DPS420540721144477700016170000000000000000001"
    assert len(id_dps("4205407", "11444777000161", 1, 42)) == 45


def test_dps_valida_no_xsd(dps_input):
    dps = build_dps(dps_input)
    inf = dps.find(f"{N}infDPS")
    assert inf.get("Id") == "DPS420540721144477700016100001000000000000042"
    assert inf.findtext(f"{N}dCompet") == "2026-09-23"
    assert inf.findtext(f"{N}dhEmi") == "2026-09-23T10:30:00-03:00"
    assert inf.findtext(f"{N}valores/{N}vServPrest/{N}vServ") == "450.00"
    # sem assinatura o XSD acusa só a falta de Signature (se exigida) — validamos após assinar
    erros = [e for e in validar_dps(dps) if e.campo != "infDPS" and "Signature" not in e.mensagem]
    assert erros == []


def test_dps_mesma_estrutura_das_notas_atuais(dps_input, nfse_exemplo_bytes):
    """A DPS de produção deve ter exatamente os mesmos campos da DPS das notas emitidas pelo Emissor Web."""
    real = etree.fromstring(nfse_exemplo_bytes).find(f"{N}infNFSe/{N}DPS/{N}infDPS")
    gerada = build_dps(replace(dps_input, ambiente=Ambiente.PRODUCAO)).find(f"{N}infDPS")
    assert _estrutura(gerada) == _estrutura(real)


def test_dps_de_homologacao_segue_a_nt009(dps_input, nfse_exemplo_bytes):
    """Produção Restrita no leiaute da NT 009 (20/10/2026): finNFSe em infDPS e xDescServ no fim de cServ."""
    real = _estrutura(etree.fromstring(nfse_exemplo_bytes).find(f"{N}infNFSe/{N}DPS/{N}infDPS"))
    esperado = [c for c in real if c != "serv/cServ/xDescServ"]
    esperado.insert(esperado.index("cLocEmi"), "finNFSe")
    esperado.insert(esperado.index("serv/cServ/cNBS") + 1, "serv/cServ/xDescServ")
    gerada = build_dps(dps_input)
    assert _estrutura(gerada.find(f"{N}infDPS")) == esperado
    assert [e for e in validar_dps(gerada) if "Signature" not in e.mensagem] == []
    # o mesmo XML não passa no XSD de produção, e a DPS de produção não passa no da Produção Restrita
    assert validar_dps(gerada, leiaute="v1_01")
    producao = build_dps(replace(dps_input, ambiente=Ambiente.PRODUCAO))
    assert validar_dps(producao, leiaute="v1_01_nt009")


def test_assinatura_valida_sem_prefixo(dps_input, certificado):
    id_, xml = preparar_dps(dps_input, certificado)
    assert id_.startswith("DPS4205407")
    assert b'<Signature xmlns="http://www.w3.org/2000/09/xmldsig#">' in xml
    assert b"ds:" not in xml
    root = etree.fromstring(xml)
    assert [etree.QName(c).localname for c in root] == ["infDPS", "Signature"]
    assert f'URI="#{id_}"'.encode() in xml
    assert b"rsa-sha256" in xml and b"xml-exc-c14n#" in xml
    assert validar_dps(root) == []
    assert verificar(xml, certificado.cert_pem())


def test_serie_do_emissor_web_e_rejeitada(dps_input):
    with pytest.raises(DpsBuildError, match="70000"):
        build_dps(replace(dps_input, serie="70000"))


def test_valor_e_descricao_obrigatorios(dps_input):
    from decimal import Decimal

    with pytest.raises(DpsBuildError):
        build_dps(replace(dps_input, valor=Decimal("0")))
    with pytest.raises(DpsBuildError):
        build_dps(replace(dps_input, descricao="   "))


def test_sem_endereco_do_tomador_ainda_valida(dps_input, certificado):
    tomador = replace(dps_input.tomador, endereco=None)
    _, xml = preparar_dps(replace(dps_input, tomador=tomador), certificado)
    assert b"<end>" not in xml


def test_sem_piscofins_e_sem_ptottribsn(dps_input, certificado):
    cfg = replace(dps_input.config, cst_pis_cofins=None, p_tot_trib_sn=None)
    _, xml = preparar_dps(replace(dps_input, config=cfg), certificado)
    assert b"tribFed" not in xml and b"<indTotTrib>0</indTotTrib>" in xml


def test_cpf_como_tomador(dps_input, certificado):
    tomador = replace(dps_input.tomador, documento="52998224725")
    _, xml = preparar_dps(replace(dps_input, tomador=tomador), certificado)
    assert b"<CPF>52998224725</CPF>" in xml


def test_xml_serializado_utf8(dps_input):
    data = to_bytes(build_dps(dps_input))
    assert data.startswith(b"<?xml version='1.0' encoding='UTF-8'?>")


def test_nfse_exemplo_valida_no_xsd_corrigido(nfse_exemplo_bytes):
    assert validar_nfse(nfse_exemplo_bytes) == []


@pytest.mark.skipif(not xmls_reais(), reason="temp/ com XMLs reais não disponível")
def test_xmls_reais_validam_no_xsd():
    for p in xmls_reais():
        assert validar_nfse(p.read_bytes()) == [], p.name


def test_cnpj_alfanumerico_de_tomador_valida_no_xsd(dps_input, certificado):
    # XSD de 27/07/2026 (CNPJ alfanumérico em produção desde 10/08/2026).
    tomador = replace(dps_input.tomador, documento="12ABC34501DE35")
    _, xml = preparar_dps(replace(dps_input, tomador=tomador), certificado)
    assert b"<CNPJ>12ABC34501DE35</CNPJ>" in xml


def test_cnpj_alfanumerico_de_prestador_valida_no_xsd(dps_input):
    cfg = replace(dps_input.config, cnpj="12ABC34501DE35")
    dps = build_dps(replace(dps_input, config=cfg))
    assert dps.find(f"{N}infDPS").get("Id") == "DPS4205407212ABC34501DE3500001000000000000042"
    erros = [e for e in validar_dps(dps) if "Signature" not in e.mensagem]
    assert erros == []


# --- IBS/CBS (grupo infDPS/IBSCBS) ---------------------------------------------------------------


def _com_ibscbs(dps_input, **kw):
    return replace(dps_input, config=replace(dps_input.config, ibscbs=True, **kw))


def test_dps_com_ibscbs_valida_no_xsd(dps_input, certificado):
    inp = _com_ibscbs(dps_input, ibscbs_c_ind_op="100301", ibscbs_c_class_trib="000001", ibscbs_ind_final="0")
    _, xml = preparar_dps(replace(inp, ambiente=Ambiente.PRODUCAO), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    # O grupo vem depois de valores, e o CST é o prefixo do cClassTrib (E0959).
    assert [etree.QName(c).localname for c in inf][-2:] == ["valores", "IBSCBS"]
    g = inf.find(f"{N}IBSCBS")
    assert [etree.QName(c).localname for c in g] == ["finNFSe", "indFinal", "cIndOp", "indDest", "valores"]
    assert g.findtext(f"{N}finNFSe") == "0" and g.findtext(f"{N}indDest") == "0"
    trib = g.find(f"{N}valores/{N}trib/{N}gIBSCBS")
    assert trib.findtext(f"{N}CST") == "000" and trib.findtext(f"{N}cClassTrib") == "000001"


def test_dps_com_ibscbs_na_nt009(dps_input, certificado):
    inp = _com_ibscbs(dps_input, ibscbs_c_ind_op="100301", ibscbs_c_class_trib="200052", ibscbs_ind_final="0")
    _, xml = preparar_dps(inp, certificado)  # homologação
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    assert inf.findtext(f"{N}finNFSe") == "0"
    g = inf.find(f"{N}IBSCBS")
    assert [etree.QName(c).localname for c in g] == ["indFinal", "cIndOp", "indDest", "valores"]
    trib = g.find(f"{N}valores/{N}trib")
    assert [etree.QName(c).localname for c in trib] == ["CST", "cClassTrib"]
    assert trib.findtext(f"{N}CST") == "200" and trib.findtext(f"{N}cClassTrib") == "200052"


def test_dps_sem_ibscbs_por_padrao(dps_input):
    assert build_dps(dps_input).find(f"{N}infDPS/{N}IBSCBS") is None


def test_ibscbs_exige_nbs_e_recusa_servico_com_tp_oper(dps_input):
    with pytest.raises(DpsBuildError, match="NBS"):
        build_dps(_com_ibscbs(dps_input, c_nbs=None))
    with pytest.raises(DpsBuildError, match="tpOper"):
        build_dps(_com_ibscbs(dps_input, c_trib_nac="250501"))
    with pytest.raises(DpsBuildError, match="E0905"):
        build_dps(_com_ibscbs(dps_input, c_trib_nac="171201", ibscbs_tp_oper="2"))  # 2 exigiria gRefNFSe
    with pytest.raises(DpsBuildError, match="cClassTrib|Classificação"):
        build_dps(_com_ibscbs(dps_input, ibscbs_c_class_trib="12"))


def test_aplicar_tributacao_troca_so_campos_de_tributacao(config_fiscal):
    from decimal import Decimal

    from emissor.nfse.models import aplicar_tributacao, tributacao_de

    t = tributacao_de(config_fiscal) | {"c_trib_nac": "010101", "tp_ret_issqn": "2", "p_tot_trib_sn": "6.00"}
    t["cnpj"] = "00000000000000"  # campo do prestador: ignorado
    cfg = aplicar_tributacao(config_fiscal, t)
    assert cfg.c_trib_nac == "010101" and cfg.tp_ret_issqn == "2" and cfg.p_tot_trib_sn == Decimal("6.00")
    assert cfg.cnpj == config_fiscal.cnpj
    assert aplicar_tributacao(config_fiscal, None) is config_fiscal


def test_padroes_do_xml_trazem_ibscbs(nfse_exemplo_bytes):
    from emissor.nfse.xml_reader import NFSeDoc, extrair_padroes

    assert extrair_padroes([NFSeDoc.from_bytes(nfse_exemplo_bytes)]).ibscbs is False
    grupo = (
        b"<IBSCBS><finNFSe>0</finNFSe><cIndOp>100301</cIndOp><indDest>0</indDest><valores><trib><gIBSCBS>"
        b"<CST>200</CST><cClassTrib>200052</cClassTrib></gIBSCBS></trib></valores></IBSCBS></infDPS>"
    )
    xml = nfse_exemplo_bytes.replace(b"</infDPS>", grupo, 1)
    cfg = extrair_padroes([NFSeDoc.from_bytes(xml)])
    assert cfg.ibscbs is True and cfg.ibscbs_c_ind_op == "100301" and cfg.ibscbs_c_class_trib == "200052"


def test_producao_passa_a_nt009_em_01_12_2026(dps_input):
    """Comunicado do CGNFS-e de 20/10/2026: o leiaute atual vale em Produção até 30/11/2026."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from emissor.nfse.constants import leiaute

    sp = ZoneInfo("America/Sao_Paulo")
    antes = datetime(2026, 11, 30, 23, 59, 59, tzinfo=sp)
    depois = datetime(2026, 12, 1, 0, 0, 0, tzinfo=sp)
    assert leiaute(Ambiente.PRODUCAO, antes) == "v1_01"
    assert leiaute(Ambiente.PRODUCAO, depois) == "v1_01_nt009"
    assert leiaute(Ambiente.HOMOLOGACAO, antes) == "v1_01_nt009"

    inp = replace(dps_input, ambiente=Ambiente.PRODUCAO)
    velha = build_dps(replace(inp, dh_emi=antes))
    nova = build_dps(replace(inp, dh_emi=depois))
    assert velha.find(f"{N}infDPS/{N}finNFSe") is None
    assert nova.find(f"{N}infDPS/{N}finNFSe") is not None
    # cada DPS valida no XSD da sua data (o leiaute sai do tpAmb + dhEmi do próprio documento)
    assert [e for e in validar_dps(velha) if "Signature" not in e.mensagem] == []
    assert [e for e in validar_dps(nova) if "Signature" not in e.mensagem] == []


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_ibscbs_com_tp_oper_valida_no_xsd(dps_input, certificado, ambiente):
    inp = _com_ibscbs(dps_input, c_trib_nac="171201", c_nbs="114012100", ibscbs_c_ind_op="100301", ibscbs_tp_oper="5")
    _, xml = preparar_dps(replace(inp, ambiente=ambiente), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    g = root.find(f"{N}infDPS/{N}IBSCBS")
    nomes = [etree.QName(c).localname for c in g]
    assert nomes[nomes.index("cIndOp") + 1] == "tpOper"
    assert g.findtext(f"{N}tpOper") == "5"


def test_tp_oper_nao_vai_em_servico_que_nao_exige(dps_input):
    dps = build_dps(_com_ibscbs(dps_input, ibscbs_tp_oper="5"))  # E0904
    assert dps.find(f"{N}infDPS/{N}IBSCBS/{N}tpOper") is None


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_imunidade_suspensao_e_beneficio_validam_no_xsd(dps_input, certificado, ambiente):
    imune = replace(dps_input.config, trib_issqn="2", tp_imunidade="3", cst_pis_cofins=None)
    _, xml = preparar_dps(replace(dps_input, ambiente=ambiente, config=imune), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    mun = root.find(f"{N}infDPS/{N}valores/{N}trib/{N}tribMun")
    assert [etree.QName(c).localname for c in mun] == ["tribISSQN", "tpImunidade", "tpRetISSQN"]
    with pytest.raises(DpsBuildError, match="E0592"):
        build_dps(replace(dps_input, config=replace(imune, tp_imunidade=None)))

    cfg = replace(dps_input.config, tp_susp="1", n_processo="1" * 30, n_bm="12345678901234", p_red_bcbm="10.00")
    _, xml = preparar_dps(replace(dps_input, ambiente=ambiente, config=cfg), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    mun = root.find(f"{N}infDPS/{N}valores/{N}trib/{N}tribMun")
    assert [etree.QName(c).localname for c in mun] == ["tribISSQN", "exigSusp", "BM", "tpRetISSQN"]
    assert mun.findtext(f"{N}BM/{N}pRedBCBM") == "10.00"
    cfg = replace(cfg, p_red_bcbm=None, v_red_bcbm="50.00")
    root = build_dps(replace(dps_input, ambiente=ambiente, config=cfg))
    assert root.findtext(f"{N}infDPS/{N}valores/{N}trib/{N}tribMun/{N}BM/{N}vRedBCBM") == "50.00"


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_descontos_e_informacoes_complementares(dps_input, certificado, ambiente):
    from emissor.nfse.models import DadosNota

    nota = DadosNota(
        x_inf_comp="Contrato 12/2026\nParcela 3 de 12",
        x_ped="PED-778",
        itens_pedido=["1", "2"],
        v_desc_incond=Decimal("50.00"),
        v_desc_cond=Decimal("10.00"),
    )
    cfg = replace(dps_input.config, p_aliq_pis="1.00")
    _, xml = preparar_dps(replace(dps_input, ambiente=ambiente, config=cfg, nota=nota), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    assert inf.findtext(f"{N}serv/{N}infoCompl/{N}xPed") == "PED-778"
    assert [e.text for e in inf.iterfind(f"{N}serv/{N}infoCompl/{N}gItemPed/{N}xItemPed")] == ["1", "2"]
    assert "Parcela 3" in inf.findtext(f"{N}serv/{N}infoCompl/{N}xInfComp")
    assert inf.findtext(f"{N}valores/{N}vDescCondIncond/{N}vDescIncond") == "50.00"
    assert inf.findtext(f"{N}valores/{N}vDescCondIncond/{N}vDescCond") == "10.00"
    # PIS sobre o valor menos o desconto incondicionado (450 - 50)
    assert inf.findtext(f"{N}valores/{N}trib/{N}tribFed/{N}piscofins/{N}vBCPisCofins") == "400.00"
    assert inf.findtext(f"{N}valores/{N}trib/{N}tribFed/{N}piscofins/{N}vPis") == "4.00"
    with pytest.raises(DpsBuildError, match="E0431"):
        build_dps(replace(dps_input, nota=DadosNota(v_desc_incond=Decimal("450.00"))))


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_ibscbs_com_diferimento_e_tributacao_regular(dps_input, certificado, ambiente):
    dif = _com_ibscbs(
        dps_input,
        ibscbs_c_class_trib="515001",
        ibscbs_p_dif_uf="10.00",
        ibscbs_p_dif_mun="10.00",
        ibscbs_p_dif_cbs="20.00",
    )
    _, xml = preparar_dps(replace(dif, ambiente=ambiente), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    assert root.findtext(f".//{N}gDif/{N}pDifCBS") == "20.00"
    with pytest.raises(DpsBuildError, match="E0972"):
        build_dps(replace(dif, config=replace(dif.config, ibscbs_p_dif_cbs=None)))

    reg = _com_ibscbs(dps_input, ibscbs_c_class_trib="550016", ibscbs_c_class_trib_reg="000001")
    _, xml = preparar_dps(replace(reg, ambiente=ambiente), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    assert root.findtext(f".//{N}gTribRegular/{N}CSTReg") == "000"
    assert root.findtext(f".//{N}gTribRegular/{N}cClassTribReg") == "000001"
    with pytest.raises(DpsBuildError, match="E0965"):
        build_dps(replace(reg, config=replace(reg.config, ibscbs_c_class_trib_reg=None)))


COMEX = dict(
    comex_md_prestacao="1",
    comex_vinc_prest="0",
    comex_tp_moeda="220",
    comex_mec_af_p="01",
    comex_mec_af_t="01",
    comex_mov_temp_bens="1",
    comex_mdic="0",
)


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_exportacao_com_tomador_no_exterior(dps_input, certificado, ambiente):
    from emissor.nfse.models import DadosNota

    externo = {
        "nif": "98-7654321",
        "nome": "EXAMPLE CONSULTING LLC",
        "c_pais": "US",
        "c_end_post": "10001",
        "cidade": "New York",
        "estado": "NY",
        "logradouro": "5th Avenue",
        "numero": "100",
        "bairro": "Manhattan",
    }
    nota = DadosNota(tomador_exterior=externo, v_serv_moeda=Decimal("90.00"), n_re="123456789012")
    cfg = replace(dps_input.config, trib_issqn="3", c_pais_result="US", cst_pis_cofins=None, **COMEX)
    inp = replace(dps_input, ambiente=ambiente, config=cfg, nota=nota, tomador=nota.tomador())
    _, xml = preparar_dps(inp, certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    assert inf.findtext(f"{N}toma/{N}NIF") == "98-7654321"
    assert inf.findtext(f"{N}toma/{N}end/{N}endExt/{N}cPais") == "US"
    assert inf.findtext(f"{N}serv/{N}comExt/{N}vServMoeda") == "90.00"
    assert inf.findtext(f"{N}valores/{N}trib/{N}tribMun/{N}cPaisResult") == "US"
    # sem NIF, com o motivo
    sem_nif = DadosNota(tomador_exterior=externo | {"nif": "", "c_nao_nif": "1"}, v_serv_moeda=Decimal("90"))
    root = build_dps(replace(inp, nota=sem_nif, tomador=sem_nif.tomador()))
    assert root.findtext(f"{N}infDPS/{N}toma/{N}cNaoNIF") == "1"
    # prestado no exterior: país no lugar do município, sem país do resultado
    fora = replace(cfg, c_pais_prestacao="AR", c_pais_result=None)
    root = build_dps(replace(inp, config=fora))
    assert root.findtext(f"{N}infDPS/{N}serv/{N}locPrest/{N}cPaisPrestacao") == "AR"
    assert [e for e in validar_dps(root) if "Signature" not in e.mensagem] == []
    with pytest.raises(DpsBuildError, match="moeda estrangeira"):
        build_dps(replace(inp, nota=DadosNota(tomador_exterior=externo)))
    with pytest.raises(DpsBuildError, match="E0590"):
        build_dps(replace(inp, config=replace(cfg, c_pais_result=None)))


OBRA = {"cep": "88010400", "logradouro": "Rua das Obras", "numero": "10", "bairro": "Centro"}


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_obra_e_deducao_da_base(dps_input, certificado, ambiente):
    from emissor.nfse.models import DadosNota

    cfg = replace(dps_input.config, c_trib_nac="070201", c_nbs="101011100", op_simp_nac="1", reg_ap_trib_sn=None)
    nota = DadosNota(obra=OBRA | {"insc_imob_fisc": "123"}, ajuste_bc_p=Decimal("40"))
    _, xml = preparar_dps(replace(dps_input, ambiente=ambiente, config=cfg, nota=nota), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    assert inf.findtext(f"{N}serv/{N}obra/{N}end/{N}CEP") == "88010400"
    assert inf.findtext(f"{N}serv/{N}obra/{N}inscImobFisc") == "123"
    tag = "vAjusteBC/{N}pAjusteBCISSQN" if ambiente is Ambiente.HOMOLOGACAO else "vDedRed/{N}pDR"
    assert inf.findtext(f"{N}valores/{N}" + tag.format(N=N)) == "40.00"
    # código da obra no lugar do endereço; valor em R$
    root = build_dps(
        replace(dps_input, config=cfg, nota=DadosNota(obra={"c_obra": "CNO-1"}, ajuste_bc_v=Decimal("100")))
    )
    assert root.findtext(f"{N}infDPS/{N}serv/{N}obra/{N}cObra") == "CNO-1"
    assert (root.findtext(f".//{N}vDR") or root.findtext(f".//{N}vAjusteBCISSQN")) == "100.00"
    with pytest.raises(DpsBuildError, match="E0370"):
        build_dps(replace(dps_input, config=cfg))
    with pytest.raises(DpsBuildError, match="E0372"):
        build_dps(replace(dps_input, nota=DadosNota(obra=OBRA)))
    with pytest.raises(DpsBuildError, match="E0436"):
        build_dps(replace(dps_input, config=replace(cfg, op_simp_nac="2"), nota=nota))
    # ME/EPP com ISS pelo Simples: só nos serviços da lista
    simples = replace(dps_input.config, op_simp_nac="3", reg_ap_trib_sn="1")
    with pytest.raises(DpsBuildError, match="E0451"):
        build_dps(replace(dps_input, config=simples, nota=DadosNota(ajuste_bc_p=Decimal("10"))))


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_atividade_de_evento(dps_input, certificado, ambiente):
    from emissor.nfse.models import DadosNota

    cfg = replace(dps_input.config, c_trib_nac="120101", c_nbs="125022000")
    evento = {"nome": "Feira de Teste", "dt_ini": "2026-11-20", "dt_fim": "2026-11-22"}
    nota = DadosNota(evento=evento | OBRA)
    _, xml = preparar_dps(replace(dps_input, ambiente=ambiente, config=cfg, nota=nota), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    assert root.findtext(f".//{N}atvEvento/{N}xNome") == "Feira de Teste"
    root = build_dps(replace(dps_input, config=cfg, nota=DadosNota(evento=evento | {"id_atv_evt": "EV-9"})))
    assert root.findtext(f".//{N}atvEvento/{N}idAtvEvt") == "EV-9"
    with pytest.raises(DpsBuildError, match="E0390"):
        build_dps(replace(dps_input, config=cfg))
    with pytest.raises(DpsBuildError, match="fim do evento"):
        build_dps(replace(dps_input, config=cfg, nota=DadosNota(evento=evento | OBRA | {"dt_fim": "2026-11-01"})))


CHAVE_REF = "42054072211444777000161000000000000626085712554750"
INTERM = {
    "documento": "11444777000161",
    "nome": "INTERMEDIARIA EXEMPLO LTDA",
    "c_mun": "4205407",
    "cep": "88010400",
    "logradouro": "Rua Exemplo",
    "numero": "1",
    "bairro": "Centro",
}


@pytest.mark.parametrize("ambiente", [Ambiente.PRODUCAO, Ambiente.HOMOLOGACAO])
def test_intermediario_destinatario_e_compra_governamental(dps_input, certificado, ambiente):
    from emissor.nfse.models import DadosNota

    nota = DadosNota(
        intermediario=INTERM | {"im": "123"},
        destinatario={"documento": "52998224725", "nome": "MARIA EXEMPLO"},
        ref_nfse=[CHAVE_REF],
    )
    inp = _com_ibscbs(dps_input, tp_ret_issqn="3", ibscbs_tp_ente_gov="4", ibscbs_tp_oper="2")
    _, xml = preparar_dps(replace(inp, ambiente=ambiente, nota=nota), certificado)
    root = etree.fromstring(xml)
    assert validar_dps(root) == []
    inf = root.find(f"{N}infDPS")
    assert inf.findtext(f"{N}interm/{N}CNPJ") == "11444777000161"
    assert inf.findtext(f"{N}interm/{N}IM") == "123"
    assert inf.findtext(f"{N}valores/{N}trib/{N}tribMun/{N}tpRetISSQN") == "3"
    assert inf.findtext(f"{N}IBSCBS/{N}tpEnteGov") == "4"
    assert inf.findtext(f"{N}IBSCBS/{N}gRefNFSe/{N}refNFSe") == CHAVE_REF
    assert inf.findtext(f"{N}IBSCBS/{N}indDest") == "1"
    assert inf.findtext(f"{N}IBSCBS/{N}dest/{N}CPF") == "52998224725"
    with pytest.raises(DpsBuildError, match="intermediário"):
        build_dps(replace(inp, nota=replace(nota, intermediario=None)))
    with pytest.raises(DpsBuildError, match="E1388"):
        build_dps(replace(inp, nota=replace(nota, intermediario={"documento": "11444777000161", "nome": "X"})))
    with pytest.raises(DpsBuildError, match="E0905"):
        build_dps(replace(inp, nota=replace(nota, ref_nfse=[])))
    with pytest.raises(DpsBuildError, match="E0903"):
        build_dps(replace(inp, config=replace(inp.config, ibscbs_tp_oper=None), nota=nota))
    with pytest.raises(DpsBuildError, match="IBS/CBS ligado"):
        build_dps(replace(dps_input, nota=DadosNota(destinatario={"documento": "52998224725", "nome": "M"})))
