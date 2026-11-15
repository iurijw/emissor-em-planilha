"""Aplicação FastAPI: API em ``/api`` e frontend (build do Vite) em ``/``."""

from __future__ import annotations

import ipaddress
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from emissor import auth, config, empresas, tokens_api
from emissor.api import (
    routes_api_v1,
    routes_auth,
    routes_config,
    routes_emissoes,
    routes_empresas,
    routes_entrada,
    routes_integracoes,
    routes_tabela,
    routes_tributacao,
)
from emissor.api.deps import exigir_login, ip_cliente
from emissor.db import EmpresaInexistente, engine
from emissor.logging_setup import bind_context, setup_logging
from emissor.nfse.certificate import CertificadoError
from emissor.nfse.errors import SefinError, TipoErro
from emissor.services import conectores, webhooks
from emissor.services.emissao import LoteError, ReferenciaEmUso, worker
from emissor.services.tabela import ImportacaoError
from emissor.services.tributacao import TributacaoError

log = logging.getLogger("emissor.api")

FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"

# Documentação da API de integração: aberta (não tem dado nenhum), para quem vai integrar ler antes do token.
V1_PUBLICAS = ("/api/v1/openapi.json", "/api/v1/docs")

# Rotas que funcionam sem empresa (usuário sem acesso a nenhuma ainda vê a conta, sai e lê as atualizações).
SEM_EMPRESA = ("/api/auth/", "/api/saude", "/api/logs/cliente", "/api/atualizacoes", "/api/assinatura", "/api/empresas")


DESCRICAO_API_V1 = """
Emita, consulte e cancele NFS-e a partir de outro sistema (cobrança, ERP, loja).

**Acesso:** crie um token em *Configurações → Integrações* (só o administrador da empresa) e envie
`Authorization: Bearer emp_...` em toda requisição. Cada token é de uma empresa e de um ambiente (produção ou
homologação) e tem permissões próprias.

**Sem nota em duplicidade:** envie `referencia_externa` (ou o cabeçalho `Idempotency-Key`) com o identificador da
nota no seu sistema. Repetir o pedido devolve a nota já criada (`repetida: true`) em vez de emitir outra; a
referência só fica livre de novo se a nota for recusada.

**Emissão assíncrona:** `POST /notas` responde 202 com a nota `pendente`; ela é enviada à Sefin pela mesma fila da
tela. Acompanhe por `GET /notas/{id}` ou receba o evento num webhook (`nota.autorizada`, `nota.rejeitada`...).

**Webhooks:** cada entrega leva `X-Emissor-Assinatura: t=<unix>,v1=<hex>`, com `v1 = HMAC-SHA256(segredo,
"<t>.<corpo>")`; confira a assinatura e recuse `t` antigo. Respostas fora de 2xx são repetidas por até 24 h.

**Cobranças do Asaas:** em *Configurações → Integrações → Receber cobranças* o administrador liga o Asaas: cada
pagamento recebido vira uma nota, sem programar nada (a nota leva `referencia_externa` = id da cobrança).

**Limites:** 60 requisições por minuto e 1.000 notas por dia por token (ajustáveis na instalação própria).
"""


def _redes_permitidas() -> list[ipaddress._BaseNetwork]:
    bruto = os.environ.get("EMISSOR_IPS_PERMITIDOS", "").strip()
    if not bruto:
        return []
    return [ipaddress.ip_network(x.strip(), strict=False) for x in bruto.split(",") if x.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    engine()
    auth.anunciar_primeiro_acesso()
    if app.state.iniciar_worker:
        worker().iniciar()
        webhooks.entregador().iniciar()
        conectores.processador().iniciar()
    log.info("servidor iniciado", extra={"versao": config.VER_APLIC, "data_dir": str(config.DATA_DIR)})
    yield


def create_app(iniciar_worker: bool = True) -> FastAPI:
    # /docs e o OpenAPI ficam atrás do login (rotas próprias abaixo).
    app = FastAPI(
        title="Emissor em Planilha",
        version=config.APP_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
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
        caminho = request.url.path
        protegido = caminho.startswith("/api") or caminho == "/docs"
        # API de integração: só token (Authorization: Bearer), nunca o cookie da tela; por isso também sem o
        # risco de CSRF (o navegador não manda o token sozinho) e sem X-Empresa (o token é de uma empresa só).
        api_v1 = caminho.startswith("/api/v1/") and caminho not in V1_PUBLICAS
        # CSRF: além do cookie SameSite=Lax, recusa escrita vinda de outra origem (outro site ou subdomínio).
        if protegido and not api_v1 and request.method not in ("GET", "HEAD", "OPTIONS"):
            origem = request.headers.get("origin")
            if origem and urlsplit(origem).netloc != request.headers.get("host"):
                log.warning("requisição de outra origem recusada", extra={"origem": origem, "path": caminho})
                return JSONResponse({"mensagem": "Requisição de outra origem recusada."}, status_code=403)
        request.state.acesso = None
        empresas.definir(None)
        if api_v1:
            autorizacao = request.headers.get("authorization", "")
            bearer = autorizacao[7:].strip() if autorizacao[:7].lower() == "bearer " else ""
            if bearer:
                acesso = await run_in_threadpool(tokens_api.resolver, bearer, ip_cliente(request))
                if acesso is None:
                    log.warning("token da API recusado", extra={"path": caminho, "ip": ip})
                    return JSONResponse({"mensagem": "Token inválido ou revogado.", "login": True}, status_code=401)
                espera = tokens_api.excedeu_limite(acesso.token_id)
                if espera is not None:
                    return JSONResponse(
                        {"mensagem": f"Limite de {tokens_api.LIMITE_POR_MINUTO} requisições por minuto atingido."},
                        status_code=429,
                        headers={"Retry-After": str(round(espera))},
                    )
                request.state.acesso = acesso
                empresas.definir(acesso.empresa_id)
        elif protegido and request.cookies.get(auth.COOKIE):
            request.state.acesso = await run_in_threadpool(
                auth.resolver_sessao, request.cookies.get(auth.COOKIE), ip_cliente(request)
            )
            acesso = request.state.acesso
            if acesso is not None:
                pedida = request.headers.get("x-empresa") or request.query_params.get("empresa")
                try:
                    eid, papel = await run_in_threadpool(empresas.resolver, acesso.usuario_id, acesso.admin, pedida)
                except empresas.EmpresaNegada as e:
                    if not caminho.startswith(SEM_EMPRESA):
                        return JSONResponse({"mensagem": e.mensagem, "sem_empresa": True}, status_code=e.status)
                    eid, papel = None, None
                request.state.acesso = replace(acesso, empresa_id=eid, papel_empresa=papel)
                empresas.definir(eid)
        usuario = request.state.acesso.email if request.state.acesso else None
        inicio = time.perf_counter()
        with bind_context(request_id=rid, usuario=usuario):
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

    @app.exception_handler(auth.AuthError)
    async def _auth(_req: Request, exc: auth.AuthError):
        return JSONResponse({"mensagem": exc.mensagem, **exc.extra}, status_code=exc.status)

    @app.exception_handler(empresas.SemEmpresa)
    async def _sem_empresa(_req: Request, exc: empresas.SemEmpresa):
        log.error("operação sem empresa definida", exc_info=exc)
        return JSONResponse({"mensagem": "Escolha a empresa antes de continuar.", "sem_empresa": True}, status_code=400)

    @app.exception_handler(EmpresaInexistente)
    async def _empresa_inexistente(_req: Request, exc: EmpresaInexistente):
        return JSONResponse({"mensagem": str(exc)}, status_code=404)

    @app.exception_handler(LoteError)
    async def _lote(_req: Request, exc: LoteError):
        status = 409 if isinstance(exc, ReferenciaEmUso) else 400
        return JSONResponse({"mensagem": exc.mensagem, "detalhes": exc.detalhes}, status_code=status)

    @app.exception_handler(tokens_api.TokenError)
    async def _token(_req: Request, exc: tokens_api.TokenError):
        return JSONResponse({"mensagem": exc.mensagem}, status_code=exc.status)

    @app.exception_handler(webhooks.WebhookError)
    async def _webhook(_req: Request, exc: webhooks.WebhookError):
        return JSONResponse({"mensagem": exc.mensagem}, status_code=exc.status)

    @app.exception_handler(conectores.ConectorError)
    async def _conector(_req: Request, exc: conectores.ConectorError):
        return JSONResponse({"mensagem": exc.mensagem}, status_code=exc.status)

    @app.exception_handler(TributacaoError)
    async def _tributacao(_req: Request, exc: TributacaoError):
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

    app.include_router(routes_auth.router, prefix="/api")
    logado = [Depends(exigir_login)]
    app.include_router(routes_config.router, prefix="/api", dependencies=logado)
    app.include_router(routes_tabela.router, prefix="/api", dependencies=logado)
    app.include_router(routes_tributacao.router, prefix="/api", dependencies=logado)
    app.include_router(routes_emissoes.router, prefix="/api", dependencies=logado)
    app.include_router(routes_empresas.router, prefix="/api", dependencies=logado)
    app.include_router(routes_integracoes.router, prefix="/api", dependencies=logado)
    app.include_router(routes_entrada.router, prefix="/api")
    app.include_router(routes_api_v1.router, prefix="/api")

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    async def openapi_v1():
        if not hasattr(app.state, "openapi_v1"):
            doc = FastAPI(title="Emissor em Planilha — API de integração", version="1", description=DESCRICAO_API_V1)
            doc.include_router(routes_api_v1.router, prefix="/api")
            app.state.openapi_v1 = doc.openapi()
            app.state.openapi_v1["components"].setdefault("securitySchemes", {})["token"] = {
                "type": "http",
                "scheme": "bearer",
                "description": "Token criado em Configurações → Integrações (emp_...).",
            }
            app.state.openapi_v1["security"] = [{"token": []}]
        return app.state.openapi_v1

    @app.get("/api/v1/docs", include_in_schema=False)
    async def docs_v1():
        return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="Emissor em Planilha — API de integração")

    @app.get("/api/openapi.json", include_in_schema=False, dependencies=logado)
    async def openapi():
        return app.openapi()

    @app.get("/docs", include_in_schema=False, dependencies=logado)
    async def docs():
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Emissor em Planilha — API")

    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{caminho:path}", include_in_schema=False)
        async def spa(caminho: str):
            if caminho.startswith("api/"):
                return JSONResponse({"mensagem": "Rota da API não encontrada."}, status_code=404)
            if "\x00" in caminho:  # robôs procurando arquivos: o resolve() quebraria com erro 500
                return JSONResponse({"mensagem": "Página não encontrada."}, status_code=404)
            alvo = (FRONTEND_DIST / caminho).resolve()
            if caminho and alvo.is_file() and FRONTEND_DIST.resolve() in alvo.parents:
                # O service worker (aviso "ligando o sistema") também nunca em cache: troca junto com a versão.
                return FileResponse(alvo, headers={"Cache-Control": "no-cache"} if caminho == "sw.js" else None)
            # index.html nunca em cache: assim uma atualização do sistema chega aos navegadores
            # (os arquivos em /assets têm hash no nome e podem ficar em cache).
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app
