"""Envio de e-mail: hoje só o link de recuperação de senha.

Dois caminhos, nesta ordem:

- **Hospedagem:** ``EMISSOR_EMAIL_URL`` + ``EMISSOR_EMAIL_TOKEN`` (definidos pelo painel). O app pede o envio ao
  painel, que manda pelo Resend. O painel monta o e-mail e só aceita links do subdomínio dono do token, então o
  token não serve para mandar outra coisa.
- **Instalação própria:** servidor SMTP em ``EMISSOR_SMTP_HOST`` (``EMISSOR_SMTP_PORTA``, ``EMISSOR_SMTP_USUARIO``,
  ``EMISSOR_SMTP_SENHA``, ``EMISSOR_SMTP_REMETENTE``, ``EMISSOR_SMTP_SEGURANCA`` = starttls | ssl | nenhuma).

Os dois exigem ``EMISSOR_URL_PUBLICA``: o link do e-mail nunca é montado com o ``Host`` da requisição (quem pede a
recuperação poderia trocá-lo e receber o token num endereço seu). Sem configuração, a recuperação por e-mail fica
desligada e a tela de login diz para pedir ao administrador.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import httpx

from emissor import config

log = logging.getLogger(__name__)

REMETENTE_NOME = "Emissor em Planilha"
TIMEOUT = 20


class CorreioError(Exception):
    pass


def _env(nome: str) -> str:
    return os.environ.get(nome, "").strip()


def url_publica() -> str:
    return _env("EMISSOR_URL_PUBLICA").rstrip("/")


def _pelo_painel() -> bool:
    return bool(_env("EMISSOR_EMAIL_URL") and _env("EMISSOR_EMAIL_TOKEN"))


def _por_smtp() -> bool:
    return bool(_env("EMISSOR_SMTP_HOST") and _env("EMISSOR_SMTP_REMETENTE"))


def configurado() -> bool:
    return bool(url_publica().startswith(("http://", "https://")) and (_pelo_painel() or _por_smtp()))


def _textos(nome: str, link: str, minutos: int) -> tuple[str, str, str]:
    saudacao = f"Olá, {nome}." if nome else "Olá."
    assunto = "Redefinir a senha do Emissor em Planilha"
    texto = (
        f"{saudacao}\n\n"
        f"Recebemos um pedido para redefinir a senha do seu acesso em {url_publica()}.\n\n"
        f"Para criar uma senha nova, abra o link abaixo (vale por {minutos} minutos e só pode ser usado uma vez):\n"
        f"{link}\n\n"
        "Se não foi você, ignore este e-mail: a senha atual continua valendo.\n"
    )
    html = (
        f"<p>{_esc(saudacao)}</p>"
        f"<p>Recebemos um pedido para redefinir a senha do seu acesso em {_esc(url_publica())}.</p>"
        f'<p><a href="{_esc(link)}">Criar uma senha nova</a></p>'
        f"<p>O link vale por {minutos} minutos e só pode ser usado uma vez.</p>"
        "<p>Se não foi você, ignore este e-mail: a senha atual continua valendo.</p>"
    )
    return assunto, texto, html


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def enviar_recuperacao(para: str, nome: str, link: str, minutos: int) -> None:
    """Envia o link de recuperação. Levanta ``CorreioError`` se o envio falhar."""
    if _pelo_painel():
        _enviar_painel(para, nome, link, minutos)
    elif _por_smtp():
        _enviar_smtp(para, *_textos(nome, link, minutos))
    else:
        raise CorreioError("Envio de e-mail não configurado.")


def _enviar_painel(para: str, nome: str, link: str, minutos: int) -> None:
    corpo = {"tipo": "recuperacao_senha", "para": para, "nome": nome, "link": link, "validade_minutos": minutos}
    try:
        r = httpx.post(
            _env("EMISSOR_EMAIL_URL"),
            json=corpo,
            headers={"Authorization": f"Bearer {_env('EMISSOR_EMAIL_TOKEN')}", "User-Agent": config.VER_APLIC},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise CorreioError(f"Painel de e-mail fora do ar: {e}") from e
    if r.status_code != 200:
        raise CorreioError(f"Painel de e-mail recusou o envio: HTTP {r.status_code} {r.text[:300]}")


def _enviar_smtp(para: str, assunto: str, texto: str, html: str) -> None:
    remetente = _env("EMISSOR_SMTP_REMETENTE")
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr((REMETENTE_NOME, remetente))
    msg["To"] = para
    msg["Message-ID"] = make_msgid(domain=remetente.rsplit("@", 1)[-1] or None)
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    seguranca = (_env("EMISSOR_SMTP_SEGURANCA") or "starttls").lower()
    porta = int(_env("EMISSOR_SMTP_PORTA") or (465 if seguranca == "ssl" else 587))
    contexto = ssl.create_default_context()
    try:
        if seguranca == "ssl":
            smtp = smtplib.SMTP_SSL(_env("EMISSOR_SMTP_HOST"), porta, timeout=TIMEOUT, context=contexto)
        else:
            smtp = smtplib.SMTP(_env("EMISSOR_SMTP_HOST"), porta, timeout=TIMEOUT)
        with smtp:
            if seguranca == "starttls":
                smtp.starttls(context=contexto)
            if _env("EMISSOR_SMTP_USUARIO"):
                smtp.login(_env("EMISSOR_SMTP_USUARIO"), os.environ.get("EMISSOR_SMTP_SENHA", ""))
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise CorreioError(f"Servidor SMTP recusou o envio: {e}") from e
