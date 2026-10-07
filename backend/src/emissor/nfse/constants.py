"""Constantes do Sistema Nacional NFS-e (leiaute 1.01)."""

from __future__ import annotations

from enum import StrEnum

NS_NFSE = "http://www.sped.fazenda.gov.br/nfse"
NS_DSIG = "http://www.w3.org/2000/09/xmldsig#"
VERSAO_LEIAUTE = "1.01"

# Faixas de série da DPS por canal de emissão (fora da faixa → rejeição E0010).
SERIE_API_MIN = 1
SERIE_API_MAX = 49999

URL_CONSULTA_PUBLICA = "https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave={chave}"


class Ambiente(StrEnum):
    """``tpAmb`` da DPS: 1 = Produção, 2 = Homologação (Produção Restrita)."""

    PRODUCAO = "1"
    HOMOLOGACAO = "2"

    @property
    def sefin_url(self) -> str:
        return {
            Ambiente.PRODUCAO: "https://sefin.nfse.gov.br/SefinNacional",
            Ambiente.HOMOLOGACAO: "https://sefin.producaorestrita.nfse.gov.br/SefinNacional",
        }[self]

    @property
    def adn_url(self) -> str:
        return {
            Ambiente.PRODUCAO: "https://adn.nfse.gov.br",
            Ambiente.HOMOLOGACAO: "https://adn.producaorestrita.nfse.gov.br",
        }[self]

    @property
    def descricao(self) -> str:
        return {Ambiente.PRODUCAO: "Produção", Ambiente.HOMOLOGACAO: "Homologação (Produção Restrita)"}[self]
