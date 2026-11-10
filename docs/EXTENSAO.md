# Extensão do navegador e importação de lista

Duas formas simples de trazer emails para o Prospector, sem API e sem configuração em serviços externos. Os contatos entram como **não verificados**, com a origem registrada, e seguem para o **Filtro**.

## 1. Extensão do Chrome (Hunter → Extensão e listas)

### Instalar (uma vez)
1. Em **Hunter → Extensão e listas**, clique em **Baixar extensão (.zip)** e descompacte em uma pasta que você não vai apagar.
2. No Chrome, abra `chrome://extensions`, ligue o **Modo do desenvolvedor** e clique em **Carregar sem compactação**. Escolha a pasta `prospector-extensao`.
3. Fixe a extensão na barra do navegador.

### Conectar (uma vez)
1. Na mesma tela do Prospector, clique em **Gerar nova chave** e copie. Ela aparece **uma única vez**.
2. Clique no ícone da extensão → **Configurar**. Endereço: `http://localhost:8000` (ou o do seu servidor). Cole a chave → **Salvar e testar conexão**. Deve aparecer o nome do workspace.

### Usar
- **Coletar desta página:** abre qualquer site, clica na extensão e envia os emails que estão na página.
- **Coleta automática:** liga a caixinha. A extensão anota os emails de cada página que você abrir ou recarregar e envia em lotes. O Chrome pergunta uma vez se pode ler os sites.
- Os contatos aparecem em **Hunter → Contatos → filtro “Extensão do navegador”**. O contador da extensão mostra quantos eram novos e quantos já existiam.

### O que sai do seu navegador
Somente o **endereço de email** e o **endereço da página** (sem parâmetros de busca nem `#`). Nunca o conteúdo da página, histórico ou cookies. O envio é só para o endereço que você configurou.

### Segurança
- A chave é guardada só como hash no sistema. Quem a tem consegue adicionar contatos ao seu workspace: não compartilhe. Para cortar o acesso, **Revogar** na mesma tela.
- Se o seu papel no sistema deixar de ser admin ou operador, a chave para de funcionar.
- Emails das suas próprias caixas, robôs (no-reply), bancos e redes sociais são descartados.
- Use a coleta automática só no navegador onde isso é aceitável, pois ela lê todas as páginas que você abrir.
- A extensão não clica nem navega sozinha (não abre a “próxima página” de buscas do Google): o Google bloqueia esse tipo de automação e pode pedir CAPTCHA.

### Se algo der errado
| Sintoma | O que fazer |
|---|---|
| “Chave inválida ou revogada” | Gere outra chave e cole de novo. |
| “Failed to fetch” / não conecta | O sistema não está no ar (`docker compose up -d`) ou o endereço está errado. |
| Coleta automática não pega a aba já aberta | Recarregue a aba (F5). |
| Servidor em outro endereço | Em **Configurar**, troque o endereço; o Chrome pede permissão para ele. |

## 2. Importar lista de texto (Hunter → Importar lista de texto)
Cole um texto ou envie **qualquer arquivo** com emails no meio: `.txt`, `.csv`, `.doc`, `.docx`, `.xlsx`, `.html`, `.eml`… O sistema acha os emails sozinho (inclusive “nome [at] empresa.com.br”), tira duplicados, robôs e nomes de imagem, e liga o contato à empresa se o domínio já estiver cadastrado.
- Limite: 10 MB e 20.000 emails por vez. **PDF não é lido**: copie o texto e cole.
- Importar a mesma lista duas vezes não duplica nada.
- Respeita o limite de contatos do workspace (Operação).

## Depois de entrar
**Filtro → Validar emails.** Sem um verificador configurado, os emails ficam “arriscados” e não são enviados. Veja `docs/PROGRESSO.md` (pendências).
