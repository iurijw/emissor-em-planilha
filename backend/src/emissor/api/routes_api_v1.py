"""API de integração (``/api/v1``): outros sistemas emitem, consultam e cancelam notas com um token da empresa.

É uma camada fina sobre os mesmos serviços da tela (nota avulsa, fila de emissão, cancelamento, cadastro de
clientes): as notas passam pela mesma conferência, numeração e proteção contra duplicidade. O que muda é a entrada
(token em ``Authorization: Bearer``, ver ``emissor.tokens_api``) e o formato das respostas, que é estável e
versionado. Documentação interativa em ``/api/v1/docs``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import func, select

from emissor import auth, tokens_api
from emissor.api import docs_v1 as doc
from emissor.api import routes_emissoes, routes_tabela, routes_tributacao
from emissor.api.deps import exigir_login
from emissor.api.routes_tributacao import AvulsaEntrada
from emissor.auth import Acesso
from emissor.db import Emissao, Lote, Tributacao, obter_config, sessao
from emissor.nfse.models import normalizar_documento
from emissor.services import certificado as cert_service
from emissor.services import eventos as eventos_service
from emissor.services import tributacao as trib_service
from emissor.services import webhooks
from emissor.services.emissao import LoteError, ReferenciaEmUso, criar_avulsas, verificar_prontidao

log = logging.getLogger(__name__)

MAX_NOTAS_LOTE = 500


def exigir_token(request: Request) -> Acesso:
    if getattr(request.state, "acesso", None) is None:
        raise HTTPException(
            401, {"mensagem": "Use um token de API no cabeçalho Authorization: Bearer emp_...", "login": True}
        )
    acesso = exigir_login(request)  # assinatura suspensa: só leitura, como na tela
    if acesso.token_id is None:
        raise HTTPException(
            401, {"mensagem": "Use um token de API no cabeçalho Authorization: Bearer emp_...", "login": True}
        )
    return acesso


def escopo(nome: str):
    def conferir(acesso: Annotated[Acesso, Depends(exigir_token)]) -> Acesso:
        if nome not in acesso.escopos:
            raise HTTPException(403, f"Este token não tem a permissão “{nome}”. Crie outro token com ela.")
        return acesso

    return conferir


Ler = Annotated[Acesso, Depends(escopo("notas:ler"))]
Emitir = Annotated[Acesso, Depends(escopo("notas:emitir"))]
Cancelar = Annotated[Acesso, Depends(escopo("notas:cancelar"))]
ClientesLer = Annotated[Acesso, Depends(escopo("clientes:ler"))]
ClientesEscrever = Annotated[Acesso, Depends(escopo("clientes:escrever"))]
Webhooks = Annotated[Acesso, Depends(escopo("webhooks"))]

router = APIRouter(prefix="/v1", dependencies=[Depends(exigir_token)])


def _ambiente_igual(acesso: Acesso) -> None:
    """O token só emite e cancela no ambiente dele: evita mandar para produção o que era teste (e o contrário)."""
    with sessao() as s:
        atual = obter_config(s).ambiente
    if atual != acesso.ambiente_token:
        token = tokens_api.NOME_AMBIENTE.get(acesso.ambiente_token or "", "?")
        empresa = tokens_api.NOME_AMBIENTE.get(atual, "?")
        raise HTTPException(
            409,
            {
                "mensagem": f"Este token é de {token}, mas a empresa está emitindo em {empresa}. "
                "Use o token do ambiente certo ou troque o ambiente nas configurações.",
                "ambiente_token": token,
                "ambiente_empresa": empresa,
            },
        )


def _emissao(acesso: Acesso, nota_id: int) -> Emissao:
    with sessao() as s:
        e = s.get(Emissao, nota_id)
    if e is None or e.ambiente != acesso.ambiente_token:
        raise HTTPException(404, "Nota não encontrada.")
    return e


# --- empresa e tributações ---------------------------------------------------------------------------------


@router.get(
    "/empresa",
    tags=["Empresa"],
    summary="Empresa do token, ambiente e se está pronta para emitir",
    description="Comece por aqui: confirma que o token funciona e diz o que falta para emitir (`problemas`).",
    responses=doc.respostas(200, doc.Empresa, doc.EMPRESA, "Empresa e token"),
)
def empresa(acesso: Annotated[Acesso, Depends(exigir_token)]) -> dict[str, Any]:
    with sessao() as s:
        cfg = obter_config(s)
        problemas = verificar_prontidao(s)
        fiscal = cfg.config_fiscal()
    try:
        info = cert_service.info()
    except Exception:
        info = None
    cert = (
        {"titular": info.titular, "valido_ate": info.valido_ate.isoformat(), "dias_para_vencer": info.dias_para_vencer}
        if info
        else None
    )
    return {
        "id": cfg.id,
        "cnpj": cfg.cnpj or (fiscal.cnpj if fiscal else None),
        "nome": cfg.nome or (info.titular if info else None),
        "ambiente_empresa": tokens_api.NOME_AMBIENTE.get(cfg.ambiente),
        "ambiente_token": tokens_api.NOME_AMBIENTE.get(acesso.ambiente_token or ""),
        "pronta_para_emitir": not problemas and cfg.ambiente == acesso.ambiente_token,
        "problemas": problemas,
        "certificado": cert,
        "token": {"nome": acesso.nome, "escopos": list(acesso.escopos)},
    }


@router.get(
    "/tributacoes",
    tags=["Empresa"],
    summary="Tributações predefinidas (use o id em tributacao_id)",
    description="Tributações cadastradas em *Configurações → Tributações*. Sem `tributacao_id`, a nota usa a "
    "tributação padrão da empresa (que não aparece nesta lista).",
    responses=doc.respostas(200, None, doc.TRIBUTACOES, "Lista de tributações"),
)
def tributacoes(_acesso: Annotated[Acesso, Depends(exigir_token)]) -> list[dict[str, Any]]:
    with sessao() as s:
        itens = list(s.exec(select(Tributacao).order_by(Tributacao.nome)))
        return [{"id": t.id, "nome": t.nome, "resumo": trib_service.resumo(t.dados())} for t in itens]


# --- notas ---------------------------------------------------------------------------------------------------


class NotaEntrada(AvulsaEntrada):
    """Os mesmos campos da nota avulsa da tela, mais a referência no sistema de quem integra."""

    referencia_externa: str | None = Field(
        default=None,
        max_length=100,
        description="Identificador da nota no seu sistema (ex.: id da cobrança), até 100 caracteres. Repetir o "
        "pedido com a mesma referência devolve a nota já criada em vez de emitir outra. Também aceito no cabeçalho "
        "`Idempotency-Key`.",
        examples=["cobranca-2026-11-0042"],
    )


class LoteEntrada(BaseModel):
    notas: list[NotaEntrada] = Field(
        ...,
        min_length=1,
        max_length=MAX_NOTAS_LOTE,
        description=f"De 1 a {MAX_NOTAS_LOTE} notas, no formato de `POST /notas`. Todas são conferidas antes: se "
        "uma tiver erro, nenhuma é emitida e a resposta diz o `indice` (a partir de 0) de cada nota com problema.",
    )


def _referencia(entrada: NotaEntrada, chave: str | None) -> str | None:
    ref = (entrada.referencia_externa or "").strip() or None
    chave = (chave or "").strip() or None
    if ref and chave and ref != chave:
        raise HTTPException(400, "Idempotency-Key e referencia_externa diferentes: envie só um, ou os dois iguais.")
    ref = ref or chave
    if ref and len(ref) > 100:
        raise HTTPException(400, "A referência externa deve ter até 100 caracteres.")
    return ref


def _notas(entradas: list[NotaEntrada]):
    notas, erros = [], []
    with sessao() as s:
        for i, ent in enumerate(entradas):
            try:
                notas.append(routes_tributacao._nota(s, ent))
            except HTTPException as exc:
                msg = exc.detail.get("mensagem") if isinstance(exc.detail, dict) else str(exc.detail)
                erros.append({"indice": i, "referencia_externa": ent.referencia_externa, "erros": [msg]})
    if erros:
        raise HTTPException(400, {"mensagem": f"{len(erros)} nota(s) com dados inválidos.", "detalhes": erros})
    return notas


def _criar(acesso: Acesso, entradas: list[NotaEntrada], referencias: list[str | None]) -> Lote:
    _ambiente_igual(acesso)
    notas = _notas(entradas)
    try:
        tokens_api.reservar_notas(acesso.token_id, len(notas))
    except tokens_api.TokenError as exc:
        raise HTTPException(exc.status, exc.mensagem) from exc
    try:
        lote = criar_avulsas(notas, referencias=referencias, origem=f"api:{acesso.token_id}")
    except LoteError:
        tokens_api.devolver_notas(acesso.token_id, len(notas))
        raise
    auth.auditar("lote_criado", f"API: lote {lote.id}, {lote.total} nota(s), ambiente {lote.ambiente}", acesso)
    return lote


def _notas_do_lote(lote_id: str) -> list[Emissao]:
    with sessao() as s:
        return list(s.exec(select(Emissao).where(Emissao.lote_id == lote_id).order_by(Emissao.id)))


@router.post(
    "/notas/validar",
    tags=["Notas"],
    summary="Confere a nota sem emitir (mesmos erros e avisos da tela)",
    description="Faz a mesma conferência de `POST /notas` (tomador, valor, variáveis da descrição, tributação, "
    "regras da prefeitura) sem reservar número nem enviar à Sefin. Útil para mostrar os erros no seu sistema antes "
    "de emitir. Responde sempre 200: veja `pode_emitir`.",
    dependencies=[Depends(escopo("notas:emitir"))],
    responses=doc.respostas(200, doc.Validacao, doc.VALIDACAO_OK, "Resultado da conferência", 403, 422)
    | {
        200: {
            "model": doc.Validacao,
            "description": "Resultado da conferência",
            "content": {
                "application/json": {
                    "examples": {
                        "ok": {"summary": "Pode emitir", "value": doc.VALIDACAO_OK},
                        "erros": {"summary": "Com erros", "value": doc.VALIDACAO_ERRO},
                    }
                }
            },
        }
    },
)
def validar_nota(entrada: Annotated[NotaEntrada, Body(openapi_examples=doc.PEDIDOS_NOTA)]) -> dict[str, Any]:
    r = routes_tributacao.validar_nota_avulsa(entrada)
    linha = r["linhas"][0]
    return {
        "pode_emitir": not r["problemas"] and not linha["erros"],
        "ambiente": tokens_api.NOME_AMBIENTE.get(r["ambiente"]),
        "problemas": r["problemas"],
        "erros": linha["erros"],
        "avisos": linha["avisos"] + [a.get("mensagem", a) if isinstance(a, dict) else a for a in r["avisos"]],
        "regras_prefeitura": r["regras_prefeitura"],
        "aliquotas_iss": r["aliquotas_iss"],
        "total": r["total"],
    }


@router.post(
    "/notas",
    tags=["Notas"],
    status_code=202,
    summary="Emite uma nota (entra na fila; acompanhe por GET /notas/{id} ou pelo webhook)",
    description="Confere a nota e a coloca na fila de emissão. A resposta (202) traz a nota `pendente`; ela é "
    "enviada à Sefin em segundos e passa a `autorizada` ou `rejeitada`. Com uma `referencia_externa` já usada, a "
    "resposta é 200 com a nota existente e `repetida: true` (nada é emitido de novo).",
    responses=doc.respostas(
        202, doc.NotaCriada, doc.NOTA_PENDENTE | {"repetida": False}, "Nota na fila", 400, 403, 409, 422, 429
    )
    | {
        200: {
            "description": "Referência já usada: a nota existente",
            "content": {"application/json": {"example": doc.NOTA_AUTORIZADA | {"repetida": True}}},
        }
    },
)
def emitir_nota(
    entrada: Annotated[NotaEntrada, Body(openapi_examples=doc.PEDIDOS_NOTA)],
    acesso: Emitir,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Alternativa a `referencia_externa` (se enviar os dois, têm de ser iguais).",
        ),
    ] = None,
):
    ref = _referencia(entrada, idempotency_key)
    try:
        lote = _criar(acesso, [entrada], [ref])
    except ReferenciaEmUso as exc:
        e = _emissao(acesso, next(iter(exc.existentes.values())))
        return JSONResponse(webhooks.nota_publica(e) | {"repetida": True}, status_code=200)
    e = _notas_do_lote(lote.id)[0]
    return webhooks.nota_publica(e) | {"repetida": False}


@router.post(
    "/lotes",
    tags=["Notas"],
    status_code=202,
    summary=f"Emite até {MAX_NOTAS_LOTE} notas num lote",
    description="As notas entram na fila juntas e são enviadas uma a uma, na ordem. Acompanhe por "
    "`GET /lotes/{id}` ou pelo evento `lote.concluido`. Se alguma `referencia_externa` já tem nota, o lote inteiro "
    "é recusado (409) com a lista; retire-as e envie de novo.",
    responses=doc.respostas(
        202,
        doc.Lote,
        doc.LOTE | {"contagem": {"pendente": 2}, "notas": [n | {"status": "pendente"} for n in doc.LOTE["notas"]]},
        "Lote na fila",
        400,
        403,
        409,
        422,
        429,
    ),
)
def emitir_lote(
    entrada: Annotated[
        LoteEntrada, Body(openapi_examples={"duas": {"summary": "Duas notas", "value": doc.PEDIDO_LOTE}})
    ],
    acesso: Emitir,
) -> dict[str, Any]:
    refs = [_referencia(n, None) for n in entrada.notas]
    try:
        lote = _criar(acesso, entrada.notas, refs)
    except ReferenciaEmUso as exc:
        raise HTTPException(
            409,
            {
                "mensagem": "Algumas referências externas já têm nota. Retire-as do lote (ou consulte as notas).",
                "detalhes": [{"referencia_externa": r, "nota_id": i} for r, i in exc.existentes.items()],
            },
        ) from exc
    with sessao() as s:
        lote = s.get(Lote, lote.id)
    return webhooks.lote_publico(lote, _notas_do_lote(lote.id))


@router.get(
    "/lotes/{lote_id}",
    tags=["Notas"],
    summary="Situação de um lote e das notas dele",
    description="`status` fica `em_andamento` até a última nota ter resposta. Para os detalhes de cada nota (número, "
    "erro), use `GET /notas?lote_id=...`.",
    responses=doc.respostas(200, doc.Lote, doc.LOTE, "Lote", 403, 404),
)
def ver_lote(
    lote_id: Annotated[str, Path(description="Id do lote (`lote_id` da nota).", examples=["77159deae426"])], acesso: Ler
) -> dict[str, Any]:
    with sessao() as s:
        lote = s.get(Lote, lote_id)
    if lote is None or lote.ambiente != acesso.ambiente_token:
        raise HTTPException(404, "Lote não encontrado.")
    return webhooks.lote_publico(lote, _notas_do_lote(lote_id))


@router.get(
    "/notas",
    tags=["Notas"],
    summary="Lista as notas do ambiente do token (mais novas primeiro)",
    description="Todos os filtros são opcionais e se somam. As datas são as de criação da nota no emissor.",
    responses=doc.respostas(
        200,
        doc.ListaNotas,
        {"itens": [doc.NOTA_AUTORIZADA, doc.NOTA_REJEITADA], "total": 2, "pagina": 1, "por_pagina": 50},
        "Uma página de notas",
        403,
    ),
)
def listar_notas(
    acesso: Ler,
    status: Annotated[
        str | None, Query(description="Uma ou mais situações separadas por vírgula.", examples=["autorizada,cancelada"])
    ] = None,
    de: Annotated[date | None, Query(description="A partir deste dia (AAAA-MM-DD).", examples=["2026-11-01"])] = None,
    ate: Annotated[date | None, Query(description="Até este dia, inclusive.", examples=["2026-11-30"])] = None,
    tomador: Annotated[
        str | None, Query(description="CNPJ/CPF do tomador, com ou sem pontuação.", examples=["11.222.333/0001-81"])
    ] = None,
    referencia_externa: Annotated[
        str | None, Query(description="A referência que o seu sistema mandou.", examples=["cobranca-2026-11-0042"])
    ] = None,
    lote_id: Annotated[str | None, Query(description="Só as notas deste lote.")] = None,
    pagina: Annotated[int, Query(description="Página, a partir de 1.")] = 1,
    por_pagina: Annotated[int, Query(description="Notas por página (até 100).")] = 50,
) -> dict[str, Any]:
    filtros = [Emissao.ambiente == acesso.ambiente_token]
    if status:
        filtros.append(Emissao.status.in_(status.split(",")))  # type: ignore[union-attr]
    if de:
        filtros.append(Emissao.criado_em >= datetime.combine(de, datetime.min.time()))
    if ate:
        filtros.append(Emissao.criado_em < datetime.combine(ate + timedelta(days=1), datetime.min.time()))
    if tomador:
        filtros.append(Emissao.tomador_documento == (normalizar_documento(tomador) or tomador))
    if referencia_externa:
        filtros.append(Emissao.referencia_externa == referencia_externa)
    if lote_id:
        filtros.append(Emissao.lote_id == lote_id)
    por_pagina = max(1, min(por_pagina, 100))
    pagina = max(pagina, 1)
    with sessao() as s:
        total = s.exec(select(func.count()).select_from(Emissao).where(*filtros)).one()
        itens = s.exec(
            select(Emissao)
            .where(*filtros)
            .order_by(Emissao.id.desc())  # type: ignore[union-attr]
            .offset((pagina - 1) * por_pagina)
            .limit(por_pagina)
        ).all()
    return {
        "itens": [webhooks.nota_publica(e) for e in itens],
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
    }


NotaId = Annotated[int, Path(description="`id` da nota (resposta de `POST /notas`).", examples=[128])]


@router.get(
    "/notas/{nota_id}",
    tags=["Notas"],
    summary="Uma nota",
    description="Consulte até a nota sair de `pendente`/`processando` (ou use um webhook). Recusada, o motivo vem "
    "em `erro.mensagens`, com o código da Sefin e a dica de correção.",
    responses=doc.respostas(200, doc.Nota, doc.NOTA_AUTORIZADA, "A nota", 403, 404)
    | {
        200: {
            "model": doc.Nota,
            "description": "A nota",
            "content": {
                "application/json": {
                    "examples": {
                        "autorizada": {"summary": "Autorizada", "value": doc.NOTA_AUTORIZADA},
                        "rejeitada": {"summary": "Recusada pela Sefin", "value": doc.NOTA_REJEITADA},
                    }
                }
            },
        }
    },
)
def ver_nota(nota_id: NotaId, acesso: Ler) -> dict[str, Any]:
    return webhooks.nota_publica(_emissao(acesso, nota_id))


@router.get(
    "/notas/{nota_id}/xml",
    tags=["Notas"],
    summary="XML da NFS-e autorizada",
    description="O XML assinado pela Sefin (o documento fiscal que vale). 404 enquanto a nota não foi autorizada.",
    responses={200: {"description": "XML da NFS-e", "content": {"application/xml": {}}}}
    | {e: doc.ERROS[e] for e in (401, 403, 404)},
)
def xml_nota(nota_id: NotaId, acesso: Ler) -> Response:
    return routes_emissoes.baixar_xml(_emissao(acesso, nota_id).id)


@router.get(
    "/notas/{nota_id}/pdf",
    tags=["Notas"],
    summary="DANFSe (PDF) da NFS-e",
    description="Gerado na hora a partir do XML, com a marca d'água de cancelada ou substituída quando for o caso.",
    responses={200: {"description": "PDF do DANFSe", "content": {"application/pdf": {}}}}
    | {e: doc.ERROS[e] for e in (401, 403, 404)},
)
def pdf_nota(nota_id: NotaId, acesso: Ler) -> Response:
    return routes_emissoes.baixar_pdf(_emissao(acesso, nota_id).id)


class CancelamentoEntrada(BaseModel):
    c_motivo: str = Field(
        ..., description="Motivo: `1` = erro na emissão, `2` = serviço não prestado, `9` = outros.", examples=["1"]
    )
    x_motivo: str = Field(
        ...,
        min_length=15,
        max_length=255,
        description="Justificativa que vai para a Sefin (15 a 255 caracteres).",
        examples=["Valor digitado errado na emissão"],
    )


@router.post(
    "/notas/{nota_id}/cancelamento",
    tags=["Notas"],
    summary="Cancela uma nota autorizada (evento 101101)",
    description="Envia o pedido de cancelamento à Sefin e espera a resposta (alguns segundos). Só notas "
    "`autorizada`. O prazo e o valor máximo para cancelar dependem do município: fora deles a Sefin recusa (400, com "
    "o código em `sefin`) e o cancelamento precisa ser pedido no portal nacional.",
    responses=doc.respostas(
        200,
        doc.NotaCancelada,
        doc.NOTA_AUTORIZADA | {"status": "cancelada", "aviso": None},
        "Nota cancelada",
        400,
        403,
        404,
        409,
        422,
    ),
)
def cancelar_nota(
    nota_id: NotaId,
    entrada: Annotated[
        CancelamentoEntrada,
        Body(openapi_examples={"erro": {"summary": "Erro na emissão", "value": doc.PEDIDO_CANCELAMENTO}}),
    ],
    acesso: Cancelar,
) -> dict[str, Any]:
    e = _emissao(acesso, nota_id)
    _ambiente_igual(acesso)
    try:
        r = eventos_service.cancelar(e.id, entrada.c_motivo, entrada.x_motivo)
    except Exception as err:
        auth.auditar("cancelamento_recusado", f"API: emissão {e.id}: {err}", acesso)
        raise
    auth.auditar(
        "nota_cancelada",
        f"API: emissão {e.id}, NFS-e {r.emissao.numero_nfse or '-'} ({r.emissao.chave_acesso}), "
        f"motivo {entrada.c_motivo}",
        acesso,
    )
    return webhooks.nota_publica(r.emissao) | {"aviso": r.aviso}


# --- clientes ------------------------------------------------------------------------------------------------


Documento = Annotated[str, Path(description="CNPJ ou CPF, com ou sem pontuação.", examples=["11222333000181"])]


@router.get(
    "/clientes/{documento}",
    tags=["Clientes"],
    summary="Cadastro de um cliente (tomador)",
    description="O endereço daqui é o que vai na nota. 404 se o cliente ainda não foi cadastrado.",
    responses=doc.respostas(200, doc.Cliente, doc.CLIENTE, "Cliente", 403, 404),
)
def ver_cliente(documento: Documento, _acesso: ClientesLer) -> dict[str, Any]:
    return routes_tabela.obter_cliente(documento)


@router.put(
    "/clientes/{documento}",
    tags=["Clientes"],
    summary="Cadastra ou altera um cliente (endereço da nota)",
    description="Substitui o cadastro inteiro: envie todos os campos (os que faltarem ficam vazios). Use antes de "
    "emitir para um CPF (que não tem consulta na Receita) ou para corrigir o endereço. Endereço completo = CEP, "
    "logradouro, número, bairro e município.",
    responses=doc.respostas(200, doc.Cliente, doc.CLIENTE, "Cliente salvo", 400, 403, 422),
)
def salvar_cliente(
    documento: Documento,
    entrada: Annotated[
        routes_tabela.ClienteEntrada,
        Body(openapi_examples={"empresa": {"summary": "Empresa com endereço", "value": doc.PEDIDO_CLIENTE}}),
    ],
    acesso: ClientesEscrever,
) -> dict[str, Any]:
    r = routes_tabela.salvar_cliente(documento, entrada)
    auth.auditar("cliente_salvo", f"API: {r['documento']}", acesso)
    return r


# --- webhooks -------------------------------------------------------------------------------------------------


class WebhookEntrada(BaseModel):
    url: str = Field(
        ...,
        description="Endereço que recebe os eventos (POST). Na hospedagem, só `https://` e fora de rede interna.",
        examples=["https://erp.exemplo.com.br/webhooks/nfse"],
    )
    eventos: list[str] = Field(
        ...,
        description="Eventos que quer receber: "
        + "; ".join(f"`{k}` ({v.lower()})" for k, v in webhooks.EVENTOS.items()),
        examples=[["nota.autorizada", "nota.rejeitada"]],
    )
    descricao: str = Field("", description="Nome para reconhecer o webhook na tela (até 200 caracteres).")


@router.get(
    "/webhooks",
    tags=["Webhooks"],
    summary="Webhooks da empresa no ambiente do token",
    responses=doc.respostas(200, None, [doc.WEBHOOK], "Lista de webhooks", 403),
)
def listar_webhooks(acesso: Webhooks) -> list[dict[str, Any]]:
    amb = tokens_api.NOME_AMBIENTE.get(acesso.ambiente_token or "")
    return [w for w in webhooks.listar(acesso.empresa_id) if w["ambiente"] == amb]


@router.post(
    "/webhooks",
    tags=["Webhooks"],
    status_code=201,
    summary="Cadastra um webhook (o segredo da assinatura só aparece nesta resposta)",
    description="O webhook recebe os eventos do ambiente do token. Guarde o `segredo`: ele confere a assinatura "
    "(`X-Emissor-Assinatura`) de cada entrega. O formato do corpo está em **Webhooks**, no fim da página.",
    responses=doc.respostas(
        201,
        doc.WebhookCriado,
        doc.WEBHOOK | {"segredo": "whsec_exemploNaoUseEsteSegredo0123456789abcd"},
        "Webhook criado",
        400,
        403,
        422,
    ),
)
def criar_webhook(
    entrada: Annotated[
        WebhookEntrada,
        Body(openapi_examples={"erp": {"summary": "Autorizadas e recusadas", "value": doc.PEDIDO_WEBHOOK}}),
    ],
    acesso: Webhooks,
) -> dict[str, Any]:
    segredo, w = webhooks.criar(
        acesso.empresa_id, entrada.url, entrada.eventos, acesso.ambiente_token, entrada.descricao, acesso.email
    )
    auth.auditar("webhook_criado", f"API: {w['url']} ({' '.join(w['eventos'])})", acesso)
    return w | {"segredo": segredo}


@router.delete(
    "/webhooks/{webhook_id}",
    tags=["Webhooks"],
    summary="Remove um webhook",
    description="Para de enviar eventos e apaga o histórico de entregas dele.",
    responses=doc.respostas(200, None, {"ok": True}, "Removido", 403, 404),
)
def remover_webhook(
    webhook_id: Annotated[int, Path(description="`id` do webhook.", examples=[7])], acesso: Webhooks
) -> dict[str, bool]:
    amb = tokens_api.NOME_AMBIENTE.get(acesso.ambiente_token or "")
    if not any(w["id"] == webhook_id and w["ambiente"] == amb for w in webhooks.listar(acesso.empresa_id)):
        raise HTTPException(404, "Webhook não encontrado.")
    w = webhooks.remover(acesso.empresa_id, webhook_id)
    auth.auditar("webhook_removido", f"API: {w.url}", acesso)
    return {"ok": True}
