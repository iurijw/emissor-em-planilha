from datetime import date

from emissor.services import variaveis


def test_troca_pela_data_e_respeita_maiusculas():
    texto = "Honorários de {mes}/{ano} - {Mês} {MES} ({mes_numero}) em {data}"
    assert variaveis.substituir(texto, date(2026, 3, 5)) == (
        "Honorários de março/2026 - Março MARÇO (03) em 05/03/2026"
    )


def test_mes_anterior_em_janeiro_vai_para_o_ano_passado():
    texto = "{mes_anterior} {mes_anterior_numero}/{ano_mes_anterior} = {competencia_anterior}; atual {competencia}"
    assert variaveis.substituir(texto, date(2027, 1, 10)) == "dezembro 12/2026 = 12/2026; atual 01/2027"


def test_desconhecidas_ficam_e_sao_apontadas():
    texto = "Serviço {mes_atual} e {ano} {x y} {}"
    assert variaveis.desconhecidas(texto) == ["{mes_atual}"]
    assert variaveis.substituir(texto, date(2026, 10, 1)) == "Serviço {mes_atual} e 2026 {x y} {}"
