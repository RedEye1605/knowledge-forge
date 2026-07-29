"""Tests for the search module in Knowledge Forge."""

from __future__ import annotations

import sqlite3

import pytest

from knowledge_forge import db, search


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "knowledge-forge-test.db")
    db.init_db(connection)
    yield connection
    connection.close()


def add_item(conn: sqlite3.Connection, item_id: str, title: str, item_type: str, tags: str) -> None:
    conn.execute(
        """
        INSERT INTO items (id, title, content, type, tags, created_at, updated_at)
        VALUES (?, ?, 'Content', ?, ?, '2026-01-01', '2026-01-01')
        """,
        (item_id, title, item_type, tags),
    )
    conn.commit()


def test_search_empty_query(conn):
    assert search.search(conn, "") == []


def test_search_by_type(conn):
    add_item(conn, "test1", "Test Paper", "paper", '["research"]')
    add_item(conn, "test2", "Test Concept", "concept", '["idea"]')
    add_item(conn, "test3", "Test Note", "note", '["note"]')

    results = search.search_by_type(conn, "paper")
    assert len(results) == 1
    assert results[0]["title"] == "Test Paper"


def test_search_by_tag(conn):
    add_item(conn, "test1", "Test Paper", "paper", '["research","ml"]')
    add_item(conn, "test2", "Test Concept", "concept", '["idea"]')
    add_item(conn, "test3", "Test Note", "note", '["note","research"]')

    results = search.search_by_tag(conn, "research")
    assert {result["title"] for result in results} == {"Test Paper", "Test Note"}


def test_fts_escape_quotes_each_term():
    test_cases = [
        ('"hello world"', '"hello" "world"'),
        ("'test'", '"test"'),
        ("*asterisk*", '"asterisk"'),
        ('"quote" "test" "world"', '"quote" "test" "world"'),
        ("simple", '"simple"'),
    ]

    for input_str, expected in test_cases:
        assert search._fts_escape(input_str) == expected


def test_row_to_dict_with_tags(conn):
    add_item(conn, "test1", "Test", "paper", '["tag1","tag2"]')
    row = conn.execute("SELECT * FROM items WHERE id = 'test1'").fetchone()
    assert search._row_to_dict(row)["tags"] == ["tag1", "tag2"]


def test_row_to_dict_with_empty_tags(conn):
    add_item(conn, "test1", "Test", "paper", "[]")
    row = conn.execute("SELECT * FROM items WHERE id = 'test1'").fetchone()
    assert search._row_to_dict(row)["tags"] == []
