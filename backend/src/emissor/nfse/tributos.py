"""Valores dos tributos de uma nota a partir dos percentuais da tributação.

A DPS leva o PIS/COFINS com base, alíquota e valor (E0694/E0696: valor = base x alíquota) e as retenções federais
(IRRF, CSLL e CP) só em R$; a tributação guarda percentuais e o valor de cada nota sai daqui. A alíquota do ISS
(``pAliq``) só vai quando a Sefin exige (``iss_exige_aliquota``); o valor do ISS é calculado pela Sefin.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from emissor.nfse.models import RETENCOES_PIS_COFINS, ConfigFiscal, iss_exige_aliquota

CENTAVO = Decimal("0.01")
ALIQ_ISS_MIN = Decimal("1.80")  # E0621/E0628: mínimo para ISS retido de ME/EPP com ISS pelo Simples
ALIQ_ISS_MAX = Decimal("5.00")  # E0595


def pct(v: str | Decimal | None) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v).replace(",", "."))
    except InvalidOperation:
        return None


def _de(valor: Decimal, p: Decimal | None) -> Decimal | None:
    if p is None:
        return None
    return (valor * p / 100).quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass
class Tributos:
    p_aliq_iss: Decimal | None = None
    v_iss_retido: Decimal | None = None  # estimativa (a Sefin calcula); só quando a alíquota vai na DPS
    v_bc_pis_cofins: Decimal | None = None
    p_aliq_pis: Decimal | None = None
    p_aliq_cofins: Decimal | None = None
    v_pis: Decimal | None = None
    v_cofins: Decimal | None = None
    v_ret_irrf: Decimal | None = None
    v_ret_csll: Decimal | None = None
    v_ret_cp: Decimal | None = None
    pis_retido: bool = False
    cofins_retido: bool = False

    @property
    def total_retido(self) -> Decimal:
        partes = [self.v_ret_irrf, self.v_ret_csll, self.v_ret_cp, self.v_iss_retido]
        if self.pis_retido:
            partes.append(self.v_pis)
        if self.cofins_retido:
            partes.append(self.v_cofins)
        return sum((p for p in partes if p), Decimal("0"))


def calcular(cfg: ConfigFiscal, valor: Decimal) -> tuple[Tributos, list[str]]:
    """Tributos da nota de ``valor`` e os problemas que fariam a Sefin rejeitá-la."""
    t = Tributos()
    erros: list[str] = []
    if iss_exige_aliquota(cfg):
        # Sem a alíquota, quem chama decide (a emissão pergunta antes; ``dps_builder`` recusa).
        t.p_aliq_iss = pct(cfg.p_aliq_iss)
        t.v_iss_retido = _de(valor, t.p_aliq_iss)
    if cfg.cst_pis_cofins:
        t.p_aliq_pis, t.p_aliq_cofins = pct(cfg.p_aliq_pis), pct(cfg.p_aliq_cofins)
        if t.p_aliq_pis is not None or t.p_aliq_cofins is not None:
            t.v_bc_pis_cofins = valor
            t.v_pis = _de(valor, t.p_aliq_pis)
            t.v_cofins = _de(valor, t.p_aliq_cofins)
        pis, cofins, _csll = RETENCOES_PIS_COFINS.get(cfg.tp_ret_pis_cofins or "", (False, False, None))
        t.pis_retido, t.cofins_retido = pis, cofins
    t.v_ret_csll = _de(valor, pct(cfg.p_ret_csll))
    t.v_ret_irrf = _de(valor, pct(cfg.p_ret_irrf))
    t.v_ret_cp = _de(valor, pct(cfg.p_ret_cp))
    # E0699–E0701: retenção informada tem de ser > 0; um valor que arredonda para zero simplesmente não vai.
    for campo in ("v_ret_irrf", "v_ret_csll", "v_ret_cp"):
        if getattr(t, campo) is not None and getattr(t, campo) <= 0:
            setattr(t, campo, None)
    if t.total_retido > valor:
        erros.append(
            f"As retenções (R$ {t.total_retido:.2f}) passam do valor do serviço (R$ {valor:.2f}). "
            "Confira os percentuais da tributação."
        )
    return t, erros
