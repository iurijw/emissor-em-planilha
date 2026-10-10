"""Autenticação: usuários, senhas (Argon2id), sessões, 2FA (TOTP), convites e auditoria.

Tudo roda dentro do próprio app, sem serviço pago: serve tanto para a instalação na rede local
quanto para a hospedagem (uma instância por cliente).

- Senha: Argon2id (``argon2-cffi``), mínimo de 10 caracteres.
- Sessão: token aleatório em cookie ``HttpOnly``/``SameSite=Lax`` (``Secure`` em HTTPS); o banco
  guarda só o SHA-256 do token. Expira após ``INATIVIDADE`` sem uso ou ``DURACAO_MAXIMA``.
- Bloqueio: ``MAX_FALHAS`` erros seguidos bloqueiam o usuário por ``BLOQUEIO``; por IP, no máximo
  ``MAX_FALHAS_IP`` erros na janela ``BLOQUEIO`` (em memória).
- 2FA: TOTP (RFC 6238, SHA-1, 6 dígitos, 30 s, tolerância de ±1 passo), segredo criptografado com a
  mesma chave do certificado; 10 códigos de recuperação de uso único.
- Primeiro acesso: enquanto não existe usuário, o administrador é criado com o token de
  ``EMISSOR_CONVITE_INICIAL`` (gerado pela hospedagem e enviado por e-mail ao cliente) ou, sem a
  variável, com um token gerado e gravado em ``data/primeiro-acesso.txt`` (e mostrado no log).
  Acessos pelo próprio servidor (127.0.0.1) dispensam o token.
- Convites e redefinição de senha: link de uso único criado pelo administrador (ou pela linha de
  comando ``emissor-admin``), válido por ``VALIDADE_CONVITE``. Não há envio de e-mail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlmodel import Session, func, select

from emissor import config, security
from emissor.db import Auditoria, ConviteUsuario, SessaoUsuario, Usuario, agora, sessao

log = logging.getLogger(__name__)

COOKIE = "emissor_sessao"
INATIVIDADE = timedelta(hours=12)
DURACAO_MAXIMA = timedelta(days=7)
MAX_FALHAS = 10
MAX_FALHAS_IP = 30
BLOQUEIO = timedelta(minutes=15)
VALIDADE_CONVITE = timedelta(days=7)
SENHA_MIN = 10
SENHA_MAX = 256
PAPEIS = ("admin", "operador")
TOTP_EMISSOR = "Emissor em Planilha"
ARQUIVO_PRIMEIRO_ACESSO = "primeiro-acesso.txt"

_hasher = PasswordHasher()  # Argon2id com os parâmetros recomendados pela biblioteca
# Hash de referência para gastar o mesmo tempo quando o e-mail não existe.
_HASH_FICTICIO = _hasher.hash(secrets.token_urlsafe(16))


class AuthError(Exception):
    """Erro de autenticação mostrado ao usuário (``status`` vira o código HTTP)."""

    def __init__(self, mensagem: str, status: int = 400, **extra):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.extra = extra


@dataclass(frozen=True)
class Acesso:
    """Quem está fazendo a requisição (vai para a auditoria e para o log)."""

    usuario_id: int
    email: str
    nome: str
    papel: str
    totp_ativo: bool
    sessao_id: str
    ip: str | None = None

    @property
    def admin(self) -> bool:
        return self.papel == "admin"

    def as_dict(self) -> dict:
        return {
            "id": self.usuario_id,
            "email": self.email,
            "nome": self.nome,
            "papel": self.papel,
            "totp_ativo": self.totp_ativo,
        }


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def validar_email(email: str) -> str:
    e = normalizar_email(email)
    if len(e) > 254 or "@" not in e or e.startswith("@") or e.endswith("@") or " " in e:
        raise AuthError("Informe um e-mail válido.")
    return e


def validar_senha(senha: str, email: str = "") -> None:
    if len(senha or "") < SENHA_MIN:
        raise AuthError(f"A senha precisa ter pelo menos {SENHA_MIN} caracteres.")
    if len(senha) > SENHA_MAX:
        raise AuthError(f"A senha pode ter no máximo {SENHA_MAX} caracteres.")
    if email and senha.strip().lower() == normalizar_email(email):
        raise AuthError("A senha não pode ser igual ao e-mail.")
    if len(set(senha)) < 4:
        raise AuthError("A senha é fácil demais de adivinhar. Use uma frase ou mais caracteres diferentes.")


def hash_senha(senha: str) -> str:
    return _hasher.hash(senha)


def conferir_senha(u: Usuario | None, senha: str) -> bool:
    if u is None or not u.senha_hash:
        try:
            _hasher.verify(_HASH_FICTICIO, senha or "")
        except VerificationError:
            pass
        return False
    try:
        return _hasher.verify(u.senha_hash, senha or "")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# --- TOTP (RFC 6238) ---------------------------------------------------------------------------


def novo_segredo_totp() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(segredo_b32: str, passo: int) -> str:
    chave = base64.b32decode(segredo_b32 + "=" * (-len(segredo_b32) % 8))
    mac = hmac.new(chave, struct.pack(">Q", passo), hashlib.sha1).digest()
    o = mac[-1] & 0x0F
    numero = (struct.unpack(">I", mac[o : o + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{numero:06d}"


def totp_agora(segredo_b32: str, instante: float | None = None) -> str:
    return _hotp(segredo_b32, int((instante if instante is not None else time.time()) // 30))


def conferir_totp(segredo_b32: str, codigo: str, ultimo_passo: int, instante: float | None = None) -> int | None:
    """Devolve o passo aceito (para gravar e impedir reuso) ou None."""
    codigo = "".join(c for c in (codigo or "") if c.isdigit())
    if len(codigo) != 6:
        return None
    atual = int((instante if instante is not None else time.time()) // 30)
    for passo in (atual - 1, atual, atual + 1):
        if passo > ultimo_passo and hmac.compare_digest(_hotp(segredo_b32, passo), codigo):
            return passo
    return None


def uri_totp(segredo_b32: str, email: str) -> str:
    rotulo = quote(f"{TOTP_EMISSOR}:{email}")
    return (
        f"otpauth://totp/{rotulo}?secret={segredo_b32}&issuer={quote(TOTP_EMISSOR)}&algorithm=SHA1&digits=6&period=30"
    )


def qr_svg_data_uri(texto: str) -> str:
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    w = QrCodeWidget(texto)
    x0, y0, x1, y1 = w.getBounds()
    d = Drawing(x1 - x0, y1 - y0)
    d.add(w)
    svg = renderSVG.drawToString(d)
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def _segredo(u: Usuario) -> str | None:
    return security.decrypt(u.totp_segredo_enc).decode() if u.totp_segredo_enc else None


def _novos_codigos_recuperacao() -> list[str]:
    return [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(10)]


def _normalizar_codigo_recuperacao(c: str) -> str:
    return "".join(ch for ch in (c or "").lower() if ch in "0123456789abcdef")


def _usar_segundo_fator(u: Usuario, codigo: str) -> bool:
    """Confere o código do app (TOTP) ou um código de recuperação; grava o uso em ``u``."""
    segredo = _segredo(u)
    if segredo:
        passo = conferir_totp(segredo, codigo, u.totp_ultimo_passo)
        if passo is not None:
            u.totp_ultimo_passo = passo
            return True
    norm = _normalizar_codigo_recuperacao(codigo)
    if len(norm) == 12:
        codigos = json.loads(u.codigos_recuperacao_json or "[]")
        h = _sha256(norm)
        if h in codigos:
            codigos.remove(h)
            u.codigos_recuperacao_json = json.dumps(codigos)
            return True
    return False


# --- limite de tentativas por IP (em memória) --------------------------------------------------

_falhas_ip: dict[str, deque[float]] = {}
_lock_ip = threading.Lock()


def _ip_bloqueado(ip: str | None) -> bool:
    if not ip:
        return False
    limite = time.monotonic() - BLOQUEIO.total_seconds()
    with _lock_ip:
        fila = _falhas_ip.get(ip)
        while fila and fila[0] < limite:
            fila.popleft()
        return bool(fila) and len(fila) >= MAX_FALHAS_IP


def _registrar_falha_ip(ip: str | None) -> None:
    if ip:
        with _lock_ip:
            _falhas_ip.setdefault(ip, deque()).append(time.monotonic())


def limpar_limites() -> None:
    """Para testes."""
    with _lock_ip:
        _falhas_ip.clear()


# --- auditoria -----------------------------------------------------------------------------------


def auditar(
    acao: str,
    detalhe: str | None = None,
    acesso: Acesso | None = None,
    *,
    usuario: Usuario | None = None,
    ip: str | None = None,
    s: Session | None = None,
) -> None:
    reg = Auditoria(
        acao=acao,
        detalhe=(detalhe or None) and detalhe[:1000],
        usuario_id=acesso.usuario_id if acesso else (usuario.id if usuario else None),
        usuario_email=acesso.email if acesso else (usuario.email if usuario else None),
        ip=(acesso.ip if acesso else None) or ip,
    )
    if s is not None:
        s.add(reg)
        return
    with sessao() as s2:
        s2.add(reg)
        s2.commit()


# --- sessões -------------------------------------------------------------------------------------


def _criar_sessao(s: Session, u: Usuario, ip: str | None, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    s.add(SessaoUsuario(id=_sha256(token), usuario_id=u.id, ip=ip, user_agent=(user_agent or "")[:300] or None))
    return token


def resolver_sessao(token: str | None, ip: str | None = None) -> Acesso | None:
    if not token or len(token) > 200:
        return None
    sid = _sha256(token)
    with sessao() as s:
        ses = s.get(SessaoUsuario, sid)
        if ses is None:
            return None
        agora_ = agora()
        u = s.get(Usuario, ses.usuario_id)
        if (
            u is None
            or not u.ativo
            or not u.senha_hash
            or agora_ - ses.ultimo_uso > INATIVIDADE
            or agora_ - ses.criada_em > DURACAO_MAXIMA
        ):
            s.delete(ses)
            s.commit()
            return None
        # Grava o uso de tempos em tempos (não a cada requisição).
        if agora_ - ses.ultimo_uso > timedelta(minutes=5):
            ses.ultimo_uso = agora_
            s.add(ses)
            s.commit()
        return Acesso(u.id, u.email, u.nome, u.papel, u.totp_ativo, sid, ip)


def encerrar_sessao(token: str | None) -> None:
    if not token:
        return
    with sessao() as s:
        ses = s.get(SessaoUsuario, _sha256(token))
        if ses:
            s.delete(ses)
            s.commit()


def _encerrar_sessoes(s: Session, usuario_id: int, exceto: str | None = None) -> None:
    for ses in s.exec(select(SessaoUsuario).where(SessaoUsuario.usuario_id == usuario_id)):
        if ses.id != exceto:
            s.delete(ses)


# --- login ---------------------------------------------------------------------------------------


def entrar(email: str, senha: str, codigo: str | None, ip: str | None, user_agent: str | None) -> tuple[str, Acesso]:
    """Confere e-mail, senha e (se ativo) 2FA. Devolve o token da sessão e o acesso."""
    if _ip_bloqueado(ip):
        raise AuthError("Muitas tentativas erradas a partir deste endereço. Aguarde 15 minutos e tente de novo.", 429)
    email_n = normalizar_email(email)
    with sessao() as s:
        u = s.exec(select(Usuario).where(Usuario.email == email_n)).first()
        agora_ = agora()
        if u is not None and u.bloqueado_ate and u.bloqueado_ate > agora_:
            minutos = max(1, int((u.bloqueado_ate - agora_).total_seconds() // 60) + 1)
            raise AuthError(
                f"Usuário bloqueado por excesso de tentativas erradas. Tente de novo em {minutos} minuto(s) "
                "ou peça a um administrador para redefinir a senha.",
                429,
            )
        if not conferir_senha(u, senha) or u is None or not u.ativo:
            _falhou(s, u, ip, "senha")
            raise AuthError("E-mail ou senha incorretos.", 401)
        if u.totp_ativo:
            if not codigo:
                # Senha certa: pede o segundo fator (não conta como falha).
                raise AuthError("Digite o código do aplicativo autenticador.", 401, precisa_codigo=True)
            if not _usar_segundo_fator(u, codigo):
                _falhou(s, u, ip, "codigo")
                raise AuthError("Código de verificação incorreto ou já usado.", 401, precisa_codigo=True)
        u.falhas_login = 0
        u.bloqueado_ate = None
        u.ultimo_login = agora_
        s.add(u)
        token = _criar_sessao(s, u, ip, user_agent)
        auditar("login", None, usuario=u, ip=ip, s=s)
        s.commit()
        acesso = Acesso(u.id, u.email, u.nome, u.papel, u.totp_ativo, _sha256(token), ip)
    log.info("login", extra={"usuario": u.email, "ip": ip})
    return token, acesso


def _falhou(s: Session, u: Usuario | None, ip: str | None, motivo: str) -> None:
    _registrar_falha_ip(ip)
    if u is not None:
        u.falhas_login += 1
        if u.falhas_login >= MAX_FALHAS:
            u.bloqueado_ate = agora() + BLOQUEIO
            u.falhas_login = 0
            auditar("usuario_bloqueado", f"{MAX_FALHAS} tentativas erradas", usuario=u, ip=ip, s=s)
            log.warning("usuário bloqueado por tentativas erradas", extra={"usuario": u.email, "ip": ip})
        s.add(u)
    auditar("login_falhou", motivo, usuario=u, ip=ip, s=s)
    s.commit()
    log.info("login recusado", extra={"usuario": u.email if u else None, "ip": ip, "motivo": motivo})


# --- primeiro acesso -----------------------------------------------------------------------------


def existe_usuario() -> bool:
    with sessao() as s:
        return s.exec(select(func.count()).select_from(Usuario)).one() > 0


def _arquivo_primeiro_acesso():
    return config.DATA_DIR / ARQUIVO_PRIMEIRO_ACESSO


def token_primeiro_acesso() -> str:
    """Token que libera a criação do primeiro administrador (variável ou arquivo em data/)."""
    env = os.environ.get("EMISSOR_CONVITE_INICIAL", "").strip()
    if env:
        return env
    p = _arquivo_primeiro_acesso()
    if p.exists():
        t = p.read_text(encoding="utf-8").strip().splitlines()
        if t and t[0].strip():
            return t[0].strip()
    config.ensure_dirs()
    token = secrets.token_urlsafe(24)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(
            f"{token}\n\nCódigo de primeiro acesso do Emissor em Planilha. Abra o sistema no navegador e "
            "informe este código para criar o administrador. Este arquivo é apagado depois disso.\n"
        )
    return token


def anunciar_primeiro_acesso() -> None:
    """Na subida do servidor sem usuários, mostra no log como criar o administrador."""
    if existe_usuario() or os.environ.get("EMISSOR_CONVITE_INICIAL", "").strip():
        return
    token = token_primeiro_acesso()
    log.warning(
        "Nenhum usuário cadastrado. Abra o sistema no navegador para criar o administrador. "
        "Pelo próprio servidor (http://127.0.0.1:%s) o código não é pedido; de outro computador, use o código "
        "de primeiro acesso: %s (também em %s).",
        os.environ.get("EMISSOR_PORT", "8000"),
        token,
        _arquivo_primeiro_acesso(),
    )


_lock_primeiro = threading.Lock()


def criar_primeiro_admin(
    token: str | None, nome: str, email: str, senha: str, local: bool, ip: str | None, user_agent: str | None
) -> tuple[str, Acesso]:
    if _ip_bloqueado(ip):
        raise AuthError("Muitas tentativas erradas a partir deste endereço. Aguarde 15 minutos.", 429)
    email_n = validar_email(email)
    validar_senha(senha, email_n)
    with _lock_primeiro, sessao() as s:
        if s.exec(select(func.count()).select_from(Usuario)).one() > 0:
            raise AuthError("O administrador já foi criado. Entre com seu e-mail e senha.", 409)
        if not local and not (token and hmac.compare_digest(token.strip(), token_primeiro_acesso())):
            _registrar_falha_ip(ip)
            raise AuthError("Código de primeiro acesso inválido.", 403)
        u = Usuario(email=email_n, nome=(nome or "").strip()[:120], papel="admin", senha_hash=hash_senha(senha))
        u.ultimo_login = agora()
        s.add(u)
        s.flush()
        tok = _criar_sessao(s, u, ip, user_agent)
        auditar("primeiro_acesso", "administrador criado", usuario=u, ip=ip, s=s)
        s.commit()
        acesso = Acesso(u.id, u.email, u.nome, u.papel, False, _sha256(tok), ip)
    _arquivo_primeiro_acesso().unlink(missing_ok=True)
    log.info("administrador inicial criado", extra={"usuario": email_n, "ip": ip})
    return tok, acesso


# --- convites e redefinição de senha -------------------------------------------------------------


def criar_convite(s: Session, u: Usuario, finalidade: str) -> str:
    # Um convite novo invalida os anteriores do mesmo usuário.
    for c in s.exec(select(ConviteUsuario).where(ConviteUsuario.usuario_id == u.id, ConviteUsuario.usado_em == None)):  # noqa: E711
        c.usado_em = agora()
        s.add(c)
    token = secrets.token_urlsafe(32)
    s.add(
        ConviteUsuario(id=_sha256(token), usuario_id=u.id, finalidade=finalidade, expira_em=agora() + VALIDADE_CONVITE)
    )
    return token


def caminho_convite(token: str) -> str:
    """Caminho relativo do link (o token vai depois do #, então não aparece em logs de servidores)."""
    return f"/#/convite?token={token}"


def link_convite(token: str) -> str:
    base = os.environ.get("EMISSOR_URL_PUBLICA", "").strip().rstrip("/")
    return f"{base}{caminho_convite(token)}"


def _convite_valido(s: Session, token: str) -> tuple[ConviteUsuario, Usuario]:
    c = s.get(ConviteUsuario, _sha256(token or ""))
    if c is None or c.usado_em is not None or c.expira_em < agora():
        raise AuthError("Este link não vale mais (já foi usado ou expirou). Peça um novo ao administrador.", 410)
    u = s.get(Usuario, c.usuario_id)
    if u is None or not u.ativo:
        raise AuthError("Este usuário foi desativado.", 410)
    return c, u


def ver_convite(token: str) -> dict:
    with sessao() as s:
        c, u = _convite_valido(s, token)
        return {"email": u.email, "nome": u.nome, "finalidade": c.finalidade}


def aceitar_convite(
    token: str, senha: str, nome: str | None, ip: str | None, user_agent: str | None
) -> tuple[str, Acesso]:
    if _ip_bloqueado(ip):
        raise AuthError("Muitas tentativas erradas a partir deste endereço. Aguarde 15 minutos.", 429)
    with sessao() as s:
        try:
            c, u = _convite_valido(s, token)
        except AuthError:
            _registrar_falha_ip(ip)
            raise
        validar_senha(senha, u.email)
        u.senha_hash = hash_senha(senha)
        if nome and nome.strip():
            u.nome = nome.strip()[:120]
        u.falhas_login = 0
        u.bloqueado_ate = None
        u.ultimo_login = agora()
        c.usado_em = agora()
        _encerrar_sessoes(s, u.id)
        s.add_all([u, c])
        tok = _criar_sessao(s, u, ip, user_agent)
        auditar("convite_aceito" if c.finalidade == "convite" else "senha_redefinida", None, usuario=u, ip=ip, s=s)
        s.commit()
        return tok, Acesso(u.id, u.email, u.nome, u.papel, u.totp_ativo, _sha256(tok), ip)


# --- conta do próprio usuário --------------------------------------------------------------------


def _usuario(s: Session, acesso: Acesso) -> Usuario:
    u = s.get(Usuario, acesso.usuario_id)
    if u is None:
        raise AuthError("Usuário não encontrado.", 404)
    return u


def trocar_senha(acesso: Acesso, atual: str, nova: str) -> None:
    with sessao() as s:
        u = _usuario(s, acesso)
        if not conferir_senha(u, atual):
            raise AuthError("A senha atual está incorreta.", 400)
        validar_senha(nova, u.email)
        u.senha_hash = hash_senha(nova)
        s.add(u)
        _encerrar_sessoes(s, u.id, exceto=acesso.sessao_id)  # desconecta os outros navegadores
        auditar("senha_alterada", None, acesso, s=s)
        s.commit()


def iniciar_2fa(acesso: Acesso, senha: str) -> dict:
    with sessao() as s:
        u = _usuario(s, acesso)
        if not conferir_senha(u, senha):
            raise AuthError("Senha incorreta.", 400)
        if u.totp_ativo:
            raise AuthError("A verificação em duas etapas já está ativa.", 409)
        segredo = novo_segredo_totp()
        u.totp_segredo_enc = security.encrypt(segredo.encode())
        s.add(u)
        s.commit()
    uri = uri_totp(segredo, acesso.email)
    return {"segredo": segredo, "uri": uri, "qr": qr_svg_data_uri(uri)}


def ativar_2fa(acesso: Acesso, codigo: str) -> list[str]:
    with sessao() as s:
        u = _usuario(s, acesso)
        segredo = _segredo(u)
        if u.totp_ativo or not segredo:
            raise AuthError("Comece a configuração da verificação em duas etapas de novo.", 409)
        passo = conferir_totp(segredo, codigo, u.totp_ultimo_passo)
        if passo is None:
            raise AuthError("Código incorreto. Confira se o relógio do celular está certo e tente o código atual.")
        codigos = _novos_codigos_recuperacao()
        u.totp_ultimo_passo = passo
        u.totp_ativo = True
        u.codigos_recuperacao_json = json.dumps([_sha256(_normalizar_codigo_recuperacao(c)) for c in codigos])
        s.add(u)
        _encerrar_sessoes(s, u.id, exceto=acesso.sessao_id)
        auditar("2fa_ativada", None, acesso, s=s)
        s.commit()
    return codigos


def desativar_2fa(acesso: Acesso, senha: str, codigo: str) -> None:
    with sessao() as s:
        u = _usuario(s, acesso)
        if not u.totp_ativo:
            return
        if not conferir_senha(u, senha) or not _usar_segundo_fator(u, codigo):
            raise AuthError("Senha ou código incorretos.")
        _limpar_2fa(u)
        s.add(u)
        auditar("2fa_desativada", None, acesso, s=s)
        s.commit()


def _limpar_2fa(u: Usuario) -> None:
    u.totp_ativo = False
    u.totp_segredo_enc = None
    u.codigos_recuperacao_json = "[]"


def codigos_restantes(acesso: Acesso) -> int:
    with sessao() as s:
        return len(json.loads(_usuario(s, acesso).codigos_recuperacao_json or "[]"))


# --- administração de usuários -------------------------------------------------------------------


def usuario_dict(u: Usuario) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "nome": u.nome,
        "papel": u.papel,
        "ativo": u.ativo,
        "totp_ativo": u.totp_ativo,
        "convite_pendente": not u.senha_hash,
        "bloqueado": bool(u.bloqueado_ate and u.bloqueado_ate > agora()),
        "criado_em": u.criado_em.isoformat() if u.criado_em else None,
        "ultimo_login": u.ultimo_login.isoformat() if u.ultimo_login else None,
    }


def listar_usuarios() -> list[dict]:
    with sessao() as s:
        return [usuario_dict(u) for u in s.exec(select(Usuario).order_by(Usuario.email))]


def _validar_papel(papel: str) -> str:
    if papel not in PAPEIS:
        raise AuthError("Papel inválido: use admin ou operador.")
    return papel


def convidar(acesso: Acesso | None, email: str, nome: str, papel: str) -> tuple[dict, str]:
    """Cria o usuário (sem senha) e devolve o token do convite."""
    email_n = validar_email(email)
    _validar_papel(papel)
    with sessao() as s:
        if s.exec(select(Usuario).where(Usuario.email == email_n)).first():
            raise AuthError("Já existe um usuário com este e-mail.", 409)
        u = Usuario(email=email_n, nome=(nome or "").strip()[:120], papel=papel)
        s.add(u)
        s.flush()
        token = criar_convite(s, u, "convite")
        auditar("usuario_convidado", f"{email_n} ({papel})", acesso, s=s)
        s.commit()
        return usuario_dict(u), token


def _admins_ativos(s: Session) -> int:
    return s.exec(
        select(func.count()).select_from(Usuario).where(Usuario.papel == "admin", Usuario.ativo == True)  # noqa: E712
    ).one()


def alterar_usuario(
    acesso: Acesso | None, usuario_id: int, papel: str | None, ativo: bool | None, nome: str | None
) -> dict:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if u is None:
            raise AuthError("Usuário não encontrado.", 404)
        mudancas = []
        perde_admin = (papel is not None and papel != "admin" and u.papel == "admin") or (
            ativo is False and u.ativo and u.papel == "admin"
        )
        if perde_admin and _admins_ativos(s) <= 1:
            raise AuthError("Este é o único administrador ativo. Promova outro usuário antes.")
        if acesso and acesso.usuario_id == u.id and ativo is False:
            raise AuthError("Você não pode desativar o próprio usuário.")
        if papel is not None and papel != u.papel:
            u.papel = _validar_papel(papel)
            mudancas.append(f"papel={papel}")
        if ativo is not None and ativo != u.ativo:
            u.ativo = ativo
            mudancas.append("ativado" if ativo else "desativado")
            if not ativo:
                _encerrar_sessoes(s, u.id)
        if nome is not None and nome.strip() != u.nome:
            u.nome = nome.strip()[:120]
            mudancas.append("nome")
        if mudancas:
            s.add(u)
            auditar("usuario_alterado", f"{u.email}: {', '.join(mudancas)}", acesso, s=s)
            s.commit()
        return usuario_dict(u)


def redefinir_acesso(
    acesso: Acesso | None, usuario_id: int | None = None, email: str | None = None, remover_2fa: bool = False
) -> tuple[dict, str]:
    """Gera um link para o usuário definir nova senha; a senha atual deixa de valer e as sessões caem."""
    with sessao() as s:
        if usuario_id is not None:
            u = s.get(Usuario, usuario_id)
        else:
            u = s.exec(select(Usuario).where(Usuario.email == normalizar_email(email or ""))).first()
        if u is None:
            raise AuthError("Usuário não encontrado.", 404)
        finalidade = "convite" if not u.senha_hash else "redefinicao"
        u.senha_hash = None
        u.falhas_login = 0
        u.bloqueado_ate = None
        if remover_2fa:
            _limpar_2fa(u)
        _encerrar_sessoes(s, u.id)
        token = criar_convite(s, u, finalidade)
        s.add(u)
        auditar("acesso_redefinido", f"{u.email}{' (2FA removida)' if remover_2fa else ''}", acesso, s=s)
        s.commit()
        return usuario_dict(u), token


def listar_auditoria(pagina: int = 1, por_pagina: int = 100) -> dict:
    pagina = max(1, pagina)
    with sessao() as s:
        total = s.exec(select(func.count()).select_from(Auditoria)).one()
        itens = s.exec(
            select(Auditoria).order_by(Auditoria.id.desc()).offset((pagina - 1) * por_pagina).limit(por_pagina)
        ).all()
    return {
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "itens": [
            {
                "quando": a.quando.isoformat(),
                "usuario": a.usuario_email,
                "acao": a.acao,
                "detalhe": a.detalhe,
                "ip": a.ip,
            }
            for a in itens
        ],
    }


def suspenso() -> bool:
    return os.environ.get("EMISSOR_SUSPENSO", "").strip().lower() in ("1", "true", "sim", "yes")


def url_assinatura() -> str | None:
    return os.environ.get("EMISSOR_URL_ASSINATURA", "").strip() or None
