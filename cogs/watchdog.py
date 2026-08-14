"""봇이 조용히 고장 났는지 스스로 살펴 관리진에게 알린다.

이 봇이 망가지는 방식은 대개 에러를 내며 멈추는 게 아니라 **아무 일도
일어나지 않는** 것이다. 실제로 대회 ID 를 넘기는 방식이 잘못돼 승부예측
자동 등록이 통째로 멎어 있었는데, 로그에는 아무것도 남지 않아 한참 뒤에야
발견했다. 사람이 매일 확인하게 만드는 대신 봇이 먼저 말하게 한다.

살피는 것:
  · 자동 등록이 며칠째 0건인가 (대회 slug 변경 · 일정 API 변화)
  · 시작 시각이 한참 지났는데 정산이 안 된 경기가 있는가 (포인트가 묶인다)

같은 경고를 주기마다 다시 보내면 관리 채널이 도배되므로, 한 번 보낸
경고는 REPEAT_HOURS 동안 다시 보내지 않는다.
"""
from __future__ import annotations

import datetime as dt
import logging

from discord.ext import commands, tasks

from config import Channels, Colors, Economy, TIMEZONE, Watchdog as Config
from utils.logs import base_embed, send_log

log = logging.getLogger("mainbot.watchdog")

# 마지막으로 경고를 보낸 시각을 담아 두는 settings 키
ALERT_STATE_KEY = "watchdog_last_alert"

ALERT_STALLED = "stalled_registration"
ALERT_UNSETTLED = "unsettled_matches"


class WatchdogCog(commands.Cog, name="Watchdog"):
    """이상 징후 감시."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.patrol.start()

    async def cog_unload(self) -> None:
        self.patrol.cancel()

    # ------------------------------------------------------------ 도배 방지

    async def _should_alert(self, kind: str) -> bool:
        """이 경고를 지금 보내도 되는지. 보낼 거면 보낸 시각을 남긴다."""
        state = await self.bot.db.get_json_setting(ALERT_STATE_KEY) or {}
        now = dt.datetime.now(dt.timezone.utc)

        raw = state.get(kind)
        if raw:
            try:
                last = dt.datetime.fromisoformat(raw)
            except ValueError:
                last = None
            if last is not None:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=dt.timezone.utc)
                if now - last < dt.timedelta(hours=Config.REPEAT_HOURS):
                    return False

        state[kind] = now.isoformat()
        await self.bot.db.set_json_setting(ALERT_STATE_KEY, state)
        return True

    async def _clear_alert(self, kind: str) -> None:
        """상황이 풀렸으면 기록을 지워, 다시 생기면 바로 알리게 한다."""
        state = await self.bot.db.get_json_setting(ALERT_STATE_KEY) or {}
        if state.pop(kind, None) is not None:
            await self.bot.db.set_json_setting(ALERT_STATE_KEY, state)

    # -------------------------------------------------------------- 점검

    @tasks.loop(hours=Config.CHECK_HOURS)
    async def patrol(self) -> None:
        try:
            await self.check_stalled_registration()
        except Exception:
            log.exception("자동 등록 점검 중 오류")
        try:
            await self.check_unsettled()
        except Exception:
            log.exception("정산 누락 점검 중 오류")

    @patrol.before_loop
    async def before_patrol(self) -> None:
        await self.bot.wait_until_ready()

    async def check_stalled_registration(self) -> None:
        """며칠째 새 예측이 안 올라왔으면 자동 등록이 멎은 것으로 본다.

        일정에 정말 경기가 없어서 0건일 수도 있으므로, 일정 API 에 앞으로의
        경기가 실제로 잡혀 있을 때만 알린다. 그래야 비시즌에 헛경보가 안 뜬다.
        """
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            days=Config.STALE_REGISTER_DAYS
        )
        recent = await self.bot.db.predictions_created_since(since.isoformat())
        if recent > 0:
            await self._clear_alert(ALERT_STALLED)
            return

        # 일정에 예정 경기가 있는지 확인한다. 조회 자체가 실패하면 그것대로 문제다
        upcoming = 0
        api_error = ""
        try:
            leagues = await self.bot.esports.tracked_leagues()
            matches = await self.bot.esports.schedule([lg.id for lg in leagues])
            now = dt.datetime.now(dt.timezone.utc)
            upcoming = sum(
                1
                for m in matches
                if m.state == "unstarted" and m.start_at > now and m.decided_teams
            )
        except Exception as exc:
            api_error = str(exc)

        if not api_error and upcoming == 0:
            # 비시즌이라 올릴 경기가 없는 것뿐이다
            await self._clear_alert(ALERT_STALLED)
            return

        if not await self._should_alert(ALERT_STALLED):
            return

        if api_error:
            detail = f"일정 조회가 실패하고 있습니다.\n```{api_error[:400]}```"
        else:
            detail = (
                f"일정에는 앞으로의 경기가 **{upcoming}건** 잡혀 있는데, "
                f"최근 **{Config.STALE_REGISTER_DAYS}일간 새로 올라온 예측이 없습니다.**"
            )

        embed = base_embed(
            "⚠️ 승부예측 자동 등록이 멈춘 것 같습니다",
            Colors.DANGER,
            description=detail,
        )
        embed.add_field(
            name="확인할 것",
            value=(
                "· `/리그목록` — 대회 slug 가 시즌 따라 바뀌었을 수 있습니다\n"
                "· 바뀌었으면 `config.py` 의 `ESPORTS_LEAGUES` 를 고쳐 주세요\n"
                "· 일정 서버 자체가 죽었으면 잠시 뒤 저절로 풀립니다"
            ),
            inline=False,
        )
        embed.set_footer(text=f"롤 같이 하자 · {Config.REPEAT_HOURS}시간에 한 번만 알립니다")
        await send_log(self.bot, Channels.STAFF_ALERT, embed)
        log.warning("자동 등록 멈춤 의심 — 예정 경기 %d건, 최근 등록 0건", upcoming)

    async def check_unsettled(self) -> None:
        """시작한 지 한참 됐는데 정산이 안 된 경기를 알린다."""
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
            hours=Config.STALE_SETTLE_HOURS
        )
        rows = await self.bot.db.stale_predictions(cutoff.isoformat())
        if not rows:
            await self._clear_alert(ALERT_UNSETTLED)
            return

        if not await self._should_alert(ALERT_UNSETTLED):
            return

        locked = sum(int(r["pool"]) for r in rows)
        embed = base_embed(
            "⚠️ 정산이 안 끝난 경기가 있습니다",
            Colors.DANGER,
            description=(
                f"시작한 지 **{Config.STALE_SETTLE_HOURS}시간** 넘게 지났는데 "
                f"결과가 안 들어간 경기 **{len(rows)}건**입니다.\n"
                f"묶여 있는 포인트: **{locked:,}{Economy.UNIT}**"
            ),
        )

        lines = []
        for row in rows[:10]:
            start = dt.datetime.fromisoformat(str(row["start_at"]))
            if start.tzinfo is None:
                start = start.replace(tzinfo=dt.timezone.utc)
            kind = "수동" if str(row["source"]) == "manual" else "자동"
            lines.append(
                f"`{row['match_id']}` [{kind}] {row['team_a']} vs {row['team_b']}\n"
                f"　{start.astimezone(TIMEZONE):%m-%d %H:%M} · "
                f"{int(row['bettors'])}명 {int(row['pool']):,}{Economy.UNIT}"
            )
        embed.add_field(name="목록", value="\n".join(lines)[:1024], inline=False)
        embed.add_field(
            name="처리",
            value=(
                "· 결과가 나왔으면 `/경기결과 경기id:<ID> 승자:<팀>`\n"
                "· 경기가 무산됐으면 `/경기취소 경기id:<ID>` — 건 포인트가 전액 환불됩니다\n"
                "· **수동으로 넣은 경기는 자동 정산되지 않습니다**"
            ),
            inline=False,
        )
        embed.set_footer(text=f"롤 같이 하자 · {Config.REPEAT_HOURS}시간에 한 번만 알립니다")
        await send_log(self.bot, Channels.STAFF_ALERT, embed)
        log.warning("정산 누락 %d건 · 묶인 포인트 %d", len(rows), locked)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WatchdogCog(bot))
