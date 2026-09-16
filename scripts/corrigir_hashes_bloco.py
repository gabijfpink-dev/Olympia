import os
import time
import requests
import pandas as pd

from config import *

GRAPH = f"https://graph.facebook.com/{API_VERSION}"

PLANILHA = EXCEL_FILE
HASHES = "imagens_subidas.xlsx"

bloco = pd.read_excel(PLANILHA)

if os.path.exists(HASHES):
    hashes = pd.read_excel(HASHES)
else:
    hashes = pd.DataFrame(columns=["city", "image", "image_hash"])

# garante colunas
for col in ["city", "image", "image_hash"]:
    if col not in hashes.columns:
        hashes[col] = None

hash_map = {}

for _, row in hashes.iterrows():
    cidade = str(row.get("city", "")).strip().lower()
    h = row.get("image_hash")

    if cidade and pd.notna(h) and str(h).strip():
        hash_map[cidade] = str(h).strip()


corrigidos = 0
ja_ok = 0
erros = 0

for _, row in bloco.iterrows():

    cidade = str(row["city"]).strip()
    imagem = str(row["image"]).strip()

    chave = cidade.lower()

    # já possui hash
    if chave in hash_map:
        print("JÁ OK:", cidade)
        ja_ok += 1
        continue

    caminho = os.path.join(IMAGES_FOLDER, imagem)

    if not os.path.exists(caminho):
        print("FALTOU ARQUIVO:", cidade, caminho)
        erros += 1
        continue

    print("SUBINDO:", cidade)

    with open(caminho, "rb") as img:

        r = requests.post(
            f"{GRAPH}/{AD_ACCOUNT_ID}/adimages",
            files={"filename": img},
            data={"access_token": ACCESS_TOKEN},
            timeout=120
        )

    try:
        resp = r.json()
    except Exception:
        print("ERRO RESPOSTA:", cidade, r.text)
        erros += 1
        continue

    if "error" in resp:

        print("ERRO API:", cidade)
        print(resp)

        erro = resp["error"]

        if (
            erro.get("code") in (4, 17, 32, 613)
            or erro.get("error_subcode") == 2446079
        ):
            print("\nRATE LIMIT. PAREI AQUI.")
            print("Espere 15 minutos e rode novamente.")
            break

        erros += 1
        continue

    imagens = resp.get("images", {})

    if not imagens:
        print("SEM HASH NA RESPOSTA:", cidade, resp)
        erros += 1
        continue

    dados = list(imagens.values())[0]
    image_hash = dados.get("hash")

    if not image_hash:
        print("SEM HASH:", cidade, resp)
        erros += 1
        continue

    print("OK:", cidade, image_hash)

    # remove registro antigo daquela cidade
    hashes = hashes[
        hashes["city"].astype(str).str.strip().str.lower()
        != chave
    ]

    novo = pd.DataFrame([{
        "city": cidade,
        "image": imagem,
        "image_hash": image_hash
    }])

    hashes = pd.concat(
        [hashes, novo],
        ignore_index=True
    )

    # salva a cada sucesso
    hashes.to_excel(
        HASHES,
        index=False
    )

    hash_map[chave] = image_hash

    corrigidos += 1

    time.sleep(2)


print("\n==============================")
print("JÁ TINHAM HASH :", ja_ok)
print("HASHES CORRIGIDOS:", corrigidos)
print("ERROS          :", erros)
print("==============================")