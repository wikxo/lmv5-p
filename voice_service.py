# voice_service.py
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
from typing import Final, Literal

import discord

from config import Settings
from permissions import is_vip, is_whitelisted, translate_forbidden_error

VOICE_DISCONNECT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:踢出語音|斷開語音|中斷語音|踢離語音|從語音踢|disconnect|kick\s+from\s+voice)",
    re.IGNORECASE,
)
VOICE_MOVE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:移到|移動到|搬到|拉到|轉移到|送去|move\s+to)\s*[「『\"']?([^」』\"'\n]+?)[」』\"']?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class VoiceCommandIntent:
    action: Literal["move", "disconnect"]
    target_member: discord.Member
    destination_query: str | None = None


class VoiceService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse_nl_voice_intent(
        self,
        message: discord.Message,
        bot_id: int,
    ) -> VoiceCommandIntent | None:
        if message.guild is None:
            return None

        cleaned = re.sub(rf"<@!?{bot_id}>", "", message.content).strip()
        target_members = [
            m for m in message.mentions if isinstance(m, discord.Member) and m.id != bot_id
        ]
        if not target_members:
            return None

        target = target_members[0]
        cleaned_without_mentions = re.sub(r"<@!?\d+>", "", cleaned).strip()

        if VOICE_DISCONNECT_PATTERN.search(cleaned_without_mentions):
            return VoiceCommandIntent(action="disconnect", target_member=target)

        move_match = VOICE_MOVE_PATTERN.search(cleaned_without_mentions)
        if move_match:
            dest_query = move_match.group(1).strip()
            return VoiceCommandIntent(
                action="move",
                target_member=target,
                destination_query=dest_query,
            )

        return None

    @staticmethod
    def resolve_voice_channel(
        guild: discord.Guild,
        query: str,
    ) -> discord.VoiceChannel | None:
        cleaned = query.strip().lstrip("#")
        if not cleaned or not guild.voice_channels:
            return None

        for vc in guild.voice_channels:
            if str(vc.id) == cleaned or vc.name.lower() == cleaned.lower():
                return vc

        for vc in guild.voice_channels:
            if cleaned.lower() in vc.name.lower() or vc.name.lower() in cleaned.lower():
                return vc

        best_match: discord.VoiceChannel | None = None
        best_score = 0.0
        for vc in guild.voice_channels:
            score = SequenceMatcher(None, cleaned.lower(), vc.name.lower()).ratio()
            if score > best_score:
                best_score = score
                best_match = vc

        return best_match if best_score >= 0.45 else None

    async def execute_voice_intent(
        self,
        guild: discord.Guild,
        source_channel: discord.abc.GuildChannel,
        invoker: discord.Member | discord.User,
        intent: VoiceCommandIntent,
    ) -> str:
        if not is_vip(invoker, self._settings):
            return "⛔ **權限不足**：僅限 VIP 或具備管理權限的成員可使用語音頻道調動功能。"

        target = intent.target_member
        if is_whitelisted(target.id, self._settings) and invoker.id != target.id:
            return f"🛡️ **白名單保護已觸發**：<@{target.id}> 位於洛神保護名單內，禁止移動或中斷其語音連線。"

        if target.voice is None or target.voice.channel is None:
            return f"ℹ️ 目標成員 **{target.display_name}** 目前不在任何語音頻道中。"

        if intent.action == "disconnect":
            try:
                prev_channel_name = target.voice.channel.name
                await target.move_to(None, reason=f"由 {invoker.display_name} 透過自然語言指令中斷語音")
                return f"🔇 已將 **{target.display_name}** 從語音頻道「**{prev_channel_name}**」中斷連線。"
            except discord.Forbidden as exc:
                return translate_forbidden_error(
                    exc,
                    guild=guild,
                    channel=source_channel,
                    target_member=target,
                    action_description=f"將 {target.display_name} 踢出語音頻道",
                )

        dest_channel = self.resolve_voice_channel(guild, intent.destination_query or "")
        if dest_channel is None:
            available = "、".join(f"`{vc.name}`" for vc in guild.voice_channels[:10]) or "無語音頻道"
            return (
                f"❓ 找不到名稱相符的語音頻道：「`{intent.destination_query}`」。\n"
                f"目前可用的語音頻道：{available}"
            )

        me = guild.me
        dest_perms = dest_channel.permissions_for(me)
        if not (dest_perms.view_channel and dest_perms.connect and me.guild_permissions.move_members):
            return (
                f"⚠️ **權限不足**：機器人在目標語音頻道「**{dest_channel.name}**」缺少"
                f" `move_members`、`connect` 或 `view_channel` 權限，無法執行移動。"
            )

        try:
            await target.move_to(
                dest_channel,
                reason=f"由 {invoker.display_name} 透過自然語言指令移動至 {dest_channel.name}",
            )
            return f"🔊 已將 **{target.display_name}** 移動至語音頻道「**{dest_channel.name}**」。"
        except discord.Forbidden as exc:
            return translate_forbidden_error(
                exc,
                guild=guild,
                channel=dest_channel,
                target_member=target,
                action_description=f"將 {target.display_name} 移動至 {dest_channel.name}",
            )