"""Lotes de emissão, consulta de emissões e downloads (XML/PDF, individual e ZIP)."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import func, or_, select

from emissor.api.serial import emissao_detalhe, emissao_resumo, lote_dict
from emissor.db import Emissao, EventoNFSe, Lote, sessao
from emissor.nfse.models import normalizar_documento
from emissor.services import arquivos
from emissor.services import eventos as eventos_service
from emissor.services.emissao import criar_lote, verificar_na_sefin, worker

# Notas que existem na Sefin e têm XML/PDF para baixar.
COM_ARQUIVOS = ("autorizada", "cancelada", "substituida")

log = logging.getLogger(__name__)
router = APIRouter()


class NovoLote(BaseModel):
    linha_ids: list[int]


@router.post("/lotes")
def novo_lote(entrada: NovoLote) -> dict[str, Any]:
    lote = criar_lote(entrada.linha_ids)
    return obter_lote(lote.id)


@router.get("/lotes")
def listar_lotes(limite: int = 20) -> list[dict[str, Any]]:
    with sessao() as s:
        return [lote_dict(lt) for lt in s.exec(select(Lote).order_by(Lote.criado_em.desc()).limit(limite))]


@router.get("/lotes/{lote_id}")
def obter_lote(lote_id: str) -> dict[str, Any]:
    with sessao() as s:
        lote = s.get(Lote, lote_id)
        if lote is None:
            raise HTTPException(404, "Lote não encontrado.")
        emissoes = list(s.exec(select(Emissao).where(Emissao.lote_id == lote_id).order_by(Emissao.id)))
    d = lote_dict(lote, emissoes)
    d["em_processamento"] = worker().lote_atual == lote_id
    return d


@router.post("/lotes/{lote_id}/cancelar")
def cancelar_lote(lote_id: str) -> dict[str, Any]:
    with sessao() as s:
        lote = s.get(Lote, lote_id)
    if lote is None:
        raise HTTPException(404, "Lote não encontrado.")
    if lote.status != "em_andamento":
        raise HTTPException(400, "Este lote já foi finalizado.")
    worker().cancelar(lote_id)
    return {"ok": True, "mensagem": "Cancelamento solicitado: a nota em envio termina e as demais não serão enviadas."}


# --- emissões -------------------------------------------------------------------------------------


@router.get("/emissoes")
def listar_emissoes(
    status: str | None = None,
    ambiente: str | None = None,
    de: date | None = None,
    ate: date | None = None,
    q: str | None = None,
    lote_id: str | None = None,
    pagina: int = 1,
    por_pagina: int = 50,
) -> dict[str, Any]:
    filtros = []
    if status:
        filtros.append(Emissao.status.in_(status.split(",")))
    if ambiente:
        filtros.append(Emissao.ambiente == ambiente)
    if de:
        filtros.append(Emissao.criado_em >= datetime.combine(de, datetime.min.time()))
    if ate:
        filtros.append(Emissao.criado_em < datetime.combine(ate + timedelta(days=1), datetime.min.time()))
    if lote_id:
        filtros.append(Emissao.lote_id == lote_id)
    if q:
        termo = f"%{q.strip()}%"
        doc = normalizar_documento(q) or "__nada__"
        filtros.append(
            or_(
                Emissao.tomador_nome.ilike(termo),
                Emissao.tomador_documento.contains(doc),
                Emissao.numero_nfse == q.strip(),
                Emissao.chave_acesso == q.strip(),
                Emissao.descricao.ilike(termo),
            )
        )
    por_pagina = max(1, min(por_pagina, 500))
    with sessao() as s:
        total = s.exec(select(func.count()).select_from(Emissao).where(*filtros)).one()
        itens = s.exec(
            select(Emissao)
            .where(*filtros)
            .order_by(Emissao.id.desc())
            .offset((max(pagina, 1) - 1) * por_pagina)
            .limit(por_pagina)
        ).all()
        soma = s.exec(select(Emissao.valor).where(*filtros, Emissao.status == "autorizada")).all()
    return {
        "itens": [emissao_resumo(e) for e in itens],
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "valor_autorizado": f"{sum(float(v) for v in soma):.2f}",
    }


def _emissao(emissao_id: int) -> Emissao:
    with sessao() as s:
        e = s.get(Emissao, emissao_id)
    if e is None:
        raise HTTPException(404, "Emissão não encontrada.")
    return e


def _detalhe(e: Emissao) -> dict[str, Any]:
    return emissao_detalhe(e, eventos_service.eventos_da_emissao(e.id))


@router.get("/emissoes/{emissao_id}")
def detalhe(emissao_id: int) -> dict[str, Any]:
    return _detalhe(_emissao(emissao_id))


@router.post("/emissoes/{emissao_id}/verificar")
def verificar(emissao_id: int) -> dict[str, Any]:
    return _detalhe(verificar_na_sefin(emissao_id))


class PedidoCancelamento(BaseModel):
    c_motivo: str
    x_motivo: str


@router.post("/emissoes/{emissao_id}/cancelar")
def cancelar(emissao_id: int, entrada: PedidoCancelamento) -> dict[str, Any]:
    r = eventos_service.cancelar(emissao_id, entrada.c_motivo, entrada.x_motivo)
    return _detalhe(r.emissao) | {"aviso": r.aviso}


@router.post("/emissoes/{emissao_id}/situacao")
def atualizar_situacao(emissao_id: int) -> dict[str, Any]:
    """Consulta no Ambiente Nacional (ADN) os eventos da nota (ex.: cancelada no portal)."""
    e, novos = eventos_service.atualizar_situacao(emissao_id)
    return _detalhe(e) | {"eventos_novos": novos}


@router.get("/sincronizacao")
def estado_sincronizacao() -> dict[str, Any]:
    return eventos_service.sincronizador().estado()


@router.post("/sincronizacao")
def sincronizar(forcar: bool = False) -> dict[str, Any]:
    """Lê no ADN os documentos novos (cancelamentos feitos fora do sistema etc.) em segundo plano."""
    return eventos_service.sincronizador().solicitar(forcar=forcar)


def _xml(e: Emissao) -> tuple[Path, bytes]:
    p = arquivos.absoluto(e.xml_path)
    if not p or not p.is_file():
        raise HTTPException(404, "XML desta NFS-e não está disponível (a nota não foi autorizada).")
    return p, p.read_bytes()


def _xmls_eventos(e: Emissao) -> list[tuple[Path, bytes]]:
    res = []
    for ev in eventos_service.eventos_da_emissao(e.id):
        p = arquivos.absoluto(ev.xml_path)
        if p and p.is_file():
            res.append((p, p.read_bytes()))
    return res


def _pdf(e: Emissao) -> tuple[str, bytes]:
    p = arquivos.absoluto(e.pdf_path)
    if p and p.is_file():
        return p.name, p.read_bytes()
    xml_path, xml = _xml(e)
    pdf = eventos_service.gerar_pdf(e, xml)
    destino = xml_path.with_suffix(".pdf")
    try:
        from emissor import config

        destino = config.PDF_DIR / xml_path.relative_to(config.XML_DIR).with_suffix(".pdf")
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(pdf)
        with sessao() as s:
            reg = s.get(Emissao, e.id)
            reg.pdf_path = arquivos.relativo(destino)
            s.add(reg)
            s.commit()
    except (OSError, ValueError):
        log.exception("não foi possível gravar o PDF gerado sob demanda")
    return destino.name, pdf


def _anexo(nome: str, conteudo: bytes, tipo: str) -> Response:
    return Response(conteudo, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.get("/emissoes/{emissao_id}/xml")
def baixar_xml(emissao_id: int) -> Response:
    p, xml = _xml(_emissao(emissao_id))
    return _anexo(p.name, xml, "application/xml")


@router.get("/emissoes/{emissao_id}/eventos/{evento_id}/xml")
def baixar_xml_evento(emissao_id: int, evento_id: str) -> Response:
    with sessao() as s:
        ev = s.get(EventoNFSe, evento_id)
    p = arquivos.absoluto(ev.xml_path) if ev and ev.emissao_id == emissao_id else None
    if not p or not p.is_file():
        raise HTTPException(404, "XML do evento não encontrado.")
    return _anexo(p.name, p.read_bytes(), "application/xml")


@router.get("/emissoes/{emissao_id}/pdf")
def baixar_pdf(emissao_id: int, inline: bool = False) -> Response:
    nome, pdf = _pdf(_emissao(emissao_id))
    disp = "inline" if inline else "attachment"
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'{disp}; filename="{nome}"'})


class ZipEntrada(BaseModel):
    ids: list[int]
    conteudo: str = "ambos"  # xml | pdf | ambos


@router.post("/emissoes/zip")
def baixar_zip(entrada: ZipEntrada) -> Response:
    if entrada.conteudo not in ("xml", "pdf", "ambos"):
        raise HTTPException(400, "Conteúdo deve ser xml, pdf ou ambos.")
    if not entrada.ids:
        raise HTTPException(400, "Selecione ao menos uma nota.")
    with sessao() as s:
        emissoes = [e for e in (s.get(Emissao, i) for i in entrada.ids) if e and e.status in COM_ARQUIVOS]
    if not emissoes:
        raise HTTPException(400, "Nenhuma das notas selecionadas foi autorizada.")
    buf = io.BytesIO()
    falhas = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for e in emissoes:
            try:
                if entrada.conteudo in ("xml", "ambos"):
                    for p, xml in [_xml(e), *_xmls_eventos(e)]:
                        z.writestr(f"xml/{p.name}" if entrada.conteudo == "ambos" else p.name, xml)
                if entrada.conteudo in ("pdf", "ambos"):
                    nome, pdf = _pdf(e)
                    z.writestr(f"pdf/{nome}" if entrada.conteudo == "ambos" else nome, pdf)
            except HTTPException as exc:
                falhas.append(f"NFS-e {e.numero_nfse or e.id}: {exc.detail}")
        if falhas:
            z.writestr("LEIA-ME_falhas.txt", "\n".join(falhas))
    log.info("ZIP gerado", extra={"qtd": len(emissoes), "conteudo": entrada.conteudo, "falhas": len(falhas)})
    return _anexo(f"nfse-{datetime.now():%Y%m%d-%H%M}.zip", buf.getvalue(), "application/zip")
