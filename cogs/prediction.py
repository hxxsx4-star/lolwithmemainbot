"""프로 경기 승부예측.

경기 시작 **24시간 전**에 예측 패널을 자동으로 올리고, 경기가 끝나면 결과를
받아 맞힌 사람에게 포인트를 준다. LCK · MSI · Worlds · EWC 는 lolesports
공개 API 에서 일정을 그대로 가져오고, 그 API 에 없는 대회(아시안게임 등)는
`/경기추가` 로 직접 넣는다.

투표는 경기 시작 시각에 닫힌다. 틀려도 잃는 것은 없고 맞히면 포인트를 받는다.
현황을 패널에 계속 띄우면 표가 한쪽으로 쏠려서, 현황은 버튼으로 따로 본다.
"""
from __future__ import annotations

import datetime as dt
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    Channels,
    Colors,
    Economy,
    LEAGUE_NAMES,
    Prediction as Config,
    TIMEZONE,
)
from core.checks import staff_only
from utils.esports import EsportsError, Match
from utils.logs import base_embed, send_log

log = logging.getLogger("mainbot.prediction")

PICK_A = "A"
PICK_B = "B"

STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_RESOLVED = "resolved"
STATE_CANCELLED = "cancelled"

TIME_FORMAT = "%Y-%m-%d %H:%M"


def league_label(name: str, slug: str = "") -> str:
    """대회 이름을 한국어 표기로. 모르는 대회는 API 이름 그대로 쓴다."""
    return LEAGUE_NAMES.get(slug.lower()) or LEAGUE_NAMES.get(name.lower()) or name


def parse_utc(raw: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment


def bar(count: int, total: int, width: int = 12) -> str:
    """`████░░░░░░░░ 33%` 형태의 막대."""
    if total <= 0:
        return "░" * width + " 0%"
    filled = round(width * count / total)
    percent = round(100 * count / total)
    return "█" * filled + "░" * (width - filled) + f" {percent}%"


class PredictionView(discord.ui.View):
    """예측 패널 버튼. 어느 경기인지는 메시지 ID 로 찾는다.

    custom_id 를 고정해 두면 봇을 재시작해도 예전 패널이 그대로 동작한다.
    """

    def __init__(self, cog: "PredictionCog", *, labels: tuple[str, str] | None = None,
                 disabled: bool = False) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        if labels is not None:
            self.pick_a.label = labels[0][:80]
            self.pick_b.label = labels[1][:80]
        if disabled:
            self.pick_a.disabled = True
            self.pick_b.disabled = True

    @discord.ui.button(
        label="팀 A", style=discord.ButtonStyle.primary, custom_id="predict:A"
    )
    async def pick_a(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_vote(interaction, PICK_A)

    @discord.ui.button(
        label="팀 B", style=discord.ButtonStyle.danger, custom_id="predict:B"
    )
    async def pick_b(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_vote(interaction, PICK_B)

    @discord.ui.button(
        label="현황", emoji="📊", style=discord.ButtonStyle.secondary,
        custom_id="predict:stats",
    )
    async def stats(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handle_stats(interaction)


class PredictionCog(commands.Cog, name="Prediction"):
    """승부예측 등록 · 투표 · 정산."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._league_ids: list[str] = []

    async def cog_load(self) -> None:
        self.bot.add_view(PredictionView(self))
        self.sync_matches.start()

    async def cog_unload(self) -> None:
        self.sync_matches.cancel()

    # ------------------------------------------------------------- 패널

    async def build_embed(self, row) -> discord.Embed:
        """예측 패널 임베드. 상태에 따라 색과 내용이 달라진다."""
        state = str(row["state"])
        start = parse_utc(str(row["start_at"]))
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        color = {
            STATE_OPEN: Colors.GOLD,
            STATE_CLOSED: Colors.DARK_GOLD,
            STATE_RESOLVED: Colors.SUCCESS,
            STATE_CANCELLED: Colors.DANGER,
        }.get(state, Colors.GOLD)

        block = str(row["block"] or "")
        title = f"🎯 {row['league']}"
        if block:
            title += f" · {block}"

        embed = base_embed(
            title,
            color,
            description=f"### {team_a}  vs  {team_b}",
        )
        embed.add_field(
            name="경기 시작",
            value=(
                f"{discord.utils.format_dt(start, 'F')}\n"
                f"{discord.utils.format_dt(start, 'R')}"
            ),
            inline=True,
        )
        embed.add_field(name="방식", value=f"BO{row['best_of']}", inline=True)

        if state == STATE_OPEN:
            embed.add_field(
                name="보상",
                value=(
                    f"맞히면 **{Config.CORRECT_REWARD:,}{Economy.UNIT}**\n"
                    "틀려도 잃지 않습니다"
                ),
                inline=True,
            )
            embed.add_field(
                name="투표",
                value=(
                    "아래에서 이길 팀을 골라 주세요. "
                    "경기 시작 전까지 몇 번이든 바꿀 수 있습니다.\n"
                    "표가 쏠리지 않도록 현재 득표는 **📊 현황** 으로만 볼 수 있습니다."
                ),
                inline=False,
            )
            embed.set_footer(text="롤 같이 하자 · 경기 시작과 함께 투표가 닫힙니다")
            return embed

        # 마감 이후에는 득표를 공개한다
        votes_a, votes_b = await self.bot.db.vote_counts(str(row["match_id"]))
        total = votes_a + votes_b
        embed.add_field(
            name=f"투표 결과 ({total}명)",
            value=(
                f"**{team_a}** `{bar(votes_a, total)}` {votes_a}표\n"
                f"**{team_b}** `{bar(votes_b, total)}` {votes_b}표"
            ),
            inline=False,
        )

        if state == STATE_RESOLVED and row["winner"]:
            winner_name = team_a if row["winner"] == PICK_A else team_b
            hit = votes_a if row["winner"] == PICK_A else votes_b
            embed.add_field(
                name="🏆 승리",
                value=(
                    f"**{winner_name}**\n"
                    f"{hit}명 적중 · 각 {Config.CORRECT_REWARD:,}{Economy.UNIT} 지급"
                ),
                inline=False,
            )
            embed.set_footer(text="롤 같이 하자 · 정산 완료")
        elif state == STATE_CANCELLED:
            embed.add_field(
                name="취소됨", value="이 경기는 취소되었습니다.", inline=False
            )
            embed.set_footer(text="롤 같이 하자")
        else:
            embed.set_footer(text="롤 같이 하자 · 투표 마감")
        return embed

    async def refresh_panel(self, row) -> None:
        """패널 메시지를 현재 상태에 맞게 다시 그린다."""
        channel_id, message_id = row["channel_id"], row["message_id"]
        if not channel_id or not message_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.HTTPException:
            log.warning("예측 패널 메시지를 찾지 못했습니다: %s", message_id)
            return

        closed = str(row["state"]) != STATE_OPEN
        view = PredictionView(
            self,
            labels=(str(row["team_a"]), str(row["team_b"])),
            disabled=closed,
        )
        if closed:
            view.stats.disabled = True
        try:
            await message.edit(embed=await self.build_embed(row), view=view)
        except discord.HTTPException as exc:
            log.warning("예측 패널 갱신 실패: %s", exc)

    async def post_panel(self, row) -> bool:
        """예측 패널을 채널에 올리고 메시지 ID 를 저장한다."""
        channel = self.bot.get_channel(Channels.PREDICTION)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(Channels.PREDICTION)
            except discord.HTTPException:
                log.warning("승부예측 채널(%s)을 찾지 못했습니다.", Channels.PREDICTION)
                return False
        if not isinstance(channel, discord.abc.Messageable):
            return False

        view = PredictionView(self, labels=(str(row["team_a"]), str(row["team_b"])))
        try:
            message = await channel.send(embed=await self.build_embed(row), view=view)
        except discord.HTTPException as exc:
            log.warning("예측 패널 등록 실패: %s", exc)
            return False

        await self.bot.db.set_prediction_message(
            str(row["match_id"]), channel.id, message.id
        )
        return True

    # ------------------------------------------------------------- 버튼

    async def _lookup(self, interaction: discord.Interaction):
        if interaction.message is None:
            return None
        return await self.bot.db.prediction_by_message(interaction.message.id)

    async def handle_vote(self, interaction: discord.Interaction, pick: str) -> None:
        row = await self._lookup(interaction)
        if row is None:
            await interaction.response.send_message(
                "이 예측 정보를 찾을 수 없습니다. 관리자에게 알려 주세요.", ephemeral=True
            )
            return
        if str(row["state"]) != STATE_OPEN:
            await interaction.response.send_message(
                "이미 투표가 마감된 경기입니다.", ephemeral=True
            )
            return

        team = str(row["team_a"]) if pick == PICK_A else str(row["team_b"])
        before = await self.bot.db.cast_vote(
            str(row["match_id"]), interaction.user.id, pick
        )
        if before == pick:
            await interaction.response.send_message(
                f"이미 **{team}** 에 투표하셨습니다.", ephemeral=True
            )
            return

        note = "투표를 바꿨습니다" if before else "투표했습니다"
        await interaction.response.send_message(
            f"✅ **{team}** 에 {note}. 경기 시작 전까지 다시 바꿀 수 있습니다.",
            ephemeral=True,
        )

    async def handle_stats(self, interaction: discord.Interaction) -> None:
        row = await self._lookup(interaction)
        if row is None:
            await interaction.response.send_message(
                "이 예측 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return

        match_id = str(row["match_id"])
        votes_a, votes_b = await self.bot.db.vote_counts(match_id)
        total = votes_a + votes_b
        mine = await self.bot.db.user_vote(match_id, interaction.user.id)
        team_a, team_b = str(row["team_a"]), str(row["team_b"])

        embed = base_embed(
            f"📊 {team_a} vs {team_b}",
            Colors.INFO,
            description=f"총 **{total}명** 참여",
        )
        embed.add_field(
            name="현황",
            value=(
                f"**{team_a}** `{bar(votes_a, total)}` {votes_a}표\n"
                f"**{team_b}** `{bar(votes_b, total)}` {votes_b}표"
            ),
            inline=False,
        )
        if mine:
            embed.add_field(
                name="내 선택",
                value=team_a if mine == PICK_A else team_b,
                inline=False,
            )
        else:
            embed.add_field(name="내 선택", value="아직 투표하지 않았습니다.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- 정산

    async def resolve(self, row, winner: str) -> int:
        """맞힌 사람에게 포인트를 주고 패널을 갱신한다. 지급 인원을 돌려준다."""
        match_id = str(row["match_id"])
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        winner_name = team_a if winner == PICK_A else team_b

        await self.bot.db.resolve_prediction(match_id, winner)
        hits = await self.bot.db.voters(match_id, winner)
        reason = f"승부예측 적중 — {row['league']} {team_a} vs {team_b}"
        for user_id in hits:
            await self.bot.db.add_points(user_id, Config.CORRECT_REWARD, reason)

        fresh = await self.bot.db.get_prediction(match_id)
        if fresh is not None:
            await self.refresh_panel(fresh)

        if hits:
            embed = base_embed(
                "🎯 승부예측 정산",
                Colors.GOLD,
                description=f"**{row['league']}** {team_a} vs {team_b}",
            )
            embed.add_field(name="승리", value=winner_name, inline=True)
            embed.add_field(name="적중", value=f"{len(hits)}명", inline=True)
            embed.add_field(
                name="지급",
                value=f"각 {Config.CORRECT_REWARD:,}{Economy.UNIT}",
                inline=True,
            )
            await send_log(self.bot, Channels.POINT_LOG, embed)

        log.info("승부예측 정산: %s (%s 승) · %d명 적중", match_id, winner_name, len(hits))
        return len(hits)

    # ------------------------------------------------------------- 루프

    @tasks.loop(minutes=Config.POLL_MINUTES)
    async def sync_matches(self) -> None:
        """일정을 확인해 예측을 올리고, 시작한 경기를 닫고, 끝난 경기를 정산한다."""
        matches: dict[str, Match] = {}
        try:
            matches = {m.id: m for m in await self.fetch_matches()}
        except EsportsError as exc:
            # 일정 서버가 잠깐 죽어도 마감 · 수동 경기 처리는 계속해야 한다
            log.warning("일정 조회 실패: %s", exc)
        except Exception:
            log.exception("일정 조회 중 예상치 못한 오류")

        if matches:
            await self._post_upcoming(matches)
            await self._refresh_times(matches)
        await self._close_started(matches)
        if matches:
            await self._resolve_finished(matches)

    async def fetch_matches(self) -> list[Match]:
        if not self._league_ids:
            leagues = await self.bot.esports.tracked_leagues()
            self._league_ids = [league.id for league in leagues]
            log.info(
                "승부예측 대상 대회 %d개: %s",
                len(leagues), ", ".join(f"{l.name}({l.slug})" for l in leagues) or "없음",
            )
        return await self.bot.esports.schedule(self._league_ids)

    async def _post_upcoming(self, matches: dict[str, Match]) -> None:
        now = dt.datetime.now(dt.timezone.utc)
        lead = dt.timedelta(hours=Config.LEAD_HOURS)
        cutoff = dt.timedelta(minutes=Config.CLOSE_BEFORE_MINUTES)

        posted = 0
        for match in sorted(matches.values(), key=lambda m: m.start_at):
            if posted >= Config.MAX_POST_PER_TICK:
                break
            if match.state != "unstarted" or not match.decided_teams:
                continue
            remaining = match.start_at - now
            if remaining <= cutoff or remaining > lead:
                continue
            if await self.bot.db.get_prediction(match.id) is not None:
                continue

            created = await self.bot.db.add_prediction(
                match.id,
                source="api",
                league=league_label(match.league_name, match.league_slug),
                block=match.block,
                team_a=match.team_a.label,
                team_b=match.team_b.label,
                best_of=match.best_of,
                start_at=match.start_at.isoformat(),
            )
            if not created:
                continue

            row = await self.bot.db.get_prediction(match.id)
            if row is None or not await self.post_panel(row):
                # 패널을 못 올렸으면 기록을 지워 다음 주기에 다시 시도한다
                await self.bot.db.drop_prediction(match.id)
                continue
            posted += 1
            log.info(
                "승부예측 등록: %s %s vs %s",
                row["league"], row["team_a"], row["team_b"],
            )

    async def _refresh_times(self, matches: dict[str, Match]) -> None:
        """경기가 미뤄졌으면 저장해 둔 시작 시각을 고치고 패널을 다시 그린다."""
        for row in await self.bot.db.predictions_in_states([STATE_OPEN]):
            match = matches.get(str(row["match_id"]))
            if match is None or match.state != "unstarted":
                continue
            if match.start_at == parse_utc(str(row["start_at"])):
                continue
            await self.bot.db.update_prediction_start(
                str(row["match_id"]), match.start_at.isoformat()
            )
            fresh = await self.bot.db.get_prediction(str(row["match_id"]))
            if fresh is not None:
                await self.refresh_panel(fresh)
            log.info("경기 시간 변경 반영: %s → %s", row["match_id"], match.start_at)

    async def _close_started(self, matches: dict[str, Match]) -> None:
        """시작한 경기의 투표를 닫는다.

        저장해 둔 시작 시각이 지났거나, 일정 서버가 이미 시작했다고 알려 주면
        닫는다. 둘 중 하나만 보면 일정이 갑자기 당겨졌을 때 놓친다.
        """
        now = dt.datetime.now(dt.timezone.utc)
        cutoff = dt.timedelta(minutes=Config.CLOSE_BEFORE_MINUTES)
        for row in await self.bot.db.predictions_in_states([STATE_OPEN]):
            match = matches.get(str(row["match_id"]))
            started = match is not None and match.state != "unstarted"
            if not started and parse_utc(str(row["start_at"])) - now > cutoff:
                continue
            await self.bot.db.set_prediction_state(str(row["match_id"]), STATE_CLOSED)
            fresh = await self.bot.db.get_prediction(str(row["match_id"]))
            if fresh is not None:
                await self.refresh_panel(fresh)

    async def _resolve_finished(self, matches: dict[str, Match]) -> None:
        """API 가 결과를 준 경기를 정산한다. 수동 경기는 `/경기결과` 로 처리한다."""
        pending = await self.bot.db.predictions_in_states([STATE_OPEN, STATE_CLOSED])
        for row in pending:
            if str(row["source"]) != "api":
                continue
            match = matches.get(str(row["match_id"]))
            if match is None or match.state != "completed":
                continue
            winner = match.winner
            if winner is None:
                continue
            await self.resolve(row, winner)

    @sync_matches.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()

    # ----------------------------------------------------------- 명령어

    @app_commands.command(
        name="예측순위", description="승부예측 적중 순위를 봅니다."
    )
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.prediction_leaderboard(10)
        embed = base_embed(
            "🎯 승부예측 순위",
            Colors.GOLD,
            description="결과가 나온 경기만 셉니다.",
        )
        if not rows:
            embed.add_field(
                name="아직 없습니다",
                value=f"<#{Channels.PREDICTION}> 에서 예측에 참여해 보세요.",
                inline=False,
            )
        else:
            medals = ("🥇", "🥈", "🥉")
            lines = []
            for index, row in enumerate(rows):
                mark = medals[index] if index < 3 else f"`{index + 1}.`"
                correct, total = int(row["correct"]), int(row["total"])
                rate = round(100 * correct / total) if total else 0
                lines.append(
                    f"{mark} <@{row['user_id']}> — **{correct}적중** "
                    f"/ {total}경기 ({rate}%)"
                )
            embed.add_field(name="상위 10명", value="\n".join(lines), inline=False)

        correct, total = await self.bot.db.prediction_stats(interaction.user.id)
        rate = round(100 * correct / total) if total else 0
        embed.add_field(
            name="내 기록",
            value=(
                f"**{correct}적중** / {total}경기 ({rate}%)"
                if total
                else "아직 결과가 나온 예측이 없습니다."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="경기추가",
        description="[관리자] 승부예측을 직접 등록합니다. (아시안게임 등)",
    )
    @app_commands.describe(
        대회="예: 아시안게임",
        팀a="먼저 표시할 팀",
        팀b="나중에 표시할 팀",
        시작="KST 기준 2026-09-20 18:00 형식",
        판수="BO 뒤에 붙는 숫자 (기본 3)",
        라운드="예: 8강 (선택)",
    )
    @staff_only()
    async def add_match(
        self,
        interaction: discord.Interaction,
        대회: str,
        팀a: str,
        팀b: str,
        시작: str,
        판수: int = 3,
        라운드: str = "",
    ) -> None:
        try:
            naive = dt.datetime.strptime(시작.strip(), TIME_FORMAT)
        except ValueError:
            await interaction.response.send_message(
                f"시작 시각을 이해하지 못했습니다. `{TIME_FORMAT.replace('%Y', '2026')}` "
                "형태로 적어 주세요. 예: `2026-09-20 18:00`",
                ephemeral=True,
            )
            return

        start = naive.replace(tzinfo=TIMEZONE).astimezone(dt.timezone.utc)
        if start <= dt.datetime.now(dt.timezone.utc):
            await interaction.response.send_message(
                "이미 지난 시각입니다. 앞으로의 경기만 등록할 수 있습니다.", ephemeral=True
            )
            return
        if 판수 < 1 or 판수 > 9:
            await interaction.response.send_message(
                "판수는 1~9 사이여야 합니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        number = await self.bot.db.next_counter("prediction")
        match_id = f"manual-{number}"
        await self.bot.db.add_prediction(
            match_id,
            source="manual",
            league=대회.strip(),
            block=라운드.strip(),
            team_a=팀a.strip(),
            team_b=팀b.strip(),
            best_of=판수,
            start_at=start.isoformat(),
        )
        row = await self.bot.db.get_prediction(match_id)
        if row is None or not await self.post_panel(row):
            await self.bot.db.drop_prediction(match_id)
            await interaction.followup.send(
                f"패널을 <#{Channels.PREDICTION}> 에 올리지 못했습니다. "
                "봇 권한을 확인해 주세요.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ **{팀a} vs {팀b}** 예측을 <#{Channels.PREDICTION}> 에 올렸습니다.\n"
            f"경기 ID `{match_id}` — 결과는 `/경기결과 경기id:{match_id}` 로 넣어 주세요.",
            ephemeral=True,
        )

    @app_commands.command(
        name="경기결과", description="[관리자] 승부예측을 정산합니다."
    )
    @app_commands.describe(경기id="`/예측목록` 에서 확인한 ID", 승자="이긴 쪽")
    @app_commands.choices(
        승자=[
            app_commands.Choice(name="왼쪽 팀 (팀A)", value=PICK_A),
            app_commands.Choice(name="오른쪽 팀 (팀B)", value=PICK_B),
        ]
    )
    @staff_only()
    async def set_result(
        self,
        interaction: discord.Interaction,
        경기id: str,
        승자: app_commands.Choice[str],
    ) -> None:
        row = await self.bot.db.get_prediction(경기id.strip())
        if row is None:
            await interaction.response.send_message(
                "그런 경기 ID 가 없습니다. `/예측목록` 으로 확인해 주세요.", ephemeral=True
            )
            return
        if str(row["state"]) == STATE_RESOLVED:
            await interaction.response.send_message(
                "이미 정산이 끝난 경기입니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        hits = await self.resolve(row, 승자.value)
        winner_name = (
            str(row["team_a"]) if 승자.value == PICK_A else str(row["team_b"])
        )
        await interaction.followup.send(
            f"✅ **{winner_name}** 승리로 정산했습니다. "
            f"{hits}명에게 {Config.CORRECT_REWARD:,}{Economy.UNIT} 씩 지급했습니다.",
            ephemeral=True,
        )

    @app_commands.command(
        name="경기취소", description="[관리자] 예측을 취소합니다. (포인트 지급 없음)"
    )
    @app_commands.describe(경기id="`/예측목록` 에서 확인한 ID")
    @staff_only()
    async def cancel_match(self, interaction: discord.Interaction, 경기id: str) -> None:
        match_id = 경기id.strip()
        row = await self.bot.db.get_prediction(match_id)
        if row is None:
            await interaction.response.send_message(
                "그런 경기 ID 가 없습니다.", ephemeral=True
            )
            return
        if str(row["state"]) == STATE_RESOLVED:
            await interaction.response.send_message(
                "이미 정산이 끝나 취소할 수 없습니다.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.db.set_prediction_state(match_id, STATE_CANCELLED)
        fresh = await self.bot.db.get_prediction(match_id)
        if fresh is not None:
            await self.refresh_panel(fresh)
        await interaction.followup.send(
            f"✅ `{match_id}` 예측을 취소했습니다.", ephemeral=True
        )

    @app_commands.command(
        name="예측목록", description="[관리자] 진행 중인 예측과 경기 ID를 봅니다."
    )
    @staff_only()
    async def list_matches(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.predictions_in_states([STATE_OPEN, STATE_CLOSED])
        embed = base_embed(
            "🎯 진행 중인 승부예측",
            Colors.INFO,
            description=f"{len(rows)}개" if rows else "없습니다.",
        )
        for row in rows[:20]:
            state = "투표 중" if str(row["state"]) == STATE_OPEN else "마감 (결과 대기)"
            votes_a, votes_b = await self.bot.db.vote_counts(str(row["match_id"]))
            start = parse_utc(str(row["start_at"]))
            embed.add_field(
                name=f"{row['league']} · {row['team_a']} vs {row['team_b']}",
                value=(
                    f"ID `{row['match_id']}` · {state}\n"
                    f"{discord.utils.format_dt(start, 'f')} · "
                    f"{votes_a}표 vs {votes_b}표"
                ),
                inline=False,
            )
        if len(rows) > 20:
            embed.set_footer(text=f"롤 같이 하자 · 20개까지만 표시 (전체 {len(rows)}개)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="리그목록",
        description="[관리자] 일정 서버가 주는 대회 목록과 자동 등록 대상을 확인합니다.",
    )
    @staff_only()
    async def list_leagues(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            everything = await self.bot.esports.leagues(refresh=True)
        except EsportsError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        tracked = self.bot.esports.pick_leagues(everything)
        self._league_ids = [league.id for league in tracked]

        embed = base_embed(
            "📅 이스포츠 대회 목록",
            Colors.INFO,
            description=f"일정 서버가 알려 준 대회 **{len(everything)}개**",
        )
        embed.add_field(
            name=f"✅ 자동 등록 대상 ({len(tracked)})",
            value=(
                "\n".join(f"`{l.slug}` — {l.name}" for l in tracked)[:1000]
                if tracked
                else "없습니다. `config.py` 의 `ESPORTS_LEAGUES` 를 고쳐 주세요."
            ),
            inline=False,
        )
        others = [l for l in everything if l not in tracked]
        if others:
            embed.add_field(
                name=f"그 외 ({len(others)})",
                value=", ".join(f"`{l.slug}`" for l in others)[:1000],
                inline=False,
            )
        embed.set_footer(text="롤 같이 하자 · slug 를 config.py 의 ESPORTS_LEAGUES 에 넣으면 됩니다")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PredictionCog(bot))
