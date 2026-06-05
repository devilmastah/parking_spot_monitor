"""SQLite persistence for cameras, zones, fleet, and analysis results."""

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from src.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    entity_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zones (
    id TEXT PRIMARY KEY,
    camera_id TEXT NOT NULL,
    name TEXT NOT NULL,
    points_json TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (camera_id) REFERENCES cameras(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fleet (
    car_number INTEGER PRIMARY KEY,
    license_plate TEXT NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spot_states (
    zone_id TEXT PRIMARY KEY,
    occupied INTEGER NOT NULL DEFAULT 0,
    car_number INTEGER,
    plate_read TEXT,
    plate_matched TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    analyzed_at TEXT,
    snapshot_path TEXT,
    FOREIGN KEY (zone_id) REFERENCES zones(id) ON DELETE CASCADE
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or settings.db_path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    @asynccontextmanager
    async def connect(self):
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def list_cameras(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM cameras ORDER BY name")
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_camera(self, camera_id: str, name: str, entity_id: str) -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO cameras (id, name, entity_id, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, entity_id=excluded.entity_id
                """,
                (camera_id, name, entity_id, _now()),
            )
            await db.commit()
        return {"id": camera_id, "name": name, "entity_id": entity_id}

    async def delete_camera(self, camera_id: str) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            await db.commit()

    async def list_zones(self, camera_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM zones"
        params: tuple = ()
        if camera_id:
            query += " WHERE camera_id = ?"
            params = (camera_id,)
        query += " ORDER BY sort_order, name"
        async with self.connect() as db:
            cur = await db.execute(query, params)
            rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                row["points"] = json.loads(row.pop("points_json"))
            return rows

    async def upsert_zone(
        self, zone_id: str, camera_id: str, name: str, points: list, sort_order: int = 0
    ) -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO zones (id, camera_id, name, points_json, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    points_json=excluded.points_json,
                    sort_order=excluded.sort_order
                """,
                (zone_id, camera_id, name, json.dumps(points), sort_order, _now()),
            )
            await db.commit()
        return {"id": zone_id, "camera_id": camera_id, "name": name, "points": points}

    async def delete_zone(self, zone_id: str) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
            await db.execute("DELETE FROM spot_states WHERE zone_id = ?", (zone_id,))
            await db.commit()

    async def list_fleet(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM fleet ORDER BY car_number")
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_fleet_car(self, car_number: int, license_plate: str, notes: str = "") -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO fleet (car_number, license_plate, notes, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(car_number) DO UPDATE SET
                    license_plate=excluded.license_plate,
                    notes=excluded.notes
                """,
                (car_number, license_plate.upper().strip(), notes, _now()),
            )
            await db.commit()
        return {"car_number": car_number, "license_plate": license_plate.upper().strip(), "notes": notes}

    async def delete_fleet_car(self, car_number: int) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM fleet WHERE car_number = ?", (car_number,))
            await db.commit()

    async def upsert_spot_state(
        self,
        zone_id: str,
        occupied: bool,
        car_number: int | None,
        plate_read: str | None,
        plate_matched: str | None,
        confidence: float,
        snapshot_path: str | None,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO spot_states (
                    zone_id, occupied, car_number, plate_read, plate_matched,
                    confidence, analyzed_at, snapshot_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(zone_id) DO UPDATE SET
                    occupied=excluded.occupied,
                    car_number=excluded.car_number,
                    plate_read=excluded.plate_read,
                    plate_matched=excluded.plate_matched,
                    confidence=excluded.confidence,
                    analyzed_at=excluded.analyzed_at,
                    snapshot_path=excluded.snapshot_path
                """,
                (
                    zone_id,
                    int(occupied),
                    car_number,
                    plate_read,
                    plate_matched,
                    confidence,
                    _now(),
                    snapshot_path,
                ),
            )
            await db.commit()

    async def list_spot_states(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT s.*, z.name AS zone_name, z.camera_id, c.name AS camera_name
                FROM spot_states s
                JOIN zones z ON z.id = s.zone_id
                JOIN cameras c ON c.id = z.camera_id
                ORDER BY c.name, z.sort_order, z.name
                """
            )
            rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                row["occupied"] = bool(row["occupied"])
            return rows

    async def get_spot_state(self, zone_id: str) -> dict | None:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM spot_states WHERE zone_id = ?", (zone_id,))
            row = await cur.fetchone()
            if not row:
                return None
            result = dict(row)
            result["occupied"] = bool(result["occupied"])
            return result


db = Database()
