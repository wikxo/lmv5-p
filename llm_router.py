# llm_router.py
from __future__ import annotations

import asyncio
import logging

from analytics_service import AnalyticsService
from config import Settings
from gemini_client import GeminiClient
from groq_client import GroqClient
from llm_prompts import (
    build_helpful_system_prompt,
    build_roast_system_prompt,
    build_whitelist_respect_response,
    clean_llm_output,
)
from llm_schemas import LLMRequestContext, LLMResponsePayload
from permissions import is_whitelisted

logger = logging.getLogger(__name__)

HISTORY_QUERY_KEYWORDS = (
    "最常說", "最常说", "常講", "常讲", "口頭禪", "口头禅", "發言", "发言",
    "紀錄", "记录", "歷史", "历史", "總結", "总结", "懶人包", "懒人包",
    "聊了什麼", "聊了什么", "討論什麼", "讨论什么", "誰說過", "谁说过",
)


class LLMRouter:
    """
    全模式優先使用已解除過濾器 (BLOCK_NONE) 的 Gemini (gemini-3.8-flash)；
    一旦 Gemini 觸發底層攔截、拒答或逾時，0 秒無縫切換至 Groq (qwen/qwen3.8-27b) 代打！
    """

    def __init__(
        self,
        settings: Settings,
        gemini_client: GeminiClient,
        groq_client: GroqClient,
        analytics_service: AnalyticsService,
    ) -> None:
        self._settings = settings
        self._gemini = gemini_client
        self._groq = groq_client
        self._analytics = analytics_service

    async def _call_gemini(
        self,
        sys_prompt: str,
        user_msg: str,
        ctx: LLMRequestContext,
        temperature: float = 0.85,
    ) -> str:
        raw, _ = await asyncio.wait_for(
            self._gemini.generate_with_tools(
                system_instruction=sys_prompt,
                user_prompt=user_msg,
                images=ctx.images,
                guild_id=ctx.guild_id,
                channel_id=ctx.channel_id,
                analytics_service=self._analytics,
                enable_tools=False,
                temperature=temperature,
            ),
            timeout=22.0,
        )
        return clean_llm_output(raw)

    async def _call_groq(
        self,
        sys_prompt: str,
        user_msg: str,
        temperature: float = 0.85,
        max_tokens: int = 1000,
    ) -> str:
        raw = await asyncio.wait_for(
            self._groq.generate_text(
                system_instruction=sys_prompt,
                user_prompt=user_msg,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            timeout=20.0,
        )
        return clean_llm_output(raw)

    async def _generate_gemini_with_groq_backup(
        self,
        sys_prompt: str,
        user_msg: str,
        ctx: LLMRequestContext,
        temperature: float = 0.85,
    ) -> LLMResponsePayload:
        try:
            text = await self._call_gemini(
                sys_prompt=sys_prompt,
                user_msg=user_msg,
                ctx=ctx,
                temperature=temperature,
            )
            logger.info("[%s] 已由 Gemini (%s) 完成回應", ctx.channel_mode, self._gemini.model_name)
            return LLMResponsePayload(
                text=text,
                provider="gemini",
                model=self._gemini.model_name,
            )
        except Exception as gem_exc:
            logger.warning(
                "[%s] Gemini 觸發過濾或失敗 (%s)，立即由 Groq 無縫接手！",
                ctx.channel_mode,
                gem_exc,
            )
            text = await self._call_groq(
                sys_prompt=sys_prompt,
                user_msg=user_msg,
                temperature=temperature,
                max_tokens=1000,
            )
            return LLMResponsePayload(
                text=text,
                provider="groq_fallback",
                model=self._groq.model_name,
                used_fallback=True,
            )

    async def generate_response(self, ctx: LLMRequestContext) -> LLMResponsePayload:
        is_asking_history = any(k in ctx.prompt_text for k in HISTORY_QUERY_KEYWORDS)

        recent_chat = await self._analytics.get_recent_channel_messages_tool(
            channel_id=ctx.channel_id,
            limit=80 if is_asking_history else 8,
        )

        target_stats_block = ""
        if ctx.target_user_id is not None:
            target_stats_block = await self._analytics.get_user_message_stats_and_samples_tool(
                guild_id=ctx.guild_id,
                user_id=ctx.target_user_id,
                username_query=ctx.target_display_name,
                sample_limit=40,
            )

        # ==================== 1. ROAST 毒舌模式 (Unfiltered Gemini 主力 -> Groq 破防保底) ====================
        if ctx.channel_mode == "roast":
            if ctx.target_user_id is not None and is_whitelisted(ctx.target_user_id, self._settings):
                if is_whitelisted(ctx.author_id, self._settings):
                    return LLMResponsePayload(
                        text=build_whitelist_respect_response(ctx.author_display_name, ctx.prompt_text),
                        provider="whitelist_guard",
                        model="shield",
                    )
                attacker_dossier = await self._analytics.build_roast_dossier(
                    guild_id=ctx.guild_id,
                    target_user_id=ctx.author_id,
                    target_display_name=ctx.author_display_name,
                )
                sys_prompt = build_roast_system_prompt(
                    user_text=ctx.prompt_text,
                    target_display_name=ctx.author_display_name,
                    dossier_text=attacker_dossier,
                )
                user_msg = (
                    f"群友「{ctx.author_display_name}」居然敢標記洛神挑釁：「{ctx.prompt_text}」\n"
                    f"請直接火力全開反殺這個小丑，2～4句話讓他徹底破防！"
                )
            elif ctx.target_user_id is not None:
                target_name = ctx.target_display_name or "目標對象"
                sys_prompt = build_roast_system_prompt(
                    user_text=ctx.prompt_text,
                    target_display_name=target_name,
                    dossier_text=target_stats_block,
                )
                user_msg = (
                    f"【近期對話背景（僅供參考）】：\n{recent_chat}\n\n"
                    f"「{ctx.author_display_name}」要求你針對「{target_name}」回應或開火：「{ctx.prompt_text}」\n"
                    f"請結合上方該成員的真實歷史發言檔案，直接對「{target_name}」進行毒舌處刑或精準吐槽！"
                )
            elif is_whitelisted(ctx.author_id, self._settings):
                sys_prompt = build_roast_system_prompt(
                    user_text=ctx.prompt_text,
                    target_display_name=ctx.author_display_name,
                )
                user_msg = (
                    f"【頻道剛剛的對話背景（僅供語境參考，請勿死板複述）】：\n{recent_chat}\n\n"
                    f"白名單大佬「{ctx.author_display_name}」對你說：「{ctx.prompt_text}」\n"
                    f"請直接以第一人稱（洛魔）聰明、幽默、帶點狠勁地正面回答大佬的這句話！"
                )
            else:
                author_dossier = await self._analytics.build_roast_dossier(
                    guild_id=ctx.guild_id,
                    target_user_id=ctx.author_id,
                    target_display_name=ctx.author_display_name,
                )
                sys_prompt = build_roast_system_prompt(
                    user_text=ctx.prompt_text,
                    target_display_name=ctx.author_display_name,
                    dossier_text=author_dossier,
                )
                user_msg = (
                    f"【頻道剛剛的對話背景（僅供語境參考）】：\n{recent_chat}\n\n"
                    f"群友「{ctx.author_display_name}」對你說：「{ctx.prompt_text}」\n"
                    f"請直接以第一人稱針對他這句話進行高智商毒舌吐槽或邊解答邊損他！"
                )

            return await self._generate_gemini_with_groq_backup(
                sys_prompt=sys_prompt,
                user_msg=user_msg,
                ctx=ctx,
                temperature=0.88,
            )

        # ==================== 2. HELPFUL 智慧助手模式 (Gemini 主力 -> Groq 備援) ====================
        sys_prompt = build_helpful_system_prompt(
            user_text=ctx.prompt_text,
            guild_id=ctx.guild_id,
            channel_id=ctx.channel_id,
            whitelist_ids=self._settings.whitelist_ids,
        )

        if is_asking_history and ctx.target_user_id is not None:
            full_user_prompt = (
                f"以下是從伺服器資料庫調出的「{ctx.target_display_name}」真實發言統計與樣本：\n"
                f"{target_stats_block}\n\n"
                f"發話者「{ctx.author_display_name}」詢問：「{ctx.prompt_text}」\n"
                f"請直接整理出該成員最常講的話、口頭禪與發言風格："
            )
        elif is_asking_history:
            full_user_prompt = (
                f"【頻道歷史對話紀錄】：\n{recent_chat}\n\n"
                f"使用者「{ctx.author_display_name}」提問：「{ctx.prompt_text}」\n"
                f"請根據上方紀錄與你的分析能力直接回答："
            )
        else:
            full_user_prompt = (
                f"（背景參考：頻道最近幾句對話如下，若與問題無關請忽略：\n{recent_chat}）\n\n"
                f"使用者「{ctx.author_display_name}」現在問你：「{ctx.prompt_text}」\n"
                f"請直接運用你自己的知識庫、邏輯推理與幽默感，以第一人稱給出最棒的回答："
            )

        return await self._generate_gemini_with_groq_backup(
            sys_prompt=sys_prompt,
            user_msg=full_user_prompt,
            ctx=ctx,
            temperature=0.5,
        )
