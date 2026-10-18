"""Autenticação: primeiro acesso, login, bloqueio, 2FA, papéis, convites, suspensão e auditoria."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from emissor import admin_cli, auth, security
from emissor.api.app import create_app
from emissor.db import SessaoUsuario, Usuario, sessao

from .conftest import ADMIN_EMAIL, ADMIN_SENHA, entrar_como_admin


@pytest.fixture
def client():
    with TestClient(create_app(iniciar_worker=False)) as c:
        yield c


@pytest.fixture
def admin(client):
    entrar_como_admin(client)
    return client


def _novo_cliente():
    return TestClient(create_app(iniciar_worker=False))


def _convidar(admin, email, papel="operador"):
    r = admin.post("/api/usuarios", json={"email": email, "nome": "Fulana", "papel": papel})
    assert r.status_code == 200, r.text
    return r.json()["link"].split("token=")[1]


def test_rotas_exigem_login(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/tabela").status_code == 401
    assert client.get("/api/openapi.json").status_code == 401
    assert client.get("/docs").status_code == 401
    r = client.get("/api/saude")
    assert r.status_code == 200 and r.json()["administrador_criado"] is False
    s = client.get("/api/auth/sessao").json()
    assert s["autenticado"] is False and s["primeiro_acesso"] is True
    assert s["primeiro_acesso_sem_codigo"] is False  # TestClient não vem de 127.0.0.1


def test_primeiro_acesso(client):
    corpo = {"nome": "A", "email": ADMIN_EMAIL, "senha": ADMIN_SENHA}
    assert client.post("/api/auth/primeiro-acesso", json=corpo | {"token": "errado"}).status_code == 403
    assert client.post("/api/auth/primeiro-acesso", json=corpo | {"token": None}).status_code == 403
    fraca = client.post(
        "/api/auth/primeiro-acesso", json=corpo | {"senha": "curta", "token": auth.token_primeiro_acesso()}
    )
    assert fraca.status_code == 400 and "10 caracteres" in fraca.json()["mensagem"]
    entrar_como_admin(client)
    assert not (auth.config.DATA_DIR / auth.ARQUIVO_PRIMEIRO_ACESSO).exists()
    assert client.get("/api/status").status_code == 200
    assert client.get("/docs").status_code == 200
    # Só funciona uma vez.
    r = _novo_cliente().post("/api/auth/primeiro-acesso", json=corpo | {"token": "x"})
    assert r.status_code == 409


def test_primeiro_acesso_pelo_proprio_servidor_dispensa_codigo():
    local = {"client": ("127.0.0.1", 5000), "base_url": "http://127.0.0.1:8000"}
    # Página de outro site com DNS apontado para 127.0.0.1 (DNS rebinding): o Host não é local.
    with TestClient(
        create_app(iniciar_worker=False), client=("127.0.0.1", 5000), base_url="http://mal.example:8000"
    ) as c:
        assert c.get("/api/auth/sessao").json()["primeiro_acesso_sem_codigo"] is False
        r = c.post("/api/auth/primeiro-acesso", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA})
        assert r.status_code == 403, r.text
    with TestClient(create_app(iniciar_worker=False), **local) as c:
        assert c.get("/api/auth/sessao").json()["primeiro_acesso_sem_codigo"] is True
        r = c.post("/api/auth/primeiro-acesso", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA})
        assert r.status_code == 200, r.text
    # Atrás de um proxy na mesma máquina, não.
    with TestClient(create_app(iniciar_worker=False), **local) as c:
        h = {"X-Forwarded-For": "203.0.113.9"}
        assert c.get("/api/auth/sessao", headers=h).json()["primeiro_acesso_sem_codigo"] is False


def test_convite_inicial_pela_variavel(client, monkeypatch):
    monkeypatch.setenv("EMISSOR_CONVITE_INICIAL", "token-da-hospedagem")
    corpo = {"email": ADMIN_EMAIL, "senha": ADMIN_SENHA}
    assert client.post("/api/auth/primeiro-acesso", json=corpo | {"token": "outro"}).status_code == 403
    assert client.post("/api/auth/primeiro-acesso", json=corpo | {"token": "token-da-hospedagem"}).status_code == 200
    assert client.get("/api/saude").json()["administrador_criado"] is True


def test_login_logout_e_bloqueio(admin):
    c = _novo_cliente()
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": "errada-errada"})
    assert r.status_code == 401 and r.json()["mensagem"] == "E-mail ou senha incorretos."
    r = c.post("/api/auth/entrar", json={"email": "ninguem@x.com", "senha": "qualquer-coisa"})
    assert r.status_code == 401 and r.json()["mensagem"] == "E-mail ou senha incorretos."
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL.upper(), "senha": ADMIN_SENHA})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/status").status_code == 200
    c.post("/api/auth/sair")
    assert c.get("/api/status").status_code == 401

    for _ in range(auth.MAX_FALHAS):
        c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": "errada-errada"})
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA})
    assert r.status_code == 429 and "bloqueado" in r.json()["mensagem"]


def test_limite_por_ip(admin):
    c = _novo_cliente()
    for i in range(auth.MAX_FALHAS_IP):
        c.post("/api/auth/entrar", json={"email": f"x{i}@x.com", "senha": "errada-errada"})
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA})
    assert r.status_code == 429


def test_sessao_expira_por_inatividade(admin):
    with sessao() as s:
        ses = s.exec(select(SessaoUsuario)).one()
        ses.ultimo_uso -= auth.INATIVIDADE + timedelta(minutes=1)
        s.add(ses)
        s.commit()
    assert admin.get("/api/status").status_code == 401


def test_totp_rfc6238():
    segredo = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert auth.totp_agora(segredo, 59) == "287082"
    assert auth.totp_agora(segredo, 1111111109) == "081804"
    assert auth.conferir_totp(segredo, "287 082", 0, 59) == 1
    assert auth.conferir_totp(segredo, "287082", 1, 59) is None  # já usado


def test_dois_fatores(admin):
    assert admin.post("/api/auth/2fa/iniciar", json={"senha": "errada"}).status_code == 400
    r = admin.post("/api/auth/2fa/iniciar", json={"senha": ADMIN_SENHA})
    assert r.status_code == 200
    dados = r.json()
    assert dados["uri"].startswith("otpauth://totp/") and dados["qr"].startswith("data:image/svg+xml;base64,")
    assert admin.post("/api/auth/2fa/ativar", json={"codigo": "000000"}).status_code == 400
    r = admin.post("/api/auth/2fa/ativar", json={"codigo": auth.totp_agora(dados["segredo"])})
    assert r.status_code == 200
    codigos = r.json()["codigos_recuperacao"]
    assert len(codigos) == 10
    assert admin.get("/api/status").status_code == 200  # a própria sessão continua

    c = _novo_cliente()
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA})
    assert r.status_code == 401 and r.json()["precisa_codigo"] is True
    # O código já usado na ativação não vale de novo.
    r = c.post(
        "/api/auth/entrar",
        json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA, "codigo": auth.totp_agora(dados["segredo"])},
    )
    assert r.status_code == 401
    r = c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA, "codigo": codigos[0].upper()})
    assert r.status_code == 200, r.text
    c2 = _novo_cliente()
    r = c2.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA, "codigo": codigos[0]})
    assert r.status_code == 401  # código de recuperação é de uso único
    assert c.get("/api/auth/conta").json()["codigos_recuperacao_restantes"] == 9

    # O segredo fica criptografado no banco.
    from emissor.db import Usuario

    with sessao() as s:
        u = s.exec(select(Usuario)).one()
        assert dados["segredo"].encode() not in u.totp_segredo_enc
        assert security.decrypt(u.totp_segredo_enc).decode() == dados["segredo"]


def test_papeis_e_convite(admin):
    token = _convidar(admin, "operador@exemplo.com.br")
    usuarios = admin.get("/api/usuarios").json()
    assert {u["email"]: u["convite_pendente"] for u in usuarios} == {
        ADMIN_EMAIL: False,
        "operador@exemplo.com.br": True,
    }
    assert admin.post("/api/usuarios", json={"email": "operador@exemplo.com.br"}).status_code == 409

    op = _novo_cliente()
    assert op.post("/api/auth/convite/ver", json={"token": token}).json()["email"] == "operador@exemplo.com.br"
    r = op.post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"})
    assert r.status_code == 200 and r.json()["usuario"]["papel"] == "operador"
    assert op.post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"}).status_code == 410

    assert op.get("/api/status").status_code == 200
    assert op.get("/api/tabela").status_code == 200
    assert op.put("/api/config", json={"serie": 2}).status_code == 403
    assert op.get("/api/diagnostico").status_code == 403
    assert op.get("/api/usuarios").status_code == 403
    assert op.get("/api/auditoria").status_code == 403
    # Tributações: só o admin altera; a alíquota do ISS retido (aviso da emissão) o operador também pode.
    assert op.post("/api/tributacoes", json={"nome": "X", "dados": {}}).status_code == 403
    r = op.post("/api/tributacoes/aliquota-iss", json={"p_aliq_iss": "2"})
    assert r.status_code != 403 and r.status_code < 500


def test_ultimo_admin_e_desativacao(admin):
    eu = admin.get("/api/auth/sessao").json()["usuario"]["id"]
    r = admin.put(f"/api/usuarios/{eu}", json={"papel": "operador"})
    assert r.status_code == 400 and "único administrador" in r.json()["mensagem"]
    token = _convidar(admin, "op@exemplo.com.br")
    op = _novo_cliente()
    op.post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"})
    op_id = next(u["id"] for u in admin.get("/api/usuarios").json() if u["email"] == "op@exemplo.com.br")
    assert admin.put(f"/api/usuarios/{op_id}", json={"ativo": False}).status_code == 200
    assert op.get("/api/status").status_code == 401  # sessão derrubada
    r = op.post("/api/auth/entrar", json={"email": "op@exemplo.com.br", "senha": "operador-senha-1"})
    assert r.status_code == 401


def test_redefinir_senha_pelo_admin_e_cli(admin, capsys):
    token = _convidar(admin, "op@exemplo.com.br")
    _novo_cliente().post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"})
    op_id = next(u["id"] for u in admin.get("/api/usuarios").json() if u["email"] == "op@exemplo.com.br")
    r = admin.post(f"/api/usuarios/{op_id}/redefinir", json={"remover_2fa": True})
    novo = r.json()["link"].split("token=")[1]
    c = _novo_cliente()
    assert (
        c.post("/api/auth/entrar", json={"email": "op@exemplo.com.br", "senha": "operador-senha-1"}).status_code == 401
    )
    assert c.post("/api/auth/convite", json={"token": novo, "senha": "outra-senha-boa"}).status_code == 200

    assert admin_cli.main(["redefinir", ADMIN_EMAIL]) == 0
    saida = capsys.readouterr().out
    assert "/#/convite?token=" in saida
    assert admin.get("/api/status").status_code == 401  # sessões do admin derrubadas
    assert admin_cli.main(["redefinir", "nao-existe@x.com"]) == 1


def test_trocar_senha(admin):
    r = admin.post("/api/auth/senha", json={"senha_atual": "errada", "nova_senha": "nova-senha-123"})
    assert r.status_code == 400
    r = admin.post("/api/auth/senha", json={"senha_atual": ADMIN_SENHA, "nova_senha": "nova-senha-123"})
    assert r.status_code == 200
    assert admin.get("/api/status").status_code == 200
    c = _novo_cliente()
    assert c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": "nova-senha-123"}).status_code == 200


def test_origem_diferente_recusada(admin):
    r = admin.put("/api/tabela", json={"linhas": []}, headers={"Origin": "https://outro.emissoremplanilha.com"})
    assert r.status_code == 403
    r = admin.put("/api/tabela", json={"linhas": []}, headers={"Origin": "http://testserver"})
    assert r.status_code == 200, r.text


def test_suspensao(admin, monkeypatch):
    monkeypatch.setenv("EMISSOR_SUSPENSO", "1")
    monkeypatch.setenv("EMISSOR_URL_ASSINATURA", "https://billing.exemplo/portal")
    s = admin.get("/api/auth/sessao").json()
    assert s["suspenso"] is True and s["url_assinatura"] == "https://billing.exemplo/portal"
    assert admin.get("/api/tabela").status_code == 200
    r = admin.put("/api/tabela", json={"linhas": []})
    assert r.status_code == 403 and r.json()["suspenso"] is True
    assert admin.post("/api/lotes", json={"linha_ids": [1]}).status_code == 403
    assert admin.post("/api/emissoes/zip", json={"ids": [], "tipo": "xml"}).status_code != 403
    # Login continua funcionando (para baixar as notas).
    c = _novo_cliente()
    assert c.post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA}).status_code == 200


def test_auditoria(admin):
    _novo_cliente().post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": "errada-errada"})
    _convidar(admin, "op@exemplo.com.br")
    admin.put("/api/config", json={"serie": 2})
    acoes = [i["acao"] for i in admin.get("/api/auditoria").json()["itens"]]
    assert acoes[:4] == ["configuracao_alterada", "usuario_convidado", "login_falhou", "primeiro_acesso"]


def test_chave_pela_variavel(monkeypatch):
    from cryptography.fernet import Fernet

    chave = Fernet.generate_key().decode()
    monkeypatch.setenv("EMISSOR_SECRET_KEY", chave)
    security.reset_cache()
    token = security.encrypt(b"segredo")
    assert Fernet(chave.encode()).decrypt(token) == b"segredo"
    assert not auth.config.SECRET_KEY_FILE.exists()
    monkeypatch.setenv("EMISSOR_SECRET_KEY", "invalida")
    security.reset_cache()
    with pytest.raises(RuntimeError):
        security.encrypt(b"x")
    security.reset_cache()


def test_senha_guardada_com_argon2id(admin):
    from emissor.db import Usuario

    with sessao() as s:
        u = s.exec(select(Usuario)).one()
    assert u.senha_hash.startswith("$argon2id$") and ADMIN_SENHA not in u.senha_hash


def test_redefinicao_que_mantem_2fa_pede_o_codigo(admin):
    token = _convidar(admin, "op@exemplo.com.br")
    op = _novo_cliente()
    op.post("/api/auth/convite", json={"token": token, "senha": "operador-senha-1"})
    segredo = op.post("/api/auth/2fa/iniciar", json={"senha": "operador-senha-1"}).json()["segredo"]
    assert op.post("/api/auth/2fa/ativar", json={"codigo": auth.totp_agora(segredo)}).status_code == 200
    op_id = next(u["id"] for u in admin.get("/api/usuarios").json() if u["email"] == "op@exemplo.com.br")
    novo = admin.post(f"/api/usuarios/{op_id}/redefinir", json={"remover_2fa": False}).json()["link"].split("token=")[1]
    c = _novo_cliente()
    assert c.post("/api/auth/convite/ver", json={"token": novo}).json()["pede_codigo"] is True
    r = c.post("/api/auth/convite", json={"token": novo, "senha": "outra-senha-boa"})
    assert r.status_code == 401 and r.json()["precisa_codigo"] is True
    assert c.get("/api/status").status_code == 401  # o link sozinho não abriu sessão
    r = c.post("/api/auth/convite", json={"token": novo, "senha": "outra-senha-boa", "codigo": "000000"})
    assert r.status_code == 401
    # O código da ativação já foi usado neste período de 30 s: libera para o teste.
    with sessao() as s:
        u = s.exec(select(Usuario).where(Usuario.email == "op@exemplo.com.br")).one()
        u.totp_ultimo_passo = 0
        s.add(u)
        s.commit()
    r = c.post(
        "/api/auth/convite", json={"token": novo, "senha": "outra-senha-boa", "codigo": auth.totp_agora(segredo)}
    )
    assert r.status_code == 200, r.text


# --- recuperação de senha por e-mail ------------------------------------------------------------


@pytest.fixture
def correio_falso(monkeypatch):
    """Liga o envio pelo painel e guarda os e-mails em vez de enviá-los."""
    import time as _time

    from emissor import correio

    monkeypatch.setenv("EMISSOR_URL_PUBLICA", "https://empresa.exemplo.com.br")
    monkeypatch.setenv("EMISSOR_EMAIL_URL", "https://painel.exemplo.com.br/api/email")
    monkeypatch.setenv("EMISSOR_EMAIL_TOKEN", "t" * 64)
    enviados: list[dict] = []
    monkeypatch.setattr(
        correio,
        "enviar_recuperacao",
        lambda para, nome, link, minutos: enviados.append({"para": para, "link": link, "minutos": minutos}),
    )

    def esperar(n: int) -> list[dict]:
        for _ in range(100):
            if len(enviados) >= n:
                break
            _time.sleep(0.02)
        return enviados

    return esperar


def test_recuperacao_desligada_sem_email_configurado(client):
    assert client.get("/api/auth/sessao").json()["recuperacao_email"] is False
    r = client.post("/api/auth/esqueci", json={"email": ADMIN_EMAIL})
    assert r.status_code == 404 and "administrador" in r.json()["mensagem"]


def test_recuperacao_por_email(admin, correio_falso):
    assert admin.get("/api/auth/sessao").json()["recuperacao_email"] is True
    c = _novo_cliente()
    # Mesma resposta para e-mail que existe e que não existe.
    r1 = c.post("/api/auth/esqueci", json={"email": "ninguem@exemplo.com.br"})
    r2 = c.post("/api/auth/esqueci", json={"email": ADMIN_EMAIL.upper()})
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    enviados = correio_falso(1)
    assert len(enviados) == 1 and enviados[0]["para"] == ADMIN_EMAIL and enviados[0]["minutos"] == 60
    assert enviados[0]["link"].startswith("https://empresa.exemplo.com.br/#/convite?token=")
    token = enviados[0]["link"].split("token=")[1]

    # Pedir a recuperação não tranca o dono fora: a senha atual continua valendo.
    assert (
        _novo_cliente().post("/api/auth/entrar", json={"email": ADMIN_EMAIL, "senha": ADMIN_SENHA}).status_code == 200
    )
    # Segundo pedido logo em seguida não manda outro e-mail.
    c.post("/api/auth/esqueci", json={"email": ADMIN_EMAIL})
    assert len(correio_falso(2)) == 1

    assert c.post("/api/auth/convite/ver", json={"token": token}).json()["finalidade"] == "recuperacao"
    r = c.post("/api/auth/convite", json={"token": token, "senha": "senha-recuperada-1"})
    assert r.status_code == 200, r.text
    assert admin.get("/api/status").status_code == 401  # as outras sessões caíram
    assert c.post("/api/auth/convite", json={"token": token, "senha": "outra-senha-boa"}).status_code == 410
    acoes = [a["acao"] for a in auth.listar_auditoria()["itens"]]
    assert "recuperacao_solicitada" in acoes and "senha_recuperada" in acoes and "recuperacao_limitada" in acoes


def test_recuperacao_expira_e_cai_ao_trocar_a_senha(admin, correio_falso):
    from emissor.db import ConviteUsuario

    _novo_cliente().post("/api/auth/esqueci", json={"email": ADMIN_EMAIL})
    token = correio_falso(1)[0]["link"].split("token=")[1]
    assert (
        admin.post("/api/auth/senha", json={"senha_atual": ADMIN_SENHA, "nova_senha": "nova-senha-boa"}).status_code
        == 200
    )
    assert _novo_cliente().post("/api/auth/convite/ver", json={"token": token}).status_code == 410
    with sessao() as s:
        for conv in s.exec(select(ConviteUsuario)):
            assert conv.expira_em - conv.criado_em <= timedelta(hours=1, seconds=5)


def test_recuperacao_ignora_usuario_desativado_e_limita_por_ip(admin, correio_falso):
    _convidar(admin, "op@exemplo.com.br")  # convite pendente: sem senha, não recebe recuperação
    c = _novo_cliente()
    for _ in range(auth.MAX_RECUPERACOES_IP):
        assert c.post("/api/auth/esqueci", json={"email": "op@exemplo.com.br"}).status_code == 200
    assert c.post("/api/auth/esqueci", json={"email": ADMIN_EMAIL}).status_code == 429
    assert correio_falso(1) == []


def test_recuperacao_com_2fa_pede_o_codigo(admin, correio_falso):
    segredo = admin.post("/api/auth/2fa/iniciar", json={"senha": ADMIN_SENHA}).json()["segredo"]
    assert admin.post("/api/auth/2fa/ativar", json={"codigo": auth.totp_agora(segredo)}).status_code == 200
    _novo_cliente().post("/api/auth/esqueci", json={"email": ADMIN_EMAIL})
    token = correio_falso(1)[0]["link"].split("token=")[1]
    r = _novo_cliente().post("/api/auth/convite", json={"token": token, "senha": "senha-recuperada-1"})
    assert r.status_code == 401 and r.json()["precisa_codigo"] is True


def test_correio_pelo_painel_e_por_smtp(monkeypatch):
    from emissor import correio

    monkeypatch.setenv("EMISSOR_URL_PUBLICA", "https://empresa.exemplo.com.br")
    assert correio.configurado() is False
    chamadas = []

    class Resposta:
        status_code = 200
        text = "{}"

    monkeypatch.setattr(correio.httpx, "post", lambda url, **kw: chamadas.append((url, kw)) or Resposta())
    monkeypatch.setenv("EMISSOR_EMAIL_URL", "https://painel.exemplo.com.br/api/email")
    monkeypatch.setenv("EMISSOR_EMAIL_TOKEN", "segredo")
    assert correio.configurado() is True
    correio.enviar_recuperacao("a@b.com", "A", "https://empresa.exemplo.com.br/#/convite?token=x", 60)
    url, kw = chamadas[0]
    assert url.endswith("/api/email") and kw["headers"]["Authorization"] == "Bearer segredo"
    assert kw["json"]["tipo"] == "recuperacao_senha" and kw["json"]["para"] == "a@b.com"
    Resposta.status_code = 429
    with pytest.raises(correio.CorreioError):
        correio.enviar_recuperacao("a@b.com", "A", "https://empresa.exemplo.com.br/#/convite?token=x", 60)

    monkeypatch.delenv("EMISSOR_EMAIL_URL")
    monkeypatch.setenv("EMISSOR_SMTP_HOST", "smtp.exemplo.com.br")
    monkeypatch.setenv("EMISSOR_SMTP_REMETENTE", "nao-responda@exemplo.com.br")
    monkeypatch.setenv("EMISSOR_SMTP_USUARIO", "u")
    monkeypatch.setenv("EMISSOR_SMTP_SENHA", "p")
    eventos = []

    class SmtpFalso:
        def __init__(self, host, porta, timeout):
            eventos.append(("conectar", host, porta))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            eventos.append(("starttls",))

        def login(self, u, p):
            eventos.append(("login", u))

        def send_message(self, msg):
            eventos.append(("enviar", msg["To"], msg["Subject"], msg.get_body(("plain",)).get_content()))

    monkeypatch.setattr(correio.smtplib, "SMTP", SmtpFalso)
    assert correio.configurado() is True
    correio.enviar_recuperacao("a@b.com", "Ana", "https://empresa.exemplo.com.br/#/convite?token=x", 60)
    assert eventos[:3] == [("conectar", "smtp.exemplo.com.br", 587), ("starttls",), ("login", "u")]
    assert eventos[3][1] == "a@b.com" and "#/convite?token=x" in eventos[3][3] and "Olá, Ana." in eventos[3][3]
