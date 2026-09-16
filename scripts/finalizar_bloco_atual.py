import json
import time
import unicodedata
import requests
import pandas as pd

from config import *

GRAPH = f"https://graph.facebook.com/{API_VERSION}"

# CAMPAIGN_ID agora vem do config.py (veja config.example.py).
CAMPAIGN_ID = CAMPAIGN_ID

PLANILHA_BLOCO = EXCEL_FILE
ARQUIVO_CREATIVES = "creatives_criados.xlsx"


# =========================================================
# NORMALIZAÇÃO
# =========================================================

def normalizar(txt):
    txt = str(txt).strip()
    txt = unicodedata.normalize("NFKD", txt)
    txt = "".join(
        c for c in txt
        if not unicodedata.combining(c)
    )
    return txt.lower()


# =========================================================
# RATE LIMIT
# =========================================================

class RateLimitMeta(Exception):
    pass


def verificar_resposta(r):

    try:
        resp = r.json()

    except Exception:

        print("\nRESPOSTA INVÁLIDA DA META:")
        print(r.text)

        return {
            "error": {
                "message": "Resposta inválida"
            }
        }

    erro = resp.get("error")

    if erro:

        codigo = erro.get("code")
        subcode = erro.get("error_subcode")

        print("\nERRO API:")
        print(resp)

        if codigo in [4, 17, 32, 613] or subcode == 2446079:

            raise RateLimitMeta()

    return resp


def meta_get(url, params):

    r = requests.get(
        url,
        params=params,
        timeout=120
    )

    return verificar_resposta(r)


def meta_post(url, data):

    r = requests.post(
        url,
        data=data,
        timeout=120
    )

    return verificar_resposta(r)


# =========================================================
# CARREGAR CIDADES DO BLOCO
# =========================================================

bloco = pd.read_excel(PLANILHA_BLOCO)

cidades_bloco = {}

for cidade in bloco["city"].dropna():

    cidade = str(cidade).strip()

    cidades_bloco[
        normalizar(cidade)
    ] = cidade


print("\n========================================")
print("CIDADES DO BLOCO:", len(cidades_bloco))
print("========================================")


# =========================================================
# CARREGAR CREATIVES LOCALMENTE
# =========================================================

creatives = pd.read_excel(ARQUIVO_CREATIVES)

creative_map = {}


for _, row in creatives.iterrows():

    cidade = str(
        row.get("city", "")
    ).strip()

    creative_id = row.get(
        "creative_id"
    )

    if not cidade:
        continue

    if pd.isna(creative_id):
        continue

    creative_map[
        normalizar(cidade)
    ] = str(creative_id).strip()


print(
    "Creatives carregados:",
    len(creative_map)
)


# =========================================================
# BUSCAR TODOS OS ADSETS UMA VEZ
# =========================================================

def buscar_adsets():

    print("\nBuscando conjuntos...")

    url = (
        f"{GRAPH}/"
        f"{CAMPAIGN_ID}/adsets"
    )

    params = {
        "fields":
            "id,name,status,effective_status",
        "limit": 500,
        "access_token": ACCESS_TOKEN
    }

    todos = []

    while True:

        resp = meta_get(
            url,
            params
        )

        todos.extend(
            resp.get("data", [])
        )

        proxima = (
            resp
            .get("paging", {})
            .get("next")
        )

        if not proxima:
            break

        url = proxima
        params = {}

    print(
        "Conjuntos carregados:",
        len(todos)
    )

    return todos


# =========================================================
# BUSCAR TODOS OS ADS UMA VEZ
# =========================================================

def buscar_ads():

    print("\nBuscando anúncios existentes...")

    url = (
        f"{GRAPH}/"
        f"{CAMPAIGN_ID}/ads"
    )

    params = {
        "fields":
            "id,name,status,effective_status,adset_id",
        "limit": 500,
        "access_token": ACCESS_TOKEN
    }

    todos = []

    while True:

        resp = meta_get(
            url,
            params
        )

        todos.extend(
            resp.get("data", [])
        )

        proxima = (
            resp
            .get("paging", {})
            .get("next")
        )

        if not proxima:
            break

        url = proxima
        params = {}

    print(
        "Anúncios carregados:",
        len(todos)
    )

    return todos


# =========================================================
# BUSCA INICIAL
# =========================================================

try:

    adsets = buscar_adsets()

    ads = buscar_ads()

except RateLimitMeta:

    print("\n========================================")
    print("META AINDA ESTÁ COM RATE LIMIT")
    print("========================================")
    print(
        "Não fiz novas alterações."
    )
    print(
        "Espere 15 minutos sem rodar"
        " nenhum script da Meta."
    )
    print(
        "Depois rode este mesmo script novamente."
    )

    raise SystemExit


# =========================================================
# MAPEAR ADSETS
# =========================================================

adset_map = {}

for adset in adsets:

    nome = normalizar(
        adset["name"]
    )

    adset_map[nome] = adset


# =========================================================
# MAPEAR ADS POR ADSET
# =========================================================

ads_por_adset = {}

for ad in ads:

    adset_id = str(
        ad.get("adset_id", "")
    )

    if not adset_id:
        continue

    if adset_id not in ads_por_adset:
        ads_por_adset[adset_id] = []

    ads_por_adset[
        adset_id
    ].append(ad)


# =========================================================
# CONTADORES
# =========================================================

ja_prontos = 0
criados = 0
ads_ativados = 0
adsets_ativados = 0

sem_adset = []
sem_creative = []
erros = []

rate_limit = False


# =========================================================
# PROCESSAR BLOCO
# =========================================================

for numero, (chave, cidade) in enumerate(
    cidades_bloco.items(),
    start=1
):

    print("\n========================================")
    print(
        f"{numero}/{len(cidades_bloco)} "
        f"- {cidade}"
    )
    print("========================================")

    adset = adset_map.get(
        chave
    )

    if not adset:

        print("SEM ADSET")

        sem_adset.append(
            cidade
        )

        continue


    creative_id = creative_map.get(
        chave
    )

    if not creative_id:

        print("SEM CREATIVE")

        sem_creative.append(
            cidade
        )

        continue


    adset_id = str(
        adset["id"]
    )


    ads_cidade = ads_por_adset.get(
        adset_id,
        []
    )


    # =====================================================
    # ESCOLHER O ANÚNCIO DA CIDADE
    # =====================================================

    ad = None

    if ads_cidade:

        # Prioriza anúncio chamado 01
        for candidato in ads_cidade:

            if (
                str(
                    candidato.get(
                        "name",
                        ""
                    )
                ).strip()
                == "01"
            ):

                ad = candidato
                break

        # Se não existir "01",
        # usa o primeiro existente
        if ad is None:

            ad = ads_cidade[0]


    try:

        # =================================================
        # ATIVAR ADSET SOMENTE SE PRECISAR
        # =================================================

        status_adset = str(
            adset.get(
                "status",
                ""
            )
        ).upper()

        if status_adset != "ACTIVE":

            print(
                "Ativando conjunto..."
            )

            resp = meta_post(
                f"{GRAPH}/{adset_id}",
                {
                    "status": "ACTIVE",
                    "access_token":
                        ACCESS_TOKEN
                }
            )

            if "error" in resp:

                print(
                    "ERRO ATIVANDO ADSET"
                )

                erros.append(
                    cidade
                )

                continue

            adset["status"] = "ACTIVE"

            adsets_ativados += 1

            print(
                "Conjunto ativo."
            )


        # =================================================
        # SE NÃO EXISTIR AD, CRIAR DIRETO ACTIVE
        # =================================================

        if ad is None:

            print(
                "Criando anúncio..."
            )

            resp = meta_post(
                f"{GRAPH}/{AD_ACCOUNT_ID}/ads",
                {
                    "name": "01",

                    "adset_id":
                        adset_id,

                    "creative":
                        json.dumps({
                            "creative_id":
                                creative_id
                        }),

                    "status":
                        "ACTIVE",

                    "access_token":
                        ACCESS_TOKEN
                }
            )

            if "id" not in resp:

                print(
                    "ERRO CRIANDO AD:"
                )

                print(resp)

                erros.append(
                    cidade
                )

                continue


            ad_id = str(
                resp["id"]
            )

            criados += 1

            print(
                "✅ AD CRIADO E ATIVO:",
                ad_id
            )

            # Atualiza memória local,
            # evitando qualquer nova consulta
            ad = {
                "id": ad_id,
                "name": "01",
                "status": "ACTIVE",
                "effective_status":
                    "ACTIVE",
                "adset_id":
                    adset_id
            }

            ads_por_adset[
                adset_id
            ] = [ad]


        # =================================================
        # SE EXISTIR, ATIVAR SOMENTE SE PRECISAR
        # =================================================

        else:

            ad_id = str(
                ad["id"]
            )

            status_ad = str(
                ad.get(
                    "status",
                    ""
                )
            ).upper()

            if status_ad == "ACTIVE":

                print(
                    "✅ JÁ ESTAVA PRONTO:",
                    ad_id
                )

                ja_prontos += 1


            else:

                print(
                    "Ativando anúncio existente:",
                    ad_id
                )

                resp = meta_post(
                    f"{GRAPH}/{ad_id}",
                    {
                        "status":
                            "ACTIVE",

                        "access_token":
                            ACCESS_TOKEN
                    }
                )

                if "error" in resp:

                    print(
                        "ERRO ATIVANDO AD"
                    )

                    erros.append(
                        cidade
                    )

                    continue


                ad["status"] = "ACTIVE"

                ads_ativados += 1

                print(
                    "✅ ANÚNCIO ATIVO"
                )


        # Pequena pausa só após alteração
        time.sleep(2)


    except RateLimitMeta:

        print("\n\n========================================")
        print("RATE LIMIT DA META")
        print("========================================")
        print(
            "O script foi interrompido"
            " propositalmente."
        )

        print(
            "Tudo feito até aqui foi preservado."
        )

        print(
            "Última cidade:",
            cidade
        )

        print(
            "\nEspere 15 minutos SEM rodar"
            " outro script da Meta."
        )

        print(
            "Depois rode novamente:"
        )

        print(
            "python finalizar_bloco_atual.py"
        )

        rate_limit = True

        break


# =========================================================
# RESUMO
# =========================================================

print("\n\n========================================")
print("RESUMO DO BLOCO")
print("========================================")

print(
    "Cidades do bloco :",
    len(cidades_bloco)
)

print(
    "Já estavam prontas:",
    ja_prontos
)

print(
    "Ads novos        :",
    criados
)

print(
    "Ads ativados     :",
    ads_ativados
)

print(
    "Adsets ativados  :",
    adsets_ativados
)

print(
    "Sem adset        :",
    len(sem_adset)
)

print(
    "Sem creative     :",
    len(sem_creative)
)

print(
    "Outros erros     :",
    len(erros)
)

print(
    "Rate limit       :",
    "SIM" if rate_limit else "NÃO"
)

print("========================================")


if sem_adset:

    print("\nSEM ADSET:")

    for cidade in sem_adset:
        print("-", cidade)


if sem_creative:

    print("\nSEM CREATIVE:")

    for cidade in sem_creative:
        print("-", cidade)


if erros:

    print("\nOUTROS ERROS:")

    for cidade in erros:
        print("-", cidade)


if not rate_limit:

    print("\n✅ PROCESSAMENTO DO BLOCO CONCLUÍDO.")