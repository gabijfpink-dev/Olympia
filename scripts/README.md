# Scripts operacionais — adicionar cidades/campanhas no Meta Ads

Estes são os scripts que já estão em uso, ajustados para dois problemas que
encontramos: `instagram_user_id` (campo que a API não reconhece — corrigido
para `instagram_actor_id`) e o `CAMPAIGN_ID`/`MODEL_ADSET_ID`/`PAGE_ID` que
antes estavam hardcoded em cada arquivo (agora vêm todos do `config.py`, então
trocar de campanha é editar um lugar só).

## Setup (uma vez por máquina)

1. Copie `config.example.py` para `config.py` na mesma pasta.
2. Preencha `ACCESS_TOKEN`, `AD_ACCOUNT_ID`, `CAMPAIGN_ID`, `MODEL_ADSET_ID`,
   `PAGE_ID`, `INSTAGRAM_ACTOR_ID` com os valores reais.
3. **Nunca** commite esse `config.py` — ele já está no `.gitignore`.
4. `pip install pandas requests openpyxl`

## Para adicionar mais cidades (conjuntos de anúncios) na campanha atual

1. Prepare a planilha do novo bloco com as colunas:
   `city, url, image, primary_text, headline, description` (mínimo — a
   coluna `budget` só é usada se você adaptar `create_missing_adsets.py`
   para não copiar o orçamento do conjunto-modelo).
2. Coloque as imagens do bloco em `IMAGES_FOLDER` e aponte `EXCEL_FILE`
   no `config.py` para a planilha nova.
3. Rode, nessa ordem:

   ```
   python create_missing_adsets.py    # cria o adset de cada cidade nova,
                                       # copiando targeting do MODEL_ADSET_ID
   python corrigir_hashes_bloco.py    # sobe as imagens que ainda não têm hash
   python create_creatives.py         # cria o criativo de cada cidade
   python create_remaining_ads.py     # cria o anúncio ligando adset + criativo
   ```

   Todos são reentrantes: se pararem por rate limit (a API do Meta te avisa
   e o script para sozinho), é só rodar de novo — eles pulam o que já foi
   feito.

4. Quando quiser colocar o bloco no ar de verdade:

   ```
   python finalizar_bloco_atual.py
   ```

   Esse é o único script que muda o `status` para `ACTIVE` (os outros criam
   tudo pausado). Ele só ativa o que ainda não está ativo, então também é
   seguro rodar mais de uma vez.

## Para trocar de campanha (ou adicionar uma campanha nova)

A campanha em si **não é criada por estes scripts** — crie-a manualmente no
Gerenciador de Anúncios (nome, objetivo, orçamento) e monte também um
conjunto de anúncios "modelo" dentro dela com o targeting-base que os demais
vão copiar. Depois:

1. Pegue o `Campaign ID` e o `Adset ID` do modelo na URL do Gerenciador de
   Anúncios.
2. Atualize `CAMPAIGN_ID` e `MODEL_ADSET_ID` no `config.py`.
3. Siga o mesmo fluxo de "adicionar mais cidades" acima.

Se for rodar duas campanhas em paralelo, a forma mais segura com esses
scripts é usar uma cópia da pasta por campanha (cada uma com seu próprio
`config.py` e seus próprios `.xlsx` de progresso), já que os `.xlsx`
(`imagens_subidas.xlsx`, `creatives_criados.xlsx`) não distinguem campanha.

## Observações

- `authorization_category: "POLITICAL"` em `create_creatives.py` foi mantido
  de propósito — assumindo que a autorização de anúncios eleitorais da
  Página/conta já está feita, como confirmado.
- Rate limit: todos os scripts agora tratam os mesmos códigos de erro da
  Meta (`4, 17, 32, 613` ou subcódigo `2446079`) e param sozinhos em vez de
  continuar tentando.
