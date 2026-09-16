"""Resolução e upload de imagens para o Meta Ads (local, URL ou hash já existente)."""

from __future__ import annotations

import logging
import os
import tempfile
from urllib.parse import urlparse

import requests
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.adobjects.adimage import AdImage

logger = logging.getLogger(__name__)


class ImageUploader:
    """Faz cache de uploads para não subir a mesma imagem mais de uma vez."""

    def __init__(self, account: AdAccount, dry_run: bool = False):
        self._account = account
        self._dry_run = dry_run
        self._hash_cache: dict[str, str] = {}

    def resolve_image_hash(self, image_ref: str | None, existing_hash: str | None = None) -> str | None:
        """Recebe um caminho local, uma URL ou um hash e devolve o image_hash do Meta."""
        if existing_hash:
            return existing_hash
        if not image_ref:
            return None

        if image_ref in self._hash_cache:
            return self._hash_cache[image_ref]

        if self._dry_run:
            fake_hash = f"DRY_RUN_IMAGE_HASH::{os.path.basename(image_ref)}"
            self._hash_cache[image_ref] = fake_hash
            return fake_hash

        local_path = self._ensure_local_file(image_ref)
        image = AdImage(parent_id=self._account.get_id())
        image[AdImage.Field.filename] = local_path
        image.remote_create()
        image_hash = image[AdImage.Field.hash]
        self._hash_cache[image_ref] = image_hash
        logger.info("Imagem enviada: %s -> hash %s", image_ref, image_hash)
        return image_hash

    @staticmethod
    def _ensure_local_file(image_ref: str) -> str:
        parsed = urlparse(image_ref)
        if parsed.scheme in ("http", "https"):
            response = requests.get(image_ref, timeout=30)
            response.raise_for_status()
            suffix = os.path.splitext(parsed.path)[1] or ".jpg"
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(response.content)
            tmp.close()
            return tmp.name

        if not os.path.isfile(image_ref):
            raise FileNotFoundError(f"Arquivo de imagem não encontrado: {image_ref}")
        return image_ref
