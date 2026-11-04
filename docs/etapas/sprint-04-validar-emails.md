# Etapa 4 — Filtro: validar emails

**Em uma frase:** saber se um email existe **sem enviar nada**.

## O que você terá no final
- Cada email com status: válido, arriscado, inválido ou bloqueado.
- Painel com a distribuição e reverificação de emails com mais de 90 dias.

## O que será feito
1. Verificação em camadas (para na primeira reprovação): sintaxe → descartável → conta de função → MX → SMTP (Reacher) → catch-all.
2. Interface `EmailVerifier` com 3 opções: Reacher, MillionVerifier, ZeroBounce.
3. Cache (email 90 dias; catch-all 7 dias); máximo de 3 verificações simultâneas por servidor.
4. Teste de porta 25 antes de rodar; se fechada, usa a API paga e avisa no painel.
5. Aviso permanente: "SMTP não garante que a caixa existe".

## Ferramentas
`engineering:testing-strategy`, `context7`.

## Antes de começar
- **Sem VPS por enquanto:** a etapa começa com API paga (MillionVerifier ou ZeroBounce). O Reacher entra quando houver servidor com porta 25 (decidir então AGPL × comercial).
- Hospedagem compartilhada (cPanel/Plesk) **não** serve para o Reacher. No seu PC a porta 25 não funciona.

## Como conferir que deu certo
```bash
pytest tests/filtro/test_validation.py -v
```
- 500 emails de teste → distribuição coerente.
- Domínio catch-all → todos os emails dele ficam "arriscado".
- Nenhuma chamada real nos testes.
