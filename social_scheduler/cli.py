"""CLI do agendador de postagens orgânicas (Facebook, Instagram, LinkedIn).

Uso:
    # Confere a agenda inteira (datas, plataformas, imagens, limites) sem postar nada
    python -m social_scheduler validate --schedule posts/agenda.xlsx

    # Mostra o que sairia agora, sem chamar API nenhuma
    python -m social_scheduler run --schedule posts/agenda.xlsx --env-file .env.cliente --dry-run

    # Publica o que já venceu (é ESTE comando que o Agendador de Tarefas/cron roda a cada 10 min)
    python -m social_scheduler run --schedule posts/agenda.xlsx --env-file .env.cliente

    # Situação de cada post (pendente / publicado / falhou)
    python -m social_scheduler status --schedule posts/agenda.xlsx

    # Liberar um post pra sair de novo (ex.: apagou no app e quer repostar, ou desistiu após 3 falhas)
    python -m social_scheduler reset --schedule posts/agenda.xlsx --id 2026-10-10-saude --platform instagram

Configuração (uma vez por cliente):
    # Descobre ID/token da Página e o ID do Instagram vinculado
    python -m social_scheduler meta-accounts --env-file .env.cliente

    # Gera o token do LinkedIn (repetir a cada ~60 dias)
    python -m social_scheduler linkedin-auth --env-file .env.cliente
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from social_scheduler import settings
from social_scheduler.schedule import ScheduleError, load_schedule, parse_datetime
from social_scheduler.state import LockedError, RunLock, StateStore, default_state_path

logger = logging.getLogger("social_scheduler")


def make_publisher(platform: str):
    if platform == "facebook":
        from social_scheduler.meta import FacebookPublisher

        return FacebookPublisher(settings.facebook_settings())
    if platform == "instagram":
        from social_scheduler.meta import InstagramPublisher

        return InstagramPublisher(settings.instagram_settings())
    if platform == "linkedin":
        from social_scheduler.linkedin import LinkedInPublisher

        return LinkedInPublisher(settings.linkedin_settings())
    raise ValueError(f"plataforma desconhecida: {platform}")


def _load(args: argparse.Namespace):
    settings.load_env(args.env_file)
    tz = settings.timezone_name()
    posts = load_schedule(args.schedule, tz)
    state_path = args.state or default_state_path(args.schedule)
    return posts, state_path, tz


def cmd_validate(args: argparse.Namespace) -> int:
    posts, _, tz = _load(args)
    bad = [p for p in posts if not p.valid]
    for post in posts:
        when = post.publish_at.astimezone(ZoneInfo(tz)).strftime("%d/%m/%Y %H:%M")
        flag = "OK " if post.valid else "ERRO"
        if not post.enabled:
            flag = "OFF"
        print(f"[{flag}] {post.id:<30} {when}  {', '.join(post.platforms)}")
        for err in post.errors:
            print(f"        - {err}")
    print(f"\n{len(posts)} posts, {len(bad)} com erro (fuso: {tz}).")
    return 1 if bad else 0


def cmd_run(args: argparse.Namespace) -> int:
    from social_scheduler.runner import run

    posts, state_path, tz = _load(args)
    now = parse_datetime(args.now, tz) if args.now else datetime.now(timezone.utc)

    try:
        with RunLock(state_path):
            state = StateStore(state_path)
            report = run(
                posts,
                state,
                now,
                make_publisher,
                max_late=timedelta(hours=args.max_late_hours),
                max_attempts=args.max_attempts,
                dry_run=args.dry_run,
            )
    except LockedError as exc:
        logger.warning("%s", exc)
        return 0  # rodada anterior ainda trabalhando; não é erro

    for title, items in (
        ("Publicados", report.published),
        ("Sairiam agora (dry-run)", report.would_publish),
        ("FALHARAM (tenta de novo na próxima rodada)", report.failed),
        ("Desistiu após várias falhas (use 'reset' pra tentar de novo)", report.gave_up),
        ("Atrasados demais, NÃO publicados (ajuste publish_at)", report.too_late),
        ("Vencidos mas com erro na agenda (rode 'validate')", report.invalid),
    ):
        if items:
            print(f"\n{title}:")
            for item in items:
                print(f"  - {item}")
    if not any(vars(report).values()):
        print("Nada pra publicar agora.")
    return 0 if report.ok else 1


def cmd_status(args: argparse.Namespace) -> int:
    posts, state_path, tz = _load(args)
    state = StateStore(state_path)
    local_tz = ZoneInfo(tz)
    for post in sorted(posts, key=lambda p: p.publish_at):
        when = post.publish_at.astimezone(local_tz).strftime("%d/%m/%Y %H:%M")
        cols = []
        for platform in post.platforms:
            info = state.get(post.id, platform)
            status = info.get("status") or ("desativado" if not post.enabled else "pendente")
            if status == "failed":
                status = f"falhou x{info.get('attempts')}: {info.get('error', '')[:60]}"
            cols.append(f"{platform}={status}")
        print(f"{when}  {post.id:<30} {'  '.join(cols)}")
    print(f"\nEstado em: {state_path}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    state_path = args.state or default_state_path(args.schedule)
    state = StateStore(state_path)
    if state.reset(args.id, args.platform):
        print(f"Liberado: {args.id} {args.platform or '(todas as plataformas)'} — sai na próxima rodada.")
        return 0
    print(f"Nada registrado pra {args.id} {args.platform or ''} em {state_path}.")
    return 1


def cmd_meta_accounts(args: argparse.Namespace) -> int:
    from social_scheduler.meta import list_pages

    settings.load_env(args.env_file)
    token = args.user_token or os.getenv("META_USER_ACCESS_TOKEN") or os.getenv("META_ACCESS_TOKEN")
    if not token:
        print("Informe --user-token (ou META_USER_ACCESS_TOKEN no .env).", file=sys.stderr)
        return 1
    pages = list_pages(token, settings.graph_version())
    if not pages:
        print("Nenhuma Página visível com esse token (falta permissão pages_show_list?).")
        return 1
    for page in pages:
        ig = page.get("instagram_business_account") or {}
        print(f"\nPágina: {page['name']}")
        print(f"  META_PAGE_ID={page['id']}")
        print(f"  META_PAGE_ACCESS_TOKEN={page.get('access_token', '(sem acesso)')}")
        if ig:
            print(f"  META_IG_USER_ID={ig['id']}   (@{ig.get('username', '?')})")
        else:
            print("  (sem Instagram profissional vinculado a esta Página)")
    print("\nCopie as linhas da Página certa pro seu .env. Trate o token como senha.")
    return 0


def cmd_linkedin_auth(args: argparse.Namespace) -> int:
    from social_scheduler.linkedin import authorization_url, exchange_code, extract_code, person_urn

    settings.load_env(args.env_file)
    client_id = os.getenv("LINKEDIN_CLIENT_ID")
    client_secret = os.getenv("LINKEDIN_CLIENT_SECRET")
    redirect_uri = os.getenv("LINKEDIN_REDIRECT_URI") or "http://localhost:8000/callback"
    if not client_id or not client_secret:
        print("Preencha LINKEDIN_CLIENT_ID e LINKEDIN_CLIENT_SECRET no .env.", file=sys.stderr)
        return 1

    scopes = ["openid", "profile", "w_member_social"]
    if args.organization:
        scopes.append("w_organization_social")
    url, state = authorization_url(client_id, redirect_uri, scopes)
    print("1) Abra este link no navegador, logado na conta do LinkedIn, e clique em Permitir:\n")
    print(f"   {url}\n")
    print("2) O navegador vai cair numa página que não carrega (normal).")
    print("   Copie a URL INTEIRA da barra de endereço e cole aqui.\n")
    redirected = input("URL: ")
    code = extract_code(redirected, state)
    token = exchange_code(client_id, client_secret, redirect_uri, code)
    access_token = token["access_token"]
    urn, name = person_urn(access_token)
    days = int(token.get("expires_in", 0)) // 86400

    print(f"\nOK, conectado como {name}. Token vale ~{days} dias. Coloque no .env:\n")
    print(f"LINKEDIN_ACCESS_TOKEN={access_token}")
    if args.organization:
        print("LINKEDIN_AUTHOR_URN=urn:li:organization:<ID_DA_PAGINA>")
        print("  (o ID é o número na URL de admin da página: linkedin.com/company/<ID>/admin)")
    else:
        print(f"LINKEDIN_AUTHOR_URN={urn}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m social_scheduler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_schedule(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--schedule", required=True, help="agenda .xlsx, .csv ou .json")
        p.add_argument("--env-file", help="credenciais do cliente (ex.: .env.bebetto)")
        p.add_argument("--state", help="arquivo de estado (padrão: .state/social_<agenda>.json)")
        return p

    with_schedule(sub.add_parser("validate", help="confere a agenda")).set_defaults(func=cmd_validate)

    p = with_schedule(sub.add_parser("run", help="publica o que já venceu"))
    p.add_argument("--dry-run", action="store_true", help="só mostra, não publica")
    p.add_argument("--now", help="simula outro horário (ex.: '2026-10-10 09:05')")
    p.add_argument("--max-late-hours", type=float, default=6, help="não publica post vencido há mais que isso (padrão 6h)")
    p.add_argument("--max-attempts", type=int, default=3, help="tentativas por post/rede antes de desistir")
    p.set_defaults(func=cmd_run)

    with_schedule(sub.add_parser("status", help="situação de cada post")).set_defaults(func=cmd_status)

    p = sub.add_parser("reset", help="libera um post pra ser publicado de novo")
    p.add_argument("--schedule", required=True)
    p.add_argument("--state")
    p.add_argument("--id", required=True)
    p.add_argument("--platform", choices=["facebook", "instagram", "linkedin"])
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("meta-accounts", help="lista Páginas/Instagram e seus IDs/tokens")
    p.add_argument("--env-file")
    p.add_argument("--user-token", help="token de usuário/usuário do sistema do Meta")
    p.set_defaults(func=cmd_meta_accounts)

    p = sub.add_parser("linkedin-auth", help="gera o token de acesso do LinkedIn")
    p.add_argument("--env-file")
    p.add_argument("--organization", action="store_true", help="postar como Página de empresa")
    p.set_defaults(func=cmd_linkedin_auth)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return args.func(args)
    except (ScheduleError, settings.MissingCredentialError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
