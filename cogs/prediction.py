"""프로 경기 승부예측 (포인트 베팅).

경기 시작 **24시간 전**에 예측 패널을 자동으로 올리고, 경기가 끝나면 결과를
받아 정산한다. LCK · MSI · Worlds · EWC 는 lolesports 공개 API 에서 일정을
그대로 가져오고, 그 API 에 없는 대회(아시안게임 등)는 `/경기추가` 로 넣는다.

**배당은 파리뮤추얼이다.** 양쪽에 걸린 포인트를 한 통에 모았다가, 맞힌 쪽이
자기가 건 비율대로 통째로 나눠 갖는다. 그래서 배당은 미리 정해져 있지 않고
마감 시점의 판돈 비율로 정해진다. 봇이 포인트를 새로 만들지 않으므로
(지급 합계 ≤ 베팅 합계) 아무리 굴려도 경제가 부풀지 않는다.

건 포인트는 **즉시 차감**되고 틀리면 그대로 잃는다. 그래서 한 번 걸면 바꿀
수 없다 — 바꾸기를 허용하면 마감 직전에 유리한 쪽으로 갈아타 배당만 빨아먹는
게 가능해진다. 같은 이유로 현황(판돈·배당)은 처음부터 공개한다. 파리뮤추얼은
판돈을 봐야 배당을 알 수 있어서 가려 두면 베팅 자체가 성립하지 않는다.
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
from utils.versus import render_versus

log = logging.getLogger("mainbot.prediction")

PICK_A = "A"
PICK_B = "B"

STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_RESOLVED = "resolved"
STATE_CANCELLED = "cancelled"

TIME_FORMAT = "%Y-%m-%d %H:%M"

# 임베드에서 `attachment://` 로 가리킬 배너 파일 이름
BANNER_NAME = "versus.png"


def league_label(name: str, slug: str = "") -> str:
    """대회 이름을 한국어 표기로. 모르는 대회는 API 이름 그대로 쓴다."""
    return LEAGUE_NAMES.get(slug.lower()) or LEAGUE_NAMES.get(name.lower()) or name


def parse_utc(raw: str) -> dt.datetime:
    moment = dt.datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment


BAR_FULL = "▰"
BAR_EMPTY = "▱"
BAR_WIDTH = 14


def bar(part: int, total: int, width: int = BAR_WIDTH) -> str:
    """`▰▰▰▰▰▱▱▱▱▱▱▱▱▱  36%` 형태의 막대."""
    if total <= 0:
        return BAR_EMPTY * width + "   0%"

    ratio = part / total
    filled = round(width * ratio)
    # 반올림 때문에 "조금 걸렸는데 빈 막대" 나 "전부는 아닌데 꽉 찬 막대" 가
    # 나오면 오해를 사므로 양 끝은 한 칸씩 남겨 둔다
    if part > 0 and filled == 0:
        filled = 1
    if part < total and filled == width:
        filled = width - 1

    return f"{BAR_FULL * filled}{BAR_EMPTY * (width - filled)} {round(100 * ratio):3d}%"


def payout_odds(side_pool: int, total_pool: int) -> float:
    """파리뮤추얼 배당. 그 쪽에 아무도 안 걸었으면 계산이 안 되므로 0."""
    if side_pool <= 0:
        return 0.0
    return total_pool / side_pool


def fmt_odds(value: float) -> str:
    return f"{value:.2f}배" if value > 0 else "—"


def payout_for(stake: int, side_pool: int, total_pool: int) -> int:
    """건 돈이 얼마로 돌아오는지. 내림해서 원금 합보다 커지지 않게 한다."""
    if side_pool <= 0:
        return 0
    return stake * total_pool // side_pool


ALL_IN_WORDS = {"올인", "전부", "전액", "다", "all", "max"}


class BetModal(discord.ui.Modal):
    """걸 포인트를 받는 입력창."""

    def __init__(self, cog: "PredictionCog", pick: str, team: str, balance: int) -> None:
        # 모달 제목은 45자까지다
        super().__init__(title=f"{team} 에 베팅"[:45], timeout=300)
        self.cog = cog
        self.pick = pick
        self.amount = discord.ui.TextInput(
            label="걸 포인트",
            placeholder=(
                f"{Config.MIN_BET:,} 이상 · 보유 {balance:,}{Economy.UNIT}"
                " · '올인' 도 됩니다"
            ),
            max_length=16,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_bet(interaction, self.pick, self.amount.value)


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
        label="팀 A", emoji="💰", style=discord.ButtonStyle.primary,
        custom_id="predict:A",
    )
    async def pick_a(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.open_bet_modal(interaction, PICK_A)

    @discord.ui.button(
        label="팀 B", emoji="💰", style=discord.ButtonStyle.danger,
        custom_id="predict:B",
    )
    async def pick_b(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.open_bet_modal(interaction, PICK_B)

    @discord.ui.button(
        label="내 베팅", emoji="📊", style=discord.ButtonStyle.secondary,
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
        if row["image_a"] or row["image_b"]:
            # 패널을 올릴 때 붙여 둔 배너. 메시지를 수정해도 첨부는 남는다
            embed.set_image(url=f"attachment://{BANNER_NAME}")

        embed.add_field(
            name="🗓️ 경기 시작",
            value=(
                f"{discord.utils.format_dt(start, 'F')}\n"
                f"{discord.utils.format_dt(start, 'R')}"
            ),
            inline=True,
        )
        embed.add_field(name="🎮 방식", value=f"BO{row['best_of']}", inline=True)

        # 파리뮤추얼이라 판돈을 봐야 배당을 알 수 있다. 늘 공개한다
        pool_a, pool_b, count_a, count_b = await self.bot.db.bet_pools(
            str(row["match_id"])
        )
        total_pool = pool_a + pool_b
        people = count_a + count_b
        unit = Economy.UNIT

        embed.add_field(
            name="💰 판돈",
            value=f"**{total_pool:,}{unit}**\n{people}명 참여",
            inline=True,
        )

        def side(name: str, pool: int, count: int, mark: str = "") -> str:
            odds = payout_odds(pool, total_pool)
            return (
                f"{mark}**{name}** · 배당 **{fmt_odds(odds)}**\n"
                f"`{bar(pool, total_pool)}`\n"
                f"{pool:,}{unit} · {count}명"
            )

        winner = str(row["winner"] or "")
        embed.add_field(
            name="​",
            value=(
                side(team_a, pool_a, count_a, "🏆 " if winner == PICK_A else "")
                + "\n\n"
                + side(team_b, pool_b, count_b, "🏆 " if winner == PICK_B else "")
            ),
            inline=False,
        )

        if state == STATE_OPEN:
            embed.add_field(
                name="거는 법",
                value=(
                    f"아래 버튼으로 이길 팀에 포인트를 겁니다. 최소 **{Config.MIN_BET:,}{unit}**, "
                    "상한은 없습니다.\n"
                    "· 맞히면 **판돈 전체를 건 비율대로** 나눠 갖고, 틀리면 건 돈을 잃습니다\n"
                    "· **한 번 걸면 팀도 금액도 바꿀 수 없습니다**\n"
                    "· 배당은 계속 움직이고 **마감 시점 기준**으로 확정됩니다"
                ),
                inline=False,
            )
            embed.set_footer(
                text=f"롤 같이 하자 · 경기 시작 {Config.CLOSE_BEFORE_MINUTES}분 전에 마감됩니다"
            )
            return embed

        if state == STATE_RESOLVED and winner:
            winner_name = team_a if winner == PICK_A else team_b
            win_pool = pool_a if winner == PICK_A else pool_b
            win_count = count_a if winner == PICK_A else count_b
            if win_pool <= 0:
                result = (
                    f"**{winner_name}** 승리\n"
                    "맞힌 사람이 없어 **전원 환불**했습니다."
                )
            else:
                result = (
                    f"**{winner_name}** 승리\n"
                    f"{win_count}명 적중 · 배당 **{fmt_odds(payout_odds(win_pool, total_pool))}** · "
                    f"총 **{total_pool:,}{unit}** 지급"
                )
            embed.add_field(name="🏆 결과", value=result, inline=False)
            embed.set_footer(text="롤 같이 하자 · 정산 완료")
        elif state == STATE_CANCELLED:
            embed.add_field(
                name="취소됨",
                value="이 경기는 취소되었습니다. 건 포인트는 **전액 환불**되었습니다.",
                inline=False,
            )
            embed.set_footer(text="롤 같이 하자")
        else:
            embed.set_footer(text="롤 같이 하자 · 베팅 마감 · 결과를 기다리는 중")
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

        # 배너는 여기서 한 번만 붙인다. 이후 패널을 고칠 때는 임베드만 바꾸고
        # 첨부는 건드리지 않아서 `attachment://` 주소가 계속 살아 있다
        banner = await render_versus(
            str(row["image_a"] or ""),
            str(row["image_b"] or ""),
            code_a=str(row["team_a"]),
            code_b=str(row["team_b"]),
        )
        if banner is None:
            # 배너를 못 만들었으면 로고 주소를 지운다. 안 그러면 나중에 패널을
            # 다시 그릴 때 있지도 않은 첨부를 가리켜 깨진 이미지가 뜬다
            await self.bot.db.clear_prediction_images(str(row["match_id"]))
            refreshed = await self.bot.db.get_prediction(str(row["match_id"]))
            if refreshed is not None:
                row = refreshed
        files = (
            [discord.File(banner, filename=BANNER_NAME)] if banner is not None else []
        )

        try:
            message = await channel.send(
                embed=await self.build_embed(row), view=view, files=files
            )
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

    def _betting_closed(self, row) -> str | None:
        """지금 베팅을 받을 수 있는지. 못 받으면 이유를 돌려준다.

        상태만 보면 안 된다. 마감 루프는 POLL_MINUTES 마다만 도니까 경기가
        시작됐는데 아직 open 으로 남아 있는 구간이 생기고, 그 사이에 초반
        상황을 보고 거는 게 가능해진다. 그래서 시작 시각을 직접 확인한다.
        """
        if str(row["state"]) != STATE_OPEN:
            return "이미 베팅이 마감된 경기입니다."
        left = parse_utc(str(row["start_at"])) - dt.datetime.now(dt.timezone.utc)
        if left <= dt.timedelta(minutes=Config.CLOSE_BEFORE_MINUTES):
            return (
                f"경기 시작 {Config.CLOSE_BEFORE_MINUTES}분 전이라 베팅이 마감되었습니다."
            )
        return None

    async def open_bet_modal(self, interaction: discord.Interaction, pick: str) -> None:
        """베팅 버튼 → 금액 입력창."""
        row = await self._lookup(interaction)
        if row is None:
            await interaction.response.send_message(
                "이 예측 정보를 찾을 수 없습니다. 관리자에게 알려 주세요.", ephemeral=True
            )
            return

        closed = self._betting_closed(row)
        if closed:
            await interaction.response.send_message(closed, ephemeral=True)
            return

        mine = await self.bot.db.user_bet(str(row["match_id"]), interaction.user.id)
        if mine is not None:
            picked = str(row["team_a"]) if str(mine["pick"]) == PICK_A else str(row["team_b"])
            await interaction.response.send_message(
                f"이미 **{picked}** 에 **{int(mine['amount']):,}{Economy.UNIT}** 거셨습니다.\n"
                "한 경기에는 한 번만 걸 수 있고, 건 뒤에는 바꿀 수 없습니다.",
                ephemeral=True,
            )
            return

        team = str(row["team_a"]) if pick == PICK_A else str(row["team_b"])
        balance = await self.bot.db.get_points(interaction.user.id)
        if balance < Config.MIN_BET:
            await interaction.response.send_message(
                f"포인트가 모자랍니다. 최소 **{Config.MIN_BET:,}{Economy.UNIT}** 이 필요한데 "
                f"지금 **{balance:,}{Economy.UNIT}** 있습니다.\n"
                "`/출석` 하거나 음성 채널에 있으면 쌓입니다.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(BetModal(self, pick, team, balance))

    async def handle_bet(
        self, interaction: discord.Interaction, pick: str, raw: str
    ) -> None:
        """입력창에서 받은 금액으로 실제 베팅을 넣는다."""
        row = await self._lookup(interaction)
        if row is None:
            await interaction.response.send_message(
                "이 예측 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return

        # 입력창을 열어 둔 사이에 마감됐을 수 있다
        closed = self._betting_closed(row)
        if closed:
            await interaction.response.send_message(closed, ephemeral=True)
            return

        balance = await self.bot.db.get_points(interaction.user.id)
        text = raw.strip().replace(",", "").replace(" ", "")
        if text.lower() in ALL_IN_WORDS:
            amount = balance
        else:
            if not text.isdigit():
                await interaction.response.send_message(
                    f"걸 포인트를 숫자로 적어 주세요. (예: `1000`, 전부 걸려면 `올인`)\n"
                    f"입력하신 값: `{raw[:50]}`",
                    ephemeral=True,
                )
                return
            amount = int(text)

        if amount < Config.MIN_BET:
            await interaction.response.send_message(
                f"최소 **{Config.MIN_BET:,}{Economy.UNIT}** 부터 걸 수 있습니다.",
                ephemeral=True,
            )
            return
        if amount > balance:
            await interaction.response.send_message(
                f"보유 포인트보다 많이 걸 수 없습니다.\n"
                f"걸려던 금액 **{amount:,}{Economy.UNIT}** · 보유 **{balance:,}{Economy.UNIT}**",
                ephemeral=True,
            )
            return

        match_id = str(row["match_id"])
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        team = team_a if pick == PICK_A else team_b
        result = await self.bot.db.place_bet(
            match_id,
            interaction.user.id,
            pick,
            amount,
            reason=f"승부예측 베팅 — {row['league']} {team_a} vs {team_b}",
        )
        if result == "dup":
            await interaction.response.send_message(
                "이미 이 경기에 거셨습니다. 한 번만 걸 수 있습니다.", ephemeral=True
            )
            return
        if result == "poor":
            await interaction.response.send_message(
                "포인트가 모자라 걸지 못했습니다. 다시 확인해 주세요.", ephemeral=True
            )
            return

        pool_a, pool_b, _, _ = await self.bot.db.bet_pools(match_id)
        total = pool_a + pool_b
        odds = payout_odds(pool_a if pick == PICK_A else pool_b, total)
        left = await self.bot.db.get_points(interaction.user.id)

        await interaction.response.send_message(
            f"✅ **{team}** 에 **{amount:,}{Economy.UNIT}** 걸었습니다.\n"
            f"현재 배당 **{fmt_odds(odds)}** · 적중 시 예상 **"
            f"{payout_for(amount, pool_a if pick == PICK_A else pool_b, total):,}{Economy.UNIT}**\n"
            f"남은 포인트 **{left:,}{Economy.UNIT}**\n\n"
            "-# 배당은 마감까지 계속 바뀌며, 최종 배당은 마감 시점 기준입니다.",
            ephemeral=True,
        )

        fresh = await self.bot.db.get_prediction(match_id)
        if fresh is not None:
            await self.refresh_panel(fresh)

    async def handle_stats(self, interaction: discord.Interaction) -> None:
        row = await self._lookup(interaction)
        if row is None:
            await interaction.response.send_message(
                "이 예측 정보를 찾을 수 없습니다.", ephemeral=True
            )
            return

        match_id = str(row["match_id"])
        pool_a, pool_b, count_a, count_b = await self.bot.db.bet_pools(match_id)
        total = pool_a + pool_b
        mine = await self.bot.db.user_bet(match_id, interaction.user.id)
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        unit = Economy.UNIT

        embed = base_embed(
            f"📊 {team_a} vs {team_b}",
            Colors.INFO,
            description=f"판돈 **{total:,}{unit}** · {count_a + count_b}명 참여",
        )
        embed.add_field(
            name="배당",
            value=(
                f"**{team_a}** · {fmt_odds(payout_odds(pool_a, total))}\n"
                f"`{bar(pool_a, total)}` {pool_a:,}{unit}\n\n"
                f"**{team_b}** · {fmt_odds(payout_odds(pool_b, total))}\n"
                f"`{bar(pool_b, total)}` {pool_b:,}{unit}"
            ),
            inline=False,
        )

        if mine is None:
            embed.add_field(
                name="내 베팅", value="아직 걸지 않았습니다.", inline=False
            )
        else:
            pick = str(mine["pick"])
            stake = int(mine["amount"])
            picked = team_a if pick == PICK_A else team_b
            side_pool = pool_a if pick == PICK_A else pool_b
            payout = mine["payout"]

            if payout is None:
                value = (
                    f"**{picked}** 에 **{stake:,}{unit}**\n"
                    f"적중 시 예상 **{payout_for(stake, side_pool, total):,}{unit}** "
                    f"(배당 {fmt_odds(payout_odds(side_pool, total))})"
                )
            else:
                got = int(payout)
                profit = got - stake
                sign = "+" if profit >= 0 else ""
                value = (
                    f"**{picked}** 에 **{stake:,}{unit}**\n"
                    f"정산 결과 **{got:,}{unit}** 수령 (**{sign}{profit:,}{unit}**)"
                )
            embed.add_field(name="내 베팅", value=value, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------- 정산

    async def resolve(self, row, winner: str) -> int:
        """파리뮤추얼로 정산하고 패널을 갱신한다. 지급받은 인원을 돌려준다.

        맞힌 사람은 `건 돈 × 전체 판돈 ÷ 맞힌 쪽 판돈` 을 받는다. 내림하므로
        지급 합계는 판돈을 절대 넘지 않는다.

        맞힌 쪽에 아무도 안 걸었으면 나눠 줄 기준이 없다. 이때는 진 사람들의
        포인트만 사라지는 셈이라 **전원 환불**한다.
        """
        match_id = str(row["match_id"])
        team_a, team_b = str(row["team_a"]), str(row["team_b"])
        winner_name = team_a if winner == PICK_A else team_b
        label = f"{row['league']} {team_a} vs {team_b}"
        unit = Economy.UNIT

        pool_a, pool_b, _, _ = await self.bot.db.bet_pools(match_id)
        total_pool = pool_a + pool_b
        win_pool = pool_a if winner == PICK_A else pool_b

        await self.bot.db.resolve_prediction(match_id, winner)

        if win_pool <= 0:
            paid = await self._refund(
                match_id, reason=f"승부예측 환불(적중자 없음) — {label}"
            )
            summary = f"맞힌 사람이 없어 {paid}명에게 전액 환불했습니다."
            odds = 0.0
        else:
            odds = payout_odds(win_pool, total_pool)
            reason = f"승부예측 적중 — {label}"
            paid = 0
            for bet in await self.bot.db.bets_on(match_id, winner):
                user_id, stake = int(bet["user_id"]), int(bet["amount"])
                payout = payout_for(stake, win_pool, total_pool)
                if payout <= 0:
                    continue
                await self.bot.db.add_points(user_id, payout, reason)
                await self.bot.db.set_bet_payout(match_id, user_id, payout)
                paid += 1
            # 진 사람은 이미 걸 때 차감됐다. 정산 결과를 0 으로 남겨 두면
            # 나중에 수익 계산이 맞는다
            for bet in await self.bot.db.bets_on(
                match_id, PICK_B if winner == PICK_A else PICK_A
            ):
                await self.bot.db.set_bet_payout(match_id, int(bet["user_id"]), 0)
            summary = f"{paid}명 적중 · 배당 {fmt_odds(odds)} · 총 {total_pool:,}{unit} 지급"

        fresh = await self.bot.db.get_prediction(match_id)
        if fresh is not None:
            await self.refresh_panel(fresh)

        if total_pool > 0:
            # 인원수만큼 로그를 보내면 도배가 되므로 요약 한 건만 남긴다
            embed = base_embed(
                "🎯 승부예측 정산",
                Colors.GOLD,
                description=f"**{row['league']}** {team_a} vs {team_b}",
            )
            embed.add_field(name="승리", value=winner_name, inline=True)
            embed.add_field(name="판돈", value=f"{total_pool:,}{unit}", inline=True)
            embed.add_field(name="배당", value=fmt_odds(odds), inline=True)
            embed.add_field(name="정산", value=summary, inline=False)
            await send_log(self.bot, Channels.POINT_LOG, embed)

        log.info("승부예측 정산: %s (%s 승) · %s", match_id, winner_name, summary)
        return paid

    async def _refund(self, match_id: str, *, reason: str) -> int:
        """건 포인트를 전부 돌려준다. 돌려받은 인원을 반환한다."""
        refunded = 0
        for bet in await self.bot.db.all_bets(match_id):
            user_id, stake = int(bet["user_id"]), int(bet["amount"])
            if stake <= 0:
                continue
            await self.bot.db.add_points(user_id, stake, reason)
            await self.bot.db.set_bet_payout(match_id, user_id, stake)
            refunded += 1
        return refunded

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
                image_a=match.team_a.image,
                image_b=match.team_b.image,
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
                profit = int(row["profit"])
                rate = round(100 * correct / total) if total else 0
                lines.append(
                    f"{mark} <@{row['user_id']}> — **+{profit:,}{Economy.UNIT}** "
                    f"· {correct}/{total}경기 ({rate}%)"
                )
            embed.add_field(name="상위 10명", value="\n".join(lines), inline=False)

        correct, total, profit = await self.bot.db.prediction_stats(interaction.user.id)
        rate = round(100 * correct / total) if total else 0
        sign = "+" if profit >= 0 else ""
        embed.add_field(
            name="내 기록",
            value=(
                f"순수익 **{sign}{profit:,}{Economy.UNIT}**\n"
                f"{correct}적중 / {total}경기 ({rate}%)"
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
        paid = await self.resolve(row, 승자.value)
        winner_name = (
            str(row["team_a"]) if 승자.value == PICK_A else str(row["team_b"])
        )
        await interaction.followup.send(
            f"✅ **{winner_name}** 승리로 정산했습니다. {paid}명에게 지급했습니다.",
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

        # 취소는 곧 "없던 일" 이므로 건 포인트를 반드시 돌려줘야 한다
        refunded = await self._refund(
            match_id,
            reason=(
                f"승부예측 취소 환불 — {row['league']} "
                f"{row['team_a']} vs {row['team_b']}"
            ),
        )

        fresh = await self.bot.db.get_prediction(match_id)
        if fresh is not None:
            await self.refresh_panel(fresh)
        await interaction.followup.send(
            f"✅ `{match_id}` 예측을 취소하고 **{refunded}명**에게 건 포인트를 "
            "전액 환불했습니다.",
            ephemeral=True,
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
            state = "베팅 중" if str(row["state"]) == STATE_OPEN else "마감 (결과 대기)"
            pool_a, pool_b, count_a, count_b = await self.bot.db.bet_pools(
                str(row["match_id"])
            )
            start = parse_utc(str(row["start_at"]))
            embed.add_field(
                name=f"{row['league']} · {row['team_a']} vs {row['team_b']}",
                value=(
                    f"ID `{row['match_id']}` · {state}\n"
                    f"{discord.utils.format_dt(start, 'f')} · "
                    f"판돈 {pool_a + pool_b:,}{Economy.UNIT} "
                    f"({pool_a:,} vs {pool_b:,} · {count_a + count_b}명)"
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
