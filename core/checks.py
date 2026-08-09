"""슬래시 명령어 권한 검사."""
from __future__ import annotations

import discord
from discord import app_commands

from config import Roles


def is_staff(member: discord.Member) -> bool:
    """서버 관리 권한이 있으면 스태프로 본다."""
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


def can_host_scrim(member: discord.Member) -> bool:
    """서버 관리 권한자 또는 내전 관리 역할 보유자."""
    if is_staff(member):
        return True
    return any(role.id == Roles.SCRIM_HOST for role in member.roles)


def can_moderate(member: discord.Member) -> bool:
    """경고를 다룰 수 있는 사람."""
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild or perms.ban_members


class MissingStaff(app_commands.CheckFailure):
    """권한 부족을 알리는 예외. 메시지를 그대로 사용자에게 보여준다."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def staff_only():
    """서버 관리 권한 필요."""

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_staff(member):
            raise MissingStaff("이 명령어는 **서버 관리 권한**이 있어야 사용할 수 있습니다.")
        return True

    return app_commands.check(predicate)


def moderator_only():
    """경고 관련 권한 필요."""

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not can_moderate(member):
            raise MissingStaff(
                "이 명령어는 **서버 관리 또는 멤버 차단 권한**이 있어야 사용할 수 있습니다."
            )
        return True

    return app_commands.check(predicate)


def scrim_host_only():
    """내전 생성 권한 필요."""

    async def predicate(interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not can_host_scrim(member):
            raise MissingStaff(
                "내전은 **서버 관리 권한**이 있거나 "
                f"<@&{Roles.SCRIM_HOST}> 역할이 있어야 생성할 수 있습니다."
            )
        return True

    return app_commands.check(predicate)
