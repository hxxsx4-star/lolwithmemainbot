"""`/프로필 [유저]` — 프로필 카드 이미지."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    CARD_THEMES,
    CHAMPIONS,
    DEFAULT_THEME,
    PROFILE_SLOGAN,
    RANK_CACHE_SECONDS,
    TIER_NAMES,
)
from utils import ddragon
from utils.card import Theme, render_profile_card, theme_from_spec
from utils.parsing import FormatError, parse_profile_format
from utils.riot import RankEntry, RiotError
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

        # 랭크를 받으러 가는 김에 닉네임도 맞춘다. 롤 닉은 바뀌어도 PUUID 는
        # 그대로여서, 등록할 때 적어 둔 이름을 놔두면 옛 이름이 계속 남는다.
        await self._refresh_riot_name(user)

        found = await self.bot.riot.fetch_ranks(user.riot_puuid)
        solo, flex = found["solo"], found["flex"]
        await self.bot.db.set_riot_ranks(
            user.user_id,
            solo.to_dict() if solo else None,
            flex.to_dict() if flex else None,
        )
        return solo, flex

    async def _refresh_riot_name(self, user) -> None:
        """등록된 계정의 현재 닉네임을 받아와 바뀌었으면 고친다.

        실패해도 프로필 카드는 그려야 하므로 조용히 넘어간다. 이름이 조금
        옛것인 건 카드를 아예 못 보는 것보다 낫다.
        """
        try:
            account = await self.bot.riot.fetch_account_by_puuid(user.riot_puuid)
        except RiotError as exc:
            log.debug("닉네임 갱신 실패 (%s): %s", user.user_id, exc)
            return
        if account is None:
            return

        if await self.bot.db.update_riot_name(
            user.user_id, account.game_name, account.tag_line
        ):
            log.info(
                "롤 닉네임 변경 반영: %s → %s#%s",
                user.riot_id, account.game_name, account.tag_line,
            )
            # 이번 카드에도 바로 반영되도록 손에 든 값을 고쳐 둔다
            user.riot_game_name = account.game_name
            user.riot_tag_line = account.tag_line

    async def _cosmetics(self, user_id: int) -> tuple[Theme, str]:
        """상점에서 산 테마와 문구. 없거나 만료됐으면 기본값."""
        theme_row = await self.bot.db.active_purchase(user_id, "theme")
        key = str(theme_row["item_key"]) if theme_row else DEFAULT_THEME
        spec = CARD_THEMES.get(key, CARD_THEMES[DEFAULT_THEME])

        slogan_row = await self.bot.db.active_purchase(user_id, "slogan")
        slogan = str(slogan_row["value"]) if slogan_row and slogan_row["value"] else PROFILE_SLOGAN
        return theme_from_spec(spec), slogan

    async def _champion(self, user_id: int) -> tuple[Optional[bytes], str]:
        """상점에서 산 챔피언의 초상화와 이름. 안 샀으면 (None, "").

        초상화를 못 받아도 카드는 그대로 그려야 하므로 조용히 비워 둔다.
        """
        row = await self.bot.db.active_purchase(user_id, "champion_role")
        if row is None:
            return None, ""
        key = str(row["item_key"])
        spec = CHAMPIONS.get(key)
        return await ddragon.portrait(key), (spec.name if spec else "")

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
            # 닉네임 양식이 있으면 단계/LP 까지 살려서 쓴다 (`E4` → 에메랄드 4)
            try:
                parsed = parse_profile_format(target.display_name)
            except FormatError:
                parsed = None

            if parsed is not None:
                solo_code = parsed.tier
                solo_name = parsed.tier_display
                solo_record = "서버 등록 티어 기준"
            elif (fallback := tier_of(target)) is not None:
                solo_code = fallback
                solo_name = TIER_NAMES.get(fallback, solo_name)
                solo_record = "서버 등록 티어 기준"

        theme, slogan = await self._cosmetics(target.id)
        champion_bytes, champion_name = await self._champion(target.id)

        # 승부예측 전적. 아직 결과가 나온 예측이 없으면 빈 줄로 두어 카드가
        # 괜히 허전해 보이지 않게 한다
        correct, played, profit = await self.bot.db.prediction_stats(target.id)
        prediction_record = ""
        if played:
            sign = "+" if profit >= 0 else ""
            prediction_record = f"예측 {correct}/{played} · {sign}{profit:,}P"

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
            theme=theme,
            slogan=slogan,
            prediction_record=prediction_record,
            champion_bytes=champion_bytes,
            champion_name=champion_name,
        )

        file = discord.File(buffer, filename=f"profile_{target.id}.png")
        await interaction.followup.send(file=file)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Profile(bot))
