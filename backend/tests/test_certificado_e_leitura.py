from decimal import Decimal

import pytest

from emissor import security
from emissor.nfse.certificate import Certificado, CertificadoError
from emissor.nfse.xml_reader import NFSeDoc, NfseXmlError, extrair_padroes, extrair_tomador

from .conftest import CNPJ_PRESTADOR, SENHA_PFX, xmls_reais


def test_info_do_certificado(certificado):
    info = certificado.info
    assert info.cnpj == CNPJ_PRESTADOR
    assert info.titular == "EMPRESA TESTE LTDA"
    assert not info.vencido
    assert 360 <= info.dias_para_vencer <= 366


def test_senha_errada(pfx_bytes):
    with pytest.raises(CertificadoError, match="Senha"):
        Certificado(pfx_bytes, "errada")


def test_arquivo_invalido():
    with pytest.raises(CertificadoError):
        Certificado(b"nao eh pfx", "x")


def test_ssl_context_carrega_certificado(certificado):
    ctx = certificado.ssl_context()
    assert ctx.verify_mode.name == "CERT_REQUIRED"


def test_criptografia_em_repouso(pfx_bytes):
    token = security.encrypt(pfx_bytes)
    assert token != pfx_bytes
    assert security.decrypt(token) == pfx_bytes
    assert Certificado(security.decrypt(token), SENHA_PFX).info.cnpj == CNPJ_PRESTADOR


def test_leitura_nfse(nfse_exemplo_bytes):
    doc = NFSeDoc.from_bytes(nfse_exemplo_bytes)
    assert len(doc.chave) == 50
    assert doc.numero == "6"
    assert doc.valor_servico == Decimal("490.00")
    assert doc.tomador_documento == "11222333000181"
    t = extrair_tomador(doc)
    assert t.nome == "CLIENTE EXEMPLO LTDA"
    assert t.endereco.c_mun == "4205407" and t.endereco.cep == "88010000"


def test_padroes_extraidos_da_nfse(nfse_exemplo_bytes):
    cfg = extrair_padroes([NFSeDoc.from_bytes(nfse_exemplo_bytes)])
    assert cfg.cnpj == CNPJ_PRESTADOR
    assert cfg.c_mun_emissor == "4205407"
    assert (cfg.op_simp_nac, cfg.reg_ap_trib_sn, cfg.reg_esp_trib) == ("3", "2", "0")
    assert (cfg.c_trib_nac, cfg.c_nbs) == ("171901", "113022100")
    assert (cfg.trib_issqn, cfg.tp_ret_issqn) == ("1", "1")
    assert cfg.cst_pis_cofins == "00" and cfg.tp_ret_pis_cofins is None
    assert cfg.p_tot_trib_sn == Decimal("11.08")
    assert cfg.fone == "4830000000"


def test_xml_que_nao_e_nfse():
    with pytest.raises(NfseXmlError):
        NFSeDoc.from_bytes(b"<outro/>")
    with pytest.raises(NfseXmlError):
        NFSeDoc.from_bytes(b"<quebrado")


@pytest.mark.skipif(not xmls_reais(), reason="temp/ com XMLs reais não disponível")
def test_padroes_dos_xmls_reais():
    docs = [NFSeDoc.from_path(p) for p in xmls_reais()]
    cfg = extrair_padroes(docs)
    # XMLs reais opcionais (temp/, fora do git): os padrões saem completos do conjunto de notas.
    assert cfg.cnpj and cfg.c_mun_emissor and cfg.c_trib_nac
    assert all(extrair_tomador(d).endereco is not None for d in docs)


def test_padroes_com_contribuicoes_sociais_digitadas(nfse_exemplo_bytes):
    """vRetCSLL menor que o PIS + COFINS retidos: a tributação importada não soma e guarda o percentual da nota."""

    def nota(v_ret_csll: str, ver_aplic: str = "EmissorWeb_1.6.0.0") -> NFSeDoc:
        grupo = (
            "<piscofins><CST>01</CST><vBCPisCofins>490.00</vBCPisCofins><pAliqPis>0.65</pAliqPis>"
            "<pAliqCofins>3.00</pAliqCofins><vPis>3.19</vPis><vCofins>14.70</vCofins><tpRetPisCofins>3</tpRetPisCofins>"
            f"</piscofins><vRetCSLL>{v_ret_csll}</vRetCSLL>"
        )
        xml = nfse_exemplo_bytes.replace(b"<piscofins><CST>00</CST></piscofins>", grupo.encode())
        return NFSeDoc.from_bytes(xml.replace(b"EmissorWeb_1.6.0.0", ver_aplic.encode()))

    # Soma cheia (CSLL 4,90 + PIS 3,19 + COFINS 14,70): continua somando e guarda só a CSLL.
    cfg = extrair_padroes([nota("22.79")])
    assert cfg.somar_contrib_sociais is True and cfg.p_ret_csll == "1.00"
    # O tomador reteve só R$ 9,80 (2%): valor digitado.
    cfg = extrair_padroes([nota("9.80")])
    assert (cfg.somar_contrib_sociais, cfg.p_ret_contrib_sociais, cfg.p_ret_csll) == (False, "2.00", None)
    # Notas deste sistema até a 0.17.0 levavam só a CSLL no vRetCSLL.
    cfg = extrair_padroes([nota("4.90", "EmPlanilha_0.17.0")])
    assert cfg.somar_contrib_sociais is True and cfg.p_ret_csll == "1.00"
