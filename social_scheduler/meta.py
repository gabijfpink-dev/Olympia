"""Publicação orgânica na Página do Facebook e no Instagram (Graph API)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Callable

from meta_ads_ops.graph import GraphClient, GraphError
from social_scheduler.schedule import Post, is_url
from social_scheduler.settings import FacebookSettings, InstagramSettings

logger = logging.getLogger(__name__)

# Imagem costuma ficar pronta na hora; vídeo (Reels) leva de segundos a
# alguns minutos pra Meta processar antes de poder publicar.
CONTAINER_POLL_INTERVAL = 10
CONTAINER_POLL_TIMEOUT = 10 * 60


class FacebookPublisher:
    platform = "facebook"

    def __init__(self, settings: FacebookSettings, client: GraphClient | None = None):
        self.page_id = settings.page_id
        self.client = client or GraphClient(settings.page_access_token, settings.api_version)

    def _upload_photo(self, image: str, extra: dict[str, str], caption: str = "") -> dict:
        data = dict(extra)
        if caption:
            data["caption"] = caption
        path = f"{self.page_id}/photos"
        if is_url(image):
            return self.client.post(path, {**data, "url": image})
        return self.client.post_file(path, "source", image, data)

    def host_image(self, image_path: str) -> str:
        """Sobe uma imagem local como foto oculta da Página e devolve a URL
        pública dela (CDN do Facebook). O Instagram só aceita imagem por URL;
        assim a pasta local funciona igual às campanhas, e PNG vira JPEG."""
        photo = self.client.post_file(f"{self.page_id}/photos", "source", image_path, {"published": "false"})
        info = self.client.get(photo["id"], {"fields": "images"})
        sizes = info.get("images") or []
        if not sizes:
            raise GraphError({"message": f"Facebook não devolveu a URL da imagem {image_path}"})
        return max(sizes, key=lambda i: i.get("width", 0) * i.get("height", 0))["source"]

    def publish(self, post: Post, scheduled_for: datetime | None = None) -> str:
        """Publica já ou, com scheduled_for, deixa agendado no próprio Facebook
        (aparece em Meta Business Suite > Planejador e sai mesmo com o PC desligado)."""
        text = post.text_for(self.platform)
        when: dict[str, str] = {}
        if scheduled_for is not None:
            when = {"published": "false", "scheduled_publish_time": str(int(scheduled_for.timestamp()))}

        if post.video:
            result = self.client.post(
                f"{self.page_id}/videos", {"file_url": post.video, "description": text, **when}
            )
            return result["id"]

        if len(post.images) == 1:
            result = self._upload_photo(post.images[0], when or {"published": "true"}, caption=text)
            return result.get("post_id") or result["id"]

        data: dict[str, str] = dict(when)
        if text:
            data["message"] = text
        if post.images:
            # Várias fotos num post só: sobe cada uma sem publicar e anexa no feed.
            # Pra post agendado, a Meta exige as fotos como "temporary".
            hidden = {"published": "false"}
            if when:
                hidden["temporary"] = "true"
            for i, image in enumerate(post.images):
                photo = self._upload_photo(image, hidden)
                data[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo["id"]})
        elif post.link:
            data["link"] = post.link
        return self.client.post(f"{self.page_id}/feed", data)["id"]


class InstagramPublisher:
    platform = "instagram"

    def __init__(
        self,
        settings: InstagramSettings,
        client: GraphClient | None = None,
        image_host: Callable[[str], str] | None = None,
    ):
        self.ig_user_id = settings.ig_user_id
        self.client = client or GraphClient(
            settings.access_token, settings.api_version, host=settings.host
        )
        self.image_host = image_host

    def _image_url(self, image: str) -> str:
        if is_url(image):
            return image
        if self.image_host is None:
            raise GraphError(
                {"message": "imagem local no Instagram precisa de META_PAGE_ID e META_PAGE_ACCESS_TOKEN "
                            "no .env (a imagem é hospedada pela Página do Facebook)"}
            )
        return self.image_host(image)

    def _wait_ready(self, container_id: str) -> None:
        deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT
        while True:
            status = self.client.get(container_id, {"fields": "status_code,status"})
            code = status.get("status_code")
            if code in (None, "FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise GraphError(
                    {"message": f"Instagram recusou a mídia ({code}): {status.get('status', '')}"}
                )
            if time.monotonic() > deadline:
                raise GraphError(
                    {"message": f"Mídia {container_id} não ficou pronta em {CONTAINER_POLL_TIMEOUT}s"}
                )
            logger.info("Instagram ainda processando a mídia (%s)...", code)
            time.sleep(CONTAINER_POLL_INTERVAL)

    def _create(self, data: dict[str, str]) -> str:
        container_id = self.client.post(f"{self.ig_user_id}/media", data)["id"]
        self._wait_ready(container_id)
        return container_id

    def publish(self, post: Post) -> str:
        caption = post.text_for(self.platform)

        if post.video:
            container = self._create(
                {"media_type": "REELS", "video_url": post.video, "caption": caption, "share_to_feed": "true"}
            )
        elif len(post.images) == 1:
            data = {"image_url": self._image_url(post.images[0]), "caption": caption}
            if post.alt_text:
                data["alt_text"] = post.alt_text
            container = self._create(data)
        else:
            children = [
                self._create({"image_url": self._image_url(image), "is_carousel_item": "true"})
                for image in post.images
            ]
            container = self._create(
                {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
            )

        return self.client.post(f"{self.ig_user_id}/media_publish", {"creation_id": container})["id"]


def list_pages(user_token: str, api_version: str) -> list[dict]:
    """Páginas que o token enxerga, com o token de cada uma e o Instagram vinculado."""
    client = GraphClient(user_token, api_version)
    return client.paginate(
        "me/accounts",
        {"fields": "id,name,access_token,tasks,instagram_business_account{id,username}"},
    )
