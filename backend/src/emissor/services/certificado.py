"""Certificados A1 guardados no servidor (criptografados em repouso).

Cada empresa usa um certificado (``configuracao.certificado_id``): o A1 dela ou o de um procurador com procuração
eletrônica, que pode servir a várias empresas. Os PFX e as senhas ficam cifrados com a mesma chave Fernet da
instalação; o PFX nunca sai pela API.
"""

from __future__ import annotations

import logging
import threading

from sqlmodel import Session, select

from emissor import security
from emissor.db import CertificadoDB, Configuracao, agora, obter_certificado, obter_config, para_local, sessao
from emissor.nfse.certificate import Certificado, CertificadoError, CertificadoInfo

log = logging.getLogger(__name__)

_cache_lock = threading.Lock()
_cache: dict[int, tuple[str, Certificado]] = {}  # id → (marca de versão, certificado decifrado)


def _abrir(pfx: bytes, senha: str) -> Certificado:
    cert = Certificado(pfx, senha)  # valida senha/arquivo
    if cert.info.vencido:
        raise CertificadoError(f"Certificado vencido em {cert.info.valido_ate:%d/%m/%Y}.")
    return cert


def _gravar(reg: CertificadoDB, pfx: bytes, senha: str, info: CertificadoInfo) -> None:
    reg.pfx_enc = security.encrypt(pfx)
    reg.senha_enc = security.encrypt(senha.encode("utf-8"))
    reg.titular = info.titular
    reg.cnpj = info.cnpj
    reg.cpf = info.cpf
    reg.emissor = info.emissor
    reg.valido_de = para_local(info.valido_de)
    reg.valido_ate = para_local(info.valido_ate)
    reg.carregado_em = agora()


def _novo_registro() -> CertificadoDB:
    return CertificadoDB(pfx_enc=b"", senha_enc=b"", titular="", emissor="", valido_de=agora(), valido_ate=agora())


def usuarias(s: Session, certificado_id: int) -> list[Configuracao]:
    return list(
        s.exec(
            select(Configuracao).where(Configuracao.certificado_id == certificado_id, Configuracao.ativa.is_not(False))
        )
    )


def salvar(pfx: bytes, senha: str, empresa_id: int | None = None) -> CertificadoInfo:
    """Troca o certificado da empresa (do contexto, se não informada).

    Se o certificado atual só serve a esta empresa, é substituído no mesmo registro; se é de um procurador usado por
    outras empresas também, a empresa passa a ter um registro próprio (as outras continuam com o do procurador).
    """
    cert = _abrir(pfx, senha)
    info = cert.info
    with sessao() as s:
        cfg = obter_config(s, empresa_id)
        reg = s.get(CertificadoDB, cfg.certificado_id) if cfg.certificado_id else None
        if reg is None or any(c.id != cfg.id for c in usuarias(s, reg.id)):
            reg = _novo_registro()
        _gravar(reg, pfx, senha, info)
        s.add(reg)
        s.flush()
        cfg.certificado_id = reg.id
        fiscal = cfg.config_fiscal()
        if fiscal and info.cnpj and fiscal.cnpj == info.cnpj:
            cfg.acesso = "certificado"
        s.add(cfg)
        s.commit()
    _limpar_cache()
    log.info(
        "certificado carregado",
        extra={"titular": info.titular, "cnpj": info.cnpj, "valido_ate": info.valido_ate.isoformat()},
    )
    return info


def guardar_novo(pfx: bytes, senha: str) -> tuple[int, CertificadoInfo]:
    """Guarda um certificado ainda sem empresa (para criar uma empresa com ele, própria ou por procuração)."""
    cert = _abrir(pfx, senha)
    with sessao() as s:
        reg = _novo_registro()
        _gravar(reg, pfx, senha, cert.info)
        s.add(reg)
        s.commit()
        s.refresh(reg)
    log.info("certificado guardado", extra={"certificado_id": reg.id, "titular": cert.info.titular})
    return reg.id, cert.info


def carregar_por_id(certificado_id: int) -> Certificado | None:
    with sessao() as s:
        reg = s.get(CertificadoDB, certificado_id)
    return _decifrar(reg) if reg else None


def _decifrar(reg: CertificadoDB) -> Certificado:
    marca = reg.carregado_em.isoformat()
    with _cache_lock:
        atual = _cache.get(reg.id)
        if atual and atual[0] == marca:
            return atual[1]
        cert = Certificado(security.decrypt(reg.pfx_enc), security.decrypt(reg.senha_enc).decode("utf-8"))
        _cache[reg.id] = (marca, cert)
        return cert


def carregar(empresa_id: int | None = None) -> Certificado | None:
    """Certificado decifrado da empresa (em memória, com cache até ser trocado)."""
    with sessao() as s:
        reg = obter_certificado(s, empresa_id)
    return _decifrar(reg) if reg else None


def info(empresa_id: int | None = None) -> CertificadoInfo | None:
    cert = carregar(empresa_id)
    return cert.info if cert else None


def listar() -> list[dict]:
    """Metadados de todos os certificados guardados e das empresas que usam cada um (nunca o PFX)."""
    with sessao() as s:
        regs = list(s.exec(select(CertificadoDB).order_by(CertificadoDB.id)))
        saida = []
        for r in regs:
            saida.append(
                {
                    "id": r.id,
                    "titular": r.titular,
                    "cnpj": r.cnpj,
                    "cpf": r.cpf,
                    "emissor": r.emissor,
                    "valido_ate": r.valido_ate.isoformat(),
                    "vencido": r.valido_ate < agora(),
                    "empresas": [c.id for c in usuarias(s, r.id)],
                }
            )
    return saida


def apagar_sem_uso(certificado_id: int | None = None, *, minutos: int | None = None) -> int:
    """Apaga certificados que nenhuma empresa usa (um, ou todos os guardados há mais de ``minutos``)."""
    from datetime import timedelta

    with sessao() as s:
        if certificado_id is not None:
            regs = [r for r in [s.get(CertificadoDB, certificado_id)] if r]
        else:
            regs = list(s.exec(select(CertificadoDB)))
        usados = {c.certificado_id for c in s.exec(select(Configuracao)) if c.certificado_id}
        n = 0
        for r in regs:
            if r.id in usados:
                continue
            if minutos is not None and r.carregado_em > agora() - timedelta(minutes=minutos):
                continue
            s.delete(r)
            n += 1
        s.commit()
    if n:
        _limpar_cache()
        log.info("certificados sem uso apagados", extra={"qtd": n})
    return n


def _limpar_cache() -> None:
    with _cache_lock:
        _cache.clear()
