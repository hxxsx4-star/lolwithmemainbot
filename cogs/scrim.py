"""내전 시스템.

`/내전생성 [제목] [룰] [판수]` 로 내전 포럼에 모집 글을 올린다.
참가자 목록에는 `/등록` 으로 등록한 **롤닉네임#태그만** 표시된다.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors
from core.checks import can_host_scrim, scrim_host_only
from utils.logs import base_embed, truncate

log = logging.getLogger("mainbot.scrim")

FULL_ROSTER = 10  # 5대5

RULE_CHOICES = [
    app_commands.Choice(name="하드 피어리스", value="하드피어리스"),
    app_commands.Choice(name="피어리스 없음", value="피어리스없음"),
]

SERIES_CHOICES = [
    app_commands.Choice(name="3판 2선", value="3판2선"),
    app_commands.Choice(name="5판 3선", value="5판3선"),
    app_commands.Choice(name="죽을 때까지", value="죽을때까지"),
]

RULE_NOTE = {
    "하드피어리스": "한 번 나온 챔피언은 시리즈 내내 양 팀 모두 다시 못 씁니다.",
    "피어리스없음": "챔피언 제한 없이 매 판 자유롭게 픽합니다.",
}


class ScrimView(discord.ui.View):
    """내전 글에 붙는 참가 버튼."""

    def __init__(self, cog: "Scrim") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="참가", emoji="✅", style=discord.ButtonStyle.success,
        custom_id="scrim:join",
    )
    async def join(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_join(interaction)

    @discord.ui.button(
        label="참가 취소", emoji="↩️", style=discord.ButtonStyle.secondary,
        custom_id="scrim:leave",
    )
    async def leave(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_leave(interaction)

    @discord.ui.button(
        label="모집 마감", emoji="🔒", style=discord.ButtonStyle.danger,
        custom_id="scrim:close",
    )
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_close(interaction)


class Scrim(commands.Cog, name="Scrim"):
    """내전 생성과 참가 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(ScrimView(self))

    # ----------------------------------------------------------- 임베드 구성

    async def build_embed(self, thread_id: int, guild: discord.Guild) -> discord.Embed:
        """현재 참가자 목록을 반영한 내전 임베드."""
        scrim = await self.bot.db.get_scrim(thread_id)
        if scrim is None:
            return base_embed("내전 정보를 찾을 수 없습니다.", Colors.DANGER)

        member_ids = await self.bot.db.scrim_members(thread_id)
        closed = scrim["status"] != "open"

        embed = base_embed(
            f"⚔️ {scrim['title']}",
            Colors.DARK_GOLD if closed else Colors.GOLD,
            description=(
                "**모집이 마감되었습니다.**" if closed else "아래 버튼으로 참가/취소할 수 있습니다."
            ),
        )
        embed.add_field(name="룰", value=scrim["rule"], inline=True)
        embed.add_field(name="방식", value=scrim["series"], inline=True)
        embed.add_field(name="주최자", value=f"<@{scrim['host_id']}>", inline=True)
        embed.add_field(
            name="상세", value=RULE_NOTE.get(scrim["rule"], "​"), inline=False
        )

        # 참가자는 등록된 롤 닉네임#태그로만 표시한다
        lines: list[str] = []
        for index, user_id in enumerate(member_ids, start=1):
            user = await self.bot.db.get_user(user_id)
            riot_id = user.riot_id or "(등록 정보 없음)"
            lines.append(f"`{index:2d}.` **{riot_id}**")

        embed.add_field(
            name=f"참가자 ({len(member_ids)} / {FULL_ROSTER}명)",
            value=truncate("\n".join(lines)) if lines else "아직 참가자가 없습니다.",
            inline=False,
        )
        if len(member_ids) >= FULL_ROSTER and not closed:
            embed.set_footer(text="롤 같이 하자 · 인원이 모두 모였습니다!")
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        """버튼이 달린 원본 메시지를 다시 그린다."""
        thread = interaction.channel
        if not isinstance(thread, discord.Thread) or interaction.guild is None:
            return
        embed = await self.build_embed(thread.id, interaction.guild)
        scrim = await self.bot.db.get_scrim(thread.id)
        view = None if (scrim and scrim["status"] != "open") else ScrimView(self)
        try:
            await interaction.message.edit(embed=embed, view=view)
        except discord.HTTPException as exc:
            log.warning("내전 메시지 갱신 실패: %s", exc)

    # -------------------------------------------------------------- 명령어

    @app_commands.command(name="내전생성", description="내전 포럼에 모집 글을 올립니다.")
    @app_commands.describe(
        제목="내전 제목",
        룰="하드 피어리스 / 피어리스 없음",
        판수="3판 2선 / 5판 3선 / 죽을 때까지",
    )
    @app_commands.choices(룰=RULE_CHOICES, 판수=SERIES_CHOICES)
    @scrim_host_only()
    async def create_scrim(
        self,
        interaction: discord.Interaction,
        제목: app_commands.Range[str, 1, 90],
        룰: app_commands.Choice[str],
        판수: app_commands.Choice[str],
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        forum = guild.get_channel(Channels.SCRIM_FORUM)
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message(
                f"내전 포럼 채널(`{Channels.SCRIM_FORUM}`)을 찾을 수 없습니다. "
                "포럼 채널이 맞는지 확인해 주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        placeholder = base_embed(f"⚔️ {제목}", Colors.GOLD, description="내전을 준비하는 중…")
        try:
            created = await forum.create_thread(
                name=f"[{판수.value}] {제목}"[:100],
                embed=placeholder,
                view=ScrimView(self),
                reason=f"내전 생성 — {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "봇에게 포럼에 글을 올릴 권한이 없습니다.", ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"내전 생성 실패: `{exc}`", ephemeral=True)
            return

        thread, message = created.thread, created.message
        await self.bot.db.create_scrim(
            thread.id, guild.id, interaction.user.id, 제목, 룰.value, 판수.value, message.id
        )
        # 주최자는 등록되어 있으면 자동 참가
        host_user = await self.bot.db.get_user(interaction.user.id)
        if host_user.registered:
            await self.bot.db.join_scrim(thread.id, interaction.user.id)

        embed = await self.build_embed(thread.id, guild)
        try:
            await message.edit(embed=embed, view=ScrimView(self))
        except discord.HTTPException:
            pass

        await interaction.followup.send(
            f"✅ 내전을 생성했습니다 → {thread.mention}", ephemeral=True
        )

    # -------------------------------------------------------------- 버튼

    async def handle_join(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message(
                "내전 글에서만 사용할 수 있습니다.", ephemeral=True
            )
            return

        scrim = await self.bot.db.get_scrim(thread.id)
        if scrim is None:
            await interaction.response.send_message(
                "이 내전 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return
        if scrim["status"] != "open":
            await interaction.response.send_message(
                "이미 마감된 내전입니다.", ephemeral=True
            )
            return

        user = await self.bot.db.get_user(interaction.user.id)
        if not user.registered:
            await interaction.response.send_message(
                "내전은 **롤 계정을 등록한 사람만** 참가할 수 있습니다.\n"
                "`/등록 유저:@본인 롤닉네임:홍길동#KR1` 으로 먼저 등록해 주세요.",
                ephemeral=True,
            )
            return

        members = await self.bot.db.scrim_members(thread.id)
        if interaction.user.id not in members and len(members) >= FULL_ROSTER:
            await interaction.response.send_message(
                f"이미 인원({FULL_ROSTER}명)이 모두 찼습니다.", ephemeral=True
            )
            return

        joined = await self.bot.db.join_scrim(thread.id, interaction.user.id)
        if not joined:
            await interaction.response.send_message(
                "이미 참가 중입니다.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"✅ **{user.riot_id}** 로 참가했습니다.", ephemeral=True
        )
        await self.refresh(interaction)

    async def handle_leave(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return
        scrim = await self.bot.db.get_scrim(thread.id)
        if scrim is None:
            await interaction.response.send_message(
                "이 내전 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return

        left = await self.bot.db.leave_scrim(thread.id, interaction.user.id)
        if not left:
            await interaction.response.send_message(
                "참가 중이 아닙니다.", ephemeral=True
            )
            return

        await interaction.response.send_message("↩️ 참가를 취소했습니다.", ephemeral=True)
        await self.refresh(interaction)

    async def handle_close(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return
        scrim = await self.bot.db.get_scrim(thread.id)
        if scrim is None:
            await interaction.response.send_message(
                "이 내전 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return

        member = interaction.user
        allowed = int(scrim["host_id"]) == member.id or (
            isinstance(member, discord.Member) and can_host_scrim(member)
        )
        if not allowed:
            await interaction.response.send_message(
                "모집 마감은 **주최자 또는 내전 관리자**만 할 수 있습니다.", ephemeral=True
            )
            return
        if scrim["status"] != "open":
            await interaction.response.send_message(
                "이미 마감된 내전입니다.", ephemeral=True
            )
            return

        await self.bot.db.set_scrim_status(thread.id, "closed")
        await interaction.response.send_message("🔒 모집을 마감했습니다.", ephemeral=True)
        await self.refresh(interaction)

    @app_commands.command(name="내전참가자", description="현재 내전 참가자 목록을 봅니다.")
    async def participants(self, interaction: discord.Interaction) -> None:
        thread = interaction.channel
        if not isinstance(thread, discord.Thread) or interaction.guild is None:
            await interaction.response.send_message(
                "내전 글 안에서 사용해 주세요.", ephemeral=True
            )
            return
        scrim = await self.bot.db.get_scrim(thread.id)
        if scrim is None:
            await interaction.response.send_message(
                "이 스레드는 내전 글이 아닙니다.", ephemeral=True
            )
            return
        embed = await self.build_embed(thread.id, interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Scrim(bot))
