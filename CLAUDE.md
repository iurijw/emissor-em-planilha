# Emissor em Planilha — Emissão de NFS-e Nacional em massa

Sistema web para emitir NFS-e do **Padrão Nacional** (Sefin Nacional) em lote a partir de uma planilha, usando o
certificado digital A1 do prestador guardado no servidor. Público-alvo: quem emite muitas notas de serviço
recorrentes (escritórios de contabilidade, consultorias, prestadores com carteira fixa de clientes).

> Este arquivo é a fonte da verdade das decisões de arquitetura. Toda decisão nova ou
> alterada deve ser registrada aqui (seção "Registro de decisões").

## Status

- **Fase atual:** F5 — verificação. O fluxo completo foi testado com Sefin simulada e com a Sefin real recusando um
  certificado falso; a validação em Produção Restrita com certificado real é feita por quem instala.
- **F6 (parcial, 2026-10-09):** cancelamento (evento 101101) e situação das notas pelo ADN implementados e testados
  com Sefin/ADN **simulados** (a Sefin/ADN não eram acessíveis do ambiente de desenvolvimento). Validar em
  Produção Restrita: cancelar uma nota pelo sistema e outra pelo portal e conferir a sincronização.
- Se a Sefin rejeitar a assinatura da DPS, trocar `C14N` em `backend/src/emissor/nfse/signer.py` para C14N 1.0
  inclusiva e registrar aqui.
- Ambientes: o sistema alterna entre **Produção Restrita (homologação)** e **Produção** nas configurações.

## Requisitos

1. Primeira abertura → **onboarding**: upload do certificado digital + configuração dos campos padrão da nota,
   pré-preenchidos a partir de XMLs de NFS-e já emitidas pelo prestador.
2. Tela principal: **tabela de notas a emitir** (Nome do cliente, CNPJ/CPF, Valor, Descrição).
   - Edição em massa selecionando várias células, como no Excel (seleção em faixa,
     copiar/colar do Excel, alça de preenchimento, digitar e aplicar a todas selecionadas).
   - Importar e exportar a tabela.
   - A tabela **persiste** entre emissões (não precisa reimportar).
3. Data de competência = data da emissão; `dhEmi` = data/hora da emissão.
4. Certificado salvo no servidor.
5. Aba de **emissões**: consultar, baixar XML e PDF, individual e em massa.
6. Frontend limpo; quando algo der errado, o usuário deve saber **exatamente** o quê
   (código e mensagem da Sefin, campo, linha da tabela, sugestão de correção).
7. **Logger detalhado** para diagnóstico (inclusive por Claude): payloads, respostas,
   tempos, IDs de correlação.

Premissas de produto:
- Hospedagem: **servidor na rede local**, acesso pelo navegador.
- Login: **não** (ver risco em "Segurança").
- Endereço do tomador: **cadastro automático** de clientes (XMLs importados + BrasilAPI).
- Alternância homologação/produção obrigatória; testes manuais em homologação antes de produção.

## Pesquisa de bibliotecas (2026-09-23)

| Biblioteca | O que faz | Veredito |
|---|---|---|
| **PyNFe** (TadaSoftware, LGPL) | NF-e/NFC-e; NFS-e só ABRASF (Ginfes/Betha) via SOAP | **Não suporta NFS-e Nacional.** Descartada. |
| **nfelib** (Akretion, MIT, 2.5.2 mar/2026) | Bindings xsdata para ler/gerar XML | NFS-e só **v1.00** (sem v1.01), sem cliente HTTP, sem DANFSe. Descartada como dependência. |
| **pynfse-nacional** (roberto-mello, 0.9.5 jul/2026) | Cliente completo (DPS, assinatura signxml, mTLS, consulta, cancelamento, PDF) | O mais maduro, porém **AGPL-3.0** e o PDF local **não segue a NT 008** (ainda "DANFSe v1.0"). Usado apenas como **referência** de comportamento (não copiar código — licença). |
| **brans-nfe** (MIT, 0.2.0 alpha jun/2026) | Cliente sobre nfelib | DPS 1.00 + RSA-SHA1, 1 contribuidor. Imaturo. |
| **nfse_sefin** (claudio-mas) | Diagnóstico | Ainda não emite. O `DESIGN.md` dele tem boas anotações de contrato da API. |
| **open-nfse** (fm-s, TypeScript, MIT) | Cliente com eventos e distribuição por NSU | Traz os **OpenAPI oficiais** (Sefin, ADN Contribuinte) e as regras do Anexo II de eventos extraídas em `specs/ruleset/`. Usado como **referência** na F6 (pesquisa de 2026-10-09). |

**Conclusão:** não existe lib Python madura + licença permissiva + DPS 1.01 + DANFSe NT 008.
Implementamos um **módulo fiscal próprio e enxuto** sobre bibliotecas de base confiáveis:

- `lxml` — montar XML da DPS e validar contra os **XSD oficiais v1.01** (gov.br/nfse).
- `signxml` — XMLDSig enveloped (puro Python, funciona no Windows sem xmlsec).
- `cryptography` — ler o PFX (A1), extrair CNPJ/validade, criptografar em repouso.
- `httpx` — cliente HTTP com **mTLS** (certificado do cliente).
- `reportlab` — DANFSe conforme **NT 008/2026 v1.02** (posições em cm da própria NT; QR Code vetorial).

### Fatos técnicos confirmados

- **DANFSe oficial por API foi suspenso em 03/08/2026** (NT 008 v1.02, item 1). O sistema
  **deve gerar o DANFSe localmente**. Especificação salva em `docs/NT-008-2026-DANFSe-v1.02.pdf`.
  Pontos-chave da NT 008:
  - A4 retrato, página única, margens 0,15–0,20 cm, linhas 0,5 pt, borda 1 pt.
  - Cabeçalho: logo NFS-e (esquerda), "DANFSe v2.0" + "Documento Auxiliar da NFS-e"
    (centro, Arial 9 negrito), município/ambiente gerador/tipo de ambiente (direita).
  - Homologação (`tpAmb=2`): "NFS-e SEM VALIDADE JURÍDICA" em vermelho no cabeçalho.
  - QR Code ≥ 1,52 cm em X 17,48 / Y 1,67 cm → `https://www.nfse.gov.br/ConsultaPublica/?tpc=1&chave=<chave>`.
  - Fontes: Arial (rótulos) e Microsoft Sans Serif (conteúdo); blocos 7 pt, rótulos 6 pt.
  - Sombreamento cinza 5% no cabeçalho, títulos de bloco, "Emitente da NFS-e" e "Valor Líquido + IBS/CBS".
  - Blocos vazios: frases padrão ("TOMADOR/ADQUIRENTE DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e" etc.).
  - Campos sem informação → traço "-". Texto longo → reticências.
  - Informações complementares obrigatórias: "Totais Aproximados dos Tributos cfe. Lei nº 12.741/2012 ...".
  - Canceladas: marca d'água diagonal "CANCELADA" (Arial ≥ 50 pt, cinza K35).
- **Endpoints Sefin Nacional** (REST + mTLS, certificado ICP-Brasil A1):
  - Produção: `https://sefin.nfse.gov.br/SefinNacional` · ADN `https://adn.nfse.gov.br`
  - Produção restrita: `https://sefin.producaorestrita.nfse.gov.br/SefinNacional` · ADN `https://adn.producaorestrita.nfse.gov.br`
  - `POST /nfse` body `{"dpsXmlGZipB64": base64(gzip(xml_assinado))}` → `chaveAcesso`, `nfseXmlGZipB64`, `alertas`, `idDps`...
  - `GET /nfse/{chave}`, `GET|HEAD /dps/{idDps}`, `POST /nfse/{chave}/eventos` (cancelamento e101101).
  - Erros vêm em formatos variados (`erro`, `erros`, lista, `codigo`/`mensagem`) → normalizar.
- **Série da DPS por canal** (rejeição E0010 se fora da faixa):
  00001–49999 aplicativo próprio/API · 50000–69999 emissor móvel · **70000–79999 Emissor Web** ·
  80000–89999 transcrição manual.
  → Este sistema usa série da faixa de **API** (padrão `1`), com numeração `nDPS` própria.
- **Assinatura da DPS:** RSA-SHA256, digest SHA-256, enveloped, referência ao `Id` de `infDPS`,
  sem prefixo de namespace (`E1228` rejeita prefixos), UTF-8 (`E1229`). Canonicalização:
  exc-c14n (mesma usada pela Sefin nos XMLs de retorno e pela lib mais madura).
  Se a homologação rejeitar, testar C14N 1.0 inclusiva (configurável no código).
- **Id da DPS:** `"DPS" + cMun(7) + tpInsc(1: 1=CPF, 2=CNPJ) + inscrição(14, zeros à esq.) + série(5) + nDPS(15)`.
  Ex.: `DPS 4205407 2 11444777000161 00001 000000000000042` (sem os espaços).
- **Reforma tributária (IBS/CBS):** para optantes do Simples Nacional o grupo IBSCBS passa a ser
  obrigatório em **jan/2027**, e a ausência não rejeita até 31/12/2026 (Ato Conjunto RFB/CGIBS 4/2026).
  → Versão inicial emite **DPS 1.01 sem IBSCBS**, igual às notas do Emissor Web; o builder deve ficar
  preparado para receber o grupo (fase F6, antes de 01/2027).

### Achados da F1 (2026-09-23)

- **XSD oficial v1.01 (pacote 20260209) tem defeito em `TSSerieDPS`**: padrão `^0{0,4}\d{1,5}$`; em regex XSD
  `^`/`$` são literais, então **nenhuma** série valida (nem as notas reais do Emissor Web). Cópia local em
  `backend/src/emissor/nfse/xsd/v1_01/` corrigida (ver `LEIAME.txt`). Pacote RTC de out/2025 é mais antigo; não usar.
- O grupo `IBSCBS` já existe (opcional) no XSD 1.01 → a F6 não exige troca de leiaute.
- **`TSCNPJ` no XSD 1.01 ainda é `[0-9]{14}`**: CNPJ alfanumérico de tomador não passa na validação local.
  O sistema aceita e valida o CNPJ alfanumérico no cadastro, mas a emissão será bloqueada com erro claro
  até a Sefin publicar XSD compatível.
- `TSString` (logradouro, bairro, número...) só aceita Latin-1 sem espaço nas pontas e sem quebra de linha →
  `limpar_texto()` normaliza aspas/travessões tipográficos e remove emoji. `xDescServ` aceita quebras de linha.
- **signxml**: com namespace padrão (sem prefixo `ds:`), o elemento `<Signature>` fica sem namespace na árvore
  em memória; `assinar()` reserializa para corrigir. Verificado que o signxml valida as assinaturas da Sefin
  em NFS-e reais (interoperabilidade de C14N/RSA-SHA256 confirmada).
- Assinatura da DPS: exc-c14n **sem** comentários (a Sefin assina a NFS-e com `#WithComments`; ambos válidos).

### Módulo fiscal (`backend/src/emissor/nfse/`)

| Arquivo | Responsabilidade |
|---|---|
| `constants.py` | `Ambiente` (tpAmb + URLs Sefin/ADN), namespaces, faixa de série |
| `models.py` | `ConfigFiscal`, `Tomador`, `Endereco`, `DpsInput`; `limpar_texto`, `formatar_decimal` |
| `documentos.py` | validação CPF/CNPJ (inclusive alfanumérico) e máscara |
| `certificate.py` | PFX → chave/cert, info (titular, CNPJ via OID 2.16.76.1.3.3), `ssl_context()` p/ mTLS |
| `dps_builder.py` | `build_dps`, `id_dps` (ordem do XSD, igual às notas do Emissor Web) |
| `signer.py` | `assinar` / `verificar` (signxml) |
| `xsd.py` | `validar_dps` / `validar_nfse` / `validar_pedido_evento` / `validar_evento` |
| `client.py` | `SefinClient`: `emitir`, `consultar_dps`, `consultar_nfse`, `registrar_evento`, `consultar_evento`; ADN: `distribuicao_dfe`, `eventos_nfse`; log de cada troca em `data/logs/sefin/` |
| `eventos.py` | pedido de cancelamento (`preparar_cancelamento`, `enviar_cancelamento` com a mesma lógica anti-duplicidade da emissão), `EventoDoc` (leitura de `<evento>`), tipos de evento e efeito na situação |
| `errors.py` | `SefinError` (`tipo`: validacao_local/rejeicao/autenticacao/indisponivel/ambiguo), mensagens + dicas |
| `emissao.py` | `preparar_dps` + `enviar_dps`: retentativa com a **mesma** DPS, verificação `/dps/{id}` antes de reenviar |
| `xml_reader.py` | `NFSeDoc`, `extrair_padroes` (nota mais recente), `extrair_tomador` |
| `codes.py` | descrições dos códigos (tpEmit, opSimpNac, tribISSQN...) e tabela IBGE de municípios |
| `diagnostico.py` | CLI de teste de certificado/mTLS |
| `../security.py` | Fernet para PFX/senha em repouso |

### DANFSe (`backend/src/emissor/nfse/danfse/`) — decisões da F2

- `render.py` desenha com ReportLab Canvas usando as coordenadas (cm) da tabela 2.4.5 da NT e a
  disposição do Anexo I (quando os dois divergem, vale o Anexo I — ex.: "Regime de Apuração pelo SN"
  na coluna 2). `gerar_danfse(xml, cancelada=, substituida=)` → bytes do PDF.
- Fontes: Arial/Microsoft Sans Serif **do sistema operacional** (não redistribuídas); fallback
  Liberation Sans/Arimo e, por último, Helvetica, com aviso no log (`fonts.py`, `EMISSOR_FONT_DIR`).
- Logo oficial NFS-e em `assets/` (baixada de gov.br). QR Code vetorial do próprio ReportLab; decodificação
  conferida com OpenCV.
- Prestador: dados cadastrais vêm de `infNFSe/emit` (a DPS só tem CNPJ/contato).
- Destinatário: sem grupo IBSCBS → "DESTINATÁRIO DA OPERAÇÃO NÃO IDENTIFICADO NA NFS-e"; com
  `indDest=0` → "O DESTINATÁRIO É O PRÓPRIO TOMADOR/ADQUIRENTE" (não se afirma nada fora do XML).
- Linhas "**" suprimidas quando vazias; linha PIS/COFINS só para competência ≤ 2026 (nota 6).
  O espaço liberado vai para "Descrição do Serviço". Canhoto (opcional) não é impresso.
- Totais aproximados: com `pTotTribSN` imprime "Federais: -; Estaduais: -; Municipais: -; Simples Nacional: X,XX%".
- Marca d'água (CANCELADA/SUBSTITUÍDA) desenhada antes do conteúdo para não cobrir o texto.
- Tabela IBGE de municípios em `backend/src/emissor/data/municipios_ibge.csv` (API IBGE, 5.571 municípios),
  usada no DANFSe e na busca de municípios do cadastro (`codes.py`). **Versionada** — o `.gitignore` ignora só
  `/data/` da raiz.

### API e emissão (F3)

Estados da emissão: `pendente → processando → autorizada | rejeitada | erro`; `pendente → nao_enviada`
(lote cancelado/interrompido); `autorizada → cancelada | substituida` (eventos, ver "Eventos" abaixo).
Lote: `em_andamento | concluido | interrompido | cancelado`.

- `rejeitada` (Sefin recusou ou validação local) **não** interrompe o lote; `erro` de certificado (403/TLS),
  Sefin indisponível, resposta ambígua ou inválida **interrompe** (restantes ficam `nao_enviada`).
- nDPS separado por ambiente (`proximo_ndps_homologacao` / `proximo_ndps_producao`), reservado só após
  a DPS ser montada/assinada/validada; XML assinado gravado em `data/dps/` **antes** do envio.
- Ao reiniciar, lotes `em_andamento` são retomados; emissão `processando` consulta `/dps/{id}` antes de reenviar.
- `POST /api/emissoes/{id}/verificar`: resolve emissões com erro de comunicação consultando a Sefin.
- Validação prévia (`POST /api/tabela/validar`): erros bloqueiam; avisos (sem endereço, CNPJ não ATIVO,
  mesma nota já autorizada no mês para o mesmo cliente e valor) só alertam.
- Arquivos: `data/xml|pdf/<homologacao|producao>/AAAA/MM/AAAAMMDD_NFSe-NNNN_<PRESTADOR>-para-<TOMADOR>_R<valor>.*`.
  PDF regenerado sob demanda se faltar.
- Erros HTTP sempre como `{"mensagem": str, "detalhes": [...]}`; 500 inclui `request_id` (mesmo do log).
  `SefinError` que chega à API vira 400 (rejeição/validação local) ou 502 (certificado, Sefin/ADN fora do ar) com
  `sefin` = `{tipo, resumo, mensagens[{codigo, descricao, complemento, dica}]}`; a tela mostra código e "Como resolver".
- Datas no SQLite: hora local sem fuso (`NaiveDatetime`; o SQLModel atual exige a anotação).
- Tabela: `PUT /api/tabela` recebe o estado completo (a grade salva após cada edição, com debounce).
- Importação de clientes por XML (`POST /api/clientes/importar-xmls`, XMLs soltos ou `.zip`): cliente novo recebe
  os dados da nota mais recente; cliente existente só tem os campos **vazios** completados; notas cujo tomador é o
  próprio prestador são ignoradas.

| Rotas | |
|---|---|
| `GET /api/status` | prontidão, ambiente, certificado, avisos |
| `POST /api/certificado` | upload PFX + senha (multipart) |
| `GET /api/onboarding/padroes`, `POST /api/onboarding/xmls`, `POST /api/onboarding/concluir` | onboarding |
| `GET/PUT /api/config`, `GET /api/config/opcoes`, `POST /api/config/testar-conexao` | configurações |
| `GET/PUT /api/tabela`, `POST /api/tabela/importar?modo=`, `GET /api/tabela/exportar?formato=`, `POST /api/tabela/validar` | tabela |
| `GET /api/clientes?q=`, `GET/PUT/DELETE /api/clientes/{doc}`, `POST /api/clientes/{doc}/consultar`, `POST /api/clientes/consultar-pendentes`, `POST /api/clientes/importar-xmls` | clientes |
| `GET /api/municipios?q=` | busca IBGE |
| `POST /api/lotes`, `GET /api/lotes`, `GET /api/lotes/{id}`, `POST /api/lotes/{id}/cancelar` | emissão em lote |
| `GET /api/emissoes?status&ambiente&de&ate&q&lote_id&pagina`, `GET /api/emissoes/{id}`, `.../xml`, `.../pdf`, `POST /api/emissoes/zip`, `POST /api/emissoes/{id}/verificar` | emissões |
| `POST /api/emissoes/{id}/cancelar` `{c_motivo, x_motivo}`, `POST /api/emissoes/{id}/situacao`, `GET /api/emissoes/{id}/eventos/{evento_id}/xml` | cancelamento e eventos |
| `GET /api/sincronizacao`, `POST /api/sincronizacao?forcar=` | situação das notas pelo ADN (segundo plano) |
| `POST /api/logs/cliente`, `GET /api/diagnostico` | logs do frontend e ZIP de diagnóstico |

### Frontend (F4)

- **Stack:** Vite 6 + React 18 (a Glide Data Grid 6 exige React ≤ 18) + TypeScript + TanStack Query. Node 20.11+
  (Vite 7 exigiria 20.19+). Fontes self-hosted via `@fontsource-variable` (funciona sem internet).
- **Identidade visual:** logo própria em `frontend/public/logo.svg` (folha de nota com canto dobrado e grade de
  planilha), cores azul `#0098D7`, preto `#020202`, fundo `#F9FDFE`; Archivo (largura expandida nos títulos) +
  Martian Mono para CNPJ, valores, números e chaves. Assinaturas: faixa de ambiente no topo (hachurada =
  homologação, preta = produção) e "fita" de progresso do lote (um segmento por nota). Produção exige digitar
  EMITIR para confirmar; botão de emitir fica preto em produção.
- **Arquivos:** `src/api.ts` (cliente + tipos + `ApiError` com `requestId`), `src/format.ts` (CNPJ/CPF, moeda,
  TSV), `src/ui.tsx` (toasts, diálogo, gaveta, `ErroCaixa`, `MensagensSefin`), `src/pages/*`
  (Onboarding, Emitir, EmissaoFluxo, Emissoes, Clientes, Configuracoes), `src/components/*`
  (FiscalForm, EditorNomeCliente, ImportarClientesXml).
- **Grade (Glide Data Grid) — armadilhas encontradas nos testes:**
  - Exige `<div id="portal">` no `index.html`, senão o editor de célula não abre.
  - Com `onCellsEdited` presente, a grade **não chama** `onCellEdited`; a edição em massa (digitar com faixa
    selecionada aplica a todas as células) está em `onCellsEdited` (edição única dentro de faixa > 1 célula).
  - A colagem nativa usa `navigator.clipboard.read()`, que só existe em HTTPS/localhost → **quebraria na rede
    local via HTTP**. Colagem própria: `keybindings={{paste:false}}` + listener de `paste` lendo `e.clipboardData`
    (`parseTSV` trata aspas/quebras do Excel). Colar além da última linha cria linhas.
  - Canvas: fontes precisam carregar antes do desenho (`document.fonts.ready` → remonta a grade).
- Tabela salva sozinha (debounce 500 ms, fila sequencial de PUTs); CNPJs novos são consultados automaticamente
  (uma vez por sessão) e preenchem o nome só se estiver vazio — **o nome da tabela prevalece** sobre o da Receita.
- `index.html` servido com `Cache-Control: no-cache` (atualizações chegam aos navegadores; `/assets` tem hash).
- Erros: servidor devolve `{mensagem, detalhes}`; tela mostra mensagem, lista de detalhes/linhas e "Código para
  suporte" (= `request_id` do log). Erros de JS/promises vão para `/api/logs/cliente`.
- Página HTML de erro da Sefin (IIS 403 com certificado não aceito) é convertida em texto (`texto_de_html`)
  com dica de correção. Fixture real em `backend/tests/fixtures/sefin_403_iis.html`.

### Verificação (F5, 2026-09-23)

Feito no Firefox (instância isolada) com certificado ICP-Brasil **falso** e XMLs reais de exemplo (fora do git):
- Onboarding completo: padrões e clientes importados dos XMLs, tabela montada.
- Grade: edição em massa (faixa → digitar → Enter), colar do Excel pelo clipboard real (TSV com aspas e
  quebra de linha, criando linhas), exclusão de linhas, CNPJ inválido em vermelho, consulta automática BrasilAPI.
- Validação prévia bloqueando linha com CNPJ inválido.
- **Sefin real (produção restrita)** com certificado falso: HTTP 403 (página IIS) → lote interrompido na 1ª nota,
  demais `nao_enviada`, mensagem clara com dica; "Testar conexão" mostra o mesmo diagnóstico.
- **Sefin simulada** (script de teste fora do repo que monta a NFS-e a partir da DPS enviada): notas autorizadas +
  1 rejeitada (E0312) no mesmo lote; aba Emissões, XML/PDF individuais, ZIP em massa e DANFSe conferidos.
- Layout conferido em 1440 px e 390 px.

Roteiro de teste em homologação (para quem instala):
1. `iniciar.bat` (ou `docker compose up -d`) no servidor; abrir `http://<servidor>:8000`.
2. Onboarding com o certificado real e XMLs de notas já emitidas; manter **Homologação**, série 1, próxima DPS 1.
3. Configurações → "Testar conexão" (deve dizer que a conexão e o certificado foram aceitos).
4. Emitir 1 ou 2 linhas; conferir PDF/XML na aba Emissões.
5. Se algo falhar: Configurações → "Baixar arquivo de diagnóstico" (tem as trocas com a Sefin, sem segredos).

Pendências conhecidas:
- Cancelamento e sincronização pelo ADN não testados contra a Sefin/ADN reais (ver Status).
- Substituição de NFS-e (DPS com `infDPS/subst`) e solicitação de análise fiscal (101103) — não implementadas;
  feitas no portal, aparecem aqui pela sincronização.
- Grupo IBS/CBS — obrigatório ao Simples em 01/2027 (F6).
- CNPJ alfanumérico de tomador bloqueado até a Sefin publicar XSD compatível. **Achado de 2026-10-09:** segundo o
  open-nfse, o pacote `esquemas-nfse-rtc-v1-01-20260727` (Produção Restrita, em produção desde 10/08/2026) já aceita
  CNPJ alfanumérico (`TSCNPJ` `[0-9A-Z]{14}`, Ids e chaves) e corrigiu o `TSSerieDPS`. Conferir no gov.br e atualizar
  `xsd/v1_01/` (não verificado na fonte oficial: gov.br inacessível do ambiente de desenvolvimento).
- Sem login (decisão de produto); usar `EMISSOR_IPS_PERMITIDOS` para restringir à rede local.
- Frontend sem testes automatizados (verificado manualmente no navegador); backend com testes pytest.

### Eventos: cancelamento e situação pelo ADN (F6, 2026-10-09)

Pesquisa (OpenAPI oficiais da Sefin e do ADN Contribuinte e regras do Anexo II, via open-nfse; XSD 1.01 local):
- **Cancelamento = evento 101101**, enviado pelo emitente para `POST {sefin}/nfse/{chave}/eventos` com
  `{"pedidoRegistroEventoXmlGZipB64": base64(gzip(pedRegEvento assinado))}` → 201 `{eventoXmlGZipB64, ...}`
  (o `<evento>` processado, assinado pela Sefin). Rejeição: 400 com `erro` **em lista** (o Swagger diz objeto).
- `pedRegEvento versao="1.01"`: `infPedReg Id="PRE"+chave(50)+tipo(6)` (59 caracteres, **sem** `nPedRegEvento` — o
  formato antigo de 62 caracteres é rejeitado com E1235), `tpAmb`, `verAplic`, `dhEvento` (com fuso, não posterior ao
  recebimento — E1843), `CNPJAutor` (= CNPJ do certificado — E0812), `chNFSe`, `e101101{xDesc "Cancelamento de
  NFS-e", cMotivo 1|2|9, xMotivo 15–255}`. Assinatura igual à da DPS (referência ao `Id` do `infPedReg`).
- Sem número de pedido: a Sefin deduplica por (chave, tipo). Em timeout/5xx ou E0840/E1805/E0802, consulta-se
  `GET {sefin}/nfse/{chave}/eventos/101101/1` antes de reenviar o **mesmo** pedido (como na emissão).
- Cancelamento e substituição são **terminais** (nenhum evento depois). 105102 (substituição) é gerado pelo sistema
  nacional ao receber uma DPS com `subst` — o contribuinte não o envia. Prazo/valor para cancelar são parametrizados
  pelo município (E0822/E0823/E0824/E0827) → dicas em `errors.py` apontam a análise fiscal no portal.
- A NFS-e não muda: `cStat` continua 100. O DANFSe indica a situação só pela marca d'água (NT 008, 2.5.1/2.5.2).
- **ADN Contribuinte** (`{adn}/contribuintes`, mesmo mTLS): `GET /DFe/{NSU}?lote=true` (até 50 documentos após o NSU:
  NFS-e e eventos em que o CNPJ aparece) e `GET /NFSe/{chave}/Eventos`. Respostas em PascalCase
  (`StatusProcessamento`, `LoteDFe[{NSU, ChaveAcesso, TipoDocumento, TipoEvento, ArquivoXml}]`); 404 **com corpo**
  = nada encontrado, 400 com corpo = rejeição; sem `StatusProcessamento` → resposta não veio do serviço.
  Sem limite de consulta documentado (na NF-e, consultar de novo em < 1 h após chegar ao fim gera "consumo indevido").

Implementação (`nfse/eventos.py`, `services/eventos.py`, tabelas `evento_nfse` e `sincronizacao_adn`):
- Cancelar: no ambiente em que a nota foi emitida, só `autorizada`; evento gravado ao lado do XML da nota
  (`..._evento-101101-001.xml`), status `cancelada`, PDF refeito com CANCELADA. E0840 → consulta os eventos no ADN e,
  se a nota já estava cancelada/substituída, atualiza e avisa.
- Efeito dos eventos: 101101/105104/305101 → `cancelada`; 105102 → `substituida`; os demais (manifestação do
  tomador, bloqueio...) só entram no histórico. Evento de chave que não é do sistema é ignorado.
- Sincronização (`Sincronizador`): cursor NSU por `ambiente:CNPJ`; pausa de 1 s entre páginas; para quando vem
  menos de 50 documentos ou nada novo. Disparada ao abrir a aba Emissões, **no máximo 1×/hora** (forçada pelo botão
  "Atualizar agora": intervalo mínimo de 2 min); thread em segundo plano. O log da troca guarda a resposta sem os XMLs
  embutidos (vão para `data/xml`). "Atualizar situação" na nota usa `GET /NFSe/{chave}/Eventos`.
- ZIP de XMLs inclui os eventos; notas canceladas/substituídas continuam baixáveis e saem do total autorizado.
- Frontend: `components/CancelarNotas.tsx` (uma ou várias notas, uma por vez; para na primeira falha que não seja
  rejeição; produção exige digitar CANCELAR). Diálogos (`.veu`, z-index 50) ficam acima da gaveta (41) — o diálogo
  de cancelamento abre a partir dela. Horários da sincronização vão com fuso (`-03:00`): "há X min" no navegador.

### Padrões que o onboarding extrai dos XMLs

`extrair_padroes` usa a nota mais recente (por `dhProc`); fone/e-mail vêm da nota mais recente que os tenha. Sem
XMLs, o CNPJ vem do certificado; o envio de um certificado de outro CNPJ gera aviso (`POST /api/certificado`).

| Campo | Exemplo | Observação |
|---|---|---|
| `prest/CNPJ` | | deve ser o mesmo do certificado |
| `prest/fone`, `prest/email` | | opcionais |
| `regTrib/opSimpNac`, `regApTribSN`, `regEspTrib` | 3 / 2 / 0 | ME/EPP optante do Simples |
| `cLocEmi`, `locPrest/cLocPrestacao` | código IBGE | município do prestador |
| `tpEmit` | 1 | Prestador |
| `cServ/cTribNac`, `cServ/cNBS` | 171901 / 113022100 | ex.: contabilidade |
| `tribMun/tribISSQN`, `tpRetISSQN` | 1 / 1 | operação tributável, ISS não retido |
| `piscofins/CST` | 00 | editável (notas antigas do Emissor Web usavam 99 + `tpRetPisCofins=0`) |
| `totTrib/pTotTribSN` | 11.08 | % aproximado do Simples (Lei 12.741) |
| Alíquota ISS | | definida pelo município (não vai na DPS) |
| Tomador | CNPJ/CPF, xNome, endereço nacional | vem do cadastro de clientes |

## Arquitetura

```
emissor-em-planilha/
├─ CLAUDE.md, README.md
├─ iniciar.bat               # Windows: instala/atualiza o que faltar e executa
├─ Dockerfile, docker-compose.yaml   # alternativa: Docker (dados em ./data)
├─ docs/                     # NT 008; img/ e video/ do README (dados fictícios)
├─ backend/                  # projeto uv (Python 3.12)
│  ├─ pyproject.toml
│  ├─ src/emissor/
│  │  ├─ __init__.py         # main(): uvicorn
│  │  ├─ config.py           # caminhos (data/), variáveis de ambiente, verAplic
│  │  ├─ db.py               # SQLite via SQLModel
│  │  ├─ logging_setup.py    # logs JSON rotativos + correlation id
│  │  ├─ security.py         # Fernet (PFX/senha em repouso)
│  │  ├─ data/               # municipios_ibge.csv (versionado)
│  │  ├─ nfse/               # núcleo fiscal (ver tabela acima) + xsd/ + danfse/
│  │  ├─ services/           # emissao (fila em lote), clientes (+BrasilAPI), tabela (import/export),
│  │  │                      # certificado, arquivos (nomes/pastas de XML e PDF)
│  │  └─ api/                # app.py + routes_config, routes_tabela, routes_emissoes
│  └─ tests/                 # pytest; certificado falso gerado em runtime; fixtures fictícias
├─ frontend/                 # Vite + React + TypeScript
└─ data/                     # (gitignored) emissor.db, cert criptografado, xml/, pdf/, logs/
```

### Backend
- **FastAPI + Uvicorn**, processo único servindo API e frontend estático (build do Vite).
  Bind em `0.0.0.0:8000` para acesso na rede local.
- **SQLite** (arquivo em `data/`) via SQLModel. Tabelas: `config` (padrões fiscais, ambiente,
  série, próximo nDPS), `certificado`, `clientes`, `linhas_tabela`, `lotes`, `emissoes`.
- **uv** gerencia Python (3.12 fixado em `.python-version`) e dependências (`uv.lock` versionado).
- **Fila de emissão:** um worker em thread de background processa as notas **sequencialmente**
  (evita rate-limit e mantém a numeração previsível). Progresso para o frontend por
  **polling** de `GET /api/lotes/{id}` (~1 s) — mais simples e robusto que SSE numa rede local.
- **Idempotência / numeração:** o `nDPS` é reservado em transação **antes** do envio e nunca
  reaproveitado. Em timeout/erro de rede/5xx, antes de reenviar consulta `GET /dps/{idDps}`;
  se a NFS-e já existe, recupera pela chave. Isso impede nota em duplicidade.
- **Datas:** `dhEmi` = agora em America/Sao_Paulo (com offset, ex. `-03:00`) menos uma pequena
  margem para relógio (evita "data de emissão posterior ao processamento"); `dCompet` = data de `dhEmi`.
- **`verAplic`** = `EmPlanilha_<versão>` (o XSD limita a 20 caracteres).
- **Armazenamento:** XML da NFS-e autorizada e PDF DANFSe salvos em `data/xml/...` e `data/pdf/...`
  (ver "API e emissão"). Download em massa gera ZIP sob demanda.
- **Clientes (tomadores):** cadastro próprio (CNPJ/CPF, razão social, endereço, IM, e-mail).
  Fontes: XMLs importados (onboarding ou tela Clientes) → BrasilAPI (`/api/cnpj/v1/{cnpj}`) para CNPJ novo →
  edição manual. Linha da tabela sem cadastro completo é sinalizada antes da emissão.
  - BrasilAPI: gratuita, sem chave, open source, **sem SLA** e sem limite de uso documentado. Os dados
    vêm do Minha Receita (dados abertos da Receita Federal, atualizados mensalmente).
    Fallback: `https://publica.cnpj.ws/cnpj/{cnpj}` (CNPJá público, 3 req/min).
  - Consulta só **uma vez por CNPJ novo**, com resultado salvo no cadastro, timeout curto e sem retentativas agressivas.
    **A emissão nunca depende da API externa**: se ela falhar, o usuário completa o endereço à mão.
  - Guardar só os campos usados na nota (razão social, endereço, e-mail, telefone); descartar
    o quadro societário (QSA) que vem na resposta.
- **CNPJ alfanumérico** (IN RFB 2.229/2024, emitido desde jul/2026): validação, máscara e
  armazenamento do CNPJ aceitam letras, não só dígitos.

### Logging (diagnóstico)
- Logs estruturados **JSON Lines** em `data/logs/emissor.jsonl` (rotação diária, 30 dias) +
  console legível.
- Todo request HTTP recebe `request_id`; toda emissão recebe `lote_id`/`emissao_id`.
- Para cada chamada à Sefin: URL, ambiente, método, status, tempo, corpo de resposta completo,
  XML da DPS (assinado) e resposta decodificada gravados também em `data/logs/sefin/<emissao_id>/`.
- **Nunca** logar senha do certificado, chave privada ou PFX.
- Erros do frontend são enviados para `/api/logs/cliente` e entram no mesmo log.
- Botão "Baixar arquivo de diagnóstico" (Configurações) gera ZIP com logs + config (sem segredos).

### Frontend
- **Vite + React + TypeScript**, TanStack Query para dados do servidor.
- Grade tipo Excel: **Glide Data Grid** (MIT, canvas, seleção em faixa, copiar/colar,
  alça de preenchimento, milhares de linhas).
- Telas: Onboarding (wizard) → Tabela de emissão → Emissões → Clientes → Configurações.
- **Ambiente sempre visível** (selo "HOMOLOGAÇÃO"/"PRODUÇÃO") e confirmação antes de emitir
  mostrando quantidade, total em R$ e ambiente.
- **Erros explícitos:** cada linha rejeitada mostra código Sefin, mensagem original, campo e
  uma explicação/sugestão em português; nenhum erro "genérico" sem detalhe.

### Segurança
- Certificado PFX e senha **criptografados em repouso** (Fernet); chave em `data/secret.key`
  (fora do git). PFX nunca é devolvido pela API; só metadados (titular, CNPJ, validade).
- **Sem login** (decisão de produto). Risco: qualquer máquina da rede local que acessar a
  página pode emitir notas com o certificado. Mitigação: lista de IPs permitidos
  (`EMISSOR_IPS_PERMITIDOS`). Não expor à internet.
- Aviso no app quando o certificado estiver a ≤ 30 dias do vencimento.

## Plano de implementação (fases)

- **F0 — Planejamento** ✅ pesquisa, decisões, repositório.
- **F1 — Núcleo fiscal** ✅: certificado, builder DPS 1.01, validação XSD, assinatura, cliente
  Sefin (homologação/produção), normalização de erros, idempotência, logging. Testes unitários
  (DPS gerada ≙ estrutura das notas do Emissor Web; assinatura verificável; XSD válido).
- **F2 — DANFSe NT 008** ✅: gerador reportlab; testes renderizando NFS-e de exemplo.
- **F3 — API** ✅: onboarding, config, tabela persistente, clientes/BrasilAPI, import/export,
  fila de emissão, emissões, downloads individuais e ZIP.
- **F4 — Frontend** ✅: design system, onboarding, grade Excel, progresso de emissão, emissões,
  clientes, configurações, tratamento de erros.
- **F5 — Verificação** (em andamento): testes ponta a ponta no navegador; testes manuais em
  homologação; ajustes; liberação de produção.
- **F6 — Futuro (em andamento):** ✅ cancelamento de NFS-e e situação pelo ADN (2026-10-09); grupo IBS/CBS
  (obrigatório ao Simples em 01/2027); importar notas emitidas fora do sistema (a distribuição por NSU já é lida —
  hoje só os eventos de notas do sistema são aplicados).

## Convenções

- Git: commits ao fim de cada unidade coerente (fase ou sub-parte testada), mensagens em
  português no imperativo.
- **Nenhum dado real no repositório:** `temp/` (XMLs reais para testes locais) e `data/` nunca vão para o git;
  fixtures e testes usam só dados fictícios (prestador `11.444.777/0001-61`, tomador `11.222.333/0001-81`,
  CPF `529.982.247-25`, município 4205407).
- Código e identificadores em inglês quando genéricos; termos fiscais mantêm o nome do leiaute
  (`nDPS`, `cTribNac`, `tpRetISSQN`...). Textos da interface em português.
- **Execução: só `iniciar.bat` ou `docker compose up -d`** (decisão de produto: um ponto de entrada por plataforma).
  - `iniciar.bat` (Windows): exige `uv` e Node; faz `npm ci` + `npm run build` quando `frontend/dist` não existe ou
    algum arquivo de `frontend/src`, `public`, `index.html`, `vite.config.ts` ou `package-lock.json` é mais novo que
    `dist/index.html` (git pull/ZIP novo); depois `uv sync --locked --no-dev` e `uv run emissor`. Arquivo com CRLF.
  - Docker: build em 2 estágios (node:22-alpine → python:3.12-slim + `fonts-liberation` + `tzdata`, uv só montado no
    build); `EMISSOR_DATA_DIR=/data`, `TZ=America/Sao_Paulo`; o compose monta `./data`, usa `pull_policy: build`
    (todo `up` reconstrói com cache) e tem linhas comentadas para usar `C:/Windows/Fonts` no DANFSe. No Docker Desktop
    o IP de origem é o do Docker, então `EMISSOR_IPS_PERMITIDOS` não serve para filtrar máquinas da rede.
  - O backend serve `frontend/dist`.
- **Desenvolvimento do frontend:** `cd frontend && npm run dev` (Vite em :5173 com proxy de `/api` para :8000;
  `EMISSOR_API=http://127.0.0.1:8765 npm run dev` aponta para outra instância);
  `npx tsc --noEmit -p tsconfig.json` para checar tipos; `npm run build` gera `frontend/dist`.
- Rodar: `cd backend && uv run emissor` → http://<servidor>:8000 (API em `/api`, docs em `/docs`).
  Variáveis: `EMISSOR_HOST`, `EMISSOR_PORT`, `EMISSOR_DATA_DIR`, `EMISSOR_IPS_PERMITIDOS` (CIDRs separados
  por vírgula; vazio = todos), `EMISSOR_FONT_DIR`.
- Testes: `cd backend && uv run pytest -q` · Lint/format: `uv run ruff check src tests && uv run ruff format src tests`.
- Testes usam certificado ICP-Brasil **falso** gerado em runtime (`tests/conftest.py`) e a fixture
  fictícia `tests/fixtures/nfse_exemplo.xml` (dados trocados a partir de uma nota real; a assinatura da Sefin
  ficou só como estrutura e não confere); testes com `temp/*.xml` reais são pulados se a pasta não existir.
- Diagnóstico de certificado + mTLS (somente leitura, não emite):
  `cd backend && uv run python -m emissor.nfse.diagnostico --pfx caminho.pfx [--producao]`
  (HTTP 404 na DPS fictícia = conexão e certificado aceitos).

## Registro de decisões

| Data | Decisão | Motivo |
|---|---|---|
| 2026-09-23 | Módulo fiscal próprio (lxml/signxml/httpx/cryptography) em vez de PyNFe/nfelib/pynfse-nacional | Nenhuma lib cobre DPS 1.01 + NT 008 com licença permissiva e maturidade |
| 2026-09-23 | DANFSe gerado localmente (reportlab) conforme NT 008 v1.02 | API oficial de DANFSe suspensa em 03/08/2026 |
| 2026-09-23 | Série da DPS na faixa de API (padrão 1) | Série 70000 é exclusiva do Emissor Web (E0010) |
| 2026-09-23 | FastAPI + SQLite + worker sequencial | Servidor único na rede local, simples de operar |
| 2026-09-23 | Sem login | Decisão de produto; risco documentado em Segurança |
| 2026-09-23 | Cadastro de clientes com BrasilAPI | Tabela só tem nome/CNPJ; as notas incluem endereço |
| 2026-09-23 | BrasilAPI + fallback CNPJá, só no cadastro, nunca bloqueando a emissão | Gratuita e sem SLA: tratar como conveniência |
| 2026-09-23 | Emitir sem grupo IBS/CBS por enquanto | Simples Nacional obrigado só a partir de 01/2027 |
| 2026-09-23 | XSD local corrigido (TSSerieDPS) | Defeito no XSD oficial rejeita qualquer série |
| 2026-09-23 | Retentativa reenviando a mesma DPS (mesmo Id) após checar `/dps/{id}` | Evita nota duplicada em timeout/5xx |
| 2026-09-23 | Polling em vez de SSE para progresso do lote | Simplicidade/robustez na rede local |
| 2026-09-23 | Rejeição não interrompe lote; falha de certificado/Sefin interrompe | Evita dezenas de erros repetidos e envios inúteis |
| 2026-09-23 | Colagem própria na grade (clipboardData) | API de clipboard indisponível em HTTP na rede local |
| 2026-09-23 | Glide Data Grid para a tabela | Seleção em faixa e colar do Excel sem licença comercial |
| 2026-10-07 | Projeto publicado como **Emissor em Planilha**: nome e logo novos, histórico git novo, fixtures com dados fictícios | Abrir o código sem dados do escritório de origem nem de clientes |
| 2026-10-07 | `verAplic` = `EmPlanilha_<versão>` | "EmissorEmPlanilha_0.1.0" passaria do limite de 20 caracteres do XSD |
| 2026-10-08 | Rodar só por `iniciar.bat` (instala, atualiza e gera a página sozinho) ou `docker-compose.yaml`; `atualizar.bat` removido | Um único ponto de entrada por plataforma; atualizar = baixar o código e iniciar de novo |
| 2026-10-08 | Imagens e vídeos do README em `docs/img` e `docs/video`, gravados com dados fictícios, certificado falso e Sefin/BrasilAPI simuladas (Chrome headless + screencast CDP + ffmpeg, scripts fora do repo) | Mostrar o fluxo real sem dados de clientes; GIF para o README (vídeo `<video>` só funciona com anexos do GitHub) e MP4 para download |
| 2026-10-08 | Publicação no GitHub pela conta `iurijw`; autor dos commits `Iuri JW <iuriwissmann@gmail.com>` | Pedido do mantenedor |
| 2026-10-09 | Cancelamento pelo evento 101101 com retentativa do **mesmo** pedido e consulta do evento antes de reenviar | Mesma garantia anti-duplicidade da emissão; a Sefin deduplica por (chave, tipo) |
| 2026-10-09 | Situação das notas pela distribuição do ADN por NSU, ao abrir a aba Emissões e no máximo 1×/hora | Pega cancelamentos feitos no portal/prefeitura com poucas consultas; sem limite documentado, segue a prática da NF-e |
| 2026-10-09 | Eventos que não cancelam (manifestação, bloqueio) só ficam no histórico da nota | Não mudam a validade da nota |
