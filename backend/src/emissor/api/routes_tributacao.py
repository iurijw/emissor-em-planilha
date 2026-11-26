"""Tributações predefinidas, abas da tabela e nota avulsa."""

from __future__ import annotations

import json
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import func, select

from emissor import auth
from emissor.api.deps import AdminEmpresa, UsuarioAtual
from emissor.api.routes_emissoes import obter_lote
from emissor.db import Aba, Cliente, Emissao, LinhaTabela, Tributacao, agora, obter_config, sessao
from emissor.nfse.models import DadosNota, aplicar_tributacao, normalizar_documento, tributacao_de
from emissor.services import historico as historico_service
from emissor.services import parametros, regras_prefeitura
from emissor.services import tabela as tabela_service
from emissor.services import tributacao as trib_service
from emissor.services.emissao import (
    NotaAvulsa,
    aliquotas_iss,
    criar_avulsa,
    linha_avulsa,
    validar_avulsa,
    verificar_prontidao,
)

log = logging.getLogger(__name__)
router = APIRouter()


# --- tributações predefinidas ----------------------------------------------------------------------


def _tributacao_dict(t: Tributacao, abas: list[Aba]) -> dict[str, Any]:
    dados = t.dados()
    return {
        "id": t.id,
        "nome": t.nome,
        "dados": dados,
        "resumo": trib_service.resumo(dados),
        "abas": [a.nome for a in abas if a.tributacao_id == t.id],
    }


@router.get("/tributacoes")
def listar_tributacoes() -> dict[str, Any]:
    """Tributações predefinidas e os padrões das configurações (ponto de partida de uma tributação nova)."""
    with sessao() as s:
        fiscal = obter_config(s).config_fiscal()
        nome_padrao = trib_service.nome_padrao(s)
        abas = trib_service.garantir_abas(s)
        itens = list(s.exec(select(Tributacao).order_by(Tributacao.nome)))
    padrao = tributacao_de(fiscal) if fiscal else None
    return {
        "padrao": padrao,
        "padrao_nome": nome_padrao,
        "padrao_resumo": trib_service.resumo(padrao) if padrao else None,
        # O regime decide quais alíquotas a tela mostra (ex.: a do ISS retido só para ISS apurado pelo Simples).
        "regime": {"op_simp_nac": fiscal.op_simp_nac, "reg_ap_trib_sn": fiscal.reg_ap_trib_sn} if fiscal else None,
        "itens": [_tributacao_dict(t, abas) for t in itens],
    }


class TributacaoEntrada(BaseModel):
    nome: str
    dados: dict[str, Any]


def _nome_tributacao(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not nome:
        raise HTTPException(400, "Dê um nome à tributação.")
    if len(nome) > 60:
        raise HTTPException(400, "Nome da tributação com mais de 60 caracteres.")
    return nome


@router.post("/tributacoes")
def criar_tributacao(entrada: TributacaoEntrada, admin: AdminEmpresa) -> dict[str, Any]:
    nome = _nome_tributacao(entrada.nome)
    with sessao() as s:
        dados = trib_service.validar_completa(entrada.dados, trib_service.config_base(s))
        if s.exec(select(Tributacao).where(Tributacao.nome == nome)).first() or nome == trib_service.nome_padrao(s):
            raise HTTPException(400, f"Já existe uma tributação chamada “{nome}”.")
        t = Tributacao(nome=nome, dados_json=trib_service.dumps(dados))
        s.add(t)
        s.commit()
        s.refresh(t)
        abas = trib_service.garantir_abas(s)
    auth.auditar("tributacao_criada", f"{nome}: {trib_service.resumo(dados)}", admin)
    return _tributacao_dict(t, abas)


class NomePadraoEntrada(BaseModel):
    nome: str


@router.put("/tributacoes/padrao")
def renomear_padrao(entrada: NomePadraoEntrada, admin: AdminEmpresa) -> dict[str, str]:
    """Nome da tributação padrão (os campos dela continuam nas configurações, ``PUT /api/config``)."""
    nome = _nome_tributacao(entrada.nome)
    with sessao() as s:
        if s.exec(select(Tributacao).where(Tributacao.nome == nome)).first():
            raise HTTPException(400, f"Já existe uma tributação chamada “{nome}”.")
        cfg = obter_config(s)
        anterior = cfg.tributacao_padrao_nome or trib_service.NOME_PADRAO
        cfg.tributacao_padrao_nome = None if nome == trib_service.NOME_PADRAO else nome
        s.add(cfg)
        s.commit()
    if nome != anterior:
        auth.auditar("tributacao_padrao_renomeada", f"{anterior} → {nome}", admin)
    return {"nome": nome}


@router.put("/tributacoes/{tributacao_id}")
def alterar_tributacao(tributacao_id: int, entrada: TributacaoEntrada, admin: AdminEmpresa) -> dict[str, Any]:
    nome = _nome_tributacao(entrada.nome)
    with sessao() as s:
        t = s.get(Tributacao, tributacao_id)
        if t is None:
            raise HTTPException(404, "Tributação não encontrada.")
        dados = trib_service.validar_completa(entrada.dados, trib_service.config_base(s))
        outra = s.exec(select(Tributacao).where(Tributacao.nome == nome, Tributacao.id != tributacao_id)).first()
        if outra or nome == trib_service.nome_padrao(s):
            raise HTTPException(400, f"Já existe uma tributação chamada “{nome}”.")
        t.nome = nome
        t.dados_json = trib_service.dumps(dados)
        t.atualizado_em = agora()
        s.add(t)
        s.commit()
        abas = trib_service.garantir_abas(s)
    auth.auditar("tributacao_alterada", f"{nome}: {trib_service.resumo(dados)}", admin)
    return _tributacao_dict(t, abas)


class AliquotaIssEntrada(BaseModel):
    tributacao_id: int | None = None  # None = padrões das configurações
    p_aliq_iss: str


@router.post("/tributacoes/aliquota-iss")
def alterar_aliquota_iss(entrada: AliquotaIssEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    """Alíquota do ISS retido de uma tributação (ou dos padrões), pelo aviso da emissão.

    É o único campo de tributação que o operador pode mudar: a alíquota efetiva do Simples muda todo mês.
    """
    hoje = agora().date().isoformat()
    with sessao() as s:
        base = trib_service.config_base(s)
        if entrada.tributacao_id is not None:
            t = s.get(Tributacao, entrada.tributacao_id)
            if t is None:
                raise HTTPException(404, "Tributação não encontrada. Recarregue a página.")
            dados = t.dados()
            nome = t.nome
        else:
            t, dados, nome = None, tributacao_de(base), "padrão"
        anterior = dados.get("p_aliq_iss")
        dados = trib_service.validar_completa(
            {**dados, "p_aliq_iss": entrada.p_aliq_iss, "p_aliq_iss_atualizada_em": hoje}, base
        )
        if not dados.get("p_aliq_iss"):
            raise HTTPException(400, "Informe a alíquota do ISS retido.")
        if t is not None:
            t.dados_json = trib_service.dumps(dados)
            t.atualizado_em = agora()
            s.add(t)
        else:
            cfg = obter_config(s)
            cfg.set_config_fiscal(aplicar_tributacao(base, dados))
            cfg.atualizado_em = agora()
            s.add(cfg)
        s.commit()
    auth.auditar("aliquota_iss_alterada", f"tributação {nome}: {anterior or '-'}% → {dados['p_aliq_iss']}%", acesso)
    return {"tributacao_id": entrada.tributacao_id, "p_aliq_iss": dados["p_aliq_iss"], "atualizada_em": hoje}


@router.delete("/tributacoes/{tributacao_id}")
def excluir_tributacao(tributacao_id: int, admin: AdminEmpresa) -> dict[str, bool]:
    with sessao() as s:
        t = s.get(Tributacao, tributacao_id)
        if t is None:
            return {"ok": True}
        usando = [a.nome for a in trib_service.garantir_abas(s) if a.tributacao_id == tributacao_id]
        if usando:
            raise HTTPException(
                400,
                {
                    "mensagem": "Esta tributação está em uso. Troque a tributação destas abas antes de excluir.",
                    "detalhes": [f"Aba “{n}”" for n in usando],
                },
            )
        s.delete(t)
        s.commit()
    auth.auditar("tributacao_excluida", t.nome, admin)
    return {"ok": True}


# --- abas ----------------------------------------------------------------------------------------------


def _aba_dict(a: Aba, linhas: int, tributacoes: dict[int, Tributacao], nomes: dict[str, str]) -> dict[str, Any]:
    t = tributacoes.get(a.tributacao_id) if a.tributacao_id is not None else None
    return {
        "id": a.id,
        "nome": a.nome,
        "ordem": a.ordem,
        "tributacao_id": a.tributacao_id,
        "tributacao_nome": t.nome if t else None,
        "tributacao_excluida": a.tributacao_id is not None and t is None,
        "clientes": [{"documento": d, "nome": nomes.get(d, "")} for d in a.clientes()],
        "linhas": linhas,
    }


@router.get("/abas")
def listar_abas() -> list[dict[str, Any]]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        contagem = dict(
            s.exec(select(LinhaTabela.aba_id, func.count()).group_by(LinhaTabela.aba_id)).all()  # type: ignore[arg-type]
        )
        tributacoes = {t.id: t for t in s.exec(select(Tributacao))}
        docs = {d for a in abas for d in a.clientes()}
        nomes = (
            {c.documento: c.nome for c in s.exec(select(Cliente).where(Cliente.documento.in_(docs)))} if docs else {}
        )
    return [_aba_dict(a, contagem.get(a.id, 0), tributacoes, nomes) for a in abas]


class AbaEntrada(BaseModel):
    nome: str | None = None
    tributacao_id: int | None = None
    clientes: list[str] | None = None
    ordem: int | None = None


def _validar_aba(s, entrada: AbaEntrada, aba: Aba) -> list[str]:
    """Aplica a entrada na aba; devolve o que mudou (para a auditoria)."""
    mudou = []
    if entrada.nome is not None:
        nome = " ".join(entrada.nome.split())
        if not nome:
            raise HTTPException(400, "Dê um nome à aba.")
        if len(nome) > 40:
            raise HTTPException(400, "Nome da aba com mais de 40 caracteres.")
        if s.exec(select(Aba).where(Aba.nome == nome, Aba.id != aba.id)).first():
            raise HTTPException(400, f"Já existe uma aba chamada “{nome}”.")
        if nome != aba.nome:
            mudou.append(f"nome “{nome}”")
        aba.nome = nome
    # tributacao_id ausente do corpo = não mexe; null explícito = padrões das configurações.
    if "tributacao_id" in entrada.model_fields_set and entrada.tributacao_id != aba.tributacao_id:
        t = s.get(Tributacao, entrada.tributacao_id) if entrada.tributacao_id is not None else None
        if entrada.tributacao_id is not None and t is None:
            raise HTTPException(400, "Tributação não encontrada.")
        aba.tributacao_id = entrada.tributacao_id
        mudou.append(f"tributação “{t.nome}”" if t else "tributação padrão")
    if entrada.clientes is not None:
        docs = []
        for d in entrada.clientes:
            n = normalizar_documento(d) or ""
            if len(n) not in (11, 14):
                raise HTTPException(400, f"CNPJ/CPF inválido na lista de clientes da aba: {d!r}.")
            if n not in docs:
                docs.append(n)
        if docs != aba.clientes():
            mudou.append(f"{len(docs)} cliente(s) exclusivo(s)" if docs else "sem clientes exclusivos")
        aba.clientes_json = json.dumps(docs)
    if entrada.ordem is not None:
        aba.ordem = entrada.ordem
    return mudou


@router.post("/abas")
def criar_aba(entrada: AbaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        aba = Aba(nome="", ordem=max((a.ordem for a in abas), default=0) + 1)
        if entrada.nome is None:
            raise HTTPException(400, "Dê um nome à aba.")
        mudou = _validar_aba(s, entrada, aba)
        s.add(aba)
        s.commit()
        s.refresh(aba)
    auth.auditar("aba_criada", f"“{aba.nome}”: " + ", ".join(mudou), acesso)
    return next(a for a in listar_abas() if a["id"] == aba.id)


@router.put("/abas/{aba_id}")
def alterar_aba(aba_id: int, entrada: AbaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        aba = s.get(Aba, aba_id)
        if aba is None:
            raise HTTPException(404, "Aba não encontrada.")
        mudou = _validar_aba(s, entrada, aba)
        s.add(aba)
        s.commit()
    if mudou:
        auth.auditar("aba_alterada", f"“{aba.nome}”: " + ", ".join(mudou), acesso)
    return next(a for a in listar_abas() if a["id"] == aba_id)


@router.delete("/abas/{aba_id}")
def excluir_aba(aba_id: int, acesso: UsuarioAtual) -> dict[str, bool]:
    with sessao() as s:
        abas = trib_service.garantir_abas(s)
        aba = next((a for a in abas if a.id == aba_id), None)
        if aba is None:
            return {"ok": True}
        if len(abas) == 1:
            raise HTTPException(400, "A tabela precisa de pelo menos uma aba.")
        linhas = list(s.exec(select(LinhaTabela).where(LinhaTabela.aba_id == aba_id)))
        ids = [ln.id for ln in linhas]
        if (
            ids
            and s.exec(
                select(Emissao).where(Emissao.linha_id.in_(ids), Emissao.status.in_(["pendente", "processando"]))
            ).first()
        ):
            raise HTTPException(400, "Há notas desta aba sendo emitidas agora. Espere a emissão terminar.")
        for ln in linhas:
            s.delete(ln)
        s.delete(aba)
        s.commit()
    auth.auditar("aba_excluida", f"“{aba.nome}” ({len(linhas)} linha(s))", acesso)
    return {"ok": True}


# --- nota avulsa -----------------------------------------------------------------------------------------


class AvulsaEntrada(BaseModel):
    """Uma nota: tomador, valor, descrição e, se precisar, a tributação e os dados que valem só para ela.

    As descrições abaixo aparecem na documentação da API (``/api/v1/docs``) e na da tela (``/docs``).
    """

    nome: str = Field(
        "",
        description="Nome ou razão social do tomador. Pode ficar vazio se o CNPJ/CPF já está no cadastro de "
        "clientes (vale o nome do cadastro) ou se o CNPJ é encontrado na Receita.",
        examples=["Cliente Exemplo Ltda"],
    )
    documento: str = Field(
        "",
        description="CNPJ ou CPF do tomador, com ou sem pontuação (CNPJ alfanumérico aceito). O endereço da nota vem "
        "do cadastro de clientes; CNPJ sem cadastro é buscado na Receita na hora. Deixe vazio só com "
        "`tomador_exterior`.",
        examples=["11.222.333/0001-81"],
    )
    valor: str | float | None = Field(
        "",
        description='Valor do serviço em R$. Aceita número (`1500.5`) ou texto (`"1500,50"`, `"1.500,50"`, '
        '`"1500.50"`). Não use vírgula como separador de milhar: `"1,500.00"` é lido como R$ 1,50.',
        examples=["1500,00"],
    )
    descricao: str = Field(
        "",
        description="Descrição do serviço (vai na nota como está). Aceita as variáveis da tabela, trocadas pela data "
        "da emissão: `{mes}`, `{mes_numero}`, `{ano}`, `{competencia}`, `{mes_anterior}`, `{mes_anterior_numero}`, "
        "`{ano_mes_anterior}`, `{competencia_anterior}` e `{data}`. Texto entre chaves que não seja variável "
        "recusa a nota.",
        examples=["Honorários contábeis de {mes_anterior}/{ano_mes_anterior}"],
    )
    tributacao_id: int | None = Field(
        None,
        description="Id de uma tributação predefinida (lista em `GET /tributacoes`). Vazio = tributação padrão da "
        "empresa. Não envie junto com `tributacao`.",
        examples=[3],
    )
    tributacao: dict[str, Any] | None = Field(
        None,
        description="Tributação só desta nota, com os campos de *Configurações → Tributações* (ex.: `c_trib_nac`, "
        "`c_nbs`, `tp_ret_issqn`, `p_ret_irrf`). Os campos que faltarem vêm da tributação padrão. Prefira cadastrar "
        "a tributação na tela e usar `tributacao_id`.",
        examples=[{"c_trib_nac": "171901", "tp_ret_issqn": "2", "p_aliq_iss": "2.00"}],
    )
    # Só desta nota: informações complementares e descontos (valores em R$, como o valor)
    x_inf_comp: str | None = Field(
        None, description="Informações complementares impressas na nota (até 2.000 caracteres, sem quebra de linha)."
    )
    x_ped: str | None = Field(None, description="Número do pedido ou da ordem de compra do tomador.")
    itens_pedido: list[str] | None = Field(None, description="Itens do pedido (até 99).")
    v_desc_incond: str | float | None = Field(
        None, description="Desconto incondicionado em R$: reduz a base de cálculo do ISSQN e dos tributos."
    )
    v_desc_cond: str | float | None = Field(
        None, description="Desconto condicionado em R$ (ex.: pagamento antecipado): só reduz o valor líquido."
    )
    # Tomador no exterior (no lugar de documento/nome): nif ou c_nao_nif, nome, c_pais, c_end_post, cidade, estado,
    # logradouro, numero, complemento, bairro, email, fone
    tomador_exterior: dict[str, str | None] | None = Field(
        None,
        description="Tomador no exterior, no lugar de `documento`: `nif` (ou `c_nao_nif`: 1 = dispensado, 2 = não exigido), "
        "`nome`, `c_pais` (ISO, ex. `US`), `c_end_post`, `cidade`, `estado`, `logradouro`, `numero`, `complemento`, "
        "`bairro`, `email`, `fone`. Não entra no cadastro de clientes.",
    )
    v_serv_moeda: str | float | None = Field(
        None, description="Comércio exterior: valor do serviço na moeda estrangeira da tributação."
    )
    n_di: str | None = Field(None, description="Comércio exterior: número da declaração de importação.")
    n_re: str | None = Field(None, description="Comércio exterior: número do registro de exportação.")
    # Dedução/redução da base do ISSQN: percentual (texto "10,5") ou valor em R$
    ajuste_bc_p: str | float | None = Field(None, description="Dedução/redução da base do ISSQN em percentual.")
    ajuste_bc_v: str | float | None = Field(None, description="Dedução/redução da base do ISSQN em R$.")
    # Obra (construção civil) e atividade/evento (item 12): ver DadosNota
    obra: dict[str, str | None] | None = Field(
        None,
        description="Obra de construção civil (obrigatória nesses serviços): `insc_imob_fisc` e um de `c_obra` "
        "(CNO/CEI), `c_cib` ou o endereço (`cep`, `logradouro`, `numero`, `complemento`, `bairro`).",
    )
    evento: dict[str, str | None] | None = Field(
        None,
        description="Atividade ou evento (serviços do item 12): `nome`, `dt_ini`, `dt_fim` (AAAA-MM-DD) e "
        "`id_atv_evt` ou o endereço (como o da obra).",
    )
    # Intermediário e destinatário (IBS/CBS) diferente do tomador; chaves de NFS-e referenciadas (tpOper 2/3)
    intermediario: dict[str, str | None] | None = Field(
        None,
        description="Intermediário do serviço: `documento`, `nome`, `im`, `c_mun`, `cep`, `logradouro`, `numero`, "
        "`complemento`, `bairro`, `email`, `fone` (CNPJ exige o endereço).",
    )
    destinatario: dict[str, str | None] | None = Field(
        None,
        description="Destinatário do IBS/CBS quando não é o tomador (mesmas chaves do intermediário, sem `im`).",
    )
    ref_nfse: list[str] | None = Field(
        None, description="Chaves de acesso (50 caracteres) das NFS-e referenciadas, quando a tributação pede."
    )
    # Locação de imóvel (99.03): cep, logradouro, numero, complemento, bairro, c_mun, insc_imob_fisc, c_cib,
    # p_copropriedade, v_tot_oper, venc (DD/MM/AAAA ou o dia), iptu, condominio, emolumentos, redutor_social, outras,
    # outras_descricao (valores totais, sem a proporção do coproprietário)
    locacao: dict[str, str | float | None] | None = Field(
        None,
        description="Locação de imóvel (subitem 99.03). Imóvel: `cep`, `logradouro`, `numero`, `complemento`, "
        "`bairro`, `c_mun` (IBGE), `insc_imob_fisc` ou `c_cib`. Locação: `p_copropriedade` (% do locador, padrão "
        "100), `v_tot_oper` (aluguel inteiro), `venc` (DD/MM/AAAA ou só o dia). Parcelas fora da base do IBS/CBS, "
        "pelo valor total: `iptu`, `condominio`, `emolumentos`, `redutor_social`, `outras` + `outras_descricao`.",
        examples=[{"cep": "88010-000", "logradouro": "Rua Exemplo", "numero": "100", "venc": "10", "iptu": "85,00"}],
    )


def _nota(s, entrada: AvulsaEntrada) -> NotaAvulsa:
    try:
        valor = tabela_service.parse_valor(entrada.valor)
    except ValueError:
        valor = str(entrada.valor or "").strip()
    nome_trib, dados_trib = None, None
    if entrada.tributacao_id is not None and entrada.tributacao is not None:
        raise HTTPException(400, "Escolha uma tributação predefinida ou uma tributação só para esta nota, não as duas.")
    if entrada.tributacao_id is not None:
        t = s.get(Tributacao, entrada.tributacao_id)
        if t is None:
            raise HTTPException(400, "Tributação não encontrada. Recarregue a página.")
        nome_trib, dados_trib = t.nome, t.dados()
    elif entrada.tributacao is not None:
        dados_trib = trib_service.validar_completa(entrada.tributacao, trib_service.config_base(s))
    descontos = {}
    for campo, rotulo in (
        ("v_desc_incond", "Desconto incondicionado"),
        ("v_desc_cond", "Desconto condicionado"),
        ("v_serv_moeda", "Valor na moeda estrangeira"),
        ("ajuste_bc_v", "Dedução/redução da base"),
    ):
        bruto = getattr(entrada, campo)
        if bruto in (None, ""):
            continue
        try:
            descontos[campo] = tabela_service.parse_valor(bruto)
        except ValueError as exc:
            raise HTTPException(400, f"{rotulo} inválido: {bruto!r}.") from exc
    if entrada.ajuste_bc_p not in (None, ""):
        try:
            p = Decimal(str(entrada.ajuste_bc_p).replace("%", "").replace(",", ".").strip())
        except InvalidOperation as exc:
            raise HTTPException(400, f"Percentual de dedução/redução inválido: {entrada.ajuste_bc_p!r}.") from exc
        if not Decimal("0") < p <= Decimal("100"):
            raise HTTPException(400, "O percentual de dedução/redução deve ser maior que 0 e até 100% (E0453).")
        if "ajuste_bc_v" in descontos:
            raise HTTPException(400, "Informe a dedução/redução em percentual ou em reais, não os dois.")
        descontos["ajuste_bc_p"] = f"{p:.2f}"
    nota = DadosNota.de_dict(
        {
            "x_inf_comp": (entrada.x_inf_comp or "").strip(),
            "x_ped": (entrada.x_ped or "").strip(),
            "itens_pedido": [i.strip() for i in entrada.itens_pedido or [] if i and i.strip()],
            "tomador_exterior": _tomador_exterior(entrada.tomador_exterior),
            "n_di": (entrada.n_di or "").strip(),
            "n_re": (entrada.n_re or "").strip(),
            "obra": _limpo(entrada.obra, _CAMPOS_OBRA),
            "evento": _limpo(entrada.evento, _CAMPOS_EVENTO),
            "intermediario": _limpo(entrada.intermediario, _CAMPOS_PESSOA),
            "destinatario": _limpo(entrada.destinatario, _CAMPOS_PESSOA),
            "ref_nfse": [c.strip().upper() for c in entrada.ref_nfse or [] if c and c.strip()],
            "locacao": entrada.locacao,
            **{k: v for k, v in descontos.items() if v and Decimal(v) != 0},
        }
    )
    return NotaAvulsa(
        nome=entrada.nome.strip(),
        documento=entrada.documento,
        valor=valor,
        descricao=entrada.descricao.strip(),
        tributacao_nome=nome_trib,
        tributacao=dados_trib,
        tributacao_id=entrada.tributacao_id,
        nota=nota,
    )


_CAMPOS_EXTERIOR = (
    "nif",
    "c_nao_nif",
    "nome",
    "c_pais",
    "c_end_post",
    "cidade",
    "estado",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "email",
    "fone",
)


_CAMPOS_ENDERECO = ("cep", "c_end_post", "cidade", "estado", "logradouro", "numero", "complemento", "bairro")
_CAMPOS_OBRA = ("insc_imob_fisc", "c_obra", "c_cib", *_CAMPOS_ENDERECO)
_CAMPOS_PESSOA = (
    "documento",
    "nome",
    "im",
    "c_mun",
    "cep",
    "logradouro",
    "numero",
    "complemento",
    "bairro",
    "email",
    "fone",
)
_CAMPOS_EVENTO = ("nome", "dt_ini", "dt_fim", "id_atv_evt", *_CAMPOS_ENDERECO)


def _limpo(d: dict[str, str | None] | None, campos: tuple[str, ...]) -> dict[str, str] | None:
    if not d:
        return None
    return {k: v for k in campos if (v := str(d.get(k) or "").strip())} or None


def _tomador_exterior(d: dict[str, str | None] | None) -> dict[str, str] | None:
    if not d:
        return None
    limpo = {k: str(d.get(k) or "").strip() for k in _CAMPOS_EXTERIOR}
    limpo["c_pais"] = limpo["c_pais"].upper()
    if limpo["nif"]:
        limpo["c_nao_nif"] = ""
    return {k: v for k, v in limpo.items() if v} or None


def _total(valor: str) -> str:
    try:
        return f"{Decimal(valor):.2f}"
    except (InvalidOperation, TypeError):
        return "0.00"


@router.get("/avulsa/historico")
def historico_avulsa(documento: str | None = None) -> dict[str, Any]:
    """Descrições e tributações das notas já emitidas (as do ``documento`` marcadas), para a nota avulsa."""
    with sessao() as s:
        return historico_service.historico(s, documento)


@router.post("/avulsa/validar")
def validar_nota_avulsa(entrada: AvulsaEntrada) -> dict[str, Any]:
    """Mesma conferência da tabela, no formato de ``POST /tabela/validar`` (uma linha, ``linha_id`` 0)."""
    with sessao() as s:
        cfg = obter_config(s)
        nota = _nota(s, entrada)
        v = validar_avulsa(s, nota, cfg.amb)
        aliquotas = aliquotas_iss(s, [linha_avulsa(nota)], nota.tributacao, nota.tributacao_id)
        problemas = verificar_prontidao(s)
        if nota.tributacao is not None:
            problemas += [
                f"Tributação: {e}" for e in trib_service.coerencia(nota.tributacao, trib_service.config_base(s))
            ]
        c_mun, servicos = parametros.alvos(s, [linha_avulsa(nota)], nota.tributacao, nota.tributacao_nome)
    ate = time.monotonic() + parametros.ORCAMENTO_VALIDACAO
    return {
        "ambiente": cfg.ambiente,
        "ambiente_descricao": cfg.amb.descricao,
        "problemas": problemas,
        "avisos": parametros.avisos(cfg.amb, c_mun, servicos, ate) if not problemas else [],
        # O que a prefeitura cadastrou (retenção, regime especial, alíquota, benefício): informa, sem pedir ciência.
        "regras_prefeitura": regras_prefeitura.avisos(cfg.amb, [(a.origem, a.cfg) for a in servicos if a.cfg], ate)
        if not problemas
        else [],
        "linhas": [{"linha_id": 0, "erros": v.erros, "avisos": v.avisos}],
        "quantidade": 1,
        "total": _total(nota.valor),
        "tributacao_nome": nota.tributacao_nome or ("Só desta nota" if nota.tributacao else None),
        "aliquotas_iss": aliquotas,
    }


@router.post("/avulsa")
def emitir_nota_avulsa(entrada: AvulsaEntrada, acesso: UsuarioAtual) -> dict[str, Any]:
    with sessao() as s:
        nota = _nota(s, entrada)
    lote = criar_avulsa(nota)
    trib = nota.tributacao_nome or (
        "só desta nota: " + trib_service.resumo(nota.tributacao) if nota.tributacao else "padrão"
    )
    auth.auditar("nota_avulsa", f"lote {lote.id}, ambiente {lote.ambiente}, tributação {trib}", acesso)
    return obter_lote(lote.id)
