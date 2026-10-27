"""Conferência do código de serviço na parametrização do município (ADN), antes de emitir.

A Sefin recusa com E0312 a DPS cujo código de tributação nacional o município de incidência do ISS não administra
na competência. O município cadastra no Sistema Nacional cada serviço que administra, com a alíquota; a consulta da
alíquota (``SefinClient.aliquota_municipal``) diz antes se o serviço está lá.

É só um aviso: nunca bloqueia a emissão nem as configurações, e qualquer falha (sem certificado, ADN fora do ar,
resposta que não se entende) vira "não conferido". O formato da consulta não tem documentação oficial publicada
(ver CLAUDE.md, "Conferência do serviço na prefeitura").
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlmodel import Session

from emissor.db import LinhaTabela, agora, obter_config
from emissor.nfse import codes
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.errors import SefinError
from emissor.nfse.models import aplicar_tributacao
from emissor.services import certificado as cert_service
from emissor.services import tributacao as trib_service

log = logging.getLogger(__name__)

# Os testes desligam a consulta (conftest); os de ponta a ponta ligam, com o ADN simulado.
ATIVA = True
TTL_RESPOSTA = 6 * 3600
TTL_FALHA = 5 * 60
# A validação antes de emitir não espera mais que isso pelas consultas (o que sobrar fica sem conferir).
ORCAMENTO_VALIDACAO = 10.0
# LC 116, art. 3º, XXIII a XXV: o ISS destes subitens é devido no domicílio do tomador (uma prefeitura por nota).
SUBITENS_DOMICILIO_TOMADOR = ("0422", "0423", "0509", "1004", "1501", "1509")

ENCONTRADO = "encontrado"
NAO_ENCONTRADO = "nao_encontrado"
NAO_CONFERIDO = "nao_conferido"


@dataclass
class Conferencia:
    situacao: str
    mensagem: str
    c_trib_nac: str
    municipio: str | None = None
    competencia: str | None = None
    aliquota: str | None = None
    # Texto devolvido pelo Ambiente Nacional, quando houver.
    detalhe: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


ClientFactory = Callable[[Ambiente, Certificado], SefinClient]
_cache: dict[tuple[str, str, str, str], tuple[float, Conferencia]] = {}
_lock = threading.Lock()


def limpar_cache() -> None:
    with _lock:
        _cache.clear()


def municipio_incidencia(c_mun_emissor: str, c_trib_nac: str, c_loc_prestacao: str | None) -> tuple[str | None, str]:
    """(município de incidência que dá para conferir, motivo quando não dá)."""
    if c_trib_nac[:4] in SUBITENS_DOMICILIO_TOMADOR:
        return (
            None,
            "O ISS deste serviço é devido no município de cada tomador: não há uma prefeitura só para conferir.",
        )
    if c_loc_prestacao and c_loc_prestacao != c_mun_emissor:
        return None, (
            "O local da prestação é outro município: a conferência só vale para serviço prestado no município do "
            "prestador."
        )
    return c_mun_emissor, ""


def conferir(
    ambiente: Ambiente,
    c_mun_emissor: str,
    c_trib_nac: str,
    c_loc_prestacao: str | None = None,
    competencia: date | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> Conferencia:
    """O município de incidência tem o serviço ``c_trib_nac`` cadastrado na competência (padrão: hoje)?"""
    competencia = competencia or agora().date()
    mes = f"{competencia:%m/%Y}"
    c_trib_nac = (c_trib_nac or "").strip()
    servico = codes.formatar_c_trib_nac(c_trib_nac)
    if not (c_trib_nac.isdigit() and len(c_trib_nac) == 6):
        return Conferencia(NAO_CONFERIDO, "Informe o código de tributação nacional (6 dígitos).", c_trib_nac)
    mun, motivo = municipio_incidencia(c_mun_emissor, c_trib_nac, c_loc_prestacao)
    if mun is None:
        return Conferencia(NAO_CONFERIDO, motivo, c_trib_nac, competencia=mes)
    nome_mun = codes.municipio_uf(mun) or mun
    base = Conferencia(NAO_CONFERIDO, "", c_trib_nac, mun, mes)
    if not ATIVA:
        base.mensagem = "Conferência com a prefeitura desligada."
        return base
    chave = (ambiente.value, mun, c_trib_nac, mes)
    with _lock:
        guardada = _cache.get(chave)
    if guardada and guardada[0] > time.monotonic():
        return guardada[1]

    cert = cert_service.carregar()
    if cert is None:
        base.mensagem = "Carregue o certificado digital para conferir com a prefeitura."
        return base
    fabrica = client_factory or SefinClient
    try:
        with fabrica(ambiente, cert) as client:
            r = client.aliquota_municipal(mun, c_trib_nac, competencia)
    except SefinError as err:
        log.warning("conferência do serviço na prefeitura falhou", extra={"c_mun": mun, "c_trib_nac": c_trib_nac})
        base.mensagem = f"Não foi possível conferir agora: {err.resumo}."
        base.detalhe = "; ".join(m.texto() for m in err.mensagens) or None
        return _guardar(chave, base, TTL_FALHA)
    except Exception:  # nunca atrapalha quem chamou (validação, configurações)
        log.exception("erro inesperado na conferência do serviço na prefeitura")
        base.mensagem = "Não foi possível conferir agora."
        return _guardar(chave, base, TTL_FALHA)

    onde = f"A prefeitura de {nome_mun}"
    sistema = "no Sistema Nacional" + (" (Produção Restrita)" if ambiente is Ambiente.HOMOLOGACAO else "")
    if r.encontrado:
        aliq = f", com alíquota de {r.aliquota.replace('.', ',')}%" if r.aliquota else ""
        res = Conferencia(
            ENCONTRADO,
            f"{onde} tem o serviço {servico} cadastrado {sistema} para {mes}{aliq}.",
            c_trib_nac,
            mun,
            mes,
            r.aliquota,
            r.mensagem,
        )
    elif r.encontrado is False:
        res = Conferencia(
            NAO_ENCONTRADO,
            f"{onde} não tem o serviço {servico} cadastrado {sistema} para {mes}: a Sefin deve recusar a nota com "
            "E0312. Confirme o código com a contabilidade ou com a prefeitura.",
            c_trib_nac,
            mun,
            mes,
            detalhe=r.mensagem,
        )
    else:
        res = Conferencia(
            NAO_CONFERIDO,
            "Não foi possível conferir agora: o Ambiente Nacional não respondeu de um jeito conhecido.",
            c_trib_nac,
            mun,
            mes,
            detalhe=r.mensagem,
        )
    log.info(
        "serviço conferido na prefeitura",
        extra={"c_mun": mun, "c_trib_nac": c_trib_nac, "situacao": res.situacao, "ambiente": ambiente.name},
    )
    return _guardar(chave, res, TTL_RESPOSTA if res.situacao != NAO_CONFERIDO else TTL_FALHA)


def _guardar(chave: tuple[str, str, str, str], c: Conferencia, ttl: float) -> Conferencia:
    with _lock:
        _cache[chave] = (time.monotonic() + ttl, c)
    return c


# --- validação antes de emitir -------------------------------------------------------------------------


@dataclass(frozen=True)
class Alvo:
    """Uma combinação (serviço, local) a conferir e onde ela aparece (tributação)."""

    c_trib_nac: str
    c_loc_prestacao: str | None
    origem: str


def alvos(
    s: Session, linhas: list[LinhaTabela], tributacao_avulsa: dict | None = None, nome_avulsa: str | None = None
) -> tuple[str | None, list[Alvo]]:
    """(município do prestador, serviços distintos das linhas). Lido dentro da sessão; a consulta vem depois."""
    base = obter_config(s).config_fiscal()
    if base is None:
        return None, []
    abas = {a.id: a for a in trib_service.garantir_abas(s)}
    vistos: dict[tuple[str, str | None], Alvo] = {}
    for ln in linhas:
        if ln.aba_id is not None:
            try:
                nome, dados = trib_service.tributacao_da_aba(s, abas.get(ln.aba_id))
            except trib_service.TributacaoError:
                continue
        else:
            nome, dados = nome_avulsa or ("só desta nota" if tributacao_avulsa else None), tributacao_avulsa
        cfg = aplicar_tributacao(base, dados)
        chave = (cfg.c_trib_nac, cfg.c_loc_prestacao)
        if chave not in vistos:
            vistos[chave] = Alvo(
                cfg.c_trib_nac, cfg.c_loc_prestacao, f"tributação “{nome}”" if nome else "tributação padrão"
            )
    return base.c_mun_emissor, list(vistos.values())


def avisos(ambiente: Ambiente, c_mun_emissor: str | None, lista: list[Alvo]) -> list[str]:
    """Avisos para a conferência antes de emitir: só o que a prefeitura disse que não tem."""
    if not c_mun_emissor:
        return []
    res = []
    limite = time.monotonic() + ORCAMENTO_VALIDACAO
    for a in lista:
        if time.monotonic() > limite:
            break
        c = conferir(ambiente, c_mun_emissor, a.c_trib_nac, a.c_loc_prestacao)
        if c.situacao == NAO_ENCONTRADO:
            res.append(f"{c.mensagem} ({a.origem})")
    return res
