"""`/프로필` 카드 이미지 생성 (Pillow).

배치는 서버에서 쓰던 프로필 카드 시안을 그대로 따랐다.

    ┌───────────────────────────────────────────────────────┐
    │ (아바타)  롤 같이 하자                    ┌ 보유 포인트 ┐ │
    │           닉네임                          │   120 P   │ │
    │           PLAY TOGETHER, WIN TOGETHER.    └───────────┘ │
    │ ┌───────────────────────────────────────────────────┐ │
    │ │ 음성 레벨 Lv.1                              #4위   │ │
    │ │ ▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░      0 / 655 XP    │ │
    │ │ 채팅 레벨 Lv.0                              #1위   │ │
    │ │ ▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░      108 / 600 XP    │ │
    │ └───────────────────────────────────────────────────┘ │
    │ ┌─ 롤 솔랭 티어 ─────┐  ┌─ 롤 자유랭크 티어 ────────┐ │
    │ │ (엠블럼) 마스터    │  │ (엠블럼) 마스터           │ │
    │ └───────────────────┘  └──────────────────────────┘ │
    └───────────────────────────────────────────────────────┘

시안의 보라색 테두리는 배경(협곡 픽셀아트 · 네이비 + 골드 프레임)에 맞춰
골드 계열로 바꿨다. 배경은 `assets/profile_bg.png` 를 흐리게+어둡게 깔아
그 위의 글자가 항상 읽히도록 했다.
"""
from __future__ import annotations

import functools
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import (
    CARD_THEMES,
    DEFAULT_THEME,
    FONT_CANDIDATES,
    FONT_CANDIDATES_REGULAR,
    PROFILE_BACKGROUND,
    PROFILE_SLOGAN,
    PROFILE_SUBTITLE,
    TIER_EMBLEM_DIR,
)

log = logging.getLogger("mainbot.card")

WIDTH, HEIGHT = 1200, 712

# 세로 리듬
# 레벨 패널 안쪽 여백은 GAP, 카드 가장자리와 블록 사이는 그보다 넉넉하게 준다.
# 머리말이 답답해 보이지 않도록 위아래 여백을 크게 잡고, 그만큼 티어 박스를 줄였다.
GAP = 28          # 레벨 패널 안쪽 여백
EDGE = 40         # 카드 위/아래 가장자리 여백
BLOCK_GAP = 34    # 머리말 · 레벨 패널 · 티어 박스 사이 간격
HEADER_HEIGHT = 106  # 아바타와 이름이 차지하는 높이

# 레벨 한 줄의 높이: 라벨(34) + 여백(10) + 게이지(16) + 여백(8) + XP 글자(22)
LEVEL_ROW_HEIGHT = 90


@dataclass(frozen=True, slots=True)
class Theme:
    """카드 강조색 한 벌. 상점에서 산 테마가 이 값으로 들어온다."""

    accent: tuple[int, int, int]         # 테두리 · 박스 외곽선 · 음성 게이지
    accent_bright: tuple[int, int, int]  # 밝은 강조 (레벨 숫자 · 게이지 끝)
    accent_dim: tuple[int, int, int]     # 어두운 강조 (바깥 테두리 · 패널 선)
    bar: tuple[int, int, int]            # 채팅 게이지
    bar_bright: tuple[int, int, int]


def theme_from_spec(spec) -> Theme:
    """`config.ThemeSpec` 을 렌더러가 쓰는 Theme 으로."""
    return Theme(
        accent=spec.accent,
        accent_bright=spec.accent_bright,
        accent_dim=spec.accent_dim,
        bar=spec.bar,
        bar_bright=spec.bar_bright,
    )


DEFAULT_CARD_THEME = theme_from_spec(CARD_THEMES[DEFAULT_THEME])

# ---------------------------------------------------------------- 팔레트
# 강조색(테두리 · 게이지)은 유저가 산 테마에 따라 바뀌므로 여기서 고정하지 않고
# `Theme` 로 넘겨받는다. 아래는 테마와 무관하게 공통으로 쓰는 색들.
CREAM = (244, 241, 232)
MUTED = (168, 178, 200)
MUTED_WARM = (186, 176, 158)
NAVY = (10, 16, 38)

PANEL_FILL = (12, 20, 44, 198)
BOX_FILL = (10, 16, 36, 214)
BAR_TRACK = (206, 210, 222, 210)

# 티어별 색 (엠블럼 이미지가 없을 때 직접 그리는 크레스트에 쓴다)
TIER_COLORS: dict[str, tuple[int, int, int]] = {
    "C": (110, 210, 240),
    "GM": (214, 96, 96),
    "M": (186, 122, 220),
    "D": (110, 168, 240),
    "E": (80, 200, 140),
    "P": (86, 196, 196),
    "G": (222, 182, 92),
    "S": (176, 188, 200),
    "B": (172, 122, 90),
    "I": (140, 140, 140),
    "U": (118, 128, 145),
}

# 티어 약자 → 라이엇 엠블럼 파일 이름
TIER_SLUGS: dict[str, str] = {
    "C": "challenger",
    "GM": "grandmaster",
    "M": "master",
    "D": "diamond",
    "E": "emerald",
    "P": "platinum",
    "G": "gold",
    "S": "silver",
    "B": "bronze",
    "I": "iron",
    "U": "unranked",
}


# ---------------------------------------------------------------- 폰트


@functools.lru_cache(maxsize=48)
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """한글이 나오는 폰트를 찾아 로드한다."""
    candidates: Sequence[Path | str] = (
        FONT_CANDIDATES if bold else FONT_CANDIDATES_REGULAR
    )
    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except (OSError, ValueError):
            continue
    log.warning(
        "한글 폰트를 찾지 못했습니다. assets/fonts/ 에 Pretendard-Bold.ttf 등을 넣어 주세요."
    )
    return ImageFont.load_default(size)


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """가로 폭에 맞게 말줄임한다."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    trimmed = text
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + "…") if trimmed else "…"


def _shrink_to_fit(
    draw: ImageDraw.ImageDraw, text: str, max_width: int, largest: int, smallest: int
):
    """폭 안에 들어가는 가장 큰 글자 크기를 고른다 (포인트 자릿수가 커질 때)."""
    for size in range(largest, smallest - 1, -1):
        font = _font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return _font(smallest)


def _tracked_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font,
    fill,
    spacing: float = 2.0,
) -> None:
    """글자 사이를 벌려서 그린다 (시안의 소제목 스타일)."""
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + spacing


# ------------------------------------------------------------ 도형 헬퍼


def _rounded(
    size: tuple[int, int], radius: int, fill, outline=None, width: int = 2
) -> Image.Image:
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )
    return layer


def _rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """안티에일리어싱된 둥근 사각형 마스크 (4배로 그린 뒤 축소)."""
    scale = 4
    big = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, size[0] * scale - 1, size[1] * scale - 1),
        radius=radius * scale,
        fill=255,
    )
    return big.resize(size, Image.LANCZOS)


def _horizontal_gradient(
    size: tuple[int, int], left: tuple[int, int, int], right: tuple[int, int, int]
) -> Image.Image:
    w, h = size
    grad = Image.new("RGB", (max(1, w), 1))
    px = grad.load()
    for x in range(max(1, w)):
        ratio = x / max(1, w - 1)
        px[x, 0] = tuple(int(left[i] + (right[i] - left[i]) * ratio) for i in range(3))
    return grad.resize((max(1, w), h), Image.BILINEAR).convert("RGBA")


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """비율을 유지하며 잘라서 꽉 채운다 (CSS object-fit: cover)."""
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new = image.resize(
        (max(1, int(src_w * scale)), max(1, int(src_h * scale))), Image.LANCZOS
    )
    left = (new.width - target_w) // 2
    top = (new.height - target_h) // 2
    return new.crop((left, top, left + target_w, top + target_h))


# ---------------------------------------------------------------- 배경


def _fallback_background(size: tuple[int, int]) -> Image.Image:
    """배경 파일이 없을 때 쓰는 네이비 + 골드 그라데이션."""
    w, h = size
    base = Image.new("RGB", (w, h), NAVY)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        ratio = y / max(1, h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=(int(9 + 26 * ratio), int(15 + 30 * ratio), int(36 + 34 * ratio)),
        )
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((-200, -300, 620, 420), fill=(78, 60, 24))     # 좌상단 따뜻한 빛
    gdraw.ellipse((w - 560, h - 340, w + 240, h + 260), fill=(14, 62, 70))  # 우하단 청록
    gdraw.ellipse((w // 2 - 220, -160, w // 2 + 220, 280), fill=(40, 34, 76))
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    return Image.blend(base, glow, 0.5)


def _load_background() -> Image.Image:
    """배경을 불러와 흐리게+어둡게 깐다.

    배경 그림에 이미 큰 제목이 들어가 있어도 흐림 처리 덕분에 카드 내용과
    부딪히지 않는다.
    """
    path = Path(PROFILE_BACKGROUND)
    source: Optional[Image.Image] = None
    if path.exists():
        try:
            with Image.open(path) as img:
                source = _cover(img.convert("RGB"), (WIDTH, HEIGHT))
        except OSError as exc:
            log.warning("배경 이미지를 열 수 없습니다 (%s): %s", path, exc)
    if source is None:
        source = _fallback_background((WIDTH, HEIGHT))
        canvas = source.convert("RGBA")
    else:
        canvas = source.filter(ImageFilter.GaussianBlur(5)).convert("RGBA")

    canvas.alpha_composite(Image.new("RGBA", (WIDTH, HEIGHT), (6, 11, 28, 132)))
    return canvas


# ------------------------------------------------------------ 아바타


def _circle_avatar(data: Optional[bytes], diameter: int) -> Image.Image:
    """아바타를 원형으로 자르고 밝은 링을 두른다."""
    ring = 4
    total = diameter + ring * 2
    canvas = Image.new("RGBA", (total, total), (0, 0, 0, 0))

    if data:
        try:
            with Image.open(io.BytesIO(data)) as img:
                avatar = _cover(img.convert("RGB"), (diameter, diameter))
        except OSError:
            avatar = Image.new("RGB", (diameter, diameter), (28, 36, 58))
    else:
        avatar = Image.new("RGB", (diameter, diameter), (28, 36, 58))

    mask = Image.new("L", (diameter * 4, diameter * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * 4 - 1, diameter * 4 - 1), fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)

    canvas.paste(avatar, (ring, ring), mask)
    ImageDraw.Draw(canvas).ellipse(
        (ring // 2, ring // 2, total - ring // 2 - 1, total - ring // 2 - 1),
        outline=(246, 244, 238),
        width=ring,
    )
    return canvas


# ------------------------------------------------------------ 티어 엠블럼


def _drawn_crest(tier_code: Optional[str], size: int) -> Image.Image:
    """엠블럼 이미지가 없을 때 직접 그리는 크레스트 (날개 + 마름모)."""
    color = TIER_COLORS.get(tier_code or "U", TIER_COLORS["U"])
    dark = tuple(int(c * 0.42) for c in color)
    light = tuple(min(255, int(c * 1.35)) for c in color)

    scale = 4
    s = size * scale
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 뒤쪽 글로우
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (s * 0.10, s * 0.10, s * 0.90, s * 0.90), fill=(*dark, 170)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(s * 0.05))
    layer.alpha_composite(glow)

    # 좌우 날개
    wing = [
        (s * 0.06, s * 0.40),
        (s * 0.40, s * 0.30),
        (s * 0.45, s * 0.47),
        (s * 0.12, s * 0.58),
    ]
    draw.polygon(wing, fill=(*color, 235))
    draw.polygon([(s - x, y) for x, y in wing], fill=(*color, 235))

    lower_wing = [
        (s * 0.16, s * 0.62),
        (s * 0.42, s * 0.55),
        (s * 0.45, s * 0.68),
        (s * 0.24, s * 0.76),
    ]
    draw.polygon(lower_wing, fill=(*dark, 240))
    draw.polygon([(s - x, y) for x, y in lower_wing], fill=(*dark, 240))

    # 가운데 마름모
    diamond = [
        (s * 0.50, s * 0.14),
        (s * 0.69, s * 0.48),
        (s * 0.50, s * 0.88),
        (s * 0.31, s * 0.48),
    ]
    draw.polygon(diamond, fill=(*light, 245), outline=(*CREAM, 220), width=int(s * 0.012))
    inner = [
        (s * 0.50, s * 0.26),
        (s * 0.61, s * 0.48),
        (s * 0.50, s * 0.75),
        (s * 0.39, s * 0.48),
    ]
    draw.polygon(inner, fill=(*dark, 220))

    return layer.resize((size, size), Image.LANCZOS)


@functools.lru_cache(maxsize=24)
def _tier_emblem(tier_code: Optional[str], size: int) -> Image.Image:
    """티어 엠블럼. `assets/tiers/` 에 이미지가 있으면 그것을 쓴다."""
    slug = TIER_SLUGS.get(tier_code or "U", "unranked")
    for name in (f"{slug}.png", f"{slug}.webp", f"{(tier_code or 'U').lower()}.png"):
        path = TIER_EMBLEM_DIR / name
        if not path.exists():
            continue
        try:
            with Image.open(path) as img:
                emblem = img.convert("RGBA")
            emblem.thumbnail((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            canvas.alpha_composite(
                emblem, ((size - emblem.width) // 2, (size - emblem.height) // 2)
            )
            return canvas
        except OSError as exc:
            log.warning("티어 엠블럼을 열 수 없습니다 (%s): %s", path, exc)
    return _drawn_crest(tier_code, size)


# ------------------------------------------------------------ 구성 요소


def _progress_bar(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
    ratio: float,
    left: tuple[int, int, int],
    right: tuple[int, int, int],
) -> None:
    """둥근 XP 게이지."""
    x, y, w, h = box
    radius = h // 2

    track = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    track.putalpha(0)
    track_layer = Image.new("RGBA", (w, h), BAR_TRACK)
    track_layer.putalpha(_rounded_mask((w, h), radius).point(lambda v: v * BAR_TRACK[3] // 255))
    canvas.alpha_composite(track_layer, (x, y))

    ratio = max(0.0, min(1.0, ratio))
    if ratio <= 0:
        return
    fill_w = max(h, int(w * ratio))  # 조금이라도 찼으면 최소한 동그라미만큼은 보이게
    fill = _horizontal_gradient((fill_w, h), left, right)
    fill.putalpha(_rounded_mask((fill_w, h), radius))
    canvas.alpha_composite(fill, (x, y))


def _level_row(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    top: int,
    left: int,
    right: int,
    title: str,
    level: int,
    current_xp: int,
    needed_xp: int,
    rank: Optional[int],
    fill_left: tuple[int, int, int],
    fill_right: tuple[int, int, int],
    level_color: tuple[int, int, int],
) -> None:
    """`음성 레벨 Lv.1 … #4위` 한 줄과 게이지."""
    title_font = _font(26)
    level_font = _font(23)
    rank_font = _font(21)
    xp_font = _font(17, bold=False)

    draw.text((left, top), title, font=title_font, fill=CREAM)
    offset = left + draw.textlength(title, font=title_font) + 14
    draw.text((offset, top + 4), f"Lv. {level}", font=level_font, fill=level_color)

    if rank is not None:
        draw.text((right, top + 4), f"#{rank}위", font=rank_font, fill=CREAM, anchor="ra")

    bar_y = top + 44
    bar_h = 16
    _progress_bar(
        canvas, (left, bar_y, right - left, bar_h), current_xp / max(1, needed_xp),
        fill_left, fill_right,
    )
    draw.text(
        (right, bar_y + bar_h + 8),
        f"{current_xp:,} / {needed_xp:,} XP",
        font=xp_font,
        fill=MUTED,
        anchor="ra",
    )


def _tier_box(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    label: str,
    tier_code: Optional[str],
    tier_name: str,
    record: str,
    accent: tuple[int, int, int],
) -> None:
    """솔랭 / 자유랭크 티어 박스."""
    box = _rounded((w, h), 18, BOX_FILL, outline=(*accent, 200), width=2)
    canvas.alpha_composite(box, (x, y))

    emblem_size = 104
    emblem = _tier_emblem(tier_code, emblem_size)
    canvas.alpha_composite(emblem, (x + 40, y + (h - emblem_size) // 2))

    text_x = x + 40 + emblem_size + 26
    max_w = x + w - 28 - text_x

    # 글자 블록의 중심이 박스 중심과 맞도록 잡은 값들
    draw.text((text_x, y + 46), label, font=_font(19, bold=False), fill=MUTED)
    name_font = _font(34)
    draw.text(
        (text_x, y + 72),
        _fit(draw, tier_name, name_font, max_w),
        font=name_font,
        fill=CREAM,
    )
    record_font = _font(18, bold=False)
    draw.text(
        (text_x, y + 126),
        _fit(draw, record, record_font, max_w),
        font=record_font,
        fill=MUTED_WARM,
    )


# ---------------------------------------------------------------- 본체


def render_profile_card(
    *,
    display_name: str,
    avatar_bytes: Optional[bytes],
    riot_id: Optional[str],
    points: int,
    voice_level: int,
    voice_current_xp: int,
    voice_needed_xp: int,
    voice_rank: Optional[int],
    chat_level: int,
    chat_current_xp: int,
    chat_needed_xp: int,
    chat_rank: Optional[int],
    solo_tier_code: Optional[str],
    solo_tier_name: str,
    solo_record: str,
    flex_tier_code: Optional[str],
    flex_tier_name: str,
    flex_record: str,
    theme: Theme = DEFAULT_CARD_THEME,
    slogan: str = PROFILE_SLOGAN,
) -> io.BytesIO:
    """프로필 카드를 그려 PNG 바이트로 돌려준다.

    `theme` 과 `slogan` 은 상점에서 산 사람만 기본값과 달라진다.
    """
    canvas = _load_background()
    draw = ImageDraw.Draw(canvas)

    # 바깥 골드 테두리
    canvas.alpha_composite(
        _rounded((WIDTH - 12, HEIGHT - 12), 28, None, outline=(*theme.accent, 230), width=3),
        (6, 6),
    )
    canvas.alpha_composite(
        _rounded((WIDTH - 22, HEIGHT - 22), 24, None, outline=(*theme.accent_dim, 150), width=1),
        (11, 11),
    )

    # ------------------------------------------------------------ 머리말
    avatar_d = 92
    canvas.alpha_composite(_circle_avatar(avatar_bytes, avatar_d), (46, EDGE))

    text_x = 156
    points_box_x = 968
    name_max_w = points_box_x - text_x - 28

    draw.text((text_x, EDGE - 6), slogan, font=_font(20), fill=MUTED_WARM)

    name_font = _font(44)
    draw.text(
        (text_x - 2, EDGE + 18),
        _fit(draw, display_name, name_font, name_max_w),
        font=name_font,
        fill=CREAM,
    )

    # 등록한 사람은 롤 계정을, 아직이면 시안의 영문 문구를 보여준다.
    # 자간 벌리기는 영문 문구에만 어울리므로 롤 계정은 그냥 쓴다.
    if riot_id:
        riot_font = _font(18)
        draw.text(
            (text_x, EDGE + 82),
            _fit(draw, riot_id, riot_font, name_max_w),
            font=riot_font,
            fill=theme.accent_bright,
        )
    else:
        sub_font = _font(15, bold=False)
        _tracked_text(
            draw,
            (text_x, EDGE + 84),
            _fit(draw, PROFILE_SUBTITLE, sub_font, name_max_w),
            sub_font,
            MUTED,
            spacing=2.4,
        )

    # ------------------------------------------------------- 보유 포인트
    pb_w, pb_h = 188, 70
    pb_y = EDGE + (avatar_d + 8 - pb_h) // 2  # 아바타 중심에 맞춘다
    canvas.alpha_composite(
        _rounded((pb_w, pb_h), 14, BOX_FILL, outline=(*theme.accent, 215), width=2),
        (points_box_x, pb_y),
    )
    draw.text(
        (points_box_x + pb_w - 20, pb_y + 12), "보유 포인트",
        font=_font(17, bold=False), fill=MUTED, anchor="ra",
    )
    points_text = f"{points:,} P"
    draw.text(
        (points_box_x + pb_w - 20, pb_y + 32), points_text,
        font=_shrink_to_fit(draw, points_text, pb_w - 40, 30, 17),
        fill=CREAM, anchor="ra",
    )

    # --------------------------------------------------------- 레벨 패널
    # 위 여백 · 줄 사이 · 아래 여백을 모두 GAP 으로 맞춰 한쪽만 비어 보이지 않게 한다
    panel_x = 44
    panel_y = EDGE + HEADER_HEIGHT + BLOCK_GAP
    panel_w = WIDTH - panel_x * 2
    panel_h = GAP * 3 + LEVEL_ROW_HEIGHT * 2
    canvas.alpha_composite(
        _rounded((panel_w, panel_h), 20, PANEL_FILL, outline=(*theme.accent_dim, 170), width=1),
        (panel_x, panel_y),
    )

    row_left = panel_x + 32
    row_right = panel_x + panel_w - 32

    _level_row(
        canvas, draw,
        top=panel_y + GAP, left=row_left, right=row_right,
        title="음성 레벨", level=voice_level,
        current_xp=voice_current_xp, needed_xp=voice_needed_xp, rank=voice_rank,
        fill_left=theme.accent, fill_right=theme.accent_bright,
        level_color=theme.accent_bright,
    )
    _level_row(
        canvas, draw,
        top=panel_y + GAP * 2 + LEVEL_ROW_HEIGHT, left=row_left, right=row_right,
        title="채팅 레벨", level=chat_level,
        current_xp=chat_current_xp, needed_xp=chat_needed_xp, rank=chat_rank,
        fill_left=theme.bar, fill_right=theme.bar_bright,
        level_color=theme.accent_bright,
    )

    # --------------------------------------------------------- 티어 박스
    box_y = panel_y + panel_h + BLOCK_GAP
    box_h = HEIGHT - EDGE - box_y  # 남은 높이를 그대로 쓴다 (아래 여백은 EDGE)
    box_w = (WIDTH - 44 * 2 - 16) // 2
    _tier_box(
        canvas, draw, x=44, y=box_y, w=box_w, h=box_h,
        label="롤 솔랭 티어", tier_code=solo_tier_code,
        tier_name=solo_tier_name, record=solo_record, accent=theme.accent,
    )
    _tier_box(
        canvas, draw, x=44 + box_w + 16, y=box_y, w=box_w, h=box_h,
        label="롤 자유랭크 티어", tier_code=flex_tier_code,
        tier_name=flex_tier_name, record=flex_record, accent=theme.accent,
    )

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
