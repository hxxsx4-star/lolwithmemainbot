"""SQLite 저장소.

포인트 · 경고 · 롤 계정 등록 · 내전 · 티켓 정보를 한 파일에 담는다.
매일 자정 백업은 이 파일과 data/ 디렉터리를 통째로 압축한다.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import aiosqlite

from config import DB_PATH, TIMEZONE

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    points         INTEGER NOT NULL DEFAULT 0,
    last_attendance TEXT,
    streak         INTEGER NOT NULL DEFAULT 0,
    total_attendance INTEGER NOT NULL DEFAULT 0,
    voice_seconds  INTEGER NOT NULL DEFAULT 0,
    riot_game_name TEXT,
    riot_tag_line  TEXT,
    riot_puuid     TEXT,
    registered_at  TEXT,
    registered_by  INTEGER
);

CREATE TABLE IF NOT EXISTS point_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    delta      INTEGER NOT NULL,
    balance    INTEGER NOT NULL,
    reason     TEXT,
    actor_id   INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_point_log_user ON point_log(user_id);

CREATE TABLE IF NOT EXISTS warnings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    actor_id   INTEGER NOT NULL,
    amount     INTEGER NOT NULL,          -- 지급은 양수, 차감은 음수
    reason     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_warnings_user ON warnings(user_id);

CREATE TABLE IF NOT EXISTS scrims (
    thread_id  INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    host_id    INTEGER NOT NULL,
    title      TEXT NOT NULL,
    rule       TEXT NOT NULL,
    series     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    message_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scrim_members (
    thread_id INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    joined_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, user_id)
);

CREATE TABLE IF NOT EXISTS tickets (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    category   TEXT NOT NULL,
    number     INTEGER NOT NULL,
    status     TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    closed_at  TEXT,
    closed_by  INTEGER
);

CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
"""


def now() -> dt.datetime:
    """KST 기준 현재 시각."""
    return dt.datetime.now(TIMEZONE)


def today() -> dt.date:
    return now().date()


def iso() -> str:
    return now().isoformat(timespec="seconds")


@dataclass(slots=True)
class UserRow:
    """users 테이블 한 줄."""

    user_id: int
    points: int = 0
    last_attendance: Optional[str] = None
    streak: int = 0
    total_attendance: int = 0
    voice_seconds: int = 0
    riot_game_name: Optional[str] = None
    riot_tag_line: Optional[str] = None
    riot_puuid: Optional[str] = None
    registered_at: Optional[str] = None
    registered_by: Optional[int] = None

    @property
    def riot_id(self) -> Optional[str]:
        if self.riot_game_name and self.riot_tag_line:
            return f"{self.riot_game_name}#{self.riot_tag_line}"
        return None

    @property
    def registered(self) -> bool:
        return self.riot_id is not None


class Database:
    """얇은 aiosqlite 래퍼."""

    def __init__(self, path=DB_PATH) -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("데이터베이스가 아직 연결되지 않았습니다.")
        return self._conn

    async def _exec(self, sql: str, params: Iterable[Any] = ()) -> None:
        await self.conn.execute(sql, tuple(params))
        await self.conn.commit()

    async def _fetchone(self, sql: str, params: Iterable[Any] = ()):
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: Iterable[Any] = ()):
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchall()

    # ------------------------------------------------------------- 유저

    async def get_user(self, user_id: int) -> UserRow:
        row = await self._fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
        if row is None:
            await self._exec("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))
            return UserRow(user_id=user_id)
        return UserRow(**dict(row))

    async def ensure_user(self, user_id: int) -> None:
        await self._exec("INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,))

    # ----------------------------------------------------------- 포인트

    async def add_points(
        self,
        user_id: int,
        delta: int,
        reason: str,
        *,
        actor_id: Optional[int] = None,
    ) -> int:
        """포인트를 더하고(음수면 차감) 변경 후 잔액을 돌려준다.

        잔액은 0 아래로 내려가지 않는다. 이때 기록에 남기는 변동량은 요청값이
        아니라 **실제로 적용된 값**이다.
        """
        await self.ensure_user(user_id)
        row = await self._fetchone("SELECT points FROM users WHERE user_id = ?", (user_id,))
        before = int(row["points"]) if row else 0
        balance = max(0, before + delta)
        applied = balance - before

        await self.conn.execute(
            "UPDATE users SET points = ? WHERE user_id = ?", (balance, user_id)
        )
        await self.conn.execute(
            "INSERT INTO point_log(user_id, delta, balance, reason, actor_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, applied, balance, reason, actor_id, iso()),
        )
        await self.conn.commit()
        return balance

    async def get_points(self, user_id: int) -> int:
        row = await self._fetchone("SELECT points FROM users WHERE user_id = ?", (user_id,))
        return int(row["points"]) if row else 0

    async def point_rank(self, user_id: int) -> Optional[int]:
        """포인트 순위 (1등부터). 기록이 없으면 None."""
        row = await self._fetchone(
            "SELECT COUNT(*) + 1 AS rank FROM users"
            " WHERE points > (SELECT points FROM users WHERE user_id = ?)",
            (user_id,),
        )
        exists = await self._fetchone("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        return int(row["rank"]) if row and exists else None

    async def top_points(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT user_id, points FROM users WHERE points > 0"
            " ORDER BY points DESC, user_id ASC LIMIT ?",
            (limit,),
        )

    # ------------------------------------------------------------- 출석

    async def try_attendance(self, user_id: int) -> tuple[bool, int, int]:
        """출석을 시도한다. (성공 여부, 연속 출석일, 총 출석일)"""
        user = await self.get_user(user_id)
        t = today()
        if user.last_attendance == t.isoformat():
            return False, user.streak, user.total_attendance

        yesterday = (t - dt.timedelta(days=1)).isoformat()
        streak = user.streak + 1 if user.last_attendance == yesterday else 1
        total = user.total_attendance + 1
        await self._exec(
            "UPDATE users SET last_attendance = ?, streak = ?, total_attendance = ?"
            " WHERE user_id = ?",
            (t.isoformat(), streak, total, user_id),
        )
        return True, streak, total

    async def add_voice_seconds(self, user_id: int, seconds: int) -> None:
        await self.ensure_user(user_id)
        await self._exec(
            "UPDATE users SET voice_seconds = voice_seconds + ? WHERE user_id = ?",
            (seconds, user_id),
        )

    # ------------------------------------------------------------- 경고

    async def add_warning(
        self, user_id: int, actor_id: int, amount: int, reason: str
    ) -> int:
        """경고를 기록하고 현재 누적 경고 수를 돌려준다."""
        await self.ensure_user(user_id)
        await self._exec(
            "INSERT INTO warnings(user_id, actor_id, amount, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, actor_id, amount, reason, iso()),
        )
        return await self.warning_count(user_id)

    async def warning_count(self, user_id: int) -> int:
        row = await self._fetchone(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM warnings WHERE user_id = ?",
            (user_id,),
        )
        return max(0, int(row["total"])) if row else 0

    async def warning_history(self, user_id: int, limit: int = 20) -> list[aiosqlite.Row]:
        return await self._fetchall(
            "SELECT * FROM warnings WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )

    async def warning_reasons(self, user_id: int) -> list[str]:
        """지급된 경고의 사유만 시간순으로."""
        rows = await self._fetchall(
            "SELECT reason, created_at FROM warnings WHERE user_id = ? AND amount > 0"
            " ORDER BY id ASC",
            (user_id,),
        )
        return [f"{r['created_at'][:10]} · {r['reason']}" for r in rows]

    # -------------------------------------------------------- 롤 계정 등록

    async def set_riot_account(
        self,
        user_id: int,
        game_name: str,
        tag_line: str,
        puuid: Optional[str],
        actor_id: int,
    ) -> None:
        await self.ensure_user(user_id)
        await self._exec(
            "UPDATE users SET riot_game_name = ?, riot_tag_line = ?, riot_puuid = ?,"
            " registered_at = ?, registered_by = ? WHERE user_id = ?",
            (game_name, tag_line, puuid, iso(), actor_id, user_id),
        )

    async def clear_riot_account(self, user_id: int) -> None:
        await self._exec(
            "UPDATE users SET riot_game_name = NULL, riot_tag_line = NULL,"
            " riot_puuid = NULL, registered_at = NULL, registered_by = NULL"
            " WHERE user_id = ?",
            (user_id,),
        )

    async def riot_owner(self, game_name: str, tag_line: str) -> Optional[int]:
        """이미 같은 롤 계정을 등록한 유저가 있는지."""
        row = await self._fetchone(
            "SELECT user_id FROM users WHERE lower(riot_game_name) = lower(?)"
            " AND lower(riot_tag_line) = lower(?)",
            (game_name, tag_line),
        )
        return int(row["user_id"]) if row else None

    # ------------------------------------------------------------- 내전

    async def create_scrim(
        self,
        thread_id: int,
        guild_id: int,
        host_id: int,
        title: str,
        rule: str,
        series: str,
        message_id: Optional[int],
    ) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO scrims"
            "(thread_id, guild_id, host_id, title, rule, series, status, message_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
            (thread_id, guild_id, host_id, title, rule, series, message_id, iso()),
        )

    async def get_scrim(self, thread_id: int) -> Optional[aiosqlite.Row]:
        return await self._fetchone("SELECT * FROM scrims WHERE thread_id = ?", (thread_id,))

    async def set_scrim_status(self, thread_id: int, status: str) -> None:
        await self._exec(
            "UPDATE scrims SET status = ? WHERE thread_id = ?", (status, thread_id)
        )

    async def join_scrim(self, thread_id: int, user_id: int) -> bool:
        """참가 등록. 이미 참가 중이면 False."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO scrim_members(thread_id, user_id, joined_at)"
            " VALUES (?, ?, ?)",
            (thread_id, user_id, iso()),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def leave_scrim(self, thread_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM scrim_members WHERE thread_id = ? AND user_id = ?",
            (thread_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def scrim_members(self, thread_id: int) -> list[int]:
        rows = await self._fetchall(
            "SELECT user_id FROM scrim_members WHERE thread_id = ? ORDER BY joined_at ASC",
            (thread_id,),
        )
        return [int(r["user_id"]) for r in rows]

    async def open_scrim_threads(self) -> list[int]:
        rows = await self._fetchall(
            "SELECT thread_id FROM scrims WHERE status = 'open'"
        )
        return [int(r["thread_id"]) for r in rows]

    # ------------------------------------------------------------- 티켓

    async def next_counter(self, name: str) -> int:
        """1부터 시작하는 연번을 하나 발급한다."""
        await self.conn.execute(
            "INSERT INTO counters(name, value) VALUES (?, 1)"
            " ON CONFLICT(name) DO UPDATE SET value = value + 1",
            (name,),
        )
        row = await self._fetchone("SELECT value FROM counters WHERE name = ?", (name,))
        await self.conn.commit()
        return int(row["value"]) if row else 0

    async def create_ticket(
        self, channel_id: int, guild_id: int, user_id: int, category: str, number: int
    ) -> None:
        await self._exec(
            "INSERT OR REPLACE INTO tickets"
            "(channel_id, guild_id, user_id, category, number, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (channel_id, guild_id, user_id, category, number, iso()),
        )

    async def get_ticket(self, channel_id: int) -> Optional[aiosqlite.Row]:
        return await self._fetchone(
            "SELECT * FROM tickets WHERE channel_id = ?", (channel_id,)
        )

    async def open_ticket_of(self, guild_id: int, user_id: int, category: str):
        return await self._fetchone(
            "SELECT * FROM tickets WHERE guild_id = ? AND user_id = ? AND category = ?"
            " AND status = 'open'",
            (guild_id, user_id, category),
        )

    async def close_ticket(self, channel_id: int, closed_by: int) -> None:
        await self._exec(
            "UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ?"
            " WHERE channel_id = ?",
            (iso(), closed_by, channel_id),
        )
