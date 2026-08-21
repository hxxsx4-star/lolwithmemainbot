"""라이엇 Data Dragon 에서 챔피언 초상화를 가져온다.

프로필 카드에 산 챔피언을 얹기 위한 것이다. 카드를 그릴 때마다 받아 오면
느리고 CDN 에도 미안하므로 **받은 것은 파일로 남겨 두고 재사용한다.**
초상화는 패치가 나와도 거의 그대로라 캐시를 오래 둬도 문제가 없다.

버전은 하루에 한 번만 확인한다. 확인에 실패하면 마지막으로 알던 버전을
계속 쓴다. 초상화 하나 못 받는다고 카드까지 못 그리면 안 되기 때문이다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

import aiohttp

from config import DATA_DIR

log = logging.getLogger("mainbot.ddragon")

VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
PORTRAIT_URL = (
    "https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{champion}.png"
)

CACHE_DIR = DATA_DIR / "champions"
VERSION_TTL = dt.timedelta(days=1)
TIMEOUT = aiohttp.ClientTimeout(total=8)

# 설정의 키(`LEESIN`)와 Data Dragon 의 ID(`LeeSin`)는 대소문자만 다르다.
# 처음 한 번 목록을 받아 맞춰 두고 계속 쓴다.
_ids: dict[str, str] = {}
_version: Optional[str] = None
_checked_at: Optional[dt.datetime] = None
_lock = asyncio.Lock()


def _normalize(text: str) -> str:
    return text.upper().replace(" ", "").replace("'", "").replace(".", "")


async def _refresh(session: aiohttp.ClientSession) -> None:
    """최신 버전과 챔피언 ID 목록을 받아 둔다."""
    global _version, _checked_at, _ids

    async with session.get(VERSIONS_URL, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"버전 조회 실패 {r.status}")
        version = (await r.json())[0]

    url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/champion.json"
    async with session.get(url, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"챔피언 목록 조회 실패 {r.status}")
        data = (await r.json())["data"]

    _ids = {_normalize(cid): cid for cid in data}
    _version = version
    _checked_at = dt.datetime.now(dt.timezone.utc)
    log.info("Data Dragon %s · 챔피언 %d종", version, len(_ids))


async def portrait(key: str) -> Optional[bytes]:
    """설정의 챔피언 키로 초상화 PNG 를 돌려준다. 못 구하면 None.

    실패해도 예외를 올리지 않는다. 카드에 챔피언만 빠질 뿐 나머지는 그대로
    그려져야 한다.
    """
    if not key:
        return None

    cached = CACHE_DIR / f"{key}.png"
    if cached.is_file():
        try:
            return cached.read_bytes()
        except OSError:
            pass

    async with _lock:
        try:
            async with aiohttp.ClientSession() as session:
                stale = (
                    _checked_at is None
                    or dt.datetime.now(dt.timezone.utc) - _checked_at > VERSION_TTL
                )
                if stale or not _ids:
                    try:
                        await _refresh(session)
                    except Exception as exc:
                        # 예전에 받아 둔 버전이 있으면 그걸로 계속 간다
                        log.debug("Data Dragon 갱신 실패: %s", exc)
                        if _version is None:
                            return None

                champion = _ids.get(_normalize(key))
                if champion is None or _version is None:
                    log.debug("Data Dragon 에서 %s 를 찾지 못했습니다.", key)
                    return None

                url = PORTRAIT_URL.format(version=_version, champion=champion)
                async with session.get(url, timeout=TIMEOUT) as r:
                    if r.status != 200:
                        return None
                    data = await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.debug("초상화를 받지 못했습니다 (%s): %s", key, exc)
            return None

    if not data:
        return None
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError as exc:
        log.debug("초상화 캐시 저장 실패: %s", exc)
    return data
