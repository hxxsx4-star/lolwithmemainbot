"""롤 같이 하자 · 메인봇 진입점."""
from __future__ import annotations

import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands

from config import GUILD_ID, TOKEN
from core.checks import MissingStaff
from core.db import Database
from utils.riot import RiotClient

EXTENSIONS = (
    "cogs.economy",
    "cogs.leveling",
    "cogs.warning",
    "cogs.riot_register",
    "cogs.ticket",
    "cogs.profile",
    "cogs.scrim",
    "cogs.onboarding",
    "cogs.backup",
    "cogs.helpcmd",
)

log = logging.getLogger("mainbot")


def build_intents() -> discord.Intents:
    intents = discord.Intents.default()
    intents.members = True          # 역할 지급 / 입장 처리
    intents.message_content = True  # 닉네임 양식 채팅 인식
    intents.guilds = True
    intents.voice_states = True     # 음성 포인트
    return intents


class MainBot(commands.Bot):
    """경제 · 경고 · 티켓 · 프로필 · 내전을 담당하는 봇."""

    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=build_intents(),
            help_command=None,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True
            ),
        )
        self.db = Database()
        self.riot = RiotClient()

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("데이터베이스 연결 완료: %s", self.db.path)

        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("확장 로드 완료: %s", ext)
            except Exception:
                log.exception("확장 로드 실패: %s", ext)

        self.tree.on_error = self.on_app_command_error

        if GUILD_ID is not None:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("슬래시 명령어 %d개를 서버(%s)에 동기화했습니다.", len(synced), GUILD_ID)
        else:
            synced = await self.tree.sync()
            log.info("슬래시 명령어 %d개를 전역 동기화했습니다. (반영까지 최대 1시간)", len(synced))

    async def close(self) -> None:
        await self.riot.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("로그인: %s (%s)", self.user, self.user.id if self.user else "?")
        if not self.riot.enabled:
            log.warning(
                "RIOT_API_KEY 가 비어 있습니다. /등록 이 실제 계정을 확인하지 않고 진행됩니다."
            )
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.playing, name="롤 같이 하자 · /도움말"
            )
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """명령어 오류를 사용자에게 친절하게 알린다."""
        if isinstance(error, MissingStaff):
            message = f"⛔ {error.message}"
        elif isinstance(error, app_commands.CheckFailure):
            message = "⛔ 이 명령어를 사용할 권한이 없습니다."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"⏳ 잠시 후 다시 시도해 주세요. ({error.retry_after:.0f}초)"
        else:
            log.exception("명령어 처리 중 오류", exc_info=error)
            message = (
                "❗ 명령어를 처리하는 중 오류가 발생했습니다. 관리자에게 알려 주세요."
            )

        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not TOKEN:
        print("DISCORD_TOKEN 이 비어 있습니다. .env 파일을 확인해 주세요.", file=sys.stderr)
        raise SystemExit(1)

    bot = MainBot()
    bot.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
