"""Home Assistant API client for camera snapshots."""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

from src.config import settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {500, 502, 503, 504, 408, 429}


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
    def prepare_script_entity(camera_entity_id: str, with_flash: bool = False) -> str:
        """ESPHome prepare_capture / prepare_capture_flash script for this camera."""
        suffix = camera_entity_id.removeprefix("camera.")
        name = "prepare_capture_flash" if with_flash else "prepare_capture"
        return f"script.{suffix}_{name}"

    async def run_prepare_capture(self, camera_entity_id: str, with_flash: bool = False) -> None:
        script_entity = self.prepare_script_entity(camera_entity_id, with_flash=with_flash)
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
        # script.turn_on returns before ESP32 finishes flash + camera settle
        await asyncio.sleep(settings.prepare_capture_wait_ms / 1000.0)
        logger.info("Ran prepare_capture via %s", script_entity)

    async def _request_snapshot(self, entity_id: str) -> bytes:
        # Cache-bust so HA/ESPHome fetches a fresh frame
        url = f"{self.base_url}/api/camera_proxy/{entity_id}?t={int(time.time() * 1000)}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.get(url, headers=self._headers())
            if response.status_code in RETRYABLE_STATUS:
                body = response.text[:200] if response.text else ""
                logger.warning(
                    "Snapshot HTTP %s for %s (body: %s)",
                    response.status_code,
                    entity_id,
                    body,
                )
            response.raise_for_status()
            return response.content

    async def fetch_snapshot(self, entity_id: str, dest_path: str) -> str:
        if not self.token:
            raise RuntimeError(
                "Home Assistant API token missing. "
                "Ensure homeassistant_api: true in add-on config and restart the add-on."
            )

        try:
            await self.run_prepare_capture(entity_id, with_flash=settings.flash_before_capture)
        except Exception:
            logger.exception("prepare_capture failed for %s, continuing anyway", entity_id)

        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        warmup = max(0, settings.snapshot_warmup_frames)
        for frame in range(1, warmup + 1):
            try:
                await self._request_snapshot(entity_id)
                logger.info(
                    "Warmup snapshot %s/%s for %s (discarded — lets OV2640 settle)",
                    frame,
                    warmup,
                    entity_id,
                )
                if frame < warmup:
                    await asyncio.sleep(0.4)
            except Exception:
                logger.warning(
                    "Warmup snapshot %s/%s failed for %s, continuing",
                    frame,
                    warmup,
                    entity_id,
                    exc_info=True,
                )

        attempts = max(1, settings.snapshot_max_attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                content = await self._request_snapshot(entity_id)
                with open(dest_path, "wb") as f:
                    f.write(content)
                logger.info(
                    "Saved snapshot for %s to %s (attempt %s/%s)",
                    entity_id,
                    dest_path,
                    attempt,
                    attempts,
                )
                return dest_path
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code not in RETRYABLE_STATUS or attempt == attempts:
                    raise
                logger.info(
                    "Retrying snapshot for %s in %ss (attempt %s/%s)",
                    entity_id,
                    settings.snapshot_retry_delay_seconds,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(settings.snapshot_retry_delay_seconds)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == attempts:
                    raise
                logger.info(
                    "Retrying snapshot for %s after error: %s (attempt %s/%s)",
                    entity_id,
                    exc,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(settings.snapshot_retry_delay_seconds)

        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch snapshot for {entity_id}")

    def snapshot_filename(self, camera_id: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        return os.path.join(settings.snapshots_dir, camera_id, f"{ts}.jpg")


ha_client = HAClient()
