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
    TIER_TICKETS = 1540765501339340810     # 티어 조정 티켓만 모아 두는 카테고리
    ONBOARDING = 1536033191381569556       # 닉네임 양식 입력 채널 (미등록용)
    NICKNAME_UPDATE = 1540234610330304574  # 등록을 마친 사람이 티어·라인을 고치는 곳
    SCRIM_FORUM = 1536049980836548720      # 내전 포럼 채널
    SCRIM_RULES = 1538233794136510535      # 내전 규칙 · 동의 패널
    ROLE_PICKER = 1536153863441223760      # 이모지로 라인 역할을 고르는 채널
    PREDICTION = 1537005994884997200       # 프로 경기 승부예측
    STAFF_ALERT = 1160943152409104556      # 봇 이상 징후를 관리진에게 알리는 곳


class Roles:
    """역할 ID."""

    SCRIM_HOST = 1536050031520514168     # 내전 생성 가능 역할 (내전매니저)
    SCRIM_MEMBER = 1536050343383662612   # 내전 규칙에 동의한 사람 (내전)
    TIER_REVIEWER = 1535961199416578088  # 티어 조정을 봐 주는 사람 (티어조정관)
    VERIFIED = 1540931146030915654       # 롤 계정 소유를 증명한 사람 (본인인증)
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
    VOICE_REWARD = 10            # 주기마다 지급할 기본 포인트
    IGNORE_AFK_CHANNEL = True    # 잠수 채널은 적립 제외

    # 연속 출석 보너스. 출석은 하루 100P 뿐이라 굳이 칠 이유가 없어서 사실상
    # 죽어 있었다(전체 31건). 며칠 이어 오면 눈에 띄게 얹어 준다.
    # {연속 일수: 그날 추가로 주는 포인트}
    STREAK_BONUS: dict[int, int] = {
        3: 100,
        7: 300,
        14: 700,
        30: 2_000,
    }

    # 같은 음성 채널에 몇 명 있느냐에 따라 배수를 준다.
    # 혼자 틀어 놓은 사람과 다섯이 모여 떠든 사람이 같은 보상을 받으면
    # 모일 이유가 없다. {최소 인원: 배수} — 큰 쪽부터 맞춰 본다.
    VOICE_GROUP_MULTIPLIER: dict[int, float] = {
        5: 2.5,
        3: 2.0,
        2: 1.5,
        1: 1.0,
    }


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

    DURATION_DAYS = 30        # 기본 유지 기간

    # 비싼 것은 더 오래 간다. 보통 유저의 자력 수입이 하루 14P 라 12,000P 짜리는
    # 모으는 데 800일이 넘게 걸리는데, 그게 30일 만에 사라지면 아무도 못 산다.
    # {이 가격 이상: 유지 일수} — 큰 쪽부터 맞춰 본다.
    LONG_DURATION: dict[int, int] = {
        10_000: 90,
        5_000: 60,
    }
    THEME_PRICE = 2_000       # 프로필 카드 테마
    SLOGAN_PRICE = 3_000      # 프로필 카드 문구
    COLOR_ROLE_PRICE = 2_000  # 색상 역할
    TEAM_ROLE_PRICE = 15_000  # LCK 응원 역할 (그라데이션이라 프리미엄)
    SLOGAN_MAX_LENGTH = 14    # 카드 좌상단에 들어가는 길이 한계

    # 챔피언 역할. 그라데이션이 붙은 20종은 따로 비싸게 판다.
    CHAMPION_ROLE_PRICE = 4_000
    CHAMPION_GRADIENT_PRICE = 12_000


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
    "GEN": TeamSpec("GEN", (170, 140, 44), (238, 212, 128)),
    "HLE": TeamSpec("HLE", (255, 102, 0), (255, 190, 80)),
    "DK": TeamSpec("DK", (27, 60, 135), (96, 168, 244)),
    "KT": TeamSpec("KT", (166, 25, 46), (244, 108, 118)),
    "DRX": TeamSpec("DRX", (43, 101, 172), (118, 196, 244)),
    "KDF": TeamSpec("KDF", (222, 89, 45), (250, 176, 92)),
    "NS": TeamSpec("NS", (231, 56, 63), (255, 148, 138)),
    "BFX": TeamSpec("BFX", (0, 176, 168), (128, 240, 226)),
    "BRO": TeamSpec("BRO", (240, 180, 40), (255, 230, 148)),
}

# 디스코드에서 그라데이션 역할을 쓸 수 있는 서버인지 판단할 기능 플래그
GRADIENT_ROLE_FEATURE = "ENHANCED_ROLE_COLORS"


@dataclass(frozen=True, slots=True)
class ChampionSpec:
    """챔피언 역할 한 개.

    `secondary` 가 있으면 **그라데이션 역할**로 만들고 비싸게 판다.
    없으면 `color` 단색이다. 그라데이션을 다른 챔피언으로 옮기고 싶으면
    여기서 `secondary` 를 지우고 원하는 챔피언에 붙여 주면 된다.
    (이미 만들어진 역할은 이름으로 재사용하므로, 색을 바꾸려면 서버에서
    해당 역할을 지우고 `/챔피언역할생성` 을 다시 돌려야 한다)
    """

    name: str                                    # 역할 이름으로도 쓰인다
    lane: str                                    # 상점에서 묶어 보여줄 라인
    color: tuple[int, int, int]                  # 기본색 (그라데이션 시작)
    secondary: tuple[int, int, int] | None = None  # 있으면 그라데이션 끝

    @property
    def gradient(self) -> bool:
        return self.secondary is not None


# 챔피언 응원 역할 60종. 라인별 12종씩 묶어 두었고, 그중 20종(라인별 4종)에
# 그라데이션을 넣었다. 색은 모두 어두운 테마에서 읽히도록 잡았다.
CHAMPIONS: dict[str, ChampionSpec] = {
    # ------------------------------------------------------------------ 탑
    "DARIUS": ChampionSpec("다리우스", "TOP", (176, 48, 48), (255, 120, 96)),
    "RIVEN": ChampionSpec("리븐", "TOP", (110, 200, 190), (200, 250, 240)),
    "AATROX": ChampionSpec("아트록스", "TOP", (198, 52, 52), (240, 200, 200)),
    "TEEMO": ChampionSpec("티모", "TOP", (110, 200, 120), (230, 200, 90)),
    "GAREN": ChampionSpec("가렌", "TOP", (86, 148, 216)),
    "FIORA": ChampionSpec("피오라", "TOP", (196, 88, 120)),
    "JAX": ChampionSpec("잭스", "TOP", (150, 130, 200)),
    "NASUS": ChampionSpec("나서스", "TOP", (216, 176, 96)),
    "ORNN": ChampionSpec("오른", "TOP", (208, 100, 56)),
    "KENNEN": ChampionSpec("케넨", "TOP", (232, 206, 88)),
    "IRELIA": ChampionSpec("이렐리아", "TOP", (96, 190, 200)),
    "KSANTE": ChampionSpec("크산테", "TOP", (200, 176, 120)),
    # --------------------------------------------------------------- 정글
    "LEESIN": ChampionSpec("리 신", "JG", (208, 132, 64), (250, 200, 120)),
    "MASTERYI": ChampionSpec("마스터 이", "JG", (216, 190, 100), (150, 230, 190)),
    "VIEGO": ChampionSpec("비에고", "JG", (100, 200, 176), (170, 130, 220)),
    "KINDRED": ChampionSpec("킨드레드", "JG", (168, 216, 200), (240, 240, 250)),
    "WARWICK": ChampionSpec("워윅", "JG", (120, 160, 200)),
    "JARVANIV": ChampionSpec("자르반 4세", "JG", (216, 180, 96)),
    "GRAVES": ChampionSpec("그레이브즈", "JG", (176, 130, 96)),
    "NIDALEE": ChampionSpec("니달리", "JG", (200, 170, 100)),
    "XINZHAO": ChampionSpec("신 짜오", "JG", (196, 88, 88)),
    "HECARIM": ChampionSpec("헤카림", "JG", (120, 200, 168)),
    "ELISE": ChampionSpec("엘리스", "JG", (176, 96, 176)),
    "RENGAR": ChampionSpec("렝가", "JG", (200, 140, 90)),
    # ---------------------------------------------------------------- 미드
    "YASUO": ChampionSpec("야스오", "MID", (100, 180, 230), (200, 240, 255)),
    "ZED": ChampionSpec("제드", "MID", (200, 60, 72), (110, 120, 150)),
    "AHRI": ChampionSpec("아리", "MID", (232, 120, 176), (255, 200, 230)),
    "YONE": ChampionSpec("요네", "MID", (150, 130, 220), (240, 130, 140)),
    "LEBLANC": ChampionSpec("르블랑", "MID", (176, 120, 216)),
    "SYNDRA": ChampionSpec("신드라", "MID", (168, 120, 224)),
    "AZIR": ChampionSpec("아지르", "MID", (230, 196, 100)),
    "KATARINA": ChampionSpec("카타리나", "MID", (216, 72, 96)),
    "TALON": ChampionSpec("탈론", "MID", (150, 160, 190)),
    "ORIANNA": ChampionSpec("오리아나", "MID", (140, 190, 210)),
    "VIKTOR": ChampionSpec("빅토르", "MID", (196, 150, 80)),
    "AKALI": ChampionSpec("아칼리", "MID", (110, 210, 160)),
    # ---------------------------------------------------------------- 원딜
    "JHIN": ChampionSpec("진", "AD", (216, 96, 120), (240, 200, 160)),
    "JINX": ChampionSpec("징크스", "AD", (216, 100, 176), (140, 200, 230)),
    "CAITLYN": ChampionSpec("케이틀린", "AD", (130, 170, 220), (230, 190, 120)),
    "EZREAL": ChampionSpec("이즈리얼", "AD", (230, 200, 110), (120, 200, 230)),
    "ASHE": ChampionSpec("애쉬", "AD", (140, 200, 230)),
    "VAYNE": ChampionSpec("베인", "AD", (170, 130, 200)),
    "MISSFORTUNE": ChampionSpec("미스 포츈", "AD", (216, 110, 90)),
    "XAYAH": ChampionSpec("자야", "AD", (200, 90, 130)),
    "LUCIAN": ChampionSpec("루시안", "AD", (200, 180, 140)),
    "KALISTA": ChampionSpec("칼리스타", "AD", (110, 200, 190)),
    "SIVIR": ChampionSpec("시비르", "AD", (216, 170, 90)),
    "KOGMAW": ChampionSpec("코그모", "AD", (150, 200, 110)),
    # ---------------------------------------------------------------- 서폿
    "THRESH": ChampionSpec("쓰레쉬", "SUP", (110, 210, 170), (180, 250, 220)),
    "LEONA": ChampionSpec("레오나", "SUP", (230, 180, 90), (255, 230, 160)),
    "PYKE": ChampionSpec("파이크", "SUP", (90, 200, 200), (170, 120, 210)),
    "YUUMI": ChampionSpec("유미", "SUP", (230, 170, 200), (180, 220, 250)),
    "LULU": ChampionSpec("룰루", "SUP", (190, 150, 230)),
    "NAMI": ChampionSpec("나미", "SUP", (110, 190, 220)),
    "SORAKA": ChampionSpec("소라카", "SUP", (170, 200, 230)),
    "BLITZCRANK": ChampionSpec("블리츠크랭크", "SUP", (196, 160, 90)),
    "BRAUM": ChampionSpec("브라움", "SUP", (150, 180, 220)),
    "MORGANA": ChampionSpec("모르가나", "SUP", (170, 120, 210)),
    "ALISTAR": ChampionSpec("알리스타", "SUP", (196, 130, 110)),
    "SENNA": ChampionSpec("세나", "SUP", (140, 200, 180)),
}


def champions_in_lane(lane: str) -> dict[str, ChampionSpec]:
    """해당 라인의 챔피언만 골라 준다. 상점 선택지가 25개를 넘지 않게 하는 용도."""
    return {k: v for k, v in CHAMPIONS.items() if v.lane == lane}


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


def shop_duration(price: int) -> int:
    """이 가격의 아이템이 며칠 유지되는지."""
    for floor in sorted(Shop.LONG_DURATION, reverse=True):
        if price >= floor:
            return Shop.LONG_DURATION[floor]
    return Shop.DURATION_DAYS


class Prediction:
    """승부예측 수치.

    배당은 **파리뮤추얼**이다. 건 포인트를 한 통에 모았다가 맞힌 사람들이
    자기가 건 비율대로 통째로 나눠 갖는다. 봇이 포인트를 새로 찍어내지 않아서
    (전체 지급액 ≤ 전체 베팅액) 경제가 부풀지 않는다.
    """

    LEAD_HOURS = 24            # 경기 시작 몇 시간 전에 예측을 올릴지
    POLL_MINUTES = 15          # 일정을 다시 확인하는 주기

    # 포인트가 걸려 있으므로 시작 직전에 닫는다. 다만 마감 루프는 POLL_MINUTES
    # 마다만 돌아서 최대 그만큼 늦게 닫힌다. 초반을 보고 거는 걸 막아야 하니
    # 베팅을 받을 때 시작 시각을 한 번 더 직접 확인한다 (cogs/prediction.py).
    CLOSE_BEFORE_MINUTES = 5

    MIN_BET = 100              # 최소 베팅액. 상한은 없다 (보유 포인트 전부 가능)
    MAX_POST_PER_TICK = 8      # 한 번에 올릴 수 있는 예측 수 (레이트 리밋 보호)

    # 패널은 24시간 전에 올라가서 그대로 두면 묻힌다. 마감 직전에 한 번 더
    # 알려 참여를 받는다. 주기가 POLL_MINUTES 라 이보다 촘촘하게는 못 잡는다.
    REMIND_BEFORE_MINUTES = 30


# 승부예측 일정을 가져올 곳. lolesports.com 웹사이트가 그대로 쓰는 공개 API 다.
# 이 키는 라이엇 웹 프론트엔드에 박혀 있는 공개 키라서 따로 발급받지 않아도 된다.
ESPORTS_BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
ESPORTS_API_KEY = os.getenv(
    "ESPORTS_API_KEY", "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
)
ESPORTS_LOCALE = os.getenv("ESPORTS_LOCALE", "ko-KR")

# 자동으로 올릴 대회. slug 가 정확히 같거나 대회 이름에 들어 있으면 잡는다.
# 실제 slug 는 서버마다 확인이 필요하므로 `/리그목록` 으로 확인하고 고치면 된다.
# 아시안게임은 이 API 에 없으므로 `/경기추가` 로 직접 넣는다.
ESPORTS_LEAGUES: tuple[str, ...] = (
    "lck",
    "msi",
    "worlds",
    "ewc",
    "esports_world_cup",
    "esports world cup",
)

# 위 목록에 부분 일치로 딸려 오지만 올리고 싶지 않은 대회. 제외가 우선한다.
# `lck` 는 `lck_challengers_league` 에도 걸리는데, 2군 경기까지 올라오면
# 채널이 지저분해져서 뺀다.
ESPORTS_LEAGUES_EXCLUDE: tuple[str, ...] = (
    "lck_challengers_league",
    "challengers",
)

# 대회 이름을 한국어로 보여줄 때 쓰는 표기 (없으면 API 이름 그대로)
LEAGUE_NAMES: dict[str, str] = {
    "lck": "LCK",
    "msi": "MSI",
    "worlds": "월드 챔피언십",
    "ewc": "EWC",
    "esports_world_cup": "EWC",
}


class Verify:
    """롤 계정 소유 인증.

    `/등록` 은 그 롤 아이디가 **존재하는지**만 확인한다. 남의 계정을 자기
    것이라고 적어도 막을 방법이 없어서, 내전 규칙의 "본인 계정만 · 대리 금지"
    가 사실상 강제되지 않았다.

    소유를 증명하는 방법은 이렇다. 봇이 아이콘 하나를 지정하면 사용자가
    롤 클라이언트에서 프로필 아이콘을 그것으로 바꾼다. 계정에 로그인할 수
    있는 사람만 바꿀 수 있으므로, 바뀐 것을 확인하면 본인이라는 뜻이 된다.

    쓰는 아이콘은 **계정을 만들면 누구나 갖고 있는 기본 아이콘**이어야 한다.
    한정 아이콘을 요구하면 없는 사람은 인증을 못 한다.
    """

    ICON_POOL: tuple[int, ...] = (
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
        11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
        21, 22, 23, 24, 25, 26, 27, 28,
    )
    TIMEOUT_MINUTES = 15   # 이 시간 안에 바꾸고 확인을 눌러야 한다
    COOLDOWN_SECONDS = 20  # 확인 버튼 연타로 API 를 두드리는 걸 막는다


class Scrim:
    """내전 자동 생성 수치.

    매일 정해진 시각에 모집 글을 하나 올린다. 만들기만 하고 두면 아무도 안
    들어온 글이 포럼에 쌓이므로, 다음 날 글을 올릴 때 **인원이 안 모인 어제
    글은 접는다.**
    """

    AUTO_HOUR = 18             # 매일 이 시각(KST)에 올린다
    AUTO_RULE = "하드피어리스"
    AUTO_SERIES = "3판2선"
    AUTO_TITLE = "{month}/{day} 정기 내전"

    # 이만큼 지난 모집 글은 접는다. 하루 주기라 그 전에 정리된다
    STALE_HOURS = 20


class Watchdog:
    """봇이 조용히 고장 났는지 스스로 살피는 주기와 기준.

    이 봇이 고장 나는 방식은 대개 "에러를 내며 멈추는" 것이 아니라 **아무
    일도 일어나지 않는** 것이다. 대회 slug 가 바뀌어 승부예측이 안 올라오거나,
    수동 경기 정산을 잊어 포인트가 묶인 채로 남는 식이다. 사람이 눈치채기까지
    오래 걸리므로 봇이 직접 알린다.
    """

    CHECK_HOURS = 6            # 점검 주기
    STALE_REGISTER_DAYS = 3    # 이 기간 자동 등록이 0건이면 멎은 것으로 본다
    STALE_SETTLE_HOURS = 6     # 마감 뒤 이만큼 지나도 정산이 안 되면 알린다
    REPEAT_HOURS = 24          # 같은 경고를 다시 보내기까지의 간격 (도배 방지)


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


class Web:
    """웹사이트 설정.

    봇과 같은 VM 에서 돌지만 **별도 프로세스**다. 웹이 죽어도 봇은 살아 있고,
    반대도 마찬가지다. DB 는 읽기만 한다 — 웹에서 포인트를 건드릴 수 있으면
    그게 곧 구멍이 된다.
    """

    BASE_URL = os.getenv("WEB_BASE_URL", "https://lolwithus.p-e.kr")
    HOST = "127.0.0.1"          # nginx 뒤에만 붙는다. 밖으로 직접 열지 않는다
    PORT = 8080

    CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1536051921587277964")
    CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
    SESSION_SECRET = os.getenv("WEB_SESSION_SECRET", "")

    SESSION_DAYS = 14           # 로그인 유지 기간
    MATCH_COUNT = 10            # 전적에서 보여줄 경기 수
    MATCH_CACHE_HOURS = 6       # 이 시간 안에 조회한 매치 목록은 다시 안 받는다
