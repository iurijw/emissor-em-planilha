"""Entrada dos conectores (``/api/v1/entrada/<tipo>/<caminho>``): o Asaas avisa um pagamento e o emissor faz a nota.

Rota pública (sem token da API): quem chama se identifica pelo caminho do conector e pelo token no cabeçalho
``asaas-access-token``. Ver ``emissor.services.conectores``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool

from emissor.services import conectores

router = APIRouter(prefix="/v1/entrada", include_in_schema=False)


@router.post("/{tipo}/{caminho}")
async def receber(tipo: str, caminho: str, request: Request) -> dict[str, Any]:
    tamanho = request.headers.get("content-length")
    if tamanho and tamanho.isdigit() and int(tamanho) > conectores.MAX_CORPO:
        raise conectores.ConectorError("Aviso grande demais.", 413)
    corpo = await request.body()
    token = request.headers.get("asaas-access-token")
    return await run_in_threadpool(conectores.receber, tipo, caminho, token, corpo)
