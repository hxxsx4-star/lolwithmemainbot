"""승부예측 패널에 붙일 VS 배너.

임베드는 큰 이미지를 **하나만** 넣을 수 있어서 두 팀 로고를 각각 붙일 방법이
없다. 그래서 로고 둘을 한 장으로 합성해 놓고 그걸 임베드 이미지로 쓴다.

로고를 못 받아 오면 배너를 포기하고 `None` 을 돌려준다. 예측 패널은 배너가
없어도 멀쩡히 동작해야 하므로, 여기서 실패해도 절대 예외를 올리지 않는다.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

import aiohttp
from PIL import Image, ImageDraw

from utils.card import _font

log = logging.getLogger("mainbot.versus")

WIDTH, HEIGHT = 720, 240
LOGO = 132                 # 로고 한 변
MARGIN = 96                # 좌우 여백
BG = (23, 26, 33, 255)     # 디스코드 어두운 배경과 붙었을 때 튀지 않는 색
FG = (235, 238, 245, 255)
DIM = (120, 128, 145, 255)

FETCH_TIMEOUT = 6          # 로고 하나당 대기 한도(초). 패널 등록이 밀리면 안 된다
MAX_BYTES = 4 * 1024 * 1024


async def _fetch(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
    if not url:
        return None
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT)) as r:
            if r.status != 200:
                return None
            # content.read(n) 은 "최대" n 바이트라 이미지가 잘려 들어온다.
            # 끝까지 받은 뒤 크기를 확인한다.
            data = await r.read()
            if not data or len(data) > MAX_BYTES:
                return None
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        log.debug("팀 로고를 받지 못했습니다 (%s): %s", url, exc)
        return None


def _logo(data: Optional[bytes], size: int) -> Optional[Image.Image]:
    """로고를 정사각형 안에 비율 그대로 맞춰 넣는다."""
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = img.convert("RGBA")
            img.thumbnail((size, size), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            canvas.paste(
                img, ((size - img.width) // 2, (size - img.height) // 2), img
            )
            return canvas
    except Exception as exc:  # Pillow 가 던지는 예외 종류가 많아 통째로 받는다
        log.debug("팀 로고를 열지 못했습니다: %s", exc)
        return None


def _placeholder(code: str, size: int) -> Image.Image:
    """로고가 없을 때 팀 약칭을 대신 그린다."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(44, 48, 58, 255))
    text = (code or "?")[:4].upper()

    # 한글 약칭은 같은 글자 수여도 훨씬 넓어서 원을 넘친다. 들어갈 때까지 줄인다.
    limit = size * 0.7
    font = _font(max(20, size // 3))
    while font.size > 12 and draw.textlength(text, font=font) > limit:
        font = _font(font.size - 2)

    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((size - (box[2] - box[0])) // 2 - box[0],
         (size - (box[3] - box[1])) // 2 - box[1]),
        text,
        font=font,
        fill=DIM,
    )
    return canvas


async def render_versus(
    url_a: str, url_b: str, *, code_a: str = "", code_b: str = ""
) -> Optional[io.BytesIO]:
    """두 팀 로고를 나란히 놓은 PNG 를 만든다. 둘 다 못 받으면 None."""
    try:
        async with aiohttp.ClientSession() as session:
            raw_a, raw_b = await asyncio.gather(
                _fetch(session, url_a), _fetch(session, url_b)
            )
    except Exception as exc:
        log.debug("로고 조회 중 오류: %s", exc)
        return None

    logo_a, logo_b = _logo(raw_a, LOGO), _logo(raw_b, LOGO)
    if logo_a is None and logo_b is None:
        # 둘 다 없으면 약칭만 두 개 그린 배너가 되어 볼 게 없다
        return None
    if logo_a is None:
        logo_a = _placeholder(code_a, LOGO)
    if logo_b is None:
        logo_b = _placeholder(code_b, LOGO)

    canvas = Image.new("RGBA", (WIDTH, HEIGHT), BG)
    top = (HEIGHT - LOGO) // 2
    canvas.alpha_composite(logo_a, (MARGIN, top))
    canvas.alpha_composite(logo_b, (WIDTH - MARGIN - LOGO, top))

    draw = ImageDraw.Draw(canvas)
    font = _font(46)
    box = draw.textbbox((0, 0), "VS", font=font)
    draw.text(
        ((WIDTH - (box[2] - box[0])) // 2 - box[0],
         (HEIGHT - (box[3] - box[1])) // 2 - box[1]),
        "VS",
        font=font,
        fill=FG,
    )

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
