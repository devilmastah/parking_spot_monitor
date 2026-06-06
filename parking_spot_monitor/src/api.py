"""FastAPI routes."""

import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.analyzer import analyze_all_bays, analyze_single_bay, latest_snapshot_for_bay
from src.config import settings
from src.database import db
from src.ha_client import ha_client

router = APIRouter(prefix="/api")


class BayIn(BaseModel):
    name: str
    camera_entity_id: str
    sort_order: int = 0


class FleetIn(BaseModel):
    car_number: int
    aruco_id: int = Field(..., ge=0)
    notes: str = ""


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


@router.post("/bays")
async def create_bay(body: BayIn):
    bay_id = body.camera_entity_id.replace(".", "_").replace(" ", "_").lower()
    return await db.upsert_bay(bay_id, body.name, body.camera_entity_id, body.sort_order)


@router.put("/bays/{bay_id}")
async def update_bay(bay_id: str, body: BayIn):
    return await db.upsert_bay(bay_id, body.name, body.camera_entity_id, body.sort_order)


@router.delete("/bays/{bay_id}")
async def delete_bay(bay_id: str):
    await db.delete_bay(bay_id)
    return {"ok": True}


@router.get("/fleet")
async def list_fleet():
    return await db.list_fleet()


@router.post("/fleet")
async def upsert_fleet(body: FleetIn):
    return await db.upsert_fleet_car(body.car_number, body.aruco_id, body.notes)


@router.delete("/fleet/{car_number}")
async def delete_fleet_car(car_number: int):
    await db.delete_fleet_car(car_number)
    return {"ok": True}


@router.get("/spots")
async def list_spots():
    return await db.list_bay_states()


@router.post("/analyze")
async def trigger_analyze():
    return await analyze_all_bays()


@router.post("/analyze/{bay_id}")
async def analyze_one(bay_id: str):
    return await analyze_single_bay(bay_id, fetch_fresh=True)


@router.get("/snapshots/{bay_id}/latest")
async def latest_snapshot(bay_id: str):
    path = latest_snapshot_for_bay(bay_id)
    bays = {b["id"]: b for b in await db.list_bays()}
    bay = bays.get(bay_id)
    if not bay:
        raise HTTPException(404, "Bay not found")

    if not path:
        path = ha_client.snapshot_filename(bay_id)
        await ha_client.fetch_snapshot(bay["camera_entity_id"], path)

    rel = os.path.relpath(path, settings.data_dir).replace("\\", "/")
    return {"path": rel, "url": f"/data/{rel}"}


@router.post("/snapshots/{bay_id}/upload")
async def upload_snapshot(bay_id: str, file: UploadFile = File(...)):
    os.makedirs(os.path.join(settings.snapshots_dir, bay_id), exist_ok=True)
    dest = ha_client.snapshot_filename(bay_id)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    rel = os.path.relpath(dest, settings.data_dir).replace("\\", "/")
    return {"path": dest, "url": f"/data/{rel}"}


@router.post("/import-addon-bays")
async def import_addon_bays():
    from src.analyzer import sync_addon_bays

    await sync_addon_bays()
    return await db.list_bays()
