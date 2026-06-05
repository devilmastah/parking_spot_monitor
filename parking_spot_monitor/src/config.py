"""Application configuration from environment variables."""

import json
import os
from dataclasses import dataclass, field


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

    def load_addon_bays(self) -> list[dict]:
        if not os.path.exists(self.addon_bays_path):
            return []
        with open(self.addon_bays_path, encoding="utf-8") as f:
            return json.load(f)


settings = Settings()
