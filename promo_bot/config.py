"""Configuração do promo_bot: lojas/canais/afiliados por JSON + segredos do Telegram por .env.

Mesmo padrão do resto do projeto (veja meta_ads_ops/clients.py): segredo de
API fica em variável de ambiente, o que muda por conta/nicho (canais
monitorados, lojas permitidas, regra de afiliado de cada loja) vive num JSON
explícito passado com --config — sem nada hardcoded em script.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AffiliateRule:
    """Como transformar um link de produto no link de afiliado dessa loja.

    Duas formas — use a que o seu programa de afiliados pedir, ou as duas:
    - `params`: parâmetros de query a acrescentar/sobrescrever na própria URL
      (ex.: Amazon Associates -> {"tag": "meunome-20"}).
    - `template`: URL de uma rede de afiliados / encurtador com o placeholder
      "{url}", que recebe a URL original já com os `params` aplicados
      (ex.: encurtador de afiliado da Shopee/Shein ou de redes como Awin,
      Admitad, Rakuten).
    """

    params: dict[str, str] = field(default_factory=dict)
    template: str | None = None


@dataclass(frozen=True)
class MonitorConfig:
    name: str
    telegram_channels: list[str]
    allowed_stores: list[str]
    affiliates: dict[str, AffiliateRule]
    keywords_include: list[str] = field(default_factory=list)
    keywords_exclude: list[str] = field(default_factory=list)
    review_chat: str | None = None
    output_log: str = "output/promos.jsonl"
    whatsapp_groups: list[str] = field(default_factory=list)
    whatsapp_session_dir: str = "whatsapp_session"


REQUIRED_FIELDS = ["name", "telegram_channels", "allowed_stores", "affiliates"]


def load_monitor_config(path: str) -> MonitorConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        raise ValueError(f"Config '{path}' sem os campos obrigatórios: {missing}")

    affiliates = {
        store.lower(): AffiliateRule(params=dict(rule.get("params", {})), template=rule.get("template") or None)
        for store, rule in data["affiliates"].items()
    }

    return MonitorConfig(
        name=str(data["name"]),
        telegram_channels=list(data["telegram_channels"]),
        allowed_stores=[s.lower() for s in data["allowed_stores"]],
        affiliates=affiliates,
        keywords_include=[k.lower() for k in data.get("keywords_include", [])],
        keywords_exclude=[k.lower() for k in data.get("keywords_exclude", [])],
        review_chat=data.get("review_chat") or None,
        output_log=data.get("output_log", "output/promos.jsonl"),
        whatsapp_groups=list(data.get("whatsapp_groups", [])),
        whatsapp_session_dir=data.get("whatsapp_session_dir", "whatsapp_session"),
    )


@dataclass(frozen=True)
class TelegramSecrets:
    api_id: int
    api_hash: str
    session_name: str = "promo_bot"


class MissingCredentialError(RuntimeError):
    """Levantado quando uma credencial obrigatória não foi configurada."""


def load_telegram_secrets(env_file: str | None = None) -> TelegramSecrets:
    """Carrega TELEGRAM_API_ID / TELEGRAM_API_HASH a partir de variáveis de ambiente / .env.

    São as credenciais do *app* Telegram (obtidas em https://my.telegram.org),
    usadas para logar com a sua própria conta e ler os grupos em que você já
    está — não é um bot token, e o promo_bot nunca entra em grupo sozinho.
    """
    load_dotenv(env_file) if env_file else load_dotenv()

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    missing = [name for name, value in {"TELEGRAM_API_ID": api_id, "TELEGRAM_API_HASH": api_hash}.items() if not value]
    if missing:
        raise MissingCredentialError(
            "Variáveis de ambiente ausentes: " + ", ".join(missing) + ". Veja .env.example."
        )

    return TelegramSecrets(
        api_id=int(api_id),
        api_hash=api_hash,
        session_name=os.getenv("TELEGRAM_SESSION_NAME", "promo_bot"),
    )
