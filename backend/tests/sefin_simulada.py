"""Sefin Nacional + ADN simulados para os testes ponta a ponta (sem certificado real e sem internet).

Diferente das Sefin falsas dos outros testes (que devolvem sempre a mesma NFS-e de exemplo), esta confere a DPS
como a Sefin real e monta a NFS-e a partir dela:

- corpo ``dpsXmlGZipB64`` (base64 + gzip), XML em UTF-8 e sem prefixo de namespace (E1228/E1229);
- DPS válida no XSD oficial local, assinatura conferida com o certificado da conexão (o "mTLS" da simulação é o
  certificado com que o ``SefinClient`` foi criado) e referência ao ``Id`` do ``infDPS``;
- ``Id`` recalculado a partir de cLocEmi/inscrição/série/nDPS, CNPJ do prestador igual ao do certificado, série na
  faixa de API (E0010), ``tpAmb`` do ambiente chamado, ``dhEmi`` não posterior ao processamento, ``dCompet`` igual à
  data de ``dhEmi``, CST = 3 primeiros dígitos do ``cClassTrib`` (E0959) e NBS presente com IBS/CBS (E0322);
- a NFS-e devolvida (com ``infNFSe/IBSCBS`` calculado quando a DPS tem o grupo) e os eventos são assinados por um
  certificado "da Sefin" e validados no XSD; se a própria simulação gerar XML inválido, o teste falha.

Os códigos dos erros que não estão documentados no CLAUDE.md levam o prefixo ``SIM`` (são só da simulação).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import httpx
from lxml import etree

from emissor.nfse import codes
from emissor.nfse.certificate import Certificado
from emissor.nfse.client import gzip_b64, ungzip_b64
from emissor.nfse.constants import NS_DSIG, NS_NFSE
from emissor.nfse.dps_builder import id_dps
from emissor.nfse.models import RETENCOES_PIS_COFINS
from emissor.nfse.signer import assinar, verificar
from emissor.nfse.xsd import validar_dps, validar_evento, validar_nfse, validar_pedido_evento

N = f"{{{NS_NFSE}}}"
TZ = ZoneInfo("America/Sao_Paulo")
DOIS = Decimal("0.01")

# Alíquotas fictícias, só para a NFS-e simulada ter valores coerentes.
ALIQ_ISS = Decimal("2.00")
ALIQ_IBS_UF = Decimal("0.10")
ALIQ_IBS_MUN = Decimal("0.00")
ALIQ_CBS = Decimal("0.90")

HTML_403 = "<html><head><title>403 - Forbidden: Access is denied.</title></head><body>Forbidden</body></html>"


class ErroSimulacao(AssertionError):
    """A própria simulação montou um XML inválido (defeito do teste, não do sistema)."""


def _q(v: Decimal) -> str:
    return str(v.quantize(DOIS, ROUND_HALF_UP))


def _dv_mod11(base: str) -> str:
    """Dígito verificador módulo 11 (pesos 2..9 da direita para a esquerda, letras pelo código ASCII - 48)."""
    soma, peso = 0, 2
    for ch in reversed(base):
        soma += (ord(ch) - 48) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return "0" if resto < 2 else str(11 - resto)


def conferir_assinatura_sefin(xml: bytes, cert: Certificado) -> None:
    """Confere a assinatura "da Sefin" (a última, filha da raiz) da NFS-e ou do evento.

    O ``verificar`` do sistema (signxml) confere a **primeira** assinatura do documento, que numa NFS-e é a da DPS
    embutida; aqui a conta é feita à mão: digest exc-c14n do grupo ``inf*`` e RSA-SHA256 do ``SignedInfo``.
    """
    import base64
    import hashlib

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    ds = f"{{{NS_DSIG}}}"
    root = etree.fromstring(xml)
    sig = root[-1]
    assert sig.tag == f"{ds}Signature", "a assinatura da Sefin deve ser o último filho da raiz"
    inf = root[0]
    ref = sig.find(f"{ds}SignedInfo/{ds}Reference")
    assert ref.get("URI") == f"#{inf.get('Id')}"
    digest = hashlib.sha256(etree.tostring(inf, method="c14n", exclusive=True)).digest()
    assert base64.b64encode(digest).decode() == ref.findtext(f"{ds}DigestValue"), "digest da Sefin não confere"
    signed_info = etree.tostring(sig.find(f"{ds}SignedInfo"), method="c14n", exclusive=True)
    cert.cert.public_key().verify(
        base64.b64decode(sig.findtext(f"{ds}SignatureValue")), signed_info, padding.PKCS1v15(), hashes.SHA256()
    )


def _cnpj_do_certificado(cert: Certificado) -> str:
    return cert.info.cnpj or ""


@dataclass
class NotaSimulada:
    chave: str
    id_dps: str
    numero: int
    tp_amb: str
    xml: bytes
    cert_prestador_pem: bytes  # certificado da conexão que enviou a DPS
    eventos: dict[str, bytes] = field(default_factory=dict)  # tipo → evento


class SefinSimulada:
    """Sefin + ADN em memória. Use ``transport(cert)`` para criar o transporte de um ``SefinClient``."""

    def __init__(self, cert_sefin: Certificado):
        self.cert_sefin = cert_sefin
        self.notas: dict[str, NotaSimulada] = {}  # chave → nota
        self.por_dps: dict[str, str] = {}  # idDps → chave
        self.adn: list[dict] = []
        self.numero = 0
        self.n_dfse = 1000
        self.chamadas: list[str] = []
        self.dps_recebidas: list[bytes] = []
        self.pedidos_recebidos: list[bytes] = []
        self.rejeicoes: list[dict] = []  # respostas de rejeição devolvidas (para o teste mostrar o motivo)
        # Ganchos de comportamento:
        self.rejeitar_tomador: dict[str, tuple[str, str]] = {}  # documento → (código, descrição)
        self.certificados_recusados: set[str] = set()  # CNPJ → 403 IIS (como certificado fora da ICP-Brasil)
        self.cair_apos_processar: int = 0  # próximas N emissões: processa e "perde" a resposta (timeout)
        self.fora_do_ar: bool = False  # 503 em tudo

    # --- infraestrutura ------------------------------------------------------------------------------
    def transport(self, cert: Certificado) -> httpx.MockTransport:
        return httpx.MockTransport(lambda req: self.handler(req, cert))

    def handler(self, req: httpx.Request, cert: Certificado) -> httpx.Response:
        path = req.url.path
        self.chamadas.append(f"{req.method} {req.url.host}{path}")
        if self.fora_do_ar:
            return httpx.Response(503, text="Service Unavailable")
        if _cnpj_do_certificado(cert) in self.certificados_recusados:
            return httpx.Response(403, text=HTML_403, headers={"Content-Type": "text/html"})
        tp_amb = "2" if "producaorestrita" in req.url.host else "1"
        if req.url.host.startswith("sefin."):
            return self._sefin(req, path.removeprefix("/SefinNacional"), cert, tp_amb)
        if req.url.host.startswith("adn."):
            return self._adn_rotas(req, path, tp_amb)
        return httpx.Response(404)

    def _sefin(self, req: httpx.Request, path: str, cert: Certificado, tp_amb: str) -> httpx.Response:
        if req.method == "POST" and path == "/nfse":
            return self._emitir(req, cert, tp_amb)
        if m := re.fullmatch(r"/dps/(DPS\w{42})", path):
            chave = self.por_dps.get(m.group(1))
            if chave is None or self.notas[chave].tp_amb != tp_amb:
                return httpx.Response(404, json={"erro": [{"codigo": "SIM404", "descricao": "DPS não encontrada"}]})
            return httpx.Response(200, json={"chaveAcesso": chave, "idDps": m.group(1)})
        if req.method == "GET" and (m := re.fullmatch(r"/nfse/(\w{50})", path)):
            nota = self.notas.get(m.group(1))
            if nota is None or nota.tp_amb != tp_amb:
                return httpx.Response(404, json={"erro": [{"codigo": "SIM404", "descricao": "NFS-e não encontrada"}]})
            return httpx.Response(200, json={"chaveAcesso": nota.chave, "nfseXmlGZipB64": gzip_b64(nota.xml)})
        if req.method == "POST" and (m := re.fullmatch(r"/nfse/(\w{50})/eventos", path)):
            return self._evento(req, m.group(1), cert, tp_amb)
        if m := re.fullmatch(r"/nfse/(\w{50})/eventos/(\d{6})/(\d+)", path):
            nota = self.notas.get(m.group(1))
            ev = nota.eventos.get(m.group(2)) if nota and m.group(3) == "1" else None
            if ev is None:
                return httpx.Response(404, json={"erro": [{"codigo": "SIM404", "descricao": "Evento não encontrado"}]})
            return httpx.Response(200, json={"eventoXmlGZipB64": gzip_b64(ev)})
        return httpx.Response(404)

    # --- emissão -------------------------------------------------------------------------------------
    def _rejeitar(self, codigo: str, descricao: str, status: int = 400) -> httpx.Response:
        corpo = {"erros": [{"Codigo": codigo, "Descricao": descricao}]}
        self.rejeicoes.append(corpo)
        return httpx.Response(status, json=corpo)

    def _emitir(self, req: httpx.Request, cert: Certificado, tp_amb: str) -> httpx.Response:
        dps = ungzip_b64(json.loads(req.content)["dpsXmlGZipB64"])
        self.dps_recebidas.append(dps)
        problema = self._conferir_documento(dps, cert, "infDPS", validar_dps)
        if problema:
            return self._rejeitar(*problema)
        root = etree.fromstring(dps)
        inf = root.find(f"{N}infDPS")

        def t(caminho: str) -> str | None:
            el = inf.find("/".join(f"{N}{p}" for p in caminho.split("/")))
            return el.text if el is not None else None

        prest_doc = t("prest/CNPJ") or t("prest/CPF") or ""
        esperado = id_dps(t("cLocEmi"), prest_doc, t("serie"), int(t("nDPS")))
        if inf.get("Id") != esperado:
            return self._rejeitar("SIM-ID", f"Id da DPS {inf.get('Id')} difere do calculado {esperado}.")
        if prest_doc != _cnpj_do_certificado(cert):
            return self._rejeitar("SIM-CERT", "CNPJ do prestador diferente do CNPJ do certificado da conexão.")
        if not 1 <= int(t("serie")) <= 49999:
            return self._rejeitar("E0010", "Série da DPS fora da faixa permitida para o canal de emissão.")
        if t("tpAmb") != tp_amb:
            return self._rejeitar("SIM-AMB", f"tpAmb {t('tpAmb')} enviado ao ambiente {tp_amb}.")
        dh_emi = datetime.fromisoformat(t("dhEmi"))
        agora = datetime.now(TZ)
        if dh_emi > agora:
            return self._rejeitar("SIM-DHEMI", "Data de emissão posterior à data de processamento.")
        if t("dCompet") != dh_emi.date().isoformat():
            return self._rejeitar("SIM-DCOMPET", "Data de competência diferente da data de emissão.")
        if inf.find(f"{N}IBSCBS") is not None:
            cst, cclass = t("IBSCBS/valores/trib/gIBSCBS/CST"), t("IBSCBS/valores/trib/gIBSCBS/cClassTrib")
            if cst != cclass[:3]:
                return self._rejeitar("E0959", "CST do IBS/CBS incompatível com a classificação tributária.")
            if not t("serv/cServ/cNBS"):
                return self._rejeitar("E0322", "Código NBS obrigatório quando o grupo IBS/CBS é informado.")
        rejeicao = _regras_tributos(t)
        if rejeicao:
            return self._rejeitar(*rejeicao)
        toma = t("toma/CNPJ") or t("toma/CPF") or ""
        if toma in self.rejeitar_tomador:
            return self._rejeitar(*self.rejeitar_tomador[toma])
        if inf.get("Id") in self.por_dps:
            chave = self.por_dps[inf.get("Id")]
            return self._rejeitar("SIM-DUP", f"DPS já existe: NFS-e {chave} gerada para este Id.")

        nota = self._gerar_nfse(root, prest_doc, tp_amb, agora, cert)
        if self.cair_apos_processar:
            self.cair_apos_processar -= 1
            raise httpx.ReadTimeout("simulação: a Sefin processou, mas a resposta não chegou")
        return httpx.Response(
            201,
            json={
                "tipoAmbiente": int(tp_amb),
                "versaoAplicativo": "SefinNac_simulada",
                "dataHoraProcessamento": agora.isoformat(timespec="seconds"),
                "idDps": nota.id_dps,
                "chaveAcesso": nota.chave,
                "nfseXmlGZipB64": gzip_b64(nota.xml),
                "alertas": [],
            },
        )

    def _conferir_documento(self, xml: bytes, cert: Certificado, tag_inf: str, validar) -> tuple[str, str] | None:
        if not xml.lstrip().startswith(b"<?xml") or b"encoding" in xml[:60] and b"UTF-8" not in xml[:60].upper():
            return ("E1229", "O XML deve estar codificado em UTF-8.")
        try:
            root = etree.fromstring(xml)
        except etree.XMLSyntaxError as exc:
            return ("SIM-XML", f"XML mal formado: {exc}")
        if any(prefixo is not None for el in root.iter() for prefixo in (el.nsmap or {})):
            return ("E1228", "Não é permitido o uso de prefixo de namespace.")
        erros = validar(root)
        if erros:
            return ("SIM-XSD", "XML fora do esquema: " + "; ".join(map(str, erros[:5])))
        inf = root.find(f"{N}{tag_inf}")
        ref = root.find(f"{{{NS_DSIG}}}Signature/{{{NS_DSIG}}}SignedInfo/{{{NS_DSIG}}}Reference")
        if ref is None or ref.get("URI") != f"#{inf.get('Id')}":
            return ("SIM-ASSIN", "A assinatura não referencia o Id do grupo assinado.")
        try:
            verificar(xml, cert.cert_pem())
        except Exception as exc:  # noqa: BLE001 - qualquer falha do verificador = assinatura inválida
            return ("SIM-ASSIN", f"Assinatura inválida: {exc}")
        return None

    def _gerar_nfse(
        self, dps: etree._Element, prest_doc: str, tp_amb: str, agora: datetime, cert: Certificado
    ) -> NotaSimulada:
        inf_dps = dps.find(f"{N}infDPS")

        def t(caminho: str) -> str | None:
            el = inf_dps.find("/".join(f"{N}{p}" for p in caminho.split("/")))
            return el.text if el is not None else None

        self.numero += 1
        self.n_dfse += 1
        c_mun = t("cLocEmi")
        tp_insc = "1" if len(prest_doc) == 11 else "2"
        base = f"{c_mun}2{tp_insc}{prest_doc.zfill(14)}{self.numero:013d}{agora:%y%m}{self.numero:09d}"
        chave = base + _dv_mod11(base)

        v_serv = Decimal(t("valores/vServPrest/vServ"))
        tributavel = t("valores/trib/tribMun/tribISSQN") == "1"
        aliq = Decimal(t("valores/trib/tribMun/pAliq") or ALIQ_ISS)
        v_iss = (v_serv * aliq / 100).quantize(DOIS, ROUND_HALF_UP) if tributavel else Decimal(0)
        retido = t("valores/trib/tribMun/tpRetISSQN") in ("2", "3")
        fed = sum(Decimal(t(f"valores/trib/tribFed/{c}") or 0) for c in ("vRetCP", "vRetIRRF", "vRetCSLL"))
        pis_ret, cofins_ret, _ = RETENCOES_PIS_COFINS.get(
            t("valores/trib/tribFed/piscofins/tpRetPisCofins") or "", (0, 0, 0)
        )
        v_pis_cofins = (Decimal(t("valores/trib/tribFed/piscofins/vPis") or 0) if pis_ret else 0) + (
            Decimal(t("valores/trib/tribFed/piscofins/vCofins") or 0) if cofins_ret else 0
        )
        v_total_ret = fed + (v_iss if retido else 0)
        v_liq = v_serv - v_total_ret - v_pis_cofins
        c_loc = t("serv/locPrest/cLocPrestacao") or c_mun
        nome_mun = (codes.municipio(c_loc) or ("MUNICIPIO",))[0]

        nfse = etree.Element(f"{N}NFSe", nsmap={None: NS_NFSE}, versao="1.01")
        inf = etree.SubElement(nfse, f"{N}infNFSe", Id=f"NFS{chave}")

        def sub(pai: etree._Element, tag: str, texto: str | None = None) -> etree._Element:
            el = etree.SubElement(pai, f"{N}{tag}")
            if texto is not None:
                el.text = texto
            return el

        sub(inf, "xLocEmi", (codes.municipio(c_mun) or (nome_mun,))[0])
        sub(inf, "xLocPrestacao", nome_mun)
        sub(inf, "nNFSe", str(self.numero))
        sub(inf, "cLocIncid", c_loc)
        sub(inf, "xLocIncid", nome_mun)
        sub(inf, "xTribNac", "Serviço de teste (descrição simulada)")
        if t("serv/cServ/cNBS"):
            sub(inf, "xNBS", "NBS de teste (descrição simulada)")
        sub(inf, "verAplic", "SefinNac_simulada")
        sub(inf, "ambGer", "2")
        sub(inf, "tpEmis", "1")
        sub(inf, "procEmi", "1")
        sub(inf, "cStat", "100")
        sub(inf, "dhProc", agora.isoformat(timespec="seconds"))
        sub(inf, "nDFSe", str(self.n_dfse))
        emit = sub(inf, "emit")
        sub(emit, "CNPJ" if tp_insc == "2" else "CPF", prest_doc)
        sub(emit, "xNome", "PRESTADOR EXEMPLO LTDA")
        end = sub(emit, "enderNac")
        for tag, valor in [("xLgr", "RUA EXEMPLO DO PRESTADOR"), ("nro", "200"), ("xBairro", "CENTRO")]:
            sub(end, tag, valor)
        sub(end, "cMun", c_mun)
        sub(end, "UF", (codes.municipio(c_mun) or ("", "SC"))[1])
        sub(end, "CEP", "88010400")
        for tag in ("fone", "email"):
            if t(f"prest/{tag}"):
                sub(emit, tag, t(f"prest/{tag}"))
        val = sub(inf, "valores")
        if tributavel:
            sub(val, "vBC", _q(v_serv))
            sub(val, "pAliqAplic", _q(aliq))
            sub(val, "vISSQN", _q(v_iss))
        if v_total_ret:
            sub(val, "vTotalRet", _q(v_total_ret))
        sub(val, "vLiq", _q(v_liq))
        if inf_dps.find(f"{N}IBSCBS") is not None:
            self._ibscbs(sub(inf, "IBSCBS"), sub, c_loc, nome_mun, v_serv - v_iss, v_liq)
        inf.append(etree.fromstring(etree.tostring(dps)))

        assinada = assinar(nfse, self.cert_sefin, f"NFS{chave}")
        erros = validar_nfse(assinada)
        if erros:
            raise ErroSimulacao(f"NFS-e simulada inválida no XSD: {[str(e) for e in erros]}")
        xml = etree.tostring(assinada, xml_declaration=True, encoding="UTF-8")
        nota = NotaSimulada(
            chave=chave,
            id_dps=inf_dps.get("Id"),
            numero=self.numero,
            tp_amb=tp_amb,
            xml=xml,
            cert_prestador_pem=cert.cert_pem(),
        )
        self.notas[chave] = nota
        self.por_dps[nota.id_dps] = chave
        self._distribuir(xml, "NFSE", chave)
        return nota

    @staticmethod
    def _ibscbs(g, sub, c_loc: str, nome_mun: str, v_bc: Decimal, v_liq: Decimal) -> None:
        v_uf = (v_bc * ALIQ_IBS_UF / 100).quantize(DOIS, ROUND_HALF_UP)
        v_mun = (v_bc * ALIQ_IBS_MUN / 100).quantize(DOIS, ROUND_HALF_UP)
        v_cbs = (v_bc * ALIQ_CBS / 100).quantize(DOIS, ROUND_HALF_UP)
        sub(g, "cLocalidadeIncid", c_loc)
        sub(g, "xLocalidadeIncid", nome_mun)
        val = sub(g, "valores")
        sub(val, "vBC", _q(v_bc))
        for grupo, aliq, tags in [
            ("uf", ALIQ_IBS_UF, ("pIBSUF", "pAliqEfetUF")),
            ("mun", ALIQ_IBS_MUN, ("pIBSMun", "pAliqEfetMun")),
            ("fed", ALIQ_CBS, ("pCBS", "pAliqEfetCBS")),
        ]:
            el = sub(val, grupo)
            for tag in tags:
                sub(el, tag, _q(aliq))
        tot = sub(g, "totCIBS")
        sub(tot, "vTotNF", _q(v_liq + v_uf + v_mun + v_cbs))
        gibs = sub(tot, "gIBS")
        sub(gibs, "vIBSTot", _q(v_uf + v_mun))
        sub(sub(gibs, "gIBSUFTot"), "vIBSUF", _q(v_uf))
        sub(sub(gibs, "gIBSMunTot"), "vIBSMun", _q(v_mun))
        sub(sub(tot, "gCBS"), "vCBS", _q(v_cbs))

    # --- eventos -------------------------------------------------------------------------------------
    def _evento(self, req: httpx.Request, chave: str, cert: Certificado, tp_amb: str) -> httpx.Response:
        pedido = ungzip_b64(json.loads(req.content)["pedidoRegistroEventoXmlGZipB64"])
        self.pedidos_recebidos.append(pedido)

        def rejeitar(codigo: str, descricao: str) -> httpx.Response:
            corpo = {"erro": [{"codigo": codigo, "descricao": descricao}]}
            self.rejeicoes.append(corpo)
            return httpx.Response(400, json=corpo)

        problema = self._conferir_documento(pedido, cert, "infPedReg", validar_pedido_evento)
        if problema:
            return rejeitar(*problema)
        inf = etree.fromstring(pedido).find(f"{N}infPedReg")
        det = next(el for el in inf if etree.QName(el).localname.startswith("e"))
        tipo = etree.QName(det).localname[1:]
        if inf.get("Id") != f"PRE{chave}{tipo}" or inf.findtext(f"{N}chNFSe") != chave:
            return rejeitar("SIM-ID", "Id do pedido ou chave diferente da chave da URL.")
        if inf.findtext(f"{N}CNPJAutor") != _cnpj_do_certificado(cert):
            return rejeitar("E0812", "CNPJ do autor do evento diferente do CNPJ do certificado.")
        if inf.findtext(f"{N}tpAmb") != tp_amb:
            return rejeitar("SIM-AMB", "tpAmb do pedido diferente do ambiente chamado.")
        if datetime.fromisoformat(inf.findtext(f"{N}dhEvento")) > datetime.now(TZ) + timedelta(seconds=5):
            return rejeitar("E1843", "Data/hora do evento posterior à data de recebimento.")
        nota = self.notas.get(chave)
        if nota is None or nota.tp_amb != tp_amb:
            return rejeitar("SIM404", "NFS-e não encontrada.")
        if nota.eventos:
            return rejeitar("E0840", "NFS-e já cancelada ou substituída.")
        evento = self._registrar_evento(nota, etree.fromstring(pedido), tipo)
        return httpx.Response(
            201,
            json={
                "tipoAmbiente": int(tp_amb),
                "dataHoraProcessamento": datetime.now(TZ).isoformat(timespec="seconds"),
                "eventoXmlGZipB64": gzip_b64(evento),
            },
        )

    def _registrar_evento(self, nota: NotaSimulada, pedido: etree._Element, tipo: str) -> bytes:
        id_ped = pedido.find(f"{N}infPedReg").get("Id")
        ev = etree.Element(f"{N}evento", nsmap={None: NS_NFSE}, versao="1.01")
        inf = etree.SubElement(ev, f"{N}infEvento", Id=f"EVT{id_ped[3:]}001")
        self.n_dfse += 1
        for tag, v in [
            ("verAplic", "SefinNac_simulada"),
            ("ambGer", "2"),
            ("nSeqEvento", "1"),
            ("dhProc", datetime.now(TZ).isoformat(timespec="seconds")),
            ("nDFSe", str(self.n_dfse)),
        ]:
            etree.SubElement(inf, f"{N}{tag}").text = v
        inf.append(pedido)
        xml = etree.tostring(assinar(ev, self.cert_sefin), xml_declaration=True, encoding="UTF-8")
        erros = validar_evento(xml)
        if erros:
            raise ErroSimulacao(f"Evento simulado inválido no XSD: {[str(e) for e in erros]}")
        nota.eventos[tipo] = xml
        self._distribuir(xml, "EVENTO", nota.chave, tipo)
        return xml

    def cancelar_no_portal(self, chave: str, motivo: str = "Cancelada pelo portal nacional") -> bytes:
        """Cancelamento feito fora do sistema (portal/prefeitura): só aparece pela distribuição do ADN."""
        nota = self.notas[chave]
        ped = etree.Element(f"{N}pedRegEvento", nsmap={None: NS_NFSE}, versao="1.01")
        inf = etree.SubElement(ped, f"{N}infPedReg", Id=f"PRE{chave}101101")
        prest = etree.fromstring(nota.xml).findtext(f".//{N}prest/{N}CNPJ")
        for tag, v in [
            ("tpAmb", nota.tp_amb),
            ("verAplic", "EmissorWeb"),
            ("dhEvento", datetime.now(TZ).isoformat(timespec="seconds")),
            ("CNPJAutor", prest),
            ("chNFSe", chave),
        ]:
            etree.SubElement(inf, f"{N}{tag}").text = v
        det = etree.SubElement(inf, f"{N}e101101")
        for tag, v in [("xDesc", "Cancelamento de NFS-e"), ("cMotivo", "9"), ("xMotivo", motivo)]:
            etree.SubElement(det, f"{N}{tag}").text = v
        return self._registrar_evento(nota, ped, "101101")

    # --- ADN -----------------------------------------------------------------------------------------
    def _distribuir(self, xml: bytes, tipo_doc: str, chave: str, tipo_evento: str | None = None) -> None:
        self.adn.append(
            {
                "NSU": len(self.adn) + 1,
                "ChaveAcesso": chave,
                "TipoDocumento": tipo_doc,
                "TipoEvento": {"101101": "CANCELAMENTO"}.get(tipo_evento or "", tipo_evento),
                "ArquivoXml": gzip_b64(xml),
                "DataHoraGeracao": datetime.now(TZ).replace(tzinfo=None).isoformat(timespec="seconds"),
            }
        )

    def _adn_rotas(self, req: httpx.Request, path: str, tp_amb: str) -> httpx.Response:
        docs = [d for d in self.adn if self.notas[d["ChaveAcesso"]].tp_amb == tp_amb]
        if m := re.fullmatch(r"/contribuintes/DFe/(\d+)", path):
            if req.url.params.get("lote") != "true":
                return httpx.Response(400, json={"StatusProcessamento": "REJEICAO", "Erros": [{"Codigo": "SIM"}]})
            return self._lote([d for d in docs if d["NSU"] > int(m.group(1))][:50], tp_amb)
        if m := re.fullmatch(r"/contribuintes/NFSe/(\w{50})/Eventos", path):
            return self._lote(
                [d for d in docs if d["ChaveAcesso"] == m.group(1) and d["TipoDocumento"] == "EVENTO"], tp_amb
            )
        return httpx.Response(404)

    @staticmethod
    def _lote(docs: list[dict], tp_amb: str) -> httpx.Response:
        base = {
            "TipoAmbiente": "HOMOLOGACAO" if tp_amb == "2" else "PRODUCAO",
            "VersaoAplicativo": "ADN_simulado",
            "DataHoraProcessamento": datetime.now(TZ).replace(tzinfo=None).isoformat(timespec="seconds"),
        }
        if not docs:
            return httpx.Response(
                404,
                json=base
                | {
                    "StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO",
                    "LoteDFe": None,
                    "Erros": [{"Codigo": "E2220", "Descricao": "Nenhum documento localizado."}],
                },
            )
        return httpx.Response(200, json=base | {"StatusProcessamento": "DOCUMENTOS_LOCALIZADOS", "LoteDFe": docs})


BRASILAPI_CLIENTE = {
    "cnpj": "11222333000181",
    "razao_social": "CLIENTE EXEMPLO LTDA",
    "nome_fantasia": "",
    "logradouro": "RUA EXEMPLO",
    "numero": "100",
    "complemento": "",
    "bairro": "CENTRO",
    "cep": "88010000",
    "codigo_municipio_ibge": 4205407,
    "municipio": "FLORIANOPOLIS",
    "uf": "SC",
    "email": None,
    "ddd_telefone_1": "4830000001",
    "descricao_situacao_cadastral": "ATIVA",
}


def brasilapi_simulada(req: httpx.Request) -> httpx.Response:
    """BrasilAPI com um único CNPJ cadastrado; os demais não existem."""
    if req.url.host == "brasilapi.com.br" and req.url.path.endswith("/" + BRASILAPI_CLIENTE["cnpj"]):
        return httpx.Response(200, json=BRASILAPI_CLIENTE)
    if req.url.host == "brasilapi.com.br":
        return httpx.Response(404, json={"message": "CNPJ não encontrado"})
    return httpx.Response(404)


def _regras_tributos(t) -> tuple[str, str] | None:
    """Regras do Anexo I sobre alíquotas, PIS/COFINS, retenções e totais (município de incidência conveniado)."""
    op, reg_ap = t("prest/regTrib/opSimpNac"), t("prest/regTrib/regApTribSN")
    retido = t("valores/trib/tribMun/tpRetISSQN") in ("2", "3")
    p_aliq = t("valores/trib/tribMun/pAliq")
    v_serv = Decimal(t("valores/vServPrest/vServ"))
    if p_aliq is not None and Decimal(p_aliq) > 5:
        return ("E0595", "Não é permitido informar alíquota superior a 5%.")
    if op == "3" and reg_ap == "1":
        if retido and p_aliq is None:
            return ("E0621", "É obrigatório informar alíquota quando há indicação de retenção do ISSQN para ME/EPP.")
        if retido and Decimal(p_aliq) < Decimal("1.8"):
            return ("E0621", "Alíquota mínima de 1,8% para retenção do ISSQN de ME/EPP.")
        if not retido and p_aliq is not None:
            return ("E0625", "Não é permitido informar alíquota quando não há indicação de retenção do ISSQN.")
    elif p_aliq is not None:
        return ("E0617" if op == "1" else "E0635", "Não é permitido informar alíquota: município conveniado.")
    if retido and op == "2":
        return ("E0583", "Não é permitido retenção do ISSQN para MEI.")
    if t("valores/trib/tribMun/tpRetISSQN") == "2" and t("toma/end/endNac/cMun") is None:
        return ("E0237", "O endereço nacional do tomador deve ser informado quando o ISSQN for retido.")
    pc = "valores/trib/tribFed/piscofins"
    base = t(f"{pc}/vBCPisCofins")
    for aliq, valor, cod in (("pAliqPis", "vPis", "E0694"), ("pAliqCofins", "vCofins", "E0696")):
        if t(f"{pc}/{aliq}") is not None:
            esperado = (Decimal(base or 0) * Decimal(t(f"{pc}/{aliq}")) / 100).quantize(DOIS, ROUND_HALF_UP)
            if Decimal(t(f"{pc}/{valor}") or -1) != esperado:
                return (cod, f"{valor} não corresponde a BC x {aliq}.")
    tp_ret = t(f"{pc}/tpRetPisCofins")
    csll = t("valores/trib/tribFed/vRetCSLL")
    if tp_ret == "0" and csll is not None:
        return ("E0720", "Com PIS/COFINS/CSLL não retidos não é permitido informar vRetCSLL.")
    if tp_ret is not None and RETENCOES_PIS_COFINS[tp_ret][2] and csll is None:
        return ("E0724", "É obrigatório informar vRetCSLL para o tipo de retenção informado.")
    for c, cod in (("vRetCP", "E0699"), ("vRetIRRF", "E0700"), ("vRetCSLL", "E0701")):
        v = t(f"valores/trib/tribFed/{c}")
        if v is not None and not Decimal(0) < Decimal(v) < v_serv:
            return (cod, f"{c} deve ser maior que zero e menor que o valor do serviço.")
    tem_ind, tem_sn = t("valores/trib/totTrib/indTotTrib") is not None, t("valores/trib/totTrib/pTotTribSN") is not None
    if op == "3" and tem_ind:
        return ("E0712", "Para ME/EPP indTotTrib nunca poderá ser informado.")
    if op == "1" and (tem_ind or tem_sn):
        return ("E0713", "Para Não Optante do SN indTotTrib e pTotTribSN não podem ser informados.")
    if op == "2" and tem_sn:
        return ("E0710", "Para MEI pTotTribSN nunca poderá ser informado.")
    return None
