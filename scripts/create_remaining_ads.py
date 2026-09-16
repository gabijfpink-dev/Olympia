import json
import time
import requests
import pandas as pd
from config import *

GRAPH = f"https://graph.facebook.com/{API_VERSION}"

# CAMPAIGN_ID agora vem do config.py (veja config.example.py).
CAMPAIGN_ID = CAMPAIGN_ID

# ===============================
# Lê os creatives criados
# ===============================

creatives = pd.read_excel("creatives_criados.xlsx")

creative_map = {
    str(r.city).strip().lower(): str(r.creative_id)
    for _, r in creatives.iterrows()
}

# ===============================
# Busca TODOS os conjuntos
# ===============================

def get_all_adsets():

    url = f"{GRAPH}/{CAMPAIGN_ID}/adsets"

    params = {
        "fields": "id,name",
        "limit": 500,
        "access_token": ACCESS_TOKEN
    }

    todos = []

    while True:

        r = requests.get(url, params=params, timeout=120)

        if r.status_code != 200:
            print(r.status_code)
            print(r.json())
            exit()

        dados = r.json()

        todos.extend(dados.get("data", []))

        if "paging" in dados and "next" in dados["paging"]:
            url = dados["paging"]["next"]
            params = {}
        else:
            break

    return todos


adsets = get_all_adsets()

print(f"Adsets encontrados: {len(adsets)}")

criados = 0
pulados = 0
erros = 0

# ===============================
# Cria anúncios faltantes
# ===============================

for adset in adsets:

    cidade = adset["name"].strip()

    if cidade.lower() in [
        "são bernardo do campo",
        "sao bernardo do campo"
    ]:
        print("IGNORANDO MODELO:", cidade)
        continue

    creative_id = creative_map.get(cidade.lower())

    if not creative_id:
        print("SEM CREATIVE:", cidade)
        continue

    ads = requests.get(
        f"{GRAPH}/{adset['id']}/ads",
        params={
            "fields": "id",
            "access_token": ACCESS_TOKEN
        },
        timeout=120
    ).json()

    if len(ads.get("data", [])) > 0:
        pulados += 1
        print("PULANDO:", cidade)
        continue

    body = {
        "name": "01",
        "adset_id": adset["id"],
        "creative": json.dumps({
            "creative_id": creative_id
        }),
        "status": "PAUSED",
        "access_token": ACCESS_TOKEN
    }

    r = requests.post(
        f"{GRAPH}/{AD_ACCOUNT_ID}/ads",
        data=body
    )

    resp = r.json()

    if "id" in resp:

        criados += 1
        print("OK:", cidade)

    else:

        erros += 1

        print("ERRO:", cidade)
        print(resp)

        if (
            "error" in resp
            and (
                resp["error"].get("code") in (4, 17, 32, 613)
                or resp["error"].get("error_subcode") == 2446079
            )
        ):
            print("\n===== LIMITE DA API =====")
            print("Espere 15 minutos e rode novamente.")
            break

    time.sleep(0.35)

print("\n==============================")
print("Criados :", criados)
print("Pulados :", pulados)
print("Erros   :", erros)
print("==============================")