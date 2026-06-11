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
from src.bay_matching import compute_correct_car
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


async def _get_bay_or_404(bay_key: str) -> dict:
    bay = await db.find_bay(bay_key)
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
        "snapshot_warmup_frames": settings.snapshot_warmup_frames,
        "snapshot_max_attempts": settings.snapshot_max_attempts,
        "snapshot_retry_delay_seconds": settings.snapshot_retry_delay_seconds,
        "aruco_dictionary": settings.aruco_dictionary,
        "mqtt_enabled": settings.mqtt_enabled,
        "ha_url": settings.ha_url,
    }


def _bay_id_from_entity(camera_entity_id: str) -> str:
    return camera_entity_id.replace(".", "_").replace(" ", "_").lower()


def _normalize_bay_row(row: dict) -> dict:
    """Ensure every bay has a stable id (legacy rows may only have camera_entity_id)."""
    camera = (row.get("camera_entity_id") or "").strip()
    bay_id = (row.get("id") or row.get("bay_id") or "").strip()
    if not bay_id and camera:
        bay_id = _bay_id_from_entity(camera)
        row["id"] = bay_id
    return row


@router.get("/bays")
async def list_bays():
    await db.repair_all_bay_ids()
    return [_normalize_bay_row(row) for row in await db.list_bays()]


@router.post("/bays/repair")
async def repair_bays():
    """Assign missing bay ids from camera entity, name, or rowid."""
    repaired = await db.repair_all_bay_ids()
    bays = [_normalize_bay_row(row) for row in await db.list_bays()]
    return {"repaired": repaired, "bays": bays}


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


async def _save_bay(bay_id: str, body: BayIn) -> dict:
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


@router.put("/bays/{bay_id}")
async def update_bay(bay_id: str, body: BayIn):
    return await _save_bay(bay_id, body)


@router.post("/bays/{bay_id}")
async def save_bay(bay_id: str, body: BayIn):
    """POST update — HA Ingress mishandles PUT with 307 redirects over HTTPS."""
    return await _save_bay(bay_id, body)


@router.patch("/bays/{bay_id}/expected-car")
async def set_expected_car(bay_id: str, body: ExpectedCarIn):
    """Assign which fleet car should park in this bay."""
    bay = await _get_bay_or_404(bay_id)
    actual_id = await db.ensure_bay_id(bay_id)
    result = await db.upsert_bay(
        actual_id,
        bay["name"],
        bay["camera_entity_id"],
        bay["sort_order"],
        expected_car_number=body.expected_car_number,
    )
    row = await refresh_bay_correct_car(actual_id)
    return {
        **result,
        "correct_car": row.get("correct_car") if row else "uncertain",
    }


@router.post("/bays/{bay_id}/expected-car")
async def set_expected_car_post(bay_id: str, body: ExpectedCarIn):
    return await set_expected_car(bay_id, body)


@router.delete("/bays/{bay_id}")
async def delete_bay(bay_id: str):
    try:
        await db.delete_bay(bay_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@router.post("/bays/{bay_id}/delete")
async def delete_bay_post(bay_id: str):
    try:
        await db.delete_bay(bay_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
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
    fleet = await db.list_fleet()
    enriched = []
    for row in rows:
        if row.get("analyzed_at") is not None:
            correct_car = compute_correct_car(
                occupied=bool(row.get("occupied")),
                car_number_detected=row.get("car_number"),
                aruco_id_detected=row.get("aruco_id_detected"),
                expected_car_number=row.get("expected_car_number"),
                fleet=fleet,
            )
            if correct_car != (row.get("correct_car") or "uncertain"):
                await db.update_bay_correct_car(row["bay_id"], correct_car)
            row["correct_car"] = correct_car
        enriched.append(_enrich_bay_row(row))
    return enriched


@router.post("/analyze")
async def trigger_analyze():
    return await analyze_all_bays()


@router.post("/analyze/{bay_id}")
async def analyze_one(bay_id: str):
    return await analyze_single_bay(bay_id, fetch_fresh=True)


@router.post("/analyze/{bay_id}/debug")
async def analyze_one_debug(bay_id: str):
    """Re-analyze latest snapshot and return vote counts (for troubleshooting)."""
    from src.aruco import PreviousBayDetection, analyze_image_with_debug
    from src.occupancy import is_dark_frame

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

    previous_row = await db.get_bay_state(bay_id)
    if is_dark_frame(image) and previous_row and previous_row.get("analyzed_at"):
        return {
            "bay_id": bay_id,
            "bay_name": bay["name"],
            "snapshot_path": path,
            "snapshot_url": _snapshot_url(path),
            "dictionary": settings.aruco_dictionary,
            "occupied": bool(previous_row.get("occupied")),
            "car_number": previous_row.get("car_number"),
            "aruco_id_detected": previous_row.get("aruco_id_detected"),
            "confidence": float(previous_row.get("confidence") or 0),
            "unchanged": True,
            "debug": {"dark_frame": True, "unchanged": True},
        }

    previous = None
    if previous_row:
        previous = PreviousBayDetection(
            aruco_id_detected=previous_row.get("aruco_id_detected"),
            car_number=previous_row.get("car_number"),
            confidence=float(previous_row.get("confidence") or 0),
        )

    result, debug = analyze_image_with_debug(
        image, fleet, settings.aruco_dictionary, previous=previous
    )
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
            "preprocess_pass": debug.preprocess_pass,
            "color_occupied": debug.color_occupied,
            "red_ratio": debug.red_ratio,
            "gray_ratio": debug.gray_ratio,
            "marker_sticky": debug.marker_sticky,
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
