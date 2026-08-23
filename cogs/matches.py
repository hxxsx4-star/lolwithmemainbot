"""롤 전적 조회 (`/전적`).

계산과 캐시는 웹사이트와 **같은 코드**(`web/matches.py`)를 쓴다. 봇과 웹이
따로 구현하면 같은 경기에 다른 숫자가 나오고, 라이엇 호출도 두 배가 된다.
DB 를 공유하므로 웹에서 이미 받아 둔 매치는 여기서 다시 받지 않는다.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import Colors, Web
from utils.logs import base_embed
from utils.parsing import FormatError, split_riot_id
from web import matches

log = logging.getLogger("mainbot.matches")

GRADE_COLOR = {
    "하드캐리": Colors.GOLD,
    "잘함": Colors.TEAL,
    "제 몫": Colors.INFO,
    "아쉬움": Colors.DARK_GOLD,
}


def bar(pct: int, width: int = 10) -> str:
    """`▰▰▱▱▱▱▱▱▱▱` — 20% 기준선을 넘겼는지 보이게 한다."""
    filled = max(0, min(width, round(width * pct / 100)))
    return "▰" * filled + "▱" * (width - filled)


def games_embed(riot_id: str, games: list[dict], totals: dict, title: str):
    color = Colors.GOLD if totals["rate"] >= 50 else Colors.DARK_GOLD
    embed = base_embed(
        f"🎮 {riot_id}",
        color,
        description=(
            f"**{title}** — {totals['wins']}승 {totals['losses']}패 "
            f"({totals['rate']}%)\n"
            + (
                f"평균 **{totals['shares']}인분**"
                + (f" · 잘하고 진 판 {totals['unlucky']}회"
                   if totals["unlucky"] else "")
                + (f" · 묻어간 판 {totals['carried']}회"
                   if totals["carried"] else "")
                if totals["shares"] else ""
            )
        ),
    )

    for game in games[:8]:
        perf = game["perf"]
        mark = "🔵" if game["win"] else "🔴"
        name = f"{mark} {game['champion']} · {game['kda']}"
        if game["custom"]:
            name += "  [내전]"

        lines = []
        if perf:
            lines.append(
                f"**{perf['shares']}인분** ({perf['grade']}) · "
                f"분당 딜 {perf['dpm']:,}"
            )
            lines.append(
                f"딜 `{bar(perf['damage_pct'])}` {perf['damage_pct']}%  ·  "
                f"관여 {perf['kp_pct']}%"
            )
        else:
            lines.append(f"평점 {game['ratio']}")
        if game["luck"]:
            lines.append(f"-# 팀운 {game['luck']} — {game['luck_note']}")
        lines.append(f"-# {game['queue']} · {game['minutes']}분")

        embed.add_field(name=name, value="\n".join(lines), inline=False)

    if not games:
        embed.add_field(
            name="기록이 없습니다",
            value="최근 경기를 찾지 못했습니다.",
            inline=False,
        )
    embed.set_footer(text="롤 같이 하자 · 다섯이 고르게 하면 1인분입니다")
    return embed


class Matches(commands.Cog, name="Matches"):
    """롤 전적."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="전적", description="롤 전적과 몇 인분 했는지를 봅니다.")
    @app_commands.describe(
        유저="확인할 서버원 (비우면 본인)",
        롤닉네임="등록 안 된 계정을 직접 조회할 때 (`닉네임#태그`)",
        내전만="내전(커스텀)만 보기",
    )
    async def matches_cmd(
        self,
        interaction: discord.Interaction,
        유저: discord.Member | None = None,
        롤닉네임: str | None = None,
        내전만: bool = False,
    ) -> None:
        await interaction.response.defer()

        puuid: str | None = None
        riot_id = ""

        if 롤닉네임:
            try:
                name, tag = split_riot_id(롤닉네임)
            except FormatError as exc:
                await interaction.followup.send(f"❌ {exc}", ephemeral=True)
                return
            if not self.bot.riot.enabled:
                await interaction.followup.send(
                    "라이엇 API 키가 없어 조회할 수 없습니다.", ephemeral=True
                )
                return
            account = await self.bot.riot.fetch_account(name, tag)
            if account is None:
                await interaction.followup.send(
                    f"`{name}#{tag}` 계정을 찾을 수 없습니다.", ephemeral=True
                )
                return
            puuid, riot_id = account.puuid, f"{account.game_name}#{account.tag_line}"
        else:
            target = 유저 or interaction.user
            user = await self.bot.db.get_user(target.id)
            if not user.registered or not user.riot_puuid:
                who = "아직" if target == interaction.user else f"{target.mention} 님은"
                await interaction.followup.send(
                    f"{who} 롤 계정을 등록하지 않았습니다.\n"
                    "`/등록 유저:@본인 롤닉네임:홍길동#KR1` 으로 등록해 주세요.",
                    ephemeral=True,
                )
                return
            puuid, riot_id = user.riot_puuid, user.riot_id or ""

        # 웹과 같은 캐시를 쓴다. 이미 받아 둔 매치는 라이엇을 다시 안 부른다
        await matches.refresh(self.bot.db, puuid)
        games = await matches.recent(
            self.bot.db, puuid, Web.MATCH_COUNT, only_custom=내전만
        )
        totals = matches.totals(games)

        embed = games_embed(
            riot_id, games, totals, "내전 전적" if 내전만 else "최근 경기"
        )
        await interaction.followup.send(embed=embed)
        log.info("전적 조회: %s (%s)", riot_id, interaction.user)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Matches(bot))
