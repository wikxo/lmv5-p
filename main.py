# main.py
from __future__ import annotations

import asyncio
import logging
import sys

import aiohttp
import discord
from discord.ext import commands

from admin_cog import AdminCog
from analytics_service import AnalyticsService
from backfill_service import BackfillService
from chat_cog import ChatCog
from config import Settings, load_settings
from db_connection import DatabaseManager
from gemini_client import GeminiClient
from groq_client import GroqClient
from llm_router import LLMRouter
from message_repo import MessageRepository
from purge_cog import PurgeCog
from purge_service import PurgeService
from state_repo import StateRepository
from voice_cog import VoiceCog
from voice_service import VoiceService


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


class DiscordBotV5(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True
        intents.guilds = True
        intents.messages = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=settings.application_id,
            help_command=None,
        )
        self.settings = settings

        self.db = DatabaseManager(settings.db_path)
        self.state_repo = StateRepository(
            db=self.db,
            default_mode=settings.default_channel_mode,
        )
        self.message_repo = MessageRepository(db=self.db)

        self.backfill_service = BackfillService(
            settings=settings,
            state_repo=self.state_repo,
            message_repo=self.message_repo,
        )
        self.analytics_service = AnalyticsService(message_repo=self.message_repo)
        self.purge_service = PurgeService(
            settings=settings,
            message_repo=self.message_repo,
        )
        self.voice_service = VoiceService(settings=settings)

        self.gemini_client = GeminiClient(
            api_key=settings.gemini_api_key,
            model_name=settings.gemini_model,
        )
        self.groq_client = GroqClient(
            api_key=settings.groq_api_key,
            model_name=settings.groq_model,
        )
        self.llm_router = LLMRouter(
            settings=settings,
            gemini_client=self.gemini_client,
            groq_client=self.groq_client,
            analytics_service=self.analytics_service,
        )

        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        await self.db.initialize()

        timeout = aiohttp.ClientTimeout(total=8.0)
        self.http_session = aiohttp.ClientSession(timeout=timeout)

        await self.add_cog(
            AdminCog(
                bot=self,
                settings=self.settings,
                db=self.db,
                state_repo=self.state_repo,
            )
        )
        await self.add_cog(PurgeCog(bot=self, purge_service=self.purge_service))
        await self.add_cog(VoiceCog(bot=self, voice_service=self.voice_service))
        await self.add_cog(
            ChatCog(
                bot=self,
                settings=self.settings,
                state_repo=self.state_repo,
                message_repo=self.message_repo,
                backfill_service=self.backfill_service,
                purge_service=self.purge_service,
                voice_service=self.voice_service,
                llm_router=self.llm_router,
            )
        )

    async def on_ready(self) -> None:
        logger = logging.getLogger("main.on_ready")
        if self.user is not None:
            logger.info(
                "機器人已上線：%s (ID: %d) | 連線伺服器數：%d",
                self.user,
                self.user.id,
                len(self.guilds),
            )

    async def on_command_error(
        self,
        context: commands.Context,
        exception: commands.CommandError,
    ) -> None:
        if isinstance(exception, commands.CommandNotFound):
            return
        await super().on_command_error(context, exception)

    async def close(self) -> None:
        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()
        await self.db.close()
        await super().close()


async def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    bot = DiscordBotV5(settings=settings)
    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass