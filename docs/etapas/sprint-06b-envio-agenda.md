# Etapa 6b — Envio seguro: agenda, bounces e disjuntor

**Em uma frase:** enviar devagar, na hora certa, e parar sozinho se algo der errado.

## O que você terá no final
- Agendador com rodízio de caixas, intervalo aleatório (3–15 min) e janela comercial (seg–sex, 8h–18h do destinatário).
- Cabeçalhos corretos: `List-Unsubscribe` (RFC 8058), `Message-ID` próprio, versão em texto.
- Lista de supressão conferida a cada mensagem.
- Tratamento de bounces e disjuntor (pausa se bounce > 3% em 24 h).

## O que será feito
1. Agendador e limites por caixa.
2. Cabeçalhos obrigatórios.
3. Processador de bounces: hard → inválido; soft → 2 tentativas, depois arriscado.
4. `circuit_breaker.py` com alerta no painel e por email ao admin.
5. Em desenvolvimento, 100% Mailpit.

## Ferramentas
`security-review`, `engineering:testing-strategy`.

## Antes de começar
Etapa 6a concluída.

## Como conferir que deu certo
```bash
pytest tests/disparo/test_senders.py -v
```
- 200 envios simulados no Mailpit respeitando limites e janela.
- Bounce simulado > 3% → disjuntor pausa tudo.
