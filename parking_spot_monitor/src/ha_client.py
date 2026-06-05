"""Home Assistant API client for camera snapshots."""

import logging
import os
from datetime import datetime, timezone

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class HAClient:
    def __init__(self):
        self.base_url = settings.ha_url.rstrip("/")
        self.token = settings.ha_token

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def fetch_snapshot(self, entity_id: str, dest_path: str) -> str:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        url = f"{self.base_url}/api/camera_proxy/{entity_id}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(response.content)

        logger.info("Saved snapshot for %s to %s", entity_id, dest_path)
        return dest_path

    def snapshot_filename(self, camera_id: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return os.path.join(settings.snapshots_dir, camera_id, f"{ts}.jpg")


ha_client = HAClient()
