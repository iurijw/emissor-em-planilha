from dataclasses import replace

import pytest
from lxml import etree

from emissor.nfse.constants import NS_NFSE
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
    """A DPS gerada deve ter exatamente os mesmos campos da DPS das notas emitidas pelo Emissor Web."""
    real = etree.fromstring(nfse_exemplo_bytes).find(f"{N}infNFSe/{N}DPS/{N}infDPS")
    gerada = build_dps(dps_input).find(f"{N}infDPS")
    assert _estrutura(gerada) == _estrutura(real)


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
    _, xml = preparar_dps(inp, certificado)
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


def test_dps_sem_ibscbs_por_padrao(dps_input):
    assert build_dps(dps_input).find(f"{N}infDPS/{N}IBSCBS") is None


def test_ibscbs_exige_nbs_e_recusa_servico_com_tp_oper(dps_input):
    with pytest.raises(DpsBuildError, match="NBS"):
        build_dps(_com_ibscbs(dps_input, c_nbs=None))
    with pytest.raises(DpsBuildError, match="tpOper"):
        build_dps(_com_ibscbs(dps_input, c_trib_nac="250501"))
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
