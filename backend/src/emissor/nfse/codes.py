"""Descrições dos códigos do leiaute (fonte: documentação dos XSD 1.01) e municípios IBGE."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

TP_AMB = {"1": "Produção", "2": "Homologação"}
AMB_GER = {"1": "Prefeitura", "2": "Sistema Nacional da NFS-e"}
TP_EMIT = {"1": "Prestador", "2": "Tomador", "3": "Intermediário"}
C_STAT = {
    "100": "NFS-e Gerada",
    "102": "NFS-e de Decisão Judicial",
    "103": "NFS-e Avulsa",
    "107": "NFS-e MEI",
}
FIN_NFSE = {"0": "NFS-e regular"}
OP_SIMP_NAC = {
    "1": "Não Optante",
    "2": "Optante - Microempreendedor Individual (MEI)",
    "3": "Optante - Microempresa ou Empresa de Pequeno Porte (ME/EPP)",
}
REG_AP_TRIB_SN = {
    "1": "Regime de apuração dos tributos federais e municipal pelo SN",
    "2": "Regime de apuração dos tributos federais pelo SN e ISSQN por fora do SN conforme respectiva "
    "legislação municipal do tributo",
    "3": "Regime de apuração dos tributos federais e municipal por fora do SN conforme respectivas "
    "legislações federal e municipal de cada tributo",
}
REG_ESP_TRIB = {
    "0": "Nenhum",
    "1": "Ato Cooperado (Cooperativa)",
    "2": "Estimativa",
    "3": "Microempresa Municipal",
    "4": "Notário ou Registrador",
    "5": "Profissional Autônomo",
    "6": "Sociedade de Profissionais",
    "9": "Outros",
}
TRIB_ISSQN = {"1": "Operação Tributável", "2": "Imunidade", "3": "Exportação de Serviço", "4": "Não Incidência"}
TP_RET_ISSQN = {"1": "Não Retido", "2": "Retido pelo Tomador", "3": "Retido pelo Intermediário"}
TP_IMUNIDADE = {
    "0": "Imunidade (tipo não informado na nota de origem)",
    "1": "Patrimônio, renda ou serviços, uns dos outros",
    "2": "Templos de qualquer culto",
    "3": "Partidos políticos, sindicatos, educação e assistência social",
    "4": "Livros, jornais, periódicos e o papel destinado a sua impressão",
    "5": "Fonogramas e videofonogramas musicais",
}
TP_SUSP = {
    "1": "Exigibilidade Suspensa por Decisão Judicial",
    "2": "Exigibilidade Suspensa por Processo Administrativo",
}
TP_BM = {"1": "Isenção", "2": "Redução da BC em %", "3": "Redução da BC em R$", "4": "Alíquota Diferenciada"}
TP_RET_PIS_COFINS = {
    "0": "PIS/COFINS/CSLL Não Retidos",
    "1": "PIS/COFINS Retidos",
    "2": "PIS/COFINS Não Retidos",
    "3": "PIS/COFINS/CSLL Retidos",
    "4": "PIS/COFINS Retidos, CSLL Não Retido",
    "5": "PIS Retido, COFINS/CSLL Não Retido",
    "6": "COFINS Retido, PIS/CSLL Não Retido",
    "7": "PIS Não Retido, COFINS/CSLL Retidos",
    "8": "PIS/COFINS Não Retidos, CSLL Retido",
    "9": "COFINS Não Retido, PIS/CSLL Retidos",
}
CST_PIS_COFINS = {
    "00": "Nenhum",
    "01": "Operação Tributável com Alíquota Básica",
    "02": "Operação Tributável com Alíquota Diferenciada",
    "03": "Operação Tributável com Alíquota por Unidade de Medida de Produto",
    "04": "Operação Tributável monofásica - Revenda a Alíquota Zero",
    "05": "Operação Tributável por Substituição Tributária",
    "06": "Operação Tributável a Alíquota Zero",
    "07": "Operação Tributável da Contribuição",
    "08": "Operação sem Incidência da Contribuição",
    "09": "Operação com Suspensão da Contribuição",
    "49": "Outras Operações de Saída",
    "50": "Operação com Direito a Crédito",
    "51": "Operação com Direito a Crédito - Vinculada Exclusivamente a Receita Não Tributada",
    "52": "Operação com Direito a Crédito - Vinculada Exclusivamente a Receita de Exportação",
}


def descricao(tabela: dict[str, str], codigo: str | None) -> str | None:
    if codigo is None:
        return None
    return tabela.get(codigo, codigo)


_MUNICIPIOS_CSV = Path(__file__).resolve().parents[1] / "data" / "municipios_ibge.csv"


@lru_cache(maxsize=1)
def _municipios() -> dict[str, tuple[str, str]]:
    with _MUNICIPIOS_CSV.open(encoding="utf-8", newline="") as fh:
        return {r["codigo"]: (r["nome"], r["uf"]) for r in csv.DictReader(fh, delimiter=";")}


def municipio(c_mun: str | None) -> tuple[str, str] | None:
    """(nome, UF) pelo código IBGE de 7 dígitos."""
    if not c_mun:
        return None
    return _municipios().get(str(c_mun))


def municipio_uf(c_mun: str | None) -> str | None:
    m = municipio(c_mun)
    return f"{m[0]} / {m[1]}" if m else None


def buscar_municipios(termo: str, limite: int = 20) -> list[dict[str, str]]:
    import unicodedata

    def norm(s: str) -> str:
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()

    t = norm(termo.strip())
    res = []
    for cod, (nome, uf) in _municipios().items():
        if t in norm(nome) or t == cod:
            res.append({"codigo": cod, "nome": nome, "uf": uf})
            if len(res) >= limite:
                break
    return res
