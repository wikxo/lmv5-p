# message_repo.py
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Sequence

from db_connection import DatabaseManager


@dataclass(frozen=True, slots=True)
class StoredMessage:
    message_id: int
    channel_id: int
    guild_id: int
    author_id: int
    author_name: str
    author_display_name: str
    content: str
    has_attachment: bool
    reply_to_msg_id: int | None
    created_at: int


def _row_to_stored_message(row: object) -> StoredMessage:
    r = row
    return StoredMessage(
        message_id=int(r["message_id"]),
        channel_id=int(r["channel_id"]),
        guild_id=int(r["guild_id"]),
        author_id=int(r["author_id"]),
        author_name=str(r["author_name"]),
        author_display_name=str(r["author_display_name"]),
        content=str(r["content"]),
        has_attachment=bool(r["has_attachment"]),
        reply_to_msg_id=int(r["reply_to_msg_id"]) if r["reply_to_msg_id"] is not None else None,
        created_at=int(r["created_at"]),
    )


class MessageRepository:
    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def insert_message(self, msg: StoredMessage) -> None:
        sql = """
            INSERT OR REPLACE INTO messages (
                message_id, channel_id, guild_id, author_id,
                author_name, author_display_name, content,
                has_attachment, reply_to_msg_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        async with self._db.write_lock:
            await self._db.connection.execute(
                sql,
                (
                    msg.message_id,
                    msg.channel_id,
                    msg.guild_id,
                    msg.author_id,
                    msg.author_name,
                    msg.author_display_name,
                    msg.content,
                    int(msg.has_attachment),
                    msg.reply_to_msg_id,
                    msg.created_at,
                ),
            )
            await self._db.connection.commit()

    async def insert_messages_batch(self, messages: Sequence[StoredMessage]) -> int:
        if not messages:
            return 0
        sql = """
            INSERT OR IGNORE INTO messages (
                message_id, channel_id, guild_id, author_id,
                author_name, author_display_name, content,
                has_attachment, reply_to_msg_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = [
            (
                m.message_id,
                m.channel_id,
                m.guild_id,
                m.author_id,
                m.author_name,
                m.author_display_name,
                m.content,
                int(m.has_attachment),
                m.reply_to_msg_id,
                m.created_at,
            )
            for m in messages
        ]
        async with self._db.write_lock:
            await self._db.connection.executemany(sql, params)
            await self._db.connection.commit()
        return len(messages)

    async def delete_message(self, message_id: int) -> None:
        async with self._db.write_lock:
            await self._db.connection.execute(
                "DELETE FROM messages WHERE message_id = ?;",
                (message_id,),
            )
            await self._db.connection.commit()

    async def delete_messages_bulk(self, message_ids: Sequence[int]) -> None:
        if not message_ids:
            return
        placeholders = ",".join("?" for _ in message_ids)
        sql = f"DELETE FROM messages WHERE message_id IN ({placeholders});"
        async with self._db.write_lock:
            await self._db.connection.execute(sql, tuple(message_ids))
            await self._db.connection.commit()

    async def get_recent_channel_messages(
        self,
        channel_id: int,
        limit: int = 100,
        before_timestamp: int | None = None,
    ) -> list[StoredMessage]:
        clamped_limit = max(1, min(limit, 250))
        if before_timestamp is not None:
            sql = """
                SELECT * FROM messages
                WHERE channel_id = ? AND created_at < ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?;
            """
            params: tuple[object, ...] = (channel_id, before_timestamp, clamped_limit)
        else:
            sql = """
                SELECT * FROM messages
                WHERE channel_id = ?
                ORDER BY created_at DESC, message_id DESC
                LIMIT ?;
            """
            params = (channel_id, clamped_limit)

        async with self._db.connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

        messages = [_row_to_stored_message(r) for r in rows]
        messages.reverse()
        return messages

    async def resolve_user_id_by_name(
        self,
        guild_id: int,
        username_query: str,
    ) -> int | None:
        cleaned = username_query.strip().lstrip("@")
        if not cleaned:
            return None
        sql = """
            SELECT author_id
            FROM messages
            WHERE guild_id = ?
              AND (author_display_name LIKE ? OR author_name LIKE ?)
            ORDER BY created_at DESC
            LIMIT 1;
        """
        pattern = f"%{cleaned}%"
        async with self._db.connection.execute(sql, (guild_id, pattern, pattern)) as cursor:
            row = await cursor.fetchone()
            return int(row["author_id"]) if row else None

    async def get_user_messages(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int | None = None,
        limit: int = 1000,
    ) -> list[StoredMessage]:
        clamped = max(1, min(limit, 1000))
        if channel_id is not None:
            sql = """
                SELECT * FROM messages
                WHERE guild_id = ? AND author_id = ? AND channel_id = ?
                ORDER BY created_at DESC
                LIMIT ?;
            """
            params: tuple[object, ...] = (guild_id, user_id, channel_id, clamped)
        else:
            sql = """
                SELECT * FROM messages
                WHERE guild_id = ? AND author_id = ?
                ORDER BY created_at DESC
                LIMIT ?;
            """
            params = (guild_id, user_id, clamped)

        async with self._db.connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_stored_message(r) for r in rows]

    async def search_messages(
        self,
        guild_id: int,
        query: str,
        channel_id: int | None = None,
        author_id: int | None = None,
        limit: int = 25,
    ) -> list[StoredMessage]:
        cleaned_query = query.strip()
        if not cleaned_query:
            return []

        clamped_limit = max(1, min(limit, 50))
        filters = ["m.guild_id = ?"]
        params: list[object] = [guild_id]

        if channel_id is not None:
            filters.append("m.channel_id = ?")
            params.append(channel_id)
        if author_id is not None:
            filters.append("m.author_id = ?")
            params.append(author_id)

        where_clause = " AND ".join(filters)

        if len(cleaned_query) >= 3:
            escaped_fts = '"' + cleaned_query.replace('"', '""') + '"'
            sql = f"""
                SELECT m.*
                FROM messages_fts f
                JOIN messages m ON m.message_id = f.rowid
                WHERE messages_fts MATCH ? AND {where_clause}
                ORDER BY bm25(messages_fts), m.created_at DESC
                LIMIT ?;
            """
            fts_params: tuple[object, ...] = (escaped_fts, *params, clamped_limit)
            async with self._db.connection.execute(sql, fts_params) as cursor:
                rows = await cursor.fetchall()
            if rows:
                return [_row_to_stored_message(r) for r in rows]

        thirty_days_ago = int(time.time()) - (30 * 86400)
        like_pattern = f"%{cleaned_query}%"
        fallback_sql = f"""
            SELECT m.*
            FROM messages m
            WHERE {where_clause}
              AND m.created_at >= ?
              AND (m.content LIKE ? OR m.author_display_name LIKE ?)
            ORDER BY m.created_at DESC
            LIMIT ?;
        """
        like_params: tuple[object, ...] = (
            *params,
            thirty_days_ago,
            like_pattern,
            like_pattern,
            clamped_limit,
        )
        async with self._db.connection.execute(fallback_sql, like_params) as cursor:
            rows = await cursor.fetchall()

        return [_row_to_stored_message(r) for r in rows]