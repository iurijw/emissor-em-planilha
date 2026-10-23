"""Variáveis na descrição do serviço, trocadas pela data da emissão.

Ex.: "Honorários contábeis de {mes}/{ano}" emitida em 05/11/2026 sai "Honorários contábeis de novembro/2026", e
"referente a {mes_anterior}" sai "referente a outubro". A tabela guarda o texto com as variáveis (vale para todo
mês); a troca acontece quando a DPS é montada, pelo ``dhEmi``, e a emissão grava o texto que foi na nota.

O nome aceita acento e maiúsculas: ``{Mês}`` → "Novembro", ``{MES}`` → "NOVEMBRO". Variável desconhecida é erro na
validação (ninguém quer "{mes_atua}" impresso na nota).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import date

from emissor.db import agora

MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _anterior(d: date) -> date:
    return date(d.year - 1, 12, 1) if d.month == 1 else date(d.year, d.month - 1, 1)


# nome → (descrição para a tela, valor na data da emissão)
VARIAVEIS: dict[str, tuple[str, Callable[[date], str]]] = {
    "mes": ("nome do mês da emissão", lambda d: MESES[d.month - 1]),
    "mes_numero": ("número do mês, com dois dígitos", lambda d: f"{d.month:02d}"),
    "ano": ("ano da emissão", lambda d: str(d.year)),
    "competencia": ("mês/ano da emissão", lambda d: f"{d.month:02d}/{d.year}"),
    "mes_anterior": ("nome do mês anterior", lambda d: MESES[_anterior(d).month - 1]),
    "mes_anterior_numero": ("número do mês anterior", lambda d: f"{_anterior(d).month:02d}"),
    "ano_mes_anterior": ("ano do mês anterior (em janeiro, o ano passado)", lambda d: str(_anterior(d).year)),
    "competencia_anterior": ("mês/ano do mês anterior", lambda d: f"{_anterior(d):%m/%Y}"),
    "data": ("data da emissão", lambda d: f"{d:%d/%m/%Y}"),
}

_PADRAO = re.compile(r"\{([^{}\s]{1,40})\}")


def _chave(nome: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return sem_acento.lower()


def desconhecidas(texto: str) -> list[str]:
    """Variáveis do texto que não existem (como foram escritas, com as chaves)."""
    return list(dict.fromkeys(m.group(0) for m in _PADRAO.finditer(texto or "") if _chave(m.group(1)) not in VARIAVEIS))


def substituir(texto: str, quando: date) -> str:
    """Troca as variáveis conhecidas pelo valor na data ``quando``; as desconhecidas ficam como estão."""

    def troca(m: re.Match) -> str:
        nome = m.group(1)
        item = VARIAVEIS.get(_chave(nome))
        if item is None:
            return m.group(0)
        valor = item[1](quando)
        letras = [c for c in nome if c.isalpha()]
        if letras and all(c.isupper() for c in letras):
            return valor.upper()
        if letras and letras[0].isupper():
            return valor[:1].upper() + valor[1:]
        return valor

    return _PADRAO.sub(troca, texto or "")


def lista() -> list[dict[str, str]]:
    """Para a tela: nome, descrição e exemplo de hoje."""
    hoje = agora().date()
    return [{"valor": "{" + n + "}", "descricao": d, "exemplo": f(hoje)} for n, (d, f) in VARIAVEIS.items()]
