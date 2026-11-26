"""Textos e exemplos da documentação da API de integração (``/api/v1/docs``).

Só documentação: nada aqui valida pedido nem muda resposta. Os modelos de resposta descrevem o que as rotas devolvem
(``webhooks.nota_publica``, ``webhooks.lote_publico``...) e entram no OpenAPI por ``responses=``; o teste ponta a
ponta confere que as chaves dos exemplos são as mesmas das respostas de verdade, então mudou uma resposta, mude o
exemplo junto.

Todos os valores são fictícios: prestador 11.444.777/0001-61, tomadores 11.222.333/0001-81 e CPF 529.982.247-25
(números de teste, válidos só no formato), município 4205407 (Florianópolis/SC).
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field

DESCRICAO = """
Emita, consulte e cancele NFS-e a partir de outro sistema (cobrança, ERP, loja). Os exemplos desta página usam
dados fictícios: empresa `11.444.777/0001-61`, cliente `11.222.333/0001-81` e CPF `529.982.247-25`.

## Primeiros passos

1. Em *Configurações → Integrações*, o administrador da empresa cria um token (`emp_...`), escolhe o ambiente
   (**homologação** para testar, **produção** para valer) e as permissões. O token só aparece uma vez: guarde-o.
2. Envie o token no cabeçalho `Authorization: Bearer emp_...` em toda requisição (clique em **Authorize** acima para
   testar por esta página).
3. Confira com `GET /api/v1/empresa`: `pronta_para_emitir` deve ser `true`.
4. Emita com `POST /api/v1/notas` e acompanhe com `GET /api/v1/notas/{id}` (ou por um webhook).

```bash
curl -X POST https://<seu-endereco>/api/v1/notas \\
  -H "Authorization: Bearer emp_ab12cd34_exemploNaoUse" \\
  -H "Content-Type: application/json" \\
  -d '{"documento": "11.222.333/0001-81", "valor": "1500,00",
       "descricao": "Honorários contábeis de {mes_anterior}/{ano_mes_anterior}",
       "referencia_externa": "cobranca-2026-11-0042"}'
```

## Permissões do token

| Permissão | O que libera |
|---|---|
| `notas:ler` | `GET /notas`, `GET /notas/{id}` (XML e PDF), `GET /lotes/{id}` |
| `notas:emitir` | `POST /notas`, `POST /lotes`, `POST /notas/validar` |
| `notas:cancelar` | `POST /notas/{id}/cancelamento` |
| `clientes:ler` / `clientes:escrever` | `GET` / `PUT /clientes/{documento}` |
| `webhooks` | `GET`, `POST` e `DELETE /webhooks` |

`GET /empresa` e `GET /tributacoes` funcionam com qualquer permissão. O token é de **um** ambiente: só vê as notas
dele e recusa com 409 emitir ou cancelar se a empresa estiver no outro ambiente.

## Situação da nota (`status`)

| Valor | Significado |
|---|---|
| `pendente` | Na fila, ainda não enviada à Sefin. |
| `processando` | Enviada à Sefin, esperando a resposta. |
| `autorizada` | NFS-e emitida: `numero_nfse` e `chave_acesso` preenchidos; XML e PDF disponíveis. |
| `rejeitada` | Recusada pela Sefin ou pela conferência do sistema; o motivo está em `erro`. Corrija e envie de novo (a mesma `referencia_externa` volta a valer). |
| `erro` | Sem resposta confirmada da Sefin (fora do ar, certificado). O sistema confere antes de reenviar, sem duplicar. |
| `nao_enviada` | O lote parou antes desta nota (ex.: certificado recusado). Pode enviar de novo. |
| `cancelada` | Cancelada pelo sistema, pelo portal nacional ou pela prefeitura. |
| `substituida` | Substituída por outra NFS-e no portal nacional. |

## Sem nota em duplicidade

Envie `referencia_externa` (ou o cabeçalho `Idempotency-Key`) com o identificador da nota no seu sistema, como o id
da cobrança. Repetir o pedido devolve a nota já criada, com `repetida: true` e status 200, em vez de emitir outra.
A referência só fica livre de novo se a nota for `rejeitada` ou `nao_enviada`.

## Erros

Todo erro vem no mesmo formato: `mensagem` (o que aconteceu, em português) e, às vezes, `detalhes` (lista com o
que corrigir). Recusas da Sefin trazem ainda `sefin` com o código (ex.: `E0312`) e a dica de correção.

| Código | Quando |
|---|---|
| 400 | Dados da nota com problema (`detalhes` lista cada um). |
| 401 | Sem token, token errado ou revogado. |
| 403 | O token não tem a permissão da rota, ou a assinatura está suspensa (só leitura). |
| 404 | Nota, lote, cliente ou webhook não encontrado (ou de outro ambiente). |
| 409 | Token de outro ambiente, ou referência externa já usada num lote. |
| 422 | JSON fora do formato (campo com tipo errado). |
| 429 | Limite de requisições ou de notas do dia atingido (veja `Retry-After`). |
| 502 | Sefin ou ADN fora do ar, ou certificado recusado. Tente de novo mais tarde. |

## Webhooks

Cadastre um endereço em `POST /webhooks` (ou na tela) para receber os eventos. Cada entrega é um `POST` com o
corpo descrito em **Webhooks** no fim desta página e os cabeçalhos:

- `X-Emissor-Evento`: nome do evento (`nota.autorizada`...);
- `X-Emissor-Entrega`: id da entrega (igual ao `id` do corpo), para ignorar repetidas;
- `X-Emissor-Assinatura: t=<unix>,v1=<hex>`, com `v1 = HMAC-SHA256(segredo, "<t>.<corpo>")`.

Confira a assinatura com o corpo exatamente como chegou e recuse `t` com mais de 5 minutos. Responda 2xx em até
10 segundos; fora disso a entrega é repetida em 1 min, 5 min, 30 min, 2 h, 6 h e 24 h.

```python
import hashlib, hmac, time

def assinatura_valida(corpo: bytes, cabecalho: str, segredo: str) -> bool:
    partes = dict(p.split("=", 1) for p in cabecalho.split(","))
    if abs(time.time() - int(partes["t"])) > 300:
        return False
    esperado = hmac.new(segredo.encode(), f"{partes['t']}.".encode() + corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, partes["v1"])
```

## Cobranças do Asaas e da Stripe

Em *Configurações → Integrações → Receber cobranças* o administrador liga o Asaas ou a Stripe: cada pagamento
recebido vira uma nota, sem programar nada (a nota leva `referencia_externa` = id da cobrança).

## Limites

60 requisições por minuto e 1.000 notas por dia por token (ajustáveis na instalação própria); até 500 notas por lote.
"""

# --- exemplos ------------------------------------------------------------------------------------------------

CHAVE = "42054072211444777000161000000000000126110000000011"

NOTA_AUTORIZADA: dict[str, Any] = {
    "id": 128,
    "referencia_externa": "cobranca-2026-11-0042",
    "status": "autorizada",
    "ambiente": "homologacao",
    "lote_id": "70e5dc6b24cc",
    "tomador": {"documento": "11222333000181", "nome": "Cliente Exemplo Ltda"},
    "valor": "1500.00",
    "descricao": "Honorários contábeis de outubro/2026",
    "numero_nfse": "42",
    "chave_acesso": CHAVE,
    "serie": 1,
    "n_dps": 42,
    "dh_emissao": "2026-11-26T09:27:43",
    "dh_processamento": "2026-11-26T09:28:43-03:00",
    "tributacao": "Contabilidade",
    "erro": None,
    "alertas": [],
    "origem": "api:3",
    "criado_em": "2026-11-26T09:28:40",
    "atualizado_em": "2026-11-26T09:28:43",
}

NOTA_PENDENTE = NOTA_AUTORIZADA | {
    "status": "pendente",
    "descricao": "Honorários contábeis de {mes_anterior}/{ano_mes_anterior}",
    "numero_nfse": None,
    "chave_acesso": None,
    "serie": None,
    "n_dps": None,
    "dh_emissao": None,
    "dh_processamento": None,
    "atualizado_em": "2026-11-26T09:28:40",
}

NOTA_REJEITADA = NOTA_PENDENTE | {
    "id": 129,
    "referencia_externa": "cobranca-2026-11-0043",
    "status": "rejeitada",
    "descricao": "Honorários contábeis de outubro/2026",
    "tomador": {"documento": "52998224725", "nome": "Maria Exemplo"},
    "valor": "350.00",
    "serie": 1,
    "n_dps": 43,
    "dh_emissao": "2026-11-26T09:29:10",
    "erro": {
        "resumo": "A Sefin recusou a nota.",
        "mensagens": [
            {
                "codigo": "E0312",
                "descricao": "O código de tributação nacional não é administrado pelo município de incidência.",
                "complemento": None,
                "dica": "Confira o código do serviço na tributação: a prefeitura não cadastrou esse serviço no "
                "Sistema Nacional.",
            }
        ],
    },
    "atualizado_em": "2026-11-26T09:29:12",
}

LOTE: dict[str, Any] = {
    "id": "77159deae426",
    "status": "em_andamento",
    "ambiente": "homologacao",
    "total": 2,
    "contagem": {"autorizada": 1, "pendente": 1},
    "motivo_interrupcao": None,
    "criado_em": "2026-11-26T09:30:00",
    "finalizado_em": None,
    "notas": [
        {"id": 130, "referencia_externa": "cobranca-2026-11-0044", "status": "autorizada"},
        {"id": 131, "referencia_externa": "cobranca-2026-11-0045", "status": "pendente"},
    ],
}

EMPRESA: dict[str, Any] = {
    "id": 1,
    "cnpj": "11444777000161",
    "nome": "EMPRESA EXEMPLO SERVICOS LTDA",
    "ambiente_empresa": "homologacao",
    "ambiente_token": "homologacao",
    "pronta_para_emitir": True,
    "problemas": [],
    "certificado": {
        "titular": "EMPRESA EXEMPLO SERVICOS LTDA",
        "valido_ate": "2027-08-31T23:59:59+00:00",
        "dias_para_vencer": 278,
    },
    "token": {"nome": "ERP", "escopos": ["notas:ler", "notas:emitir"]},
}

TRIBUTACOES = [
    {"id": 3, "nome": "Contabilidade", "resumo": "serviço 171901, Não Retido, com IBS/CBS"},
    {"id": 5, "nome": "Consultoria com retenção", "resumo": "serviço 170101, Retido pelo Tomador (2%), retém IRRF"},
]

VALIDACAO_OK: dict[str, Any] = {
    "pode_emitir": True,
    "ambiente": "homologacao",
    "problemas": [],
    "erros": [],
    "avisos": [],
    "regras_prefeitura": [],
    "aliquotas_iss": [],
    "total": "1500.00",
}

VALIDACAO_ERRO = VALIDACAO_OK | {
    "pode_emitir": False,
    "erros": [
        "Valor inválido ou zerado: '0.00'.",
        "A descrição tem {mes_atual}, que não é uma variável conhecida. Use {mes}, {mes_numero}, {ano}, "
        "{competencia}, {mes_anterior}, {mes_anterior_numero}, {ano_mes_anterior}, {competencia_anterior}, {data}.",
    ],
    "total": "0.00",
}

CLIENTE: dict[str, Any] = {
    "documento": "11222333000181",
    "nome": "CLIENTE EXEMPLO LTDA",
    "c_mun": "4205407",
    "municipio": "Florianópolis / SC",
    "cep": "88010000",
    "logradouro": "RUA EXEMPLO",
    "numero": "100",
    "complemento": "SALA 2",
    "bairro": "CENTRO",
    "fone": "4833330000",
    "email": "financeiro@cliente-exemplo.com.br",
    "inscricao_municipal": None,
    "situacao_cadastral": "ATIVA",
    "origem": "brasilapi",
    "endereco_completo": True,
    "atualizado_em": "2026-11-26T09:28:43",
}

WEBHOOK: dict[str, Any] = {
    "id": 7,
    "url": "https://erp.exemplo.com.br/webhooks/nfse",
    "eventos": ["nota.autorizada", "nota.rejeitada"],
    "ambiente": "homologacao",
    "descricao": "ERP financeiro",
    "ativo": True,
    "criado_por": "token:ERP",
    "criado_em": "2026-11-26T09:28:43",
    "falhas_seguidas": 0,
}

EVENTO_WEBHOOK: dict[str, Any] = {
    "id": "ent_5f2c0a9d7e7b4c1f8a3e6d2b9c0f1a4e",
    "evento": "nota.autorizada",
    "criado_em": "2026-11-26T09:28:43-03:00",
    "empresa": {"id": 1, "cnpj": "11444777000161"},
    "dados": NOTA_AUTORIZADA,
}

# Pedidos (aparecem como exemplos escolhíveis no "Try it out").
PEDIDOS_NOTA: dict[str, dict[str, Any]] = {
    "simples": {
        "summary": "Nota com a tributação padrão",
        "description": "Cliente já cadastrado (ou CNPJ que a Receita encontra): basta documento, valor e descrição.",
        "value": {
            "documento": "11.222.333/0001-81",
            "valor": "1500,00",
            "descricao": "Honorários contábeis de {mes_anterior}/{ano_mes_anterior}",
            "referencia_externa": "cobranca-2026-11-0042",
        },
    },
    "cpf_tributacao": {
        "summary": "Pessoa física com tributação predefinida",
        "description": "CPF sem cadastro: informe o nome. `tributacao_id` vem de `GET /tributacoes`.",
        "value": {
            "documento": "529.982.247-25",
            "nome": "Maria Exemplo",
            "valor": 350.0,
            "descricao": "Consultoria em {competencia}",
            "tributacao_id": 3,
            "referencia_externa": "cobranca-2026-11-0043",
        },
    },
    "desconto_complemento": {
        "summary": "Com desconto e informações complementares",
        "value": {
            "documento": "11222333000181",
            "valor": "2000,00",
            "descricao": "Implantação de sistema",
            "v_desc_incond": "200,00",
            "x_inf_comp": "Contrato 2026/015. Pagamento por Pix.",
            "x_ped": "OC-7781",
            "referencia_externa": "pedido-7781",
        },
    },
    "aluguel": {
        "summary": "Aluguel de imóvel (99.03)",
        "description": "A tributação escolhida precisa ser de locação (subitem 99.03, com IBS/CBS).",
        "value": {
            "documento": "529.982.247-25",
            "nome": "Maria Exemplo",
            "valor": "2500,00",
            "descricao": "Aluguel de {mes}/{ano}",
            "tributacao_id": 9,
            "locacao": {
                "cep": "88010-000",
                "logradouro": "Rua Exemplo",
                "numero": "100",
                "complemento": "Apto 301",
                "bairro": "Centro",
                "venc": "10",
                "iptu": "85,00",
                "condominio": "420,00",
            },
            "referencia_externa": "aluguel-301-2026-11",
        },
    },
}

PEDIDO_LOTE = {
    "notas": [
        PEDIDOS_NOTA["simples"]["value"],
        PEDIDOS_NOTA["cpf_tributacao"]["value"],
    ]
}

PEDIDO_CANCELAMENTO = {"c_motivo": "1", "x_motivo": "Valor digitado errado na emissão"}
PEDIDO_CLIENTE = {
    "nome": "Cliente Exemplo Ltda",
    "cep": "88010-000",
    "logradouro": "Rua Exemplo",
    "numero": "100",
    "complemento": "Sala 2",
    "bairro": "Centro",
    "c_mun": "4205407",
    "email": "financeiro@cliente-exemplo.com.br",
    "fone": "(48) 3333-0000",
}
PEDIDO_WEBHOOK = {
    "url": "https://erp.exemplo.com.br/webhooks/nfse",
    "eventos": ["nota.autorizada", "nota.rejeitada"],
    "descricao": "ERP financeiro",
}

# --- modelos das respostas (só documentação) -----------------------------------------------------------------


class Tomador(BaseModel):
    documento: str | None = Field(description="CNPJ/CPF sem pontuação (ou o NIF, no exterior).")
    nome: str | None = Field(description="Nome ou razão social que foi na nota.")


class MensagemSefin(BaseModel):
    codigo: str | None = Field(description="Código da Sefin (ex.: E0312) ou do sistema.")
    descricao: str | None = Field(description="Mensagem original.")
    complemento: str | None = Field(description="Detalhe extra que a Sefin mandou, quando há.")
    dica: str | None = Field(description="Como resolver, em português.")


class ErroNota(BaseModel):
    resumo: str | None = Field(description="O que aconteceu, numa frase.")
    mensagens: list[MensagemSefin] = Field(description="Cada problema apontado.")


class Nota(BaseModel):
    """A nota como a API mostra (também é o `dados` dos eventos `nota.*` dos webhooks)."""

    id: int = Field(description="Id da nota neste emissor (use em `/notas/{id}`).")
    referencia_externa: str | None = Field(description="O identificador que o seu sistema mandou.")
    status: str = Field(
        description="pendente, processando, autorizada, rejeitada, erro, nao_enviada, cancelada ou substituida "
        "(ver a tabela no início da página)."
    )
    ambiente: str = Field(description="producao ou homologacao.")
    lote_id: str | None = Field(description="Lote em que a nota foi enviada (uma nota sozinha também tem lote).")
    tomador: Tomador
    valor: str = Field(description='Valor do serviço em R$, com ponto decimal (`"1500.00"`).')
    descricao: str = Field(
        description="Descrição. Enquanto `pendente` aparece como foi enviada; ao emitir, com as variáveis trocadas."
    )
    numero_nfse: str | None = Field(description="Número da NFS-e dado pela Sefin (só depois de autorizada).")
    chave_acesso: str | None = Field(description="Chave de acesso da NFS-e (50 caracteres).")
    serie: int | None = Field(description="Série da DPS.")
    n_dps: int | None = Field(description="Número da DPS (numeração do emissor, por ambiente).")
    dh_emissao: str | None = Field(description="Data e hora de emissão da DPS (horário de Brasília).")
    dh_processamento: str | None = Field(description="Data e hora em que a Sefin processou a nota (com fuso).")
    tributacao: str | None = Field(
        description="Nome da tributação usada; vazio = padrão da empresa; `Só desta nota` = enviada em `tributacao`."
    )
    erro: ErroNota | None = Field(description="Motivo, quando `rejeitada` ou `erro`.")
    alertas: list[dict[str, Any]] = Field(description="Alertas que a Sefin devolveu junto da autorização.")
    origem: str = Field(description="`api:<id do token>`, `asaas:<id>`, `stripe:<id>` ou `tela`.")
    criado_em: str | None = Field(description="Quando a nota entrou na fila.")
    atualizado_em: str | None = Field(description="Última mudança de situação.")


class NotaCriada(Nota):
    repetida: bool = Field(
        description="`true` quando a referência externa já tinha nota: a resposta é a nota existente (status 200)."
    )


class NotaCancelada(Nota):
    aviso: str | None = Field(description="Observação do cancelamento (ex.: a nota já estava cancelada no portal).")


class ListaNotas(BaseModel):
    itens: list[Nota]
    total: int = Field(description="Total de notas no filtro (todas as páginas).")
    pagina: int
    por_pagina: int


class NotaDoLote(BaseModel):
    id: int
    referencia_externa: str | None
    status: str


class Lote(BaseModel):
    id: str = Field(description="Id do lote (use em `GET /lotes/{id}`).")
    status: str = Field(description="em_andamento, concluido, interrompido ou cancelado.")
    ambiente: str
    total: int = Field(description="Quantidade de notas.")
    contagem: dict[str, int] = Field(description="Quantas notas há em cada situação.")
    motivo_interrupcao: str | None = Field(
        description="Por que o lote parou (certificado recusado, Sefin fora do ar...). As notas que faltavam ficam "
        "`nao_enviada`."
    )
    criado_em: str | None
    finalizado_em: str | None
    notas: list[NotaDoLote]


class Certificado(BaseModel):
    titular: str
    valido_ate: str
    dias_para_vencer: int


class TokenInfo(BaseModel):
    nome: str
    escopos: list[str] = Field(description="Permissões do token.")


class Empresa(BaseModel):
    id: int
    cnpj: str | None = Field(description="CNPJ do prestador (emitente das notas).")
    nome: str | None
    ambiente_empresa: str | None = Field(description="Ambiente em que a empresa está emitindo agora.")
    ambiente_token: str | None = Field(description="Ambiente do token. Para emitir, os dois precisam ser iguais.")
    pronta_para_emitir: bool = Field(description="Configuração completa, certificado válido e ambientes iguais.")
    problemas: list[str] = Field(description="O que falta para emitir (vazio quando está tudo certo).")
    certificado: Certificado | None
    token: TokenInfo


class TributacaoResumo(BaseModel):
    id: int = Field(description="Use em `tributacao_id` ao emitir.")
    nome: str
    resumo: str = Field(description="Serviço, retenção do ISS e IBS/CBS, em poucas palavras.")


class Validacao(BaseModel):
    pode_emitir: bool = Field(description="`true` quando não há `problemas` nem `erros`.")
    ambiente: str | None
    problemas: list[str] = Field(description="Problemas da empresa (configuração, certificado).")
    erros: list[str] = Field(description="Problemas da nota: impedem a emissão.")
    avisos: list[str] = Field(description="Pontos para conferir (não impedem).")
    regras_prefeitura: list[dict[str, Any]] = Field(description="O que a prefeitura cadastrou para o serviço.")
    aliquotas_iss: list[dict[str, Any]] = Field(description="Alíquota do ISS retido que será usada, quando há.")
    total: str = Field(description="Valor total em R$.")


class Cliente(BaseModel):
    documento: str
    nome: str
    c_mun: str | None = Field(description="Código IBGE do município (7 dígitos).")
    municipio: str | None = Field(description="Nome do município / UF.")
    cep: str | None
    logradouro: str | None
    numero: str | None
    complemento: str | None
    bairro: str | None
    fone: str | None
    email: str | None
    inscricao_municipal: str | None
    situacao_cadastral: str | None = Field(description="Situação na Receita, quando consultada.")
    origem: str | None = Field(
        description="De onde veio o cadastro: xml (notas importadas), brasilapi/cnpja (Receita), planilha ou manual."
    )
    endereco_completo: bool = Field(
        description="Tem o que a nota precisa (CEP, logradouro, número, bairro, município)."
    )
    atualizado_em: str | None


class Webhook(BaseModel):
    id: int
    url: str
    eventos: list[str]
    ambiente: str
    descricao: str
    ativo: bool = Field(description="Desligado depois de muitas falhas seguidas (religue na tela).")
    criado_por: str | None
    criado_em: str | None
    falhas_seguidas: int


class WebhookCriado(Webhook):
    segredo: str = Field(description="Segredo da assinatura (`whsec_...`). Só aparece nesta resposta: guarde-o.")


class EmpresaEvento(BaseModel):
    id: int
    cnpj: str | None


class EventoWebhook(BaseModel):
    """Corpo de cada entrega de webhook."""

    id: str = Field(description="Id da entrega (igual ao cabeçalho `X-Emissor-Entrega`): use para ignorar repetidas.")
    evento: str = Field(description="Nome do evento.")
    criado_em: str = Field(description="Quando o evento aconteceu.")
    empresa: EmpresaEvento
    dados: Nota = Field(description="A nota (eventos `nota.*`) ou o lote (`lote.concluido`, formato de `Lote`).")


class Erro(BaseModel):
    mensagem: str = Field(description="O que aconteceu, em português.")
    detalhes: list[Any] | None = Field(None, description="O que corrigir, item por item (quando há).")


def _json(exemplo: Any) -> dict[str, Any]:
    return {"application/json": {"example": exemplo}}


ERROS: dict[int, dict[str, Any]] = {
    400: {
        "model": Erro,
        "description": "Dados com problema",
        "content": _json(
            {
                "mensagem": "A nota tem erros. Corrija antes de emitir.",
                "detalhes": ["Valor inválido ou zerado: 'abc'."],
            }
        ),
    },
    401: {
        "model": Erro,
        "description": "Sem token ou token inválido",
        "content": _json({"mensagem": "Use um token de API no cabeçalho Authorization: Bearer emp_...", "login": True}),
    },
    403: {
        "model": Erro,
        "description": "Token sem a permissão",
        "content": _json({"mensagem": "Este token não tem a permissão “notas:emitir”. Crie outro token com ela."}),
    },
    404: {"model": Erro, "description": "Não encontrado", "content": _json({"mensagem": "Nota não encontrada."})},
    409: {
        "model": Erro,
        "description": "Ambiente diferente",
        "content": _json(
            {
                "mensagem": "Este token é de producao, mas a empresa está emitindo em homologacao. Use o token do "
                "ambiente certo ou troque o ambiente nas configurações.",
                "ambiente_token": "producao",
                "ambiente_empresa": "homologacao",
            }
        ),
    },
    422: {
        "model": Erro,
        "description": "JSON fora do formato",
        "content": _json(
            {"mensagem": "Dados inválidos na requisição.", "detalhes": ["valor.float: Input should be a valid number"]}
        ),
    },
    429: {
        "model": Erro,
        "description": "Limite atingido",
        "content": _json({"mensagem": "Limite de 60 requisições por minuto atingido."}),
    },
}


def respostas(status: int, modelo: type[BaseModel] | None, exemplo: Any, descricao: str, *erros: int):
    """``responses=`` de uma rota: a resposta de sucesso com modelo e exemplo, mais os erros que ela pode dar."""
    r: dict[int | str, dict[str, Any]] = {status: {"description": descricao, "content": _json(exemplo)}}
    if modelo is not None:
        r[status]["model"] = modelo
    for e in (401, *erros):
        r[e] = ERROS[e]
    return r


def documentar_webhooks(app) -> None:
    """Seção **Webhooks** do OpenAPI: um item por evento, com o corpo e os cabeçalhos de cada entrega."""
    from fastapi import Body, Header

    from emissor.services import webhooks

    for nome, descricao in webhooks.EVENTOS.items():
        exemplo = EVENTO_WEBHOOK | {"evento": nome}
        if nome == "lote.concluido":
            exemplo["dados"] = LOTE | {
                "status": "concluido",
                "contagem": {"autorizada": 2},
                "finalizado_em": "2026-11-26T09:30:05",
                "notas": [n | {"status": "autorizada"} for n in LOTE["notas"]],
            }
        elif nome == "nota.rejeitada":
            exemplo["dados"] = NOTA_REJEITADA
        elif nome in ("nota.cancelada", "nota.substituida"):
            exemplo["dados"] = NOTA_AUTORIZADA | {"status": nome.split(".")[1]}
        elif nome == "nota.erro":
            exemplo["dados"] = NOTA_PENDENTE | {
                "status": "erro",
                "erro": {
                    "resumo": "A Sefin não respondeu.",
                    "mensagens": [
                        {
                            "codigo": None,
                            "descricao": "Tempo esgotado ao falar com a Sefin.",
                            "complemento": None,
                            "dica": "O sistema confere na Sefin antes de reenviar; não emita a nota de novo.",
                        }
                    ],
                },
            }

        def receber(corpo, x_emissor_evento, x_emissor_entrega, x_emissor_assinatura) -> None: ...

        # Anotações de verdade (não texto): com ``from __future__ import annotations`` o FastAPI não acharia
        # ``nome`` e ``exemplo``, que são deste laço.
        receber.__annotations__ = {
            "corpo": Annotated[EventoWebhook, Body(openapi_examples={"exemplo": {"summary": nome, "value": exemplo}})],
            "x_emissor_evento": Annotated[str, Header(description="Nome do evento.", examples=[nome])],
            "x_emissor_entrega": Annotated[
                str, Header(description="Id da entrega, igual ao `id` do corpo.", examples=[exemplo["id"]])
            ],
            "x_emissor_assinatura": Annotated[
                str,
                Header(
                    description='`t=<unix>,v1=<hex>`, com `v1 = HMAC-SHA256(segredo, "<t>.<corpo>")`.',
                    examples=["t=1795696123,v1=3f1c...e9a0"],
                ),
            ],
            "return": None,
        }

        app.webhooks.post(
            nome,
            summary=descricao,
            description="Responda com 2xx em até 10 s. Fora disso, novas tentativas em 1 min, 5 min, 30 min, 2 h, "
            "6 h e 24 h.",
            responses={200: {"description": "Recebido"}},
        )(receber)


def _exemplos_com_nulos(openapi: dict[str, Any], app, rotas_api, prefixo: str) -> None:
    """Põe de volta os exemplos como foram escritos: o FastAPI tira as chaves ``None`` ao gerar o OpenAPI, e o
    exemplo mostraria uma nota sem ``numero_nfse``, ``erro``... quando a resposta de verdade traz ``null``."""
    from fastapi.routing import APIRoute

    for secao, rotas, antes in (("paths", rotas_api, prefixo), ("webhooks", app.webhooks.routes, "")):
        for rota in rotas:
            if not isinstance(rota, APIRoute):
                continue
            for metodo in rota.methods:
                op = openapi.get(secao, {}).get(antes + rota.path, {}).get(metodo.lower())
                if op is None:
                    continue
                for status, resp in (rota.responses or {}).items():
                    destino = op["responses"].get(str(status), {}).get("content", {})
                    for tipo, conteudo in resp.get("content", {}).items():
                        if tipo in destino:
                            destino[tipo].update({k: v for k, v in conteudo.items() if k in ("example", "examples")})
                corpo = rota.body_field
                exemplos = getattr(corpo.field_info, "openapi_examples", None) if corpo else None
                if exemplos and "requestBody" in op:
                    op["requestBody"]["content"]["application/json"]["examples"] = exemplos


def ajustar(openapi: dict[str, Any], app, rotas_api, prefixo: str) -> dict[str, Any]:
    """Acertos no OpenAPI gerado: exemplos com ``null`` e o 422 no formato que a API devolve (``mensagem``).

    ``rotas_api`` são as rotas do router da API (o FastAPI não as expõe em ``app.routes`` depois do include)."""
    _exemplos_com_nulos(openapi, app, rotas_api, prefixo)
    for operacoes in [*openapi.get("paths", {}).values(), *openapi.get("webhooks", {}).values()]:
        for op in operacoes.values():
            if op.get("operationId", "").startswith("receber"):
                op["responses"].pop("422", None)  # o emissor é quem envia: não há 422 dele aqui
                op["responses"].get("200", {}).pop("content", None)  # basta um 2xx, sem corpo
            elif "422" in op.get("responses", {}):
                op["responses"]["422"] = {
                    "description": ERROS[422]["description"],
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Erro"},
                            "example": ERROS[422]["content"]["application/json"]["example"],
                        }
                    },
                }
    esquemas = openapi.get("components", {}).get("schemas", {})
    for nome in ("HTTPValidationError", "ValidationError"):
        esquemas.pop(nome, None)
    return openapi
