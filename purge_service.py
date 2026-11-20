# purge_service.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Final

import discord

from config import Settings
from message_repo import MessageRepository
from permissions import is_vip, is_whitelisted, translate_forbidden_error

BULK_DELETE_MAX_AGE: Final[timedelta] = timedelta(days=14) - timedelta(minutes=2)
NL_PURGE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:刪除|刪掉|清除|清空|purge|delete|clear).*?(?:(\d{1,3})\s*[則條個篇]?|訊息|消息|留言)",
    re.IGNORECASE,
)
KEYWORD_EXTRACT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:包含|含有|關鍵字|keyword)\s*[「『\"']?([^」』\"'\s]+)[」』\"']?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PurgeIntent:
    count: int
    target_user: discord.Member | discord.User | None
    keyword: str | None


class PurgeService:
    def __init__(self, settings: Settings, message_repo: MessageRepository) -> None:
        self._settings = settings
        self._message_repo = message_repo

    def parse_nl_purge_intent(
        self,
        message: discord.Message,
        bot_id: int,
    ) -> PurgeIntent | None:
        cleaned = re.sub(rf"<@!?{bot_id}>", "", message.content).strip()
        match = NL_PURGE_PATTERN.search(cleaned)
        if not match:
            return None

        raw_count = match.group(1)
        count = int(raw_count) if raw_count and raw_count.isdigit() else 20
        count = max(1, min(count, 200))

        target_mentions = [u for u in message.mentions if u.id != bot_id]
        target_user = target_mentions[0] if target_mentions else None

        kw_match = KEYWORD_EXTRACT_PATTERN.search(cleaned)
        keyword = kw_match.group(1).strip() if kw_match else None

        return PurgeIntent(count=count, target_user=target_user, keyword=keyword)

    async def execute_purge(
        self,
        channel: discord.TextChannel,
        invoker: discord.Member | discord.User,
        count: int,
        target_user: discord.Member | discord.User | None = None,
        keyword: str | None = None,
        exclude_message_id: int | None = None,
    ) -> str:
        if not is_vip(invoker, self._settings):
            return "⛔ **權限不足**：僅限 VIP 或伺服器管理員可使用智慧清除訊息功能。"

        if (
            target_user is not None
            and is_whitelisted(target_user.id, self._settings)
            and invoker.id != target_user.id
            and invoker.id != channel.guild.owner_id
        ):
            return f"🛡️ **白名單保護已觸發**：<@{target_user.id}> 位於洛神保護名單內，禁止針對其訊息進行清除。"

        clamped_count = max(1, min(count, 200))
        scan_limit = min(clamped_count * 5, 500) if (target_user or keyword) else clamped_count + 5

        matched_messages: list[discord.Message] = []
        keyword_lower = keyword.lower().strip() if keyword else None

        try:
            async for msg in channel.history(limit=scan_limit):
                if exclude_message_id and msg.id == exclude_message_id:
                    continue
                if msg.pinned:
                    continue
                if (
                    is_whitelisted(msg.author.id, self._settings)
                    and invoker.id != msg.author.id
                    and invoker.id != channel.guild.owner_id
                ):
                    continue
                if target_user is not None and msg.author.id != target_user.id:
                    continue
                if keyword_lower and keyword_lower not in (msg.content or "").lower():
                    continue

                matched_messages.append(msg)
                if len(matched_messages) >= clamped_count:
                    break
        except discord.Forbidden as exc:
            return translate_forbidden_error(
                exc,
                guild=channel.guild,
                channel=channel,
                action_description="讀取頻道歷史訊息以執行清除",
            )

        if not matched_messages:
            return "ℹ️ 找不到符合篩選條件的可刪除訊息。"

        cutoff_dt = datetime.now(timezone.utc) - BULK_DELETE_MAX_AGE
        young_messages = [m for m in matched_messages if m.created_at > cutoff_dt]
        old_messages = [m for m in matched_messages if m.created_at <= cutoff_dt]

        deleted_ids: list[int] = []

        try:
            for idx in range(0, len(young_messages), 100):
                batch = young_messages[idx : idx + 100]
                if len(batch) == 1:
                    await batch[0].delete()
                else:
                    await channel.delete_messages(batch)
                deleted_ids.extend(m.id for m in batch)
                if idx + 100 < len(young_messages):
                    await asyncio.sleep(0.5)

            for old_msg in old_messages:
                await old_msg.delete()
                deleted_ids.append(old_msg.id)
                await asyncio.sleep(0.85)

        except discord.Forbidden as exc:
            if deleted_ids:
                await self._message_repo.delete_messages_bulk(deleted_ids)
            return translate_forbidden_error(
                exc,
                guild=channel.guild,
                channel=channel,
                action_description="刪除頻道訊息",
            )
        except discord.HTTPException as exc:
            if deleted_ids:
                await self._message_repo.delete_messages_bulk(deleted_ids)
            return f"⚠️ 刪除過程中發生 Discord API 例外（已成功刪除 `{len(deleted_ids)}` 則）：`{exc}`"

        await self._message_repo.delete_messages_bulk(deleted_ids)

        filter_desc: list[str] = []
        if target_user:
            filter_desc.append(f"目標成員：<@{target_user.id}>")
        if keyword:
            filter_desc.append(f"關鍵字：`{keyword}`")
        extra_info = f"（{'、'.join(filter_desc)}）" if filter_desc else ""

        return (
            f"🧹 **智慧清除完成**{extra_info}：共刪除 `{len(deleted_ids)}` 則訊息"
            f"（14天內批次：`{len(young_messages)}` 則｜14天以上逐筆：`{len(old_messages)}` 則），"
            f"並已同步更新 SQLite + FTS5 索引。"
        )