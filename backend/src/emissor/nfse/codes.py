"""Descrições dos códigos do leiaute (fonte: documentação dos XSD 1.01), municípios IBGE e serviços nacionais."""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

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
# CST do PIS/COFINS (tribFed/piscofins/CST): todos os códigos do XSD 1.01 (TSTipoCST), com as descrições do XSD.
CST_PIS_COFINS = {
    "00": "Nenhum",
    "01": "Operação Tributável com Alíquota Básica",
    "02": "Operação Tributável com Alíquota Diferenciada",
    "03": "Operação Tributável com Alíquota por Unidade de Medida de Produto",
    "04": "Operação Tributável monofásica - Revenda a Alíquota Zero",
    "05": "Operação Tributável por Substituição Tributária",
    "06": "Operação Tributável a Alíquota Zero",
    "07": "Operação Isenta da Contribuição",
    "08": "Operação sem Incidência da Contribuição",
    "09": "Operação com Suspensão da Contribuição",
    "49": "Outras Operações de Saída",
    "50": "Operação com Direito a Crédito – Vinculada Exclusivamente a Receita Tributada no Mercado Interno",
    "51": "Operação com Direito a Crédito – Vinculada Exclusivamente a Receita Não-Tributada no Mercado Interno",
    "52": "Operação com Direito a Crédito – Vinculada Exclusivamente a Receita de Exportação",
    "53": "Operação com Direito a Crédito – Vinculada a Receitas Tributadas e Não-Tributadas no Mercado Interno",
    "54": "Operação com Direito a Crédito – Vinculada a Receitas Tributadas no Mercado Interno e de Exportação",
    "55": "Operação com Direito a Crédito – Vinculada a Receitas Não Tributadas no Mercado Interno e de Exportação",
    "56": "Operação com Direito a Crédito – Vinculada a Receitas Tributadas e Não-Tributadas no Mercado Interno e de Exportação",
    "60": "Crédito Presumido – Operação de Aquisição Vinculada Exclusivamente a Receita Tributada no Mercado Interno",
    "61": "Crédito Presumido – Operação de Aquisição Vinculada Exclusivamente a Receita Não-Tributada no Mercado Interno",
    "62": "Crédito Presumido – Operação de Aquisição Vinculada Exclusivamente a Receita de Exportação",
    "63": "Crédito Presumido – Operação de Aquisição Vinculada a Receitas Tributadas e Não-Tributadas no Mercado Interno",
    "64": "Crédito Presumido – Operação de Aquisição Vinculada a Receitas Tributadas no Mercado Interno e de Exportação",
    "65": "Crédito Presumido – Operação de Aquisição Vinculada a Receitas Não-Tributadas no Mercado Interno e de Exportação",
    "66": "Crédito Presumido – Operação de Aquisição Vinculada a Receitas Tributadas e Não-Tributadas no Mercado Interno e de Exportação",
    "67": "Crédito Presumido – Outras Operações",
    "70": "Operação de Aquisição sem Direito a Crédito",
    "71": "Operação de Aquisição com Isenção",
    "72": "Operação de Aquisição com Suspensão",
    "73": "Operação de Aquisição a Alíquota Zero",
    "74": "Operação de Aquisição sem Incidência da Contribuição",
    "75": "Operação de Aquisição por Substituição Tributária",
    "98": "Outras Operações de Entrada",
    "99": "Outras Operações",
}


# Código indicador da operação do IBS/CBS (cIndOp, Anexo C v1.01 de 2026-01-22). Só os que este sistema consegue
# emitir: destinatário = tomador (indDest 0), tomador no Brasil e sem os grupos de imóvel, evento ou transporte.
C_IND_OP = {
    "100301": "Demais serviços, operação onerosa: local do domicílio do adquirente (tomador)",
    "100401": "Demais serviços, operação não onerosa: local do domicílio do destinatário (tomador)",
    "030101": "Serviço prestado fisicamente sobre a pessoa ou fruído presencialmente: estabelecimento do prestador",
    "030102": "Serviço prestado fisicamente sobre a pessoa ou fruído presencialmente: endereço do tomador",
    "030104": "Serviço prestado fisicamente sobre a pessoa ou fruído presencialmente: local da prestação",
    "050101": "Serviço prestado fisicamente sobre bem móvel material: estabelecimento do prestador",
    "050102": "Serviço prestado fisicamente sobre bem móvel material: endereço do tomador",
    "050104": "Serviço prestado fisicamente sobre bem móvel material: local da prestação",
    "050201": "Serviços portuários: local da prestação",
    "100101": "Cessão de espaço publicitário, operação onerosa: domicílio do adquirente (tomador)",
    "100201": "Cessão de espaço publicitário, operação não onerosa: domicílio do destinatário (tomador)",
    "100501": "Bens móveis imateriais e direitos, operação onerosa: domicílio do adquirente (tomador)",
    "100601": "Bens móveis imateriais e direitos, operação não onerosa: domicílio do destinatário (tomador)",
}
# Sugestões de cClassTrib (tabela oficial de classificação tributária do IBS e da CBS). O campo aceita qualquer
# código de 6 dígitos; o CST é sempre os 3 primeiros dígitos (regra E0959).
C_CLASS_TRIB_SUGESTOES = {
    "000001": "Situações tributadas integralmente pelo IBS e CBS",
}
IND_FINAL = {"0": "Não", "1": "Sim (uso ou consumo pessoal)"}
# Subitens da LC 116 que exigem tpOper (E0903): locação/cessão de imóvel, factoring, administração de fundos...
SUBITENS_EXIGEM_TP_OPER = ("2505", "1509", "1712", "1005")


def descricao(tabela: dict[str, str], codigo: str | None) -> str | None:
    if codigo is None:
        return None
    return tabela.get(codigo, codigo)


_DADOS = Path(__file__).resolve().parents[1] / "data"
_MUNICIPIOS_CSV = _DADOS / "municipios_ibge.csv"


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


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def buscar_municipios(termo: str, limite: int = 20) -> list[dict[str, str]]:
    norm = _norm
    t = norm(termo.strip())
    res = []
    for cod, (nome, uf) in _municipios().items():
        if t in norm(nome) or t == cod:
            res.append({"codigo": cod, "nome": nome, "uf": uf})
            if len(res) >= limite:
                break
    return res


# --- serviços nacionais ----------------------------------------------------------------------------------
# Lista nacional (cTribNac, Anexo B) com os NBS e cClassTrib que o Anexo VIII correlaciona a cada item da LC 116.
# Gerada por ``scripts/servicos_nacionais.py``; quando o gov.br publicar versão nova de um dos anexos, gerar de novo.


@lru_cache(maxsize=1)
def _servicos_nacionais() -> dict[str, Any]:
    dados = json.loads((_DADOS / "servicos_nacionais.json").read_text(encoding="utf-8"))
    dados["por_codigo"] = {s["codigo"]: s for s in dados["servicos"]}
    for s in dados["servicos"]:
        s["_texto"] = _norm(" ".join([s["descricao"], s["subitem_descricao"], *(n["descricao"] for n in s["nbs"])]))
        s["_descricao"] = _norm(s["descricao"])
    return dados


def fontes_servicos() -> list[str]:
    return _servicos_nacionais()["fontes"]


def formatar_c_trib_nac(codigo: str | None) -> str:
    """``171901`` → ``17.19.01`` (como a lista nacional mostra)."""
    c = codigo or ""
    return f"{c[:2]}.{c[2:4]}.{c[4:6]}" if len(c) == 6 and c.isdigit() else c


def formatar_nbs(codigo: str | None) -> str:
    """``113022100`` → ``1.1302.21.00`` (como a lista NBS mostra)."""
    c = codigo or ""
    return f"{c[0]}.{c[1:5]}.{c[5:7]}.{c[7:9]}" if len(c) == 9 and c.isdigit() else c


def _resumo_servico(s: dict[str, Any]) -> dict[str, Any]:
    return {
        "codigo": s["codigo"],
        "descricao": s["descricao"],
        "subitem": s["subitem"],
        "subitem_descricao": s["subitem_descricao"],
    }


def servico(c_trib_nac: str | None) -> dict[str, Any] | None:
    """Serviço da lista nacional com NBS e sugestões de cClassTrib (nome e redução), ou None se não existe."""
    dados = _servicos_nacionais()
    s = dados["por_codigo"].get(re.sub(r"\D", "", c_trib_nac or ""))
    if s is None:
        return None
    classes = dados["classes"]
    return {
        **_resumo_servico(s),
        "grupo_descricao": s["grupo_descricao"],
        "nbs": s["nbs"],
        "c_class_trib": [{"codigo": c, **classes[c]} for c in s["c_class_trib"] if c in classes],
    }


def buscar_servicos(termo: str, limite: int = 30) -> list[dict[str, Any]]:
    """Busca por código (``17.19``, ``1719``, ``171901``; um NBS de 9 dígitos acha os serviços dele) ou por palavras.

    Com palavras, todas precisam aparecer (sem acento e sem caixa) na descrição do serviço, do subitem ou de um NBS
    dele; quem tem todas na descrição do próprio serviço vem primeiro.
    """
    servicos = _servicos_nacionais()["servicos"]
    t = termo.strip()
    digitos = re.sub(r"[\s./-]", "", t)
    if digitos.isdigit():
        if len(digitos) == 9:
            achados = [s for s in servicos if any(n["codigo"] == digitos for n in s["nbs"])]
        else:
            achados = [s for s in servicos if s["codigo"].startswith(digitos)]
        return [_resumo_servico(s) for s in achados[:limite]]
    palavras = [p for p in re.split(r"\W+", _norm(t)) if len(p) >= 2]
    if not palavras:
        return []
    achados = [s for s in servicos if all(p in s["_texto"] for p in palavras)]
    achados.sort(key=lambda s: not all(p in s["_descricao"] for p in palavras))
    return [_resumo_servico(s) for s in achados[:limite]]
