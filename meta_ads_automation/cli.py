"""CLI: sobe Campanhas + Conjuntos de Anúncios + Anúncios no Meta Ads a partir de um JSON.

Uso:
    python -m meta_ads_automation.cli --config config/minha_campanha.json --dry-run
    python -m meta_ads_automation.cli --config config/minha_campanha.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from meta_ads_automation.client import MissingCredentialError, init_api, load_credentials
from meta_ads_automation.creator import Creator


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Caminho do arquivo JSON com campanhas/adsets/anúncios")
    parser.add_argument("--env-file", default=None, help="Caminho de um .env alternativo")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Não faz nenhuma chamada real à API; apenas valida e mostra o que seria criado",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continua criando os demais itens mesmo se um deles falhar",
    )
    parser.add_argument("--output", default=None, help="Caminho para salvar um JSON com os IDs criados")
    parser.add_argument("--verbose", action="store_true", help="Log em nível DEBUG")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("meta_ads_automation")

    config_path = Path(args.config)
    if not config_path.is_file():
        logger.error("Arquivo de configuração não encontrado: %s", config_path)
        return 1

    with config_path.open(encoding="utf-8") as f:
        config = json.load(f)

    try:
        credentials = load_credentials(args.env_file)
    except MissingCredentialError as exc:
        logger.error(str(exc))
        return 1

    account = init_api(credentials)
    logger.info(
        "Conectado à conta %s (dry-run=%s)", credentials.account_id_with_prefix, args.dry_run
    )

    creator = Creator(account, dry_run=args.dry_run, continue_on_error=args.continue_on_error)

    try:
        result = creator.run(config)
    except Exception as exc:  # noqa: BLE001 - queremos reportar qualquer falha de criação
        logger.error("Execução interrompida: %s", exc)
        return 1

    summary = result.as_dict()
    logger.info("Concluído. %d campanha(s) processada(s).", len(summary["campaigns"]))

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Resumo salvo em %s", args.output)
    else:
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
