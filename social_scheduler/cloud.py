"""Rodada na nuvem (GitHub Actions): todos os clientes de uma pasta de conteúdo.

Estrutura da pasta de conteúdo (repositório privado):

    <cliente>/agenda.xlsx    (ou agenda.csv / agenda.json)
    <cliente>/<imagens...>   referenciadas pelo nome na coluna "images"
    <cliente>/estado.json    criado/atualizado pela automação — não editar

Credenciais: um secret do GitHub por cliente, SOCIAL_ENV_<CLIENTE> (nome da
pasta em maiúsculas, "-" vira "_"), com o conteúdo inteiro do .env dele.
Chegam aqui todos juntos, em JSON, pela variável SOCIAL_SECRETS_JSON.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from datetime import datetime, timezone

from dotenv import dotenv_values

from social_scheduler import settings
from social_scheduler.runner import run, schedule_native
from social_scheduler.schedule import ScheduleError, load_schedule
from social_scheduler.state import LockedError, RunLock, StateStore

logger = logging.getLogger(__name__)

AGENDA_NAMES = ("agenda.xlsx", "agenda.csv", "agenda.json")
STATE_NAME = "estado.json"
ENV_PREFIXES = ("META_", "LINKEDIN_", "SCHEDULER_")


def secret_name(client: str) -> str:
    return "SOCIAL_ENV_" + re.sub(r"[^A-Z0-9]", "_", client.upper())


def find_clients(content_dir: str) -> list[tuple[str, str]]:
    clients = []
    for name in sorted(os.listdir(content_dir)):
        folder = os.path.join(content_dir, name)
        if name.startswith(".") or not os.path.isdir(folder):
            continue
        for agenda in AGENDA_NAMES:
            if os.path.isfile(os.path.join(folder, agenda)):
                clients.append((name, os.path.join(folder, agenda)))
                break
    return clients


def _use_client_env(env_text: str) -> None:
    # Zera tudo do cliente anterior antes: sem isso, um cliente sem LinkedIn
    # configurado herdaria o token do anterior e postaria na conta errada.
    for key in list(os.environ):
        if key.startswith(ENV_PREFIXES):
            del os.environ[key]
    for key, value in dotenv_values(stream=io.StringIO(env_text)).items():
        if value is not None:
            os.environ[key] = value


def run_client(client: str, agenda: str, env_text: str, make_publisher, now: datetime) -> bool:
    from social_scheduler.cli import print_native_report, print_run_report

    _use_client_env(env_text)
    posts = load_schedule(agenda, settings.timezone_name())
    state_path = os.path.join(os.path.dirname(agenda), STATE_NAME)
    ok = True
    with RunLock(state_path):
        state = StateStore(state_path)
        if any("facebook" in post.platforms for post in posts):
            # Facebook fica agendado no próprio Facebook (sai no minuto exato,
            # sem depender do atraso do GitHub).
            native = schedule_native(posts, state, now, make_publisher)
            print_native_report(native)
            ok &= native.ok
        report = run(posts, state, now, make_publisher)
        print_run_report(report)
        ok &= report.ok
    return ok


def run_all(content_dir: str, secrets: dict[str, str], make_publisher, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    clients = find_clients(content_dir)
    if not clients:
        print(f"Nenhuma pasta de cliente com {' / '.join(AGENDA_NAMES)} em {content_dir}.")
        return 0

    failures = []
    for client, agenda in clients:
        print(f"\n===== {client} ({os.path.basename(agenda)}) =====")
        env_text = secrets.get(secret_name(client))
        if not env_text:
            print(f"Sem credenciais: crie o secret {secret_name(client)} no GitHub (veja o guia).")
            failures.append(client)
            continue
        try:
            if not run_client(client, agenda, env_text, make_publisher, now):
                failures.append(client)
        except LockedError as exc:
            logger.warning("%s: %s", client, exc)
        except (ScheduleError, settings.MissingCredentialError) as exc:
            print(f"Erro: {exc}")
            failures.append(client)
        except Exception:  # noqa: BLE001 — um cliente com problema não trava os outros
            logger.exception("Erro inesperado em %s", client)
            failures.append(client)

    if failures:
        # Saída != 0 faz o GitHub marcar a execução como falha e mandar e-mail.
        print(f"\nCom problema: {', '.join(failures)} (detalhes acima).")
        return 1
    return 0


def load_secrets_from_env() -> dict[str, str]:
    raw = os.getenv("SOCIAL_SECRETS_JSON") or "{}"
    data = json.loads(raw)
    return {k: v for k, v in data.items() if k.startswith("SOCIAL_ENV_")}
