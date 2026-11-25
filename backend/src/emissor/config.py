"""Caminhos e configurações de runtime.

Tudo que é gerado em execução (banco, certificado, XML/PDF, logs) fica em ``data/``
na raiz do repositório, ou no diretório apontado por ``EMISSOR_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Emissor em Planilha"
APP_VERSION = "0.20.0"
# Vai na tag <verAplic> da DPS (máx. 20 caracteres).
VER_APLIC = f"EmPlanilha_{APP_VERSION}"

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parents[2]

DATA_DIR = Path(os.environ.get("EMISSOR_DATA_DIR", REPO_ROOT / "data")).resolve()
LOG_DIR = DATA_DIR / "logs"
SEFIN_LOG_DIR = LOG_DIR / "sefin"
XML_DIR = DATA_DIR / "xml"
PDF_DIR = DATA_DIR / "pdf"
SECRET_KEY_FILE = DATA_DIR / "secret.key"
DB_FILE = DATA_DIR / "emissor.db"

TIMEZONE = "America/Sao_Paulo"


def servidor_publico() -> bool:
    """Instalação exposta na internet: a nossa hospedagem (``FLY_APP_NAME``) ou ``EMISSOR_PUBLICO=1``.

    Liga as proteções de servidor público (webhook só https e fora da rede interna; primeiro acesso sempre com
    código), sem os ajustes próprios da hospedagem (limite de empresas, atualizações pelo painel).
    """
    if os.environ.get("FLY_APP_NAME"):
        return True
    return os.environ.get("EMISSOR_PUBLICO", "").strip().lower() in ("1", "true", "sim", "yes")


def ensure_dirs() -> None:
    for d in (DATA_DIR, LOG_DIR, SEFIN_LOG_DIR, XML_DIR, PDF_DIR):
        d.mkdir(parents=True, exist_ok=True)
