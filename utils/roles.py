"""역할 계산 / 미등록 역할 동기화."""
from __future__ import annotations

import logging
from typing import Optional

import discord

from config import LANE_NAMES, LANE_SHORT, Roles, TIER_NAMES
from utils.parsing import matches_format

log = logging.getLogger("mainbot.roles")

# 역할 ID → 약자 (역방향 조회용)
TIER_BY_ROLE: dict[int, str] = {rid: code for code, rid in Roles.TIERS.items()}
MAIN_LANE_BY_ROLE: dict[int, str] = {rid: code for code, rid in Roles.MAIN_LANES.items()}
SUB_LANE_BY_ROLE: dict[int, str] = {rid: code for code, rid in Roles.SUB_LANES.items()}

# 티어 높은 순 (여러 개 붙어 있으면 가장 높은 것을 대표로)
TIER_ORDER = ["C", "GM", "M", "D", "E", "P", "G", "S", "B", "I", "U"]


def tier_of(member: discord.Member) -> Optional[str]:
    """멤버가 가진 티어 역할 중 가장 높은 티어의 약자."""
    owned = {TIER_BY_ROLE[r.id] for r in member.roles if r.id in TIER_BY_ROLE}
    for code in TIER_ORDER:
        if code in owned:
            return code
    return None


def tier_name_of(member: discord.Member) -> str:
    code = tier_of(member)
    return TIER_NAMES.get(code, "미설정") if code else "미설정"


def main_lane_of(member: discord.Member) -> Optional[str]:
    for role in member.roles:
        if role.id in MAIN_LANE_BY_ROLE:
            return MAIN_LANE_BY_ROLE[role.id]
    return None


def sub_lane_of(member: discord.Member) -> Optional[str]:
    for role in member.roles:
        if role.id in SUB_LANE_BY_ROLE:
            return SUB_LANE_BY_ROLE[role.id]
    return None


def lane_label(code: Optional[str]) -> str:
    """임베드용 라인 표기."""
    if code is None:
        return "미설정"
    return f"{LANE_NAMES.get(code, code)} ({code})"


def lane_label_short(code: Optional[str]) -> str:
    """프로필 카드처럼 폭이 좁은 곳에서 쓰는 라인 표기."""
    if code is None:
        return "미설정"
    return f"{LANE_SHORT.get(code, code)} ({code})"


async def apply_tier_and_lanes(
    member: discord.Member,
    tier: str,
    main_lane: str,
    sub_lane: Optional[str],
    *,
    reason: str = "닉네임 양식 등록",
) -> tuple[list[discord.Role], list[discord.Role]]:
    """티어·주라인·부라인 역할을 갈아끼운다. (추가된 역할, 제거된 역할)"""
    guild = member.guild

    wanted_ids: set[int] = set()
    if (rid := Roles.TIERS.get(tier)) is not None:
        wanted_ids.add(rid)
    if (rid := Roles.MAIN_LANES.get(main_lane)) is not None:
        wanted_ids.add(rid)
    if sub_lane and (rid := Roles.SUB_LANES.get(sub_lane)) is not None:
        wanted_ids.add(rid)

    managed_ids = (
        set(Roles.TIERS.values())
        | set(Roles.MAIN_LANES.values())
        | set(Roles.SUB_LANES.values())
    )

    current_ids = {r.id for r in member.roles}
    to_add_ids = wanted_ids - current_ids
    to_remove_ids = (managed_ids & current_ids) - wanted_ids

    to_add = [r for rid in to_add_ids if (r := guild.get_role(rid)) is not None]
    to_remove = [r for rid in to_remove_ids if (r := guild.get_role(rid)) is not None]

    try:
        if to_add:
            await member.add_roles(*to_add, reason=reason)
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
    except discord.Forbidden:
        log.warning("[%s] 역할을 변경할 권한이 없습니다.", member)
        return [], []
    except discord.HTTPException as exc:
        log.warning("[%s] 역할 변경 실패: %s", member, exc)
        return [], []

    return to_add, to_remove


def needs_unregistered_role(member: discord.Member, registered: bool) -> bool:
    """미등록 역할을 달고 있어야 하는 사람인지.

    닉네임 양식을 갖췄고 `/등록`까지 마쳤다면 더 이상 미등록이 아니다.
    """
    if member.bot:
        return False
    return not (registered and matches_format(member.display_name))


async def sync_unregistered_role(
    member: discord.Member, registered: bool, *, reason: str = "미등록 역할 동기화"
) -> Optional[bool]:
    """미등록 역할을 붙이거나 뗀다.

    반환값: True=지급함, False=회수함, None=변화 없음/실패
    """
    if member.bot:
        return None
    role = member.guild.get_role(Roles.UNREGISTERED)
    if role is None:
        log.warning("미등록 역할(%s)을 찾을 수 없습니다.", Roles.UNREGISTERED)
        return None

    has = role in member.roles
    should = needs_unregistered_role(member, registered)

    try:
        if should and not has:
            await member.add_roles(role, reason=reason)
            return True
        if not should and has:
            await member.remove_roles(role, reason=reason)
            return False
    except discord.Forbidden:
        log.warning("[%s] 미등록 역할을 변경할 권한이 없습니다.", member)
    except discord.HTTPException as exc:
        log.warning("[%s] 미등록 역할 변경 실패: %s", member, exc)
    return None
