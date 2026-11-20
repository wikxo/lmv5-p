# purge_cog.py
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from purge_service import PurgeService


class PurgeCog(commands.Cog, name="PurgeCog"):
    def __init__(self, bot: commands.Bot, purge_service: PurgeService) -> None:
        self.bot = bot
        self.purge_service = purge_service

    @app_commands.command(
        name="purge",
        description="VIP 專屬智慧清除訊息（支援人數、關鍵字篩選與跨 14 天自動分流刪除）",
    )
    @app_commands.describe(
        count="要清除的訊息數量（1~200，預設 20）",
        user="選填：僅清除特定成員的訊息",
        keyword="選填：僅清除包含指定關鍵字的訊息",
    )
    async def purge_cmd(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 200] = 20,
        user: discord.Member | None = None,
        keyword: str | None = None,
    ) -> None:
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.TextChannel
        ):
            await interaction.response.send_message(
                "❌ 此指令僅能在伺服器文字頻道內使用。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        result_msg = await self.purge_service.execute_purge(
            channel=interaction.channel,
            invoker=interaction.user,
            count=count,
            target_user=user,
            keyword=keyword,
        )
        await interaction.followup.send(result_msg, ephemeral=True)