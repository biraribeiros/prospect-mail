# Etapa 6a — Envio seguro: cadastro e trava

**Em uma frase:** cadastrar caixas e domínios de envio e garantir, no código, que o domínio principal nunca faz prospecção fria.

## O que você terá no final
- Cadastro de caixas (Gmail, Microsoft, SMTP) e domínios.
- Verificação de DNS (SPF, DKIM, DMARC, DNS reverso); envio bloqueado se algo falhar.
- `guard.py`: bloqueio do domínio principal.
- Plano de aquecimento por caixa.

## O que será feito
1. Telas de caixas e domínios.
2. Pré-voo de DNS com `checkdmarc`.
3. **Guard reforçado (F6):** o domínio principal é derivado das caixas lidas pelo Hunter; `tipo` não muda após o primeiro envio; testes tentando burlar.
4. Aquecimento padrão 5 / 15 / 30 / 50 envios por dia nas semanas 1–4.

## Ferramentas
`security-review`, `engineering:deploy-checklist`.

## Antes de começar (decisões)
Quais domínios secundários comprar · quantas caixas por domínio · Google Workspace, Microsoft 365 ou SMTP.

## Como conferir que deu certo
```bash
pytest tests/disparo/test_senders.py -v
```
- Domínio sem DMARC adequado → envio bloqueado.
- Tentativa de enviar pelo domínio principal → exceção.
