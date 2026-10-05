# Agendador de postagens — Facebook, Instagram e LinkedIn

Publica **posts orgânicos** (não anúncios) nos horários que você define numa
planilha. Uma planilha por cliente, um `.env` por cliente.

Como funciona:

1. Você monta a agenda (`.xlsx`, `.csv` ou `.json`) com data/hora, redes, texto e mídia.
2. O Agendador de Tarefas do Windows (ou o cron) roda `python -m social_scheduler run`
   **a cada 10 minutos**.
3. Em cada rodada ele publica o que já passou do horário e ainda não saiu, e
   anota o resultado em `.state/social_<agenda>.json`. **Nenhum post sai duas vezes.**

> Por que não usar o agendamento nativo de cada rede? O Instagram e o LinkedIn
> não têm agendamento pela API, só o Facebook. Com o agendador próprio, as três
> redes funcionam do mesmo jeito.

---

## Passo 1 — Preparar o Instagram e a Página do Facebook (Meta)

1. **O Instagram precisa ser conta profissional** (Empresa ou Criador de conteúdo),
   **vinculada à Página do Facebook**. No app do Instagram: Configurações → Tipo
   de conta e ferramentas → Mudar para conta profissional. Depois, na Página:
   Configurações → Contas vinculadas → Instagram.
2. Crie um app em <https://developers.facebook.com/apps> → **Criar app** → tipo
   **Empresa (Business)**. Pode ser o mesmo app que você já usa pros anúncios.
3. No app, adicione os casos de uso/produtos de **Páginas** e **Instagram**
   (API do Instagram com login do Facebook).
4. Gere um token com estas permissões:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `instagram_basic`, `instagram_content_publish` (e `business_management`
   se a Página estiver num Business Manager).

   **Opção recomendada (token que não expira):** Business Manager →
   Configurações do negócio → Usuários → **Usuários do sistema** → Adicionar
   → em "Atribuir ativos", dê controle total da **Página** e da **conta do
   Instagram** → **Gerar novo token** escolhendo o app e as permissões acima.

   **Opção rápida (teste):** <https://developers.facebook.com/tools/explorer> →
   escolha o app → "Gerar token de acesso" marcando as permissões. Esse token
   dura ~1h. Pra durar mais, abra o
   [Depurador de token](https://developers.facebook.com/tools/debug/accesstoken/)
   e clique em **Estender token de acesso** (60 dias).

   Usando ativos que são seus (você é admin do app e da Página), **não precisa
   de App Review** — o app pode ficar em modo de desenvolvimento.

5. Crie o `.env` do cliente a partir do modelo:

   ```bash
   copy .env.social.example .env.cliente      # Windows
   cp .env.social.example .env.cliente        # Linux/Mac
   ```

   Cole o token do passo 4 em `META_USER_ACCESS_TOKEN` e rode:

   ```bash
   python -m social_scheduler meta-accounts --env-file .env.cliente
   ```

   Ele lista cada Página com `META_PAGE_ID`, `META_PAGE_ACCESS_TOKEN` e
   `META_IG_USER_ID`. Copie as três linhas da Página certa pro `.env.cliente`.
   (O token da Página gerado a partir de um token de usuário do sistema ou de
   um token estendido **não expira**.)

## Passo 2 — Preparar o LinkedIn

1. Em <https://www.linkedin.com/developers/apps> → **Create app**. O LinkedIn
   exige associar o app a uma Página de empresa. Verifique a Página quando ele pedir.
2. Aba **Products** → solicite:
   - **Sign In with LinkedIn using OpenID Connect**
   - **Share on LinkedIn** (dá a permissão `w_member_social` — postar no **perfil pessoal**)

   Pra postar como **Página de empresa** é preciso o **Community Management
   API** (permissão `w_organization_social`). Esse produto passa por aprovação
   do LinkedIn e costuma exigir um app separado, só com ele.
3. Aba **Auth** → em *Authorized redirect URLs* adicione
   `http://localhost:8000/callback`. Copie o **Client ID** e o
   **Client Secret** pro `.env.cliente` (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`).
4. Gere o token:

   ```bash
   python -m social_scheduler linkedin-auth --env-file .env.cliente
   # pra Página de empresa:
   python -m social_scheduler linkedin-auth --env-file .env.cliente --organization
   ```

   Abra o link que aparece, autorize e **cole a URL inteira** em que o
   navegador parar (a página não carrega, isso é normal). O comando imprime
   `LINKEDIN_ACCESS_TOKEN` e `LINKEDIN_AUTHOR_URN`. Copie os dois pro `.env.cliente`.

   ⚠️ **O token do LinkedIn vale 60 dias.** Coloque um lembrete na agenda pra
   rodar `linkedin-auth` de novo antes de vencer. Se vencer, o agendador avisa
   com "token expirado" e os posts do LinkedIn ficam aguardando.

## Passo 3 — Montar a agenda

Modelo: [`posts/exemplo_agenda.csv`](../posts/exemplo_agenda.csv). Abra no
Excel e salve como `.xlsx`, se preferir. Planilhas `.xlsx` ficam fora do git.

| coluna | obrigatório | exemplo / observação |
|---|---|---|
| `id` | sim | `2026-10-10-saude`. Precisa ser único e **não pode mudar depois**: é ele que impede post duplicado. |
| `publish_at` | sim | `10/10/2026 09:00` (horário de Brasília por padrão) |
| `platforms` | sim | `facebook, instagram, linkedin` (ou `fb, ig, li`) |
| `text` | | legenda/texto |
| `text_facebook`, `text_instagram`, `text_linkedin` | | texto diferente pra uma rede específica |
| `images` | IG: sim (ou vídeo) | URL(s) separadas por `\|`. Mais de uma = carrossel |
| `video` | | URL do vídeo (Reels no Instagram, vídeo na Página) |
| `link` | | Facebook: post com prévia do link · LinkedIn: artigo |
| `link_title` | | título do artigo no LinkedIn |
| `alt_text` | | descrição da imagem (acessibilidade) |
| `status` | | `rascunho` = não publica (o padrão é publicar) |

Regras de cada rede (o `validate` confere tudo isso):

- **Instagram**: precisa de imagem ou vídeo. **A imagem tem que estar numa URL
  pública** em JPEG, porque o Instagram baixa o arquivo da URL. Link de
  compartilhamento do Google Drive/Dropbox não serve; use o site do cliente,
  um bucket S3/Cloudinary etc. Carrossel tem no máximo 10 itens e a legenda até 2.200 caracteres.
- **Facebook**: aceita texto puro, link, imagem (URL ou arquivo local) e vídeo (URL).
- **LinkedIn**: aceita texto, imagem (URL ou arquivo local, até 20) **ou**
  link, mas não os dois juntos. Vídeo ainda não é suportado. Texto até 3.000
  caracteres. As `#hashtags` continuam clicáveis.

Caminho local de imagem é relativo à pasta da planilha.

## Passo 4 — Testar

```bash
# instalar dependências (uma vez)
pip install -r requirements.txt

# confere a agenda inteira
python -m social_scheduler validate --schedule C:\MetaAPI\Posts\agenda.xlsx

# simula: o que sairia se fosse 10/10 às 09:05? (não chama API nenhuma)
python -m social_scheduler run --schedule C:\MetaAPI\Posts\agenda.xlsx --dry-run --now "10/10/2026 09:05"
```

Pra testar de verdade, faça uma linha de teste com `publish_at` de agora,
rode sem `--dry-run` e confira nas redes:

```bash
python -m social_scheduler run --schedule C:\MetaAPI\Posts\agenda.xlsx --env-file .env.cliente
python -m social_scheduler status --schedule C:\MetaAPI\Posts\agenda.xlsx
```

## Passo 5 — Deixar rodando sozinho

### Windows (Agendador de Tarefas)

1. Crie `agendar_posts.bat` na pasta do projeto (ajuste os caminhos):

   ```bat
   @echo off
   cd /d C:\MetaAPI\Olympia
   call .venv\Scripts\activate
   if not exist logs mkdir logs
   python -m social_scheduler run --schedule C:\MetaAPI\Posts\agenda.xlsx --env-file .env.cliente >> logs\social.log 2>&1
   ```

   Pra vários clientes, repita a linha do `python` com a agenda e o `.env` de cada um.

2. Abra o **Agendador de Tarefas** → **Criar Tarefa...**
   - Geral: nome "Postagens redes sociais". Marque **Executar estando o
     usuário conectado ou não**.
   - Disparadores → Novo: **Diariamente**, início hoje 00:00. Marque
     **Repetir a tarefa a cada: 10 minutos** por **Indefinidamente**.
   - Ações → Novo: *Iniciar um programa* → escolha o `agendar_posts.bat`.
   - Condições: desmarque "Iniciar somente se estiver ligado à rede elétrica"
     (se for notebook).
3. **O computador precisa estar ligado** nos horários dos posts. Se ficar
   desligado, ao voltar ele publica o que venceu nas **últimas 6 horas**. O
   que for mais antigo **não sai sozinho** (aparece como "atrasado demais"),
   pra nunca publicar fora de contexto. Ajuste com `--max-late-hours`.

### Linux / servidor (cron)

```cron
*/10 * * * * cd /opt/Olympia && .venv/bin/python -m social_scheduler run --schedule posts/agenda.xlsx --env-file .env.cliente >> logs/social.log 2>&1
```

Um servidor sempre ligado (VPS de baixo custo) evita o problema do computador desligado.

## Dia a dia

- **Adicionar posts:** é só incluir linhas novas na planilha. Na próxima rodada elas já entram.
- **Cancelar um post:** escreva `rascunho` em `status` ou apague a linha antes do horário.
- **Ver o que saiu / falhou:** `python -m social_scheduler status --schedule ...`
- **Falhas:** cada rede de cada post é tentada até 3 vezes (uma por rodada).
  Uma rede falhando não impede as outras. Depois de 3 falhas ele desiste.
  Corrija o problema e libere de novo:

  ```bash
  python -m social_scheduler reset --schedule ... --id 2026-10-10-saude --platform instagram
  ```

- **Repostar algo** que já saiu: use o mesmo `reset`, ou crie uma linha com `id` novo.
- **Editar texto depois de publicado** não altera o post na rede. Edite direto no app.

## Erros comuns

| mensagem | o que fazer |
|---|---|
| `Variáveis ausentes no .env: ...` | preencha a variável no `.env.cliente` (Passos 1 e 2) |
| `instagram: imagens precisam ser URLs públicas` | hospede a imagem num endereço público (veja o Passo 3) |
| `Instagram recusou a mídia (ERROR)` | imagem não é JPEG, proporção fora do aceito (entre 4:5 e 1.91:1) ou vídeo fora do padrão de Reels |
| `(#200) ... permission` / `(#10)` | falta permissão no token do Meta (Passo 1.4); gere de novo |
| `HTTP 401 ... linkedin-auth` | token do LinkedIn venceu: rode `linkedin-auth` |
| `HTTP 403` no LinkedIn | o app não tem o produto/permissão (Share on LinkedIn ou Community Management) |
| erro de versão no LinkedIn | troque `LINKEDIN_VERSION` no `.env` pra um mês recente (`AAAAMM`) |
| `Outra execução está em andamento` | normal se uma rodada anterior ainda está processando um vídeo; se travou, apague o `.lock` em `.state/` |
