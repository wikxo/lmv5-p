# voice_cog.py
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from voice_service import VoiceCommandIntent, VoiceService


class VoiceCog(commands.Cog, name="VoiceCog"):
    def __init__(self, bot: commands.Bot, voice_service: VoiceService) -> None:
        self.bot = bot
        self.voice_service = voice_service

    @app_commands.command(
        name="voicemove",
        description="將指定成員移動至目標語音頻道或中斷其語音連線（支援洛神白名單保護）",
    )
    @app_commands.describe(
        member="要調動的目標成員",
        channel="目標語音頻道（若留空則中斷該成員的語音連線）",
    )
    async def voicemove_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        channel: discord.VoiceChannel | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.abc.GuildChannel
        ):
            await interaction.response.send_message(
                "❌ 此指令僅能在伺服器內使用。",
                ephemeral=True,
            )
            return

        intent = (
            VoiceCommandIntent(
                action="move",
                target_member=member,
                destination_query=str(channel.id),
            )
            if channel is not None
            else VoiceCommandIntent(action="disconnect", target_member=member)
        )

        result = await self.voice_service.execute_voice_intent(
            guild=interaction.guild,
            source_channel=interaction.channel,
            invoker=interaction.user,
            intent=intent,
        )
        await interaction.response.send_message(result)