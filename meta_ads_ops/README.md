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

## Anúncios/campanhas comerciais vs. eleitorais

`authorization_category` no `clients/<nome>.json` só deve ser `"POLITICAL"`
para campanhas eleitorais (com a autorização e o disclaimer já configurados
no Meta). Para contas comerciais (ex. Bebetto), deixe `null`.
