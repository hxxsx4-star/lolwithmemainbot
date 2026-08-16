"""이모지로 역할 고르기.

역할 선택 채널에 패널을 올리고, 거기 붙은 이모지를 누르면 역할을 지급한다.
다시 누르면 회수한다.

  · 주 라인 / 부 라인 — 패널당 **하나만** 고를 수 있다. 주 라인은 정의상
    하나뿐이고, 프로필 카드와 내전에서도 라인을 하나로 읽기 때문이다.
  · 즐겨 하는 게임 모드 — 여러 개를 동시에 고를 수 있다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from config import (
    Channels,
    Colors,
    GAME_MODE_EMOJIS,
    GAME_MODE_NAMES,
    LANE_EMOJIS,
    LANE_NAMES,
    Roles,
    Warning as WarnConfig,
)
from core.checks import staff_only
from utils.logs import base_embed
from utils.roles import role_problem

log = logging.getLogger("mainbot.reaction_roles")

CUSTOM_EMOJI = re.compile(r"^<a?:[A-Za-z0-9_~]+:(\d+)>$")


def emoji_key(spec: str) -> str:
    """패널에 적어 둔 이모지 표기를 비교용 키로 바꾼다.

    커스텀 이모지는 ID 로, 기본 이모지는 글자 그대로 쓴다. 반응 이벤트에서
    오는 값과 같은 형태로 맞추기 위한 것이다.
    """
    match = CUSTOM_EMOJI.match(spec.strip())
    return match.group(1) if match else spec.strip()


def payload_key(emoji: discord.PartialEmoji) -> str:
    """반응 이벤트의 이모지를 `emoji_key` 와 같은 형태로."""
    return str(emoji.id) if emoji.id is not None else (emoji.name or "")


@dataclass(frozen=True, slots=True)
class RoleOption:
    key: str        # 내부 식별자
    label: str      # 사람이 읽을 이름
    emoji: str      # 반응에 쓸 이모지 (커스텀 `<:이름:ID>` 또는 기본 이모지)
    role_id: int


@dataclass(frozen=True, slots=True)
class RolePanel:
    key: str
    title: str
    description: str
    color: int
    options: tuple[RoleOption, ...]
    exclusive: bool  # True 면 패널 안에서 하나만 고를 수 있다

    # 라인·모드 패널은 `/역할패널생성` 이 한꺼번에 올리지만, 내전 규칙처럼
    # 본문이 따로 있는 패널은 자기 명령어로 올린다. 그런 패널은 여기서
    # 제외해야 `/역할패널생성 전체` 가 엉뚱한 모양으로 덮어쓰지 않는다.
    standalone: bool = False

    def by_emoji(self, key: str) -> RoleOption | None:
        for option in self.options:
            if emoji_key(option.emoji) == key:
                return option
        return None


LANE_ORDER = ("TOP", "JG", "MID", "AD", "SUP")
GAME_MODE_ORDER = ("SOLO", "FLEX", "FIVE", "ARAM", "ARAM_AUG")

ONE_PICK_NOTE = (
    "**하나만** 고를 수 있고, 다른 걸 누르면 자동으로 바뀝니다.\n"
    "같은 이모지를 다시 누르면 역할이 해제됩니다."
)
MULTI_PICK_NOTE = (
    "**여러 개**를 함께 고를 수 있습니다.\n"
    "같은 이모지를 다시 누르면 역할이 해제됩니다."
)


def _lane_options(roles_map: dict[str, int]) -> tuple[RoleOption, ...]:
    return tuple(
        RoleOption(lane, LANE_NAMES[lane], LANE_EMOJIS[lane], roles_map[lane])
        for lane in LANE_ORDER
    )


PANELS: dict[str, RolePanel] = {
    "main_lane": RolePanel(
        key="main_lane",
        title="🎯 주 라인 선택",
        description="가장 자신 있는 라인의 이모지를 눌러 주세요.\n" + ONE_PICK_NOTE,
        color=Colors.GOLD,
        options=_lane_options(Roles.MAIN_LANES),
        exclusive=True,
    ),
    "sub_lane": RolePanel(
        key="sub_lane",
        title="🎲 부 라인 선택",
        description=(
            "주 라인 다음으로 자신 있는 라인의 이모지를 눌러 주세요.\n" + ONE_PICK_NOTE
        ),
        color=Colors.TEAL,
        options=_lane_options(Roles.SUB_LANES),
        exclusive=True,
    ),
    "game_mode": RolePanel(
        key="game_mode",
        title="🎮 즐겨 하는 게임 모드",
        description="평소에 자주 하는 모드를 골라 주세요.\n" + MULTI_PICK_NOTE,
        color=Colors.INFO,
        options=tuple(
            RoleOption(
                mode,
                GAME_MODE_NAMES[mode],
                GAME_MODE_EMOJIS[mode],
                Roles.GAME_MODES[mode],
            )
            for mode in GAME_MODE_ORDER
        ),
        exclusive=False,
    ),
    "scrim_rules": RolePanel(
        key="scrim_rules",
        title="⚔️ 내전 참가 규칙",
        description="규칙에 동의하면 내전 역할을 받습니다.",
        color=Colors.GOLD,
        options=(
            RoleOption("agree", "내전 참가", "✅", Roles.SCRIM_MEMBER),
        ),
        exclusive=False,
        standalone=True,
    ),
}


def scrim_rules_embed() -> discord.Embed:
    """내전 참가 규칙.

    적는 내용은 **이 봇이 실제로 하는 것**에 맞춘다. 없는 기능을 규칙에
    적어 두면 서버원이 기대했다가 안 돼서 문의가 늘어난다.
    """
    embed = base_embed(
        "⚔️ 내전 참가 규칙",
        Colors.GOLD,
        description="내전에 참여하기 전에 아래 내용을 꼭 읽어 주세요.",
    )
    embed.add_field(
        name="📋 참가 방법",
        value=(
            f"<@&{Roles.SCRIM_HOST}> 가 `/내전생성` 으로 모집 글을 올리면 "
            f"<#{Channels.SCRIM_FORUM}> 에 글이 생깁니다.\n"
            "글에 붙은 **참가 버튼**을 누르면 참가자 목록에 들어갑니다. "
            "**10명(5대5)** 이 모이면 팀을 나눕니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="🪪 계정 인증은 필수",
        value=(
            "참가자 목록에는 `/등록` 으로 연결한 **롤 닉네임#태그**가 표시됩니다.\n"
            "등록을 안 하면 `(등록 정보 없음)` 으로 뜨니 미리 해 주세요.\n"
            "· 인증된 **본인 계정으로만** 참여할 수 있습니다\n"
            "· 계정 공유 · 대리 · 타인 계정 사용은 금지입니다"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎮 진행 방식",
        value=(
            "· **하드 피어리스** — 한 번 나온 챔피언은 시리즈 내내 양 팀 모두 다시 못 씁니다\n"
            "· **피어리스 없음** — 챔피언 제한 없이 매 판 자유롭게 픽합니다\n"
            "· 판수는 3판 2선 · 5판 3선 · 죽을 때까지 중에서 정합니다\n"
            "· 같은 팀 안에서 **동일 챔피언 중복 선택은 불가**합니다"
        ),
        inline=False,
    )
    embed.add_field(
        name="📌 지켜 주세요",
        value=(
            "**1. 매너는 기본** — 욕설 · 정치질 · 비난 · 남탓 금지. 실수는 웃고 넘겨 주세요\n"
            "**2. 오더 존중** — 게임 중 오더는 최대한 따라 주시고, 의견은 정중하게\n"
            "**3. 트롤 금지** — 고의 트롤 · 잠수 · 던지기 · 탈주 금지. 끝까지 최선을\n"
            "**4. 팀 · 라인 변경 금지** — 설정 후 임의 변경 불가. 필요하면 관리자에게 문의\n"
            "**5. 디스코드 참여** — 내전 중 음성 참여는 필수입니다. "
            "듣기만 해도 되지만 웬만하면 마이크를 써 주세요\n"
            "**6. 인장 · 감정표현 금지** — 내전 중에는 사용하지 말아 주세요\n"
            "**7. 지각 · 잠수** — 호출 후 응답이 없으면 대기 또는 제외될 수 있습니다"
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ 제재",
        value=(
            "고의 트롤 · 무단 노쇼 · 불참 등은 **경고 대상**입니다.\n"
            f"**경고 {WarnConfig.BAN_THRESHOLD}회 누적 시 자동으로 서버에서 차단**됩니다. "
            "(`/경고목록` 으로 내 경고를 확인할 수 있습니다)\n"
            "내전 진행 중에는 관리진의 안내를 우선으로 따라 주세요. "
            "규칙 위반 시 경고 없이 내전 참여가 제한될 수 있습니다."
        ),
        inline=False,
    )
    embed.add_field(
        name="✅ 동의하기",
        value=(
            f"이 메시지에 **✅ 반응**을 누르면 <@&{Roles.SCRIM_MEMBER}> 역할이 "
            "지급되어 모집에 참여할 수 있습니다.\n"
            "-# 반응을 취소하면 역할도 함께 회수됩니다."
        ),
        inline=False,
    )
    embed.set_footer(text="롤 같이 하자 · 즐겁게 합시다")
    return embed


def panel_embed(panel: RolePanel) -> discord.Embed:
    embed = base_embed(panel.title, panel.color, description=panel.description)
    embed.add_field(
        name="목록",
        value="\n".join(
            f"{option.emoji}  **{option.label}** — <@&{option.role_id}>"
            for option in panel.options
        ),
        inline=False,
    )
    return embed


class ReactionRoles(commands.Cog, name="ReactionRoles"):
    """이모지 역할 선택 패널."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------ 패널 생성

    @app_commands.command(
        name="역할패널생성",
        description="[관리자] 주 라인 · 부 라인 · 게임 모드 선택 패널을 올립니다.",
    )
    @app_commands.describe(
        채널="패널을 올릴 채널 (비우면 기본 역할 선택 채널)",
        종류="특정 패널만 다시 올리고 싶을 때",
    )
    @app_commands.choices(
        종류=[
            app_commands.Choice(name="전체", value="all"),
            app_commands.Choice(name="주 라인", value="main_lane"),
            app_commands.Choice(name="부 라인", value="sub_lane"),
            app_commands.Choice(name="게임 모드", value="game_mode"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def create_panels(
        self,
        interaction: discord.Interaction,
        채널: discord.TextChannel | None = None,
        종류: app_commands.Choice[str] | None = None,
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

        wanted = (
            [panel for panel in PANELS.values() if not panel.standalone]
            if 종류 is None or 종류.value == "all"
            else [PANELS[종류.value]]
        )

        # 역할을 실제로 줄 수 있는지 먼저 확인한다 (조용한 실패 방지)
        problems = [
            f"· {panel.title} / {option.label} — {issue}"
            for panel in wanted
            for option in panel.options
            if (issue := role_problem(guild, option.role_id)) is not None
        ]
        if problems:
            await interaction.response.send_message(
                "⛔ 역할을 지급할 수 없는 상태입니다.\n\n" + "\n".join(problems[:6]),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        posted: list[str] = []
        for panel in wanted:
            try:
                message = await target.send(embed=panel_embed(panel))
                for option in panel.options:
                    await message.add_reaction(option.emoji)
            except discord.Forbidden:
                await interaction.followup.send(
                    f"{target.mention} 에 메시지를 보내거나 반응을 달 권한이 없습니다.",
                    ephemeral=True,
                )
                return
            except discord.HTTPException as exc:
                await interaction.followup.send(
                    f"{panel.title} 패널 생성 실패: `{exc}`\n"
                    "이모지가 이 서버에서 쓸 수 있는 것인지 확인해 주세요.",
                    ephemeral=True,
                )
                return

            await self.bot.db.set_panel(panel.key, guild.id, target.id, message.id)
            posted.append(f"{panel.title} → [바로가기]({message.jump_url})")

        await interaction.followup.send(
            "✅ 역할 선택 패널을 올렸습니다.\n"
            + "\n".join(posted)
            + "\n\n같은 종류의 예전 패널이 있다면 **삭제해 주세요.** 최신 패널만 반응합니다.",
            ephemeral=True,
        )

    # ------------------------------------------------------------ 내전 규칙

    @app_commands.command(
        name="내전규칙",
        description="[관리자] 내전 규칙과 동의 패널을 올립니다.",
    )
    @app_commands.describe(채널="규칙을 올릴 채널 (비우면 내전 규칙 채널)")
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def post_scrim_rules(
        self,
        interaction: discord.Interaction,
        채널: discord.TextChannel | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        target = 채널 or guild.get_channel(Channels.SCRIM_RULES)
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                f"내전 규칙 채널(`{Channels.SCRIM_RULES}`)을 찾을 수 없습니다.",
                ephemeral=True,
            )
            return

        panel = PANELS["scrim_rules"]
        # 동의해도 역할이 안 붙으면 아무도 원인을 모른다. 미리 확인한다
        issue = role_problem(guild, Roles.SCRIM_MEMBER)
        if issue is not None:
            await interaction.response.send_message(
                f"⛔ 내전 역할을 지급할 수 없는 상태입니다.\n{issue}", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            message = await target.send(embed=scrim_rules_embed())
            await message.add_reaction(panel.options[0].emoji)
        except discord.Forbidden:
            await interaction.followup.send(
                f"{target.mention} 에 메시지를 보내거나 반응을 달 권한이 없습니다.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"규칙 게시 실패: `{exc}`", ephemeral=True)
            return

        await self.bot.db.set_panel(panel.key, guild.id, target.id, message.id)
        await interaction.followup.send(
            f"✅ 내전 규칙을 올렸습니다. → [바로가기]({message.jump_url})\n"
            f"✅ 반응을 누르면 <@&{Roles.SCRIM_MEMBER}> 역할이 지급됩니다.\n\n"
            "예전 규칙 메시지가 있다면 **삭제해 주세요.** 최신 것만 반응합니다.",
            ephemeral=True,
        )

    # -------------------------------------------------------------- 반응 처리

    async def _resolve(
        self, payload: discord.RawReactionActionEvent
    ) -> tuple[RolePanel, RoleOption, discord.Guild] | None:
        """이 반응이 우리 패널의 것인지 확인하고 (패널, 항목, 길드) 를 돌려준다."""
        if payload.guild_id is None:
            return None
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return None

        key = await self.bot.db.panel_key_of(payload.message_id)
        if key is None:
            return None
        panel = PANELS.get(key)
        if panel is None:
            return None

        option = panel.by_emoji(payload_key(payload.emoji))
        if option is None:
            return None

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None
        return panel, option, guild

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        resolved = await self._resolve(payload)
        if resolved is None:
            return
        panel, option, guild = resolved

        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(option.role_id)
        if role is None:
            log.warning("역할(%s)을 찾을 수 없습니다.", option.role_id)
            return

        # 하나만 고르는 패널이면 같은 패널의 다른 역할은 정리한다
        others: list[discord.Role] = []
        if panel.exclusive:
            others = [
                other
                for candidate in panel.options
                if candidate.key != option.key
                and (other := guild.get_role(candidate.role_id)) is not None
                and other in member.roles
            ]

        try:
            if role not in member.roles:
                await member.add_roles(role, reason=f"{panel.title} 선택")
            if others:
                await member.remove_roles(*others, reason=f"{panel.title} 변경")
        except discord.Forbidden:
            log.warning("[%s] 역할을 변경할 권한이 없습니다.", member)
            return
        except discord.HTTPException as exc:
            log.warning("[%s] 역할 변경 실패: %s", member, exc)
            return

        if panel.exclusive:
            await self._clear_other_reactions(payload, panel, member, keep=option.key)

    async def _clear_other_reactions(
        self,
        payload: discord.RawReactionActionEvent,
        panel: RolePanel,
        member: discord.Member,
        *,
        keep: str,
    ) -> None:
        """방금 고른 것 말고 이 사람이 남긴 다른 반응을 지운다.

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
            emoji = reaction.emoji
            key = (
                str(emoji.id)
                if getattr(emoji, "id", None) is not None
                else str(emoji)
            )
            option = panel.by_emoji(key)
            if option is None or option.key == keep:
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
        panel, option, guild = resolved

        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        role = guild.get_role(option.role_id)
        if role is None or role not in member.roles:
            return

        try:
            await member.remove_roles(role, reason=f"{panel.title} 해제")
        except discord.Forbidden:
            log.warning("[%s] 역할을 회수할 권한이 없습니다.", member)
        except discord.HTTPException as exc:
            log.warning("[%s] 역할 회수 실패: %s", member, exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReactionRoles(bot))
