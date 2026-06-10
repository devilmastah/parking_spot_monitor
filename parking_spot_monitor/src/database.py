"""SQLite persistence for parking bays, fleet, and analysis results."""

import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)


def _slug_id(text: str) -> str:
    return text.replace(".", "_").replace(" ", "_").lower()


def _slug_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bays (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    camera_entity_id TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    expected_car_number INTEGER,
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
    correct_car TEXT NOT NULL DEFAULT 'uncertain',
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
            await self._migrate(db)
            await db.commit()

    async def _migrate(self, db) -> None:
        cur = await db.execute("PRAGMA table_info(bays)")
        bay_cols = {row[1] for row in await cur.fetchall()}
        if "expected_car_number" not in bay_cols:
            await db.execute("ALTER TABLE bays ADD COLUMN expected_car_number INTEGER")

        cur = await db.execute("PRAGMA table_info(bay_states)")
        state_cols = {row[1] for row in await cur.fetchall()}
        if "correct_car" not in state_cols:
            await db.execute(
                "ALTER TABLE bay_states ADD COLUMN correct_car TEXT NOT NULL DEFAULT 'uncertain'"
            )
        if "car_parked_at" not in state_cols:
            await db.execute("ALTER TABLE bay_states ADD COLUMN car_parked_at TEXT")
        if "car_left_at" not in state_cols:
            await db.execute("ALTER TABLE bay_states ADD COLUMN car_left_at TEXT")

        await self._migrate_fleet_table(db)
        await self._migrate_bay_ids(db)

    async def _migrate_bay_ids(self, db) -> None:
        await self._repair_bay_ids_in_tx(db)

    async def _repair_bay_ids_in_tx(self, db) -> int:
        """Assign ids to legacy rows (empty id) using camera, name, or rowid."""
        cur = await db.execute(
            "SELECT rowid, id, camera_entity_id, name FROM bays ORDER BY rowid"
        )
        repaired = 0
        for rowid, old_id, camera, name in await cur.fetchall():
            old_id = (old_id or "").strip()
            if old_id:
                continue
            camera = (camera or "").strip()
            name = (name or "").strip()
            if camera:
                new_id = _slug_id(camera)
            elif name:
                new_id = _slug_name(name) or f"bay_{rowid}"
            else:
                new_id = f"bay_{rowid}"
            logger.info("Repairing bay id rowid=%s name=%r camera=%r → %s", rowid, name, camera, new_id)
            await db.execute("UPDATE bays SET id = ? WHERE rowid = ?", (new_id, rowid))
            if old_id:
                await db.execute(
                    "UPDATE bay_states SET bay_id = ? WHERE bay_id = ?",
                    (new_id, old_id),
                )
            repaired += 1
        if repaired:
            await db.execute(
                "DELETE FROM bay_states WHERE bay_id = '' OR bay_id IS NULL"
            )
        return repaired

    async def repair_all_bay_ids(self) -> int:
        async with self.connect() as db:
            repaired = await self._repair_bay_ids_in_tx(db)
            await db.commit()
            return repaired

    async def _migrate_fleet_table(self, db) -> None:
        """Upgrade v1 fleet (license_plate) to v2+ fleet (aruco_id)."""
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fleet'"
        )
        if not await cur.fetchone():
            return

        cur = await db.execute("PRAGMA table_info(fleet)")
        cols = {row[1] for row in await cur.fetchall()}
        if "aruco_id" in cols:
            return

        if "license_plate" not in cols:
            logger.warning("Unknown fleet table schema %s — recreating", cols)
        else:
            logger.info("Migrating fleet table from v1 (license plates) to ArUco schema")

        await db.execute("DROP TABLE fleet")
        await db.execute(
            """
            CREATE TABLE fleet (
                car_number INTEGER PRIMARY KEY,
                aruco_id INTEGER NOT NULL UNIQUE,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

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
            cur = await db.execute(
                """
                SELECT rowid AS _rowid, id, name, camera_entity_id,
                       sort_order, expected_car_number, created_at
                FROM bays
                ORDER BY sort_order, name
                """
            )
            return [dict(row) for row in await cur.fetchall()]

    async def find_bay(self, bay_key: str) -> dict | None:
        key = (bay_key or "").strip()
        if not key:
            return None
        for bay in await self.list_bays():
            stored_id = (bay.get("id") or "").strip()
            camera = (bay.get("camera_entity_id") or "").strip()
            rowid = bay.get("_rowid")
            if key == stored_id:
                return bay
            if camera and key in (camera, _slug_id(camera)):
                return bay
            if rowid is not None and key == f"bay_{rowid}":
                return bay
        return None

    async def ensure_bay_id(self, bay_key: str) -> str:
        bay = await self.find_bay(bay_key)
        if not bay:
            raise ValueError(f"Bay not found: {bay_key}")
        stored_id = (bay.get("id") or "").strip()
        if stored_id:
            return stored_id
        async with self.connect() as db:
            actual_id = await self._ensure_bay_id_in_tx(db, bay)
            await db.commit()
            return actual_id

    async def _ensure_bay_id_in_tx(self, db, bay: dict) -> str:
        stored_id = (bay.get("id") or "").strip()
        if stored_id:
            return stored_id
        rowid = bay.get("_rowid")
        camera = (bay.get("camera_entity_id") or "").strip()
        name = (bay.get("name") or "").strip()
        if camera:
            new_id = _slug_id(camera)
        elif name:
            new_id = _slug_name(name) or (f"bay_{rowid}" if rowid else "bay_unknown")
        elif rowid is not None:
            new_id = f"bay_{rowid}"
        else:
            raise ValueError("Bay has no id, camera, or rowid — cannot repair")
        await db.execute("UPDATE bays SET id = ? WHERE rowid = ?", (new_id, rowid))
        return new_id

    async def upsert_bay(
        self,
        bay_id: str,
        name: str,
        camera_entity_id: str,
        sort_order: int = 0,
        expected_car_number: int | None = None,
    ) -> dict:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO bays (id, name, camera_entity_id, sort_order, expected_car_number, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    camera_entity_id=excluded.camera_entity_id,
                    sort_order=excluded.sort_order,
                    expected_car_number=excluded.expected_car_number
                """,
                (bay_id, name, camera_entity_id, sort_order, expected_car_number, _now()),
            )
            await db.commit()
        return {
            "id": bay_id,
            "name": name,
            "camera_entity_id": camera_entity_id,
            "sort_order": sort_order,
            "expected_car_number": expected_car_number,
        }

    async def create_bay(
        self,
        bay_id: str,
        name: str,
        camera_entity_id: str,
        sort_order: int = 0,
        expected_car_number: int | None = None,
    ) -> dict:
        """Insert a new bay — fails if id or camera entity already exists."""
        async with self.connect() as db:
            try:
                await db.execute(
                    """
                    INSERT INTO bays (id, name, camera_entity_id, sort_order, expected_car_number, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (bay_id, name, camera_entity_id, sort_order, expected_car_number, _now()),
                )
                await db.commit()
            except aiosqlite.IntegrityError as exc:
                message = str(exc).lower()
                if "camera_entity_id" in message:
                    raise ValueError(
                        f"Camera {camera_entity_id} is already assigned to another bay"
                    ) from exc
                if "id" in message or "primary key" in message:
                    raise ValueError(
                        f"A bay already exists for camera {camera_entity_id}"
                    ) from exc
                raise ValueError(f"Could not create bay: {exc}") from exc
        return {
            "id": bay_id,
            "name": name,
            "camera_entity_id": camera_entity_id,
            "sort_order": sort_order,
            "expected_car_number": expected_car_number,
        }

    async def update_bay(
        self,
        bay_key: str,
        name: str,
        camera_entity_id: str,
        sort_order: int = 0,
        expected_car_number: int | None = None,
    ) -> dict:
        bay = await self.find_bay(bay_key)
        if not bay:
            raise ValueError(f"Bay not found: {bay_key}")
        async with self.connect() as db:
            try:
                actual_id = await self._ensure_bay_id_in_tx(db, bay)
                cur = await db.execute(
                    """
                    UPDATE bays
                    SET name = ?, camera_entity_id = ?, sort_order = ?, expected_car_number = ?
                    WHERE id = ?
                    """,
                    (name, camera_entity_id, sort_order, expected_car_number, actual_id),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"Bay not found: {bay_key}")
                await db.commit()
                bay_id = actual_id
            except aiosqlite.IntegrityError as exc:
                if "camera_entity_id" in str(exc).lower():
                    raise ValueError(
                        f"Camera {camera_entity_id} is already assigned to another bay"
                    ) from exc
                raise ValueError(f"Could not update bay: {exc}") from exc
        return {
            "id": bay_id,
            "name": name,
            "camera_entity_id": camera_entity_id,
            "sort_order": sort_order,
            "expected_car_number": expected_car_number,
        }

    async def delete_bay(self, bay_key: str) -> None:
        bay = await self.find_bay(bay_key)
        if not bay:
            raise ValueError(f"Bay not found: {bay_key}")
        async with self.connect() as db:
            stored_id = (bay.get("id") or "").strip()
            rowid = bay.get("_rowid")
            if stored_id:
                await db.execute("DELETE FROM bay_states WHERE bay_id = ?", (stored_id,))
                await db.execute("DELETE FROM bays WHERE id = ?", (stored_id,))
            elif rowid is not None:
                await db.execute(
                    "DELETE FROM bay_states WHERE bay_id = '' OR bay_id IS NULL"
                )
                await db.execute("DELETE FROM bays WHERE rowid = ?", (rowid,))
            else:
                raise ValueError(f"Bay not found: {bay_key}")
            await db.commit()

    async def list_fleet(self) -> list[dict]:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM fleet ORDER BY car_number")
            return [dict(row) for row in await cur.fetchall()]

    async def upsert_fleet_car(self, car_number: int, aruco_id: int, notes: str = "") -> dict:
        async with self.connect() as db:
            try:
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
            except aiosqlite.IntegrityError as exc:
                message = str(exc).lower()
                if "aruco_id" in message:
                    raise ValueError(
                        f"ArUco ID {aruco_id} is already assigned to another car"
                    ) from exc
                if "car_number" in message:
                    raise ValueError(
                        f"Car number {car_number} already exists"
                    ) from exc
                raise ValueError(f"Fleet entry conflict: {exc}") from exc
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
        correct_car: str = "uncertain",
        car_parked_at: str | None = None,
        car_left_at: str | None = None,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO bay_states (
                    bay_id, occupied, car_number, aruco_id_detected,
                    confidence, correct_car, analyzed_at, snapshot_path,
                    car_parked_at, car_left_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bay_id) DO UPDATE SET
                    occupied=excluded.occupied,
                    car_number=excluded.car_number,
                    aruco_id_detected=excluded.aruco_id_detected,
                    confidence=excluded.confidence,
                    correct_car=excluded.correct_car,
                    analyzed_at=excluded.analyzed_at,
                    snapshot_path=excluded.snapshot_path,
                    car_parked_at=excluded.car_parked_at,
                    car_left_at=excluded.car_left_at
                """,
                (
                    bay_id,
                    int(occupied),
                    car_number,
                    aruco_id_detected,
                    confidence,
                    correct_car,
                    _now(),
                    snapshot_path,
                    car_parked_at,
                    car_left_at,
                ),
            )
            await db.commit()

    async def update_bay_correct_car(self, bay_id: str, correct_car: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "UPDATE bay_states SET correct_car = ? WHERE bay_id = ?",
                (correct_car, bay_id),
            )
            await db.commit()

    async def get_bay_state(self, bay_id: str) -> dict | None:
        async with self.connect() as db:
            cur = await db.execute("SELECT * FROM bay_states WHERE bay_id = ?", (bay_id,))
            row = await cur.fetchone()
            if not row:
                return None
            result = dict(row)
            result["occupied"] = bool(result.get("occupied"))
            return result

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

    async def list_dashboard(self) -> list[dict]:
        """All bays with optional last analysis (LEFT JOIN)."""
        async with self.connect() as db:
            cur = await db.execute(
                """
                SELECT
                    b.id AS bay_id,
                    b.name AS bay_name,
                    b.camera_entity_id,
                    b.sort_order,
                    b.expected_car_number,
                    s.occupied,
                    s.car_number,
                    s.aruco_id_detected,
                    s.confidence,
                    s.correct_car,
                    s.analyzed_at,
                    s.snapshot_path,
                    s.car_parked_at,
                    s.car_left_at
                FROM bays b
                LEFT JOIN bay_states s ON s.bay_id = b.id
                ORDER BY b.sort_order, b.name
                """
            )
            rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                if row.get("occupied") is not None:
                    row["occupied"] = bool(row["occupied"])
            return rows


db = Database()
