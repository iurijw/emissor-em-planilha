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
# Tipos de imunidade que a DPS aceita (o 0 é só para notas vindas de outros sistemas, E0593).
TP_IMUNIDADE_DPS = {k: v for k, v in TP_IMUNIDADE.items() if k != "0"}
# Comércio exterior (serv/comExt). O 0 ("desconhecido") só vale para notas vindas de outros sistemas (E0333...).
MD_PRESTACAO = {
    "1": "Transfronteiriço",
    "2": "Consumo no Brasil",
    "3": "Movimento temporário de pessoas físicas",
    "4": "Consumo no exterior",
}
VINC_PREST = {
    "0": "Sem vínculo com o tomador",
    "1": "Controlada",
    "2": "Controladora",
    "3": "Coligada",
    "4": "Matriz",
    "5": "Filial ou sucursal",
    "6": "Outro vínculo",
}
MEC_AF_COMEX_P = {
    "01": "Nenhum",
    "02": "ACC - Adiantamento sobre Contrato de Câmbio",
    "03": "ACE - Adiantamento sobre Cambiais Entregues",
    "04": "BNDES-Exim Pós-Embarque - Serviços",
    "05": "BNDES-Exim Pré-Embarque - Serviços",
    "06": "FGE - Fundo de Garantia à Exportação",
    "07": "PROEX - Equalização",
    "08": "PROEX - Financiamento",
}
MEC_AF_COMEX_T = {
    "01": "Nenhum",
    "02": "Adm. Pública e Repr. Internacional",
    "03": "Aluguéis e arrend. mercantil de máquinas, equip., embarcações e aeronaves",
    "04": "Arrendamento mercantil de aeronave para empresa de transporte aéreo público",
    "05": "Comissão a agentes externos na exportação",
    "06": "Despesas de armazenagem, movimentação e transporte de carga no exterior",
    "07": "Eventos FIFA (subsidiária)",
    "08": "Eventos FIFA",
    "09": "Fretes, arrendamentos de embarcações ou aeronaves e outros",
    "10": "Material aeronáutico",
    "11": "Promoção de bens no exterior",
    "12": "Promoção de destinos turísticos brasileiros",
    "13": "Promoção do Brasil no exterior",
    "14": "Promoção de serviços no exterior",
    "15": "RECINE",
    "16": "RECOPA",
    "17": "Registro e manutenção de marcas, patentes e cultivares",
    "18": "REICOMP",
    "19": "REIDI",
    "20": "REPENEC",
    "21": "REPES",
    "22": "RETAERO",
    "23": "RETID",
    "24": "Royalties, assistência técnica, científica e assemelhados",
    "25": "Serviços de avaliação da conformidade vinculados aos Acordos da OMC",
    "26": "ZPE",
}
MOV_TEMP_BENS = {
    "1": "Não",
    "2": "Vinculada - Declaração de Importação",
    "3": "Vinculada - Declaração de Exportação",
}
# Serviços que exigem o grupo de obra (E0370); os demais não podem tê-lo, salvo 99.01.01 (E0372).
SERVICOS_OBRA = frozenset(
    "070201 070202 070401 070501 070502 070601 070602 070701 070801 071701 071901 141403 141404".split()
)
# ME/EPP com ISS pelo Simples só deduz/reduz a base nestes serviços (E0451/E0452).
SERVICOS_AJUSTE_BC_SN = frozenset(
    (
        "042201 042301 050901 060101 060201 070201 070202 070501 070502 090201 090202 100101 100102 100103 100104 "
        "100105 100201 100202 100301 100401 100402 100403 100501 100502 100601 100701 100801 100901 101001 150101 "
        "150102 150103 150104 150105 151001 151002 151003 151004 151005 160201 170501 170601 171001 171002 171101 "
        "171102 171201 210101 250301"
    ).split()
)


def exige_obra(c_trib_nac: str | None) -> bool:
    return (c_trib_nac or "") in SERVICOS_OBRA


def aceita_obra(c_trib_nac: str | None) -> bool:
    return exige_obra(c_trib_nac) or c_trib_nac == "990101"


def exige_evento(c_trib_nac: str | None) -> bool:
    """Item 12 da lista (espetáculos, shows, feiras...) exige o grupo atvEvento (E0390)."""
    c = c_trib_nac or ""
    return len(c) == 6 and c.isdigit() and c.startswith("12")


def aceita_evento(c_trib_nac: str | None) -> bool:
    return exige_evento(c_trib_nac) or c_trib_nac == "990101"


MDIC = {"0": "Não enviar para o MDIC", "1": "Enviar para o MDIC"}
C_NAO_NIF = {"1": "Dispensado do NIF", "2": "Não exigência do NIF"}
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
IND_FINAL = {"0": "Não", "1": "Sim (uso ou consumo pessoal)"}
# Subitens da LC 116 que exigem tpOper (E0903) e só eles podem levá-lo sem tpEnteGov (E0904): 25.05 (cessão de
# jazigos), 15.09 (arrendamento mercantil), 17.12 (administração de bens e negócios de terceiros) e 10.05
# (intermediação de bens). Ver ``TP_OPER``.
SUBITENS_EXIGEM_TP_OPER = ("2505", "1509", "1712", "1005")
# Tipo de operação (tpOper, TSRTCTpOper). 2 e 3 (uma das partes já feita) exigem as NFS-e referenciadas (gRefNFSe,
# E0905), que vêm de cada nota: só na nota avulsa.
TP_OPER = {
    "5": "Fornecimento e recebimento do pagamento concomitantes",
    "1": "Fornecimento com pagamento posterior",
    "4": "Recebimento do pagamento com fornecimento posterior",
    "2": "Recebimento do pagamento com fornecimento já realizado",
    "3": "Fornecimento com pagamento já realizado",
}
TP_OPER_COM_REFERENCIA = ("2", "3")
# Compras governamentais (tpEnteGov): administração pública direta, autarquias e fundações.
TP_ENTE_GOV = {"1": "União", "2": "Estado", "3": "Distrito Federal", "4": "Município"}


def exige_tp_oper(c_trib_nac: str | None, tp_ente_gov: str | None = None) -> bool:
    """tpOper: obrigatório nos subitens listados e nas compras governamentais (E0903), proibido no resto (E0904)."""
    return (c_trib_nac or "")[:4] in SUBITENS_EXIGEM_TP_OPER or bool(tp_ente_gov)


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


@lru_cache(maxsize=1)
def paises() -> dict[str, str]:
    """Países ISO2 do Anexo A (sem o Brasil: endereço e local no exterior não podem ser BR, E0246/E0304)."""
    with (_DADOS / "paises_iso2.csv").open(encoding="utf-8", newline="") as fh:
        return {r["codigo"]: r["nome"] for r in csv.DictReader(fh) if r["codigo"] != "BR"}


@lru_cache(maxsize=1)
def moedas() -> dict[str, str]:
    """Moedas do Banco Central (tpMoeda, 3 dígitos): código → "USD - Dólar dos Estados Unidos"."""
    dados = json.loads((_DADOS / "moedas_bacen.json").read_text(encoding="utf-8"))
    return {
        m["codigo"]: f"{m['sigla']} - {m['nome']}" if m["nome"] != m["sigla"] else m["sigla"]
        for m in sorted(dados["moedas"], key=lambda m: (m["sigla"] not in ("USD", "EUR"), m["sigla"]))
    }


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


def codigo_municipio(nome: str | None, uf: str | None) -> str | None:
    """Código IBGE pelo nome e UF (sem acento, caixa, hífen ou apóstrofo), como os serviços de CEP devolvem."""
    if not nome or not uf:
        return None
    chave = re.sub(r"[^a-z0-9]", "", _norm(nome))
    uf = uf.strip().upper()
    for cod, (n, u) in _municipios().items():
        if u == uf and re.sub(r"[^a-z0-9]", "", _norm(n)) == chave:
            return cod
    return None


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


def csts_ibscbs() -> list[dict[str, str]]:
    """CST do IBS/CBS que têm alguma classificação para NFS-e (ordem do código)."""
    return [{"valor": c, "descricao": n} for c, n in sorted(_servicos_nacionais()["csts"].items())]


def classes_ibscbs() -> list[dict[str, Any]]:
    """cClassTrib vigentes para NFS-e, com o CST (3 primeiros dígitos), nome e redução do IBS e da CBS."""
    return [
        {
            "valor": c,
            "descricao": v["nome"],
            "cst": v["cst"],
            "reducao_ibs": v["reducao_ibs"],
            "reducao_cbs": v["reducao_cbs"],
            "trib_regular": v.get("trib_regular", False),
            "diferimento": v.get("diferimento", False),
        }
        for c, v in _servicos_nacionais()["classes"].items()
    ]


def classe_ibscbs(codigo: str | None) -> dict[str, Any] | None:
    """Classificação do IBS/CBS (CST, nome, reduções e grupos exigidos), ou None se não está na tabela para NFS-e."""
    return _servicos_nacionais()["classes"].get(codigo or "")


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
