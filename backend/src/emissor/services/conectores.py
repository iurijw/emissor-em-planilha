"""Conectores de entrada: um sistema de cobrança avisa um pagamento e o emissor faz a nota sozinho.

Primeiro (e por enquanto único) conector: **Asaas**. Como funciona:

- O administrador cadastra a chave da API do Asaas (para ler o cliente da cobrança) e escolhe o ambiente da NFS-e,
  a tributação, a descrição e quando emitir. O emissor devolve o endereço do webhook
  (``/api/v1/entrada/asaas/<caminho>``) e um token, mostrado uma vez, que vai no campo "Token de autenticação" do
  webhook no Asaas (ele o manda no cabeçalho ``asaas-access-token``; o Asaas não assina o corpo).
- A rota de entrada confere o token, **grava o aviso e responde 200 na hora** (o Asaas pausa a fila depois de 15
  falhas seguidas e entrega o mesmo evento mais de uma vez). Uma thread processa os avisos gravados.
- ``PAYMENT_RECEIVED`` (gatilho "recebido": dinheiro disponível) ou também ``PAYMENT_CONFIRMED`` (gatilho
  "confirmado": pago, ainda não disponível; cartão de crédito confirma no dia e só fica disponível ~32 dias depois).
  Pix e dinheiro só têm ``PAYMENT_RECEIVED``. Estornos e outros eventos ficam registrados, sem nota e sem cancelar
  nada (quem decide cancelar é a pessoa).
- Contra duplicidade: o aviso tem id próprio (``evt_...``) e a nota leva ``referencia_externa = id da cobrança``
  (``pay_...``): confirmado e depois recebido da mesma cobrança fazem uma nota só.
- O cliente vem de ``GET /v3/customers/{id}``: CPF/CNPJ, nome, e-mail e endereço vão para o cadastro (cliente que já
  existe só tem os campos vazios completados: o cadastro do emissor prevalece). Cliente sem CPF/CNPJ ou estrangeiro
  vira erro no aviso (pode ser emitido pela nota avulsa).
- Chave do sandbox do Asaas só com NFS-e em homologação (cobrança de teste nunca vira nota de verdade).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from sqlmodel import select

from emissor import auth, config, empresas, security
from emissor.db import (
    Cliente,
    ConectorEntrada,
    EventoEntrada,
    Tributacao,
    agora,
    obter_cliente,
    obter_config,
    sessao,
)
from emissor.nfse import codes
from emissor.nfse.constants import Ambiente
from emissor.nfse.models import normalizar_documento, somente_digitos
from emissor.services import clientes as clientes_service

log = logging.getLogger(__name__)

TIPOS = {"asaas": "Asaas"}
URL_ASAAS = {"producao": "https://api.asaas.com/v3", "sandbox": "https://api-sandbox.asaas.com/v3"}
GATILHOS = {
    "recebido": ("PAYMENT_RECEIVED",),
    "confirmado": ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"),
}
DESCRICAO_PADRAO = "{cobranca}"
NOME_AMBIENTE = {Ambiente.PRODUCAO.value: "producao", Ambiente.HOMOLOGACAO.value: "homologacao"}
AMBIENTES = {v: k for k, v in NOME_AMBIENTE.items()}
ESPERAS = [60, 300, 1800, 7200]  # Asaas fora do ar: novas tentativas de ler o cliente
MAX_CONECTORES_POR_EMPRESA = 5
MAX_CORPO = 256 * 1024
GUARDAR_EVENTOS = timedelta(days=90)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)

_transporte: httpx.BaseTransport | None = None


class ConectorError(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


class _Temporario(Exception):
    """Falha que vale tentar de novo depois (Asaas fora do ar, tempo esgotado)."""


def definir_transporte(t: httpx.BaseTransport | None) -> None:
    """Injeção para testes (Asaas simulado)."""
    global _transporte
    _transporte = t


def _sha256(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


# --- API do Asaas ----------------------------------------------------------------------------------------------


def _ambiente_da_chave(chave: str) -> str | None:
    if chave.startswith("$aact_prod_"):
        return "producao"
    if chave.startswith("$aact_hmlg_"):
        return "sandbox"
    return None


def _asaas(chave: str, origem: str, caminho: str) -> dict[str, Any]:
    """GET na API do Asaas. 401 vira ``ConectorError``; rede/5xx/429 viram ``_Temporario``."""
    url = URL_ASAAS[origem] + caminho
    cab = {"access_token": chave, "User-Agent": f"EmissorEmPlanilha/{config.APP_VERSION}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transporte, follow_redirects=False) as http:
            r = http.get(url, headers=cab)
    except httpx.HTTPError as exc:
        raise _Temporario(f"Sem resposta do Asaas ({exc.__class__.__name__}).") from exc
    if r.status_code == 401:
        raise ConectorError(
            "O Asaas recusou a chave da API (inválida, revogada ou de outro ambiente). Cadastre a chave de novo."
        )
    if r.status_code == 429 or r.status_code >= 500:
        raise _Temporario(f"Asaas respondeu {r.status_code}.")
    try:
        dados = r.json()
    except ValueError:
        dados = {}
    if r.status_code == 404:
        raise ConectorError("O Asaas não encontrou o cliente da cobrança (excluído?).")
    if r.status_code >= 400:
        erros = "; ".join(e.get("description", "") for e in dados.get("errors", []) if isinstance(e, dict))
        raise ConectorError(f"O Asaas recusou a consulta ({r.status_code}): {erros or r.text[:200]}")
    return dados if isinstance(dados, dict) else {}


def conferir_chave(chave: str, origem: str) -> None:
    try:
        _asaas(chave, origem, "/customers?limit=1")
    except _Temporario as exc:
        raise ConectorError(f"Não foi possível falar com o Asaas para conferir a chave: {exc}", 502) from exc


# --- cadastro ----------------------------------------------------------------------------------------------------


def conector_dict(c: ConectorEntrada) -> dict[str, Any]:
    with sessao() as s:
        t = s.get(Tributacao, c.tributacao_id) if c.tributacao_id else None
        q = select(EventoEntrada).where(EventoEntrada.conector_id == c.id, EventoEntrada.status == "erro")
        erros = len(s.exec(q).all())
    return {
        "id": c.id,
        "tipo": c.tipo,
        "nome": c.nome,
        "ambiente": NOME_AMBIENTE.get(c.ambiente, c.ambiente),
        "ambiente_origem": c.ambiente_origem,
        "caminho": f"/api/v1/entrada/{c.tipo}/{c.caminho}",
        "gatilho": c.gatilho,
        "tributacao_id": c.tributacao_id,
        "tributacao_nome": t.nome if t else None,
        "descricao": c.descricao,
        "ativo": c.ativo,
        "criado_por": c.criado_por,
        "criado_em": c.criado_em.isoformat() if c.criado_em else None,
        "ultimo_evento_em": c.ultimo_evento_em.isoformat() if c.ultimo_evento_em else None,
        "eventos_com_erro": erros,
    }


def evento_dict(e: EventoEntrada) -> dict[str, Any]:
    return {
        "id": e.id,
        "evento": e.evento,
        "pagamento_id": e.pagamento_id,
        "valor": e.valor,
        "status": e.status,
        "mensagem": e.mensagem,
        "emissao_id": e.emissao_id,
        "tentativas": e.tentativas,
        "recebido_em": e.recebido_em.isoformat() if e.recebido_em else None,
        "processado_em": e.processado_em.isoformat() if e.processado_em else None,
    }


def _conferir_opcoes(s, gatilho: str, tributacao_id: int | None, descricao: str) -> str:
    if gatilho not in GATILHOS:
        raise ConectorError("Escolha quando emitir: recebido ou confirmado.")
    if tributacao_id is not None and s.get(Tributacao, tributacao_id) is None:
        raise ConectorError("Tributação não encontrada.")
    descricao = (descricao or "").strip()
    if len(descricao) > 1000:
        raise ConectorError("A descrição deve ter até 1.000 caracteres.")
    return descricao


def criar(
    empresa_id: int,
    *,
    chave: str,
    ambiente: str,
    nome: str = "",
    ambiente_origem: str | None = None,
    gatilho: str = "recebido",
    tributacao_id: int | None = None,
    descricao: str = "",
    criado_por: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Cadastra o conector (conferindo a chave no Asaas) e devolve ``(token, dados)``. O token só aparece agora."""
    chave = (chave or "").strip()
    if not chave or len(chave) > 300 or " " in chave:
        raise ConectorError("Cole a chave da API do Asaas (Integrações → Chaves de API, no Asaas).")
    if ambiente not in AMBIENTES:
        raise ConectorError("Ambiente da NFS-e deve ser producao ou homologacao.")
    pela_chave = _ambiente_da_chave(chave)
    origem = ambiente_origem or pela_chave
    if origem not in URL_ASAAS:
        raise ConectorError("Diga se a chave é da conta de produção ou do sandbox do Asaas.")
    if pela_chave and origem != pela_chave:
        raise ConectorError(f"Esta chave é do {'sandbox' if pela_chave == 'sandbox' else 'Asaas de produção'}.")
    if origem == "sandbox" and ambiente != "homologacao":
        raise ConectorError("Chave do sandbox do Asaas só emite em homologação: cobrança de teste não vira nota real.")
    with sessao() as s:
        descricao = _conferir_opcoes(s, gatilho, tributacao_id, descricao)
        n = len(s.exec(select(ConectorEntrada).where(ConectorEntrada.empresa_id == empresa_id)).all())
        if n >= MAX_CONECTORES_POR_EMPRESA:
            raise ConectorError(f"Esta empresa já tem {MAX_CONECTORES_POR_EMPRESA} conectores. Remova os que não usa.")
    conferir_chave(chave, origem)
    token = secrets.token_urlsafe(32)
    with sessao() as s:
        c = ConectorEntrada(
            empresa_id=empresa_id,
            tipo="asaas",
            nome=(nome or "").strip()[:80] or "Asaas",
            ambiente=AMBIENTES[ambiente],
            caminho=secrets.token_urlsafe(18),
            token_hash=_sha256(token),
            chave_enc=security.encrypt(chave.encode()),
            ambiente_origem=origem,
            gatilho=gatilho,
            tributacao_id=tributacao_id,
            descricao=descricao,
            criado_por=criado_por,
        )
        s.add(c)
        s.commit()
        s.refresh(c)
    return token, conector_dict(c)


def _conector(s, empresa_id: int, conector_id: int) -> ConectorEntrada:
    c = s.get(ConectorEntrada, conector_id)
    if c is None or c.empresa_id != empresa_id:
        raise ConectorError("Conector não encontrado.", 404)
    return c


def listar(empresa_id: int) -> list[dict[str, Any]]:
    with sessao() as s:
        itens = s.exec(
            select(ConectorEntrada).where(ConectorEntrada.empresa_id == empresa_id).order_by(ConectorEntrada.id)
        ).all()
    return [conector_dict(c) for c in itens]


def alterar(empresa_id: int, conector_id: int, mudancas: dict[str, Any]) -> dict[str, Any]:
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        gatilho = mudancas.get("gatilho") or c.gatilho
        trib = mudancas["tributacao_id"] if "tributacao_id" in mudancas else c.tributacao_id
        valores = {
            "gatilho": gatilho,
            "tributacao_id": trib,
            "descricao": _conferir_opcoes(s, gatilho, trib, mudancas.get("descricao", c.descricao)),
        }
        if mudancas.get("ativo") is not None:
            valores["ativo"] = bool(mudancas["ativo"])
        if mudancas.get("nome"):
            valores["nome"] = str(mudancas["nome"]).strip()[:80] or c.nome
        origem = c.ambiente_origem
    chave = (mudancas.get("chave") or "").strip()
    if chave:
        pela_chave = _ambiente_da_chave(chave)
        if pela_chave and pela_chave != origem:
            raise ConectorError("A chave nova é de outro ambiente do Asaas. Cadastre outro conector.")
        conferir_chave(chave, origem)
        valores["chave_enc"] = security.encrypt(chave.encode())
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        for campo, valor in valores.items():
            setattr(c, campo, valor)
        s.add(c)
        s.commit()
        s.refresh(c)
    return conector_dict(c)


def novo_token(empresa_id: int, conector_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        c.token_hash = _sha256(token)
        s.add(c)
        s.commit()
    return token


def remover(empresa_id: int, conector_id: int) -> ConectorEntrada:
    with sessao() as s:
        c = _conector(s, empresa_id, conector_id)
        for e in s.exec(select(EventoEntrada).where(EventoEntrada.conector_id == c.id)):
            s.delete(e)
        s.delete(c)
        s.commit()
    return c


def eventos(empresa_id: int, conector_id: int, limite: int = 50) -> list[dict[str, Any]]:
    with sessao() as s:
        _conector(s, empresa_id, conector_id)
        itens = s.exec(
            select(EventoEntrada)
            .where(EventoEntrada.conector_id == conector_id)
            .order_by(EventoEntrada.recebido_em.desc())  # type: ignore[union-attr]
            .limit(limite)
        ).all()
    return [evento_dict(e) for e in itens]


def reprocessar(empresa_id: int, evento_id: str) -> dict[str, Any]:
    with sessao() as s:
        e = s.get(EventoEntrada, evento_id)
        if e is None or e.empresa_id != empresa_id:
            raise ConectorError("Aviso não encontrado.", 404)
        if e.status not in ("erro", "ignorado"):
            raise ConectorError("Só um aviso com erro (ou ignorado) pode ser processado de novo.")
        e.status, e.mensagem, e.tentativas, e.proxima_em = "pendente", None, 0, agora()
        s.add(e)
        s.commit()
        s.refresh(e)
    processador().acordar()
    return evento_dict(e)


# --- entrada (rota pública) ---------------------------------------------------------------------------------------


def receber(tipo: str, caminho: str, token: str | None, corpo: bytes) -> dict[str, Any]:
    """Grava o aviso do Asaas e devolve na hora (o processamento é em segundo plano)."""
    if tipo not in TIPOS:
        raise ConectorError("Conector não encontrado.", 404)
    with sessao() as s:
        c = s.exec(
            select(ConectorEntrada).where(ConectorEntrada.caminho == caminho, ConectorEntrada.tipo == tipo)
        ).first()
    if c is None:
        raise ConectorError("Conector não encontrado.", 404)
    if not token or not hmac.compare_digest(c.token_hash, _sha256(token)):
        log.warning("aviso do conector com token errado", extra={"conector_id": c.id})
        raise ConectorError("Token de autenticação inválido (confira o campo no webhook do Asaas).", 401)
    if len(corpo) > MAX_CORPO:
        raise ConectorError("Aviso grande demais.", 413)
    try:
        dados = json.loads(corpo)
    except ValueError as exc:
        raise ConectorError("O corpo do aviso não é JSON.") from exc
    if not isinstance(dados, dict) or not isinstance(dados.get("event"), str):
        raise ConectorError("Aviso sem o campo event.")
    pagamento = dados.get("payment") if isinstance(dados.get("payment"), dict) else {}
    pid = str(pagamento.get("id") or "")[:60] or None
    evt = str(dados.get("id") or "")[:80] or f"{dados['event']}:{pid or secrets.token_hex(8)}"
    eid = f"{c.id}:{evt}"
    with sessao() as s:
        if s.get(EventoEntrada, eid) is not None:
            return {"ok": True, "repetido": True}
        ativo = c.ativo
        s.add(
            EventoEntrada(
                id=eid,
                conector_id=c.id,
                empresa_id=c.empresa_id,
                evento=dados["event"][:60],
                pagamento_id=pid,
                valor=str(pagamento.get("value"))[:20] if pagamento.get("value") is not None else None,
                corpo=corpo.decode("utf-8", "replace"),
                status="pendente" if ativo else "ignorado",
                mensagem=None if ativo else "Conector desativado quando o aviso chegou.",
            )
        )
        conector = s.get(ConectorEntrada, c.id)
        conector.ultimo_evento_em = agora()
        s.add(conector)
        s.commit()
    if ativo:
        processador().acordar()
    return {"ok": True, "repetido": False}


# --- processamento -----------------------------------------------------------------------------------------------


def _endereco(cli: dict[str, Any]) -> dict[str, Any]:
    cep = somente_digitos(str(cli.get("postalCode") or "")) or ""
    return {
        "cep": cep if len(cep) == 8 else None,
        "logradouro": (cli.get("address") or "").strip() or None,
        "numero": (str(cli.get("addressNumber") or "")).strip() or None,
        "complemento": (cli.get("complement") or "").strip() or None,
        "bairro": (cli.get("province") or "").strip() or None,
        "c_mun": codes.codigo_municipio(cli.get("cityName"), cli.get("state")),
    }


def _cliente(s, cli: dict[str, Any]) -> tuple[str, str]:
    """Grava o cliente do Asaas no cadastro (só completa o que falta) e devolve ``(documento, nome)``."""
    if cli.get("foreignCustomer"):
        raise ConectorError("Cliente estrangeiro no Asaas: emita pela nota avulsa (tomador no exterior).")
    doc = normalizar_documento(str(cli.get("cpfCnpj") or ""))
    if not doc or not clientes_service.documento_valido(doc):
        raise ConectorError("O cliente da cobrança não tem CPF/CNPJ válido no Asaas. Complete lá e processe de novo.")
    nome = (cli.get("name") or "").strip()
    novo = Cliente(
        documento=doc,
        nome=nome,
        email=(cli.get("email") or "").strip() or None,
        fone=somente_digitos(str(cli.get("mobilePhone") or cli.get("phone") or "")) or None,
        inscricao_municipal=(cli.get("municipalInscription") or "").strip() or None,
        origem="asaas",
        **_endereco(cli),
    )
    atual = obter_cliente(s, doc)
    if atual is None:
        novo.atualizado_em = agora()
        s.add(novo)
    elif clientes_service.completar_vazios(atual, novo):
        s.add(atual)
    s.commit()
    return doc, (atual.nome if atual is not None and atual.nome else nome)


def _valor(pagamento: dict[str, Any]) -> str:
    try:
        v = Decimal(str(pagamento.get("value")))
    except (InvalidOperation, TypeError) as exc:
        raise ConectorError("Cobrança sem valor.") from exc
    if v <= 0:
        raise ConectorError("Cobrança com valor zero.")
    return f"{v.quantize(Decimal('0.01'))}"


def _descricao(c: ConectorEntrada, pagamento: dict[str, Any]) -> str:
    cobranca = re.sub(r"\s+", " ", str(pagamento.get("description") or "")).strip()
    # Chaves no texto do Asaas seriam lidas como variáveis (e a nota recusada): ficam como parênteses.
    cobranca = cobranca.replace("{", "(").replace("}", ")")
    modelo = c.descricao or DESCRICAO_PADRAO
    texto = modelo.replace("{cobranca}", cobranca).strip()
    if not texto:
        texto = f"Serviço referente à cobrança {pagamento.get('invoiceNumber') or pagamento.get('id') or ''}".strip()
    return texto


def _processar(evento_id: str) -> None:
    from emissor.services.emissao import LoteError, NotaAvulsa, ReferenciaEmUso, criar_avulsas, notas_da_referencia

    with sessao() as s:
        ev = s.get(EventoEntrada, evento_id)
        c = s.get(ConectorEntrada, ev.conector_id) if ev else None
    if ev is None or ev.status != "pendente":
        return
    status, mensagem, emissao_id, temporario = "erro", None, None, False
    try:
        if c is None or not c.ativo:
            raise _Ignorar("Conector removido ou desativado.")
        if auth.suspenso():
            raise ConectorError("Assinatura suspensa: a nota não foi emitida. Reative e processe o aviso de novo.")
        if ev.evento not in GATILHOS.get(c.gatilho, ()):
            if ev.evento in ("PAYMENT_REFUNDED", "PAYMENT_PARTIALLY_REFUNDED", "PAYMENT_CHARGEBACK_REQUESTED"):
                raise _Ignorar("Estorno no Asaas: a nota não foi cancelada sozinha. Cancele-a se for o caso.")
            raise _Ignorar(f"Evento {ev.evento} não emite nota.")
        dados = json.loads(ev.corpo)
        pagamento = dados.get("payment") or {}
        if pagamento.get("deleted"):
            raise _Ignorar("Cobrança excluída no Asaas.")
        if not ev.pagamento_id or not pagamento.get("customer"):
            raise ConectorError("Aviso sem a cobrança ou o cliente.")
        valor = _valor(pagamento)
        with empresas.usar(c.empresa_id), sessao() as s:
            cfg = obter_config(s)
            if cfg.ambiente != c.ambiente:
                raise ConectorError(
                    f"O conector emite em {NOME_AMBIENTE.get(c.ambiente)}, mas a empresa está em "
                    f"{NOME_AMBIENTE.get(cfg.ambiente)}. Troque o ambiente e processe de novo."
                )
            existentes = notas_da_referencia(s, [ev.pagamento_id], cfg.ambiente)
            if existentes:
                raise ReferenciaEmUso(existentes)
            trib = s.get(Tributacao, c.tributacao_id) if c.tributacao_id else None
            if c.tributacao_id and trib is None:
                raise ConectorError("A tributação do conector foi excluída. Escolha outra no conector.")
            trib_nome, trib_dados = (trib.nome, trib.dados()) if trib else (None, None)
        chave = security.decrypt(c.chave_enc).decode()
        cli = _asaas(chave, c.ambiente_origem, f"/customers/{pagamento['customer']}")
        with empresas.usar(c.empresa_id):
            with sessao() as s:
                doc, nome = _cliente(s, cli)
            nota = NotaAvulsa(
                nome=nome,
                documento=doc,
                valor=valor,
                descricao=_descricao(c, pagamento),
                tributacao_nome=trib_nome,
                tributacao=trib_dados,
                tributacao_id=c.tributacao_id,
            )
            lote = criar_avulsas([nota], referencias=[ev.pagamento_id], origem=f"asaas:{c.id}")
            with sessao() as s:
                from emissor.db import Emissao

                e = s.exec(select(Emissao).where(Emissao.lote_id == lote.id)).first()
                emissao_id = e.id if e else None
            auth.auditar("lote_criado", f"Asaas ({c.nome}): cobrança {ev.pagamento_id}, lote {lote.id}")
        status = "emitida"
    except ReferenciaEmUso as exc:
        status, emissao_id = "repetida", next(iter(exc.existentes.values()), None)
        mensagem = "A cobrança já tem nota."
    except _Ignorar as exc:
        status, mensagem = "ignorado", str(exc)
    except _Temporario as exc:
        mensagem, temporario = str(exc), True
    except ConectorError as exc:
        mensagem = exc.mensagem
    except LoteError as exc:
        detalhes = [d if isinstance(d, str) else "; ".join(d.get("erros", [])) for d in exc.detalhes]
        mensagem = " ".join([exc.mensagem, *[d for d in detalhes if d]])[:1000]
    except Exception as exc:  # não pode derrubar a thread
        log.exception("falha ao processar aviso do conector", extra={"evento": evento_id})
        mensagem = f"Erro interno ({exc.__class__.__name__}); veja o log."
    with sessao() as s:
        ev = s.get(EventoEntrada, evento_id)
        ev.tentativas += 1
        ev.mensagem = mensagem
        ev.emissao_id = emissao_id
        if temporario and ev.tentativas <= len(ESPERAS):
            ev.proxima_em = agora() + timedelta(seconds=ESPERAS[ev.tentativas - 1])
        else:
            ev.status = status
            ev.processado_em = agora()
        s.add(ev)
        s.commit()
    log.log(
        logging.WARNING if status == "erro" else logging.INFO,
        "aviso do conector: %s",
        ev.status,
        extra={"evento": evento_id, "pagamento": ev.pagamento_id, "mensagem": mensagem, "emissao_id": emissao_id},
    )


class _Ignorar(Exception):
    pass


class Processador:
    """Thread que processa os avisos pendentes (um por vez, na ordem de chegada)."""

    def __init__(self) -> None:
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None
        self._ultima_limpeza = 0.0

    def iniciar(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="conectores", daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        self._acordar.set()

    def _loop(self) -> None:
        while True:
            self._acordar.wait(timeout=30)
            self._acordar.clear()
            try:
                self.processar_pendentes()
                if time.monotonic() - self._ultima_limpeza > 6 * 3600:
                    self._ultima_limpeza = time.monotonic()
                    limpar_antigos()
            except Exception:
                log.exception("falha no processamento dos conectores")
                time.sleep(5)

    def processar_pendentes(self, limite: int = 50) -> int:
        with sessao() as s:
            q = (
                select(EventoEntrada)
                .where(EventoEntrada.status == "pendente", EventoEntrada.proxima_em <= agora())
                .order_by(EventoEntrada.recebido_em)  # type: ignore[arg-type]
                .limit(limite)
            )
            ids = [e.id for e in s.exec(q)]
        for eid in ids:
            _processar(eid)
        return len(ids)


def limpar_antigos() -> int:
    limite = agora() - GUARDAR_EVENTOS
    with sessao() as s:
        antigos = s.exec(
            select(EventoEntrada).where(EventoEntrada.status != "pendente", EventoEntrada.recebido_em < limite)
        ).all()
        for e in antigos:
            s.delete(e)
        s.commit()
    return len(antigos)


_processador: Processador | None = None


def processador() -> Processador:
    global _processador
    if _processador is None:
        _processador = Processador()
    return _processador


def definir_processador(p: Processador | None) -> None:
    """Injeção para testes."""
    global _processador
    _processador = p
