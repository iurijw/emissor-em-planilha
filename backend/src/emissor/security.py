"""Criptografia em repouso (Fernet) para o certificado e sua senha.

A chave fica em ``data/secret.key`` (gerada na primeira execução, fora do git).
Quem tiver acesso a ``data/`` inteiro consegue decifrar; o objetivo é que o PFX e a
senha nunca fiquem em texto puro no banco, em backups parciais ou em logs.
"""

from __future__ import annotations

import os
from functools import lru_cache

from cryptography.fernet import Fernet

from emissor import config


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
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
