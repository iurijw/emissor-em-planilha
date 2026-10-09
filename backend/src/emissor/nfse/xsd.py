"""Validação local contra os XSD oficiais v1.01 (ver ``xsd/v1_01/LEIAME.txt``)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from lxml import etree

XSD_DIR = Path(__file__).parent / "xsd" / "v1_01"


@dataclass(frozen=True)
class XsdErro:
    linha: int
    campo: str | None
    mensagem: str

    def __str__(self) -> str:
        return f"{self.campo}: {self.mensagem}" if self.campo else self.mensagem


@cache
def _schema(nome: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_DIR / nome)))


def _validar(nome: str, doc: etree._Element | bytes) -> list[XsdErro]:
    root = etree.fromstring(doc) if isinstance(doc, bytes) else doc
    schema = _schema(nome)
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


def validar_dps(doc: etree._Element | bytes) -> list[XsdErro]:
    return _validar("DPS_v1.01.xsd", doc)


def validar_nfse(doc: etree._Element | bytes) -> list[XsdErro]:
    return _validar("NFSe_v1.01.xsd", doc)


def validar_pedido_evento(doc: etree._Element | bytes) -> list[XsdErro]:
    return _validar("pedRegEvento_v1.01.xsd", doc)


def validar_evento(doc: etree._Element | bytes) -> list[XsdErro]:
    return _validar("evento_v1.01.xsd", doc)
