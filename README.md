<p align="center">
  <img src="frontend/public/logo.svg" alt="" width="96" height="96">
</p>

<h1 align="center">Emissor em Planilha</h1>

<p align="center">
  Emissão de <strong>NFS-e do Padrão Nacional</strong> (Sefin Nacional) <strong>em lote</strong>, a partir de uma planilha,
  direto do navegador.
</p>

<!--
  CAPTURA PRINCIPAL: substitua pelo print da tela "Emitir" (tabela de notas).
  Sugestão: salvar em docs/img/tabela.png e descomentar a linha abaixo.
  <p align="center"><img src="docs/img/tabela.png" alt="Tabela de notas a emitir" width="900"></p>
-->

---

Para quem emite **muitas notas de serviço recorrentes todo mês** (escritórios de contabilidade, consultorias,
prestadores com carteira fixa de clientes): você mantém uma tabela com **cliente, CNPJ/CPF, valor e descrição**,
edita como no Excel e emite tudo de uma vez. O sistema monta a DPS, assina com o certificado A1 da empresa, envia
para a Sefin Nacional, guarda o XML autorizado e gera o **DANFSe** em PDF.

## Funcionalidades

- **Tabela tipo Excel**: seleção em faixa, copiar/colar do Excel, alça de preenchimento, digitar e aplicar a várias
  células de uma vez. A tabela fica salva entre uma emissão e outra.
- **Importar e exportar** a tabela em XLSX ou CSV.
- **Emissão em lote** com progresso em tempo real, fila sequencial e proteção contra nota duplicada (antes de
  reenviar, o sistema consulta a Sefin para saber se a DPS já virou nota).
- **Homologação e produção**: alterna entre Produção Restrita e Produção nas configurações, com faixa de ambiente
  sempre visível e confirmação reforçada em produção.
- **Erros explicados**: código e mensagem da Sefin, campo, linha da tabela e sugestão de correção.
- **Emissões**: consulta, filtros e download de XML e PDF, individual ou em massa (ZIP).
- **DANFSe local** conforme a **NT 008/2026 v1.02** (a geração oficial por API foi suspensa em 03/08/2026).
- **Cadastro automático de clientes** (tomadores) a partir de XMLs de notas já emitidas e da
  [BrasilAPI](https://brasilapi.com.br) (com fallback). A emissão nunca depende da API externa.
- **Onboarding**: envie o certificado e alguns XMLs de notas antigas; os padrões da nota (código de serviço, regime
  tributário, tributação...) são extraídos deles.
- **Certificado A1 criptografado em repouso** no servidor; o PFX nunca é devolvido pela API.
- **Logs detalhados** (JSON Lines) com todas as trocas com a Sefin e um botão para gerar o arquivo de diagnóstico,
  sem segredos.

<!--
  VÍDEO / GIF: demonstração do fluxo completo (colar do Excel → validar → emitir → baixar PDFs).
  No GitHub, arraste o .mp4 para a edição do README (vira um link user-attachments) ou use um GIF:
  <p align="center"><img src="docs/img/demo.gif" alt="Demonstração" width="900"></p>
-->

## Capturas de tela

<!--
  Adicione as imagens em docs/img/ e descomente:

| Onboarding | Emissão em lote |
|---|---|
| <img src="docs/img/onboarding.png" width="440"> | <img src="docs/img/emissao.png" width="440"> |

| Emissões | DANFSe |
|---|---|
| <img src="docs/img/emissoes.png" width="440"> | <img src="docs/img/danfse.png" width="440"> |
-->

_Em breve._

## Como funciona

```
Planilha (cliente, CNPJ, valor, descrição)
   └─► DPS 1.01 (lxml) ─► validação nos XSD oficiais ─► assinatura XMLDSig (RSA-SHA256)
          └─► Sefin Nacional (REST + mTLS com o certificado A1)
                 └─► NFS-e autorizada ─► XML salvo + DANFSe em PDF (ReportLab, NT 008)
```

- **Backend:** Python 3.12, FastAPI, SQLite (SQLModel), lxml, signxml, cryptography, httpx, ReportLab.
- **Frontend:** React 18, TypeScript, Vite, TanStack Query, [Glide Data Grid](https://github.com/glideapps/glide-data-grid).
- Um único processo serve a API (`/api`) e a página. Pensado para rodar em **um computador da rede local** e ser
  acessado pelo navegador dos outros computadores.

## Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (instala o Python 3.12 sozinho)
- [Node.js](https://nodejs.org/en/download) 20 LTS ou mais novo (só para gerar a página)
- Certificado digital **A1** (arquivo `.pfx`) da empresa prestadora
- Windows ou Linux. No Linux, instale as fontes Liberation/Arimo para o DANFSe (`fonts-liberation`)

## Instalação

### Windows

1. Baixe o projeto (`git clone` ou ZIP) para o computador que vai servir o sistema.
2. Rode **`atualizar.bat`**: instala as dependências e gera a página. Rode de novo sempre que atualizar o código.
3. Rode **`iniciar.bat`** e deixe a janela aberta.
4. Abra `http://<IP-deste-computador>:8000` no navegador de qualquer máquina da rede.

### Linux / manual

```bash
cd frontend && npm ci && npm run build
cd ../backend && uv sync && uv run emissor
```

Na primeira abertura, o **onboarding** pede o certificado, os padrões da nota e o ambiente. Comece sempre em
**Homologação (Produção Restrita)**, emita uma ou duas notas de teste, confira o PDF e o XML e só então mude para
Produção.

## Configuração

| Variável | Padrão | Para quê |
|---|---|---|
| `EMISSOR_HOST` | `0.0.0.0` | Endereço em que o servidor escuta |
| `EMISSOR_PORT` | `8000` | Porta |
| `EMISSOR_DATA_DIR` | `./data` | Banco, certificado criptografado, XML/PDF emitidos e logs |
| `EMISSOR_IPS_PERMITIDOS` | _(vazio = todos)_ | CIDRs separados por vírgula, ex.: `192.168.0.0/24` |
| `EMISSOR_FONT_DIR` | | Pasta extra com Arial/Microsoft Sans Serif para o DANFSe |

## Segurança

> [!WARNING]
> O sistema **não tem login**: qualquer computador que acessar a página pode emitir notas com o certificado da
> empresa. Use em rede local confiável, restrinja com `EMISSOR_IPS_PERMITIDOS` e **nunca exponha à internet**.

- O PFX e a senha ficam criptografados (Fernet) em `data/`, com a chave em `data/secret.key`. Faça backup da pasta
  `data/` e não a compartilhe.
- Senha, chave privada e PFX nunca vão para os logs.
- O arquivo de diagnóstico (Configurações → "Baixar arquivo de diagnóstico") traz as trocas com a Sefin, que
  contêm dados das notas (clientes e valores). Envie só para quem pode vê-los.

## Desenvolvimento

```bash
# backend (API em http://127.0.0.1:8000, docs em /docs)
cd backend
uv run emissor
uv run pytest -q
uv run ruff check src tests && uv run ruff format src tests

# frontend (Vite em http://localhost:5173, com proxy de /api para :8000)
cd frontend
npm run dev
npx tsc --noEmit -p tsconfig.json
```

Os testes usam um certificado ICP-Brasil **falso** gerado na hora e uma NFS-e de exemplo com dados fictícios
(`backend/tests/fixtures/`). Se quiser testar com notas reais, coloque os XMLs em `temp/` (fora do git): os testes
extras rodam sozinhos.

Diagnóstico de certificado e conexão mTLS, sem emitir nada:

```bash
cd backend
uv run python -m emissor.nfse.diagnostico --pfx caminho/do/certificado.pfx   # --producao para o ambiente real
```

Decisões de arquitetura, detalhes do leiaute e armadilhas encontradas estão em [`CLAUDE.md`](CLAUDE.md).

### Estrutura

```
backend/src/emissor/
  nfse/          núcleo fiscal: DPS, XSD, assinatura, cliente Sefin, erros, leitura de XML
  nfse/danfse/   gerador do DANFSe (NT 008)
  services/      emissão em lote, clientes, tabela, certificado, arquivos
  api/           rotas FastAPI
frontend/src/    páginas React (Onboarding, Emitir, Emissões, Clientes, Configurações)
docs/            NT 008/2026 (DANFSe) e imagens do README
```

## Limitações conhecidas

- **Cancelamento** de NFS-e (evento e101101) ainda não implementado: cancele pelo portal nacional.
- **Grupo IBS/CBS** (reforma tributária) ainda não é enviado. Para optantes do Simples Nacional ele passa a ser
  obrigatório em **janeiro de 2027**.
- **CNPJ alfanumérico** de tomador é aceito no cadastro, mas a emissão fica bloqueada até a Sefin publicar um XSD
  compatível.
- Série da DPS na faixa de **aplicativo próprio/API** (1 a 49999). As séries 70000 a 79999 são exclusivas do Emissor
  Web nacional.

## Aviso

Projeto independente, **sem vínculo** com a Receita Federal, o Comitê Gestor da NFS-e ou a Sefin Nacional. Notas
emitidas em Produção têm validade jurídica: teste em homologação e confira os padrões fiscais com o seu contador
antes de usar.

Feito com [Claude Code](https://claude.com/claude-code), com supervisão humana.

<!-- LICENÇA: escolher (ex.: MIT) e adicionar o arquivo LICENSE na raiz. -->
