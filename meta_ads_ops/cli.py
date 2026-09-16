"""CLI: mantém conjuntos de anúncios/criativos/anúncios por cidade sincronizados
com a planilha de um cliente, numa campanha do Meta Ads que já existe.

Uso:
    python -m meta_ads_ops.cli sync --client clients/bebetto.json --dry-run
    python -m meta_ads_ops.cli sync --client clients/bebetto.json
    python -m meta_ads_ops.cli activate --client clients/bebetto.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .clients import load_client, load_secrets
from .graph import GraphClient
from .sync import CitySync


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["sync", "activate"], help="sync: cria o que falta (pausado). activate: coloca no ar o que já existe.")
    parser.add_argument("--client", required=True, help="Caminho do JSON de configuração do cliente (ex.: clients/bebetto.json)")
    parser.add_argument("--config", default="config.py", help="Caminho do config.py com as credenciais (padrão: ./config.py)")
    parser.add_argument("--dry-run", action="store_true", help="Não chama a API de verdade, só simula")
    parser.add_argument("--output", default=None, help="Salva o resumo em JSON nesse caminho")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("meta_ads_ops")

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
    sync = CitySync(graph, client, secrets.ad_account_id, dry_run=args.dry_run)

    try:
        result = sync.sync() if args.action == "sync" else sync.activate()
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
