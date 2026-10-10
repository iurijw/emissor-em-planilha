"""``emissor-admin``: recuperação de acesso pela linha de comando do servidor.

Serve para quando ninguém consegue entrar (senha esquecida do único administrador, celular do 2FA
perdido). Quem roda o comando já tem acesso ao servidor e a ``data/``, então não pede senha.

    uv run emissor-admin usuarios
    uv run emissor-admin redefinir admin@empresa.com.br [--remover-2fa]
    uv run emissor-admin convidar novo@empresa.com.br [--nome "Fulana"] [--papel admin|operador]
    uv run emissor-admin primeiro-acesso

No Docker: ``docker compose exec emissor emissor-admin ...``. Os links valem por 7 dias e são de uso
único; defina ``EMISSOR_URL_PUBLICA`` (ex.: https://empresa.emissoremplanilha.com) para o link sair
completo.
"""

from __future__ import annotations

import argparse
import sys

from emissor import auth


def _link(token: str) -> str:
    link = auth.link_convite(token)
    return link if link.startswith("http") else f"http://<endereço-do-sistema>{link}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="emissor-admin", description="Recuperação de acesso ao Emissor em Planilha.")
    sub = p.add_subparsers(dest="comando", required=True)
    sub.add_parser("usuarios", help="lista os usuários")
    r = sub.add_parser("redefinir", help="gera link para definir nova senha")
    r.add_argument("email")
    r.add_argument("--remover-2fa", action="store_true", help="também desliga a verificação em duas etapas")
    c = sub.add_parser("convidar", help="cria um usuário e gera o link de convite")
    c.add_argument("email")
    c.add_argument("--nome", default="")
    c.add_argument("--papel", default="admin", choices=auth.PAPEIS)
    sub.add_parser("primeiro-acesso", help="mostra o código para criar o primeiro administrador")
    args = p.parse_args(argv)

    try:
        if args.comando == "usuarios":
            for u in auth.listar_usuarios():
                situacao = "ativo" if u["ativo"] else "desativado"
                if u["convite_pendente"]:
                    situacao += ", convite pendente"
                if u["bloqueado"]:
                    situacao += ", bloqueado"
                print(f"{u['email']:<40} {u['papel']:<9} {situacao}{', 2FA' if u['totp_ativo'] else ''}")
        elif args.comando == "redefinir":
            u, token = auth.redefinir_acesso(None, email=args.email, remover_2fa=args.remover_2fa)
            print(f"Senha de {u['email']} redefinida. Link de uso único (válido por 7 dias):\n{_link(token)}")
        elif args.comando == "convidar":
            u, token = auth.convidar(None, args.email, args.nome, args.papel)
            print(f"Usuário {u['email']} ({u['papel']}) criado. Link de convite (válido por 7 dias):\n{_link(token)}")
        elif args.comando == "primeiro-acesso":
            if auth.existe_usuario():
                print("Já existem usuários. Use 'redefinir' ou 'convidar'.")
                return 1
            print(f"Código de primeiro acesso: {auth.token_primeiro_acesso()}")
    except auth.AuthError as err:
        print(f"Erro: {err.mensagem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
