"""신규 인원 등록 안내.

소개 채널에 `롤닉네임#태그/티어/주라인 부라인` 양식으로 채팅을 치면 한 번에

  1. 서버 닉네임을 양식대로 바꾸고
  2. 티어 · 주라인 · 부라인 역할을 지급하고
  3. 적어 낸 롤닉#태그로 롤 계정 등록까지 마친 뒤
  4. 미등록 역할을 회수한다.

따로 `/등록` 을 칠 필요가 없다. 등록만 실패하면(계정 없음 · 중복 등) 닉네임과
역할은 그대로 두고 미등록 역할을 남긴 채 사유를 알려 준다.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from config import Channels, Colors, GUILD_ID, Roles
from core.checks import staff_only
from core.registration import register_riot_account
from utils.logs import base_embed, truncate
from utils.parsing import FormatError, parse_profile_format
from utils import roles
from utils.roles import (
    apply_tier_and_lanes,
    ensure_default_roles,
    lane_label,
    role_problem,
    sync_unregistered_role,
)

log = logging.getLogger("mainbot.onboarding")

GUIDE = (
    "**양식**  `롤닉네임#태그/올해최고티어/주라인 부라인`\n"
    "**예시**  `홍길동#KR1/M405/MID AD`\n\n"
    "**티어** 언랭 `U` · 아이언 `I` · 브론즈 `B` · 실버 `S` · 골드 `G` · "
    "플래티넘 `P` · 에메랄드 `E` · 다이아 `D` · 마스터 `M` · 그마 `GM` · 챌린저 `C`\n"
    "**라인** 탑 `TOP` · 정글 `JG` · 미드 `MID` · 원딜 `AD` · 서폿 `SUP`"
)


class Onboarding(commands.Cog, name="Onboarding"):
    """닉네임 양식 처리와 미등록 역할 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        # 같은 사람이 연달아 올린 소개가 겹쳐 처리되는 것을 막는다
        self._processing: set[int] = set()

    # ------------------------------------------------- 시작할 때 자동 동기화

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """봇이 켜지면 기존 인원 전체의 미등록 역할을 한 번 맞춰 준다.

        on_ready 는 재접속할 때마다 불리므로 프로세스당 한 번만 돌린다.
        인원이 많으면 시간이 걸리니 백그라운드로 돌려 시작을 막지 않는다.
        """
        if self._startup_done:
            return
        self._startup_done = True
        self.bot.loop.create_task(self._startup_sync())

    async def _startup_sync(self) -> None:
        for guild in self.bot.guilds:
            if GUILD_ID is not None and guild.id != GUILD_ID:
                continue

            problem = role_problem(guild, Roles.UNREGISTERED)
            if problem is not None:
                log.warning(
                    "[%s] 미등록 역할을 자동 지급하지 못했습니다: %s",
                    guild.name,
                    discord.utils.remove_markdown(problem),
                )
                continue

            given, taken, skipped, failed, defaults = await self._bulk_sync(
                guild, reason="봇 시작 시 역할 자동 동기화"
            )
            log.info(
                "[%s] 역할 자동 동기화 완료 — 미등록 지급 %d명 / 회수 %d명 /"
                " 기본 역할 보충 %d명 / 봇 제외 %d명 / 실패 %d명",
                guild.name,
                given,
                taken,
                defaults,
                skipped,
                failed,
            )

    async def _bulk_sync(
        self, guild: discord.Guild, *, reason: str
    ) -> tuple[int, int, int, int, int]:
        """서버 전원의 역할을 맞춘다.

        기본 역할은 빠진 사람에게 채워 주고, 미등록 역할은 조건에 맞춰 붙이거나 뗀다.
        (지급, 회수, 봇제외, 실패, 기본역할 보충)
        """
        # 멤버 캐시가 비어 있으면 먼저 받아 온다
        if not guild.chunked:
            try:
                await guild.chunk()
            except (discord.ClientException, discord.HTTPException) as exc:
                log.warning("[%s] 멤버 목록을 불러오지 못했습니다: %s", guild.name, exc)

        given = taken = skipped = failed = defaults = 0

        for index, member in enumerate(guild.members):
            if member.bot:
                skipped += 1
                continue

            if await ensure_default_roles(member, reason=reason):
                defaults += 1

            user = await self.bot.db.get_user(member.id)
            result = await sync_unregistered_role(member, user.registered, reason=reason)
            if result == roles.GIVEN:
                given += 1
            elif result == roles.TAKEN:
                taken += 1
            elif result == roles.FAILED:
                failed += 1

            # 디스코드 속도 제한을 피하려고 잠깐씩 쉰다
            if index % 20 == 19:
                await asyncio.sleep(1)

        return given, taken, skipped, failed, defaults

    # -------------------------------------------------------- 양식 채팅 처리

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.channel.id != Channels.ONBOARDING:
            return
        if not isinstance(message.author, discord.Member):
            return

        if message.author.id in self._processing:
            return

        try:
            parsed = parse_profile_format(message.content)
        except FormatError as exc:
            embed = base_embed(
                "❌ 양식을 확인해 주세요",
                Colors.DANGER,
                description=f"{exc}\n\n{GUIDE}",
            )
            await self._reply_temp(message, embed, seconds=60)
            return

        member = message.author
        problems: list[str] = []
        self._processing.add(member.id)
        try:
            await self._apply_intro(message, member, parsed, problems)
        finally:
            self._processing.discard(member.id)

    async def _apply_intro(self, message, member, parsed, problems) -> None:
        """소개 한 건을 닉네임 → 역할 → 계정 등록 순서로 처리한다."""
        # 1) 서버 닉네임을 양식 그대로 맞춘다
        nickname = parsed.raw[:32]
        if member.display_name != nickname:
            try:
                await member.edit(nick=nickname, reason="닉네임 양식 등록")
            except discord.Forbidden:
                problems.append(
                    "닉네임을 바꿀 권한이 없습니다. (봇 역할을 대상보다 위로 올려 주세요)"
                )
            except discord.HTTPException as exc:
                problems.append(f"닉네임 변경 실패: `{exc}`")

        # 2) 티어 · 주라인 · 부라인 역할
        added, removed = await apply_tier_and_lanes(
            member, parsed.tier, parsed.main_lane, parsed.sub_lane
        )
        if not added and not removed and not member.guild.me.guild_permissions.manage_roles:
            problems.append("봇에게 **역할 관리 권한**이 없습니다.")

        # 3) 소개에 적은 롤닉#태그로 그 자리에서 등록까지 끝낸다
        result = await register_riot_account(
            self.bot,
            member,
            parsed.game_name,
            parsed.tag_line,
            actor=member,
            source=f"<#{Channels.ONBOARDING}> 자동 등록",
        )
        if not result.ok:
            # 등록만 실패해도 닉네임·역할은 이미 반영됐으니 그 사실을 함께 알린다
            await sync_unregistered_role(member, False, reason="닉네임 양식 등록")
            embed = base_embed(
                "⚠️ 닉네임과 역할만 반영되었습니다",
                Colors.DANGER,
                description=(
                    f"{member.mention} 님, 롤 계정 등록에 실패해서 "
                    "미등록 역할이 아직 남아 있습니다."
                ),
            )
            embed.add_field(name="닉네임", value=f"`{nickname}`", inline=False)
            embed.add_field(name="실패 사유", value=truncate(result.error), inline=False)
            embed.add_field(
                name="어떻게 하나요?",
                value=(
                    "롤 닉네임과 태그를 확인한 뒤 다시 적어 주세요.\n"
                    "계속 안 되면 관리자에게 문의해 주세요."
                ),
                inline=False,
            )
            if problems:
                embed.add_field(
                    name="그 밖에 처리하지 못한 항목",
                    value=truncate("\n".join(problems)),
                    inline=False,
                )
            await self._reply_temp(message, embed, seconds=120)
            return

        embed = base_embed(
            "✅ 등록이 모두 끝났습니다",
            Colors.SUCCESS,
            description=f"{member.mention} 님, 환영합니다!",
        )
        embed.add_field(name="닉네임", value=f"`{nickname}`", inline=False)
        tier_text = parsed.tier_name + (f" {parsed.lp}LP" if parsed.lp is not None else "")
        embed.add_field(name="티어", value=f"{tier_text} (`{parsed.tier}`)", inline=True)
        embed.add_field(name="주 라인", value=lane_label(parsed.main_lane), inline=True)
        embed.add_field(name="부 라인", value=lane_label(parsed.sub_lane), inline=True)

        account_lines = [f"`{result.riot_id}` 등록 완료"]
        if result.verified:
            account_lines.append("라이엇 API 확인 완료")
        else:
            account_lines.append("⚠️ 라이엇 API 키가 없어 계정 확인은 생략했습니다")
        if result.rank_label:
            account_lines.append(f"솔로랭크 · {result.rank_label}")
        if result.role_removed:
            account_lines.append("미등록 역할이 회수되었습니다")
        embed.add_field(name="롤 계정", value="\n".join(account_lines), inline=False)

        if problems:
            embed.add_field(name="처리하지 못한 항목", value=truncate("\n".join(problems)), inline=False)

        await self._reply_temp(message, embed, seconds=90)

    async def _reply_temp(
        self, message: discord.Message, embed: discord.Embed, *, seconds: int
    ) -> None:
        """안내 메시지를 보내고 일정 시간 뒤 지운다 (채널을 깔끔하게 유지).

        삭제는 백그라운드로 미룬다. 여기서 기다려 버리면 처리 중 표시가 그만큼
        오래 걸려 있어서, 실패 안내를 보고 곧바로 다시 적은 소개가 무시된다.
        """
        try:
            reply = await message.reply(embed=embed, mention_author=False)
        except discord.HTTPException:
            return

        async def delete_later() -> None:
            await asyncio.sleep(seconds)
            try:
                await reply.delete()
            except discord.HTTPException:
                pass

        self.bot.loop.create_task(delete_later())

    # --------------------------------------------------- 입장/닉네임 변경 대응

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        await ensure_default_roles(member, reason="신규 입장")
        user = await self.bot.db.get_user(member.id)
        await sync_unregistered_role(member, user.registered, reason="신규 입장")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """닉네임이 바뀌면 미등록 역할 상태를 다시 계산한다."""
        if after.bot or before.display_name == after.display_name:
            return
        user = await self.bot.db.get_user(after.id)
        await sync_unregistered_role(after, user.registered, reason="닉네임 변경 감지")

    # -------------------------------------------------------------- 명령어

    @app_commands.command(
        name="역할동기화",
        description="[관리자] 봇을 제외한 전원에게 기본 역할을 채우고 미등록 역할을 정리합니다.",
    )
    @staff_only()
    async def sync_unregistered(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        # 조용히 아무 일도 안 일어나는 상황을 막기 위해 원인을 먼저 확인한다
        problem = role_problem(guild, Roles.UNREGISTERED)
        if problem is not None:
            await interaction.response.send_message(
                f"⛔ 미등록 역할을 지급할 수 없습니다.\n\n{problem}", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        role = guild.get_role(Roles.UNREGISTERED)

        given, taken, skipped, failed, defaults = await self._bulk_sync(
            guild, reason=f"역할 일괄 정리 ({interaction.user})"
        )

        embed = base_embed(
            "🔁 역할 동기화 완료",
            Colors.DANGER if failed else Colors.SUCCESS,
            description=(
                f"{role.mention} 역할을 기준에 맞춰 정리하고, "
                "기본 역할이 빠진 사람에게 채워 넣었습니다."
            ),
        )
        embed.add_field(name="미등록 지급", value=f"{given}명", inline=True)
        embed.add_field(name="미등록 회수", value=f"{taken}명", inline=True)
        embed.add_field(name="기본 역할 보충", value=f"{defaults}명", inline=True)
        embed.add_field(name="제외(봇)", value=f"{skipped}명", inline=True)
        embed.add_field(
            name="확인한 인원", value=f"{len(guild.members)}명", inline=True
        )
        if failed:
            embed.add_field(
                name="⚠️ 실패",
                value=(
                    f"{failed}명은 역할을 바꾸지 못했습니다. "
                    "대상의 역할이 봇보다 높은 경우일 수 있습니다. (로그 확인)"
                ),
                inline=False,
            )
        embed.add_field(
            name="기준",
            value="닉네임이 양식에 맞고 `/등록` 까지 마친 사람만 역할이 없습니다.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="양식안내", description="닉네임 등록 양식을 안내합니다.")
    async def guide(self, interaction: discord.Interaction) -> None:
        embed = base_embed(
            "📝 닉네임 등록 양식",
            Colors.GOLD,
            description=(
                f"<#{Channels.ONBOARDING}> 채널에 아래 양식으로 채팅을 쳐 주세요.\n\n{GUIDE}"
            ),
        )
        embed.add_field(
            name="이것만 치면 끝",
            value=(
                "닉네임 변경 · 티어/라인 역할 · **롤 계정 등록**까지 한 번에 처리됩니다.\n"
                "따로 `/등록` 을 칠 필요가 없습니다."
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Onboarding(bot))
