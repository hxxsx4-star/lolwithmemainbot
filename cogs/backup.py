"""매일 자정(KST) 동적 데이터 백업.

SQLite 스냅샷과 사람이 읽을 수 있는 JSON 내보내기를 zip 으로 묶어
백업 채널에 올린다. `/백업` 으로 수동 실행도 가능하다.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
import zipfile
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import Channels, Colors, DATA_DIR, TIMEZONE
from core.checks import staff_only
from utils.logs import base_embed, send_log

log = logging.getLogger("mainbot.backup")

MIDNIGHT = dt.time(hour=0, minute=0, tzinfo=TIMEZONE)

# JSON 으로도 내보낼 테이블
EXPORT_TABLES = (
    "users",
    "point_log",
    "warnings",
    "scrims",
    "scrim_members",
    "tickets",
    "counters",
)

# 디스코드 기본 업로드 한도보다 살짝 낮게 잡는다
MAX_UPLOAD_BYTES = 9 * 1024 * 1024


class Backup(commands.Cog, name="Backup"):
    """데이터 백업."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.daily_backup.start()

    async def cog_unload(self) -> None:
        self.daily_backup.cancel()

    # ------------------------------------------------------------ 백업 생성

    async def _snapshot(self) -> Path:
        """일관된 SQLite 스냅샷 파일을 만든다."""
        target = DATA_DIR / "_snapshot.sqlite3"
        if target.exists():
            target.unlink()
        # VACUUM INTO 는 쓰기 중에도 안전한 사본을 만들어 준다
        await self.bot.db.conn.execute("VACUUM INTO ?", (str(target),))
        await self.bot.db.conn.commit()
        return target

    async def _export_json(self) -> dict[str, list[dict]]:
        dump: dict[str, list[dict]] = {}
        for table in EXPORT_TABLES:
            try:
                async with self.bot.db.conn.execute(f"SELECT * FROM {table}") as cur:
                    rows = await cur.fetchall()
                dump[table] = [dict(row) for row in rows]
            except Exception as exc:  # 테이블이 없어도 백업 자체는 계속한다
                log.warning("%s 테이블 내보내기 실패: %s", table, exc)
                dump[table] = []
        return dump

    async def build_archive(self) -> tuple[io.BytesIO, str, dict[str, int]]:
        """zip 버퍼, 파일명, 테이블별 행 수를 돌려준다."""
        stamp = dt.datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M")
        snapshot = await self._snapshot()
        dump = await self._export_json()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(snapshot, arcname="lolwithme.sqlite3")
            zf.writestr(
                "export.json",
                json.dumps(dump, ensure_ascii=False, indent=2, default=str),
            )
            zf.writestr(
                "README.txt",
                "롤 같이 하자 메인봇 데이터 백업\n"
                f"생성 시각: {dt.datetime.now(TIMEZONE).isoformat(timespec='seconds')}\n\n"
                "lolwithme.sqlite3 — 그대로 data/ 에 넣으면 복구됩니다.\n"
                "export.json      — 사람이 읽을 수 있는 전체 내보내기\n",
            )

        try:
            snapshot.unlink()
        except OSError:
            pass

        buffer.seek(0)
        counts = {table: len(rows) for table, rows in dump.items()}
        return buffer, f"backup_{stamp}.zip", counts

    async def run_backup(self, *, triggered_by: str) -> discord.Embed:
        buffer, filename, counts = await self.build_archive()
        size = buffer.getbuffer().nbytes

        embed = base_embed(
            "💾 데이터 백업",
            Colors.TEAL,
            description=f"동적 데이터를 압축해 저장했습니다. ({triggered_by})",
        )
        embed.add_field(name="파일", value=f"`{filename}`", inline=True)
        embed.add_field(name="크기", value=f"{size / 1024:,.1f} KB", inline=True)
        embed.add_field(
            name="내용",
            value="\n".join(f"`{table}` — {n:,}행" for table, n in counts.items()),
            inline=False,
        )

        if size > MAX_UPLOAD_BYTES:
            embed.color = Colors.DANGER
            embed.add_field(
                name="⚠️ 업로드 실패",
                value=(
                    f"백업 파일이 업로드 한도({MAX_UPLOAD_BYTES // 1024 // 1024}MB)를 "
                    "넘었습니다. 서버에서 직접 내려받아 주세요."
                ),
                inline=False,
            )
            await send_log(self.bot, Channels.BACKUP, embed)
            return embed

        await send_log(
            self.bot,
            Channels.BACKUP,
            embed,
            files=[discord.File(buffer, filename=filename)],
        )
        return embed

    # ------------------------------------------------------------ 자동 실행

    @tasks.loop(time=MIDNIGHT)
    async def daily_backup(self) -> None:
        try:
            await self.run_backup(triggered_by="매일 자정 자동 백업")
        except Exception:
            log.exception("자동 백업 실패")

    @daily_backup.before_loop
    async def before_daily_backup(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------ 수동 실행

    @app_commands.command(name="백업", description="[관리자] 지금 바로 데이터 백업을 실행합니다.")
    @app_commands.default_permissions(manage_guild=True)
    @staff_only()
    async def manual_backup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            embed = await self.run_backup(triggered_by=f"{interaction.user} 수동 실행")
        except Exception as exc:
            log.exception("수동 백업 실패")
            await interaction.followup.send(f"백업 실패: `{exc}`", ephemeral=True)
            return
        await interaction.followup.send(
            f"✅ 백업을 <#{Channels.BACKUP}> 채널에 올렸습니다.", embed=embed, ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Backup(bot))
