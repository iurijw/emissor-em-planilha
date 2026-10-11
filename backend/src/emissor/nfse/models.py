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
    c_trib_nac: str = "171901"
    c_trib_mun: str | None = None
    c_nbs: str | None = "113022100"
    # Tributação
    trib_issqn: str = "1"
    tp_ret_issqn: str = "1"
    cst_pis_cofins: str | None = "00"  # None → não envia grupo tribFed
    tp_ret_pis_cofins: str | None = None
    p_tot_trib_sn: Decimal | None = Decimal("11.08")  # None → indTotTrib = 0
    # IBS/CBS (reforma tributária): grupo infDPS/IBSCBS, com destinatário = tomador (indDest 0)
    ibscbs: bool = False  # False → DPS sem o grupo (aceito sem rejeição até 31/12/2026)
    ibscbs_c_ind_op: str = "100301"
    ibscbs_c_class_trib: str = "000001"  # o CST vai como os 3 primeiros dígitos (E0959)
    ibscbs_ind_final: str | None = None  # opcional; será descontinuado pela NT 005
    # Emissão
    tp_emit: str = "1"


# Campos que mudam de uma tributação para outra (abas e nota avulsa). Os demais (prestador, regime,
# tpEmit) valem sempre os das configurações.
CAMPOS_TRIBUTACAO = (
    "c_loc_prestacao",
    "c_trib_nac",
    "c_trib_mun",
    "c_nbs",
    "trib_issqn",
    "tp_ret_issqn",
    "cst_pis_cofins",
    "tp_ret_pis_cofins",
    "p_tot_trib_sn",
    "ibscbs",
    "ibscbs_c_ind_op",
    "ibscbs_c_class_trib",
    "ibscbs_ind_final",
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
    documento: str  # CNPJ (14) ou CPF (11), só dígitos/letras
    nome: str
    endereco: Endereco | None = None
    fone: str | None = None
    email: str | None = None
    inscricao_municipal: str | None = None

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
    extras: dict = field(default_factory=dict)


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
