"""Diagnóstico somente leitura: certificado + conexão mTLS com a Sefin.

Não emite nada. Consulta uma DPS inexistente: HTTP 404 significa que o mTLS
funcionou e o certificado foi aceito.

    uv run python -m emissor.nfse.diagnostico --pfx caminho.pfx [--producao]
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from emissor.logging_setup import setup_logging
from emissor.nfse.certificate import Certificado, CertificadoError
from emissor.nfse.client import SefinClient
from emissor.nfse.constants import Ambiente
from emissor.nfse.dps_builder import id_dps
from emissor.nfse.errors import SefinError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pfx", required=True, type=Path)
    ap.add_argument("--producao", action="store_true", help="usar Produção (padrão: Produção Restrita)")
    ap.add_argument(
        "--cmun", default="5300108", help="código IBGE do município emissor (só compõe o Id da DPS fictícia)"
    )
    args = ap.parse_args(argv)
    setup_logging()

    senha = getpass.getpass("Senha do certificado: ")
    try:
        cert = Certificado(args.pfx.read_bytes(), senha)
    except (OSError, CertificadoError) as exc:
        print(f"ERRO certificado: {exc}")
        return 2
    info = cert.info
    print(f"Titular : {info.titular}\nCNPJ    : {info.cnpj}\nEmissor : {info.emissor}")
    print(f"Validade: {info.valido_de:%d/%m/%Y} a {info.valido_ate:%d/%m/%Y} ({info.dias_para_vencer} dias)")
    if info.vencido:
        print("ERRO: certificado vencido.")
        return 2

    amb = Ambiente.PRODUCAO if args.producao else Ambiente.HOMOLOGACAO
    ident = id_dps(args.cmun, info.cnpj or "0", 49999, 999_999_999_999_999)
    print(f"\nTestando mTLS em {amb.descricao}: GET /dps/{ident}")
    try:
        with SefinClient(amb, cert) as client:
            chave = client.consultar_dps(ident)
    except SefinError as exc:
        print(f"FALHA ({exc.tipo.value}): {exc}")
        for m in exc.mensagens:
            if m.dica:
                print(f"  dica: {m.dica}")
        return 1
    print(
        "OK: conexão e certificado aceitos pela Sefin"
        + (f" (DPS existe: {chave})" if chave else " (DPS de teste inexistente, como esperado).")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
