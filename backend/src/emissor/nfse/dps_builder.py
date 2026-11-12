"""Montagem do XML da DPS (leiaute 1.01).

A ordem dos elementos segue o XSD oficial (``tiposComplexos_v1.01.xsd``) e reproduz a
estrutura das notas geradas pelo Emissor Web nacional (ver CLAUDE.md).
"""

from __future__ import annotations

import re
from datetime import datetime

from lxml import etree

from emissor.config import VER_APLIC
from emissor.nfse import codes
from emissor.nfse.codes import TP_OPER, classe_ibscbs, exige_tp_oper
from emissor.nfse.constants import (
    LEIAUTE_NT009,
    NS_NFSE,
    SERIE_API_MAX,
    SERIE_API_MIN,
    VERSAO_LEIAUTE,
    leiaute,
)
from emissor.nfse.models import (
    ConfigFiscal,
    DadosNota,
    DpsInput,
    Tomador,
    exige_comext,
    formatar_decimal,
    iss_exige_aliquota,
    limpar_texto,
    normalizar_documento,
    somente_digitos,
)
from emissor.nfse.tributos import calcular as calcular_tributos
from emissor.nfse.tributos import pct


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


def _pessoa_exterior(parent: etree._Element, tag: str, t: Tomador) -> None:
    """Tomador no exterior: NIF (ou o motivo de não ter, E0226) e endereço no exterior (E0242)."""
    e = t.exterior
    el = _sub(parent, tag)
    nif = limpar_texto(t.documento, max_len=40)
    if nif:
        _sub(el, "NIF", nif)
    elif t.c_nao_nif in ("1", "2"):
        _sub(el, "cNaoNIF", t.c_nao_nif)
    else:
        raise DpsBuildError("Tomador no exterior: informe o NIF ou o motivo de não ter (dispensado ou não exigido).")
    nome = limpar_texto(t.nome, max_len=300)
    if not nome:
        raise DpsBuildError("Nome do tomador é obrigatório.")
    _sub(el, "xNome", nome)
    campos = {
        "país": e.c_pais,
        "código postal": e.c_end_post,
        "cidade": e.cidade,
        "estado/província": e.estado,
        "logradouro": e.logradouro,
        "bairro": e.bairro,
    }
    faltam = [k for k, v in campos.items() if not limpar_texto(v)]
    if faltam:
        raise DpsBuildError(f"Endereço do tomador no exterior incompleto: falta {', '.join(faltam)}.")
    if not re.fullmatch(r"[A-Z]{2}", e.c_pais) or e.c_pais == "BR":
        raise DpsBuildError(f"País do tomador inválido: {e.c_pais!r} (código ISO de 2 letras, diferente de BR).")
    end = _sub(el, "end")
    ext = _sub(end, "endExt")
    _sub(ext, "cPais", e.c_pais)
    _sub(ext, "cEndPost", limpar_texto(e.c_end_post, max_len=11))
    _sub(ext, "xCidade", limpar_texto(e.cidade, max_len=60))
    _sub(ext, "xEstProvReg", limpar_texto(e.estado, max_len=60))
    _sub(end, "xLgr", limpar_texto(e.logradouro, max_len=255))
    _sub(end, "nro", limpar_texto(e.numero, max_len=60) or "S/N")
    _opt(end, "xCpl", limpar_texto(e.complemento, max_len=156))
    _sub(end, "xBairro", limpar_texto(e.bairro, max_len=60))
    _opt(el, "fone", somente_digitos(t.fone))
    _opt(el, "email", limpar_texto(t.email, max_len=80))


def _comext(serv: etree._Element, cfg: ConfigFiscal, nota: DadosNota) -> None:
    campos = {
        "modo de prestação": cfg.comex_md_prestacao,
        "vínculo entre as partes": cfg.comex_vinc_prest,
        "moeda": cfg.comex_tp_moeda,
        "mecanismo de apoio do prestador": cfg.comex_mec_af_p,
        "mecanismo de apoio do tomador": cfg.comex_mec_af_t,
        "movimentação temporária de bens": cfg.comex_mov_temp_bens,
        "envio ao MDIC": cfg.comex_mdic,
    }
    faltam = [k for k, v in campos.items() if not v]
    if faltam:
        raise DpsBuildError(
            "Operação com o exterior: a nota precisa dos dados de comércio exterior (regra E0330). Falta na "
            f"tributação: {', '.join(faltam)}."
        )
    if nota.v_serv_moeda is None or nota.v_serv_moeda <= 0:
        raise DpsBuildError("Operação com o exterior: informe o valor do serviço na moeda estrangeira.")
    c = _sub(serv, "comExt")
    _sub(c, "mdPrestacao", cfg.comex_md_prestacao)
    _sub(c, "vincPrest", cfg.comex_vinc_prest)
    _sub(c, "tpMoeda", cfg.comex_tp_moeda)
    _sub(c, "vServMoeda", formatar_decimal(nota.v_serv_moeda))
    _sub(c, "mecAFComexP", cfg.comex_mec_af_p)
    _sub(c, "mecAFComexT", cfg.comex_mec_af_t)
    _sub(c, "movTempBens", cfg.comex_mov_temp_bens)
    _opt(c, "nDI", limpar_texto(nota.n_di, max_len=12))
    _opt(c, "nRE", limpar_texto(nota.n_re, max_len=12))
    _sub(c, "mdic", cfg.comex_mdic)


def _pessoa(parent: etree._Element, tag: str, t: Tomador) -> None:
    if t.exterior is not None:
        _pessoa_exterior(parent, tag, t)
        return
    el = _sub(parent, tag)
    doc = normalizar_documento(t.documento)
    if not doc or len(doc) not in (11, 14):
        raise DpsBuildError(f"Documento do tomador inválido: {t.documento!r}")
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
    nt009 = leiaute(inp.ambiente, inp.dh_emi) == LEIAUTE_NT009
    if nt009:
        _sub(inf, "finNFSe", "0")  # NT 009: obrigatório em infDPS (antes ficava no grupo IBSCBS)
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
    if cfg.c_pais_prestacao:
        _sub(loc, "cPaisPrestacao", cfg.c_pais_prestacao)
    else:
        _sub(loc, "cLocPrestacao", cfg.c_loc_prestacao or cfg.c_mun_emissor)
    c_serv = _sub(serv, "cServ")
    _sub(c_serv, "cTribNac", cfg.c_trib_nac)
    _opt(c_serv, "cTribMun", cfg.c_trib_mun)
    if nt009:  # NT 009: xDescServ passa a ser o último de cServ
        _opt(c_serv, "cNBS", cfg.c_nbs)
        _sub(c_serv, "xDescServ", descricao)
    else:
        _sub(c_serv, "xDescServ", descricao)
        _opt(c_serv, "cNBS", cfg.c_nbs)
    exterior = inp.tomador is not None and inp.tomador.exterior is not None
    if exige_comext(cfg, exterior):
        _comext(serv, cfg, inp.nota)
    montar_obra(serv, cfg, inp.nota)
    montar_evento(serv, cfg, inp.nota)
    _info_compl(serv, inp.nota)

    # Valores
    valores = _sub(inf, "valores")
    v_serv_prest = _sub(valores, "vServPrest")
    _sub(v_serv_prest, "vServ", formatar_decimal(inp.valor))
    nota = inp.nota
    if nota.v_desc_incond or nota.v_desc_cond:
        desc = _sub(valores, "vDescCondIncond")
        if nota.v_desc_incond:
            _sub(desc, "vDescIncond", formatar_decimal(nota.v_desc_incond))
        if nota.v_desc_cond:
            _sub(desc, "vDescCond", formatar_decimal(nota.v_desc_cond))
    ajuste = nota.ajuste_bc(inp.valor)
    if ajuste is not None:
        erro = erro_ajuste_bc(cfg)
        if erro:
            raise DpsBuildError(erro)
        # v1_01: vDedRed{pDR | vDR}; NT 009: vAjusteBC{pAjusteBCISSQN | vAjusteBCISSQN} (mesmo lugar, nomes novos)
        aj = _sub(valores, "vAjusteBC" if nt009 else "vDedRed")
        if nota.ajuste_bc_v is not None:
            _sub(aj, "vAjusteBCISSQN" if nt009 else "vDR", formatar_decimal(nota.ajuste_bc_v))
        else:
            _sub(aj, "pAjusteBCISSQN" if nt009 else "pDR", formatar_decimal(nota.ajuste_bc_p))
    tributos, erros = calcular_tributos(cfg, inp.valor, nota.v_desc_incond, nota.v_desc_cond, ajuste)
    if iss_exige_aliquota(cfg) and tributos.p_aliq_iss is None:
        erros.insert(0, "Informe a alíquota do ISS retido na tributação (obrigatória para ISS retido no Simples).")
    if erros:
        raise DpsBuildError(" ".join(erros))
    trib = _sub(valores, "trib")
    trib_mun = _sub(trib, "tribMun")
    _sub(trib_mun, "tribISSQN", cfg.trib_issqn)
    if cfg.trib_issqn == "3" and not cfg.c_pais_prestacao:
        if not cfg.c_pais_result:
            raise DpsBuildError("Exportação de serviço: informe o país onde se deu o resultado do serviço (E0590).")
        _sub(trib_mun, "cPaisResult", cfg.c_pais_result)
    if cfg.trib_issqn == "2":
        if cfg.tp_imunidade not in ("1", "2", "3", "4", "5"):
            raise DpsBuildError("Com imunidade do ISSQN, escolha o tipo de imunidade na tributação (regra E0592).")
        _sub(trib_mun, "tpImunidade", cfg.tp_imunidade)
    if cfg.trib_issqn == "1" and cfg.tp_susp:
        susp = _sub(trib_mun, "exigSusp")
        _sub(susp, "tpSusp", cfg.tp_susp)
        _sub(susp, "nProcesso", somente_digitos(cfg.n_processo) or "")
    if cfg.trib_issqn == "1" and cfg.n_bm:
        bm = _sub(trib_mun, "BM")
        _sub(bm, "nBM", somente_digitos(cfg.n_bm) or "")
        if pct(cfg.v_red_bcbm):
            _sub(bm, "vRedBCBM", formatar_decimal(pct(cfg.v_red_bcbm)))
        elif pct(cfg.p_red_bcbm):
            _sub(bm, "pRedBCBM", formatar_decimal(pct(cfg.p_red_bcbm)))
    _sub(trib_mun, "tpRetISSQN", cfg.tp_ret_issqn)
    if tributos.p_aliq_iss is not None:
        _sub(trib_mun, "pAliq", formatar_decimal(tributos.p_aliq_iss))
    if cfg.cst_pis_cofins or tributos.v_ret_cp or tributos.v_ret_irrf or tributos.v_ret_csll:
        trib_fed = _sub(trib, "tribFed")
        if cfg.cst_pis_cofins:
            pc = _sub(trib_fed, "piscofins")
            _sub(pc, "CST", cfg.cst_pis_cofins)
            for tag, v in (
                ("vBCPisCofins", tributos.v_bc_pis_cofins),
                ("pAliqPis", tributos.p_aliq_pis),
                ("pAliqCofins", tributos.p_aliq_cofins),
                ("vPis", tributos.v_pis),
                ("vCofins", tributos.v_cofins),
            ):
                if v is not None:
                    _sub(pc, tag, formatar_decimal(v))
            _opt(pc, "tpRetPisCofins", cfg.tp_ret_pis_cofins)
        for tag, v in (
            ("vRetCP", tributos.v_ret_cp),
            ("vRetIRRF", tributos.v_ret_irrf),
            ("vRetCSLL", tributos.v_ret_csll),
        ):
            if v is not None:
                _sub(trib_fed, tag, formatar_decimal(v))
    tot = _sub(trib, "totTrib")
    if cfg.op_simp_nac == "1":
        # Não optante: pTotTribSN e indTotTrib são proibidos (E0713).
        p_tot = _sub(tot, "pTotTrib")
        for tag, v in (
            ("pTotTribFed", cfg.p_tot_trib_fed),
            ("pTotTribEst", cfg.p_tot_trib_est),
            ("pTotTribMun", cfg.p_tot_trib_mun),
        ):
            _sub(p_tot, tag, formatar_decimal(pct(v) or 0))
    elif cfg.op_simp_nac == "3" and cfg.p_tot_trib_sn is not None:
        _sub(tot, "pTotTribSN", formatar_decimal(cfg.p_tot_trib_sn))
    else:
        # MEI (pTotTribSN proibido, E0710). ME/EPP sem o percentual é recusado localmente (E0712).
        _sub(tot, "indTotTrib", "0")

    if cfg.ibscbs:
        _ibscbs(inf, cfg, nt009)

    return dps


def _ibscbs(inf: etree._Element, cfg: ConfigFiscal, nt009: bool) -> None:
    """Grupo IBS/CBS (``TCRTCInfoIBSCBS``): NFS-e regular, destinatário = tomador.

    Os valores e alíquotas não vão na DPS: a Sefin calcula e devolve em ``infNFSe/IBSCBS``. No leiaute da NT 009
    ``finNFSe`` sai daqui (vai para infDPS) e ``CST``/``cClassTrib`` sobem de ``trib/gIBSCBS`` para ``trib``.
    """
    if not cfg.c_nbs:
        raise DpsBuildError("Com IBS/CBS informado, o código NBS do serviço é obrigatório (regra E0322).")
    if not re.fullmatch(r"\d{6}", cfg.ibscbs_c_ind_op or ""):
        raise DpsBuildError(f"Código indicador da operação do IBS/CBS inválido: {cfg.ibscbs_c_ind_op!r} (6 dígitos).")
    if not re.fullmatch(r"\d{6}", cfg.ibscbs_c_class_trib or ""):
        raise DpsBuildError(f"Classificação tributária do IBS/CBS inválida: {cfg.ibscbs_c_class_trib!r} (6 dígitos).")
    tp_oper = cfg.ibscbs_tp_oper if exige_tp_oper(cfg.c_trib_nac) else None  # fora dos subitens: E0904
    if exige_tp_oper(cfg.c_trib_nac) and tp_oper not in TP_OPER:
        raise DpsBuildError(
            f"O serviço {cfg.c_trib_nac} exige o tipo de operação do IBS/CBS (tpOper, regra E0903): escolha um na "
            "tributação."
        )
    g = _sub(inf, "IBSCBS")
    if not nt009:
        _sub(g, "finNFSe", "0")
    _opt(g, "indFinal", cfg.ibscbs_ind_final)
    _sub(g, "cIndOp", cfg.ibscbs_c_ind_op)
    _opt(g, "tpOper", tp_oper)
    _sub(g, "indDest", "0")
    trib = _sub(_sub(g, "valores"), "trib")
    sit = trib if nt009 else _sub(trib, "gIBSCBS")
    _sub(sit, "CST", cfg.ibscbs_c_class_trib[:3])
    _sub(sit, "cClassTrib", cfg.ibscbs_c_class_trib)
    classe = classe_ibscbs(cfg.ibscbs_c_class_trib) or {}
    regular = classe.get("trib_regular")
    dif = [pct(v) for v in (cfg.ibscbs_p_dif_uf, cfg.ibscbs_p_dif_mun, cfg.ibscbs_p_dif_cbs)]
    if regular and not re.fullmatch(r"\d{6}", cfg.ibscbs_c_class_trib_reg or ""):
        raise DpsBuildError(
            f"A classificação {cfg.ibscbs_c_class_trib} exige a classificação da tributação regular (regra E0965)."
        )
    if classe.get("diferimento") and any(p is None for p in dif):
        raise DpsBuildError(
            f"A classificação {cfg.ibscbs_c_class_trib} exige os percentuais de diferimento (regra E0972)."
        )
    if regular or classe.get("diferimento"):
        grupo = _sub(trib, "gIBSCBS") if nt009 else sit
        if regular:
            reg = _sub(grupo, "gTribRegular")
            _sub(reg, "CSTReg", cfg.ibscbs_c_class_trib_reg[:3])
            _sub(reg, "cClassTribReg", cfg.ibscbs_c_class_trib_reg)
        if classe.get("diferimento"):
            g_dif = _sub(grupo, "gDif")
            for tag, p in zip(("pDifUF", "pDifMun", "pDifCBS"), dif, strict=True):
                _sub(g_dif, tag, formatar_decimal(p))


def erro_ajuste_bc(cfg: ConfigFiscal) -> str | None:
    """Quem não pode deduzir/reduzir a base do ISSQN (sem os ajustes por documento, que este sistema não envia)."""
    if cfg.op_simp_nac == "2":
        return "MEI não pode deduzir/reduzir a base do ISSQN (E0436)."
    if cfg.reg_esp_trib != "0":
        return "Com regime especial de tributação não há dedução/redução da base do ISSQN (E0445)."
    if cfg.trib_issqn != "1":
        return "Dedução/redução da base só existe em operação tributável."
    if cfg.op_simp_nac == "3" and cfg.reg_ap_trib_sn == "1" and cfg.c_trib_nac not in codes.SERVICOS_AJUSTE_BC_SN:
        return (
            f"ME/EPP com ISS pelo Simples não deduz/reduz a base do ISSQN no serviço {cfg.c_trib_nac} (E0451/E0452): "
            "só em obras, hospedagem, turismo, agenciamento e outros serviços da lista da Sefin."
        )
    return None


def _endereco_simples(parent: etree._Element, d: dict, cfg: ConfigFiscal, grupo: str) -> None:
    """``TCEnderecoSimples``/``TCEnderObraEvento``: CEP (no Brasil) ou endExt (prestação no exterior, E0382/E0384)."""
    end = _sub(parent, "end")
    if cfg.c_pais_prestacao:
        faltam = [
            r
            for k, r in (("c_end_post", "código postal"), ("cidade", "cidade"), ("estado", "estado"))
            if not limpar_texto(d.get(k))
        ]
        if faltam:
            raise DpsBuildError(f"Endereço {grupo} no exterior incompleto: falta {', '.join(faltam)}.")
        ext = _sub(end, "endExt")
        _sub(ext, "cEndPost", limpar_texto(d.get("c_end_post"), max_len=11))
        _sub(ext, "xCidade", limpar_texto(d.get("cidade"), max_len=60))
        _sub(ext, "xEstProvReg", limpar_texto(d.get("estado"), max_len=60))
    else:
        cep = somente_digitos(d.get("cep"))
        if not cep or len(cep) != 8:
            raise DpsBuildError(f"Informe o CEP {grupo} (8 dígitos, do município da prestação: E0380/E0398).")
        _sub(end, "CEP", cep)
    for campo, rotulo in (("logradouro", "logradouro"), ("bairro", "bairro")):
        if not limpar_texto(d.get(campo)):
            raise DpsBuildError(f"Endereço {grupo} incompleto: falta o {rotulo}.")
    _sub(end, "xLgr", limpar_texto(d.get("logradouro"), max_len=255))
    _sub(end, "nro", limpar_texto(d.get("numero"), max_len=60) or "S/N")
    _opt(end, "xCpl", limpar_texto(d.get("complemento"), max_len=156))
    _sub(end, "xBairro", limpar_texto(d.get("bairro"), max_len=60))


def montar_obra(serv: etree._Element, cfg: ConfigFiscal, nota: DadosNota) -> None:
    """Grupo ``obra``: obrigatório nos serviços de construção (E0370), proibido nos demais salvo 99.01.01 (E0372)."""
    o = nota.obra
    if not o:
        if codes.exige_obra(cfg.c_trib_nac):
            raise DpsBuildError(
                f"O serviço {cfg.c_trib_nac} exige os dados da obra (E0370): código da obra (CNO), CIB ou endereço."
            )
        return
    if not codes.aceita_obra(cfg.c_trib_nac):
        raise DpsBuildError(f"O serviço {cfg.c_trib_nac} não leva dados de obra (E0372): tire a obra da nota.")
    el = _sub(serv, "obra")
    _opt(el, "inscImobFisc", limpar_texto(o.get("insc_imob_fisc"), max_len=30))
    c_obra = limpar_texto(o.get("c_obra"), max_len=30)
    c_cib = re.sub(r"[\s.-]", "", o.get("c_cib") or "").upper()
    if c_obra:
        _sub(el, "cObra", c_obra)
    elif c_cib:
        if len(c_cib) != 8:
            raise DpsBuildError("O CIB tem 8 caracteres (7 + dígito verificador, E0373).")
        _sub(el, "cCIB", c_cib)
    else:
        _endereco_simples(el, o, cfg, "da obra")


def montar_evento(serv: etree._Element, cfg: ConfigFiscal, nota: DadosNota) -> None:
    """Grupo ``atvEvento``: obrigatório no item 12 (E0390), proibido nos demais salvo 99.01.01 (E0392)."""
    ev = nota.evento
    if not ev:
        if codes.exige_evento(cfg.c_trib_nac):
            raise DpsBuildError(f"O serviço {cfg.c_trib_nac} exige os dados do evento (E0390): nome, datas e local.")
        return
    if not codes.aceita_evento(cfg.c_trib_nac):
        raise DpsBuildError(f"O serviço {cfg.c_trib_nac} não leva dados de evento (E0392): tire o evento da nota.")
    nome = limpar_texto(ev.get("nome"), max_len=255)
    ini, fim = ev.get("dt_ini") or "", ev.get("dt_fim") or ""
    if not nome:
        raise DpsBuildError("Informe o nome do evento.")
    if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", ini) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", fim)):
        raise DpsBuildError("Informe as datas de início e fim do evento.")
    if fim < ini:
        raise DpsBuildError("O fim do evento não pode ser antes do início.")
    el = _sub(serv, "atvEvento")
    _sub(el, "xNome", nome)
    _sub(el, "dtIni", ini)
    _sub(el, "dtFim", fim)
    id_evt = limpar_texto(ev.get("id_atv_evt"), max_len=30)
    if id_evt:
        _sub(el, "idAtvEvt", id_evt)
    else:
        _endereco_simples(el, ev, cfg, "do evento")


def _info_compl(serv: etree._Element, nota: DadosNota) -> None:
    """Informações complementares (``serv/infoCompl``): pedido, itens do pedido e texto livre."""
    x_ped = limpar_texto(nota.x_ped, max_len=60)
    itens = [i for i in (limpar_texto(x, max_len=60) for x in nota.itens_pedido[:99]) if i]
    x_inf = limpar_texto(nota.x_inf_comp, max_len=2000)  # TSString: sem quebra de linha (vira espaço)
    if not (x_ped or itens or x_inf):
        return
    info = _sub(serv, "infoCompl")
    _opt(info, "xPed", x_ped)
    if itens:
        g = _sub(info, "gItemPed")
        for i in itens:
            _sub(g, "xItemPed", i)
    _opt(info, "xInfComp", x_inf)


def to_bytes(el: etree._Element) -> bytes:
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8")
