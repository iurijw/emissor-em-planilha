"""Eventos da NFS-e (leiaute 1.01): pedido de cancelamento e leitura de eventos.

- **Cancelamento (e101101):** o prestador monta o ``pedRegEvento``, assina o ``infPedReg``
  (mesmas regras da DPS) e envia para ``POST /nfse/{chave}/eventos``. A Sefin devolve o
  ``evento`` já processado e assinado por ela.
- O ``Id`` do pedido é ``PRE`` + chave(50) + tipo do evento(6): não há número de pedido,
  então reenviar o mesmo pedido cai na deduplicação da Sefin (não cria um segundo evento).
- Cancelamento é estado terminal: depois de 101101 ou 105102 nenhum evento é aceito.
- Eventos feitos fora do sistema (portal, prefeitura) chegam pelo ADN e são lidos com
  ``EventoDoc`` — ver ``services/eventos.py``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

from lxml import etree

from emissor.config import VER_APLIC
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import NS_NFSE, VERSAO_LEIAUTE, Ambiente
from emissor.nfse.dps_builder import formatar_dh, to_bytes
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro
from emissor.nfse.models import limpar_texto, normalizar_documento
from emissor.nfse.signer import assinar
from emissor.nfse.xsd import validar_pedido_evento

log = logging.getLogger(__name__)

TIPO_CANCELAMENTO = "101101"

# Tipos de evento do leiaute (tiposEventos_v1.01.xsd).
TIPOS_EVENTO = {
    "101101": "Cancelamento de NFS-e",
    "105102": "Cancelamento de NFS-e por Substituição",
    "101103": "Solicitação de Análise Fiscal para Cancelamento de NFS-e",
    "105104": "Cancelamento de NFS-e Deferido por Análise Fiscal",
    "105105": "Cancelamento de NFS-e Indeferido por Análise Fiscal",
    "202201": "Confirmação do Prestador",
    "203202": "Confirmação do Tomador",
    "204203": "Confirmação do Intermediário",
    "205204": "Confirmação Tácita",
    "202205": "Rejeição do Prestador",
    "203206": "Rejeição do Tomador",
    "204207": "Rejeição do Intermediário",
    "205208": "Anulação da Rejeição",
    "305101": "Cancelamento de NFS-e por Ofício",
    "305102": "Bloqueio de NFS-e por Ofício",
    "305103": "Desbloqueio de NFS-e por Ofício",
}

# Eventos que mudam a situação da nota no sistema (os demais só ficam no histórico).
SITUACAO_POR_EVENTO = {
    "101101": "cancelada",
    "105104": "cancelada",
    "305101": "cancelada",
    "105102": "substituida",
}

# Nomes usados pelo ADN (campo ``TipoEvento`` da distribuição) → código do leiaute.
TIPO_EVENTO_ADN = {
    "CANCELAMENTO": "101101",
    "CANCELAMENTO_POR_SUBSTITUICAO": "105102",
    "SOLICITACAO_CANCELAMENTO_ANALISE_FISCAL": "101103",
    "CANCELAMENTO_DEFERIDO_ANALISE_FISCAL": "105104",
    "CANCELAMENTO_INDEFERIDO_ANALISE_FISCAL": "105105",
    "CONFIRMACAO_PRESTADOR": "202201",
    "CONFIRMACAO_TOMADOR": "203202",
    "CONFIRMACAO_INTERMEDIARIO": "204203",
    "CONFIRMACAO_TACITA": "205204",
    "REJEICAO_PRESTADOR": "202205",
    "REJEICAO_TOMADOR": "203206",
    "REJEICAO_INTERMEDIARIO": "204207",
    "ANULACAO_REJEICAO": "205208",
    "CANCELAMENTO_POR_OFICIO": "305101",
    "BLOQUEIO_POR_OFICIO": "305102",
    "DESBLOQUEIO_POR_OFICIO": "305103",
}

MOTIVOS_CANCELAMENTO = {"1": "Erro na emissão", "2": "Serviço não prestado", "9": "Outros"}
MOTIVO_MIN, MOTIVO_MAX = 15, 255

# Rejeições que indicam que o evento já existe (ou que a nota já está cancelada/substituída):
# antes de desistir, consulta-se o evento na Sefin.
_CODIGOS_JA_REGISTRADO = {"E0840", "E1805", "E0802"}


class EventoBuildError(ValueError):
    """Dado inválido para montar o pedido de evento (mensagem para o usuário)."""


def id_pedido_evento(chave: str, tipo: str) -> str:
    return f"PRE{chave}{tipo}"


def _sub(parent: etree._Element, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{NS_NFSE}}}{tag}")
    if text is not None:
        el.text = text
    return el


def limpar_motivo(x_motivo: str | None) -> str:
    """Normaliza o texto do motivo (``TSMotivo``: 15–255 caracteres, uma linha)."""
    texto = limpar_texto(x_motivo, max_len=MOTIVO_MAX) or ""
    if len(texto) < MOTIVO_MIN:
        raise EventoBuildError(
            f"Descreva o motivo do cancelamento com pelo menos {MOTIVO_MIN} caracteres (informado: {len(texto)})."
        )
    return texto


def build_pedido_cancelamento(
    *,
    chave: str,
    autor: str,
    ambiente: Ambiente,
    c_motivo: str,
    x_motivo: str,
    dh_evento: datetime,
) -> etree._Element:
    """Monta o ``pedRegEvento`` de cancelamento (sem assinatura)."""
    if not re.fullmatch(r"\d{50}", chave or ""):
        raise EventoBuildError(f"Chave de acesso inválida: {chave!r} (são 50 dígitos).")
    if c_motivo not in MOTIVOS_CANCELAMENTO:
        raise EventoBuildError("Motivo do cancelamento deve ser 1 (erro na emissão), 2 (serviço não prestado) ou 9.")
    doc = normalizar_documento(autor) or ""
    if len(doc) not in (11, 14):
        raise EventoBuildError(f"CNPJ/CPF do autor inválido: {autor!r}.")
    root = etree.Element(f"{{{NS_NFSE}}}pedRegEvento", nsmap={None: NS_NFSE}, versao=VERSAO_LEIAUTE)
    inf = _sub(root, "infPedReg")
    inf.set("Id", id_pedido_evento(chave, TIPO_CANCELAMENTO))
    _sub(inf, "tpAmb", ambiente.value)
    _sub(inf, "verAplic", VER_APLIC)
    _sub(inf, "dhEvento", formatar_dh(dh_evento))
    _sub(inf, "CPFAutor" if len(doc) == 11 else "CNPJAutor", doc)
    _sub(inf, "chNFSe", chave)
    ev = _sub(inf, f"e{TIPO_CANCELAMENTO}")
    _sub(ev, "xDesc", TIPOS_EVENTO[TIPO_CANCELAMENTO])
    _sub(ev, "cMotivo", c_motivo)
    _sub(ev, "xMotivo", limpar_motivo(x_motivo))
    return root


def preparar_cancelamento(cert: Certificado, **kw) -> tuple[str, bytes]:
    """Monta, assina e valida o pedido. Retorna (Id do pedido, XML assinado)."""
    try:
        pedido = build_pedido_cancelamento(**kw)
    except EventoBuildError as exc:
        raise SefinError(
            TipoErro.VALIDACAO_LOCAL, "Pedido de cancelamento inválido", [MensagemSefin(None, str(exc))]
        ) from exc
    assinado = assinar(pedido, cert)
    erros = validar_pedido_evento(assinado)
    if erros:
        raise SefinError(
            TipoErro.VALIDACAO_LOCAL,
            "O pedido de cancelamento não passou na validação do leiaute (nada foi enviado)",
            [MensagemSefin(codigo="XSD", descricao=str(e)) for e in erros],
        )
    return pedido[0].get("Id"), to_bytes(assinado)


@dataclass
class ResultadoEvento:
    evento_xml: bytes
    recuperado: bool = False  # True se o evento já existia e foi obtido por consulta
    tentativas: int = 1


def enviar_cancelamento(
    client: SefinClient,
    chave: str,
    pedido_xml: bytes,
    *,
    max_tentativas: int = 3,
    espera_base: float = 3.0,
    dormir=time.sleep,
) -> ResultadoEvento:
    """Envia o pedido; em resposta ambígua/duplicidade consulta o evento antes de reenviar.

    Reenvia sempre o **mesmo** pedido assinado (mesmo Id).
    """
    for tentativa in range(1, max_tentativas + 1):
        try:
            resp = client.registrar_evento(chave, pedido_xml, operacao="o cancelamento")
            log.info("cancelamento registrado", extra={"chave": chave, "tentativa": tentativa})
            return ResultadoEvento(resp.evento_xml, tentativas=tentativa)
        except SefinError as err:
            log.warning(
                "falha ao registrar cancelamento: %s",
                err,
                extra={"chave": chave, "tentativa": tentativa, "erro": err.as_dict()},
            )
            ja_registrado = any((m.codigo or "").upper() in _CODIGOS_JA_REGISTRADO for m in err.mensagens)
            if err.pode_ter_sido_processada or err.indica_duplicidade or ja_registrado:
                existente = _consultar_cancelamento(client, chave)
                if existente:
                    return ResultadoEvento(existente, recuperado=True, tentativas=tentativa)
            if not err.transitorio or tentativa == max_tentativas:
                raise
            dormir(espera_base * (2 ** (tentativa - 1)))
    raise AssertionError("inalcançável")


def _consultar_cancelamento(client: SefinClient, chave: str) -> bytes | None:
    try:
        xml = client.consultar_evento(chave, TIPO_CANCELAMENTO, 1)
    except SefinError as err:
        log.warning("não foi possível consultar o evento de cancelamento: %s", err, extra={"chave": chave})
        return None
    if xml:
        log.info("evento de cancelamento já existia; recuperado", extra={"chave": chave})
    return xml


# --- leitura de eventos ---------------------------------------------------------------------------

_NS = {"n": NS_NFSE}


class EventoXmlError(ValueError):
    pass


@dataclass
class EventoDoc:
    """Evento processado (``<evento>``), como devolvido pela Sefin ou distribuído pelo ADN."""

    root: etree._Element

    @classmethod
    def from_bytes(cls, data: bytes) -> EventoDoc:
        try:
            parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
            root = etree.fromstring(data, parser)
        except etree.XMLSyntaxError as exc:
            raise EventoXmlError(f"XML de evento inválido: {exc}") from exc
        if etree.QName(root).localname != "evento" or root.find("n:infEvento", _NS) is None:
            raise EventoXmlError("O arquivo não é um evento de NFS-e (elemento raiz <evento>/<infEvento>).")
        return cls(root)

    def _t(self, caminho: str) -> str | None:
        res = self.root.xpath("n:infEvento/" + "/".join(f"n:{p}" for p in caminho.split("/")), namespaces=_NS)
        return res[0].text.strip() if res and res[0].text else None

    @property
    def _inf_ped(self) -> etree._Element | None:
        return self.root.find("n:infEvento/n:pedRegEvento/n:infPedReg", _NS)

    @property
    def _detalhe(self) -> etree._Element | None:
        inf = self._inf_ped
        if inf is None:
            return None
        return next((el for el in inf if re.fullmatch(r"e\d{6}", etree.QName(el).localname)), None)

    def _det(self, tag: str) -> str | None:
        det = self._detalhe
        el = det.find(f"n:{tag}", _NS) if det is not None else None
        return el.text.strip() if el is not None and el.text else None

    @property
    def id(self) -> str:
        return self.root.find("n:infEvento", _NS).get("Id", "")

    @property
    def tipo(self) -> str:
        det = self._detalhe
        if det is not None:
            return etree.QName(det).localname[1:]
        # Sem o grupo de detalhe: o tipo está no Id (EVT + chave(50) + tipo(6) + nSeq(3)).
        return self.id[53:59]

    @property
    def descricao(self) -> str:
        return self._det("xDesc") or TIPOS_EVENTO.get(self.tipo, f"Evento {self.tipo}")

    @property
    def chave(self) -> str | None:
        return self._t("pedRegEvento/infPedReg/chNFSe") or (self.id[3:53] if self.id.startswith("EVT") else None)

    @property
    def n_seq(self) -> int:
        try:
            return int(self._t("nSeqEvento") or 1)
        except ValueError:
            return 1

    @property
    def tp_amb(self) -> str | None:
        return self._t("pedRegEvento/infPedReg/tpAmb")

    @property
    def autor(self) -> str | None:
        return self._t("pedRegEvento/infPedReg/CNPJAutor") or self._t("pedRegEvento/infPedReg/CPFAutor")

    @property
    def dh_processamento(self) -> datetime | None:
        texto = self._t("dhProc") or self._t("pedRegEvento/infPedReg/dhEvento")
        try:
            return datetime.fromisoformat(texto) if texto else None
        except ValueError:
            return None

    @property
    def c_motivo(self) -> str | None:
        return self._det("cMotivo")

    @property
    def x_motivo(self) -> str | None:
        return self._det("xMotivo") or self._det("xProcAdm")

    @property
    def ch_substituta(self) -> str | None:
        return self._det("chSubstituta")

    @property
    def motivo_texto(self) -> str | None:
        """Motivo legível (código + descrição), quando o evento tem um."""
        partes = []
        if self.tipo == TIPO_CANCELAMENTO and self.c_motivo in MOTIVOS_CANCELAMENTO:
            partes.append(MOTIVOS_CANCELAMENTO[self.c_motivo])
        if self.x_motivo:
            partes.append(self.x_motivo)
        if self.ch_substituta:
            partes.append(f"NFS-e substituta: {self.ch_substituta}")
        return " — ".join(partes) or None
