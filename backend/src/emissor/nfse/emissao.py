"""Fluxo completo de emissão de uma NFS-e, com proteção contra duplicidade.

1. Monta a DPS, assina e valida localmente contra o XSD (erro → nada é enviado).
2. Envia. Se a resposta for ambígua (timeout de leitura, 5xx no POST) ou a Sefin
   disser que a DPS já existe, consulta ``GET /dps/{id}``: se já virou NFS-e,
   recupera a nota em vez de reenviar — desde que a NFS-e encontrada seja **desta**
   DPS (mesma data/hora, tomador e valor). Se for de outra nota com o mesmo número
   (outro sistema emitindo com o mesmo CNPJ e série), levanta ``NumeracaoEmUso``.
3. Reenvia a **mesma** DPS (mesmo Id) em falhas transitórias, com espera crescente.

A numeração (``nDPS``) é responsabilidade de quem chama: o número deve ser reservado
antes da chamada e nunca reaproveitado para outra nota.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from lxml import etree

from emissor import config
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import NS_NFSE
from emissor.nfse.dps_builder import build_dps, to_bytes
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro
from emissor.nfse.models import DpsInput
from emissor.nfse.signer import assinar
from emissor.nfse.xml_reader import NFSeDoc
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


class NumeracaoEmUso(SefinError):
    """O número da DPS já gerou outra NFS-e (de outro sistema ou instalação): nada foi emitido."""


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
        return buscar_nfse_da_dps(client, id_dps, dps_xml, tentativa)
    except NumeracaoEmUso:
        raise
    except SefinError as err:
        log.warning("não foi possível verificar a DPS na Sefin: %s", err, extra={"id_dps": id_dps})
        return None


def buscar_nfse_da_dps(client: SefinClient, id_dps: str, dps_xml: bytes, tentativa: int = 1) -> ResultadoEmissao | None:
    """NFS-e gerada por esta DPS, ou None se o Id não gerou nota. Sem ``dps_xml`` não confere o conteúdo."""
    chave = client.consultar_dps(id_dps)
    if not chave:
        log.info("DPS não encontrada na Sefin; seguro reenviar", extra={"id_dps": id_dps})
        return None
    resp = client.consultar_nfse(chave)
    if dps_xml:
        conferir_mesma_dps(dps_xml, resp.nfse_xml)
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


def conferir_mesma_dps(dps_xml: bytes, nfse_xml: bytes) -> None:
    """Levanta ``NumeracaoEmUso`` se a NFS-e não foi gerada por esta DPS.

    O Id da DPS é só CNPJ + município + série + número: outro sistema que use o mesmo CNPJ e série gera o mesmo Id
    para outra nota (E0014). A data/hora de emissão, o tomador e o valor distinguem a nossa DPS.
    """
    ns = {"n": NS_NFSE}
    inf = etree.fromstring(dps_xml).find("n:infDPS", ns)

    def t(caminho: str) -> str | None:
        return inf.findtext("/".join(f"n:{p}" for p in caminho.split("/")), namespaces=ns)

    nota = NFSeDoc.from_bytes(nfse_xml)
    nosso = (t("dhEmi"), t("toma/CNPJ") or t("toma/CPF") or t("toma/NIF"), Decimal(t("valores/vServPrest/vServ")))
    dela = (nota.dps("dhEmi"), nota.tomador_documento, nota.valor_servico)
    if nosso == dela:
        return
    log.error(
        "o número da DPS já gerou outra NFS-e; a nota não foi emitida",
        extra={"id_dps": inf.get("Id"), "chave_existente": nota.chave, "numero_existente": nota.numero},
    )
    valor = f"{nota.valor_servico:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    data = (nota.dps("dhEmi") or "")[:10]
    data = "/".join(reversed(data.split("-"))) if data else "data desconhecida"
    raise NumeracaoEmUso(
        TipoErro.REJEICAO,
        "O número desta DPS já foi usado por outra nota; esta NÃO foi emitida",
        [
            MensagemSefin(
                codigo="E0014",
                descricao=f"A DPS série {t('serie')} nº {t('nDPS')} já gerou a NFS-e nº {nota.numero} de {data}, "
                f"para {nota.tomador_nome or nota.tomador_documento or 'outro tomador'} (R$ {valor}).",
                dica="Outro sistema ou outra instalação está emitindo com o mesmo CNPJ e a mesma série. "
                "Em Configurações, use uma série só para este sistema, ou ajuste a próxima DPS para depois do "
                "último número já usado nessa série, e emita esta linha de novo.",
            )
        ],
    )


def emitir(client: SefinClient, cert: Certificado, inp: DpsInput, **kw) -> ResultadoEmissao:
    id_dps, dps_xml = preparar_dps(inp, cert)
    return enviar_dps(client, id_dps, dps_xml, **kw)


def chave_de_nfse(nfse_xml: bytes) -> str | None:
    root = etree.fromstring(nfse_xml)
    inf = root.find("{http://www.sped.fazenda.gov.br/nfse}infNFSe")
    ident = inf.get("Id") if inf is not None else None
    return ident[3:] if ident and ident.startswith("NFS") else ident
