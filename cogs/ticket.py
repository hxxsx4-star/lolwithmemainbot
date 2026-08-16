"""티켓(문의함) 시스템.

문의 채널에 버튼 패널을 띄우고, 버튼을 누르면 본인과 스태프만 볼 수 있는
비공개 채널을 만들어 준다.

버튼 배치
  1행 (초록) : 서버 문의 · 티어 조정
  2행 (빨강) : 분쟁 및 유저 신고
  3행 (파랑) : 내전 문의 · 기타 문의
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors
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


TICKET_KINDS: tuple[TicketKind, ...] = (
    TicketKind(
        key="server",
        label="서버 문의",
        emoji="🏠",
        style=discord.ButtonStyle.success,
        row=0,
        prompt="서버 이용 중 궁금하거나 건의하고 싶은 점을 자세히 적어 주세요.",
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
    ),
    TicketKind(
        key="scrim",
        label="내전 문의",
        emoji="⚔️",
        style=discord.ButtonStyle.primary,
        row=2,
        prompt="내전 진행·참가·팀 배정 관련해서 궁금한 점을 적어 주세요.",
    ),
    TicketKind(
        key="etc",
        label="기타 문의",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        row=2,
        prompt="위 항목에 해당하지 않는 문의를 자유롭게 적어 주세요.",
    ),
)

KIND_BY_KEY: dict[str, TicketKind] = {k.key: k for k in TICKET_KINDS}


def panel_embed() -> discord.Embed:
    embed = base_embed(
        "📮 문의함",
        Colors.GOLD,
        description=(
            "문의하실 항목의 버튼을 눌러 주세요.\n"
            "본인과 스태프만 볼 수 있는 **비공개 채널**이 만들어집니다.\n\n"
            "· 장난성 문의는 제재 대상이 될 수 있습니다.\n"
            "· 답변까지 시간이 걸릴 수 있으니 조금만 기다려 주세요."
        ),
    )
    for kind in TICKET_KINDS:
        embed.add_field(
            name=f"{kind.emoji} {kind.label}", value=kind.prompt, inline=False
        )
    return embed


class TicketPanel(discord.ui.View):
    """항상 살아 있는 버튼 패널."""

    def __init__(self, cog: "TicketCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog
        for kind in TICKET_KINDS:
            self.add_item(TicketButton(kind))


class TicketButton(discord.ui.Button):
    def __init__(self, kind: TicketKind) -> None:
        super().__init__(
            label=kind.label,
            emoji=kind.emoji,
            style=kind.style,
            row=kind.row,
            custom_id=f"ticket:open:{kind.key}",
        )
        self.kind = kind

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: TicketCog = self.view.cog  # type: ignore[attr-defined]
        await cog.open_ticket(interaction, self.kind)


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

    @app_commands.command(name="문의함생성", description="[관리자] 문의함 버튼 패널을 올립니다.")
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

        await channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=TicketControls(self),
            allowed_mentions=discord.AllowedMentions(users=True),
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
                "기록을 남길 필요가 없다면 **채널 삭제** 버튼을 눌러 주세요. (스태프 전용)"
            ),
        )
        await interaction.followup.send(embed=embed)

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
