"""Cliente REST da Sefin Nacional e do ADN (mTLS com certificado A1).

- Sefin Nacional: emissão (``/nfse``), consulta por DPS/chave e registro/consulta de eventos.
- ADN (Ambiente de Dados Nacional, ``/contribuintes``): distribuição de documentos por NSU e
  eventos de uma NFS-e — é por ele que chegam cancelamentos feitos fora do sistema (portal).

Toda chamada é registrada:
- no log JSON (``emissor.nfse.client``) com método, URL, status, tempo e corpo de resposta;
- em arquivos ``data/logs/sefin/<emissao_id|avulso>/<hora>_<op>_{req,resp}.*`` com o XML
  enviado e a resposta completa (XML da NFS-e já decodificado).
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from emissor import config
from emissor.logging_setup import get_context
from emissor.nfse.certificate import Certificado
from emissor.nfse.constants import Ambiente
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro, extrair_alertas, extrair_mensagens

log = logging.getLogger(__name__)


def gzip_b64(data: bytes) -> str:
    return base64.b64encode(gzip.compress(data)).decode("ascii")


def ungzip_b64(data: str) -> bytes:
    return gzip.decompress(base64.b64decode(data))


@dataclass
class RespostaEmissao:
    chave_acesso: str
    nfse_xml: bytes
    id_dps: str | None
    alertas: list[MensagemSefin] = field(default_factory=list)
    data_processamento: str | None = None
    bruto: dict[str, Any] = field(default_factory=dict)


@dataclass
class RespostaEvento:
    evento_xml: bytes
    data_processamento: str | None = None


@dataclass
class DocumentoDistribuido:
    """Item de ``LoteDFe`` do ADN (NFS-e, evento...)."""

    nsu: int | None
    chave_acesso: str | None
    tipo_documento: str  # NFSE | EVENTO | DPS | PEDIDO_REGISTRO_EVENTO | CNC | NENHUM
    tipo_evento: str | None  # CANCELAMENTO, CANCELAMENTO_POR_SUBSTITUICAO...
    xml: bytes | None
    data_geracao: str | None = None


@dataclass
class LoteDistribuicao:
    status: str  # DOCUMENTOS_LOCALIZADOS | NENHUM_DOCUMENTO_LOCALIZADO | REJEICAO
    documentos: list[DocumentoDistribuido] = field(default_factory=list)
    alertas: list[MensagemSefin] = field(default_factory=list)


class SefinClient:
    def __init__(
        self,
        ambiente: Ambiente,
        certificado: Certificado,
        *,
        timeout_conexao: float = 15.0,
        timeout_leitura: float = 90.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.ambiente = ambiente
        self.certificado = certificado
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(timeout_leitura, connect=timeout_conexao),
            "headers": {"Accept": "application/json", "User-Agent": config.VER_APLIC},
        }
        if transport is not None:
            kwargs["transport"] = transport
        else:
            kwargs["verify"] = certificado.ssl_context()
        self._http = httpx.Client(**kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SefinClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- operações ------------------------------------------------------------
    def emitir(self, dps_assinada: bytes) -> RespostaEmissao:
        """``POST /nfse`` — envia a DPS assinada e recebe a NFS-e gerada."""
        url = f"{self.ambiente.sefin_url}/nfse"
        corpo = {"dpsXmlGZipB64": gzip_b64(dps_assinada)}
        self._arquivo("emitir", "req", "xml", dps_assinada)
        resp = self._request("POST", url, "emitir", json=corpo, ambigua_em_timeout=True)
        dados = self._json(resp, "emitir")
        if resp.status_code not in (200, 201) or not _get(dados, "chaveAcesso"):
            raise self._erro_http(resp, dados, "A Sefin recusou a DPS")
        return self._resposta_nfse(dados, "emitir")

    def consultar_dps(self, id_dps: str) -> str | None:
        """``GET /dps/{id}`` — retorna a chave de acesso se a DPS já gerou NFS-e."""
        url = f"{self.ambiente.sefin_url}/dps/{id_dps}"
        resp = self._request("GET", url, "consultar_dps")
        if resp.status_code == 404:
            return None
        dados = self._json(resp, "consultar_dps")
        if resp.status_code != 200:
            raise self._erro_http(resp, dados, "Falha ao consultar a DPS")
        return _get(dados, "chaveAcesso")

    def consultar_nfse(self, chave: str) -> RespostaEmissao:
        """``GET /nfse/{chave}`` — baixa o XML da NFS-e."""
        url = f"{self.ambiente.sefin_url}/nfse/{chave}"
        resp = self._request("GET", url, "consultar_nfse")
        dados = self._json(resp, "consultar_nfse")
        if resp.status_code != 200:
            raise self._erro_http(resp, dados, "Falha ao consultar a NFS-e")
        return self._resposta_nfse(dados, "consultar_nfse")

    def registrar_evento(self, chave: str, pedido_assinado: bytes, *, operacao: str = "o evento") -> RespostaEvento:
        """``POST /nfse/{chave}/eventos`` — registra o pedido de evento (ex.: cancelamento)."""
        url = f"{self.ambiente.sefin_url}/nfse/{chave}/eventos"
        corpo = {"pedidoRegistroEventoXmlGZipB64": gzip_b64(pedido_assinado)}
        self._arquivo("evento", "req", "xml", pedido_assinado)
        resp = self._request("POST", url, "evento", json=corpo, ambigua_em_timeout=True)
        dados = self._json(resp, "evento")
        b64 = _get(dados, "eventoXmlGZipB64")
        if resp.status_code not in (200, 201) or not b64:
            raise self._erro_http(resp, dados, f"A Sefin recusou {operacao}")
        xml = ungzip_b64(b64)
        self._arquivo("evento", "evento", "xml", xml)
        return RespostaEvento(xml, _get(dados, "dataHoraProcessamento"))

    def consultar_evento(self, chave: str, tipo: str, n_seq: int = 1) -> bytes | None:
        """``GET /nfse/{chave}/eventos/{tipo}/{nSeq}`` — XML do evento, ou None se não existe."""
        url = f"{self.ambiente.sefin_url}/nfse/{chave}/eventos/{tipo}/{n_seq}"
        resp = self._request("GET", url, "consultar_evento")
        if resp.status_code == 404:
            self._json(resp, "consultar_evento")
            return None
        dados = self._json(resp, "consultar_evento")
        b64 = _get(dados, "eventoXmlGZipB64")
        if resp.status_code != 200 or not b64:
            raise self._erro_http(resp, dados, "Falha ao consultar o evento da NFS-e")
        return ungzip_b64(b64)

    def distribuicao_dfe(self, nsu: int, *, cnpj_consulta: str | None = None) -> LoteDistribuicao:
        """ADN ``GET /contribuintes/DFe/{NSU}?lote=true`` — até 50 documentos a partir do NSU."""
        params: dict[str, str] = {"lote": "true"}
        if cnpj_consulta:
            params["cnpjConsulta"] = cnpj_consulta
        url = f"{self.ambiente.adn_url}/contribuintes/DFe/{nsu}"
        return self._lote_adn(self._request("GET", url, "adn_dfe", params=params), "adn_dfe")

    def eventos_nfse(self, chave: str) -> LoteDistribuicao:
        """ADN ``GET /contribuintes/NFSe/{chave}/Eventos`` — todos os eventos da NFS-e."""
        url = f"{self.ambiente.adn_url}/contribuintes/NFSe/{chave}/Eventos"
        return self._lote_adn(self._request("GET", url, "adn_eventos"), "adn_eventos")

    # --- infraestrutura -----------------------------------------------------------
    def _request(
        self, metodo: str, url: str, op: str, *, ambigua_em_timeout: bool = False, **kw: Any
    ) -> httpx.Response:
        inicio = time.perf_counter()
        try:
            resp = self._http.request(metodo, url, **kw)
        except httpx.ConnectTimeout as exc:
            self._log_falha(op, metodo, url, inicio, exc)
            raise SefinError(TipoErro.INDISPONIVEL, "Tempo esgotado ao conectar na Sefin Nacional") from exc
        except httpx.ConnectError as exc:
            self._log_falha(op, metodo, url, inicio, exc)
            texto = str(exc)
            if "certificate" in texto.lower() or "handshake" in texto.lower() or "ssl" in texto.lower():
                raise SefinError(
                    TipoErro.AUTENTICACAO,
                    "Falha no aperto de mão TLS com a Sefin (certificado recusado ou cadeia inválida)",
                    [MensagemSefin(None, texto)],
                ) from exc
            raise SefinError(
                TipoErro.INDISPONIVEL,
                "Não foi possível conectar na Sefin Nacional (sem internet ou serviço fora do ar)",
                [MensagemSefin(None, texto)],
            ) from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError, httpx.ReadError) as exc:
            self._log_falha(op, metodo, url, inicio, exc)
            tipo = TipoErro.AMBIGUO if ambigua_em_timeout else TipoErro.INDISPONIVEL
            raise SefinError(
                tipo,
                "A conexão com a Sefin caiu antes da resposta"
                + (" — a nota pode ter sido gerada; será verificado antes de reenviar" if ambigua_em_timeout else ""),
                [MensagemSefin(None, f"{type(exc).__name__}: {exc}")],
            ) from exc
        ms = round((time.perf_counter() - inicio) * 1000)
        log.info(
            "sefin %s %s -> %s (%d ms)",
            op,
            url,
            resp.status_code,
            ms,
            extra={
                "sefin_op": op,
                "metodo": metodo,
                "url": url,
                "status": resp.status_code,
                "ms": ms,
                "ambiente": self.ambiente.name,
                "resposta": resp.text[:20000],
            },
        )
        return resp

    def _log_falha(self, op: str, metodo: str, url: str, inicio: float, exc: Exception) -> None:
        ms = round((time.perf_counter() - inicio) * 1000)
        log.warning(
            "sefin %s %s falhou: %s: %s",
            op,
            url,
            type(exc).__name__,
            exc,
            extra={"sefin_op": op, "metodo": metodo, "url": url, "ms": ms, "ambiente": self.ambiente.name},
        )

    def _json(self, resp: httpx.Response, op: str, *, registrar: bool = True) -> Any:
        if registrar:
            self._arquivo(op, "resp", "json", resp.content)
        if not resp.content:
            return None
        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError):
            return resp.text

    def _erro_http(self, resp: httpx.Response, dados: Any, resumo: str) -> SefinError:
        st = resp.status_code
        mensagens = extrair_mensagens(dados)
        if st in (401, 403, 495, 496):
            tipo = TipoErro.AUTENTICACAO
            resumo = f"Acesso negado pela Sefin (HTTP {st}): certificado não autorizado para esta operação/CNPJ"
        elif st == 429:
            tipo = TipoErro.INDISPONIVEL
            resumo = "Muitas requisições em pouco tempo (HTTP 429); aguarde e tente novamente"
        elif st >= 500:
            # 5xx no POST pode ter processado; quem chama decide consultando /dps.
            tipo = TipoErro.AMBIGUO if resp.request.method == "POST" else TipoErro.INDISPONIVEL
            resumo = f"Erro interno na Sefin Nacional (HTTP {st})"
        elif 400 <= st < 500:
            tipo = TipoErro.REJEICAO
            resumo = f"{resumo} (HTTP {st})"
        else:
            tipo = TipoErro.RESPOSTA_INVALIDA
            resumo = f"Resposta inesperada da Sefin (HTTP {st})"
        return SefinError(tipo, resumo, mensagens, http_status=st, corpo=resp.text[:20000])

    def _resposta_nfse(self, dados: Any, op: str) -> RespostaEmissao:
        b64 = _get(dados, "nfseXmlGZipB64")
        if not b64:
            raise SefinError(
                TipoErro.RESPOSTA_INVALIDA, "Resposta da Sefin sem o XML da NFS-e", corpo=str(dados)[:20000]
            )
        xml = ungzip_b64(b64)
        self._arquivo(op, "nfse", "xml", xml)
        return RespostaEmissao(
            chave_acesso=_get(dados, "chaveAcesso"),
            nfse_xml=xml,
            id_dps=_get(dados, "idDps"),
            alertas=extrair_alertas(dados),
            data_processamento=_get(dados, "dataHoraProcessamento"),
            bruto={k: v for k, v in dados.items() if k.lower() != "nfsexmlgzipb64"},
        )

    def _lote_adn(self, resp: httpx.Response, op: str) -> LoteDistribuicao:
        """Resposta ``LoteDistribuicaoNSUResponse`` do ADN.

        O ADN responde 404 (nada encontrado) e 400 (rejeição) **com** o corpo normal; o que
        vale é ``StatusProcessamento``. Sem esse campo, a resposta não veio do serviço.
        """
        dados = self._json(resp, op, registrar=False)
        self._arquivo(op, "resp", "json", _sem_arquivos(dados, resp.content))
        status = _get(dados, "StatusProcessamento")
        if not isinstance(status, str):
            if resp.status_code < 400:
                raise SefinError(
                    TipoErro.RESPOSTA_INVALIDA,
                    f"Resposta inesperada do Ambiente Nacional (HTTP {resp.status_code})",
                    corpo=resp.text[:20000],
                )
            raise self._erro_http(resp, dados, "O Ambiente Nacional (ADN) recusou a consulta")
        if status.upper() == "REJEICAO":
            raise SefinError(
                TipoErro.REJEICAO,
                "O Ambiente Nacional (ADN) recusou a consulta",
                extrair_mensagens(_get(dados, "Erros") or []),
                http_status=resp.status_code,
                corpo=resp.text[:20000],
            )
        docs = []
        for item in _get(dados, "LoteDFe") or []:
            nsu = _get(item, "NSU")
            docs.append(
                DocumentoDistribuido(
                    nsu=int(nsu) if nsu is not None else None,
                    chave_acesso=_get(item, "ChaveAcesso"),
                    tipo_documento=str(_get(item, "TipoDocumento") or "").upper(),
                    tipo_evento=(str(_get(item, "TipoEvento")).upper() if _get(item, "TipoEvento") else None),
                    xml=_decodificar_xml(_get(item, "ArquivoXml")),
                    data_geracao=_get(item, "DataHoraGeracao"),
                )
            )
        return LoteDistribuicao(status.upper(), docs, extrair_mensagens(_get(dados, "Alertas") or []))

    def _arquivo(self, op: str, parte: str, ext: str, conteudo: bytes) -> None:
        try:
            ctx = get_context()
            pasta = Path(config.SEFIN_LOG_DIR) / str(ctx.get("emissao_id") or f"avulso-{datetime.now():%Y%m%d}")
            pasta.mkdir(parents=True, exist_ok=True)
            nome = f"{datetime.now():%H%M%S_%f}_{self.ambiente.name.lower()}_{op}_{parte}.{ext}"
            (pasta / nome).write_bytes(conteudo)
        except OSError:
            log.exception("não foi possível gravar arquivo de log da Sefin")


def _decodificar_xml(valor: Any) -> bytes | None:
    """``ArquivoXml`` do ADN: gzip+base64 (documentado); tolera base64 puro ou XML em texto."""
    if not valor or not isinstance(valor, str):
        return None
    if valor.lstrip().startswith("<"):
        return valor.encode("utf-8")
    try:
        bruto = base64.b64decode(valor)
    except ValueError:
        return None
    if bruto[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(bruto)
        except OSError:
            return None
    return bruto if bruto.lstrip().startswith(b"<") else None


def _sem_arquivos(dados: Any, original: bytes) -> bytes:
    """Cópia da resposta do ADN para o log sem os XMLs embutidos (que vão para data/xml)."""
    if not isinstance(dados, dict):
        return original
    copia = dict(dados)
    for k, v in copia.items():
        if k.lower() == "lotedfe" and isinstance(v, list):
            copia[k] = [
                {
                    kk: (f"<{len(vv)} caracteres omitidos>" if kk.lower() == "arquivoxml" and vv else vv)
                    for kk, vv in item.items()
                }
                if isinstance(item, dict)
                else item
                for item in v
            ]
    return json.dumps(copia, ensure_ascii=False, indent=1).encode("utf-8")


def _get(dados: Any, chave: str) -> Any:
    if not isinstance(dados, dict):
        return None
    for k, v in dados.items():
        if k.lower() == chave.lower():
            return v
    return None
