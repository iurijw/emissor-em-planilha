# Segurança

O Emissor em Planilha guarda o certificado digital A1 da empresa e emite notas com validade jurídica, então qualquer
falha de segurança é tratada com prioridade.

## Como relatar uma falha

**Não abra um relato público.** Escreva para **contato@emissoremplanilha.com** com o assunto "Segurança" e conte:

- o que a falha permite (por exemplo, ler o certificado, entrar sem senha, emitir em nome de outro);
- como reproduzir, com dados fictícios;
- a versão do sistema (aparece no ícone de atualizações, no topo da tela).

A resposta vem assim que possível. A correção sai numa versão nova, que a versão hospedada recebe
automaticamente, e o crédito pela descoberta vai nas novidades da versão, se você quiser.

## Versões cobertas

Só a versão mais recente recebe correções. Quem instala por conta própria atualiza pelo ícone de atualizações
(`iniciar.bat`) ou com `git pull` e `docker compose up -d`.
