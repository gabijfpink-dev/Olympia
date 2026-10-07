"""Leitura e validação da agenda de postagens (.xlsx, .csv ou .json).

Cada linha é um post. Colunas:

    id            identificador único e estável (ex.: "2026-10-10-saude").
                  É ele que evita postar duas vezes — não reaproveite.
    publish_at    data/hora de publicação. Aceita "2026-10-10 09:00",
                  "10/10/2026 09:00" ou ISO com fuso ("2026-10-10T09:00-03:00").
                  Sem fuso, vale SCHEDULER_TIMEZONE (padrão America/Sao_Paulo).
    platforms     "facebook, instagram, linkedin" (qualquer combinação).
    text          legenda/texto do post.
    text_facebook / text_instagram / text_linkedin
                  (opcionais) texto específico daquela rede, no lugar de "text".
    images        URL(s) ou nome(s) de arquivo de imagem, separados por "|"
                  (arquivo local é relativo à pasta da agenda).
                  Mais de uma = carrossel (Instagram até 10).
    video         URL de vídeo (Reels no Instagram, vídeo na Página).
    link          link (Facebook: post com prévia; LinkedIn: artigo).
    link_title    (opcional) título do link no LinkedIn.
    alt_text      (opcional) texto alternativo das imagens.
    status        (opcional) "rascunho"/"draft"/"pausado" = não publica.
"""

from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

PLATFORMS = ("facebook", "instagram", "linkedin")
PLATFORM_ALIASES = {
    "fb": "facebook",
    "face": "facebook",
    "facebook": "facebook",
    "ig": "instagram",
    "insta": "instagram",
    "instagram": "instagram",
    "li": "linkedin",
    "linkedin": "linkedin",
}
SKIP_STATUSES = {"rascunho", "draft", "pausado", "paused", "cancelado", "cancelled", "nao", "não"}

INSTAGRAM_MAX_CAPTION = 2200
INSTAGRAM_MAX_CAROUSEL = 10
LINKEDIN_MAX_TEXT = 3000
LINKEDIN_MAX_IMAGES = 20

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
)


class ScheduleError(ValueError):
    """Arquivo de agenda ilegível (problema no arquivo todo, não numa linha)."""


@dataclass
class Post:
    id: str
    publish_at: datetime  # sempre com fuso
    platforms: list[str]
    text: str = ""
    texts: dict[str, str] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)
    video: str = ""
    link: str = ""
    link_title: str = ""
    alt_text: str = ""
    enabled: bool = True
    errors: list[str] = field(default_factory=list)

    def text_for(self, platform: str) -> str:
        return self.texts.get(platform) or self.text

    @property
    def valid(self) -> bool:
        return not self.errors


def is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:  # NaN do pandas
        return ""
    return str(value).strip()


def _split(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_clean(v) for v in value if _clean(v)]
    return [part.strip() for part in re.split(r"[|\n]", _clean(value)) if part.strip()]


def parse_datetime(value: Any, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    if hasattr(value, "to_pydatetime"):  # pandas.Timestamp
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        dt = value
    else:
        text = _clean(value)
        if not text:
            raise ValueError("publish_at vazio")
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(
                    f"publish_at inválido: {text!r} (use 2026-10-10 09:00 ou 10/10/2026 09:00)"
                ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


def _validate(post: Post) -> None:
    errors = post.errors
    if not post.platforms:
        errors.append("nenhuma plataforma em 'platforms'")
    if post.video and post.images:
        errors.append("use 'images' OU 'video', não os dois")
    for img in post.images:
        if not is_url(img) and not os.path.isfile(img):
            errors.append(f"imagem não encontrada: {img}")
    if post.video and not is_url(post.video):
        errors.append("'video' precisa ser uma URL pública (http/https)")

    for platform in post.platforms:
        text = post.text_for(platform)
        if platform == "instagram":
            if not post.images and not post.video:
                errors.append("instagram: precisa de 'images' ou 'video'")
            if len(post.images) > INSTAGRAM_MAX_CAROUSEL:
                errors.append(f"instagram: carrossel aceita no máximo {INSTAGRAM_MAX_CAROUSEL} imagens")
            if len(text) > INSTAGRAM_MAX_CAPTION:
                errors.append(f"instagram: legenda passa de {INSTAGRAM_MAX_CAPTION} caracteres")
        elif platform == "facebook":
            if not (text or post.images or post.video or post.link):
                errors.append("facebook: post vazio (sem texto, imagem, vídeo ou link)")
        elif platform == "linkedin":
            if post.video:
                errors.append("linkedin: vídeo ainda não é suportado por esta automação")
            if post.images and post.link:
                errors.append("linkedin: não dá pra combinar imagem e link no mesmo post")
            if len(post.images) > LINKEDIN_MAX_IMAGES:
                errors.append(f"linkedin: no máximo {LINKEDIN_MAX_IMAGES} imagens")
            if not (text or post.images or post.link):
                errors.append("linkedin: post vazio")
            if len(text) > LINKEDIN_MAX_TEXT:
                errors.append(f"linkedin: texto passa de {LINKEDIN_MAX_TEXT} caracteres")


def build_post(row: dict[str, Any], tz_name: str, line: int) -> Post:
    row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
    post_id = _clean(row.get("id"))
    errors: list[str] = []
    if not post_id:
        post_id = f"linha-{line}"
        errors.append("coluna 'id' vazia (obrigatória, e única)")

    try:
        publish_at = parse_datetime(row.get("publish_at"), tz_name)
    except ValueError as exc:
        publish_at = datetime.max.replace(tzinfo=timezone.utc)
        errors.append(str(exc))

    platforms: list[str] = []
    for raw in re.split(r"[,;|/\s]+", _clean(row.get("platforms")).lower()):
        if not raw:
            continue
        name = PLATFORM_ALIASES.get(raw)
        if name is None:
            errors.append(f"plataforma desconhecida: {raw!r} (use {', '.join(PLATFORMS)})")
        elif name not in platforms:
            platforms.append(name)

    post = Post(
        id=post_id,
        publish_at=publish_at,
        platforms=platforms,
        text=_clean(row.get("text")),
        texts={p: _clean(row.get(f"text_{p}")) for p in PLATFORMS if _clean(row.get(f"text_{p}"))},
        images=_split(row.get("images")),
        video=_clean(row.get("video")),
        link=_clean(row.get("link")),
        link_title=_clean(row.get("link_title")),
        alt_text=_clean(row.get("alt_text")),
        enabled=_clean(row.get("status")).lower() not in SKIP_STATUSES,
        errors=errors,
    )
    _validate(post)
    return post


def _read_rows(path: str) -> list[dict[str, Any]]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("posts", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise ScheduleError("JSON deve ser uma lista de posts ou {\"posts\": [...]}")
        return rows
    if ext == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            # Excel em português exporta CSV com ";".
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
            return list(csv.DictReader(fh, delimiter=delimiter))
    if ext in (".xlsx", ".xls"):
        import pandas as pd

        return pd.read_excel(path, dtype=object).to_dict(orient="records")
    raise ScheduleError(f"Formato não suportado: {ext} (use .xlsx, .csv ou .json)")


def load_schedule(path: str, tz_name: str) -> list[Post]:
    if not os.path.isfile(path):
        raise ScheduleError(f"Agenda não encontrada: {path}")
    rows = _read_rows(path)

    # Caminho local de imagem é relativo à pasta da agenda.
    base_dir = os.path.dirname(os.path.abspath(path))
    posts: list[Post] = []
    for line, row in enumerate(rows, start=2):
        if not any(_clean(v) for v in row.values()):
            continue  # linha em branco no meio da planilha
        images = _split(row.get("images"))
        row = dict(row)
        row["images"] = [
            img if is_url(img) or os.path.isabs(img) else os.path.join(base_dir, img) for img in images
        ]
        posts.append(build_post(row, tz_name, line))

    seen: dict[str, int] = {}
    for post in posts:
        seen[post.id] = seen.get(post.id, 0) + 1
    for post in posts:
        if seen[post.id] > 1:
            post.errors.append(f"id duplicado na agenda: {post.id!r}")
    return posts


def platforms_of(posts: Iterable[Post]) -> set[str]:
    return {p for post in posts for p in post.platforms}
