"""Full-text search using SQLite FTS5 for Knowledge Forge."""

from __future__ import annotations

import sqlite3
from typing import Any


def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Full-text search across items using FTS5.

    Returns ranked results with snippet highlighting.
    """
    if not query.strip():
        return []

    # Escape special FTS5 characters
    safe_query = _fts_escape(query)

    try:
        rows = conn.execute(
            """
            SELECT i.*, rank
            FROM items_fts f
            JOIN items i ON i.rowid = f.rowid
            WHERE items_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (safe_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # FTS5 not available — fall back to LIKE
        return _fallback_search(conn, query, limit)

    return [_row_to_dict(r) for r in rows]


def search_by_tag(conn: sqlite3.Connection, tag: str, limit: int = 20) -> list[dict[str, Any]]:
    """Filter items by tag."""
    rows = conn.execute(
        "SELECT * FROM items WHERE tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
        (f'%"{tag}"%', limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def search_by_type(conn: sqlite3.Connection, item_type: str, limit: int = 20) -> list[dict[str, Any]]:
    """Filter items by type."""
    rows = conn.execute(
        "SELECT * FROM items WHERE type = ? ORDER BY updated_at DESC LIMIT ?",
        (item_type, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _fts_escape(query: str) -> str:
    """Escape query for FTS5 MATCH. Wrap terms in quotes for safety."""
    # Remove special FTS5 operators, split into words, rejoin as AND
    terms = query.replace('"', "").replace("'", "").replace("*", "").split()
    return " ".join(f'"{t}"' for t in terms if t)


def _fallback_search(conn: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
    """Fallback LIKE search when FTS5 is unavailable."""
    pattern = f"%{query}%"
    rows = conn.execute(
        "SELECT * FROM items WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? "
        "ORDER BY updated_at DESC LIMIT ?",
        (pattern, pattern, pattern, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    if "tags" in d and isinstance(d["tags"], str):
        import json
        try:
            d["tags"] = json.loads(d["tags"])
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
    return d
