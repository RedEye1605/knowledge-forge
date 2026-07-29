"""Safe, explicit promotion of Knowledge Forge candidates into Obsidian Topics."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from knowledge_forge import db

VAULT_ROOT = Path("/home/rclaw/Obsidian/RhendyVault")
TOPICS_DIR = VAULT_ROOT / "Topics"
EVENT_HOOK = Path("/home/rclaw/.claude/hooks/vault-event.py")


def canonicalize_markdown(content: str) -> bytes:
    content = content.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return (content.rstrip("\n") + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_title(title: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", title).strip().split()).casefold()


def namespaced_id(namespace: str, *parts: str) -> str:
    return hashlib.sha256((namespace + "\x1f" + "\x1f".join(parts)).encode()).hexdigest()


def topic_id(title: str) -> str:
    return namespaced_id("knowledge-forge-topic-v1", normalize_title(title))


def topic_filename(title: str) -> str:
    cleaned = " ".join(unicodedata.normalize("NFKC", title).strip().split())
    if not cleaned or cleaned in {".", ".."} or any(char in cleaned for char in ("/", "\\", "\x00")):
        raise ValueError("Unsafe topic title")
    return f"{cleaned}.md"


def target_path(topics_dir: Path, title: str) -> Path:
    root = topics_dir.resolve()
    target = (root / topic_filename(title)).resolve()
    if target.parent != root:
        raise ValueError("Topic target escapes Topics directory")
    return target


def render_note(
    *, title: str, topic_type: str, description: str, related_notes: list[str],
    stable_topic_id: str, candidate_id: str,
) -> str:
    links = "\n".join(f"- [[{note}]]" for note in related_notes) or "- None yet."
    return f"""---
type: {topic_type}
created: {date.today().isoformat()}
tags: [topic-note, {topic_type}]
knowledge_forge:
  schema_version: 1
  topic_id: {stable_topic_id}
---

# {title}

## Overview

{description.strip()}

## Related

{links}

## Provenance

Managed by Knowledge Forge. Candidate: `{candidate_id}`.
"""


def queue_candidate(
    conn,
    *,
    title: str,
    topic_type: str,
    description: str,
    source_kind: str,
    source_id: str,
    source_path: Path | None = None,
    related_notes: list[str] | None = None,
    correlation_id: str = "",
    provenance: dict[str, Any] | None = None,
    topics_dir: Path = TOPICS_DIR,
) -> dict[str, Any]:
    stable_topic_id = topic_id(title)
    source_bytes = source_path.read_bytes() if source_path else json.dumps(
        {"title": title, "description": description, "source_id": source_id},
        sort_keys=True,
    ).encode()
    source_hash = digest(source_bytes)
    candidate_id = namespaced_id(
        "promotion-candidate-v1", source_kind, source_id, source_hash, stable_topic_id
    )
    proposed = canonicalize_markdown(
        render_note(
            title=title,
            topic_type=topic_type,
            description=description,
            related_notes=related_notes or [],
            stable_topic_id=stable_topic_id,
            candidate_id=candidate_id,
        )
    )
    target = target_path(topics_dir, title)
    expected_hash = digest(target.read_bytes()) if target.exists() else None
    return db.add_promotion_candidate(
        conn,
        id=candidate_id,
        topic_id=stable_topic_id,
        title=title,
        topic_type=topic_type,
        target_path=str(target),
        proposed_content=proposed.decode(),
        content_hash=digest(proposed),
        expected_target_hash=expected_hash,
        source_kind=source_kind,
        source_id=source_id,
        source_path=str(source_path) if source_path else "",
        source_hash=source_hash,
        correlation_id=correlation_id,
        provenance=provenance or {},
    )


def approve(conn, candidate_id: str) -> dict[str, Any]:
    result = db.transition_promotion_candidate(
        conn, candidate_id, expected_status="pending", new_status="approved"
    )
    if result is None:
        raise ValueError("Candidate is missing or not pending")
    return result


def reject(conn, candidate_id: str) -> dict[str, Any]:
    result = db.transition_promotion_candidate(
        conn, candidate_id, expected_status="pending", new_status="rejected"
    )
    if result is None:
        raise ValueError("Candidate is missing or not pending")
    return result


def emit_event(candidate: dict[str, Any], operation: str, content_hash: str) -> None:
    if not EVENT_HOOK.exists():
        return
    metadata = json.dumps(
        {
            "candidate_id": candidate["id"],
            "target_path": candidate["target_path"],
            "source_kind": candidate["source_kind"],
            "source_id": candidate["source_id"],
        },
        separators=(",", ":"),
    )
    subprocess.run(
        [
            str(EVENT_HOOK), "--actor", "knowledge-forge",
            "--entity-type", "topic-note", "--entity-id", candidate["topic_id"],
            "--operation", operation, "--content-hash", content_hash,
            "--correlation-id", candidate.get("correlation_id") or candidate["id"],
            "--event-id", f"promotion:{candidate['id']}:{operation}:{content_hash}",
            "--metadata-json", metadata,
        ],
        check=False,
        timeout=5,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply(conn, candidate_id: str, *, topics_dir: Path = TOPICS_DIR) -> dict[str, Any]:
    candidate = db.get_promotion_candidate(conn, candidate_id)
    if candidate is None:
        raise ValueError("Candidate not found")
    if candidate["status"] == "promoted":
        return candidate
    if candidate["status"] != "approved":
        raise ValueError("Candidate must be approved before apply")

    target = target_path(topics_dir, candidate["title"])
    proposed = canonicalize_markdown(candidate["proposed_content"])
    lock_path = topics_dir / ".knowledge-forge.lock"
    topics_dir.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = target.read_bytes() if target.exists() else None
        current_hash = digest(current) if current is not None else None
        if current_hash == candidate["content_hash"]:
            result = db.transition_promotion_candidate(
                conn, candidate_id, expected_status="approved", new_status="promoted",
                observed_target_hash=current_hash,
            )
            return result or db.get_promotion_candidate(conn, candidate_id)
        if current_hash != candidate["expected_target_hash"]:
            result = db.transition_promotion_candidate(
                conn, candidate_id, expected_status="approved", new_status="conflict",
                observed_target_hash=current_hash,
                conflict_reason="expected_target_hash_mismatch",
            )
            emit_event(candidate, "promotion-conflict", current_hash or "missing")
            return result

        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=topics_dir)
        try:
            with os.fdopen(fd, "wb") as temp:
                temp.write(proposed)
                temp.flush()
                os.fsync(temp.fileno())
            os.chmod(temp_name, target.stat().st_mode & 0o777 if target.exists() else 0o644)
            os.replace(temp_name, target)
            directory_fd = os.open(topics_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

        result = db.transition_promotion_candidate(
            conn, candidate_id, expected_status="approved", new_status="promoted",
            observed_target_hash=candidate["content_hash"],
        )
    emit_event(candidate, "promoted", candidate["content_hash"])
    return result
