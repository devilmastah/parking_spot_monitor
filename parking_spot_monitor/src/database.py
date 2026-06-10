"""SQLite persistence for parking bays, fleet, and analysis results."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import aiosqlite

from src.config import settings

logger = logging.getLogger(__name__)

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

        await self._migrate_fleet_table(db)
        await self._migrate_bay_ids(db)

    async def _migrate_bay_ids(self, db) -> None:
        """Fill missing bay ids from camera_entity_id (legacy imports)."""
        cur = await db.execute("SELECT id, camera_entity_id FROM bays")
        for row in await cur.fetchall():
            old_id = (row[0] or "").strip()
            camera = (row[1] or "").strip()
            if not camera:
                continue
            new_id = camera.replace(".", "_").replace(" ", "_").lower()
            if old_id:
                continue
            logger.info("Migrating bay id for camera %s → %s", camera, new_id)
            await db.execute(
                "UPDATE bay_states SET bay_id = ? WHERE bay_id = ?",
                (new_id, old_id),
            )
            await db.execute(
                """
                UPDATE bays
                SET id = ?
                WHERE camera_entity_id = ? AND (id IS NULL OR id = '')
                """,
                (new_id, camera),
            )

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
            cur = await db.execute("SELECT * FROM bays ORDER BY sort_order, name")
            return [dict(row) for row in await cur.fetchall()]

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
        bay_id: str,
        name: str,
        camera_entity_id: str,
        sort_order: int = 0,
        expected_car_number: int | None = None,
    ) -> dict:
        async with self.connect() as db:
            try:
                cur = await db.execute(
                    """
                    UPDATE bays
                    SET name = ?, camera_entity_id = ?, sort_order = ?, expected_car_number = ?
                    WHERE id = ?
                    """,
                    (name, camera_entity_id, sort_order, expected_car_number, bay_id),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"Bay not found: {bay_id}")
                await db.commit()
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
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO bay_states (
                    bay_id, occupied, car_number, aruco_id_detected,
                    confidence, correct_car, analyzed_at, snapshot_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bay_id) DO UPDATE SET
                    occupied=excluded.occupied,
                    car_number=excluded.car_number,
                    aruco_id_detected=excluded.aruco_id_detected,
                    confidence=excluded.confidence,
                    correct_car=excluded.correct_car,
                    analyzed_at=excluded.analyzed_at,
                    snapshot_path=excluded.snapshot_path
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
                    s.snapshot_path
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
