"""Tributações predefinidas e abas da tabela de notas.

Uma tributação é o conjunto completo de ``CAMPOS_TRIBUTACAO`` (código do serviço, ISSQN, PIS/COFINS, total
aproximado e IBS/CBS). Os dados do prestador e o regime tributário vêm sempre das configurações. Uma aba sem
tributação usa os padrões das configurações.
"""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlmodel import Session, select

from emissor.db import Aba, LinhaTabela, Tributacao, obter_config
from emissor.nfse import codes
from emissor.nfse.models import (
    CAMPOS_PERCENTUAIS,
    CAMPOS_TRIBUTACAO,
    RETENCOES_PIS_COFINS,
    ConfigFiscal,
    aplicar_tributacao,
    iss_exige_aliquota,
    tributacao_de,
)
from emissor.nfse.tributos import ALIQ_ISS_MAX, ALIQ_ISS_MIN, pct

ABA_PADRAO = "Notas"

_ENUMS = {
    "trib_issqn": codes.TRIB_ISSQN,
    "tp_ret_issqn": codes.TP_RET_ISSQN,
    "cst_pis_cofins": codes.CST_PIS_COFINS,
    "tp_ret_pis_cofins": codes.TP_RET_PIS_COFINS,
    "ibscbs_ind_final": codes.IND_FINAL,
    "ibscbs_tp_oper": codes.TP_OPER,
    "tp_imunidade": codes.TP_IMUNIDADE_DPS,
    "tp_susp": codes.TP_SUSP,
}


class TributacaoError(ValueError):
    def __init__(self, mensagem: str, detalhes: list[str] | None = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhes = detalhes or []


def _bool(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "sim", "s")
    return bool(v)


def normalizar(d: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normaliza e confere, um a um, os campos de tributação presentes em ``d`` (os ausentes ficam de fora).

    ``p_tot_trib_sn`` sai como texto ("11.08") para caber em JSON.
    """
    dados: dict[str, Any] = {}
    erros: list[str] = []
    for k in CAMPOS_TRIBUTACAO:
        if k not in d:
            continue
        v = d[k]
        if isinstance(v, str):
            v = v.strip() or None
        dados[k] = v
    if "ibscbs" in dados:
        dados["ibscbs"] = _bool(dados["ibscbs"])
    for campo in (
        "c_trib_nac",
        "c_nbs",
        "c_trib_mun",
        "ibscbs_c_ind_op",
        "ibscbs_c_class_trib",
        "ibscbs_c_class_trib_reg",
        "n_processo",
        "n_bm",
    ):
        if dados.get(campo) is not None:
            dados[campo] = re.sub(r"[\s./-]", "", str(dados[campo])) or None
    mun, nac = dados.get("c_trib_mun"), dados.get("c_trib_nac")
    if mun and nac and len(mun) == 9 and mun.startswith(nac):
        # "080101003" = código nacional (080101) + municipal (003): o campo municipal leva só os 3 últimos.
        dados["c_trib_mun"] = mun[6:]
    if dados.get("c_loc_prestacao") and not codes.municipio(dados["c_loc_prestacao"]):
        erros.append(f"Código IBGE de município inválido em c_loc_prestacao: {dados['c_loc_prestacao']}.")
    for campo, tabela in _ENUMS.items():
        if dados.get(campo) is not None and str(dados[campo]) not in tabela:
            erros.append(f"Valor inválido para {campo}: {dados[campo]}.")
    if "p_tot_trib_sn" in dados and dados["p_tot_trib_sn"] is not None:
        try:
            p = Decimal(str(dados["p_tot_trib_sn"]).replace(",", "."))
            if not Decimal("0") <= p < Decimal("100"):
                erros.append("Percentual aproximado de tributos (Simples) deve estar entre 0 e 99,99.")
            dados["p_tot_trib_sn"] = str(p)
        except InvalidOperation:
            erros.append("Percentual aproximado de tributos inválido.")
    for campo in CAMPOS_PERCENTUAIS:
        if dados.get(campo) is None:
            continue
        p = pct(dados[campo])
        if p is None or not Decimal("0") <= p < Decimal("100"):
            erros.append(f"{_ROTULOS[campo]}: percentual inválido ({dados[campo]}). Use um número entre 0 e 99,99.")
            continue
        dados[campo] = f"{p.quantize(Decimal('0.01'))}"
    if dados.get("v_red_bcbm") is not None:
        v = _reais(dados["v_red_bcbm"])
        if v is None or v <= 0:
            erros.append(f"Redução do benefício municipal em R$ inválida ({dados['v_red_bcbm']}).")
        else:
            dados["v_red_bcbm"] = f"{v:.2f}"
    if dados.get("p_aliq_iss_atualizada_em") is not None:
        try:
            dados["p_aliq_iss_atualizada_em"] = date.fromisoformat(
                str(dados["p_aliq_iss_atualizada_em"])[:10]
            ).isoformat()
        except ValueError:
            dados["p_aliq_iss_atualizada_em"] = None
    return dados, erros


def _reais(v: Any) -> Decimal | None:
    """ "1.234,56", "1234.56" ou número → Decimal com 2 casas (None se não for valor)."""
    txt = str(v).strip().replace("R$", "").strip()
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return Decimal(txt).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


_ROTULOS = {
    "p_aliq_iss": "Alíquota do ISS retido",
    "p_aliq_pis": "Alíquota do PIS",
    "p_aliq_cofins": "Alíquota da COFINS",
    "p_ret_irrf": "IRRF retido",
    "p_ret_csll": "CSLL retida",
    "p_ret_cp": "INSS (CP) retido",
    "p_tot_trib_fed": "Tributos aproximados federais",
    "p_tot_trib_est": "Tributos aproximados estaduais",
    "p_tot_trib_mun": "Tributos aproximados municipais",
    "p_red_bcbm": "Redução do benefício municipal",
    "ibscbs_p_dif_uf": "Diferimento do IBS estadual",
    "ibscbs_p_dif_mun": "Diferimento do IBS municipal",
    "ibscbs_p_dif_cbs": "Diferimento da CBS",
}


def coerencia(t: dict[str, Any], base: ConfigFiscal | None = None) -> list[str]:
    """Regras que dependem de vários campos (recebe uma tributação completa).

    Com ``base`` (configurações do prestador) confere também as regras que dependem do regime tributário.
    """
    erros = []
    erros += _coerencia_retencoes(t)
    if base is not None:
        erros += _coerencia_regime(aplicar_tributacao(base, t))
    c_trib_nac = t.get("c_trib_nac") or ""
    if not c_trib_nac.isdigit() or len(c_trib_nac) != 6:
        erros.append("Código de Tributação Nacional deve ter 6 dígitos.")
    c_trib_mun = t.get("c_trib_mun")
    if c_trib_mun and (not c_trib_mun.isdigit() or len(c_trib_mun) != 3):
        if c_trib_mun.isdigit() and len(c_trib_mun) == 9:
            erros.append(
                f"Código de tributação municipal tem só 3 dígitos: {c_trib_mun} parece o código nacional "
                f"{c_trib_mun[:6]} seguido do municipal {c_trib_mun[6:]}. Ponha {c_trib_mun[6:]} no código municipal "
                f"e confira se o nacional é mesmo {c_trib_mun[:6]}."
            )
        else:
            erros.append(
                "Código de tributação municipal deve ter 3 dígitos (o desdobro que a prefeitura criou para o "
                "serviço, ex.: 001). Na dúvida, deixe vazio."
            )
    c_nbs = t.get("c_nbs")
    if c_nbs and (not c_nbs.isdigit() or len(c_nbs) != 9):
        erros.append("Código NBS deve ter 9 dígitos.")
    erros += _coerencia_issqn(t)
    if t.get("ibscbs"):
        if not c_nbs:
            erros.append("Para informar o IBS/CBS, o código NBS do serviço é obrigatório.")
        if t.get("ibscbs_c_ind_op") not in codes.C_IND_OP:
            erros.append(
                f"Indicador da operação do IBS/CBS não suportado: {t.get('ibscbs_c_ind_op')!r}. Escolha um da lista "
                "(o sistema emite só serviços ao próprio tomador, no Brasil, sem os dados de imóvel ou evento)."
            )
        c_class = t.get("ibscbs_c_class_trib") or ""
        if not c_class.isdigit() or len(c_class) != 6:
            erros.append("Classificação tributária do IBS/CBS (cClassTrib) deve ter 6 dígitos.")
        erros += _coerencia_grupos_ibscbs(t)
        if codes.exige_tp_oper(c_trib_nac) and not t.get("ibscbs_tp_oper"):
            erros.append(
                f"O serviço {c_trib_nac} exige o tipo de operação do IBS/CBS (quando o serviço é prestado e quando é "
                "pago): escolha um na lista."
            )
    return erros


def _coerencia_issqn(t: dict[str, Any]) -> list[str]:
    """Imunidade, exigibilidade suspensa e benefício municipal (grupo tribMun)."""
    erros = []
    trib = t.get("trib_issqn") or "1"
    if trib == "2" and not t.get("tp_imunidade"):
        erros.append("Com imunidade do ISSQN, escolha o tipo de imunidade (E0592).")
    if trib == "3":
        erros.append(
            "Exportação de serviço ainda não é emitida por este sistema (a nota precisa do tomador no exterior e dos "
            "dados de comércio exterior)."
        )
    if trib != "1" and (t.get("tp_susp") or t.get("n_processo")):
        erros.append("Só há exigibilidade suspensa em operação tributável (E0585): tire a suspensão ou mude o ISSQN.")
    if trib != "1" and t.get("n_bm"):
        erros.append("Só há benefício municipal em operação tributável (E0533): tire o benefício ou mude o ISSQN.")
    if bool(t.get("tp_susp")) != bool(t.get("n_processo")):
        erros.append("Exigibilidade suspensa: informe o tipo (decisão judicial ou processo administrativo) e o número.")
    elif t.get("n_processo") and not re.fullmatch(r"\d{30}", t["n_processo"]):
        erros.append(
            f"O número do processo da exigibilidade suspensa tem 30 dígitos (veio {len(t['n_processo'])}): complete "
            "com zeros à esquerda se a prefeitura mostrar menos."
        )
    if t.get("n_bm"):
        if not re.fullmatch(r"\d{14}", t["n_bm"]):
            erros.append("O número do benefício municipal tem 14 dígitos (o que a prefeitura cadastrou).")
        if t.get("p_red_bcbm") and t.get("v_red_bcbm"):
            erros.append("Benefício municipal: informe a redução em % ou em R$, não as duas.")
    elif t.get("p_red_bcbm") or t.get("v_red_bcbm"):
        erros.append("Para informar a redução do benefício municipal, informe o número do benefício.")
    return erros


def _coerencia_grupos_ibscbs(t: dict[str, Any]) -> list[str]:
    """Grupos que a classificação do IBS/CBS exige: tributação regular (E0965) e diferimento (E0972)."""
    erros = []
    classe = codes.classe_ibscbs(t.get("ibscbs_c_class_trib")) or {}
    reg = t.get("ibscbs_c_class_trib_reg")
    if classe.get("trib_regular"):
        if not reg:
            erros.append(
                f"A classificação {t.get('ibscbs_c_class_trib')} exige a classificação que valeria na tributação "
                "regular (E0965): escolha-a no IBS/CBS."
            )
        elif not codes.classe_ibscbs(reg):
            erros.append(f"Classificação da tributação regular {reg} não está na tabela para NFS-e.")
    elif reg:
        erros.append("A classificação escolhida não tem tributação regular (E0964): tire a classificação regular.")
    dif = [t.get(f"ibscbs_p_dif_{x}") for x in ("uf", "mun", "cbs")]
    if classe.get("diferimento"):
        if any(p is None for p in dif):
            erros.append(
                f"A classificação {t.get('ibscbs_c_class_trib')} é de diferimento (E0972): informe os percentuais "
                "diferidos do IBS estadual, do IBS municipal e da CBS."
            )
    elif any(p is not None for p in dif):
        erros.append("A classificação escolhida não permite diferimento (E0971): tire os percentuais diferidos.")
    return erros


def _coerencia_retencoes(t: dict[str, Any]) -> list[str]:
    erros = []
    tp_ret_iss = t.get("tp_ret_issqn")
    if tp_ret_iss == "3":
        erros.append(
            "ISS retido pelo intermediário ainda não é suportado (a nota precisaria dos dados do intermediário). "
            "Use “Retido pelo Tomador” ou “Não Retido”."
        )
    if tp_ret_iss in ("2", "3") and t.get("trib_issqn") not in (None, "1"):
        erros.append("Só há retenção do ISS em operação tributável (E0580): mude a tributação do ISS ou a retenção.")
    tp_ret = t.get("tp_ret_pis_cofins")
    if t.get("cst_pis_cofins"):
        pis, cofins, csll = RETENCOES_PIS_COFINS.get(tp_ret or "", (False, False, None))
        if pis and pct(t.get("p_aliq_pis")) is None:
            erros.append("PIS retido: informe a alíquota do PIS.")
        if cofins and pct(t.get("p_aliq_cofins")) is None:
            erros.append("COFINS retida: informe a alíquota da COFINS.")
        if csll and not pct(t.get("p_ret_csll")):
            erros.append("CSLL retida: informe o percentual da CSLL retida.")
        if csll is False and pct(t.get("p_ret_csll")):
            erros.append(
                "A retenção escolhida em PIS/COFINS diz que a CSLL não é retida, mas há percentual de CSLL retida "
                "(E0720). Escolha uma opção com CSLL retida ou apague o percentual."
            )
    elif pct(t.get("p_aliq_pis")) is not None or pct(t.get("p_aliq_cofins")) is not None:
        erros.append("Para informar as alíquotas do PIS/COFINS, escolha o CST do PIS/COFINS.")
    elif pct(t.get("p_ret_csll")):
        erros.append("Para informar a CSLL retida, escolha o CST e a retenção do PIS/COFINS/CSLL.")
    return erros


def _coerencia_regime(cfg: ConfigFiscal) -> list[str]:
    """Regras da Sefin que dependem do regime do prestador (Anexo I, RN DPS_NFS-e)."""
    erros = []
    retido = cfg.tp_ret_issqn in ("2", "3")
    if retido and cfg.op_simp_nac == "2":
        erros.append("MEI não pode ter ISS retido (E0583): use “Não Retido”.")
    if retido and cfg.reg_esp_trib not in (None, "0"):
        erros.append("Prestador com regime especial de tributação não pode ter ISS retido (E0588).")
    if cfg.trib_issqn in ("2", "3", "4") and cfg.reg_esp_trib not in (None, "0"):
        erros.append(
            "Com imunidade, exportação ou não incidência do ISSQN o regime especial de tributação tem de ser "
            "“Nenhum” (E0172)."
        )
    if cfg.n_bm and cfg.trib_issqn == "1":
        if cfg.op_simp_nac == "2":
            erros.append("MEI não pode informar benefício municipal (E0534).")
        elif cfg.reg_esp_trib not in (None, "0"):
            erros.append("Prestador com regime especial de tributação não pode informar benefício municipal (E0535).")
        elif (
            cfg.op_simp_nac == "3" and cfg.reg_ap_trib_sn == "1" and (cfg.c_trib_nac or "")[:4] not in ("0702", "0705")
        ):
            erros.append(
                "ME/EPP com o ISSQN apurado pelo Simples não pode informar benefício municipal (E0536), salvo nos "
                "subitens 07.02 e 07.05."
            )
    if iss_exige_aliquota(cfg):
        p = pct(cfg.p_aliq_iss)
        if p is not None and not ALIQ_ISS_MIN <= p <= ALIQ_ISS_MAX:
            erros.append(
                f"Alíquota do ISS retido deve estar entre {ALIQ_ISS_MIN:.2f}% e {ALIQ_ISS_MAX:.2f}% (E0621/E0595)."
            )
    if cfg.op_simp_nac == "3" and cfg.p_tot_trib_sn is None:
        erros.append(
            "Optante do Simples (ME/EPP) precisa do percentual aproximado de tributos do Simples Nacional (E0712)."
        )
    return erros


def validar_completa(d: dict[str, Any], base: ConfigFiscal) -> dict[str, Any]:
    """Tributação completa: os campos ausentes em ``d`` vêm de ``base`` (normalmente os padrões)."""
    dados, erros = normalizar(d)
    completa = tributacao_de(base)
    completa.update(dados)
    erros += coerencia(completa, base)
    if erros:
        raise TributacaoError("Tributação inválida.", erros)
    return completa


def config_base(s: Session) -> ConfigFiscal:
    fiscal = obter_config(s).config_fiscal()
    if fiscal is None:
        raise TributacaoError("Conclua a configuração inicial antes de usar tributações.")
    return fiscal


def resumo(t: dict[str, Any]) -> str:
    """Texto curto para listas: serviço, ISS e IBS/CBS."""
    partes = [f"serviço {t.get('c_trib_nac')}"]
    partes.append(codes.descricao(codes.TP_RET_ISSQN, t.get("tp_ret_issqn")) or "")
    if t.get("tp_ret_issqn") in ("2", "3") and t.get("p_aliq_iss"):
        partes[-1] += f" ({str(t['p_aliq_iss']).replace('.', ',')}%)"
    retencoes = [
        nome
        for campo, nome in (("p_ret_irrf", "IRRF"), ("p_ret_csll", "CSLL"), ("p_ret_cp", "INSS"))
        if pct(t.get(campo))
    ]
    if retencoes:
        partes.append("retém " + "/".join(retencoes))
    partes.append("com IBS/CBS" if t.get("ibscbs") else "sem IBS/CBS")
    return ", ".join(p for p in partes if p)


# --- abas ---------------------------------------------------------------------------------------------


def garantir_abas(s: Session) -> list[Aba]:
    """Abas em ordem; cria a primeira e adota linhas sem aba (tabelas de versões anteriores)."""
    abas = list(s.exec(select(Aba).order_by(Aba.ordem, Aba.id)))
    criada = not abas
    if criada:
        aba = Aba(nome=ABA_PADRAO, ordem=0)
        s.add(aba)
        s.flush()
        abas = [aba]
    ids = {a.id for a in abas}
    orfas = [ln for ln in s.exec(select(LinhaTabela)) if ln.aba_id not in ids]
    for ln in orfas:
        ln.aba_id = abas[0].id
        s.add(ln)
    if orfas or criada:
        s.commit()
    return abas


def resolver_aba(s: Session, aba_id: int | None) -> Aba:
    abas = garantir_abas(s)
    if aba_id is None:
        return abas[0]
    for a in abas:
        if a.id == aba_id:
            return a
    raise TributacaoError("Aba não encontrada. Recarregue a página.")


def tributacao_da_aba(s: Session, aba: Aba | None) -> tuple[str | None, dict[str, Any] | None]:
    """(nome, dados) da tributação da aba; (None, None) = padrões das configurações."""
    if aba is None or aba.tributacao_id is None:
        return None, None
    t = s.get(Tributacao, aba.tributacao_id)
    if t is None:
        raise TributacaoError(f"A tributação da aba “{aba.nome}” foi excluída. Escolha outra para a aba.")
    return t.nome, t.dados()


def config_efetiva(base: ConfigFiscal, dados: dict[str, Any] | None) -> ConfigFiscal:
    return aplicar_tributacao(base, dados)


def clientes_exclusivos(abas: list[Aba]) -> dict[str, Aba]:
    """CNPJ/CPF → aba que o tem como cliente exclusivo."""
    res: dict[str, Aba] = {}
    for a in abas:
        for doc in a.clientes():
            res.setdefault(doc, a)
    return res


def dumps(d: dict[str, Any]) -> str:
    return json.dumps(d, ensure_ascii=False)
