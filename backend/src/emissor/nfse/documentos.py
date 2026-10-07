"""Validação de CPF e CNPJ (inclusive CNPJ alfanumérico, IN RFB 2.229/2024)."""

from __future__ import annotations

import re

from emissor.nfse.models import normalizar_documento

_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")


def _valor_cnpj(ch: str) -> int:
    # Regra do CNPJ alfanumérico: valor = código ASCII - 48 (dígitos mantêm o valor).
    return ord(ch) - 48


def cnpj_valido(valor: str | None) -> bool:
    s = normalizar_documento(valor)
    if not s or not _CNPJ_RE.match(s) or len(set(s)) == 1:
        return False
    pesos1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    pesos2 = [6] + pesos1
    for pesos, pos in ((pesos1, 12), (pesos2, 13)):
        soma = sum(_valor_cnpj(c) * p for c, p in zip(s[:pos], pesos, strict=True))
        dv = 11 - soma % 11
        dv = 0 if dv >= 10 else dv
        if int(s[pos]) != dv:
            return False
    return True


def cpf_valido(valor: str | None) -> bool:
    s = re.sub(r"\D", "", valor or "")
    if len(s) != 11 or len(set(s)) == 1:
        return False
    for pos in (9, 10):
        soma = sum(int(s[i]) * (pos + 1 - i) for i in range(pos))
        dv = (soma * 10) % 11 % 10
        if int(s[pos]) != dv:
            return False
    return True


def documento_valido(valor: str | None) -> bool:
    s = normalizar_documento(valor) or ""
    return cpf_valido(s) if len(s) == 11 else cnpj_valido(s)


def cnpj_alfanumerico(valor: str | None) -> bool:
    s = normalizar_documento(valor) or ""
    return len(s) == 14 and not s.isdigit()


def formatar_documento(valor: str | None) -> str:
    s = normalizar_documento(valor) or ""
    if len(s) == 14:
        return f"{s[:2]}.{s[2:5]}.{s[5:8]}/{s[8:12]}-{s[12:]}"
    if len(s) == 11:
        return f"{s[:3]}.{s[3:6]}.{s[6:9]}-{s[9:]}"
    return s
