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
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Create and return a SQLite connection with optimal settings.
    
    Args:
        db_path: Path to database file. If None, uses default DB_PATH.
        
    Returns:
        SQLite connection with row_factory and WAL mode enabled.
    """
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
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
                type TEXT NOT NULL CHECK(type IN ('paper','concept','technique','note','lecture'))
                ,
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

            CREATE TABLE IF NOT EXISTS promotion_candidates (
                id TEXT PRIMARY KEY,
                topic_id TEXT NOT NULL,
                title TEXT NOT NULL,
                topic_type TEXT NOT NULL,
                target_path TEXT NOT NULL,
                proposed_content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                expected_target_hash TEXT,
                source_kind TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_path TEXT DEFAULT '',
                source_hash TEXT NOT NULL,
                correlation_id TEXT DEFAULT '',
                provenance TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','approved','rejected','promoted','conflict')),
                approved_at TEXT,
                promoted_at TEXT,
                observed_target_hash TEXT,
                conflict_reason TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(source_kind, source_id, source_hash, topic_id),
                UNIQUE(target_path, content_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_promotion_candidates_status
                ON promotion_candidates(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_promotion_candidates_topic
                ON promotion_candidates(topic_id, created_at);
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
# Promotion queue
# ---------------------------------------------------------------------------

PROMOTION_STATUSES = {"pending", "approved", "rejected", "promoted", "conflict"}


def _promotion_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    try:
        result["provenance"] = json.loads(result.get("provenance") or "{}")
    except (json.JSONDecodeError, TypeError):
        result["provenance"] = {}
    return result


def add_promotion_candidate(conn: sqlite3.Connection, **candidate: Any) -> dict[str, Any]:
    """Insert an idempotent pending promotion candidate."""
    now = _now()
    conn.execute(
        """
        INSERT INTO promotion_candidates (
            id, topic_id, title, topic_type, target_path, proposed_content,
            content_hash, expected_target_hash, source_kind, source_id,
            source_path, source_hash, correlation_id, provenance,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            candidate["id"], candidate["topic_id"], candidate["title"],
            candidate["topic_type"], candidate["target_path"],
            candidate["proposed_content"], candidate["content_hash"],
            candidate.get("expected_target_hash"), candidate["source_kind"],
            candidate["source_id"], candidate.get("source_path", ""),
            candidate["source_hash"], candidate.get("correlation_id", ""),
            json.dumps(candidate.get("provenance") or {}, sort_keys=True), now, now,
        ),
    )
    conn.commit()
    result = get_promotion_candidate(conn, candidate["id"])
    if result is None:
        raise RuntimeError("promotion candidate insert failed")
    return result


def get_promotion_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM promotion_candidates WHERE id = ?", (candidate_id,)
    ).fetchone()
    return _promotion_row(row)


def list_promotion_candidates(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    if status is not None and status not in PROMOTION_STATUSES:
        raise ValueError(f"Invalid promotion status: {status}")
    if status is None:
        rows = conn.execute(
            "SELECT * FROM promotion_candidates ORDER BY created_at, id LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM promotion_candidates WHERE status = ? "
            "ORDER BY created_at, id LIMIT ?",
            (status, limit),
        ).fetchall()
    return [_promotion_row(row) for row in rows]


def transition_promotion_candidate(
    conn: sqlite3.Connection,
    candidate_id: str,
    *,
    expected_status: str,
    new_status: str,
    observed_target_hash: str | None = None,
    conflict_reason: str = "",
) -> dict[str, Any] | None:
    """Atomically advance one promotion candidate from an expected state."""
    if expected_status not in PROMOTION_STATUSES or new_status not in PROMOTION_STATUSES:
        raise ValueError("Invalid promotion status transition")
    now = _now()
    approved_at = now if new_status == "approved" else None
    promoted_at = now if new_status == "promoted" else None
    cursor = conn.execute(
        """
        UPDATE promotion_candidates
        SET status = ?, approved_at = COALESCE(?, approved_at),
            promoted_at = COALESCE(?, promoted_at), observed_target_hash = ?,
            conflict_reason = ?, updated_at = ?
        WHERE id = ? AND status = ?
        """,
        (
            new_status, approved_at, promoted_at, observed_target_hash,
            conflict_reason, now, candidate_id, expected_status,
        ),
    )
    conn.commit()
    if cursor.rowcount != 1:
        return None
    return get_promotion_candidate(conn, candidate_id)


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
    """Add a new knowledge item to the database.
    
    Args:
        conn: SQLite connection.
        title: Item title.
        type: Item type (paper, concept, technique, note, lecture).
        content: Item content/body.
        source_url: Optional URL source.
        tags: List of tags.
        item_id: Optional custom ID (UUID generated if not provided).
        
    Returns:
        Created item as dictionary.
    """
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
    """Retrieve an item by ID.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        
    Returns:
        Item dictionary or None if not found.
    """
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def update_item(
    conn: sqlite3.Connection,
    item_id: str,
    **fields: Any,
) -> dict[str, Any] | None:
    """Update an item's fields.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        **fields: Fields to update (title, type, content, source_url, tags).
        
    Returns:
        Updated item dictionary or None.
    """
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
    """Delete an item and its related reviews/relations.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        
    Returns:
        True if deleted, False if not found.
    """
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
    """List items with optional filtering.
    
    Args:
        conn: SQLite connection.
        type: Filter by item type.
        tag: Filter by tag.
        limit: Maximum results.
        offset: Pagination offset.
        
    Returns:
        List of item dictionaries.
    """
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
    """Add a spaced repetition review record.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        difficulty: Rating (easy, medium, hard).
        next_review: ISO timestamp for next review.
        interval_days: Current SM-2 interval.
        ease_factor: SM-2 ease factor.
        repetitions: Number of successful reviews.
        
    Returns:
        Created review dictionary.
    """
    review_id = str(uuid.uuid4())
    now = _now()
    conn.execute(
        "INSERT INTO reviews (id, item_id, reviewed_at, difficulty, next_review, "
        "interval_days, ease_factor, repetitions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (review_id, item_id, now, difficulty, next_review, interval_days, ease_factor, repetitions),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    return _row_to_dict(row)


def get_latest_review(conn: sqlite3.Connection, item_id: str) -> dict[str, Any] | None:
    """Get most recent review for an item.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        
    Returns:
        Review dictionary or None.
    """
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
    """Get review history for an item.
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        limit: Maximum results.
        
    Returns:
        List of review dictionaries (newest first).
    """
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
    """Add a relation between two items.
    
    Args:
        conn: SQLite connection.
        source_id: Source item UUID.
        target_id: Target item UUID.
        relation_type: Relation type (related, prerequisite, builds_on, contrasts).
        
    Returns:
        Created relation dictionary.
    """
    rel_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO relations (id, source_id, target_id, relation_type) "
        "VALUES (?, ?, ?, ?)",
        (rel_id, source_id, target_id, relation_type),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM relations WHERE id = ?", (rel_id,)).fetchone()
    return _row_to_dict(row)


def get_relations(
    conn: sqlite3.Connection, item_id: str
) -> list[dict[str, Any]]:
    """Get all relations for an item (incoming and outgoing).
    
    Args:
        conn: SQLite connection.
        item_id: Item UUID.
        
    Returns:
        Dict with 'outgoing' and 'incoming' lists of relations.
    """
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
    return {
        "outgoing": [_row_to_dict(r) for r in rows],
        "incoming": [_row_to_dict(r) for r in back_rows],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert SQLite Row to dict with JSON tags deserialized.
    
    Args:
        row: SQLite Row object.
        
    Returns:
        Dictionary with tags parsed from JSON if present.
    """
    d = dict(row)
    if "tags" in d and isinstance(d["tags"], str):
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d
