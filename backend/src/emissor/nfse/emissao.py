"""Fluxo completo de emissão de uma NFS-e, com proteção contra duplicidade.

1. Monta a DPS, assina e valida localmente contra o XSD (erro → nada é enviado).
2. Envia. Se a resposta for ambígua (timeout de leitura, 5xx no POST) ou a Sefin
   disser que a DPS já existe, consulta ``GET /dps/{id}``: se já virou NFS-e,
   recupera a nota em vez de reenviar.
3. Reenvia a **mesma** DPS (mesmo Id) em falhas transitórias, com espera crescente.

A numeração (``nDPS``) é responsabilidade de quem chama: o número deve ser reservado
antes da chamada e nunca reaproveitado para outra nota.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from lxml import etree

from emissor import config
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.dps_builder import build_dps, to_bytes
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro
from emissor.nfse.models import DpsInput
from emissor.nfse.signer import assinar
from emissor.nfse.xsd import validar_dps

log = logging.getLogger(__name__)

# Margem para o relógio do servidor não ficar à frente do da Sefin
# (dhEmi posterior ao processamento é rejeitado).
MARGEM_RELOGIO = timedelta(seconds=60)


def agora_emissao() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0) - MARGEM_RELOGIO


@dataclass
class ResultadoEmissao:
    id_dps: str
    chave_acesso: str
    dps_xml: bytes
    nfse_xml: bytes
    alertas: list[MensagemSefin] = field(default_factory=list)
    recuperada: bool = False  # True se a NFS-e já existia e foi recuperada via /dps
    tentativas: int = 1


def preparar_dps(inp: DpsInput, cert: Certificado) -> tuple[str, bytes]:
    """Monta, assina e valida. Retorna (id_dps, xml assinado)."""
    dps = build_dps(inp)
    id_dps = dps[0].get("Id")
    assinada = assinar(dps, cert)
    erros = validar_dps(assinada)
    if erros:
        raise SefinError(
            TipoErro.VALIDACAO_LOCAL,
            "O XML da DPS não passou na validação do leiaute (nada foi enviado)",
            [MensagemSefin(codigo="XSD", descricao=str(e)) for e in erros],
        )
    return id_dps, to_bytes(assinada)


def enviar_dps(
    client: SefinClient,
    id_dps: str,
    dps_xml: bytes,
    *,
    max_tentativas: int = 3,
    espera_base: float = 3.0,
    dormir=time.sleep,
) -> ResultadoEmissao:
    ultimo_erro: SefinError | None = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            resp = client.emitir(dps_xml)
            log.info("NFS-e autorizada", extra={"id_dps": id_dps, "chave": resp.chave_acesso, "tentativa": tentativa})
            return ResultadoEmissao(
                id_dps=id_dps,
                chave_acesso=resp.chave_acesso,
                dps_xml=dps_xml,
                nfse_xml=resp.nfse_xml,
                alertas=resp.alertas,
                tentativas=tentativa,
            )
        except SefinError as err:
            ultimo_erro = err
            log.warning(
                "falha ao enviar DPS: %s",
                err,
                extra={"id_dps": id_dps, "tentativa": tentativa, "erro": err.as_dict()},
            )
            if err.pode_ter_sido_processada or err.indica_duplicidade:
                recuperada = _recuperar(client, id_dps, dps_xml, tentativa)
                if recuperada:
                    return recuperada
            if not err.transitorio or tentativa == max_tentativas:
                raise
            dormir(espera_base * (2 ** (tentativa - 1)))
    assert ultimo_erro is not None
    raise ultimo_erro


def _recuperar(client: SefinClient, id_dps: str, dps_xml: bytes, tentativa: int) -> ResultadoEmissao | None:
    try:
        chave = client.consultar_dps(id_dps)
        if not chave:
            log.info("DPS não encontrada na Sefin; seguro reenviar", extra={"id_dps": id_dps})
            return None
        resp = client.consultar_nfse(chave)
    except SefinError as err:
        log.warning("não foi possível verificar a DPS na Sefin: %s", err, extra={"id_dps": id_dps})
        return None
    log.info("NFS-e já existia para a DPS; recuperada", extra={"id_dps": id_dps, "chave": chave})
    return ResultadoEmissao(
        id_dps=id_dps,
        chave_acesso=chave,
        dps_xml=dps_xml,
        nfse_xml=resp.nfse_xml,
        alertas=resp.alertas,
        recuperada=True,
        tentativas=tentativa,
    )


def emitir(client: SefinClient, cert: Certificado, inp: DpsInput, **kw) -> ResultadoEmissao:
    id_dps, dps_xml = preparar_dps(inp, cert)
    return enviar_dps(client, id_dps, dps_xml, **kw)


def chave_de_nfse(nfse_xml: bytes) -> str | None:
    root = etree.fromstring(nfse_xml)
    inf = root.find("{http://www.sped.fazenda.gov.br/nfse}infNFSe")
    ident = inf.get("Id") if inf is not None else None
    return ident[3:] if ident and ident.startswith("NFS") else ident
