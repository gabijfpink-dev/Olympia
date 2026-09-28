"""CLI: mantém conjuntos de anúncios/criativos/anúncios por cidade sincronizados
com a planilha de um cliente, numa campanha do Meta Ads que já existe.

Uso:
    python -m meta_ads_ops.cli sync --client clients/bebetto.json --dry-run
    python -m meta_ads_ops.cli sync --client clients/bebetto.json
    python -m meta_ads_ops.cli activate --client clients/bebetto.json

Pra corrigir o criativo (ex.: link errado) de cidades que JÁ têm anúncio
criado, sem mexer em adset/imagem de progresso/status — atualize a planilha
com o dado certo (ex.: coluna "url") e rode:
    python -m meta_ads_ops.cli update-creative --client clients/rui-falcao.json --dry-run
    python -m meta_ads_ops.cli update-creative --client clients/rui-falcao.json

Pra criar a campanha do zero (conta nova, sem campanha ainda):
    python -m meta_ads_ops.cli bootstrap --bootstrap-config clients/leandro-grass.bootstrap.json --dry-run
    python -m meta_ads_ops.cli bootstrap --bootstrap-config clients/leandro-grass.bootstrap.json

Pra testar se uns nomes de região existem na busca de geolocalização da
Meta ANTES de montar a planilha inteira (evita descobrir tarde que nada
resolve):
    python -m meta_ads_ops.cli geocheck --config config_1818.py --names "Taguatinga,Ceilândia,Gama"

Pra achar o instagram_actor_id CERTO pra usar num criativo (o ID do
Business Settings costuma ser diferente do que a API de anúncios aceita):
    python -m meta_ads_ops.cli instacheck --config config_1818.py --client clients/leandro-grass.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .clients import load_bootstrap, load_client, load_secrets
from .graph import GraphClient, GraphError
from .sync import CampaignBootstrapper, CitySync, _buscar_cidade


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "action", choices=["sync", "activate", "update-creative", "bootstrap", "geocheck", "instacheck"],
        help="sync: cria o que falta (pausado). activate: coloca no ar o que já existe. "
        "update-creative: recria o criativo (link/texto/imagem) de cidades já com anúncio, a "
        "partir dos dados atuais da planilha, e troca a referência do anúncio pro criativo novo. "
        "bootstrap: cria campanha+1º adset+criativo+anúncio do zero (conta sem campanha ainda). "
        "geocheck: testa se uns nomes existem na busca de geolocalização, sem criar nada. "
        "instacheck: lista os instagram_actor_id válidos pra usar num criativo.",
    )
    parser.add_argument("--client", default=None, help="Caminho do JSON de configuração do cliente (obrigatório pra sync/activate)")
    parser.add_argument("--bootstrap-config", default=None, help="Caminho do JSON de bootstrap (obrigatório pra bootstrap)")
    parser.add_argument("--names", default=None, help="Nomes separados por vírgula pra testar (obrigatório pra 'geocheck')")
    parser.add_argument(
        "--preferred-region", default=None,
        help="Só pra 'geocheck': só aceita resultado dessa região (ex.: 'Federal District'), "
        "evitando pegar cidade homônima de outro estado.",
    )
    parser.add_argument("--config", default="config.py", help="Caminho do config.py com as credenciais (padrão: ./config.py)")
    parser.add_argument("--dry-run", action="store_true", help="Não chama a API de verdade, só simula")
    parser.add_argument("--output", default=None, help="Salva o resumo em JSON nesse caminho")
    parser.add_argument(
        "--adset-suffix", default="",
        help="Sufixo pro nome do adset/anúncio (ex.: ' - Aumento'), pra criar um SEGUNDO "
        "adset numa cidade que já tem um, sem colidir com o existente. A busca de "
        "geolocalização continua usando o nome puro da cidade.",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _run_geocheck(args: argparse.Namespace, logger: logging.Logger) -> int:
    if not args.names:
        logger.error("--names é obrigatório pra 'geocheck' (ex.: --names \"Taguatinga,Ceilândia,Gama\")")
        return 1

    try:
        secrets = load_secrets(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    graph = GraphClient(secrets.access_token, secrets.api_version)
    nomes = [n.strip() for n in args.names.split(",") if n.strip()]

    resultados: dict[str, dict | None] = {}
    for nome in nomes:
        local = _buscar_cidade(
            graph, nome, location_types=["city", "neighborhood"], preferred_region=args.preferred_region,
        )
        resultados[nome] = local
        if local:
            logger.info(
                "%s -> ENCONTRADO (tipo=%s, key=%s, nome na Meta='%s')",
                nome, local.get("type"), local.get("key"), local.get("name"),
            )
        else:
            logger.info("%s -> NÃO encontrado (nem city nem neighborhood)", nome)

    output_json = json.dumps(resultados, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("Resumo salvo em %s", args.output)
    else:
        print(output_json)

    return 0


def _run_instacheck(args: argparse.Namespace, logger: logging.Logger) -> int:
    if not args.client:
        logger.error("--client é obrigatório pra 'instacheck' (usa o page_id do cliente pra referência cruzada)")
        return 1

    try:
        client = load_client(args.client)
        secrets = load_secrets(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    graph = GraphClient(secrets.access_token, secrets.api_version)

    try:
        contas = graph.paginate(f"{secrets.ad_account_id}/instagram_accounts", {"fields": "id,username"})
    except GraphError as exc:
        logger.error("Erro buscando instagram_accounts da conta de anúncios: %s", exc)
        contas = []

    if contas:
        logger.info("Contas de Instagram vinculadas à conta de anúncios %s (use o 'id' como instagram_actor_id):", secrets.ad_account_id)
        for conta in contas:
            logger.info("  instagram_actor_id=%s  username=@%s", conta.get("id"), conta.get("username"))
    else:
        logger.warning(
            "Nenhuma conta de Instagram vinculada à conta de anúncios %s. "
            "Sem uma delas, deixe instagram_actor_id como null no clients/<nome>.json.",
            secrets.ad_account_id,
        )

    try:
        pagina = graph.get(client.page_id, {"fields": "instagram_business_account"})
        iba = pagina.get("instagram_business_account")
        if iba:
            logger.info(
                "Referência cruzada — Página %s tem instagram_business_account.id=%s "
                "(pode ou não ser igual ao instagram_actor_id certo acima).",
                client.page_id, iba.get("id"),
            )
    except GraphError as exc:
        logger.warning("Não consegui checar instagram_business_account da Página: %s", exc)

    output_json = json.dumps({"ad_account_instagram_accounts": contas}, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("Resumo salvo em %s", args.output)
    else:
        print(output_json)

    return 0


def _run_bootstrap(args: argparse.Namespace, logger: logging.Logger) -> int:
    if not args.bootstrap_config:
        logger.error("--bootstrap-config é obrigatório pra 'bootstrap'")
        return 1

    try:
        cfg = load_bootstrap(args.bootstrap_config)
        secrets = load_secrets(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    logger.info("Bootstrap: campanha='%s' região='%s' dry-run=%s", cfg.campaign_name, cfg.region, args.dry_run)

    graph = GraphClient(secrets.access_token, secrets.api_version)
    bootstrapper = CampaignBootstrapper(graph, secrets.ad_account_id, dry_run=args.dry_run)

    try:
        result = bootstrapper.bootstrap(cfg)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de execução deve ser reportada, não engolida
        logger.error("Execução interrompida: %s", exc)
        return 1

    output_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("Resumo salvo em %s", args.output)
    else:
        print(output_json)

    logger.info(
        "Pronto. Cole campaign_id=%s e model_adset_id=%s no clients/<nome>.json e siga com sync/activate.",
        result["campaign_id"], result["model_adset_id"],
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("meta_ads_ops")

    if args.action == "bootstrap":
        return _run_bootstrap(args, logger)

    if args.action == "geocheck":
        return _run_geocheck(args, logger)

    if args.action == "instacheck":
        return _run_instacheck(args, logger)

    if not args.client:
        logger.error("--client é obrigatório pra sync/activate")
        return 1

    try:
        client = load_client(args.client)
        secrets = load_secrets(args.config)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        return 1

    logger.info(
        "Cliente=%s campanha=%s adset-modelo=%s dry-run=%s",
        client.name, client.campaign_id, client.model_adset_id, args.dry_run,
    )

    graph = GraphClient(secrets.access_token, secrets.api_version)
    sync = CitySync(
        graph, client, secrets.ad_account_id, dry_run=args.dry_run, adset_suffix=args.adset_suffix,
    )

    try:
        if args.action == "sync":
            result = sync.sync()
        elif args.action == "activate":
            result = sync.activate()
        else:
            result = sync.update_creatives()
    except Exception as exc:  # noqa: BLE001 - qualquer falha de execução deve ser reportada, não engolida
        logger.error("Execução interrompida: %s", exc)
        return 1

    summary = result.as_dict()
    logger.info(
        "Concluído. adsets=%d ads=%d já prontos=%d não encontradas=%d erros=%d",
        len(summary["adsets_criados"]), len(summary["ads_criados"]),
        summary["ja_prontos"], len(summary["cidades_nao_encontradas"]), len(summary["erros"]),
    )

    output_json = json.dumps(summary, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        logger.info("Resumo salvo em %s", args.output)
    else:
        print(output_json)

    return 1 if summary["erros"] else 0


if __name__ == "__main__":
    sys.exit(main())
