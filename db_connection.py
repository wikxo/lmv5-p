# db_connection.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Final

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_PATH: Final[Path] = Path(__file__).with_name("schema.sql")

REQUIRED_SCHEMA_OBJECTS: Final[dict[str, str]] = {
    "channel_state": "table",
    "messages": "table",
    "messages_fts": "table",
    "trg_messages_ai": "trigger",
    "trg_messages_ad": "trigger",
    "trg_messages_au": "trigger",
}


class DatabaseManager:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._write_semaphore = asyncio.Semaphore(1)

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DatabaseManager 尚未初始化，請先呼叫 initialize()。")
        return self._conn

    @property
    def write_lock(self) -> asyncio.Semaphore:
        return self._write_semaphore

    async def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        await self._apply_pragmas()
        await self._run_migrations()
        health = await self.verify_schema()
        logger.info(
            "SQLite 初始化完成 (path=%s, journal_mode=%s, schema_ok=%s)",
            self._db_path,
            health["journal_mode"],
            health["is_healthy"],
        )

    async def _apply_pragmas(self) -> None:
        conn = self.connection
        pragmas = (
            "PRAGMA journal_mode = WAL;",
            "PRAGMA synchronous = NORMAL;",
            "PRAGMA foreign_keys = ON;",
            "PRAGMA temp_store = MEMORY;",
            "PRAGMA cache_size = -64000;",
            "PRAGMA busy_timeout = 5000;",
        )
        for stmt in pragmas:
            await conn.execute(stmt)
        await conn.commit()

    async def _run_migrations(self) -> None:
        if not SCHEMA_PATH.exists():
            raise FileNotFoundError(f"找不到資料庫 Schema 檔案：{SCHEMA_PATH}")
        schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
        async with self._write_semaphore:
            await self.connection.executescript(schema_sql)
            await self.connection.execute(
                "UPDATE channel_state SET mode = 'helpful' WHERE mode NOT IN ('helpful', 'roast');"
            )
            await self.connection.commit()

    async def verify_schema(self) -> dict[str, object]:
        conn = self.connection
        async with conn.execute("PRAGMA journal_mode;") as cursor:
            row = await cursor.fetchone()
            journal_mode = str(row[0]).lower() if row else "unknown"

        async with conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'trigger');"
        ) as cursor:
            rows = await cursor.fetchall()
            existing = {str(r["name"]): str(r["type"]) for r in rows}

        missing = [
            name
            for name, expected_type in REQUIRED_SCHEMA_OBJECTS.items()
            if existing.get(name) != expected_type
        ]

        return {
            "journal_mode": journal_mode,
            "existing_objects": existing,
            "missing_objects": missing,
            "is_healthy": journal_mode == "wal" and len(missing) == 0,
        }

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite 連線已安全關閉。")