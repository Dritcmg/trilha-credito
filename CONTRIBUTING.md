# Validação externa e contribuições

Abra uma issue com versão, comando, cenário mínimo, comportamento esperado/observado e dados inteiramente sintéticos. Não envie contratos, credenciais ou dados pessoais. Para uma correção, inclua um teste de regressão e execute `python scripts/verify.py --postgres` em PostgreSQL dedicado. Mudanças de layout precisam explicar a regra de extração e os casos ambíguos que seguirão para revisão.

Separe alterações funcionais de formatação ampla. Discuta mecanismos de fila, cache e autenticação pelo risco que resolvem. O mantenedor deve revisar e compreender contribuições, inclusive as assistidas por IA.
