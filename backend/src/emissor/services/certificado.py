"""Certificado A1 guardado no servidor (criptografado em repouso)."""

from __future__ import annotations

import logging
import threading

from emissor import security
from emissor.db import CertificadoDB, agora, obter_certificado, para_local, sessao
from emissor.nfse.certificate import Certificado, CertificadoError, CertificadoInfo

log = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: tuple[str, Certificado] | None = None  # (marca de versão, certificado)


def salvar(pfx: bytes, senha: str) -> CertificadoInfo:
    cert = Certificado(pfx, senha)  # valida senha/arquivo
    info = cert.info
    if info.vencido:
        raise CertificadoError(f"Certificado vencido em {info.valido_ate:%d/%m/%Y}.")
    with sessao() as s:
        reg = obter_certificado(s) or CertificadoDB(
            id=1, pfx_enc=b"", senha_enc=b"", titular="", emissor="", valido_de=agora(), valido_ate=agora()
        )
        reg.pfx_enc = security.encrypt(pfx)
        reg.senha_enc = security.encrypt(senha.encode("utf-8"))
        reg.titular = info.titular
        reg.cnpj = info.cnpj
        reg.emissor = info.emissor
        reg.valido_de = para_local(info.valido_de)
        reg.valido_ate = para_local(info.valido_ate)
        reg.carregado_em = agora()
        s.add(reg)
        s.commit()
    _limpar_cache()
    log.info(
        "certificado carregado",
        extra={"titular": info.titular, "cnpj": info.cnpj, "valido_ate": info.valido_ate.isoformat()},
    )
    return info


def carregar() -> Certificado | None:
    """Certificado decifrado (em memória, com cache até ser trocado)."""
    global _cache
    with sessao() as s:
        reg = obter_certificado(s)
    if reg is None:
        return None
    marca = reg.carregado_em.isoformat()
    with _cache_lock:
        if _cache and _cache[0] == marca:
            return _cache[1]
        cert = Certificado(security.decrypt(reg.pfx_enc), security.decrypt(reg.senha_enc).decode("utf-8"))
        _cache = (marca, cert)
        return cert


def info() -> CertificadoInfo | None:
    cert = carregar()
    return cert.info if cert else None


def _limpar_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None
