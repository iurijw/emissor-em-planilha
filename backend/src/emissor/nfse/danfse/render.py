"""Geração do DANFSe v2.0 conforme NT SE/CGNFS-e nº 008/2026 v1.02 (``docs/``).

Coordenadas em cm a partir do canto superior esquerdo da página, tiradas da tabela do
item 2.4.5 da NT; a disposição dos campos segue o Anexo I (modelo obrigatório).
Blocos sem dados (destinatário, intermediário) são reduzidos à frase padrão da NT
(item 2.3) e o espaço liberado vai para "Descrição do Serviço".

Regras aplicadas: A4 retrato, página única, linhas 0,5 pt, borda 1 pt, sombreamento
cinza 5 %, rótulos Arial (bloco 7 pt caixa alta; campo 6 pt), conteúdo Microsoft Sans
Serif 7 pt, QR Code ≥ 1,52 cm, campo sem informação → "-", texto longo → "...",
"NFS-e SEM VALIDADE JURÍDICA" em homologação, marca d'água CANCELADA/SUBSTITUÍDA.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import CMYKColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from emissor.config import VER_APLIC
from emissor.nfse import codes
from emissor.nfse.constants import URL_CONSULTA_PUBLICA
from emissor.nfse.danfse.fonts import Fontes, carregar_fontes
from emissor.nfse.documentos import formatar_documento
from emissor.nfse.xml_reader import NFSeDoc

LOGO = Path(__file__).parent / "assets" / "logo_nfse_horizontal.png"

PAGE_W, PAGE_H = A4
ESQ, LARG = 0.30, 20.40  # corpo do DANFSe
DIR = ESQ + LARG
COL = [0.30, 5.41, 10.51, 15.62]
W1, W2 = 5.09, 10.19
PAD = 0.07
FIM_PAGINA = 28.77  # topo 0,30 + corpo (sem canhoto) até o limite inferior do modelo
ALTURA_BLOCO_SUPRIMIDO = 0.40

PRETO = CMYKColor(0, 0, 0, 1)
CINZA_5 = CMYKColor(0, 0, 0, 0.05)
CINZA_35 = CMYKColor(0, 0, 0, 0.35)
VERMELHO = CMYKColor(0, 1, 1, 0)

TEXTO_QR = (
    "A autenticidade desta NFS-e pode ser verificada pela leitura deste código QR "
    "ou pela consulta da chave de acesso no portal nacional da NFS-e"
)


# --- formatação ---------------------------------------------------------------------


def _dec(v: str | None) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def moeda(v: str | Decimal | None) -> str:
    d = _dec(v) if not isinstance(v, Decimal) else v
    if d is None:
        return "-"
    s = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def pct(v: str | None) -> str:
    d = _dec(v)
    return "-" if d is None else f"{d:.2f}".replace(".", ",") + "%"


def data_br(v: str | None) -> str:
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).strftime("%d/%m/%Y")
    except ValueError:
        return v


def data_hora_br(v: str | None) -> str:
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return v


def cep(v: str | None) -> str | None:
    if not v:
        return None
    return f"{v[:2]}.{v[2:5]}-{v[5:]}" if len(v) == 8 else v


def fone(v: str | None) -> str | None:
    if not v:
        return None
    if len(v) == 10:
        return f"({v[:2]}) {v[2:6]}-{v[6:]}"
    if len(v) == 11:
        return f"({v[:2]}) {v[2:7]}-{v[7:]}"
    return v


def cod_trib_nac(v: str | None) -> str | None:
    return f"{v[:2]}.{v[2:4]}.{v[4:]}" if v and len(v) == 6 else v


def cod_nbs(v: str | None) -> str | None:
    return f"{v[0]}.{v[1:5]}.{v[5:7]}.{v[7:]}" if v and len(v) == 9 else v


def juntar(*partes: str | None, sep: str = " / ") -> str | None:
    p = [x for x in partes if x]
    return sep.join(p) if p else None


# --- estrutura de layout --------------------------------------------------------------


@dataclass
class Celula:
    col: int
    span: int
    rotulo: str | None
    valor: str | None
    rotulo_destaque: bool = False  # 7 pt caixa alta (campos de identificação / valores principais)
    sombra: bool = False
    multilinha: bool = False


@dataclass
class Linha:
    altura: float
    celulas: list[Celula]
    suprimivel_se_vazia: bool = False  # nota 5 (**) da NT

    def vazia(self) -> bool:
        return all(c.valor in (None, "", "-") for c in self.celulas)


@dataclass
class Bloco:
    titulo: str
    linhas: list[Linha] = field(default_factory=list)
    frase_suprimido: str | None = None  # se definido, o bloco vira só esta frase
    # Altura no modelo completo, quando difere da soma das linhas (bloco suprimido ou linha
    # removida por regra de data); o espaço que sobra vai para a Descrição do Serviço.
    altura_nominal: float | None = None


# --- renderizador -------------------------------------------------------------------------


class _Desenho:
    def __init__(self, c: Canvas, fontes: Fontes):
        self.c = c
        self.f = fontes

    @staticmethod
    def ry(y_cm: float) -> float:
        return PAGE_H - y_cm * cm

    def ajustar(self, texto: str, fonte: str, tam: float, largura_cm: float) -> str:
        maxw = largura_cm * cm
        if stringWidth(texto, fonte, tam) <= maxw:
            return texto
        while texto and stringWidth(texto + "...", fonte, tam) > maxw:
            texto = texto[:-1]
        return texto.rstrip() + "..."

    def quebrar(self, texto: str, fonte: str, tam: float, largura_cm: float) -> list[str]:
        maxw = largura_cm * cm
        linhas: list[str] = []
        for par in texto.split("\n"):
            palavras = par.split(" ")
            atual = ""
            for p in palavras:
                cand = f"{atual} {p}" if atual else p
                if stringWidth(cand, fonte, tam) <= maxw:
                    atual = cand
                    continue
                if atual:
                    linhas.append(atual)
                while stringWidth(p, fonte, tam) > maxw:  # palavra maior que a linha
                    corte = len(p)
                    while corte > 1 and stringWidth(p[:corte], fonte, tam) > maxw:
                        corte -= 1
                    linhas.append(p[:corte])
                    p = p[corte:]
                atual = p
            linhas.append(atual)
        return linhas

    def texto(self, x: float, y_base: float, s: str, fonte: str, tam: float, larg: float | None = None, cor=PRETO):
        if larg is not None:
            s = self.ajustar(s, fonte, tam, larg)
        self.c.setFillColor(cor)
        self.c.setFont(fonte, tam)
        self.c.drawString(x * cm, self.ry(y_base), s)

    def centro(self, x: float, larg: float, y_base: float, s: str, fonte: str, tam: float, cor=PRETO):
        self.c.setFillColor(cor)
        self.c.setFont(fonte, tam)
        self.c.drawCentredString((x + larg / 2) * cm, self.ry(y_base), s)

    def sombra(self, x: float, y: float, larg: float, alt: float):
        self.c.setFillColor(CINZA_5)
        self.c.rect(x * cm, self.ry(y + alt), larg * cm, alt * cm, stroke=0, fill=1)

    def linha_h(self, y: float):
        self.c.setStrokeColor(PRETO)
        self.c.setLineWidth(0.5)
        self.c.line(ESQ * cm, self.ry(y), DIR * cm, self.ry(y))


def _pt_cm(pt: float) -> float:
    return pt / 72 * 2.54


class GeradorDanfse:
    def __init__(self, doc: NFSeDoc, marca_dagua: str | None = None):
        self.doc = doc
        self.marca = marca_dagua
        self.fontes = carregar_fontes()

    # -- dados ----------------------------------------------------------------------------
    def _v(self, caminho: str) -> str | None:
        return self.doc.t(caminho)

    def _d(self, caminho: str) -> str | None:
        return self.doc.dps(caminho)

    def _pessoa(self, base: str, rotulo_titulo: str, com_im: bool = True) -> list[Linha]:
        d = self._d
        doc = d(f"{base}/CNPJ") or d(f"{base}/CPF") or d(f"{base}/NIF")
        c_mun = d(f"{base}/end/endNac/cMun")
        ext_cidade = d(f"{base}/end/endExt/xCidade")
        mun = codes.municipio_uf(c_mun) if c_mun else ext_cidade
        cod_cep = juntar(c_mun, cep(d(f"{base}/end/endNac/CEP"))) or juntar(None, d(f"{base}/end/endExt/cEndPost"))
        endereco = juntar(
            d(f"{base}/end/xLgr"), d(f"{base}/end/nro"), d(f"{base}/end/xCpl"), d(f"{base}/end/xBairro"), sep=", "
        )
        r1 = [Celula(1, 1, "CNPJ / CPF / NIF", formatar_documento(doc) if doc and len(doc) in (11, 14) else doc)]
        if com_im:
            r1.append(Celula(2, 1, "Indicador Municipal (Inscrição)", d(f"{base}/IM")))
        r1.append(Celula(3, 1, "Telefone", fone(d(f"{base}/fone"))))
        return [
            Linha(0.64, r1),
            Linha(
                0.64,
                [
                    Celula(0, 2, "Nome / Nome Empresarial", d(f"{base}/xNome")),
                    Celula(2, 1, "Município / Sigla UF", mun),
                    Celula(3, 1, "Código IBGE / CEP", cod_cep),
                ],
            ),
            Linha(0.66, [Celula(0, 2, "Endereço", endereco), Celula(2, 2, "E-mail", d(f"{base}/email"))]),
        ]

    def _blocos(self) -> list[Bloco]:
        v, d = self._v, self._d
        blocos: list[Bloco] = []

        # Prestador: dados cadastrais vêm de <emit> (a DPS só traz CNPJ/contato).
        prest_doc = d("prest/CNPJ") or d("prest/CPF") or v("emit/CNPJ") or v("emit/CPF")
        c_mun_p = v("emit/enderNac/cMun")
        mun_p = codes.municipio_uf(c_mun_p) or juntar(v("xLocEmi"), v("emit/enderNac/UF"))
        blocos.append(
            Bloco(
                "PRESTADOR / FORNECEDOR",
                [
                    Linha(
                        0.64,
                        [
                            Celula(1, 1, "CNPJ / CPF / NIF", formatar_documento(prest_doc)),
                            Celula(2, 1, "Indicador Municipal (Inscrição)", d("prest/IM") or v("emit/IM")),
                            Celula(3, 1, "Telefone", fone(d("prest/fone") or v("emit/fone"))),
                        ],
                    ),
                    Linha(
                        0.64,
                        [
                            Celula(0, 2, "Nome / Nome Empresarial", v("emit/xNome") or d("prest/xNome")),
                            Celula(2, 1, "Município / Sigla UF", mun_p),
                            Celula(3, 1, "Código IBGE / CEP", juntar(c_mun_p, cep(v("emit/enderNac/CEP")))),
                        ],
                    ),
                    Linha(
                        0.66,
                        [
                            Celula(
                                0,
                                2,
                                "Endereço",
                                juntar(
                                    v("emit/enderNac/xLgr"),
                                    v("emit/enderNac/nro"),
                                    v("emit/enderNac/xCpl"),
                                    v("emit/enderNac/xBairro"),
                                    sep=", ",
                                ),
                            ),
                            Celula(2, 2, "E-mail", d("prest/email") or v("emit/email")),
                        ],
                    ),
                    Linha(
                        0.64,
                        [
                            Celula(
                                0,
                                1,
                                "Simples Nacional na Data de Competência",
                                codes.descricao(codes.OP_SIMP_NAC, d("prest/regTrib/opSimpNac")),
                            ),
                            Celula(
                                1,
                                3,
                                "Regime de Apuração Tributária pelo SN",
                                codes.descricao(codes.REG_AP_TRIB_SN, d("prest/regTrib/regApTribSN")),
                            ),
                        ],
                    ),
                ],
            )
        )

        tem_toma = self.doc.el("DPS/infDPS/toma") is not None
        blocos.append(
            Bloco(
                "TOMADOR / ADQUIRENTE",
                self._pessoa("toma", "TOMADOR") if tem_toma else [],
                None if tem_toma else "TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e",
            )
        )

        tem_dest = self.doc.el("DPS/infDPS/IBSCBS/dest") is not None
        if tem_dest:
            blocos.append(Bloco("DESTINATÁRIO DA OPERAÇÃO", self._pessoa("IBSCBS/dest", "DEST", com_im=False)))
        elif d("IBSCBS/indDest") == "0":
            blocos.append(Bloco("", [], "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE DA OPERAÇÃO"))
        else:
            blocos.append(Bloco("", [], "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"))

        if self.doc.el("DPS/infDPS/interm") is not None:
            blocos.append(Bloco("INTERMEDIÁRIO DA OPERAÇÃO", self._pessoa("interm", "INTERM")))
        else:
            blocos.append(Bloco("", [], "INTERMEDIÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"))

        # Serviço
        c_loc = d("serv/locPrest/cLocPrestacao")
        pais_prest = d("serv/locPrest/cPaisPrestacao") or ("BR" if c_loc else None)
        local = juntar(v("xLocPrestacao"), (codes.municipio(c_loc) or (None, None))[1], pais_prest)
        blocos.append(
            Bloco(
                "SERVIÇO PRESTADO",
                [
                    Linha(
                        0.65,
                        [
                            Celula(
                                1,
                                1,
                                "Código de Tributação Nacional / Municipal",
                                juntar(cod_trib_nac(d("serv/cServ/cTribNac")), d("serv/cServ/cTribMun")),
                            ),
                            Celula(2, 1, "Código da NBS", cod_nbs(d("serv/cServ/cNBS"))),
                            Celula(3, 1, "Local da Prestação / Sigla UF / País", local),
                        ],
                    ),
                    Linha(0.40, [Celula(0, 4, None, v("xTribMun") or v("xTribNac"))]),
                    Linha(0.64, [Celula(0, 4, "Descrição do Serviço", d("serv/cServ/xDescServ"), multilinha=True)]),
                ],
            )
        )

        # Tributação municipal
        if self.doc.el("DPS/infDPS/valores/trib/tribMun") is not None:
            c_inc = v("cLocIncid")
            inc = juntar(
                v("xLocIncid"),
                (codes.municipio(c_inc) or (None, None))[1],
                d("valores/trib/tribMun/cPaisResult") or "BR",
            )
            calc_bm = v("valores/vCalcBM") or d("valores/trib/tribMun/BM/vRedBCBM")
            ded = v("valores/vCalcDR") or d("valores/vDedRed/vDR")
            blocos.append(
                Bloco(
                    "TRIBUTAÇÃO MUNICIPAL (ISSQN)",
                    [
                        Linha(
                            0.65,
                            [
                                Celula(
                                    1,
                                    1,
                                    "Tipo de Tributação do ISSQN",
                                    codes.descricao(codes.TRIB_ISSQN, d("valores/trib/tribMun/tribISSQN")),
                                ),
                                Celula(2, 2, "Município / Sigla UF / País de Incidência do ISSQN", inc),
                            ],
                        ),
                        Linha(
                            0.65,
                            [
                                Celula(
                                    0,
                                    1,
                                    "Regime Especial de Tributação do ISSQN",
                                    codes.descricao(codes.REG_ESP_TRIB, d("prest/regTrib/regEspTrib")),
                                ),
                                Celula(
                                    1,
                                    1,
                                    "Tipo de Imunidade do ISSQN",
                                    codes.descricao(codes.TP_IMUNIDADE, d("valores/trib/tribMun/tpImunidade")),
                                ),
                                Celula(
                                    2,
                                    1,
                                    "Suspensão da Exigibilidade do ISSQN",
                                    codes.descricao(codes.TP_SUSP, d("valores/trib/tribMun/exigSusp/tpSusp")),
                                ),
                                Celula(3, 1, "Número Processo Suspensão", d("valores/trib/tribMun/exigSusp/nProcesso")),
                            ],
                            suprimivel_se_vazia=True,
                        ),
                        Linha(
                            0.64,
                            [
                                Celula(0, 1, "Benefício Municipal", codes.descricao(codes.TP_BM, v("valores/tpBM"))),
                                Celula(1, 1, "Cálculo do BM", moeda(calc_bm) if calc_bm else None),
                                Celula(2, 1, "Total Deduções/Reduções", moeda(ded) if ded else None),
                                Celula(
                                    3,
                                    1,
                                    "Desconto Incondicionado",
                                    moeda(d("valores/vDescCondIncond/vDescIncond"))
                                    if d("valores/vDescCondIncond/vDescIncond")
                                    else None,
                                ),
                            ],
                            suprimivel_se_vazia=True,
                        ),
                        Linha(
                            0.65,
                            [
                                Celula(0, 1, "BC ISSQN", moeda(v("valores/vBC"))),
                                Celula(1, 1, "Alíquota Aplicada", pct(v("valores/pAliqAplic"))),
                                Celula(
                                    2,
                                    1,
                                    "Retenção do ISSQN",
                                    codes.descricao(codes.TP_RET_ISSQN, d("valores/trib/tribMun/tpRetISSQN")),
                                ),
                                Celula(3, 1, "ISSQN Apurado", moeda(v("valores/vISSQN"))),
                            ],
                        ),
                    ],
                )
            )
        else:
            blocos.append(Bloco("", [], "TRIBUTAÇÃO MUNICIPAL (ISSQN) - OPERAÇÃO NÃO SUJEITA AO ISSQN", 2.59))

        # Tributação federal (item 2.4.5: tratamento de vPis/vCofins conforme tpRetPisCofins)
        pc = "valores/trib/tribFed/piscofins"
        tp_ret = d(f"{pc}/tpRetPisCofins")
        v_pis, v_cofins, v_csll = (
            _dec(d(f"{pc}/vPis")),
            _dec(d(f"{pc}/vCofins")),
            _dec(d("valores/trib/tribFed/vRetCSLL")),
        )
        if tp_ret == "1":
            contrib = (v_csll or 0) + (v_pis or 0) + (v_cofins or 0)
            contrib_txt = moeda(Decimal(contrib)) if any(x is not None for x in (v_csll, v_pis, v_cofins)) else None
            pis_txt, cofins_txt = moeda(Decimal("0")), moeda(Decimal("0"))
        else:
            contrib_txt = moeda(v_csll) if v_csll is not None else None
            pis_txt = moeda(v_pis) if v_pis is not None else None
            cofins_txt = moeda(v_cofins) if v_cofins is not None else None
        linhas_fed = [
            Linha(
                0.65,
                [
                    Celula(
                        1,
                        1,
                        "IRRF",
                        moeda(d("valores/trib/tribFed/vRetIRRF")) if d("valores/trib/tribFed/vRetIRRF") else None,
                    ),
                    Celula(
                        2,
                        1,
                        "Contribuição Previdenciária - Retida",
                        moeda(d("valores/trib/tribFed/vRetCP")) if d("valores/trib/tribFed/vRetCP") else None,
                    ),
                    Celula(3, 1, "Contribuições Sociais - Retidas", contrib_txt),
                ],
            )
        ]
        if (d("dCompet") or "")[:4] <= "2026":  # nota 6: só até o fim de 2026
            linhas_fed.append(
                Linha(
                    0.65,
                    [
                        Celula(0, 1, "PIS - Débito Apuração Própria", pis_txt),
                        Celula(1, 1, "COFINS - Débito Apuração Própria", cofins_txt),
                        Celula(
                            2,
                            2,
                            "Descrição Contrib. Sociais - Retidas",
                            codes.descricao(codes.TP_RET_PIS_COFINS, tp_ret),
                        ),
                    ],
                )
            )
        blocos.append(Bloco("TRIBUTAÇÃO FEDERAL (EXCETO CBS)", linhas_fed, altura_nominal=1.30))

        # IBS / CBS
        ib = "IBSCBS"
        g = "DPS/infDPS/IBSCBS/valores/trib/gIBSCBS"
        tem_ibs = self.doc.el(ib) is not None
        excl = None
        if tem_ibs:
            parts = [
                _dec(d("valores/vDescCondIncond/vDescIncond")),
                _dec(v(f"{ib}/valores/vCalcReeRepRes")),
                _dec(v("valores/vISSQN")),
                _dec(d(f"{pc}/vPis")),
                _dec(d(f"{pc}/vCofins")),
            ]
            excl = moeda(sum((p for p in parts if p is not None), Decimal("0")))
        tot_ibs, v_cbs = _dec(v(f"{ib}/totCIBS/gIBS/vIBSTot")), _dec(v(f"{ib}/totCIBS/gCBS/vCBS"))

        def m(c: str) -> str | None:
            return moeda(v(c)) if v(c) else None

        def p(c: str) -> str | None:
            return pct(v(c)) if v(c) else None

        blocos.append(
            Bloco(
                "TRIBUTAÇÃO IBS / CBS",
                [
                    Linha(
                        0.64,
                        [
                            Celula(
                                1, 1, "CST / cClassTrib", juntar(self.doc.t(f"{g}/CST"), self.doc.t(f"{g}/cClassTrib"))
                            ),
                            Celula(
                                2,
                                2,
                                "Indicador de Operação / Código IBGE Incidência / Município Incidência / Sigla UF",
                                juntar(
                                    d("IBSCBS/cIndOp"),
                                    v(f"{ib}/cLocalidadeIncid"),
                                    v(f"{ib}/xLocalidadeIncid"),
                                    (codes.municipio(v(f"{ib}/cLocalidadeIncid")) or (None, None))[1],
                                ),
                            ),
                        ],
                    ),
                    Linha(
                        0.65,
                        [
                            Celula(0, 1, "Exclusões e Reduções da Base de Cálculo", excl),
                            Celula(1, 1, "Base de Cálculo Após Exclusões e Reduções", m(f"{ib}/valores/vBC")),
                            Celula(
                                2,
                                1,
                                "Red. Alíquota IBS / Red. Alíquota CBS",
                                juntar(
                                    p(f"{ib}/valores/uf/pRedAliqUF"),
                                    p(f"{ib}/valores/mun/pRedAliqMun"),
                                    p(f"{ib}/valores/fed/pRedAliqCBS"),
                                ),
                            ),
                            Celula(
                                3,
                                1,
                                "Alíquota - IBS UF / IBS Mun",
                                juntar(p(f"{ib}/valores/uf/pIBSUF"), p(f"{ib}/valores/mun/pIBSMun")),
                            ),
                        ],
                    ),
                    Linha(
                        0.65,
                        [
                            Celula(0, 1, "Alíq. Efetiva Municipal - IBS", p(f"{ib}/valores/mun/pAliqEfetMun")),
                            Celula(1, 1, "Valor Apurado Municipal - IBS", m(f"{ib}/totCIBS/gIBS/gIBSMunTot/vIBSMun")),
                            Celula(2, 1, "Alíq. Efetiva Estadual - IBS", p(f"{ib}/valores/uf/pAliqEfetUF")),
                            Celula(3, 1, "Valor Apurado Estadual - IBS", m(f"{ib}/totCIBS/gIBS/gIBSUFTot/vIBSUF")),
                        ],
                    ),
                    Linha(
                        0.64,
                        [
                            Celula(0, 1, "Valor Total Apurado - IBS", m(f"{ib}/totCIBS/gIBS/vIBSTot")),
                            Celula(1, 1, "Alíquota - CBS", p(f"{ib}/valores/fed/pCBS")),
                            Celula(2, 1, "Alíquota Efetiva - CBS", p(f"{ib}/valores/fed/pAliqEfetCBS")),
                            Celula(3, 1, "Valor Total Apurado - CBS", m(f"{ib}/totCIBS/gCBS/vCBS")),
                        ],
                    ),
                ],
            )
        )

        # Valor total
        tot_ibscbs = (
            moeda((tot_ibs or Decimal("0")) + (v_cbs or Decimal("0")))
            if (tot_ibs is not None or v_cbs is not None)
            else None
        )
        blocos.append(
            Bloco(
                "VALOR TOTAL DA NFS-E",
                [
                    Linha(
                        0.69,
                        [
                            Celula(1, 1, "VALOR DA OPERAÇÃO / SERVIÇO", moeda(d("valores/vServPrest/vServ")), True),
                            Celula(
                                2, 1, "Desconto Incondicionado", m("DPS/infDPS/valores/vDescCondIncond/vDescIncond")
                            ),
                            Celula(3, 1, "Desconto Condicionado", m("DPS/infDPS/valores/vDescCondIncond/vDescCond")),
                        ],
                    ),
                    Linha(
                        0.68,
                        [
                            Celula(0, 1, "Total das Retenções (ISSQN / Federais)", m("valores/vTotalRet")),
                            Celula(1, 1, "VALOR LÍQUIDO DA NFS-e", moeda(v("valores/vLiq")), True),
                            Celula(2, 1, "Total do IBS/CBS", tot_ibscbs),
                            Celula(
                                3, 1, "VALOR LÍQUIDO DA NFS-e + IBS/CBS", m(f"{ib}/totCIBS/vTotNF"), True, sombra=True
                            ),
                        ],
                    ),
                ],
            )
        )
        return blocos

    def _info_complementares(self) -> str:
        d, v = self._d, self._v
        itens = [
            ("Inf. Cont.:", d("serv/infoCompl/xInfComp")),
            ("NFS-e Subst.:", d("subst/chSubstda")),
            ("Doc. Ref.:", d("serv/infoCompl/docRef")),
            ("Cod. Obra:", d("serv/obra/cObra")),
            ("Insc. Imob.:", d("serv/obra/inscImobFisc") or d("IBSCBS/imovel/inscImobFisc")),
            ("Cod. Evt.:", d("serv/atvEvento/idAtvEvt")),
            ("Doc. Tec.:", d("serv/infoCompl/idDocTec")),
            ("Núm. Ped.:", d("serv/infoCompl/gItemPed/xPed") or d("serv/infoCompl/xPed")),
            ("Item Ped.:", d("serv/infoCompl/gItemPed/xItemPed")),
            ("Inf. A. T. Mun.:", v("xOutInf")),
        ]
        partes = [f"{r} {val}" for r, val in itens if val]
        tt = "valores/trib/totTrib"
        if d(f"{tt}/vTotTrib/vTotTribFed") is not None:
            fed, est, mun = (moeda(d(f"{tt}/vTotTrib/{k}")) for k in ("vTotTribFed", "vTotTribEst", "vTotTribMun"))
        elif d(f"{tt}/pTotTrib/pTotTribFed") is not None:
            fed, est, mun = (pct(d(f"{tt}/pTotTrib/{k}")) for k in ("pTotTribFed", "pTotTribEst", "pTotTribMun"))
        else:
            fed = est = mun = "-"
        totais = f"Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012: Federais: {fed}; Estaduais: {est}; Municipais: {mun}"
        if d(f"{tt}/pTotTribSN") is not None:
            totais += f"; Simples Nacional: {pct(d(f'{tt}/pTotTribSN'))}"
        corpo = " | ".join(partes)
        if len(corpo) > 1997:
            corpo = corpo[:1997] + "..."
        return f"{corpo}\n{totais}" if corpo else totais

    # -- desenho ---------------------------------------------------------------------------
    def gerar(self) -> bytes:
        buf = io.BytesIO()
        c = Canvas(buf, pagesize=A4, pageCompression=1)
        c.setTitle(f"DANFSe {self.doc.numero or ''} - {self.doc.chave}")
        c.setAuthor(self.doc.emitente_nome or "")
        c.setCreator(VER_APLIC)
        g = _Desenho(c, self.fontes)
        if self.marca:  # desenhada primeiro para ficar atrás do conteúdo
            self._marca_dagua(c)
        self._cabecalho(g)
        self._identificacao(g)
        blocos = self._blocos()
        # Espaço liberado por blocos/linhas suprimidos vai para a Descrição do Serviço.
        extra = 0.0
        for b in blocos:
            if b.frase_suprimido:
                extra += (b.altura_nominal or 1.94) - ALTURA_BLOCO_SUPRIMIDO
            else:
                extra += sum(ln.altura for ln in b.linhas if ln.suprimivel_se_vazia and ln.vazia())
                if b.altura_nominal is not None:
                    extra += b.altura_nominal - sum(ln.altura for ln in b.linhas)
        y = 4.34
        for b in blocos:
            y = self._bloco(g, b, y, extra)
        self._informacoes(g, y)
        # Borda da página: 1 pt
        c.setStrokeColor(PRETO)
        c.setLineWidth(1)
        c.rect(ESQ * cm, g.ry(FIM_PAGINA), LARG * cm, (FIM_PAGINA - 0.30) * cm, stroke=1, fill=0)
        c.showPage()
        c.save()
        return buf.getvalue()

    def _cabecalho(self, g: _Desenho):
        f = self.fontes
        g.sombra(ESQ, 0.30, LARG, 1.18)
        if LOGO.exists():
            img = ImageReader(str(LOGO))
            iw, ih = img.getSize()
            w = 4.00
            h = min(0.85, w * ih / iw)
            g.c.drawImage(img, 0.49 * cm, g.ry(0.44 + h), w * cm, h * cm, mask="auto", preserveAspectRatio=True)
        g.centro(5.41, W2, 0.30 + 0.42, "DANFSe v2.0", f.rotulo_negrito, 9)
        g.centro(5.41, W2, 0.30 + 0.78, "Documento Auxiliar da NFS-e", f.rotulo_negrito, 9)
        if self.doc.tp_amb == "2":
            g.centro(5.41, W2, 0.30 + 1.12, "NFS-e SEM VALIDADE JURÍDICA", f.rotulo_negrito, 9, cor=VERMELHO)
        # Município (8 pt, até 2 linhas) / ambiente gerador / tipo de ambiente (6 pt)
        if (self._d("serv/cServ/cTribNac") or "")[:2] != "99":  # NT: não exibir para item 99
            mun = f"Município: {juntar(self._v('xLocEmi'), self._v('emit/enderNac/UF')) or '-'}"
            linhas = g.quebrar(mun, f.conteudo, 8, W1 - 2 * PAD)[:2]
            for i, ln in enumerate(linhas):
                g.texto(15.62 + PAD, 0.30 + 0.30 + i * 0.30, ln, f.conteudo, 8, W1 - 2 * PAD)
        amb_ger = codes.descricao(codes.AMB_GER, self._v("ambGer")) or "-"
        tp_amb = codes.descricao(codes.TP_AMB, self.doc.tp_amb) or "-"
        g.texto(15.62 + PAD, 0.97 + 0.21, f"Ambiente Gerador: {amb_ger}", f.conteudo, 6, W1 - 2 * PAD)
        g.texto(15.62 + PAD, 1.22 + 0.21, f"Tipo de Ambiente: {tp_amb}", f.conteudo, 6, W1 - 2 * PAD)
        g.linha_h(1.48)

    def _campo(self, g: _Desenho, x: float, y: float, larg: float, alt: float, cel: Celula, ident: bool = False):
        f = self.fontes
        if cel.sombra:
            g.sombra(x, y, larg, alt)
        util = larg - 2 * PAD
        base = y + PAD + _pt_cm(6)
        if cel.rotulo:
            if ident or cel.rotulo_destaque:
                rotulo = cel.rotulo.upper() if ident else cel.rotulo
                g.texto(x + PAD, y + PAD + _pt_cm(7), rotulo, f.rotulo_negrito, 7, util)
                base = y + PAD + _pt_cm(7)
            else:
                g.texto(x + PAD, base, cel.rotulo, f.rotulo_negrito, 6, util)
            y_val = base + _pt_cm(7) + 0.06
        else:
            y_val = y + PAD + _pt_cm(7)
        valor = cel.valor if cel.valor not in (None, "") else "-"
        if cel.multilinha:
            passo = _pt_cm(7) * 1.15
            max_linhas = max(1, int((y + alt - y_val) / passo) + 1)
            linhas = g.quebrar(valor, f.conteudo, 7, util)
            if len(linhas) > max_linhas:
                linhas = linhas[:max_linhas]
                linhas[-1] = g.ajustar(linhas[-1] + " ...", f.conteudo, 7, util)
            for i, ln in enumerate(linhas):
                g.texto(x + PAD, y_val + i * passo, ln, f.conteudo, 7)
        else:
            g.texto(x + PAD, y_val, valor, f.conteudo, 7, util)

    def _identificacao(self, g: _Desenho):
        v, d = self._v, self._d
        self._campo(g, ESQ, 1.48, 15.30, 0.79, Celula(0, 1, "Chave de Acesso da NFS-e", self.doc.chave), ident=True)
        linhas = [
            (
                2.27,
                [
                    ("Número da NFS-e", v("nNFSe")),
                    ("Competência da NFS-e", data_br(d("dCompet"))),
                    ("Data e Hora da Emissão da NFS-e", data_hora_br(v("dhProc"))),
                ],
            ),
            (
                2.96,
                [
                    ("Número da DPS", d("nDPS")),
                    ("Série da DPS", d("serie")),
                    ("Data e Hora da Emissão da DPS", data_hora_br(d("dhEmi"))),
                ],
            ),
            (
                3.65,
                [
                    ("Emitente da NFS-e", codes.descricao(codes.TP_EMIT, d("tpEmit"))),
                    ("Situação da NFS-e", codes.descricao(codes.C_STAT, v("cStat"))),
                    ("Finalidade", codes.descricao(codes.FIN_NFSE, d("IBSCBS/finNFSe"))),
                ],
            ),
        ]
        for y, campos in linhas:
            for i, (rot, val) in enumerate(campos):
                self._campo(
                    g, COL[i], y, W1, 0.69, Celula(i, 1, rot, val, sombra=(rot == "Emitente da NFS-e")), ident=True
                )
        # QR Code
        url = URL_CONSULTA_PUBLICA.format(chave=self.doc.chave)
        qr = QrCodeWidget(url, barLevel="M", barBorder=0)
        x1, y1, x2, y2 = qr.getBounds()
        lado = 1.52 * cm
        dr = Drawing(lado, lado, transform=[lado / (x2 - x1), 0, 0, lado / (y2 - y1), 0, 0])
        dr.add(qr)
        renderPDF.draw(dr, g.c, 17.48 * cm, g.ry(1.67 + 1.52))
        for i, ln in enumerate(g.quebrar(TEXTO_QR, self.fontes.conteudo, 6, 4.72)[:3]):
            g.texto(15.80, 3.36 + _pt_cm(6) + i * 0.23, ln, self.fontes.conteudo, 6)
        g.linha_h(4.34)

    def _bloco(self, g: _Desenho, b: Bloco, y: float, extra_descricao: float) -> float:
        f = self.fontes
        if b.frase_suprimido:
            g.texto(
                ESQ + PAD, y + (ALTURA_BLOCO_SUPRIMIDO + _pt_cm(7)) / 2 - 0.03, b.frase_suprimido, f.rotulo_negrito, 7
            )
            y += ALTURA_BLOCO_SUPRIMIDO
            g.linha_h(y)
            return y
        # Título do bloco (sombreado) na primeira coluna da primeira linha
        linhas = [ln for ln in b.linhas if not (ln.suprimivel_se_vazia and ln.vazia())]
        primeira = True
        for ln in linhas:
            alt = ln.altura
            if any(c.multilinha for c in ln.celulas):
                alt += extra_descricao
            if primeira:
                g.sombra(ESQ, y, W1, ln.altura)
                g.texto(ESQ + PAD, y + PAD + _pt_cm(7), b.titulo, f.rotulo_negrito, 7, W1 - 2 * PAD)
                primeira = False
            for cel in ln.celulas:
                larg = W1 if cel.span == 1 else (COL[min(cel.col + cel.span, 4) - 1] + W1 - COL[cel.col])
                self._campo(g, COL[cel.col], y, larg, alt, cel)
            y += alt
        g.linha_h(y)
        return y

    def _informacoes(self, g: _Desenho, y: float):
        f = self.fontes
        g.sombra(ESQ, y, W1, 0.41)
        g.texto(ESQ + PAD, y + PAD + _pt_cm(7), "INFORMAÇÕES COMPLEMENTARES", f.rotulo_negrito, 7, W1 - 2 * PAD)
        y += 0.41
        passo = _pt_cm(7) * 1.25
        texto = self._info_complementares()
        corpo, totais = (texto.rsplit("\n", 1) + [""])[:2] if "\n" in texto else ("", texto)
        util = LARG - 2 * PAD
        disponiveis = int((FIM_PAGINA - y - 0.15) / passo)
        linhas_tot = g.quebrar(totais, f.conteudo, 7, util)
        linhas_corpo = g.quebrar(corpo, f.conteudo, 7, util) if corpo else []
        cabe = max(0, disponiveis - len(linhas_tot))
        if len(linhas_corpo) > cabe:
            linhas_corpo = linhas_corpo[:cabe]
            if linhas_corpo:
                linhas_corpo[-1] = g.ajustar(linhas_corpo[-1] + " ...", f.conteudo, 7, util)
        for i, ln in enumerate(linhas_corpo + linhas_tot):
            g.texto(ESQ + PAD, y + _pt_cm(7) + i * passo, ln, f.conteudo, 7)

    def _marca_dagua(self, c: Canvas):
        c.saveState()
        c.setFillColor(CINZA_35)
        c.setFont(self.fontes.rotulo, 90)
        c.translate(PAGE_W / 2, PAGE_H / 2)
        c.rotate(55)
        c.drawCentredString(0, -30, self.marca)
        c.restoreState()


def gerar_danfse(nfse_xml: bytes, *, cancelada: bool = False, substituida: bool = False) -> bytes:
    marca = "CANCELADA" if cancelada else ("SUBSTITUÍDA" if substituida else None)
    return GeradorDanfse(NFSeDoc.from_bytes(nfse_xml), marca).gerar()
