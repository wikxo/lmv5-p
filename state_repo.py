# state_repo.py
from __future__ import annotations

from dataclasses import dataclass

from config import VALID_MODES
from db_connection import DatabaseManager


@dataclass(frozen=True, slots=True)
class ChannelStateRecord:
    channel_id: int
    guild_id: int
    mode: str
    last_indexed_msg_id: int
    updated_at: int


class StateRepository:
    def __init__(self, db: DatabaseManager, default_mode: str = "helpful") -> None:
        self._db = db
        self._default_mode = default_mode

    async def get_channel_state(
        self,
        channel_id: int,
        guild_id: int,
    ) -> ChannelStateRecord:
        query = """
            SELECT channel_id, guild_id, mode, last_indexed_msg_id, updated_at
            FROM channel_state
            WHERE channel_id = ?;
        """
        async with self._db.connection.execute(query, (channel_id,)) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return ChannelStateRecord(
                channel_id=channel_id,
                guild_id=guild_id,
                mode=self._default_mode,
                last_indexed_msg_id=0,
                updated_at=0,
            )

        mode_val = str(row["mode"])
        if mode_val not in VALID_MODES:
            mode_val = "helpful"

        return ChannelStateRecord(
            channel_id=int(row["channel_id"]),
            guild_id=int(row["guild_id"]),
            mode=mode_val,
            last_indexed_msg_id=int(row["last_indexed_msg_id"]),
            updated_at=int(row["updated_at"]),
        )

    async def get_channel_mode(self, channel_id: int, guild_id: int) -> str:
        state = await self.get_channel_state(channel_id, guild_id)
        return state.mode

    async def set_channel_mode(
        self,
        channel_id: int,
        guild_id: int,
        mode: str,
    ) -> ChannelStateRecord:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in VALID_MODES:
            raise ValueError(f"不支援的頻道模式：{mode!r}（僅支援 helpful 與 roast）")

        upsert_sql = """
            INSERT INTO channel_state (channel_id, guild_id, mode, last_indexed_msg_id, updated_at)
            VALUES (?, ?, ?, 0, unixepoch())
            ON CONFLICT(channel_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                mode = excluded.mode,
                updated_at = unixepoch();
        """
        async with self._db.write_lock:
            await self._db.connection.execute(
                upsert_sql,
                (channel_id, guild_id, normalized_mode),
            )
            await self._db.connection.commit()

        return await self.get_channel_state(channel_id, guild_id)

    async def get_last_indexed_msg_id(self, channel_id: int) -> int:
        query = "SELECT last_indexed_msg_id FROM channel_state WHERE channel_id = ?;"
        async with self._db.connection.execute(query, (channel_id,)) as cursor:
            row = await cursor.fetchone()
            return int(row["last_indexed_msg_id"]) if row else 0

    async def update_last_indexed_msg_id(
        self,
        channel_id: int,
        guild_id: int,
        last_indexed_msg_id: int,
    ) -> None:
        upsert_sql = """
            INSERT INTO channel_state (channel_id, guild_id, mode, last_indexed_msg_id, updated_at)
            VALUES (?, ?, ?, ?, unixepoch())
            ON CONFLICT(channel_id) DO UPDATE SET
                guild_id = excluded.guild_id,
                last_indexed_msg_id = MAX(channel_state.last_indexed_msg_id, excluded.last_indexed_msg_id),
                updated_at = unixepoch();
        """
        async with self._db.write_lock:
            await self._db.connection.execute(
                upsert_sql,
                (channel_id, guild_id, self._default_mode, last_indexed_msg_id),
            )
            await self._db.connection.commit()