# llm_tools.py
from __future__ import annotations

from typing import Any, Final

from analytics_service import AnalyticsService

TOOL_DECLARATIONS: Final[list[dict[str, Any]]] = [
    {
        "name": "get_recent_channel_messages",
        "description": "Fetch the most recent chronological messages in a Discord channel to summarize discussions, catch up on context, or answer what happened recently.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "channel_id": {
                    "type": "STRING",
                    "description": "Discord channel ID as a string. Defaults to the current channel ID provided in system context.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Number of recent messages to retrieve. Clamped between 1 and 250. Default is 100.",
                },
                "before_timestamp": {
                    "type": "INTEGER",
                    "description": "Optional Unix timestamp (seconds) to fetch messages prior to a specific time.",
                },
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "get_user_message_stats_and_samples",
        "description": "Analyze a specific Discord user's chat history, activity volume, top catchphrases/frequent n-grams, and representative message samples.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "user_id": {
                    "type": "STRING",
                    "description": "Target Discord user ID extracted from mention (<@123456>) or context.",
                },
                "username_query": {
                    "type": "STRING",
                    "description": "Fallback display name or username string if user_id is not explicitly known.",
                },
                "channel_id": {
                    "type": "STRING",
                    "description": "Optional channel ID to restrict analysis to a single channel. Omit for server-wide stats.",
                },
                "sample_limit": {
                    "type": "INTEGER",
                    "description": "Number of recent/representative messages to include. Clamped between 10 and 80. Default is 40.",
                },
            },
        },
    },
    {
        "name": "search_server_messages",
        "description": "Full-text search across server chat history for specific keywords, topics, quotes, or events.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Search keywords or phrase (supports English, Traditional Chinese, and Simplified Chinese).",
                },
                "channel_id": {
                    "type": "STRING",
                    "description": "Optional channel ID filter. Omit to search across the entire server.",
                },
                "author_id": {
                    "type": "STRING",
                    "description": "Optional user ID filter to search only messages sent by a specific person.",
                },
                "limit": {
                    "type": "INTEGER",
                    "description": "Maximum matching messages to return. Clamped between 1 and 50. Default is 25.",
                },
            },
            "required": ["query"],
        },
    },
]


def _safe_int(val: Any, default: int | None = None) -> int | None:
    if val is None:
        return default
    cleaned = str(val).strip().lstrip("<@!#").rstrip(">")
    return int(cleaned) if cleaned.isdigit() else default


async def execute_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    default_guild_id: int,
    default_channel_id: int,
    analytics_service: AnalyticsService,
) -> str:
    if tool_name == "get_recent_channel_messages":
        cid = _safe_int(arguments.get("channel_id"), default_channel_id) or default_channel_id
        limit = _safe_int(arguments.get("limit"), 100) or 100
        before_ts = _safe_int(arguments.get("before_timestamp"), None)
        return await analytics_service.get_recent_channel_messages_tool(
            channel_id=cid,
            limit=limit,
            before_timestamp=before_ts,
        )

    if tool_name == "get_user_message_stats_and_samples":
        uid = _safe_int(arguments.get("user_id"), None)
        uname = str(arguments.get("username_query", "")).strip() or None
        cid = _safe_int(arguments.get("channel_id"), None)
        sample_limit = _safe_int(arguments.get("sample_limit"), 40) or 40
        return await analytics_service.get_user_message_stats_and_samples_tool(
            guild_id=default_guild_id,
            user_id=uid,
            username_query=uname,
            channel_id=cid,
            sample_limit=sample_limit,
        )

    if tool_name == "search_server_messages":
        query = str(arguments.get("query", "")).strip()
        cid = _safe_int(arguments.get("channel_id"), None)
        aid = _safe_int(arguments.get("author_id"), None)
        limit = _safe_int(arguments.get("limit"), 25) or 25
        return await analytics_service.search_server_messages_tool(
            guild_id=default_guild_id,
            query=query,
            channel_id=cid,
            author_id=aid,
            limit=limit,
        )

    return f"未知的工具名稱：{tool_name}"