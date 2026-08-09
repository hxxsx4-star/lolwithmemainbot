"""경제 시스템 — 포인트(P).

- `/출석` : 하루 한 번 100P
- 음성 채널에 머무르면 10분마다 10P 자동 지급
- `/포인트` `/랭킹` 조회, 관리자용 `/포인트지급` `/포인트차감`
"""
from __future__ import annotations

import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Channels, Colors, Economy, GUILD_ID
from core.checks import staff_only
from utils.logs import base_embed, send_log, truncate, user_field

log = logging.getLogger("mainbot.economy")

TICK_SECONDS = 60
REQUIRED_SECONDS = Economy.VOICE_INTERVAL_MINUTES * 60


def fmt_points(value: int) -> str:
    return f"{value:,}{Economy.UNIT}"


def fmt_duration(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


class Economy_(commands.Cog, name="Economy"):
    """포인트 지급과 조회."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # 유저별로 아직 포인트로 환산되지 않은 음성 체류 초
        self._pending: defaultdict[int, int] = defaultdict(int)

    async def cog_load(self) -> None:
        self.voice_tick.start()

    async def cog_unload(self) -> None:
        self.voice_tick.cancel()

    # -------------------------------------------------------- 음성 포인트

    def _eligible(self, member: discord.Member, channel: discord.VoiceChannel) -> bool:
        if member.bot:
            return False
        if GUILD_ID is not None and member.guild.id != GUILD_ID:
            return False
        if Economy.IGNORE_AFK_CHANNEL and member.guild.afk_channel is not None:
            if channel.id == member.guild.afk_channel.id:
                return False
        return True

    @tasks.loop(seconds=TICK_SECONDS)
    async def voice_tick(self) -> None:
        """1분마다 음성 채널을 훑으며 체류 시간을 쌓고, 10분마다 포인트를 준다."""
        for guild in self.bot.guilds:
            if GUILD_ID is not None and guild.id != GUILD_ID:
                continue
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not self._eligible(member, channel):
                        continue
                    self._pending[member.id] += TICK_SECONDS
                    await self.bot.db.add_voice_seconds(member.id, TICK_SECONDS)

                    if self._pending[member.id] < REQUIRED_SECONDS:
                        continue
                    self._pending[member.id] -= REQUIRED_SECONDS
                    await self._grant_voice_points(member, channel)

    async def _grant_voice_points(
        self, member: discord.Member, channel: discord.VoiceChannel
    ) -> None:
        balance = await self.bot.db.add_points(
            member.id,
            Economy.VOICE_REWARD,
            f"음성 활동 {Economy.VOICE_INTERVAL_MINUTES}분",
        )
        user = await self.bot.db.get_user(member.id)
        embed = base_embed(
            "🎧 음성 활동 포인트",
            Colors.TEAL,
            description=(
                f"{member.mention} 님이 음성 채널에 "
                f"**{Economy.VOICE_INTERVAL_MINUTES}분** 머물러 "
                f"**{fmt_points(Economy.VOICE_REWARD)}** 를 받았습니다."
            ),
        )
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.add_field(name="유저", value=user_field(member), inline=True)
        embed.add_field(name="음성 채널", value=channel.mention, inline=True)
        embed.add_field(name="현재 보유", value=fmt_points(balance), inline=True)
        embed.add_field(
            name="누적 음성 활동",
            value=fmt_duration(user.voice_seconds),
            inline=True,
        )
        await send_log(self.bot, Channels.VOICE_POINT_LOG, embed)

    @voice_tick.before_loop
    async def before_voice_tick(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """음성 채널을 완전히 떠나면 남은 자투리 시간은 버린다."""
        if after.channel is None and before.channel is not None:
            self._pending.pop(member.id, None)

    # ------------------------------------------------------------- 출석

    @app_commands.command(name="출석", description="하루 한 번 출석하고 포인트를 받습니다.")
    async def attendance(self, interaction: discord.Interaction) -> None:
        ok, streak, total = await self.bot.db.try_attendance(interaction.user.id)
        if not ok:
            balance = await self.bot.db.get_points(interaction.user.id)
            embed = base_embed(
                "📅 이미 출석했습니다",
                Colors.DARK_GOLD,
                description=(
                    "오늘은 이미 출석했어요. 내일 자정 이후에 다시 시도해 주세요.\n"
                    f"현재 보유: **{fmt_points(balance)}** · 연속 출석 **{streak}일**"
                ),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        balance = await self.bot.db.add_points(
            interaction.user.id, Economy.ATTENDANCE_REWARD, "출석 체크"
        )
        embed = base_embed(
            "✅ 출석 완료",
            Colors.SUCCESS,
            description=(
                f"**{fmt_points(Economy.ATTENDANCE_REWARD)}** 를 받았습니다!"
            ),
        )
        embed.set_author(
            name=str(interaction.user), icon_url=interaction.user.display_avatar.url
        )
        embed.add_field(name="현재 보유", value=fmt_points(balance), inline=True)
        embed.add_field(name="연속 출석", value=f"{streak}일", inline=True)
        embed.add_field(name="총 출석", value=f"{total}일", inline=True)
        await interaction.response.send_message(embed=embed)

        log_embed = base_embed(
            "📅 출석 체크",
            Colors.SUCCESS,
            description=f"{interaction.user.mention} 님이 출석했습니다.",
        )
        log_embed.set_author(
            name=str(interaction.user), icon_url=interaction.user.display_avatar.url
        )
        log_embed.add_field(name="유저", value=user_field(interaction.user), inline=True)
        log_embed.add_field(
            name="지급", value=f"+{fmt_points(Economy.ATTENDANCE_REWARD)}", inline=True
        )
        log_embed.add_field(name="현재 보유", value=fmt_points(balance), inline=True)
        log_embed.add_field(name="연속 출석", value=f"{streak}일", inline=True)
        log_embed.add_field(name="총 출석", value=f"{total}일", inline=True)
        await send_log(self.bot, Channels.POINT_LOG, log_embed)

    # ------------------------------------------------------------- 조회

    @app_commands.command(name="포인트", description="보유 포인트를 확인합니다.")
    @app_commands.describe(유저="확인할 유저 (비우면 본인)")
    async def points(
        self, interaction: discord.Interaction, 유저: discord.Member | None = None
    ) -> None:
        target = 유저 or interaction.user
        user = await self.bot.db.get_user(target.id)
        rank = await self.bot.db.point_rank(target.id)

        embed = base_embed(
            "💰 포인트",
            Colors.GOLD,
            description=f"{target.mention} 님의 포인트 정보입니다.",
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="보유 포인트", value=fmt_points(user.points), inline=True)
        embed.add_field(name="순위", value=f"{rank}위" if rank else "-", inline=True)
        embed.add_field(name="총 출석", value=f"{user.total_attendance}일", inline=True)
        embed.add_field(
            name="누적 음성 활동", value=fmt_duration(user.voice_seconds), inline=True
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="랭킹", description="포인트 상위 10명을 봅니다.")
    async def ranking(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.top_points(10)
        if not rows:
            await interaction.response.send_message(
                "아직 포인트를 가진 사람이 없습니다.", ephemeral=True
            )
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows):
            marker = medals[index] if index < 3 else f"`{index + 1}위`"
            lines.append(f"{marker} <@{row['user_id']}> — **{fmt_points(row['points'])}**")

        embed = base_embed(
            "🏆 포인트 랭킹",
            Colors.GOLD,
            description="\n".join(lines),
        )
        await interaction.response.send_message(embed=embed)

    # --------------------------------------------------------- 관리자 지급

    @app_commands.command(name="포인트지급", description="[관리자] 유저에게 포인트를 지급합니다.")
    @app_commands.describe(유저="지급할 대상", 포인트="지급할 포인트", 사유="지급 사유")
    @staff_only()
    async def give_points(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        포인트: app_commands.Range[int, 1, 1_000_000],
        사유: str = "관리자 지급",
    ) -> None:
        await self._adjust(interaction, 유저, 포인트, 사유)

    @app_commands.command(name="포인트차감", description="[관리자] 유저의 포인트를 차감합니다.")
    @app_commands.describe(유저="차감할 대상", 포인트="차감할 포인트", 사유="차감 사유")
    @staff_only()
    async def take_points(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        포인트: app_commands.Range[int, 1, 1_000_000],
        사유: str = "관리자 차감",
    ) -> None:
        await self._adjust(interaction, 유저, -포인트, 사유)

    async def _adjust(
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        delta: int,
        reason: str,
    ) -> None:
        if target.bot:
            await interaction.response.send_message(
                "봇에게는 포인트를 지급할 수 없습니다.", ephemeral=True
            )
            return

        before = await self.bot.db.get_points(target.id)
        balance = await self.bot.db.add_points(
            target.id, delta, reason, actor_id=interaction.user.id
        )
        applied = balance - before  # 잔액이 0에서 막히면 요청보다 적게 빠진다
        sign = "지급" if delta > 0 else "차감"
        color = Colors.SUCCESS if delta > 0 else Colors.DANGER

        note = ""
        if applied != delta:
            note = f"\n(보유가 {fmt_points(before)} 뿐이라 **{fmt_points(abs(applied))}** 만 차감했습니다.)"

        embed = base_embed(
            f"💰 포인트 {sign}",
            color,
            description=(
                f"{target.mention} 님에게 **{fmt_points(abs(applied))}** 를 {sign}했습니다.{note}"
            ),
        )
        embed.add_field(name="현재 보유", value=fmt_points(balance), inline=True)
        embed.add_field(name="사유", value=truncate(reason), inline=False)
        await interaction.response.send_message(embed=embed)

        log_embed = base_embed(f"💰 포인트 {sign}", color)
        log_embed.set_author(name=str(target), icon_url=target.display_avatar.url)
        log_embed.add_field(name="대상", value=user_field(target), inline=True)
        log_embed.add_field(name="처리자", value=user_field(interaction.user), inline=True)
        log_embed.add_field(
            name="변동", value=f"{applied:+,}{Economy.UNIT}", inline=True
        )
        log_embed.add_field(name="현재 보유", value=fmt_points(balance), inline=True)
        log_embed.add_field(name="사유", value=truncate(reason), inline=False)
        await send_log(self.bot, Channels.POINT_LOG, log_embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy_(bot))
