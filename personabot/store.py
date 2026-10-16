"""SQLite 儲存層:對話、訊息、長期記憶。

刻意不用 ORM。每次操作開一條連線,所以多執行緒下不用煩惱 connection 共用的問題;
量級是「一個人的陪聊機器人」,這樣綽綽有餘。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    user_id         TEXT NOT NULL,
    persona_id      TEXT NOT NULL,
    display_name    TEXT,
    summary         TEXT NOT NULL DEFAULT '',
    summarized_upto INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    last_active_at  REAL NOT NULL,
    UNIQUE (platform, user_id, persona_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text            TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS facts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,
    created_at      REAL NOT NULL,
    UNIQUE (conversation_id, content)
);
CREATE INDEX IF NOT EXISTS idx_facts_conv ON facts (conversation_id, id);
"""


@dataclass
class Conversation:
    id: int
    platform: str
    user_id: str
    persona_id: str
    display_name: str | None
    summary: str
    summarized_upto: int
    created_at: float
    last_active_at: float


@dataclass
class Message:
    id: int
    role: str
    text: str
    created_at: float


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if str(self.db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # :memory: 每開一條連線就是一個新的空資料庫,所以測試時共用同一條。
        self._shared: sqlite3.Connection | None = None
        if str(self.db_path) == ":memory:":
            self._shared = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            with self._shared:
                yield self._shared
            return
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ---- 對話 ----

    def get_or_create_conversation(
        self,
        platform: str,
        user_id: str,
        persona_id: str,
        display_name: str | None = None,
    ) -> Conversation:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO conversations
                       (platform, user_id, persona_id, display_name, created_at, last_active_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (platform, user_id, persona_id) DO NOTHING""",
                (platform, user_id, persona_id, display_name, now, now),
            )
            if display_name:
                conn.execute(
                    """UPDATE conversations SET display_name = ?
                       WHERE platform = ? AND user_id = ? AND persona_id = ?""",
                    (display_name, platform, user_id, persona_id),
                )
            row = conn.execute(
                """SELECT * FROM conversations
                   WHERE platform = ? AND user_id = ? AND persona_id = ?""",
                (platform, user_id, persona_id),
            ).fetchone()
        return Conversation(**dict(row))

    def touch(self, conversation_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET last_active_at = ? WHERE id = ?",
                (time.time(), conversation_id),
            )

    def set_summary(self, conversation_id: int, summary: str, upto_message_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE conversations SET summary = ?, summarized_upto = ? WHERE id = ?",
                (summary, upto_message_id, conversation_id),
            )

    def get_conversation(self, conversation_id: int) -> Conversation | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return Conversation(**dict(row)) if row else None

    # ---- 訊息 ----

    def add_message(self, conversation_id: int, role: str, text: str) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO messages (conversation_id, role, text, created_at)
                   VALUES (?, ?, ?, ?)""",
                (conversation_id, role, text, time.time()),
            )
            conn.execute(
                "UPDATE conversations SET last_active_at = ? WHERE id = ?",
                (time.time(), conversation_id),
            )
            return int(cur.lastrowid)

    def recent_messages(self, conversation_id: int, limit: int, after_id: int = 0) -> list[Message]:
        """最近 limit 則,依時間由舊到新。"""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, role, text, created_at FROM messages
                   WHERE conversation_id = ? AND id > ?
                   ORDER BY id DESC LIMIT ?""",
                (conversation_id, after_id, limit),
            ).fetchall()
        return [Message(**dict(r)) for r in reversed(rows)]

    def messages_between(self, conversation_id: int, after_id: int, before_id: int) -> list[Message]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, role, text, created_at FROM messages
                   WHERE conversation_id = ? AND id > ? AND id <= ?
                   ORDER BY id ASC""",
                (conversation_id, after_id, before_id),
            ).fetchall()
        return [Message(**dict(r)) for r in rows]

    def count_messages(self, conversation_id: int, after_id: int = 0) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND id > ?",
                (conversation_id, after_id),
            ).fetchone()
        return int(row["n"])

    # ---- 長期記憶 ----

    def add_facts(self, conversation_id: int, contents: list[str], cap: int) -> int:
        """寫入新的記憶,回傳實際新增幾則。超過上限就丟掉最舊的。"""
        added = 0
        now = time.time()
        with self._conn() as conn:
            for content in contents:
                content = content.strip()
                if not content:
                    continue
                cur = conn.execute(
                    """INSERT INTO facts (conversation_id, content, created_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT (conversation_id, content) DO NOTHING""",
                    (conversation_id, content, now),
                )
                added += cur.rowcount if cur.rowcount > 0 else 0
            conn.execute(
                """DELETE FROM facts WHERE conversation_id = ? AND id NOT IN (
                       SELECT id FROM facts WHERE conversation_id = ?
                       ORDER BY id DESC LIMIT ?
                   )""",
                (conversation_id, conversation_id, cap),
            )
        return added

    def get_facts(self, conversation_id: int) -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT content FROM facts WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
        return [r["content"] for r in rows]

    # ---- 清除 ----

    def reset_conversation(self, conversation_id: int) -> None:
        """忘記這個人的一切,但保留對話本身。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM facts WHERE conversation_id = ?", (conversation_id,))
            conn.execute(
                "UPDATE conversations SET summary = '', summarized_upto = 0 WHERE id = ?",
                (conversation_id,),
            )
