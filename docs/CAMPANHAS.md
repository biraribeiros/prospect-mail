# Campanha manual: da lista ao envio

Fluxo sem IA e sem API: você escreve a mensagem, escolhe quem recebe, aprova, e o sistema envia devagar e com segurança. A IA entra depois, no mesmo lugar.

```
Lista (texto/extensão/planilha) → Filtro (liberar) → Campanha (escrever) → Gerar → Aprovar → Envio automático → Respostas → Trello
```

## 1. Trazer a lista
**Hunter → Importar lista de texto** (ou a extensão do Chrome, ou planilha CSV). Veja [EXTENSAO.md](EXTENSAO.md).

## 2. Liberar os contatos (Filtro)
Só recebe quem estiver **válido**. Sem verificador pago há dois caminhos automáticos e um manual:
- **Quem já escreveu para você** vira válido sozinho ao clicar em *Validar emails* (vem das suas caixas).
- **Verificador** (Reacher no seu servidor, ou serviço pago): confirma o endereço de contatos frios.
- **Liberar manualmente** (Filtro → card "Liberar manualmente"): você decide que a lista curada pode receber. O sistema ainda barra sintaxe errada, descartável, domínio sem servidor de email e lista de supressão. Se algum endereço não existir, volta como devolução; acima de 3% em 24 h o envio **pausa sozinho**.

## 3. Escrever (Disparo → Campanhas → Nova campanha)
- **Nome interno**, **assunto** e **mensagem**. Duas formas: **HTML** (editor com abas **Visual** e **Código**, como no WordPress) ou **Só texto**.
- O HTML sai junto com a versão em texto puro (o leitor de email escolhe). Só ficam marcas simples: parágrafos, negrito, itálico, sublinhado, listas, títulos, citação, alinhamento e links. **Sem imagem, vídeo, script ou estilo livre**: o que não está na lista é descartado ao salvar. Para mostrar imagem ou vídeo, ponha um link.
- **Variáveis** (botão "Inserir variável"): `{{primeiro_nome}}`, `{{nome}}`, `{{empresa}}`, `{{cargo}}`, `{{cidade}}`, `{{remetente}}`. Se um contato não tem o dado, a mensagem dele fica **bloqueada** com o motivo, para você revisar.
- O link para sair da lista é colocado no rodapé automaticamente (texto e HTML) e o cabeçalho *List-Unsubscribe* de um clique também.
- Abaixo do editor aparecem a **conferência** (erros que impedem aprovar e avisos), a **prévia** com dados de exemplo e o botão **Conferir nota de spam** (Rspamd; precisa ser menor que 5).

Regras que impedem aprovar: assunto vazio ou com mais de 90 letras, mensagem vazia ou com mais de 300 palavras, mais de 3 links, variável desconhecida. Avisos (não bloqueiam): mais de 180 palavras, MAIÚSCULAS demais, palavras típicas de spam.

## 4. Gerar mensagens
Em **Quem recebe**: escolha a origem, a quantidade e o máximo por domínio (padrão 2). Ficam de fora automaticamente: contatos que não estão válidos, quem já foi contatado nos últimos 90 dias, lista de supressão e excesso de pessoas do mesmo domínio. Os melhores (nota final) entram primeiro. Gerar de novo refaz só os rascunhos.

## 5. Aprovar
O botão **Aprovar N mensagens e agendar** só aparece para administradores e aprovadores. Antes de liberar, o sistema confere as regras e a nota de spam. Depois disso o agendador cuida de tudo:
- aquecimento por caixa (5, 15, 30 e 50 por dia nas semanas 1 a 4);
- segunda a sexta, 8h às 18h no fuso do destinatário, 3 a 15 minutos entre envios da mesma caixa;
- lista de supressão conferida a cada mensagem; disjuntor de 3%.

Acompanhe em **Disparo** (agendadas, enviadas, taxa de devolução) e na própria campanha.

## 6. Respostas e Trello
Respostas aparecem em **Disparo → Respostas**. Quem responde "interessado" vira negócio e **um cartão no Trello**:
1. No Trello, abra o quadro → menu → **Mais** → **Email para o quadro** (ou Configurações do quadro) e copie o endereço (é secreto).
2. No `.env`: `TRELLO_BOARD_EMAIL=...` e um SMTP real para o email interno do sistema: `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls` ou `ssl`), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`. Reinicie.
3. O assunto vira o título do cartão ("Interessado: Empresa - Nome") e o corpo a descrição (contato, telefone, resposta e link). `@` e `#` são removidos do título para não virarem menção ou etiqueta.
Em modo seguro (`DRY_RUN=true`), com o SMTP apontando para o Mailpit, nada chega ao Trello; o cartão aparece no Mailpit para você conferir.

## Limites desta versão
- Campanha de **um passo** (sem seguimento automático dia 4 e 10; a sequência automática continua existindo para o fluxo com IA).
- O teste A/B e o teste entre perfis (ICP) só funcionam com os dados que a campanha tem (empresa vinculada).
- Nunca foi testada com SMTP real nem com um quadro do Trello real.
