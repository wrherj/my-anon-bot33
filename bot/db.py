import asyncio
import time
from collections.abc import Iterable
from pathlib import Path

import aiosqlite

from .domain import Conversation, EndedConversation, Gender, MatchResult, UserProfile

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    gender TEXT CHECK (gender IN ('male', 'female')),
    age INTEGER,
    subscription_expires_at INTEGER,
    last_partner_id INTEGER,
    is_banned INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS waiting_queue (
    user_id INTEGER PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
    joined_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER NOT NULL REFERENCES users(telegram_id),
    user2_id INTEGER NOT NULL REFERENCES users(telegram_id),
    started_at INTEGER NOT NULL,
    ends_at INTEGER,
    ended_at INTEGER,
    end_reason TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_conversations_active_1
ON conversations(user1_id, active);

CREATE INDEX IF NOT EXISTS idx_conversations_active_2
ON conversations(user2_id, active);

CREATE INDEX IF NOT EXISTS idx_conversations_expiry
ON conversations(ends_at, active);

CREATE TABLE IF NOT EXISTS blocks (
    blocker_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (blocker_id, blocked_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL REFERENCES users(telegram_id),
    reported_id INTEGER NOT NULL REFERENCES users(telegram_id),
    conversation_id INTEGER REFERENCES conversations(id),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    telegram_charge_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id),
    amount INTEGER NOT NULL,
    currency TEXT NOT NULL,
    subscription_expires_at INTEGER NOT NULL,
    is_recurring INTEGER NOT NULL DEFAULT 0,
    is_first_recurring INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""


def _profile(row: aiosqlite.Row | None) -> UserProfile | None:
    if row is None:
        return None
    gender = Gender(row["gender"]) if row["gender"] else None
    return UserProfile(
        telegram_id=row["telegram_id"],
        username=row["username"],
        gender=gender,
        age=row["age"],
        subscription_expires_at=row["subscription_expires_at"],
        is_banned=bool(row["is_banned"]),
    )


def _conversation(row: aiosqlite.Row) -> Conversation:
    return Conversation(
        id=row["id"],
        user1_id=row["user1_id"],
        user2_id=row["user2_id"],
        started_at=row["started_at"],
        ends_at=row["ends_at"],
    )


class Database:
    """SQLite storage for a single running bot process."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA busy_timeout = 5000")
        await self.connection.executescript(SCHEMA)
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def touch_user(self, user_id: int, username: str | None) -> None:
        now = int(time.time())
        async with self._write_lock:
            await self.conn.execute(
                """
                INSERT INTO users (telegram_id, username, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, now, now),
            )
            await self.conn.commit()

    async def get_user(self, user_id: int) -> UserProfile | None:
        cursor = await self.conn.execute(
            """
            SELECT telegram_id, username, gender, age,
                   subscription_expires_at, is_banned
            FROM users WHERE telegram_id = ?
            """,
            (user_id,),
        )
        return _profile(await cursor.fetchone())

    async def save_profile(self, user_id: int, gender: Gender, age: int) -> None:
        now = int(time.time())
        async with self._write_lock:
            await self.conn.execute(
                "UPDATE users SET gender = ?, age = ?, updated_at = ? WHERE telegram_id = ?",
                (gender.value, age, now, user_id),
            )
            await self.conn.commit()

    async def get_active_conversation(self, user_id: int) -> Conversation | None:
        cursor = await self.conn.execute(
            """
            SELECT id, user1_id, user2_id, started_at, ends_at
            FROM conversations
            WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
            ORDER BY id DESC LIMIT 1
            """,
            (user_id, user_id),
        )
        row = await cursor.fetchone()
        return _conversation(row) if row else None

    async def is_waiting(self, user_id: int) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM waiting_queue WHERE user_id = ?",
            (user_id,),
        )
        return await cursor.fetchone() is not None

    async def leave_queue(self, user_id: int) -> None:
        async with self._write_lock:
            await self.conn.execute("DELETE FROM waiting_queue WHERE user_id = ?", (user_id,))
            await self.conn.commit()

    async def waiting_user_ids(self) -> list[int]:
        cursor = await self.conn.execute("SELECT user_id FROM waiting_queue ORDER BY joined_at")
        return [row["user_id"] for row in await cursor.fetchall()]

    async def join_or_match(
        self,
        user_id: int,
        free_chat_seconds: int,
        *,
        now: int | None = None,
    ) -> MatchResult:
        current_time = int(time.time()) if now is None else now
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                user_cursor = await self.conn.execute(
                    """
                    SELECT telegram_id, username, gender, age,
                           subscription_expires_at, is_banned, last_partner_id
                    FROM users WHERE telegram_id = ?
                    """,
                    (user_id,),
                )
                user = await user_cursor.fetchone()
                if not user or not user["gender"] or user["age"] is None:
                    await self.conn.rollback()
                    return MatchResult(status="profile_required")
                if user["is_banned"]:
                    await self.conn.rollback()
                    return MatchResult(status="banned")

                active_cursor = await self.conn.execute(
                    """
                    SELECT id, user1_id, user2_id, started_at, ends_at
                    FROM conversations
                    WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
                    LIMIT 1
                    """,
                    (user_id, user_id),
                )
                active = await active_cursor.fetchone()
                if active:
                    await self.conn.rollback()
                    return MatchResult(status="active", conversation=_conversation(active))

                await self.conn.execute(
                    "INSERT OR IGNORE INTO waiting_queue(user_id, joined_at) VALUES (?, ?)",
                    (user_id, current_time),
                )

                candidate_cursor = await self.conn.execute(
                    """
                    SELECT u.telegram_id, u.age, u.subscription_expires_at
                    FROM waiting_queue q
                    JOIN users u ON u.telegram_id = q.user_id
                    WHERE q.user_id != ?
                      AND u.gender != ?
                      AND u.gender IS NOT NULL
                      AND u.age IS NOT NULL
                      AND u.is_banned = 0
                      AND (? IS NULL OR u.telegram_id != ?)
                      AND NOT EXISTS (
                          SELECT 1 FROM blocks b
                          WHERE (b.blocker_id = ? AND b.blocked_id = u.telegram_id)
                             OR (b.blocker_id = u.telegram_id AND b.blocked_id = ?)
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM conversations c
                          WHERE c.active = 1
                            AND (c.user1_id = u.telegram_id OR c.user2_id = u.telegram_id)
                      )
                    ORDER BY q.joined_at, q.user_id
                    LIMIT 1
                    """,
                    (
                        user_id,
                        user["gender"],
                        user["last_partner_id"],
                        user["last_partner_id"],
                        user_id,
                        user_id,
                    ),
                )
                candidate = await candidate_cursor.fetchone()
                if candidate is None:
                    await self.conn.commit()
                    return MatchResult(status="waiting", user_age=user["age"])

                partner_id = candidate["telegram_id"]
                premium = (user["subscription_expires_at"] or 0) > current_time or (
                    candidate["subscription_expires_at"] or 0
                ) > current_time
                ends_at = None if premium else current_time + free_chat_seconds

                await self.conn.execute(
                    "DELETE FROM waiting_queue WHERE user_id IN (?, ?)",
                    (user_id, partner_id),
                )
                insert = await self.conn.execute(
                    """
                    INSERT INTO conversations
                        (user1_id, user2_id, started_at, ends_at, active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (user_id, partner_id, current_time, ends_at),
                )
                await self.conn.execute(
                    "UPDATE users SET last_partner_id = ? WHERE telegram_id = ?",
                    (partner_id, user_id),
                )
                await self.conn.execute(
                    "UPDATE users SET last_partner_id = ? WHERE telegram_id = ?",
                    (user_id, partner_id),
                )
                await self.conn.commit()
                conversation = Conversation(
                    id=insert.lastrowid,
                    user1_id=user_id,
                    user2_id=partner_id,
                    started_at=current_time,
                    ends_at=ends_at,
                )
                return MatchResult(
                    status="matched",
                    conversation=conversation,
                    user_age=user["age"],
                    partner_age=candidate["age"],
                )
            except Exception:
                await self.conn.rollback()
                raise

    async def end_active_conversation(
        self,
        actor_id: int,
        reason: str,
        *,
        requeue_actor: bool,
        requeue_partner: bool,
        now: int | None = None,
    ) -> EndedConversation | None:
        current_time = int(time.time()) if now is None else now
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT id, user1_id, user2_id
                    FROM conversations
                    WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
                    LIMIT 1
                    """,
                    (actor_id, actor_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self.conn.rollback()
                    return None
                partner_id = row["user2_id"] if row["user1_id"] == actor_id else row["user1_id"]
                await self.conn.execute(
                    """
                    UPDATE conversations
                    SET active = 0, ended_at = ?, end_reason = ?
                    WHERE id = ? AND active = 1
                    """,
                    (current_time, reason, row["id"]),
                )
                for should_queue, queued_id in (
                    (requeue_actor, actor_id),
                    (requeue_partner, partner_id),
                ):
                    if should_queue:
                        await self.conn.execute(
                            """
                            INSERT INTO waiting_queue(user_id, joined_at) VALUES (?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET joined_at = excluded.joined_at
                            """,
                            (queued_id, current_time),
                        )
                    else:
                        await self.conn.execute(
                            "DELETE FROM waiting_queue WHERE user_id = ?",
                            (queued_id,),
                        )
                await self.conn.commit()
                return EndedConversation(row["id"], actor_id, partner_id)
            except Exception:
                await self.conn.rollback()
                raise

    async def expire_due(self, *, now: int | None = None) -> list[Conversation]:
        current_time = int(time.time()) if now is None else now
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT id, user1_id, user2_id, started_at, ends_at
                    FROM conversations
                    WHERE active = 1 AND ends_at IS NOT NULL AND ends_at <= ?
                    """,
                    (current_time,),
                )
                rows = await cursor.fetchall()
                conversations = [_conversation(row) for row in rows]
                for conversation in conversations:
                    await self.conn.execute(
                        """
                        UPDATE conversations
                        SET active = 0, ended_at = ?, end_reason = 'free_timeout'
                        WHERE id = ? AND active = 1
                        """,
                        (current_time, conversation.id),
                    )
                    for user_id in (conversation.user1_id, conversation.user2_id):
                        await self.conn.execute(
                            """
                            INSERT INTO waiting_queue(user_id, joined_at) VALUES (?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET joined_at = excluded.joined_at
                            """,
                            (user_id, current_time),
                        )
                await self.conn.commit()
                return conversations
            except Exception:
                await self.conn.rollback()
                raise

    async def restore_free_timers(
        self,
        free_chat_seconds: int,
        *,
        now: int | None = None,
    ) -> list[Conversation]:
        """Start a free timer when neither participant has an active subscription."""
        current_time = int(time.time()) if now is None else now
        new_ends_at = current_time + free_chat_seconds
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    SELECT c.id, c.user1_id, c.user2_id, c.started_at
                    FROM conversations c
                    JOIN users u1 ON u1.telegram_id = c.user1_id
                    JOIN users u2 ON u2.telegram_id = c.user2_id
                    WHERE c.active = 1
                      AND c.ends_at IS NULL
                      AND COALESCE(u1.subscription_expires_at, 0) <= ?
                      AND COALESCE(u2.subscription_expires_at, 0) <= ?
                    """,
                    (current_time, current_time),
                )
                rows = await cursor.fetchall()
                conversations = [
                    Conversation(
                        id=row["id"],
                        user1_id=row["user1_id"],
                        user2_id=row["user2_id"],
                        started_at=row["started_at"],
                        ends_at=new_ends_at,
                    )
                    for row in rows
                ]
                if conversations:
                    placeholders = ",".join("?" for _ in conversations)
                    await self.conn.execute(
                        f"UPDATE conversations SET ends_at = ? WHERE id IN ({placeholders})",
                        (new_ends_at, *(item.id for item in conversations)),
                    )
                await self.conn.commit()
                return conversations
            except Exception:
                await self.conn.rollback()
                raise

    async def add_report_and_block(
        self,
        reporter_id: int,
        reported_id: int,
        conversation_id: int,
    ) -> None:
        now = int(time.time())
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute(
                    """
                    INSERT INTO reports(reporter_id, reported_id, conversation_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (reporter_id, reported_id, conversation_id, now),
                )
                await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO blocks(blocker_id, blocked_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (reporter_id, reported_id, now),
                )
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def record_payment(
        self,
        *,
        user_id: int,
        telegram_charge_id: str,
        amount: int,
        currency: str,
        subscription_expires_at: int,
        is_recurring: bool,
        is_first_recurring: bool,
    ) -> bool:
        now = int(time.time())
        async with self._write_lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO payments(
                        telegram_charge_id, user_id, amount, currency,
                        subscription_expires_at, is_recurring,
                        is_first_recurring, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        telegram_charge_id,
                        user_id,
                        amount,
                        currency,
                        subscription_expires_at,
                        int(is_recurring),
                        int(is_first_recurring),
                        now,
                    ),
                )
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(
                    """
                    UPDATE users
                    SET subscription_expires_at = MAX(
                        COALESCE(subscription_expires_at, 0), ?
                    ), updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (subscription_expires_at, now, user_id),
                )
                await self.conn.execute(
                    """
                    UPDATE conversations SET ends_at = NULL
                    WHERE active = 1 AND (user1_id = ? OR user2_id = ?)
                    """,
                    (user_id, user_id),
                )
                await self.conn.commit()
                return True
            except Exception:
                await self.conn.rollback()
                raise

    async def stats(self) -> dict[str, int]:
        names_and_queries: Iterable[tuple[str, str]] = (
            ("users", "SELECT COUNT(*) AS value FROM users"),
            ("waiting", "SELECT COUNT(*) AS value FROM waiting_queue"),
            ("active_chats", "SELECT COUNT(*) AS value FROM conversations WHERE active = 1"),
            ("reports", "SELECT COUNT(*) AS value FROM reports"),
            (
                "subscribers",
                "SELECT COUNT(*) AS value FROM users WHERE subscription_expires_at > unixepoch()",
            ),
        )
        result: dict[str, int] = {}
        for name, query in names_and_queries:
            cursor = await self.conn.execute(query)
            row = await cursor.fetchone()
            result[name] = row["value"]
        return result
