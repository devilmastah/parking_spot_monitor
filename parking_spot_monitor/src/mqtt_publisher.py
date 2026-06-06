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
        if not self.client:
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
            {
                "platform": "sensor",
                "name": f"{bay_name} Correct Car",
                "unique_id": f"parking_{unique}_correct_car",
                "state_topic": f"{prefix}/{bay_id}/correct_car",
                "device": device,
                "icon": "mdi:car-select",
            },
            {
                "platform": "sensor",
                "name": f"{bay_name} Expected Car",
                "unique_id": f"parking_{unique}_expected_car",
                "state_topic": f"{prefix}/{bay_id}/expected_car",
                "device": device,
                "icon": "mdi:car-arrow-right",
            },
        ]

        for cfg in configs:
            platform = cfg.pop("platform")
            object_id = cfg["unique_id"].removeprefix(f"parking_{unique}_")
            cfg["object_id"] = object_id
            topic = f"homeassistant/{platform}/{unique}/{object_id}/config"
            info = self.client.publish(topic, json.dumps(cfg), retain=True)
            if info.rc != 0:
                logger.warning("MQTT discovery publish failed for %s rc=%s", topic, info.rc)

        self.discovered_bays.add(bay_id)
        logger.info("Published MQTT discovery for bay %s (%s entities)", bay_name, len(configs))

    def publish_bay_state(
        self,
        bay_id: str,
        bay_name: str,
        occupied: bool,
        car_number: int | None,
        aruco_id_detected: int | None,
        confidence: float,
        correct_car: str = "uncertain",
        expected_car_number: int | None = None,
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
        self.client.publish(self._topic(bay_id, "correct_car"), correct_car, retain=True)
        self.client.publish(
            self._topic(bay_id, "expected_car"),
            str(expected_car_number) if expected_car_number is not None else "none",
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
                    "correct_car": correct_car,
                    "expected_car_number": expected_car_number,
                }
            ),
            retain=True,
        )


async def publish_all_bays_mqtt() -> dict:
    """Publish HA MQTT discovery and last-known state for every configured bay."""
    from src.database import db

    if not settings.mqtt_enabled:
        return {"published": 0, "error": "MQTT disabled"}

    if not mqtt_publisher.client:
        return {"published": 0, "error": "MQTT not connected"}

    rows = await db.list_dashboard()
    if not rows:
        logger.info("MQTT: no bays configured — nothing to publish")
        return {"published": 0, "error": "No bays configured"}

    for row in rows:
        has_result = row.get("analyzed_at") is not None
        mqtt_publisher.publish_bay_state(
            bay_id=row["bay_id"],
            bay_name=row["bay_name"],
            occupied=bool(row.get("occupied")) if has_result else False,
            car_number=row.get("car_number") if has_result else None,
            aruco_id_detected=row.get("aruco_id_detected") if has_result else None,
            confidence=float(row.get("confidence") or 0.0) if has_result else 0.0,
            correct_car=row.get("correct_car") or "uncertain",
            expected_car_number=row.get("expected_car_number"),
        )

    logger.info("MQTT: published discovery + state for %s bay(s)", len(rows))
    return {"published": len(rows), "bays": [r["bay_id"] for r in rows]}


mqtt_publisher = MQTTPublisher()
