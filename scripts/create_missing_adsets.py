import copy
import json
import time
import unicodedata
import requests
import pandas as pd

from config import *


# =========================================================
# CONFIGURAÇÕES
# =========================================================

GRAPH = f"https://graph.facebook.com/{API_VERSION}"

# CAMPAIGN_ID e MODEL_ADSET_ID agora vêm do config.py, para trocar de
# campanha sem precisar editar cada script (veja config.example.py).
CAMPAIGN_ID = CAMPAIGN_ID
MODEL_ADSET_ID = MODEL_ADSET_ID


# =========================================================
# NORMALIZAÇÃO
# =========================================================

def normalizar(texto):

    texto = str(texto or "").strip()

    texto = unicodedata.normalize(
        "NFKD",
        texto
    )

    texto = "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )

    return texto.lower().strip()


# =========================================================
# RATE LIMIT
# =========================================================

class RateLimitMeta(Exception):
    pass


def analisar_resposta(resp):

    try:
        dados = resp.json()

    except Exception:

        print("\nERRO: resposta inválida da Meta")
        print(resp.text)

        return {
            "error": {
                "message": "Resposta inválida"
            }
        }


    erro = dados.get("error")

    if erro:

        print("\nERRO API:")
        print(dados)

        codigo = erro.get("code")
        subcode = erro.get("error_subcode")


        # Rate limit
        if (
            codigo in [4, 17, 32, 613]
            or subcode == 2446079
        ):

            raise RateLimitMeta()


    return dados


def meta_get(url, params):

    try:

        resp = requests.get(
            url,
            params=params,
            timeout=120
        )

    except requests.exceptions.RequestException as e:

        print("\nERRO DE CONEXÃO:")
        print(e)

        return {
            "error": {
                "message": str(e)
            }
        }


    return analisar_resposta(resp)


def meta_post(url, data):

    try:

        resp = requests.post(
            url,
            data=data,
            timeout=120
        )

    except requests.exceptions.RequestException as e:

        print("\nERRO DE CONEXÃO:")
        print(e)

        return {
            "error": {
                "message": str(e)
            }
        }


    return analisar_resposta(resp)


# =========================================================
# PLANILHA DO BLOCO ATUAL
# =========================================================

print("\n========================================")
print("PLANILHA:", EXCEL_FILE)
print("========================================")

try:

    planilha = pd.read_excel(
        EXCEL_FILE
    )

except FileNotFoundError:

    print("\nARQUIVO NÃO ENCONTRADO:")
    print(EXCEL_FILE)

    print(
        "\nConfirme se ele está dentro de C:\\MetaAPI"
    )

    raise SystemExit


if "city" not in planilha.columns:

    print(
        "\nERRO: coluna 'city' não encontrada."
    )

    print(
        "Colunas disponíveis:",
        list(planilha.columns)
    )

    raise SystemExit


cidades_planilha = []

for cidade in planilha["city"].dropna():

    cidade = str(cidade).strip()

    if not cidade:
        continue

    cidades_planilha.append(
        cidade
    )


print(
    "Cidades na planilha:",
    len(cidades_planilha)
)


# =========================================================
# PEGAR CONJUNTO MODELO
# =========================================================

print("\nBuscando conjunto modelo...")


try:

    modelo = meta_get(
        f"{GRAPH}/{MODEL_ADSET_ID}",
        {
            "fields":
                "id,name,"
                "optimization_goal,"
                "billing_event,"
                "bid_strategy,"
                "daily_budget,"
                "targeting",
            "access_token":
                ACCESS_TOKEN
        }
    )

except RateLimitMeta:

    print("\n========================================")
    print("RATE LIMIT DA META")
    print("========================================")

    print(
        "Espere 15 minutos sem rodar scripts da Meta."
    )

    print(
        "Depois rode novamente:"
    )

    print(
        "python create_missing_adsets.py"
    )

    raise SystemExit


if "error" in modelo:

    print(
        "\nNão consegui carregar o conjunto modelo."
    )

    print(modelo)

    raise SystemExit


print(
    "Modelo:",
    modelo.get("name")
)


if not modelo.get("targeting"):

    print(
        "\nERRO: conjunto modelo não possui targeting."
    )

    raise SystemExit


# =========================================================
# BUSCAR TODOS OS CONJUNTOS EXISTENTES
# =========================================================

def buscar_adsets_existentes():

    print(
        "\nBuscando conjuntos já existentes..."
    )


    url = (
        f"{GRAPH}/"
        f"{CAMPAIGN_ID}/adsets"
    )

    params = {
        "fields":
            "id,name,status",
        "limit":
            500,
        "access_token":
            ACCESS_TOKEN
    }

    todos = []


    while True:

        dados = meta_get(
            url,
            params
        )

        if "error" in dados:

            print(
                "\nERRO buscando conjuntos:"
            )

            print(dados)

            raise SystemExit


        todos.extend(
            dados.get(
                "data",
                []
            )
        )


        proxima = (
            dados
            .get("paging", {})
            .get("next")
        )


        if not proxima:
            break


        url = proxima
        params = {}


    return todos


try:

    existentes = buscar_adsets_existentes()

except RateLimitMeta:

    print("\n========================================")
    print("RATE LIMIT DA META")
    print("========================================")

    print(
        "Espere 15 minutos e rode novamente."
    )

    raise SystemExit


print(
    "Conjuntos encontrados na campanha:",
    len(existentes)
)


nomes_existentes = {
    normalizar(a["name"])
    for a in existentes
}


# =========================================================
# LOCALIZAR CIDADE NA META
# =========================================================

def buscar_cidade_meta(nome_cidade):

    nome_busca = (
        str(nome_cidade)
        .replace(" - Capital", "")
        .strip()
    )


    dados = meta_get(
        f"{GRAPH}/search",
        {
            "type":
                "adgeolocation",

            "location_types":
                json.dumps(
                    ["city"]
                ),

            "q":
                nome_busca,

            "country_code":
                "BR",

            "limit":
                50,

            "access_token":
                ACCESS_TOKEN
        }
    )


    if "error" in dados:

        return None


    resultados = dados.get(
        "data",
        []
    )


    if not resultados:

        return None


    alvo = normalizar(
        nome_busca
    )


    # -----------------------------------------------------
    # 1. Nome exato + estado São Paulo
    # -----------------------------------------------------

    for local in resultados:

        nome = normalizar(
            local.get(
                "name",
                ""
            )
        )

        regiao = normalizar(
            local.get(
                "region",
                ""
            )
        )

        pais = str(
            local.get(
                "country_code",
                ""
            )
        ).upper()


        if (
            nome == alvo
            and pais == "BR"
            and (
                "sao paulo" in regiao
                or regiao == "sp"
            )
        ):

            return local


    # -----------------------------------------------------
    # 2. Nome exato no Brasil
    # -----------------------------------------------------

    candidatos = []

    for local in resultados:

        if (
            normalizar(
                local.get(
                    "name",
                    ""
                )
            )
            == alvo
            and str(
                local.get(
                    "country_code",
                    ""
                )
            ).upper()
            == "BR"
        ):

            candidatos.append(
                local
            )


    # Só aceita automaticamente se houver
    # exatamente uma cidade com esse nome
    if len(candidatos) == 1:

        return candidatos[0]


    return None


# =========================================================
# CRIAÇÃO
# =========================================================

criados = 0
pulados = 0
nao_encontrados = []
erros = []
rate_limit = False


for numero, cidade in enumerate(
    cidades_planilha,
    start=1
):

    print("\n========================================")
    print(
        f"{numero}/{len(cidades_planilha)} - {cidade}"
    )
    print("========================================")


    # -----------------------------------------------------
    # JÁ EXISTE
    # -----------------------------------------------------

    if normalizar(cidade) in nomes_existentes:

        print(
            "JÁ EXISTE - PULANDO"
        )

        pulados += 1

        continue


    # -----------------------------------------------------
    # BUSCAR GEOLOCATION
    # -----------------------------------------------------

    try:

        local = buscar_cidade_meta(
            cidade
        )

    except RateLimitMeta:

        print("\n========================================")
        print("RATE LIMIT DA META")
        print("========================================")

        print(
            "Tudo que já foi criado foi preservado."
        )

        print(
            "Cidade onde parou:",
            cidade
        )

        print(
            "\nEspere 15 minutos."
        )

        print(
            "Depois rode novamente:"
        )

        print(
            "python create_missing_adsets.py"
        )

        rate_limit = True

        break


    if not local:

        print(
            "⚠️ CIDADE NÃO ENCONTRADA COM SEGURANÇA"
        )

        nao_encontrados.append(
            cidade
        )

        continue


    print(
        "Meta encontrou:",
        local.get("name"),
        "-",
        local.get("region"),
        "-",
        local.get("country_code")
    )


    city_key = local.get(
        "key"
    )


    if not city_key:

        print(
            "ERRO: localização sem key"
        )

        erros.append(
            cidade
        )

        continue


    # -----------------------------------------------------
    # COPIAR TARGETING DO MODELO
    # -----------------------------------------------------

    targeting = copy.deepcopy(
        modelo["targeting"]
    )


    if "geo_locations" not in targeting:

        targeting[
            "geo_locations"
        ] = {}


    # Mantém o mesmo raio usado pelo modelo.
    # Não vamos mexer em raio agora.
    raio_modelo = 40

    cidades_modelo = (
        modelo
        .get("targeting", {})
        .get("geo_locations", {})
        .get("cities", [])
    )

    if cidades_modelo:

        raio_existente = (
            cidades_modelo[0]
            .get("radius")
        )

        if raio_existente:
            raio_modelo = raio_existente


    targeting[
        "geo_locations"
    ]["cities"] = [
        {
            "key":
                str(city_key),

            "radius":
                raio_modelo,

            "distance_unit":
                "kilometer"
        }
    ]


    # Não deixa regiões/países antigos do
    # conjunto modelo contaminarem a cidade nova
    targeting[
        "geo_locations"
    ].pop(
        "regions",
        None
    )

    targeting[
        "geo_locations"
    ].pop(
        "countries",
        None
    )

    targeting[
        "geo_locations"
    ].pop(
        "country_groups",
        None
    )


    # -----------------------------------------------------
    # BODY DO NOVO ADSET
    # -----------------------------------------------------

    body = {
        "name":
            cidade,

        "campaign_id":
            CAMPAIGN_ID,

        "status":
            "PAUSED",

        "optimization_goal":
            modelo.get(
                "optimization_goal"
            ),

        "billing_event":
            modelo.get(
                "billing_event"
            ),

        "targeting":
            json.dumps(
                targeting
            ),

        "access_token":
            ACCESS_TOKEN
    }


    # Só envia bid_strategy se existir
    if modelo.get(
        "bid_strategy"
    ):

        body[
            "bid_strategy"
        ] = modelo[
            "bid_strategy"
        ]


    # Só envia daily_budget se o modelo tiver.
    # Em CBO normalmente não terá.
    if modelo.get(
        "daily_budget"
    ):

        body[
            "daily_budget"
        ] = modelo[
            "daily_budget"
        ]


    # Remove valores None
    body = {
        k: v
        for k, v in body.items()
        if v is not None
    }


    # -----------------------------------------------------
    # CRIAR
    # -----------------------------------------------------

    try:

        resp = meta_post(
            f"{GRAPH}/{AD_ACCOUNT_ID}/adsets",
            body
        )

    except RateLimitMeta:

        print("\n========================================")
        print("RATE LIMIT DA META")
        print("========================================")

        print(
            "Cidade onde parou:",
            cidade
        )

        print(
            "Espere 15 minutos e rode novamente."
        )

        rate_limit = True

        break


    if "id" in resp:

        print(
            "✅ CRIADO:",
            resp["id"]
        )

        criados += 1

        # Atualiza localmente para evitar
        # duplicação na mesma execução
        nomes_existentes.add(
            normalizar(cidade)
        )

        time.sleep(2)

        continue


    # -----------------------------------------------------
    # OUTRO ERRO
    # -----------------------------------------------------

    print(
        "\n❌ ERRO CRIANDO:",
        cidade
    )

    print(resp)

    erros.append(
        {
            "city":
                cidade,

            "error":
                str(resp)
        }
    )


    time.sleep(2)


# =========================================================
# SALVAR ERROS
# =========================================================

if nao_encontrados:

    pd.DataFrame({
        "city":
            nao_encontrados
    }).to_excel(
        "cidades_nao_encontradas.xlsx",
        index=False
    )


if erros:

    pd.DataFrame(
        erros
    ).to_excel(
        "erros_adsets.xlsx",
        index=False
    )


# =========================================================
# RESUMO
# =========================================================

print("\n\n========================================")
print("RESUMO - CRIAÇÃO DE ADSETS")
print("========================================")

print(
    "Cidades do bloco :",
    len(cidades_planilha)
)

print(
    "Criados          :",
    criados
)

print(
    "Já existentes    :",
    pulados
)

print(
    "Não encontradas  :",
    len(nao_encontrados)
)

print(
    "Outros erros     :",
    len(erros)
)

print(
    "Rate limit       :",
    "SIM"
    if rate_limit
    else "NÃO"
)

print("========================================")


if nao_encontrados:

    print(
        "\nCIDADES NÃO ENCONTRADAS:"
    )

    for cidade in nao_encontrados:

        print(
            "-",
            cidade
        )


if erros:

    print(
        "\nCIDADES COM ERRO:"
    )

    for erro in erros:

        print(
            "-",
            erro["city"]
        )


if not rate_limit:

    print(
        "\n✅ PROCESSAMENTO DE ADSETS CONCLUÍDO."
    )