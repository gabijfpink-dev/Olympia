import os
import requests
import pandas as pd
import json
import time
from config import *

GRAPH = f"https://graph.facebook.com/{API_VERSION}"

# PAGE_ID e INSTAGRAM_ACTOR_ID agora vêm do config.py (veja config.example.py).
ARQUIVO_CREATIVES = "creatives_criados.xlsx"

planilha = pd.read_excel(EXCEL_FILE)
imagens = pd.read_excel("imagens_subidas.xlsx")

if os.path.exists(ARQUIVO_CREATIVES):
    resultado = pd.read_excel(ARQUIVO_CREATIVES).to_dict("records")
else:
    resultado = []

cidades_ja_criadas = {
    str(r["city"]).strip().lower() for r in resultado if r.get("city")
}


def salvar():
    pd.DataFrame(resultado).to_excel(ARQUIVO_CREATIVES, index=False)


for _, row in planilha.iterrows():

    cidade = row["city"]

    if str(cidade).strip().lower() in cidades_ja_criadas:
        print("JÁ TEM CREATIVE:", cidade)
        continue

    try:

        img = imagens.loc[
            imagens["city"] == cidade,
            "image_hash"
        ].values[0]

    except Exception:

        print("SEM HASH:", cidade)
        continue

    body = {
        "name": f"{cidade} Creative",
        "authorization_category": "POLITICAL",
        "object_story_spec": json.dumps(
            {
                "page_id": PAGE_ID,
                "instagram_actor_id": INSTAGRAM_ACTOR_ID,
                "link_data": {
                    "link": row["url"],
                    "message": row["primary_text"],
                    "name": row["headline"],
                    "description": row["description"],
                    "image_hash": img,
                    "call_to_action": {
                        "type": "LEARN_MORE"
                    }
                }
            },
            ensure_ascii=False
        ),
        "access_token": ACCESS_TOKEN
    }

    r = requests.post(
        f"{GRAPH}/{AD_ACCOUNT_ID}/adcreatives",
        data=body,
        timeout=120
    )

    resp = r.json()

    if "id" in resp:

        print("OK:", cidade)

        resultado.append({
            "city": cidade,
            "creative_id": resp["id"]
        })

        cidades_ja_criadas.add(str(cidade).strip().lower())

        salvar()

    else:

        print("ERRO:", cidade)
        print(resp)

        if (
            "error" in resp
            and (
                resp["error"].get("code") in (4, 17, 32, 613)
                or resp["error"].get("error_subcode") == 2446079
            )
        ):
            print("Limite da API atingido. Espere 15 minutos e rode novamente.")
            break

    time.sleep(0.35)

salvar()

print("\nFINALIZADO")
print("Creatives:", len(resultado))
