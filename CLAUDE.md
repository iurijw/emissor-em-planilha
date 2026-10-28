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
- **F6 (2026-10-11):** grupo IBS/CBS na DPS, tributações predefinidas, abas da tabela (com tributação própria e
  clientes exclusivos) e nota avulsa; ver "Tributações, abas e nota avulsa". Testado com Sefin simulada; validar o
  grupo IBS/CBS em Produção Restrita.
- **F6 (2026-10-12):** alíquotas e retenções na tributação (ISS retido, PIS/COFINS, IRRF/CSLL/INSS, totais
  aproximados do não optante) e aviso da alíquota do ISS retido na emissão; ver "Alíquotas e retenções". Testado com
  Sefin simulada; validar em Produção Restrita (principalmente E0621 e o `tpRetPisCofins` 4–9 com CSLL).
- **F6 (2026-10-12):** configurações reorganizadas: "Dados da empresa" (prestador e regime) separada de
  "Tributações" (a padrão + as outras numa lista só); retenções federais só aparecem quando marcadas; o onboarding
  tem os passos "Dados da empresa" e "Tributação padrão". Só tela: a tributação padrão continua em `config.fiscal`.
- **F6 (2026-10-27):** tela de tributação na ordem e com os nomes do Emissor Nacional (as retenções federais voltaram a
  ficar sempre à vista, como no portal), busca do serviço na lista nacional com NBS/cClassTrib do Anexo VIII, código
  municipal só com 3 dígitos e consulta do serviço na prefeitura (E0312); ver "Código do serviço e tela de tributação".
  A consulta à prefeitura **não foi conferida no ADN real** (o formato da rota não tem documentação oficial): validar
  com um certificado real antes de confiar nela.
- **Leiaute da NT 009 na Produção Restrita (2026-10-22):** desde 20/10/2026 a Produção Restrita usa o pacote
  `esquemas_prod_restrita_20261020` (NT 009). A DPS de homologação sai nesse leiaute (XSD em `nfse/xsd/v1_01_nt009/`)
  e a de produção continua no anterior (`nfse/xsd/v1_01/`) até 30/11/2026. A Produção passa **sozinha** à NT 009 em
  01/12/2026 00:00 (comunicado do CGNFS-e de 20/10/2026): `leiaute(ambiente, quando)` em `nfse/constants.py` decide pelo
  `dhEmi` da DPS (`INICIO_NT009_PRODUCAO`). Antes de 01/12, conferir o pacote de produção contra `v1_01_nt009`.
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
- Login: **sim** desde 2026-10-10 (antes era "não"): exigido pela hospedagem paga (uma instância por cliente na
  internet); ver "Autenticação".
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
  obrigatório em **jan/2027**, e a ausência não rejeita até 31/12/2026 (Ato Conjunto RFB/CGIBS 4/2026; orientação
  CGNFS-e de 07/08/2026: destaque escalonado 01/10/2026, 01/12/2026 e 01/01/2027).
  → Desde 2026-10-11 o grupo é **opcional por tributação** (`ConfigFiscal.ibscbs`, desligado por padrão; o onboarding
  liga se a nota mais recente o tiver). Ver "Tributações, abas e nota avulsa".

### Achados da F1 (2026-09-23)

- **XSD oficial v1.01 (pacote 20260209) tem defeito em `TSSerieDPS`**: padrão `^0{0,4}\d{1,5}$`; em regex XSD
  `^`/`$` são literais, então **nenhuma** série valida (nem as notas reais do Emissor Web). Corrigido pela Sefin no
  pacote `esquemas-nfse-rtc-v1-01-20260727` (ver abaixo).
- **XSD local = pacote `esquemas-nfse-rtc-v1-01-20260727`** (página "Produção Restrita" do gov.br; o CNPJ alfanumérico
  entrou em produção em 10/08/2026, página "Atualizações e Implantações"), conferido no gov.br em 2026-10-11:
  `TSCNPJ` `[0-9A-Z]{14}`, Ids e chaves alfanuméricos, `TSSerieDPS` `[0-9]{1,4}|[0-8][0-9]{4}`, descrições não podem
  ser só espaços. Único patch local: `TSChaveNFSe` (o oficial põe as letras nas posições 7-20; o CNPJ fica em 10-23),
  ver `LEIAME.txt`. A estrutura do grupo `IBSCBS` não mudou em relação ao pacote 20260209.
- O grupo `IBSCBS` já existe (opcional) no XSD 1.01 → a F6 não exige troca de leiaute.
- CNPJ alfanumérico (tomador e prestador) liberado na emissão e no cancelamento desde 2026-10-11 (antes era bloqueado
  porque o XSD 20260209 só aceitava `[0-9]{14}`).
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
| `xsd.py` | `validar_dps` / `validar_nfse` / `validar_pedido_evento` / `validar_evento` (leiaute pelo `tpAmb` do documento) |
| `client.py` | `SefinClient`: `emitir`, `consultar_dps`, `consultar_nfse`, `registrar_evento`, `consultar_evento`; ADN: `distribuicao_dfe`, `eventos_nfse`, `aliquota_municipal` (parametrização); log de cada troca em `data/logs/sefin/` |
| `eventos.py` | pedido de cancelamento (`preparar_cancelamento`, `enviar_cancelamento` com a mesma lógica anti-duplicidade da emissão), `EventoDoc` (leitura de `<evento>`), tipos de evento e efeito na situação |
| `errors.py` | `SefinError` (`tipo`: validacao_local/rejeicao/autenticacao/indisponivel/ambiguo), mensagens + dicas |
| `emissao.py` | `preparar_dps` + `enviar_dps`: retentativa com a **mesma** DPS, verificação `/dps/{id}` antes de reenviar |
| `xml_reader.py` | `NFSeDoc`, `extrair_padroes` (nota mais recente), `extrair_tomador` |
| `codes.py` | descrições dos códigos (tpEmit, opSimpNac, tribISSQN...), tabela IBGE de municípios e lista nacional de serviços (`servico`, `buscar_servicos`, classificações do IBS/CBS) |
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
- **Nota recuperada só se for desta DPS** (2026-10-27): o Id da DPS é só CNPJ + município + série + número, então outro
  sistema com o mesmo CNPJ e série gera o mesmo Id para outra nota (E0014). `conferir_mesma_dps` compara `dhEmi`, tomador
  e valor da NFS-e achada em `/dps/{id}` com os da DPS enviada; se diferem, `NumeracaoEmUso`: a emissão fica `rejeitada`
  (E0014 com número/data/tomador da nota existente e a dica de trocar a série ou a próxima DPS) e o lote é interrompido.
  Até a 0.6.1 a nota alheia era adotada como emitida (visto numa instalação antiga em 27/10/2026).
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
| `GET /api/servicos?q=`, `GET /api/servicos/{codigo}`, `POST /api/config/conferir-servico` | lista nacional de serviços e consulta do serviço na prefeitura |
| `GET/POST /api/tributacoes`, `PUT/DELETE /api/tributacoes/{id}` (escrita só admin) | tributações predefinidas |
| `POST /api/tributacoes/aliquota-iss` `{tributacao_id \| null, p_aliq_iss}` (qualquer usuário, auditado) | alíquota do ISS retido pelo aviso da emissão |
| `GET/POST /api/abas`, `PUT/DELETE /api/abas/{id}`; `aba_id` em `GET/PUT /api/tabela`, importar e exportar | abas da tabela |
| `POST /api/avulsa/validar`, `POST /api/avulsa` | nota avulsa |
| `POST /api/lotes`, `GET /api/lotes`, `GET /api/lotes/{id}`, `POST /api/lotes/{id}/cancelar` | emissão em lote |
| `GET /api/emissoes/relatorio?formato=xlsx\|csv` (mesmos filtros da lista) | planilha das notas filtradas |
| `GET /api/emissoes?status&ambiente&de&ate&q&lote_id&pagina`, `GET /api/emissoes/{id}`, `.../xml`, `.../pdf`, `POST /api/emissoes/zip`, `POST /api/emissoes/{id}/verificar` | emissões |
| `POST /api/emissoes/{id}/cancelar` `{c_motivo, x_motivo}`, `POST /api/emissoes/{id}/situacao`, `GET /api/emissoes/{id}/eventos/{evento_id}/xml` | cancelamento e eventos |
| `GET /api/sincronizacao`, `POST /api/sincronizacao?forcar=` | situação das notas pelo ADN (segundo plano) |
| `POST /api/logs/cliente`, `GET /api/diagnostico` | logs do frontend e ZIP de diagnóstico |
| `GET /api/assinatura`, `POST /api/assinatura/confirmar\|recusar` (hospedagem: aceite no fim do teste) | assinatura |
| `GET /api/saude` (sem login), `GET /api/auth/sessao`, `POST /api/auth/entrar\|sair\|esqueci\|primeiro-acesso\|convite\|convite/ver` | acesso |
| `GET /api/auth/conta`, `POST /api/auth/senha`, `POST /api/auth/2fa/iniciar\|ativar\|desativar` | conta do usuário |
| `GET/POST /api/usuarios`, `PUT /api/usuarios/{id}`, `POST /api/usuarios/{id}/redefinir`, `GET /api/auditoria` | administrador |

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
- **Aviso "ligando o sistema"** (`public/sw.js`, 2026-10-13): service worker registrado só no build de produção em
  HTTPS/localhost. Se a abertura da página não responde em 1,2 s (ou volta 5xx/sem conexão), mostra uma página própria
  que consulta `/api/saude` e recarrega sozinha. Não guarda nada do app (a página sempre vem do servidor); 60 s depois
  de `/api/saude` responder, as aberturas vão direto ao servidor. Na rede local por HTTP não faz nada. `sw.js` sai com
  `no-cache`. Para a máquina da hospedagem acordar, que com autostop `suspend` leva < 1 s (≈ 6 s ligando do zero).
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
- Grupo IBS/CBS enviado só no caso simples (ver "Tributações, abas e nota avulsa"); validar em Produção Restrita
  e conferir o `cClassTrib` certo para optantes do Simples com a contabilidade.
- NT 009: implantada na Produção Restrita em 20/10/2026 (pacote `esquemas_prod_restrita_20261020`); em Produção ainda
  sem data. O que o pacote mudou e o sistema já trata na homologação: `finNFSe` obrigatório em `infDPS` (sai do grupo
  IBSCBS), `CST`/`cClassTrib` em `IBSCBS/valores/trib`, `xDescServ` no fim de `cServ`, `vDedRed`/`gReeRepRes` →
  `vAjusteBC` (e `vCalcDR`/`vCalcReeRepRes` → `vCalcAjusteBCISSQN`/`vCalcAjusteBCIBSCBS` na NFS-e). `indDest`/`dest`
  continuaram no grupo IBSCBS, e `regApIBSCBSSN`, `cAtvSN` e `opSimpNac=4` ainda não estão no XSD. Produção em
  01/12/2026 (troca automática, ver Status); conferir antes o pacote de produção contra `v1_01_nt009`.
- Consulta do serviço na prefeitura (`/parametrizacao`) só testada contra o ADN simulado; ver "Código do serviço e
  tela de tributação".
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

### Tributações, abas e nota avulsa (F6, 2026-10-11)

**IBS/CBS** — base: XSD 1.01 local (`TCRTCInfoIBSCBS`) e o Anexo I (RN DPS_NFS-e v1.01 2026-02-09) e Anexo C
(IndOp v1.01 2026-01-22), conferidos no gov.br em 2026-10-11 (regras E0322, E0901, E0903/E0904, E0910, E0928/E0932,
E0958, E0959 e E0964 batem com a implementação). A DPS leva `finNFSe=0`, `indFinal` (opcional;
a NT 005 previa retirar e a NT 009 o reinseriu), `cIndOp`, `indDest=0` e `valores/trib/gIBSCBS/{CST, cClassTrib}`, depois de
`valores`. Valores/alíquotas não vão na DPS: a Sefin calcula e devolve em `infNFSe/IBSCBS` (o DANFSe já os imprime).
- CST = 3 primeiros dígitos do `cClassTrib` (E0959), então só o `cClassTrib` é pedido. Sugestão: `000001`.
  O Anexo VIII (correlação item LC 116 × NBS × cIndOp × cClassTrib v1.01, página "RTC") sugere outro código para
  alguns serviços: ex. contabilidade 17.19 / NBS 1.1302.21.00 → cIndOp `100301` e cClassTrib `200052` (profissões
  intelectuais, redução de 30%). A escolha é da contabilidade de cada cliente; o padrão continua `000001`.
- `cIndOp`: só os 13 códigos de `codes.C_IND_OP` (destinatário = tomador, tomador no Brasil, sem grupos de imóvel,
  evento ou transporte). Padrão `100301` (demais serviços, onerosa: domicílio do adquirente).
- Bloqueios locais com mensagem: sem `cNBS` (E0322); `cTribNac` 25.05/15.09/17.12/10.05 exige `tpOper` (E0903),
  que não é enviado. Fora do escopo: `dest`, `imovel`, `gRefNFSe`, `tpEnteGov`, `gReeRepRes`, `gTribRegular`, `gDif`,
  `cCredPres` e a NT 009 (`gTribSN`, ainda sem cronograma).

**Tributação** = os `CAMPOS_TRIBUTACAO` de `ConfigFiscal` (local da prestação, cTribNac/cTribMun/cNBS, ISSQN,
PIS/COFINS, pTotTribSN, IBS/CBS). Prestador, regime e tpEmit vêm sempre das configurações. Tabela `tributacao`
(JSON completo, partindo dos padrões); só admin cria/altera/exclui; não exclui se alguma aba usa.
- `aba`: nome, ordem, `tributacao_id` (null = padrões das configurações), `clientes_json`. `garantir_abas` cria a aba
  "Notas" e adota linhas sem aba (bancos antigos). `PUT /api/tabela?aba_id=` substitui só as linhas daquela aba.
  Qualquer usuário logado cria/edita/exclui abas (auditado); a última aba não pode ser excluída.
- **Clientes exclusivos** (segregação): linha de outro cliente na aba → erro; o cliente numa aba que não é a dele →
  só aviso.
- `criar_lote`/`criar_avulsa` copiam a tributação para `emissao.tributacao_nome/tributacao_json`; o worker aplica com
  `aplicar_tributacao` (mudar a tributação depois não afeta notas na fila). Sem cópia = padrões do momento do envio.
- **Nota avulsa**: lote de 1 com `linha_id` nulo, mesma fila, numeração e validação da tabela (linha transitória).
  Tomador (2026-10-23): CNPJ válido sem cadastro é buscado na Receita na hora (`/api/clientes/consultar-pendentes`, o
  mesmo da tabela), que cadastra o cliente com endereço e preenche o nome vazio; CNPJ do cadastro com nome vazio mostra
  o nome do cadastro; só o nome → ao sair do campo, um cadastro com o mesmo nome (sem acento/caixa) dá o CNPJ, senão a
  tela pede o CNPJ/CPF (sem ele não emite). CPF sem cadastro sai sem endereço.
  Tributação: padrão, predefinida (`tributacao_id`) ou só desta nota (`tributacao`, validada como completa). Operador
  pode usar as três (auditado em `nota_avulsa`).
- **Migração mínima** (`db._acrescentar_colunas`): colunas novas de tabelas existentes entram com `ALTER TABLE ADD
  COLUMN` (sempre aceitando nulo). Antes disso não havia como acrescentar colunas.
- Frontend: abas no rodapé da grade (`components/Abas.tsx`, selo da tributação em Martian Mono), editor de aba,
  `components/NotaAvulsa.tsx` (gaveta + `EmissaoFluxo` com `avulsa`), `CamposTributacao` em `FiscalForm.tsx`,
  Configurações → Tributações. Ao trocar de aba, a edição pendente (debounce) é salva na aba em que foi feita.

### Alíquotas e retenções (F6, 2026-10-12)

Base: Anexo I v1.01 (2026-02-09), abas LEIAUTE e RN DPS_NFS-e, conferido no gov.br em 2026-10-11 (os campos não
mudam no Anexo VI v1.04.01 da NT 009). Estudo: `/mnt/project-files/estudos/aliquotas-e-retencoes.md`.
- Campos novos de tributação (`CAMPOS_PERCENTUAIS`, texto "2.50"): `p_aliq_iss` (+ `p_aliq_iss_atualizada_em`),
  `p_aliq_pis`, `p_aliq_cofins`, `p_ret_irrf`, `p_ret_csll`, `p_ret_cp`, `p_tot_trib_fed/est/mun`.
  `nfse/tributos.py` converte em R$ por nota (arredondamento meio para cima); base do PIS/COFINS = valor do serviço.
- **`pAliq` do ISS só vai quando a Sefin exige** (`iss_exige_aliquota`): ME/EPP (`opSimpNac 3`) com ISS pelo Simples
  (`regApTribSN 1`) e ISS retido — E0621, faixa 1,80–5,00%. Nos demais casos a alíquota é a do município e informá-la
  rejeita (E0617/E0625/E0635); município de incidência fora do Sistema Nacional não é tratado.
- PIS/COFINS podem ir só informados (sem retenção); só os retidos (`tpRetPisCofins`) saem do líquido. IRRF/CSLL/CP só
  existem como valor retido (sempre descontados). CSLL obrigatória/proibida conforme `RETENCOES_PIS_COFINS` (E0720/E0724;
  "1" exige CSLL pela regra literal da E0724).
- Totais aproximados: não optante → `pTotTrib` (fed/est/mun, E0713); ME/EPP → `pTotTribSN` obrigatório (E0712); MEI →
  `indTotTrib 0`.
- Bloqueios locais: retido pelo intermediário (o sistema não envia o grupo), MEI/regime especial com retenção
  (E0583/E0588), retenção fora de operação tributável (E0580), ISS retido sem endereço do tomador (E0237, erro na
  validação), retenções acima do valor (E0428).
- **Aviso na emissão:** `/tabela/validar` e `/avulsa/validar` devolvem `aliquotas_iss` (uma por tributação que exige a
  alíquota). O diálogo pergunta **toda vez** "manter ou alterar" (destaque quando a última mudança foi em outro mês);
  alterar chama `POST /api/tributacoes/aliquota-iss` (o operador pode; é o único campo de tributação que ele altera) ou,
  na tributação "só desta nota", muda só o corpo da avulsa. Sem alíquota, `criar_lote`/`criar_avulsa` recusam.
- Onboarding: alíquotas e retenções da nota mais recente viram percentuais (`_padroes_aliquotas`).
- CST do PIS/COFINS: os 34 códigos do XSD 1.01 (`TSTipoCST`, 00 a 99, com as descrições do XSD) em
  `codes.CST_PIS_COFINS`. Até 2026-10-12 faltavam 53–56, 60–67, 70–75, 98 e 99 (lista digitada à mão na F1).
  `test_listas_de_codigos_iguais_ao_xsd` compara cada lista de `codes.py` com o tipo enumerado do XSD (lista nova →
  acrescentar lá; `C_IND_OP` é um recorte proposital do Anexo C).

### Variáveis na descrição e planilha das notas (2026-10-23)

- **Variáveis** (`services/variaveis.py`): `{mes}`, `{mes_numero}`, `{ano}`, `{competencia}`, `{mes_anterior}`,
  `{mes_anterior_numero}`, `{ano_mes_anterior}`, `{competencia_anterior}`, `{data}`; nome sem acento e sem diferença de
  caixa, mas a caixa escrita vale para o valor (`{Mes}` → Outubro, `{MES}` → OUTUBRO). Trocadas em `_preparar` pela data
  do `dhEmi`; a emissão grava o texto trocado em `emissao.descricao` e a linha da tabela continua com as variáveis.
  Variável desconhecida é **erro** na validação (texto entre chaves que não é variável também). A lista vai em
  `GET /api/config/opcoes` (`variaveis_descricao`, com o exemplo de hoje); a tela repete a troca para a prévia
  (`components/Variaveis.tsx`), então mudar a regra de caixa exige mudar os dois.
- **Planilha das notas** (`services/relatorio.py`, `GET /api/emissoes/relatorio?formato=xlsx|csv` com os filtros da
  lista; botão "Exportar notas (Excel)"): uma linha por nota, valores lidos do XML autorizado (`vBC`, `pAliqAplic`,
  `vISSQN`, `vTotalRet`, `vLiq`, retenções federais; PIS/COFINS só quando retidos, por `RETENCOES_PIS_COFINS`);
  canceladas/substituídas em cinza. Até 20.000 notas. Um resumo por competência foi feito e retirado a pedido do Iuri.

### Código do serviço e tela de tributação (2026-10-27)

Motivo: notas recusadas com **E0312** (o município de incidência não administra o `cTribNac`) e código municipal
digitado com o nacional junto (`080101003`), que o XSD recusa (`cTribMun` = 3 dígitos).
- **Lista nacional de serviços** (`data/servicos_nacionais.json`, 338 códigos): gerada por
  `backend/scripts/servicos_nacionais.py` a partir do Anexo B (lista de serviços e NBS), do Anexo VIII (NBS, `cIndOp` e
  `cClassTrib` sugeridos por serviço) e da tabela de cClassTrib do Portal da Conformidade Fácil (`--classtrib`, JSON).
  Versões e data da consulta ficam em `fontes`. **Regerar quando o Anexo B ou o VIII mudarem** (o mesmo monitor da
  ferramenta de códigos da hospedagem avisa). `codes.buscar_servicos`: por código (prefixo, com ou sem pontos), por
  NBS (9 dígitos) ou por palavras (sem acento/caixa, todas presentes; descrição do serviço antes da do NBS).
- **`cTribMun`**: `normalizar` tira pontos/espaços; 9 dígitos que começam com o `cTribNac` viram os 3 últimos;
  `coerencia` recusa outro tamanho (mensagem própria para o caso nacional + municipal). A tela faz a mesma troca.
- **Consulta à prefeitura** (`services/parametros.py`, `SefinClient.aliquota_municipal`): `GET {adn}/parametrizacao/
  {cMun}/{cTribNac}/{AAAA-MM-DD}/aliquota` com o mTLS do certificado (só leitura); sem achar, tenta o formato
  alternativo (`17.19.01.000`, `MM-DD-AAAA`). Encontrado = alguma alíquota vigente na competência; "não encontrado" só
  com 404/200 que diga que não há cadastro (`mensagem`/`aliquotas` vazio); 401/403/429/5xx viram `SefinError`; o resto
  é "não conferido". **Formato tirado das bibliotecas de referência, sem documentação oficial nem teste no ADN real.**
  Município de incidência = o do prestador quando o local da prestação é o mesmo (ou vazio); serviços com ISS no
  domicílio do tomador (subitens 4.22, 4.23, 5.09, 10.04, 15.01, 15.09) e local em outro município não são conferidos.
  Cache em memória por (ambiente, município, serviço, mês): 6 h para respostas, 5 min para falhas. **Nunca bloqueia**:
  a tela de tributação mostra o resultado (e a alíquota da prefeitura); `/tabela/validar` e `/avulsa/validar`
  devolvem `avisos` só para "não encontrado" (no máximo 10 s de consultas por validação), que exigem ciência no
  diálogo de emissão. Os testes desligam a consulta (`parametros.ATIVA` no conftest); os de ponta a ponta ligam, com a
  rota simulada em `sefin_simulada.py` (`servicos_nao_administrados` também faz a Sefin simulada recusar com E0312).
- **Tela de tributação** (`CamposTributacao` em `FiscalForm.tsx`, usada em Configurações → Tributações, no
  onboarding e na nota avulsa "Só para esta nota"): mesma ordem e nomes do Emissor Nacional (telas Serviço e Valores):
  IBS/CBS sim/não; local, "Código de Tributação Nacional" (busca), "Código de Tributação Municipal" (com o código
  completo `17.19.01.000`), município de incidência, imunidade/exportação/não incidência sim/não, NBS (lista do Anexo
  VIII para o serviço ou outro código; ao trocar o serviço, um NBS que não é dele é trocado pelo único do serviço
  novo ou fica para escolher), indicador da operação; retenção do ISSQN sim/não e alíquota (a da prefeitura, só
  mostrada, ou a do Simples quando a Sefin exige); PIS/COFINS, tipo de retenção, IRRF, CSLL e CP sempre à vista
  (substitui o "o tomador retém" de 2026-10-12); IBS/CBS com CST e cClassTrib em listas (sugestões do Anexo VIII
  primeiro); valor aproximado dos tributos; "Prévia dos valores da NFS-e" numa nota de exemplo. Nada mudou nos dados
  (`DadosTributacao`), só a tela. A tabela e o resto da nota avulsa ficaram como estavam (escolha do Iuri).

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
├─ .github/workflows/imagem.yml      # publica a imagem em ghcr.io (usada pela hospedagem); PR → :pr-N e :dev
├─ .github/workflows/versao.yml      # "Publicar versão" (manual): release vX.Y.Z + imagem :X.Y.Z
├─ docs/                     # NT 008; guia.md (manual completo); img/ e video/ (dados fictícios)
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
│  │  │                      # certificado, arquivos (nomes/pastas de XML e PDF), tributacao (abas)
│  │  └─ api/                # app.py + routes_config, routes_tabela, routes_emissoes, routes_tributacao
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
- **Login obrigatório** (ver "Autenticação"); `EMISSOR_IPS_PERMITIDOS` continua como camada extra na rede local.
- Chave Fernet em `EMISSOR_SECRET_KEY` quando definida (hospedagem: segredo do Fly, um backup do volume sozinho não
  abre o certificado); senão `data/secret.key`.

### Autenticação (2026-10-10)

Requisito da hospedagem paga, sem serviço pago (nada de SMS nem e-mail): tudo em `emissor/auth.py`.
- Usuários (`usuario`): e-mail = login, papel `admin` (certificado, onboarding, configurações, diagnóstico, usuários,
  auditoria) ou `operador` (tabela, clientes, emitir, cancelar, baixar). Senha Argon2id (`argon2-cffi`), mínimo 10.
- Sessão: token aleatório no cookie `emissor_sessao` (`HttpOnly`, `SameSite=Lax`, `Secure` em HTTPS ou com
  `X-Forwarded-Proto: https`); o banco guarda só o SHA-256. 12 h de inatividade / 7 dias no máximo. Trocar a senha,
  ativar 2FA, desativar ou redefinir o usuário derruba as outras sessões.
- O middleware de `app.py` resolve a sessão em `/api` e `/docs` (`request.state.acesso`, `usuario` no log) e recusa
  escrita com `Origin` diferente do `Host` (CSRF entre subdomínios do mesmo site). Os routers de config/tabela/emissões
  entram com `Depends(exigir_login)`; rotas de administrador usam `AdminAtual` (`api/deps.py`).
- Bloqueio: 10 erros seguidos → usuário bloqueado 15 min; 30 erros por IP em 15 min → 429 (em memória). No Fly o IP
  vem de `Fly-Client-IP` (só quando `FLY_APP_NAME` existe).
- 2FA: TOTP próprio (RFC 6238, sem dependência), segredo criptografado com a chave Fernet, passo usado gravado (sem
  reuso), 10 códigos de recuperação (SHA-256). QR Code em SVG pelo ReportLab. Opcional, com aviso em todas as telas.
- Primeiro acesso: só enquanto não há usuário. Token de `EMISSOR_CONVITE_INICIAL` (hospedagem) ou gerado em
  `data/primeiro-acesso.txt` e mostrado no log da subida; acesso de 127.0.0.1 sem cabeçalho de proxy dispensa o token
  (nunca com `EMISSOR_CONVITE_INICIAL` ou no Fly). Link da hospedagem: `https://<sub>/#/primeiro-acesso?token=<t>`.
- Convites e redefinição: link `/#/convite?token=` de uso único (7 dias) gerado pelo admin, copiado e enviado por ele.
- **Esqueci minha senha** (2026-10-18, `correio.py`): `POST /api/auth/esqueci` manda um link `finalidade=recuperacao` de
  1 h. A senha atual continua valendo até o uso (pedir a recuperação de outro não o tranca fora) e o 2FA continua
  sendo pedido; trocar a senha invalida os links pendentes. Resposta igual para e-mail com ou sem acesso, envio numa
  thread; limites: 5 pedidos por IP em 15 min, 1 e-mail por usuário a cada 2 min e 5 por dia. Envio pelo painel da
  hospedagem (`EMISSOR_EMAIL_URL` + `EMISSOR_EMAIL_TOKEN`; o painel monta o e-mail e manda pelo Resend, só com link do
  subdomínio do token) ou por SMTP (`EMISSOR_SMTP_*`). Exige `EMISSOR_URL_PUBLICA`: o link nunca usa o `Host` da
  requisição. Sem configuração, a tela diz para pedir ao administrador (`recuperacao_email` em `/api/auth/sessao`).
  Recuperação sem nenhum admin: `emissor-admin redefinir <email> [--remover-2fa]` / `convidar` / `usuarios`.
- Auditoria (`auditoria`): login/falha/bloqueio, senha, 2FA, usuários, certificado, configurações (com ambiente),
  lote criado/interrompido, nota cancelada/cancelamento recusado. Não há coluna nova em tabelas antigas (o
  `create_all` não altera tabelas existentes).
- Suspensão (hospedagem): `EMISSOR_SUSPENSO=1` → GET e `POST /api/emissoes/zip` continuam; o resto responde 403
  `{suspenso: true}`; `EMISSOR_URL_ASSINATURA` vira link na faixa. Lotes `em_andamento` ainda são retomados na subida
  (anti-duplicidade). `GET /api/saude` (público) diz `{ok, versao, suspenso, administrador_criado}`; healthcheck do
  Docker usa ele.
- Frontend: `App.tsx` `Raiz` (sessão → convite / primeiro acesso / login / app), `pages/Acesso.tsx`,
  `pages/Conta.tsx`, `pages/Usuarios.tsx`; 401 com `login: true` dispara `emissor:sessao-expirada`. Operador não vê
  Configurações nem Usuários. `copiar()` em `ui.tsx` funciona em HTTP (sem `navigator.clipboard`).
- Aviso no app quando o certificado estiver a ≤ 30 dias do vencimento.
- **Aceite no fim do teste** (2026-10-28, `assinatura.py`, `components/Assinatura.tsx`): na hospedagem
  (`EMISSOR_ASSINATURA_API` + `EMISSOR_EMAIL_TOKEN`), o painel marca a assinatura nova para acabar no fim do teste e o
  app pergunta. `GET /api/assinatura` (qualquer usuário; cache de 2 min; painel fora do ar = `disponivel: false`) dá a
  situação; nos últimos 3 dias uma faixa sob a do ambiente pede a confirmação e, nas últimas 24 h, o diálogo abre
  sozinho para o administrador (uma vez por dia). `POST /api/assinatura/confirmar` (admin, com o "estou de acordo")
  cobra na hora; `POST /api/assinatura/recusar` só registra. Sem aceite, o painel suspende a instância e a faixa troca
  o aviso de suspensão por "Continuar e assinar", que funciona com a instância suspensa (`PERMITIDAS_SUSPENSO`) e
  espera o `/api/saude` voltar sem suspensão para recarregar. Tudo na auditoria (`assinatura_*`).
- Hospedagem: com `EMISSOR_URL_ASSINATURA`, o administrador vê o link "Assinatura" no topo e a seção "Assinatura" em
  Minha conta (portal do Stripe: cartão, faturas, cancelamento); sem a variável (instalação própria) nada aparece.

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
- **F6 — Futuro (em andamento):** ✅ cancelamento de NFS-e e situação pelo ADN (2026-10-09); ✅ grupo IBS/CBS,
  tributações, abas e nota avulsa (2026-10-11); ✅ variáveis na descrição e planilha das notas (2026-10-23); importar notas emitidas fora do sistema (a distribuição por NSU já é lida —
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
- **Testes ponta a ponta** (`tests/test_ponta_a_ponta.py` + `tests/sefin_simulada.py`): Sefin/ADN simulados que conferem a
  DPS como a real (XSD oficial, assinatura com o certificado da conexão, Id, série, tpAmb, datas, E0959/E0322) e montam
  a NFS-e a partir dela (assinada por um certificado "da Sefin" e validada no XSD). Todo `SefinClient` sem transporte
  vai para a simulação; `test_todas_as_rotas` chama cada rota da API e falha se surgir rota sem teste ou erro 500.
  Rota nova → acrescentar a chamada nesse teste.
- **CI** (`.github/workflows/testes.yml`): ruff + pytest do backend e `npm run build` do frontend a cada PR e push na main.
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
| 2026-10-10 | Login próprio (Argon2id, sessão em cookie, TOTP, papéis admin/operador, auditoria), sem serviço externo | Hospedagem paga expõe cada instância na internet; custo zero; a versão instalada localmente também fica protegida |
| 2026-10-10 | Login separado em cada instância (subdomínio), sem login central | Isolamento por cliente; o app continua independente da hospedagem |
| 2026-10-10 | Sem e-mail: convites e redefinição por link copiado pelo admin; recuperação total por `emissor-admin` | Envio de e-mail custaria um serviço; não é essencial |
| 2026-10-18 | "Esqueci minha senha" por e-mail: na hospedagem pelo painel (Resend, plano grátis), na instalação própria por SMTP opcional | Pedido do Iuri; o Resend grátis cobre o volume (100/dia) e o app continua sem depender de serviço pago |
| 2026-10-10 | Operador pode emitir e cancelar; só admin mexe em certificado, configurações e usuários | Cancelar é do dia a dia de quem emite; fica registrado na auditoria |
| 2026-10-10 | Suspensão da hospedagem por `EMISSOR_SUSPENSO` (consulta e download continuam) | O cliente precisa conseguir baixar os XMLs fiscais mesmo inadimplente |
| 2026-10-10 | Imagem Docker publicada em `ghcr.io/iurijw/emissor-em-planilha` por GitHub Actions (`latest` e `sha-<commit>` a cada push na main; `X.Y.Z` nas tags `v*`) | A hospedagem cria uma máquina Fly por cliente a partir dela; quem roda sozinho continua com `iniciar.bat`/`docker compose` |
| 2026-10-10 | README curto com a hospedagem em emissoremplanilha.com em destaque; manual completo em `docs/guia.md` | O README vira a vitrine da hospedagem paga; quem roda sozinho continua com todas as instruções no guia |
| 2026-10-10 | Versões publicadas pelo workflow manual "Publicar versão" (release `vX.Y.Z` da main + imagem `:X.Y.Z`); a versão tem de ser a de `backend/pyproject.toml` | A hospedagem atualiza os clientes só por release; o workflow pode ser disparado pelo Claude, que não consegue criar tags pelo git |
| 2026-10-11 | IBS/CBS opcional por tributação, só com destinatário = tomador; CST derivado do cClassTrib | Cobre o caso comum (serviço a cliente no Brasil) sem pedir campos que a maioria não usa; evita E0959 |
| 2026-10-11 | Tributações predefinidas completas (cópia dos padrões), aplicadas por aba ou na nota avulsa; cópia na emissão | Comportamento previsível: a tributação é exatamente o que se vê; notas na fila não mudam se ela for editada |
| 2026-10-11 | Abas com clientes exclusivos: erro dentro da aba, só aviso fora dela | Segrega sem travar quem precisa emitir o mesmo cliente com outra tributação |
| 2026-10-11 | Nota avulsa = lote de 1 sem linha da tabela | Reaproveita fila, numeração, anti-duplicidade e validação |
| 2026-10-11 | XSD local trocado pelo pacote `esquemas-nfse-rtc-v1-01-20260727` e CNPJ alfanumérico liberado | Conferido no gov.br: é o leiaute em produção desde 10/08/2026; corrige `TSSerieDPS` |
| 2026-10-11 | Testes ponta a ponta com Sefin/ADN simulados e workflow "Testes" no GitHub Actions a cada PR | Conferir todas as rotas e emissões sem certificado real; Actions é grátis em repositório público |
| 2026-10-11 | Auditoria de segurança (skill cloudflare/security-audit-skill): link de redefinição pede o 2FA se ele continua ativo; primeiro acesso sem código só com Host local; limites de memória/tempo nas importações, no ZIP em massa, no log do navegador e no Argon2 (4 verificações ao mesmo tempo); nDPS pelo ambiente do lote e troca de ambiente/numeração bloqueada com lote em andamento; "Verificar na Sefin" não sobrescreve nota que mudou durante a consulta; exportação sem fórmulas | Achados confirmados com reprodução local; relatório na thread "Checagem de segurança" |
| 2026-10-11 | Vídeos em HD com zoom (`docs/video/*-hd.mp4`, 1920x1080, em 1,5x, legendas e cursor desenhados na montagem) gravados com dados fictícios e Sefin/BrasilAPI simuladas; scripts em `/mnt/project-files/ferramentas/videos/` (fora do repo). O site da hospedagem exibe esses arquivos | Mostrar o sistema em detalhe no guia do site; os scripts permitem regravar quando a tela mudar |
| 2026-10-12 | Tributação padrão listada junto das outras em Configurações → Tributações; dados da empresa em seção própria; retenções federais atrás de um "o tomador retém" | Pedido do Iuri: duas seções de tributação eram confusas; a padrão continua guardada nas configurações (sem migração) |
| 2026-10-12 | Alíquotas e retenções em % na tributação; pAliq do ISS só onde a Sefin exige; aviso "manter ou alterar" a cada emissão com ISS retido, que o operador pode responder | Pedido do Iuri: a alíquota efetiva do Simples muda todo mês; tributos federais informados mesmo sem retenção |
| 2026-10-13 | Página "ligando o sistema" por service worker, sem guardar o app em cache | Pedido do Iuri: na hospedagem a máquina dorme sem uso e o primeiro acesso ficava em branco; o service worker é o único jeito grátis de mostrar algo antes de o servidor responder |
| 2026-10-22 | Dois leiautes ao mesmo tempo: NT 009 (`xsd/v1_01_nt009`) na homologação e o anterior (`xsd/v1_01`) na produção, escolhidos por `LEIAUTE_POR_AMBIENTE` | A Produção Restrita passou à NT 009 em 20/10/2026 (finNFSe obrigatório em infDPS: a DPS antiga é recusada no XSD) e a Produção ainda não tem data |
| 2026-10-22 | Produção troca sozinha para a NT 009 em 01/12/2026 00:00 (pelo `dhEmi` da DPS; `leiaute()` substitui `LEIAUTE_POR_AMBIENTE`), publicada antes da data | Data do comunicado do CGNFS-e de 20/10/2026; não depender de uma versão publicada na noite da troca |
| 2026-10-23 | Variáveis na descrição trocadas na montagem da DPS; variável desconhecida bloqueia a linha | Pedido do Iuri (estudo de funções): evita editar a tabela todo mês; um "{mes_atua}" impresso na nota seria pior que um erro |
| 2026-10-23 | Planilha das notas filtradas (uma linha por nota) lida do XML autorizado, sem aba de resumo | Pedido do Iuri: o valor que vale é o que a Sefin calculou; o resumo por competência não era necessário |
| 2026-10-27 | Tela de tributação na ordem e com os nomes do Emissor Nacional; serviço buscado na lista nacional (Anexos B e VIII); `cTribMun` só com 3 dígitos | Pedido do Iuri: quem emite já conhece o portal; códigos digitados errado geravam E0312 e recusa no XSD |
| 2026-10-27 | Consulta do serviço na prefeitura (parametrização do ADN) só como aviso, nunca bloqueando | Antecipa a E0312, mas a rota não tem documentação oficial: uma resposta mal interpretada não pode impedir a emissão |
| 2026-10-27 | NFS-e achada pelo Id da DPS só é adotada se `dhEmi`, tomador e valor baterem com a DPS enviada; senão a nota fica rejeitada e o lote para | Outro sistema emitindo com o mesmo CNPJ e série fez a 0.1.0 registrar notas de outros clientes como emitidas (as notas pedidas nunca saíram) |
| 2026-10-28 | Cobrança no fim do teste só com o aceite do administrador no app; sem aceite, suspensão como num cancelamento e "Continuar e assinar" a qualquer momento | Pedido do Iuri: ninguém deve ser cobrado sem saber; o painel marca a assinatura no Stripe para acabar no fim do teste |
| 2026-10-22 | Cada PR (do próprio repositório) publica a imagem `:pr-N` e move a `:dev`, que cada release também move; a instância `teste` da hospedagem segue a `:dev` (canal dev do painel) | Pedido do Iuri: conferir mudanças de tela na instância de testes antes de publicar a release para os clientes |
