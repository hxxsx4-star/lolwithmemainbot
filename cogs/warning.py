"""경고 시스템.

- `/경고 [유저] [횟수] [사유]` : 경고 지급
- `/차감 [유저] [횟수] [사유]` : 경고 차감
- 누적 경고가 기준치(기본 3회) 이상이면 자동으로 서버 차단
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors, Warning as WarnConfig
from core.checks import moderator_only
from utils.logs import base_embed, send_log, truncate, user_field

log = logging.getLogger("mainbot.warning")


class WarningCog(commands.Cog, name="Warning"):
    """경고 지급 · 차감 · 자동 제재."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ 경고 지급

    @app_commands.command(name="경고", description="유저에게 경고를 지급합니다.")
    @app_commands.describe(유저="경고를 줄 대상", 횟수="지급할 경고 횟수", 사유="경고 사유")
    @moderator_only()
    async def warn(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        횟수: app_commands.Range[int, 1, 10],
        사유: str,
    ) -> None:
        if 유저.bot:
            await interaction.response.send_message(
                "봇에게는 경고를 줄 수 없습니다.", ephemeral=True
            )
            return
        if 유저.id == interaction.user.id:
            await interaction.response.send_message(
                "자기 자신에게는 경고를 줄 수 없습니다.", ephemeral=True
            )
            return

        await interaction.response.defer()

        total = await self.bot.db.add_warning(유저.id, interaction.user.id, 횟수, 사유)
        remaining = max(0, WarnConfig.BAN_THRESHOLD - total)

        embed = base_embed(
            "⚠️ 경고 지급",
            Colors.DANGER,
            description=(
                f"{유저.mention} 님에게 경고 **{횟수}회**가 지급되었습니다.\n"
                f"현재 누적 경고: **{total}회 / {WarnConfig.BAN_THRESHOLD}회**"
            ),
        )
        embed.set_thumbnail(url=유저.display_avatar.url)
        embed.add_field(name="사유", value=truncate(사유), inline=False)
        embed.add_field(name="처리자", value=interaction.user.mention, inline=True)
        if remaining:
            embed.add_field(
                name="차단까지",
                value=f"경고 **{remaining}회** 남음",
                inline=True,
            )
        await interaction.followup.send(embed=embed)

        log_embed = base_embed("⚠️ 경고 지급", Colors.DANGER)
        log_embed.set_author(name=str(유저), icon_url=유저.display_avatar.url)
        log_embed.add_field(name="대상", value=user_field(유저), inline=True)
        log_embed.add_field(name="처리자", value=user_field(interaction.user), inline=True)
        log_embed.add_field(name="지급 횟수", value=f"+{횟수}회", inline=True)
        log_embed.add_field(
            name="누적 경고",
            value=f"{total}회 / {WarnConfig.BAN_THRESHOLD}회",
            inline=True,
        )
        log_embed.add_field(
            name="채널",
            value=interaction.channel.mention if interaction.channel else "-",
            inline=True,
        )
        log_embed.add_field(name="사유", value=truncate(사유), inline=False)
        await send_log(self.bot, Channels.WARN_LOG, log_embed)

        if total >= WarnConfig.BAN_THRESHOLD:
            await self._auto_ban(interaction, 유저, total)

    async def _auto_ban(
        self, interaction: discord.Interaction, member: discord.Member, total: int
    ) -> None:
        """누적 경고 기준을 넘긴 유저를 자동으로 차단한다."""
        reasons = await self.bot.db.warning_reasons(member.id)
        summary = " / ".join(r.split(" · ", 1)[-1] for r in reasons) or "사유 미기재"
        ban_reason = f"[자동 제재] 누적 경고 {total}회 — {summary}"
        if len(ban_reason) > 500:
            ban_reason = ban_reason[:497] + "…"

        # 차단 전에 미리 알린다 (차단 후에는 DM 을 보낼 수 없다)
        try:
            dm = base_embed(
                "🔨 서버 차단 안내",
                Colors.DANGER,
                description=(
                    f"**{member.guild.name}** 서버에서 누적 경고 **{total}회**로 "
                    "차단되었습니다."
                ),
            )
            dm.add_field(name="경고 내역", value=truncate("\n".join(reasons)), inline=False)
            await member.send(embed=dm)
        except (discord.Forbidden, discord.HTTPException):
            pass

        failure: str | None = None
        try:
            await member.ban(
                reason=ban_reason,
                delete_message_days=WarnConfig.BAN_DELETE_MESSAGE_DAYS,
            )
        except discord.Forbidden:
            failure = (
                "봇에게 **멤버 차단 권한**이 없거나, 대상의 역할이 봇보다 높아 "
                "자동 차단에 실패했습니다."
            )
        except discord.HTTPException as exc:
            failure = f"자동 차단 중 오류가 발생했습니다: `{exc}`"

        if failure:
            log.warning("자동 차단 실패 (%s): %s", member, failure)
            embed = base_embed("❗ 자동 차단 실패", Colors.DANGER, description=failure)
            embed.add_field(name="대상", value=user_field(member), inline=True)
            embed.add_field(name="누적 경고", value=f"{total}회", inline=True)
            await interaction.followup.send(embed=embed)
            await send_log(self.bot, Channels.WARN_LOG, embed)
            return

        embed = base_embed(
            "🔨 누적 경고 자동 차단",
            Colors.DANGER,
            description=(
                f"**{member.display_name}** 님이 누적 경고 **{total}회**로 "
                "서버에서 자동 차단되었습니다."
            ),
        )
        embed.add_field(name="대상", value=user_field(member), inline=True)
        embed.add_field(name="기준", value=f"{WarnConfig.BAN_THRESHOLD}회 이상", inline=True)
        embed.add_field(name="종합 사유", value=truncate("\n".join(reasons)), inline=False)
        await interaction.followup.send(embed=embed)
        await send_log(self.bot, Channels.WARN_LOG, embed)

    # ------------------------------------------------------------ 경고 차감

    @app_commands.command(name="차감", description="유저의 경고를 차감합니다.")
    @app_commands.describe(유저="경고를 뺄 대상", 횟수="차감할 경고 횟수", 사유="차감 사유")
    @moderator_only()
    async def unwarn(
        self,
        interaction: discord.Interaction,
        유저: discord.Member,
        횟수: app_commands.Range[int, 1, 10],
        사유: str,
    ) -> None:
        current = await self.bot.db.warning_count(유저.id)
        if current <= 0:
            await interaction.response.send_message(
                f"{유저.mention} 님은 차감할 경고가 없습니다.", ephemeral=True
            )
            return

        amount = min(횟수, current)
        total = await self.bot.db.add_warning(
            유저.id, interaction.user.id, -amount, 사유
        )

        note = ""
        if amount != 횟수:
            note = f"\n(보유 경고가 {current}회라 **{amount}회**만 차감했습니다.)"

        embed = base_embed(
            "🩹 경고 차감",
            Colors.SUCCESS,
            description=(
                f"{유저.mention} 님의 경고 **{amount}회**를 차감했습니다.\n"
                f"현재 누적 경고: **{total}회 / {WarnConfig.BAN_THRESHOLD}회**{note}"
            ),
        )
        embed.set_thumbnail(url=유저.display_avatar.url)
        embed.add_field(name="사유", value=truncate(사유), inline=False)
        embed.add_field(name="처리자", value=interaction.user.mention, inline=True)
        await interaction.response.send_message(embed=embed)

        log_embed = base_embed("🩹 경고 차감", Colors.SUCCESS)
        log_embed.set_author(name=str(유저), icon_url=유저.display_avatar.url)
        log_embed.add_field(name="대상", value=user_field(유저), inline=True)
        log_embed.add_field(name="처리자", value=user_field(interaction.user), inline=True)
        log_embed.add_field(name="차감 횟수", value=f"-{amount}회", inline=True)
        log_embed.add_field(
            name="차감 전 / 후", value=f"{current}회 → {total}회", inline=True
        )
        log_embed.add_field(name="사유", value=truncate(사유), inline=False)
        await send_log(self.bot, Channels.WARN_REMOVE_LOG, log_embed)

    # ------------------------------------------------------------ 경고 조회

    @app_commands.command(name="경고목록", description="유저의 경고 내역을 확인합니다.")
    @app_commands.describe(유저="확인할 유저 (비우면 본인)")
    async def warn_list(
        self, interaction: discord.Interaction, 유저: discord.Member | None = None
    ) -> None:
        target = 유저 or interaction.user
        # 남의 경고 내역은 관리자만 볼 수 있다
        if target.id != interaction.user.id:
            from core.checks import can_moderate

            if not isinstance(interaction.user, discord.Member) or not can_moderate(
                interaction.user
            ):
                await interaction.response.send_message(
                    "다른 사람의 경고 내역은 관리자만 볼 수 있습니다.", ephemeral=True
                )
                return

        total = await self.bot.db.warning_count(target.id)
        rows = await self.bot.db.warning_history(target.id, limit=15)

        embed = base_embed(
            "📋 경고 내역",
            Colors.DANGER if total else Colors.GOLD,
            description=(
                f"{target.mention} 님의 누적 경고: "
                f"**{total}회 / {WarnConfig.BAN_THRESHOLD}회**"
            ),
        )
        embed.set_thumbnail(url=target.display_avatar.url)

        if not rows:
            embed.add_field(name="내역", value="기록이 없습니다.", inline=False)
        else:
            lines = []
            for row in rows:
                mark = "⚠️ 지급" if row["amount"] > 0 else "🩹 차감"
                lines.append(
                    f"{mark} **{abs(row['amount'])}회** · <@{row['actor_id']}>\n"
                    f"`{row['created_at'][:16].replace('T', ' ')}` — {row['reason']}"
                )
            embed.add_field(
                name=f"최근 내역 ({len(rows)}건)",
                value=truncate("\n\n".join(lines)),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WarningCog(bot))
