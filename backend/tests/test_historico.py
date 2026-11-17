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
