# media.py
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Final

import aiohttp
import discord

logger = logging.getLogger(__name__)

ALLOWED_IMAGE_MIMES: Final[frozenset[str]] = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }
)
MAX_IMAGE_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    data: bytes
    mime_type: str
    filename: str


def _infer_mime_type(content_type: str | None, url_or_name: str) -> str | None:
    if content_type:
        base_mime = content_type.split(";")[0].strip().lower()
        if base_mime in ALLOWED_IMAGE_MIMES:
            return base_mime

    lowered = url_or_name.lower().split("?")[0]
    if lowered.endswith(".png"):
        return "image/png"
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".webp"):
        return "image/webp"
    if lowered.endswith(".gif"):
        return "image/gif"
    return None


async def _fetch_bytes(
    session: aiohttp.ClientSession,
    url: str,
    expected_mime: str,
    filename: str,
) -> DownloadedMedia | None:
    try:
        timeout = aiohttp.ClientTimeout(total=8.0)
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            content_length = resp.content_length
            if content_length is not None and content_length > MAX_IMAGE_BYTES:
                return None

            raw_bytes = await resp.read()
            if len(raw_bytes) > MAX_IMAGE_BYTES:
                return None

            return DownloadedMedia(
                data=raw_bytes,
                mime_type=expected_mime,
                filename=filename,
            )
    except Exception as exc:
        logger.warning("下載圖片發生例外 (%s): %s", filename, exc)
        return None


async def _extract_from_single_message(
    msg: discord.Message,
    session: aiohttp.ClientSession,
) -> list[DownloadedMedia]:
    results: list[DownloadedMedia] = []

    for attachment in msg.attachments:
        if attachment.size > MAX_IMAGE_BYTES:
            continue
        mime = _infer_mime_type(attachment.content_type, attachment.filename)
        if mime is None:
            continue
        downloaded = await _fetch_bytes(
            session=session,
            url=attachment.url,
            expected_mime=mime,
            filename=attachment.filename,
        )
        if downloaded is not None:
            results.append(downloaded)

    for embed in msg.embeds:
        image_url = (embed.image and embed.image.url) or (embed.thumbnail and embed.thumbnail.url)
        if not image_url:
            continue
        mime = _infer_mime_type(None, image_url)
        if mime is None:
            continue
        downloaded = await _fetch_bytes(
            session=session,
            url=image_url,
            expected_mime=mime,
            filename="embed_image",
        )
        if downloaded is not None:
            results.append(downloaded)

    return results


async def download_message_images(
    message: discord.Message,
    session: aiohttp.ClientSession | None,
    max_images: int = 3,
) -> list[DownloadedMedia]:
    if session is None:
        return []

    collected = await _extract_from_single_message(message, session)

    if len(collected) < max_images and message.reference and message.reference.message_id:
        ref_msg: discord.Message | None = None
        if isinstance(message.reference.resolved, discord.Message):
            ref_msg = message.reference.resolved
        else:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                ref_msg = None

        if ref_msg is not None:
            ref_media = await _extract_from_single_message(ref_msg, session)
            collected.extend(ref_media)

    return collected[:max_images]