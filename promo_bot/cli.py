"""CLI do promo_bot.

Uso:
    # Testa o pipeline com um texto de exemplo, sem Telegram nem rede:
    python -m promo_bot.cli --config config/promo.example.json --texto "..."

    # Modo contínuo: conecta no Telegram e escuta os canais configurados:
    python -m promo_bot.cli --config config/promo.example.json --listen
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from promo_bot.config import MissingCredentialError, load_monitor_config, load_telegram_secrets
from promo_bot.pipeline import process_message
from promo_bot.publisher import save_to_log


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Caminho do JSON de configuração (lojas/canais/afiliados)")
    parser.add_argument("--texto", help="Processa um texto único (modo teste, sem Telegram) e imprime o resultado")
    parser.add_argument("--listen", action="store_true", help="Conecta no Telegram e escuta os canais configurados")
    parser.add_argument("--env-file", default=None, help="Caminho de um .env alternativo")
    parser.add_argument("--verbose", action="store_true", help="Log em nível DEBUG")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger = logging.getLogger("promo_bot")

    config = load_monitor_config(args.config)

    if args.texto:
        results = process_message(args.texto, config)
        if not results:
            logger.info(
                "Nenhuma promoção reconhecida nesse texto (loja não configurada, sem regra de "
                "afiliado ou filtro de palavra-chave bloqueou)."
            )
            return 0
        for result in results:
            save_to_log(result, config.output_log)
            print(
                json.dumps(
                    {
                        "loja": result.store,
                        "link_afiliado": result.affiliate_link,
                        "legenda": result.caption,
                        "roteiro": result.script,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0

    if args.listen:
        from promo_bot.telegram_listener import run_listener

        try:
            secrets = load_telegram_secrets(args.env_file)
        except MissingCredentialError as exc:
            logger.error(str(exc))
            return 1
        asyncio.run(run_listener(secrets, config))
        return 0

    logger.error('Nada a fazer: use --texto "..." (teste) ou --listen (modo contínuo). Veja --help.')
    return 1


if __name__ == "__main__":
    sys.exit(main())
