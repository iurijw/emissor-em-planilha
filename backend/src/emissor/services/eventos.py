"""Cancelamento de NFS-e e situação atualizada a partir do Ambiente Nacional.

- **Cancelar** (``cancelar``): envia o evento 101101 para a Sefin no ambiente em que a nota
  foi emitida; a nota passa a ``cancelada`` e o DANFSe ganha a marca d'água CANCELADA.
- **Situação de uma nota** (``atualizar_situacao``): lê no ADN todos os eventos da chave.
- **Sincronização** (``Sincronizador``): lê a distribuição do ADN por NSU (documentos em que o
  prestador aparece) e aplica os eventos às notas do sistema. É assim que um cancelamento
  feito no portal nacional (ou pela prefeitura) chega aqui. Roda em segundo plano quando a
  aba Emissões é aberta, no máximo uma vez por hora (ou a pedido, com intervalo mínimo).

Eventos que mudam a situação: 101101/105104/305101 → ``cancelada``; 105102 → ``substituida``.
Os demais (manifestação do tomador, bloqueio...) ficam só no histórico da nota.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlmodel import select

from emissor import config
from emissor.db import Emissao, EventoNFSe, SincronizacaoADN, agora, obter_config, para_local, sessao
from emissor.logging_setup import bind_context
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.danfse.render import gerar_danfse
from emissor.nfse.emissao import agora_emissao
from emissor.nfse.errors import SefinError
from emissor.nfse.eventos import (
    MOTIVOS_CANCELAMENTO,
    SITUACAO_POR_EVENTO,
    EventoDoc,
    EventoXmlError,
    enviar_cancelamento,
    preparar_cancelamento,
)
from emissor.services import arquivos
from emissor.services import certificado as cert_service
from emissor.services import emissao as emissao_service
from emissor.services.emissao import ClientFactory, LoteError, verificar_prontidao

log = logging.getLogger(__name__)

# Situações em que a nota existe na Sefin (tem chave) e pode receber eventos.
COM_NFSE = ("autorizada", "cancelada", "substituida")

_lock_registro = threading.Lock()


class EventoError(LoteError):
    """Operação de evento impossível (mensagem para o usuário)."""


def _fabrica(client_factory: ClientFactory | None) -> ClientFactory:
    # Mesma fábrica de clientes do worker de emissão (um único ponto de injeção nos testes).
    return client_factory or emissao_service.worker().client_factory


# --- registro de eventos ----------------------------------------------------------------------------


@dataclass
class Registro:
    evento: EventoNFSe | None  # None: a nota não é do sistema
    novo: bool = False  # False: o evento já estava gravado
    mudou_situacao: bool = False


def registrar_evento(xml: bytes, origem: str) -> Registro:
    """Grava um evento de uma nota do sistema e aplica seu efeito na situação.

    Idempotente: o mesmo evento (mesmo Id) não é gravado duas vezes.
    """
    doc = EventoDoc.from_bytes(xml)
    chave = doc.chave
    if not chave:
        raise EventoXmlError("Evento sem chave de acesso da NFS-e.")
    with _lock_registro, sessao() as s:
        filtros = [Emissao.chave_acesso == chave]
        if doc.tp_amb:
            filtros.append(Emissao.ambiente == doc.tp_amb)
        e = s.exec(select(Emissao).where(*filtros).order_by(Emissao.id)).first()
        if e is None:
            return Registro(None)
        ev = s.get(EventoNFSe, doc.id)
        novo = ev is None
        if novo:
            ev = EventoNFSe(
                id=doc.id,
                emissao_id=e.id,
                chave_acesso=chave,
                ambiente=e.ambiente,
                tipo=doc.tipo,
                n_seq=doc.n_seq,
                descricao=doc.descricao,
                motivo=doc.motivo_texto,
                autor=doc.autor,
                dh_evento=para_local(doc.dh_processamento) if doc.dh_processamento else None,
                origem=origem,
                xml_path=arquivos.relativo(_salvar_xml_evento(e, doc, xml)),
            )
            s.add(ev)
            log.info(
                "evento registrado: %s",
                ev.descricao,
                extra={"chave": chave, "tipo": ev.tipo, "origem": origem, "emissao_id": e.id},
            )
        nova = SITUACAO_POR_EVENTO.get(doc.tipo)
        mudou = bool(nova) and e.status == "autorizada"
        if mudou:
            e.status = nova
            e.atualizado_em = agora()
            s.add(e)
        s.commit()
    if mudou:
        log.warning("NFS-e nº %s agora está %s", e.numero_nfse, nova, extra={"chave": chave, "origem": origem})
        _regerar_pdf(e)
    return Registro(ev, novo, mudou)


def _salvar_xml_evento(e: Emissao, doc: EventoDoc, xml: bytes) -> Path:
    base = arquivos.absoluto(e.xml_path)
    sufixo = f"_evento-{doc.tipo}-{doc.n_seq:03d}.xml"
    if base is not None:
        destino = base.with_name(base.stem + sufixo)
    else:
        destino = config.XML_DIR / ("homologacao" if e.ambiente == "2" else "producao") / "eventos" / f"{doc.id}.xml"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(xml)
    return destino


def gerar_pdf(e: Emissao, nfse_xml: bytes) -> bytes:
    """DANFSe com a marca d'água conforme a situação da nota."""
    return gerar_danfse(nfse_xml, cancelada=e.status == "cancelada", substituida=e.status == "substituida")


def _regerar_pdf(e: Emissao) -> None:
    """Refaz o PDF já gravado com a marca CANCELADA/SUBSTITUÍDA (ou apaga para gerar sob demanda)."""
    xml = arquivos.absoluto(e.xml_path)
    pdf = arquivos.absoluto(e.pdf_path)
    if not (xml and xml.is_file() and pdf):
        return
    try:
        pdf.write_bytes(gerar_pdf(e, xml.read_bytes()))
    except Exception:
        log.exception("falha ao refazer o DANFSe; será gerado sob demanda", extra={"emissao_id": e.id})
        with sessao() as s:
            reg = s.get(Emissao, e.id)
            reg.pdf_path = None
            s.add(reg)
            s.commit()


def eventos_da_emissao(emissao_id: int) -> list[EventoNFSe]:
    with sessao() as s:
        return list(
            s.exec(
                select(EventoNFSe)
                .where(EventoNFSe.emissao_id == emissao_id)
                .order_by(EventoNFSe.dh_evento, EventoNFSe.registrado_em)
            )
        )


# --- cancelamento -------------------------------------------------------------------------------------


@dataclass
class ResultadoCancelamento:
    emissao: Emissao
    aviso: str | None = None


def _carregar_emissao(emissao_id: int) -> Emissao:
    with sessao() as s:
        e = s.get(Emissao, emissao_id)
    if e is None:
        raise EventoError("Emissão não encontrada.")
    return e


def cancelar(
    emissao_id: int,
    c_motivo: str,
    x_motivo: str,
    client_factory: ClientFactory | None = None,
    dormir=None,
) -> ResultadoCancelamento:
    e = _carregar_emissao(emissao_id)
    if e.status in ("cancelada", "substituida"):
        raise EventoError(f"A NFS-e nº {e.numero_nfse} já está {e.status}.")
    if e.status != "autorizada" or not e.chave_acesso:
        raise EventoError("Só é possível cancelar NFS-e autorizada.")
    if c_motivo not in MOTIVOS_CANCELAMENTO:
        raise EventoError("Escolha o motivo do cancelamento.")
    with sessao() as s:
        problemas = verificar_prontidao(s)
        fiscal = obter_config(s).config_fiscal()
    if problemas or fiscal is None:
        raise EventoError("Não é possível cancelar: " + " ".join(problemas or ["configuração fiscal ausente."]))
    cert = cert_service.carregar()
    amb = Ambiente(e.ambiente)
    kw = {"dormir": dormir} if dormir else {}
    with bind_context(emissao_id=e.id):
        _, pedido = preparar_cancelamento(
            cert,
            chave=e.chave_acesso,
            autor=fiscal.cnpj,
            ambiente=amb,
            c_motivo=c_motivo,
            x_motivo=x_motivo,
            dh_evento=agora_emissao(),
        )
        log.info("pedido de cancelamento", extra={"chave": e.chave_acesso, "c_motivo": c_motivo, "ambiente": amb.name})
        with _fabrica(client_factory)(amb, cert) as client:
            try:
                resultado = enviar_cancelamento(client, e.chave_acesso, pedido, **kw)
            except SefinError as err:
                if any((m.codigo or "").upper() == "E0840" for m in err.mensagens):
                    # A nota já tem evento que impede o cancelamento: traz a situação real.
                    atual = _reconciliar(client, e)
                    if atual and atual.status != "autorizada":
                        return ResultadoCancelamento(
                            atual, f"A NFS-e já estava {atual.status} no Ambiente Nacional; situação atualizada."
                        )
                raise
    try:
        registrar_evento(resultado.evento_xml, "sistema")
    except EventoXmlError:
        # A Sefin confirmou o cancelamento; o XML ilegível não pode deixar a nota como autorizada.
        log.exception("evento de cancelamento ilegível; marcando a nota mesmo assim", extra={"emissao_id": e.id})
        with sessao() as s:
            reg = s.get(Emissao, e.id)
            reg.status = "cancelada"
            reg.atualizado_em = agora()
            s.add(reg)
            s.commit()
        _regerar_pdf(_carregar_emissao(e.id))
    aviso = "O cancelamento já estava registrado na Sefin; situação atualizada." if resultado.recuperado else None
    return ResultadoCancelamento(_carregar_emissao(e.id), aviso)


def _reconciliar(client: SefinClient, e: Emissao) -> Emissao | None:
    try:
        _aplicar_lote(client.eventos_nfse(e.chave_acesso).documentos)
    except SefinError as err:
        log.warning("não foi possível consultar os eventos no ADN: %s", err, extra={"chave": e.chave_acesso})
        return None
    return _carregar_emissao(e.id)


# --- situação de uma nota (ADN) ---------------------------------------------------------------------


def _aplicar_lote(documentos) -> tuple[int, int]:
    """Registra os eventos distribuídos. Retorna (eventos novos, notas que mudaram de situação)."""
    novos = mudaram = 0
    for d in documentos:
        if d.tipo_documento != "EVENTO" or not d.xml:
            continue
        try:
            reg = registrar_evento(d.xml, "ambiente_nacional")
        except EventoXmlError as exc:
            log.warning("evento distribuído ilegível (NSU %s): %s", d.nsu, exc)
            continue
        novos += int(reg.novo)
        mudaram += int(reg.mudou_situacao)
    return novos, mudaram


def atualizar_situacao(emissao_id: int, client_factory: ClientFactory | None = None) -> tuple[Emissao, int]:
    """Consulta no ADN os eventos de uma nota. Retorna (emissão, eventos novos)."""
    e = _carregar_emissao(emissao_id)
    if e.status not in COM_NFSE or not e.chave_acesso:
        raise EventoError("Só notas autorizadas têm situação no Ambiente Nacional.")
    cert = cert_service.carregar()
    if cert is None:
        raise EventoError("Nenhum certificado carregado.")
    with bind_context(emissao_id=e.id), _fabrica(client_factory)(Ambiente(e.ambiente), cert) as client:
        novos, _ = _aplicar_lote(client.eventos_nfse(e.chave_acesso).documentos)
    return _carregar_emissao(e.id), novos


# --- sincronização por NSU ------------------------------------------------------------------------------


@dataclass
class EstadoSincronizacao:
    em_andamento: bool = False
    iniciada_em: datetime | None = None
    concluida_em: datetime | None = None
    erro: str | None = None
    documentos: int = 0
    eventos_novos: int = 0
    notas_atualizadas: int = 0

    def as_dict(self) -> dict:
        d = asdict(self)
        for k in ("iniciada_em", "concluida_em"):
            # Com fuso: a tela mostra "há X min" e o navegador pode estar em outro fuso.
            d[k] = d[k].replace(tzinfo=ZoneInfo(config.TIMEZONE)).isoformat() if d[k] else None
        return d


class Sincronizador:
    INTERVALO_AUTOMATICO = timedelta(hours=1)
    INTERVALO_MINIMO = timedelta(minutes=2)
    PAUSA_ENTRE_PAGINAS = 1.0  # segundos
    TAMANHO_LOTE = 50  # o ADN devolve até 50 documentos por consulta
    MAX_PAGINAS = 400

    def __init__(self, client_factory: ClientFactory | None = None, dormir=time.sleep):
        self.client_factory = client_factory
        self.dormir = dormir
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._estado = EstadoSincronizacao()

    def estado(self) -> dict:
        with self._lock:
            est = EstadoSincronizacao(**asdict(self._estado))
        if not est.em_andamento and est.iniciada_em is None:
            # Após reiniciar o servidor: última execução registrada no banco.
            with sessao() as s:
                ult = s.exec(select(SincronizacaoADN).order_by(SincronizacaoADN.iniciada_em.desc())).first()
            if ult:
                est.iniciada_em, est.concluida_em, est.erro = ult.iniciada_em, ult.concluida_em, ult.erro
        return est.as_dict()

    def solicitar(self, forcar: bool = False) -> dict:
        """Inicia a sincronização em segundo plano se o intervalo permitir."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self._estado.as_dict() | {"motivo": "em_andamento"}
            ultima = self._ultima_execucao()
            intervalo = self.INTERVALO_MINIMO if forcar else self.INTERVALO_AUTOMATICO
            if ultima and agora() - ultima < intervalo:
                motivo = "recente"
            elif not self._ambientes():
                motivo = "sem_notas"
            else:
                self._estado = EstadoSincronizacao(em_andamento=True, iniciada_em=agora())
                self._thread = threading.Thread(target=self._rodar, name="sincronizacao-adn", daemon=True)
                self._thread.start()
                return self._estado.as_dict() | {"motivo": "iniciada"}
        return self.estado() | {"motivo": motivo}

    def _ultima_execucao(self) -> datetime | None:
        with sessao() as s:
            datas = [r.iniciada_em for r in s.exec(select(SincronizacaoADN)) if r.iniciada_em]
        return max(datas) if datas else None

    def _ambientes(self) -> list[Ambiente]:
        with sessao() as s:
            ambs = s.exec(
                select(Emissao.ambiente)
                .where(Emissao.chave_acesso.is_not(None), Emissao.status.in_(COM_NFSE))
                .distinct()
            ).all()
        return [Ambiente(a) for a in sorted(ambs)]

    def _rodar(self) -> None:
        try:
            self.executar()
        except Exception as exc:  # nunca derrubar a thread sem registrar
            log.exception("falha inesperada na sincronização com o ADN")
            with self._lock:
                self._estado.erro = f"Falha inesperada: {exc}"

    def executar(self) -> None:
        """Sincroniza todos os ambientes com notas (síncrono; a thread chama isto)."""
        with self._lock:
            if not self._estado.em_andamento:
                self._estado = EstadoSincronizacao(em_andamento=True, iniciada_em=agora())
        try:
            self._executar()
        finally:
            with self._lock:
                self._estado.em_andamento = False

    def _executar(self) -> None:
        cert = cert_service.carregar()
        with sessao() as s:
            fiscal = obter_config(s).config_fiscal()
        if cert is None or fiscal is None:
            with self._lock:
                self._estado.erro = "Certificado ou configuração fiscal ausente."
            return
        erros = []
        for amb in self._ambientes():
            try:
                self._sincronizar_ambiente(amb, fiscal.cnpj, cert)
            except SefinError as err:
                erros.append(f"{amb.descricao}: {err}")
        with self._lock:
            self._estado.erro = "; ".join(erros) or None
            self._estado.concluida_em = None if erros else agora()
        log.info("sincronização com o ADN finalizada", extra=self._estado.as_dict())

    def _sincronizar_ambiente(self, amb: Ambiente, cnpj: str, cert) -> None:
        chave_cursor = f"{amb.value}:{cnpj}"
        with sessao() as s:
            cursor = s.get(SincronizacaoADN, chave_cursor) or SincronizacaoADN(id=chave_cursor)
            cursor.iniciada_em = agora()
            s.add(cursor)
            s.commit()
        nsu = cursor.ultimo_nsu
        erro = None
        try:
            with bind_context(sincronizacao=chave_cursor), _fabrica(self.client_factory)(amb, cert) as client:
                for pagina in range(self.MAX_PAGINAS):
                    if pagina:
                        self.dormir(self.PAUSA_ENTRE_PAGINAS)
                    lote = client.distribuicao_dfe(nsu)
                    docs = sorted(
                        (d for d in lote.documentos if d.nsu is not None and d.nsu > nsu), key=lambda d: d.nsu
                    )
                    novos, mudaram = _aplicar_lote(docs)
                    with self._lock:
                        self._estado.documentos += len(docs)
                        self._estado.eventos_novos += novos
                        self._estado.notas_atualizadas += mudaram
                    if docs:
                        nsu = docs[-1].nsu
                        self._gravar_cursor(chave_cursor, nsu=nsu)
                    if not docs or len(lote.documentos) < self.TAMANHO_LOTE:
                        break
        except SefinError as err:
            erro = str(err)
            raise
        finally:
            self._gravar_cursor(chave_cursor, erro=erro, concluida=erro is None)
        log.info("ADN sincronizado", extra={"ambiente": amb.name, "ultimo_nsu": nsu})

    @staticmethod
    def _gravar_cursor(chave: str, *, nsu: int | None = None, erro: str | None = None, concluida=False) -> None:
        with sessao() as s:
            cursor = s.get(SincronizacaoADN, chave)
            if nsu is not None:
                cursor.ultimo_nsu = nsu
            if concluida:
                cursor.concluida_em = agora()
                cursor.erro = None
            elif erro:
                cursor.erro = erro[:2000]
            s.add(cursor)
            s.commit()


_sincronizador: Sincronizador | None = None


def sincronizador() -> Sincronizador:
    global _sincronizador
    if _sincronizador is None:
        _sincronizador = Sincronizador()
    return _sincronizador


def definir_sincronizador(sinc: Sincronizador | None) -> None:
    """Injeção para testes."""
    global _sincronizador
    _sincronizador = sinc
