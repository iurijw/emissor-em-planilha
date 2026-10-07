"""Leitura de XMLs de NFS-e (padrão nacional).

Usado para: importar padrões fiscais e clientes a partir das notas já emitidas
(onboarding), exibir emissões e gerar o DANFSe.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from lxml import etree

from emissor.nfse.constants import NS_NFSE
from emissor.nfse.models import ConfigFiscal, Endereco, Tomador

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
    )
