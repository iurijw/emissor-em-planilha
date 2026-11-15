"""Webhooks de saída: avisam outro sistema quando uma nota muda de situação ou um lote termina.

- Cada webhook é de uma empresa e de um ambiente, com os eventos que quer receber (``EVENTOS``).
- O evento vira uma linha de ``entrega_webhook`` na mesma hora (o corpo fica gravado) e uma thread entrega em
  segundo plano: ``POST`` JSON com ``X-Emissor-Evento``, ``X-Emissor-Entrega`` (id único; o recebedor deduplica por
  ele) e ``X-Emissor-Assinatura: t=<unix>,v1=<hex>``, onde ``v1 = HMAC-SHA256(segredo, f"{t}.{corpo}")``.
- Resposta 2xx = entregue. Qualquer outra coisa (ou sem resposta em ``TIMEOUT``) tenta de novo depois de
  ``ESPERAS`` (1 min a 24 h); esgotadas as tentativas, a entrega fica ``falhou`` (dá para reenviar pela tela).
- Redirecionamentos não são seguidos. Na hospedagem (``FLY_APP_NAME``) só ``https`` e nunca endereços da rede
  interna (conferido no cadastro e a cada entrega), para um webhook não servir de ponte para dentro da máquina.
  Na instalação própria ``http`` e a rede local valem (o sistema integrado costuma estar no mesmo escritório).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import time
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlmodel import select

from emissor import config, empresas, security
from emissor.db import Configuracao, Emissao, EntregaWebhook, Lote, Webhook, agora, sessao
from emissor.nfse.constants import Ambiente

log = logging.getLogger(__name__)

EVENTOS = {
    "nota.autorizada": "Nota autorizada pela Sefin",
    "nota.rejeitada": "Nota recusada (pela Sefin ou pela conferência do sistema)",
    "nota.erro": "Nota sem resposta confirmada da Sefin (pode precisar de verificação)",
    "nota.cancelada": "Nota cancelada (pelo sistema, pelo portal ou pela prefeitura)",
    "nota.substituida": "Nota substituída por outra",
    "lote.concluido": "Lote terminou (concluído, interrompido ou cancelado)",
}
ESPERAS = [60, 300, 1800, 7200, 21600, 86400]  # segundos antes de cada nova tentativa
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_WEBHOOKS_POR_EMPRESA = 10
GUARDAR_ENTREGAS = timedelta(days=30)
NOME_AMBIENTE = {Ambiente.PRODUCAO.value: "producao", Ambiente.HOMOLOGACAO.value: "homologacao"}


class WebhookError(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _hospedagem() -> bool:
    return bool(os.environ.get("FLY_APP_NAME"))


def _endereco_proibido(ip: str) -> bool:
    try:
        end = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return not end.is_global


def conferir_url(url: str, *, resolver: bool = True) -> str:
    """URL aceita para um webhook (erro com a explicação se não for)."""
    url = (url or "").strip()
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise WebhookError("Informe o endereço completo, começando com https://.")
    if partes.username or partes.password:
        raise WebhookError("O endereço não pode ter usuário e senha; use o segredo da assinatura para conferir.")
    if len(url) > 500:
        raise WebhookError("Endereço longo demais (até 500 caracteres).")
    if _hospedagem():
        if partes.scheme != "https":
            raise WebhookError("Use um endereço https://.")
        if resolver:
            try:
                ips = {i[4][0] for i in socket.getaddrinfo(partes.hostname, partes.port or 443)}
            except OSError as exc:
                raise WebhookError(f"Não foi possível encontrar o endereço {partes.hostname}.") from exc
            if any(_endereco_proibido(ip) for ip in ips):
                raise WebhookError("O endereço aponta para uma rede interna, o que não é permitido na hospedagem.")
    return url


def webhook_dict(w: Webhook) -> dict[str, Any]:
    return {
        "id": w.id,
        "url": w.url,
        "eventos": w.eventos.split(),
        "ambiente": NOME_AMBIENTE.get(w.ambiente, w.ambiente),
        "descricao": w.descricao,
        "ativo": w.ativo,
        "criado_por": w.criado_por,
        "criado_em": w.criado_em.isoformat() if w.criado_em else None,
        "falhas_seguidas": w.falhas_seguidas,
    }


def entrega_dict(en: EntregaWebhook) -> dict[str, Any]:
    return {
        "id": en.id,
        "webhook_id": en.webhook_id,
        "evento": en.evento,
        "status": en.status,
        "tentativas": en.tentativas,
        "proxima_em": en.proxima_em.isoformat() if en.status == "pendente" and en.proxima_em else None,
        "ultimo_status": en.ultimo_status,
        "ultimo_erro": en.ultimo_erro,
        "criado_em": en.criado_em.isoformat() if en.criado_em else None,
        "entregue_em": en.entregue_em.isoformat() if en.entregue_em else None,
    }


def criar(
    empresa_id: int, url: str, eventos: list[str], ambiente: str, descricao: str = "", criado_por: str | None = None
) -> tuple[str, dict[str, Any]]:
    """Cadastra o webhook e devolve ``(segredo, dados)``. O segredo só é mostrado agora."""
    url = conferir_url(url)
    eventos = [e for e in EVENTOS if e in set(eventos or [])]
    if not eventos:
        raise WebhookError("Escolha ao menos um evento: " + ", ".join(EVENTOS) + ".")
    if ambiente not in NOME_AMBIENTE:
        raise WebhookError("Ambiente inválido.")
    with sessao() as s:
        n = len(s.exec(select(Webhook).where(Webhook.empresa_id == empresa_id)).all())
        if n >= MAX_WEBHOOKS_POR_EMPRESA:
            raise WebhookError(f"Esta empresa já tem {MAX_WEBHOOKS_POR_EMPRESA} webhooks. Remova os que não usa.")
        segredo = "whsec_" + secrets.token_urlsafe(32)
        w = Webhook(
            empresa_id=empresa_id,
            url=url,
            eventos=" ".join(eventos),
            ambiente=ambiente,
            segredo_enc=security.encrypt(segredo.encode()),
            descricao=(descricao or "").strip()[:200],
            criado_por=criado_por,
        )
        s.add(w)
        s.commit()
        s.refresh(w)
        return segredo, webhook_dict(w)


def listar(empresa_id: int) -> list[dict[str, Any]]:
    with sessao() as s:
        return [
            webhook_dict(w)
            for w in s.exec(select(Webhook).where(Webhook.empresa_id == empresa_id).order_by(Webhook.id))
        ]


def _webhook(s, empresa_id: int, webhook_id: int) -> Webhook:
    w = s.get(Webhook, webhook_id)
    if w is None or w.empresa_id != empresa_id:
        raise WebhookError("Webhook não encontrado.", 404)
    return w


def alterar(empresa_id: int, webhook_id: int, *, ativo: bool | None = None) -> dict[str, Any]:
    with sessao() as s:
        w = _webhook(s, empresa_id, webhook_id)
        if ativo is not None:
            w.ativo = ativo
            if ativo:
                w.falhas_seguidas = 0
        s.add(w)
        s.commit()
        s.refresh(w)
        return webhook_dict(w)


def remover(empresa_id: int, webhook_id: int) -> Webhook:
    with sessao() as s:
        w = _webhook(s, empresa_id, webhook_id)
        for en in s.exec(select(EntregaWebhook).where(EntregaWebhook.webhook_id == w.id)).all():
            s.delete(en)
        s.delete(w)
        s.commit()
        return w


def entregas(empresa_id: int, webhook_id: int, limite: int = 50) -> list[dict[str, Any]]:
    with sessao() as s:
        _webhook(s, empresa_id, webhook_id)
        q = (
            select(EntregaWebhook)
            .where(EntregaWebhook.webhook_id == webhook_id)
            .order_by(EntregaWebhook.criado_em.desc())  # type: ignore[union-attr]
            .limit(max(1, min(limite, 200)))
        )
        return [entrega_dict(en) for en in s.exec(q)]


def reenviar(empresa_id: int, entrega_id: str) -> dict[str, Any]:
    with sessao() as s:
        en = s.get(EntregaWebhook, entrega_id)
        if en is None or en.empresa_id != empresa_id:
            raise WebhookError("Entrega não encontrada.", 404)
        en.status = "pendente"
        en.proxima_em = agora()
        s.add(en)
        s.commit()
        s.refresh(en)
        d = entrega_dict(en)
    entregador().acordar()
    return d


# --- montagem dos eventos ---------------------------------------------------------------------------


def nota_publica(e: Emissao) -> dict[str, Any]:
    """A nota como a API ``/api/v1`` mostra (também é o ``dados`` dos eventos ``nota.*``)."""
    erro = e.erro()
    return {
        "id": e.id,
        "referencia_externa": e.referencia_externa,
        "status": e.status,
        "ambiente": NOME_AMBIENTE.get(e.ambiente, e.ambiente),
        "lote_id": e.lote_id,
        "tomador": {"documento": e.tomador_documento, "nome": e.tomador_nome},
        "valor": e.valor,
        "descricao": e.descricao,
        "numero_nfse": e.numero_nfse,
        "chave_acesso": e.chave_acesso,
        "serie": e.serie,
        "n_dps": e.n_dps,
        "dh_emissao": e.dh_emissao.isoformat() if e.dh_emissao else None,
        "dh_processamento": e.dh_processamento,
        "tributacao": e.tributacao_nome or ("Só desta nota" if e.tributacao_json else None),
        "erro": {"resumo": erro.get("resumo"), "mensagens": erro.get("mensagens", [])} if erro else None,
        "alertas": e.alertas(),
        "origem": e.origem or "tela",
        "criado_em": e.criado_em.isoformat() if e.criado_em else None,
        "atualizado_em": e.atualizado_em.isoformat() if e.atualizado_em else None,
    }


def lote_publico(lote: Lote, emissoes: list[Emissao]) -> dict[str, Any]:
    contagem: dict[str, int] = {}
    for e in emissoes:
        contagem[e.status] = contagem.get(e.status, 0) + 1
    return {
        "id": lote.id,
        "status": lote.status,
        "ambiente": NOME_AMBIENTE.get(lote.ambiente, lote.ambiente),
        "total": lote.total,
        "contagem": contagem,
        "motivo_interrupcao": lote.motivo_interrupcao,
        "criado_em": lote.criado_em.isoformat() if lote.criado_em else None,
        "finalizado_em": lote.finalizado_em.isoformat() if lote.finalizado_em else None,
        "notas": [{"id": e.id, "referencia_externa": e.referencia_externa, "status": e.status} for e in emissoes],
    }


def _registrar(empresa_id: int, ambiente: str, evento: str, dados: dict[str, Any]) -> int:
    with sessao() as s:
        alvos = [
            w
            for w in s.exec(
                select(Webhook).where(
                    Webhook.empresa_id == empresa_id,
                    Webhook.ativo == True,  # noqa: E712
                    Webhook.ambiente == ambiente,
                )
            )
            if evento in w.eventos.split()
        ]
        if not alvos:
            return 0
        emp = s.get(Configuracao, empresa_id)
        for w in alvos:
            eid = "ent_" + uuid.uuid4().hex
            corpo = {
                "id": eid,
                "evento": evento,
                "criado_em": agora().isoformat(),
                "empresa": {"id": empresa_id, "cnpj": emp.cnpj if emp else None},
                "dados": dados,
            }
            s.add(
                EntregaWebhook(
                    id=eid,
                    webhook_id=w.id,
                    empresa_id=empresa_id,
                    evento=evento,
                    corpo=json.dumps(corpo, ensure_ascii=False, separators=(",", ":")),
                )
            )
        s.commit()
    entregador().acordar()
    return len(alvos)


def notificar_emissao(emissao_id: int, evento: str) -> None:
    """Chamado depois que a situação da nota foi gravada. Nunca levanta erro (o aviso não pode atrapalhar a nota)."""
    try:
        with sessao() as s:
            q = select(Emissao).where(Emissao.id == emissao_id).execution_options(todas_empresas=True)
            e = s.exec(q).first()
            if e is None:
                return
            dados = nota_publica(e)
        _registrar(e.empresa_id or 1, e.ambiente, evento, dados)
    except Exception:
        log.exception("falha ao registrar o webhook", extra={"emissao_id": emissao_id, "evento": evento})


def notificar_lote(lote_id: str) -> None:
    try:
        with sessao() as s:
            lote = s.exec(select(Lote).where(Lote.id == lote_id).execution_options(todas_empresas=True)).first()
            if lote is None:
                return
            q = select(Emissao).where(Emissao.lote_id == lote_id).order_by(Emissao.id)
            emissoes = list(s.exec(q.execution_options(todas_empresas=True)))
            dados = lote_publico(lote, emissoes)
        _registrar(lote.empresa_id or 1, lote.ambiente, "lote.concluido", dados)
    except Exception:
        log.exception("falha ao registrar o webhook do lote", extra={"lote_id": lote_id})


# --- entrega ----------------------------------------------------------------------------------------


def assinar(segredo: str, instante: int, corpo: str) -> str:
    return hmac.new(segredo.encode(), f"{instante}.{corpo}".encode(), hashlib.sha256).hexdigest()


def _cabecalhos(segredo: str, evento: str, entrega_id: str, corpo: str) -> dict[str, str]:
    t = int(time.time())
    return {
        "Content-Type": "application/json",
        "User-Agent": f"EmissorEmPlanilha/{config.APP_VERSION} (webhook)",
        "X-Emissor-Evento": evento,
        "X-Emissor-Entrega": entrega_id,
        "X-Emissor-Assinatura": f"t={t},v1={assinar(segredo, t, corpo)}",
    }


def _enviar(http: httpx.Client, w: Webhook, evento: str, entrega_id: str, corpo: str) -> tuple[int | None, str | None]:
    """(status HTTP, erro). Status 2xx com erro None = entregue."""
    try:
        conferir_url(w.url)
    except WebhookError as exc:
        return None, exc.mensagem
    segredo = security.decrypt(w.segredo_enc).decode()
    try:
        r = http.post(w.url, content=corpo.encode(), headers=_cabecalhos(segredo, evento, entrega_id, corpo))
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]
    if 200 <= r.status_code < 300:
        return r.status_code, None
    return r.status_code, f"Resposta HTTP {r.status_code}: {r.text[:200]}"


def testar(empresa_id: int, webhook_id: int, http: httpx.Client | None = None) -> dict[str, Any]:
    """Envia um evento ``teste`` na hora (sem gravar entrega) e diz o que o endereço respondeu."""
    with sessao() as s:
        w = _webhook(s, empresa_id, webhook_id)
        emp = s.get(Configuracao, empresa_id)
    eid = "ent_teste_" + uuid.uuid4().hex[:12]
    corpo = json.dumps(
        {
            "id": eid,
            "evento": "teste",
            "criado_em": agora().isoformat(),
            "empresa": {"id": empresa_id, "cnpj": emp.cnpj if emp else None},
            "dados": {"mensagem": "Teste do webhook do Emissor em Planilha."},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    dono = http is None
    http = http or entregador().cliente()
    try:
        status, erro = _enviar(http, w, "teste", eid, corpo)
    finally:
        if dono:
            http.close()
    return {"ok": erro is None, "status": status, "erro": erro}


class Entregador:
    """Thread que entrega as ``entrega_webhook`` pendentes (uma por vez, na ordem)."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        self._ultima_limpeza = 0.0

    def cliente(self) -> httpx.Client:
        return httpx.Client(timeout=TIMEOUT, follow_redirects=False, transport=self.transport)

    def iniciar(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="webhooks", daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        self._acordar.set()

    def _loop(self) -> None:
        while True:
            self._acordar.wait(timeout=15)
            self._acordar.clear()
            try:
                self.processar_pendentes()
                if time.monotonic() - self._ultima_limpeza > 6 * 3600:
                    self._ultima_limpeza = time.monotonic()
                    limpar_antigas()
            except Exception:
                log.exception("falha no envio de webhooks")
                time.sleep(5)

    def processar_pendentes(self, limite: int = 50) -> int:
        with sessao() as s:
            q = (
                select(EntregaWebhook)
                .where(EntregaWebhook.status == "pendente", EntregaWebhook.proxima_em <= agora())
                .order_by(EntregaWebhook.criado_em)  # type: ignore[arg-type]
                .limit(limite)
            )
            ids = [en.id for en in s.exec(q)]
        if not ids:
            return 0
        with self.cliente() as http:
            for eid in ids:
                self._entregar(http, eid)
        return len(ids)

    def _entregar(self, http: httpx.Client, entrega_id: str) -> None:
        with sessao() as s:
            en = s.get(EntregaWebhook, entrega_id)
            w = s.get(Webhook, en.webhook_id) if en else None
        if en is None or en.status != "pendente":
            return
        if w is None or not w.ativo:
            status, erro = None, "Webhook removido ou desativado."
        else:
            with empresas.usar(en.empresa_id):
                status, erro = _enviar(http, w, en.evento, en.id, en.corpo)
        with sessao() as s:
            en = s.get(EntregaWebhook, entrega_id)
            w = s.get(Webhook, en.webhook_id)
            en.tentativas += 1
            en.ultimo_status = status
            en.ultimo_erro = erro
            if erro is None:
                en.status = "entregue"
                en.entregue_em = agora()
            elif w is None or not w.ativo or en.tentativas > len(ESPERAS):
                en.status = "falhou"
            else:
                en.proxima_em = agora() + timedelta(seconds=ESPERAS[en.tentativas - 1])
            if w is not None:
                w.falhas_seguidas = 0 if erro is None else w.falhas_seguidas + 1
                s.add(w)
            s.add(en)
            s.commit()
        nivel = logging.INFO if erro is None else logging.WARNING
        log.log(
            nivel,
            "webhook %s: %s",
            "entregue" if erro is None else "falhou",
            en.evento,
            extra={"entrega": entrega_id, "webhook_id": en.webhook_id, "status": status, "erro": erro},
        )


def limpar_antigas() -> int:
    limite = agora() - GUARDAR_ENTREGAS
    with sessao() as s:
        antigas = s.exec(
            select(EntregaWebhook).where(EntregaWebhook.status != "pendente", EntregaWebhook.criado_em < limite)
        ).all()
        for en in antigas:
            s.delete(en)
        s.commit()
    return len(antigas)


_entregador: Entregador | None = None


def entregador() -> Entregador:
    global _entregador
    if _entregador is None:
        _entregador = Entregador()
    return _entregador


def definir_entregador(e: Entregador | None) -> None:
    """Injeção para testes."""
    global _entregador
    _entregador = e
