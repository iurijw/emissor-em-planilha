"""Status, certificado, onboarding, configurações e diagnóstico."""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import func, select

from emissor import config
from emissor.api.serial import CAMPOS_FISCAIS, cliente_dict, fiscal_dict
from emissor.db import Cliente, Emissao, LinhaTabela, agora, obter_config, sessao
from emissor.logging_setup import get_context
from emissor.nfse import codes
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import SERIE_API_MAX, SERIE_API_MIN, Ambiente
from emissor.nfse.documentos import cnpj_valido, formatar_documento
from emissor.nfse.dps_builder import id_dps
from emissor.nfse.errors import SefinError
from emissor.nfse.models import ConfigFiscal, normalizar_documento, somente_digitos
from emissor.nfse.xml_reader import NFSeDoc, NfseXmlError, extrair_padroes, extrair_tomador
from emissor.services import certificado as cert_service
from emissor.services import clientes as cli_service
from emissor.services.emissao import verificar_prontidao, worker

log = logging.getLogger(__name__)
router = APIRouter()

MAX_PFX = 1_000_000
MAX_XML = 2_000_000


def _cert_dict() -> dict[str, Any] | None:
    try:
        info = cert_service.info()
    except Exception:
        log.exception("falha ao abrir certificado salvo")
        return {"erro": "O certificado salvo não pôde ser aberto. Carregue-o novamente."}
    return info.as_dict() if info else None


@router.get("/status")
def status() -> dict[str, Any]:
    with sessao() as s:
        cfg = obter_config(s)
        fiscal = cfg.config_fiscal()
        problemas = verificar_prontidao(s)
    cert = _cert_dict()
    avisos = []
    if cert and not cert.get("erro") and 0 <= cert["dias_para_vencer"] <= 30:
        avisos.append(f"O certificado digital vence em {cert['dias_para_vencer']} dia(s).")
    return {
        "versao": config.VER_APLIC,
        "onboarding_concluido": cfg.onboarding_concluido,
        "ambiente": cfg.ambiente,
        "ambiente_descricao": cfg.amb.descricao,
        "certificado": cert,
        "prestador": {
            "cnpj": fiscal.cnpj if fiscal else None,
            "municipio": codes.municipio_uf(fiscal.c_mun_emissor) if fiscal else None,
        },
        "pronto_para_emitir": not problemas,
        "problemas": problemas,
        "avisos": avisos,
        "lote_em_andamento": worker().lote_atual,
    }


# --- certificado ---------------------------------------------------------------------------


@router.post("/certificado")
async def enviar_certificado(arquivo: UploadFile = File(...), senha: str = Form(...)) -> dict[str, Any]:
    pfx = await arquivo.read()
    if len(pfx) > MAX_PFX:
        raise HTTPException(400, "Arquivo grande demais para um certificado A1.")
    info = cert_service.salvar(pfx, senha)
    resp = info.as_dict()
    with sessao() as s:
        fiscal = obter_config(s).config_fiscal()
    if fiscal and info.cnpj and normalizar_documento(fiscal.cnpj) != info.cnpj:
        resp["aviso"] = (
            f"Atenção: o certificado é do CNPJ {formatar_documento(info.cnpj)}, diferente do prestador configurado "
            f"({formatar_documento(fiscal.cnpj)})."
        )
    return resp


# --- onboarding --------------------------------------------------------------------------------


@router.get("/onboarding/padroes")
def padroes() -> dict[str, Any]:
    """Padrões iniciais (o CNPJ vem do certificado; o resto, dos XMLs importados ou do formulário)."""
    info = cert_service.info()
    cfg = ConfigFiscal(cnpj=(info.cnpj if info else "") or "", c_mun_emissor="")
    return {"fiscal": fiscal_dict(cfg)}


@router.post("/onboarding/xmls")
async def importar_xmls(arquivos: list[UploadFile] = File(...)) -> dict[str, Any]:
    docs, erros = [], []
    for a in arquivos:
        conteudo = await a.read()
        if len(conteudo) > MAX_XML:
            erros.append({"arquivo": a.filename, "erro": "Arquivo grande demais."})
            continue
        try:
            docs.append((a.filename, NFSeDoc.from_bytes(conteudo)))
        except NfseXmlError as exc:
            erros.append({"arquivo": a.filename, "erro": str(exc)})
    if not docs:
        raise HTTPException(400, {"mensagem": "Nenhum XML de NFS-e válido.", "detalhes": erros})
    fiscal = extrair_padroes([d for _, d in docs])
    clientes, linhas = [], {}
    with sessao() as s:
        for _, d in sorted(docs, key=lambda x: x[1].data_processamento or ""):
            t = extrair_tomador(d)
            if t is None:
                continue
            c = cli_service.upsert(s, cli_service.de_tomador(t, "xml"))
            clientes.append(c)
            # Sugestão de tabela: última nota de cada cliente (valor e descrição mais recentes).
            linhas[c.documento] = {
                "nome": t.nome,
                "documento": c.documento,
                "valor": f"{d.valor_servico:.2f}",
                "descricao": d.dps("serv/cServ/xDescServ") or "",
            }
        s.commit()
    log.info("XMLs importados no onboarding", extra={"qtd": len(docs), "erros": len(erros), "clientes": len(linhas)})
    return {
        "fiscal": fiscal_dict(fiscal),
        "clientes": [cliente_dict(c) for c in {c.documento: c for c in clientes}.values()],
        "linhas_sugeridas": list(linhas.values()),
        "erros": erros,
        "notas_lidas": len(docs),
    }


class ConfigEntrada(BaseModel):
    fiscal: dict[str, Any] | None = None
    ambiente: str | None = None
    serie: int | None = None
    proximo_ndps_homologacao: int | None = None
    proximo_ndps_producao: int | None = None


class ConcluirOnboarding(ConfigEntrada):
    linhas: list[dict[str, Any]] = []


def _validar_fiscal(d: dict[str, Any]) -> ConfigFiscal:
    erros = []
    dados = {k: (v.strip() if isinstance(v, str) else v) for k, v in d.items() if k in CAMPOS_FISCAIS}
    for k, v in list(dados.items()):
        if v == "":
            dados[k] = None
    cnpj = normalizar_documento(dados.get("cnpj"))
    if not cnpj or not cnpj_valido(cnpj):
        erros.append("CNPJ do prestador inválido.")
    dados["cnpj"] = cnpj
    for campo in ("c_mun_emissor", "c_loc_prestacao"):
        if dados.get(campo) and not codes.municipio(dados[campo]):
            erros.append(f"Código IBGE de município inválido em {campo}: {dados[campo]}.")
    if not dados.get("c_mun_emissor"):
        erros.append("Município do prestador (código IBGE) é obrigatório.")
    if not (dados.get("c_trib_nac") or "").isdigit() or len(dados.get("c_trib_nac") or "") != 6:
        erros.append("Código de Tributação Nacional deve ter 6 dígitos.")
    if dados.get("c_nbs") and (not dados["c_nbs"].isdigit() or len(dados["c_nbs"]) != 9):
        erros.append("Código NBS deve ter 9 dígitos.")
    dados["fone"] = somente_digitos(dados.get("fone"))
    enums = {
        "op_simp_nac": codes.OP_SIMP_NAC,
        "reg_ap_trib_sn": codes.REG_AP_TRIB_SN,
        "reg_esp_trib": codes.REG_ESP_TRIB,
        "trib_issqn": codes.TRIB_ISSQN,
        "tp_ret_issqn": codes.TP_RET_ISSQN,
        "cst_pis_cofins": codes.CST_PIS_COFINS,
        "tp_ret_pis_cofins": codes.TP_RET_PIS_COFINS,
        "tp_emit": codes.TP_EMIT,
    }
    for campo, tabela in enums.items():
        if dados.get(campo) is not None and str(dados[campo]) not in tabela:
            erros.append(f"Valor inválido para {campo}: {dados[campo]}.")
    p = dados.get("p_tot_trib_sn")
    if p not in (None, ""):
        try:
            dados["p_tot_trib_sn"] = Decimal(str(p).replace(",", "."))
            if not Decimal("0") <= dados["p_tot_trib_sn"] < Decimal("100"):
                erros.append("Percentual aproximado de tributos (Simples) deve estar entre 0 e 99,99.")
        except InvalidOperation:
            erros.append("Percentual aproximado de tributos inválido.")
    else:
        dados["p_tot_trib_sn"] = None
    if erros:
        raise HTTPException(400, {"mensagem": "Configuração fiscal inválida.", "detalhes": erros})
    defaults = ConfigFiscal(cnpj=cnpj, c_mun_emissor=dados["c_mun_emissor"])
    base = {f: getattr(defaults, f) for f in CAMPOS_FISCAIS}
    base.update({k: v for k, v in dados.items() if k in base})
    return ConfigFiscal(**base)


def _aplicar_config(entrada: ConfigEntrada) -> None:
    with sessao() as s:
        cfg = obter_config(s)
        if entrada.fiscal is not None:
            cfg.set_config_fiscal(_validar_fiscal(entrada.fiscal))
        if entrada.ambiente is not None:
            if entrada.ambiente not in ("1", "2"):
                raise HTTPException(400, "Ambiente inválido.")
            if entrada.ambiente != cfg.ambiente:
                log.warning(
                    "ambiente alterado",
                    extra={"de": cfg.amb.descricao, "para": Ambiente(entrada.ambiente).descricao},
                )
            cfg.ambiente = entrada.ambiente
        if entrada.serie is not None:
            if not SERIE_API_MIN <= entrada.serie <= SERIE_API_MAX:
                raise HTTPException(
                    400, f"Série deve estar entre {SERIE_API_MIN} e {SERIE_API_MAX} (faixa de sistema próprio)."
                )
            cfg.serie = entrada.serie
        for campo in ("proximo_ndps_homologacao", "proximo_ndps_producao"):
            v = getattr(entrada, campo)
            if v is not None:
                if v < 1:
                    raise HTTPException(400, "Número da próxima DPS deve ser maior que zero.")
                setattr(cfg, campo, v)
        cfg.atualizado_em = agora()
        s.add(cfg)
        s.commit()


@router.post("/onboarding/concluir")
def concluir_onboarding(entrada: ConcluirOnboarding) -> dict[str, Any]:
    if cert_service.info() is None:
        raise HTTPException(400, "Carregue o certificado digital antes de concluir.")
    if entrada.fiscal is None:
        raise HTTPException(400, "Informe a configuração fiscal.")
    _aplicar_config(entrada)
    with sessao() as s:
        cfg = obter_config(s)
        cfg.onboarding_concluido = True
        s.add(cfg)
        if entrada.linhas and not s.exec(select(LinhaTabela)).first():
            for i, ln in enumerate(entrada.linhas):
                s.add(
                    LinhaTabela(
                        ordem=i,
                        nome=str(ln.get("nome") or ""),
                        documento=normalizar_documento(str(ln.get("documento") or "")) or "",
                        valor=str(ln.get("valor") or ""),
                        descricao=str(ln.get("descricao") or ""),
                    )
                )
        s.commit()
    log.info("onboarding concluído", extra={"linhas": len(entrada.linhas)})
    return status()


# --- configurações -------------------------------------------------------------------------------


@router.get("/config")
def obter() -> dict[str, Any]:
    with sessao() as s:
        cfg = obter_config(s)
    return {
        "ambiente": cfg.ambiente,
        "serie": cfg.serie,
        "proximo_ndps_homologacao": cfg.proximo_ndps_homologacao,
        "proximo_ndps_producao": cfg.proximo_ndps_producao,
        "fiscal": fiscal_dict(cfg.config_fiscal()),
        "onboarding_concluido": cfg.onboarding_concluido,
    }


@router.put("/config")
def salvar(entrada: ConfigEntrada) -> dict[str, Any]:
    _aplicar_config(entrada)
    log.info("configuração salva", extra={"campos": [k for k, v in entrada.model_dump().items() if v is not None]})
    return obter()


@router.get("/config/opcoes")
def opcoes() -> dict[str, Any]:
    def lista(t: dict[str, str]) -> list[dict[str, str]]:
        return [{"valor": k, "descricao": v} for k, v in t.items()]

    return {
        "op_simp_nac": lista(codes.OP_SIMP_NAC),
        "reg_ap_trib_sn": lista(codes.REG_AP_TRIB_SN),
        "reg_esp_trib": lista(codes.REG_ESP_TRIB),
        "trib_issqn": lista(codes.TRIB_ISSQN),
        "tp_ret_issqn": lista(codes.TP_RET_ISSQN),
        "cst_pis_cofins": lista(codes.CST_PIS_COFINS),
        "tp_ret_pis_cofins": lista(codes.TP_RET_PIS_COFINS),
        "tp_emit": lista(codes.TP_EMIT),
        "ambiente": [{"valor": a.value, "descricao": a.descricao} for a in Ambiente],
    }


@router.get("/municipios")
def municipios(q: str = "") -> list[dict[str, str]]:
    if len(q.strip()) < 2:
        return []
    return codes.buscar_municipios(q)


@router.post("/config/testar-conexao")
def testar_conexao() -> dict[str, Any]:
    """Consulta uma DPS fictícia: HTTP 404 = mTLS e certificado aceitos (nada é emitido)."""
    cert = cert_service.carregar()
    if cert is None:
        raise HTTPException(400, "Nenhum certificado carregado.")
    with sessao() as s:
        cfg = obter_config(s)
        fiscal = cfg.config_fiscal()
    c_mun = fiscal.c_mun_emissor if fiscal else "5300108"
    ident = id_dps(c_mun, cert.info.cnpj or "0", SERIE_API_MAX, 999_999_999_999_999)
    try:
        with SefinClient(cfg.amb, cert) as client:
            client.consultar_dps(ident)
    except SefinError as err:
        return {"ok": False, "ambiente": cfg.amb.descricao, "erro": err.as_dict()}
    return {"ok": True, "ambiente": cfg.amb.descricao, "mensagem": "Conexão e certificado aceitos pela Sefin."}


# --- diagnóstico / logs ------------------------------------------------------------------------------


class LogCliente(BaseModel):
    nivel: str = "error"
    mensagem: str
    contexto: dict[str, Any] | None = None


@router.post("/logs/cliente")
def log_cliente(entrada: LogCliente) -> dict[str, bool]:
    nivel = {"error": logging.ERROR, "warn": logging.WARNING, "warning": logging.WARNING}.get(
        entrada.nivel.lower(), logging.INFO
    )
    logging.getLogger("emissor.frontend").log(
        nivel, entrada.mensagem[:2000], extra={"contexto_frontend": entrada.contexto}
    )
    return {"ok": True}


@router.get("/diagnostico")
def diagnostico(dias: int = 7) -> Response:
    """ZIP com logs recentes, trocas com a Sefin e resumo do banco (sem certificado/senha)."""
    limite = datetime.now() - timedelta(days=max(1, min(dias, 30)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(config.LOG_DIR.rglob("*")):
            if p.is_file() and datetime.fromtimestamp(p.stat().st_mtime) >= limite:
                z.write(p, f"logs/{p.relative_to(config.LOG_DIR).as_posix()}")
        with sessao() as s:
            cfg = obter_config(s)
            resumo = {
                "gerado_em": agora().isoformat(),
                "versao": config.VER_APLIC,
                "ambiente": cfg.ambiente,
                "serie": cfg.serie,
                "proximo_ndps_homologacao": cfg.proximo_ndps_homologacao,
                "proximo_ndps_producao": cfg.proximo_ndps_producao,
                "fiscal": fiscal_dict(cfg.config_fiscal()),
                "certificado": _cert_dict(),
                "emissoes_por_status": dict(
                    s.exec(select(Emissao.status, func.count()).group_by(Emissao.status)).all()
                ),
                "clientes": s.exec(select(func.count()).select_from(Cliente)).one(),
                "linhas_tabela": s.exec(select(func.count()).select_from(LinhaTabela)).one(),
            }
        z.writestr("resumo.json", json.dumps(resumo, ensure_ascii=False, indent=2, default=str))
    log.info("diagnóstico exportado", extra={"dias": dias, **get_context()})
    nome = f"diagnostico-emissor-{datetime.now():%Y%m%d-%H%M}.zip"
    return Response(
        buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{nome}"'}
    )
