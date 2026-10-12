"""Tabela de notas a emitir e cadastro de clientes."""

from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import select

from emissor.api.serial import cliente_dict, linha_dict
from emissor.db import Aba, Cliente, Emissao, LinhaTabela, agora, obter_config, sessao
from emissor.nfse.models import limpar_texto, normalizar_documento, somente_digitos
from emissor.services import clientes as cli_service
from emissor.services import tabela as tabela_service
from emissor.services import tributacao as trib_service
from emissor.services.emissao import aliquotas_iss, validar_linhas, verificar_prontidao

log = logging.getLogger(__name__)
router = APIRouter()


# --- tabela ------------------------------------------------------------------------------------


def _listar_linhas(aba_id: int) -> list[dict[str, Any]]:
    with sessao() as s:
        linhas = list(
            s.exec(select(LinhaTabela).where(LinhaTabela.aba_id == aba_id).order_by(LinhaTabela.ordem, LinhaTabela.id))
        )
        ids = [ln.ultima_emissao_id for ln in linhas if ln.ultima_emissao_id]
        emissoes = {e.id: e for e in s.exec(select(Emissao).where(Emissao.id.in_(ids)))} if ids else {}
        return [linha_dict(ln, emissoes.get(ln.ultima_emissao_id)) for ln in linhas]


def _aba(aba_id: int | None) -> Aba:
    with sessao() as s:
        return trib_service.resolver_aba(s, aba_id)


@router.get("/tabela")
def obter_tabela(aba_id: int | None = None) -> dict[str, Any]:
    aba = _aba(aba_id)
    return {"aba_id": aba.id, "linhas": _listar_linhas(aba.id)}


class LinhaEntrada(BaseModel):
    id: int | None = None
    nome: str = ""
    documento: str = ""
    valor: str | float | None = ""
    descricao: str = ""


class TabelaEntrada(BaseModel):
    linhas: list[LinhaEntrada]


def _normalizar_valor(v: str | float | None) -> str:
    try:
        return tabela_service.parse_valor(v)
    except ValueError:
        return str(v or "").strip()  # mantém o texto para o usuário corrigir; a validação acusa


@router.put("/tabela")
def salvar_tabela(entrada: TabelaEntrada, aba_id: int | None = None) -> dict[str, Any]:
    """Substitui as linhas de uma aba (a grade envia o estado completo da aba após cada edição)."""
    aba = _aba(aba_id)
    with sessao() as s:
        existentes = {ln.id: ln for ln in s.exec(select(LinhaTabela).where(LinhaTabela.aba_id == aba.id))}
        manter = set()
        for ordem, e in enumerate(entrada.linhas):
            ln = existentes.get(e.id) if e.id is not None else None
            if ln is None:
                ln = LinhaTabela(aba_id=aba.id)
            ln.ordem = ordem
            ln.nome = (e.nome or "").strip()
            ln.documento = normalizar_documento(e.documento) or ""
            ln.valor = _normalizar_valor(e.valor)
            ln.descricao = (e.descricao or "").strip()
            ln.atualizado_em = agora()
            s.add(ln)
            s.flush()
            manter.add(ln.id)
        for id_, ln in existentes.items():
            if id_ not in manter:
                s.delete(ln)
        s.commit()
    return obter_tabela(aba.id)


@router.post("/tabela/importar")
def importar_tabela(
    arquivo: UploadFile = File(...), modo: str = Query("substituir"), aba_id: int | None = None
) -> dict[str, Any]:
    if modo not in ("substituir", "acrescentar"):
        raise HTTPException(400, "Modo deve ser 'substituir' ou 'acrescentar'.")
    aba = _aba(aba_id)
    # Rota síncrona (roda fora do loop de eventos); lê no máximo 1 byte além do limite.
    conteudo = arquivo.file.read(tabela_service.MAX_BYTES_IMPORTACAO + 1)
    res = tabela_service.importar(arquivo.filename or "", conteudo)
    with sessao() as s:
        atuais = list(s.exec(select(LinhaTabela).where(LinhaTabela.aba_id == aba.id)))
        if modo == "substituir":
            for ln in atuais:
                s.delete(ln)
            inicio = 0
        else:
            inicio = len(atuais)
        for i, li in enumerate(res.linhas):
            s.add(
                LinhaTabela(
                    aba_id=aba.id,
                    ordem=inicio + i,
                    nome=li.nome,
                    documento=li.documento,
                    valor=li.valor,
                    descricao=li.descricao,
                )
            )
        s.commit()
    log.info(
        "tabela importada",
        extra={"arquivo": arquivo.filename, "linhas": len(res.linhas), "modo": modo, "aba_id": aba.id},
    )
    return {"aba_id": aba.id, "linhas": _listar_linhas(aba.id), "importadas": len(res.linhas), "avisos": res.avisos}


@router.get("/tabela/exportar")
def exportar_tabela(formato: str = "xlsx", aba_id: int | None = None) -> Response:
    aba = _aba(aba_id)
    linhas = _listar_linhas(aba.id)
    sufixo = re.sub(r"[^0-9A-Za-z]+", "-", unicodedata.normalize("NFKD", aba.nome).encode("ascii", "ignore").decode())
    nome = f"notas-a-emitir-{sufixo.strip('-').lower() or 'aba'}-{datetime.now():%Y%m%d}"
    if formato == "csv":
        return Response(
            tabela_service.exportar_csv(linhas),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{nome}.csv"'},
        )
    return Response(
        tabela_service.exportar_xlsx(linhas),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}.xlsx"'},
    )


class ValidarEntrada(BaseModel):
    linha_ids: list[int]


@router.post("/tabela/validar")
def validar(entrada: ValidarEntrada) -> dict[str, Any]:
    with sessao() as s:
        cfg = obter_config(s)
        linhas = [ln for ln in (s.get(LinhaTabela, i) for i in entrada.linha_ids) if ln is not None]
        res = validar_linhas(s, linhas, cfg.amb)
        aliquotas = aliquotas_iss(s, linhas)
        problemas = verificar_prontidao(s)
        total = sum((float(ln.valor) for ln in linhas if _eh_numero(ln.valor)), 0.0)
    return {
        "ambiente": cfg.ambiente,
        "ambiente_descricao": cfg.amb.descricao,
        "problemas": problemas,
        "linhas": [{"linha_id": v.linha_id, "erros": v.erros, "avisos": v.avisos} for v in res],
        "quantidade": len(linhas),
        "total": f"{total:.2f}",
        "aliquotas_iss": aliquotas,
    }


def _eh_numero(v: str) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# --- clientes ----------------------------------------------------------------------------------


@router.get("/clientes")
def listar_clientes(q: str | None = None) -> list[dict[str, Any]]:
    with sessao() as s:
        return [cliente_dict(c) for c in cli_service.listar(s, q)]


@router.get("/clientes/{documento}")
def obter_cliente(documento: str) -> dict[str, Any]:
    with sessao() as s:
        c = s.get(Cliente, normalizar_documento(documento) or "")
    if c is None:
        raise HTTPException(404, "Cliente não cadastrado.")
    return cliente_dict(c)


class ClienteEntrada(BaseModel):
    nome: str
    c_mun: str | None = None
    cep: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    fone: str | None = None
    email: str | None = None
    inscricao_municipal: str | None = None


@router.put("/clientes/{documento}")
def salvar_cliente(documento: str, entrada: ClienteEntrada) -> dict[str, Any]:
    doc = normalizar_documento(documento) or ""
    if not cli_service.documento_valido(doc):
        raise HTTPException(400, "CNPJ/CPF inválido.")
    from emissor.nfse import codes

    erros = []
    if not limpar_texto(entrada.nome):
        erros.append("Nome é obrigatório.")
    if entrada.c_mun and not codes.municipio(entrada.c_mun):
        erros.append(f"Código IBGE de município inválido: {entrada.c_mun}.")
    cep = somente_digitos(entrada.cep)
    if cep and len(cep) != 8:
        erros.append("CEP deve ter 8 dígitos.")
    if erros:
        raise HTTPException(400, {"mensagem": "Cadastro inválido.", "detalhes": erros})
    with sessao() as s:
        c = s.get(Cliente, doc) or Cliente(documento=doc, nome="")
        c.nome = limpar_texto(entrada.nome) or ""
        c.c_mun = somente_digitos(entrada.c_mun)
        c.cep = cep
        c.logradouro = limpar_texto(entrada.logradouro)
        c.numero = limpar_texto(entrada.numero)
        c.complemento = limpar_texto(entrada.complemento)
        c.bairro = limpar_texto(entrada.bairro)
        c.fone = somente_digitos(entrada.fone)
        c.email = (entrada.email or "").strip() or None
        c.inscricao_municipal = limpar_texto(entrada.inscricao_municipal)
        c.origem = "manual"
        c.atualizado_em = agora()
        s.add(c)
        s.commit()
    log.info("cliente salvo", extra={"documento": doc})
    return cliente_dict(c)


@router.delete("/clientes/{documento}")
def excluir_cliente(documento: str) -> dict[str, bool]:
    with sessao() as s:
        c = s.get(Cliente, normalizar_documento(documento) or "")
        if c:
            s.delete(c)
            s.commit()
    return {"ok": True}


@router.post("/clientes/{documento}/consultar")
def consultar_cliente(documento: str) -> dict[str, Any]:
    doc = normalizar_documento(documento) or ""
    if len(doc) != 14:
        raise HTTPException(400, "Consulta automática disponível só para CNPJ.")
    r = cli_service.consultar_cnpj(doc)
    if not r.encontrado:
        raise HTTPException(404 if r.fonte else 502, r.erro)
    with sessao() as s:
        c = cli_service.upsert(s, r.cliente)
        s.commit()
    return {"fonte": r.fonte, "cliente": cliente_dict(c)}


class ConsultarPendentes(BaseModel):
    documentos: list[str] = Field(max_length=20_000)


MAX_ARQUIVOS_XML = 2000
# Uma consulta por vez na instância: cada chamada segura uma thread por até ~15 s ou mais.
_consulta_em_andamento = threading.Lock()


@router.post("/clientes/consultar-pendentes")
def consultar_pendentes(entrada: ConsultarPendentes) -> dict[str, Any]:
    """Consulta na Receita (BrasilAPI/CNPJá) os CNPJs da tabela ainda sem cadastro."""
    if not _consulta_em_andamento.acquire(blocking=False):
        raise HTTPException(429, "Já há uma consulta de CNPJs em andamento. Tente de novo em instantes.")
    try:
        return _consultar_pendentes(entrada)
    finally:
        _consulta_em_andamento.release()


def _consultar_pendentes(entrada: ConsultarPendentes) -> dict[str, Any]:
    resultados = []
    docs = []
    for d in entrada.documentos:
        n = normalizar_documento(d) or ""
        if len(n) == 14 and n not in docs:
            docs.append(n)
    with sessao() as s:
        faltando = [d for d in docs if s.get(Cliente, d) is None]
    for i, doc in enumerate(faltando[:50]):  # limite por chamada; o front repete se houver mais
        if i:
            time.sleep(0.3)  # gentileza com a API pública
        r = cli_service.consultar_cnpj(doc)
        if r.encontrado:
            with sessao() as s:
                c = cli_service.upsert(s, r.cliente, sobrescrever=False)
                s.commit()
            resultados.append({"documento": doc, "ok": True, "fonte": r.fonte, "nome": c.nome})
        else:
            resultados.append({"documento": doc, "ok": False, "erro": r.erro})
    return {"consultados": len(resultados), "restantes": max(0, len(faltando) - 50), "resultados": resultados}


@router.post("/clientes/importar-xmls")
def importar_clientes_xmls(arquivos: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Cadastro automático de clientes a partir de XMLs de NFS-e (soltos ou em .zip)."""
    if len(arquivos) > MAX_ARQUIVOS_XML:
        raise HTTPException(400, f"Envie no máximo {MAX_ARQUIVOS_XML} arquivos por vez (ou junte-os num .zip).")
    entrada = [(a.filename or "arquivo", a.file.read()) for a in arquivos]
    with sessao() as s:
        fiscal = obter_config(s).config_fiscal()
        res = cli_service.importar_de_xmls(s, entrada, fiscal.cnpj if fiscal else None)
        s.commit()
        nomes = {
            c.documento: c.nome
            for c in s.exec(select(Cliente).where(Cliente.documento.in_(res.novos + res.completados)))
        }
    if res.notas_lidas == 0:
        raise HTTPException(
            400,
            {
                "mensagem": "Nenhuma NFS-e válida encontrada nos arquivos enviados.",
                "detalhes": [f"{i['arquivo']}: {i['motivo']}" for i in res.ignorados],
            },
        )
    return {
        "notas_lidas": res.notas_lidas,
        "novos": [{"documento": d, "nome": nomes.get(d, "")} for d in res.novos],
        "completados": [{"documento": d, "nome": nomes.get(d, "")} for d in res.completados],
        "sem_alteracao": len(res.sem_alteracao),
        "ignorados": res.ignorados,
    }
