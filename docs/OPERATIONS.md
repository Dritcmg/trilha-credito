# Operação e limites de segurança

## Prontidão e diagnóstico

`/health/live` confirma processo; `/health/ready` verifica banco e tabela migrada. `/metrics` exige papel reviewer. `X-Request-ID` é gerado pelo servidor. Logs estruturados omitem corpo, query string, autorização e exceção bruta. A métrica é por processo, sem agregação Prometheus multiprocess.

O worker pode ser repetido com `--once`; falha gera exit code 1. O processo contínuo trata falhas com espera curta e aceita sinais para encerramento. Falhas de domínio persistentes encaminham o documento para revisão após três tentativas. Ao reiniciar o processo, a inbox permanece no banco. Uma indisponibilidade prolongada de banco precisa de monitoramento/supervisão operacional; não há SLO de produção declarado.

## Retenção e ameaças

Somente fixtures sintéticas devem ser enviadas. O texto fica temporariamente na inbox e é removido ao finalizar. Digest, fatos normalizados, resultado e ações permanecem. O operador deve definir retenção, backup, criptografia, privilégios de banco e restauração antes de qualquer uso além do laboratório.

Arquivo PDF limitado a 1 MiB/cinco páginas não equivale a limite de memória/CPU após descompressão. O parser foi exercitado com PDFs textuais sintéticos, não com arquivos arbitrários adversariais. Uma exposição externa exigiria sandbox de parsing, quotas/timeout no processo isolado, antivírus conforme risco e controle de upload no gateway. Não exponha esta demonstração à internet como serviço de documentos reais.

Tokens podem ser revogados removendo o hash da configuração e reiniciando a API. O gerador local preserva arquivo existente. Não há recuperação de senha, OAuth/OIDC, RBAC corporativo completo, segredo gerenciado ou trilha imutável contra administrador do banco. Papéis e isolamento são verificáveis no escopo da API.

## Mudanças e recuperação

1. Confira revisão e backup antes de uma migração em ambiente com dados que devam ser preservados.
2. Execute `alembic upgrade head` em banco isolado e os testes PostgreSQL.
3. Verifique readiness, ingestão sintética, worker, consulta e conciliação.
4. Pare a promoção se qualquer gate falhar; não teste em dados reais.
5. Voltar o binário não reverte automaticamente os dados. O downgrade inicial remove tabelas e só deve ser usado no banco descartável de testes.

No Compose, `stop` preserva volumes. Não use `down -v` para encerrar. Não há cloud/conta AWS, email, LLM ou serviço terceiro necessário para esta demonstração.
