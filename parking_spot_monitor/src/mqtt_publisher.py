"""MQTT publisher with Home Assistant discovery."""

import json
import logging
import re

import paho.mqtt.client as mqtt

from src.config import settings

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


class MQTTPublisher:
    def __init__(self):
        self.client: mqtt.Client | None = None
        self.discovered_bays: set[str] = set()

    def connect(self) -> None:
        if not settings.mqtt_enabled:
            logger.info("MQTT disabled")
            return

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

        try:
            self.client.connect(settings.mqtt_broker, settings.mqtt_port, 60)
            self.client.loop_start()
            logger.info("Connected to MQTT broker %s:%s", settings.mqtt_broker, settings.mqtt_port)
        except Exception:
            logger.exception("Failed to connect to MQTT broker")
            self.client = None

    def disconnect(self) -> None:
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    def _topic(self, bay_id: str, suffix: str) -> str:
        return f"{settings.mqtt_topic_prefix}/{bay_id}/{suffix}"

    def publish_discovery(self, bay_id: str, bay_name: str) -> None:
        if not self.client or bay_id in self.discovered_bays:
            return

        device = {
            "identifiers": [f"parking_bay_{bay_id}"],
            "name": f"Parking {bay_name}",
            "manufacturer": "Parking Spot Monitor",
            "model": "ESP32-CAM ArUco",
        }
        prefix = settings.mqtt_topic_prefix
        unique = _slug(bay_id)

        configs = [
            {
                "platform": "binary_sensor",
                "name": f"{bay_name} Occupied",
                "unique_id": f"parking_{unique}_occupied",
                "state_topic": f"{prefix}/{bay_id}/occupied",
                "payload_on": "ON",
                "payload_off": "OFF",
                "device": device,
                "icon": "mdi:car",
            },
            {
                "platform": "sensor",
                "name": f"{bay_name} Car Number",
                "unique_id": f"parking_{unique}_car_number",
                "state_topic": f"{prefix}/{bay_id}/car_number",
                "device": device,
                "icon": "mdi:numeric",
            },
            {
                "platform": "sensor",
                "name": f"{bay_name} ArUco ID",
                "unique_id": f"parking_{unique}_aruco_id",
                "state_topic": f"{prefix}/{bay_id}/aruco_id",
                "device": device,
                "icon": "mdi:qrcode",
            },
            {
                "platform": "sensor",
                "name": f"{bay_name} Confidence",
                "unique_id": f"parking_{unique}_confidence",
                "state_topic": f"{prefix}/{bay_id}/confidence",
                "unit_of_measurement": "%",
                "device": device,
                "icon": "mdi:percent",
            },
        ]

        for cfg in configs:
            platform = cfg.pop("platform")
            topic = f"homeassistant/{platform}/{unique}/{cfg['unique_id']}/config"
            self.client.publish(topic, json.dumps(cfg), retain=True)

        self.discovered_bays.add(bay_id)
        logger.info("Published MQTT discovery for bay %s", bay_id)

    def publish_bay_state(
        self,
        bay_id: str,
        bay_name: str,
        occupied: bool,
        car_number: int | None,
        aruco_id_detected: int | None,
        confidence: float,
    ) -> None:
        if not self.client:
            return

        self.publish_discovery(bay_id, bay_name)
        self.client.publish(self._topic(bay_id, "occupied"), "ON" if occupied else "OFF", retain=True)
        self.client.publish(
            self._topic(bay_id, "car_number"),
            str(car_number) if car_number is not None else "unknown",
            retain=True,
        )
        self.client.publish(
            self._topic(bay_id, "aruco_id"),
            str(aruco_id_detected) if aruco_id_detected is not None else "none",
            retain=True,
        )
        self.client.publish(
            self._topic(bay_id, "confidence"),
            str(round(confidence * 100, 1)),
            retain=True,
        )
        self.client.publish(
            self._topic(bay_id, "state"),
            json.dumps(
                {
                    "occupied": occupied,
                    "car_number": car_number,
                    "aruco_id_detected": aruco_id_detected,
                    "confidence": confidence,
                }
            ),
            retain=True,
        )


mqtt_publisher = MQTTPublisher()
