# analytics_service.py
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final, Sequence

from message_repo import MessageRepository, StoredMessage
from text_processing import (
    estimate_tokens,
    extract_top_catchphrases,
    truncate_message_content,
)

MAX_TOOL_CHARS: Final[int] = 18_000


@dataclass(frozen=True, slots=True)
class UserTopHistoryReport:
    user_id: int
    display_name: str
    total_messages: int
    avg_length: float
    top_5_phrases: list[tuple[str, int]]
    recent_quotes: list[str]


class AnalyticsService:
    def __init__(self, message_repo: MessageRepository) -> None:
        self._message_repo = message_repo

    @staticmethod
    def _format_wire_line(msg: StoredMessage) -> str:
        ts = datetime.fromtimestamp(msg.created_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        truncated = truncate_message_content(msg.content, max_chars=300)
        return f"[{ts}] {msg.author_display_name}: {truncated}"

    def _pack_messages_with_budget(
        self,
        messages: Sequence[StoredMessage],
        header: str,
    ) -> str:
        if not messages:
            return f"{header}\n(查無相符的歷史訊息)"

        formatted_lines = [self._format_wire_line(m) for m in messages]
        joined = f"{header}\n" + "\n".join(formatted_lines)
        if len(joined) <= MAX_TOOL_CHARS:
            return joined

        if len(messages) > 80:
            oldest_part = list(messages[:30])
            newest_part = list(messages[-50:])
            middle_candidates = list(messages[30:-50])
            middle_sorted = sorted(
                middle_candidates,
                key=lambda m: len(m.content),
                reverse=True,
            )[:25]
            middle_sorted.sort(key=lambda m: m.created_at)
            sampled = oldest_part + middle_sorted + newest_part
        else:
            sampled = list(messages)

        lines: list[str] = [f"{header} (共 {len(messages)} 則)"]
        current_chars = len(lines[0])

        for m in sampled:
            line = self._format_wire_line(m)
            if current_chars + len(line) + 1 > MAX_TOOL_CHARS:
                break
            lines.append(line)
            current_chars += len(line) + 1

        return "\n".join(lines)

    async def get_user_top_history_report(
        self,
        guild_id: int,
        user_id: int,
        fallback_display_name: str,
    ) -> UserTopHistoryReport:
        """供 /history 指令使用：計算指定成員在伺服器最常講的 Top 5 語錄與統計。"""
        history = await self._message_repo.get_user_messages(
            guild_id=guild_id,
            user_id=user_id,
            channel_id=None,
            limit=1000,
        )
        if not history:
            return UserTopHistoryReport(
                user_id=user_id,
                display_name=fallback_display_name,
                total_messages=0,
                avg_length=0.0,
                top_5_phrases=[],
                recent_quotes=[],
            )

        display_name = history[0].author_display_name or fallback_display_name
        total_msgs = len(history)
        avg_len = sum(len(m.content) for m in history) / max(1, total_msgs)

        top_5 = extract_top_catchphrases([m.content for m in history], top_k=5)
        recent_quotes = [
            truncate_message_content(m.content, max_chars=80)
            for m in history[:5]
            if m.content.strip()
        ]

        return UserTopHistoryReport(
            user_id=user_id,
            display_name=display_name,
            total_messages=total_msgs,
            avg_length=avg_len,
            top_5_phrases=top_5,
            recent_quotes=recent_quotes,
        )

    async def get_recent_channel_messages_tool(
        self,
        channel_id: int,
        limit: int = 100,
        before_timestamp: int | None = None,
    ) -> str:
        clamped_limit = max(1, min(limit, 250))
        messages = await self._message_repo.get_recent_channel_messages(
            channel_id=channel_id,
            limit=clamped_limit,
            before_timestamp=before_timestamp,
        )
        if not messages:
            return f"頻道 ID {channel_id} 目前沒有可讀取的歷史訊息。"

        start_ts = datetime.fromtimestamp(messages[0].created_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
        end_ts = datetime.fromtimestamp(messages[-1].created_at, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
        header = (
            f"=== 頻道最近訊息紀錄 (筆數: {len(messages)} | {start_ts} ~ {end_ts} UTC) ==="
        )
        return self._pack_messages_with_budget(messages, header)

    async def get_user_message_stats_and_samples_tool(
        self,
        guild_id: int,
        user_id: int | None = None,
        username_query: str | None = None,
        channel_id: int | None = None,
        sample_limit: int = 40,
    ) -> str:
        resolved_uid = user_id
        if resolved_uid is None and username_query:
            resolved_uid = await self._message_repo.resolve_user_id_by_name(
                guild_id=guild_id,
                username_query=username_query,
            )
        if resolved_uid is None:
            return f"找不到符合「{username_query or '未知'}」的使用者發言紀錄。"

        history = await self._message_repo.get_user_messages(
            guild_id=guild_id,
            user_id=resolved_uid,
            channel_id=channel_id,
            limit=1000,
        )
        if not history:
            return f"使用者 <@{resolved_uid}> 目前在資料庫中尚無發言紀錄。"

        display_name = history[0].author_display_name
        total_msgs = len(history)
        avg_len = sum(len(m.content) for m in history) / max(1, total_msgs)

        hour_counts: Counter[int] = Counter(
            datetime.fromtimestamp(m.created_at, tz=timezone.utc).hour for m in history
        )
        top_hours = ", ".join(f"{hr:02d}:00 UTC ({cnt}則)" for hr, cnt in hour_counts.most_common(3))

        catchphrases = extract_top_catchphrases([m.content for m in history], top_k=10)
        catchphrase_str = (
            "、".join(f"「{phrase}」({cnt}次)" for phrase, cnt in catchphrases)
            if catchphrases
            else "尚無重複超過 2 次的口頭禪"
        )

        clamped_samples = max(10, min(sample_limit, 80))
        recent_samples = history[:clamped_samples]
        recent_samples.reverse()

        summary_header = (
            f"=== 使用者發言統計與樣本 ===\n"
            f"使用者：{display_name} (ID: {resolved_uid})\n"
            f"已記錄總訊息數：{total_msgs} 則 | 平均字數：{avg_len:.1f} 字\n"
            f"最活躍時段：{top_hours}\n"
            f"最常講的話 Top 10：{catchphrase_str}\n"
            f"--- 近期實際發言樣本 ({len(recent_samples)} 則) ---"
        )
        return self._pack_messages_with_budget(recent_samples, summary_header)

    async def search_server_messages_tool(
        self,
        guild_id: int,
        query: str,
        channel_id: int | None = None,
        author_id: int | None = None,
        limit: int = 25,
    ) -> str:
        matches = await self._message_repo.search_messages(
            guild_id=guild_id,
            query=query,
            channel_id=channel_id,
            author_id=author_id,
            limit=limit,
        )
        header = f"=== 全文檢索結果 (關鍵字: {query!r} | 命中筆數: {len(matches)}) ==="
        return self._pack_messages_with_budget(matches, header)

    async def build_roast_dossier(
        self,
        guild_id: int,
        target_user_id: int,
        target_display_name: str,
        channel_id: int | None = None,
    ) -> str:
        stats_block = await self.get_user_message_stats_and_samples_tool(
            guild_id=guild_id,
            user_id=target_user_id,
            username_query=target_display_name,
            channel_id=None,
            sample_limit=30,
        )
        if estimate_tokens(stats_block) > 4000:
            stats_block = stats_block[:12000]
        return stats_block