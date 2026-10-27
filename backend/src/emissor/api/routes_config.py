"""Status, certificado, onboarding, configurações e diagnóstico."""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import func, select

from emissor import auth, config
from emissor.api.deps import AdminAtual
from emissor.api.serial import CAMPOS_FISCAIS, cliente_dict, fiscal_dict
from emissor.db import Cliente, Emissao, LinhaTabela, Lote, agora, obter_config, sessao
from emissor.logging_setup import get_context
from emissor.nfse import codes
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import SERIE_API_MAX, SERIE_API_MIN, Ambiente
from emissor.nfse.documentos import cnpj_valido, formatar_documento
from emissor.nfse.dps_builder import id_dps
from emissor.nfse.errors import SefinError
from emissor.nfse.models import CAMPOS_TRIBUTACAO, ConfigFiscal, normalizar_documento, somente_digitos, tributacao_de
from emissor.nfse.xml_reader import NFSeDoc, NfseXmlError, extrair_padroes, extrair_tomador
from emissor.services import certificado as cert_service
from emissor.services import clientes as cli_service
from emissor.services import parametros, variaveis
from emissor.services import tributacao as trib_service
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
async def enviar_certificado(
    admin: AdminAtual, arquivo: UploadFile = File(...), senha: str = Form(...)
) -> dict[str, Any]:
    pfx = await arquivo.read()
    if len(pfx) > MAX_PFX:
        raise HTTPException(400, "Arquivo grande demais para um certificado A1.")
    info = cert_service.salvar(pfx, senha)
    auth.auditar("certificado_enviado", f"{info.titular} (válido até {info.valido_ate:%d/%m/%Y})", admin)
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
def padroes(_admin: AdminAtual) -> dict[str, Any]:
    """Padrões iniciais (o CNPJ vem do certificado; o resto, dos XMLs importados ou do formulário)."""
    info = cert_service.info()
    cfg = ConfigFiscal(cnpj=(info.cnpj if info else "") or "", c_mun_emissor="")
    return {"fiscal": fiscal_dict(cfg)}


@router.post("/onboarding/xmls")
async def importar_xmls(_admin: AdminAtual, arquivos: list[UploadFile] = File(...)) -> dict[str, Any]:
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
    trib, erros_trib = trib_service.normalizar({k: v for k, v in dados.items() if k in CAMPOS_TRIBUTACAO})
    dados.update(trib)
    erros += erros_trib
    cnpj = normalizar_documento(dados.get("cnpj"))
    if not cnpj or not cnpj_valido(cnpj):
        erros.append("CNPJ do prestador inválido.")
    dados["cnpj"] = cnpj
    if dados.get("c_mun_emissor") and not codes.municipio(dados["c_mun_emissor"]):
        erros.append(f"Código IBGE de município inválido em c_mun_emissor: {dados['c_mun_emissor']}.")
    if not dados.get("c_mun_emissor"):
        erros.append("Município do prestador é obrigatório: digite o nome da cidade e escolha-a na lista.")
    dados["fone"] = somente_digitos(dados.get("fone"))
    enums = {
        "op_simp_nac": codes.OP_SIMP_NAC,
        "reg_ap_trib_sn": codes.REG_AP_TRIB_SN,
        "reg_esp_trib": codes.REG_ESP_TRIB,
        "tp_emit": codes.TP_EMIT,
    }
    for campo, tabela in enums.items():
        if dados.get(campo) is not None and str(dados[campo]) not in tabela:
            erros.append(f"Valor inválido para {campo}: {dados[campo]}.")
    defaults = ConfigFiscal(cnpj=cnpj or "", c_mun_emissor=dados.get("c_mun_emissor") or "")
    base = {f: getattr(defaults, f) for f in CAMPOS_FISCAIS}
    base.update({k: v for k, v in dados.items() if k in base})
    base["p_tot_trib_sn"] = Decimal(base["p_tot_trib_sn"]) if base.get("p_tot_trib_sn") is not None else None
    cfg = ConfigFiscal(**base)
    erros += trib_service.coerencia(tributacao_de(cfg), cfg)
    if erros:
        raise HTTPException(400, {"mensagem": "Configuração fiscal inválida.", "detalhes": erros})
    return cfg


def _aplicar_config(entrada: ConfigEntrada) -> None:
    with sessao() as s:
        cfg = obter_config(s)
        muda_numeracao = (entrada.ambiente is not None and entrada.ambiente != cfg.ambiente) or any(
            getattr(entrada, c) is not None and getattr(entrada, c) != getattr(cfg, c)
            for c in ("serie", "proximo_ndps_homologacao", "proximo_ndps_producao")
        )
        if muda_numeracao and s.exec(select(Lote).where(Lote.status == "em_andamento")).first():
            raise HTTPException(
                400,
                "Há notas sendo emitidas agora. Espere o lote terminar para trocar o ambiente, a série ou a numeração.",
            )
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
def concluir_onboarding(entrada: ConcluirOnboarding, admin: AdminAtual) -> dict[str, Any]:
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
    auth.auditar("onboarding_concluido", None, admin)
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
def salvar(entrada: ConfigEntrada, admin: AdminAtual) -> dict[str, Any]:
    _aplicar_config(entrada)
    campos = [k for k, v in entrada.model_dump().items() if v is not None]
    log.info("configuração salva", extra={"campos": campos})
    detalhe = ", ".join(campos)
    if entrada.ambiente is not None:
        detalhe += f" (ambiente {Ambiente(entrada.ambiente).descricao})"
    auth.auditar("configuracao_alterada", detalhe, admin)
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
        "ibscbs_c_ind_op": lista(codes.C_IND_OP),
        "ibscbs_c_class_trib": lista(codes.C_CLASS_TRIB_SUGESTOES),
        "ibscbs_ind_final": lista(codes.IND_FINAL),
        "ambiente": [{"valor": a.value, "descricao": a.descricao} for a in Ambiente],
        "variaveis_descricao": variaveis.lista(),
    }


@router.get("/municipios")
def municipios(q: str = "") -> list[dict[str, str]]:
    if len(q.strip()) < 2:
        return []
    return codes.buscar_municipios(q)


@router.get("/servicos")
def servicos(q: str = "") -> list[dict[str, Any]]:
    """Lista nacional de serviços (cTribNac) por código ou palavras da descrição."""
    if len(q.strip()) < 2:
        return []
    return codes.buscar_servicos(q)


@router.get("/servicos/{codigo}")
def servico(codigo: str) -> dict[str, Any]:
    """Um serviço com os NBS e as sugestões de cClassTrib do Anexo VIII."""
    s = codes.servico(codigo)
    if s is None:
        raise HTTPException(
            404,
            f"O código {codigo} não está na lista nacional de serviços ({', '.join(codes.fontes_servicos()[:1])}).",
        )
    return {**s, "fontes": codes.fontes_servicos()}


class ConferirServico(BaseModel):
    c_trib_nac: str
    c_loc_prestacao: str | None = None
    # Município do prestador ainda não salvo (onboarding); sem ele, o das configurações.
    c_mun_emissor: str | None = None


@router.post("/config/conferir-servico")
def conferir_servico(entrada: ConferirServico) -> dict[str, Any]:
    """A prefeitura tem o serviço cadastrado no Sistema Nacional? (consulta de leitura no ADN, com o certificado)"""
    with sessao() as s:
        cfg = obter_config(s)
        fiscal = cfg.config_fiscal()
    c_mun = somente_digitos(entrada.c_mun_emissor) or (fiscal.c_mun_emissor if fiscal else None)
    if not c_mun or not codes.municipio(c_mun):
        raise HTTPException(400, "Informe o município do prestador antes de conferir.")
    c = parametros.conferir(
        cfg.amb,
        c_mun,
        somente_digitos(entrada.c_trib_nac) or "",
        somente_digitos(entrada.c_loc_prestacao) or None,
    )
    return {**c.as_dict(), "ambiente": cfg.amb.descricao}


@router.post("/config/testar-conexao")
def testar_conexao(_admin: AdminAtual) -> dict[str, Any]:
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


MAX_CONTEXTO_LOG = 8000


@router.post("/logs/cliente")
def log_cliente(entrada: LogCliente) -> dict[str, bool]:
    nivel = {"error": logging.ERROR, "warn": logging.WARNING, "warning": logging.WARNING}.get(
        entrada.nivel.lower(), logging.INFO
    )
    contexto = entrada.contexto
    if contexto is not None:
        texto = json.dumps(contexto, ensure_ascii=False, default=str)
        # Links de convite/primeiro acesso levam o token no endereço: não vão para o log.
        texto_limpo = re.sub(r"token=[^&\"\s]+", "token=***", texto)
        if len(texto_limpo) > MAX_CONTEXTO_LOG or texto_limpo != texto:
            contexto = {"resumo": texto_limpo[:MAX_CONTEXTO_LOG]}
    logging.getLogger("emissor.frontend").log(nivel, entrada.mensagem[:2000], extra={"contexto_frontend": contexto})
    return {"ok": True}


@router.get("/diagnostico")
def diagnostico(_admin: AdminAtual, dias: int = 7) -> Response:
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
