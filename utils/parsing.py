"""서버 닉네임 양식 파싱.

양식: `롤닉네임#태그/이번년도최고티어/주라인 부라인`
예시: `홍길동#KR1/M405/MID AD`

티어는 약자(U I B S G P E D M GM C)를 쓰고, 마스터 이상은 뒤에 LP 를 붙일 수 있다.
라인은 TOP JG MID AD SUP 을 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from config import LANE_NAMES, TIER_NAMES

# 긴 약자(GM)를 먼저 시도해야 G 와 헷갈리지 않는다.
TIER_PATTERN = re.compile(r"^(GM|C|M|D|E|P|G|S|B|I|U)\s*(\d+)?$", re.IGNORECASE)

# 사람들이 흔히 쓰는 다른 표기도 받아준다.
LANE_ALIASES: dict[str, str] = {
    "TOP": "TOP",
    "T": "TOP",
    "탑": "TOP",
    "JG": "JG",
    "JUG": "JG",
    "JUNGLE": "JG",
    "JGL": "JG",
    "정글": "JG",
    "MID": "MID",
    "M": "MID",
    "MIDDLE": "MID",
    "미드": "MID",
    "AD": "AD",
    "ADC": "AD",
    "BOT": "AD",
    "BOTTOM": "AD",
    "원딜": "AD",
    "SUP": "SUP",
    "SUPPORT": "SUP",
    "SUPP": "SUP",
    "서폿": "SUP",
}

RIOT_TAG_PATTERN = re.compile(r"^[A-Za-z0-9]{2,5}$")


@dataclass(slots=True)
class ProfileFormat:
    """파싱 결과."""

    game_name: str
    tag_line: str
    tier: str                       # 약자 (예: "M")
    lp: Optional[int]               # 티어 뒤 숫자 (없으면 None)
    main_lane: str                  # 약자 (예: "MID")
    sub_lane: Optional[str]         # 약자 (예: "AD")
    raw: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"

    @property
    def tier_name(self) -> str:
        return TIER_NAMES.get(self.tier, self.tier)

    @property
    def main_lane_name(self) -> str:
        return LANE_NAMES.get(self.main_lane, self.main_lane)

    @property
    def sub_lane_name(self) -> Optional[str]:
        if self.sub_lane is None:
            return None
        return LANE_NAMES.get(self.sub_lane, self.sub_lane)


class FormatError(ValueError):
    """양식이 맞지 않을 때. 메시지를 그대로 안내에 쓴다."""


def parse_lane(token: str) -> Optional[str]:
    return LANE_ALIASES.get(token.strip().upper())


def parse_profile_format(text: str) -> ProfileFormat:
    """닉네임 양식 문자열을 해석한다. 실패하면 FormatError."""
    raw = " ".join(text.strip().split())
    if not raw:
        raise FormatError("내용이 비어 있습니다.")

    # 티어와 라인은 항상 뒤쪽이므로 오른쪽부터 자른다 (롤 닉네임에 / 가 있어도 안전)
    parts = raw.rsplit("/", 2)
    if len(parts) != 3:
        raise FormatError(
            "`/` 로 구분된 세 부분이 필요합니다. "
            "`롤닉네임#태그/티어/주라인 부라인` 형태로 적어 주세요."
        )

    riot_part, tier_part, lane_part = (p.strip() for p in parts)

    # 1) 롤 닉네임 # 태그
    if "#" not in riot_part:
        raise FormatError("롤 닉네임 뒤에 `#태그`를 붙여 주세요. (예: `홍길동#KR1`)")
    game_name, tag_line = riot_part.rsplit("#", 1)
    game_name = game_name.strip()
    tag_line = tag_line.strip()
    if not game_name:
        raise FormatError("롤 닉네임이 비어 있습니다.")
    if not RIOT_TAG_PATTERN.match(tag_line):
        raise FormatError("`#태그`는 영문·숫자 2~5자여야 합니다. (예: `KR1`)")

    # 2) 티어
    tier_match = TIER_PATTERN.match(tier_part.replace(" ", ""))
    if tier_match is None:
        raise FormatError(
            "티어 약자를 확인해 주세요. "
            "`U I B S G P E D M GM C` 중 하나입니다. (예: `M405`, `D`)"
        )
    tier = tier_match.group(1).upper()
    lp = int(tier_match.group(2)) if tier_match.group(2) else None

    # 3) 주 라인 / 부 라인
    lane_tokens = lane_part.split()
    if not lane_tokens:
        raise FormatError("주 라인을 적어 주세요. (`TOP JG MID AD SUP`)")
    if len(lane_tokens) > 2:
        raise FormatError("라인은 주 라인과 부 라인, 최대 두 개까지만 적을 수 있습니다.")

    main_lane = parse_lane(lane_tokens[0])
    if main_lane is None:
        raise FormatError(
            f"주 라인 `{lane_tokens[0]}` 을(를) 알아볼 수 없습니다. "
            "`TOP JG MID AD SUP` 중 하나로 적어 주세요."
        )

    sub_lane = None
    if len(lane_tokens) == 2:
        sub_lane = parse_lane(lane_tokens[1])
        if sub_lane is None:
            raise FormatError(
                f"부 라인 `{lane_tokens[1]}` 을(를) 알아볼 수 없습니다. "
                "`TOP JG MID AD SUP` 중 하나로 적어 주세요."
            )
        if sub_lane == main_lane:
            raise FormatError("주 라인과 부 라인은 서로 달라야 합니다.")

    return ProfileFormat(
        game_name=game_name,
        tag_line=tag_line,
        tier=tier,
        lp=lp,
        main_lane=main_lane,
        sub_lane=sub_lane,
        raw=raw,
    )


def matches_format(text: str) -> bool:
    """닉네임이 양식에 맞는지만 빠르게 확인."""
    try:
        parse_profile_format(text)
    except FormatError:
        return False
    return True


def split_riot_id(text: str) -> tuple[str, str]:
    """`롤닉네임#태그` 문자열을 분리한다."""
    value = text.strip()
    if "#" not in value:
        raise FormatError("`롤닉네임#태그` 형태로 입력해 주세요. (예: `홍길동#KR1`)")
    game_name, tag_line = value.rsplit("#", 1)
    game_name = game_name.strip()
    tag_line = tag_line.strip()
    if not game_name:
        raise FormatError("롤 닉네임이 비어 있습니다.")
    if not RIOT_TAG_PATTERN.match(tag_line):
        raise FormatError("`#태그`는 영문·숫자 2~5자여야 합니다. (예: `KR1`)")
    return game_name, tag_line
