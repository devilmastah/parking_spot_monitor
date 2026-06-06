"""Home Assistant API client for camera snapshots."""

import asyncio
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

    @staticmethod
    def prepare_script_entity(camera_entity_id: str) -> str:
        """ESPHome script entity matching parking_bay_esp32cam.yaml (id: prepare_capture)."""
        suffix = camera_entity_id.removeprefix("camera.")
        return f"script.{suffix}_prepare_capture"

    async def run_prepare_capture(self, camera_entity_id: str) -> None:
        script_entity = self.prepare_script_entity(camera_entity_id)
        url = f"{self.base_url}/api/services/script/turn_on"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                headers=self._headers(),
                json={"entity_id": script_entity},
            )
            if response.status_code == 404:
                logger.warning("Prepare script not found: %s", script_entity)
                return
            response.raise_for_status()
        await asyncio.sleep(settings.prepare_capture_wait_ms / 1000.0)
        logger.info("Ran prepare_capture via %s", script_entity)

    async def fetch_snapshot(self, entity_id: str, dest_path: str) -> str:
        if not self.token:
            raise RuntimeError(
                "Home Assistant API token missing. "
                "Ensure homeassistant_api: true in add-on config and restart the add-on."
            )
        if settings.flash_before_capture:
            try:
                await self.run_prepare_capture(entity_id)
            except Exception:
                logger.exception("prepare_capture failed for %s, continuing anyway", entity_id)

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
