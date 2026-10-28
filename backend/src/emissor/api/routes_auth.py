"""Login, primeiro acesso, convites, conta do usuário (senha e 2FA), usuários, auditoria e assinatura da hospedagem."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from emissor import assinatura, auth, config
from emissor.api.deps import AdminAtual, UsuarioAtual, ip_cliente

router = APIRouter()


def _https(request: Request) -> bool:
    forcar = os.environ.get("EMISSOR_COOKIE_SECURE", "").strip().lower()
    if forcar in ("0", "false", "nao", "no"):
        return False
    if forcar in ("1", "true", "sim", "yes"):
        return True
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def _gravar_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE,
        token,
        max_age=int(auth.DURACAO_MAXIMA.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=_https(request),
        path="/",
    )


def _acesso_local(request: Request) -> bool:
    """Acesso pelo próprio servidor, sem proxy na frente: dispensa o código de primeiro acesso."""
    if os.environ.get("EMISSOR_CONVITE_INICIAL", "").strip() or os.environ.get("FLY_APP_NAME"):
        return False
    if any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip", "fly-client-ip")):
        return False
    # O navegador tem de ter aberto o endereço local: um proxy sem esses cabeçalhos ou uma página
    # maliciosa com DNS apontado para 127.0.0.1 (DNS rebinding) chegam com outro Host.
    if urlsplit("//" + request.headers.get("host", "")).hostname not in ("localhost", "127.0.0.1", "::1"):
        return False
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1")


def _ua(request: Request) -> str | None:
    return request.headers.get("user-agent")


# --- público -------------------------------------------------------------------------------------


@router.get("/saude")
def saude() -> dict[str, Any]:
    """Sem login: usado pelo healthcheck do Docker e pela hospedagem para saber se a instância subiu."""
    return {
        "ok": True,
        "versao": config.VER_APLIC,
        "suspenso": auth.suspenso(),
        "administrador_criado": auth.existe_usuario(),
    }


@router.get("/auth/sessao")
def sessao_atual(request: Request) -> dict[str, Any]:
    acesso = getattr(request.state, "acesso", None)
    existe = auth.existe_usuario()
    return {
        "autenticado": acesso is not None,
        "usuario": acesso.as_dict() if acesso else None,
        "primeiro_acesso": not existe,
        "primeiro_acesso_sem_codigo": (not existe) and _acesso_local(request),
        "suspenso": auth.suspenso(),
        "url_assinatura": auth.url_assinatura(),
        "recuperacao_email": auth.recuperacao_por_email(),
    }


class Entrar(BaseModel):
    email: str
    senha: str
    codigo: str | None = None


@router.post("/auth/entrar")
def entrar(entrada: Entrar, request: Request, response: Response) -> dict[str, Any]:
    token, acesso = auth.entrar(entrada.email, entrada.senha, entrada.codigo, ip_cliente(request), _ua(request))
    _gravar_cookie(request, response, token)
    return {"usuario": acesso.as_dict()}


@router.post("/auth/sair")
def sair(request: Request, response: Response) -> dict[str, bool]:
    acesso = getattr(request.state, "acesso", None)
    auth.encerrar_sessao(request.cookies.get(auth.COOKIE))
    if acesso:
        auth.auditar("logout", None, acesso)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


class Esqueci(BaseModel):
    email: str


@router.post("/auth/esqueci")
def esqueci(entrada: Esqueci, request: Request) -> dict[str, Any]:
    auth.solicitar_recuperacao(entrada.email, ip_cliente(request))
    return {
        "ok": True,
        "mensagem": "Se este e-mail tiver acesso ao sistema, chega em instantes um link para criar uma senha nova. "
        "Confira também a caixa de spam.",
    }


class PrimeiroAcesso(BaseModel):
    token: str | None = None
    nome: str = ""
    email: str
    senha: str


@router.post("/auth/primeiro-acesso")
def primeiro_acesso(entrada: PrimeiroAcesso, request: Request, response: Response) -> dict[str, Any]:
    token, acesso = auth.criar_primeiro_admin(
        entrada.token,
        entrada.nome,
        entrada.email,
        entrada.senha,
        _acesso_local(request),
        ip_cliente(request),
        _ua(request),
    )
    _gravar_cookie(request, response, token)
    return {"usuario": acesso.as_dict()}


class TokenConvite(BaseModel):
    token: str


@router.post("/auth/convite/ver")
def ver_convite(entrada: TokenConvite) -> dict[str, Any]:
    return auth.ver_convite(entrada.token)


class AceitarConvite(BaseModel):
    token: str
    senha: str
    nome: str | None = None
    codigo: str | None = None


@router.post("/auth/convite")
def aceitar_convite(entrada: AceitarConvite, request: Request, response: Response) -> dict[str, Any]:
    token, acesso = auth.aceitar_convite(
        entrada.token, entrada.senha, entrada.nome, ip_cliente(request), _ua(request), entrada.codigo
    )
    _gravar_cookie(request, response, token)
    return {"usuario": acesso.as_dict()}


# --- conta do usuário logado ---------------------------------------------------------------------


class TrocarSenha(BaseModel):
    senha_atual: str
    nova_senha: str


@router.post("/auth/senha")
def trocar_senha(entrada: TrocarSenha, acesso: UsuarioAtual) -> dict[str, Any]:
    auth.trocar_senha(acesso, entrada.senha_atual, entrada.nova_senha)
    return {"ok": True, "mensagem": "Senha alterada. Os outros navegadores conectados foram desconectados."}


@router.get("/auth/conta")
def conta(acesso: UsuarioAtual) -> dict[str, Any]:
    return acesso.as_dict() | {"codigos_recuperacao_restantes": auth.codigos_restantes(acesso)}


class Senha(BaseModel):
    senha: str


class Codigo(BaseModel):
    codigo: str


class DesativarDoisFatores(BaseModel):
    senha: str
    codigo: str


@router.post("/auth/2fa/iniciar")
def iniciar_2fa(entrada: Senha, acesso: UsuarioAtual) -> dict[str, Any]:
    return auth.iniciar_2fa(acesso, entrada.senha)


@router.post("/auth/2fa/ativar")
def ativar_2fa(entrada: Codigo, acesso: UsuarioAtual) -> dict[str, Any]:
    return {"codigos_recuperacao": auth.ativar_2fa(acesso, entrada.codigo)}


@router.post("/auth/2fa/desativar")
def desativar_2fa(entrada: DesativarDoisFatores, acesso: UsuarioAtual) -> dict[str, bool]:
    auth.desativar_2fa(acesso, entrada.senha, entrada.codigo)
    return {"ok": True}


# --- usuários (administrador) --------------------------------------------------------------------


@router.get("/usuarios")
def listar_usuarios(_admin: AdminAtual) -> list[dict[str, Any]]:
    return auth.listar_usuarios()


class NovoUsuario(BaseModel):
    email: str
    nome: str = ""
    papel: str = "operador"


@router.post("/usuarios")
def convidar(entrada: NovoUsuario, admin: AdminAtual) -> dict[str, Any]:
    usuario, token = auth.convidar(admin, entrada.email, entrada.nome, entrada.papel)
    return {"usuario": usuario, "link": auth.caminho_convite(token)}


class AlterarUsuario(BaseModel):
    nome: str | None = None
    papel: str | None = None
    ativo: bool | None = None


@router.put("/usuarios/{usuario_id}")
def alterar_usuario(usuario_id: int, entrada: AlterarUsuario, admin: AdminAtual) -> dict[str, Any]:
    return auth.alterar_usuario(admin, usuario_id, entrada.papel, entrada.ativo, entrada.nome)


class Redefinir(BaseModel):
    remover_2fa: bool = False


@router.post("/usuarios/{usuario_id}/redefinir")
def redefinir(usuario_id: int, entrada: Redefinir, admin: AdminAtual) -> dict[str, Any]:
    usuario, token = auth.redefinir_acesso(admin, usuario_id, remover_2fa=entrada.remover_2fa)
    return {"usuario": usuario, "link": auth.caminho_convite(token)}


@router.get("/auditoria")
def auditoria(_admin: AdminAtual, pagina: int = 1) -> dict[str, Any]:
    return auth.listar_auditoria(pagina)


# --- assinatura da hospedagem (aceite da cobrança no fim do teste) -------------------------------


def _erro_assinatura(e: assinatura.AssinaturaError) -> HTTPException:
    return HTTPException(e.status, {"mensagem": str(e), "cartao_recusado": e.cartao_recusado})


@router.get("/assinatura")
def ver_assinatura(acesso: UsuarioAtual, atualizar: bool = False) -> dict[str, Any]:
    return assinatura.situacao(forcar=atualizar) | {"pode_confirmar": acesso.admin}


@router.post("/assinatura/confirmar")
def confirmar_assinatura(admin: AdminAtual) -> dict[str, Any]:
    if not assinatura.ligada():
        raise HTTPException(404, "Esta instalação não tem assinatura.")
    try:
        r = assinatura.confirmar()
    except assinatura.AssinaturaError as e:
        auth.auditar("assinatura_recusada" if e.cartao_recusado else "assinatura_falhou", str(e), admin)
        raise _erro_assinatura(e) from e
    auth.auditar(
        "assinatura_confirmada", "retomada depois do fim do teste" if r.get("reativar") else "fim do teste", admin
    )
    return r


@router.post("/assinatura/recusar")
def recusar_assinatura(admin: AdminAtual) -> dict[str, Any]:
    if not assinatura.ligada():
        raise HTTPException(404, "Esta instalação não tem assinatura.")
    try:
        r = assinatura.recusar()
    except assinatura.AssinaturaError as e:
        raise _erro_assinatura(e) from e
    auth.auditar("assinatura_nao_continuar", None, admin)
    return r
