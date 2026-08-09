"""롤 계정 등록.

- `/등록 [유저] [롤닉네임#태그]` : 라이엇 API 로 계정을 확인하고 등록
- `/등록해제 [유저]` : 관리자용 등록 해제
- `/내계정` : 내 등록 정보 확인
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors
from core.checks import is_staff
from utils.logs import base_embed, send_log, truncate, user_field
from utils.parsing import FormatError, split_riot_id
from utils.riot import RiotError
from utils.roles import TAKEN, sync_unregistered_role

log = logging.getLogger("mainbot.register")


class RiotRegister(commands.Cog, name="RiotRegister"):
    """롤 닉네임 등록 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="등록", description="롤 닉네임#태그를 서버에 등록합니다.")
    @app_commands.describe(
        유저="등록할 대상 (본인이 아니면 서버 관리 권한 필요)",
        롤닉네임="`롤닉네임#태그` 형식 (예: 홍길동#KR1)",
    )
    async def register(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        롤닉네임: str,
    ) -> None:
        actor = interaction.user
        if 유저.id != actor.id:
            if not isinstance(actor, discord.Member) or not is_staff(actor):
                await interaction.response.send_message(
                    "다른 사람의 계정은 **서버 관리 권한**이 있어야 등록할 수 있습니다.\n"
                    "본인 계정은 `/등록 유저:@본인` 으로 등록해 주세요.",
                    ephemeral=True,
                )
                return
        if 유저.bot:
            await interaction.response.send_message(
                "봇은 롤 계정을 등록할 수 없습니다.", ephemeral=True
            )
            return

        try:
            game_name, tag_line = split_riot_id(롤닉네임)
        except FormatError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.defer()

        # 다른 사람이 이미 쓰고 있는 계정인지
        owner = await self.bot.db.riot_owner(game_name, tag_line)
        if owner is not None and owner != 유저.id:
            await interaction.followup.send(
                f"❌ `{game_name}#{tag_line}` 계정은 이미 <@{owner}> 님이 등록했습니다.\n"
                "잘못된 등록이라면 관리자에게 `/등록해제` 를 요청해 주세요.",
                ephemeral=True,
            )
            return

        # 라이엇 API 로 실제 존재하는 계정인지 확인
        verified = False
        puuid: str | None = None
        rank_label: str | None = None
        note = ""

        if self.bot.riot.enabled:
            try:
                account = await self.bot.riot.fetch_account(game_name, tag_line)
            except RiotError as exc:
                await interaction.followup.send(f"❌ {exc}", ephemeral=True)
                return
            if account is None:
                await interaction.followup.send(
                    f"❌ `{game_name}#{tag_line}` 계정을 찾을 수 없습니다.\n"
                    "닉네임과 태그를 다시 확인해 주세요. (대소문자는 상관없습니다)",
                    ephemeral=True,
                )
                return
            # 라이엇이 알려준 정확한 표기로 저장한다
            game_name, tag_line = account.game_name, account.tag_line
            puuid = account.puuid
            verified = True

            rank = await self.bot.riot.fetch_solo_rank(puuid)
            rank_label = rank.label if rank else "언랭크 / 배치 미완료"
        else:
            note = (
                "\n\n⚠️ 라이엇 API 키가 설정되어 있지 않아 **실제 계정 확인 없이** "
                "등록했습니다. (`.env` 의 `RIOT_API_KEY`)"
            )

        previous = await self.bot.db.get_user(유저.id)
        await self.bot.db.set_riot_account(유저.id, game_name, tag_line, puuid, actor.id)
        removed = await sync_unregistered_role(유저, True, reason="롤 계정 등록 완료")

        embed = base_embed(
            "✅ 롤 계정 등록 완료",
            Colors.SUCCESS,
            description=(
                f"{유저.mention} 님의 롤 계정이 **`{game_name}#{tag_line}`** 로 "
                f"등록되었습니다.{note}"
            ),
        )
        embed.set_thumbnail(url=유저.display_avatar.url)
        embed.add_field(
            name="계정 확인", value="라이엇 API 확인 완료" if verified else "확인 안 함", inline=True
        )
        if rank_label:
            embed.add_field(name="솔로랭크", value=rank_label, inline=True)
        if previous.riot_id and previous.riot_id != f"{game_name}#{tag_line}":
            embed.add_field(name="이전 등록", value=f"`{previous.riot_id}`", inline=True)
        if removed == TAKEN:
            embed.add_field(
                name="역할",
                value="미등록 역할이 회수되었습니다.",
                inline=False,
            )
        await interaction.followup.send(embed=embed)

        log_embed = base_embed("🎮 롤 계정 등록", Colors.TEAL)
        log_embed.set_author(name=str(유저), icon_url=유저.display_avatar.url)
        log_embed.add_field(name="대상", value=user_field(유저), inline=True)
        log_embed.add_field(name="등록한 사람", value=user_field(actor), inline=True)
        log_embed.add_field(name="롤 계정", value=f"`{game_name}#{tag_line}`", inline=False)
        log_embed.add_field(
            name="이전 등록", value=f"`{previous.riot_id}`" if previous.riot_id else "없음", inline=True
        )
        log_embed.add_field(
            name="API 확인", value="완료" if verified else "미확인", inline=True
        )
        if rank_label:
            log_embed.add_field(name="솔로랭크", value=rank_label, inline=True)
        if puuid:
            log_embed.add_field(name="PUUID", value=f"`{truncate(puuid, 90)}`", inline=False)
        await send_log(self.bot, Channels.REGISTER_LOG, log_embed)

    @app_commands.command(name="등록해제", description="[관리자] 유저의 롤 계정 등록을 해제합니다.")
    @app_commands.describe(유저="등록을 해제할 대상", 사유="해제 사유")
    async def unregister(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        사유: str = "관리자 처리",
    ) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not is_staff(actor):
            await interaction.response.send_message(
                "이 명령어는 **서버 관리 권한**이 있어야 사용할 수 있습니다.", ephemeral=True
            )
            return

        previous = await self.bot.db.get_user(유저.id)
        if not previous.registered:
            await interaction.response.send_message(
                f"{유저.mention} 님은 등록된 롤 계정이 없습니다.", ephemeral=True
            )
            return

        await self.bot.db.clear_riot_account(유저.id)
        await sync_unregistered_role(유저, False, reason="롤 계정 등록 해제")

        embed = base_embed(
            "🗑️ 롤 계정 등록 해제",
            Colors.DANGER,
            description=(
                f"{유저.mention} 님의 등록(`{previous.riot_id}`)이 해제되었습니다."
            ),
        )
        embed.add_field(name="사유", value=truncate(사유), inline=False)
        await interaction.response.send_message(embed=embed)

        log_embed = base_embed("🗑️ 롤 계정 등록 해제", Colors.DANGER)
        log_embed.set_author(name=str(유저), icon_url=유저.display_avatar.url)
        log_embed.add_field(name="대상", value=user_field(유저), inline=True)
        log_embed.add_field(name="처리자", value=user_field(actor), inline=True)
        log_embed.add_field(name="해제된 계정", value=f"`{previous.riot_id}`", inline=False)
        log_embed.add_field(name="사유", value=truncate(사유), inline=False)
        await send_log(self.bot, Channels.REGISTER_LOG, log_embed)

    @app_commands.command(name="내계정", description="등록된 내 롤 계정을 확인합니다.")
    async def my_account(self, interaction: discord.Interaction) -> None:
        user = await self.bot.db.get_user(interaction.user.id)
        if not user.registered:
            await interaction.response.send_message(
                "아직 등록된 롤 계정이 없습니다.\n"
                "`/등록 유저:@본인 롤닉네임:홍길동#KR1` 형태로 등록해 주세요.",
                ephemeral=True,
            )
            return

        embed = base_embed(
            "🎮 내 롤 계정",
            Colors.TEAL,
            description=f"**`{user.riot_id}`**",
        )
        embed.add_field(
            name="등록 일시",
            value=(user.registered_at or "").replace("T", " ")[:19] or "알 수 없음",
            inline=True,
        )
        if user.registered_by:
            embed.add_field(name="등록 처리", value=f"<@{user.registered_by}>", inline=True)

        if self.bot.riot.enabled and user.riot_puuid:
            rank = await self.bot.riot.fetch_solo_rank(user.riot_puuid)
            embed.add_field(
                name="솔로랭크",
                value=rank.label if rank else "언랭크 / 배치 미완료",
                inline=True,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RiotRegister(bot))
