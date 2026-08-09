"""`/프로필 [유저]` — 프로필 카드 이미지."""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import TIER_NAMES
from utils.card import render_profile_card
from utils.parsing import FormatError, parse_profile_format
from utils.roles import lane_label_short, main_lane_of, sub_lane_of, tier_of

log = logging.getLogger("mainbot.profile")


def fmt_duration(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


class Profile(commands.Cog, name="Profile"):
    """서버 프로필 카드."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
        points = user.points
        warnings = await db.warning_count(target.id)
        rank = await db.point_rank(target.id)

        # 역할에서 티어·라인을 읽고, 없으면 닉네임 양식에서 보충한다
        tier_code = tier_of(target)
        main_lane = main_lane_of(target)
        sub_lane = sub_lane_of(target)

        lp: int | None = None
        try:
            parsed = parse_profile_format(target.display_name)
        except FormatError:
            parsed = None
        if parsed is not None:
            tier_code = tier_code or parsed.tier
            main_lane = main_lane or parsed.main_lane
            sub_lane = sub_lane or parsed.sub_lane
            if parsed.tier == tier_code:
                lp = parsed.lp

        if tier_code:
            tier_label = TIER_NAMES.get(tier_code, tier_code)
            if lp is not None:
                tier_label = f"{tier_label} {lp}LP"
        else:
            tier_label = "미설정"

        try:
            avatar_bytes = await target.display_avatar.replace(
                format="png", size=256
            ).read()
        except (discord.HTTPException, discord.NotFound):
            avatar_bytes = None

        joined = target.joined_at.strftime("%Y-%m-%d") if target.joined_at else "알 수 없음"

        buffer = await asyncio.to_thread(
            render_profile_card,
            display_name=target.display_name,
            user_tag=f"@{target.name}",
            avatar_bytes=avatar_bytes,
            riot_id=user.riot_id,
            tier_code=tier_code,
            tier_label=tier_label,
            main_lane=lane_label_short(main_lane),
            sub_lane=lane_label_short(sub_lane),
            points=points,
            warnings=warnings,
            rank=rank,
            joined=joined,
            voice_time=fmt_duration(user.voice_seconds),
            attendance=user.total_attendance,
        )

        file = discord.File(buffer, filename=f"profile_{target.id}.png")
        await interaction.followup.send(file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
