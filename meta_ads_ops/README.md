# meta_ads_ops — adicionar cidades/campanhas numa campanha já existente

Ferramenta operacional para o fluxo real do dia a dia: você já tem uma
campanha rodando no Meta Ads com um conjunto de anúncios "modelo" (uma
cidade), e quer preencher as cidades que faltam (adset + criativo + anúncio)
a partir de uma planilha — sem depender de arquivos `.xlsx` de progresso
espalhados nem de IDs de campanha hardcoded em scripts diferentes.

## Por que isto existe

Os scripts anteriores tinham `CAMPAIGN_ID`/`MODEL_ADSET_ID`/`PAGE_ID`
escritos direto no código-fonte, então trocar de cliente/campanha exigia
editar vários arquivos — e foi exatamente isso que causou a confusão entre
duas campanhas diferentes (Vicentinho e Bebetto) usando o mesmo `CAMPAIGN_ID`
por engano. Aqui:

- **Cada cliente é um arquivo JSON explícito** (`clients/<nome>.json`),
  sempre passado com `--client` — impossível rodar "sem querer" na campanha
  errada.
- **O que já existe é sempre consultado ao vivo na API** (lista de adsets da
  campanha + anúncios de cada adset), não em planilhas `.xlsx` de progresso
  que podem ficar desatualizadas ou serem sobrescritas por engano.
- **Rate limit tratado igual em todo lugar** (códigos `4, 17, 32, 613` e
  subcódigo `2446079`).

## Setup

```bash
pip install -r requirements.txt
cp config.example.py config.py        # preencha ACCESS_TOKEN, AD_ACCOUNT_ID, API_VERSION
cp clients/example.json clients/<seu-cliente>.json   # preencha campaign_id, model_adset_id, page_id...
```

`config.py` nunca deve ser commitado (já está no `.gitignore`). Os arquivos
`clients/*.json` **podem** ir pro Git — não têm segredo, só IDs de campanha.

## Uso

Preencha a planilha do cliente com as colunas: `city, url, image,
primary_text, headline, description` (uma linha por cidade; o nome da coluna
`image` é o nome do arquivo dentro de `images_folder`).

```bash
# 1. Simula, sem chamar a API de verdade
python -m meta_ads_ops.cli sync --client clients/bebetto.json --dry-run

# 2. Cria de verdade (tudo pausado): adset -> imagem -> criativo -> anúncio,
#    só para as cidades que ainda não têm anúncio
python -m meta_ads_ops.cli sync --client clients/bebetto.json

# 3. Quando quiser colocar no ar o que foi criado
python -m meta_ads_ops.cli activate --client clients/bebetto.json
```

Rodar `sync` de novo é seguro: cidade que já tem adset e anúncio é
automaticamente pulada (checado ao vivo na API, não em arquivo local).

## Cidade não encontrada

Se a busca de geolocalização não achar a cidade com segurança (nome
ambíguo, mais de uma cidade com o mesmo nome em estados diferentes etc.):

- **Sem `fallback_state` configurado**: ela entra em `cidades_nao_encontradas`
  no resumo e nada é criado — crie o adset manualmente no Gerenciador de
  Anúncios e rode `sync` de novo; ele reconhece o adset por nome e segue
  para criativo + anúncio.
- **Com `fallback_state` configurado** (ex. `"São Paulo"` no
  `clients/bebetto.json`): o adset é criado automaticamente com o nome da
  cidade, mas segmentado pelo **estado inteiro** em vez de um raio de
  cidade. Essas cidades aparecem separadas em `adsets_fallback_estado` no
  resumo, para você revisar o targeting delas depois se quiser refinar.

## Conta nova, sem campanha ainda (bootstrap)

Quando é uma conta de anúncios nova e a campanha ainda não existe no Meta,
use `bootstrap` pra criar do zero: campanha (com orçamento por campanha —
CBO) → 1º conjunto de anúncios (a primeira região/cidade) → imagem →
criativo → anúncio, tudo pausado. Depois disso o `campaign_id` e
`model_adset_id` resultantes viram um `clients/<nome>.json` normal, e o
`sync`/`activate` seguem exatamente como pra qualquer outro cliente.

```bash
cp clients/example.bootstrap.json clients/<cliente>.bootstrap.json   # preencha
python -m meta_ads_ops.cli bootstrap --bootstrap-config clients/<cliente>.bootstrap.json --dry-run
python -m meta_ads_ops.cli bootstrap --bootstrap-config clients/<cliente>.bootstrap.json --output resultado_bootstrap.json
```

O comando imprime `campaign_id` e `model_adset_id` — copie os dois pro
`clients/<cliente>.json` (junto com `page_id`, `instagram_actor_id`,
`authorization_category`, `excel_file`, `images_folder`) e siga com `sync`
normalmente a partir da segunda cidade/região em diante (a planilha pode
repetir a primeira linha — ela já vai aparecer como "pronta", já que o
adset criado no bootstrap tem o mesmo nome).

Campos do `clients/<cliente>.bootstrap.json`:
- `campaign_name`, `page_id`, `region` (nome da 1ª cidade/região),
  `daily_budget_cents` (orçamento diário da campanha inteira, em centavos —
  ex.: `500000` = R$ 5.000,00/dia)
- `url`, `image`, `images_folder`, `primary_text`, `headline`, `description`
  (o 1º criativo — igual às colunas da planilha normal)
- Opcionais: `objective` (padrão `"OUTCOME_TRAFFIC"`), `optimization_goal`
  (padrão `"LINK_CLICKS"`), `billing_event` (padrão `"IMPRESSIONS"`),
  `instagram_actor_id`, `authorization_category` (`"POLITICAL"` pra
  eleitoral), `special_ad_categories` (ex.: `["ISSUES_ELECTIONS_POLITICS"]`
  — obrigatório em campanha eleitoral), `age_min`/`age_max`/`genders`,
  `radius_km`, `fallback_state` (mesmo mecanismo do `sync`, caso a região
  não seja encontrada com segurança).

## Aumentar investimento numa cidade que já está rodando

Pra criar um **segundo** conjunto de anúncios numa cidade que já tem um
(ex.: aumentar o investimento, ou testar um criativo novo em paralelo sem
mexer no que já está rodando), use `--adset-suffix`:

```bash
python -m meta_ads_ops.cli sync --client clients/bebetto.json \
    --adset-suffix " - Aumento" --output resultado_aumento.json
python -m meta_ads_ops.cli activate --client clients/bebetto.json \
    --adset-suffix " - Aumento" --output resultado_aumento_activate.json
```

O nome do novo adset vira `"<Cidade> - Aumento"` (não colide com o adset
original), mas a busca de geolocalização continua usando o nome puro da
cidade da planilha. Use uma planilha separada (pode ter as mesmas cidades,
com imagem/texto novos) e passe o mesmo `--adset-suffix` no `sync` e no
`activate` pra apontar sempre pro mesmo conjunto de adsets "extras".

## Anúncios/campanhas comerciais vs. eleitorais

`authorization_category` no `clients/<nome>.json` só deve ser `"POLITICAL"`
para campanhas eleitorais (com a autorização e o disclaimer já configurados
no Meta). Para contas comerciais (ex. Bebetto), deixe `null`.
