"""Erros da Sefin Nacional normalizados para exibição ao usuário.

A API devolve erros em formatos variados (``erros``/``erro``/lista/``codigo``+``mensagem``,
com chaves em maiúsculas ou minúsculas). Tudo vira ``MensagemSefin``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class TipoErro(StrEnum):
    VALIDACAO_LOCAL = "validacao_local"  # nem chegou a ser enviado
    REJEICAO = "rejeicao"  # Sefin analisou e recusou
    AUTENTICACAO = "autenticacao"  # certificado recusado no mTLS / sem permissão
    INDISPONIVEL = "indisponivel"  # 5xx, 429, falha de conexão: não processado
    AMBIGUO = "ambiguo"  # pode ter sido processado (timeout de leitura etc.)
    RESPOSTA_INVALIDA = "resposta_invalida"


@dataclass
class MensagemSefin:
    codigo: str | None
    descricao: str
    complemento: str | None = None
    dica: str | None = None

    def texto(self) -> str:
        partes = [f"[{self.codigo}] " if self.codigo else "", self.descricao]
        if self.complemento:
            partes.append(f" — {self.complemento}")
        return "".join(partes)


@dataclass(eq=False)
class SefinError(Exception):
    tipo: TipoErro
    resumo: str
    mensagens: list[MensagemSefin] = field(default_factory=list)
    http_status: int | None = None
    corpo: str | None = None

    def __post_init__(self) -> None:
        super().__init__(self.resumo)

    def __str__(self) -> str:
        detalhes = "; ".join(m.texto() for m in self.mensagens)
        return f"{self.resumo}: {detalhes}" if detalhes else self.resumo

    @property
    def pode_ter_sido_processada(self) -> bool:
        return self.tipo == TipoErro.AMBIGUO

    @property
    def transitorio(self) -> bool:
        return self.tipo in (TipoErro.INDISPONIVEL, TipoErro.AMBIGUO)

    @property
    def indica_duplicidade(self) -> bool:
        texto = " ".join(m.texto().lower() for m in self.mensagens)
        return any(k in texto for k in ("já existe", "ja existe", "duplic", "já foi", "ja foi"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "tipo": self.tipo.value,
            "resumo": self.resumo,
            "http_status": self.http_status,
            "mensagens": [asdict(m) for m in self.mensagens],
        }


# Dicas por código conhecido (confirmado em documentação/comunidade).
_DICAS_CODIGO = {
    "E0010": "A série não pertence à faixa do canal de emissão. Para sistema próprio use série entre 1 e 49999 "
    "(Configurações → Série).",
    # Eventos (cancelamento) — Anexo II do leiaute.
    "E0822": "O prazo que o município dá para cancelar a nota já passou. Fora do prazo, peça a "
    "“Solicitação de Análise Fiscal para Cancelamento” no Portal Nacional da NFS-e ou procure a prefeitura.",
    "E0823": "O valor da nota passa do limite que o município permite cancelar direto. Peça a análise fiscal "
    "para cancelamento no Portal Nacional da NFS-e ou procure a prefeitura.",
    "E0824": "O município não permite cancelar direto nota sem tomador identificado. Procure a prefeitura.",
    "E0827": "A nota já tem tributos recolhidos vinculados e o município não permite cancelá-la. Procure a prefeitura.",
    "E0831": "O cancelamento deve ser pedido ao sistema que gerou a nota (ex.: emissor próprio da prefeitura).",
    "E0840": "A nota já tem um evento que impede o cancelamento (já cancelada, substituída ou bloqueada). "
    "Use “Atualizar situação” para trazer o estado atual do Ambiente Nacional.",
    "E0812": "O CNPJ do autor do cancelamento precisa ser o mesmo do certificado digital carregado.",
    "E1831": "A nota não foi encontrada no Ambiente Nacional. Confira se ela é deste ambiente (homologação/produção).",
    "E1843": "Data/hora do pedido posterior à da Sefin: confira o relógio do servidor (data, hora e fuso).",
    "E1845": "O ambiente do pedido (homologação/produção) é diferente do ambiente da nota.",
    "E1805": "Este evento já foi registrado antes (pedido em duplicidade).",
    "E0802": "Este evento já foi registrado antes (pedido em duplicidade).",
}

# Dicas por palavra-chave, para códigos ainda não mapeados.
_DICAS_TEXTO = [
    (
        ("forbidden", "access is denied", "acesso negado", "não autorizado"),
        "A Sefin recusou o certificado. Confira se é um e-CNPJ A1 ICP-Brasil válido, do mesmo CNPJ do prestador, "
        "e se a empresa está habilitada no ambiente escolhido (homologação ou produção).",
    ),
    (
        ("certificado",),
        "Verifique se o certificado A1 carregado é da empresa emitente e está válido (Configurações → Certificado).",
    ),
    (("assinatura",), "Problema na assinatura digital do XML. Envie o arquivo de diagnóstico para suporte."),
    (("série", "serie"), "Confira a série configurada (faixa 1–49999 para sistema próprio)."),
    (("data de emissão", "dhemi", "data/hora"), "Confira se o relógio do servidor está correto (data, hora e fuso)."),
    (("competência", "dcompet"), "A data de competência é a data da emissão; confira o relógio do servidor."),
    (("tomador",), "Revise o cadastro do cliente (CNPJ/CPF, nome e endereço) em Clientes."),
    (("cep",), "Revise o CEP do endereço do cliente em Clientes."),
    (("município", "municipio"), "Revise o código IBGE do município (cliente ou configurações)."),
    (
        ("inscrição municipal", "inscricao municipal"),
        "Confira a Inscrição Municipal nas configurações do prestador ou no cliente.",
    ),
    (
        ("simples nacional", "opsimpnac", "regaptribsn"),
        "Confira o regime do Simples Nacional nas configurações fiscais.",
    ),
    (
        ("código de tributação", "ctribnac", "nbs"),
        "Confira o código de tributação nacional/NBS nas configurações fiscais.",
    ),
    (
        ("alíquota", "aliquota"),
        "Alíquota definida pelo município; confira a parametrização municipal ou as configurações.",
    ),
    (("convênio", "convenio", "conveniado"), "O município precisa estar conveniado ao Sistema Nacional NFS-e."),
]


def dica_para(codigo: str | None, texto: str) -> str | None:
    if codigo and codigo.upper() in _DICAS_CODIGO:
        return _DICAS_CODIGO[codigo.upper()]
    t = texto.lower()
    for chaves, dica in _DICAS_TEXTO:
        if any(k in t for k in chaves):
            return dica
    return None


def _ci(d: dict, *nomes: str) -> Any:
    """Busca chave ignorando maiúsculas/minúsculas."""
    lower = {k.lower(): v for k, v in d.items()} if isinstance(d, dict) else {}
    for n in nomes:
        if n.lower() in lower and lower[n.lower()] not in (None, ""):
            return lower[n.lower()]
    return None


def _mensagem(item: Any) -> MensagemSefin:
    if isinstance(item, str):
        return MensagemSefin(codigo=None, descricao=item, dica=dica_para(None, item))
    codigo = _ci(item, "codigo", "code", "cod")
    descricao = _ci(item, "descricao", "mensagem", "message", "descricaoErro", "detail", "title") or json.dumps(
        item, ensure_ascii=False
    )
    complemento = _ci(item, "complemento", "correcao", "detalhe")
    codigo = str(codigo) if codigo is not None else None
    return MensagemSefin(
        codigo=codigo,
        descricao=str(descricao),
        complemento=str(complemento) if complemento else None,
        dica=dica_para(codigo, f"{descricao} {complemento or ''}"),
    )


def extrair_mensagens(corpo: Any) -> list[MensagemSefin]:
    """Extrai mensagens de erro/alerta de qualquer formato conhecido."""
    if corpo is None:
        return []
    if isinstance(corpo, list):
        return [_mensagem(i) for i in corpo]
    if isinstance(corpo, dict):
        for chave in ("erros", "erro", "errors", "error", "mensagens"):
            v = _ci(corpo, chave)
            if v:
                return extrair_mensagens(v if isinstance(v, list) else [v])
        if _ci(corpo, "codigo", "mensagem", "descricao", "message", "title"):
            return [_mensagem(corpo)]
    if isinstance(corpo, str) and corpo.strip():
        texto = texto_de_html(corpo) if _parece_html(corpo) else corpo.strip()
        return [MensagemSefin(codigo=None, descricao=texto[:2000], dica=dica_para(None, texto))]
    return []


def _parece_html(s: str) -> bool:
    inicio = s.lstrip()[:200].lower()
    return inicio.startswith("<!doctype html") or inicio.startswith("<html") or "<body" in s[:2000].lower()


def texto_de_html(html: str) -> str:
    """Páginas de erro do servidor web (ex.: IIS 403) viram texto legível.

    Prioriza título e cabeçalhos (h1-h3); ignora CSS/JS.
    """
    import html as html_mod
    import re

    sem_script = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", html)
    partes = []
    for tag in ("title", "h1", "h2", "h3"):
        for m in re.findall(rf"(?is)<{tag}[^>]*>(.*?)</{tag}>", sem_script):
            t = " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", m)).split())
            if t and t not in partes and t.lower() not in ("server error",):
                partes.append(t)
    if not partes:
        partes = [" ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", sem_script)).split())]
    return " — ".join(partes)


def extrair_alertas(corpo: Any) -> list[MensagemSefin]:
    if isinstance(corpo, dict):
        v = _ci(corpo, "alertas", "alerts")
        if v:
            return [_mensagem(i) for i in (v if isinstance(v, list) else [v])]
    return []
