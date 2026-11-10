"""Banco SQLite (SQLModel). Um arquivo em ``data/emissor.db``.

Tabelas:
- ``configuracao``  uma linha por empresa (emitente): ambiente, série, contadores de nDPS, padrões fiscais (JSON) e
                    o certificado usado (o da empresa ou o de um procurador); a empresa 1 é a configuração de antes
- ``certificado``   PFX e senha criptografados + metadados (um por empresa ou um de procurador para várias)
- ``cliente``       cadastro de tomadores (por empresa, ou compartilhado na empresa 0)
- ``tributacao``    tributações predefinidas (código do serviço, ISS, PIS/COFINS, IBS/CBS) usadas por abas e notas avulsas
- ``aba``           abas da tabela de notas (cada uma pode ter tributação própria e clientes exclusivos)
- ``linha_tabela``  tabela de notas a emitir (persistente)
- ``lote``          execução de emissão em massa
- ``emissao``       uma DPS/NFS-e (histórico completo)
- ``evento_nfse``   eventos das NFS-e emitidas (cancelamento pelo sistema ou vindos do ADN)
- ``sincronizacao_adn`` cursor (NSU) da distribuição de documentos do Ambiente Nacional
- ``usuario``, ``sessao_usuario``, ``convite_usuario`` login (ver ``emissor.auth``)
- ``auditoria``     quem fez o quê (login, emissão, cancelamento, configurações, usuários)
- ``usuario_empresa`` empresas que cada usuário (não administrador da instalação) acessa e o papel nelas
- ``instancia``     opções da instalação (cadastro de clientes compartilhado)
- ``migracao``      passos de migração do banco já feitos

As tabelas com ``empresa_id`` são filtradas pela empresa do contexto (ver ``emissor.empresas``).
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
from sqlmodel import Column, Field, LargeBinary, Session, SQLModel, create_engine

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
    """Uma empresa (emitente). A tabela manteve o nome de quando havia uma só."""

    id: int | None = Field(default=None, primary_key=True)
    cnpj: str | None = None  # CNPJ da empresa (o mesmo de config_fiscal_json; existe antes do onboarding)
    nome: str | None = None  # nome curto mostrado no seletor (vazio = razão social do certificado/CNPJ)
    ativa: bool | None = True
    # certificado | procuracao (certificado de outro CNPJ/CPF com procuração eletrônica para emitir pela empresa)
    acesso: str | None = "certificado"
    certificado_id: int | None = None
    criado_em: NaiveDatetime | None = Field(default_factory=agora)
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

    def proximo_ndps(self, amb: Ambiente | None = None) -> int:
        """Próximo nDPS do ambiente informado (o do lote); sem ele, o ambiente atual das configurações."""
        amb = amb or self.amb
        return self.proximo_ndps_producao if amb == Ambiente.PRODUCAO else self.proximo_ndps_homologacao

    def avancar_ndps(self, usado: int, amb: Ambiente | None = None) -> None:
        if (amb or self.amb) == Ambiente.PRODUCAO:
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
        self.cnpj = cfg.cnpj


class CertificadoDB(SQLModel, table=True):
    __tablename__ = "certificado"
    id: int | None = Field(default=None, primary_key=True)
    pfx_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    senha_enc: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    titular: str
    cnpj: str | None = None
    cpf: str | None = None  # e-CPF (procurador pessoa física)
    emissor: str
    valido_de: NaiveDatetime
    valido_ate: NaiveDatetime
    carregado_em: NaiveDatetime = Field(default_factory=agora)


class Cliente(SQLModel, table=True):
    documento: str = Field(primary_key=True)  # CNPJ/CPF sem máscara
    # Empresa dona do cadastro (0 = compartilhado entre todas); preenchido sozinho (``emissor.empresas``).
    empresa_id: int | None = Field(default=None, primary_key=True)
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


class Tributacao(SQLModel, table=True):
    """Tributação predefinida: os campos de ``CAMPOS_TRIBUTACAO`` completos, em JSON."""

    id: int | None = Field(default=None, primary_key=True)
    empresa_id: int | None = Field(default=None, index=True)
    nome: str = Field(index=True)  # único por empresa (conferido em services/tributacao)
    dados_json: str = "{}"
    criado_em: NaiveDatetime = Field(default_factory=agora)
    atualizado_em: NaiveDatetime = Field(default_factory=agora)

    def dados(self) -> dict[str, Any]:
        return json.loads(self.dados_json or "{}")


class Aba(SQLModel, table=True):
    """Aba da tabela de notas. Sem tributação = padrões das configurações.

    ``clientes_json`` (lista de CNPJ/CPF) torna a aba exclusiva desses clientes: só eles podem ser emitidos nela, e
    emitir para eles em outra aba gera aviso.
    """

    id: int | None = Field(default=None, primary_key=True)
    empresa_id: int | None = Field(default=None, index=True)
    nome: str
    ordem: int = 0
    tributacao_id: int | None = Field(default=None, index=True)
    clientes_json: str = "[]"
    criado_em: NaiveDatetime = Field(default_factory=agora)

    def clientes(self) -> list[str]:
        return json.loads(self.clientes_json or "[]")


class LinhaTabela(SQLModel, table=True):
    __tablename__ = "linha_tabela"
    id: int | None = Field(default=None, primary_key=True)
    empresa_id: int | None = Field(default=None, index=True)
    aba_id: int | None = Field(default=None, index=True)
    ordem: int = 0
    nome: str = ""
    documento: str = ""
    valor: str = ""  # decimal em texto ("450.00")
    descricao: str = ""
    ultima_emissao_id: int | None = Field(default=None, index=True)
    atualizado_em: NaiveDatetime = Field(default_factory=agora)


class Lote(SQLModel, table=True):
    id: str = Field(primary_key=True)
    empresa_id: int | None = Field(default=None, index=True)
    ambiente: str
    status: str = "em_andamento"  # em_andamento | concluido | interrompido | cancelado
    total: int = 0
    motivo_interrupcao: str | None = None
    criado_em: NaiveDatetime = Field(default_factory=agora)
    finalizado_em: NaiveDatetime | None = None


class Emissao(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    empresa_id: int | None = Field(default=None, index=True)
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
    # Tributação usada (cópia feita ao criar o lote; None = padrões das configurações no momento do envio)
    tributacao_nome: str | None = None
    tributacao_json: str | None = None
    criado_em: NaiveDatetime = Field(default_factory=agora)
    atualizado_em: NaiveDatetime = Field(default_factory=agora)

    def tributacao(self) -> dict[str, Any] | None:
        return json.loads(self.tributacao_json) if self.tributacao_json else None

    def erro(self) -> dict[str, Any] | None:
        from .nfse.errors import com_aviso_homologacao, com_dicas_atuais

        if not self.erro_json:
            return None
        return com_aviso_homologacao(com_dicas_atuais(json.loads(self.erro_json)), self.ambiente)

    def alertas(self) -> list[dict[str, Any]]:
        return json.loads(self.alertas_json) if self.alertas_json else []


class EventoNFSe(SQLModel, table=True):
    """Evento de uma NFS-e emitida pelo sistema (cancelamento, substituição, manifestação...)."""

    __tablename__ = "evento_nfse"
    id: str = Field(primary_key=True)  # Id do evento ("EVT" + 59 dígitos)
    empresa_id: int | None = Field(default=None, index=True)
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


class Usuario(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)  # minúsculo; é o login
    nome: str = ""
    papel: str = "operador"  # admin | operador
    senha_hash: str | None = None  # Argon2id; None até aceitar o convite
    ativo: bool = True
    totp_segredo_enc: bytes | None = Field(default=None, sa_column=Column(LargeBinary, nullable=True))
    totp_ativo: bool = False
    totp_ultimo_passo: int = 0  # impede reusar o mesmo código
    codigos_recuperacao_json: str = "[]"  # SHA-256 dos códigos ainda não usados
    falhas_login: int = 0
    bloqueado_ate: NaiveDatetime | None = None
    criado_em: NaiveDatetime = Field(default_factory=agora)
    ultimo_login: NaiveDatetime | None = None


class SessaoUsuario(SQLModel, table=True):
    __tablename__ = "sessao_usuario"
    id: str = Field(primary_key=True)  # SHA-256 do token do cookie (o token em si não é guardado)
    usuario_id: int = Field(index=True)
    criada_em: NaiveDatetime = Field(default_factory=agora)
    ultimo_uso: NaiveDatetime = Field(default_factory=agora)
    ip: str | None = None
    user_agent: str | None = None


class ConviteUsuario(SQLModel, table=True):
    """Link de uso único para definir a senha (usuário novo ou senha redefinida pelo administrador)."""

    __tablename__ = "convite_usuario"
    id: str = Field(primary_key=True)  # SHA-256 do token
    usuario_id: int = Field(index=True)
    finalidade: str = "convite"  # convite | redefinicao (pelo administrador) | recuperacao (pelo e-mail)
    criado_em: NaiveDatetime = Field(default_factory=agora)
    expira_em: NaiveDatetime
    usado_em: NaiveDatetime | None = None


class Auditoria(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    quando: NaiveDatetime = Field(default_factory=agora, index=True)
    usuario_id: int | None = Field(default=None, index=True)
    usuario_email: str | None = None
    acao: str = Field(index=True)  # ex.: login, login_falhou, lote_criado, nota_cancelada
    detalhe: str | None = None
    ip: str | None = None
    empresa_id: int | None = None  # empresa em que a ação foi feita (None = da instalação: login, usuários...)


class UsuarioEmpresa(SQLModel, table=True):
    """Empresa que um usuário acessa. O administrador da instalação acessa todas e não precisa de linha aqui."""

    __tablename__ = "usuario_empresa"
    usuario_id: int = Field(primary_key=True)
    empresa_id: int = Field(primary_key=True)
    papel: str = "operador"  # admin (configurações e certificado da empresa) | operador


class Instancia(SQLModel, table=True):
    """Opções que valem para todas as empresas da instalação (linha única)."""

    id: int = Field(default=1, primary_key=True)
    compartilhar_clientes: bool = False
    atualizado_em: NaiveDatetime = Field(default_factory=agora)


class Migracao(SQLModel, table=True):
    nome: str = Field(primary_key=True)
    feita_em: NaiveDatetime = Field(default_factory=agora)


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
        _acrescentar_colunas(_engine)
        _migrar_empresas(_engine)
        from emissor import empresas

        empresas.instalar()
        empresas.limpar_cache()
    return _engine


def _acrescentar_colunas(eng: Engine) -> None:
    """Migração mínima: o ``create_all`` não altera tabelas existentes, então colunas novas de tabelas antigas
    entram aqui (sempre como colunas que aceitam nulo; o valor padrão vem do modelo ao ler)."""
    with eng.begin() as con:
        for tabela in SQLModel.metadata.sorted_tables:
            existentes = {r[1] for r in con.exec_driver_sql(f'PRAGMA table_info("{tabela.name}")')}
            for col in tabela.columns:
                if col.name not in existentes:
                    tipo = col.type.compile(dialect=eng.dialect)
                    con.exec_driver_sql(f'ALTER TABLE "{tabela.name}" ADD COLUMN "{col.name}" {tipo}')


TABELAS_DA_EMPRESA = ("aba", "linha_tabela", "tributacao", "lote", "emissao", "evento_nfse")


def _copia_de_seguranca(eng: Engine, motivo: str) -> None:
    """Cópia do banco antes de uma migração que refaz tabelas (``data/backups``)."""
    import sqlite3

    if not config.DB_FILE.exists():
        return
    pasta = config.DATA_DIR / "backups"
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"emissor-antes-{motivo}-{datetime.now():%Y%m%d-%H%M%S}.db"
    origem = sqlite3.connect(config.DB_FILE)
    try:
        alvo = sqlite3.connect(destino)
        with alvo:
            origem.backup(alvo)
        alvo.close()
    finally:
        origem.close()


def _migrar_empresas(eng: Engine) -> None:
    """Banco de antes das várias empresas → tudo na empresa 1 (idempotente; só refaz ``cliente`` uma vez).

    - ``empresa_id`` nulo vira 1 nas tabelas da empresa;
    - ``cliente`` passa a ter chave (documento, empresa_id): a tabela é refeita (SQLite não troca a chave);
    - o nome da tributação deixa de ser único na instalação (passa a ser por empresa);
    - a empresa 1 aponta para o certificado que já existia;
    - operadores que já existiam continuam acessando a empresa 1.
    """
    with eng.connect() as con:
        pk_cliente = [r[1] for r in con.exec_driver_sql('PRAGMA table_info("cliente")') if r[5]]
        feita = con.exec_driver_sql("SELECT 1 FROM migracao WHERE nome = 'empresas'").first()
    if feita and "empresa_id" in pk_cliente:
        return
    if pk_cliente and "empresa_id" not in pk_cliente:
        _copia_de_seguranca(eng, "varias-empresas")
    with eng.begin() as con:
        for t in TABELAS_DA_EMPRESA:
            con.exec_driver_sql(f'UPDATE "{t}" SET empresa_id = 1 WHERE empresa_id IS NULL')
        if pk_cliente and "empresa_id" not in pk_cliente:
            colunas = [r[1] for r in con.exec_driver_sql('PRAGMA table_info("cliente")') if r[1] != "empresa_id"]
            con.exec_driver_sql('ALTER TABLE "cliente" RENAME TO "cliente_antes_empresas"')
            Cliente.__table__.create(con)
            lista = ", ".join(f'"{c}"' for c in colunas)
            con.exec_driver_sql(
                f'INSERT INTO "cliente" ({lista}, "empresa_id") SELECT {lista}, 1 FROM "cliente_antes_empresas"'
            )
            con.exec_driver_sql('DROP TABLE "cliente_antes_empresas"')
        for idx in con.exec_driver_sql('PRAGMA index_list("tributacao")').fetchall():
            nome_idx, unico = idx[1], idx[2]
            cols = [r[2] for r in con.exec_driver_sql(f'PRAGMA index_info("{nome_idx}")')]
            if unico and cols == ["nome"] and not nome_idx.startswith("sqlite_autoindex"):
                con.exec_driver_sql(f'DROP INDEX "{nome_idx}"')
                con.exec_driver_sql(f'CREATE INDEX "{nome_idx}" ON "tributacao" ("nome")')
        con.exec_driver_sql(
            "UPDATE configuracao SET certificado_id = (SELECT MIN(id) FROM certificado) "
            "WHERE id = 1 AND certificado_id IS NULL"
        )
        con.exec_driver_sql("UPDATE configuracao SET ativa = 1 WHERE ativa IS NULL")
        con.exec_driver_sql(
            "UPDATE configuracao SET cnpj = json_extract(config_fiscal_json, '$.cnpj') "
            "WHERE cnpj IS NULL AND json_valid(config_fiscal_json) AND json_extract(config_fiscal_json, '$.cnpj') != ''"
        )
        con.exec_driver_sql("UPDATE configuracao SET acesso = 'certificado' WHERE acesso IS NULL")
        if not feita:
            con.exec_driver_sql(
                "INSERT INTO usuario_empresa (usuario_id, empresa_id, papel) "
                "SELECT id, 1, 'operador' FROM usuario WHERE papel != 'admin' "
                "AND id NOT IN (SELECT usuario_id FROM usuario_empresa WHERE empresa_id = 1)"
            )
            con.exec_driver_sql(
                "INSERT INTO migracao (nome, feita_em) VALUES ('empresas', ?)", (agora().isoformat(sep=" "),)
            )


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


class EmpresaInexistente(LookupError):
    pass


def obter_config(s: Session, empresa_id: int | None = None) -> Configuracao:
    """Configuração da empresa informada ou da empresa do contexto (a 1 é criada se ainda não existe)."""
    from emissor import empresas

    eid = empresa_id if empresa_id is not None else empresas.atual()
    cfg = s.get(Configuracao, eid)
    if cfg is None:
        if eid != 1:
            raise EmpresaInexistente(f"Empresa {eid} não encontrada.")
        cfg = Configuracao(id=1)
        s.add(cfg)
        s.commit()
        s.refresh(cfg)
    return cfg


def obter_certificado(s: Session, empresa_id: int | None = None) -> CertificadoDB | None:
    """Certificado que a empresa usa (o próprio A1 ou o do procurador)."""
    cfg = obter_config(s, empresa_id)
    return s.get(CertificadoDB, cfg.certificado_id) if cfg.certificado_id else None


def obter_cliente(s: Session, documento: str | None) -> Cliente | None:
    """Cliente do cadastro que vale para a empresa do contexto (o dela ou o compartilhado)."""
    from emissor import empresas

    if not documento:
        return None
    return s.get(Cliente, (documento, empresas.escopo_clientes()))


def obter_instancia(s: Session) -> Instancia:
    """Opções da instalação (linha única). Criada com ``INSERT OR IGNORE``: duas requisições ao mesmo tempo na
    primeira abertura não podem tentar inserir a mesma linha (aconteceu numa instalação própria)."""
    inst = s.get(Instancia, 1)
    if inst is None:
        s.connection().exec_driver_sql(
            "INSERT OR IGNORE INTO instancia (id, compartilhar_clientes, atualizado_em) VALUES (1, 0, ?)",
            (agora().isoformat(sep=" "),),
        )
        s.commit()
        inst = s.get(Instancia, 1)
    return inst
