"""Cadastro de clientes (tomadores) e consulta de CNPJ em APIs públicas.

BrasilAPI é gratuita e sem SLA: é usada só para preencher o cadastro de CNPJs novos,
com fallback no CNPJá público. A emissão nunca depende dessas APIs (ver CLAUDE.md).
Só os campos usados na nota são guardados (o quadro societário é descartado).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from sqlmodel import Session, select

from emissor import config
from emissor.db import Cliente, agora
from emissor.nfse.documentos import cnpj_valido, cpf_valido
from emissor.nfse.models import Endereco, Tomador, limpar_texto, normalizar_documento, somente_digitos

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(8.0, connect=5.0)


@dataclass
class ConsultaCnpj:
    encontrado: bool
    fonte: str | None = None
    cliente: Cliente | None = None
    erro: str | None = None


def _brasilapi(cnpj: str, http: httpx.Client) -> Cliente | None:
    r = http.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    tipo = (d.get("descricao_tipo_de_logradouro") or "").strip()
    lgr = (d.get("logradouro") or "").strip()
    return Cliente(
        documento=cnpj,
        nome=d.get("razao_social") or "",
        c_mun=str(d["codigo_municipio_ibge"]) if d.get("codigo_municipio_ibge") else None,
        cep=somente_digitos(d.get("cep")),
        logradouro=limpar_texto(f"{tipo} {lgr}" if tipo and not lgr.upper().startswith(tipo.upper()) else lgr),
        numero=limpar_texto(d.get("numero")),
        complemento=limpar_texto(d.get("complemento")),
        bairro=limpar_texto(d.get("bairro")),
        fone=somente_digitos(d.get("ddd_telefone_1")),
        email=(d.get("email") or None),
        situacao_cadastral=d.get("descricao_situacao_cadastral"),
        origem="brasilapi",
    )


def _cnpja(cnpj: str, http: httpx.Client) -> Cliente | None:
    r = http.get(f"https://publica.cnpj.ws/cnpj/{cnpj}")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    e = d.get("estabelecimento") or {}
    tipo = (e.get("tipo_logradouro") or "").strip()
    lgr = (e.get("logradouro") or "").strip()
    fone = somente_digitos(f"{e.get('ddd1') or ''}{e.get('telefone1') or ''}")
    cidade = e.get("cidade") or {}
    return Cliente(
        documento=cnpj,
        nome=d.get("razao_social") or "",
        c_mun=str(cidade["ibge_id"]) if cidade.get("ibge_id") else None,
        cep=somente_digitos(e.get("cep")),
        logradouro=limpar_texto(f"{tipo} {lgr}" if tipo else lgr),
        numero=limpar_texto(e.get("numero")),
        complemento=limpar_texto(e.get("complemento")),
        bairro=limpar_texto(e.get("bairro")),
        fone=fone,
        email=(e.get("email") or None),
        situacao_cadastral=e.get("situacao_cadastral"),
        origem="cnpja",
    )


def consultar_cnpj(documento: str, http: httpx.Client | None = None) -> ConsultaCnpj:
    cnpj = normalizar_documento(documento) or ""
    if not cnpj_valido(cnpj):
        return ConsultaCnpj(False, erro="CNPJ inválido (dígitos verificadores não conferem).")
    fechar = http is None
    http = http or httpx.Client(timeout=TIMEOUT, headers={"User-Agent": config.VER_APLIC})
    erros = []
    try:
        for nome, fn in (("brasilapi", _brasilapi), ("cnpja", _cnpja)):
            try:
                cli = fn(cnpj, http)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                log.warning("consulta de CNPJ falhou em %s: %s", nome, exc, extra={"cnpj": cnpj, "fonte": nome})
                erros.append(f"{nome}: {type(exc).__name__}")
                continue
            if cli is None:
                log.info("CNPJ não encontrado em %s", nome, extra={"cnpj": cnpj})
                return ConsultaCnpj(False, fonte=nome, erro="CNPJ não encontrado na base da Receita Federal.")
            log.info("CNPJ consultado", extra={"cnpj": cnpj, "fonte": nome})
            return ConsultaCnpj(True, fonte=nome, cliente=cli)
    finally:
        if fechar:
            http.close()
    return ConsultaCnpj(
        False,
        erro="Serviços de consulta de CNPJ indisponíveis no momento (" + "; ".join(erros) + "). "
        "Preencha o endereço manualmente ou tente mais tarde.",
    )


def upsert(s: Session, novo: Cliente, *, sobrescrever: bool = True) -> Cliente:
    atual = s.get(Cliente, novo.documento)
    if atual is None:
        novo.atualizado_em = agora()
        s.add(novo)
        return novo
    if sobrescrever:
        for campo in (
            "nome",
            "c_mun",
            "cep",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "fone",
            "email",
            "inscricao_municipal",
            "situacao_cadastral",
            "origem",
        ):
            valor = getattr(novo, campo)
            if valor not in (None, ""):
                setattr(atual, campo, valor)
        atual.atualizado_em = agora()
    s.add(atual)
    return atual


def de_tomador(t: Tomador, origem: str = "xml") -> Cliente:
    e = t.endereco
    return Cliente(
        documento=normalizar_documento(t.documento) or "",
        nome=t.nome,
        c_mun=e.c_mun if e else None,
        cep=e.cep if e else None,
        logradouro=e.logradouro if e else None,
        numero=e.numero if e else None,
        complemento=e.complemento if e else None,
        bairro=e.bairro if e else None,
        fone=t.fone,
        email=t.email,
        inscricao_municipal=t.inscricao_municipal,
        origem=origem,
    )


def para_tomador(c: Cliente | None, documento: str, nome: str) -> Tomador:
    """Tomador da DPS: cadastro quando existe; senão só documento + nome da tabela."""
    nome_final = (nome or "").strip() or (c.nome if c else "")
    if c is None:
        return Tomador(documento=documento, nome=nome_final)
    endereco = None
    if c.endereco_completo:
        endereco = Endereco(
            c_mun=c.c_mun,
            cep=c.cep,
            logradouro=c.logradouro,
            numero=c.numero or "S/N",
            bairro=c.bairro,
            complemento=c.complemento,
        )
    return Tomador(
        documento=documento,
        nome=nome_final,
        endereco=endereco,
        fone=c.fone,
        email=c.email,
        inscricao_municipal=c.inscricao_municipal,
    )


def listar(s: Session, termo: str | None = None) -> list[Cliente]:
    q = select(Cliente).order_by(Cliente.nome)
    itens = list(s.exec(q))
    if termo:
        t = termo.lower()
        doc = normalizar_documento(termo) or ""
        itens = [c for c in itens if t in c.nome.lower() or (doc and doc in c.documento)]
    return itens


def documento_valido(doc: str) -> bool:
    d = normalizar_documento(doc) or ""
    return cpf_valido(d) if len(d) == 11 else cnpj_valido(d)


# --- cadastro automático a partir de XMLs de NFS-e ---------------------------------------

_CAMPOS_CADASTRO = (
    "nome",
    "c_mun",
    "cep",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "fone",
    "email",
    "inscricao_municipal",
)
MAX_ARQUIVO = 5_000_000  # por XML
MAX_ZIP_DESCOMPACTADO = 200_000_000


@dataclass
class ResultadoImportacaoXml:
    notas_lidas: int = 0
    novos: list[str] = field(default_factory=list)
    completados: list[str] = field(default_factory=list)
    sem_alteracao: list[str] = field(default_factory=list)
    ignorados: list[dict] = field(default_factory=list)  # {"arquivo", "motivo"}


def _expandir_arquivos(arquivos: list[tuple[str, bytes]], res: ResultadoImportacaoXml) -> list[tuple[str, bytes]]:
    """Abre .zip (inclusive pastas dentro dele) e devolve só os .xml."""
    import io
    import zipfile

    saida = []
    for nome, conteudo in arquivos:
        if nome.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(conteudo)) as z:
                    total = sum(i.file_size for i in z.infolist())
                    if total > MAX_ZIP_DESCOMPACTADO:
                        res.ignorados.append(
                            {"arquivo": nome, "motivo": "ZIP grande demais (máx. 200 MB descompactado)."}
                        )
                        continue
                    for info in z.infolist():
                        if info.is_dir() or not info.filename.lower().endswith(".xml"):
                            continue
                        saida.append((f"{nome}/{info.filename}", z.read(info)))
            except zipfile.BadZipFile:
                res.ignorados.append({"arquivo": nome, "motivo": "Arquivo ZIP inválido ou corrompido."})
        elif nome.lower().endswith(".xml"):
            saida.append((nome, conteudo))
        else:
            res.ignorados.append({"arquivo": nome, "motivo": "Não é .xml nem .zip."})
    return saida


def importar_de_xmls(
    s: Session, arquivos: list[tuple[str, bytes]], cnpj_prestador: str | None
) -> ResultadoImportacaoXml:
    """Cadastra os tomadores das NFS-e enviadas.

    - Cliente novo: criado com os dados da nota mais recente dele.
    - Cliente já cadastrado: só os campos vazios são completados (nada editado é sobrescrito).
    - Notas em que o tomador é o próprio prestador são ignoradas.
    """
    from emissor.nfse.xml_reader import NFSeDoc, NfseXmlError, extrair_tomador

    res = ResultadoImportacaoXml()
    docs = []
    for nome, conteudo in _expandir_arquivos(arquivos, res):
        if len(conteudo) > MAX_ARQUIVO:
            res.ignorados.append({"arquivo": nome, "motivo": "Arquivo grande demais para uma NFS-e."})
            continue
        try:
            docs.append((nome, NFSeDoc.from_bytes(conteudo)))
        except NfseXmlError as exc:
            res.ignorados.append({"arquivo": nome, "motivo": str(exc)})
    res.notas_lidas = len(docs)

    # Mais recente primeiro: é ela que define os dados de um cliente novo.
    docs.sort(key=lambda x: x[1].data_processamento or "", reverse=True)
    prestador = normalizar_documento(cnpj_prestador) if cnpj_prestador else None
    vistos: set[str] = set()
    for nome, d in docs:
        t = extrair_tomador(d)
        if t is None:
            res.ignorados.append({"arquivo": nome, "motivo": "Nota sem tomador identificado."})
            continue
        novo = de_tomador(t, "xml")
        if not novo.documento or not documento_valido(novo.documento):
            res.ignorados.append({"arquivo": nome, "motivo": f"Documento do tomador inválido: {t.documento}."})
            continue
        if prestador and novo.documento == prestador:
            res.ignorados.append({"arquivo": nome, "motivo": "O tomador é o próprio prestador (nota recebida)."})
            continue
        if novo.documento in vistos:
            continue  # já tratado com uma nota mais recente
        vistos.add(novo.documento)
        atual = s.get(Cliente, novo.documento)
        if atual is None:
            novo.atualizado_em = agora()
            s.add(novo)
            res.novos.append(novo.documento)
            continue
        mudou = False
        for campo in _CAMPOS_CADASTRO:
            if getattr(atual, campo) in (None, "") and getattr(novo, campo) not in (None, ""):
                setattr(atual, campo, getattr(novo, campo))
                mudou = True
        if mudou:
            atual.atualizado_em = agora()
            s.add(atual)
            res.completados.append(atual.documento)
        else:
            res.sem_alteracao.append(atual.documento)
    log.info(
        "clientes importados de XMLs",
        extra={
            "notas": res.notas_lidas,
            "novos": len(res.novos),
            "completados": len(res.completados),
            "sem_alteracao": len(res.sem_alteracao),
            "ignorados": len(res.ignorados),
        },
    )
    return res
