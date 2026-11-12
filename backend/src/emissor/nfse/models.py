"""Estruturas de entrada para montar a DPS.

``ConfigFiscal`` guarda os padrões do prestador (preenchidos no onboarding a partir
dos XMLs atuais); ``Tomador`` vem do cadastro de clientes; valor e descrição vêm da
linha da tabela.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from emissor.nfse.constants import Ambiente


@dataclass
class Endereco:
    c_mun: str  # IBGE, 7 dígitos
    cep: str  # 8 dígitos
    logradouro: str
    numero: str
    bairro: str
    complemento: str | None = None


@dataclass
class EnderecoExterior:
    c_pais: str  # ISO2, diferente de BR (E0246)
    c_end_post: str  # código postal (até 11)
    cidade: str
    estado: str  # estado, província ou região
    logradouro: str
    numero: str
    bairro: str
    complemento: str | None = None


@dataclass
class ConfigFiscal:
    # Prestador
    cnpj: str
    c_mun_emissor: str  # cLocEmi
    fone: str | None = None
    email: str | None = None
    inscricao_municipal: str | None = None
    # Regime tributário
    op_simp_nac: str = "3"  # 1 não optante, 2 MEI, 3 ME/EPP
    reg_ap_trib_sn: str | None = "2"
    reg_esp_trib: str = "0"
    # Serviço
    c_loc_prestacao: str | None = None  # padrão = c_mun_emissor
    c_pais_prestacao: str | None = None  # serviço prestado no exterior (ISO2): vai no lugar do município
    c_trib_nac: str = "171901"
    c_trib_mun: str | None = None
    c_nbs: str | None = "113022100"
    # Tributação
    trib_issqn: str = "1"
    c_pais_result: str | None = None  # exportação (tribISSQN 3): país onde se deu o resultado (E0590)
    tp_imunidade: str | None = None  # obrigatório com tribISSQN 2 (E0592); 0 não é aceito (E0593)
    # Exigibilidade do ISSQN suspensa (exigSusp): 1 decisão judicial, 2 processo administrativo; nº com 30 dígitos
    tp_susp: str | None = None
    n_processo: str | None = None
    # Benefício municipal (BM): nº de 14 dígitos parametrizado pela prefeitura, com a redução da base em % ou R$
    n_bm: str | None = None
    p_red_bcbm: str | None = None
    v_red_bcbm: str | None = None
    tp_ret_issqn: str = "1"
    cst_pis_cofins: str | None = "00"  # None → não envia grupo tribFed
    tp_ret_pis_cofins: str | None = None
    p_tot_trib_sn: Decimal | None = Decimal("11.08")  # None → indTotTrib = 0
    # Alíquotas e retenções em % (texto "2.50"; None = não informar). Os valores em R$ saem de cada nota.
    p_aliq_iss: str | None = None  # pAliq: só com ISS retido e ISS apurado pelo Simples (E0621)
    p_aliq_iss_atualizada_em: str | None = None  # AAAA-MM-DD da última mudança (aviso na emissão)
    p_aliq_pis: str | None = None  # pAliqPis (base = valor do serviço)
    p_aliq_cofins: str | None = None  # pAliqCofins
    p_ret_irrf: str | None = None  # vRetIRRF = valor x %
    p_ret_csll: str | None = None  # vRetCSLL
    p_ret_cp: str | None = None  # vRetCP (INSS)
    # Total aproximado dos tributos (Lei 12.741) para não optante do Simples (pTotTrib)
    p_tot_trib_fed: str | None = None
    p_tot_trib_est: str | None = None
    p_tot_trib_mun: str | None = None
    # IBS/CBS (reforma tributária): grupo infDPS/IBSCBS, com destinatário = tomador (indDest 0)
    ibscbs: bool = False  # False → DPS sem o grupo (aceito sem rejeição até 31/12/2026)
    ibscbs_c_ind_op: str = "100301"
    ibscbs_c_class_trib: str = "000001"  # o CST vai como os 3 primeiros dígitos (E0959)
    ibscbs_ind_final: str | None = None  # opcional; será descontinuado pela NT 005
    # tpOper: obrigatório nos subitens de codes.SUBITENS_EXIGEM_TP_OPER (E0903) e proibido nos demais (E0904)
    ibscbs_tp_oper: str | None = None
    # Grupos que a classificação exige: tributação regular (gTribRegular, E0965) e diferimento (gDif, E0972)
    ibscbs_c_class_trib_reg: str | None = None  # o CSTReg vai como os 3 primeiros dígitos
    ibscbs_p_dif_uf: str | None = None
    ibscbs_p_dif_mun: str | None = None
    ibscbs_p_dif_cbs: str | None = None
    # Comércio exterior (serv/comExt): obrigatório com exportação, tomador ou local no exterior (E0330). O valor na
    # moeda estrangeira e os nº de DI/RE são de cada nota (DadosNota).
    comex_md_prestacao: str | None = None
    comex_vinc_prest: str | None = None
    comex_tp_moeda: str | None = None  # código do Banco Central (3 dígitos)
    comex_mec_af_p: str | None = None
    comex_mec_af_t: str | None = None
    comex_mov_temp_bens: str | None = None
    comex_mdic: str | None = None
    # Emissão
    tp_emit: str = "1"


# Campos de percentual guardados como texto (``Decimal`` só na montagem da DPS).
CAMPOS_PERCENTUAIS = (
    "p_aliq_iss",
    "p_aliq_pis",
    "p_aliq_cofins",
    "p_ret_irrf",
    "p_ret_csll",
    "p_ret_cp",
    "p_tot_trib_fed",
    "p_tot_trib_est",
    "p_tot_trib_mun",
    "p_red_bcbm",
    "ibscbs_p_dif_uf",
    "ibscbs_p_dif_mun",
    "ibscbs_p_dif_cbs",
)

# tpRetPisCofins → (PIS retido, COFINS retido, CSLL retida); None = o código não diz (CSLL opcional).
# "1" exige a CSLL: E0724 obriga vRetCSLL para todo código que não seja 0 ou 2.
RETENCOES_PIS_COFINS: dict[str, tuple[bool, bool, bool | None]] = {
    "0": (False, False, False),
    "1": (True, True, True),
    "2": (False, False, None),
    "3": (True, True, True),
    "4": (True, True, False),
    "5": (True, False, False),
    "6": (False, True, False),
    "7": (False, True, True),
    "8": (False, False, True),
    "9": (True, False, True),
}


def exige_comext(cfg: ConfigFiscal, tomador_no_exterior: bool) -> bool:
    """Grupo de comércio exterior obrigatório (E0330): exportação, prestação ou tomador no exterior."""
    return cfg.trib_issqn == "3" or bool(cfg.c_pais_prestacao) or tomador_no_exterior


def iss_exige_aliquota(cfg: ConfigFiscal) -> bool:
    """A Sefin exige ``pAliq`` (E0621/E0628): ME/EPP com ISS pelo Simples e ISS retido.

    Nos demais casos a alíquota é a parametrizada pelo município e informá-la rejeita a nota (E0617/E0625/E0635)
    — este sistema não trata município de incidência fora do Sistema Nacional.
    """
    return cfg.op_simp_nac == "3" and cfg.reg_ap_trib_sn == "1" and cfg.tp_ret_issqn in ("2", "3")


# Campos que mudam de uma tributação para outra (abas e nota avulsa). Os demais (prestador, regime,
# tpEmit) valem sempre os das configurações.
CAMPOS_TRIBUTACAO = (
    "c_loc_prestacao",
    "c_pais_prestacao",
    "c_pais_result",
    "comex_md_prestacao",
    "comex_vinc_prest",
    "comex_tp_moeda",
    "comex_mec_af_p",
    "comex_mec_af_t",
    "comex_mov_temp_bens",
    "comex_mdic",
    "c_trib_nac",
    "c_trib_mun",
    "c_nbs",
    "trib_issqn",
    "tp_imunidade",
    "tp_susp",
    "n_processo",
    "n_bm",
    "p_red_bcbm",
    "v_red_bcbm",
    "tp_ret_issqn",
    "cst_pis_cofins",
    "tp_ret_pis_cofins",
    "p_tot_trib_sn",
    "p_aliq_iss",
    "p_aliq_iss_atualizada_em",
    "p_aliq_pis",
    "p_aliq_cofins",
    "p_ret_irrf",
    "p_ret_csll",
    "p_ret_cp",
    "p_tot_trib_fed",
    "p_tot_trib_est",
    "p_tot_trib_mun",
    "ibscbs",
    "ibscbs_c_ind_op",
    "ibscbs_c_class_trib",
    "ibscbs_ind_final",
    "ibscbs_tp_oper",
    "ibscbs_c_class_trib_reg",
    "ibscbs_p_dif_uf",
    "ibscbs_p_dif_mun",
    "ibscbs_p_dif_cbs",
)


def tributacao_de(cfg: ConfigFiscal) -> dict:
    """Campos de tributação de uma configuração, prontos para JSON."""
    d = {k: getattr(cfg, k) for k in CAMPOS_TRIBUTACAO}
    d["p_tot_trib_sn"] = str(cfg.p_tot_trib_sn) if cfg.p_tot_trib_sn is not None else None
    return d


def aplicar_tributacao(cfg: ConfigFiscal, tributacao: dict | None) -> ConfigFiscal:
    """Configuração do prestador com os campos de tributação trocados pelos de ``tributacao``."""
    if not tributacao:
        return cfg
    novos = {k: v for k, v in tributacao.items() if k in CAMPOS_TRIBUTACAO}
    if "p_tot_trib_sn" in novos:
        p = novos["p_tot_trib_sn"]
        novos["p_tot_trib_sn"] = Decimal(str(p)) if p not in (None, "") else None
    return replace(cfg, **novos)


@dataclass
class Tomador:
    documento: str  # CNPJ (14) ou CPF (11), só dígitos/letras; no exterior, o NIF (ou vazio com c_nao_nif)
    nome: str
    endereco: Endereco | None = None
    fone: str | None = None
    email: str | None = None
    inscricao_municipal: str | None = None
    exterior: EnderecoExterior | None = None  # tomador no exterior: identificado pelo NIF ou pelo motivo de não ter
    c_nao_nif: str | None = None  # 1 dispensado, 2 não exigência (sem NIF)

    @property
    def tipo_documento(self) -> str:
        return "CPF" if len(self.documento) == 11 else "CNPJ"


@dataclass
class DpsInput:
    ambiente: Ambiente
    serie: str
    n_dps: int
    dh_emi: datetime  # timezone-aware
    config: ConfigFiscal
    tomador: Tomador | None
    valor: Decimal
    descricao: str
    nota: DadosNota = field(default_factory=lambda: DadosNota())


@dataclass
class DadosNota:
    """O que vale só para uma nota (nota avulsa): informações complementares e descontos."""

    x_inf_comp: str | None = None  # infoCompl/xInfComp, até 2000 caracteres
    x_ped: str | None = None  # infoCompl/xPed: número do pedido/ordem de compra
    itens_pedido: list[str] = field(default_factory=list)  # infoCompl/gItemPed/xItemPed (até 99)
    v_desc_incond: Decimal | None = None  # desconto incondicionado: reduz a base do ISSQN (vBC)
    v_desc_cond: Decimal | None = None  # desconto condicionado: só sai do valor líquido
    # Tomador no exterior (só na nota avulsa): nif ou c_nao_nif, nome, email, fone e os campos de EnderecoExterior
    tomador_exterior: dict | None = None
    v_serv_moeda: Decimal | None = None  # comExt/vServMoeda: valor na moeda da tributação
    n_di: str | None = None  # comExt/nDI
    n_re: str | None = None  # comExt/nRE

    @classmethod
    def de_dict(cls, d: dict | None) -> DadosNota:
        d = d or {}

        def dec(v):
            return Decimal(str(v)) if v not in (None, "") else None

        return cls(
            x_inf_comp=d.get("x_inf_comp") or None,
            x_ped=d.get("x_ped") or None,
            itens_pedido=[str(i) for i in d.get("itens_pedido") or [] if str(i).strip()],
            v_desc_incond=dec(d.get("v_desc_incond")),
            v_desc_cond=dec(d.get("v_desc_cond")),
            tomador_exterior=d.get("tomador_exterior") or None,
            v_serv_moeda=dec(d.get("v_serv_moeda")),
            n_di=d.get("n_di") or None,
            n_re=d.get("n_re") or None,
        )

    def para_dict(self) -> dict:
        d = {
            "x_inf_comp": self.x_inf_comp,
            "x_ped": self.x_ped,
            "itens_pedido": self.itens_pedido or None,
            "v_desc_incond": f"{self.v_desc_incond:.2f}" if self.v_desc_incond is not None else None,
            "v_desc_cond": f"{self.v_desc_cond:.2f}" if self.v_desc_cond is not None else None,
            "tomador_exterior": self.tomador_exterior or None,
            "v_serv_moeda": f"{self.v_serv_moeda:.2f}" if self.v_serv_moeda is not None else None,
            "n_di": self.n_di,
            "n_re": self.n_re,
        }
        return {k: v for k, v in d.items() if v is not None}

    @property
    def vazia(self) -> bool:
        return not self.para_dict()

    def tomador(self) -> Tomador | None:
        """Tomador no exterior da nota, ou None."""
        t = self.tomador_exterior
        if not t:
            return None
        return Tomador(
            documento=(t.get("nif") or "").strip(),
            nome=t.get("nome") or "",
            email=t.get("email") or None,
            fone=t.get("fone") or None,
            c_nao_nif=None if (t.get("nif") or "").strip() else t.get("c_nao_nif") or None,
            exterior=EnderecoExterior(
                c_pais=(t.get("c_pais") or "").upper(),
                c_end_post=t.get("c_end_post") or "",
                cidade=t.get("cidade") or "",
                estado=t.get("estado") or "",
                logradouro=t.get("logradouro") or "",
                numero=t.get("numero") or "",
                bairro=t.get("bairro") or "",
                complemento=t.get("complemento") or None,
            ),
        )


# --- normalização de dados ------------------------------------------------------

_TRADUCOES = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "–": "-",
        "—": "-",
        "−": "-",
        "…": "...",
        " ": " ",
        "•": "-",
        "º": "o",
        "ª": "a",
    }
)


def limpar_texto(valor: str | None, *, multilinha: bool = False, max_len: int | None = None) -> str | None:
    """Ajusta texto às restrições do leiaute (``TSString``: só Latin-1, sem espaço
    nas pontas; quebras de linha só onde o tipo permite)."""
    if valor is None:
        return None
    s = unicodedata.normalize("NFC", str(valor)).translate(_TRADUCOES)
    # Remove caracteres fora do Latin-1 (emoji etc.), tentando transliterar antes.
    out = []
    for ch in s:
        if ord(ch) <= 0xFF:
            out.append(ch)
        else:
            base = unicodedata.normalize("NFKD", ch).encode("latin-1", "ignore").decode("latin-1")
            out.append(base)
    s = "".join(out)
    if multilinha:
        s = "\n".join(re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in s.splitlines())
        s = re.sub(r"\n{3,}", "\n\n", s).strip()
    else:
        s = re.sub(r"\s+", " ", s).strip()
    # Controles restantes (exceto \n no modo multilinha)
    s = "".join(ch for ch in s if ch == "\n" or ord(ch) >= 0x20)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len].rstrip()
    return s or None


def somente_digitos(valor: str | None) -> str | None:
    if valor is None:
        return None
    d = re.sub(r"\D", "", str(valor))
    return d or None


def normalizar_documento(valor: str | None) -> str | None:
    """CNPJ (inclusive alfanumérico) ou CPF sem máscara, em maiúsculas."""
    if valor is None:
        return None
    s = re.sub(r"[^0-9A-Za-z]", "", str(valor)).upper()
    return s or None


def formatar_decimal(valor: Decimal | float | str) -> str:
    """Decimal com 2 casas no formato do leiaute (ponto decimal, sem milhar)."""
    d = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d:.2f}"
