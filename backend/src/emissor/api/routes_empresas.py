"""Empresas da instalação, certificados guardados, procuração e cadastro de clientes compartilhado."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from emissor import auth, empresas
from emissor.api.deps import AdminAtual, AdminEmpresa, UsuarioAtual
from emissor.db import obter_config, obter_instancia, sessao
from emissor.nfse.documentos import formatar_documento
from emissor.services import certificado as cert_service
from emissor.services import empresas as emp_service

router = APIRouter()

MAX_PFX = 1_000_000


def _erro(e: emp_service.EmpresaError) -> HTTPException:
    return HTTPException(e.status, e.mensagem)


@router.get("/empresas")
def listar(acesso: UsuarioAtual) -> dict[str, Any]:
    perm = empresas.permitidas(acesso.usuario_id, acesso.admin)
    lim = empresas.limite()
    with sessao() as s:
        todas = emp_service.ativas(s)
        lista = [emp_service.empresa_dict(s, c, perm[c.id]) for c in todas if c.id in perm]
        compartilhar = bool(obter_instancia(s).compartilhar_clientes)
        total = len(todas)
    return {
        "empresas": lista,
        "atual": acesso.empresa_id,
        "limite": lim,
        # Na hospedagem a opção fica desligada até o painel liberar (limite 1).
        "disponivel": lim is None or lim > 1,
        "pode_adicionar": acesso.admin and (lim is None or total < lim),
        "compartilhar_clientes": compartilhar,
        "desativadas": emp_service.desativadas() if acesso.admin else [],
    }


class NovaEmpresa(BaseModel):
    certificado_id: int
    cnpj: str | None = None  # vazio = CNPJ do certificado (certificado da empresa); outro = por procuração
    nome: str | None = None
    # Usuários (não administradores) que passam a acessar a empresa: [{usuario_id, papel}]
    usuarios: list[dict[str, Any]] = []


@router.post("/empresas")
def criar(entrada: NovaEmpresa, admin: AdminAtual) -> dict[str, Any]:
    try:
        eid = emp_service.criar(entrada.certificado_id, entrada.cnpj, entrada.nome, entrada.usuarios)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e
    with sessao() as s:
        d = emp_service.empresa_dict(s, obter_config(s, eid), "admin")
    modo = "por procuração" if d["acesso"] == "procuracao" else "certificado da empresa"
    auth.auditar("empresa_criada", f"{formatar_documento(d['cnpj'] or '')} ({modo})", admin)
    return d


class AlterarEmpresa(BaseModel):
    nome: str | None = None


@router.put("/empresas/{empresa_id}")
def alterar(empresa_id: int, entrada: AlterarEmpresa, admin: AdminEmpresa) -> dict[str, Any]:
    _exigir_mesma(empresa_id, admin)
    emp_service.alterar(empresa_id, entrada.nome)
    auth.auditar("empresa_alterada", f"nome={entrada.nome or ''}", admin)
    with sessao() as s:
        return emp_service.empresa_dict(s, obter_config(s, empresa_id), admin.papel_empresa)


def _exigir_mesma(empresa_id: int, acesso) -> None:
    """Rotas de uma empresa valem para a empresa da requisição (a permissão foi conferida no middleware); o
    administrador da instalação pode em qualquer uma."""
    if acesso.empresa_id != empresa_id and not acesso.admin:
        raise HTTPException(404, "Empresa não encontrada ou sem acesso para o seu usuário.")


class UsarCertificado(BaseModel):
    certificado_id: int


@router.post("/empresas/{empresa_id}/usar-certificado")
def usar_certificado(empresa_id: int, entrada: UsarCertificado, admin: AdminAtual) -> dict[str, Any]:
    """Troca o certificado por outro já guardado (só o administrador da instalação vê os certificados de todos)."""
    try:
        acesso = emp_service.usar_certificado(empresa_id, entrada.certificado_id)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e
    with empresas.usar(empresa_id):
        auth.auditar(
            "empresa_certificado", "por procuração" if acesso == "procuracao" else "certificado da empresa", admin
        )
    with sessao() as s:
        return emp_service.empresa_dict(s, obter_config(s, empresa_id), "admin")


@router.post("/empresas/{empresa_id}/desativar")
def desativar(empresa_id: int, admin: AdminAtual) -> dict[str, bool]:
    try:
        emp_service.desativar(empresa_id)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e
    with empresas.usar(empresa_id):
        auth.auditar("empresa_desativada", None, admin)
    return {"ok": True}


@router.post("/empresas/{empresa_id}/desistir")
def desistir(empresa_id: int, admin: AdminAtual) -> dict[str, bool]:
    """Desfaz a adição de uma empresa que ainda não passou pela configuração inicial."""
    with sessao() as s:
        cfg = obter_config(s, empresa_id)
        doc = formatar_documento(emp_service._cnpj(cfg) or "")
    try:
        emp_service.desistir(empresa_id)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e
    auth.auditar("empresa_descartada", doc, admin)
    return {"ok": True}


@router.post("/empresas/{empresa_id}/reativar")
def reativar(empresa_id: int, admin: AdminAtual) -> dict[str, bool]:
    try:
        emp_service.reativar(empresa_id)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e
    with empresas.usar(empresa_id):
        auth.auditar("empresa_reativada", None, admin)
    return {"ok": True}


@router.post("/empresas/{empresa_id}/conferir-procuracao")
def conferir_procuracao(empresa_id: int, admin: AdminEmpresa) -> dict[str, Any]:
    _exigir_mesma(empresa_id, admin)
    try:
        return emp_service.conferir_procuracao(empresa_id)
    except emp_service.EmpresaError as e:
        raise _erro(e) from e


# --- certificados guardados ------------------------------------------------------------------------------


@router.get("/certificados")
def certificados(_admin: AdminAtual) -> list[dict[str, Any]]:
    return cert_service.listar()


@router.post("/certificados")
async def guardar_certificado(
    admin: AdminAtual, arquivo: UploadFile = File(...), senha: str = Form(...)
) -> dict[str, Any]:
    """Guarda um certificado para criar uma empresa com ele (o dela ou o de um procurador)."""
    pfx = await arquivo.read()
    if len(pfx) > MAX_PFX:
        raise HTTPException(400, "Arquivo grande demais para um certificado A1.")
    cert_service.apagar_sem_uso(minutos=60)  # envios de antes que não viraram empresa
    cid, info = cert_service.guardar_novo(pfx, senha)
    auth.auditar("certificado_guardado", f"{info.titular} (válido até {info.valido_ate:%d/%m/%Y})", admin)
    return {"id": cid, **info.as_dict()}


@router.delete("/certificados/{certificado_id}")
def apagar_certificado(certificado_id: int, admin: AdminAtual) -> dict[str, bool]:
    if not cert_service.apagar_sem_uso(certificado_id):
        raise HTTPException(409, "Este certificado está em uso por alguma empresa (ou já foi apagado).")
    auth.auditar("certificado_apagado", str(certificado_id), admin)
    return {"ok": True}


# --- cadastro de clientes compartilhado ------------------------------------------------------------------


@router.get("/instancia/clientes")
def ver_compartilhar(_admin: AdminAtual) -> dict[str, Any]:
    with sessao() as s:
        compartilhar = bool(obter_instancia(s).compartilhar_clientes)
    return {"compartilhar": compartilhar, **emp_service.previa_compartilhar()}


class Compartilhar(BaseModel):
    compartilhar: bool
    empresa_destino: int | None = None


@router.put("/instancia/clientes")
def definir_compartilhar(entrada: Compartilhar, admin: AdminAtual) -> dict[str, Any]:
    r = emp_service.definir_compartilhar(entrada.compartilhar, entrada.empresa_destino)
    auth.auditar(
        "clientes_compartilhados" if entrada.compartilhar else "clientes_separados",
        f"{r['clientes']} cliente(s)",
        admin,
    )
    return {"compartilhar": entrada.compartilhar, **r}


# --- empresas de cada usuário -------------------------------------------------------------------------------


class EmpresasUsuario(BaseModel):
    empresas: list[dict[str, Any]]  # [{empresa_id, papel}]


@router.put("/usuarios/{usuario_id}/empresas")
def empresas_usuario(usuario_id: int, entrada: EmpresasUsuario, admin: AdminAtual) -> dict[str, Any]:
    return auth.definir_empresas(admin, usuario_id, entrada.empresas)
