"""Copiar nota emitida e histórico da nota avulsa (services/historico.py)."""

from __future__ import annotations

import json

from emissor.db import Emissao, Tributacao, sessao
from emissor.services import historico

from .test_api import api, sefin  # noqa: F401 (fixtures)


def _emissao(s, **kw) -> Emissao:
    e = Emissao(
        ambiente="2",
        status=kw.pop("status", "autorizada"),
        tomador_documento=kw.pop("doc", "11222333000181"),
        tomador_nome=kw.pop("nome", "Cliente"),
        valor=kw.pop("valor", "100.00"),
        descricao=kw.pop("descricao", "Serviço"),
        **kw,
    )
    s.add(e)
    s.commit()
    s.refresh(e)
    return e


def test_copia_e_historico(api):  # noqa: F811 (banco e empresa prontos)
    dados = {"c_trib_nac": "171901", "tp_ret_issqn": "1", "p_aliq_iss": "2.00"}
    with sessao() as s:
        t = Tributacao(nome="Retido", dados_json=json.dumps(dados))
        s.add(t)
        s.commit()
        s.refresh(t)
        padrao = _emissao(s, descricao="Honorários de {mes}", valor="10.00")
        # Mesma tributação, só com outra alíquota do ISS (muda todo mês): continua sendo a "Retido".
        pre = _emissao(
            s,
            descricao="Honorários  de {mes}",
            tributacao_nome="Retido",
            tributacao_json=json.dumps(dados | {"p_aliq_iss": "3.10"}),
            nota_json=json.dumps({"x_ped": "PED-1", "ref_nfse": ["X" * 50]}),
        )
        # A tributação mudou depois da nota: volta como "só para esta nota", com os campos da nota.
        mudou = _emissao(
            s,
            doc="52998224725",
            descricao="Consultoria",
            tributacao_nome="Retido",
            tributacao_json=json.dumps(dados | {"tp_ret_issqn": "2"}),
        )
        _emissao(s, status="rejeitada", descricao="Recusada")  # recusada não entra no histórico

        assert historico.copia(s, padrao)["tributacao"] == {"tipo": "padrao", "nome": "Tributação padrão"}
        c = historico.copia(s, pre)
        assert c["tributacao"]["tipo"] == "predefinida" and c["tributacao"]["tributacao_id"] == t.id
        assert c["nota"] == {"x_ped": "PED-1"}  # a NFS-e referenciada não se repete
        assert c["valor"] == "100.00" and c["documento"] == "11222333000181"
        m = historico.copia(s, mudou)["tributacao"]
        assert m["tipo"] == "propria" and m["dados"]["tp_ret_issqn"] == "2" and m["nome"] == "Retido"

        h = historico.historico(s, "52.998.224/725")
        textos = [d["texto"] for d in h["descricoes"]]
        # Mais recente primeiro; espaços a mais não criam outra descrição; recusada fica de fora.
        assert textos == ["Consultoria", "Honorários  de {mes}"]
        assert h["descricoes"][1]["vezes"] == 2
        assert [d["do_tomador"] for d in h["descricoes"]] == [True, False]
        assert [x["tipo"] for x in h["tributacoes"]] == ["propria", "predefinida", "padrao"]
        assert h["tributacoes"][0]["do_tomador"] is True


def test_excluir_notas_nao_emitidas(api):  # noqa: F811
    from emissor.db import LinhaTabela, Lote

    with sessao() as s:
        s.add(Lote(id="L1", ambiente="2", status="concluido", total=4))
        rej = _emissao(s, status="rejeitada", lote_id="L1")
        sem_envio = _emissao(s, status="erro", lote_id="L1")
        incerta = _emissao(s, status="erro", id_dps="DPS" + "1" * 42, lote_id="L1")  # pode ter sido gerada
        # "Verificar na Sefin" confirmou que a DPS não gerou nota: pode sair da lista
        conferida = _emissao(s, status="erro", id_dps="DPS" + "2" * 42, lote_id="L1")
        conferida.erro_json = json.dumps({"tipo": "nao_encontrada", "resumo": "", "mensagens": []})
        s.add(conferida)
        aut = _emissao(s, lote_id="L1")
        ln = LinhaTabela(ordem=0, nome="Cliente", documento="11222333000181", valor="100.00", ultima_emissao_id=rej.id)
        s.add(ln)
        s.commit()
        ids = [rej.id, sem_envio.id, incerta.id, aut.id, conferida.id]
        ln_id = ln.id
    r = api.post("/api/emissoes/excluir", json={"ids": ids})
    assert r.status_code == 200 and r.json() == {"excluidas": 3, "ignoradas": 2}, r.text
    with sessao() as s:
        assert s.get(Emissao, conferida.id) is None
        assert s.get(Emissao, incerta.id) is not None and s.get(Emissao, aut.id) is not None
        assert s.get(Emissao, rej.id) is None and s.get(Emissao, sem_envio.id) is None
        assert s.get(LinhaTabela, ln_id).ultima_emissao_id is None
        assert s.get(Lote, "L1") is not None  # ainda tem notas
    assert api.post("/api/emissoes/excluir", json={"ids": []}).status_code == 422
