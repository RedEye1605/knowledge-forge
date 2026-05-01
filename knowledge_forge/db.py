"""Database layer for Knowledge Forge — SQLite with FTS5."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".openclaw" / "knowledgeforge.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Create tables and FTS index. Returns the connection."""
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('paper','concept','technique','note','lecture')),
                content TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                reviewed_at TEXT NOT NULL,
                difficulty TEXT NOT NULL CHECK(difficulty IN ('easy','medium','hard')),
                next_review TEXT NOT NULL,
                interval_days REAL NOT NULL DEFAULT 1.0,
                ease_factor REAL NOT NULL DEFAULT 2.5,
                repetitions INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                target_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                relation_type TEXT NOT NULL DEFAULT 'related'
                    CHECK(relation_type IN ('related','prerequisite','builds_on','contrasts'))
            );

            CREATE INDEX IF NOT EXISTS idx_reviews_item_id ON reviews(item_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_next_review ON reviews(next_review);
            CREATE INDEX IF NOT EXISTS idx_items_type ON items(type);
            CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source_id);
            CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target_id);
            """
        )

        # FTS5 virtual table — try to create, ignore if exists
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5("
                "title, content, tags, content='items', content_rowid='rowid'"
                ")"
            )
            # Triggers to keep FTS in sync
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                    INSERT INTO items_fts(rowid, title, content, tags)
                    VALUES (new.rowid, new.title, new.content, new.tags);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, content, tags)
                    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, content, tags)
                    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
                    INSERT INTO items_fts(rowid, title, content, tags)
                    VALUES (new.rowid, new.title, new.content, new.tags);
                END
                """
            )
        except sqlite3.OperationalError:
            pass  # FTS5 not available or already exists

        conn.commit()
        return conn
    finally:
        if close:
            conn.close()


# ---------------------------------------------------------------------------
# Item CRUD
# ---------------------------------------------------------------------------

def add_item(
    conn: sqlite3.Connection,
    title: str,
    type: str,
    content: str = "",
    source_url: str = "",
    tags: list[str] | None = None,
    item_id: str | None = None,
) -> dict[str, Any]:
    now = _now()
    item_id = item_id or str(uuid.uuid4())
    tags_json = json.dumps(tags or [])
    conn.execute(
        "INSERT INTO items (id, title, type, content, source_url, tags, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, title, type, content, source_url, tags_json, now, now),
    )
    conn.commit()
    return get_item(conn, item_id)


def get_item(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def update_item(
    conn: sqlite3.Connection,
    item_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    allowed = {"title", "type", "content", "source_url", "tags"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_item(conn, item_id)
    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [item_id]
    conn.execute(f"UPDATE items SET {set_clause} WHERE id = ?", values)
    conn.commit()
    return get_item(conn, item_id)


def delete_item(conn: sqlite3.Connection, item_id: str) -> bool:
    cursor = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_items(
    conn: sqlite3.Connection,
    type: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM items"
    conditions: list[str] = []
    params: list[Any] = []
    if type:
        conditions.append("type = ?")
        params.append(type)
    if tag:
        conditions.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def add_review(
    conn: sqlite3.Connection,
    item_id: str,
    difficulty: str,
    next_review: str,
    interval_days: float,
    ease_factor: float,
    repetitions: int,
) -> dict[str, Any]:
    review_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO reviews (id, item_id, reviewed_at, difficulty, next_review, "
        "interval_days, ease_factor, repetitions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (review_id, item_id, now, difficulty, next_review, interval_days, ease_factor, repetitions),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    return _row_to_dict(row)


def get_latest_review(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM reviews WHERE item_id = ? ORDER BY reviewed_at DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_due_items(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    """Get items that are due for review (next_review <= now)."""
    now = _now()
    rows = conn.execute(
        """
        SELECT i.*, r.next_review, r.difficulty AS last_difficulty,
               r.interval_days, r.ease_factor, r.repetitions
        FROM items i
        LEFT JOIN reviews r ON r.id = (
            SELECT r2.id FROM reviews r2 WHERE r2.item_id = i.id
            ORDER BY r2.reviewed_at DESC LIMIT 1
        )
        WHERE r.next_review <= ? OR r.next_review IS NULL
        ORDER BY r.next_review ASC
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_review_history(
    conn: sqlite3.Connection, item_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM reviews WHERE item_id = ? ORDER BY reviewed_at DESC LIMIT ?",
        (item_id, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

def add_relation(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    relation_type: str = "related",
) -> dict[str, Any]:
    rel_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO relations (id, source_id, target_id, relation_type) VALUES (?, ?, ?, ?)",
        (rel_id, source_id, target_id, relation_type),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM relations WHERE id = ?", (rel_id,)).fetchone()
    return _row_to_dict(row)


def get_relations(
    conn: sqlite3.Connection, item_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT r.*, i.title AS target_title FROM relations r "
        "JOIN items i ON r.target_id = i.id "
        "WHERE r.source_id = ?",
        (item_id,),
    ).fetchall()
    back_rows = conn.execute(
        "SELECT r.*, i.title AS source_title FROM relations r "
        "JOIN items i ON r.source_id = i.id "
        "WHERE r.target_id = ?",
        (item_id,),
    ).fetchall()
    return {"outgoing": [_row_to_dict(r) for r in rows], "incoming": [_row_to_dict(r) for r in back_rows]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d
