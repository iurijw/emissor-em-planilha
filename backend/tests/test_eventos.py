"""Cancelamento (evento 101101) e situação das notas pelo Ambiente Nacional (ADN)."""

from __future__ import annotations

import base64
import gzip
import io
import json
import re
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from lxml import etree

from emissor.api.app import create_app
from emissor.nfse.client import SefinClient, gzip_b64, ungzip_b64
from emissor.nfse.constants import NS_NFSE, Ambiente
from emissor.nfse.errors import SefinError, TipoErro
from emissor.nfse.eventos import EventoDoc, enviar_cancelamento, preparar_cancelamento
from emissor.nfse.signer import assinar, verificar
from emissor.nfse.xsd import validar_evento, validar_pedido_evento
from emissor.services import emissao as emissao_service
from emissor.services import eventos as eventos_service

from .conftest import CNPJ_PRESTADOR, SENHA_PFX

CHAVE = "4205407" + "2" + CNPJ_PRESTADOR + "0" * 27 + "1"
DH = datetime(2026, 10, 9, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
N = f"{{{NS_NFSE}}}"


def _pedido(certificado, chave=CHAVE, motivo="Valor digitado errado na nota", c_motivo="1"):
    return preparar_cancelamento(
        certificado,
        chave=chave,
        autor=CNPJ_PRESTADOR,
        ambiente=Ambiente.HOMOLOGACAO,
        c_motivo=c_motivo,
        x_motivo=motivo,
        dh_evento=DH,
    )


def _pedido_portal(chave: str, tipo: str, detalhe: dict[str, str], autor=CNPJ_PRESTADOR) -> etree._Element:
    """pedRegEvento de outro autor/canal (portal, prefeitura), sem assinatura."""
    ped = etree.Element(f"{N}pedRegEvento", nsmap={None: NS_NFSE}, versao="1.01")
    inf = etree.SubElement(ped, f"{N}infPedReg", Id=f"PRE{chave}{tipo}")
    for tag, v in [("tpAmb", "2"), ("verAplic", "EmissorWeb"), ("dhEvento", "2026-10-08T09:00:00-03:00")]:
        etree.SubElement(inf, f"{N}{tag}").text = v
    etree.SubElement(inf, f"{N}{'CPFAutor' if len(autor) == 11 else 'CNPJAutor'}").text = autor
    etree.SubElement(inf, f"{N}chNFSe").text = chave
    det = etree.SubElement(inf, f"{N}e{tipo}")
    for tag, v in detalhe.items():
        etree.SubElement(det, f"{N}{tag}").text = v
    return ped


def evento_de(pedido: bytes | etree._Element, cert=None, n_seq=1, dh_proc="2026-10-09T10:00:05-03:00") -> bytes:
    """Monta o <evento> que a Sefin devolve a partir do pedido (como o processamento real)."""
    ped = etree.fromstring(pedido) if isinstance(pedido, bytes) else pedido
    id_ped = ped.find(f"{N}infPedReg").get("Id")
    ev = etree.Element(f"{N}evento", nsmap={None: NS_NFSE}, versao="1.01")
    inf = etree.SubElement(ev, f"{N}infEvento", Id=f"EVT{id_ped[3:]}{n_seq:03d}")
    for tag, v in [("verAplic", "SefinNac_1.6"), ("ambGer", "2"), ("nSeqEvento", str(n_seq)), ("dhProc", dh_proc)]:
        etree.SubElement(inf, f"{N}{tag}").text = v
    etree.SubElement(inf, f"{N}nDFSe").text = "123"
    inf.append(ped)
    if cert is not None:
        ev = assinar(ev, cert)
    return etree.tostring(ev, xml_declaration=True, encoding="UTF-8")


# --- pedido de cancelamento (leiaute) ---------------------------------------------------------------


def test_pedido_cancelamento_valida_no_xsd_e_assinatura_confere(certificado):
    id_ped, xml = _pedido(certificado, motivo="  Valor “errado”\n na nota  ")
    assert id_ped == f"PRE{CHAVE}101101" and len(id_ped) == 59
    assert validar_pedido_evento(xml) == []
    assert verificar(xml, certificado.cert_pem())
    texto = xml.decode()
    assert texto.startswith("<?xml") and "<ds:" not in texto  # sem prefixo de namespace (E1228)
    assert '<pedRegEvento xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.01">' in texto
    assert f'<Reference URI="#{id_ped}">' in texto
    ordem = re.findall(r"<(tpAmb|verAplic|dhEvento|CNPJAutor|chNFSe|e101101|xDesc|cMotivo|xMotivo)>", texto)
    assert ordem == ["tpAmb", "verAplic", "dhEvento", "CNPJAutor", "chNFSe", "e101101", "xDesc", "cMotivo", "xMotivo"]
    assert "<dhEvento>2026-10-09T10:00:00-03:00</dhEvento>" in texto
    assert '<xMotivo>Valor "errado" na nota</xMotivo>' in texto


@pytest.mark.parametrize(
    ("kw", "trecho"),
    [
        ({"motivo": "curto demais"}, "pelo menos 15"),
        ({"c_motivo": "4"}, "Motivo do cancelamento"),
        ({"chave": "123"}, "Chave de acesso inválida"),
    ],
)
def test_pedido_invalido_nao_e_enviado(certificado, kw, trecho):
    with pytest.raises(SefinError) as exc:
        _pedido(certificado, **kw)
    assert exc.value.tipo == TipoErro.VALIDACAO_LOCAL
    assert trecho in exc.value.mensagens[0].descricao


def test_evento_devolvido_pela_sefin_e_lido(certificado):
    _, pedido = _pedido(certificado)
    xml = evento_de(pedido, certificado)
    assert validar_evento(xml) == []
    doc = EventoDoc.from_bytes(xml)
    assert doc.id == f"EVT{CHAVE}101101001"
    assert (doc.tipo, doc.chave, doc.n_seq, doc.tp_amb, doc.autor) == ("101101", CHAVE, 1, "2", CNPJ_PRESTADOR)
    assert doc.descricao == "Cancelamento de NFS-e"
    assert doc.motivo_texto == "Erro na emissão — Valor digitado errado na nota"
    assert doc.dh_processamento.isoformat() == "2026-10-09T10:00:05-03:00"

    subst = EventoDoc.from_bytes(
        evento_de(
            _pedido_portal(
                CHAVE,
                "105102",
                {"xDesc": "Cancelamento de NFS-e por Substituição", "cMotivo": "99", "chSubstituta": "9" * 50},
            )
        )
    )
    assert subst.tipo == "105102" and "NFS-e substituta: " + "9" * 50 in subst.motivo_texto


# --- cliente Sefin/ADN --------------------------------------------------------------------------------


class SefinAdnFalsa:
    """Sefin + ADN em memória: emite com chaves únicas, registra eventos e distribui por NSU."""

    def __init__(self, nfse_xml: bytes, cert=None):
        self.nfse_xml = nfse_xml
        self.cert = cert
        self.numero = 100
        self.eventos: dict[tuple[str, str], bytes] = {}  # (chave, tipo) → evento
        self.adn: list[dict] = []  # documentos distribuídos, em ordem de NSU
        self.rejeitar_evento: dict[str, tuple[int, dict]] = {}  # chave → resposta
        self.falhas_evento: list = []  # exceções/respostas antes do processamento normal
        self.pedidos: list[bytes] = []
        self.chamadas: list[str] = []
        self.lote_adn = 50

    def distribuir(self, xml: bytes, tipo_doc: str, chave: str, tipo_evento: str | None = None) -> None:
        self.adn.append(
            {
                "NSU": len(self.adn) + 1,
                "ChaveAcesso": chave,
                "TipoDocumento": tipo_doc,
                "TipoEvento": tipo_evento,
                "ArquivoXml": gzip_b64(xml),
                "DataHoraGeracao": "2026-10-09T10:00:06",
            }
        )

    def _adn(self, docs: list[dict], status_vazio=404) -> httpx.Response:
        base = {"TipoAmbiente": "HOMOLOGACAO", "VersaoAplicativo": "1.0", "DataHoraProcessamento": "2026-10-09T10:00"}
        if not docs:
            return httpx.Response(
                status_vazio,
                json=base
                | {
                    "StatusProcessamento": "NENHUM_DOCUMENTO_LOCALIZADO",
                    "LoteDFe": None,
                    "Erros": [{"Codigo": "E2220", "Descricao": "Nenhum documento localizado."}],
                },
            )
        return httpx.Response(200, json=base | {"StatusProcessamento": "DOCUMENTOS_LOCALIZADOS", "LoteDFe": docs})

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.chamadas.append(f"{req.method} {path}")
        if req.method == "POST" and path.endswith("/nfse"):
            self.numero += 1
            chave = "4205407" + "2" + CNPJ_PRESTADOR + f"{self.numero:028d}"
            nfse = self.nfse_xml.replace(b"<nNFSe>6</nNFSe>", f"<nNFSe>{self.numero}</nNFSe>".encode())
            self.distribuir(nfse, "NFSE", chave)
            return httpx.Response(201, json={"chaveAcesso": chave, "nfseXmlGZipB64": gzip_b64(nfse)})
        m = re.search(r"/nfse/(\d{50})/eventos$", path)
        if req.method == "POST" and m:
            chave = m.group(1)
            pedido = ungzip_b64(json.loads(req.content)["pedidoRegistroEventoXmlGZipB64"])
            self.pedidos.append(pedido)
            if self.falhas_evento:
                item = self.falhas_evento.pop(0)
                if isinstance(item, Exception):
                    raise item
                if isinstance(item, httpx.Response):
                    return item
            if chave in self.rejeitar_evento:
                st, corpo = self.rejeitar_evento[chave]
                return httpx.Response(st, json=corpo)
            assert validar_pedido_evento(pedido) == []
            evento = evento_de(pedido, self.cert)
            self.eventos[(chave, "101101")] = evento
            self.distribuir(evento, "EVENTO", chave, "CANCELAMENTO")
            return httpx.Response(201, json={"tipoAmbiente": 2, "eventoXmlGZipB64": gzip_b64(evento)})
        m = re.search(r"/nfse/(\d{50})/eventos/(\d{6})/(\d+)$", path)
        if m:
            ev = self.eventos.get((m.group(1), m.group(2)))
            if ev is None:
                return httpx.Response(404, json={"erro": {"codigo": "E2001", "descricao": "Evento não encontrado"}})
            return httpx.Response(200, json={"eventoXmlGZipB64": gzip_b64(ev)})
        m = re.search(r"/contribuintes/DFe/(\d+)$", path)
        if m:
            assert req.url.params.get("lote") == "true"
            nsu = int(m.group(1))
            return self._adn([d for d in self.adn if d["NSU"] > nsu][: self.lote_adn])
        m = re.search(r"/contribuintes/NFSe/(\d{50})/Eventos$", path)
        if m:
            return self._adn([d for d in self.adn if d["ChaveAcesso"] == m.group(1) and d["TipoDocumento"] == "EVENTO"])
        return httpx.Response(404)


@pytest.fixture
def falsa(nfse_exemplo_bytes, certificado):
    return SefinAdnFalsa(nfse_exemplo_bytes, certificado)


@pytest.fixture
def client(falsa, certificado):
    c = SefinClient(Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(falsa.handler))
    yield c
    c.close()


def test_cancelamento_aceito(client, falsa, certificado):
    _, pedido = _pedido(certificado)
    r = enviar_cancelamento(client, CHAVE, pedido, dormir=lambda s: None)
    assert not r.recuperado and EventoDoc.from_bytes(r.evento_xml).tipo == "101101"
    assert falsa.chamadas == [f"POST /SefinNacional/nfse/{CHAVE}/eventos"]


def test_cancelamento_rejeitado_tem_codigo_e_dica_sem_reenviar(client, falsa, certificado):
    # A Sefin devolve "erro" como lista em eventos rejeitados (o Swagger diz objeto).
    falsa.rejeitar_evento[CHAVE] = (
        400,
        {"erro": [{"codigo": "E0822", "descricao": "Cancelamento fora do prazo", "complemento": None}]},
    )
    _, pedido = _pedido(certificado)
    with pytest.raises(SefinError) as exc:
        enviar_cancelamento(client, CHAVE, pedido, dormir=lambda s: None)
    assert exc.value.tipo == TipoErro.REJEICAO
    assert exc.value.mensagens[0].codigo == "E0822" and "Análise Fiscal" in exc.value.mensagens[0].dica
    assert len(falsa.pedidos) == 1


def test_queda_apos_envio_recupera_evento_sem_reenviar(client, falsa, certificado):
    _, pedido = _pedido(certificado)
    falsa.eventos[(CHAVE, "101101")] = evento_de(pedido)  # a Sefin processou, mas a resposta se perdeu
    falsa.falhas_evento.append(httpx.ReadTimeout("caiu"))
    r = enviar_cancelamento(client, CHAVE, pedido, dormir=lambda s: None)
    assert r.recuperado and len(falsa.pedidos) == 1
    assert falsa.chamadas[-1] == f"GET /SefinNacional/nfse/{CHAVE}/eventos/101101/1"


def test_erro_500_sem_evento_reenvia_o_mesmo_pedido(client, falsa, certificado):
    _, pedido = _pedido(certificado)
    falsa.falhas_evento.append(httpx.Response(500, json={"erro": []}))
    r = enviar_cancelamento(client, CHAVE, pedido, dormir=lambda s: None)
    assert not r.recuperado and falsa.pedidos == [pedido, pedido]


def test_ja_cancelada_e0840_recupera_evento_existente(client, falsa, certificado):
    _, pedido = _pedido(certificado)
    falsa.eventos[(CHAVE, "101101")] = evento_de(pedido)
    falsa.rejeitar_evento[CHAVE] = (400, {"erro": [{"codigo": "E0840", "descricao": "Evento não permitido"}]})
    assert enviar_cancelamento(client, CHAVE, pedido, dormir=lambda s: None).recuperado


def test_distribuicao_adn(client, falsa, certificado):
    assert client.distribuicao_dfe(0).documentos == []  # 404 com corpo = nada a distribuir
    _, pedido = _pedido(certificado)
    falsa.distribuir(evento_de(pedido), "EVENTO", CHAVE, "CANCELAMENTO")
    lote = client.distribuicao_dfe(0)
    assert lote.status == "DOCUMENTOS_LOCALIZADOS"
    d = lote.documentos[0]
    assert (d.nsu, d.tipo_documento, d.tipo_evento, d.chave_acesso) == (1, "EVENTO", "CANCELAMENTO", CHAVE)
    assert EventoDoc.from_bytes(d.xml).tipo == "101101"
    # O log da troca não guarda os XMLs embutidos (vão para data/xml).
    from emissor import config

    logs = list(config.SEFIN_LOG_DIR.rglob("*adn_dfe_resp.json"))
    assert logs and all("caracteres omitidos" in p.read_text() or "LoteDFe" in p.read_text() for p in logs)
    assert not any(gzip_b64(evento_de(pedido))[:40] in p.read_text() for p in logs)


@pytest.mark.parametrize(
    ("resposta", "tipo"),
    [
        (
            httpx.Response(
                400, json={"StatusProcessamento": "REJEICAO", "Erros": [{"Codigo": "E2210", "Descricao": "x"}]}
            ),
            TipoErro.REJEICAO,
        ),
        (httpx.Response(404, text="<html>Not Found</html>"), TipoErro.REJEICAO),
        (httpx.Response(403, text="Forbidden"), TipoErro.AUTENTICACAO),
        (httpx.Response(200, json={"ok": True}), TipoErro.RESPOSTA_INVALIDA),
    ],
)
def test_distribuicao_adn_erros(certificado, resposta, tipo):
    c = SefinClient(Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(lambda r: resposta))
    with pytest.raises(SefinError) as exc:
        c.distribuicao_dfe(0)
    assert exc.value.tipo == tipo


def test_arquivo_xml_aceita_base64_sem_gzip(certificado):
    xml = b"<evento/>"
    corpo = {
        "StatusProcessamento": "DOCUMENTOS_LOCALIZADOS",
        "LoteDFe": [{"NSU": 7, "ArquivoXml": base64.b64encode(xml).decode()}],
    }
    c = SefinClient(
        Ambiente.HOMOLOGACAO, certificado, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=corpo))
    )
    assert c.distribuicao_dfe(0).documentos[0].xml == xml
    assert gzip.decompress(base64.b64decode(gzip_b64(xml))) == xml


# --- pela API ------------------------------------------------------------------------------------------


@pytest.fixture
def api(falsa):
    fabrica = lambda amb, cert: SefinClient(amb, cert, transport=httpx.MockTransport(falsa.handler))  # noqa: E731
    w = emissao_service.EmissionWorker(client_factory=fabrica, dormir=lambda s: None)
    emissao_service.definir_worker(w)
    eventos_service.definir_sincronizador(eventos_service.Sincronizador(dormir=lambda s: None))
    with TestClient(create_app(iniciar_worker=False)) as c:
        c.worker = w
        yield c


def _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes) -> list[dict]:
    api.post("/api/certificado", files={"arquivo": ("c.pfx", pfx_bytes)}, data={"senha": SENHA_PFX})
    dados = api.post("/api/onboarding/xmls", files=[("arquivos", ("n.xml", nfse_exemplo_bytes))]).json()
    api.post(
        "/api/onboarding/concluir",
        json={"fiscal": dados["fiscal"], "ambiente": "2", "serie": 1, "linhas": dados["linhas_sugeridas"]},
    )
    linhas = api.get("/api/tabela").json()["linhas"]
    linhas.append({"nome": "OUTRO CLIENTE", "documento": "529.982.247-25", "valor": "100", "descricao": "consultoria"})
    ids = [ln["id"] for ln in api.put("/api/tabela", json={"linhas": linhas}).json()["linhas"]]
    lote = api.post("/api/lotes", json={"linha_ids": ids}).json()
    api.worker.processar_lote(lote["id"])
    itens = sorted(api.get("/api/emissoes").json()["itens"], key=lambda e: e["id"])
    assert [e["status"] for e in itens] == ["autorizada", "autorizada"]
    assert itens[0]["chave_acesso"] != itens[1]["chave_acesso"]
    return itens


def test_cancelar_pela_api(api, falsa, pfx_bytes, nfse_exemplo_bytes, monkeypatch):
    e1, e2 = _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    api.get(f"/api/emissoes/{e1['id']}/pdf")  # PDF já gerado antes do cancelamento
    marcas = []
    original = eventos_service.gerar_danfse
    monkeypatch.setattr(eventos_service, "gerar_danfse", lambda xml, **kw: marcas.append(kw) or original(xml, **kw))

    r = api.post(f"/api/emissoes/{e1['id']}/cancelar", json={"c_motivo": "2", "x_motivo": "curto"})
    assert r.status_code == 400 and "pelo menos 15" in r.json()["detalhes"][0]
    assert not falsa.pedidos  # nada enviado

    r = api.post(
        f"/api/emissoes/{e1['id']}/cancelar",
        json={"c_motivo": "2", "x_motivo": "Serviço não foi prestado neste mês"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "cancelada" and d["pode_cancelar"] is False and d["aviso"] is None
    ev = d["eventos"][0]
    assert (ev["tipo"], ev["origem"], ev["descricao"]) == ("101101", "sistema", "Cancelamento de NFS-e")
    assert ev["motivo"] == "Serviço não prestado — Serviço não foi prestado neste mês"
    assert marcas == [{"cancelada": True, "substituida": False}]  # DANFSe refeito com a marca d'água
    assert b"<CNPJAutor>11444777000161</CNPJAutor>" in falsa.pedidos[0]
    assert b"<tpAmb>2</tpAmb>" in falsa.pedidos[0]

    x = api.get(f"/api/emissoes/{e1['id']}/eventos/{ev['id']}/xml")
    assert (
        x.status_code == 200
        and b"<evento" in x.content
        and "_evento-101101-001.xml" in x.headers["content-disposition"]
    )

    r = api.post(f"/api/emissoes/{e1['id']}/cancelar", json={"c_motivo": "1", "x_motivo": "de novo por engano aqui"})
    assert r.status_code == 400 and "já está cancelada" in r.json()["mensagem"]

    lista = api.get("/api/emissoes").json()
    assert lista["valor_autorizado"] == "100.00"  # nota cancelada sai do total
    assert api.get("/api/emissoes", params={"status": "cancelada"}).json()["total"] == 1

    z = api.post("/api/emissoes/zip", json={"ids": [e1["id"], e2["id"]], "conteudo": "xml"})
    nomes = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(nomes) == 3 and any(n.endswith("_evento-101101-001.xml") for n in nomes)


def test_cancelamento_rejeitado_mostra_codigo_e_dica(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    e1, _ = _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    falsa.rejeitar_evento[e1["chave_acesso"]] = (
        400,
        {"erro": [{"codigo": "E0822", "descricao": "Cancelamento de NFS-e fora do prazo"}]},
    )
    r = api.post(f"/api/emissoes/{e1['id']}/cancelar", json={"c_motivo": "1", "x_motivo": "Valor errado na nota"})
    assert r.status_code == 400
    corpo = r.json()
    assert corpo["sefin"]["mensagens"][0]["codigo"] == "E0822"
    assert "Análise Fiscal" in corpo["detalhes"][0]
    assert api.get(f"/api/emissoes/{e1['id']}").json()["status"] == "autorizada"


def test_cancelada_no_portal_aparece_na_sincronizacao(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    e1, e2 = _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    # No portal: a nota 2 foi cancelada e o tomador confirmou a nota 1; outra nota qualquer também.
    falsa.distribuir(
        evento_de(
            _pedido_portal(
                e2["chave_acesso"],
                "101101",
                {"xDesc": "Cancelamento de NFS-e", "cMotivo": "1", "xMotivo": "Cancelada pelo portal nacional"},
            )
        ),
        "EVENTO",
        e2["chave_acesso"],
        "CANCELAMENTO",
    )
    falsa.distribuir(
        evento_de(_pedido_portal(e1["chave_acesso"], "203202", {"xDesc": "Confirmação do Tomador"}, "11222333000181")),
        "EVENTO",
        e1["chave_acesso"],
        "CONFIRMACAO_TOMADOR",
    )
    falsa.distribuir(
        evento_de(
            _pedido_portal(
                "9" * 50,
                "101101",
                {"xDesc": "Cancelamento de NFS-e", "cMotivo": "9", "xMotivo": "Nota de outro sistema"},
            )
        ),
        "EVENTO",
        "9" * 50,
        "CANCELAMENTO",
    )
    sinc = eventos_service.sincronizador()
    falsa.lote_adn = sinc.TAMANHO_LOTE = 2  # força paginação
    sinc.executar()
    est = api.get("/api/sincronizacao").json()
    assert est["erro"] is None and est["concluida_em"] and not est["em_andamento"]
    assert (est["documentos"], est["eventos_novos"], est["notas_atualizadas"]) == (5, 2, 1)
    consultas = [c for c in falsa.chamadas if "/DFe/" in c]
    assert consultas == [f"GET /contribuintes/DFe/{n}" for n in (0, 2, 4)]

    d2 = api.get(f"/api/emissoes/{e2['id']}").json()
    assert d2["status"] == "cancelada"
    assert d2["eventos"][0]["origem"] == "ambiente_nacional"
    d1 = api.get(f"/api/emissoes/{e1['id']}").json()
    assert d1["status"] == "autorizada" and d1["eventos"][0]["descricao"] == "Confirmação do Tomador"

    # Nova rodada continua do último NSU e não duplica nada.
    falsa.chamadas.clear()
    sinc.executar()
    assert [c for c in falsa.chamadas if "/DFe/" in c] == ["GET /contribuintes/DFe/5"]
    assert len(api.get(f"/api/emissoes/{e2['id']}").json()["eventos"]) == 1

    # Pedido automático logo depois não roda de novo (intervalo mínimo).
    assert api.post("/api/sincronizacao").json()["motivo"] == "recente"
    assert api.post("/api/sincronizacao", params={"forcar": True}).json()["motivo"] == "recente"


def test_sincronizacao_em_segundo_plano(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    assert api.post("/api/sincronizacao").json()["motivo"] == "sem_notas"
    _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    r = api.post("/api/sincronizacao").json()
    assert r["motivo"] == "iniciada" and r["em_andamento"]
    eventos_service.sincronizador()._thread.join(5)
    est = api.get("/api/sincronizacao").json()
    assert not est["em_andamento"] and est["erro"] is None and est["documentos"] == 2


def test_sincronizacao_com_adn_fora_do_ar_registra_erro(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    original = falsa.handler
    falsa.handler = lambda req: httpx.Response(503) if "/contribuintes/" in req.url.path else original(req)
    eventos_service.sincronizador().executar()
    est = api.get("/api/sincronizacao").json()
    assert "HTTP 503" in est["erro"] and est["concluida_em"] is None


def test_atualizar_situacao_de_uma_nota(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    e1, _ = _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    r = api.post(f"/api/emissoes/{e1['id']}/situacao")
    assert r.status_code == 200 and r.json()["status"] == "autorizada" and r.json()["eventos_novos"] == 0
    falsa.distribuir(
        evento_de(
            _pedido_portal(
                e1["chave_acesso"],
                "105102",
                {"xDesc": "Cancelamento de NFS-e por Substituição", "cMotivo": "99", "chSubstituta": "8" * 50},
            )
        ),
        "EVENTO",
        e1["chave_acesso"],
        "CANCELAMENTO_POR_SUBSTITUICAO",
    )
    d = api.post(f"/api/emissoes/{e1['id']}/situacao").json()
    assert d["status"] == "substituida" and d["eventos_novos"] == 1 and d["pode_cancelar"] is False
    assert api.get(f"/api/emissoes/{e1['id']}/pdf").content.startswith(b"%PDF")


def test_cancelar_nota_ja_substituida_no_portal_atualiza_situacao(api, falsa, pfx_bytes, nfse_exemplo_bytes):
    e1, _ = _emitir_duas(api, pfx_bytes, nfse_exemplo_bytes)
    falsa.distribuir(
        evento_de(
            _pedido_portal(
                e1["chave_acesso"],
                "105102",
                {"xDesc": "Cancelamento de NFS-e por Substituição", "cMotivo": "01", "chSubstituta": "8" * 50},
            )
        ),
        "EVENTO",
        e1["chave_acesso"],
        "CANCELAMENTO_POR_SUBSTITUICAO",
    )
    falsa.rejeitar_evento[e1["chave_acesso"]] = (
        400,
        {"erro": [{"codigo": "E0840", "descricao": "Evento de cancelamento não permitido para a situação da NFS-e"}]},
    )
    r = api.post(f"/api/emissoes/{e1['id']}/cancelar", json={"c_motivo": "1", "x_motivo": "Valor errado na nota"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "substituida" and "já estava substituida" in r.json()["aviso"]
