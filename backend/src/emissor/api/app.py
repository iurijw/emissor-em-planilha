"""Aplicação FastAPI: API em ``/api`` e frontend (build do Vite) em ``/``."""

from __future__ import annotations

import ipaddress
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from emissor import config
from emissor.api import routes_config, routes_emissoes, routes_tabela
from emissor.db import engine
from emissor.logging_setup import bind_context, setup_logging
from emissor.nfse.certificate import CertificadoError
from emissor.nfse.errors import SefinError, TipoErro
from emissor.services.emissao import LoteError, worker
from emissor.services.tabela import ImportacaoError

log = logging.getLogger("emissor.api")

FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"


def _redes_permitidas() -> list[ipaddress._BaseNetwork]:
    bruto = os.environ.get("EMISSOR_IPS_PERMITIDOS", "").strip()
    if not bruto:
        return []
    return [ipaddress.ip_network(x.strip(), strict=False) for x in bruto.split(",") if x.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    engine()
    if app.state.iniciar_worker:
        worker().iniciar()
    log.info("servidor iniciado", extra={"versao": config.VER_APLIC, "data_dir": str(config.DATA_DIR)})
    yield


def create_app(iniciar_worker: bool = True) -> FastAPI:
    app = FastAPI(title="Emissor em Planilha", version=config.APP_VERSION, lifespan=lifespan)
    app.state.iniciar_worker = iniciar_worker
    redes = _redes_permitidas()

    @app.middleware("http")
    async def contexto_e_log(request: Request, call_next):
        rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:10]
        ip = request.client.host if request.client else "?"
        if redes and ip not in ("127.0.0.1", "::1"):
            try:
                permitido = any(ipaddress.ip_address(ip) in r for r in redes)
            except ValueError:
                permitido = False
            if not permitido:
                log.warning("acesso bloqueado por IP", extra={"ip": ip, "path": request.url.path})
                return JSONResponse({"mensagem": f"Acesso não permitido a partir de {ip}."}, status_code=403)
        inicio = time.perf_counter()
        with bind_context(request_id=rid):
            try:
                resp = await call_next(request)
            except Exception:
                log.exception("erro não tratado", extra={"path": request.url.path, "metodo": request.method})
                resp = JSONResponse(
                    {
                        "mensagem": "Erro interno do servidor. O detalhe foi registrado no log.",
                        "request_id": rid,
                    },
                    status_code=500,
                )
            ms = round((time.perf_counter() - inicio) * 1000)
            if request.url.path.startswith("/api"):
                nivel = logging.DEBUG if request.method == "GET" and resp.status_code < 400 else logging.INFO
                log.log(
                    nivel,
                    "%s %s -> %s (%d ms)",
                    request.method,
                    request.url.path,
                    resp.status_code,
                    ms,
                    extra={"ip": ip, "status": resp.status_code, "ms": ms},
                )
        resp.headers["X-Request-Id"] = rid
        return resp

    @app.exception_handler(HTTPException)
    async def _http(_req: Request, exc: HTTPException):
        corpo = exc.detail if isinstance(exc.detail, dict) else {"mensagem": str(exc.detail)}
        return JSONResponse(corpo, status_code=exc.status_code)

    @app.exception_handler(LoteError)
    async def _lote(_req: Request, exc: LoteError):
        return JSONResponse({"mensagem": exc.mensagem, "detalhes": exc.detalhes}, status_code=400)

    @app.exception_handler(SefinError)
    async def _sefin(_req: Request, exc: SefinError):
        # Rejeição: o pedido tem problema (400). Demais: falha ao falar com a Sefin/ADN (502).
        st = 400 if exc.tipo in (TipoErro.REJEICAO, TipoErro.VALIDACAO_LOCAL) else 502
        detalhes = [m.texto() + (f" — {m.dica}" if m.dica else "") for m in exc.mensagens]
        return JSONResponse({"mensagem": exc.resumo, "detalhes": detalhes, "sefin": exc.as_dict()}, status_code=st)

    @app.exception_handler(CertificadoError)
    async def _cert(_req: Request, exc: CertificadoError):
        return JSONResponse({"mensagem": str(exc)}, status_code=400)

    @app.exception_handler(ImportacaoError)
    async def _imp(_req: Request, exc: ImportacaoError):
        return JSONResponse({"mensagem": str(exc)}, status_code=400)

    @app.exception_handler(RequestValidationError)
    async def _val(_req: Request, exc: RequestValidationError):
        erros = [f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse({"mensagem": "Dados inválidos na requisição.", "detalhes": erros}, status_code=422)

    app.include_router(routes_config.router, prefix="/api")
    app.include_router(routes_tabela.router, prefix="/api")
    app.include_router(routes_emissoes.router, prefix="/api")

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{caminho:path}", include_in_schema=False)
        async def spa(caminho: str):
            if caminho.startswith("api/"):
                return JSONResponse({"mensagem": "Rota da API não encontrada."}, status_code=404)
            alvo = (FRONTEND_DIST / caminho).resolve()
            if caminho and alvo.is_file() and FRONTEND_DIST.resolve() in alvo.parents:
                return FileResponse(alvo)
            # index.html nunca em cache: assim uma atualização do sistema chega aos navegadores
            # (os arquivos em /assets têm hash no nome e podem ficar em cache).
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app
