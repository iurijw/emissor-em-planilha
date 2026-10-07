from decimal import Decimal

import pytest

from emissor.nfse.documentos import (
    cnpj_alfanumerico,
    cnpj_valido,
    cpf_valido,
    documento_valido,
    formatar_documento,
)
from emissor.nfse.models import formatar_decimal, limpar_texto, normalizar_documento


@pytest.mark.parametrize("cnpj", ["11444777000161", "11.444.777/0001-61", "11222333000181"])
def test_cnpj_valido(cnpj):
    assert cnpj_valido(cnpj)


@pytest.mark.parametrize("cnpj", ["11444777000160", "11111111111111", "123", "", None])
def test_cnpj_invalido(cnpj):
    assert not cnpj_valido(cnpj)


def test_cnpj_alfanumerico_exemplo_receita():
    # Exemplo publicado pela Receita Federal para o CNPJ alfanumérico.
    assert cnpj_valido("12.ABC.345/01DE-35")
    assert cnpj_alfanumerico("12ABC34501DE35")
    assert not cnpj_valido("12ABC34501DE36")


def test_cpf():
    assert cpf_valido("529.982.247-25")
    assert not cpf_valido("529.982.247-24")
    assert documento_valido("52998224725")


def test_formatar_documento():
    assert formatar_documento("11444777000161") == "11.444.777/0001-61"
    assert formatar_documento("52998224725") == "529.982.247-25"
    assert normalizar_documento("12.abc.345/01de-35") == "12ABC34501DE35"


def test_limpar_texto_remove_fora_do_latin1_e_espacos():
    assert limpar_texto("  Honorários “09/2026” – ok 🚀  ") == 'Honorários "09/2026" - ok'
    assert limpar_texto("linha 1\r\n\n\n\nlinha   2", multilinha=True) == "linha 1\n\nlinha 2"
    assert limpar_texto("a\nb") == "a b"
    assert limpar_texto("   ") is None
    assert limpar_texto("abcdef", max_len=3) == "abc"


def test_formatar_decimal():
    assert formatar_decimal(Decimal("450")) == "450.00"
    assert formatar_decimal("1720.005") == "1720.01"
    assert formatar_decimal(11.08) == "11.08"
