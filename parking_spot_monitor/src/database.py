"""SQLite persistence for parking bays, fleet, and analysis results."""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from src.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS bays (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    camera_entity_id TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fleet (
    car_number INTEGER PRIMARY KEY,
    aruco_id INTEGER NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bay_states (
    bay_id TEXT PRIMARY KEY,
    occupied INTEGER NOT NULL DEFAULT 0,
    car_number INTEGER,
    aruco_id_detected INTEGER,
    confidence REAL NOT NULL DEFAULT 0,
    analyzed_at TEXT,
    snapshot_path TEXT,
    FOREIGN KEY (bay_id) REFERENCES bays(id) ON DELETE CASCADE
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

    async def list_bays(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM bays ORDER BY sort_order, name")
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_bay(
        self, bay_id: str, name: str, camera_entity_id: str, sort_order: int = 0
    ) -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO bays (id, name, camera_entity_id, sort_order, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    camera_entity_id=excluded.camera_entity_id,
                    sort_order=excluded.sort_order
                """,
                (bay_id, name, camera_entity_id, sort_order, _now()),
            )
            await db.commit()
        return {
            "id": bay_id,
            "name": name,
            "camera_entity_id": camera_entity_id,
            "sort_order": sort_order,
        }

    async def delete_bay(self, bay_id: str) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM bays WHERE id = ?", (bay_id,))
            await db.execute("DELETE FROM bay_states WHERE bay_id = ?", (bay_id,))
            await db.commit()

    async def list_fleet(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM fleet ORDER BY car_number")
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_fleet_car(self, car_number: int, aruco_id: int, notes: str = "") -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO fleet (car_number, aruco_id, notes, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(car_number) DO UPDATE SET
                    aruco_id=excluded.aruco_id,
                    notes=excluded.notes
                """,
                (car_number, aruco_id, notes, _now()),
            )
            await db.commit()
        return {"car_number": car_number, "aruco_id": aruco_id, "notes": notes}

    async def delete_fleet_car(self, car_number: int) -> None:
        async with self.connect() as db:
            await db.execute("DELETE FROM fleet WHERE car_number = ?", (car_number,))
            await db.commit()

    async def upsert_bay_state(
        self,
        bay_id: str,
        occupied: bool,
        car_number: int | None,
        aruco_id_detected: int | None,
        confidence: float,
        snapshot_path: str | None,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO bay_states (
                    bay_id, occupied, car_number, aruco_id_detected,
                    confidence, analyzed_at, snapshot_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bay_id) DO UPDATE SET
                    occupied=excluded.occupied,
                    car_number=excluded.car_number,
                    aruco_id_detected=excluded.aruco_id_detected,
                    confidence=excluded.confidence,
                    analyzed_at=excluded.analyzed_at,
                    snapshot_path=excluded.snapshot_path
                """,
                (
                    bay_id,
                    int(occupied),
                    car_number,
                    aruco_id_detected,
                    confidence,
                    _now(),
                    snapshot_path,
                ),
            )
            await db.commit()

    async def list_bay_states(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT s.*, b.name AS bay_name, b.camera_entity_id
                FROM bay_states s
                JOIN bays b ON b.id = s.bay_id
                ORDER BY b.sort_order, b.name
                """
            )
            rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                row["occupied"] = bool(row["occupied"])
            return rows


db = Database()
