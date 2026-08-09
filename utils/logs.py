"""로그 채널 전송 공통 로직."""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import discord

from config import TIMEZONE

log = logging.getLogger("mainbot.logs")


def base_embed(title: str, color: int, *, description: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="롤 같이 하자")
    return embed


def truncate(text: Optional[str], limit: int = 1000) -> str:
    if not text:
        return "없음"
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def local_str(moment) -> str:
    if moment is None:
        return "알 수 없음"
    return moment.astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


async def send_log(
    bot: discord.Client,
    channel_id: int,
    embed: discord.Embed,
    *,
    content: Optional[str] = None,
    files: Optional[Sequence[discord.File]] = None,
) -> Optional[discord.Message]:
    """로그 채널로 임베드를 보낸다. 실패해도 명령어 처리를 막지 않는다."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.warning("로그 채널 %s 을(를) 찾을 수 없습니다.", channel_id)
            return None
    if not isinstance(channel, discord.abc.Messageable):
        return None
    try:
        return await channel.send(content=content, embed=embed, files=list(files or []))
    except discord.HTTPException as exc:
        log.warning("로그 전송 실패 (%s): %s", channel_id, exc)
        return None


def user_field(user: discord.abc.User | discord.Member) -> str:
    """유저 표기 (멘션 + 태그 + ID)."""
    return f"{user.mention}\n`{user}`\n`{user.id}`"
