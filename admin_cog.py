# admin_cog.py
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from analytics_service import AnalyticsService
from config import MODE_DESCRIPTIONS, VALID_MODES, Settings
from db_connection import DatabaseManager
from permissions import build_permission_diagnostics, is_vip
from state_repo import StateRepository


class AdminCog(commands.Cog, name="AdminCog"):
    """提供 /history (Top 5 最常說的句子) 與無權限限制的 /setmode 指令。"""

    def __init__(
        self,
        bot: commands.Bot,
        settings: Settings,
        db: DatabaseManager,
        state_repo: StateRepository,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.db = db
        self.state_repo = state_repo

    @app_commands.command(
        name="history",
        description="查看指定成員在伺服器最常說的 Top 5 句子",
    )
    @app_commands.describe(member="要查詢的伺服器成員（不填則查詢自己）")
    async def history_cmd(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("❌ 此指令僅能在伺服器內使用。", ephemeral=True)
            return

        await interaction.response.defer()
        target = member or interaction.user

        analytics: AnalyticsService | None = getattr(self.bot, "analytics_service", None)
        backfill = getattr(self.bot, "backfill_service", None)
        message_repo = getattr(self.bot, "message_repo", None)

        if analytics is None or message_repo is None:
            await interaction.followup.send("⚠️ 統計服務尚未就緒。")
            return

        report = await analytics.get_user_top_history_report(
            guild_id=interaction.guild.id,
            user_id=target.id,
            fallback_display_name=target.display_name,
        )

        # 若資料庫內該成員訊息不足 10 則，現場快速掃描當前頻道最近 300 則訊息入庫
        if (
            report.total_messages < 10
            and isinstance(interaction.channel, discord.TextChannel)
            and backfill is not None
        ):
            try:
                batch = []
                async for msg in interaction.channel.history(limit=300):
                    rec = backfill.discord_message_to_record(msg)
                    if rec is not None:
                        batch.append(rec)
                if batch:
                    await message_repo.insert_messages_batch(batch)
                    report = await analytics.get_user_top_history_report(
                        guild_id=interaction.guild.id,
                        user_id=target.id,
                        fallback_display_name=target.display_name,
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

        if report.total_messages == 0 or not report.top_5_phrases:
            await interaction.followup.send(
                f"📭 **{target.display_name}** 目前在資料庫中還沒有足夠的發言紀錄可供統計。"
            )
            return

        medals = ("🥇", "🥈", "🥉", "4️⃣", "5️⃣")
        top_lines = [
            f"{medals[idx]} **「{sentence}」** — 說過 `{count}` 次"
            for idx, (sentence, count) in enumerate(report.top_5_phrases[:5])
        ]

        embed = discord.Embed(
            title=f"📊 {report.display_name} 最常說的句子 Top 5",
            color=discord.Color.gold(),
        )
        if target.display_avatar:
            embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(
            name="💬 最常說的句子 Top 5",
            value="\n".join(top_lines),
            inline=False,
        )
        embed.add_field(
            name="📨 累積記錄訊息數",
            value=f"`{report.total_messages}` 則",
            inline=True,
        )
        embed.add_field(
            name="📏 平均每則字數",
            value=f"`{report.avg_length:.1f}` 字",
            inline=True,
        )

        await interaction.followup.send(embed=embed)

    async def _build_setmode_embed(
        self,
        channel_id: int,
        guild_id: int,
        new_mode: str,
        operator_name: str,
    ) -> discord.Embed:
        previous_mode = await self.state_repo.get_channel_mode(
            channel_id=channel_id,
            guild_id=guild_id,
        )
        updated_record = await self.state_repo.set_channel_mode(
            channel_id=channel_id,
            guild_id=guild_id,
            mode=new_mode,
        )
        embed = discord.Embed(
            title="⚙️ 頻道模式已更新",
            color=discord.Color.teal(),
        )
        embed.add_field(name="目標頻道", value=f"<#{channel_id}>", inline=True)
        embed.add_field(name="先前模式", value=f"`{previous_mode}`", inline=True)
        embed.add_field(name="目前模式", value=f"`{updated_record.mode}`", inline=True)
        embed.add_field(
            name="模式說明",
            value=MODE_DESCRIPTIONS.get(updated_record.mode, "無說明"),
            inline=False,
        )
        embed.set_footer(text=f"設定已寫入 SQLite • 操作者：{operator_name}")
        return embed

    @app_commands.command(
        name="setmode",
        description="切換當前頻道的機器人運作模式（所有成員皆可使用）",
    )
    @app_commands.describe(mode="選擇此頻道要啟用的運作模式")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="helpful — 萬能 AI 助手模式", value="helpful"),
            app_commands.Choice(name="roast — 毒舌吐槽模式", value="roast"),
        ]
    )
    async def setmode_cmd(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message("❌ 此指令僅能在伺服器頻道內使用。", ephemeral=True)
            return

        embed = await self._build_setmode_embed(
            channel_id=interaction.channel.id,
            guild_id=interaction.guild.id,
            new_mode=mode.value,
            operator_name=interaction.user.display_name,
        )
        await interaction.response.send_message(embed=embed)

    @commands.command(name="setmode")
    async def setmode_prefix_cmd(self, ctx: commands.Context, mode: str = "") -> None:
        if ctx.guild is None:
            return

        normalized = mode.strip().lower()
        if normalized not in VALID_MODES:
            await ctx.reply("❓ 請指定有效模式：`!setmode helpful` 或 `!setmode roast`")
            return

        embed = await self._build_setmode_embed(
            channel_id=ctx.channel.id,
            guild_id=ctx.guild.id,
            new_mode=normalized,
            operator_name=ctx.author.display_name,
        )
        await ctx.reply(embed=embed)

    @commands.command(name="checkperms")
    async def checkperms_prefix_cmd(self, ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.channel, discord.abc.GuildChannel):
            return
        report = build_permission_diagnostics(ctx.guild, ctx.channel)
        perm_lines = [f"{'✅' if v else '❌'} `{k}`" for k, v in report.permissions.items()]
        await ctx.reply("🔍 **權限診斷**\n" + "\n".join(perm_lines))

    @commands.command(name="sync")
    async def sync_prefix_cmd(self, ctx: commands.Context) -> None:
        if not is_vip(ctx.author, self.settings) or ctx.guild is None:
            return

        saved_cmds = list(self.bot.tree.get_commands(guild=None))

        # 1. 清除當前伺服器舊指令並僅同步最新指令至 Guild Tree
        self.bot.tree.clear_commands(guild=ctx.guild)
        for cmd in saved_cmds:
            self.bot.tree.add_command(cmd, guild=ctx.guild, override=True)
        synced = await self.bot.tree.sync(guild=ctx.guild)

        # 2. 清空殘留的全域指令（消除舊版 v4 造成的重複雙胞胎指令），再還原記憶體設定
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync(guild=None)
        for cmd in saved_cmds:
            self.bot.tree.add_command(cmd, guild=None, override=True)

        cmd_names = "、".join(f"`/{c.name}`" for c in synced)
        await ctx.reply(
            f"✅ 已清除舊版與重複指令，並將 {len(synced)} 個指令（{cmd_names}）同步至本伺服器！"
        )