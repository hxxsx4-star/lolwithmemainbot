"""`/도움말 [일반|관리자]` — 봇 기능 요약."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors, Economy, Level, Roles, Warning as WarnConfig
from core.checks import can_moderate, is_staff

GENERAL_SECTIONS: list[tuple[str, str]] = [
    (
        "📝 서버 등록",
        f"`/양식안내` 닉네임 등록 양식을 봅니다\n"
        f"`/등록 [유저] [롤닉네임#태그]` 롤 계정을 등록합니다 (본인만 가능)\n"
        f"`/내계정` 등록된 내 롤 계정을 확인합니다\n"
        f"→ <#{Channels.ONBOARDING}> 에 양식대로 채팅하고 `/등록` 까지 마치면 "
        f"<@&{Roles.UNREGISTERED}> 역할이 사라집니다.",
    ),
    (
        "💰 포인트",
        f"`/출석` 하루 한 번 **{Economy.ATTENDANCE_REWARD}{Economy.UNIT}**\n"
        f"`/포인트 [유저]` 보유 포인트 확인\n"
        f"`/랭킹` 포인트 상위 10명\n"
        f"→ 음성 채널에 있으면 **{Economy.VOICE_INTERVAL_MINUTES}분마다 "
        f"{Economy.VOICE_REWARD}{Economy.UNIT}** 가 자동으로 쌓입니다.",
    ),
    (
        "🎯 라인 역할",
        f"<#{Channels.ROLE_PICKER}> 에서 이모지를 눌러 **주 라인 · 부 라인** 역할을 "
        f"받을 수 있습니다.\n"
        f"각 패널에서 하나만 고를 수 있고, 같은 이모지를 다시 누르면 해제됩니다.",
    ),
    (
        "📈 레벨",
        f"`/레벨 [유저]` 음성 · 채팅 레벨 확인\n"
        f"`/레벨랭킹 [음성|채팅]` 레벨 상위 10명\n"
        f"→ 음성 채널에 있으면 **분당 {Level.VOICE_XP_PER_MINUTE}XP** 가 자동으로 쌓입니다. "
        f"(Lv.0→1 은 {Level.BASE_XP}XP, 약 4시간)\n"
        f"→ 채팅은 메시지당 **{Level.CHAT_XP_PER_MESSAGE}XP** "
        f"({Level.CHAT_COOLDOWN_SECONDS}초 쿨타임)",
    ),
    (
        "🪪 프로필",
        "`/프로필 [유저]` 포인트 · 음성/채팅 레벨 · 솔랭/자유랭크 티어가 담긴 "
        "프로필 카드를 봅니다",
    ),
    (
        "⚔️ 내전",
        "내전 글의 **참가 / 참가 취소** 버튼으로 참여합니다\n"
        "`/내전참가자` 현재 참가자 목록\n"
        "→ 참가자 목록에는 `/등록` 한 **롤닉네임#태그만** 표시됩니다. "
        "등록하지 않으면 참가할 수 없습니다.",
    ),
    (
        "⚠️ 경고",
        f"`/경고목록` 내 경고 내역을 확인합니다\n"
        f"→ 누적 경고 **{WarnConfig.BAN_THRESHOLD}회** 이상이면 자동으로 서버가 차단됩니다.",
    ),
    (
        "📮 문의",
        f"<#{Channels.TICKET_PANEL}> 에서 버튼을 눌러 문의하세요.\n"
        f"서버 문의 · 티어 조정 · 분쟁 및 유저 신고 · 내전 문의 · 기타 문의",
    ),
]

ADMIN_SECTIONS: list[tuple[str, str]] = [
    (
        "⚠️ 경고 관리",
        f"`/경고 [유저] [횟수] [사유]` 경고 지급 → <#{Channels.WARN_LOG}>\n"
        f"`/차감 [유저] [횟수] [사유]` 경고 차감 → <#{Channels.WARN_REMOVE_LOG}>\n"
        f"`/경고목록 [유저]` 다른 사람의 경고 내역 조회\n"
        f"→ 누적 **{WarnConfig.BAN_THRESHOLD}회** 도달 시 사유를 종합해 자동 차단합니다.\n"
        "필요 권한: 서버 관리 또는 멤버 차단",
    ),
    (
        "💰 포인트 관리",
        f"`/포인트지급 [유저] [포인트] [사유]`\n"
        f"`/포인트차감 [유저] [포인트] [사유]`\n"
        f"→ 모든 변동은 <#{Channels.POINT_LOG}> 에, "
        f"음성 적립은 <#{Channels.VOICE_POINT_LOG}> 에 기록됩니다.",
    ),
    (
        "🎮 계정 등록 관리",
        f"`/등록 [유저] [롤닉네임#태그]` 다른 사람 계정도 등록 가능\n"
        f"`/등록해제 [유저] [사유]` 등록 해제\n"
        f"→ 로그: <#{Channels.REGISTER_LOG}>",
    ),
    (
        "🎭 역할 관리",
        f"`/역할동기화` 봇을 제외한 전 인원에게 기본 역할을 채우고 "
        f"<@&{Roles.UNREGISTERED}> 역할을 기준에 맞춰 정리합니다.\n"
        f"`/라인패널생성 [채널]` <#{Channels.ROLE_PICKER}> 에 라인 선택 패널을 올립니다.\n"
        f"→ 기본 역할과 미등록 역할은 봇이 켜질 때 자동으로 동기화됩니다.",
    ),
    (
        "⚔️ 내전 관리",
        f"`/내전생성 [제목] [룰] [판수]` → <#{Channels.SCRIM_FORUM}> 에 모집 글 작성\n"
        f"룰: 하드 피어리스 / 피어리스 없음 · 판수: 3판 2선 / 5판 3선 / 죽을 때까지\n"
        f"필요 권한: 서버 관리 또는 <@&{Roles.SCRIM_HOST}> 역할",
    ),
    (
        "📮 문의함 관리",
        "`/문의함생성 [채널]` 문의함 버튼 패널을 올립니다\n"
        "티켓 채널의 **티켓 닫기 / 채널 삭제** 버튼으로 정리합니다.",
    ),
    (
        "💾 백업",
        f"`/백업` 지금 바로 백업 실행\n"
        f"→ 매일 자정(KST)에 자동으로 <#{Channels.BACKUP}> 에 올라갑니다.",
    ),
]

SCOPE_CHOICES = [
    app_commands.Choice(name="일반", value="일반"),
    app_commands.Choice(name="관리자", value="관리자"),
]


class Help(commands.Cog, name="Help"):
    """도움말."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="도움말", description="봇 기능을 요약해서 보여줍니다.")
    @app_commands.describe(구분="일반 / 관리자")
    @app_commands.choices(구분=SCOPE_CHOICES)
    async def help_command(
        self,
        interaction: discord.Interaction,
        구분: app_commands.Choice[str] | None = None,
    ) -> None:
        scope = 구분.value if 구분 else "일반"
        member = interaction.user

        if scope == "관리자":
            allowed = isinstance(member, discord.Member) and (
                is_staff(member) or can_moderate(member)
            )
            if not allowed:
                await interaction.response.send_message(
                    "관리자 도움말은 **서버 관리 또는 멤버 차단 권한**이 있어야 볼 수 있습니다.\n"
                    "`/도움말 구분:일반` 을 사용해 주세요.",
                    ephemeral=True,
                )
                return
            embed = discord.Embed(
                title="🛠️ 롤 같이 하자 · 관리자 도움말",
                description="관리자 전용 명령어 모음입니다.",
                color=Colors.DANGER,
            )
            sections = ADMIN_SECTIONS
        else:
            embed = discord.Embed(
                title="📖 롤 같이 하자 · 도움말",
                description="서버에서 쓸 수 있는 명령어 모음입니다.",
                color=Colors.GOLD,
            )
            sections = GENERAL_SECTIONS

        for name, value in sections:
            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text="롤 같이 하자 · /도움말 구분:관리자 로 관리자 명령어를 볼 수 있습니다")
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
