"""롤 계정 등록.

- `/등록 [유저] [롤닉네임#태그]` : 라이엇 API 로 계정을 확인하고 등록
- `/등록해제 [유저]` : 관리자용 등록 해제
- `/내계정` : 내 등록 정보 확인

서버를 나가면 등록은 자동으로 풀린다. 포인트와 경고 기록은 남겨 둔다.
"""
from __future__ import annotations

import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors, GUILD_ID, Verify
from core.checks import is_staff
from core.registration import register_riot_account
from utils.logs import base_embed, send_log, truncate, user_field
from utils.parsing import FormatError, split_riot_id
from utils.riot import RiotError
from utils.roles import sync_registration_roles

log = logging.getLogger("mainbot.register")

ICON_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/profileicon/{icon}.png"
)
# 아이콘 이미지는 패치가 바뀌어도 그대로라 버전을 고정해도 문제가 없다
ICON_VERSION = "15.1.1"


def verify_embed(riot_id: str, target: int) -> discord.Embed:
    embed = base_embed(
        "🪪 명의인증",
        Colors.TEAL,
        description=(
            f"**`{riot_id}`** 이 정말 본인 계정인지 확인합니다.\n"
            "계정에 로그인할 수 있는 사람만 프로필 아이콘을 바꿀 수 있습니다."
        ),
    )
    embed.add_field(
        name="이렇게 해 주세요",
        value=(
            "**1.** 롤 클라이언트를 켜고 우측 상단 **프로필**을 누릅니다\n"
            "**2.** 프로필 아이콘을 아래 그림과 **똑같은 것**으로 바꿉니다\n"
            "**3.** 돌아와서 **확인** 버튼을 누릅니다\n"
            "**4.** 인증이 끝나면 아이콘은 원래대로 바꾸셔도 됩니다"
        ),
        inline=False,
    )
    embed.add_field(
        name="바꿔야 할 아이콘",
        value=f"기본 아이콘 **{target}번** (아래 그림)",
        inline=False,
    )
    embed.set_image(url=ICON_URL.format(version=ICON_VERSION, icon=target))
    embed.set_footer(
        text=f"롤 같이 하자 · {Verify.TIMEOUT_MINUTES}분 안에 마쳐 주세요"
    )
    return embed


class VerifyView(discord.ui.View):
    """명의인증 확인 버튼.

    누른 사람 본인만 쓸 수 있어야 하므로 대상 유저를 들고 있는다. 지속 뷰가
    아니라 명령어를 칠 때마다 새로 만들어지므로 custom_id 는 고정하지 않는다.
    """

    def __init__(self, cog: "RiotRegister", user_id: int, target: int) -> None:
        super().__init__(timeout=Verify.TIMEOUT_MINUTES * 60)
        self.cog = cog
        self.user_id = user_id
        self.target = target
        self.last_try = 0.0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "본인만 누를 수 있습니다.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="확인", emoji="✅", style=discord.ButtonStyle.success)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # 연타로 라이엇 API 를 두드리지 않게 잠깐 쉬게 한다
        left = Verify.COOLDOWN_SECONDS - (time.monotonic() - self.last_try)
        if left > 0:
            await interaction.response.send_message(
                f"{left:.0f}초 뒤에 다시 눌러 주세요.", ephemeral=True
            )
            return
        self.last_try = time.monotonic()

        await interaction.response.defer(ephemeral=True)
        user = await self.cog.bot.db.get_user(interaction.user.id)
        if not user.riot_puuid:
            await interaction.followup.send("등록 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        try:
            icon = await self.cog.bot.riot.fetch_profile_icon(user.riot_puuid)
        except RiotError as exc:
            await interaction.followup.send(
                f"라이엇 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.\n`{exc}`",
                ephemeral=True,
            )
            return

        if icon is None:
            await interaction.followup.send(
                "프로필 정보를 읽지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
                ephemeral=True,
            )
            return

        if icon != self.target:
            await interaction.followup.send(
                f"아직 **{self.target}번** 아이콘이 아닙니다. "
                f"(지금 **{icon}번**)\n"
                "롤 클라이언트에서 바꾼 뒤 잠시 기다렸다 다시 눌러 주세요. "
                "라이엇 쪽에 반영되는 데 **1~2분** 걸리기도 합니다.",
                ephemeral=True,
            )
            return

        await self.cog.bot.db.set_verified(interaction.user.id)
        button.disabled = True
        self.stop()

        done = base_embed(
            "✅ 명의인증 완료",
            Colors.SUCCESS,
            description=(
                f"**`{user.riot_id}`** 이 본인 계정임이 확인되었습니다.\n"
                "프로필 아이콘은 이제 원래대로 바꾸셔도 됩니다."
            ),
        )
        await interaction.followup.send(embed=done, ephemeral=True)

        log_embed = base_embed("🪪 명의인증 완료", Colors.SUCCESS)
        log_embed.set_author(
            name=str(interaction.user), icon_url=interaction.user.display_avatar.url
        )
        log_embed.add_field(name="유저", value=user_field(interaction.user), inline=True)
        log_embed.add_field(name="계정", value=f"`{user.riot_id}`", inline=True)
        log_embed.add_field(name="확인 방법", value=f"아이콘 {self.target}번", inline=True)
        await send_log(self.cog.bot, Channels.REGISTER_LOG, log_embed)
        log.info("명의인증 완료: %s (%s)", interaction.user, user.riot_id)


class RiotRegister(commands.Cog, name="RiotRegister"):
    """롤 닉네임 등록 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------- 나가면 자동 해제

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """서버를 나가면 롤 계정 등록을 푼다.

        같은 롤 계정을 다른 사람이 다시 등록할 수 있어야 하고, 나간 사람이
        서버원으로 남아 있으면 집계도 어긋난다.

        **포인트와 경고 기록은 지우지 않는다.** 다시 들어오는 사람이 적지
        않은데 모아 둔 포인트가 사라지면 문의로 이어진다. 등록만 푼다.
        """
        if GUILD_ID is not None and member.guild.id != GUILD_ID:
            return

        user = await self.bot.db.get_user(member.id)
        if not user.registered:
            return

        await self.bot.db.clear_riot_account(member.id)
        log.info("서버 탈퇴로 등록 해제: %s (%s)", member, user.riot_id)

        embed = base_embed(
            "🚪 서버 탈퇴 · 등록 자동 해제",
            Colors.DANGER,
            description="서버를 나가서 롤 계정 등록을 해제했습니다.",
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="대상", value=user_field(member), inline=True)
        embed.add_field(name="해제된 계정", value=f"`{user.riot_id}`", inline=True)
        embed.add_field(
            name="보유 포인트",
            value=f"{user.points:,}P (그대로 유지)",
            inline=True,
        )
        await send_log(self.bot, Channels.REGISTER_LOG, embed)

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

        result = await register_riot_account(
            self.bot, 유저, game_name, tag_line, actor=actor, source="`/등록` 명령어"
        )
        if not result.ok:
            await interaction.followup.send(f"❌ {result.error}", ephemeral=True)
            return

        note = ""
        if not result.verified:
            note = (
                "\n\n⚠️ 라이엇 API 키가 설정되어 있지 않아 **실제 계정 확인 없이** "
                "등록했습니다. (`.env` 의 `RIOT_API_KEY`)"
            )

        embed = base_embed(
            "✅ 롤 계정 등록 완료",
            Colors.SUCCESS,
            description=(
                f"{유저.mention} 님의 롤 계정이 **`{result.riot_id}`** 로 "
                f"등록되었습니다.{note}"
            ),
        )
        embed.set_thumbnail(url=유저.display_avatar.url)
        embed.add_field(
            name="계정 확인",
            value="라이엇 API 확인 완료" if result.verified else "확인 안 함",
            inline=True,
        )
        if result.rank_label:
            embed.add_field(name="솔로랭크", value=result.rank_label, inline=True)
        if result.previous_riot_id and not result.unchanged:
            embed.add_field(
                name="이전 등록", value=f"`{result.previous_riot_id}`", inline=True
            )
        if result.role_removed:
            embed.add_field(
                name="역할", value="미등록 역할이 회수되었습니다.", inline=False
            )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="등록해제", description="[관리자] 유저의 롤 계정 등록을 해제합니다.")
    @app_commands.describe(유저="등록을 해제할 대상", 사유="해제 사유")
    @app_commands.default_permissions(manage_guild=True)
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
        await sync_registration_roles(유저, False, reason="롤 계정 등록 해제")

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

    @app_commands.command(
        name="명의인증", description="등록한 롤 계정이 본인 것인지 인증합니다."
    )
    async def verify_account(self, interaction: discord.Interaction) -> None:
        user = await self.bot.db.get_user(interaction.user.id)
        if not user.registered or not user.riot_puuid:
            await interaction.response.send_message(
                "먼저 롤 계정을 등록해 주세요.\n"
                "`/등록 유저:@본인 롤닉네임:홍길동#KR1`",
                ephemeral=True,
            )
            return
        if user.verified:
            await interaction.response.send_message(
                f"이미 **`{user.riot_id}`** 명의인증을 마치셨습니다. ✅",
                ephemeral=True,
            )
            return
        if not self.bot.riot.enabled:
            await interaction.response.send_message(
                "라이엇 API 키가 없어 인증할 수 없습니다. 관리자에게 알려 주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        current = await self.bot.riot.fetch_profile_icon(user.riot_puuid)
        # 지금 쓰는 아이콘을 그대로 요구하면 아무것도 안 해도 통과된다
        choices = [i for i in Verify.ICON_POOL if i != current]
        target = random.choice(choices)

        view = VerifyView(self, interaction.user.id, target)
        await interaction.followup.send(
            embed=verify_embed(user.riot_id or "", target), view=view, ephemeral=True
        )

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
            description=(
                f"**`{user.riot_id}`**\n"
                + (
                    "🪪 **명의인증 완료** — 본인 계정으로 확인되었습니다."
                    if user.verified
                    else "⚠️ 아직 명의인증 전입니다. `/명의인증` 으로 본인 계정임을 "
                         "확인해 주세요."
                )
            ),
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
