from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

FIXTURES = Path(__file__).parent / "fixtures"
TEMP_DIR = Path(__file__).resolve().parents[2] / "temp"

CNPJ_PRESTADOR = "11444777000161"
SENHA_PFX = "senha-teste"


@pytest.fixture(autouse=True)
def data_dir_isolado(tmp_path, monkeypatch):
    """Nenhum teste escreve em data/ do projeto."""
    from emissor import config, security

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "data" / "logs")
    monkeypatch.setattr(config, "SEFIN_LOG_DIR", tmp_path / "data" / "logs" / "sefin")
    monkeypatch.setattr(config, "XML_DIR", tmp_path / "data" / "xml")
    monkeypatch.setattr(config, "PDF_DIR", tmp_path / "data" / "pdf")
    monkeypatch.setattr(config, "SECRET_KEY_FILE", tmp_path / "data" / "secret.key")
    monkeypatch.setattr(config, "DB_FILE", tmp_path / "data" / "emissor.db")
    from emissor import db
    from emissor.services import certificado as cert_service
    from emissor.services import emissao as emissao_service

    security.reset_cache()
    db.reset_engine()
    cert_service._limpar_cache()
    emissao_service.definir_worker(None)
    yield
    db.reset_engine()
    security.reset_cache()
    cert_service._limpar_cache()
    emissao_service.definir_worker(None)


def _gerar_pfx(cnpj: str, senha: str, dias: int = 365) -> bytes:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "BR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ICP-Brasil"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"EMPRESA TESTE LTDA:{cnpj}"),
        ]
    )
    # otherName 2.16.76.1.3.3 com o CNPJ em OCTET STRING, como nos e-CNPJ reais.
    other = x509.OtherName(x509.ObjectIdentifier("2.16.76.1.3.3"), b"\x04\x0e" + cnpj.encode())
    agora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(agora - timedelta(days=1))
        .not_valid_after(agora + timedelta(days=dias))
        .add_extension(x509.SubjectAlternativeName([other]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", key, cert, None, serialization.BestAvailableEncryption(senha.encode())
    )


@pytest.fixture(scope="session")
def pfx_bytes() -> bytes:
    return _gerar_pfx(CNPJ_PRESTADOR, SENHA_PFX)


@pytest.fixture(scope="session")
def certificado(pfx_bytes):
    from emissor.nfse.certificate import Certificado

    return Certificado(pfx_bytes, SENHA_PFX)


@pytest.fixture
def config_fiscal():
    from emissor.nfse.models import ConfigFiscal

    return ConfigFiscal(
        cnpj=CNPJ_PRESTADOR,
        c_mun_emissor="4205407",
        fone="4830000000",
        email="CONTATO@EXEMPLO.COM.BR",
        op_simp_nac="3",
        reg_ap_trib_sn="2",
        reg_esp_trib="0",
        c_loc_prestacao="4205407",
        c_trib_nac="171901",
        c_nbs="113022100",
        trib_issqn="1",
        tp_ret_issqn="1",
        cst_pis_cofins="00",
        p_tot_trib_sn=Decimal("11.08"),
    )


@pytest.fixture
def tomador():
    from emissor.nfse.models import Endereco, Tomador

    return Tomador(
        documento="11222333000181",
        nome="CLIENTE EXEMPLO LTDA",
        endereco=Endereco(c_mun="4205407", cep="88010000", logradouro="RUA EXEMPLO", numero="100", bairro="CENTRO"),
    )


@pytest.fixture
def dps_input(config_fiscal, tomador):
    from zoneinfo import ZoneInfo

    from emissor.nfse.constants import Ambiente
    from emissor.nfse.models import DpsInput

    return DpsInput(
        ambiente=Ambiente.HOMOLOGACAO,
        serie="1",
        n_dps=42,
        dh_emi=datetime(2026, 9, 23, 10, 30, 0, tzinfo=ZoneInfo("America/Sao_Paulo")),
        config=config_fiscal,
        tomador=tomador,
        valor=Decimal("450"),
        descricao="HONORARIOS 09/2026",
    )


@pytest.fixture
def nfse_exemplo_bytes() -> bytes:
    return (FIXTURES / "nfse_exemplo.xml").read_bytes()


def xmls_reais() -> list[Path]:
    return sorted(TEMP_DIR.glob("*.xml")) if TEMP_DIR.exists() else []
