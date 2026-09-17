# Automação Meta Ads

Três ferramentas complementares:

- **[`meta_ads_automation/`](meta_ads_automation)** — cria uma campanha do
  zero (Campanha → Conjunto de Anúncios → Anúncios) a partir de um JSON
  único, com legendas, imagens e qualquer configuração que você enviar.
  Veja detalhes abaixo.
- **[`meta_ads_ops/`](meta_ads_ops/README.md)** — uso do dia a dia: preenche
  cidades faltantes (adset + criativo + anúncio) numa campanha **que já
  existe**, clonando um adset-modelo, a partir de uma planilha por cliente.
  Cada cliente/campanha é um arquivo `clients/<nome>.json` explícito — sem
  IDs hardcoded em script, sem risco de misturar campanhas de clientes
  diferentes.
- **[`promo_bot/`](promo_bot/README.md)** — captura promoções (Amazon,
  Shopee, Shein, Mercado Livre, ...) de grupos do Telegram ou de qualquer
  texto colado, converte o link para o seu link de afiliada e já gera
  legenda + roteiro de vídeo prontos para postar.

O restante deste README cobre o `meta_ads_automation` (criação do zero). Para
o fluxo operacional (adicionar cidades numa campanha existente), veja
[`meta_ads_ops/README.md`](meta_ads_ops/README.md).

---

Script em Python que sobe, de uma vez só, a hierarquia completa no Meta Ads
(Facebook/Instagram): **Campanha → Conjunto de Anúncios → Anúncios**, com
legendas, textos, imagens (upload automático) e qualquer outra configuração
que você enviar — tudo a partir de um único arquivo JSON.

Usa o SDK oficial (`facebook-business`) e a Marketing API do Meta.

## 1. Pré-requisitos

- Uma conta de anúncios do Meta (Ad Account ID).
- Uma Página do Facebook vinculada (Page ID) — necessária para publicar os anúncios.
- Um app em https://developers.facebook.com/apps com o produto **Marketing API** ativado.
- Um **token de acesso** com as permissões `ads_management` e `ads_read`
  (recomendado: token de usuário do sistema de longa duração, gerado no
  Business Manager, para não expirar a cada automação).

## 2. Instalação

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edite o `.env` com suas credenciais:

```
META_APP_ID=...
META_APP_SECRET=...
META_ACCESS_TOKEN=...
META_AD_ACCOUNT_ID=...       # com ou sem o prefixo "act_"
META_API_VERSION=v21.0
```

## 3. Formato do arquivo de configuração

Veja o exemplo completo em [`config/example_campaign.json`](config/example_campaign.json).
Estrutura geral:

```jsonc
{
  "defaults": {
    "page_id": "SUA_PAGINA_ID",           // usado por todos os anúncios, se não sobrescrito
    "instagram_actor_id": "SEU_IG_ID"     // opcional, para posicionamento no Instagram
  },
  "campaigns": [
    {
      "name": "Nome da campanha",
      "objective": "OUTCOME_TRAFFIC",      // OUTCOME_SALES, OUTCOME_LEADS, OUTCOME_ENGAGEMENT, ...
      "status": "PAUSED",                  // ou "ACTIVE" para publicar já ativo
      "special_ad_categories": [],
      "settings": { "buying_type": "AUCTION" },   // qualquer campo extra da API de Campaign
      "ad_sets": [
        {
          "name": "Nome do conjunto de anúncios",
          "status": "PAUSED",
          "daily_budget": 5000,           // em centavos (5000 = R$50,00)
          "billing_event": "IMPRESSIONS",
          "optimization_goal": "LINK_CLICKS",
          "bid_amount": 150,
          "start_time": "2026-09-20T09:00:00-0300",
          "targeting": {                  // objeto de targeting completo da API
            "geo_locations": { "countries": ["BR"] },
            "age_min": 25,
            "age_max": 45,
            "publisher_platforms": ["facebook", "instagram"]
          },
          "settings": { },                // qualquer campo extra da API de AdSet
          "ads": [
            {
              "name": "Nome do anúncio",
              "status": "PAUSED",
              "creative": {
                "message": "Texto principal / legenda do anúncio",
                "caption": "seusite.com.br",
                "headline": "Título",
                "description": "Descrição menor",
                "link": "https://seusite.com.br/pagina",
                "image_path": "https://.../imagem.jpg",   // URL ou caminho local
                "call_to_action_type": "SHOP_NOW",
                "settings": { }            // qualquer campo extra da API de AdCreative
              }
            }
          ]
        }
      ]
    }
  ]
}
```

Pontos importantes:

- **Qualquer campo que a Marketing API aceite** pode ser passado dentro de
  `settings` (em campanha, ad set, criativo ou anúncio) — ele é mesclado por
  cima dos campos padrão, então nada fica limitado ao que o script já conhece.
- `image_path` aceita **URL** (baixa automaticamente), **caminho local** ou,
  se você já tiver feito upload antes, pode informar diretamente `image_hash`.
- Para formatos avançados (carrossel, vídeo, etc.), envie o
  `object_story_spec` completo dentro de `creative` — o script usa exatamente
  o que for enviado, sem tentar reconstruir.
- `page_id` pode ser definido uma vez em `defaults` ou sobrescrito por
  conjunto de anúncios (`ad_sets[].page_id`).

## 4. Uso

**Sempre valide primeiro em modo dry-run** (não chama a API de verdade,
só valida e mostra o que seria criado):

```bash
python -m meta_ads_automation.cli --config config/example_campaign.json --dry-run
```

Quando estiver tudo certo, rode de verdade:

```bash
python -m meta_ads_automation.cli --config config/example_campaign.json
```

Outras opções:

```bash
# Salva os IDs criados em arquivo
python -m meta_ads_automation.cli --config config/example_campaign.json --output resultado.json

# Continua criando os demais itens mesmo se algum falhar
python -m meta_ads_automation.cli --config config/example_campaign.json --continue-on-error

# Logs detalhados
python -m meta_ads_automation.cli --config config/example_campaign.json --verbose
```

Recomendação: mantenha `status: "PAUSED"` por padrão e ative manualmente (ou
via um segundo passo da automação) depois de conferir tudo no Gerenciador de
Anúncios.

## 5. Testes

```bash
python -m unittest discover -s tests -v
```

Os testes rodam em dry-run / com mocks e não fazem nenhuma chamada de rede.

## 6. Estrutura do projeto

```
meta_ads_automation/
  client.py     -> carrega credenciais e inicializa a API
  image_utils.py-> upload/resolução de imagens (local, URL ou hash existente)
  creator.py    -> monta os parâmetros e cria Campanha -> AdSet -> Criativo -> Anúncio
  cli.py        -> ponto de entrada de linha de comando
config/
  example_campaign.json -> modelo de configuração
tests/
  test_creator.py -> testes unitários (dry-run, sem rede)
```

## 7. Erros comuns

- **"page_id" ausente**: defina `defaults.page_id` ou `ad_sets[].page_id`.
- **Token expirado / sem permissão**: gere um token de usuário do sistema com
  `ads_management` no Business Manager.
- **Imagem rejeitada**: confira formato (JPG/PNG) e tamanho mínimo exigido
  pelo Meta.
- **Conta em revisão / anúncio reprovado**: normal em contas novas; o
  anúncio é criado com sucesso, mas fica em análise antes de veicular.
