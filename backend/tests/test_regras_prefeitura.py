"""Leitura das regras que a prefeitura cadastrou no ADN, com respostas no formato real (conferido em 15/11/2026)."""

from __future__ import annotations

from datetime import date

import pytest

from emissor.nfse.constants import Ambiente
from emissor.nfse.errors import SefinError, TipoErro
from emissor.nfse.models import ConfigFiscal
from emissor.services import parametros, regras_prefeitura

COMP = date(2026, 11, 15)
CURITIBA = "4106902"

# Recorte da resposta real de GET /parametrizacao/4106902/{data}/retencoes
RETENCOES = {
    "retencoes": {
        "artigoSexto": {
            "habilitado": True,
            "historico": [{"dataInicioVigencia": "2024-09-01T00:00:00", "dataFimVigencia": None}],
        },
        "retencoesMunicipais": [
            {
                "descricao": "XI - a pessoa jurídica de direito público, ainda que imune ou isenta, tomadora ou "
                "intermediária dos serviços descritos nos subitens 3.05, 7.02, 7.04 ",
                "dataInicioVigencia": "2024-01-01T00:00:00",
                "dataFimVigencia": None,
                "tiposRetencao": [2],
                "servicos": [
                    {
                        "codigoCompleto": "07.11.01.000",
                        "historico": [{"dataInicioVigencia": "2024-01-01T00:00:00", "dataFimVigencia": None}],
                    }
                ],
            },
            {
                "descricao": "X - o tomador ou intermediário do serviço proveniente do exterior do País",
                "dataInicioVigencia": "2024-01-01T00:00:00",
                "dataFimVigencia": None,
                "tiposRetencao": [2, 3],
                "servicos": [
                    {
                        "codigoCompleto": "01.01.01.000",
                        "historico": [
                            {"dataInicioVigencia": "2024-01-01T00:00:00", "dataFimVigencia": "2025-01-01T00:00:00"}
                        ],
                    }
                ],
            },
        ],
    }
}
SEM_RETENCOES = {
    "retencoes": None,
    "mensagem": "Parâmetros de retenções do Artigo Sexto não encontrados para a competência.",
}
REGIMES = {  # Uberlândia, 04.16.01.000: a chave do regime muda a cada consulta
    "regimesEspeciais": {
        "04.16.01.000": {
            "700731623": [{"situacao": 3, "dataInicio": "2025-12-29T00:00:00", "dataFim": None, "observacoes": ""}]
        }
    },
    "mensagem": "Parâmetros de regimes especiais recuperados com sucesso.",
}
SEM_REGIMES = {"regimesEspeciais": None, "mensagem": "Parâmetros de regimes especiais não encontrados."}
HISTORICO = {
    "aliquotas": {"07.11.01.000": [
        {"Incidencia": "SIM", "Aliq": 4.0, "DtIni": "2023-08-30T00:00:00", "DtFim": "2026-10-31T00:00:00"},
        {"Incidencia": "SIM", "Aliq": 5.0, "DtIni": "2026-11-01T00:00:00", "DtFim": None},
    ]},
    "mensagem": "Histórico de alíquotas recuperadas com sucesso.",
}  # fmt: skip


class Fake:
    def __init__(self, respostas: dict[str, tuple[int, object]]):
        self.respostas = respostas
        self.caminhos: list[str] = []

    def __call__(self, ambiente, cert):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def parametrizacao(self, caminho: str):
        self.caminhos.append(caminho)
        for fim, r in self.respostas.items():
            if caminho.endswith(fim):
                if isinstance(r, Exception):
                    raise r
                return r
        return 404, {"mensagem": "não encontrado"}


@pytest.fixture(autouse=True)
def ligado(monkeypatch, certificado):
    monkeypatch.setattr(parametros, "ATIVA", True)
    monkeypatch.setattr(regras_prefeitura.cert_service, "carregar", lambda: certificado)
    regras_prefeitura.limpar_cache()


def _cfg(**kw) -> ConfigFiscal:
    return ConfigFiscal(**({"cnpj": "11444777000161", "c_mun_emissor": CURITIBA, "c_trib_nac": "071101"} | kw))


def test_retencao_cadastrada_e_aliquota_que_mudou():
    fake = Fake(
        {
            "/retencoes": (200, RETENCOES),
            "/historicoaliquotas": (200, HISTORICO),
            "/regimes_especiais": (404, SEM_REGIMES),
        }
    )
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(), COMP, client_factory=fake)
    assert r.mensagem is None and r.municipio == CURITIBA
    tipos = {(x.tipo, x.nivel) for x in r.regras}
    assert tipos == {("retencao", "aviso"), ("aliquota", "aviso")}, r.regras
    ret = next(x for x in r.regras if x.tipo == "retencao")
    assert (
        "pode ser retido pelo tomador" in ret.mensagem and "“XI - a pessoa jurídica de direito público" in ret.mensagem
    )
    ali = next(x for x in r.regras if x.tipo == "aliquota")
    assert "de 4% para 5% em 01/11/2026" in ali.mensagem
    assert r.historico_aliquotas[0] == {"aliquota": "5.00", "inicio": "2026-11-01", "fim": None}
    assert "4106902/07.11.01.000/2026-11-15/regimes_especiais" in fake.caminhos


def test_retido_sem_cadastro_e_servico_de_vigencia_encerrada():
    # 01.01: a retenção (exterior) acabou em 2025 → retido sem cadastro vale um aviso.
    fake = Fake({"/retencoes": (200, RETENCOES), "/regimes_especiais": (404, SEM_REGIMES)})
    r = regras_prefeitura.conferir(
        Ambiente.PRODUCAO, _cfg(c_trib_nac="010101", tp_ret_issqn="2"), COMP, client_factory=fake
    )
    assert [(x.tipo, x.nivel, x.titulo) for x in r.regras] == [
        ("retencao", "aviso", "Retenção sem cadastro na prefeitura")
    ]
    # Retido e cadastrado: só informação.
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(tp_ret_issqn="2"), COMP, client_factory=fake)
    assert [(x.tipo, x.nivel) for x in r.regras] == [("retencao", "info")]


def test_artigo_sexto_e_municipio_sem_retencoes():
    fake = Fake({"/retencoes": (200, RETENCOES)})
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(c_trib_nac="070201"), COMP, client_factory=fake)
    assert any("art. 6º" in x.mensagem and x.nivel == "aviso" for x in r.regras), r.regras
    regras_prefeitura.limpar_cache()
    fake = Fake({"/retencoes": (404, SEM_RETENCOES)})
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(), COMP, client_factory=fake)
    assert r.regras == [] and r.mensagem is None


def test_regime_especial():
    fake = Fake({"/regimes_especiais": (200, REGIMES)})
    cfg = _cfg(c_mun_emissor="3170206", c_trib_nac="041601")
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, cfg, COMP, client_factory=fake)
    assert [(x.tipo, x.nivel) for x in r.regras] == [("regime_especial", "aviso")]
    assert "sociedade de profissionais" in r.regras[0].mensagem
    regras_prefeitura.limpar_cache()
    r = regras_prefeitura.conferir(
        Ambiente.PRODUCAO,
        _cfg(c_mun_emissor="3170206", c_trib_nac="041601", reg_esp_trib="6"),
        COMP,
        client_factory=fake,
    )
    assert [(x.tipo, x.nivel) for x in r.regras] == [("regime_especial", "info")]


def test_beneficio_de_outro_municipio_nao_consulta():
    fake = Fake({})
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(n_bm="42054070400001"), COMP, client_factory=fake)
    assert any(x.titulo == "Benefício de outro município" for x in r.regras)
    assert not any("/beneficio" in c for c in fake.caminhos)


def test_falha_do_adn_fica_sem_conferir_e_e_guardada():
    erro = SefinError(TipoErro.INDISPONIVEL, "Erro interno na Sefin Nacional (HTTP 503)")
    fake = Fake({"/retencoes": erro, "/historicoaliquotas": (200, HISTORICO), "/regimes_especiais": (404, SEM_REGIMES)})
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(), COMP, client_factory=fake)
    assert r.mensagem and "as retenções" in r.mensagem
    antes = len(fake.caminhos)
    regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(), COMP, client_factory=fake)
    assert len(fake.caminhos) == antes  # respostas e falha guardadas


def test_fora_do_municipio_e_sem_tributacao():
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(c_loc_prestacao="3550308"), COMP, client_factory=Fake({}))
    assert r.municipio is None and r.regras == [] and "outro município" in (r.mensagem or "")
    r = regras_prefeitura.conferir(Ambiente.PRODUCAO, _cfg(trib_issqn="2"), COMP, client_factory=Fake({}))
    assert not any(x.tipo == "retencao" for x in r.regras)
