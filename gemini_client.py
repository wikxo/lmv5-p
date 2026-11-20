# gemini_client.py
from __future__ import annotations

import logging
from typing import Final, Sequence

from google import genai
from google.genai import types

from analytics_service import AnalyticsService
from llm_tools import TOOL_DECLARATIONS, execute_tool_call
from media import DownloadedMedia

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS: Final[int] = 3

# 關閉所有可調式 Gemini 安全過濾器 (BLOCK_NONE)
UNFILTERED_SAFETY_SETTINGS: Final[list[types.SafetySetting]] = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

# 偵測 Gemini 是否出現軟性道德說教或拒答
REFUSAL_PREFIXES: Final[tuple[str, ...]] = (
    "我無法",
    "我不能",
    "很抱歉，我無法",
    "抱歉，我無法",
    "抱歉，我不能",
    "對不起，我無法",
    "作為一個",
    "身為一個",
    "I cannot",
    "I'm sorry, I cannot",
)


class GeminiClient:
    def __init__(self, api_key: str, model_name: str = "gemini-3.8-flash") -> None:
        self.model_name = model_name
        self._client = genai.Client(api_key=api_key) if api_key else None

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def generate_with_tools(
        self,
        system_instruction: str,
        user_prompt: str,
        images: Sequence[DownloadedMedia],
        guild_id: int,
        channel_id: int,
        analytics_service: AnalyticsService,
        enable_tools: bool = False,
        temperature: float = 0.8,
    ) -> tuple[str, int]:
        if self._client is None:
            raise RuntimeError("GEMINI_API_KEY 尚未設定。")

        user_parts: list[types.Part] = []
        for img in images:
            user_parts.append(
                types.Part.from_bytes(data=img.data, mime_type=img.mime_type)
            )
        user_parts.append(types.Part.from_text(text=user_prompt or "請分析圖片內容。"))

        contents: list[types.Content] = [
            types.Content(role="user", parts=user_parts)
        ]

        tools_config = (
            [types.Tool(function_declarations=TOOL_DECLARATIONS)]
            if enable_tools
            else None
        )

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools_config,
            safety_settings=UNFILTERED_SAFETY_SETTINGS,
            temperature=temperature,
        )

        tool_calls_executed = 0

        for _ in range(MAX_TOOL_TURNS + 1):
            response = await self._client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config,
            )

            function_calls = getattr(response, "function_calls", None)
            if not function_calls or tool_calls_executed >= MAX_TOOL_TURNS:
                final_text = (response.text or "").strip()

                # 若被硬性過濾器攔截（回傳空字串）或觸發軟性拒答開頭，拋出例外讓 Router 無縫交給 Groq 處理
                if not final_text:
                    raise RuntimeError("Gemini 回傳空內容（可能觸發底層安全攔截）")
                if any(final_text.startswith(prefix) for prefix in REFUSAL_PREFIXES):
                    raise RuntimeError(f"Gemini 觸發軟性拒答：{final_text[:25]}...")

                return final_text, tool_calls_executed

            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            tool_response_parts: list[types.Part] = []
            for fc in function_calls:
                tool_calls_executed += 1
                args_dict = dict(fc.args) if fc.args else {}
                result_str = await execute_tool_call(
                    tool_name=fc.name,
                    arguments=args_dict,
                    default_guild_id=guild_id,
                    default_channel_id=channel_id,
                    analytics_service=analytics_service,
                )
                tool_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result_str},
                    )
                )

            contents.append(types.Content(role="user", parts=tool_response_parts))

        raise RuntimeError("工具呼叫已達最大輪數上限。")
