"""Statistics and analytics for Knowledge Forge."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import sqlite3


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Get overview statistics."""
    total_items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    now = datetime.now(timezone.utc).isoformat()
    due_reviews = conn.execute(
        """
        SELECT COUNT(*) FROM items i
        LEFT JOIN reviews r ON r.id = (
            SELECT r2.id FROM reviews r2 WHERE r2.item_id = i.id
            ORDER BY r2.reviewed_at DESC LIMIT 1
        )
        WHERE r.next_review <= ? OR r.next_review IS NULL
        """,
        (now,),
    ).fetchone()[0]

    # Retention rate: % of reviews rated easy or medium
    total_reviews = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    if total_reviews > 0:
        good_reviews = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE difficulty IN ('easy', 'medium')"
        ).fetchone()[0]
        retention_rate = round(good_reviews / total_reviews * 100, 1)
    else:
        retention_rate = 0.0

    # Streak: consecutive days with at least one review
    streak = _compute_streak(conn)

    return {
        "total_items": total_items,
        "due_reviews": due_reviews,
        "total_reviews": total_reviews,
        "retention_rate": retention_rate,
        "streak": streak,
    }


def get_review_calendar(conn: sqlite3.Connection, days: int = 30) -> dict[str, int]:
    """Get review counts per day for the last N days."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT DATE(reviewed_at) AS day, COUNT(*) AS count "
        "FROM reviews WHERE reviewed_at >= ? GROUP BY day ORDER BY day",
        (since,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def get_type_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    """Count items by type."""
    rows = conn.execute(
        "SELECT type, COUNT(*) AS count FROM items GROUP BY type ORDER BY count DESC"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def get_tag_cloud(conn: sqlite3.Connection, limit: int = 20) -> list[tuple[str, int]]:
    """Get top tags with their counts."""
    rows = conn.execute("SELECT tags FROM items").fetchall()
    counter: Counter[str] = Counter()
    for (tags_json,) in rows:
        try:
            tags = json.loads(tags_json) if tags_json else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        counter.update(tags)
    return counter.most_common(limit)


def _compute_streak(conn: sqlite3.Connection) -> int:
    """Compute current review streak in days."""
    rows = conn.execute(
        "SELECT DISTINCT DATE(reviewed_at) AS day FROM reviews ORDER BY day DESC"
    ).fetchall()
    if not rows:
        return 0

    streak = 0
    today = datetime.now(timezone.utc).date()
    prev_day: datetime.date | None = None

    for (day_str,) in rows:
        day = datetime.strptime(day_str, "%Y-%m-%d").date()
        if prev_day is None:
            # First iteration: must be today or yesterday
            if day == today or day == today - timedelta(days=1):
                streak = 1
                prev_day = day
            else:
                break
        else:
            if day == prev_day - timedelta(days=1):
                streak += 1
                prev_day = day
            else:
                break

    return streak
