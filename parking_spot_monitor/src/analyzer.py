"""Scheduled snapshot capture and zone analysis."""

import logging
import os

import cv2

from src.config import settings
from src.database import db
from src.ha_client import ha_client
from src.mqtt_publisher import mqtt_publisher
from src.ocr import analyze_zone, crop_zone

logger = logging.getLogger(__name__)


async def sync_addon_cameras() -> None:
    """Import cameras from addon options into the database."""
    addon_cameras = settings.load_addon_cameras()
    for cam in addon_cameras:
        entity_id = cam.get("entity_id", "")
        name = cam.get("name") or entity_id
        if not entity_id:
            continue
        camera_id = _slug_id(entity_id)
        await db.upsert_camera(camera_id, name, entity_id)


def _slug_id(text: str) -> str:
    return text.replace(".", "_").replace(" ", "_").lower()


async def analyze_all_cameras() -> dict:
    await sync_addon_cameras()
    cameras = await db.list_cameras()
    fleet = await db.list_fleet()
    results = {"cameras": len(cameras), "zones_analyzed": 0, "errors": []}

    for camera in cameras:
        try:
            snapshot_path = ha_client.snapshot_filename(camera["id"])
            await ha_client.fetch_snapshot(camera["entity_id"], snapshot_path)
            image = cv2.imread(snapshot_path)
            if image is None:
                raise RuntimeError(f"Could not read snapshot: {snapshot_path}")

            zones = await db.list_zones(camera["id"])
            for zone in zones:
                crop = crop_zone(image, zone["points"])
                if crop.size == 0:
                    continue

                ocr = analyze_zone(crop, fleet)
                await db.upsert_spot_state(
                    zone_id=zone["id"],
                    occupied=ocr.occupied,
                    car_number=ocr.car_number,
                    plate_read=ocr.plate_read or None,
                    plate_matched=ocr.plate_matched,
                    confidence=ocr.confidence,
                    snapshot_path=snapshot_path,
                )
                mqtt_publisher.publish_spot_state(
                    zone_id=zone["id"],
                    zone_name=zone["name"],
                    camera_name=camera["name"],
                    occupied=ocr.occupied,
                    car_number=ocr.car_number,
                    plate_read=ocr.plate_read or None,
                    confidence=ocr.confidence,
                )
                results["zones_analyzed"] += 1
        except Exception as exc:
            logger.exception("Analysis failed for camera %s", camera.get("name"))
            results["errors"].append(f"{camera.get('name')}: {exc}")

    return results


async def analyze_camera_snapshot(camera_id: str, snapshot_path: str) -> list[dict]:
    """Analyze an uploaded or existing snapshot (used by manual trigger)."""
    fleet = await db.list_fleet()
    zones = await db.list_zones(camera_id)
    cameras = {c["id"]: c for c in await db.list_cameras()}
    camera = cameras.get(camera_id)
    if not camera:
        raise ValueError(f"Unknown camera: {camera_id}")

    image = cv2.imread(snapshot_path)
    if image is None:
        raise ValueError(f"Could not read image: {snapshot_path}")

    outputs = []
    for zone in zones:
        crop = crop_zone(image, zone["points"])
        if crop.size == 0:
            continue
        ocr = analyze_zone(crop, fleet)
        await db.upsert_spot_state(
            zone_id=zone["id"],
            occupied=ocr.occupied,
            car_number=ocr.car_number,
            plate_read=ocr.plate_read or None,
            plate_matched=ocr.plate_matched,
            confidence=ocr.confidence,
            snapshot_path=snapshot_path,
        )
        mqtt_publisher.publish_spot_state(
            zone_id=zone["id"],
            zone_name=zone["name"],
            camera_name=camera["name"],
            occupied=ocr.occupied,
            car_number=ocr.car_number,
            plate_read=ocr.plate_read or None,
            confidence=ocr.confidence,
        )
        outputs.append(
            {
                "zone_id": zone["id"],
                "zone_name": zone["name"],
                "occupied": ocr.occupied,
                "car_number": ocr.car_number,
                "plate_read": ocr.plate_read,
                "plate_matched": ocr.plate_matched,
                "confidence": ocr.confidence,
            }
        )
    return outputs


def latest_snapshot_for_camera(camera_id: str) -> str | None:
    cam_dir = os.path.join(settings.snapshots_dir, camera_id)
    if not os.path.isdir(cam_dir):
        return None
    files = sorted(
        [f for f in os.listdir(cam_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))],
        reverse=True,
    )
    if not files:
        return None
    return os.path.join(cam_dir, files[0])
