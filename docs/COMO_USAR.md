# Como usar este projeto (sem ser técnico)

## Regras simples
1. **Uma etapa por vez.** Nunca pule.
2. **Antes de programar, a IA mostra o plano.** Você lê e diz "aprovado" ou pede ajuste.
3. **Nada é enviado de verdade durante o desenvolvimento.** Os emails de teste vão para o Mailpit (caixa de testes em `localhost:8025`).
4. **Toda mensagem passa pela sua aprovação** antes de sair.
5. **Ao fim de cada etapa**, a IA atualiza [PROGRESSO.md](PROGRESSO.md) e você confere a seção "Como conferir".

## Como pedir a próxima etapa
Abra uma sessão nova e escreva exatamente:

> Leia docs/PROMPT_MESTRE.md, docs/VALIDACAO_DO_PLANO.md e docs/PROGRESSO.md e execute a etapa N. Não faça nada além dela.

## O que você precisa ter
- Windows com **Docker Desktop** (já instalado na sua máquina) — roda tudo com `docker compose up -d`.
- Conta GitHub (já configurada).
- Mais adiante: domínio secundário para envio, caixas de email, chave da API Claude.

## Se algo der errado
- Tela não abre: rode `docker compose ps` e envie a saída para a IA.
- Dúvida de termo: [GLOSSARIO.md](GLOSSARIO.md).
