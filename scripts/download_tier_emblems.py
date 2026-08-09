"""라이엇 티어 엠블럼을 내려받아 `assets/tiers/` 에 저장한다.

프로필 카드는 이 폴더에 엠블럼이 있으면 그것을 쓰고, 없으면 직접 그린
크레스트를 대신 표시한다. 한 번만 실행하면 된다.

    python scripts/download_tier_emblems.py

이미지는 Community Dragon 에서 가져온다. (라이엇 API 키가 필요 없다)
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TARGET_DIR = BASE_DIR / "assets" / "tiers"

SOURCE = (
    "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/"
    "global/default/images/ranked-mini-crests/{slug}.svg"
)
# SVG 는 Pillow 가 읽지 못하므로 PNG 로 제공되는 경로를 쓴다.
PNG_SOURCE = (
    "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-shared-components/"
    "global/default/{slug}.png"
)

TIERS = (
    "iron",
    "bronze",
    "silver",
    "gold",
    "platinum",
    "emerald",
    "diamond",
    "master",
    "grandmaster",
    "challenger",
)


def fetch(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "lolwithmemainbot"})
    try:
        with urllib.request.urlopen(request, timeout=20) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    saved, failed = 0, []
    for tier in TIERS:
        target = TARGET_DIR / f"{tier}.png"
        if target.exists():
            print(f"건너뜀  {tier} (이미 있음)")
            saved += 1
            continue

        data = fetch(PNG_SOURCE.format(slug=tier))
        if data is None:
            failed.append(tier)
            print(f"실패    {tier}")
            continue

        target.write_bytes(data)
        saved += 1
        print(f"저장    {tier} → {target.relative_to(BASE_DIR)}")

    print(f"\n완료: {saved}/{len(TIERS)}개")
    if failed:
        print(
            "\n내려받지 못한 티어: " + ", ".join(failed) + "\n"
            "Community Dragon 경로가 바뀌었을 수 있습니다.\n"
            f"직접 PNG 를 구해서 {TARGET_DIR} 에 `master.png` 처럼 넣어 주세요.\n"
            "엠블럼이 없어도 봇은 직접 그린 크레스트로 대신 표시합니다."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
