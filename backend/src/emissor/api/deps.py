"""Dependências de autenticação das rotas: usuário logado, administrador e assinatura suspensa."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from emissor import auth
from emissor.auth import Acesso

# Ações permitidas com a assinatura suspensa, além das leituras (GET): baixar as notas emitidas.
PERMITIDAS_SUSPENSO = {"/api/emissoes/zip", "/api/logs/cliente"}


def ip_cliente(request: Request) -> str | None:
    """IP de quem acessa. No Fly.io o proxy informa o IP real em ``Fly-Client-IP``."""
    if os.environ.get("FLY_APP_NAME") and request.headers.get("fly-client-ip"):
        return request.headers["fly-client-ip"]
    return request.client.host if request.client else None


def exigir_login(request: Request) -> Acesso:
    acesso: Acesso | None = getattr(request.state, "acesso", None)
    if acesso is None:
        raise HTTPException(401, {"mensagem": "Entre com seu usuário para continuar.", "login": True})
    if auth.suspenso() and request.method not in ("GET", "HEAD") and request.url.path not in PERMITIDAS_SUSPENSO:
        raise HTTPException(
            403,
            {
                "mensagem": "A assinatura está suspensa: dá para consultar e baixar as notas, mas não emitir, "
                "cancelar nem alterar configurações.",
                "suspenso": True,
            },
        )
    return acesso


def exigir_admin(acesso: Annotated[Acesso, Depends(exigir_login)]) -> Acesso:
    if not acesso.admin:
        raise HTTPException(403, "Só um administrador pode fazer isto.")
    return acesso


UsuarioAtual = Annotated[Acesso, Depends(exigir_login)]
AdminAtual = Annotated[Acesso, Depends(exigir_admin)]
