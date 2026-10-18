"""SQLite 儲存層:對話、訊息、長期記憶。

刻意不用 ORM。每次操作開一條連線,所以多執行緒下不用煩惱 connection 共用的問題;
量級是「一個人的陪聊機器人」,這樣綽綽有餘。
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

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
    interest        INTEGER NOT NULL DEFAULT 50,
    guard           INTEGER NOT NULL DEFAULT 0,
    quiet_turns     INTEGER NOT NULL DEFAULT 0,
    read_state      TEXT NOT NULL DEFAULT '',
    last_gap        REAL NOT NULL DEFAULT 0,
    last_chars      INTEGER NOT NULL DEFAULT 0,
    stage           INTEGER NOT NULL DEFAULT 1,
    last_energy     TEXT NOT NULL DEFAULT '',
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

# CREATE TABLE IF NOT EXISTS 對已經存在的表什麼都不做,所以後來加的欄位要自己補。
# 不然舊的 data/personabot.db 一升級就整個壞掉。
_ADDED_COLUMNS = [
    ("conversations", "interest", "INTEGER NOT NULL DEFAULT 50"),
    ("conversations", "guard", "INTEGER NOT NULL DEFAULT 0"),
    ("conversations", "quiet_turns", "INTEGER NOT NULL DEFAULT 0"),
    ("conversations", "read_state", "TEXT NOT NULL DEFAULT ''"),
    ("conversations", "last_gap", "REAL NOT NULL DEFAULT 0"),
    ("conversations", "last_chars", "INTEGER NOT NULL DEFAULT 0"),
    ("conversations", "stage", "INTEGER NOT NULL DEFAULT 1"),
    ("conversations", "last_energy", "TEXT NOT NULL DEFAULT ''"),
]


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
    interest: int = 50
    guard: int = 0
    quiet_turns: int = 0
    read_state: str = ""
    last_gap: float = 0.0
    last_chars: int = 0
    stage: int = 1  # 1 開場、2 熟悉、3 吸引
    last_energy: str = ""  # 上一則的語氣能量,拿來讓這則不要同調


@dataclass
class Message:
    id: int
    role: str
    text: str
    created_at: float


# 小模型抽記憶時很愛把同一件事換句話再講一次:
#     ・使用者在台中念資工三年級
#     ・使用者阿明在台中市就讀資訊工程學系三年級,正在考慮升學
# 字串不一樣,UNIQUE 擋不住,兩則就都留著了。長期記憶每一輪都會整份送進
# system prompt,所以重複的每一則都變成之後每一次對話的固定開銷 ——
# 本地模型上這直接反映成每輪多吃的 prompt eval 時間。
#
# 這裡刻意保守。中文差兩個字意思就全變了(「在台中念書」/「在台中打工」
# 相似度有 0.75),而把兩件不同的事併掉比留著一則重複糟糕得多 ——
# 記憶錯了是這個專案最不能壞的東西。所以只處理機械性的重複:
# 一則完整包住另一則,或只差標點、異體字那種程度。
# 真正「換句話說」的語意重複(「在台中念資工」vs「就讀資訊工程學系三年級」)
# 字面上差太遠,抓不到 —— 那要讓模型判斷,是另一回事。
_SIMILAR_ENOUGH = 0.85

# 太短的句子不做模糊比對:「事實1」和「事實10」字面上像得要命,
# 但那是兩件事。短的記憶本來也不佔什麼位置,留著就留著。
_MIN_FUZZY_CHARS = 6

# 比對前先把標點和空白拿掉。中文的斷句習慣不固定,同一句話加不加逗號
# 都會讓相似度掉下來。
# 全形與半形都要列進去,這裡的「長得像」正是重點,所以關掉 RUF001。
_PUNCT = re.compile(r"""[\s,.、，。!！?？;；:：\-—~～「」『』()（）\[\]【】"'’”]""")  # noqa: RUF001


def normalize_fact(text: str) -> str:
    """比對記憶內容前的正規化。brain 那邊檢查模型合併結果時也用同一套。"""
    return _PUNCT.sub("", text)


def _closest(content: str, known: list[tuple[int, str]]) -> tuple[int, str] | None:
    """在既有記憶裡找出跟 content 講同一件事的那一則,沒有就回 None。"""
    normalized = normalize_fact(content)
    if len(normalized) < _MIN_FUZZY_CHARS:
        return None
    best: tuple[int, str] | None = None
    best_ratio = 0.0
    for row_id, existing in known:
        other = normalize_fact(existing)
        if len(other) < _MIN_FUZZY_CHARS:
            continue
        # 一則完整包住另一則,幾乎都是「同一件事講得比較細」。
        if normalized in other or other in normalized:
            ratio = 1.0
        else:
            ratio = SequenceMatcher(None, normalized, other).ratio()
        if ratio >= _SIMILAR_ENOUGH and ratio > best_ratio:
            best, best_ratio = (row_id, existing), ratio
    return best


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
            for table, column, spec in _ADDED_COLUMNS:
                existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")

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

    # ---- 關係狀態 ----

    def update_rapport(self, conversation_id: int, **fields: object) -> None:
        """更新興趣度、戒心那幾個欄位。只寫有傳進來的,沒傳的不動。"""
        allowed = {
            "interest", "guard", "quiet_turns", "read_state", "last_gap", "last_chars",
            "stage", "last_energy",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_rapport 不認得這些欄位:{sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self._conn() as conn:
            conn.execute(
                f"UPDATE conversations SET {assignments} WHERE id = ?",
                (*fields.values(), conversation_id),
            )

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

    def last_message(self, conversation_id: int, role: str) -> Message | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id, role, text, created_at FROM messages
                   WHERE conversation_id = ? AND role = ?
                   ORDER BY id DESC LIMIT 1""",
                (conversation_id, role),
            ).fetchone()
        return Message(**dict(row)) if row else None

    def messages_between(self, conversation_id: int, after_id: int, before_id: int) -> list[Message]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, role, text, created_at FROM messages
                   WHERE conversation_id = ? AND id > ? AND id <= ?
                   ORDER BY id ASC""",
                (conversation_id, after_id, before_id),
            ).fetchall()
        return [Message(**dict(r)) for r in rows]

    def count_messages(
        self, conversation_id: int, after_id: int = 0, role: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ? AND id > ?"
        params: tuple = (conversation_id, after_id)
        if role is not None:
            sql += " AND role = ?"
            params += (role,)
        with self._conn() as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row["n"])

    # ---- 長期記憶 ----

    def add_facts(self, conversation_id: int, contents: list[str], cap: int) -> int:
        """寫入新的記憶,回傳實際新增幾則。超過上限就丟掉最舊的。

        跟既有記憶講的是同一件事就不另外開一則:講得比較清楚的那版就地取代舊的,
        留在原本的位置 —— 清單的順序穩定,後端的 prompt 快取才有得用。
        """
        added = 0
        now = time.time()
        with self._conn() as conn:
            known = self._facts_with_ids(conn, conversation_id)
            for content in contents:
                content = content.strip()
                if not content:
                    continue
                match = _closest(content, known)
                if match is not None:
                    row_id, existing = match
                    if len(normalize_fact(content)) <= len(normalize_fact(existing)):
                        continue  # 舊的已經講得一樣多,不動它
                    try:
                        conn.execute(
                            "UPDATE facts SET content = ? WHERE id = ?", (content, row_id)
                        )
                    except sqlite3.IntegrityError:
                        continue  # 改完會跟另一則一模一樣,那就維持原狀
                    known = [(i, content if i == row_id else c) for i, c in known]
                    continue
                cur = conn.execute(
                    """INSERT INTO facts (conversation_id, content, created_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT (conversation_id, content) DO NOTHING""",
                    (conversation_id, content, now),
                )
                if cur.rowcount > 0:
                    known.append((int(cur.lastrowid), content))
                    added += 1
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

    def compact_facts(self, conversation_id: int) -> int:
        """把清單裡講同一件事的那幾則併成一則,回傳刪掉幾則。

        add_facts 只擋得住「新的跟舊的重複」。但記憶是一則一則長出來的,
        到後來才會看出早先那兩三則其實在講同一件事 —— 這裡從頭掃一次補掉。
        """
        with self._conn() as conn:
            known = self._facts_with_ids(conn, conversation_id)
            keep: list[tuple[int, str]] = []
            drop: list[int] = []
            for row_id, content in known:
                match = _closest(content, keep)
                if match is None:
                    keep.append((row_id, content))
                    continue
                kept_id, kept = match
                drop.append(row_id)
                # 併的時候留講得比較清楚的那版,但位置用早出現的那一則。
                if len(normalize_fact(content)) > len(normalize_fact(kept)):
                    keep = [(i, content if i == kept_id else c) for i, c in keep]
            if drop:
                # 先刪再改。反過來做會撞上 UNIQUE —— 要寫進去的那句話
                # 這時候還躺在等著被刪的那一列上。
                conn.execute(
                    f"DELETE FROM facts WHERE id IN ({','.join('?' * len(drop))})",
                    drop,
                )
                conn.executemany(
                    "UPDATE facts SET content = ? WHERE id = ?",
                    [(content, row_id) for row_id, content in keep],
                )
        return len(drop)

    def replace_facts(self, conversation_id: int, contents: list[str]) -> None:
        """整份換掉。給「讓模型重新整理過一遍」用的,順序照傳進來的。"""
        rows = [(conversation_id, c.strip(), time.time()) for c in contents if c.strip()]
        with self._conn() as conn:
            conn.execute("DELETE FROM facts WHERE conversation_id = ?", (conversation_id,))
            conn.executemany(
                """INSERT INTO facts (conversation_id, content, created_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT (conversation_id, content) DO NOTHING""",
                rows,
            )

    @staticmethod
    def _facts_with_ids(conn: sqlite3.Connection, conversation_id: int) -> list[tuple[int, str]]:
        rows = conn.execute(
            "SELECT id, content FROM facts WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
        return [(int(r["id"]), r["content"]) for r in rows]

    # ---- 清除 ----

    def reset_conversation(self, conversation_id: int) -> None:
        """忘記這個人的一切,但保留對話本身。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM facts WHERE conversation_id = ?", (conversation_id,))
            # 關係狀態也要歸零。漏掉的話 /reset 之後她還是記得「他一直在試探我」,
            # smoke 每跑一次戒心就往上疊一層。
            conn.execute(
                """UPDATE conversations SET summary = '', summarized_upto = 0,
                       interest = 50, guard = 0, quiet_turns = 0, read_state = '',
                       last_gap = 0, last_chars = 0, stage = 1, last_energy = ''
                   WHERE id = ?""",
                (conversation_id,),
            )
