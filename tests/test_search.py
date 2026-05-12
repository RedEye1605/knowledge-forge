"""Tests for the search module in Knowledge Forge."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from knowledge_forge import search


def test_search_empty_query():
    """Test that an empty query returns an empty list."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # Create minimal test database
        conn.execute(
            """
            CREATE TABLE items (
                id TEXT PRIMARY KEY,
                title TEXT,
                content TEXT,
                type TEXT,
                tags TEXT,
                updated_at TEXT,
                next_review TEXT
            )
            """
        )
        conn.commit()

        # Search with empty query
        results = search.search(conn, "")
        assert results == []
    finally:
        conn.close()
        Path(db_path).unlink()


def test_search_by_type():
    """Test filtering items by type."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # Create items
        conn.execute(
            """
            INSERT INTO items (id, title, content, type, tags, updated_at, next_review)
            VALUES
                ('test1', 'Test Paper', 'Content', 'paper', '["research"]', '2026-01-01', 'new'),
                ('test2', 'Test Concept', 'Content', 'concept', '["idea"]', '2026-01-01', 'new'),
                ('test3', 'Test Note', 'Content', 'note', '["note"]', '2026-01-01', 'new')
            """
        )
        conn.commit()

        # Filter by type
        results = search.search_by_type(conn, "paper")
        assert len(results) == 1
        assert results[0]["title"] == "Test Paper"
    finally:
        conn.close()
        Path(db_path).unlink()


def test_search_by_tag():
    """Test filtering items by tag."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # Create items with tags
        conn.execute(
            """
            INSERT INTO items (id, title, content, type, tags, updated_at, next_review)
            VALUES
                ('test1', 'Test Paper', 'Content', 'paper', '["research","ml"]', '2026-01-01', 'new'),
                ('test2', 'Test Concept', 'Content', 'concept', '["idea"]', '2026-01-01', 'new'),
                ('test3', 'Test Note', 'Content', 'note', '["note","research"]', '2026-01-01', 'new')
            """
        )
        conn.commit()

        # Filter by tag
        results = search.search_by_tag(conn, "research")
        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert titles == {"Test Paper", "Test Note"}
    finally:
        conn.close()
        Path(db_path).unlink()


def test_fts_escape():
    """Test that special characters are properly escaped."""
    test_cases = [
        ('"hello world"', 'hello world'),
        ("'test'", 'test'),
        ('*asterisk*', 'asterisk'),
        ('"quote" "test" "world"', 'quote test world'),
        ('simple', 'simple'),
    ]

    for input_str, expected in test_cases:
        result = search._fts_escape(input_str)
        assert result == expected, f"Expected {expected!r}, got {result!r}"


def test_row_to_dict_with_tags():
    """Test that tags are properly parsed from JSON strings."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # Create item with JSON tags
        conn.execute(
            """
            INSERT INTO items (id, title, content, type, tags, updated_at, next_review)
            VALUES ('test1', 'Test', 'Content', 'paper', '["tag1","tag2"]', '2026-01-01', 'new')
            """
        )
        conn.commit()

        # Get row and test conversion
        row = conn.execute("SELECT * FROM items WHERE id = 'test1'").fetchone()
        result = search._row_to_dict(row)
        assert result["tags"] == ["tag1", "tag2"]
    finally:
        conn.close()
        Path(db_path).unlink()


def test_row_to_dict_with_empty_tags():
    """Test that empty tag string is handled correctly."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    try:
        # Create item with empty tags
        conn.execute(
            """
            INSERT INTO items (id, title, content, type, tags, updated_at, next_review)
            VALUES ('test1', 'Test', 'Content', 'paper', '[]', '2026-01-01', 'new')
            """
        )
        conn.commit()

        # Get row and test conversion
        row = conn.execute("SELECT * FROM items WHERE id = 'test1'").fetchone()
        result = search._row_to_dict(row)
        assert result["tags"] == []
    finally:
        conn.close()
        Path(db_path).unlink()
