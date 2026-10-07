"""Estruturas de entrada para montar a DPS.

``ConfigFiscal`` guarda os padrões do prestador (preenchidos no onboarding a partir
dos XMLs atuais); ``Tomador`` vem do cadastro de clientes; valor e descrição vêm da
linha da tabela.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
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
    # Emissão
    tp_emit: str = "1"


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
