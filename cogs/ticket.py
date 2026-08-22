"""티켓(문의함) 시스템.

문의 채널에 선택 메뉴를 띄우고, 종류를 고르면 본인과 스태프만 볼 수 있는
비공개 채널을 만들어 준다.

버튼 다섯 개를 늘어놓는 대신 선택 메뉴 하나를 쓴다. 종류마다 설명 한 줄이
같이 보여서, 무엇을 고르는지 눌러 보기 전에 알 수 있다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors, Roles
from core.checks import is_staff, staff_only
from utils.logs import base_embed

log = logging.getLogger("mainbot.ticket")


@dataclass(frozen=True, slots=True)
class TicketKind:
    key: str
    label: str
    emoji: str
    style: discord.ButtonStyle
    row: int
    prompt: str

    # 선택 메뉴 한 줄에 붙는 설명. 디스코드가 100자까지만 받는다
    summary: str = ""

    # 이 종류만 따로 모아 둘 카테고리. 비우면 문의함 패널이 있는 곳에 만든다
    category_id: int | None = None

    # 티켓이 열릴 때 부를 역할. 담당자가 따로 있는 종류에 쓴다
    notify_role: int | None = None


TICKET_KINDS: tuple[TicketKind, ...] = (
    TicketKind(
        key="server",
        label="서버 문의",
        emoji="🏠",
        style=discord.ButtonStyle.success,
        row=0,
        prompt="서버 이용 중 궁금하거나 건의하고 싶은 점을 자세히 적어 주세요.",
        summary="서버 이용 문의 · 건의",
    ),
    TicketKind(
        key="tier",
        label="티어 조정",
        emoji="🏅",
        style=discord.ButtonStyle.success,
        row=0,
        prompt=(
            "티어 조정을 원하시면 **롤 닉네임#태그**와 **현재 티어**를 적고, "
            "전적 검색 링크나 인게임 프로필 사진을 함께 올려 주세요."
        ),
        summary="내 티어 역할을 실제 티어에 맞게 고치고 싶을 때",
        category_id=Channels.TIER_TICKETS,
        notify_role=Roles.TIER_REVIEWER,
    ),
    TicketKind(
        key="report",
        label="분쟁 및 유저 신고",
        emoji="🚨",
        style=discord.ButtonStyle.danger,
        row=1,
        prompt=(
            "**신고 대상**, **언제 있었던 일인지**, **어떤 일이 있었는지**를 적고 "
            "증거(스크린샷·영상)를 함께 올려 주세요."
        ),
        summary="유저 신고 · 분쟁 조정 (증거 필요)",
    ),
    TicketKind(
        key="scrim",
        label="내전 문의",
        emoji="⚔️",
        style=discord.ButtonStyle.primary,
        row=2,
        prompt="내전 진행·참가·팀 배정 관련해서 궁금한 점을 적어 주세요.",
        summary="내전 진행 · 참가 · 팀 배정 문의",
    ),
    TicketKind(
        key="etc",
        label="기타 문의",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        row=2,
        prompt="위 항목에 해당하지 않는 문의를 자유롭게 적어 주세요.",
        summary="위에 없는 그 밖의 문의",
    ),
)

KIND_BY_KEY: dict[str, TicketKind] = {k.key: k for k in TICKET_KINDS}


def panel_embed() -> discord.Embed:
    """문의함 안내.

    종류별 설명은 선택 메뉴 각 줄에 붙으므로 여기서 또 늘어놓지 않는다.
    같은 내용을 두 번 적으면 나중에 한쪽만 고쳐져 어긋난다.
    """
    return base_embed(
        "📮 문의함",
        Colors.GOLD,
        description=(
            "아래에서 문의 종류를 고르면 본인과 스태프만 볼 수 있는 "
            "**비공개 채널**이 만들어집니다.\n\n"
            "· 장난성 문의는 제재 대상이 될 수 있습니다.\n"
            "· 답변까지 시간이 걸릴 수 있으니 조금만 기다려 주세요."
        ),
    )


class TicketSelect(discord.ui.Select):
    """문의 종류 고르기.

    버튼 다섯 개를 세 줄로 늘어놓는 것보다, 한 줄짜리 선택 메뉴에 종류별
    설명을 붙이는 편이 읽기 쉽다. 무엇을 고르는지도 눌러 보기 전에 알 수 있다.
    """

    def __init__(self) -> None:
        super().__init__(
            placeholder="문의 종류를 선택해 주세요",
            min_values=1,
            max_values=1,
            custom_id="ticket:select",
            options=[
                discord.SelectOption(
                    label=kind.label,
                    value=kind.key,
                    description=kind.summary[:100] or None,
                    emoji=kind.emoji,
                )
                for kind in TICKET_KINDS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: TicketCog = self.view.cog  # type: ignore[attr-defined]
        kind = KIND_BY_KEY.get(self.values[0])
        if kind is None:
            await interaction.response.send_message(
                "알 수 없는 문의 종류입니다. 관리자에게 알려 주세요.", ephemeral=True
            )
            return
        await cog.open_ticket(interaction, kind)


class TicketPanel(discord.ui.View):
    """항상 살아 있는 문의함 패널."""

    def __init__(self, cog: "TicketCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(TicketSelect())


class TicketControls(discord.ui.View):
    """티켓 채널 안의 닫기 / 삭제 버튼."""

    def __init__(self, cog: "TicketCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="티켓 닫기", emoji="🔒", style=discord.ButtonStyle.secondary,
        custom_id="ticket:close",
    )
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.close_ticket(interaction)

    @discord.ui.button(
        label="다시 열기", emoji="🔓", style=discord.ButtonStyle.primary,
        custom_id="ticket:reopen",
    )
    async def reopen(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.reopen_ticket(interaction)

    @discord.ui.button(
        label="채널 삭제", emoji="🗑️", style=discord.ButtonStyle.danger,
        custom_id="ticket:delete",
    )
    async def delete(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.delete_ticket(interaction)


class TicketCog(commands.Cog, name="Ticket"):
    """문의 채널 생성과 정리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanel(self))
        self.bot.add_view(TicketControls(self))

    # -------------------------------------------------------------- 패널

    @app_commands.command(name="문의함생성", description="[관리자] 문의함 선택 패널을 올립니다.")
    @app_commands.describe(채널="패널을 올릴 채널 (비우면 기본 문의함 채널)")
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def post_panel(
        self,
        interaction: discord.Interaction,
        채널: discord.TextChannel | None = None,
    ) -> None:
        target = 채널 or interaction.guild.get_channel(Channels.TICKET_PANEL)
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                f"문의함 채널(`{Channels.TICKET_PANEL}`)을 찾을 수 없습니다.", ephemeral=True
            )
            return

        try:
            await target.send(embed=panel_embed(), view=TicketPanel(self))
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{target.mention} 에 메시지를 보낼 권한이 없습니다.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ {target.mention} 에 문의함 패널을 올렸습니다.", ephemeral=True
        )

    # -------------------------------------------------------------- 생성

    async def open_ticket(
        self, interaction: discord.Interaction, kind: TicketKind
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(ephemeral=True)

        existing = await self.bot.db.open_ticket_of(guild.id, interaction.user.id, kind.key)
        if existing is not None:
            channel = guild.get_channel(int(existing["channel_id"]))
            if channel is not None:
                await interaction.followup.send(
                    f"이미 열려 있는 **{kind.label}** 문의가 있습니다 → {channel.mention}",
                    ephemeral=True,
                )
                return
            # 채널이 이미 지워졌다면 기록만 정리하고 새로 만든다
            await self.bot.db.close_ticket(int(existing["channel_id"]), self.bot.user.id)

        number = await self.bot.db.next_counter("ticket")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True,
                manage_messages=True, embed_links=True, attach_files=True,
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True,
            ),
        }
        for role in guild.roles:
            if role.permissions.administrator or role.permissions.manage_guild:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    attach_files=True, embed_links=True,
                )

        # 담당 역할이 정해진 종류는 그 역할도 채널을 볼 수 있어야 한다
        if kind.notify_role:
            handler = guild.get_role(kind.notify_role)
            if handler is not None:
                overwrites[handler] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, read_message_history=True,
                    attach_files=True, embed_links=True,
                )

        # 종류별로 지정한 카테고리가 있으면 거기에, 없으면 문의함 패널 옆에
        category = None
        if kind.category_id:
            found = guild.get_channel(kind.category_id)
            if isinstance(found, discord.CategoryChannel):
                category = found
            else:
                log.warning(
                    "%s 카테고리(%s)를 찾지 못해 기본 위치에 만듭니다.",
                    kind.label, kind.category_id,
                )
        if category is None:
            panel_channel = guild.get_channel(Channels.TICKET_PANEL)
            category = getattr(panel_channel, "category", None)

        try:
            channel = await guild.create_text_channel(
                name=f"{kind.key}-{number:04d}",
                category=category,
                overwrites=overwrites,
                topic=f"{kind.label} · 문의자 {interaction.user} ({interaction.user.id})",
                reason=f"티켓 생성 — {kind.label} / {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "봇에게 **채널 관리 권한**이 없어 문의 채널을 만들지 못했습니다.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"문의 채널 생성 실패: `{exc}`", ephemeral=True)
            return

        await self.bot.db.create_ticket(
            channel.id, guild.id, interaction.user.id, kind.key, number
        )

        embed = base_embed(
            f"{kind.emoji} {kind.label} #{number:04d}",
            Colors.GOLD,
            description=(
                f"{interaction.user.mention} 님의 문의가 접수되었습니다.\n\n"
                f"{kind.prompt}\n\n"
                "내용을 남겨 주시면 스태프가 확인 후 답변드립니다.\n"
                "-# 문의를 닫는 것은 스태프가 합니다. 볼일이 끝나셨으면 알려 주세요."
            ),
        )
        embed.add_field(name="문의자", value=f"{interaction.user.mention}", inline=True)
        embed.add_field(name="유저 ID", value=f"`{interaction.user.id}`", inline=True)
        embed.add_field(name="종류", value=kind.label, inline=True)

        # 담당 역할이 있으면 같이 불러서 바로 확인하게 한다
        content = interaction.user.mention
        if kind.notify_role:
            content = f"<@&{kind.notify_role}> · {content}"

        await channel.send(
            content=content,
            embed=embed,
            view=TicketControls(self),
            allowed_mentions=discord.AllowedMentions(users=True, roles=True),
        )
        await interaction.followup.send(
            f"✅ 문의 채널이 만들어졌습니다 → {channel.mention}", ephemeral=True
        )

    # -------------------------------------------------------------- 닫기

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        ticket = await self.bot.db.get_ticket(channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "이 채널은 문의 채널이 아닙니다.", ephemeral=True
            )
            return
        if ticket["status"] == "closed":
            await interaction.response.send_message(
                "이미 닫힌 문의입니다.", ephemeral=True
            )
            return

        member = interaction.user
        opener_id = int(ticket["user_id"])
        # 문의자 본인은 닫을 수 없다. 답변을 받기 전에 실수로 닫아 버리면
        # 채널이 잠겨 이어서 물어볼 수 없게 되고, 스태프도 처리가 끝났는지
        # 아닌지 알 수 없다. 닫는 판단은 스태프가 한다.
        if not (isinstance(member, discord.Member) and is_staff(member)):
            await interaction.response.send_message(
                "문의는 **스태프만** 닫을 수 있습니다.\n"
                "볼일이 끝나셨으면 채팅으로 알려 주시면 스태프가 닫아 드립니다.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self.bot.db.close_ticket(channel.id, member.id)

        opener = interaction.guild.get_member(opener_id) if interaction.guild else None
        if opener is not None:
            try:
                await channel.set_permissions(
                    opener, view_channel=True, send_messages=False,
                    read_message_history=True,
                    reason="티켓 닫힘",
                )
            except discord.HTTPException:
                pass

        try:
            await channel.edit(name=f"닫힘-{channel.name}"[:100], reason="티켓 닫힘")
        except discord.HTTPException:
            pass

        embed = base_embed(
            "🔒 문의가 닫혔습니다",
            Colors.DARK_GOLD,
            description=(
                f"{member.mention} 님이 이 문의를 닫았습니다.\n"
                "이야기가 더 남았다면 **다시 열기**, 기록이 필요 없으면 "
                "**채널 삭제** 를 눌러 주세요. (둘 다 스태프 전용)"
            ),
        )
        await interaction.followup.send(embed=embed)

    async def reopen_ticket(self, interaction: discord.Interaction) -> None:
        """닫은 문의를 되돌린다. 닫기가 실수였거나 이야기가 더 남았을 때 쓴다."""
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        ticket = await self.bot.db.get_ticket(channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "이 채널은 문의 채널이 아닙니다.", ephemeral=True
            )
            return
        if ticket["status"] != "closed":
            await interaction.response.send_message(
                "이미 열려 있는 문의입니다.", ephemeral=True
            )
            return

        member = interaction.user
        if not (isinstance(member, discord.Member) and is_staff(member)):
            await interaction.response.send_message(
                "문의를 다시 여는 것도 **스태프만** 할 수 있습니다.", ephemeral=True
            )
            return

        await interaction.response.defer()
        await self.bot.db.reopen_ticket(channel.id)

        # 닫을 때 막아 둔 문의자의 발언권을 돌려준다
        opener_id = int(ticket["user_id"])
        opener = interaction.guild.get_member(opener_id) if interaction.guild else None
        if opener is not None:
            try:
                await channel.set_permissions(
                    opener, view_channel=True, send_messages=True,
                    read_message_history=True, attach_files=True, embed_links=True,
                    reason="티켓 다시 열림",
                )
            except discord.HTTPException:
                pass

        # 닫을 때 붙인 접두어를 뗀다
        if channel.name.startswith("닫힘-"):
            try:
                await channel.edit(
                    name=channel.name[len("닫힘-"):], reason="티켓 다시 열림"
                )
            except discord.HTTPException:
                pass

        embed = base_embed(
            "🔓 문의를 다시 열었습니다",
            Colors.SUCCESS,
            description=(
                f"{member.mention} 님이 이 문의를 다시 열었습니다.\n"
                f"<@{opener_id}> 님, 이어서 말씀해 주세요."
            ),
        )
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        log.info("티켓 다시 열림: %s (%s)", channel.name, member)

    async def delete_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return
        ticket = await self.bot.db.get_ticket(channel.id)
        if ticket is None:
            await interaction.response.send_message(
                "이 채널은 문의 채널이 아닙니다.", ephemeral=True
            )
            return
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_staff(member):
            await interaction.response.send_message(
                "채널 삭제는 **스태프만** 할 수 있습니다.", ephemeral=True
            )
            return

        await interaction.response.send_message("3초 뒤 이 채널을 삭제합니다.")
        await self.bot.db.close_ticket(channel.id, member.id)
        import asyncio

        await asyncio.sleep(3)
        try:
            await channel.delete(reason=f"티켓 삭제 — {member}")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """채널이 수동으로 지워져도 기록을 정리한다."""
        ticket = await self.bot.db.get_ticket(channel.id)
        if ticket is not None and ticket["status"] == "open":
            await self.bot.db.close_ticket(channel.id, self.bot.user.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))
