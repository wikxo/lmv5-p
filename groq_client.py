# groq_client.py
from __future__ import annotations

from groq import AsyncGroq


class GroqClient:
    def __init__(self, api_key: str, model_name: str = "qwen/qwen3.8-27b") -> None:
        self.model_name = model_name
        self._client = AsyncGroq(api_key=api_key) if api_key else None

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def generate_text(
        self,
        system_instruction: str,
        user_prompt: str,
        temperature: float = 0.75,
        max_tokens: int = 1000,
    ) -> str:
        if self._client is None:
            raise RuntimeError("GROQ_API_KEY 尚未設定。")

        completion = await self._client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = completion.choices[0].message.content if completion.choices else ""
        return (content or "").strip()