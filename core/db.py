"""SQLite 저장소.

포인트 · 경고 · 롤 계정 등록 · 내전 · 티켓 정보를 한 파일에 담는다.
매일 자정 백업은 이 파일과 data/ 디렉터리를 통째로 압축한다.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import aiosqlite

from config import DB_PATH, Level, TIMEZONE

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
    registered_by  INTEGER,
    voice_xp       INTEGER NOT NULL DEFAULT 0,
    chat_xp        INTEGER NOT NULL DEFAULT 0,
    rank_solo      TEXT,
    rank_flex      TEXT,
    rank_updated_at TEXT
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

CREATE TABLE IF NOT EXISTS panels (
    key        TEXT PRIMARY KEY,   -- 예: 'main_lane'
    guild_id   INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_panels_message ON panels(message_id);

CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL,      -- 'theme' | 'slogan' | 'color_role'
    item_key   TEXT NOT NULL,      -- 테마 키 · 색상 키 등
    value      TEXT,               -- 문구 내용처럼 함께 저장할 값
    price      INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_purchases_expiry ON purchases(expires_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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


def xp_for_level(level: int) -> int:
    """`level` 에서 다음 레벨로 올라가는 데 필요한 경험치."""
    return Level.BASE_XP + Level.STEP_XP * max(0, level)


def level_progress(total_xp: int) -> tuple[int, int, int]:
    """누적 경험치를 (레벨, 현재 레벨에서 모은 XP, 다음 레벨까지 필요한 XP) 로."""
    level = 0
    remaining = max(0, total_xp)
    while remaining >= xp_for_level(level):
        remaining -= xp_for_level(level)
        level += 1
    return level, remaining, xp_for_level(level)


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
    voice_xp: int = 0
    chat_xp: int = 0
    rank_solo: Optional[str] = None
    rank_flex: Optional[str] = None
    rank_updated_at: Optional[str] = None

    @property
    def riot_id(self) -> Optional[str]:
        if self.riot_game_name and self.riot_tag_line:
            return f"{self.riot_game_name}#{self.riot_tag_line}"
        return None

    @property
    def registered(self) -> bool:
        return self.riot_id is not None

    @property
    def voice_level(self) -> tuple[int, int, int]:
        return level_progress(self.voice_xp)

    @property
    def chat_level(self) -> tuple[int, int, int]:
        return level_progress(self.chat_xp)

    def _rank(self, raw: Optional[str]) -> Optional[dict]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    @property
    def solo_rank(self) -> Optional[dict]:
        return self._rank(self.rank_solo)

    @property
    def flex_rank(self) -> Optional[dict]:
        return self._rank(self.rank_flex)

    def rank_is_fresh(self, max_age_seconds: int) -> bool:
        """캐시된 랭크 정보를 그대로 써도 되는지."""
        if not self.rank_updated_at:
            return False
        try:
            updated = dt.datetime.fromisoformat(self.rank_updated_at)
        except ValueError:
            return False
        return (now() - updated).total_seconds() < max_age_seconds


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
        await self._migrate()

    async def _migrate(self) -> None:
        """예전 버전에서 만들어진 DB 에 빠진 컬럼을 채워 넣는다."""
        additions = {
            "voice_xp": "INTEGER NOT NULL DEFAULT 0",
            "chat_xp": "INTEGER NOT NULL DEFAULT 0",
            "rank_solo": "TEXT",
            "rank_flex": "TEXT",
            "rank_updated_at": "TEXT",
        }
        async with self.conn.execute("PRAGMA table_info(users)") as cur:
            existing = {row["name"] for row in await cur.fetchall()}

        for column, definition in additions.items():
            if column in existing:
                continue
            await self.conn.execute(
                f"ALTER TABLE users ADD COLUMN {column} {definition}"
            )
        await self.conn.commit()

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

    # ------------------------------------------------------------- 레벨

    async def add_xp(self, user_id: int, column: str, amount: int) -> tuple[int, int]:
        """경험치를 더하고 (이전 레벨, 새 레벨) 을 돌려준다."""
        if column not in ("voice_xp", "chat_xp"):
            raise ValueError(f"알 수 없는 경험치 종류: {column}")
        await self.ensure_user(user_id)
        row = await self._fetchone(
            f"SELECT {column} AS xp FROM users WHERE user_id = ?", (user_id,)
        )
        before = int(row["xp"]) if row else 0
        after = before + max(0, amount)
        await self._exec(
            f"UPDATE users SET {column} = ? WHERE user_id = ?", (after, user_id)
        )
        return level_progress(before)[0], level_progress(after)[0]

    async def xp_rank(self, user_id: int, column: str) -> Optional[int]:
        """해당 경험치 기준 순위 (1등부터)."""
        if column not in ("voice_xp", "chat_xp"):
            raise ValueError(f"알 수 없는 경험치 종류: {column}")
        exists = await self._fetchone("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        if exists is None:
            return None
        row = await self._fetchone(
            f"SELECT COUNT(*) + 1 AS rank FROM users"
            f" WHERE {column} > (SELECT {column} FROM users WHERE user_id = ?)",
            (user_id,),
        )
        return int(row["rank"]) if row else None

    async def top_xp(self, column: str, limit: int = 10) -> list[aiosqlite.Row]:
        if column not in ("voice_xp", "chat_xp"):
            raise ValueError(f"알 수 없는 경험치 종류: {column}")
        return await self._fetchall(
            f"SELECT user_id, {column} AS xp FROM users WHERE {column} > 0"
            f" ORDER BY {column} DESC, user_id ASC LIMIT ?",
            (limit,),
        )

    # --------------------------------------------------------- 랭크 캐시

    async def set_riot_ranks(
        self, user_id: int, solo: Optional[dict], flex: Optional[dict]
    ) -> None:
        """라이엇에서 받아온 솔랭/자유랭크 정보를 캐시한다."""
        await self.ensure_user(user_id)
        await self._exec(
            "UPDATE users SET rank_solo = ?, rank_flex = ?, rank_updated_at = ?"
            " WHERE user_id = ?",
            (
                json.dumps(solo, ensure_ascii=False) if solo else None,
                json.dumps(flex, ensure_ascii=False) if flex else None,
                iso(),
                user_id,
            ),
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
        # 계정이 바뀌면 캐시된 랭크 정보는 더 이상 이 사람 것이 아니다
        await self._exec(
            "UPDATE users SET riot_game_name = ?, riot_tag_line = ?, riot_puuid = ?,"
            " registered_at = ?, registered_by = ?,"
            " rank_solo = NULL, rank_flex = NULL, rank_updated_at = NULL"
            " WHERE user_id = ?",
            (game_name, tag_line, puuid, iso(), actor_id, user_id),
        )

    async def clear_riot_account(self, user_id: int) -> None:
        await self._exec(
            "UPDATE users SET riot_game_name = NULL, riot_tag_line = NULL,"
            " riot_puuid = NULL, registered_at = NULL, registered_by = NULL,"
            " rank_solo = NULL, rank_flex = NULL, rank_updated_at = NULL"
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

    # --------------------------------------------------------------- 상점

    async def add_purchase(
        self,
        user_id: int,
        kind: str,
        item_key: str,
        price: int,
        days: int,
        value: Optional[str] = None,
    ) -> str:
        """구매를 기록하고 만료 시각(ISO)을 돌려준다."""
        await self.ensure_user(user_id)
        expires = now() + dt.timedelta(days=days)
        await self._exec(
            "INSERT INTO purchases"
            "(user_id, kind, item_key, value, price, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, kind, item_key, value, price, iso(), expires.isoformat(timespec="seconds")),
        )
        return expires.isoformat(timespec="seconds")

    async def active_purchase(self, user_id: int, kind: str):
        """아직 유효한 구매 중 가장 최근 것. 없으면 None."""
        return await self._fetchone(
            "SELECT * FROM purchases WHERE user_id = ? AND kind = ?"
            " AND expires_at > ? ORDER BY id DESC LIMIT 1",
            (user_id, kind, iso()),
        )

    async def active_purchases(self, user_id: int) -> list[aiosqlite.Row]:
        """이 사람이 지금 쓰고 있는 아이템 전부."""
        return await self._fetchall(
            "SELECT * FROM purchases WHERE user_id = ? AND expires_at > ?"
            " ORDER BY expires_at ASC",
            (user_id, iso()),
        )

    async def active_by_kind(self, kind: str) -> list[aiosqlite.Row]:
        """종류별로 아직 유효한 구매 전부 (역할 회수 판단에 쓴다)."""
        return await self._fetchall(
            "SELECT * FROM purchases WHERE kind = ? AND expires_at > ?",
            (kind, iso()),
        )

    # ------------------------------------------------------- 설정 저장소

    async def get_setting(self, key: str) -> Optional[str]:
        row = await self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return str(row["value"]) if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._exec(
            "INSERT INTO settings(key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_json_setting(self, key: str) -> Optional[dict]:
        raw = await self.get_setting(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    async def set_json_setting(self, key: str, value: dict) -> None:
        await self.set_setting(key, json.dumps(value, ensure_ascii=False))

    # -------------------------------------------------------- 역할 선택 패널

    async def set_panel(
        self, key: str, guild_id: int, channel_id: int, message_id: int
    ) -> None:
        """패널 메시지를 기억해 둔다 (재시작 후에도 반응을 알아보기 위해)."""
        await self._exec(
            "INSERT OR REPLACE INTO panels(key, guild_id, channel_id, message_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (key, guild_id, channel_id, message_id, iso()),
        )

    async def get_panel(self, key: str) -> Optional[aiosqlite.Row]:
        return await self._fetchone("SELECT * FROM panels WHERE key = ?", (key,))

    async def panel_key_of(self, message_id: int) -> Optional[str]:
        """이 메시지가 역할 선택 패널이라면 그 종류를 돌려준다."""
        row = await self._fetchone(
            "SELECT key FROM panels WHERE message_id = ?", (message_id,)
        )
        return str(row["key"]) if row else None

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
