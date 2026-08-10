"""서버 닉네임 양식 파싱.

양식: `롤닉네임#태그/이번년도최고티어/주라인 부라인`
예시: `홍길동#KR1/M405/MID AD`

티어와 라인은 **대문자 약자만** 받는다. 서버 닉네임이 그대로 이 문자열이 되기
때문에, 소문자나 다른 표기를 그냥 통과시키면 사람마다 닉네임 모양이 달라진다.
대신 틀렸을 때는 무엇을 어떻게 고쳐야 하는지 하나하나 짚어 준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from config import LANE_NAMES, TIER_NAMES

# 티어 약자 (긴 것부터 확인해야 GM 과 G 가 헷갈리지 않는다)
TIER_CODES = ("GM", "C", "M", "D", "E", "P", "G", "S", "B", "I", "U")
TIER_PATTERN = re.compile(rf"^({'|'.join(TIER_CODES)})(\d+)?$")

# 마스터 이상은 단계가 없고 LP 만 있다. 그 아래는 1~4 단계로 나뉜다.
# 즉 `M405` 는 마스터 405LP 지만 `E4` 는 에메랄드 4단계다.
APEX_TIERS = frozenset({"M", "GM", "C"})
MAX_DIVISION = 4
MAX_LP = 9999

LANE_CODES = ("TOP", "JG", "MID", "AD", "SUP")

# 자주 쓰는 다른 표기 → 올바른 약자. 통과시키지는 않고 "이렇게 적어 주세요" 로 안내한다.
TIER_SUGGESTIONS: dict[str, str] = {
    "UNRANK": "U", "UNRANKED": "U", "언랭": "U", "언랭크": "U",
    "IRON": "I", "아이언": "I",
    "BRONZE": "B", "브론즈": "B",
    "SILVER": "S", "실버": "S",
    "GOLD": "G", "골드": "G",
    "PLAT": "P", "PLATINUM": "P", "플레": "P", "플래티넘": "P", "플레티넘": "P",
    "EME": "E", "EMERALD": "E", "에메랄드": "E",
    "DIA": "D", "DIAMOND": "D", "다이아": "D", "다이아몬드": "D",
    "MASTER": "M", "마스터": "M",
    "GRANDMASTER": "GM", "그마": "GM", "그랜드마스터": "GM",
    "CHALL": "C", "CHALLENGER": "C", "챌린저": "C", "챌": "C",
}

LANE_SUGGESTIONS: dict[str, str] = {
    "T": "TOP", "탑": "TOP", "TOPLANE": "TOP",
    "JUG": "JG", "JGL": "JG", "JUNGLE": "JG", "정글": "JG", "졍글": "JG",
    "M": "MID", "MIDDLE": "MID", "미드": "MID",
    "ADC": "AD", "BOT": "AD", "BOTTOM": "AD", "원딜": "AD", "원거리": "AD",
    "SUPPORT": "SUP", "SUPP": "SUP", "SP": "SUP", "서폿": "SUP", "서포터": "SUP",
}

# 전각 문자로 잘못 입력되는 경우가 잦아 먼저 바꿔 준다
FULLWIDTH = {"／": "/", "＃": "#", "∕": "/", "﹡": "*"}

TAG_MAX_LENGTH = 10
TAG_FORBIDDEN = "#/"

TAG_ERROR = (
    f"`#태그`는 공백 포함 1~{TAG_MAX_LENGTH}자여야 하고 `#` `/` 는 쓸 수 없습니다. "
    "(예: `KR1`, `올 킬`)"
)


class FormatError(ValueError):
    """양식 오류. 잘못된 곳을 여러 개 모아서 한 번에 알려 준다."""

    def __init__(self, *issues: str) -> None:
        self.issues: list[str] = [i for i in issues if i]
        super().__init__(self.text)

    @property
    def text(self) -> str:
        if len(self.issues) == 1:
            return self.issues[0]
        return "\n".join(f"• {issue}" for issue in self.issues)

    def __str__(self) -> str:
        return self.text


def valid_tag(tag: str) -> bool:
    """`#태그` 로 쓸 수 있는 문자열인지."""
    if not 1 <= len(tag) <= TAG_MAX_LENGTH:
        return False
    if any(ch in tag for ch in TAG_FORBIDDEN):
        return False
    return all(ch.isalnum() or ch == " " for ch in tag)


def normalize(text: str) -> str:
    """전각 기호를 반각으로 바꾸고 공백을 정리한다."""
    for wide, narrow in FULLWIDTH.items():
        text = text.replace(wide, narrow)
    return " ".join(text.strip().split())


@dataclass(slots=True)
class ProfileFormat:
    """파싱 결과."""

    game_name: str
    tag_line: str
    tier: str                # 약자 (예: "M")
    number: Optional[int]    # 티어 뒤 숫자. 마스터 이상이면 LP, 아니면 단계
    main_lane: str           # 약자 (예: "MID")
    sub_lane: Optional[str]  # 약자 (예: "AD")
    raw: str

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"

    @property
    def is_apex(self) -> bool:
        """마스터 · 그랜드마스터 · 챌린저인지 (단계 없이 LP 만 있는 구간)."""
        return self.tier in APEX_TIERS

    @property
    def lp(self) -> Optional[int]:
        """리그 포인트. 마스터 이상에서만 의미가 있다."""
        return self.number if self.is_apex else None

    @property
    def division(self) -> Optional[int]:
        """티어 단계(1~4). 마스터 미만에서만 의미가 있다."""
        return None if self.is_apex else self.number

    @property
    def canonical(self) -> str:
        """서버 닉네임으로 쓸 정규 표기."""
        tier = f"{self.tier}{self.number}" if self.number is not None else self.tier
        lanes = self.main_lane + (f" {self.sub_lane}" if self.sub_lane else "")
        return f"{self.riot_id}/{tier}/{lanes}"

    @property
    def tier_name(self) -> str:
        return TIER_NAMES.get(self.tier, self.tier)

    @property
    def tier_display(self) -> str:
        """사람에게 보여줄 티어 표기. `마스터 405LP` / `에메랄드 4`."""
        if self.number is None:
            return self.tier_name
        if self.is_apex:
            return f"{self.tier_name} {self.number}LP"
        return f"{self.tier_name} {self.number}"

    @property
    def main_lane_name(self) -> str:
        return LANE_NAMES.get(self.main_lane, self.main_lane)

    @property
    def sub_lane_name(self) -> Optional[str]:
        if self.sub_lane is None:
            return None
        return LANE_NAMES.get(self.sub_lane, self.sub_lane)


# ------------------------------------------------------------------ 티어


def _check_tier_number(code: str, number: Optional[int]) -> Optional[str]:
    """티어 뒤 숫자가 그 티어에 맞는 값인지 확인한다.

    마스터 이상은 LP, 그 아래는 1~4 단계다. 같은 자리에 오는 숫자지만 뜻이
    다르므로 여기서 갈라 준다.
    """
    if number is None:
        return None

    name = TIER_NAMES.get(code, code)
    if code == "U":
        return f"언랭크는 뒤에 숫자를 붙이지 않습니다. `{code}{number}` → **`U`**"
    if code in APEX_TIERS:
        if number > MAX_LP:
            return f"{name}의 LP `{number}` 가 너무 큽니다. (예: `{code}405`)"
        return None
    if not 1 <= number <= MAX_DIVISION:
        return (
            f"{name}는 **1~{MAX_DIVISION} 단계**입니다. `{code}{number}` 는 없습니다.\n"
            f"　(가장 높은 단계가 1입니다. 예: `{code}1`)"
        )
    return None


def _check_tier(part: str) -> tuple[Optional[str], Optional[int], Optional[str]]:
    """티어 조각을 확인한다. (약자, LP, 오류 메시지)"""
    raw = part.replace(" ", "")
    if not raw:
        return None, None, "티어가 비어 있습니다. `U I B S G P E D M GM C` 중 하나를 적어 주세요."

    match = TIER_PATTERN.match(raw)
    if match is not None:
        code = match.group(1)
        number = int(match.group(2)) if match.group(2) else None
        return (code, number, _check_tier_number(code, number))

    # 소문자로 썼는지 먼저 확인한다 (mid, gm 처럼 흔한 실수)
    upper = TIER_PATTERN.match(raw.upper())
    if upper is not None:
        fixed = upper.group(1) + (upper.group(2) or "")
        return None, None, (
            f"티어를 소문자로 쓰셨습니다. `{raw}` → **`{fixed}`** 처럼 "
            "**대문자**로 적어 주세요."
        )

    # 티어 이름을 그대로 쓴 경우
    letters = re.sub(r"\d", "", raw).upper()
    digits = re.sub(r"\D", "", raw)
    if letters in TIER_SUGGESTIONS:
        code = TIER_SUGGESTIONS[letters]
        fixed = f"{code}{digits}" if digits else code
        return None, None, (
            f"티어는 약자로 적어 주세요. `{raw}` → **`{fixed}`** "
            f"({TIER_NAMES.get(code, code)})"
        )

    return None, None, (
        f"티어 `{raw}` 를 알아볼 수 없습니다. "
        "`U I B S G P E D M GM C` 중 하나를 **대문자**로 적어 주세요.\n"
        "　(아이언~다이아는 뒤에 단계 1~4: `E4`, 마스터 이상은 LP: `M405`)"
    )


# ------------------------------------------------------------------ 라인


def _check_lane(token: str, label: str) -> tuple[Optional[str], Optional[str]]:
    """라인 한 개를 확인한다. (약자, 오류 메시지)"""
    if token in LANE_CODES:
        return token, None

    upper = token.upper()
    if upper in LANE_CODES:
        return None, (
            f"{label}을 소문자로 쓰셨습니다. `{token}` → **`{upper}`** 처럼 "
            "**대문자**로 적어 주세요."
        )
    if upper in LANE_SUGGESTIONS:
        return None, (
            f"{label}은 `{LANE_SUGGESTIONS[upper]}` 로 적어 주세요. "
            f"`{token}` → **`{LANE_SUGGESTIONS[upper]}`**"
        )
    return None, (
        f"{label} `{token}` 을(를) 알아볼 수 없습니다. "
        "`TOP JG MID AD SUP` 중 하나를 **대문자**로 적어 주세요."
    )


def _check_lanes(part: str) -> tuple[Optional[str], Optional[str], list[str]]:
    """라인 조각을 확인한다. (주 라인, 부 라인, 오류 목록)"""
    tokens = part.split()
    if not tokens:
        return None, None, ["주 라인을 적어 주세요. (`TOP JG MID AD SUP`)"]
    if len(tokens) > 2:
        return None, None, [
            "라인은 주 라인과 부 라인, **최대 두 개**까지만 적을 수 있습니다. "
            f"(`{part}` — {len(tokens)}개를 적으셨습니다)"
        ]

    issues: list[str] = []
    main, error = _check_lane(tokens[0], "주 라인")
    if error:
        issues.append(error)

    sub = None
    if len(tokens) == 2:
        sub, error = _check_lane(tokens[1], "부 라인")
        if error:
            issues.append(error)

    if main is not None and sub is not None and main == sub:
        issues.append(
            f"주 라인과 부 라인이 똑같습니다. (`{main}`) 서로 다르게 적어 주세요."
        )
    return main, sub, issues


# ------------------------------------------------------------------ 본체


def parse_profile_format(text: str) -> ProfileFormat:
    """닉네임 양식 문자열을 해석한다. 실패하면 잘못된 곳을 모두 담아 FormatError."""
    raw = normalize(text)
    if not raw:
        raise FormatError("내용이 비어 있습니다.")

    # 티어와 라인은 항상 뒤쪽이므로 오른쪽부터 자른다 (롤 닉네임에 / 가 있어도 안전)
    parts = raw.rsplit("/", 2)
    if len(parts) != 3:
        raise FormatError(
            f"`/` 가 {len(parts) - 1}개뿐입니다. "
            "`롤닉네임#태그/티어/주라인 부라인` 처럼 **`/` 두 개**로 나눠 주세요."
        )

    riot_part, tier_part, lane_part = (p.strip() for p in parts)
    issues: list[str] = []

    # 1) 롤 닉네임 # 태그
    game_name = tag_line = ""
    if "#" not in riot_part:
        issues.append(
            f"롤 닉네임 뒤에 `#태그`가 없습니다. `{riot_part}` → "
            f"**`{riot_part}#KR1`** 처럼 적어 주세요."
        )
    else:
        game_name, tag_line = (s.strip() for s in riot_part.rsplit("#", 1))
        if not game_name:
            issues.append("롤 닉네임이 비어 있습니다. `#` 앞에 닉네임을 적어 주세요.")
        if not valid_tag(tag_line):
            issues.append(TAG_ERROR + f" (지금: `{tag_line}`)")

    # 2) 티어
    tier, number, tier_error = _check_tier(tier_part)
    if tier_error:
        issues.append(tier_error)

    # 3) 주 라인 / 부 라인
    main_lane, sub_lane, lane_issues = _check_lanes(lane_part)
    issues.extend(lane_issues)

    if issues:
        raise FormatError(*issues)

    return ProfileFormat(
        game_name=game_name,
        tag_line=tag_line,
        tier=tier,           # type: ignore[arg-type]
        number=number,
        main_lane=main_lane,  # type: ignore[arg-type]
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
    value = normalize(text)
    if "#" not in value:
        raise FormatError("`롤닉네임#태그` 형태로 입력해 주세요. (예: `홍길동#KR1`)")
    game_name, tag_line = (s.strip() for s in value.rsplit("#", 1))
    if not game_name:
        raise FormatError("롤 닉네임이 비어 있습니다.")
    if not valid_tag(tag_line):
        raise FormatError(TAG_ERROR + f" (지금: `{tag_line}`)")
    return game_name, tag_line
