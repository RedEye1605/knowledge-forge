"""CLI for Knowledge Forge — Click + Rich."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from . import __version__
from .db import (
    DB_PATH,
    get_connection,
    init_db,
    add_item,
    get_item,
    delete_item,
    list_items,
    add_review,
    get_latest_review,
    get_due_items,
    get_review_history,
    add_relation,
    get_relations,
)
from .scheduler import process_review
from .search import search
from .stats import get_stats, get_type_distribution, get_tag_cloud
from .importer import import_arxiv, import_obsidian, import_file

console = Console()

_json_output = False
_quiet_mode = False


def _out(msg=""):
    if not _quiet_mode:
        console.print(msg)


def _json_out(data):
    if _json_output:
        print(json.dumps(data, indent=2, default=str))
        return True
    return False


def _conn():
    override = os.environ.get("KNOWLEDGE_FORGE_DB")
    return get_connection(Path(override) if override else None)


def _resolve_item(conn, item_id: str):
    item = get_item(conn, item_id)
    if item:
        return item_id, item
    rows = conn.execute(
        "SELECT id, title FROM items WHERE id LIKE ?", (f"{item_id}%",)
    ).fetchall()
    if len(rows) == 1:
        full_id = rows[0][0]
        return full_id, get_item(conn, full_id)
    elif len(rows) > 1:
        _out("[yellow]Multiple matches:[/yellow]")
        for r in rows:
            _out(f"  {r[0]}  {r[1]}")
    return None, None


@click.group()
@click.version_option(__version__, prog_name="knowledge-forge")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-essential output")
def main(json_flag, quiet):
    """Knowledge Forge — Personal knowledge retention engine with spaced repetition."""
    global _json_output, _quiet_mode
    _json_output = json_flag
    _quiet_mode = quiet


@main.command()
def init():
    """Initialize the database and FTS index."""
    conn = _conn()
    init_db(conn)
    if _json_output:
        _json_out({"status": "initialized", "path": str(DB_PATH)})
    else:
        _out(f"[green]✓[/green] Database initialized at [cyan]{DB_PATH}[/cyan]")
    conn.close()


@main.command("add")
@click.option("--title", "-t", required=True, help="Item title")
@click.option("--type", "item_type", required=True,
              type=click.Choice(["paper", "concept", "technique", "note", "lecture"]),
              help="Item type")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--url", default="", help="Source URL")
@click.option("--content", "-c", default="", help="Content/notes text")
def add_cmd(title: str, item_type: str, tags: str, url: str, content: str):
    """Add a new knowledge item."""
    conn = _conn()
    init_db(conn)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if not content:
        content = click.edit("# Write your notes here\n") or ""
    item = add_item(conn, title=title, type=item_type, content=content,
                    source_url=url, tags=tag_list)
    if _json_output:
        _json_out(item)
    else:
        _print_item(item)
        _out(f"\n[green]✓[/green] Added item [cyan]{item['id']}[/cyan]")
    conn.close()


@main.command("add-paper")
@click.option("--url", "-u", required=True, help="arXiv URL or ID")
def add_paper_cmd(url: str):
    """Import a paper from arXiv."""
    conn = _conn()
    init_db(conn)
    with console.status("Fetching from arXiv..."):
        data = import_arxiv(url)
    authors = data.pop("_authors", [])
    item = add_item(conn, **data)
    if _json_output:
        item_data = dict(item)
        item_data["authors"] = authors[:5]
        _json_out(item_data)
    else:
        _print_item(item)
        if authors:
            _out(f"\n[dim]Authors: {', '.join(authors[:5])}[/dim]")
        _out(f"\n[green]✓[/green] Imported paper [cyan]{item['id']}[/cyan]")
    conn.close()


@main.command("import")
@click.option(
    "--source",
    required=True,
    type=click.Choice(["obsidian", "file", "papers"]),
    help="Import source type",
)
@click.option("--path", "-p", required=True, help="Path to directory or file")
def import_cmd(source: str, path: str):
    """Batch import from various sources."""
    conn = _conn()
    init_db(conn)

    if source == "obsidian":
        items = import_obsidian(path)
        count = 0
        for item_data in items:
            try:
                add_item(conn, **item_data)
                count += 1
            except Exception as e:
                _out(f"[yellow]⚠[/yellow] Skipped '{item_data.get('title', '?')}': {e}")
        if _json_output:
            _json_out({"imported": count, "source": "obsidian"})
        else:
            _out(f"[green]✓[/green] Imported {count} items from Obsidian vault")
    elif source == "file":
        item_data = import_file(path)
        item = add_item(conn, **item_data)
        if _json_output:
            _json_out(item)
        else:
            _out(f"[green]✓[/green] Imported file as [cyan]{item['id']}[/cyan]")
    elif source == "papers":
        _import_papers(conn, path)

    conn.close()


def _import_papers(conn, path: str):
    """Import papers from a directory of PDFs/markdown or a bookmarks file."""
    from pathlib import Path
    base = Path(path)
    count = 0

    if base.is_file():
        # Assume a text file with arXiv URLs (one per line)
        for line in base.read_text().splitlines():
            line = line.strip()
            if "arxiv.org" in line or line.startswith("http"):
                try:
                    data = import_arxiv(line)
                    data.pop("_authors", None)
                    add_item(conn, **data)
                    count += 1
                except Exception as e:
                    _out(f"[yellow]⚠[/yellow] Failed: {line[:50]} — {e}")
    elif base.is_dir():
        # Import all markdown files as notes
        for md_file in sorted(base.glob("*.md")):
            item_data = import_file(str(md_file))
            add_item(conn, **item_data)
            count += 1

    if _json_output:
        _json_out({"imported": count, "source": "papers", "path": str(base)})
    else:
        _out(f"[green]✓[/green] Imported {count} papers from [cyan]{base}[/cyan]")


@main.command("due")
@click.option("--limit", "-n", default=10, help="Max items to show")
def due_cmd(limit: int):
    """Quick: what do I need to review today?"""
    conn = _conn()
    init_db(conn)
    items = get_due_items(conn, limit=limit)
    if not items:
        if _json_output:
            _json_out({"due": 0, "items": []})
        else:
            _out("[green]🎉 No items due for review![/green]")
        conn.close()
        return

    if _json_output:
        _json_out({"due": len(items), "items": [
            {"id": i["id"][:8], "title": i["title"], "type": i["type"]}
            for i in items
        ]})
        conn.close()
        return

    _out(f"[bold yellow]📋 {len(items)} items due for review[/bold yellow]")
    for i, item in enumerate(items, 1):
        _out(f"  {i}. [cyan]{item['id'][:8]}[/cyan] {item['title'][:50]} ({item['type']})")
    _out(
        "\n[dim]Review with: knowledge-forge rate ITEM_ID --difficulty easy|medium|hard[/dim]"
    )
    conn.close()


@main.command("review")
@click.option("--limit", "-n", default=10, help="Max items to show")
def review_cmd(limit: int):
    """Show items due for review."""
    conn = _conn()
    init_db(conn)
    items = get_due_items(conn, limit=limit)
    if not items:
        if _json_output:
            _json_out({"due": 0, "items": []})
        else:
            _out("[green]🎉 No items due for review![/green]")
        conn.close()
        return

    if _json_output:
        _json_out(items)
        conn.close()
        return

    table = Table(title="📋 Review Queue", box=box.ROUNDED)
    table.add_column("#", style="dim", width=4)
    table.add_column("ID", style="cyan", width=8)
    table.add_column("Title", style="white")
    table.add_column("Type", style="magenta")
    table.add_column("Due", style="yellow")

    for i, item in enumerate(items, 1):
        due = item.get("next_review", "new")
        if due and due != "new":
            try:
                due = due[:10]
            except (TypeError, IndexError):
                pass
        else:
            due = "[green]new[/green]"
        table.add_row(str(i), item["id"][:8], item["title"][:50], item["type"], str(due))

    _out(table)
    _out(
        "\n[dim]Review with: knowledge-forge rate ITEM_ID --difficulty easy|medium|hard[/dim]"
    )
    conn.close()


@main.command("rate")
@click.argument("item_id")
@click.option("--difficulty", "-d", required=True,
              type=click.Choice(["easy", "medium", "hard"]),
              help="Review difficulty rating")
def rate_cmd(item_id: str, difficulty: str):
    """Rate a reviewed item (easy/medium/hard)."""
    conn = _conn()
    init_db(conn)
    item_id, item = _resolve_item(conn, item_id)
    if not item:
        if item_id is None:
            pass
        else:
            _out(f"[red]✗[/red] Item not found: {item_id}")
        conn.close()
        return

    latest = get_latest_review(conn, item_id)
    result = process_review(item_id, difficulty, latest)
    add_review(
        conn,
        item_id=item_id,
        difficulty=difficulty,
        next_review=result["next_review"],
        interval_days=result["interval_days"],
        ease_factor=result["ease_factor"],
        repetitions=result["repetitions"],
    )

    if _json_output:
        _json_out(
            {
                "item_id": item_id,
                "difficulty": difficulty,
                "next_review": result["next_review"],
                "interval_days": result["interval_days"],
            }
        )
    else:
        diff_color = {"easy": "green", "medium": "yellow", "hard": "red"}[difficulty]
        _out(f"[{diff_color}]● {difficulty.upper()}[/{diff_color}] [cyan]{item['title']}[/cyan]")
        _out(f"  Next review: [bold]{result['next_review'][:10]}[/bold] "
             f"(interval: {result['interval_days']}d, ease: {result['ease_factor']})")
    conn.close()


@main.command("search")
@click.argument("query")
@click.option("--limit", "-n", default=15, help="Max results")
def search_cmd(query: str, limit: int):
    """Search the knowledge base."""
    conn = _conn()
    init_db(conn)
    results = search(conn, query, limit=limit)
    if not results:
        _out(f"[yellow]No results for:[/yellow] {query}")
        conn.close()
        return

    if _json_output:
        _json_out(results)
        conn.close()
        return

    table = Table(title=f"🔍 Results for '{query}'", box=box.ROUNDED)
    table.add_column("ID", style="cyan", width=8)
    table.add_column("Title", style="white")
    table.add_column("Type", style="magenta", width=10)
    table.add_column("Tags", style="dim")

    for item in results:
        tags = ", ".join(item.get("tags", [])[:3])
        table.add_row(item["id"][:8], item["title"][:60], item["type"], tags)

    _out(table)
    conn.close()


@main.command("list")
@click.option("--type", "item_type", default=None,
              type=click.Choice(["paper", "concept", "technique", "note", "lecture"]),
              help="Filter by type")
@click.option("--tag", "-t", default=None, help="Filter by tag")
@click.option("--limit", "-n", default=30, help="Max results")
def list_cmd(item_type: str | None, tag: str | None, limit: int):
    """List knowledge items."""
    conn = _conn()
    init_db(conn)
    items = list_items(conn, type=item_type, tag=tag, limit=limit)
    if not items:
        _out("[yellow]No items found.[/yellow]")
        conn.close()
        return

    if _json_output:
        _json_out(items)
        conn.close()
        return

    table = Table(title="📚 Knowledge Items", box=box.ROUNDED)
    table.add_column("ID", style="cyan", width=8)
    table.add_column("Title", style="white")
    table.add_column("Type", style="magenta", width=10)
    table.add_column("Tags", style="dim")
    table.add_column("Updated", style="yellow", width=10)

    for item in items:
        tags = ", ".join(item.get("tags", [])[:3])
        updated = item.get("updated_at", "")[:10]
        table.add_row(item["id"][:8], item["title"][:50], item["type"], tags, updated)

    _out(table)
    _out(f"\n[dim]Showing {len(items)} items[/dim]")
    conn.close()


@main.command("show")
@click.argument("item_id")
def show_cmd(item_id: str):
    """Show item detail with review history."""
    conn = _conn()
    init_db(conn)
    item_id, item = _resolve_item(conn, item_id)
    if not item:
        _out(f"[red]✗[/red] Item not found: {item_id}")
        conn.close()
        return

    if _json_output:
        _json_out(item)
        conn.close()
        return

    _print_item(item)

    history = get_review_history(conn, item_id)
    if history:
        _out("\n[bold]Review History:[/bold]")
        for rev in history[:10]:
            diff_color = {"easy": "green", "medium": "yellow", "hard": "red"}.get(
                rev.get("difficulty", ""), "white")
            _out(f"  [{diff_color}]● {rev['difficulty']}[/{diff_color}] "
                 f"{rev['reviewed_at'][:10]} → next: {rev['next_review'][:10]} "
                 f"(interval: {rev['interval_days']}d, ease: {rev['ease_factor']})")

    rels = get_relations(conn, item_id)
    if rels["outgoing"] or rels["incoming"]:
        _out("\n[bold]Relations:[/bold]")
        for r in rels["outgoing"]:
            _out(f"  → [{r['relation_type']}] {r['target_title']}")
        for r in rels["incoming"]:
            _out(f"  ← [{r['relation_type']}] {r['source_title']}")

    conn.close()


@main.command("relate")
@click.argument("source_id")
@click.argument("target_id")
@click.option("--type", "relation_type", default="related",
              type=click.Choice(["related", "prerequisite", "builds_on", "contrasts"]),
              help="Relation type")
def relate_cmd(source_id: str, target_id: str, relation_type: str):
    """Create a relation between two items."""
    conn = _conn()
    init_db(conn)
    source_id, src = _resolve_item(conn, source_id)
    target_id, tgt = _resolve_item(conn, target_id)
    if not src:
        _out(f"[red]✗[/red] Source item not found: {source_id}")
        conn.close()
        return
    if not tgt:
        _out(f"[red]✗[/red] Target item not found: {target_id}")
        conn.close()
        return

    add_relation(conn, source_id, target_id, relation_type)
    if _json_output:
        _json_out({"source": source_id, "target": target_id, "type": relation_type})
    else:
        _out(
            f"[green]✓[/green] Linked [cyan]{src['title']}[/cyan] "
            f"→[{relation_type}]→ [cyan]{tgt['title']}[/cyan]",
        )
    conn.close()


@main.command()
def stats():
    """Show knowledge base statistics."""
    conn = _conn()
    init_db(conn)
    s = get_stats(conn)

    if _json_output:
        _json_out(s)
        conn.close()
        return

    panel_content = Text()
    panel_content.append("📊 Knowledge Forge Stats\n\n", style="bold")
    panel_content.append("  Total Items:    ", style="dim")
    panel_content.append(f"{s['total_items']}\n", style="bold white")
    panel_content.append("  Due Reviews:    ", style="dim")
    due_color = "red" if s["due_reviews"] > 5 else "yellow" if s["due_reviews"] > 0 else "green"
    panel_content.append(f"{s['due_reviews']}\n", style=f"bold {due_color}")
    panel_content.append("  Total Reviews:  ", style="dim")
    panel_content.append(f"{s['total_reviews']}\n", style="bold white")
    panel_content.append("  Retention Rate: ", style="dim")
    panel_content.append(f"{s['retention_rate']}%\n", style="bold green")
    panel_content.append("  Streak:         ", style="dim")
    panel_content.append(f"{s['streak']} days 🔥\n", style="bold yellow")

    console.print(Panel(panel_content, border_style="cyan", padding=(1, 2)))

    type_dist = get_type_distribution(conn)
    if type_dist:
        table = Table(title="Type Distribution", box=box.SIMPLE)
        table.add_column("Type", style="magenta")
        table.add_column("Count", style="white", justify="right")
        table.add_column("Bar", style="cyan")
        max_count = max(type_dist.values()) if type_dist else 1
        for t, c in type_dist.items():
            bar = "█" * int(c / max_count * 20)
            table.add_row(t, str(c), bar)
        console.print(table)

    tag_cloud = get_tag_cloud(conn, limit=10)
    if tag_cloud:
        tags_str = "  ".join(f"[cyan]{tag}[/cyan]({count})" for tag, count in tag_cloud)
        console.print(f"\n[bold]Top Tags:[/bold]\n{tags_str}")

    conn.close()


@main.command("delete")
@click.argument("item_id")
@click.confirmation_option(prompt="Delete this item and all its reviews?")
def delete_cmd(item_id: str):
    """Delete a knowledge item."""
    conn = _conn()
    init_db(conn)
    item_id, item = _resolve_item(conn, item_id)
    if not item:
        _out(f"[red]✗[/red] Item not found: {item_id}")
        conn.close()
        return
    if delete_item(conn, item_id):
        if _json_output:
            _json_out({"deleted": item_id})
        else:
            _out(f"[green]✓[/green] Deleted: {item['title']}")
    else:
        _out(f"[red]✗[/red] Failed to delete: {item_id}")
    conn.close()


@main.group("promote")
def promote_group():
    """Queue, approve, and publish durable topic notes."""


@promote_group.command("queue")
@click.option("--title", required=True)
@click.option("--type", "topic_type", required=True)
@click.option("--description", required=True)
@click.option("--source-kind", required=True)
@click.option("--source-id", required=True)
@click.option("--source-path", type=click.Path(path_type=Path))
@click.option("--related-note", multiple=True)
@click.option("--correlation-id", default="")
@click.option("--topics-dir", type=click.Path(path_type=Path), hidden=True)
def promote_queue_cmd(title, topic_type, description, source_kind, source_id,
                      source_path, related_note, correlation_id, topics_dir):
    """Queue a pending topic-note candidate without writing the vault."""
    from . import promotion
    conn = _conn()
    init_db(conn)
    candidate = promotion.queue_candidate(
        conn, title=title, topic_type=topic_type, description=description,
        source_kind=source_kind, source_id=source_id, source_path=source_path,
        related_notes=list(related_note), correlation_id=correlation_id,
        topics_dir=topics_dir or promotion.TOPICS_DIR,
    )
    conn.close()
    if not _json_out(candidate):
        _out(f"[green]Queued[/green] {candidate['id']} — {candidate['title']}")


@promote_group.command("list")
@click.option("--status", type=click.Choice(["pending", "approved", "rejected", "promoted", "conflict"]))
@click.option("--limit", default=50, type=click.IntRange(1, 500))
def promote_list_cmd(status, limit):
    """List promotion candidates."""
    from .db import list_promotion_candidates
    conn = _conn()
    init_db(conn)
    candidates = list_promotion_candidates(conn, status=status, limit=limit)
    conn.close()
    if not _json_out(candidates):
        for candidate in candidates:
            _out(f"{candidate['id'][:12]}  {candidate['status']:9}  {candidate['title']}")


@promote_group.command("show")
@click.argument("candidate_id")
def promote_show_cmd(candidate_id):
    """Show one promotion candidate."""
    from .db import get_promotion_candidate
    conn = _conn()
    init_db(conn)
    candidate = get_promotion_candidate(conn, candidate_id)
    conn.close()
    if candidate is None:
        raise click.ClickException("Candidate not found")
    if not _json_out(candidate):
        _out(json.dumps(candidate, indent=2, default=str))


@promote_group.command("approve")
@click.argument("candidate_id")
def promote_approve_cmd(candidate_id):
    """Approve a pending candidate without writing it."""
    from .promotion import approve
    conn = _conn()
    init_db(conn)
    try:
        candidate = approve(conn, candidate_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        conn.close()
    if not _json_out(candidate):
        _out(f"[green]Approved[/green] {candidate_id}")


@promote_group.command("reject")
@click.argument("candidate_id")
def promote_reject_cmd(candidate_id):
    """Reject and retain a pending candidate."""
    from .promotion import reject
    conn = _conn()
    init_db(conn)
    try:
        candidate = reject(conn, candidate_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        conn.close()
    if not _json_out(candidate):
        _out(f"[yellow]Rejected[/yellow] {candidate_id}")


@promote_group.command("apply")
@click.argument("candidate_id")
@click.option("--topics-dir", type=click.Path(path_type=Path), hidden=True)
def promote_apply_cmd(candidate_id, topics_dir):
    """Atomically apply an approved candidate or quarantine a conflict."""
    from . import promotion
    conn = _conn()
    init_db(conn)
    try:
        candidate = promotion.apply(
            conn, candidate_id, topics_dir=topics_dir or promotion.TOPICS_DIR
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        conn.close()
    if candidate["status"] == "conflict":
        raise click.ClickException("Promotion conflict quarantined; target was not changed")
    if not _json_out(candidate):
        _out(f"[green]Promoted[/green] {candidate['target_path']}")


def _print_item(item: dict[str, Any]):
    console.print(Panel(
        f"[bold]{item['title']}[/bold]\n"
        f"[magenta]{item['type']}[/magenta]  "
        f"{' '.join(f'[cyan]#{t}[/cyan]' for t in item.get('tags', []))}\n"
        f"[dim]ID: {item['id']}[/dim]"
        + (f"\n[dim]URL: {item.get('source_url', '')}[/dim]" if item.get("source_url") else ""),
        border_style="blue", padding=(0, 1),
    ))
    if item.get("content"):
        content = item["content"][:500]
        if len(item["content"]) > 500:
            content += "..."
        console.print(f"\n{content}")


if __name__ == "__main__":
    main()
