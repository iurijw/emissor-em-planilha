"""Emissão em lote: validação prévia, fila em background e registro de cada NFS-e.

Fluxo de uma emissão (status):
    pendente → processando → autorizada | rejeitada | erro
    pendente → nao_enviada (lote cancelado/interrompido antes de chegar nela)

Garantias:
- Um único worker processa as notas **em sequência** (numeração previsível, sem
  concorrência no contador de nDPS).
- O nDPS só é reservado depois que a DPS foi montada, assinada e validada; o XML
  assinado é gravado **antes** do envio. Se o servidor cair no meio, a emissão fica
  ``processando`` e, ao reiniciar, é retomada consultando ``/dps/{id}`` antes de reenviar.
- Falha de certificado ou Sefin fora do ar interrompe o lote (as demais notas ficam
  ``nao_enviada``) em vez de gerar dezenas de erros iguais.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlmodel import Session, select

from emissor.db import Cliente, Emissao, LinhaTabela, Lote, agora, obter_config, para_local, sessao
from emissor.logging_setup import bind_context
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.danfse.render import gerar_danfse
from emissor.nfse.documentos import cnpj_alfanumerico, documento_valido, formatar_documento
from emissor.nfse.dps_builder import DpsBuildError
from emissor.nfse.emissao import ResultadoEmissao, agora_emissao, enviar_dps, preparar_dps
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro
from emissor.nfse.models import DpsInput, normalizar_documento
from emissor.nfse.xml_reader import NFSeDoc
from emissor.services import arquivos
from emissor.services import certificado as cert_service
from emissor.services.clientes import para_tomador

log = logging.getLogger(__name__)

ClientFactory = Callable[[Ambiente, Certificado], SefinClient]


class LoteError(Exception):
    """Erro que impede criar o lote (mensagem + detalhes por linha)."""

    def __init__(self, mensagem: str, detalhes: list[dict] | None = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhes = detalhes or []


# --- validação prévia -------------------------------------------------------------------


@dataclass
class ValidacaoLinha:
    linha_id: int
    erros: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def _valor(v: str) -> Decimal | None:
    try:
        d = Decimal(v)
    except (InvalidOperation, TypeError):
        return None
    return d if d > 0 else None


def validar_linhas(s: Session, linhas: list[LinhaTabela], ambiente: Ambiente) -> list[ValidacaoLinha]:
    res = []
    inicio_mes = agora().replace(day=1, hour=0, minute=0, second=0)
    for ln in linhas:
        v = ValidacaoLinha(ln.id)
        doc = normalizar_documento(ln.documento) or ""
        cli = s.get(Cliente, doc) if doc else None
        if not doc:
            v.erros.append("CNPJ/CPF não informado.")
        elif len(doc) not in (11, 14) or not documento_valido(doc):
            v.erros.append(f"CNPJ/CPF inválido: {ln.documento!r} (dígitos verificadores não conferem).")
        elif cnpj_alfanumerico(doc):
            v.erros.append("CNPJ alfanumérico ainda não é aceito pelo leiaute oficial da NFS-e (XSD 1.01).")
        if not (ln.nome or "").strip() and not (cli and cli.nome):
            v.erros.append("Nome da empresa não informado.")
        valor = _valor(ln.valor)
        if valor is None:
            v.erros.append(f"Valor inválido ou zerado: {ln.valor!r}.")
        if not (ln.descricao or "").strip():
            v.erros.append("Descrição do serviço não informada.")
        elif len(ln.descricao) > 2000:
            v.erros.append(f"Descrição com {len(ln.descricao)} caracteres (máximo 2000).")
        if doc and not v.erros:
            if cli is None:
                v.avisos.append("Cliente sem cadastro: a nota sairá sem o endereço do tomador.")
            elif not cli.endereco_completo:
                v.avisos.append("Cadastro do cliente sem endereço completo: a nota sairá sem endereço.")
            if cli and cli.situacao_cadastral and cli.situacao_cadastral.upper() != "ATIVA":
                v.avisos.append(f"Situação cadastral do CNPJ na Receita: {cli.situacao_cadastral}.")
            dup = s.exec(
                select(Emissao).where(
                    Emissao.ambiente == ambiente.value,
                    Emissao.tomador_documento == doc,
                    Emissao.status == "autorizada",
                    Emissao.dh_emissao >= inicio_mes,
                )
            ).all()
            for e in dup:
                if valor is not None and Decimal(e.valor) == valor:
                    v.avisos.append(
                        f"Já existe NFS-e nº {e.numero_nfse} autorizada para este cliente com o mesmo valor "
                        f"neste mês ({e.dh_emissao:%d/%m/%Y})."
                    )
                    break
        em_andamento = s.exec(
            select(Emissao).where(Emissao.linha_id == ln.id, Emissao.status.in_(["pendente", "processando"]))
        ).first()
        if em_andamento:
            v.erros.append("Esta linha já está em uma emissão em andamento.")
        res.append(v)
    return res


def verificar_prontidao(s: Session) -> list[str]:
    """Problemas de configuração que impedem qualquer emissão."""
    problemas = []
    cfg = obter_config(s)
    fiscal = cfg.config_fiscal()
    if not cfg.onboarding_concluido or fiscal is None:
        problemas.append("Configuração inicial não concluída.")
    info = cert_service.info()
    if info is None:
        problemas.append("Nenhum certificado digital carregado.")
    else:
        if info.vencido:
            problemas.append(f"Certificado vencido em {info.valido_ate:%d/%m/%Y}.")
        if fiscal and info.cnpj and normalizar_documento(fiscal.cnpj) != info.cnpj:
            problemas.append(
                f"O certificado é do CNPJ {formatar_documento(info.cnpj)}, mas o prestador configurado é "
                f"{formatar_documento(fiscal.cnpj)}."
            )
    return problemas


def criar_lote(linha_ids: list[int]) -> Lote:
    if not linha_ids:
        raise LoteError("Selecione ao menos uma linha para emitir.")
    with sessao() as s:
        problemas = verificar_prontidao(s)
        if problemas:
            raise LoteError("Não é possível emitir: " + " ".join(problemas))
        cfg = obter_config(s)
        linhas = [s.get(LinhaTabela, i) for i in linha_ids]
        faltando = [i for i, ln in zip(linha_ids, linhas, strict=True) if ln is None]
        if faltando:
            raise LoteError(f"Linhas não encontradas: {faltando}. Recarregue a tabela.")
        validacoes = validar_linhas(s, linhas, cfg.amb)
        com_erro = [asdict(v) for v in validacoes if v.erros]
        if com_erro:
            raise LoteError(f"{len(com_erro)} linha(s) com erro. Corrija antes de emitir.", com_erro)
        lote = Lote(id=uuid.uuid4().hex[:12], ambiente=cfg.ambiente, total=len(linhas))
        s.add(lote)
        for ln in linhas:
            doc = normalizar_documento(ln.documento)
            cli = s.get(Cliente, doc)
            s.add(
                Emissao(
                    lote_id=lote.id,
                    linha_id=ln.id,
                    ambiente=cfg.ambiente,
                    tomador_documento=doc,
                    tomador_nome=(ln.nome or "").strip() or (cli.nome if cli else ""),
                    valor=f"{Decimal(ln.valor):.2f}",
                    descricao=ln.descricao.strip(),
                )
            )
        s.commit()
    log.info("lote criado", extra={"lote_id": lote.id, "total": lote.total, "ambiente": lote.ambiente})
    worker().enfileirar(lote.id)
    return lote


# --- worker -----------------------------------------------------------------------------------


class _Interromper(Exception):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def _client_padrao(amb: Ambiente, cert: Certificado) -> SefinClient:
    return SefinClient(amb, cert)


class EmissionWorker:
    def __init__(self, client_factory: ClientFactory = _client_padrao, dormir=None):
        self.client_factory = client_factory
        self.dormir = dormir
        self._fila: queue.Queue[str] = queue.Queue()
        self._cancelados: set[str] = set()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.lote_atual: str | None = None

    # -- controle ------------------------------------------------------------------------
    def iniciar(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._loop, name="emissao-worker", daemon=True)
            self._thread.start()
        for lote_id in self._lotes_para_retomar():
            log.warning("retomando lote interrompido por reinício do servidor", extra={"lote_id": lote_id})
            self.enfileirar(lote_id)

    def enfileirar(self, lote_id: str) -> None:
        self._fila.put(lote_id)

    def cancelar(self, lote_id: str) -> None:
        self._cancelados.add(lote_id)
        log.info("cancelamento de lote solicitado", extra={"lote_id": lote_id})

    def _lotes_para_retomar(self) -> list[str]:
        with sessao() as s:
            return [lt.id for lt in s.exec(select(Lote).where(Lote.status == "em_andamento").order_by(Lote.criado_em))]

    def _loop(self) -> None:
        while True:
            lote_id = self._fila.get()
            try:
                self.processar_lote(lote_id)
            except Exception:  # nunca derrubar o worker
                log.exception("falha inesperada ao processar lote", extra={"lote_id": lote_id})
            finally:
                self._fila.task_done()

    # -- processamento -------------------------------------------------------------------------
    def processar_lote(self, lote_id: str) -> None:
        with bind_context(lote_id=lote_id):
            with sessao() as s:
                lote = s.get(Lote, lote_id)
                if lote is None or lote.status != "em_andamento":
                    return
                amb = Ambiente(lote.ambiente)
                fiscal = obter_config(s).config_fiscal()
            self.lote_atual = lote_id
            cert = cert_service.carregar()
            motivo = None
            try:
                if cert is None or fiscal is None:
                    raise _Interromper("Certificado ou configuração fiscal ausente.")
                client = self.client_factory(amb, cert)
                try:
                    while True:
                        if lote_id in self._cancelados:
                            raise _Interromper("Cancelado pelo usuário.")
                        e = self._proxima(lote_id)
                        if e is None:
                            break
                        self._processar(e, client, cert, fiscal, amb)
                finally:
                    client.close()
            except _Interromper as exc:
                motivo = exc.motivo
                log.warning("lote interrompido: %s", motivo)
            finally:
                self.lote_atual = None
                self._cancelados.discard(lote_id)
                self._finalizar_lote(lote_id, motivo)

    def _proxima(self, lote_id: str) -> Emissao | None:
        with sessao() as s:
            # Retomadas (processando) primeiro, depois pendentes, na ordem de criação.
            for status in ("processando", "pendente"):
                e = s.exec(
                    select(Emissao).where(Emissao.lote_id == lote_id, Emissao.status == status).order_by(Emissao.id)
                ).first()
                if e:
                    return e
        return None

    def _processar(self, e: Emissao, client: SefinClient, cert: Certificado, fiscal, amb: Ambiente) -> None:
        with bind_context(emissao_id=e.id):
            kw = {"dormir": self.dormir} if self.dormir else {}
            try:
                if e.status == "processando" and e.dps_path:
                    resultado = self._retomar(e, client, **kw)
                else:
                    preparado = self._preparar(e, cert, fiscal, amb)
                    if preparado is None:
                        return
                    id_dps, xml = preparado
                    resultado = enviar_dps(client, id_dps, xml, **kw)
            except SefinError as err:
                self._falha(e, err)
                return
            self._sucesso(e, resultado)

    def _preparar(self, e: Emissao, cert: Certificado, fiscal, amb: Ambiente) -> tuple[str, bytes] | None:
        with sessao() as s:
            cfg = obter_config(s)
            n = cfg.proximo_ndps()
            serie = cfg.serie
            cli = s.get(Cliente, e.tomador_documento)
        dh = agora_emissao()
        inp = DpsInput(
            ambiente=amb,
            serie=str(serie),
            n_dps=n,
            dh_emi=dh,
            config=fiscal,
            tomador=para_tomador(cli, e.tomador_documento, e.tomador_nome),
            valor=Decimal(e.valor),
            descricao=e.descricao,
        )
        try:
            id_dps, xml = preparar_dps(inp, cert)
        except DpsBuildError as exc:
            self._falha(
                e, SefinError(TipoErro.VALIDACAO_LOCAL, "Dados inválidos para a DPS", [MensagemSefin(None, str(exc))])
            )
            return None
        except SefinError as err:
            self._falha(e, err)
            return None
        caminho = arquivos.caminho_dps(amb.value, id_dps)
        caminho.write_bytes(xml)
        with sessao() as s:
            cfg = obter_config(s)
            if cfg.proximo_ndps() != n:  # não deveria ocorrer com um único worker
                raise _Interromper("Contador de numeração alterado durante a emissão; lote interrompido.")
            cfg.avancar_ndps(n)
            cfg.atualizado_em = agora()
            reg = s.get(Emissao, e.id)
            reg.status = "processando"
            reg.serie = serie
            reg.n_dps = n
            reg.id_dps = id_dps
            reg.dps_path = arquivos.relativo(caminho)
            reg.dh_emissao = para_local(dh)
            reg.atualizado_em = agora()
            s.add_all([cfg, reg])
            s.commit()
        log.info("DPS preparada", extra={"id_dps": id_dps, "n_dps": n, "serie": serie})
        return id_dps, xml

    def _retomar(self, e: Emissao, client: SefinClient, **kw) -> ResultadoEmissao:
        xml = arquivos.absoluto(e.dps_path).read_bytes()
        log.warning("retomando emissão que estava em processamento", extra={"id_dps": e.id_dps})
        chave = client.consultar_dps(e.id_dps)
        if chave:
            resp = client.consultar_nfse(chave)
            return ResultadoEmissao(e.id_dps, chave, xml, resp.nfse_xml, resp.alertas, recuperada=True)
        return enviar_dps(client, e.id_dps, xml, **kw)

    def _sucesso(self, e: Emissao, r: ResultadoEmissao) -> None:
        finalizar_autorizada(e.id, r)

    def _falha(self, e: Emissao, err: SefinError) -> None:
        status = "rejeitada" if err.tipo in (TipoErro.REJEICAO, TipoErro.VALIDACAO_LOCAL) else "erro"
        with sessao() as s:
            reg = s.get(Emissao, e.id)
            reg.status = status
            reg.erro_json = json.dumps(err.as_dict(), ensure_ascii=False)
            reg.tentativas += 1
            reg.atualizado_em = agora()
            s.add(reg)
            _vincular_linha(s, reg)
            s.commit()
        log.warning("emissão %s: %s", status, err, extra={"erro": err.as_dict()})
        if err.tipo == TipoErro.AUTENTICACAO:
            raise _Interromper(f"Certificado recusado pela Sefin: {err.resumo}")
        if err.tipo in (TipoErro.INDISPONIVEL, TipoErro.AMBIGUO, TipoErro.RESPOSTA_INVALIDA):
            raise _Interromper(f"Sefin Nacional indisponível ou sem resposta confirmada: {err.resumo}")

    def _finalizar_lote(self, lote_id: str, motivo: str | None) -> None:
        with sessao() as s:
            lote = s.get(Lote, lote_id)
            if lote is None:
                return
            pendentes = s.exec(select(Emissao).where(Emissao.lote_id == lote_id, Emissao.status == "pendente")).all()
            for p in pendentes:
                p.status = "nao_enviada"
                p.erro_json = json.dumps(
                    {"tipo": "nao_enviada", "resumo": f"Não enviada: {motivo or 'lote encerrado'}", "mensagens": []},
                    ensure_ascii=False,
                )
                p.atualizado_em = agora()
                s.add(p)
                _vincular_linha(s, p)
            if motivo is None:
                lote.status = "concluido"
            else:
                lote.status = "cancelado" if motivo.startswith("Cancelado") else "interrompido"
                lote.motivo_interrupcao = motivo
            lote.finalizado_em = agora()
            s.add(lote)
            s.commit()
        log.info("lote finalizado", extra={"lote_id": lote_id, "status": lote.status})


def _vincular_linha(s: Session, e: Emissao) -> None:
    if e.linha_id is None:
        return
    ln = s.get(LinhaTabela, e.linha_id)
    if ln is not None:
        ln.ultima_emissao_id = e.id
        s.add(ln)


def finalizar_autorizada(emissao_id: int, r: ResultadoEmissao) -> Emissao:
    """Grava XML/PDF da NFS-e autorizada e atualiza o registro."""
    doc = NFSeDoc.from_bytes(r.nfse_xml)
    with sessao() as s:
        e = s.get(Emissao, emissao_id)
        data = e.dh_emissao or agora()
        prestador = doc.emitente_nome or "PRESTADOR"
        base = arquivos.nome_base(data, doc.numero, prestador, e.tomador_nome, e.valor)
        xml_path = arquivos.caminho_xml(e.ambiente, data, base)
        xml_path.write_bytes(r.nfse_xml)
        pdf_rel = None
        try:
            pdf_path = arquivos.caminho_pdf(e.ambiente, data, base)
            pdf_path.write_bytes(gerar_danfse(r.nfse_xml))
            pdf_rel = arquivos.relativo(pdf_path)
        except Exception:
            log.exception("falha ao gerar DANFSe; será gerado sob demanda", extra={"chave": r.chave_acesso})
        e.status = "autorizada"
        e.chave_acesso = r.chave_acesso
        e.numero_nfse = doc.numero
        e.dh_processamento = doc.data_processamento
        e.xml_path = arquivos.relativo(xml_path)
        e.pdf_path = pdf_rel
        e.alertas_json = json.dumps([asdict(a) for a in r.alertas], ensure_ascii=False) if r.alertas else None
        e.erro_json = None
        e.recuperada = r.recuperada
        e.tentativas += r.tentativas
        e.atualizado_em = agora()
        s.add(e)
        _vincular_linha(s, e)
        s.commit()
    log.info(
        "NFS-e registrada",
        extra={"chave": r.chave_acesso, "numero": doc.numero, "recuperada": r.recuperada, "xml": str(xml_path)},
    )
    return e


def verificar_na_sefin(emissao_id: int, client_factory: ClientFactory = _client_padrao) -> Emissao:
    """Para emissões com resultado incerto: consulta a DPS e recupera a NFS-e se existir."""
    with sessao() as s:
        e = s.get(Emissao, emissao_id)
    if e is None:
        raise LoteError("Emissão não encontrada.")
    if not e.id_dps or e.status not in ("erro", "processando"):
        raise LoteError("Só é possível verificar emissões com erro de comunicação que chegaram a ser enviadas.")
    cert = cert_service.carregar()
    if cert is None:
        raise LoteError("Nenhum certificado carregado.")
    with bind_context(emissao_id=e.id), client_factory(Ambiente(e.ambiente), cert) as client:
        chave = client.consultar_dps(e.id_dps)
        if chave:
            resp = client.consultar_nfse(chave)
            xml = arquivos.absoluto(e.dps_path).read_bytes() if e.dps_path else b""
            return finalizar_autorizada(
                e.id, ResultadoEmissao(e.id_dps, chave, xml, resp.nfse_xml, resp.alertas, recuperada=True)
            )
    with sessao() as s:
        reg = s.get(Emissao, emissao_id)
        reg.status = "erro"
        reg.erro_json = json.dumps(
            {
                "tipo": "nao_encontrada",
                "resumo": "A Sefin confirmou que esta DPS NÃO gerou NFS-e. É seguro emitir novamente pela tabela.",
                "mensagens": [],
            },
            ensure_ascii=False,
        )
        reg.atualizado_em = agora()
        s.add(reg)
        s.commit()
    return reg


_worker: EmissionWorker | None = None


def worker() -> EmissionWorker:
    global _worker
    if _worker is None:
        _worker = EmissionWorker()
    return _worker


def definir_worker(w: EmissionWorker | None) -> None:
    """Injeção para testes."""
    global _worker
    _worker = w


def _dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None
