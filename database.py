"""Database module for storing vehicles and monitoring settings."""
import aiosqlite
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "bot.db"


@dataclass
class Vehicle:
    id: int
    name: str
    reference_number: str  # Numer referencyjny SENT/RMPD
    registration_number: str  # Numer rejestracyjny
    locator_id: str  # ID GPS
    monitoring_enabled: bool = False
    monitoring_interval: int = 60  # minutes
    last_location: Optional[str] = None
    last_update: Optional[str] = None
    created_at: Optional[str] = None


async def init_db():
    """Initialize database and create tables."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                reference_number TEXT NOT NULL,
                registration_number TEXT NOT NULL,
                locator_id TEXT NOT NULL,
                monitoring_enabled BOOLEAN DEFAULT FALSE,
                monitoring_interval INTEGER DEFAULT 60,
                last_location TEXT,
                last_update TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS check_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id INTEGER,
                location TEXT,
                location_time TEXT,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
            )
        """)

        await db.commit()


async def add_vehicle(name: str, ref_num: str, reg_num: str, locator: str) -> int:
    """Add a new vehicle to the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO vehicles (name, reference_number, registration_number, locator_id)
               VALUES (?, ?, ?, ?)""",
            (name, ref_num, reg_num, locator)
        )
        await db.commit()
        return cursor.lastrowid


async def get_all_vehicles() -> list[Vehicle]:
    """Get all vehicles from the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM vehicles ORDER BY name")
        rows = await cursor.fetchall()
        return [Vehicle(**dict(row)) for row in rows]


async def get_vehicle(vehicle_id: int) -> Optional[Vehicle]:
    """Get a specific vehicle by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,))
        row = await cursor.fetchone()
        return Vehicle(**dict(row)) if row else None


async def delete_vehicle(vehicle_id: int) -> bool:
    """Delete a vehicle from the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))
        await db.commit()
        return True


async def update_monitoring(vehicle_id: int, enabled: bool, interval: int = None):
    """Update monitoring settings for a vehicle."""
    async with aiosqlite.connect(DB_PATH) as db:
        if interval is not None:
            await db.execute(
                "UPDATE vehicles SET monitoring_enabled = ?, monitoring_interval = ? WHERE id = ?",
                (enabled, interval, vehicle_id)
            )
        else:
            await db.execute(
                "UPDATE vehicles SET monitoring_enabled = ? WHERE id = ?",
                (enabled, vehicle_id)
            )
        await db.commit()


async def update_vehicle_location(vehicle_id: int, location: str, location_time: str):
    """Update the last known location for a vehicle."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE vehicles SET last_location = ?, last_update = ? WHERE id = ?",
            (location, location_time, vehicle_id)
        )
        await db.execute(
            "INSERT INTO check_history (vehicle_id, location, location_time) VALUES (?, ?, ?)",
            (vehicle_id, location, location_time)
        )
        await db.commit()


async def get_monitored_vehicles() -> list[Vehicle]:
    """Get all vehicles with monitoring enabled."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM vehicles WHERE monitoring_enabled = TRUE"
        )
        rows = await cursor.fetchall()
        return [Vehicle(**dict(row)) for row in rows]
