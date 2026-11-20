# backfill_service.py
from __future__ import annotations

import asyncio
import logging

import discord

from config import Settings
from message_repo import MessageRepository, StoredMessage
from state_repo import StateRepository

logger = logging.getLogger(__name__)


class BackfillService:
    def __init__(
        self,
        settings: Settings,
        state_repo: StateRepository,
        message_repo: MessageRepository,
    ) -> None:
        self._settings = settings
        self._state_repo = state_repo
        self._message_repo = message_repo
        self._running = False

    @staticmethod
    def discord_message_to_record(message: discord.Message) -> StoredMessage | None:
        if message.guild is None or message.author.bot:
            return None
        clean_text = message.clean_content or message.content or ""
        if not clean_text.strip() and not message.attachments:
            return None

        reply_id = (
            message.reference.message_id
            if message.reference and message.reference.message_id
            else None
        )
        return StoredMessage(
            message_id=message.id,
            channel_id=message.channel.id,
            guild_id=message.guild.id,
            author_id=message.author.id,
            author_name=message.author.name,
            author_display_name=message.author.display_name,
            content=clean_text,
            has_attachment=bool(message.attachments),
            reply_to_msg_id=reply_id,
            created_at=int(message.created_at.timestamp()),
        )

    async def backfill_guild_channels(self, guilds: list[discord.Guild]) -> None:
        if self._running or self._settings.backfill_initial_limit <= 0:
            return
        self._running = True
        try:
            for guild in guilds:
                for channel in guild.text_channels:
                    perms = channel.permissions_for(guild.me)
                    if not (perms.view_channel and perms.read_message_history):
                        continue
                    await self._backfill_single_channel(channel)
                    await asyncio.sleep(0.25)
        finally:
            self._running = False

    async def _backfill_single_channel(self, channel: discord.TextChannel) -> None:
        last_id = await self._state_repo.get_last_indexed_msg_id(channel.id)
        batch: list[StoredMessage] = []
        highest_id = last_id
        total_indexed = 0

        try:
            if last_id > 0:
                history_iter = channel.history(
                    after=discord.Object(id=last_id),
                    limit=self._settings.backfill_initial_limit,
                    oldest_first=True,
                )
            else:
                history_iter = channel.history(
                    limit=self._settings.backfill_initial_limit,
                    oldest_first=False,
                )

            async for msg in history_iter:
                if msg.id > highest_id:
                    highest_id = msg.id
                record = self.discord_message_to_record(msg)
                if record is not None:
                    batch.append(record)
                if len(batch) >= 100:
                    total_indexed += await self._message_repo.insert_messages_batch(batch)
                    batch.clear()

            if batch:
                total_indexed += await self._message_repo.insert_messages_batch(batch)
                batch.clear()

            if highest_id > last_id:
                await self._state_repo.update_last_indexed_msg_id(
                    channel_id=channel.id,
                    guild_id=channel.guild.id,
                    last_indexed_msg_id=highest_id,
                )
            if total_indexed > 0:
                logger.info(
                    "頻道 #%s (%d) 增量索引完成：新增 %d 則訊息 (水線=%d)",
                    channel.name,
                    channel.id,
                    total_indexed,
                    highest_id,
                )
        except (discord.Forbidden, discord.HTTPException) as exc:
            logger.warning("略過頻道 #%s 回補 (%s)", channel.name, exc)