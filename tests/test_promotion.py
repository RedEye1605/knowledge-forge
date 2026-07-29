from __future__ import annotations

import hashlib

import pytest

from knowledge_forge import db, promotion


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "knowledge.db")
    db.init_db(connection)
    yield connection
    connection.close()


def queue(conn, topics, source, title="Knowledge Forge"):
    source.write_text("source evidence\n")
    return promotion.queue_candidate(
        conn,
        title=title,
        topic_type="tool",
        description="Durable knowledge promotion engine.",
        source_kind="session-log",
        source_id="session-1",
        source_path=source,
        related_notes=["../06_system/second-brain-sync"],
        topics_dir=topics,
    )


def test_queue_is_idempotent(conn, tmp_path):
    topics = tmp_path / "Topics"
    topics.mkdir()
    first = queue(conn, topics, tmp_path / "source.md")
    second = queue(conn, topics, tmp_path / "source.md")
    assert first["id"] == second["id"]
    assert len(db.list_promotion_candidates(conn)) == 1


def test_topic_filename_rejects_traversal():
    for title in ("../escape", "/absolute", "name/child", "name\\child", ".", ".."):
        with pytest.raises(ValueError):
            promotion.topic_filename(title)


def test_approve_and_apply_creates_topic(conn, tmp_path, monkeypatch):
    topics = tmp_path / "Topics"
    topics.mkdir()
    candidate = queue(conn, topics, tmp_path / "source.md")
    promotion.approve(conn, candidate["id"])
    monkeypatch.setattr(promotion, "EVENT_HOOK", tmp_path / "missing-event-hook")
    result = promotion.apply(conn, candidate["id"], topics_dir=topics)
    target = topics / "Knowledge Forge.md"
    assert result["status"] == "promoted"
    assert target.read_text() == candidate["proposed_content"]
    assert hashlib.sha256(target.read_bytes()).hexdigest() == candidate["content_hash"]
    assert not list(topics.glob(".Knowledge Forge.md.*"))


def test_apply_retry_recovers_after_file_write(conn, tmp_path, monkeypatch):
    topics = tmp_path / "Topics"
    topics.mkdir()
    candidate = queue(conn, topics, tmp_path / "source.md")
    promotion.approve(conn, candidate["id"])
    (topics / "Knowledge Forge.md").write_text(candidate["proposed_content"])
    monkeypatch.setattr(promotion, "EVENT_HOOK", tmp_path / "missing-event-hook")
    assert promotion.apply(conn, candidate["id"], topics_dir=topics)["status"] == "promoted"


def test_human_edit_causes_conflict_without_overwrite(conn, tmp_path, monkeypatch):
    topics = tmp_path / "Topics"
    topics.mkdir()
    target = topics / "Knowledge Forge.md"
    target.write_text("original\n")
    candidate = queue(conn, topics, tmp_path / "source.md")
    promotion.approve(conn, candidate["id"])
    target.write_text("human edit\n")
    monkeypatch.setattr(promotion, "EVENT_HOOK", tmp_path / "missing-event-hook")
    result = promotion.apply(conn, candidate["id"], topics_dir=topics)
    assert result["status"] == "conflict"
    assert result["conflict_reason"] == "expected_target_hash_mismatch"
    assert target.read_text() == "human edit\n"
    assert result["proposed_content"] == candidate["proposed_content"]


def test_rejected_candidate_cannot_apply(conn, tmp_path):
    topics = tmp_path / "Topics"
    topics.mkdir()
    candidate = queue(conn, topics, tmp_path / "source.md")
    promotion.reject(conn, candidate["id"])
    with pytest.raises(ValueError):
        promotion.apply(conn, candidate["id"], topics_dir=topics)
