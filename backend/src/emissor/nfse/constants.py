"""Constantes do Sistema Nacional NFS-e (leiaute 1.01)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

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


# Pasta do XSD (``xsd/<pasta>``) usada em cada ambiente. A NT 009 (finNFSe em infDPS, CST/cClassTrib em
# IBSCBS/valores/trib, vAjusteBC) entrou na Produção Restrita em 20/10/2026 e entra na Produção em 01/12/2026
# (comunicado do CGNFS-e de 20/10/2026: o leiaute atual vale até 30/11/2026). O builder e o XSD seguem ``leiaute()``.
# Em 25/11/2026 a Produção Restrita passou ao pacote "Esquemas XSD Produção Restrita 20261124" (``v1_01_nt009_20261124``):
# indDest/dest saem do grupo IBSCBS e vão para infDPS (indDest obrigatório em toda DPS, logo depois de toma), o comExt
# troca vServMoeda/nDI/nRE por nFatura/vFatura/nDUIMP/nDUE/nContCambio e entram condomínios, cMun do evento e
# vAjusteBCIBSCBSComExt. A Produção continua indo para ``v1_01_nt009`` (20/10) em 01/12 até a Sefin publicar o pacote de
# produção: se ele for o de 24/11, basta trocar ``LEIAUTE_NT009_PRODUCAO``.
# Em 26/11/2026 de manhã a Produção Restrita recusou indDest em infDPS (E1235, "esperado interm, serv") e, à tarde,
# passou a exigi-lo (E1235, "esperado indDest", visto às 15:45 e em 27/11): a Sefin troca de pacote sem aviso. Por isso
# o pacote da NT 009 de cada ambiente é só o ponto de partida: uma recusa E1235 sobre o indDest troca o pacote do
# ambiente (``trocar_pacote_nt009``) e a mesma DPS é montada de novo e reenviada (``services/emissao.py``). A troca vale
# para o processo inteiro até o servidor reiniciar.
LEIAUTE_ANTERIOR = "v1_01"
LEIAUTE_NT009 = "v1_01_nt009"
LEIAUTE_NT009_20261124 = "v1_01_nt009_20261124"
LEIAUTES_NT009 = (LEIAUTE_NT009, LEIAUTE_NT009_20261124)
LEIAUTE_NT009_PRODUCAO = LEIAUTE_NT009
INICIO_NT009_PRODUCAO = datetime(2026, 12, 1, tzinfo=ZoneInfo("America/Sao_Paulo"))
LEIAUTE_NT009_HOMOLOGACAO = LEIAUTE_NT009_20261124


def leiaute(ambiente: Ambiente, quando: datetime | None = None) -> str:
    """Pasta do XSD do ambiente na data ``quando`` (com fuso; sem ela, agora). A homologação já está toda na NT 009."""
    quando = quando or datetime.now(ZoneInfo("America/Sao_Paulo"))
    if ambiente is Ambiente.HOMOLOGACAO:
        return LEIAUTE_NT009_HOMOLOGACAO
    return LEIAUTE_NT009_PRODUCAO if quando >= INICIO_NT009_PRODUCAO else LEIAUTE_ANTERIOR


def trocar_pacote_nt009(ambiente: Ambiente, enviado: str) -> str | None:
    """A Sefin recusou o pacote ``enviado`` da NT 009 (E1235 sobre o indDest): passa o ambiente ao outro pacote e
    devolve a pasta nova (``None`` se ``enviado`` não é da NT 009)."""
    global LEIAUTE_NT009_HOMOLOGACAO, LEIAUTE_NT009_PRODUCAO
    if enviado not in LEIAUTES_NT009:
        return None
    outro = LEIAUTE_NT009 if enviado == LEIAUTE_NT009_20261124 else LEIAUTE_NT009_20261124
    if ambiente is Ambiente.HOMOLOGACAO:
        LEIAUTE_NT009_HOMOLOGACAO = outro
    else:
        LEIAUTE_NT009_PRODUCAO = outro
    return outro


def eh_nt009(pasta: str) -> bool:
    """O leiaute é da NT 009 (qualquer pacote)."""
    return pasta in LEIAUTES_NT009


def dest_em_infdps(pasta: str) -> bool:
    """indDest/dest em infDPS (pacote de 24/11/2026) em vez do grupo IBSCBS."""
    return pasta == LEIAUTE_NT009_20261124
