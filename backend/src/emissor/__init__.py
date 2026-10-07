"""Emissor em Planilha — emissão de NFS-e Nacional em lote a partir de uma planilha."""


def main() -> None:
    """``uv run emissor`` — sobe o servidor web (API + frontend).

    Variáveis: EMISSOR_HOST (padrão 0.0.0.0), EMISSOR_PORT (8000), EMISSOR_DATA_DIR,
    EMISSOR_IPS_PERMITIDOS (ex.: "192.168.0.0/24"), EMISSOR_FONT_DIR.
    """
    import os

    import uvicorn

    from emissor.api.app import create_app
    from emissor.logging_setup import setup_logging

    setup_logging()
    uvicorn.run(
        create_app(),
        host=os.environ.get("EMISSOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("EMISSOR_PORT", "8000")),
        log_config=None,
        access_log=False,
    )
