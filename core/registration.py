"""롤 계정 등록 공통 로직.

`/등록` 명령어와 소개 채널 자동 등록이 같은 절차를 쓰도록 한곳에 모았다.
중복 확인 → 라이엇 API 검증 → 저장 → 미등록 역할 정리 → 로그 전송.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import discord

from config import Channels, Colors
from utils.logs import base_embed, send_log, truncate, user_field
from utils.riot import RiotError
from utils.roles import SET_REGISTERED, set_verified_role, sync_registration_roles

log = logging.getLogger("mainbot.registration")


@dataclass(slots=True)
class RegisterResult:
    """등록 시도 결과. 실패해도 예외 대신 이 객체로 돌려준다."""

    ok: bool
    error: Optional[str] = None      # 실패 사유 (그대로 보여 줄 수 있는 문장)
    game_name: str = ""
    tag_line: str = ""
    puuid: Optional[str] = None
    verified: bool = False           # 라이엇 API 로 실제 확인했는지
    rank_label: Optional[str] = None
    previous_riot_id: Optional[str] = None
    role_removed: bool = False       # 미등록 역할이 회수됐는지
    unchanged: bool = False          # 이미 같은 계정으로 등록되어 있었는지

    @property
    def riot_id(self) -> str:
        return f"{self.game_name}#{self.tag_line}"


async def register_riot_account(
    bot,
    member: discord.Member,
    game_name: str,
    tag_line: str,
    *,
    actor: discord.abc.User,
    source: str = "명령어",
) -> RegisterResult:
    """롤 계정을 등록한다.

    `source` 는 로그에 남길 경로 표시다. (예: '명령어', '소개 채널 자동 등록')
    """
    if member.bot:
        return RegisterResult(ok=False, error="봇은 롤 계정을 등록할 수 없습니다.")

    # 1) 다른 사람이 이미 쓰고 있는 계정인지
    owner = await bot.db.riot_owner(game_name, tag_line)
    if owner is not None and owner != member.id:
        return RegisterResult(
            ok=False,
            error=(
                f"`{game_name}#{tag_line}` 계정은 이미 <@{owner}> 님이 등록했습니다.\n"
                "잘못된 등록이라면 관리자에게 `/등록해제` 를 요청해 주세요."
            ),
        )

    # 2) 라이엇 API 로 실제 존재하는 계정인지 확인
    verified = False
    puuid: str | None = None
    rank_label: str | None = None

    if bot.riot.enabled:
        try:
            account = await bot.riot.fetch_account(game_name, tag_line)
        except RiotError as exc:
            return RegisterResult(ok=False, error=str(exc))
        if account is None:
            return RegisterResult(
                ok=False,
                error=(
                    f"`{game_name}#{tag_line}` 계정을 찾을 수 없습니다.\n"
                    "닉네임과 태그를 다시 확인해 주세요. (대소문자는 상관없습니다)"
                ),
            )
        # 라이엇이 알려준 정확한 표기로 저장한다
        game_name, tag_line = account.game_name, account.tag_line
        puuid = account.puuid
        verified = True

        rank = await bot.riot.fetch_solo_rank(puuid)
        rank_label = rank.label if rank else "언랭크 / 배치 미완료"

    # 3) 저장
    previous = await bot.db.get_user(member.id)
    unchanged = previous.riot_id == f"{game_name}#{tag_line}"
    await bot.db.set_riot_account(member.id, game_name, tag_line, puuid, actor.id)

    # 4) 미등록 역할 정리
    swapped = await sync_registration_roles(member, True, reason="롤 계정 등록 완료")

    # 다른 계정으로 갈아 끼우면 DB 의 인증 기록이 지워진다. 역할도 같이 빼야
    # "본인인증" 을 달고 남의 계정을 쓰는 상태가 생기지 않는다
    fresh = await bot.db.get_user(member.id)
    await set_verified_role(member, fresh.verified, reason="롤 계정 등록 변경")

    result = RegisterResult(
        ok=True,
        game_name=game_name,
        tag_line=tag_line,
        puuid=puuid,
        verified=verified,
        rank_label=rank_label,
        previous_riot_id=previous.riot_id,
        role_removed=swapped == SET_REGISTERED,
        unchanged=unchanged,
    )

    # 5) 로그
    log_embed = base_embed("🎮 롤 계정 등록", Colors.TEAL)
    log_embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    log_embed.add_field(name="대상", value=user_field(member), inline=True)
    log_embed.add_field(name="등록한 사람", value=user_field(actor), inline=True)
    log_embed.add_field(name="경로", value=source, inline=True)
    log_embed.add_field(name="롤 계정", value=f"`{result.riot_id}`", inline=False)
    log_embed.add_field(
        name="이전 등록",
        value=f"`{previous.riot_id}`" if previous.riot_id else "없음",
        inline=True,
    )
    log_embed.add_field(name="API 확인", value="완료" if verified else "미확인", inline=True)
    if rank_label:
        log_embed.add_field(name="솔로랭크", value=rank_label, inline=True)
    if puuid:
        log_embed.add_field(name="PUUID", value=f"`{truncate(puuid, 90)}`", inline=False)
    await send_log(bot, Channels.REGISTER_LOG, log_embed)

    return result
