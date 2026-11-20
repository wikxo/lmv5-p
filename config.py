# config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

load_dotenv()

VALID_MODES: Final[tuple[str, ...]] = (
    "helpful",
    "roast",
    "roastgoose",
    "spike",
    "evanwang",
    "muted",
)

MODE_DESCRIPTIONS: Final[dict[str, str]] = {
    "helpful": "AI 助手模式（支援歷史訊息檢索、圖片分析與工具呼叫）",
    "roast": "毒舌吐槽模式（根據歷史發言紀錄與口頭禪進行客製化吐槽）",
    "roastgoose": "固定回覆模式：燒鵝（RoastGoose）專屬語錄",
    "spike": "固定回覆模式：Spike 專屬語錄",
    "evanwang": "固定回覆模式：EvanWang 專屬語錄",
    "muted": "靜音模式（僅背景記錄訊息，不主動回覆一般對話）",
}

FIXED_MODE_RESPONSES: Final[dict[str, tuple[str, ...]]] = {
    "roastgoose": (
        "燒鵝今天也在優雅地擺爛 🦢",
        "別吵燒鵝，燒鵝正在思考宇宙與宵夜的終極奧義。",
        "這題太難了，建議直接問燒鵝本人。",
    ),
    "spike": (
        "Spike 表示：這波操作我給 87 分，不能再高了。",
        "Spike 正在趕工中，請稍後再撥。",
        "這看起來像是 Spike 會踩到的坑。",
    ),
    "evanwang": (
        "EvanWang：這需求看起來很簡單，怎麼還沒寫完？",
        "EvanWang 覺得這段架構還需要再重構三次。",
        "先別管這個了，EvanWang 問你今天 commit 了沒？",
    ),
}


def _parse_id_set(raw_value: str | None) -> frozenset[int]:
    if not raw_value:
        return frozenset()
    parsed: set[int] = set()
    for token in raw_value.split(","):
        cleaned = token.strip()
        if cleaned.isdigit():
            parsed.add(int(cleaned))
    return frozenset(parsed)


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    application_id: int | None
    gemini_api_key: str
    groq_api_key: str
    gemini_model: str
    groq_model: str
    whitelist_ids: frozenset[int]
    vip_user_ids: frozenset[int]
    vip_role_ids: frozenset[int]
    db_path: Path
    default_channel_mode: str
    backfill_initial_limit: int
    sync_commands_on_startup: bool
    log_level: str

    def validate(self) -> None:
        if not self.discord_token or self.discord_token == "your_discord_bot_token_here":
            raise ValueError("DISCORD_TOKEN 未設定或仍為預設範例值，請檢查 .env 檔案。")
        if self.default_channel_mode not in VALID_MODES:
            raise ValueError(
                f"DEFAULT_CHANNEL_MODE ({self.default_channel_mode!r}) 無效，"
                f"必須為下列之一：{', '.join(VALID_MODES)}"
            )
        if self.backfill_initial_limit < 0:
            raise ValueError("BACKFILL_INITIAL_LIMIT 必須為非負整數。")


def load_settings() -> Settings:
    raw_app_id = os.getenv("APPLICATION_ID", "").strip()
    app_id = int(raw_app_id) if raw_app_id.isdigit() else None

    default_mode = os.getenv("DEFAULT_CHANNEL_MODE", "helpful").strip().lower()
    backfill_limit_str = os.getenv("BACKFILL_INITIAL_LIMIT", "500").strip()
    backfill_limit = int(backfill_limit_str) if backfill_limit_str.isdigit() else 500

    settings = Settings(
        discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
        application_id=app_id,
        gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
        groq_model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip(),
        whitelist_ids=_parse_id_set(os.getenv("WHITELIST_IDS")),
        vip_user_ids=_parse_id_set(os.getenv("VIP_USER_IDS")),
        vip_role_ids=_parse_id_set(os.getenv("VIP_ROLE_IDS")),
        db_path=Path(os.getenv("DB_PATH", "data/chat_memory.db").strip()),
        default_channel_mode=default_mode,
        backfill_initial_limit=backfill_limit,
        sync_commands_on_startup=_parse_bool(os.getenv("SYNC_COMMANDS_ON_STARTUP"), default=False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )
    settings.validate()
    return settings