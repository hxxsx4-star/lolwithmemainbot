"""이모지로 라인 역할 고르기.

역할 선택 채널에 **주 라인**과 **부 라인** 패널을 하나씩 올리고, 거기에 붙은
이모지를 누르면 해당 역할을 지급한다. 다시 누르면 회수한다.

한 패널 안에서는 하나만 고를 수 있다. 주 라인은 정의상 하나뿐이고, 프로필
카드와 내전에서도 라인을 하나로 읽기 때문이다. 새로 고르면 이전 선택은
역할과 이모지 모두 자동으로 정리된다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    Channels,
    Colors,
    LANE_BY_EMOJI_ID,
    LANE_EMOJIS,
    LANE_NAMES,
    Roles,
)
from core.checks import staff_only
from utils.logs import base_embed
from utils.roles import role_problem

log = logging.getLogger("mainbot.reaction_roles")

# 패널에 이모지를 붙이는 순서
LANE_ORDER = ("TOP", "JG", "MID", "AD", "SUP")


@dataclass(frozen=True, slots=True)
class LanePanel:
    key: str
    title: str
    description: str
    roles: dict[str, int]
    color: int


PANELS: dict[str, LanePanel] = {
    "main_lane": LanePanel(
        key="main_lane",
        title="🎯 주 라인 선택",
        description=(
            "가장 자신 있는 라인의 이모지를 눌러 주세요.\n"
            "**하나만** 고를 수 있고, 다른 라인을 누르면 자동으로 바뀝니다.\n"
            "같은 이모지를 다시 누르면 역할이 해제됩니다."
        ),
        roles=Roles.MAIN_LANES,
        color=Colors.GOLD,
    ),
    "sub_lane": LanePanel(
        key="sub_lane",
        title="🎲 부 라인 선택",
        description=(
            "주 라인 다음으로 자신 있는 라인의 이모지를 눌러 주세요.\n"
            "**하나만** 고를 수 있고, 다른 라인을 누르면 자동으로 바뀝니다.\n"
            "같은 이모지를 다시 누르면 역할이 해제됩니다."
        ),
        roles=Roles.SUB_LANES,
        color=Colors.TEAL,
    ),
}


def panel_embed(panel: LanePanel) -> discord.Embed:
    embed = base_embed(panel.title, panel.color, description=panel.description)
    embed.add_field(
        name="라인",
        value="\n".join(
            f"{LANE_EMOJIS[lane]}  **{LANE_NAMES[lane]}** — <@&{panel.roles[lane]}>"
            for lane in LANE_ORDER
        ),
        inline=False,
    )
    return embed


class ReactionRoles(commands.Cog, name="ReactionRoles"):
    """라인 역할 선택 패널."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ 패널 생성

    @app_commands.command(
        name="라인패널생성",
        description="[관리자] 주 라인 · 부 라인 선택 패널을 올립니다.",
    )
    @app_commands.describe(채널="패널을 올릴 채널 (비우면 기본 역할 선택 채널)")
    @staff_only()
    async def create_panels(
        self,
        interaction: discord.Interaction,
        채널: discord.TextChannel | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        target = 채널 or guild.get_channel(Channels.ROLE_PICKER)
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                f"역할 선택 채널(`{Channels.ROLE_PICKER}`)을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        # 역할을 실제로 줄 수 있는지 먼저 확인한다 (조용한 실패 방지)
        problems: list[str] = []
        for panel in PANELS.values():
            for lane in LANE_ORDER:
                issue = role_problem(guild, panel.roles[lane])
                if issue is not None:
                    problems.append(f"· {panel.title} / {LANE_NAMES[lane]} — {issue}")
        if problems:
            await interaction.response.send_message(
                "⛔ 라인 역할을 지급할 수 없는 상태입니다.\n\n" + "\n".join(problems[:6]),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        posted: list[str] = []
        for panel in PANELS.values():
            try:
                message = await target.send(embed=panel_embed(panel))
                for lane in LANE_ORDER:
                    await message.add_reaction(LANE_EMOJIS[lane])
            except discord.Forbidden:
                await interaction.followup.send(
                    f"{target.mention} 에 메시지를 보내거나 반응을 달 권한이 없습니다.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"{panel.title} 패널 생성 실패: `{exc}`\n"
                    "이모지 ID 가 이 서버의 것이 맞는지 확인해 주세요.",
                    ephemeral=True,
                )
                return

            await self.bot.db.set_panel(panel.key, guild.id, target.id, message.id)
            posted.append(f"{panel.title} → [바로가기]({message.jump_url})")

        await interaction.followup.send(
            "✅ 라인 선택 패널을 올렸습니다.\n"
            + "\n".join(posted)
            + "\n\n이전에 올린 패널이 있다면 **삭제해 주세요.** 최신 패널만 반응합니다.",
            ephemeral=True,
        )

    # -------------------------------------------------------------- 반응 처리

    async def _resolve(
        self, payload: discord.RawReactionActionEvent
    ) -> tuple[LanePanel, str, discord.Guild] | None:
        """이 반응이 라인 패널의 것인지 확인하고 (패널, 라인, 길드) 를 돌려준다."""
        if payload.guild_id is None:
            return None
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return None
        if payload.emoji.id is None:
            return None  # 기본 이모지는 우리 패널의 것이 아니다

        lane = LANE_BY_EMOJI_ID.get(payload.emoji.id)
        if lane is None:
            return None

        key = await self.bot.db.panel_key_of(payload.message_id)
        if key is None:
            return None
        panel = PANELS.get(key)
        if panel is None:
            return None

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None
        return panel, lane, guild

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        resolved = await self._resolve(payload)
        if resolved is None:
            return
        panel, lane, guild = resolved

        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(panel.roles[lane])
        if role is None:
            log.warning("라인 역할(%s)을 찾을 수 없습니다.", panel.roles[lane])
            return

        # 같은 패널의 다른 라인 역할은 정리한다 (라인은 하나만)
        others = [
            other
            for code, role_id in panel.roles.items()
            if code != lane and (other := guild.get_role(role_id)) is not None
            and other in member.roles
        ]

        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"{panel.title} 선택")
            if others:
                await member.remove_roles(*others, reason=f"{panel.title} 변경")
        except discord.Forbidden:
            log.warning("[%s] 라인 역할을 변경할 권한이 없습니다.", member)
            return
        except discord.HTTPException as exc:
            log.warning("[%s] 라인 역할 변경 실패: %s", member, exc)
            return

        await self._clear_other_reactions(payload, member, lane)

    async def _clear_other_reactions(
        self,
        payload: discord.RawReactionActionEvent,
        member: discord.Member,
        keep: str,
    ) -> None:
        """방금 고른 것 말고 이 사람이 남긴 다른 라인 반응을 지운다.

        이렇게 해야 패널에서도 '하나만 골랐다'는 게 눈으로 보인다.
        """
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        for reaction in message.reactions:
            emoji_id = getattr(reaction.emoji, "id", None)
            if emoji_id is None:
                continue
            code = LANE_BY_EMOJI_ID.get(emoji_id)
            if code is None or code == keep:
                continue
            try:
                await reaction.remove(member)
            except (discord.Forbidden, discord.HTTPException):
                # 반응을 못 지워도 역할은 이미 정리됐으니 넘어간다
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        resolved = await self._resolve(payload)
        if resolved is None:
            return
        panel, lane, guild = resolved

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(panel.roles[lane])
        if role is None or role not in member.roles:
            return

        try:
            await member.remove_roles(role, reason=f"{panel.title} 해제")
        except discord.Forbidden:
            log.warning("[%s] 라인 역할을 회수할 권한이 없습니다.", member)
        except discord.HTTPException as exc:
            log.warning("[%s] 라인 역할 회수 실패: %s", member, exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
