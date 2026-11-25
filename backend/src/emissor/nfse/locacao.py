"""Locação, cessão onerosa e arrendamento de bem imóvel (subitem 99.03, leiaute da NT 009).

Na DPS vão no grupo ``IBSCBS/imovel``: município do imóvel, ``gLocacao`` (percentual de copropriedade, valor total da
operação, descontos totais e vencimento original) e uma ``gUnidImob`` (inscrição imobiliária, CIB ou endereço e as
parcelas que não entram na base do IBS/CBS, como IPTU e condomínio). Regras do Anexo I da Produção Restrita
(2026-10-20), aba RN DPS_NFS-e: E0433/E0434/E0443 (valores × copropriedade), E0902, E0932/E0937/E0938, E0933,
E0940, E0944, E0949/E0954, E0966 e E0532 (sem ISSQN).

Os dados ficam num dicionário simples (``DadosNota.locacao`` e ``LinhaTabela.locacao_json``) com as chaves de
``CAMPOS``, iguais às colunas da planilha.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from emissor.nfse import codes

CEM = Decimal("100")
CENTAVO = Decimal("0.01")

# Parcelas que não entram na base do IBS/CBS (tpAjusteBCLocImoveis), na ordem das colunas.
AJUSTES = (("iptu", "01"), ("emolumentos", "02"), ("condominio", "03"), ("redutor_social", "04"), ("outras", "99"))

# Chave → rótulo (colunas da planilha e mensagens).
CAMPOS = {
    # Na ordem das colunas (planilha e tela): o imóvel (obrigatório: CEP e endereço, ou o CIB), as parcelas mais comuns
    # e o resto.
    "cep": "CEP do imóvel",
    "logradouro": "Endereço do imóvel",
    "numero": "Nº do imóvel",
    "bairro": "Bairro do imóvel",
    "complemento": "Complemento do imóvel",
    "iptu": "IPTU",
    "condominio": "Condomínio",
    "venc": "Vencimento",
    "p_copropriedade": "% copropriedade",
    "v_tot_oper": "Valor total do aluguel",
    "insc_imob_fisc": "Inscrição imobiliária",
    "c_cib": "CIB",
    "emolumentos": "Emolumentos",
    "redutor_social": "Redutor social",
    "outras": "Outras parcelas",
    "outras_descricao": "Descrição das outras parcelas",
    "c_mun": "Município do imóvel",
}
# Cabeçalhos aceitos na importação, além do rótulo (comparados sem acento e sem caixa).
SINONIMOS = {
    "cep": ("cep imovel", "cep do imovel"),
    "logradouro": ("endereco imovel", "logradouro do imovel", "logradouro imovel", "rua do imovel", "imovel"),
    "numero": ("numero do imovel", "numero imovel", "no do imovel", "n do imovel", "nro do imovel"),
    "complemento": ("complemento imovel",),
    "bairro": ("bairro imovel",),
    "insc_imob_fisc": ("inscricao imobiliaria fiscal", "inscricao do imovel", "inscricao iptu", "indicacao fiscal"),
    "c_cib": ("cib", "codigo cib", "cadastro imobiliario brasileiro"),
    "p_copropriedade": ("copropriedade", "% copropriedade", "percentual de copropriedade", "% de copropriedade"),
    "v_tot_oper": ("valor total do aluguel", "valor total da operacao", "aluguel total"),
    "venc": ("vencimento", "data de vencimento", "dia do vencimento", "vencimento original"),
    "iptu": ("iptu",),
    "condominio": ("condominio",),
    "emolumentos": ("emolumentos", "emolumento"),
    "redutor_social": ("redutor social",),
    "outras": ("outras parcelas",),
    "outras_descricao": ("descricao das outras parcelas", "descricao outras parcelas"),
}
CAMPOS_VALOR = ("p_copropriedade", "v_tot_oper", *(k for k, _ in AJUSTES))


def _dec(v) -> Decimal | None:
    """ "1.234,56", "1234.56", "100%" ou número → Decimal (None se vazio ou inválido)."""
    if v in (None, ""):
        return None
    txt = str(v).strip().replace("R$", "").replace("%", "").strip()
    if not txt:
        return None
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return Decimal(txt)
    except InvalidOperation:
        return None


def limpar(d: dict | None) -> dict | None:
    """Só as chaves conhecidas, como texto sem espaço nas pontas; None se não sobrou nada."""
    if not d:
        return None
    res = {}
    for k in CAMPOS:
        v = d.get(k)
        if v is None:
            continue
        v = str(v).strip()
        if v:
            res[k] = v
    return res or None


def _venc(txt: str | None, hoje: date) -> tuple[str | None, str | None]:
    """Vencimento original: ``AAAA-MM-DD``, ``DD/MM/AAAA`` ou só o dia (no mês da emissão). (data, erro)."""
    t = (txt or "").strip()
    if not t:
        return None, None
    try:
        if re.fullmatch(r"\d{1,2}", t):
            dia = int(t)
            return date(hoje.year, hoje.month, dia).isoformat(), None
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", t):
            return date(int(m[3]), int(m[2]), int(m[1])).isoformat(), None
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            return date.fromisoformat(t).isoformat(), None
    except ValueError:
        pass
    return None, f"Vencimento inválido: {t!r} (use DD/MM/AAAA ou só o dia do mês)."


@dataclass
class Valores:
    """Valores da locação calculados para uma nota."""

    p_copropriedade: Decimal
    v_tot_oper: Decimal
    v_desc_incond_tot: Decimal | None
    v_desc_cond_tot: Decimal | None
    d_venc_orig: str | None
    ajustes: list[tuple[str, str | None, Decimal]]  # (tpAjusteBCLocImoveis, descrição, valor)


def _total(parte: Decimal, p: Decimal) -> Decimal:
    """Valor total de que ``parte`` é ``p``% (a Sefin confere ``parte = p x total`` com R$ 0,01 de tolerância)."""
    if p == CEM:
        return parte
    return (parte * CEM / p).quantize(CENTAVO, ROUND_HALF_EVEN)


def _parte(total: Decimal, p: Decimal) -> Decimal:
    return (total * p / CEM).quantize(CENTAVO, ROUND_HALF_EVEN)


def calcular(
    loc: dict | None,
    valor: Decimal,
    hoje: date,
    *,
    c_nbs: str | None,
    v_desc_incond: Decimal | None = None,
    v_desc_cond: Decimal | None = None,
) -> tuple[Valores | None, list[str]]:
    """Valores do grupo gLocacao/gUnidImob e os erros (o que a Sefin recusaria ou o que falta).

    ``valor`` é o valor do serviço da nota (``vServ``): a parte do locador. Sem o valor total do aluguel, ele é o
    valor dividido pela copropriedade (com 100%, o próprio valor).
    """
    loc = loc or {}
    erros: list[str] = []
    p = _dec(loc.get("p_copropriedade"))
    if loc.get("p_copropriedade") and p is None:
        erros.append(f"Percentual de copropriedade inválido: {loc.get('p_copropriedade')!r}.")
    p = CEM if p is None else p.quantize(CENTAVO)
    if not Decimal("0.01") <= p <= CEM:
        erros.append("O percentual de copropriedade vai de 0,01% a 100% (E0940); com um só locador, 100%.")
        p = CEM
    tot = _dec(loc.get("v_tot_oper"))
    if loc.get("v_tot_oper") and tot is None:
        erros.append(f"Valor total do aluguel inválido: {loc.get('v_tot_oper')!r}.")
    if tot is None:
        tot = _total(valor, p)
    elif abs(_parte(tot, p) - valor) > CENTAVO:
        erros.append(
            f"O valor da nota (R$ {valor:.2f}) tem de ser o valor total do aluguel (R$ {tot:.2f}) vezes a "
            f"copropriedade ({p}%) = R$ {_parte(tot, p):.2f} (E0433)."
        )
    tot = tot.quantize(CENTAVO)
    desc_incond = _total(v_desc_incond, p) if v_desc_incond else None
    desc_cond = _total(v_desc_cond, p) if v_desc_cond else None
    venc, erro = _venc(loc.get("venc"), hoje)
    if erro:
        erros.append(erro)
    ajustes = []
    for chave, tp in AJUSTES:
        txt = loc.get(chave)
        v = _dec(txt)
        if txt and (v is None or v < 0):
            erros.append(f"{CAMPOS[chave]} inválido: {txt!r}.")
            continue
        if not v:
            continue
        v = v.quantize(CENTAVO)
        if v > tot:
            erros.append(f"{CAMPOS[chave]} (R$ {v:.2f}) maior que o valor total do aluguel (E0966).")
        if tp == "04" and c_nbs != codes.NBS_IMOVEL_RESIDENCIAL:
            erros.append("O redutor social só vale para imóvel residencial (NBS 1.1002.10.00, E0944).")
        descricao = None
        if tp == "99":
            descricao = (loc.get("outras_descricao") or "").strip()[:150] or None
            if not descricao:
                erros.append("Outras parcelas fora da base do IBS/CBS: descreva quais são (E0949).")
        ajustes.append((tp, descricao, v))
    if sum((v for *_, v in ajustes), Decimal(0)) > tot:
        erros.append("As parcelas fora da base do IBS/CBS somam mais que o valor total do aluguel (E1547).")
    return Valores(p, tot, desc_incond, desc_cond, venc, ajustes), erros


def erros_unidade(loc: dict | None) -> list[str]:
    """Unidade imobiliária (E0938): o CIB ou o endereço do imóvel com CEP e logradouro (sem número vai S/N)."""
    loc = loc or {}
    cib = re.sub(r"[\s.-]", "", loc.get("c_cib") or "").upper()
    if cib:
        return [] if len(cib) == 8 else ["O CIB do imóvel tem 8 caracteres (7 + dígito verificador, E0933)."]
    erros = []
    cep = re.sub(r"\D", "", loc.get("cep") or "")
    if len(cep) != 8:
        erros.append("CEP do imóvel")
    if not (loc.get("logradouro") or "").strip():
        erros.append("endereço do imóvel")
    if erros:
        return [f"Locação: falta o {', o '.join(erros)} (ou o CIB do imóvel)."]
    return []


def de_nfse(d) -> dict | None:
    """Dados do imóvel de uma NFS-e de locação já emitida (``NFSeDoc``), com as chaves de ``CAMPOS``.

    Para a próxima nota (onboarding): o vencimento vira só o dia do mês e o valor total só vai com copropriedade
    menor que 100% (com 100% ele é o próprio valor da nota). Sem o grupo do imóvel, None.
    """
    base = "IBSCBS/imovel"
    if d.el(f"DPS/infDPS/{base}") is None:
        return None
    unid = f"{base}/gUnidImob"
    loc = {
        "c_mun": d.dps(f"{base}/cMun"),
        "insc_imob_fisc": d.dps(f"{unid}/inscImobFisc"),
        "c_cib": d.dps(f"{unid}/cCIB"),
        "cep": d.dps(f"{unid}/end/CEP"),
        "logradouro": d.dps(f"{unid}/end/xLgr"),
        "numero": d.dps(f"{unid}/end/nro"),
        "complemento": d.dps(f"{unid}/end/xCpl"),
        "bairro": d.dps(f"{unid}/end/xBairro"),
    }
    if loc["numero"] and loc["numero"].upper() in ("S/N", "SN"):
        loc["numero"] = None
    p = _dec(d.dps(f"{base}/gLocacao/pCopropriedade"))
    if p is not None and p != CEM:
        loc["p_copropriedade"] = _br(p)
        loc["v_tot_oper"] = _br(_dec(d.dps(f"{base}/gLocacao/vTotOper")))
    venc = d.dps(f"{base}/gLocacao/dVencOrig")
    if venc and re.fullmatch(r"\d{4}-\d{2}-\d{2}", venc):
        loc["venc"] = str(int(venc[8:10]))
    por_tipo = {tp: chave for chave, tp in AJUSTES}
    el_unid = d.el(f"DPS/infDPS/{unid}")
    for aj in el_unid.iterchildren() if el_unid is not None else ():
        if not isinstance(aj.tag, str) or not aj.tag.endswith("gAjusteBCLocImoveis"):
            continue
        campos = {etree_local(c): (c.text or "").strip() for c in aj.iterchildren() if isinstance(c.tag, str)}
        chave = por_tipo.get(campos.get("tpAjusteBCLocImoveis", ""))
        v = _dec(campos.get("vAjusteBCLocImoveis"))
        if chave and v:
            anterior = _dec(loc.get(chave)) or Decimal(0)
            loc[chave] = _br(anterior + v)
            if chave == "outras" and campos.get("xTpAjusteBCLocImoveis"):
                loc["outras_descricao"] = campos["xTpAjusteBCLocImoveis"]
    return limpar(loc)


def _br(v: Decimal | None) -> str | None:
    """Decimal → "1234,56" (como se digita na planilha)."""
    if v is None:
        return None
    return f"{v:.2f}".replace(".", ",")


def etree_local(el) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1]
