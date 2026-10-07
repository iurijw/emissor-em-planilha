"""Certificado digital A1 (PFX/PKCS#12) ICP-Brasil.

Carrega o PFX, extrai metadados (titular, CNPJ, validade) e produz o ``SSLContext``
usado no mTLS com a Sefin Nacional.
"""

from __future__ import annotations

import os
import re
import secrets
import ssl
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime

import certifi
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

# OIDs ICP-Brasil no SubjectAltName (otherName)
OID_CNPJ = "2.16.76.1.3.3"
OID_CPF_TITULAR_PF = "2.16.76.1.3.1"  # data nasc. (8) + CPF (11) + ...


class CertificadoError(Exception):
    """Erro com mensagem pronta para o usuário final."""


@dataclass(frozen=True)
class CertificadoInfo:
    titular: str
    cnpj: str | None
    cpf: str | None
    emissor: str
    serial: str
    valido_de: datetime
    valido_ate: datetime

    @property
    def dias_para_vencer(self) -> int:
        return (self.valido_ate - datetime.now(UTC)).days

    @property
    def vencido(self) -> bool:
        return datetime.now(UTC) > self.valido_ate

    def as_dict(self) -> dict:
        return {
            "titular": self.titular,
            "cnpj": self.cnpj,
            "cpf": self.cpf,
            "emissor": self.emissor,
            "serial": self.serial,
            "valido_de": self.valido_de.isoformat(),
            "valido_ate": self.valido_ate.isoformat(),
            "dias_para_vencer": self.dias_para_vencer,
            "vencido": self.vencido,
        }


class Certificado:
    def __init__(self, pfx: bytes, senha: str):
        try:
            key, cert, extra = pkcs12.load_key_and_certificates(pfx, senha.encode("utf-8") if senha else None)
        except ValueError as exc:
            msg = str(exc).lower()
            if "password" in msg or "mac" in msg or "decrypt" in msg:
                raise CertificadoError("Senha do certificado incorreta.") from exc
            raise CertificadoError(f"Arquivo não é um certificado PFX/P12 válido ({exc}).") from exc
        if key is None or cert is None:
            raise CertificadoError("O arquivo PFX não contém a chave privada e o certificado.")
        if not isinstance(key, RSAPrivateKey):
            raise CertificadoError("A chave do certificado não é RSA; a NFS-e exige RSA.")
        self.key: RSAPrivateKey = key
        self.cert: x509.Certificate = cert
        self.chain: list[x509.Certificate] = list(extra or [])
        self.info = _extrair_info(cert)

    # --- PEM -----------------------------------------------------------------
    def cert_pem(self) -> bytes:
        return self.cert.public_bytes(serialization.Encoding.PEM)

    def chain_pem(self) -> bytes:
        return self.cert_pem() + b"".join(c.public_bytes(serialization.Encoding.PEM) for c in self.chain)

    def key_pem(self, password: bytes | None = None) -> bytes:
        enc = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
        return self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)

    # --- mTLS ----------------------------------------------------------------
    def ssl_context(self) -> ssl.SSLContext:
        """SSLContext com o certificado do cliente carregado.

        O módulo ``ssl`` só aceita certificado/chave por arquivo; gravamos arquivos
        temporários (a chave cifrada com senha aleatória) e apagamos em seguida.
        """
        ctx = ssl.create_default_context(cafile=certifi.where())
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        tmp_pass = secrets.token_hex(16).encode()
        cert_fd, cert_path = tempfile.mkstemp(suffix=".pem")
        key_fd, key_path = tempfile.mkstemp(suffix=".pem")
        try:
            with os.fdopen(cert_fd, "wb") as fh:
                fh.write(self.chain_pem())
            with os.fdopen(key_fd, "wb") as fh:
                fh.write(self.key_pem(tmp_pass))
            ctx.load_cert_chain(cert_path, key_path, password=tmp_pass)
        finally:
            for p in (cert_path, key_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
        return ctx


def _extrair_info(cert: x509.Certificate) -> CertificadoInfo:
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    cn = str(cn_attrs[0].value) if cn_attrs else cert.subject.rfc4514_string()
    issuer_cn = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    emissor = str(issuer_cn[0].value) if issuer_cn else cert.issuer.rfc4514_string()

    cnpj = cpf = None
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for other in san.get_values_for_type(x509.OtherName):
            raw = other.value.decode("latin-1", errors="ignore")
            if other.type_id.dotted_string == OID_CNPJ:
                m = re.search(r"[0-9A-Z]{12}[0-9]{2}", raw)
                cnpj = m.group(0) if m else cnpj
            elif other.type_id.dotted_string == OID_CPF_TITULAR_PF:
                digits = re.sub(r"\D", "", raw)
                if len(digits) >= 19:
                    cpf = digits[8:19]
    except x509.ExtensionNotFound:
        pass
    if cnpj is None:
        # Padrão comum no CN de e-CNPJ: "RAZAO SOCIAL:12345678000199"
        m = re.search(r":([0-9A-Z]{12}[0-9]{2})\b", cn)
        cnpj = m.group(1) if m else None

    titular = cn.split(":")[0].strip()
    return CertificadoInfo(
        titular=titular,
        cnpj=cnpj,
        cpf=cpf,
        emissor=emissor,
        serial=format(cert.serial_number, "x"),
        valido_de=cert.not_valid_before_utc,
        valido_ate=cert.not_valid_after_utc,
    )
