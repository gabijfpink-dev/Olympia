# promo_bot — captura de promoções + link de afiliado + legenda/roteiro

Modelo de automação para quem divulga promoções (Amazon, Shopee, Shein,
Mercado Livre, AliExpress, Magalu, ...) com link de afiliada: lê mensagens
(de grupos do Telegram que você já participa, ou de qualquer texto que você
cole), reconhece a loja pelo link, gera o link de afiliado com o seu código
e já monta a legenda e um roteiro curto de vídeo.

## O que ele faz

1. **Extrai links** do texto da mensagem (`link_extractor.py`).
2. **Reconhece a loja** pelo domínio do link (`stores.py`).
3. **Converte para link de afiliado**, aplicando a regra que você configurou
   para aquela loja (`affiliate.py`) — nunca deixa passar link sem afiliação:
   se a loja não tiver regra configurada, o link é ignorado.
4. **Gera legenda e roteiro** prontos, com gancho + preço + CTA
   (`caption_generator.py`), usando preço/desconto extraídos do próprio texto
   quando presentes (`price_extractor.py`).
5. **Salva tudo** num log local (`output/promos.jsonl`) e, se configurado,
   **envia para um chat de revisão no Telegram** antes de você postar de
   verdade — o bot nunca posta sozinho num canal público.

## O que ele **não** faz (e por quê)

- **Não entra em grupo sozinho, não raspa conteúdo de terceiros além do
  texto das mensagens que chegam** nos grupos/canais em que você já está.
  Ele usa Telethon com a *sua própria conta* (não é bot token) — é o mesmo
  tipo de acesso que qualquer cliente de Telegram tem.
- **Não faz scraping das páginas de produto da Shopee/Shein/etc.** para
  puxar preço "oficial" — isso depende de cada plataforma e mudaria o
  tempo todo. Em vez disso, ele extrai preço/desconto que já vêm no texto da
  própria mensagem de promoção (que é o normal em grupo de promo).
- **Não inventa a URL de afiliado de cada rede.** Cada programa de afiliados
  (Amazon Associates, Shopee Affiliates, Shein via rede tipo Awin/Admitad,
  Mercado Livre Afiliados, etc.) tem seu próprio jeito de gerar o link — veja
  a seção "Configurando o link de afiliado por loja" abaixo. O que o
  promo_bot garante é: **nenhuma promoção sai sem passar pela regra que você
  configurou.**

## 1. Instalação

```bash
pip install -r requirements.txt
cp .env.example .env
```

Preencha no `.env`:

```
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_NAME=promo_bot
```

`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` são do **seu app**, criados de graça
em https://my.telegram.org (seção "API development tools"). Na primeira vez
que rodar `--listen`, o Telethon vai pedir seu número e o código que chega
por SMS/Telegram para logar — depois disso a sessão fica salva localmente
(`promo_bot.session`, nunca commitar).

## 2. Configuração (lojas, canais, afiliados)

Copie o modelo e edite:

```bash
cp config/promo.example.json config/minhas-promocoes.json
```

```jsonc
{
  "name": "minhas-promocoes",
  "telegram_channels": ["@nome_do_grupo_1", "@nome_do_grupo_2"],
  "allowed_stores": ["amazon", "shopee", "shein", "mercadolivre"],
  "keywords_exclude": ["esgotado", "encerrada"],
  "review_chat": "me",          // "me" = suas Mensagens Salvas, pra revisar antes de postar
  "output_log": "output/promos.jsonl",
  "affiliates": {
    "amazon": { "params": { "tag": "SEU-TAG-AMAZON-20" } },
    "shopee": { "template": "https://sua-rede-de-afiliados.com/redirect?url={url}&aff_id=SEU_ID" }
  }
}
```

### Configurando o link de afiliado por loja

Cada regra em `affiliates` aceita `params` (query params acrescentados na
própria URL) e/ou `template` (uma URL com `{url}` no lugar onde a URL
original entra). Use o que o seu programa de afiliados pedir:

- **Amazon Associates** — normalmente é só acrescentar `?tag=seu-id-20` no
  link do produto: use `"params": {"tag": "seu-id-20"}`.
- **Mercado Livre Afiliados** — o programa oficial gera o link pelo próprio
  painel/API deles (parâmetros `matt_word`/`matt_tool`); configure esses
  valores em `params` conforme o que o painel te der.
- **Shopee Affiliates / Shein** — normalmente você recebe um link de
  redirecionamento de uma rede de afiliados (Shopee Affiliate Program direto,
  ou redes tipo Awin/Admitad/Rakuten no caso da Shein). Use `template` com o
  formato de link que essa rede exigir.
- Qualquer loja nova: basta adicionar o domínio em `promo_bot/stores.py`
  (`STORE_DOMAINS`) e a regra correspondente em `affiliates`.

**Sempre confira nos Termos de Uso do programa de afiliados que você está
usando** se divulgar em grupo/canal é permitido e se o formato de link está
correto — isso varia por programa e por país.

## 3. Uso

Testar sem Telegram, com um texto qualquer (ex.: colado de um grupo):

```bash
python -m promo_bot.cli --config config/minhas-promocoes.json --texto "
🔥 Fone Bluetooth XYZ
de R$ 199,90 por R$ 89,90 (55% OFF)
https://shopee.com.br/produto-exemplo-i.123.456
"
```

Isso já imprime a legenda e o roteiro prontos, e grava em
`output/promos.jsonl` — útil pra ajustar os templates de legenda antes de
ligar o modo contínuo.

Modo contínuo (escuta os grupos/canais configurados e manda pra revisão):

```bash
python -m promo_bot.cli --config config/minhas-promocoes.json --listen
```

## 4. Testes

```bash
python -m unittest discover -s tests -v
```

Os testes cobrem extração de link/preço, conversão de afiliado e geração de
legenda/roteiro — tudo offline, sem Telegram nem rede.

## 5. Estrutura

```
promo_bot/
  stores.py             -> reconhece a loja pelo domínio do link
  link_extractor.py     -> extrai URLs de um texto
  price_extractor.py    -> extrai preço/desconto do texto (heurístico)
  affiliate.py           -> aplica a regra de afiliado da loja
  caption_generator.py  -> gera legenda + roteiro curto
  config.py              -> carrega config JSON + segredos do Telegram (.env)
  pipeline.py            -> junta tudo: texto -> promoções prontas
  publisher.py           -> salva em log local / envia pro chat de revisão
  telegram_listener.py  -> conecta no Telegram e chama o pipeline a cada mensagem
  cli.py                 -> ponto de entrada (--texto para teste, --listen para produção)
config/
  promo.example.json    -> modelo de configuração
```

## 6. Ideias de extensão

- Trocar `caption_generator.py` por uma chamada a um modelo de linguagem
  (ex.: API da Anthropic) para legendas mais variadas — os templates atuais
  são propositalmente simples e 100% offline/testáveis.
- Publicar automaticamente num canal próprio depois da revisão (hoje o fluxo
  padrão é gerar e mandar para "Mensagens Salvas" antes de postar manualmente).
- Resolver encurtadores (`amzn.to`, `shp.ee`, etc.) seguindo o redirecionamento
  antes de aplicar a regra de afiliado, caso a loja não aceite link já
  encurtado por outra afiliada.
