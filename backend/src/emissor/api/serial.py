"""Conversão dos registros do banco para JSON da API."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime
from typing import Any

from emissor.db import Cliente, Emissao, LinhaTabela, Lote
from emissor.nfse import codes
from emissor.nfse.models import ConfigFiscal


def dt(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def fiscal_dict(cfg: ConfigFiscal | None) -> dict[str, Any] | None:
    if cfg is None:
        return None
    d = asdict(cfg)
    d["p_tot_trib_sn"] = str(cfg.p_tot_trib_sn) if cfg.p_tot_trib_sn is not None else None
    return d


CAMPOS_FISCAIS = [f.name for f in fields(ConfigFiscal)]


def emissao_resumo(e: Emissao) -> dict[str, Any]:
    erro = e.erro()
    return {
        "id": e.id,
        "lote_id": e.lote_id,
        "linha_id": e.linha_id,
        "ambiente": e.ambiente,
        "status": e.status,
        "tomador_documento": e.tomador_documento,
        "tomador_nome": e.tomador_nome,
        "valor": e.valor,
        "descricao": e.descricao,
        "numero_nfse": e.numero_nfse,
        "chave_acesso": e.chave_acesso,
        "serie": e.serie,
        "n_dps": e.n_dps,
        "dh_emissao": dt(e.dh_emissao),
        "tem_xml": bool(e.xml_path),
        "tem_pdf": bool(e.xml_path),  # PDF é gerado sob demanda a partir do XML se faltar
        "erro": erro,
        "alertas": e.alertas(),
        "recuperada": e.recuperada,
        "atualizado_em": dt(e.atualizado_em),
    }


def emissao_detalhe(e: Emissao) -> dict[str, Any]:
    d = emissao_resumo(e)
    d.update(
        {
            "id_dps": e.id_dps,
            "dh_processamento": e.dh_processamento,
            "tentativas": e.tentativas,
            "criado_em": dt(e.criado_em),
        }
    )
    return d


def linha_dict(ln: LinhaTabela, ultima: Emissao | None) -> dict[str, Any]:
    return {
        "id": ln.id,
        "ordem": ln.ordem,
        "nome": ln.nome,
        "documento": ln.documento,
        "valor": ln.valor,
        "descricao": ln.descricao,
        "ultima_emissao": emissao_resumo(ultima) if ultima else None,
    }


def cliente_dict(c: Cliente) -> dict[str, Any]:
    return {
        "documento": c.documento,
        "nome": c.nome,
        "c_mun": c.c_mun,
        "municipio": codes.municipio_uf(c.c_mun),
        "cep": c.cep,
        "logradouro": c.logradouro,
        "numero": c.numero,
        "complemento": c.complemento,
        "bairro": c.bairro,
        "fone": c.fone,
        "email": c.email,
        "inscricao_municipal": c.inscricao_municipal,
        "situacao_cadastral": c.situacao_cadastral,
        "origem": c.origem,
        "endereco_completo": c.endereco_completo,
        "atualizado_em": dt(c.atualizado_em),
    }


def lote_dict(lote: Lote, emissoes: list[Emissao] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": lote.id,
        "ambiente": lote.ambiente,
        "status": lote.status,
        "total": lote.total,
        "motivo_interrupcao": lote.motivo_interrupcao,
        "criado_em": dt(lote.criado_em),
        "finalizado_em": dt(lote.finalizado_em),
    }
    if emissoes is not None:
        contagem: dict[str, int] = {}
        for e in emissoes:
            contagem[e.status] = contagem.get(e.status, 0) + 1
        d["contagem"] = contagem
        d["emissoes"] = [emissao_resumo(e) for e in emissoes]
    return d
