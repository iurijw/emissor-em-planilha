"""Cliente REST da Sefin Nacional (mTLS com certificado A1).

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

    def _json(self, resp: httpx.Response, op: str) -> Any:
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

    def _arquivo(self, op: str, parte: str, ext: str, conteudo: bytes) -> None:
        try:
            ctx = get_context()
            pasta = Path(config.SEFIN_LOG_DIR) / str(ctx.get("emissao_id") or f"avulso-{datetime.now():%Y%m%d}")
            pasta.mkdir(parents=True, exist_ok=True)
            nome = f"{datetime.now():%H%M%S_%f}_{self.ambiente.name.lower()}_{op}_{parte}.{ext}"
            (pasta / nome).write_bytes(conteudo)
        except OSError:
            log.exception("não foi possível gravar arquivo de log da Sefin")


def _get(dados: Any, chave: str) -> Any:
    if not isinstance(dados, dict):
        return None
    for k, v in dados.items():
        if k.lower() == chave.lower():
            return v
    return None
