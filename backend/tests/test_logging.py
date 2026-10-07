import json
import logging

from emissor.logging_setup import JsonFormatter, _ContextFilter, bind_context, get_context


def _formatar(msg, **extra):
    rec = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": msg, **extra})
    _ContextFilter().filter(rec)
    return json.loads(JsonFormatter().format(rec))


def test_contexto_entra_no_log_e_e_removido_ao_sair():
    with bind_context(lote_id="L1"):
        with bind_context(emissao_id="E7"):
            dado = _formatar("oi", status=201)
            assert dado["lote_id"] == "L1" and dado["emissao_id"] == "E7" and dado["status"] == 201
        assert get_context() == {"lote_id": "L1"}
    assert get_context() == {}
    assert "lote_id" not in _formatar("fora")


def test_excecao_serializada():
    try:
        raise ValueError("falhou")
    except ValueError:
        import sys

        dado = _formatar("erro", exc_info=sys.exc_info())
    assert dado["exc_type"] == "ValueError" and "falhou" in dado["exc"]
