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
from emissor.nfse.documentos import documento_valido, formatar_documento
from emissor.nfse.dps_builder import DpsBuildError
from emissor.nfse.emissao import ResultadoEmissao, agora_emissao, enviar_dps, preparar_dps
from emissor.nfse.errors import MensagemSefin, SefinError, TipoErro
from emissor.nfse.models import (
    ConfigFiscal,
    DpsInput,
    aplicar_tributacao,
    iss_exige_aliquota,
    normalizar_documento,
    tributacao_de,
)
from emissor.nfse.tributos import calcular as calcular_tributos
from emissor.nfse.xml_reader import NFSeDoc
from emissor.services import arquivos
from emissor.services import certificado as cert_service
from emissor.services import tributacao as trib_service
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


def validar_linhas(
    s: Session, linhas: list[LinhaTabela], ambiente: Ambiente, tributacao_avulsa: dict | None = None
) -> list[ValidacaoLinha]:
    """Erros e avisos de cada linha. Linhas sem aba (nota avulsa) usam ``tributacao_avulsa`` (None = padrões)."""
    res = []
    inicio_mes = agora().replace(day=1, hour=0, minute=0, second=0)
    abas = {a.id: a for a in trib_service.garantir_abas(s)}
    exclusivos = trib_service.clientes_exclusivos(list(abas.values()))
    erros_aba = _erros_tributacao_abas(s, list(abas.values()))
    base = obter_config(s).config_fiscal()
    efetivas: dict[int | None, ConfigFiscal | None] = {}

    def efetiva(aba) -> ConfigFiscal | None:
        chave = aba.id if aba is not None else None
        if chave not in efetivas:
            try:
                dados = trib_service.tributacao_da_aba(s, aba)[1] if aba is not None else tributacao_avulsa
            except trib_service.TributacaoError:
                dados = None
            efetivas[chave] = aplicar_tributacao(base, dados) if base else None
        return efetivas[chave]

    for ln in linhas:
        v = ValidacaoLinha(ln.id)
        aba = abas.get(ln.aba_id) if ln.aba_id is not None else None
        if aba is not None:
            v.erros.extend(erros_aba.get(aba.id, []))
        cfg_ef = efetiva(aba)
        iss_retido = cfg_ef is not None and cfg_ef.tp_ret_issqn in ("2", "3")
        doc = normalizar_documento(ln.documento) or ""
        cli = s.get(Cliente, doc) if doc else None
        if not doc:
            v.erros.append("CNPJ/CPF não informado.")
        elif len(doc) not in (11, 14) or not documento_valido(doc):
            v.erros.append(f"CNPJ/CPF inválido: {ln.documento!r} (dígitos verificadores não conferem).")
        if not (ln.nome or "").strip() and not (cli and cli.nome):
            v.erros.append("Nome da empresa não informado.")
        valor = _valor(ln.valor)
        if valor is None:
            v.erros.append(f"Valor inválido ou zerado: {ln.valor!r}.")
        if not (ln.descricao or "").strip():
            v.erros.append("Descrição do serviço não informada.")
        elif len(ln.descricao) > 2000:
            v.erros.append(f"Descrição com {len(ln.descricao)} caracteres (máximo 2000).")
        if doc and aba is not None:
            dona = exclusivos.get(doc)
            if aba.clientes() and doc not in aba.clientes():
                v.erros.append(
                    f"A aba “{aba.nome}” é só dos clientes escolhidos para ela, e este CNPJ/CPF não está entre eles. "
                    "Mova a linha para outra aba ou inclua o cliente na aba."
                )
            elif dona is not None and dona.id != aba.id:
                v.avisos.append(
                    f"Este cliente tem aba própria (“{dona.nome}”); aqui a nota sai com a tributação desta aba."
                )
        if cfg_ef is not None and valor is not None:
            v.erros.extend(calcular_tributos(cfg_ef, valor)[1])
        if doc and iss_retido and not (cli and cli.endereco_completo):
            v.erros.append(
                "Com ISS retido a nota precisa do endereço do tomador (E0237): complete o cadastro do cliente."
            )
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


def _erros_tributacao_abas(s: Session, abas: list) -> dict[int, list[str]]:
    """Problemas da tributação de cada aba (excluída ou incoerente com o cadastro atual)."""
    base = obter_config(s).config_fiscal()
    res: dict[int, list[str]] = {}
    for aba in abas:
        try:
            nome, dados = trib_service.tributacao_da_aba(s, aba)
        except trib_service.TributacaoError as exc:
            res[aba.id] = [exc.mensagem]
            continue
        if base is None:
            continue
        erros = trib_service.coerencia(tributacao_de(aplicar_tributacao(base, dados)), base)
        if erros:
            origem = f"tributação “{nome}”" if nome else "padrões da nota (Configurações)"
            res[aba.id] = [f"{origem}: {e}" for e in erros]
    return res


def aliquotas_iss(
    s: Session, linhas: list[LinhaTabela], tributacao_avulsa: dict | None = None, tributacao_id: int | None = None
) -> list[dict]:
    """Tributações das linhas que exigem a alíquota do ISS retido (a emissão pergunta se mantém ou altera).

    ``origem``: ``tributacao`` (predefinida, ``tributacao_id``), ``padrao`` (Configurações) ou ``nota`` (tributação
    só desta nota avulsa, alterada só na tela).
    """
    base = obter_config(s).config_fiscal()
    if base is None:
        return []
    abas = {a.id: a for a in trib_service.garantir_abas(s)}
    grupos: dict[tuple, dict] = {}
    for ln in linhas:
        if ln.aba_id is not None:
            aba = abas.get(ln.aba_id)
            try:
                nome, dados = trib_service.tributacao_da_aba(s, aba)
            except trib_service.TributacaoError:
                continue
            tid = aba.tributacao_id if aba is not None and dados is not None else None
            origem = "tributacao" if tid is not None else "padrao"
        else:
            nome, dados, tid = None, tributacao_avulsa, tributacao_id
            origem = "tributacao" if tid is not None else ("nota" if dados is not None else "padrao")
        cfg = aplicar_tributacao(base, dados)
        if not iss_exige_aliquota(cfg):
            continue
        chave = (origem, tid)
        if chave not in grupos:
            grupos[chave] = {
                "origem": origem,
                "tributacao_id": tid,
                "nome": nome if origem == "tributacao" else ("Só desta nota" if origem == "nota" else "Padrão"),
                "p_aliq_iss": cfg.p_aliq_iss,
                "atualizada_em": cfg.p_aliq_iss_atualizada_em,
                "linhas": 0,
            }
        grupos[chave]["linhas"] += 1
    return list(grupos.values())


def _exigir_aliquotas(itens: list[dict]) -> None:
    faltando = [i["nome"] for i in itens if not i["p_aliq_iss"]]
    if faltando:
        raise LoteError(
            "Informe a alíquota do ISS retido antes de emitir.",
            [f"Tributação “{n}”: sem alíquota do ISS retido." for n in faltando],
        )


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
        _exigir_aliquotas(aliquotas_iss(s, linhas))
        abas = {a.id: a for a in trib_service.garantir_abas(s)}
        itens = []
        for ln in linhas:
            nome_trib, dados_trib = trib_service.tributacao_da_aba(s, abas.get(ln.aba_id))
            itens.append((ln.id, ln.nome, ln.documento, ln.valor, ln.descricao, nome_trib, dados_trib))
        lote = _gravar_lote(s, cfg.ambiente, itens)
    worker().enfileirar(lote.id)
    return lote


@dataclass
class NotaAvulsa:
    nome: str
    documento: str
    valor: str
    descricao: str
    tributacao_nome: str | None = None  # None com dados = tributação só desta nota
    tributacao: dict | None = None  # None = padrões das configurações
    tributacao_id: int | None = None  # tributação predefinida de onde vieram os dados


def linha_avulsa(n: NotaAvulsa) -> LinhaTabela:
    """Linha transitória (fora do banco) para reaproveitar a validação da tabela."""
    return LinhaTabela(
        id=None,
        aba_id=None,
        nome=n.nome,
        documento=normalizar_documento(n.documento) or "",
        valor=n.valor,
        descricao=n.descricao,
    )


def validar_avulsa(s: Session, n: NotaAvulsa, ambiente: Ambiente) -> ValidacaoLinha:
    return validar_linhas(s, [linha_avulsa(n)], ambiente, n.tributacao)[0]


def criar_avulsa(n: NotaAvulsa) -> Lote:
    """Uma nota só, fora da tabela, emitida como um lote de 1 (mesma fila e mesmas garantias)."""
    with sessao() as s:
        problemas = verificar_prontidao(s)
        if problemas:
            raise LoteError("Não é possível emitir: " + " ".join(problemas))
        cfg = obter_config(s)
        v = validar_avulsa(s, n, cfg.amb)
        if v.erros:
            raise LoteError("A nota tem erros. Corrija antes de emitir.", v.erros)
        ln = linha_avulsa(n)
        _exigir_aliquotas(aliquotas_iss(s, [ln], n.tributacao, n.tributacao_id))
        lote = _gravar_lote(
            s, cfg.ambiente, [(None, ln.nome, ln.documento, ln.valor, ln.descricao, n.tributacao_nome, n.tributacao)]
        )
    worker().enfileirar(lote.id)
    return lote


def _gravar_lote(s: Session, ambiente: str, itens: list[tuple]) -> Lote:
    """Grava o lote e uma emissão por item ``(linha_id, nome, documento, valor, descricao, trib_nome, trib)``.

    A tributação é copiada para a emissão: mudar a tributação depois não altera notas já na fila.
    """
    lote = Lote(id=uuid.uuid4().hex[:12], ambiente=ambiente, total=len(itens))
    s.add(lote)
    for linha_id, nome, documento, valor, descricao, trib_nome, trib in itens:
        doc = normalizar_documento(documento)
        cli = s.get(Cliente, doc)
        s.add(
            Emissao(
                lote_id=lote.id,
                linha_id=linha_id,
                ambiente=ambiente,
                tomador_documento=doc,
                tomador_nome=(nome or "").strip() or (cli.nome if cli else ""),
                valor=f"{Decimal(valor):.2f}",
                descricao=descricao.strip(),
                tributacao_nome=trib_nome if trib else None,
                tributacao_json=trib_service.dumps(trib) if trib else None,
            )
        )
    s.commit()
    log.info("lote criado", extra={"lote_id": lote.id, "total": lote.total, "ambiente": lote.ambiente})
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
            config=aplicar_tributacao(fiscal, e.tributacao()),
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
