# chat_cog.py
from __future__ import annotations

import asyncio
import logging
import re

import discord
from discord.ext import commands

from backfill_service import BackfillService
from chunker import chunk_markdown
from config import Settings
from llm_router import LLMRouter
from llm_schemas import LLMRequestContext
from media import download_message_images
from message_repo import MessageRepository, StoredMessage
from purge_service import PurgeService
from state_repo import StateRepository
from voice_service import VoiceService

logger = logging.getLogger(__name__)


class ChatCog(commands.Cog, name="ChatCog"):
    def __init__(
        self,
        bot: commands.Bot,
        settings: Settings,
        state_repo: StateRepository,
        message_repo: MessageRepository,
        backfill_service: BackfillService,
        purge_service: PurgeService,
        voice_service: VoiceService,
        llm_router: LLMRouter,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.state_repo = state_repo
        self.message_repo = message_repo
        self.backfill_service = backfill_service
        self.purge_service = purge_service
        self.voice_service = voice_service
        self.llm_router = llm_router
        self._startup_tasks_ran = False

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._startup_tasks_ran:
            return
        self._startup_tasks_ran = True

        if self.settings.sync_commands_on_startup:
            saved_global_cmds = list(self.bot.tree.get_commands(guild=None))

            # 1. 對每個伺服器執行乾淨的 Guild Tree Sync（覆蓋舊有伺服器層級指令）
            for guild in self.bot.guilds:
                try:
                    self.bot.tree.clear_commands(guild=guild)
                    for cmd in saved_global_cmds:
                        self.bot.tree.add_command(cmd, guild=guild, override=True)
                    synced = await self.bot.tree.sync(guild=guild)
                    logger.info(
                        "已透過 Guild Tree 同步 %d 個指令至伺服器：%s (%d)",
                        len(synced),
                        guild.name,
                        guild.id,
                    )
                except Exception as exc:
                    logger.warning("同步伺服器 %s Slash 指令時發生錯誤：%s", guild.name, exc)

            # 2. 移除 Discord 全域層級殘留的舊版指令（避免與 Guild Tree 指令重複顯示）
            try:
                self.bot.tree.clear_commands(guild=None)
                await self.bot.tree.sync(guild=None)
                for cmd in saved_global_cmds:
                    self.bot.tree.add_command(cmd, guild=None, override=True)
                logger.info("已清除殘留的全域重複 Slash 指令。")
            except Exception as exc:
                logger.warning("清除全域舊指令時發生錯誤：%s", exc)

        asyncio.create_task(
            self.backfill_service.backfill_guild_channels(list(self.bot.guilds))
        )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self.message_repo.delete_message(payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(
        self,
        payload: discord.RawBulkMessageDeleteEvent,
    ) -> None:
        await self.message_repo.delete_messages_bulk(list(payload.message_ids))

    def _is_bot_addressed(self, message: discord.Message) -> bool:
        if self.bot.user is None:
            return False
        if self.bot.user in message.mentions:
            return True
        if (
            message.reference
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author.id == self.bot.user.id
        ):
            return True
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return

        clean_text = message.clean_content or message.content or ""
        if clean_text.strip() or message.attachments:
            reply_id = (
                message.reference.message_id
                if message.reference and message.reference.message_id
                else None
            )
            record = StoredMessage(
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
            await self.message_repo.insert_message(record)
            await self.state_repo.update_last_indexed_msg_id(
                channel_id=message.channel.id,
                guild_id=message.guild.id,
                last_indexed_msg_id=message.id,
            )

        if message.author.bot:
            return

        if message.content.startswith("!"):
            return

        if not self._is_bot_addressed(message):
            return

        assert self.bot.user is not None
        bot_id = self.bot.user.id

        if isinstance(message.channel, discord.TextChannel):
            purge_intent = self.purge_service.parse_nl_purge_intent(message, bot_id)
            if purge_intent is not None:
                status_text = await self.purge_service.execute_purge(
                    channel=message.channel,
                    invoker=message.author,
                    count=purge_intent.count,
                    target_user=purge_intent.target_user,
                    keyword=purge_intent.keyword,
                    exclude_message_id=message.id,
                )
                await message.reply(status_text, mention_author=False)
                return

        if isinstance(message.channel, discord.abc.GuildChannel):
            voice_intent = self.voice_service.parse_nl_voice_intent(message, bot_id)
            if voice_intent is not None:
                voice_reply = await self.voice_service.execute_voice_intent(
                    guild=message.guild,
                    source_channel=message.channel,
                    invoker=message.author,
                    intent=voice_intent,
                )
                await message.reply(voice_reply, mention_author=False)
                return

        channel_mode = await self.state_repo.get_channel_mode(
            channel_id=message.channel.id,
            guild_id=message.guild.id,
        )

        cleaned_prompt = re.sub(rf"<@!?{bot_id}>", "", message.content).strip()
        other_mentions = [u for u in message.mentions if u.id != bot_id]
        target_user = other_mentions[0] if other_mentions else None

        for u in other_mentions:
            cleaned_prompt = re.sub(rf"<@!?{u.id}>", f"@{u.display_name}", cleaned_prompt)

        http_session = getattr(self.bot, "http_session", None)
        images = (
            await download_message_images(message, http_session)
            if channel_mode == "helpful"
            else []
        )

        req_ctx = LLMRequestContext(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            channel_mode=channel_mode,
            author_id=message.author.id,
            author_display_name=message.author.display_name,
            prompt_text=cleaned_prompt,
            target_user_id=target_user.id if target_user else None,
            target_display_name=target_user.display_name if target_user else None,
            images=images,
        )

        try:
            async with message.channel.typing():
                llm_response = await asyncio.wait_for(
                    self.llm_router.generate_response(req_ctx),
                    timeout=35.0,
                )
        except asyncio.TimeoutError:
            await message.reply("⚠️ 思考逾時（超過 35 秒），請再試一次。", mention_author=False)
            return
        except Exception as exc:
            logger.exception("處理訊息時發生未預期錯誤：%s", exc)
            await message.reply(f"⚠️ 處理訊息時發生錯誤：`{exc}`", mention_author=False)
            return

        chunks = chunk_markdown(llm_response.text, max_length=1900)
        for idx, chunk in enumerate(chunks):
            if idx == 0:
                await message.reply(chunk, mention_author=False)
            else:
                await message.channel.send(chunk)