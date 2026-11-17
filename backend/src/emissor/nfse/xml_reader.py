"""Leitura de XMLs de NFS-e (padrão nacional).

Usado para: importar padrões fiscais e clientes a partir das notas já emitidas
(onboarding), exibir emissões e gerar o DANFSe.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from lxml import etree

from emissor.nfse.codes import C_IND_OP, TP_OPER, TP_OPER_COM_REFERENCIA
from emissor.nfse.constants import NS_NFSE
from emissor.nfse.models import RETENCOES_PIS_COFINS, ConfigFiscal, Endereco, Tomador, csll_sem_pis_cofins

_NS = {"n": NS_NFSE}


class NfseXmlError(ValueError):
    pass


@dataclass
class NFSeDoc:
    root: etree._Element

    @classmethod
    def from_bytes(cls, data: bytes) -> NFSeDoc:
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
            root = etree.fromstring(data, parser)
        except etree.XMLSyntaxError as exc:
            raise NfseXmlError(f"XML inválido: {exc}") from exc
        if etree.QName(root).localname != "NFSe" or etree.QName(root).namespace != NS_NFSE:
            raise NfseXmlError("O arquivo não é uma NFS-e do padrão nacional (elemento raiz <NFSe>).")
        return cls(root)

    @classmethod
    def from_path(cls, path: str | Path) -> NFSeDoc:
        return cls.from_bytes(Path(path).read_bytes())

    # --- acesso genérico ---------------------------------------------------------
    def el(self, caminho: str) -> etree._Element | None:
        """Caminho relativo a ``infNFSe``, ex.: ``DPS/infDPS/prest/CNPJ``."""
        xp = "n:infNFSe" + "".join(f"/n:{p}" for p in caminho.split("/") if p)
        res = self.root.xpath(xp, namespaces=_NS)
        return res[0] if res else None

    def t(self, caminho: str, padrao: str | None = None) -> str | None:
        el = self.el(caminho)
        if el is None or el.text is None:
            return padrao
        return el.text.strip()

    def dps(self, caminho: str, padrao: str | None = None) -> str | None:
        return self.t(f"DPS/infDPS/{caminho}", padrao)

    # --- atalhos --------------------------------------------------------------------
    @property
    def chave(self) -> str:
        ident = self.root.find("n:infNFSe", _NS).get("Id", "")
        return ident[3:] if ident.startswith("NFS") else ident

    @property
    def numero(self) -> str | None:
        return self.t("nNFSe")

    @property
    def tp_amb(self) -> str | None:
        return self.dps("tpAmb")

    @property
    def valor_servico(self) -> Decimal:
        return Decimal(self.dps("valores/vServPrest/vServ", "0"))

    @property
    def data_processamento(self) -> str | None:
        return self.t("dhProc")

    @property
    def tomador_documento(self) -> str | None:
        return self.dps("toma/CNPJ") or self.dps("toma/CPF") or self.dps("toma/NIF")

    @property
    def tomador_nome(self) -> str | None:
        return self.dps("toma/xNome")

    @property
    def emitente_nome(self) -> str | None:
        return self.t("emit/xNome")


def extrair_tomador(doc: NFSeDoc) -> Tomador | None:
    documento = doc.tomador_documento
    if not documento:
        return None
    endereco = None
    if doc.dps("toma/end/endNac/cMun"):
        endereco = Endereco(
            c_mun=doc.dps("toma/end/endNac/cMun"),
            cep=doc.dps("toma/end/endNac/CEP") or "",
            logradouro=doc.dps("toma/end/xLgr") or "",
            numero=doc.dps("toma/end/nro") or "",
            bairro=doc.dps("toma/end/xBairro") or "",
            complemento=doc.dps("toma/end/xCpl"),
        )
    return Tomador(
        documento=documento,
        nome=doc.tomador_nome or "",
        endereco=endereco,
        fone=doc.dps("toma/fone"),
        email=doc.dps("toma/email"),
        inscricao_municipal=doc.dps("toma/IM"),
    )


def extrair_padroes(docs: list[NFSeDoc]) -> ConfigFiscal:
    """Padrões fiscais a partir da nota mais recente (por ``dhProc``).

    Contato do prestador (fone/e-mail) vem da nota mais recente que o tenha,
    já que algumas notas são emitidas sem ele.
    """
    if not docs:
        raise NfseXmlError("Nenhuma NFS-e informada.")
    ordenados = sorted(docs, key=lambda d: d.data_processamento or "", reverse=True)
    d = ordenados[0]
    fone = next((x.dps("prest/fone") for x in ordenados if x.dps("prest/fone")), None) or d.t("emit/fone")
    email = next((x.dps("prest/email") for x in ordenados if x.dps("prest/email")), None) or d.t("emit/email")
    p_tot = d.dps("valores/trib/totTrib/pTotTribSN")
    return ConfigFiscal(
        cnpj=d.dps("prest/CNPJ") or d.t("emit/CNPJ"),
        c_mun_emissor=d.dps("cLocEmi"),
        fone=fone,
        email=email,
        inscricao_municipal=d.dps("prest/IM"),
        op_simp_nac=d.dps("prest/regTrib/opSimpNac", "1"),
        reg_ap_trib_sn=d.dps("prest/regTrib/regApTribSN"),
        reg_esp_trib=d.dps("prest/regTrib/regEspTrib", "0"),
        c_loc_prestacao=d.dps("serv/locPrest/cLocPrestacao"),
        c_trib_nac=d.dps("serv/cServ/cTribNac"),
        c_trib_mun=d.dps("serv/cServ/cTribMun"),
        c_nbs=d.dps("serv/cServ/cNBS"),
        trib_issqn=d.dps("valores/trib/tribMun/tribISSQN", "1"),
        tp_ret_issqn=d.dps("valores/trib/tribMun/tpRetISSQN", "1"),
        cst_pis_cofins=d.dps("valores/trib/tribFed/piscofins/CST"),
        tp_ret_pis_cofins=d.dps("valores/trib/tribFed/piscofins/tpRetPisCofins"),
        p_tot_trib_sn=Decimal(p_tot) if p_tot else None,
        tp_emit=d.dps("tpEmit", "1"),
        **_padroes_aliquotas(d),
        **_padroes_ibscbs(d),
    )


def _padroes_aliquotas(d: NFSeDoc) -> dict:
    """Alíquotas e retenções da nota mais recente; retenções em R$ viram % do valor do serviço."""
    tf = "valores/trib/tribFed"
    res = {
        "p_aliq_iss": d.dps("valores/trib/tribMun/pAliq"),
        "p_aliq_pis": d.dps(f"{tf}/piscofins/pAliqPis"),
        "p_aliq_cofins": d.dps(f"{tf}/piscofins/pAliqCofins"),
        "p_tot_trib_fed": d.dps("valores/trib/totTrib/pTotTrib/pTotTribFed"),
        "p_tot_trib_est": d.dps("valores/trib/totTrib/pTotTrib/pTotTribEst"),
        "p_tot_trib_mun": d.dps("valores/trib/totTrib/pTotTrib/pTotTribMun"),
    }
    try:
        v_serv = Decimal(d.dps("valores/vServPrest/vServ") or "0")
    except InvalidOperation:
        v_serv = Decimal("0")
    digitadas = False
    for campo, tag in (("p_ret_irrf", "vRetIRRF"), ("p_ret_csll", "vRetCSLL"), ("p_ret_cp", "vRetCP")):
        v = d.dps(f"{tf}/{tag}")
        if v and campo == "p_ret_csll":
            # Contribuições Sociais - Retidas somam o PIS/COFINS retidos; a tributação guarda só a CSLL. Menor que
            # PIS + COFINS retidos = o tomador reteve só parte (valor digitado no Emissor Nacional, por exemplo).
            if _contrib_digitadas(d, Decimal(v)):
                digitadas = True
                campo = "p_ret_contrib_sociais"
            else:
                v = _so_csll(d, Decimal(v))
        if v and v_serv > 0:
            res[campo] = f"{(Decimal(v) * 100 / v_serv).quantize(Decimal('0.01'))}"
    res = {k: v for k, v in res.items() if v}
    if digitadas:
        res["somar_contrib_sociais"] = False
    return res


def _dec(d: NFSeDoc, caminho: str) -> Decimal | None:
    try:
        return Decimal(d.dps(caminho)) if d.dps(caminho) else None
    except InvalidOperation:
        return None


def _contrib_digitadas(d: NFSeDoc, v_ret_csll: Decimal) -> bool:
    """``vRetCSLL`` menor que o PIS + COFINS retidos: não é a soma, é o valor que o tomador reteve de fato.

    Exceção: notas deste sistema até a 0.17.0, que levavam só a CSLL no ``vRetCSLL``.
    """
    pc = "valores/trib/tribFed/piscofins"
    tp_ret = d.dps(f"{pc}/tpRetPisCofins")
    if tp_ret in (None, "1"):
        return False
    pis, cofins, _ = RETENCOES_PIS_COFINS.get(tp_ret, (False, False, None))
    soma = ((_dec(d, f"{pc}/vPis") or Decimal("0")) if pis else Decimal("0")) + (
        (_dec(d, f"{pc}/vCofins") or Decimal("0")) if cofins else Decimal("0")
    )
    if not soma or v_ret_csll >= soma:
        return False
    return not _deste_sistema_ate_0_17_0(d.dps("verAplic") or "")


def _deste_sistema_ate_0_17_0(ver_aplic: str) -> bool:
    if not ver_aplic.startswith("EmPlanilha_"):
        return False
    try:
        versao = tuple(int(x) for x in ver_aplic.removeprefix("EmPlanilha_").split(".")[:3])
    except ValueError:
        return False
    return versao < (0, 17, 1)


def _so_csll(d: NFSeDoc, v_ret_csll: Decimal) -> Decimal | None:
    pc = "valores/trib/tribFed/piscofins"

    def dec(caminho: str) -> Decimal | None:
        try:
            return Decimal(d.dps(caminho)) if d.dps(caminho) else None
        except InvalidOperation:
            return None

    csll = csll_sem_pis_cofins(v_ret_csll, d.dps(f"{pc}/tpRetPisCofins"), dec(f"{pc}/vPis"), dec(f"{pc}/vCofins"))
    return csll or None


def _padroes_ibscbs(d: NFSeDoc) -> dict:
    """IBS/CBS da nota mais recente; sem o grupo, mantém os padrões (grupo desligado)."""
    if d.dps("IBSCBS/cIndOp") is None:
        return {}
    padroes = {"ibscbs": True, "ibscbs_ind_final": d.dps("IBSCBS/indFinal")}
    if d.dps("IBSCBS/cIndOp") in C_IND_OP:  # um indicador que o sistema não emite (imóvel, exterior...) fica no padrão
        padroes["ibscbs_c_ind_op"] = d.dps("IBSCBS/cIndOp")
    if d.dps("IBSCBS/tpOper") in TP_OPER and d.dps("IBSCBS/tpOper") not in TP_OPER_COM_REFERENCIA:
        padroes["ibscbs_tp_oper"] = d.dps("IBSCBS/tpOper")
    c_class = d.dps("IBSCBS/valores/trib/gIBSCBS/cClassTrib") or d.dps("IBSCBS/valores/trib/cClassTrib")
    if c_class:
        padroes["ibscbs_c_class_trib"] = c_class
    return padroes
