"""Application configuration from environment variables."""

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "/data"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8099")))
    ha_url: str = field(default_factory=lambda: os.getenv("HA_URL", "http://supervisor/core"))
    ha_token: str = field(default_factory=lambda: os.getenv("HA_TOKEN", ""))
    snapshot_interval_minutes: int = field(
        default_factory=lambda: int(os.getenv("SNAPSHOT_INTERVAL", "5"))
    )
    capture_delay_seconds: int = field(
        default_factory=lambda: int(os.getenv("CAPTURE_DELAY", "3"))
    )
    flash_before_capture: bool = field(
        default_factory=lambda: os.getenv("FLASH_BEFORE_CAPTURE", "true").lower() == "true"
    )
    prepare_capture_wait_ms: int = field(
        default_factory=lambda: int(os.getenv("PREPARE_CAPTURE_WAIT_MS", "600"))
    )
    aruco_dictionary: str = field(
        default_factory=lambda: os.getenv("ARUCO_DICTIONARY", "DICT_4X4_50")
    )
    mqtt_enabled: bool = field(
        default_factory=lambda: os.getenv("MQTT_ENABLED", "true").lower() == "true"
    )
    mqtt_broker: str = field(default_factory=lambda: os.getenv("MQTT_BROKER", "core-mosquitto"))
    mqtt_port: int = field(default_factory=lambda: int(os.getenv("MQTT_PORT", "1883")))
    mqtt_username: str = field(default_factory=lambda: os.getenv("MQTT_USERNAME", ""))
    mqtt_password: str = field(default_factory=lambda: os.getenv("MQTT_PASSWORD", ""))
    mqtt_topic_prefix: str = field(
        default_factory=lambda: os.getenv("MQTT_TOPIC_PREFIX", "parking_spot")
    )

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "parking.db")

    @property
    def snapshots_dir(self) -> str:
        return os.path.join(self.data_dir, "snapshots")

    @property
    def addon_bays_path(self) -> str:
        return os.path.join(self.data_dir, "addon_bays.json")

    @property
    def options_json_path(self) -> str:
        return os.path.join(self.data_dir, "options.json")

    def _normalize_bays(self, raw) -> list[dict]:
        """Accept list, single dict, or empty — HA add-on options vary by UI mode."""
        if raw is None:
            return []
        if isinstance(raw, dict):
            if "camera_entity_id" in raw or "name" in raw:
                return [raw]
            logger.warning("Ignoring unrecognized bays dict: %s", list(raw.keys()))
            return []
        if isinstance(raw, list):
            bays = []
            for item in raw:
                if isinstance(item, dict):
                    bays.append(item)
                else:
                    logger.warning("Skipping invalid bay entry (expected object): %r", item)
            return bays
        logger.warning("Ignoring bays config with unexpected type: %s", type(raw).__name__)
        return []

    def load_addon_bays(self) -> list[dict]:
        raw = None
        if os.path.exists(self.options_json_path):
            try:
                with open(self.options_json_path, encoding="utf-8") as f:
                    opts = json.load(f)
                raw = opts.get("bays")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read options.json: %s", exc)

        if raw is None and os.path.exists(self.addon_bays_path):
            try:
                with open(self.addon_bays_path, encoding="utf-8") as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read addon_bays.json: %s", exc)

        return self._normalize_bays(raw)


settings = Settings()
