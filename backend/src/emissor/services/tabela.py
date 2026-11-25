"""Tabela de notas a emitir: normalização, importação e exportação (XLSX/CSV)."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from emissor.nfse import locacao as loc_imovel
from emissor.nfse.models import normalizar_documento

COLUNAS = ["Nome", "CNPJ/CPF", "Valor", "Descrição"]

_SINONIMOS = {
    "nome": ("nome", "empresa", "razao social", "razão social", "cliente", "tomador", "nome da empresa"),
    "documento": ("cnpj", "cpf", "cnpj/cpf", "cpf/cnpj", "documento", "cnpj cpf"),
    "valor": ("valor", "valor (r$)", "valor r$", "preco", "preço", "total", "honorarios", "honorários"),
    "descricao": ("descricao", "descrição", "servico", "serviço", "descricao do servico", "discriminacao"),
}

# Dados do cadastro do cliente que algumas planilhas trazem (o relatório NF/RPA da Shopee traz e-mail e endereço).
# Só completam campos vazios do cadastro; o endereço numa coluna só é ignorado (não dá para separar com segurança).
_SINONIMOS_CADASTRO = {
    "email": ("email", "e-mail", "e mail", "email do vendedor", "e-mail do vendedor", "email da loja"),
    "cep": ("cep",),
    "logradouro": ("logradouro", "rua", "endereco (logradouro)"),
    "numero": ("numero", "número", "nº", "n°", "no"),
    "complemento": ("complemento",),
    "bairro": ("bairro",),
    "cidade": ("cidade", "municipio", "município"),
    "uf": ("uf", "estado"),
}

# Relatório mensal NF/RPA da Central do Afiliado da Shopee (Comissão Extra, uma NFS-e por marca desde 01/08/2026).
# A Shopee não publica o leiaute; os nomes abaixo vêm dos guias de emissores e são reconhecidos por trecho.
_TRECHOS_SHOPEE = {
    "nome": ("razao social", "nome da loja", "nome do vendedor", "loja", "vendedor", "marca", "seller"),
    "documento": ("cnpj", "cpf"),
}
DESCRICAO_SHOPEE = "Comissão Extra de afiliado Shopee referente a {competencia_anterior}"


class ImportacaoError(ValueError):
    pass


class _PontoEVirgula(csv.excel):
    delimiter = ";"


@dataclass
class LinhaImportada:
    nome: str
    documento: str
    valor: str
    descricao: str
    cadastro: dict[str, str] = field(default_factory=dict)  # e-mail e endereço vindos da planilha
    locacao: dict[str, str] | None = None  # colunas do imóvel (locação, nfse/locacao.py)


@dataclass
class ResultadoImportacao:
    linhas: list[LinhaImportada] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    reconhecido: str | None = None  # "shopee" quando é o relatório NF/RPA
    informacoes: list[str] = field(default_factory=list)  # o que foi reconhecido (não são problemas)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def parse_valor(v) -> str:
    """Aceita número, "1.234,56", "1234.56", "R$ 1.234,56". Retorna "1234.56" ou "" se vazio."""
    if v is None:
        return ""
    if isinstance(v, int | float | Decimal):
        return f"{Decimal(str(v)).quantize(Decimal('0.01')):.2f}"
    s = str(v).strip().replace("R$", "").replace(" ", "").replace(" ", "")
    if not s:
        return ""
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return f"{Decimal(s).quantize(Decimal('0.01')):.2f}"
    except InvalidOperation as exc:
        raise ValueError(f"valor inválido: {v!r}") from exc


def _mapear_cabecalho(cab: list) -> dict[str, int]:
    mapa: dict[str, int] = {}
    for i, c in enumerate(cab):
        n = _norm(c)
        for campo, nomes in (_SINONIMOS | _SINONIMOS_CADASTRO).items():
            if campo not in mapa and n in {_norm(x) for x in nomes}:
                mapa[campo] = i
    return mapa


def _mapear_locacao(cab: list) -> dict[str, int]:
    """Colunas do imóvel (locação): o rótulo da exportação ou um sinônimo, sem acento e sem caixa."""
    mapa: dict[str, int] = {}
    for i, c in enumerate(cab):
        n = _norm(c)
        for campo, rotulo in loc_imovel.CAMPOS.items():
            nomes = {_norm(rotulo), *(_norm(x) for x in loc_imovel.SINONIMOS.get(campo, ()))}
            if campo not in mapa and n in nomes:
                mapa[campo] = i
    return mapa


def _texto_celula(v) -> str:
    """Célula como texto: número inteiro sem ",0", data como DD/MM/AAAA."""
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, int | float | Decimal):
        return str(v)
    return str(v).strip()


def _coluna_comissao(cab: list) -> int | None:
    """Coluna do valor no relatório da Shopee: a "comissão extra" se houver, senão a primeira com "comissão"."""
    nomes = [_norm(c) for c in cab]
    for trecho in ("comissao extra", "comiss"):
        for i, n in enumerate(nomes):
            if trecho in n:
                return i
    return None


def _mapear_shopee(cab: list) -> dict[str, int] | None:
    """Mapa das colunas quando o cabeçalho é o do relatório NF/RPA da Shopee (tem CNPJ e uma coluna de comissão)."""
    valor = _coluna_comissao(cab)
    # Planilha com uma coluna "Valor" de verdade é a de sempre, mesmo que também tenha uma coluna de comissão.
    if valor is None or _mapear_cabecalho(cab).get("valor") is not None:
        return None
    mapa = {k: v for k, v in _mapear_cabecalho(cab).items() if k not in ("valor", "nome", "documento")}
    mapa["valor"] = valor
    nomes = [_norm(c) for c in cab]
    for campo, trechos in _TRECHOS_SHOPEE.items():
        for trecho in trechos:
            i = next((i for i, n in enumerate(nomes) if trecho in n and i != valor), None)
            if i is not None:
                mapa[campo] = i
                break
    return mapa if "documento" in mapa else None


# Relatórios exportados costumam ter linhas de título antes do cabeçalho.
LINHAS_PROCURA_CABECALHO = 15


def _achar_cabecalho(linhas: list[list]) -> tuple[int, dict[str, int], bool] | None:
    for i, row in enumerate(linhas[:LINHAS_PROCURA_CABECALHO]):
        shopee = _mapear_shopee(row)
        if shopee:
            return i, shopee, True
        mapa = _mapear_cabecalho(row)
        if len({"nome", "documento", "valor", "descricao"} & set(mapa)) >= 2:
            return i, mapa, False
    return None


def _somar_por_documento(res: ResultadoImportacao) -> None:
    """Uma nota por marca: junta as linhas do mesmo CNPJ (o relatório pode vir com uma linha por venda)."""
    juntas: dict[str, LinhaImportada] = {}
    saida: list[LinhaImportada] = []
    somadas = 0
    for ln in res.linhas:
        atual = juntas.get(ln.documento) if ln.documento else None
        if atual is None:
            saida.append(ln)
            if ln.documento:
                juntas[ln.documento] = ln
            continue
        somadas += 1
        atual.valor = f"{Decimal(atual.valor or '0') + Decimal(ln.valor or '0'):.2f}"
        atual.nome = atual.nome or ln.nome
        for k, v in ln.cadastro.items():
            atual.cadastro.setdefault(k, v)
    res.linhas = saida
    if somadas:
        res.informacoes.append(f"{somadas} linha(s) do mesmo CNPJ foram somadas: uma nota por marca.")


# Relatório de comissões da Central do Afiliado ("AffiliateCommissionReport_*.csv", conferido com um arquivo real em
# 15/11/2026): uma linha por item de pedido, sem CNPJ da loja, valores com ponto decimal e várias casas.
COLUNAS_COMISSOES_SHOPEE = {
    "pedido": "id do pedido",
    "loja": "nome da loja",
    "id_loja": "id da loja",
    "comissao_marca": "comissao do item da marca(r$)",
    "status": "status do item do afiliado",
}
STATUS_COMISSAO_VALIDA = "concluido"


def _decimal(v) -> Decimal:
    if v in (None, ""):
        return Decimal(0)
    if isinstance(v, int | float | Decimal):
        return Decimal(str(v))
    s = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal(0)


def _comissoes_shopee(linhas: list[list]) -> ResultadoImportacao | None:
    """Uma linha por loja com a soma da comissão paga pela loja (a parte da Comissão Extra que é dela).

    Só itens concluídos entram (pendentes e cancelados ainda podem mudar). O relatório não traz o CNPJ: a linha sai sem
    ele, e a rota de importação procura a loja pelo nome no cadastro e nas notas já emitidas.
    """
    cab = [_norm(c).replace(" (r$)", "(r$)") for c in linhas[0]]
    col = {k: cab.index(v) for k, v in COLUNAS_COMISSOES_SHOPEE.items() if v in cab}
    if {"pedido", "loja", "comissao_marca"} - set(col):
        return None

    def cel(row, k):
        i = col.get(k)
        return row[i] if i is not None and i < len(row) else None

    lojas: dict[str, dict] = {}
    fora: dict[str, int] = {}
    for row in linhas[1:]:
        nome = str(cel(row, "loja") or "").strip()
        valor = _decimal(cel(row, "comissao_marca"))
        if not nome or valor <= 0:
            continue
        status = _norm(cel(row, "status") or "")
        if "status" in col and status != STATUS_COMISSAO_VALIDA:
            fora[status or "sem situação"] = fora.get(status or "sem situação", 0) + 1
            continue
        chave = str(cel(row, "id_loja") or "").strip() or _norm(nome)
        loja = lojas.setdefault(chave, {"nome": nome, "valor": Decimal(0), "itens": 0})
        loja["valor"] += valor
        loja["itens"] += 1
    res = ResultadoImportacao(reconhecido="shopee_comissoes")
    for loja in lojas.values():
        valor = loja["valor"].quantize(Decimal("0.01"))
        if valor <= 0:
            continue
        res.linhas.append(
            LinhaImportada(nome=loja["nome"], documento="", valor=f"{valor:.2f}", descricao=DESCRICAO_SHOPEE)
        )
    total = sum(Decimal(ln.valor) for ln in res.linhas)
    res.informacoes.append(
        f"Relatório de comissões da Shopee reconhecido: {len(res.linhas)} loja(s), total R$ {_moeda(total)} "
        "(comissão paga por cada loja, itens concluídos). Confira a descrição e a tributação da aba antes de emitir."
    )
    if fora:
        partes = ", ".join(f"{n} {s}" for s, n in sorted(fora.items()))
        res.informacoes.append(f"Itens que ainda não estão concluídos ficaram de fora: {partes}.")
    return res


def _moeda(v: Decimal) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _linhas_para_resultado(linhas: list[list]) -> ResultadoImportacao:
    linhas = [row for row in linhas if any(str(c).strip() for c in row if c is not None)]
    if not linhas:
        raise ImportacaoError("O arquivo está vazio.")
    comissoes = _comissoes_shopee(linhas)
    if comissoes is not None:
        return comissoes
    achado = _achar_cabecalho(linhas)
    shopee = False
    mapa_loc: dict[str, int] = {}
    if achado:
        pos, mapa, shopee = achado
        corpo = linhas[pos + 1 :]
        mapa_loc = _mapear_locacao(linhas[pos])
    else:
        # Sem cabeçalho reconhecível: assume a ordem Nome, CNPJ, Valor, Descrição.
        mapa = {"nome": 0, "documento": 1, "valor": 2, "descricao": 3}
        corpo = linhas
    faltando = [c for c in ("documento", "valor") if c not in mapa]
    if faltando:
        raise ImportacaoError(
            "Não encontrei as colunas obrigatórias: "
            + ", ".join({"documento": "CNPJ/CPF", "valor": "Valor"}[c] for c in faltando)
            + f". Use os cabeçalhos: {', '.join(COLUNAS)}."
        )
    res = ResultadoImportacao(reconhecido="shopee" if shopee else None)

    def cel(row: list, campo: str):
        i = mapa.get(campo)
        return row[i] if i is not None and i < len(row) else None

    inicio = len(linhas) - len(corpo) + 1
    for n, row in enumerate(corpo, start=inicio):
        if shopee and not str(cel(row, "documento") or "").strip():
            continue  # linha de total ou de observação do relatório
        try:
            valor = parse_valor(cel(row, "valor"))
        except ValueError:
            res.avisos.append(f"Linha {n}: valor {cel(row, 'valor')!r} não reconhecido; deixado em branco.")
            valor = ""
        doc_bruto = cel(row, "documento")
        if isinstance(doc_bruto, int | float):  # Excel converte CNPJ em número e perde zeros à esquerda
            doc_bruto = f"{int(doc_bruto):014d}" if int(doc_bruto) > 99999999999 else f"{int(doc_bruto):011d}"
        cadastro = {}
        for campo in _SINONIMOS_CADASTRO:
            v = cel(row, campo)
            if isinstance(v, int | float) and campo == "cep":
                v = f"{int(v):08d}"
            if v not in (None, "") and str(v).strip():
                cadastro[campo] = str(v).strip()
        descricao = _sem_apostrofo(str(cel(row, "descricao") or "").strip())
        locacao = {}
        for campo, i in mapa_loc.items():
            v = row[i] if i < len(row) else None
            if campo == "cep" and isinstance(v, int | float):
                v = f"{int(v):08d}"
            elif campo in loc_imovel.CAMPOS_VALOR and isinstance(v, int | float | Decimal):
                v = f"{Decimal(str(v)):.2f}"
            texto = _sem_apostrofo(_texto_celula(v))
            if texto:
                locacao[campo] = texto
        res.linhas.append(
            LinhaImportada(
                nome=_sem_apostrofo(str(cel(row, "nome") or "").strip()),
                documento=normalizar_documento(str(doc_bruto or "")) or "",
                valor=valor,
                descricao=descricao or (DESCRICAO_SHOPEE if shopee else ""),
                cadastro=cadastro,
                locacao=loc_imovel.limpar(locacao),
            )
        )
    if mapa_loc:
        res.informacoes.append(
            f"Colunas do imóvel reconhecidas: {', '.join(loc_imovel.CAMPOS[c] for c in mapa_loc)}. Elas valem nas abas "
            "de locação de imóvel."
        )
    if shopee:
        _somar_por_documento(res)
        total = sum(Decimal(ln.valor or "0") for ln in res.linhas)
        res.informacoes.insert(
            0,
            f"Relatório da Shopee reconhecido: {len(res.linhas)} marca(s), total R$ {_moeda(total)}. "
            "Confira a descrição e a tributação da aba antes de emitir.",
        )
    return res


def _sem_apostrofo(v: str) -> str:
    """Desfaz o apóstrofo que o exportar_csv põe antes de =, +, - e @ (reimportar devolve o texto original)."""
    return v[1:] if v.startswith("'") and v[1:].startswith(_INICIO_FORMULA) else v


MAX_LINHAS = 10_000
MAX_BYTES_IMPORTACAO = 10_000_000


def _limitar(linhas) -> list[list]:
    saida = []
    for r in linhas:
        if len(saida) > MAX_LINHAS:  # +1 do cabeçalho
            raise ImportacaoError(
                f"A planilha passa de {MAX_LINHAS:,} linhas. Divida em arquivos menores.".replace(",", ".")
            )
        saida.append(list(r))
    return saida


def importar(nome_arquivo: str, conteudo: bytes) -> ResultadoImportacao:
    if len(conteudo) > MAX_BYTES_IMPORTACAO:
        raise ImportacaoError("Arquivo grande demais (máx. 10 MB).")
    ext = nome_arquivo.lower().rsplit(".", 1)[-1] if "." in nome_arquivo else ""
    if ext in ("xlsx", "xlsm"):
        try:
            wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        except Exception as exc:  # openpyxl lança vários tipos para arquivo inválido
            raise ImportacaoError(f"Não foi possível ler a planilha: {exc}") from exc
        ws = wb.worksheets[0]
        return _linhas_para_resultado(_limitar(ws.iter_rows(values_only=True)))
    if ext in ("csv", "txt"):
        for enc in ("utf-8-sig", "cp1252"):
            try:
                texto = conteudo.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        try:
            dialeto = csv.Sniffer().sniff(texto[:4096], delimiters=";,\t")
        except csv.Error:
            dialeto = _PontoEVirgula
        return _linhas_para_resultado(_limitar(csv.reader(io.StringIO(texto), dialeto)))
    raise ImportacaoError("Formato não suportado. Use .xlsx ou .csv.")


_INICIO_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _texto_csv(v: object) -> str:
    """Texto que o Excel/LibreOffice não executa como fórmula ao abrir o CSV."""
    v = str(v or "")
    return "'" + v if v.startswith(_INICIO_FORMULA) else v


# Colunas do imóvel na exportação de uma aba de locação (o município fica na tributação da aba).
COLUNAS_LOCACAO = [c for c in loc_imovel.CAMPOS if c != "c_mun"]


def _locacao_valores(ln: dict) -> list:
    loc = ln.get("locacao") or {}
    return [loc.get(c, "") for c in COLUNAS_LOCACAO]


def exportar_xlsx(linhas: list[dict], locacao: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Notas"
    ws.append(COLUNAS + ([loc_imovel.CAMPOS[c] for c in COLUNAS_LOCACAO] if locacao else []))
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="0098D7")
        c.alignment = Alignment(vertical="center")
    for ln in linhas:
        valor = Decimal(ln["valor"]) if ln.get("valor") else None
        extras = _locacao_valores(ln) if locacao else []
        ws.append([ln.get("nome", ""), ln.get("documento", ""), valor, ln.get("descricao", ""), *extras])
        for col in (1, 4, *range(5, 5 + len(extras))):  # texto digitado nunca vira fórmula na planilha exportada
            c = ws.cell(row=ws.max_row, column=col)
            if c.data_type == "f":
                c.data_type = "s"
    for row in ws.iter_rows(min_row=2, min_col=2, max_col=3):
        row[0].number_format = "@"
        row[1].number_format = '"R$" #,##0.00'
    for i, larg in enumerate((45, 20, 14, 50, *([16] * len(COLUNAS_LOCACAO) if locacao else ())), start=1):
        ws.column_dimensions[get_column_letter(i)].width = larg
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def exportar_csv(linhas: list[dict], locacao: bool = False) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(COLUNAS + ([loc_imovel.CAMPOS[c] for c in COLUNAS_LOCACAO] if locacao else []))
    for ln in linhas:
        valor = (ln.get("valor") or "").replace(".", ",")
        extras = [_texto_csv(v) for v in _locacao_valores(ln)] if locacao else []
        w.writerow(
            [_texto_csv(ln.get("nome")), ln.get("documento", ""), valor, _texto_csv(ln.get("descricao")), *extras]
        )
    return buf.getvalue().encode("utf-8-sig")
