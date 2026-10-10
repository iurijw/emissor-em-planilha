"""Criptografia em repouso (Fernet) para o certificado e sua senha.

A chave vem de ``EMISSOR_SECRET_KEY`` (chave Fernet em base64) quando definida — é o caso da
hospedagem, em que ela fica nos segredos do provedor e um backup do volume sozinho não abre o
certificado. Sem a variável, fica em ``data/secret.key`` (gerada na primeira execução, fora do git):
quem tiver acesso a ``data/`` inteiro consegue decifrar; o objetivo é que o PFX e a senha nunca
fiquem em texto puro no banco, em backups parciais ou em logs.

Para levar uma instalação existente para a variável, copie o conteúdo de ``data/secret.key``
para ``EMISSOR_SECRET_KEY`` (e só então apague o arquivo).
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet

from emissor import config


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    chave_env = os.environ.get("EMISSOR_SECRET_KEY", "").strip()
    if chave_env:
        try:
            return Fernet(chave_env.encode())
        except ValueError as err:
            raise RuntimeError(
                "EMISSOR_SECRET_KEY inválida: deve ser uma chave Fernet (32 bytes em base64 urlsafe)."
            ) from err
    config.ensure_dirs()
    path = config.SECRET_KEY_FILE
    if not path.exists():
        key = Fernet.generate_key()
        # Cria já com permissão restrita onde o SO suportar.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    return Fernet(path.read_bytes().strip())


def encrypt(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt(token: bytes) -> bytes:
    return _fernet().decrypt(token)


def reset_cache() -> None:
    """Para testes que trocam ``EMISSOR_DATA_DIR``."""
    _fernet.cache_clear()
