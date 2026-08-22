"""라이엇 API 클라이언트 (계정 조회 · 솔로랭크 티어 조회)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

from config import RIOT_ACCOUNT_REGION, RIOT_API_KEY, RIOT_PLATFORM

log = logging.getLogger("mainbot.riot")

ACCOUNT_URL = "https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
ACCOUNT_BY_PUUID_URL = (
    "https://{region}.api.riotgames.com/riot/account/v1/accounts/by-puuid/{puuid}"
)
LEAGUE_URL = "https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
SUMMONER_URL = (
    "https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}"
)

# 라이엇 티어 문자열 → 서버에서 쓰는 약자
TIER_CODE: dict[str, str] = {
    "IRON": "I",
    "BRONZE": "B",
    "SILVER": "S",
    "GOLD": "G",
    "PLATINUM": "P",
    "EMERALD": "E",
    "DIAMOND": "D",
    "MASTER": "M",
    "GRANDMASTER": "GM",
    "CHALLENGER": "C",
}


class RiotError(Exception):
    """사용자에게 그대로 보여줄 수 있는 라이엇 API 오류."""


@dataclass(slots=True)
class RiotAccount:
    puuid: str
    game_name: str
    tag_line: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


APEX_TIERS = ("MASTER", "GRANDMASTER", "CHALLENGER")

# 로마 숫자 division 을 그대로 쓴다 (마스터 이상은 division 이 없다)
QUEUE_SOLO = "RANKED_SOLO_5x5"
QUEUE_FLEX = "RANKED_FLEX_SR"


@dataclass(slots=True)
class RankEntry:
    queue: str
    tier: str          # 예: "MASTER"
    rank: str          # 예: "I"
    league_points: int
    wins: int
    losses: int

    @property
    def tier_code(self) -> Optional[str]:
        return TIER_CODE.get(self.tier.upper())

    @property
    def tier_name(self) -> str:
        """한글 티어 이름 (마스터 미만은 division 포함)."""
        from config import TIER_NAMES

        code = self.tier_code
        base = TIER_NAMES.get(code, self.tier.capitalize()) if code else self.tier
        if self.tier.upper() in APEX_TIERS or not self.rank:
            return base
        return f"{base} {self.rank}"

    @property
    def record(self) -> str:
        """`25 LP · 159승 130패` 형태의 한 줄 요약."""
        return f"{self.league_points} LP · {self.wins}승 {self.losses}패"

    @property
    def label(self) -> str:
        base = self.tier.capitalize()
        if self.tier.upper() in APEX_TIERS:
            return f"{base} {self.league_points}LP"
        return f"{base} {self.rank} {self.league_points}LP"

    def to_dict(self) -> dict:
        return {
            "queue": self.queue,
            "tier": self.tier,
            "rank": self.rank,
            "leaguePoints": self.league_points,
            "wins": self.wins,
            "losses": self.losses,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RankEntry":
        return cls(
            queue=data.get("queue", ""),
            tier=data.get("tier", ""),
            rank=data.get("rank", ""),
            league_points=int(data.get("leaguePoints", 0)),
            wins=int(data.get("wins", 0)),
            losses=int(data.get("losses", 0)),
        )


class RiotClient:
    """필요할 때만 세션을 여는 얇은 클라이언트."""

    def __init__(self, api_key: str = RIOT_API_KEY) -> None:
        self.api_key = api_key
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-Riot-Token": self.api_key},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _get(self, url: str) -> Optional[dict | list]:
        if not self.enabled:
            raise RiotError(
                "라이엇 API 키가 설정되어 있지 않습니다. `.env` 의 `RIOT_API_KEY` 를 확인해 주세요."
            )
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status == 404:
                    return None
                if resp.status == 403:
                    raise RiotError(
                        "라이엇 API 키가 만료되었거나 권한이 없습니다. 키를 다시 발급해 주세요."
                    )
                if resp.status == 429:
                    raise RiotError(
                        "라이엇 API 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."
                    )
                if resp.status >= 400:
                    raise RiotError(f"라이엇 API 오류 (HTTP {resp.status})")
                return await resp.json()
        except aiohttp.ClientError as exc:
            log.warning("라이엇 API 호출 실패: %s", exc)
            raise RiotError("라이엇 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.") from exc

    async def fetch_account(self, game_name: str, tag_line: str) -> Optional[RiotAccount]:
        """롤 닉네임#태그로 계정을 조회한다. 없으면 None."""
        from urllib.parse import quote

        url = ACCOUNT_URL.format(
            region=RIOT_ACCOUNT_REGION,
            name=quote(game_name, safe=""),
            tag=quote(tag_line, safe=""),
        )
        data = await self._get(url)
        if not isinstance(data, dict):
            return None
        return RiotAccount(
            puuid=data["puuid"],
            game_name=data.get("gameName", game_name),
            tag_line=data.get("tagLine", tag_line),
        )

    async def fetch_account_by_puuid(self, puuid: str) -> Optional[RiotAccount]:
        """PUUID 로 **지금** 쓰고 있는 닉네임#태그를 받아온다.

        롤 닉네임은 바뀌지만 PUUID 는 안 바뀐다. 등록할 때 적어 둔 이름을
        그대로 두면 닉을 바꾼 사람은 프로필에 옛 이름이 계속 남는다.
        """
        url = ACCOUNT_BY_PUUID_URL.format(region=RIOT_ACCOUNT_REGION, puuid=puuid)
        data = await self._get(url)
        if not isinstance(data, dict) or not data.get("gameName"):
            return None
        return RiotAccount(
            puuid=data.get("puuid", puuid),
            game_name=data["gameName"],
            tag_line=data.get("tagLine", ""),
        )

    async def fetch_profile_icon(self, puuid: str) -> Optional[int]:
        """지금 이 계정이 쓰고 있는 프로필 아이콘 번호. 못 읽으면 None.

        계정 소유 인증에 쓴다. 아이콘은 계정에 로그인할 수 있는 사람만 바꿀
        수 있어서, 지정한 번호로 바뀐 걸 확인하면 본인이라는 뜻이 된다.
        """
        url = SUMMONER_URL.format(platform=RIOT_PLATFORM, puuid=puuid)
        data = await self._get(url)
        if not isinstance(data, dict):
            return None
        icon = data.get("profileIconId")
        return int(icon) if isinstance(icon, int) else None

    async def fetch_ranks(self, puuid: str) -> dict[str, Optional[RankEntry]]:
        """솔로랭크와 자유랭크를 한 번에 조회한다. 실패하면 둘 다 None."""
        result: dict[str, Optional[RankEntry]] = {"solo": None, "flex": None}
        url = LEAGUE_URL.format(platform=RIOT_PLATFORM, puuid=puuid)
        try:
            data = await self._get(url)
        except RiotError:
            return result
        if not isinstance(data, list):
            return result

        by_queue = {QUEUE_SOLO: "solo", QUEUE_FLEX: "flex"}
        for entry in data:
            key = by_queue.get(entry.get("queueType", ""))
            if key is None:
                continue
            result[key] = RankEntry(
                queue=entry["queueType"],
                tier=entry.get("tier", ""),
                rank=entry.get("rank", ""),
                league_points=int(entry.get("leaguePoints", 0)),
                wins=int(entry.get("wins", 0)),
                losses=int(entry.get("losses", 0)),
            )
        return result

    async def fetch_solo_rank(self, puuid: str) -> Optional[RankEntry]:
        """솔로랭크만 필요할 때 쓰는 단축 함수."""
        return (await self.fetch_ranks(puuid))["solo"]
