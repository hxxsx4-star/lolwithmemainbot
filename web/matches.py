"""롤 전적 조회.

라이엇 개발자 키는 **2분에 100회**다. 전적 한 번을 보여 주려면 매치마다
한 번씩 호출해야 해서, 아무 캐시 없이 만들면 몇 사람만 써도 키가 막히고
봇의 랭크 조회까지 같이 죽는다.

그래서 두 겹으로 아낀다.
  · 끝난 매치의 내용은 바뀌지 않으므로 **한 번 받으면 DB 에 영구 보관**한다.
  · 매치 **목록**만 짧은 주기로 다시 확인한다.

커스텀(내전)은 `gameType == "CUSTOM_GAME"` 으로 구분한다. queueId 는 모드마다
값이 달라서(내전 소환사의 협곡은 3130) 그것으로 판별하면 놓친다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging

import aiohttp

from config import RIOT_ACCOUNT_REGION, RIOT_API_KEY, Web

log = logging.getLogger("web.matches")

BASE = f"https://{RIOT_ACCOUNT_REGION}.api.riotgames.com"
IDS = BASE + "/lol/match/v5/matches/by-puuid/{puuid}/ids?start=0&count={count}"
DETAIL = BASE + "/lol/match/v5/matches/{match_id}"
TIMEOUT = aiohttp.ClientTimeout(total=10)

QUEUE_NAMES = {
    420: "솔로랭크", 440: "자유랭크", 430: "일반", 400: "일반",
    450: "칼바람", 490: "빠른대전", 1700: "아레나", 1900: "URF",
}

# 한 번에 새로 받아 올 매치 수. 너무 크게 잡으면 첫 조회가 오래 걸린다
FETCH_LIMIT = 12


def queue_label(queue_id: int, game_type: str) -> str:
    if game_type == "CUSTOM_GAME":
        return "내전"
    return QUEUE_NAMES.get(queue_id, "기타")


def summarize(data: dict) -> dict:
    """필요한 것만 남긴다. 매치 원본은 수백 KB 라 그대로 두면 DB 가 부푼다."""
    info = data["info"]
    return {
        "players": [
            {
                "puuid": p["puuid"],
                "name": p.get("riotIdGameName") or p.get("summonerName") or "",
                "tag": p.get("riotIdTagline") or "",
                "champion": p.get("championName", ""),
                "kills": p.get("kills", 0),
                "deaths": p.get("deaths", 0),
                "assists": p.get("assists", 0),
                "win": bool(p.get("win")),
                "team": p.get("teamId", 0),
                "position": p.get("teamPosition") or "",
            }
            for p in info["participants"]
        ],
    }


async def _get(session: aiohttp.ClientSession, url: str):
    async with session.get(url, timeout=TIMEOUT) as r:
        if r.status == 429:
            wait = float(r.headers.get("Retry-After", "2"))
            log.warning("라이엇 레이트리밋 — %.0f초", wait)
            return "ratelimited", wait
        if r.status != 200:
            return None, r.status
        return await r.json(), 200


async def refresh(db, puuid: str) -> int:
    """새 매치를 받아 캐시에 채운다. 새로 받은 개수를 돌려준다.

    최근에 확인했으면 아무것도 안 한다. 페이지를 열 때마다 라이엇을
    두드릴 이유가 없다.
    """
    if not RIOT_API_KEY:
        return 0

    synced = await db.match_synced_at(puuid)
    if synced:
        try:
            last = dt.datetime.fromisoformat(synced)
        except ValueError:
            last = None
        if last and (dt.datetime.now(last.tzinfo) - last) < dt.timedelta(
            hours=Web.MATCH_CACHE_HOURS
        ):
            return 0

    headers = {"X-Riot-Token": RIOT_API_KEY}
    added = 0
    async with aiohttp.ClientSession(headers=headers) as s:
        ids, status = await _get(s, IDS.format(puuid=puuid, count=FETCH_LIMIT))
        if ids == "ratelimited" or not isinstance(ids, list):
            log.info("매치 목록을 못 받았습니다 (%s)", status)
            return 0

        await db.link_matches(puuid, ids)

        for match_id in ids:
            if await db.cached_match(match_id) is not None:
                continue                      # 이미 받아 둔 매치는 건너뛴다
            data, status = await _get(s, DETAIL.format(match_id=match_id))
            if data == "ratelimited":
                await asyncio.sleep(status)
                continue
            if not isinstance(data, dict):
                continue
            info = data["info"]
            await db.save_match(
                match_id,
                int(info.get("queueId", 0)),
                str(info.get("gameType", "")),
                int(info.get("gameStartTimestamp", 0)),
                int(info.get("gameDuration", 0)),
                json.dumps(summarize(data), ensure_ascii=False),
            )
            added += 1
            await asyncio.sleep(1.3)          # 레이트리밋을 넉넉히 밑돈다

    await db.mark_match_synced(puuid)
    if added:
        log.info("매치 %d건 새로 받음 (%s…)", added, puuid[:12])
    return added


async def recent(db, puuid: str, limit: int, only_custom: bool = False) -> list[dict]:
    """캐시에서 전적을 꺼내 보여 줄 형태로 만든다."""
    rows = await db.matches_of(puuid, limit * 3 if only_custom else limit)
    out: list[dict] = []
    for row in rows:
        game_type = str(row["game_type"] or "")
        if only_custom and game_type != "CUSTOM_GAME":
            continue
        try:
            payload = json.loads(row["payload"])
        except ValueError:
            continue
        me = next((p for p in payload["players"] if p["puuid"] == puuid), None)
        if me is None:
            continue
        deaths = me["deaths"] or 1
        out.append({
            "match_id": row["match_id"],
            "queue": queue_label(int(row["queue_id"] or 0), game_type),
            "custom": game_type == "CUSTOM_GAME",
            "champion": me["champion"],
            "kda": f"{me['kills']}/{me['deaths']}/{me['assists']}",
            "ratio": round((me["kills"] + me["assists"]) / deaths, 2),
            "win": me["win"],
            "minutes": int(row["duration"] or 0) // 60,
            "when": dt.datetime.fromtimestamp(
                int(row["started_at"] or 0) / 1000, dt.timezone.utc
            ),
            "teammates": [
                p for p in payload["players"]
                if p["team"] == me["team"] and p["puuid"] != puuid
            ],
        })
        if len(out) >= limit:
            break
    return out


def totals(games: list[dict]) -> dict:
    wins = sum(1 for g in games if g["win"])
    return {
        "played": len(games),
        "wins": wins,
        "losses": len(games) - wins,
        "rate": round(wins / len(games) * 100) if games else 0,
    }
