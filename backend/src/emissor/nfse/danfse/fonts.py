"""Fontes exigidas pela NT 008: Arial (rótulos) e Microsoft Sans Serif (conteúdo).

As fontes não são distribuídas com o sistema (licença Microsoft). Busca-se no SO:
Windows (``C:\\Windows\\Fonts``), pasta em ``EMISSOR_FONT_DIR`` ou pacotes Linux
(msttcorefonts). Sem elas, usa Liberation Sans/Arimo (métricas idênticas à Arial) ou,
em último caso, Helvetica embutida no ReportLab — o log avisa do fallback.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fontes:
    rotulo: str  # Arial
    rotulo_negrito: str  # Arial Bold
    conteudo: str  # Microsoft Sans Serif
    origem: str


def _dirs() -> list[Path]:
    dirs = []
    if os.environ.get("EMISSOR_FONT_DIR"):
        dirs.append(Path(os.environ["EMISSOR_FONT_DIR"]))
    windir = os.environ.get("WINDIR", r"C:\Windows")
    dirs += [
        Path(windir) / "Fonts",
        Path("/usr/share/fonts/truetype/msttcorefonts"),
        Path("/usr/share/fonts/truetype/liberation"),
        Path("/usr/share/fonts/liberation-sans"),
        Path("/usr/share/fonts/truetype/croscore"),
    ]
    return [d for d in dirs if d.is_dir()]


def _achar(*nomes: str) -> Path | None:
    for d in _dirs():
        for n in nomes:
            for cand in (d / n, d / n.lower(), d / n.capitalize()):
                if cand.is_file():
                    return cand
    return None


def _registrar(nome: str, arquivo: Path) -> str:
    if nome not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(nome, str(arquivo)))
    return nome


@lru_cache(maxsize=1)
def carregar_fontes() -> Fontes:
    arial = _achar("arial.ttf", "Arial.ttf")
    arial_b = _achar("arialbd.ttf", "Arial_Bold.ttf")
    micross = _achar("micross.ttf")
    if arial and arial_b:
        conteudo_arq = micross or arial
        f = Fontes(
            _registrar("DANFSe-Arial", arial),
            _registrar("DANFSe-Arial-Bold", arial_b),
            _registrar("DANFSe-MSSansSerif", conteudo_arq),
            "Arial/Microsoft Sans Serif" if micross else "Arial (Microsoft Sans Serif ausente)",
        )
        if not micross:
            log.warning("Fonte Microsoft Sans Serif não encontrada; usando Arial no conteúdo do DANFSe")
        return f
    lib = _achar("LiberationSans-Regular.ttf", "Arimo-Regular.ttf")
    lib_b = _achar("LiberationSans-Bold.ttf", "Arimo-Bold.ttf")
    if lib and lib_b:
        log.warning("Arial não encontrada; DANFSe usará Liberation Sans/Arimo (métricas compatíveis)")
        return Fontes(
            _registrar("DANFSe-Arial", lib),
            _registrar("DANFSe-Arial-Bold", lib_b),
            _registrar("DANFSe-MSSansSerif", lib),
            "Liberation/Arimo",
        )
    log.warning("Nenhuma fonte TrueType compatível encontrada; DANFSe usará Helvetica embutida")
    return Fontes("Helvetica", "Helvetica-Bold", "Helvetica", "Helvetica")
