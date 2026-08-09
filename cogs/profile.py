"""`/프로필 [유저]` — 프로필 카드 이미지."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import RANK_CACHE_SECONDS, TIER_NAMES
from utils.card import render_profile_card
from utils.parsing import FormatError, parse_profile_format
from utils.riot import RankEntry
from utils.roles import tier_of

log = logging.getLogger("mainbot.profile")


class Profile(commands.Cog, name="Profile"):
    """서버 프로필 카드."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- 랭크

    async def _ranks(self, user) -> tuple[Optional[RankEntry], Optional[RankEntry]]:
        """솔랭 / 자유랭크. 캐시가 신선하면 그대로 쓰고, 아니면 다시 받아온다."""
        if not user.registered or not user.riot_puuid:
            return None, None

        if user.rank_is_fresh(RANK_CACHE_SECONDS):
            solo = user.solo_rank
            flex = user.flex_rank
            return (
                RankEntry.from_dict(solo) if solo else None,
                RankEntry.from_dict(flex) if flex else None,
            )

        if not self.bot.riot.enabled:
            return None, None

        found = await self.bot.riot.fetch_ranks(user.riot_puuid)
        solo, flex = found["solo"], found["flex"]
        await self.bot.db.set_riot_ranks(
            user.user_id,
            solo.to_dict() if solo else None,
            flex.to_dict() if flex else None,
        )
        return solo, flex

    def _tier_fields(
        self, entry: Optional[RankEntry], registered: bool
    ) -> tuple[Optional[str], str, str]:
        """(티어 약자, 표시할 티어 이름, 승패 한 줄)."""
        if entry is not None:
            return entry.tier_code, entry.tier_name, entry.record
        if not registered:
            return None, "미등록", "/등록 으로 롤 계정을 연결해 주세요"
        if not self.bot.riot.enabled:
            return None, "확인 불가", "라이엇 API 키가 설정되지 않았습니다"
        return None, "언랭크", "배치 미완료"

    # ----------------------------------------------------------- 명령어

    @app_commands.command(name="프로필", description="서버 프로필 카드를 봅니다.")
    @app_commands.describe(유저="확인할 유저 (비우면 본인)")
    async def profile(
        self, interaction: discord.Interaction, 유저: discord.Member | None = None
    ) -> None:
        target = 유저 or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                "서버 안에서만 사용할 수 있는 명령어입니다.", ephemeral=True
            )
            return

        await interaction.response.defer()

        db = self.bot.db
        user = await db.get_user(target.id)

        voice_level, voice_now, voice_need = user.voice_level
        chat_level, chat_now, chat_need = user.chat_level
        voice_rank = await db.xp_rank(target.id, "voice_xp")
        chat_rank = await db.xp_rank(target.id, "chat_xp")

        solo, flex = await self._ranks(user)
        solo_code, solo_name, solo_record = self._tier_fields(solo, user.registered)
        flex_code, flex_name, flex_record = self._tier_fields(flex, user.registered)

        # 라이엇 정보가 없으면 역할/닉네임 양식에 적힌 티어라도 보여준다
        if solo is None:
            fallback = tier_of(target)
            if fallback is None:
                try:
                    fallback = parse_profile_format(target.display_name).tier
                except FormatError:
                    fallback = None
            if fallback is not None:
                solo_code = fallback
                solo_name = TIER_NAMES.get(fallback, solo_name)
                solo_record = "서버 등록 티어 기준"

        try:
            avatar_bytes = await target.display_avatar.replace(
                format="png", size=256
            ).read()
        except (discord.HTTPException, discord.NotFound):
            avatar_bytes = None

        buffer = await asyncio.to_thread(
            render_profile_card,
            display_name=target.display_name,
            avatar_bytes=avatar_bytes,
            riot_id=user.riot_id,
            points=user.points,
            voice_level=voice_level,
            voice_current_xp=voice_now,
            voice_needed_xp=voice_need,
            voice_rank=voice_rank,
            chat_level=chat_level,
            chat_current_xp=chat_now,
            chat_needed_xp=chat_need,
            chat_rank=chat_rank,
            solo_tier_code=solo_code,
            solo_tier_name=solo_name,
            solo_record=solo_record,
            flex_tier_code=flex_code,
            flex_tier_name=flex_name,
            flex_record=flex_record,
        )

        file = discord.File(buffer, filename=f"profile_{target.id}.png")
        await interaction.followup.send(file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
