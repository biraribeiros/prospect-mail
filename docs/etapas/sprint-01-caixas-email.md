# Etapa 1 — Hunter: caixas de email

**Em uma frase:** o sistema lê suas caixas de email e lista quem você já conhece, com uma nota de relacionamento.

## O que você terá no final
- Conexão com Gmail, Outlook, IMAP (Locaweb/Horde) ou arquivo `.mbox/.eml`.
- Lista de contatos sem duplicados, com nota 0–100 de relacionamento.
- Tela de revisão com filtros e exportação CSV.

## O que será feito
1. Cadastro de caixas (`mailbox_account`, **correção F2**; tokens cifrados).
2. Quatro leitores: Gmail, Microsoft Graph, IMAP, mbox/eml. Inclui "Enviados".
3. Filtro de ruído (noreply, bancos, notificações, marketplaces), editável.
4. Leitura da assinatura (nome, cargo, telefone); IA só como reserva, com custo registrado.
5. Nota de relacionamento e separação corporativo × pessoal.
6. Registro de conformidade por contato. **Só metadados e assinatura são guardados — nunca o corpo do email.**

## Ferramentas
`claude-api`, `security-review`, `context7`.

## Antes de começar
- **`docs/LGPD.md` criado (F10).**
- Decidir: Gmail em Workspace da VIA (consentimento "Interno", F12) ou IMAP com senha de app.
- Ter uma conta de teste ou um `.mbox` de amostra (F17).

## Como conferir que deu certo
```bash
pytest tests/hunter/test_mailbox.py -v
```
- 1.000 mensagens → contatos sem duplicados.
- Rodar de novo → **zero registros novos**.
- Todo contato tem registro de conformidade.

## Atenção
Tokens nunca aparecem em log.
