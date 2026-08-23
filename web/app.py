"""롤 같이 하자 웹사이트.

봇과 **같은 VM 의 다른 프로세스**로 돈다. 웹이 죽어도 봇은 살아 있고,
그 반대도 마찬가지다. nginx 뒤에만 붙고 밖으로 직접 열리지 않는다.

DB 는 **읽기만 한다.** 웹에서 포인트를 건드릴 수 있으면 그게 곧 구멍이라,
쓰기 경로를 아예 만들지 않았다.
"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Web  # noqa: E402
from core.db import Database, now  # noqa: E402
from utils.riot import RankEntry  # noqa: E402
from web import auth, matches  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("web")

HERE = Path(__file__).resolve().parent
app = FastAPI(title="롤 같이 하자", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

db = Database()


@app.on_event("startup")
async def startup() -> None:
    await db.connect()
    log.info("웹 시작 · DB 연결 완료")


@app.on_event("shutdown")
async def shutdown() -> None:
    await db.close()


def current_user(request: Request) -> dict | None:
    return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))


def page(request: Request, name: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"user": current_user(request), **context},
    )


# ------------------------------------------------------------------ 로그인


@app.get("/login")
async def login() -> RedirectResponse:
    state = auth.make_state()
    response = RedirectResponse(auth.login_url(state), status_code=302)
    response.set_cookie(
        auth.STATE_COOKIE, state,
        max_age=600, httponly=True, secure=True, samesite="lax",
    )
    return response


@app.get("/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return page(request, "error.html",
                    title="로그인 취소", message="디스코드에서 로그인을 취소했습니다.")

    if not auth.check_state(request.cookies.get(auth.STATE_COOKIE), state):
        # 정상적인 흐름이 아니면 여기서 끊는다 (CSRF 방지)
        return page(request, "error.html", title="로그인 실패",
                    message="로그인 요청이 올바르지 않습니다. 다시 시도해 주세요.")

    token = await auth.exchange(code) if code else None
    if not token or "access_token" not in token:
        return page(request, "error.html", title="로그인 실패",
                    message="디스코드와 통신하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")

    identity = await auth.fetch_identity(token["access_token"])
    if identity is None:
        return page(
            request, "error.html", title="서버 멤버가 아닙니다",
            message="이 사이트는 「롤 같이 하자」 서버원만 이용할 수 있습니다.",
            invite="https://discord.gg/Jt7PxHapxV",
        )

    response = RedirectResponse("/me", status_code=302)
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session(identity["id"], identity["nick"] or identity["name"],
                          identity["avatar"]),
        max_age=Web.SESSION_DAYS * 86400,
        httponly=True, secure=True, samesite="lax",
    )
    response.delete_cookie(auth.STATE_COOKIE)
    log.info("로그인: %s (%s)", identity["name"], identity["id"])
    return response


@app.get("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(auth.SESSION_COOKIE)
    return response


# -------------------------------------------------------------------- 화면


KIND_LABELS = {
    "theme": "카드 테마",
    "slogan": "카드 문구",
    "color_role": "색상 역할",
    "team_role": "LCK 응원",
    "champion_role": "챔피언",
}


def _rank_label(raw: dict | None) -> str:
    """캐시된 랭크 정보를 한 줄로. 없으면 빈 문자열."""
    if not raw:
        return ""
    entry = RankEntry.from_dict(raw)
    return entry.label if entry else ""


@app.get("/me", response_class=HTMLResponse)
async def me(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=302)

    uid = int(user["id"])
    row = await db.get_user(uid)
    correct, played, profit = await db.prediction_stats(uid)

    items = []
    for purchase in await db.active_purchases(uid):
        expires = dt.datetime.fromisoformat(str(purchase["expires_at"]))
        items.append({
            "kind": KIND_LABELS.get(str(purchase["kind"]), str(purchase["kind"])),
            "name": str(purchase["value"] or purchase["item_key"]),
            "left": max(0, (expires - now()).days),
        })

    return page(
        request, "me.html", active="me", me=row,
        avatar=auth.avatar_url(user["id"], user["avatar"], 128),
        predictions={"correct": correct, "played": played, "profit": profit},
        solo=_rank_label(row.solo_rank), flex=_rank_label(row.flex_rank),
        items=items,
    )


@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    """서버원 안에서만 전적을 찾는다.

    아무나 검색하게 두면 라이엇 레이트리밋이 금방 차서 봇의 랭크 조회까지
    같이 죽는다. 등록을 마친 서버원으로 범위를 닫아 두면 조회량이 예측
    가능해지고, 캐시도 잘 듣는다.
    """
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=302)

    members = await db.registered_members()
    needle = q.strip().lower()
    hits = [
        m for m in members
        if needle and needle in f"{m['riot_game_name']}#{m['riot_tag_line']}".lower()
    ]

    target = None
    games: list[dict] = []
    customs: list[dict] = []
    if len(hits) == 1 or (hits and needle == f"{hits[0]['riot_game_name']}#"
                          f"{hits[0]['riot_tag_line']}".lower()):
        target = hits[0]
        puuid = str(target["riot_puuid"])
        await matches.refresh(db, puuid)
        games = await matches.recent(db, puuid, Web.MATCH_COUNT)
        customs = await matches.recent(db, puuid, Web.MATCH_COUNT, only_custom=True)

    return page(
        request, "search.html", active="search", q=q,
        member_count=len(members),
        hits=[] if target else hits[:20],
        target=target, games=games, customs=customs,
        totals=matches.totals(games), custom_totals=matches.totals(customs),
        avatar=auth.avatar_url(user["id"], user["avatar"], 128),
    )


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
