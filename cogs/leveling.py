"""채팅 레벨.

메시지를 보낼 때마다 채팅 경험치가 쌓인다. 도배로 레벨을 올리지 못하도록
유저별 쿨타임과 최소 글자 수를 둔다. (음성 경험치는 `cogs/economy.py` 의
음성 틱에서 자동으로 쌓인다.)
"""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from config import Colors, GUILD_ID, Level
from core.db import level_progress
from utils.logs import base_embed

log = logging.getLogger("mainbot.leveling")


class Leveling(commands.Cog, name="Leveling"):
    """채팅 경험치 적립과 레벨 조회."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_award: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if GUILD_ID is not None and message.guild.id != GUILD_ID:
            return
        if len(message.content.strip()) < Level.CHAT_MIN_LENGTH:
            return

        now = time.monotonic()
        last = self._last_award.get(message.author.id, 0.0)
        if now - last < Level.CHAT_COOLDOWN_SECONDS:
            return
        self._last_award[message.author.id] = now

        before, after = await self.bot.db.add_xp(
            message.author.id, "chat_xp", Level.CHAT_XP_PER_MESSAGE
        )
        if after > before:
            log.info("[%s] 채팅 레벨 %d → %d", message.author, before, after)

    # -------------------------------------------------------------- 명령어

    @app_commands.command(name="레벨", description="음성 · 채팅 레벨을 확인합니다.")
    @app_commands.describe(유저="확인할 유저 (비우면 본인)")
    async def level(
        self, interaction: discord.Interaction, 유저: discord.Member | None = None
    ) -> None:
        target = 유저 or interaction.user
        user = await self.bot.db.get_user(target.id)

        voice_level, voice_now, voice_need = user.voice_level
        chat_level, chat_now, chat_need = user.chat_level
        voice_rank = await self.bot.db.xp_rank(target.id, "voice_xp")
        chat_rank = await self.bot.db.xp_rank(target.id, "chat_xp")

        embed = base_embed(
            "📈 레벨",
            Colors.GOLD,
            description=f"{target.mention} 님의 레벨 정보입니다.",
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="🎧 음성 레벨",
            value=(
                f"**Lv. {voice_level}**  ·  {voice_rank}위\n"
                f"{voice_now:,} / {voice_need:,} XP  (누적 {user.voice_xp:,})"
            ),
            inline=False,
        )
        embed.add_field(
            name="💬 채팅 레벨",
            value=(
                f"**Lv. {chat_level}**  ·  {chat_rank}위\n"
                f"{chat_now:,} / {chat_need:,} XP  (누적 {user.chat_xp:,})"
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"음성 분당 {Level.VOICE_XP_PER_MINUTE}XP · "
                f"채팅 {Level.CHAT_XP_PER_MESSAGE}XP({Level.CHAT_COOLDOWN_SECONDS}초 쿨타임)"
            )
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="레벨랭킹", description="음성 · 채팅 레벨 상위 10명을 봅니다.")
    @app_commands.describe(종류="음성 / 채팅")
    @app_commands.choices(
        종류=[
            app_commands.Choice(name="음성", value="voice_xp"),
            app_commands.Choice(name="채팅", value="chat_xp"),
        ]
    )
    async def level_ranking(
        self,
        interaction: discord.Interaction,
        종류: app_commands.Choice[str] | None = None,
    ) -> None:
        column = 종류.value if 종류 else "voice_xp"
        title = "🎧 음성 레벨 랭킹" if column == "voice_xp" else "💬 채팅 레벨 랭킹"

        rows = await self.bot.db.top_xp(column, 10)
        if not rows:
            await interaction.response.send_message(
                "아직 경험치를 쌓은 사람이 없습니다.", ephemeral=True
            )
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, row in enumerate(rows):
            marker = medals[index] if index < 3 else f"`{index + 1}위`"
            lvl, _, _ = level_progress(int(row["xp"]))
            lines.append(
                f"{marker} <@{row['user_id']}> — **Lv. {lvl}** ({row['xp']:,} XP)"
            )

        embed = base_embed(title, Colors.GOLD, description="\n".join(lines))
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))
