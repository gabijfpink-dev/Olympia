# Agendador de postagens — Facebook, Instagram e LinkedIn

Publica **posts orgânicos** (não anúncios) nos horários que você define numa
planilha. Uma planilha por cliente, um `.env` por cliente.

Como funciona:

1. Você monta a agenda (`.xlsx`, `.csv` ou `.json`) com data/hora, redes, texto e mídia.
2. O Agendador de Tarefas do Windows (ou o cron) roda `python -m social_scheduler run`
   **a cada 10 minutos**.
3. Em cada rodada ele publica o que já passou do horário e ainda não saiu, e
   anota o resultado em `.state/social_<agenda>.json`. **Nenhum post sai duas vezes.**

> **Subir o mês inteiro de uma vez:** só o **Facebook** permite isso pela API.
> O comando `facebook-schedule` (Passo 4b) deixa todos os posts agendados no
> próprio Facebook, e eles saem mesmo com o computador desligado. **O Instagram e o
> LinkedIn não têm agendamento pela API** (nenhuma ferramenta consegue isso; as
> que "agendam" guardam o post num servidor e publicam na hora). Pra essas
> duas redes, o `run` precisa estar rodando no horário de cada post.
>
> **Recomendado: [modo nuvem](#modo-nuvem--subir-o-mês-e-esquecer-github-actions).**
> Você sobe a planilha e as imagens do mês uma vez, e o GitHub cuida do resto:
> agenda o Facebook e publica Instagram/LinkedIn na hora, com o PC desligado.

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
| `images` | IG: sim (ou vídeo) | nome do arquivo na pasta da planilha (ex.: `post01.jpg`) ou URL. Várias separadas por `\|` = carrossel |
| `video` | | URL do vídeo (Reels no Instagram, vídeo na Página) |
| `link` | | Facebook: post com prévia do link · LinkedIn: artigo |
| `link_title` | | título do artigo no LinkedIn |
| `alt_text` | | descrição da imagem (acessibilidade) |
| `status` | | `rascunho` = não publica (o padrão é publicar) |

Regras de cada rede (o `validate` confere tudo isso):

- **Instagram**: precisa de imagem ou vídeo. A imagem pode ser um arquivo da
  pasta (JPG ou PNG). O código hospeda a imagem como foto oculta da Página do
  Facebook, então `META_PAGE_ID` e `META_PAGE_ACCESS_TOKEN` precisam estar no
  `.env`. Vídeo (Reels) precisa ser URL pública. Carrossel tem no máximo 10
  itens e a legenda até 2.200 caracteres.
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

## Passo 4b — Facebook: agendar o mês inteiro de uma vez

```bash
# confere o que seria agendado
python -m social_scheduler facebook-schedule --schedule C:\MetaAPI\Posts\agenda.xlsx --env-file .env.cliente --dry-run

# agenda de verdade
python -m social_scheduler facebook-schedule --schedule C:\MetaAPI\Posts\agenda.xlsx --env-file .env.cliente
```

- Os posts aparecem em **Meta Business Suite → Planejador**, já com data e hora.
  Dá pra conferir, editar ou excluir por lá.
- O horário precisa estar pelo menos 15 min no futuro. Posts mais em cima da
  hora ficam para o `run` publicar normalmente.
- Pode rodar de novo à vontade (ex.: depois de incluir posts novos na
  planilha): o que já foi agendado não é agendado de novo.
- O Facebook aceita agendar até alguns meses à frente. Se recusar uma data
  muito distante, o erro aparece no relatório e os outros posts seguem.
- **Mudou a data ou o texto depois de agendar?** Edite direto no Planejador. Ou
  exclua o post lá, rode `reset --id ... --platform facebook` e agende de novo.
- O `run` ignora os posts já agendados no Facebook. Se o mesmo post também vai
  pro Instagram/LinkedIn, o `run` publica só nessas redes.

## Passo 5 — Deixar rodando sozinho (Instagram e LinkedIn)

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

## Modo nuvem — subir o mês e esquecer (GitHub Actions)

O workflow [`.github/workflows/social-scheduler.yml`](../.github/workflows/social-scheduler.yml)
roda a cada 10 minutos nos servidores do GitHub, de graça porque este
repositório é público. Em cada rodada, pra cada cliente, ele:

1. agenda no próprio Facebook os posts futuros (saem no minuto exato);
2. publica no Instagram e no LinkedIn o que já venceu (pode atrasar alguns
   minutos, porque o GitHub às vezes demora pra disparar);
3. salva o `estado.json`, pra nunca postar duas vezes.

**As planilhas e imagens NÃO vão neste repositório**, porque ele é público e
qualquer um veria os posts antes de saírem. Elas ficam num repositório privado só de conteúdo.

### Configuração (uma vez)

1. **Crie o repositório de conteúdo privado.** No GitHub: **New repository** →
   nome `olympia-conteudo` → marque **Private** → Create.
2. **Crie um token para o robô ler e gravar nele.** GitHub → sua foto →
   **Settings → Developer settings → Personal access tokens → Fine-grained
   tokens → Generate new token**:
   - Expiration: até 1 ano (anote a data pra renovar)
   - Repository access: **Only select repositories** → `olympia-conteudo`
   - Permissions → Repository permissions → **Contents: Read and write**
   - Generate e copie o token.
3. **No repositório Olympia → Settings → Secrets and variables → Actions:**
   - aba **Variables** → New variable: `SOCIAL_CONTENT_REPO` = `gabijfpink-dev/olympia-conteudo`
   - aba **Secrets** → New secret: `SOCIAL_CONTENT_TOKEN` = o token do passo 2
   - aba **Secrets** → um secret por cliente, `SOCIAL_ENV_<CLIENTE>`, com o
     **conteúdo inteiro do `.env` daquele cliente** (copie e cole o arquivo todo).
     O nome é o da pasta do cliente em maiúsculas, com `-` virando `_`:
     pasta `leandro-grass` → secret `SOCIAL_ENV_LEANDRO_GRASS`.
4. **O workflow precisa estar na branch principal** do Olympia, porque o GitHub só
   roda agendamentos de lá. Faça o merge desta branch.
5. **Teste:** aba **Actions** → "Postagens agendadas" → **Run workflow**. O
   log mostra, por cliente, o que foi agendado, publicado ou deu erro.

### Todo mês

No `olympia-conteudo`, uma pasta por cliente:

```
olympia-conteudo/
  bebetto/
    agenda.xlsx        ← a planilha do mês (colunas do Passo 3)
    post01.jpg         ← imagens citadas na coluna "images"
    post02.png
    ...
    estado.json        ← criado pelo robô; não mexa
  leandro-grass/
    agenda.xlsx
    ...
```

Pra subir: entre no repositório pelo navegador → pasta do cliente → **Add
file → Upload files** → arraste a planilha e todas as imagens → **Commit
changes**. Pronto: na próxima rodada (até 10 min) os posts do Facebook já
aparecem no Planejador do Business Suite, e Instagram/LinkedIn saem no horário.

- Pra corrigir um post que ainda não saiu, suba a planilha de novo (mesmo nome).
  Atenção: post do Facebook já agendado não muda com isso. Edite no Planejador.
- Pode deixar as linhas dos meses anteriores na planilha ou apagar. O que já
  saiu está no `estado.json` e não repete.
- Pra cancelar tudo de um cliente, apague a pasta dele (os posts do Facebook já
  agendados precisam ser excluídos no Planejador).

### Avisos

- **Se algo falhar, o GitHub manda e-mail** pra você (execução marcada como
  falha). Os detalhes ficam na aba Actions.
- **Token do LinkedIn vence em 60 dias.** Rode `linkedin-auth` no seu PC e
  atualize o secret `SOCIAL_ENV_<CLIENTE>` com o `.env` novo.
- O token do passo 2 também vence (na data que você escolheu). Gere outro e
  atualize `SOCIAL_CONTENT_TOKEN`.
- Em repositório público, o GitHub **desliga agendamentos após 60 dias sem
  nenhum commit no Olympia**. Ele avisa por e-mail antes; é só clicar pra
  reativar (ou fazer qualquer commit).

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
| `imagem local no Instagram precisa de META_PAGE_ID...` | preencha a Página do Facebook no `.env`: ela é usada pra hospedar a imagem |
| `Sem credenciais: crie o secret SOCIAL_ENV_...` | modo nuvem: falta o secret daquele cliente (veja "Configuração", passo 3) |
| `Instagram recusou a mídia (ERROR)` | imagem não é JPEG, proporção fora do aceito (entre 4:5 e 1.91:1) ou vídeo fora do padrão de Reels |
| `(#200) ... permission` / `(#10)` | falta permissão no token do Meta (Passo 1.4); gere de novo |
| `HTTP 401 ... linkedin-auth` | token do LinkedIn venceu: rode `linkedin-auth` |
| `HTTP 403` no LinkedIn | o app não tem o produto/permissão (Share on LinkedIn ou Community Management) |
| erro de versão no LinkedIn | troque `LINKEDIN_VERSION` no `.env` pra um mês recente (`AAAAMM`) |
| `Outra execução está em andamento` | normal se uma rodada anterior ainda está processando um vídeo; se travou, apague o `.lock` em `.state/` |
