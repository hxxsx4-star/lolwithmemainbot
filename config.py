"""롤 같이 하자 - 메인봇 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
ASSET_DIR = BASE_DIR / "assets"
FONT_DIR = ASSET_DIR / "fonts"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "lolwithme.sqlite3"

TOKEN = os.getenv("DISCORD_TOKEN", "")
RIOT_API_KEY = os.getenv("RIOT_API_KEY", "")

_guild = os.getenv("GUILD_ID", "").strip()
GUILD_ID = int(_guild) if _guild.isdigit() else None

TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))

# 라이엇 계정 API 지역 라우팅 (한국 계정은 asia)
RIOT_ACCOUNT_REGION = os.getenv("RIOT_ACCOUNT_REGION", "asia")
RIOT_PLATFORM = os.getenv("RIOT_PLATFORM", "kr")


class Channels:
    """채널 ID."""

    POINT_LOG = 1535952070207610930        # 포인트 로그 (출석 · 지급 · 차감)
    VOICE_POINT_LOG = 1535952369978834954  # 음성 활동 포인트 지급 로그
    WARN_LOG = 1535953668619116554         # 경고 지급 로그
    WARN_REMOVE_LOG = 1535960186450087957  # 경고 차감 로그
    BACKUP = 1535960305065267210           # 매일 자정 데이터 백업
    REGISTER_LOG = 1535960377014227034     # 롤 닉네임 등록 로그
    TICKET_PANEL = 1536033094442942565     # 티켓(문의함) 생성 채널
    ONBOARDING = 1536033191381569556       # 닉네임 양식 입력 채널
    SCRIM_FORUM = 1536049980836548720      # 내전 포럼 채널
    ROLE_PICKER = 1536153863441223760      # 이모지로 라인 역할을 고르는 채널


class Roles:
    """역할 ID."""

    SCRIM_HOST = 1536050031520514168     # 내전 생성 가능 역할
    UNREGISTERED = 1536052613538250803   # 미등록 인원 역할
    MEMBER = 1536050217332244551         # 등록을 마친 서버원 역할

    # 즐겨 하는 게임 모드 (중복 선택 가능)
    GAME_MODES: dict[str, int] = {
        "SOLO": 1536368670840983602,      # 솔로랭크
        "FLEX": 1536368697260642364,      # 자유랭크
        "FIVE": 1536368725911937054,      # 5인랭크
        "ARAM": 1536368764210253844,      # 칼바람
        "ARAM_AUG": 1536368796367847465,  # 증강 칼바람
    }

    # 봇을 제외한 서버원 전원에게 조건 없이 지급하는 기본 역할
    # (구분선 · 공지 알림 등. 봇이 켜질 때와 입장할 때 자동으로 채워 준다)
    DEFAULT: tuple[int, ...] = (
        1536042110329946252,
        1536042185810645012,
        1536042063026716725,
        1536042027186126848,
        1536110233863327875,
        1536110394265833522,
    )

    # 티어 약자 → 역할 ID
    TIERS: dict[str, int] = {
        "C": 1369251450814988309,   # 챌린저
        "GM": 1369251448369582150,  # 그랜드마스터
        "M": 1369251445882355732,   # 마스터
        "D": 1369251442975707218,   # 다이아몬드
        "E": 1369251429889478676,   # 에메랄드
        "P": 1369251427251130429,   # 플레티넘
        "G": 1369251424113922138,   # 골드
        "S": 1369251378630889584,   # 실버
        "B": 1369251332552134768,   # 브론즈
        "I": 1369251237865717840,   # 아이언
        "U": 1369251029140635668,   # 언랭
    }

    # 주 라인 약자 → 역할 ID
    MAIN_LANES: dict[str, int] = {
        "TOP": 1536110430446162073,
        "JG": 1536110449576386700,
        "MID": 1536110468329115678,
        "AD": 1536110487065067602,
        "SUP": 1536110508057436170,
    }

    # 부 라인 약자 → 역할 ID
    SUB_LANES: dict[str, int] = {
        "TOP": 1536110528676634724,
        "JG": 1536110544602275900,
        "MID": 1536110565275996191,
        "AD": 1536110580778147933,
        "SUP": 1536110603175858266,
    }


# 약자 → 한글 이름
TIER_NAMES: dict[str, str] = {
    "U": "언랭크",
    "I": "아이언",
    "B": "브론즈",
    "S": "실버",
    "G": "골드",
    "P": "플래티넘",
    "E": "에메랄드",
    "D": "다이아몬드",
    "M": "마스터",
    "GM": "그랜드마스터",
    "C": "챌린저",
}

LANE_NAMES: dict[str, str] = {
    "TOP": "탑",
    "JG": "정글",
    "MID": "미드",
    "AD": "원거리 딜러",
    "SUP": "서포터",
}

# 라인 선택 패널에 쓰는 서버 커스텀 이모지
LANE_EMOJIS: dict[str, str] = {
    "TOP": "<:LWM_LINE_1:1536155329476436048>",
    "JG": "<:LWM_LINE_2:1536155353748738088>",
    "MID": "<:LWM_LINE_3:1536155379329662996>",
    "AD": "<:LWM_LINE_4:1536155402683547748>",
    "SUP": "<:LWM_LINE_5:1536155432459051028>",
}

GAME_MODE_NAMES: dict[str, str] = {
    "SOLO": "솔로랭크",
    "FLEX": "자유랭크",
    "FIVE": "5인랭크",
    "ARAM": "칼바람",
    "ARAM_AUG": "증강 칼바람",
}

# 게임 모드 패널 이모지. 서버 커스텀 이모지를 쓰려면 `<:이름:ID>` 형태로 바꾸면 된다.
GAME_MODE_EMOJIS: dict[str, str] = {
    "SOLO": "🗡️",
    "FLEX": "🛡️",
    "FIVE": "🏆",
    "ARAM": "❄️",
    "ARAM_AUG": "✨",
}

# 프로필 카드처럼 폭이 좁은 곳에서 쓰는 짧은 표기
LANE_SHORT: dict[str, str] = {
    "TOP": "탑",
    "JG": "정글",
    "MID": "미드",
    "AD": "원딜",
    "SUP": "서폿",
}


class Economy:
    """경제 시스템 수치."""

    UNIT = "P"
    ATTENDANCE_REWARD = 100      # /출석 보상
    VOICE_INTERVAL_MINUTES = 10  # 음성 포인트 지급 주기
    VOICE_REWARD = 10            # 주기마다 지급할 포인트
    IGNORE_AFK_CHANNEL = True    # 잠수 채널은 적립 제외


class Level:
    """레벨 시스템 수치.

    누구나 Lv.0 에서 시작하고, 필요 경험치는 `BASE_XP + STEP_XP × 현재레벨` 로
    조금씩 늘어난다. (Lv.0→1 = 600XP, Lv.1→2 = 655XP, Lv.2→3 = 710XP …)
    """

    BASE_XP = 600
    STEP_XP = 55

    # 음성: 음성 채널에 있으면 자동으로 오른다. 분당 2.5XP → Lv.1 까지 4시간.
    VOICE_XP_PER_MINUTE = 2.5

    # 채팅: 도배로 올리지 못하도록 쿨타임과 최소 길이를 둔다.
    # 10XP × 쿨타임 60초 → Lv.1 까지 최소 60분(60개). 음성 4시간과 비슷한 무게.
    CHAT_XP_PER_MESSAGE = 10
    CHAT_COOLDOWN_SECONDS = 60
    CHAT_MIN_LENGTH = 2


class Shop:
    """상점 수치."""

    DURATION_DAYS = 30        # 구매 아이템 유지 기간
    THEME_PRICE = 2_000       # 프로필 카드 테마
    SLOGAN_PRICE = 3_000      # 프로필 카드 문구
    COLOR_ROLE_PRICE = 2_000  # 색상 역할
    TEAM_ROLE_PRICE = 15_000  # LCK 응원 역할 (그라데이션이라 프리미엄)
    SLOGAN_MAX_LENGTH = 14    # 카드 좌상단에 들어가는 길이 한계


@dataclass(frozen=True, slots=True)
class TeamSpec:
    """LCK 응원 역할 한 개.

    `color` 와 `secondary` 두 색으로 **그라데이션 역할**을 만든다. 서버에
    그라데이션 기능(`ENHANCED_ROLE_COLORS`)이 없으면 `color` 단색으로 떨어진다.
    두 색 모두 어두운 테마에서 읽히도록 너무 어둡지 않게 잡았다.
    """

    name: str                          # 역할 이름으로도 쓰인다
    color: tuple[int, int, int]        # 기본색 (그라데이션 시작)
    secondary: tuple[int, int, int]    # 그라데이션 끝


# LCK 팀 응원 역할.
# 팀 이름과 색은 스폰서가 바뀌면 함께 바뀌므로, 시즌마다 여기를 손보면 된다.
# `/응원역할생성` 은 같은 이름의 역할이 이미 있으면 새로 만들지 않고 그것을 쓴다.
# (손으로 꾸며 둔 그라데이션을 덮어쓰지 않기 위해서다)
LCK_TEAMS: dict[str, TeamSpec] = {
    "T1": TeamSpec("T1", (226, 1, 45), (255, 122, 122)),
    "GEN": TeamSpec("젠지", (170, 140, 44), (238, 212, 128)),
    "HLE": TeamSpec("한화생명e스포츠", (255, 102, 0), (255, 190, 80)),
    "DK": TeamSpec("디플러스 기아", (27, 60, 135), (96, 168, 244)),
    "KT": TeamSpec("KT 롤스터", (166, 25, 46), (244, 108, 118)),
    "DRX": TeamSpec("DRX", (43, 101, 172), (118, 196, 244)),
    "KDF": TeamSpec("광동 프릭스", (222, 89, 45), (250, 176, 92)),
    "NS": TeamSpec("농심 레드포스", (231, 56, 63), (255, 148, 138)),
    "BFX": TeamSpec("BNK 피어엑스", (0, 176, 168), (128, 240, 226)),
    "BRO": TeamSpec("OK저축은행 브리온", (240, 180, 40), (255, 230, 148)),
}

# 디스코드에서 그라데이션 역할을 쓸 수 있는 서버인지 판단할 기능 플래그
GRADIENT_ROLE_FEATURE = "ENHANCED_ROLE_COLORS"


@dataclass(frozen=True, slots=True)
class ThemeSpec:
    """프로필 카드 테마 한 벌.

    강조색은 테두리 · 박스 외곽선 · 음성 게이지에, 보조색은 채팅 게이지에 쓴다.
    색상 역할을 만들 때도 `accent` 를 그대로 역할 색으로 쓴다.
    """

    name: str
    accent: tuple[int, int, int]
    accent_bright: tuple[int, int, int]
    accent_dim: tuple[int, int, int]
    bar: tuple[int, int, int]
    bar_bright: tuple[int, int, int]


# 기본 테마. 아무것도 사지 않은 사람에게 쓰인다.
DEFAULT_THEME = "gold"

CARD_THEMES: dict[str, ThemeSpec] = {
    "gold": ThemeSpec(
        "골드", (212, 179, 106), (240, 217, 140), (146, 116, 60),
        (110, 175, 235), (150, 205, 250),
    ),
    "violet": ThemeSpec(
        "바이올렛", (168, 120, 214), (206, 170, 240), (96, 64, 132),
        (150, 120, 220), (190, 165, 245),
    ),
    "mint": ThemeSpec(
        "민트", (72, 196, 168), (140, 232, 208), (34, 104, 90),
        (70, 190, 200), (130, 225, 232),
    ),
    "crimson": ThemeSpec(
        "크림슨", (206, 92, 96), (238, 150, 150), (118, 44, 48),
        (214, 110, 110), (240, 165, 165),
    ),
    "azure": ThemeSpec(
        "애저", (86, 156, 232), (150, 200, 250), (38, 78, 130),
        (86, 176, 232), (150, 214, 250),
    ),
    "rose": ThemeSpec(
        "로즈", (226, 126, 168), (248, 178, 206), (128, 58, 92),
        (222, 130, 190), (246, 180, 220),
    ),
    "amber": ThemeSpec(
        "앰버", (230, 150, 70), (250, 196, 130), (132, 80, 30),
        (232, 168, 90), (250, 206, 150),
    ),
    "jade": ThemeSpec(
        "제이드", (96, 190, 118), (156, 226, 174), (44, 100, 60),
        (90, 196, 150), (150, 230, 194),
    ),
    "silver": ThemeSpec(
        "실버", (188, 198, 212), (228, 234, 244), (100, 110, 124),
        (160, 180, 208), (206, 220, 240),
    ),
    "abyss": ThemeSpec(
        "심연", (110, 132, 196), (168, 186, 236), (52, 64, 108),
        (120, 140, 210), (176, 194, 240),
    ),
}

# 상점에서 파는 테마 (기본 테마는 팔지 않는다)
SHOP_THEME_KEYS: tuple[str, ...] = tuple(
    k for k in CARD_THEMES if k != DEFAULT_THEME
)


class Warning:
    """경고 시스템 수치."""

    BAN_THRESHOLD = 3            # 이 횟수 이상이면 자동 서버 차단
    BAN_DELETE_MESSAGE_DAYS = 0  # 차단 시 삭제할 메시지 기간(일)


class Colors:
    """LoL 골드/네이비 계열 팔레트."""

    GOLD = 0xC8AA6E
    DARK_GOLD = 0x785A28
    TEAL = 0x0AC8B9
    NAVY = 0x0A1428
    SUCCESS = 0x3FBF7F
    DANGER = 0xC8443C
    INFO = 0x5865F2


# 소개 채널에 인증 안내를 다시 올릴 시각 (KST). 기본 00 · 06 · 12 · 18시
VERIFY_REMINDER_HOURS: tuple[int, ...] = (0, 6, 12, 18)

# 인증 안내 문구. 앞에 미등록 역할 멘션이 자동으로 붙는다.
VERIFY_REMINDER_TEXT = (
    '"롤닉#태그/이번년도최고티어(영어로)/주라인(영어로) 부라인(영어로)" '
    "로 쓰시면 자동 인증됩니다!\n"
    "인증하지 않을 시 서버 이용에 제한이 있으니 양해 부탁드립니다."
)

# 새 안내를 올릴 때 직전 안내를 지울지 (채널이 안내로 도배되는 것을 막는다)
VERIFY_REMINDER_REPLACE = True


# 프로필 카드 배경 이미지 (없으면 자동으로 그라데이션 배경을 생성)
PROFILE_BACKGROUND = ASSET_DIR / os.getenv("PROFILE_BACKGROUND", "profile_bg.png")

# 티어 엠블럼 이미지 폴더 (assets/tiers/master.png 형태)
TIER_EMBLEM_DIR = ASSET_DIR / "tiers"

# 프로필 카드 문구
PROFILE_SLOGAN = "롤 같이 하자"
PROFILE_SUBTITLE = "PLAY TOGETHER, WIN TOGETHER."

# 라이엇 랭크 정보를 다시 조회하기까지의 간격 (초)
RANK_CACHE_SECONDS = 600

# 한글 폰트 후보. 위에서부터 존재하는 것을 사용한다.
FONT_CANDIDATES: tuple[Path | str, ...] = (
    FONT_DIR / "Pretendard-Bold.ttf",
    FONT_DIR / "NanumGothicBold.ttf",
    FONT_DIR / "NotoSansKR-Bold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # 최후의 수단 (한글 글리프 포함)
)

FONT_CANDIDATES_REGULAR: tuple[Path | str, ...] = (
    FONT_DIR / "Pretendard-Regular.ttf",
    FONT_DIR / "NanumGothic.ttf",
    FONT_DIR / "NotoSansKR-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",  # 최후의 수단 (한글 글리프 포함)
)
