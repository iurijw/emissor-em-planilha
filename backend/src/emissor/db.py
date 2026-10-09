"""Banco SQLite (SQLModel). Um arquivo em ``data/emissor.db``.

Tabelas:
- ``configuracao``  (linha única) ambiente, série, contadores de nDPS, padrões fiscais (JSON)
- ``certificado``   (linha única) PFX e senha criptografados + metadados
- ``cliente``       cadastro de tomadores
- ``linha_tabela``  tabela de notas a emitir (persistente)
- ``lote``          execução de emissão em massa
- ``emissao``       uma DPS/NFS-e (histórico completo)
- ``evento_nfse``   eventos das NFS-e emitidas (cancelamento pelo sistema ou vindos do ADN)
- ``sincronizacao_adn`` cursor (NSU) da distribuição de documentos do Ambiente Nacional
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import NaiveDatetime
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Column, Field, LargeBinary, Session, SQLModel, create_engine, select

from emissor import config
from emissor.nfse.constants import Ambiente
from emissor.nfse.models import ConfigFiscal


def agora() -> datetime:
    """Hora local (America/Sao_Paulo) sem tzinfo — o SQLite não guarda fuso."""
    return datetime.now(ZoneInfo(config.TIMEZONE)).replace(microsecond=0, tzinfo=None)


def para_local(dt: datetime) -> datetime:
    """Converte datetime com fuso para hora local sem tzinfo (padrão do banco)."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(ZoneInfo(config.TIMEZONE)).replace(tzinfo=None)


class Configuracao(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    onboarding_concluido: bool = False
    ambiente: str = Ambiente.HOMOLOGACAO.value
    serie: int = 1
    proximo_ndps_homologacao: int = 1
    proximo_ndps_producao: int = 1
    config_fiscal_json: str = "{}"
    atualizado_em: NaiveDatetime = Field(default_factory=agora)

    @property
    def amb(self) -> Ambiente:
        return Ambiente(self.ambiente)

    def proximo_ndps(self) -> int:
        return self.proximo_ndps_producao if self.amb == Ambiente.PRODUCAO else self.proximo_ndps_homologacao

    def avancar_ndps(self, usado: int) -> None:
        if self.amb == Ambiente.PRODUCAO:
            self.proximo_ndps_producao = usado + 1
        else:
            self.proximo_ndps_homologacao = usado + 1

    def config_fiscal(self) -> ConfigFiscal | None:
        dados = json.loads(self.config_fiscal_json or "{}")
        if not dados.get("cnpj"):
            return None
        nomes = {f.name for f in fields(ConfigFiscal)}
        dados = {k: v for k, v in dados.items() if k in nomes}
        if dados.get("p_tot_trib_sn") not in (None, ""):
            dados["p_tot_trib_sn"] = Decimal(str(dados["p_tot_trib_sn"]))
        else:
            dados["p_tot_trib_sn"] = None
        return ConfigFiscal(**dados)

    def set_config_fiscal(self, cfg: ConfigFiscal) -> None:
        d = asdict(cfg)
        d["p_tot_trib_sn"] = str(cfg.p_tot_trib_sn) if cfg.p_tot_trib_sn is not None else None
        self.config_fiscal_json = json.dumps(d, ensure_ascii=False)


class CertificadoDB(SQLModel, table=True):
    __tablename__ = "certificado"
    id: int = Field(default=1, primary_key=True)
    pfx_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    senha_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    titular: str
    cnpj: str | None = None
    emissor: str
    valido_de: NaiveDatetime
    valido_ate: NaiveDatetime
    carregado_em: NaiveDatetime = Field(default_factory=agora)


class Cliente(SQLModel, table=True):
    documento: str = Field(primary_key=True)  # CNPJ/CPF sem máscara
    nome: str
    c_mun: str | None = None
    cep: str | None = None
    logradouro: str | None = None
    numero: str | None = None
    complemento: str | None = None
    bairro: str | None = None
    fone: str | None = None
    email: str | None = None
    inscricao_municipal: str | None = None
    situacao_cadastral: str | None = None  # ATIVA, BAIXADA... (da consulta de CNPJ)
    origem: str = "manual"  # xml | brasilapi | cnpja | manual
    atualizado_em: NaiveDatetime = Field(default_factory=agora)

    @property
    def endereco_completo(self) -> bool:
        return all([self.c_mun, self.cep, self.logradouro, self.bairro])


class LinhaTabela(SQLModel, table=True):
    __tablename__ = "linha_tabela"
    id: int | None = Field(default=None, primary_key=True)
    ordem: int = 0
    nome: str = ""
    documento: str = ""
    valor: str = ""  # decimal em texto ("450.00")
    descricao: str = ""
    ultima_emissao_id: int | None = Field(default=None, index=True)
    atualizado_em: NaiveDatetime = Field(default_factory=agora)


class Lote(SQLModel, table=True):
    id: str = Field(primary_key=True)
    ambiente: str
    status: str = "em_andamento"  # em_andamento | concluido | interrompido | cancelado
    total: int = 0
    motivo_interrupcao: str | None = None
    criado_em: NaiveDatetime = Field(default_factory=agora)
    finalizado_em: NaiveDatetime | None = None


class Emissao(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    lote_id: str | None = Field(default=None, index=True)
    linha_id: int | None = Field(default=None, index=True)
    ambiente: str
    # pendente | processando | autorizada | rejeitada | erro | nao_enviada | cancelada | substituida
    status: str = Field(default="pendente", index=True)
    serie: int | None = None
    n_dps: int | None = None
    id_dps: str | None = Field(default=None, index=True)
    tomador_documento: str
    tomador_nome: str
    valor: str
    descricao: str
    chave_acesso: str | None = Field(default=None, index=True)
    numero_nfse: str | None = None
    dh_emissao: NaiveDatetime | None = None
    dh_processamento: str | None = None
    erro_json: str | None = None
    alertas_json: str | None = None
    dps_path: str | None = None
    xml_path: str | None = None
    pdf_path: str | None = None
    tentativas: int = 0
    recuperada: bool = False
    criado_em: NaiveDatetime = Field(default_factory=agora)
    atualizado_em: NaiveDatetime = Field(default_factory=agora)

    def erro(self) -> dict[str, Any] | None:
        return json.loads(self.erro_json) if self.erro_json else None

    def alertas(self) -> list[dict[str, Any]]:
        return json.loads(self.alertas_json) if self.alertas_json else []


class EventoNFSe(SQLModel, table=True):
    """Evento de uma NFS-e emitida pelo sistema (cancelamento, substituição, manifestação...)."""

    __tablename__ = "evento_nfse"
    id: str = Field(primary_key=True)  # Id do evento ("EVT" + 59 dígitos)
    emissao_id: int | None = Field(default=None, index=True)
    chave_acesso: str = Field(index=True)
    ambiente: str
    tipo: str  # código do leiaute, ex. 101101
    n_seq: int = 1
    descricao: str
    motivo: str | None = None
    autor: str | None = None
    dh_evento: NaiveDatetime | None = None  # dhProc (processamento na Sefin)
    origem: str = "sistema"  # sistema (pedido feito aqui) | ambiente_nacional (portal, prefeitura...)
    xml_path: str | None = None
    registrado_em: NaiveDatetime = Field(default_factory=agora)


class SincronizacaoADN(SQLModel, table=True):
    """Último NSU lido da distribuição do ADN, por ambiente e CNPJ."""

    __tablename__ = "sincronizacao_adn"
    id: str = Field(primary_key=True)  # "<ambiente>:<cnpj>"
    ultimo_nsu: int = 0
    iniciada_em: NaiveDatetime | None = None
    concluida_em: NaiveDatetime | None = None  # última sincronização completa (sem erro)
    erro: str | None = None


_engine: Engine | None = None


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - configuração do driver
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def engine() -> Engine:
    global _engine
    if _engine is None:
        config.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{config.DB_FILE}", connect_args={"check_same_thread": False}, pool_pre_ping=True
        )
        SQLModel.metadata.create_all(_engine)
    return _engine


def reset_engine() -> None:
    """Para testes que trocam ``data/``."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def sessao() -> Iterator[Session]:
    with Session(engine(), expire_on_commit=False) as s:
        yield s


def obter_config(s: Session) -> Configuracao:
    cfg = s.get(Configuracao, 1)
    if cfg is None:
        cfg = Configuracao(id=1)
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
    return cfg


def obter_certificado(s: Session) -> CertificadoDB | None:
    return s.exec(select(CertificadoDB)).first()
