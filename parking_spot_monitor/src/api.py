"""FastAPI routes."""

import json
import os
import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src.analyzer import analyze_all_cameras, analyze_camera_snapshot, latest_snapshot_for_camera
from src.config import settings
from src.database import db
from src.ha_client import ha_client

router = APIRouter(prefix="/api")


class CameraIn(BaseModel):
    name: str
    entity_id: str


class ZoneIn(BaseModel):
    camera_id: str
    name: str
    points: list[dict] = Field(..., min_length=3)
    sort_order: int = 0


class FleetIn(BaseModel):
    car_number: int
    license_plate: str
    notes: str = ""


@router.get("/status")
async def status():
    return {
        "snapshot_interval_minutes": settings.snapshot_interval_minutes,
        "mqtt_enabled": settings.mqtt_enabled,
        "ha_url": settings.ha_url,
    }


@router.get("/cameras")
async def list_cameras():
    return await db.list_cameras()


@router.post("/cameras")
async def create_camera(body: CameraIn):
    camera_id = body.entity_id.replace(".", "_").replace(" ", "_").lower()
    return await db.upsert_camera(camera_id, body.name, body.entity_id)


@router.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str):
    await db.delete_camera(camera_id)
    return {"ok": True}


@router.get("/zones")
async def list_zones(camera_id: str | None = None):
    return await db.list_zones(camera_id)


@router.post("/zones")
async def create_zone(body: ZoneIn):
    zone_id = str(uuid.uuid4())[:8]
    return await db.upsert_zone(zone_id, body.camera_id, body.name, body.points, body.sort_order)


@router.put("/zones/{zone_id}")
async def update_zone(zone_id: str, body: ZoneIn):
    return await db.upsert_zone(zone_id, body.camera_id, body.name, body.points, body.sort_order)


@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str):
    await db.delete_zone(zone_id)
    return {"ok": True}


@router.get("/fleet")
async def list_fleet():
    return await db.list_fleet()


@router.post("/fleet")
async def upsert_fleet(body: FleetIn):
    return await db.upsert_fleet_car(body.car_number, body.license_plate, body.notes)


@router.delete("/fleet/{car_number}")
async def delete_fleet_car(car_number: int):
    await db.delete_fleet_car(car_number)
    return {"ok": True}


@router.get("/spots")
async def list_spots():
    return await db.list_spot_states()


@router.post("/analyze")
async def trigger_analyze():
    return await analyze_all_cameras()


@router.post("/analyze/{camera_id}")
async def analyze_one(camera_id: str):
    snapshot = latest_snapshot_for_camera(camera_id)
    if not snapshot:
        snapshot_path = ha_client.snapshot_filename(camera_id)
        cameras = {c["id"]: c for c in await db.list_cameras()}
        cam = cameras.get(camera_id)
        if not cam:
            raise HTTPException(404, "Camera not found")
        await ha_client.fetch_snapshot(cam["entity_id"], snapshot_path)
        snapshot = snapshot_path
    return await analyze_camera_snapshot(camera_id, snapshot)


@router.get("/snapshots/{camera_id}/latest")
async def latest_snapshot(camera_id: str):
    path = latest_snapshot_for_camera(camera_id)
    if not path:
        cameras = {c["id"]: c for c in await db.list_cameras()}
        cam = cameras.get(camera_id)
        if not cam:
            raise HTTPException(404, "Camera not found")
        path = ha_client.snapshot_filename(camera_id)
        await ha_client.fetch_snapshot(cam["entity_id"], path)
    rel = os.path.relpath(path, settings.data_dir).replace("\\", "/")
    return {"path": rel, "url": f"/data/{rel}"}


@router.post("/snapshots/{camera_id}/upload")
async def upload_snapshot(camera_id: str, file: UploadFile = File(...)):
    os.makedirs(os.path.join(settings.snapshots_dir, camera_id), exist_ok=True)
    dest = ha_client.snapshot_filename(camera_id)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)
    return {"path": dest, "url": f"/data/{os.path.relpath(dest, settings.data_dir).replace(chr(92), '/')}"}


@router.post("/import-addon-cameras")
async def import_addon_cameras():
    addon_cameras = settings.load_addon_cameras()
    imported = []
    for cam in addon_cameras:
        entity_id = cam.get("entity_id", "")
        name = cam.get("name") or entity_id
        if entity_id:
            camera_id = entity_id.replace(".", "_").replace(" ", "_").lower()
            imported.append(await db.upsert_camera(camera_id, name, entity_id))
    return imported
