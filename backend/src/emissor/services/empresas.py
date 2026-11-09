"""Cadastro das empresas da instalação: criar (com o A1 dela ou por procuração), renomear, trocar o certificado,
desativar, conferir a procuração no ADN e compartilhar o cadastro de clientes."""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session, select

from emissor import empresas
from emissor.db import (
    CertificadoDB,
    Cliente,
    Configuracao,
    Emissao,
    Lote,
    UsuarioEmpresa,
    agora,
    obter_config,
    obter_instancia,
    sessao,
)
from emissor.nfse.client import SefinClient
from emissor.nfse.documentos import cnpj_valido, formatar_documento
from emissor.nfse.errors import SefinError
from emissor.nfse.models import normalizar_documento
from emissor.services import certificado as cert_service

log = logging.getLogger(__name__)

ACESSOS = ("certificado", "procuracao")


class EmpresaError(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _cnpj(cfg: Configuracao) -> str | None:
    return cfg.cnpj or (cfg.config_fiscal().cnpj if cfg.config_fiscal() else None)


def ativas(s: Session) -> list[Configuracao]:
    return list(s.exec(select(Configuracao).where(Configuracao.ativa.is_not(False)).order_by(Configuracao.id)))


def empresa_dict(s: Session, cfg: Configuracao, papel: str | None = None) -> dict[str, Any]:
    cert = s.get(CertificadoDB, cfg.certificado_id) if cfg.certificado_id else None
    cnpj = _cnpj(cfg)
    dias = (cert.valido_ate - agora()).days if cert else None
    return {
        "id": cfg.id,
        "nome": cfg.nome
        or (cert.titular if cert and cfg.acesso != "procuracao" else None)
        or formatar_documento(cnpj or ""),
        "nome_definido": cfg.nome,
        "cnpj": cnpj,
        "acesso": cfg.acesso or "certificado",
        "ambiente": cfg.ambiente,
        "onboarding_concluido": cfg.onboarding_concluido,
        "papel": papel,
        "certificado": (
            {
                "id": cert.id,
                "titular": cert.titular,
                "cnpj": cert.cnpj,
                "cpf": cert.cpf,
                "valido_ate": cert.valido_ate.isoformat(),
                "dias_para_vencer": dias,
                "vencido": dias is not None and dias < 0,
            }
            if cert
            else None
        ),
    }


def _conferir_limite(s: Session) -> None:
    lim = empresas.limite()
    if lim is not None and len(ativas(s)) >= lim:
        if lim == 1:
            raise EmpresaError(
                "Várias empresas na mesma instalação ainda não estão disponíveis na versão hospedada.", 403
            )
        raise EmpresaError(f"O limite desta instalação é de {lim} empresas.", 403)


def criar(certificado_id: int, cnpj: str | None, nome: str | None, usuarios: list[dict] | None = None) -> int:
    """Cria uma empresa nova com um certificado já guardado.

    Sem CNPJ (ou com o mesmo CNPJ do certificado): certificado da empresa. Com outro CNPJ: por procuração — o dono
    do certificado (e-CNPJ ou e-CPF) precisa ter procuração eletrônica da empresa para a NFS-e (conferível depois
    pelo botão "Conferir procuração", que consulta o ADN).
    """
    with sessao() as s:
        _conferir_limite(s)
        cert = s.get(CertificadoDB, certificado_id)
        if cert is None:
            raise EmpresaError("Certificado não encontrado. Envie o certificado de novo.", 404)
        doc = normalizar_documento(cnpj) if cnpj else cert.cnpj
        if not doc:
            raise EmpresaError("O certificado não tem CNPJ (é de pessoa física): informe o CNPJ da empresa.")
        if len(doc) != 14 or not cnpj_valido(doc):
            raise EmpresaError("CNPJ da empresa inválido.")
        for outra in s.exec(select(Configuracao)):
            if _cnpj(outra) == doc:
                if outra.ativa is False:
                    raise EmpresaError(
                        f"O CNPJ {formatar_documento(doc)} já foi cadastrado e está desativado. Reative a empresa.", 409
                    )
                raise EmpresaError(f"O CNPJ {formatar_documento(doc)} já está cadastrado.", 409)
        acesso = "certificado" if doc == cert.cnpj else "procuracao"
        cfg = Configuracao(
            nome=(nome or "").strip()[:80] or None,
            acesso=acesso,
            certificado_id=cert.id,
            ativa=True,
            criado_em=agora(),
            onboarding_concluido=False,
        )
        cfg.cnpj = doc
        s.add(cfg)
        s.flush()
        for it in usuarios or []:
            s.add(
                UsuarioEmpresa(usuario_id=int(it["usuario_id"]), empresa_id=cfg.id, papel=it.get("papel") or "operador")
            )
        s.commit()
        eid = cfg.id
    empresas.limpar_cache()
    log.info("empresa criada", extra={"empresa_id": eid, "acesso": acesso})
    return eid


def alterar(empresa_id: int, nome: str | None) -> None:
    with sessao() as s:
        cfg = obter_config(s, empresa_id)
        cfg.nome = (nome or "").strip()[:80] or None
        cfg.atualizado_em = agora()
        s.add(cfg)
        s.commit()


def usar_certificado(empresa_id: int, certificado_id: int) -> str:
    """Troca o certificado da empresa por outro já guardado (ex.: passar a emitir por procuração)."""
    with sessao() as s:
        cfg = obter_config(s, empresa_id)
        cert = s.get(CertificadoDB, certificado_id)
        if cert is None:
            raise EmpresaError("Certificado não encontrado.", 404)
        _sem_lote_em_andamento(s, empresa_id)
        anterior = cfg.certificado_id
        cfg.certificado_id = cert.id
        cfg.acesso = "certificado" if cert.cnpj and cert.cnpj == _cnpj(cfg) else "procuracao"
        s.add(cfg)
        s.commit()
        acesso = cfg.acesso
    if anterior and anterior != certificado_id:
        cert_service.apagar_sem_uso(anterior)
    cert_service._limpar_cache()
    return acesso


def _sem_lote_em_andamento(s: Session, empresa_id: int) -> None:
    q = select(Lote).where(Lote.status == "em_andamento", Lote.empresa_id == empresa_id)
    if s.exec(q.execution_options(todas_empresas=True)).first():
        raise EmpresaError("Há notas desta empresa sendo emitidas agora. Espere o lote terminar.", 409)


def desativar(empresa_id: int) -> None:
    """Desativa a empresa: some do seletor e deixa de emitir; as notas continuam guardadas (e voltam ao reativar).

    O certificado dela é apagado se nenhuma outra empresa o usa (a chave privada não fica sem necessidade).
    """
    with sessao() as s:
        cfg = obter_config(s, empresa_id)
        if len(ativas(s)) <= 1:
            raise EmpresaError("Esta é a única empresa ativa.")
        _sem_lote_em_andamento(s, empresa_id)
        cert_id = cfg.certificado_id
        cfg.ativa = False
        cfg.certificado_id = None
        s.add(cfg)
        s.commit()
    if cert_id:
        cert_service.apagar_sem_uso(cert_id)
    empresas.limpar_cache()


def reativar(empresa_id: int) -> None:
    with sessao() as s:
        _conferir_limite(s)
        cfg = obter_config(s, empresa_id)
        cfg.ativa = True
        s.add(cfg)
        s.commit()
    empresas.limpar_cache()


def desativadas() -> list[dict[str, Any]]:
    with sessao() as s:
        return [
            {"id": c.id, "nome": c.nome or formatar_documento(_cnpj(c) or ""), "cnpj": _cnpj(c)}
            for c in s.exec(select(Configuracao).where(Configuracao.ativa.is_(False)).order_by(Configuracao.id))
        ]


def conferir_procuracao(empresa_id: int, client_factory=SefinClient) -> dict[str, Any]:
    """Pergunta ao ADN pelos documentos da empresa com o certificado dela (só leitura).

    Sem procuração o ADN recusa com E2245; com ela, responde (com ou sem documentos). Não confere a procuração para
    emitir em si (só a Sefin confere, na emissão), mas a mesma procuração eletrônica da NFS-e vale para os dois.
    """
    with empresas.usar(empresa_id):
        with sessao() as s:
            cfg = obter_config(s)
            cnpj = _cnpj(cfg)
        cert = cert_service.carregar()
        if cert is None or not cnpj:
            raise EmpresaError("A empresa não tem certificado ou CNPJ.")
        from emissor.nfse.constants import Ambiente

        amb = Ambiente(cfg.ambiente)
        try:
            with client_factory(amb, cert) as client:
                lote = client.distribuicao_dfe(0, cnpj_consulta=cnpj)
        except SefinError as err:
            codigos = {(m.codigo or "").upper() for m in err.mensagens}
            if "E2245" in codigos:
                return {
                    "ok": False,
                    "ambiente": amb.descricao,
                    "mensagem": (
                        f"O ADN recusou: o certificado de {cert.info.titular} não tem procuração do CNPJ "
                        f"{formatar_documento(cnpj)} (E2245). Cadastre a procuração eletrônica no e-CAC (serviço da "
                        "NFS-e) ou peça o vínculo no cadastro do CNPJ, e confira de novo."
                    ),
                    "erro": err.as_dict(),
                }
            return {"ok": False, "ambiente": amb.descricao, "mensagem": err.resumo, "erro": err.as_dict()}
    return {
        "ok": True,
        "ambiente": amb.descricao,
        "documentos": len(lote.documentos),
        "mensagem": f"O Ambiente Nacional aceitou o certificado de {cert.info.titular} para o CNPJ "
        f"{formatar_documento(cnpj)} ({amb.descricao}).",
    }


# --- clientes compartilhados ----------------------------------------------------------------------------------


def _mesclar(destino: Cliente, outro: Cliente) -> None:
    """Campos vazios do destino são completados pelo outro (a mesma regra da importação de XMLs)."""
    for campo in Cliente.model_fields:
        if campo in ("documento", "empresa_id", "atualizado_em"):
            continue
        if getattr(destino, campo) in (None, "") and getattr(outro, campo) not in (None, ""):
            setattr(destino, campo, getattr(outro, campo))


def previa_compartilhar() -> dict[str, int]:
    with sessao() as s:
        todos = list(s.exec(select(Cliente).execution_options(todas_empresas=True)))
    docs = [c.documento for c in todos if c.empresa_id != empresas.CLIENTES_COMPARTILHADOS]
    return {"clientes": len(set(docs)), "repetidos": len(docs) - len(set(docs))}


def definir_compartilhar(compartilhar: bool, empresa_destino: int | None = None) -> dict[str, int]:
    """Liga ou desliga o cadastro único de clientes.

    Ligar junta os cadastros de todas as empresas (mesmo CNPJ/CPF vira um cliente: fica o atualizado por último,
    com os campos vazios completados pelos outros). Desligar dá a cada empresa uma cópia dos clientes para quem ela
    já emitiu; os demais ficam com ``empresa_destino`` (padrão: a primeira empresa). Nada é apagado.
    """
    resumo = {"clientes": 0}
    with sessao() as s:
        inst = obter_instancia(s)
        if bool(inst.compartilhar_clientes) == compartilhar:
            return resumo
        todos = list(s.exec(select(Cliente).execution_options(todas_empresas=True)))
        if compartilhar:
            por_doc: dict[str, list[Cliente]] = {}
            for c in todos:
                por_doc.setdefault(c.documento, []).append(c)
            for lista in por_doc.values():
                lista.sort(key=lambda c: c.atualizado_em or agora(), reverse=True)
                dados = lista[0].model_dump()
                novo = Cliente(**{**dados, "empresa_id": empresas.CLIENTES_COMPARTILHADOS})
                for outro in lista[1:]:
                    _mesclar(novo, outro)
                for c in lista:
                    s.delete(c)
                s.flush()
                s.add(novo)
                resumo["clientes"] += 1
        else:
            ids = [c.id for c in ativas(s)]
            destino = empresa_destino if empresa_destino in ids else (ids[0] if ids else 1)
            emitidos: dict[str, set[int]] = {}
            q = select(Emissao.tomador_documento, Emissao.empresa_id).distinct().execution_options(todas_empresas=True)
            for doc, eid in s.exec(q):
                emitidos.setdefault(doc, set()).add(eid)
            for c in todos:
                if c.empresa_id != empresas.CLIENTES_COMPARTILHADOS:
                    continue
                donos = (emitidos.get(c.documento) or set()) & set(ids) or {destino}
                dados = c.model_dump()
                s.delete(c)
                s.flush()
                for eid in sorted(donos):
                    s.add(Cliente(**{**dados, "empresa_id": eid}))
                resumo["clientes"] += 1
        inst.compartilhar_clientes = compartilhar
        inst.atualizado_em = agora()
        s.add(inst)
        s.commit()
    empresas.limpar_cache()
    log.info("cadastro de clientes compartilhado" if compartilhar else "cadastro de clientes separado", extra=resumo)
    return resumo
