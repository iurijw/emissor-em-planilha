"""Logging estruturado (JSON Lines) com IDs de correlação.

- ``data/logs/emissor.jsonl``: um evento JSON por linha, rotação diária, 30 dias.
- Console: formato legível.
- ``bind_context(lote_id=..., emissao_id=...)`` injeta IDs em todos os logs daquele
  contexto (thread/tarefa), para rastrear uma emissão de ponta a ponta.

Nunca logar senha do certificado, chave privada ou conteúdo do PFX.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from emissor import config

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("log_context", default=None)

# Atributos padrão de LogRecord que não devem ir para o campo "extra".
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


def get_context() -> dict[str, Any]:
    return dict(_context.get() or {})


@contextmanager
def bind_context(**values: Any) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **{k: v for k, v in values.items() if v is not None}})
    try:
        yield
    finally:
        _context.reset(token)


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.ctx = _context.get() or {}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data.update(getattr(record, "ctx", {}) or {})
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key != "ctx":
                data[key] = value
        if record.exc_info:
            data["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            data["exc"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(data, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = getattr(record, "ctx", {}) or {}
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items())
        base = f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if ctx_str:
            base += f"  [{ctx_str}]"
        if record.exc_info:
            base += "\n" + "".join(traceback.format_exception(*record.exc_info))
        return base


_configured = False


def setup_logging(level: int = logging.INFO, console: bool = True) -> None:
    global _configured
    if _configured:
        return
    config.ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        config.LOG_DIR / "emissor.jsonl", when="midnight", backupCount=30, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(_ContextFilter())
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(level)
        stream.setFormatter(ConsoleFormatter())
        stream.addFilter(_ContextFilter())
        root.addHandler(stream)

    for noisy in ("httpx", "httpcore", "uvicorn.access", "multipart", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
