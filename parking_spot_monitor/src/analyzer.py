"""Sequential bay capture and ArUco analysis."""

import asyncio
import logging
import os

import cv2

from src.aruco import analyze_image
from src.bay_matching import compute_correct_car
from src.config import settings
from src.database import db
from src.ha_client import ha_client
from src.mqtt_publisher import mqtt_publisher

logger = logging.getLogger(__name__)


def _slug_id(text: str) -> str:
    return text.replace(".", "_").replace(" ", "_").lower()


async def sync_addon_bays() -> None:
    """Import bays from add-on options into the database."""
    existing = {b["id"]: b for b in await db.list_bays()}
    for bay in settings.load_addon_bays():
        if not isinstance(bay, dict):
            logger.warning("Skipping invalid bay entry: %r", bay)
            continue
        entity_id = str(bay.get("camera_entity_id", "")).strip()
        name = str(bay.get("name") or entity_id).strip()
        if not entity_id:
            continue
        bay_id = _slug_id(entity_id)
        sort_order = int(bay.get("sort_order") or 0)
        expected_raw = bay.get("expected_car_number")
        if expected_raw not in (None, ""):
            expected_car_number = int(expected_raw)
        else:
            expected_car_number = existing.get(bay_id, {}).get("expected_car_number")
        await db.upsert_bay(
            bay_id, name, entity_id, sort_order, expected_car_number=expected_car_number
        )


async def _analyze_bay_image(bay: dict, image_path: str, fleet: list[dict]) -> dict:
    image = cv2.imread(image_path)
    if image is None:
        raise RuntimeError(f"Could not read snapshot: {image_path}")

    result = analyze_image(image, fleet, settings.aruco_dictionary)
    correct_car = compute_correct_car(
        occupied=result.occupied,
        car_number_detected=result.car_number,
        aruco_id_detected=result.aruco_id_detected,
        expected_car_number=bay.get("expected_car_number"),
        fleet=fleet,
    )
    await db.upsert_bay_state(
        bay_id=bay["id"],
        occupied=result.occupied,
        car_number=result.car_number,
        aruco_id_detected=result.aruco_id_detected,
        confidence=result.confidence,
        snapshot_path=image_path,
        correct_car=correct_car,
    )
    mqtt_publisher.publish_bay_state(
        bay_id=bay["id"],
        bay_name=bay["name"],
        occupied=result.occupied,
        car_number=result.car_number,
        aruco_id_detected=result.aruco_id_detected,
        confidence=result.confidence,
        correct_car=correct_car,
        expected_car_number=bay.get("expected_car_number"),
    )
    return {
        "bay_id": bay["id"],
        "bay_name": bay["name"],
        "occupied": result.occupied,
        "car_number": result.car_number,
        "aruco_id_detected": result.aruco_id_detected,
        "confidence": result.confidence,
        "correct_car": correct_car,
        "expected_car_number": bay.get("expected_car_number"),
        "snapshot_path": image_path,
    }


async def analyze_all_bays() -> dict:
    """Capture and analyze each bay one at a time."""
    await sync_addon_bays()
    bays = await db.list_bays()
    fleet = await db.list_fleet()
    results = {"bays": len(bays), "analyzed": 0, "details": [], "errors": []}

    for index, bay in enumerate(bays):
        if index > 0 and settings.capture_delay_seconds > 0:
            logger.info(
                "Waiting %ss before next bay capture",
                settings.capture_delay_seconds,
            )
            await asyncio.sleep(settings.capture_delay_seconds)

        try:
            snapshot_path = ha_client.snapshot_filename(bay["id"])
            await ha_client.fetch_snapshot(bay["camera_entity_id"], snapshot_path)
            detail = await _analyze_bay_image(bay, snapshot_path, fleet)
            results["details"].append(detail)
            results["analyzed"] += 1
        except Exception as exc:
            logger.exception("Analysis failed for bay %s", bay.get("name"))
            results["errors"].append(f"{bay.get('name')}: {exc}")

    return results


async def analyze_single_bay(bay_id: str, fetch_fresh: bool = True) -> dict:
    bays = {b["id"]: b for b in await db.list_bays()}
    bay = bays.get(bay_id)
    if not bay:
        raise ValueError(f"Unknown bay: {bay_id}")

    fleet = await db.list_fleet()
    snapshot_path = latest_snapshot_for_bay(bay_id)

    if fetch_fresh or not snapshot_path:
        snapshot_path = ha_client.snapshot_filename(bay_id)
        await ha_client.fetch_snapshot(bay["camera_entity_id"], snapshot_path)

    return await _analyze_bay_image(bay, snapshot_path, fleet)


def latest_snapshot_for_bay(bay_id: str) -> str | None:
    bay_dir = os.path.join(settings.snapshots_dir, bay_id)
    if not os.path.isdir(bay_dir):
        return None
    files = sorted(
        [f for f in os.listdir(bay_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))],
        reverse=True,
    )
    if not files:
        return None
    return os.path.join(bay_dir, files[0])


async def refresh_bay_correct_car(bay_id: str) -> dict | None:
    """Recompute correct_car after expected-car assignment changes."""
    rows = await db.list_dashboard()
    row = next((r for r in rows if r["bay_id"] == bay_id), None)
    if not row:
        return None

    fleet = await db.list_fleet()
    has_result = row.get("analyzed_at") is not None
    correct_car = compute_correct_car(
        occupied=bool(row.get("occupied")) if has_result else False,
        car_number_detected=row.get("car_number") if has_result else None,
        aruco_id_detected=row.get("aruco_id_detected") if has_result else None,
        expected_car_number=row.get("expected_car_number"),
        fleet=fleet,
    )

    if has_result:
        await db.update_bay_correct_car(bay_id, correct_car)
    row["correct_car"] = correct_car

    from src.mqtt_publisher import publish_dashboard_row_mqtt

    publish_dashboard_row_mqtt(row, has_result)
    return row


async def capture_bay_snapshot(bay_id: str) -> dict:
    """Fetch a fresh still from the bay camera and save it to disk."""
    bays = {b["id"]: b for b in await db.list_bays()}
    bay = bays.get(bay_id)
    if not bay:
        raise ValueError(f"Unknown bay: {bay_id}")

    snapshot_path = ha_client.snapshot_filename(bay_id)
    await ha_client.fetch_snapshot(bay["camera_entity_id"], snapshot_path)
    return {
        "bay_id": bay_id,
        "bay_name": bay["name"],
        "camera_entity_id": bay["camera_entity_id"],
        "snapshot_path": snapshot_path,
    }
