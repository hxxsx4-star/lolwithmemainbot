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
import datetime as dt
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import (
    Channels,
    Colors,
    GUILD_ID,
    Roles,
    TIMEZONE,
    VERIFY_REMINDER_HOURS,
    VERIFY_REMINDER_REPLACE,
    VERIFY_REMINDER_TEXT,
)
from core.checks import is_staff, staff_only
from core.registration import register_riot_account
from utils.logs import base_embed, truncate
from utils.parsing import FormatError, parse_profile_format
from utils import roles
from utils.roles import (
    apply_tier_and_lanes,
    ensure_default_roles,
    lane_label,
    role_problem,
    sync_registration_roles,
)

log = logging.getLogger("mainbot.onboarding")

REMINDER_TIMES = [dt.time(hour=h, tzinfo=TIMEZONE) for h in VERIFY_REMINDER_HOURS]

# 직전에 올린 안내 메시지를 기억해 두는 키 (panels 테이블 재사용)
REMINDER_PANEL_KEY = "verify_reminder"
NICKNAME_GUIDE_KEY = "nickname_guide"

GUIDE = (
    "**양식**  `롤닉네임#태그/올해최고티어/주라인 부라인`\n"
    "**예시**  `홍길동#KR1/M405/MID AD`\n\n"
    "**티어** 언랭 `U` · 아이언 `I` · 브론즈 `B` · 실버 `S` · 골드 `G` · "
    "플래티넘 `P` · 에메랄드 `E` · 다이아 `D` · 마스터 `M` · 그마 `GM` · 챌린저 `C`\n"
    "**라인** 탑 `TOP` · 정글 `JG` · 미드 `MID` · 원딜 `AD` · 서폿 `SUP`\n"
    "**숫자** 아이언~다이아는 **단계 1~4** (`E4` = 에메랄드 4), "
    "마스터 이상은 **LP** (`M405` = 마스터 405LP)"
)


def nickname_guide_embed() -> discord.Embed:
    """닉네임 변경 채널에 붙일 안내.

    내용을 문자열로 박아 두지 않고 `GUIDE` 를 비롯한 설정에서 만들어 낸다.
    티어 약자나 라인 표기를 바꾸면 여기 문구도 저절로 따라온다.
    """
    embed = base_embed(
        "⚒️ 닉네임 · 티어 변경",
        Colors.TEAL,
        description=(
            "이 채널에 **양식 그대로 한 줄**만 적으면 서버 닉네임과 "
            "티어 · 라인 역할이 자동으로 바뀝니다."
        ),
    )
    embed.add_field(name="양식", value=GUIDE, inline=False)
    embed.add_field(
        name="이럴 때 쓰세요",
        value=(
            "· 시즌이 지나 **티어가 올랐을 때**\n"
            "· 주 라인 · 부 라인이 바뀌었을 때\n"
            "· **롤 닉네임을 바꿨을 때** (새 계정으로 다시 연결됩니다)"
        ),
        inline=False,
    )
    embed.add_field(
        name="알아두기",
        value=(
            "· 양식이 틀리면 봇이 **어디가 틀렸는지 짚어 줍니다.** 고쳐서 다시 적으면 됩니다\n"
            "· 예전 티어 · 라인 역할은 자동으로 회수되니 직접 뗄 필요 없습니다\n"
            "· 남의 계정은 등록할 수 없습니다"
        ),
        inline=False,
    )
    embed.set_footer(text="롤 같이 하자 · 이 안내는 봇이 최신 설정으로 유지합니다")
    return embed


class Onboarding(commands.Cog, name="Onboarding"):
    """닉네임 양식 처리와 미등록 역할 관리."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._startup_done = False
        # 같은 사람이 연달아 올린 소개가 겹쳐 처리되는 것을 막는다
        self._processing: set[int] = set()

    async def cog_load(self) -> None:
        self.verify_reminder.start()
        self.refresh_guide.start()

    async def cog_unload(self) -> None:
        self.verify_reminder.cancel()
        self.refresh_guide.cancel()

    # -------------------------------------------------- 닉네임 변경 안내 유지

    @tasks.loop(count=1)
    async def refresh_guide(self) -> None:
        """봇이 뜰 때마다 안내를 지금 설정으로 다시 그린다.

        새로 올리지 않고 **기존 메시지를 고친다.** 매번 새로 올리면 채널이
        같은 안내로 도배되고, 사람들이 위쪽 낡은 안내를 보게 된다.
        """
        for guild in self.bot.guilds:
            if GUILD_ID is not None and guild.id != GUILD_ID:
                continue
            try:
                await self.post_nickname_guide(guild)
            except Exception:
                log.exception("[%s] 닉네임 안내 갱신 실패", guild.name)

    @refresh_guide.before_loop
    async def before_refresh_guide(self) -> None:
        await self.bot.wait_until_ready()

    async def post_nickname_guide(
        self, guild: discord.Guild
    ) -> discord.Message | None:
        """안내를 올리거나, 이미 있으면 최신 내용으로 고친다."""
        channel = guild.get_channel(Channels.NICKNAME_UPDATE)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning(
                "닉네임 변경 채널(%s)을 찾을 수 없습니다.", Channels.NICKNAME_UPDATE
            )
            return None

        embed = nickname_guide_embed()
        panel = await self.bot.db.get_panel(NICKNAME_GUIDE_KEY)

        if panel and int(panel["channel_id"]) == channel.id:
            try:
                message = await channel.fetch_message(int(panel["message_id"]))
                await message.edit(embed=embed)
                return message
            except discord.NotFound:
                pass                      # 지워졌으면 새로 올린다
            except discord.HTTPException as exc:
                log.warning("닉네임 안내 수정 실패: %s", exc)
                return None

        try:
            message = await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.warning("닉네임 안내 게시 실패: %s", exc)
            return None

        await self.bot.db.set_panel(
            NICKNAME_GUIDE_KEY, guild.id, channel.id, message.id
        )
        return message

    # ------------------------------------------------------- 인증 안내 재공지

    @tasks.loop(time=REMINDER_TIMES)
    async def verify_reminder(self) -> None:
        for guild in self.bot.guilds:
            if GUILD_ID is not None and guild.id != GUILD_ID:
                continue
            try:
                await self.post_verify_reminder(guild)
            except Exception:
                log.exception("[%s] 인증 안내 전송 실패", guild.name)

    @verify_reminder.before_loop
    async def before_verify_reminder(self) -> None:
        await self.bot.wait_until_ready()

    async def post_verify_reminder(
        self, guild: discord.Guild
    ) -> discord.Message | None:
        """소개 채널에 미등록 역할을 멘션하며 인증 안내를 올린다."""
        channel = guild.get_channel(Channels.ONBOARDING)
        if not isinstance(channel, discord.abc.Messageable):
            log.warning("소개 채널(%s)을 찾을 수 없습니다.", Channels.ONBOARDING)
            return None

        role = guild.get_role(Roles.UNREGISTERED)
        if role is None:
            log.warning("미등록 역할(%s)을 찾을 수 없습니다.", Roles.UNREGISTERED)
            return None

        # 역할 멘션이 실제로 울리려면 역할이 '멘션 허용'이거나 봇에게 권한이 있어야 한다
        me = guild.me
        if not role.mentionable and not (
            me is not None and me.guild_permissions.mention_everyone
        ):
            log.warning(
                "미등록 역할이 '멘션 허용'이 아니고 봇에게 everyone 멘션 권한도 없어"
                " 알림이 울리지 않습니다. 역할 설정을 확인해 주세요."
            )

        embed = base_embed(
            "📝 서버 인증 안내",
            Colors.GOLD,
            description=VERIFY_REMINDER_TEXT,
        )
        embed.add_field(name="양식", value=GUIDE, inline=False)

        try:
            message = await channel.send(
                content=role.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(roles=[role]),
            )
        except discord.Forbidden:
            log.warning("소개 채널에 메시지를 보낼 권한이 없습니다.")
            return None
        except discord.HTTPException as exc:
            log.warning("인증 안내 전송 실패: %s", exc)
            return None

        # 직전 안내는 지워서 채널이 안내로 도배되지 않게 한다
        if VERIFY_REMINDER_REPLACE:
            previous = await self.bot.db.get_panel(REMINDER_PANEL_KEY)
            if previous is not None:
                await self._delete_message(
                    int(previous["channel_id"]), int(previous["message_id"])
                )
        await self.bot.db.set_panel(
            REMINDER_PANEL_KEY, guild.id, channel.id, message.id
        )
        return message

    async def _delete_message(self, channel_id: int, message_id: int) -> None:
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(message_id)
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

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

            # 미등록 역할과 서버원 역할은 짝으로 움직이므로 둘 다 확인한다
            blockers = [
                (name, problem)
                for name, role_id in (
                    ("미등록 역할", Roles.UNREGISTERED),
                    ("서버원 역할", Roles.MEMBER),
                )
                if (problem := role_problem(guild, role_id)) is not None
            ]
            if blockers:
                for name, problem in blockers:
                    log.warning(
                        "[%s] %s을(를) 자동 지급하지 못했습니다: %s",
                        guild.name,
                        name,
                        discord.utils.remove_markdown(problem),
                    )
                continue

            unreg, memb, skipped, failed, defaults = await self._bulk_sync(
                guild, reason="봇 시작 시 역할 자동 동기화"
            )
            log.info(
                "[%s] 역할 자동 동기화 완료 — 미등록 %d명 / 서버원 %d명 /"
                " 기본 역할 보충 %d명 / 봇 제외 %d명 / 실패 %d명",
                guild.name,
                unreg,
                memb,
                defaults,
                skipped,
                failed,
            )

    async def _bulk_sync(
        self, guild: discord.Guild, *, reason: str
    ) -> tuple[int, int, int, int, int]:
        """서버 전원의 역할을 맞춘다.

        기본 역할은 빠진 사람에게 채워 주고, 등록 여부에 따라 미등록 역할과
        서버원 역할을 맞바꾼다.
        (미등록 처리, 서버원 처리, 봇제외, 실패, 기본역할 보충)
        """
        # 멤버 캐시가 비어 있으면 먼저 받아 온다
        if not guild.chunked:
            try:
                await guild.chunk()
            except (discord.ClientException, discord.HTTPException) as exc:
                log.warning("[%s] 멤버 목록을 불러오지 못했습니다: %s", guild.name, exc)

        unreg = memb = skipped = failed = defaults = 0

        for index, member in enumerate(guild.members):
            if member.bot:
                skipped += 1
                continue

            if await ensure_default_roles(member, reason=reason):
                defaults += 1

            user = await self.bot.db.get_user(member.id)
            result = await sync_registration_roles(member, user.registered, reason=reason)
            if result == roles.SET_UNREGISTERED:
                unreg += 1
            elif result == roles.SET_REGISTERED:
                memb += 1
            elif result == roles.FAILED:
                failed += 1

            # 디스코드 속도 제한을 피하려고 잠깐씩 쉰다
            if index % 20 == 19:
                await asyncio.sleep(1)

        return unreg, memb, skipped, failed, defaults

    # -------------------------------------------------------- 양식 채팅 처리

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        # 자기소개는 미등록만, 닉네임변경은 등록을 마친 사람만 볼 수 있다.
        # 양식 처리는 똑같으므로 두 채널을 함께 받는다.
        if message.channel.id not in (Channels.ONBOARDING, Channels.NICKNAME_UPDATE):
            return
        if not isinstance(message.author, discord.Member):
            return

        # 관리진은 이 채널에 안내나 공지를 올린다. 그걸 양식으로 읽고 오류를
        # 붙이면 안내글마다 답장이 따라붙어 채널이 지저분해진다.
        #
        #   · 이미 등록을 마친 관리진 — 여기 쓰는 건 전부 안내글이므로 통째로 무시
        #   · 아직 등록 안 한 관리진 — 양식이 맞으면 등록해 주되, 양식이 아니면
        #     그냥 안내글로 보고 조용히 넘어간다
        quiet = False
        if is_staff(message.author):
            author = await self.bot.db.get_user(message.author.id)
            if author.registered:
                return
            quiet = True

        if message.author.id in self._processing:
            return

        try:
            parsed = parse_profile_format(message.content)
        except FormatError as exc:
            if quiet:
                return
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
        nickname = parsed.canonical[:32]
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
            source=f"<#{message.channel.id}> 자동 등록",
        )
        if not result.ok:
            # 등록만 실패해도 닉네임·역할은 이미 반영됐으니 그 사실을 함께 알린다
            await sync_registration_roles(member, False, reason="닉네임 양식 등록")
            embed = base_embed(
                "⚠️ 닉네임과 역할만 반영되었습니다",
                Colors.DANGER,
                description=(
                    f"{member.mention} 님, 롤 계정 등록에 실패해서 "
                    "서버원 역할을 아직 드리지 못했습니다."
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
        embed.add_field(
            name="티어", value=f"{parsed.tier_display} (`{parsed.tier}`)", inline=True
        )
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
        await sync_registration_roles(member, user.registered, reason="신규 입장")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        """닉네임이 바뀌면 미등록 역할 상태를 다시 계산한다."""
        if after.bot or before.display_name == after.display_name:
            return
        user = await self.bot.db.get_user(after.id)
        await sync_registration_roles(after, user.registered, reason="닉네임 변경 감지")

    # -------------------------------------------------------------- 명령어

    @app_commands.command(
        name="역할동기화",
        description="[관리자] 봇을 제외한 전원에게 기본 역할을 채우고 미등록 역할을 정리합니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def sync_unregistered(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return

        # 조용히 아무 일도 안 일어나는 상황을 막기 위해 원인을 먼저 확인한다
        blockers = [
            f"· **{name}** — {problem}"
            for name, role_id in (
                ("미등록 역할", Roles.UNREGISTERED),
                ("서버원 역할", Roles.MEMBER),
            )
            if (problem := role_problem(guild, role_id)) is not None
        ]
        if blockers:
            await interaction.response.send_message(
                "⛔ 역할을 지급할 수 없습니다.\n\n" + "\n".join(blockers), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        role = guild.get_role(Roles.UNREGISTERED)

        unreg, memb, skipped, failed, defaults = await self._bulk_sync(
            guild, reason=f"역할 일괄 정리 ({interaction.user})"
        )

        embed = base_embed(
            "🔁 역할 동기화 완료",
            Colors.DANGER if failed else Colors.SUCCESS,
            description=(
                f"등록 여부에 따라 {role.mention} 과 <@&{Roles.MEMBER}> 를 맞바꾸고, "
                "기본 역할이 빠진 사람에게 채워 넣었습니다."
            ),
        )
        embed.add_field(name="미등록으로 전환", value=f"{unreg}명", inline=True)
        embed.add_field(name="서버원으로 전환", value=f"{memb}명", inline=True)
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

    @app_commands.command(
        name="인증안내",
        description="[관리자] 소개 채널에 인증 안내를 지금 바로 올립니다.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def send_reminder(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        message = await self.post_verify_reminder(guild)
        if message is None:
            await interaction.followup.send(
                "인증 안내를 올리지 못했습니다. 봇 로그를 확인해 주세요.", ephemeral=True
            )
            return
        await interaction.followup.send(
            f"✅ 인증 안내를 올렸습니다 → [바로가기]({message.jump_url})", ephemeral=True
        )

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
