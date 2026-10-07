"""Onde e com que nome ficam DPS, XML e PDF de cada emissão.

Padrão de nome dos arquivos:
``AAAAMMDD_NFSe-NNNN_<PRESTADOR>-para-<TOMADOR>_R<valor>``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from emissor import config


def _slug(s: str, limite: int) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9&.\-]+", "_", s.upper()).strip("_")
    return s[:limite].rstrip("_")


def nome_base(data: datetime, numero: str | None, prestador: str, tomador: str, valor: str) -> str:
    v = Decimal(valor or "0")
    valor_txt = f"{v:.2f}".rstrip("0").rstrip(".") if v != v.to_integral() else f"{int(v)}"
    num = f"{int(numero):04d}" if numero and numero.isdigit() else (numero or "SEMNUMERO")
    return f"{data:%Y%m%d}_NFSe-{num}_{_slug(prestador, 30)}-para-{_slug(tomador, 30)}_R{valor_txt}"


def _pasta(raiz: Path, ambiente: str, data: datetime) -> Path:
    sub = "homologacao" if ambiente == "2" else "producao"
    p = raiz / sub / f"{data:%Y}" / f"{data:%m}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def caminho_xml(ambiente: str, data: datetime, base: str) -> Path:
    return _pasta(config.XML_DIR, ambiente, data) / f"{base}.xml"


def caminho_pdf(ambiente: str, data: datetime, base: str) -> Path:
    return _pasta(config.PDF_DIR, ambiente, data) / f"{base}.pdf"


def caminho_dps(ambiente: str, id_dps: str) -> Path:
    p = config.DATA_DIR / "dps" / ("homologacao" if ambiente == "2" else "producao")
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{id_dps}.xml"


def relativo(p: Path) -> str:
    try:
        return str(p.relative_to(config.DATA_DIR)).replace("\\", "/")
    except ValueError:
        return str(p)


def absoluto(rel: str | None) -> Path | None:
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else config.DATA_DIR / p
