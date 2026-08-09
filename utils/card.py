"""`/프로필` 카드 이미지 생성 (Pillow).

배경은 `assets/profile_bg.png` 를 쓰고, 없으면 LoL 느낌의 네이비 그라데이션을
직접 그려서 대신 사용한다. 테두리와 강조색은 배경(협곡/골드 계열)에 어울리도록
골드(#C8AA6E) · 청록(#0AC8B9) 조합으로 맞췄다.
"""
from __future__ import annotations

import functools
import io
import logging
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import (
    FONT_CANDIDATES,
    FONT_CANDIDATES_REGULAR,
    PROFILE_BACKGROUND,
    PROFILE_SLOGAN,
)

log = logging.getLogger("mainbot.card")

WIDTH, HEIGHT = 1000, 420

# 팔레트 — 배경 이미지 위에서도 읽히도록 대비를 확보한 값들
GOLD = (200, 170, 110)
GOLD_SOFT = (120, 90, 40)
CREAM = (240, 230, 210)
TEAL = (10, 200, 185)
NAVY = (10, 20, 40)
MUTED = (170, 180, 195)
DANGER = (230, 90, 80)

PANEL_FILL = (8, 16, 34, 205)
CHIP_FILL = (14, 28, 54, 225)
CHIP_EDGE = (120, 90, 40, 255)

# 티어별 강조색
TIER_COLORS: dict[str, tuple[int, int, int]] = {
    "C": (240, 230, 210),
    "GM": (220, 100, 100),
    "M": (190, 120, 220),
    "D": (110, 170, 240),
    "E": (80, 200, 140),
    "P": (90, 200, 200),
    "G": (220, 180, 90),
    "S": (180, 190, 200),
    "B": (170, 120, 90),
    "I": (140, 140, 140),
    "U": (120, 130, 145),
}


@functools.lru_cache(maxsize=32)
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """한글이 나오는 폰트를 찾아 로드한다. 없으면 기본 폰트."""
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


def _rounded(size: tuple[int, int], radius: int, fill, outline=None, width: int = 2) -> Image.Image:
    """둥근 사각형 레이어를 만든다."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=radius,
        fill=fill,
        outline=outline,
        width=width,
    )
    return layer


def _cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """비율을 유지하며 잘라서 꽉 채운다 (CSS 의 object-fit: cover)."""
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new = image.resize((max(1, int(src_w * scale)), max(1, int(src_h * scale))), Image.LANCZOS)
    left = (new.width - target_w) // 2
    top = (new.height - target_h) // 2
    return new.crop((left, top, left + target_w, top + target_h))


def _fallback_background(size: tuple[int, int]) -> Image.Image:
    """배경 파일이 없을 때 쓰는 네이비→골드 그라데이션."""
    w, h = size
    base = Image.new("RGB", (w, h), NAVY)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        ratio = y / max(1, h - 1)
        r = int(8 + 40 * ratio)
        g = int(18 + 40 * ratio)
        b = int(38 + 30 * ratio)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((-160, -220, 520, 340), fill=(70, 55, 20))
    gdraw.ellipse((w - 480, h - 260, w + 200, h + 220), fill=(10, 60, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.blend(base, glow, 0.45)


def _load_background() -> Image.Image:
    path = Path(PROFILE_BACKGROUND)
    if path.exists():
        try:
            with Image.open(path) as img:
                return _cover(img.convert("RGB"), (WIDTH, HEIGHT))
        except OSError as exc:
            log.warning("배경 이미지를 열 수 없습니다 (%s): %s", path, exc)
    return _fallback_background((WIDTH, HEIGHT))


def _circle_avatar(data: Optional[bytes], diameter: int) -> Image.Image:
    """아바타를 원형으로 자르고 골드 링을 두른다."""
    ring = 5
    total = diameter + ring * 2
    canvas = Image.new("RGBA", (total, total), (0, 0, 0, 0))

    if data:
        try:
            with Image.open(io.BytesIO(data)) as img:
                avatar = _cover(img.convert("RGB"), (diameter, diameter))
        except OSError:
            avatar = Image.new("RGB", (diameter, diameter), (30, 40, 60))
    else:
        avatar = Image.new("RGB", (diameter, diameter), (30, 40, 60))

    mask = Image.new("L", (diameter * 4, diameter * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * 4 - 1, diameter * 4 - 1), fill=255)
    mask = mask.resize((diameter, diameter), Image.LANCZOS)

    canvas.paste(avatar, (ring, ring), mask)
    ImageDraw.Draw(canvas).ellipse(
        (ring // 2, ring // 2, total - ring // 2 - 1, total - ring // 2 - 1),
        outline=GOLD,
        width=ring,
    )
    return canvas


def _fit(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """가로 폭에 맞게 말줄임한다."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and draw.textlength(trimmed + ellipsis, font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


def _chip(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    label: str,
    value: str,
    value_color: tuple[int, int, int],
) -> None:
    """라벨 + 값으로 이루어진 정보 칩 하나."""
    x, y, w, h = box
    chip = _rounded((w, h), 14, CHIP_FILL, outline=CHIP_EDGE, width=2)
    canvas.alpha_composite(chip, (x, y))
    draw.text((x + 16, y + 11), label, font=_font(17, bold=False), fill=MUTED)
    value_font = _font(27)
    draw.text(
        (x + 16, y + 33),
        _fit(draw, value, value_font, w - 32),
        font=value_font,
        fill=value_color,
    )


def render_profile_card(
    *,
    display_name: str,
    user_tag: str,
    avatar_bytes: Optional[bytes],
    riot_id: Optional[str],
    tier_code: Optional[str],
    tier_label: str,
    main_lane: str,
    sub_lane: str,
    points: int,
    warnings: int,
    rank: Optional[int],
    joined: str,
    voice_time: str,
    attendance: int,
) -> io.BytesIO:
    """프로필 카드를 그려 PNG 바이트로 돌려준다."""
    background = _load_background().convert("RGBA")

    # 배경을 살짝 어둡게 깔아 글자 대비를 확보
    shade = Image.new("RGBA", (WIDTH, HEIGHT), (5, 10, 24, 110))
    background.alpha_composite(shade)

    canvas = background
    draw = ImageDraw.Draw(canvas)

    # 바깥 골드 프레임
    frame = _rounded((WIDTH - 12, HEIGHT - 12), 30, None, outline=(*GOLD_SOFT, 255), width=3)
    canvas.alpha_composite(frame, (6, 6))

    # 본문 패널
    panel = _rounded((WIDTH - 48, HEIGHT - 48), 26, PANEL_FILL, outline=(*GOLD, 210), width=2)
    canvas.alpha_composite(panel, (24, 24))

    # ------------------------------------------------------------ 아바타
    avatar_d = 156
    avatar = _circle_avatar(avatar_bytes, avatar_d)
    avatar_x = 64
    avatar_y = 62
    canvas.alpha_composite(avatar, (avatar_x, avatar_y))

    # ------------------------------------------------- 슬로건 (우물 밖 → 롤 같이 하자)
    slogan_font = _font(24)
    slogan_w = int(draw.textlength(PROFILE_SLOGAN, font=slogan_font)) + 44
    slogan_h = 44
    slogan_x = avatar_x + (avatar_d + 10 - slogan_w) // 2
    slogan_y = 300
    pill = _rounded((slogan_w, slogan_h), slogan_h // 2, (*GOLD, 235))
    canvas.alpha_composite(pill, (slogan_x, slogan_y))
    draw.text(
        (slogan_x + slogan_w // 2, slogan_y + slogan_h // 2),
        PROFILE_SLOGAN,
        font=slogan_font,
        fill=NAVY,
        anchor="mm",
    )

    # -------------------------------------------------------------- 이름
    left = 254
    right_limit = WIDTH - 48
    name_font = _font(42)
    draw.text(
        (left, 52),
        _fit(draw, display_name, name_font, right_limit - left),
        font=name_font,
        fill=CREAM,
    )

    tag_font = _font(21, bold=False)
    draw.text(
        (left, 104),
        _fit(draw, user_tag, tag_font, right_limit - left),
        font=tag_font,
        fill=MUTED,
    )

    riot_font = _font(25)
    riot_text = riot_id or "롤 계정 미등록 · /등록 으로 등록해 주세요"
    draw.text(
        (left, 132),
        _fit(draw, riot_text, riot_font, right_limit - left),
        font=riot_font,
        fill=TEAL if riot_id else MUTED,
    )

    # 구분선
    draw.line([(left, 174), (right_limit, 174)], fill=(*GOLD_SOFT, 255), width=2)

    # -------------------------------------------------------------- 정보 칩
    chip_w, chip_h, gap = 218, 70, 15
    xs = [left + i * (chip_w + gap) for i in range(3)]

    tier_color = TIER_COLORS.get(tier_code or "", CREAM)
    _chip(canvas, draw, (xs[0], 188, chip_w, chip_h), "티어", tier_label, tier_color)
    _chip(canvas, draw, (xs[1], 188, chip_w, chip_h), "주 라인", main_lane, CREAM)
    _chip(canvas, draw, (xs[2], 188, chip_w, chip_h), "부 라인", sub_lane, CREAM)

    _chip(canvas, draw, (xs[0], 270, chip_w, chip_h), "포인트", f"{points:,} P", GOLD)
    _chip(
        canvas,
        draw,
        (xs[1], 270, chip_w, chip_h),
        "경고",
        f"{warnings}회",
        DANGER if warnings else CREAM,
    )
    _chip(
        canvas,
        draw,
        (xs[2], 270, chip_w, chip_h),
        "포인트 순위",
        f"{rank}위" if rank else "-",
        TEAL,
    )

    # -------------------------------------------------------------- 하단 정보
    footer_font = _font(19, bold=False)
    footer = f"서버 입장 {joined}   ·   음성 활동 {voice_time}   ·   출석 {attendance}일"
    draw.text(
        (left, 356),
        _fit(draw, footer, footer_font, right_limit - left),
        font=footer_font,
        fill=MUTED,
    )

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
