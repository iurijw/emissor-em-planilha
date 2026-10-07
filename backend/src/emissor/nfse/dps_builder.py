"""Montagem do XML da DPS (leiaute 1.01).

A ordem dos elementos segue o XSD oficial (``tiposComplexos_v1.01.xsd``) e reproduz a
estrutura das notas geradas pelo Emissor Web nacional (ver CLAUDE.md).
"""

from __future__ import annotations

from datetime import datetime

from lxml import etree

from emissor.config import VER_APLIC
from emissor.nfse.constants import NS_NFSE, SERIE_API_MAX, SERIE_API_MIN, VERSAO_LEIAUTE
from emissor.nfse.models import (
    DpsInput,
    Tomador,
    formatar_decimal,
    limpar_texto,
    normalizar_documento,
    somente_digitos,
)


class DpsBuildError(ValueError):
    """Dado de entrada inválido para montar a DPS (mensagem para o usuário)."""


def formatar_dh(dh: datetime) -> str:
    if dh.tzinfo is None:
        raise DpsBuildError("dhEmi precisa de fuso horário.")
    return dh.replace(microsecond=0).isoformat()


def id_dps(c_mun: str, documento_prestador: str, serie: str | int, n_dps: int) -> str:
    """``DPS`` + cMun(7) + tpInsc(1) + inscrição(14) + série(5) + nDPS(15)."""
    doc = normalizar_documento(documento_prestador) or ""
    tp_insc = "1" if len(doc) == 11 else "2"
    return f"DPS{c_mun}{tp_insc}{doc.zfill(14)}{int(serie):05d}{int(n_dps):015d}"


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS_NFSE}}}{tag}")
    if text is not None:
        el.text = text
    return el


def _opt(parent: etree._Element, tag: str, text: str | None) -> None:
    if text not in (None, ""):
        _sub(parent, tag, text)


def _pessoa(parent: etree._Element, tag: str, t: Tomador) -> None:
    el = _sub(parent, tag)
    doc = normalizar_documento(t.documento)
    if not doc or len(doc) not in (11, 14):
        raise DpsBuildError(f"Documento do tomador inválido: {t.documento!r}")
    if not doc.isdigit():
        raise DpsBuildError(
            f"CNPJ alfanumérico ({doc}) ainda não é aceito pelo leiaute oficial da NFS-e (XSD 1.01 só admite "
            "CNPJ numérico). Aguarde a atualização da Sefin para emitir para este cliente."
        )
    _sub(el, "CPF" if len(doc) == 11 else "CNPJ", doc)
    _opt(el, "IM", limpar_texto(t.inscricao_municipal, max_len=15))
    nome = limpar_texto(t.nome, max_len=300)
    if not nome:
        raise DpsBuildError("Nome do tomador é obrigatório.")
    _sub(el, "xNome", nome)
    if t.endereco:
        e = t.endereco
        end = _sub(el, "end")
        nac = _sub(end, "endNac")
        _sub(nac, "cMun", somente_digitos(e.c_mun))
        _sub(nac, "CEP", somente_digitos(e.cep))
        _sub(end, "xLgr", limpar_texto(e.logradouro, max_len=255))
        _sub(end, "nro", limpar_texto(e.numero, max_len=60) or "S/N")
        _opt(end, "xCpl", limpar_texto(e.complemento, max_len=156))
        _sub(end, "xBairro", limpar_texto(e.bairro, max_len=60))
    _opt(el, "fone", somente_digitos(t.fone))
    _opt(el, "email", limpar_texto(t.email, max_len=80))


def build_dps(inp: DpsInput) -> etree._Element:
    cfg = inp.config
    serie_int = int(inp.serie)
    if not SERIE_API_MIN <= serie_int <= SERIE_API_MAX:
        raise DpsBuildError(
            f"Série {inp.serie} fora da faixa permitida para emissão por sistema próprio "
            f"({SERIE_API_MIN}–{SERIE_API_MAX}). A série 70000 é exclusiva do Emissor Web."
        )
    if inp.n_dps < 1:
        raise DpsBuildError("Número da DPS deve ser maior que zero.")
    if inp.valor <= 0:
        raise DpsBuildError("Valor do serviço deve ser maior que zero.")
    descricao = limpar_texto(inp.descricao, multilinha=True, max_len=2000)
    if not descricao:
        raise DpsBuildError("Descrição do serviço é obrigatória.")

    dps = etree.Element(f"{{{NS_NFSE}}}DPS", nsmap={None: NS_NFSE})
    dps.set("versao", VERSAO_LEIAUTE)
    inf = _sub(dps, "infDPS")
    inf.set("Id", id_dps(cfg.c_mun_emissor, cfg.cnpj, serie_int, inp.n_dps))

    _sub(inf, "tpAmb", inp.ambiente.value)
    _sub(inf, "dhEmi", formatar_dh(inp.dh_emi))
    _sub(inf, "verAplic", VER_APLIC)
    _sub(inf, "serie", str(serie_int))
    _sub(inf, "nDPS", str(inp.n_dps))
    # Competência = data da emissão (requisito do usuário).
    _sub(inf, "dCompet", inp.dh_emi.date().isoformat())
    _sub(inf, "tpEmit", cfg.tp_emit)
    _sub(inf, "cLocEmi", cfg.c_mun_emissor)

    # Prestador
    prest = _sub(inf, "prest")
    doc_prest = normalizar_documento(cfg.cnpj)
    _sub(prest, "CPF" if doc_prest and len(doc_prest) == 11 else "CNPJ", doc_prest)
    _opt(prest, "IM", limpar_texto(cfg.inscricao_municipal, max_len=15))
    _opt(prest, "fone", somente_digitos(cfg.fone))
    _opt(prest, "email", limpar_texto(cfg.email, max_len=80))
    reg = _sub(prest, "regTrib")
    _sub(reg, "opSimpNac", cfg.op_simp_nac)
    if cfg.op_simp_nac == "3":
        _opt(reg, "regApTribSN", cfg.reg_ap_trib_sn)
    _sub(reg, "regEspTrib", cfg.reg_esp_trib)

    # Tomador
    if inp.tomador is not None:
        _pessoa(inf, "toma", inp.tomador)

    # Serviço
    serv = _sub(inf, "serv")
    loc = _sub(serv, "locPrest")
    _sub(loc, "cLocPrestacao", cfg.c_loc_prestacao or cfg.c_mun_emissor)
    c_serv = _sub(serv, "cServ")
    _sub(c_serv, "cTribNac", cfg.c_trib_nac)
    _opt(c_serv, "cTribMun", cfg.c_trib_mun)
    _sub(c_serv, "xDescServ", descricao)
    _opt(c_serv, "cNBS", cfg.c_nbs)

    # Valores
    valores = _sub(inf, "valores")
    v_serv_prest = _sub(valores, "vServPrest")
    _sub(v_serv_prest, "vServ", formatar_decimal(inp.valor))
    trib = _sub(valores, "trib")
    trib_mun = _sub(trib, "tribMun")
    _sub(trib_mun, "tribISSQN", cfg.trib_issqn)
    _sub(trib_mun, "tpRetISSQN", cfg.tp_ret_issqn)
    if cfg.cst_pis_cofins:
        trib_fed = _sub(trib, "tribFed")
        pc = _sub(trib_fed, "piscofins")
        _sub(pc, "CST", cfg.cst_pis_cofins)
        _opt(pc, "tpRetPisCofins", cfg.tp_ret_pis_cofins)
    tot = _sub(trib, "totTrib")
    if cfg.p_tot_trib_sn is not None:
        _sub(tot, "pTotTribSN", formatar_decimal(cfg.p_tot_trib_sn))
    else:
        _sub(tot, "indTotTrib", "0")

    return dps


def to_bytes(el: etree._Element) -> bytes:
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8")
