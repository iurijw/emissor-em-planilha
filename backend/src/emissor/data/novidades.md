# Novidades do Emissor em Planilha

<!--
Mostradas no app (ícone de atualizações no topo) e copiadas para a release pelo workflow "Publicar versão",
que recusa publicar uma versão sem seção aqui. Uma seção por versão, a mais nova em cima, no formato
"## X.Y.Z (DD/MM/AAAA)" seguido de itens "- ". Escreva para quem usa o sistema: o que mudou na tela ou na nota,
sem nomes de código, PR ou leiaute.
-->

## 0.13.2 (12/11/2026)

- Com várias empresas, o seletor do topo e Configurações → Empresas têm busca por nome ou CNPJ.

## 0.13.1 (11/11/2026)

- A recusa E0116 explica quando a prefeitura ainda não liberou a empresa no sistema nacional (alguns municípios só liberam o MEI e o Simples Nacional).

## 0.13.0 (11/11/2026)

- Instalação própria: emita por várias empresas no mesmo sistema, cada uma com o certificado dela ou por procuração.
- A nota avulsa mostra no fim os tributos, as retenções e o valor líquido da nota.
- A atualização pela tela não fica mais reiniciando sem parar quando a cópia do sistema está em outro ramo do git.

## 0.12.2 (10/11/2026)

- Na hospedagem, a tela de primeiro acesso avisa que o link para criar o administrador foi enviado para o e-mail usado
  na assinatura: por segurança, só quem recebeu o link consegue criar o administrador.

## 0.12.1 (07/11/2026)

- Ao digitar o CEP no cadastro do cliente ou na nota avulsa, rua, bairro e município são preenchidos sozinhos.
- Explicações mais claras para as recusas da Sefin, inclusive E0116 (falta a inscrição municipal).

## 0.12.0 (05/11/2026)

- Na nota avulsa, o endereço do tomador aparece logo abaixo do CNPJ/CPF, já preenchido com o do cadastro ou com o
  que veio da Receita. Clique para abrir e corrigir: o que você mudar fica salvo no cadastro do cliente.
- No campo CNPJ/CPF da nota avulsa dá para digitar o nome ou parte do número: aparecem os clientes do cadastro para
  escolher. Um tomador novo com nome, como um CPF, passa a entrar nos clientes ao conferir a nota.
- Uma etiqueta avisa quando o tomador está sem endereço ou com o endereço incompleto. Para CPF, o endereço é opcional.
- Nota recusada na homologação agora avisa que o cadastro das empresas no ambiente de testes do governo costuma estar
  incompleto: recusas sobre a inscrição municipal ou o estabelecimento no município podem não acontecer em produção.
- Explicação e "Como resolver" para as recusas E0120 (inscrição municipal fora do cadastro nacional) e E0084 (CNPJ
  sem estabelecimento no município).

## 0.11.1 (03/11/2026)

- Corrigida a mensagem "O Ambiente Nacional (ADN) recusou a consulta" ao atualizar a situação das notas. O Ambiente
  Nacional mudou a forma de responder em 03/11/2026 e o sistema passou a se identificar do jeito novo.

## 0.11.0 (02/11/2026)

- Desfazer e refazer na tabela, como no Excel: Ctrl+Z volta a última alteração e Ctrl+Y a refaz. Também há dois
  botões no começo da barra da tabela, que mostram o que vão desfazer.
- Vale para digitar, colar, preencher pela alça, limpar células, incluir e excluir linhas, ordenar e importar
  planilha. Uma linha excluída volta com a última emissão dela.

## 0.10.0 (31/10/2026)

- Novo ícone de atualizações no topo: mostra a sua versão e o que mudou em cada uma.
- Na instalação própria pelo iniciar.bat, o administrador atualiza com um clique e pode deixar o sistema se
  atualizar sozinho ao iniciar.

## 0.9.0 (29/10/2026)

- Com o sistema suspenso, “Continuar e assinar” reativa a qualquer momento.
- Depois da suspensão, as notas ficam disponíveis para consulta e download por 35 dias.

## 0.8.0 (28/10/2026)

- Antes do fim do período de teste, o administrador confirma se quer continuar com a assinatura.

## 0.7.1 (28/10/2026)

- O PDF da nota é gerado na hora do download e ocupa bem menos espaço.
- Dá para baixar de uma vez todas as notas do filtro (até 20.000).

## 0.7.0 (27/10/2026)

- Tela de tributação na mesma ordem e com os mesmos nomes do Emissor Nacional.
- Busca do código de serviço na lista nacional, com o NBS e a classificação do IBS/CBS sugeridos.
- Aviso quando a prefeitura não aceita o código de serviço escolhido.

## 0.6.2 (27/10/2026)

- Correção: quando o número da DPS já tinha sido usado por outro sistema, a nota dele não é mais registrada como
  emitida aqui.

## 0.6.1 (27/10/2026)

- Ao passar a empresa para ME/EPP, o sistema pede o % aproximado dos tributos do Simples.

## 0.6.0 (23/10/2026)

- Variáveis na descrição, como {mes} e {ano}, trocadas pela data da emissão.
- Exportação das notas filtradas para Excel.
- Na nota avulsa, o CNPJ é buscado na Receita e o cliente pode ser escolhido pelo nome.

## 0.5.3 (22/10/2026)

- A Produção passa sozinha ao novo leiaute da NFS-e (NT 009) em 01/12/2026.

## 0.5.2 (22/10/2026)

- Homologação no novo leiaute da NFS-e (NT 009), em uso na Produção Restrita desde 20/10/2026.

## 0.5.1 (21/10/2026)

- Na hospedagem, o administrador acessa a assinatura (cartão e faturas) pelo topo da tela.

## 0.5.0 (18/10/2026)

- “Esqueci minha senha”: o link para criar uma senha nova chega por e-mail.

## 0.4.2 (13/10/2026)

- Aviso “ligando o sistema” enquanto o servidor da hospedagem acorda.

## 0.4.1 (12/10/2026)

- Todos os CST do PIS/COFINS disponíveis na tributação.

## 0.4.0 (12/10/2026)

- Configurações reorganizadas: dados da empresa numa seção e todas as tributações em outra.

## 0.3.0 (11/10/2026)

- Alíquotas e retenções (ISS retido, PIS/COFINS, IRRF, CSLL e INSS) na tributação.
- Aviso para conferir a alíquota do ISS retido a cada emissão.

## 0.2.0 (11/10/2026)

- Grupo IBS/CBS opcional na nota.
- Abas na tabela, cada uma com a sua tributação e os seus clientes.
- Nota avulsa, para emitir uma nota fora da tabela.
- CNPJ alfanumérico aceito.

## 0.1.0 (10/10/2026)

- Primeira versão publicada: emissão em lote, cancelamento, situação das notas pelo Ambiente Nacional, login com
  verificação em duas etapas e usuários administrador e operador.
