"""라이엇 API 클라이언트 (계정 조회 · 솔로랭크 티어 조회)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp

from config import RIOT_ACCOUNT_REGION, RIOT_API_KEY, RIOT_PLATFORM

log = logging.getLogger("mainbot.riot")

ACCOUNT_URL = "https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
LEAGUE_URL = "https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"

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
    def label(self) -> str:
        base = self.tier.capitalize()
        if self.tier.upper() in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{base} {self.league_points}LP"
        return f"{base} {self.rank} {self.league_points}LP"


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

    async def fetch_solo_rank(self, puuid: str) -> Optional[RankEntry]:
        """솔로랭크 티어를 조회한다. 배치 전이거나 실패하면 None."""
        url = LEAGUE_URL.format(platform=RIOT_PLATFORM, puuid=puuid)
        try:
            data = await self._get(url)
        except RiotError:
            return None
        if not isinstance(data, list):
            return None
        for entry in data:
            if entry.get("queueType") == "RANKED_SOLO_5x5":
                return RankEntry(
                    queue=entry["queueType"],
                    tier=entry.get("tier", ""),
                    rank=entry.get("rank", ""),
                    league_points=int(entry.get("leaguePoints", 0)),
                    wins=int(entry.get("wins", 0)),
                    losses=int(entry.get("losses", 0)),
                )
        return None
