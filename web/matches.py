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


# 챔피언 초상화는 라이엇 CDN 을 그대로 쓴다. 우리가 중계하면 대역폭만 쓰고
# 느려진다. 버전은 패치마다 바뀌지만 초상화 자체는 거의 그대로라 하루에
# 한 번만 확인한다.
_version: str | None = None
_version_at: dt.datetime | None = None


async def ddragon_version() -> str:
    global _version, _version_at
    fresh = _version_at and (dt.datetime.now(dt.timezone.utc) - _version_at
                             < dt.timedelta(days=1))
    if _version and fresh:
        return _version
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://ddragon.leagueoflegends.com/api/versions.json",
                timeout=TIMEOUT,
            ) as r:
                if r.status == 200:
                    _version = (await r.json())[0]
                    _version_at = dt.datetime.now(dt.timezone.utc)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    # 못 받아도 화면은 떠야 한다. 마지막으로 알던 값이나 알려진 버전을 쓴다
    return _version or "15.1.1"


def portrait_url(version: str, champion: str) -> str:
    return (
        f"https://ddragon.leagueoflegends.com/cdn/{version}"
        f"/img/champion/{champion}.png"
    )


def team_luck(me: dict, teammates: list[dict]) -> tuple[str, str]:
    """팀운을 한마디로. (등급, 설명)

    내 KDA 가 팀 평균보다 뚜렷이 높은데 졌으면 팀운이 나빴다고 본다.
    반대로 내가 팀 평균보다 못했는데 이겼으면 팀 덕을 본 것이다.
    딜량 같은 건 저장하지 않으므로 KDA 로만 판단한다.
    """
    if not teammates:
        return "", ""

    def ratio(p: dict) -> float:
        return (p["kills"] + p["assists"]) / max(1, p["deaths"])

    mine = ratio(me)
    others = sum(ratio(p) for p in teammates) / len(teammates)
    gap = mine - others

    if not me["win"] and gap >= 1.5:
        return "나쁨", "내가 팀 평균보다 잘했는데 졌습니다"
    if me["win"] and gap <= -1.5:
        return "좋음", "팀이 캐리해 준 판입니다"
    if not me["win"] and gap <= -1.5:
        return "", "내 지표가 팀 평균보다 낮았습니다"
    return "", ""


# 저장 형식이 바뀌면 올린다. 예전 형식으로 받아 둔 매치는 다시 받는다
PAYLOAD_VERSION = 2


def summarize(data: dict) -> dict:
    """필요한 것만 남긴다. 매치 원본은 수백 KB 라 그대로 두면 DB 가 부푼다.

    "몇 인분 했나" 를 계산하려면 KDA 만으로는 모자라다. 딜과 골드, 팀 전체
    킬까지 있어야 **내가 팀에서 차지한 몫**을 낼 수 있다.
    """
    info = data["info"]
    return {
        "v": PAYLOAD_VERSION,
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
                "damage": p.get("totalDamageDealtToChampions", 0),
                "taken": p.get("totalDamageTaken", 0),
                "gold": p.get("goldEarned", 0),
                "cs": p.get("totalMinionsKilled", 0) + p.get("neutralMinionsKilled", 0),
                "vision": p.get("visionScore", 0),
                "level": p.get("champLevel", 0),
            }
            for p in info["participants"]
        ],
    }


def _share(mine: float, team_total: float) -> float:
    """팀 안에서 내가 차지한 비율. 5명이 고르면 0.2 다."""
    return (mine / team_total) if team_total else 0.0


def performance(me: dict, team: list[dict], minutes: int) -> dict:
    """"몇 인분 했나" 를 낸다.

    다섯이 고르게 나눠 가지면 각자 20% 다. 그래서 **내 비중 ÷ 20%** 가 곧
    몇 인분인지가 된다. 딜 비중을 중심으로 보되 킬 관여와 골드도 함께 본다 —
    탱커나 서포터는 딜이 적어도 제 몫을 하기 때문이다.
    """
    everyone = team + [me]
    dmg = _share(me["damage"], sum(p["damage"] for p in everyone))
    gold = _share(me["gold"], sum(p["gold"] for p in everyone))
    taken = _share(me["taken"], sum(p["taken"] for p in everyone))
    team_kills = sum(p["kills"] for p in everyone)
    kp = ((me["kills"] + me["assists"]) / team_kills) if team_kills else 0.0

    # 딜을 절반, 나머지를 나눠 담는다. 서폿·탱커가 억울하지 않도록
    # 받은 피해와 킬 관여를 함께 센다
    score = dmg * 0.5 + kp * 0.25 + gold * 0.15 + taken * 0.10
    shares = round(score / 0.2, 2)

    return {
        "shares": shares,
        "damage_pct": round(dmg * 100),
        "kp_pct": round(kp * 100),
        "gold_pct": round(gold * 100),
        "dpm": round(me["damage"] / minutes) if minutes else 0,
        "cs_per_min": round(me["cs"] / minutes, 1) if minutes else 0,
        "grade": (
            "하드캐리" if shares >= 1.6 else
            "잘함" if shares >= 1.2 else
            "제 몫" if shares >= 0.85 else
            "아쉬움"
        ),
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


def _is_current(row) -> bool:
    """저장 형식이 지금 것인지. 예전 것이면 다시 받아야 한다."""
    try:
        return json.loads(row["payload"]).get("v", 1) >= PAYLOAD_VERSION
    except (ValueError, TypeError):
        return False


async def refresh(db, puuid: str) -> int:
    """새 매치를 받아 캐시에 채운다. 새로 받은 개수를 돌려준다.

    최근에 확인했으면 아무것도 안 한다. 페이지를 열 때마다 라이엇을
    두드릴 이유가 없다.
    """
    if not RIOT_API_KEY:
        return 0

    # 저장 형식이 바뀐 뒤라면 쿨다운을 무시하고 다시 받는다. 안 그러면
    # 예전 형식으로 담긴 매치가 영영 갱신되지 않아 새 지표가 안 나온다
    cached = await db.matches_of(puuid, 1)
    stale_format = bool(cached) and not _is_current(cached[0])

    synced = await db.match_synced_at(puuid)
    if synced and not stale_format:
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
            cached = await db.cached_match(match_id)
            if cached is not None and _is_current(cached):
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
    version = await ddragon_version()
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

        minutes = int(row["duration"] or 0) // 60
        mates = [
            p for p in payload["players"]
            if p["team"] == me["team"] and p["puuid"] != puuid
        ]
        detailed = payload.get("v", 1) >= PAYLOAD_VERSION
        luck, luck_note = team_luck(me, mates)

        out.append({
            "match_id": row["match_id"],
            "queue": queue_label(int(row["queue_id"] or 0), game_type),
            "custom": game_type == "CUSTOM_GAME",
            "champion": me["champion"],
            "icon": portrait_url(version, me["champion"]) if me["champion"] else "",
            "kda": f"{me['kills']}/{me['deaths']}/{me['assists']}",
            "ratio": round((me["kills"] + me["assists"]) / max(1, me["deaths"]), 2),
            "win": me["win"],
            "minutes": minutes,
            "when": dt.datetime.fromtimestamp(
                int(row["started_at"] or 0) / 1000, dt.timezone.utc
            ),
            "perf": performance(me, mates, minutes) if detailed else None,
            "luck": luck,
            "luck_note": luck_note,
            "teammates": [
                {**p, "icon": portrait_url(version, p["champion"])}
                for p in mates
            ],
        })
        if len(out) >= limit:
            break
    return out


def totals(games: list[dict]) -> dict:
    wins = sum(1 for g in games if g["win"])
    scored = [g for g in games if g["perf"]]
    avg = (
        round(sum(g["perf"]["shares"] for g in scored) / len(scored), 2)
        if scored else 0
    )
    return {
        "played": len(games),
        "wins": wins,
        "losses": len(games) - wins,
        "rate": round(wins / len(games) * 100) if games else 0,
        "shares": avg,
        "unlucky": sum(1 for g in games if g["luck"] == "나쁨"),
        "carried": sum(1 for g in games if g["luck"] == "좋음"),
    }
