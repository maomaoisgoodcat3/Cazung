"""SQLite persistence: full message archive (never deleted), FTS search,
long-term memories with embeddings, feedback, reminders and a small KV store."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .embeddings import strip_accents

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT DEFAULT '',
    created_at REAL NOT NULL,
    summary TEXT DEFAULT '',
    summary_updated REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    role TEXT NOT NULL,              -- user | assistant | tool | system
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0,
    summarized INTEGER NOT NULL DEFAULT 0,
    meta TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_id, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,              -- fact | preference | instruction | event | episode | lesson | skill | note
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    embedding BLOB,
    emb_model TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_accessed REAL DEFAULT 0,
    access_count INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    conv_id TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind, active);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    rating INTEGER NOT NULL,
    comment TEXT DEFAULT '',
    created_at REAL NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at REAL NOT NULL,
    created_at REAL NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, content='', tokenize='unicode61 remove_diacritics 2'
);
"""

_WORD = re.compile(r"\w+", re.UNICODE)


def est_tokens(text: str) -> int:
    # Rough estimate that is a bit pessimistic for Vietnamese (diacritics cost tokens).
    return int(len(text) / 3.0) + 4


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)
            try:
                self.db.executescript(FTS_SCHEMA)
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False
            self.db.commit()

    def _q(self, sql: str, args=(), one=False):
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
            self.db.commit()
        if one:
            return dict(rows[0]) if rows else None
        return [dict(r) for r in rows]

    def _exec(self, sql: str, args=()) -> int:
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    # ------------------------------------------------------------- kv
    def kv_get(self, key: str, default=None):
        row = self._q("SELECT value FROM kv WHERE key=?", (key,), one=True)
        return json.loads(row["value"]) if row else default

    def kv_set(self, key: str, value) -> None:
        self._exec(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # -------------------------------------------------- conversations
    def new_conversation(self, title: str = "") -> str:
        cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self._exec("INSERT INTO conversations(id, title, created_at) VALUES(?,?,?)", (cid, title, time.time()))
        self.kv_set("current_conv", cid)
        return cid

    def current_conversation(self) -> str:
        cid = self.kv_get("current_conv")
        if cid and self._q("SELECT id FROM conversations WHERE id=?", (cid,), one=True):
            return cid
        return self.new_conversation()

    def get_conversation(self, cid: str) -> dict | None:
        return self._q("SELECT * FROM conversations WHERE id=?", (cid,), one=True)

    def set_summary(self, cid: str, summary: str) -> None:
        self._exec(
            "UPDATE conversations SET summary=?, summary_updated=? WHERE id=?", (summary, time.time(), cid)
        )

    # ------------------------------------------------------- messages
    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> int:
        mid = self._exec(
            "INSERT INTO messages(conv_id, role, content, created_at, tokens, meta) VALUES(?,?,?,?,?,?)",
            (cid, role, content, time.time(), est_tokens(content), json.dumps(meta or {}, ensure_ascii=False)),
        )
        if self.fts and role in ("user", "assistant"):
            # index an accent-free copy so "da nang" finds "Đà Nẵng"
            self._exec("INSERT INTO messages_fts(rowid, content) VALUES(?, ?)", (mid, strip_accents(content)))
        return mid

    def update_message(self, mid: int, content: str) -> None:
        self._exec("UPDATE messages SET content=?, tokens=? WHERE id=?", (content, est_tokens(content), mid))

    def get_message(self, mid: int) -> dict | None:
        return self._q("SELECT * FROM messages WHERE id=?", (mid,), one=True)

    def unsummarized(self, cid: str) -> list[dict]:
        return self._q("SELECT * FROM messages WHERE conv_id=? AND summarized=0 ORDER BY id", (cid,))

    def recent_messages(self, cid: str, limit: int = 50) -> list[dict]:
        rows = self._q("SELECT * FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?", (cid, limit))
        return rows[::-1]

    def last_messages_any(self, limit: int = 30) -> list[dict]:
        rows = self._q(
            "SELECT * FROM messages WHERE role IN ('user','assistant','tool') ORDER BY id DESC LIMIT ?", (limit,)
        )
        return rows[::-1]

    def mark_summarized(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.lock:
            self.db.executemany("UPDATE messages SET summarized=1 WHERE id=?", [(i,) for i in ids])
            self.db.commit()

    def search_messages(self, query: str, limit: int = 5, exclude_conv_recent: tuple[str, int] | None = None,
                        roles=("user", "assistant")) -> list[dict]:
        """Keyword search over the whole archive (accent-insensitive)."""
        words = [w for w in _WORD.findall(strip_accents(query.lower())) if len(w) > 2][:12]
        if not words:
            return []
        min_id = None
        if exclude_conv_recent:
            cid, after_id = exclude_conv_recent
            min_id = (cid, after_id)
        role_sql = ",".join("?" * len(roles))
        if self.fts:
            match = " OR ".join(f'"{w}"' for w in words)
            sql = (
                f"SELECT m.*, bm25(messages_fts) AS score FROM messages_fts f JOIN messages m ON m.id=f.rowid "
                f"WHERE messages_fts MATCH ? AND m.role IN ({role_sql}) "
            )
            args: list = [match, *roles]
            if min_id:
                sql += "AND NOT (m.conv_id=? AND m.id>=?) "
                args += list(min_id)
            sql += "ORDER BY score LIMIT ?"
            args.append(limit)
            try:
                return self._q(sql, tuple(args))
            except sqlite3.OperationalError:
                pass
        sql = f"SELECT * FROM messages WHERE role IN ({role_sql}) AND (" + " OR ".join(
            "content LIKE ?" for _ in words) + ") "
        args = [*roles, *[f"%{w}%" for w in words]]
        if min_id:
            sql += "AND NOT (conv_id=? AND id>=?) "
            args += list(min_id)
        sql += "ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self._q(sql, tuple(args))

    def count_messages(self) -> int:
        return self._q("SELECT COUNT(*) AS n FROM messages", one=True)["n"]

    # ------------------------------------------------------- memories
    def add_memory(self, kind: str, content: str, importance: float, embedding: bytes | None,
                   emb_model: str, source: str = "", conv_id: str = "") -> int:
        now = time.time()
        return self._exec(
            "INSERT INTO memories(kind, content, importance, embedding, emb_model, created_at, updated_at, "
            "source, conv_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (kind, content, float(importance), embedding, emb_model, now, now, source, conv_id),
        )

    def update_memory(self, mid: int, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE memories SET {cols} WHERE id=?", (*fields.values(), mid))

    def get_memory(self, mid: int) -> dict | None:
        return self._q("SELECT * FROM memories WHERE id=?", (mid,), one=True)

    def list_memories(self, kinds: list[str] | None = None, active_only: bool = True,
                      with_embedding: bool = False) -> list[dict]:
        cols = "*" if with_embedding else (
            "id, kind, content, importance, emb_model, created_at, updated_at, last_accessed, "
            "access_count, source, active, pinned")
        sql = f"SELECT {cols} FROM memories WHERE 1=1"
        args: list = []
        if active_only:
            sql += " AND active=1"
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        sql += " ORDER BY importance DESC, updated_at DESC"
        return self._q(sql, tuple(args))

    def deactivate_memory(self, mid: int) -> None:
        self.update_memory(mid, active=0)

    def touch_memories(self, ids: list[int]) -> None:
        if not ids:
            return
        now = time.time()
        with self.lock:
            self.db.executemany(
                "UPDATE memories SET last_accessed=?, access_count=access_count+1 WHERE id=?",
                [(now, i) for i in ids],
            )
            self.db.commit()

    def memory_counts(self) -> dict:
        rows = self._q("SELECT kind, COUNT(*) AS n FROM memories WHERE active=1 GROUP BY kind")
        return {r["kind"]: r["n"] for r in rows}

    # ------------------------------------------------------- feedback
    def add_feedback(self, message_id: int, rating: int, comment: str = "") -> int:
        return self._exec(
            "INSERT INTO feedback(message_id, rating, comment, created_at) VALUES(?,?,?,?)",
            (message_id, rating, comment, time.time()),
        )

    def unprocessed_feedback(self) -> list[dict]:
        return self._q(
            "SELECT f.*, m.content AS message FROM feedback f LEFT JOIN messages m ON m.id=f.message_id "
            "WHERE f.processed=0 ORDER BY f.id"
        )

    def mark_feedback_processed(self, ids: list[int]) -> None:
        with self.lock:
            self.db.executemany("UPDATE feedback SET processed=1 WHERE id=?", [(i,) for i in ids])
            self.db.commit()

    # ------------------------------------------------------ reminders
    def add_reminder(self, text: str, due_at: float) -> int:
        return self._exec(
            "INSERT INTO reminders(text, due_at, created_at) VALUES(?,?,?)", (text, due_at, time.time())
        )

    def due_reminders(self) -> list[dict]:
        return self._q("SELECT * FROM reminders WHERE delivered=0 AND due_at<=? ORDER BY due_at", (time.time(),))

    def pending_reminders(self) -> list[dict]:
        return self._q("SELECT * FROM reminders WHERE delivered=0 ORDER BY due_at")

    def mark_reminder(self, rid: int, delivered: int = 1) -> None:
        self._exec("UPDATE reminders SET delivered=? WHERE id=?", (delivered, rid))
