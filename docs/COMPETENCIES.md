# Competências e validação

| Competência da vaga | Evidência para inspecionar |
| --- | --- |
| Python e fundamentos | domain.py: funções, tipos, modelos e aritmética Decimal |
| FastAPI e REST | api.py: HTTP, validações, dependências, papéis e status |
| Banco relacional e SQL | db.py, migrations e service.py: constraints, transações e consultas |
| Testes e manutenção | testes de contrato, regressões, falhas, calendário e referência independente |
| Git e CI | workflow com PostgreSQL real, lint, dependências e artefatos |
| Linux e Docker | imagem com usuário sem root e Compose local separado |
| Assíncrono e concorrência | inbox persistente, worker e testes PostgreSQL SKIP LOCKED |
| Observabilidade | correlação, logs sem payload, métricas e readiness |
| Segurança de API | tenant derivado de credencial, 404 cruzado, papel de revisão e limites |
| Regras de negócio | estado documental e conciliação; sem alegar experiência bancária regulada |

## Roteiro de explicação

Explique a diferença entre 202 recebido e trabalho concluído. Mostre por que duas entregas iguais não geram dois jobs, por que uma referência contratual repetida exige revisão e onde a transação protege o estado. Demonstre uma falha antes do commit e a retomada. Compare centavos inteiros com Decimal e explique o corte temporal do snapshot. Mostre o teste que falharia ao remover o filtro de tenant.

Reconheça os limites: dados sintéticos, formatos fechados, autenticação local, auditoria por ação/tenant, ausência de OCR/LLM e nenhuma concessão real. O portfólio foi desenvolvido com assistência de IA, e a capacidade pessoal deve ser demonstrada explicando, modificando e reproduzindo os testes.
