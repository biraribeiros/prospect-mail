# Teste de carga (F16) — onde está o gargalo

Script: `docker compose exec app python scripts/teste_carga.py 3000` (workspace descartável, SMTP para o Mailpit, apaga tudo no final).
Medido em 06/11/2026, no computador de desenvolvimento (Docker Desktop), com 3.000 contatos e 10 caixas já aquecidas.

| Parte | Resultado | Conclusão |
|---|---|---|
| Agendar 3.000 mensagens | 40 s (76 por segundo) | Não é gargalo. |
| Painel com lista de 3.000 empresas, 20 acessos simultâneos | p50 328 ms, p95 540 ms, 0 erros | Folga grande. |
| Enviar por SMTP (Mailpit local) | 2,5 ms por mensagem | Não é gargalo do sistema. |
| **Limite de segurança do envio** | 50 por dia por caixa (após 4 semanas), 3–15 min entre envios | **É o verdadeiro gargalo, e é proposital.** |

**Meta definida:** com 10 caixas aquecidas o sistema envia 500 mensagens/dia; as 3.000 do teste se espalharam por 7 dias úteis. Para enviar mais, adicione caixas e domínios secundários — nunca acelere uma caixa. A capacidade do servidor (2 vCPU, 4 GB) comporta dezenas de caixas.
