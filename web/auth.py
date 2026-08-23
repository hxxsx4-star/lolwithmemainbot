"""디스코드 로그인.

봇 토큰과는 다른 값(클라이언트 시크릿)을 쓴다. 사용자를 디스코드로 보내
동의를 받고, 돌아온 코드를 액세스 토큰으로 바꿔 누구인지 확인한다.

로그인 상태는 **서명된 쿠키**로만 들고 있는다. 서버에 세션 저장소를 두면
웹앱을 재시작할 때마다 전원이 로그아웃되고, 관리할 것만 늘어난다.
액세스 토큰은 한 번 쓰고 버린다 — 저장해 둘 이유가 없다.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

import aiohttp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from config import GUILD_ID, Web

log = logging.getLogger("web.auth")

AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = "https://discord.com/api/v10/oauth2/token"
ME = "https://discord.com/api/v10/users/@me"
MY_MEMBER = "https://discord.com/api/v10/users/@me/guilds/{guild}/member"

# 필요한 최소한만 받는다. 이메일도 주소록도 필요 없다
SCOPES = "identify guilds.members.read"

_signer = URLSafeTimedSerializer(Web.SESSION_SECRET, salt="lolwithus-session")
_state_signer = URLSafeTimedSerializer(Web.SESSION_SECRET, salt="lolwithus-state")

SESSION_COOKIE = "lw_session"
STATE_COOKIE = "lw_state"


def login_url(state: str) -> str:
    from urllib.parse import urlencode

    return AUTHORIZE + "?" + urlencode({
        "client_id": Web.CLIENT_ID,
        "redirect_uri": f"{Web.BASE_URL}/callback",
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "prompt": "none",     # 이미 동의했으면 다시 묻지 않는다
    })


def make_state() -> str:
    """CSRF 방지용 일회용 값. 쿠키에 넣고 돌아올 때 대조한다."""
    return _state_signer.dumps(secrets.token_urlsafe(16))


def check_state(cookie: Optional[str], returned: Optional[str]) -> bool:
    if not cookie or not returned or cookie != returned:
        return False
    try:
        _state_signer.loads(cookie, max_age=600)
    except (BadSignature, SignatureExpired):
        return False
    return True


def make_session(user_id: int, name: str, avatar: Optional[str]) -> str:
    return _signer.dumps({"id": str(user_id), "name": name, "avatar": avatar})


def read_session(cookie: Optional[str]) -> Optional[dict]:
    """쿠키에서 로그인 정보를 꺼낸다. 위조·만료면 None."""
    if not cookie:
        return None
    try:
        return _signer.loads(cookie, max_age=Web.SESSION_DAYS * 86400)
    except (BadSignature, SignatureExpired):
        return None


async def exchange(code: str) -> Optional[dict]:
    """인가 코드를 액세스 토큰으로 바꾼다."""
    data = {
        "client_id": Web.CLIENT_ID,
        "client_secret": Web.CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": f"{Web.BASE_URL}/callback",
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(
            TOKEN, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as r:
            if r.status != 200:
                log.warning("토큰 교환 실패 %s: %s", r.status, (await r.text())[:200])
                return None
            return await r.json()


async def fetch_identity(access_token: str) -> Optional[dict]:
    """이 사람이 누구이고, 우리 서버 멤버인지 확인한다.

    서버 멤버가 아니면 로그인시키지 않는다. 우리 서버 활동을 보여 주는
    사이트라 바깥 사람에게는 보여 줄 것도 없고, 남의 데이터에 접근할 길을
    만들 이유도 없다.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(ME) as r:
            if r.status != 200:
                return None
            user = await r.json()

        if GUILD_ID is not None:
            async with s.get(MY_MEMBER.format(guild=GUILD_ID)) as r:
                if r.status != 200:
                    log.info("서버 멤버가 아님: %s (%s)", user.get("username"), r.status)
                    return None
                member = await r.json()
        else:
            member = {}

    return {
        "id": int(user["id"]),
        "name": user.get("global_name") or user.get("username") or "알 수 없음",
        "avatar": user.get("avatar"),
        "nick": member.get("nick"),
        "roles": [int(x) for x in member.get("roles", [])],
    }


def avatar_url(user_id: str, avatar: Optional[str], size: int = 128) -> str:
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return (
            f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size={size}"
        )
    index = (int(user_id) >> 22) % 6
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"
