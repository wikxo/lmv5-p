# llm_schemas.py
from __future__ import annotations

from dataclasses import dataclass, field

from media import DownloadedMedia


@dataclass(frozen=True, slots=True)
class LLMRequestContext:
    guild_id: int
    channel_id: int
    channel_mode: str
    author_id: int
    author_display_name: str
    prompt_text: str
    target_user_id: int | None = None
    target_display_name: str | None = None
    images: list[DownloadedMedia] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class LLMResponsePayload:
    text: str
    provider: str
    model: str
    used_fallback: bool = False
    tool_calls_count: int = 0