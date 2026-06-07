"""FastAPI routes."""

import logging
import os
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from src.analyzer import (
    analyze_all_bays,
    analyze_single_bay,
    capture_bay_snapshot,
    latest_snapshot_for_bay,
    refresh_bay_correct_car,
)
from src.config import settings
from src.database import db
from src.ha_client import ha_client

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


def _snapshot_url(path: str | None) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    rel = os.path.relpath(path, settings.data_dir).replace("\\", "/")
    return f"data/{rel}"


def _enrich_bay_row(row: dict) -> dict:
    latest = latest_snapshot_for_bay(row["bay_id"])
    analyzed = row.get("snapshot_path")
    path = latest or analyzed
    row["snapshot_path_analyzed"] = analyzed
    row["snapshot_path"] = path
    row["snapshot_url"] = _snapshot_url(path)
    return row


def _snapshot_response(path: str, bay_id: str | None = None) -> dict:
    rel = os.path.relpath(path, settings.data_dir).replace("\\", "/")
    result = {"path": rel, "url": f"data/{rel}"}
    if bay_id:
        result["bay_id"] = bay_id
    return result


class BayIn(BaseModel):
    name: str
    camera_entity_id: str
    sort_order: int = 0
    expected_car_number: int | None = None


class FleetIn(BaseModel):
    car_number: int
    aruco_id: int = Field(..., ge=0)
    notes: str = ""


class ExpectedCarIn(BaseModel):
    expected_car_number: int | None = None


async def _get_bay_or_404(bay_id: str) -> dict:
    bay = next((b for b in await db.list_bays() if b["id"] == bay_id), None)
    if not bay:
        raise HTTPException(404, "Bay not found")
    return bay


async def _after_bay_saved(bay_id: str) -> None:
    await refresh_bay_correct_car(bay_id)


@router.get("/status")
async def status():
    return {
        "snapshot_interval_minutes": settings.snapshot_interval_minutes,
        "capture_delay_seconds": settings.capture_delay_seconds,
        "flash_before_capture": settings.flash_before_capture,
        "prepare_capture_wait_ms": settings.prepare_capture_wait_ms,
        "snapshot_max_attempts": settings.snapshot_max_attempts,
        "snapshot_retry_delay_seconds": settings.snapshot_retry_delay_seconds,
        "aruco_dictionary": settings.aruco_dictionary,
        "mqtt_enabled": settings.mqtt_enabled,
        "ha_url": settings.ha_url,
    }


@router.get("/bays")
async def list_bays():
    return await db.list_bays()


def _bay_id_from_entity(camera_entity_id: str) -> str:
    return camera_entity_id.replace(".", "_").replace(" ", "_").lower()


@router.post("/bays")
async def create_bay(body: BayIn):
    camera_entity_id = body.camera_entity_id.strip()
    if not camera_entity_id:
        raise HTTPException(400, "Camera entity ID is required")
    bay_id = _bay_id_from_entity(camera_entity_id)
    try:
        result = await db.create_bay(
            bay_id,
            body.name.strip() or camera_entity_id,
            camera_entity_id,
            body.sort_order,
            expected_car_number=body.expected_car_number,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    await _after_bay_saved(bay_id)
    return result


@router.put("/bays/{bay_id}")
async def update_bay(bay_id: str, body: BayIn):
    camera_entity_id = body.camera_entity_id.strip()
    if not camera_entity_id:
        raise HTTPException(400, "Camera entity ID is required")
    try:
        result = await db.update_bay(
            bay_id,
            body.name.strip() or camera_entity_id,
            camera_entity_id,
            body.sort_order,
            expected_car_number=body.expected_car_number,
        )
    except ValueError as exc:
        status = 404 if "not found" in str(exc).lower() else 409
        raise HTTPException(status, str(exc)) from exc
    await _after_bay_saved(bay_id)
    return result


@router.patch("/bays/{bay_id}/expected-car")
async def set_expected_car(bay_id: str, body: ExpectedCarIn):
    """Assign which fleet car should park in this bay."""
    bay = await _get_bay_or_404(bay_id)
    result = await db.upsert_bay(
        bay_id,
        bay["name"],
        bay["camera_entity_id"],
        bay["sort_order"],
        expected_car_number=body.expected_car_number,
    )
    row = await refresh_bay_correct_car(bay_id)
    return {
        **result,
        "correct_car": row.get("correct_car") if row else "uncertain",
    }


@router.delete("/bays/{bay_id}")
async def delete_bay(bay_id: str):
    await db.delete_bay(bay_id)
    return {"ok": True}


@router.get("/fleet")
async def list_fleet():
    return await db.list_fleet()


@router.post("/fleet")
async def upsert_fleet(body: FleetIn):
    try:
        return await db.upsert_fleet_car(body.car_number, body.aruco_id, body.notes)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to save fleet car %s", body.car_number)
        raise HTTPException(500, f"Could not save fleet car: {exc}") from exc


@router.delete("/fleet/{car_number}")
async def delete_fleet_car(car_number: int):
    await db.delete_fleet_car(car_number)
    return {"ok": True}


@router.get("/spots")
async def list_spots():
    rows = await db.list_bay_states()
    for row in rows:
        row["snapshot_url"] = _snapshot_url(row.get("snapshot_path"))
    return rows


@router.get("/dashboard")
async def dashboard():
    """All bays with last snapshot URL and analysis results for the debug UI."""
    rows = await db.list_dashboard()
    return [_enrich_bay_row(row) for row in rows]


@router.post("/analyze")
async def trigger_analyze():
    return await analyze_all_bays()


@router.post("/analyze/{bay_id}")
async def analyze_one(bay_id: str):
    return await analyze_single_bay(bay_id, fetch_fresh=True)


@router.post("/analyze/{bay_id}/debug")
async def analyze_one_debug(bay_id: str):
    """Re-analyze latest snapshot and return vote counts (for troubleshooting)."""
    from src.aruco import analyze_image_with_debug

    bays = {b["id"]: b for b in await db.list_bays()}
    bay = bays.get(bay_id)
    if not bay:
        raise HTTPException(404, "Bay not found")

    fleet = await db.list_fleet()
    path = latest_snapshot_for_bay(bay_id)
    if not path:
        raise HTTPException(404, "No snapshot on disk — take a snapshot first")

    import cv2

    image = cv2.imread(path)
    if image is None:
        raise HTTPException(500, f"Could not read snapshot: {path}")

    result, debug = analyze_image_with_debug(image, fleet, settings.aruco_dictionary)
    return {
        "bay_id": bay_id,
        "bay_name": bay["name"],
        "snapshot_path": path,
        "snapshot_url": _snapshot_url(path),
        "dictionary": settings.aruco_dictionary,
        "occupied": result.occupied,
        "car_number": result.car_number,
        "aruco_id_detected": result.aruco_id_detected,
        "confidence": result.confidence,
        "debug": {
            "votes": debug.votes,
            "best_confidence": debug.best_confidence,
            "attempts": debug.attempts,
            "used_flip": debug.used_flip,
        },
    }


@router.post("/snapshots/{bay_id}/capture")
async def capture_snapshot(bay_id: str):
    """Trigger a new still capture from the bay camera."""
    try:
        result = await capture_bay_snapshot(bay_id)
    except ValueError:
        raise HTTPException(404, "Bay not found") from None
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return _snapshot_response(result["snapshot_path"], bay_id)


@router.get("/snapshots/{bay_id}/latest")
async def latest_snapshot(bay_id: str, fetch: bool = Query(default=True)):
    """Return latest snapshot. Set fetch=false to only use cached file on disk."""
    bays = {b["id"]: b for b in await db.list_bays()}
    bay = bays.get(bay_id)
    if not bay:
        raise HTTPException(404, "Bay not found")

    if fetch:
        try:
            result = await capture_bay_snapshot(bay_id)
            path = result["snapshot_path"]
        except Exception as exc:
            raise HTTPException(500, str(exc)) from exc
    else:
        path = latest_snapshot_for_bay(bay_id)
        if not path:
            raise HTTPException(404, "No cached snapshot")

    return _snapshot_response(path, bay_id)


@router.post("/snapshots/{bay_id}/upload")
async def upload_snapshot(bay_id: str, file: UploadFile = File(...)):
    os.makedirs(os.path.join(settings.snapshots_dir, bay_id), exist_ok=True)
    dest = ha_client.snapshot_filename(bay_id)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    rel = os.path.relpath(dest, settings.data_dir).replace("\\", "/")
    return {"path": dest, "url": f"data/{rel}"}


@router.post("/import-addon-bays")
async def import_addon_bays():
    from src.analyzer import sync_addon_bays
    from src.mqtt_publisher import publish_all_bays_mqtt

    await sync_addon_bays()
    bays = await db.list_bays()
    mqtt = await publish_all_bays_mqtt()
    return {"bays": bays, "mqtt": mqtt}


@router.post("/mqtt/publish")
async def mqtt_publish():
    """Re-send Home Assistant MQTT discovery for all bays."""
    return await publish_all_bays_mqtt()
