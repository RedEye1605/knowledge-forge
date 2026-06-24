"""Importer module — arXiv, Obsidian, and generic file imports."""

from __future__ import annotations

import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def import_arxiv(url_or_id: str) -> dict[str, Any]:
    """Fetch paper metadata from arXiv API.

    Accepts full URL (https://arxiv.org/abs/...) or bare arXiv ID (e.g. 2301.01234).
    Returns dict with title, content (abstract), source_url, and type='paper'.
    """
    arxiv_id = _extract_arxiv_id(url_or_id)
    api_url = f"http://export.arxiv.org/api/query?id_list={arxiv_id}"

    req = urllib.request.Request(api_url, headers={"User-Agent": "KnowledgeForge/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch arXiv paper {arxiv_id}: {e}") from e

    root = ET.fromstring(data)
    entry = root.find("atom:entry", ARXIV_NS)
    if entry is None:
        raise RuntimeError(f"No entry found for arXiv ID {arxiv_id}")

    title_el = entry.find("atom:title", ARXIV_NS)
    summary_el = entry.find("atom:summary", ARXIV_NS)

    title = (
        (title_el.text or "").strip().replace("\n", " ")
        if title_el is not None
        else arxiv_id
    )
    abstract = (
        (summary_el.text or "").strip().replace("\n", " ")
        if summary_el is not None
        else ""
    )

    authors = [
        (a.find("atom:name", ARXIV_NS).text or "").strip()
        for a in entry.findall("atom:author", ARXIV_NS)
        if a.find("atom:name", ARXIV_NS) is not None
    ]

    # Extract categories as tags
    categories = [
        c.get("term", "")
        for c in entry.findall("atom:category", ARXIV_NS)
        if c.get("term")
    ]

    return {
        "title": title,
        "type": "paper",
        "content": abstract,
        "source_url": f"https://arxiv.org/abs/{arxiv_id}",
        "tags": categories[:5],
        "_authors": authors,
    }


def _extract_arxiv_id(url_or_id: str) -> str:
    """Extract arXiv ID from URL or return as-is."""
    url_or_id = url_or_id.strip()
    # Match patterns like 2301.01234 or 2301.01234v2
    match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", url_or_id)
    if match:
        return match.group(1)
    # Old-style IDs: hep-th/9901001
    match = re.search(r"([a-z-]+/\d{7})", url_or_id)
    if match:
        return match.group(1)
    # Assume it's already a bare ID
    return url_or_id


def import_obsidian(path: str | Path) -> list[dict[str, Any]]:
    """Scan Obsidian markdown files, extract title, content, and tags.

    Accepts a directory path. Returns list of item dicts.
    """
    base = Path(path)
    if not base.is_dir():
        raise ValueError(f"Path is not a directory: {base}")

    items: list[dict[str, Any]] = []
    for md_file in sorted(base.rglob("*.md")):
        try:
            item = _parse_markdown(md_file)
            if item:
                items.append(item)
        except Exception:
            continue  # Skip unreadable files
    return items


def _parse_markdown(path: Path) -> dict[str, Any] | None:
    """Parse a single markdown file into an item dict."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return None

    tags: list[str] = []
    title = path.stem  # Default title = filename

    # Parse frontmatter
    content = text
    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(1)
        content = text[fm_match.end():]
        # Extract frontmatter tags
        for line in fm_text.split("\n"):
            line = line.strip()
            if line.startswith("tags:"):
                tag_val = line[5:].strip()
                if tag_val.startswith("["):
                    # YAML list: [tag1, tag2]
                    tags.extend(
                        t.strip().strip("\"'")
                        for t in tag_val.strip("[]").split(",")
                        if t.strip()
                    )
                else:
                    tags.append(tag_val.strip("\"'"))

    # Extract H1 title
    h1_match = re.match(r"^#\s+(.+)$", content, re.MULTILINE)
    if h1_match:
        title = h1_match.group(1).strip()
        # Remove H1 from content
        content = content[:h1_match.start()] + content[h1_match.end():]

    # Extract inline #tags from content
    inline_tags = re.findall(r"(?:^|\s)#([a-zA-Z][\w/-]*)", content)
    tags.extend(inline_tags)

    # Deduplicate tags
    tags = list(dict.fromkeys(tags))

    return {
        "title": title,
        "type": "note",
        "content": content.strip()[:5000],  # Cap content length
        "source_url": str(path),
        "tags": tags[:20],
    }


def import_file(path: str | Path) -> dict[str, Any]:
    """Import a single text file as a note."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"File not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    return {
        "title": p.stem,
        "type": "note",
        "content": text.strip()[:5000],
        "source_url": str(p),
        "tags": [],
    }
