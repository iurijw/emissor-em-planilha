"""Avisos e conversa com o suporte (só na hospedagem).

Os avisos e as mensagens ficam no painel da hospedagem, não aqui: o navegador busca direto lá enquanto o app está
aberto, então uma mensagem do suporte não acorda nem mantém ligada esta instância. Esta parte só entrega ao navegador
um passe curto, assinado com o mesmo ``EMISSOR_EMAIL_TOKEN`` do envio de e-mails, que diz quem é o usuário.

Passe: ``<payload>.<assinatura>``, payload = base64url(JSON ``{u, n, e, p, x}``: id, nome, e-mail, papel e validade em
segundos epoch) e assinatura = base64url(HMAC-SHA256(token, payload)). O painel recalcula o token pelo subdomínio.

Variáveis definidas pelo painel: ``EMISSOR_SUPORTE_URL`` (endereço desta instância no painel) e
``EMISSOR_EMAIL_TOKEN``. Sem elas (instalação própria), nada disso aparece.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

from emissor.auth import Acesso

VALIDADE_S = 30 * 60


def _env(nome: str) -> str:
    return os.environ.get(nome, "").strip()


def ligada() -> bool:
    return bool(_env("EMISSOR_SUPORTE_URL").startswith("https://") and _env("EMISSOR_EMAIL_TOKEN"))


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode()


def passe(acesso: Acesso, agora: float | None = None) -> dict[str, Any]:
    expira = int(agora if agora is not None else time.time()) + VALIDADE_S
    dados = {
        "u": acesso.usuario_id,
        "n": acesso.nome or "",
        "e": acesso.email,
        "p": "admin" if acesso.admin else "operador",
        "x": expira,
    }
    payload = _b64(json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode())
    assinatura = _b64(hmac.new(_env("EMISSOR_EMAIL_TOKEN").encode(), payload.encode(), hashlib.sha256).digest())
    return {"url": _env("EMISSOR_SUPORTE_URL").rstrip("/"), "passe": f"{payload}.{assinatura}", "expira": expira}
