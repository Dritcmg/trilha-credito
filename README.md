# Trilha Crédito

Estudo pessoal de Adriano Camargo de Oliveira, desenvolvido com assistência de IA declarada. Uma trilha inspecionável liga o recebimento de um contrato sintético, sua validação ou revisão humana e a conciliação de parcelas. Código aberto sob MIT; sem vínculo, patrocínio ou integração com a byx.

O problema explorado é a passagem de informação recebida de parceiros para uma operação rastreável. Não se trata de um motor de concessão, OCR universal ou sistema bancário homologado.

## O que funciona

- API Python/FastAPI com modelos Pydantic e documentação OpenAPI.
- Entrada JSON/texto ou PDF textual de até 1 MiB e cinco páginas; dois layouts sintéticos explícitos.
- Inbox durável PostgreSQL: ingestão confirma a gravação, worker separado valida depois. Duplicação do mesmo texto por tenant não cria outro trabalho.
- Campos ambíguos, layout desconhecido ou PDF sem texto seguem para revisão humana. O revisor fornece fatos normalizados, novamente validados.
- Conciliação de recebimentos com Decimal, referência de pagamento deduplicada, chave de idempotência, instantâneos por data e calendário ancorado ao dia original.
- Isolamento por tenant, papéis reader/operator/reviewer, credenciais locais por hash e trilha de ações.
- Testes de regras, API, PDF real sintético, falhas e concorrência PostgreSQL, comparação com oráculo independente em centavos, CI e evidências.

## Executar no VS Code

Python 3.12. Crie e selecione a `.venv` pelo editor:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts/local_setup.py
```

O gerador não sobrescreve arquivos existentes nem imprime segredos. Para a demonstração SQLite local, carregue as variáveis da `.env.local` no terminal privado e execute `alembic upgrade head`. A configuração de depuração já aponta para esse arquivo. Inicie **Trilha API** e **Trilha worker once** no painel Run and Debug. A API fica em `http://127.0.0.1:8003/docs`. Não crie schema na inicialização do servidor; migrations são uma etapa explícita.

Para PostgreSQL/container, `docker compose --env-file .env.local up --build -d` usa portas locais 8003/54363. O banco tem credenciais geradas localmente. A aplicação migra o banco antes de ficar pronta; só então o worker inicia. Pare com `docker compose --env-file .env.local stop`; não use remoção de volumes para um encerramento comum.

## Exemplo sintético

Envie o JSON abaixo a `POST /v1/documents`, usando seu token local em `Authorization: Bearer ...`:

```json
{"source_text":"TRILHA CCB V1\nContrato: DEMO-001\nPrincipal: 1200.00\nValor da parcela: 400.00\nParcelas: 3\nPrimeiro vencimento: 2026-01-31"}
```

A resposta `202` significa recebido e persistido, não concluído. Após o worker, `GET /v1/documents/{id}` mostra `accepted` ou `needs_review`. `POST /v1/documents/{id}/reconciliations` recebe `Idempotency-Key` e pagamentos sintéticos. Repetir a mesma chave/payload devolve o mesmo resultado; mudar o conteúdo retorna `409`. Tentativas de ler outro tenant devolvem `404`.

## Validar externamente

```powershell
.venv\Scripts\python scripts/verify.py
# Em banco PostgreSQL dedicado cujo nome termine em _test:
$env:TRILHA_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@localhost:54363/trilha_test'
.venv\Scripts\python scripts/verify.py --postgres
```

O segundo comando exige PostgreSQL real e recusa skips. Cada teste usa um schema recém-criado, com nome `test_trilha_<uuid>`; nunca remove o schema `public`. O relatório fica em [evidence/results.json](evidence/results.json). [A CI](../../actions) valida também PostgreSQL. O resultado de uma revisão não promete todos os comportamentos futuros.

## Contexto, decisões e limites

Leia [contexto público e hipótese](docs/CONTEXT.md), [decisões e arquitetura](docs/DECISIONS.md), [ameaças e operação](docs/OPERATIONS.md), [mapa de competências](docs/COMPETENCIES.md) e [assistência de IA](AI_ASSISTANCE.md).

Use somente fixtures sintéticas. A extração é determinística e limitada a dois layouts, não generaliza contratos de instituições reais. PDF não confiável exigiria sandbox de processamento e controles adicionais. A autenticação local não implementa SSO, rotação centralizada ou identidade corporativa; há auditoria de ações, não certificação regulatória. Métricas são por processo. Não há Redis, broker ou LLM inseridos sem necessidade: PostgreSQL é a fonte de estado; interpretação e validação ficam explícitas.
