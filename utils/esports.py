"""프로 경기 일정 조회 (lolesports.com 이 쓰는 공개 API).

승부예측 자동 등록에만 쓴다. 라이엇 개발자 API 와는 다른 곳이고 키도 따로다.
공식 문서가 없는 엔드포인트라서 **응답이 조금 달라져도 죽지 않도록** 모든
필드를 `.get()` 으로 읽고, 모양이 어긋나는 경기는 건너뛴다.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

import aiohttp

from config import (
    ESPORTS_API_KEY,
    ESPORTS_BASE_URL,
    ESPORTS_LEAGUES,
    ESPORTS_LEAGUES_EXCLUDE,
    ESPORTS_LOCALE,
)

log = logging.getLogger("mainbot.esports")

# TBD 대진에는 예측을 올리지 않는다
UNDECIDED = frozenset({"", "TBD", "TBA", "UNKNOWN"})


class EsportsError(Exception):
    """사용자에게 그대로 보여줄 수 있는 일정 조회 오류."""


def _parse_time(raw: Any) -> Optional[dt.datetime]:
    """`2026-08-13T08:00:00Z` 형태를 UTC datetime 으로."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


@dataclass(frozen=True, slots=True)
class League:
    id: str
    slug: str
    name: str
    region: str


@dataclass(frozen=True, slots=True)
class Team:
    name: str
    code: str
    image: str
    outcome: Optional[str]   # win / loss / None
    game_wins: int

    @property
    def decided(self) -> bool:
        return (
            bool(self.name)
            and self.code.upper() not in UNDECIDED
            and self.name.upper() not in UNDECIDED
        )

    @property
    def label(self) -> str:
        """`T1` 처럼 코드가 있으면 코드를, 없으면 이름을."""
        return self.code or self.name


@dataclass(frozen=True, slots=True)
class Match:
    id: str
    league_name: str
    league_slug: str
    block: str
    start_at: dt.datetime    # UTC
    state: str               # unstarted / inProgress / completed
    best_of: int
    team_a: Team
    team_b: Team

    @property
    def decided_teams(self) -> bool:
        return self.team_a.decided and self.team_b.decided

    @property
    def winner(self) -> Optional[str]:
        """끝난 경기의 승자를 'A' / 'B' 로. 아직이면 None."""
        if self.team_a.outcome == "win":
            return "A"
        if self.team_b.outcome == "win":
            return "B"
        return None

    @property
    def score(self) -> str:
        return f"{self.team_a.game_wins} - {self.team_b.game_wins}"


def _team(raw: Any) -> Optional[Team]:
    if not isinstance(raw, dict):
        return None
    result = raw.get("result") or {}
    if not isinstance(result, dict):
        result = {}
    try:
        wins = int(result.get("gameWins") or 0)
    except (TypeError, ValueError):
        wins = 0
    return Team(
        name=str(raw.get("name") or ""),
        code=str(raw.get("code") or ""),
        image=str(raw.get("image") or ""),
        outcome=result.get("outcome") or None,
        game_wins=wins,
    )


def _match(event: Any) -> Optional[Match]:
    """스케줄 이벤트 하나를 Match 로. 경기가 아니면 None."""
    if not isinstance(event, dict):
        return None
    if event.get("type") not in (None, "match"):
        return None  # show / 기타 이벤트는 예측 대상이 아니다

    raw = event.get("match")
    if not isinstance(raw, dict):
        return None
    match_id = raw.get("id")
    if not match_id:
        return None

    teams = raw.get("teams")
    if not isinstance(teams, list) or len(teams) < 2:
        return None
    team_a, team_b = _team(teams[0]), _team(teams[1])
    if team_a is None or team_b is None:
        return None

    start_at = _parse_time(event.get("startTime"))
    if start_at is None:
        return None

    league = event.get("league")
    league = league if isinstance(league, dict) else {}
    strategy = raw.get("strategy")
    strategy = strategy if isinstance(strategy, dict) else {}
    try:
        best_of = int(strategy.get("count") or 1)
    except (TypeError, ValueError):
        best_of = 1

    return Match(
        id=str(match_id),
        league_name=str(league.get("name") or "LoL"),
        league_slug=str(league.get("slug") or ""),
        block=str(event.get("blockName") or ""),
        start_at=start_at,
        state=str(event.get("state") or "unstarted"),
        best_of=best_of,
        team_a=team_a,
        team_b=team_b,
    )


class EsportsClient:
    """필요할 때만 세션을 여는 얇은 클라이언트."""

    def __init__(self, api_key: str = ESPORTS_API_KEY) -> None:
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._leagues: Optional[list[League]] = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"x-api-key": self.api_key},
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get(self, path: str, params: Sequence[tuple[str, str]]) -> dict:
        if not self.enabled:
            raise EsportsError(
                "이스포츠 API 키가 비어 있습니다. `.env` 의 `ESPORTS_API_KEY` 를 확인해 주세요."
            )
        session = await self._get_session()
        url = f"{ESPORTS_BASE_URL}/{path}"
        query = [("hl", ESPORTS_LOCALE), *params]
        try:
            async with session.get(url, params=query) as resp:
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    raise EsportsError(
                        f"일정 서버가 {resp.status} 를 돌려주었습니다. `{body}`"
                    )
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise EsportsError(f"일정 서버에 연결하지 못했습니다: `{exc}`") from exc

        if not isinstance(data, dict):
            raise EsportsError("일정 서버 응답을 이해하지 못했습니다.")
        return data

    # ---------------------------------------------------------------- 리그

    async def leagues(self, *, refresh: bool = False) -> list[League]:
        """대회 목록. 한 번 받아 두고 재사용한다."""
        if self._leagues is not None and not refresh:
            return self._leagues

        data = await self._get("getLeagues", [])
        raw = ((data.get("data") or {}).get("leagues")) or []
        found: list[League] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            found.append(
                League(
                    id=str(item["id"]),
                    slug=str(item.get("slug") or ""),
                    name=str(item.get("name") or ""),
                    region=str(item.get("region") or ""),
                )
            )
        self._leagues = found
        return found

    @staticmethod
    def pick_leagues(
        leagues: Iterable[League],
        wanted: Sequence[str] = ESPORTS_LEAGUES,
        excluded: Sequence[str] = ESPORTS_LEAGUES_EXCLUDE,
    ) -> list[League]:
        """slug 가 정확히 같거나 대회 이름에 들어 있는 것을 고른다.

        slug 는 시즌마다 바뀌기도 해서 이름 부분 일치까지 함께 본다. 다만
        부분 일치는 원하지 않는 것까지 끌어온다. `lck` 가 `lck_challengers_league`
        에도 걸리는 식이다. 그래서 제외 목록을 먼저 본다.
        """
        keys = [w.strip().lower() for w in wanted if w.strip()]
        blocked = [w.strip().lower() for w in excluded if w.strip()]
        chosen: dict[str, League] = {}
        for league in leagues:
            slug = league.slug.lower()
            name = league.name.lower()
            if any(key == slug or key in slug or key in name for key in blocked):
                continue
            for key in keys:
                if slug == key or key in name or key in slug:
                    chosen[league.id] = league
                    break
        return list(chosen.values())

    async def tracked_leagues(self) -> list[League]:
        return self.pick_leagues(await self.leagues())

    # ---------------------------------------------------------------- 일정

    async def schedule(self, league_ids: Sequence[str]) -> list[Match]:
        """해당 대회들의 최근 · 예정 경기를 가져온다.

        대회 ID 는 **쉼표로 이어 붙여 한 번만** 넘겨야 한다. `leagueId` 를
        여러 번 반복해서 붙이면 (`leagueId=A&leagueId=B`) 서버가 거의 빈
        응답을 돌려준다. 예전에 그렇게 보내는 바람에 6개 대회를 넘겼을 때
        경기가 1건만 들어와 자동 등록이 통째로 멎어 있었다.
        """
        if not league_ids:
            return []
        params = [("leagueId", ",".join(league_ids))]
        data = await self._get("getSchedule", params)
        events = (
            ((data.get("data") or {}).get("schedule") or {}).get("events")
        ) or []

        matches: list[Match] = []
        for event in events:
            parsed = _match(event)
            if parsed is not None:
                matches.append(parsed)
        log.debug("일정 %d경기를 받았습니다.", len(matches))
        return matches
