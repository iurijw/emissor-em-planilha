# Como contribuir

Obrigado pelo interesse em melhorar o Emissor em Planilha. Relatos de erro, ideias e pull requests são bem-vindos.

## Antes de tudo: nada de dados reais

O repositório é público. Em relatos, pull requests, testes e capturas de tela, **não use** CNPJ/CPF ou nomes de
clientes reais, XMLs de notas reais, senhas nem certificado digital. Use os dados fictícios do projeto: prestador
`11.444.777/0001-61`, tomador `11.222.333/0001-81`, CPF `529.982.247-25` e município `4205407`.

## Relatar um problema ou sugerir uma melhoria

Use os [formulários de relato](https://github.com/iurijw/emissor-em-planilha/issues/new/choose). Copie a mensagem de
erro exatamente como apareceu, com o código da Sefin se houver. Falha de segurança **não** vai em relato público: veja
[SECURITY.md](SECURITY.md).

## Enviar uma alteração

1. Faça um fork e crie um branch a partir da `main`.
2. Prepare o ambiente e rode o sistema como descrito em [Desenvolvimento](docs/guia.md#desenvolvimento).
3. Antes de abrir o pull request, rode as verificações:

   ```bash
   cd backend && uv run ruff check src tests && uv run ruff format --check src tests && uv run pytest -q
   cd frontend && npx tsc --noEmit -p tsconfig.json && npm run build
   ```

4. Rota nova na API precisa de uma chamada em `test_todas_as_rotas` (`backend/tests/test_ponta_a_ponta.py`).
5. Mudança de leiaute fiscal (DPS, eventos, XSD) precisa citar a nota técnica ou o anexo oficial que a justifica.
6. Commits em português, no imperativo ("Corrigir...", "Adicionar...").

As decisões de arquitetura ficam em [CLAUDE.md](CLAUDE.md). Se a sua mudança altera uma delas, atualize o registro de
decisões no mesmo pull request.

## Licença das contribuições

O projeto é distribuído sob a [GNU AGPL-3.0](LICENSE). Ao enviar um pull request, você concorda que a sua
contribuição seja distribuída sob a mesma licença.

## Conduta

Ao participar, você concorda com o [código de conduta](CODE_OF_CONDUCT.md).
