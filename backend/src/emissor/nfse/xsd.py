"""Validação local contra os XSD oficiais v1.01 (ver ``xsd/*/LEIAME.txt``).

Desde 20/10/2026 a Produção Restrita usa o leiaute da NT 009 (``xsd/v1_01_nt009``) e a Produção continua no anterior
(``xsd/v1_01``). O leiaute de cada documento sai do seu ``tpAmb`` (``LEIAUTE_POR_AMBIENTE``).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lxml import etree

from emissor.nfse.constants import LEIAUTE_POR_AMBIENTE, NS_NFSE, Ambiente

XSD_BASE = Path(__file__).parent / "xsd"
XSD_DIR = XSD_BASE / "v1_01"


@dataclass(frozen=True)
class XsdErro:
    linha: int
    campo: str | None
    mensagem: str

    def __str__(self) -> str:
        return f"{self.campo}: {self.mensagem}" if self.campo else self.mensagem


@cache
def _schema(leiaute: str, nome: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_BASE / leiaute / nome)))


def leiaute_do_documento(root: etree._Element) -> str:
    """Pasta do XSD pelo primeiro ``tpAmb`` do documento (sem ``tpAmb``: o leiaute de produção)."""
    tp_amb = root.findtext(f".//{{{NS_NFSE}}}tpAmb")
    try:
        return LEIAUTE_POR_AMBIENTE[Ambiente(tp_amb)]
    except ValueError:
        return LEIAUTE_POR_AMBIENTE[Ambiente.PRODUCAO]


def _validar(nome: str, doc: etree._Element | bytes, leiaute: str | None) -> list[XsdErro]:
    root = etree.fromstring(doc) if isinstance(doc, bytes) else doc
    schema = _schema(leiaute or leiaute_do_documento(root), nome)
    if schema.validate(root):
        return []
    erros = []
    for e in schema.error_log:
        campo = None
        msg = e.message
        if msg.startswith("Element '"):
            campo = msg.split("'")[1].split("}")[-1]
            msg = msg.split("': ", 1)[-1]
        erros.append(XsdErro(linha=e.line, campo=campo, mensagem=msg))
    return erros


def validar_dps(doc: etree._Element | bytes, leiaute: str | None = None) -> list[XsdErro]:
    return _validar("DPS_v1.01.xsd", doc, leiaute)


def validar_nfse(doc: etree._Element | bytes, leiaute: str | None = None) -> list[XsdErro]:
    return _validar("NFSe_v1.01.xsd", doc, leiaute)


def validar_pedido_evento(doc: etree._Element | bytes, leiaute: str | None = None) -> list[XsdErro]:
    return _validar("pedRegEvento_v1.01.xsd", doc, leiaute)


def validar_evento(doc: etree._Element | bytes, leiaute: str | None = None) -> list[XsdErro]:
    return _validar("evento_v1.01.xsd", doc, leiaute)
