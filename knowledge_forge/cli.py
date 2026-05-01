"""CLI for Knowledge Forge — Click + Rich."""

from __future__ import annotations

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


def _conn():
    return get_connection()


def _resolve_item(conn, item_id: str):
    """Resolve an item by full or partial ID. Returns (item_id, item_dict) or (None, None)."""
    item = get_item(conn, item_id)
    if item:
        return item_id, item
    # Try partial ID match
    rows = conn.execute(
        "SELECT id, title FROM items WHERE id LIKE ?", (f"{item_id}%",)
    ).fetchall()
    if len(rows) == 1:
        full_id = rows[0][0]
        return full_id, get_item(conn, full_id)
    elif len(rows) > 1:
        console.print("[yellow]Multiple matches:[/yellow]")
        for r in rows:
            console.print(f"  {r[0]}  {r[1]}")
    return None, None


# ---------------------------------------------------------------------------
# Main group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name="knowledge-forge")
def main():
    """Knowledge Forge — Personal knowledge retention engine with spaced repetition."""
    pass


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@main.command()
def init():
    """Initialize the database and FTS index."""
    conn = _conn()
    init_db(conn)
    console.print(f"[green]✓[/green] Database initialized at [cyan]{DB_PATH}[/cyan]")
    conn.close()


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

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
    _print_item(item)
    console.print(f"\n[green]✓[/green] Added item [cyan]{item['id']}[/cyan]")
    conn.close()


# ---------------------------------------------------------------------------
# add-paper
# ---------------------------------------------------------------------------

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
    _print_item(item)
    if authors:
        console.print(f"\n[dim]Authors: {', '.join(authors[:5])}[/dim]")
    console.print(f"\n[green]✓[/green] Imported paper [cyan]{item['id']}[/cyan]")
    conn.close()


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@main.command("import")
@click.option("--source", required=True, type=click.Choice(["obsidian", "file"]),
              help="Import source type")
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
                console.print(f"[yellow]⚠[/yellow] Skipped '{item_data.get('title', '?')}': {e}")
        console.print(f"[green]✓[/green] Imported {count} items from Obsidian vault")
    elif source == "file":
        item_data = import_file(path)
        item = add_item(conn, **item_data)
        console.print(f"[green]✓[/green] Imported file as [cyan]{item['id']}[/cyan]")

    conn.close()


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@main.command("review")
@click.option("--limit", "-n", default=10, help="Max items to show")
def review_cmd(limit: int):
    """Show items due for review."""
    conn = _conn()
    init_db(conn)
    items = get_due_items(conn, limit=limit)
    if not items:
        console.print("[green]🎉 No items due for review![/green]")
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
                due = due[:10]  # Just the date part
            except (TypeError, IndexError):
                pass
        else:
            due = "[green]new[/green]"
        table.add_row(str(i), item["id"][:8], item["title"][:50], item["type"], str(due))

    console.print(table)
    console.print("\n[dim]Review with: knowledge-forge rate ITEM_ID --difficulty easy|medium|hard[/dim]")
    conn.close()


# ---------------------------------------------------------------------------
# rate
# ---------------------------------------------------------------------------

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
            pass  # _resolve_item already printed matches
        else:
            console.print(f"[red]✗[/red] Item not found: {item_id}")
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

    diff_color = {"easy": "green", "medium": "yellow", "hard": "red"}[difficulty]
    console.print(
        f"[{diff_color}]● {difficulty.upper()}[/{diff_color}] "
        f"[cyan]{item['title']}[/cyan]"
    )
    console.print(
        f"  Next review: [bold]{result['next_review'][:10]}[/bold] "
        f"(interval: {result['interval_days']}d, ease: {result['ease_factor']})"
    )
    conn.close()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

@main.command("search")
@click.argument("query")
@click.option("--limit", "-n", default=15, help="Max results")
def search_cmd(query: str, limit: int):
    """Search the knowledge base."""
    conn = _conn()
    init_db(conn)
    results = search(conn, query, limit=limit)
    if not results:
        console.print(f"[yellow]No results for:[/yellow] {query}")
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

    console.print(table)
    conn.close()


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

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
        console.print("[yellow]No items found.[/yellow]")
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

    console.print(table)
    console.print(f"\n[dim]Showing {len(items)} items[/dim]")
    conn.close()


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@main.command("show")
@click.argument("item_id")
def show_cmd(item_id: str):
    """Show item detail with review history."""
    conn = _conn()
    init_db(conn)
    item_id, item = _resolve_item(conn, item_id)
    if not item:
        console.print(f"[red]✗[/red] Item not found: {item_id}")
        conn.close()
        return

    _print_item(item)

    # Review history
    history = get_review_history(conn, item_id)
    if history:
        console.print("\n[bold]Review History:[/bold]")
        for rev in history[:10]:
            diff_color = {"easy": "green", "medium": "yellow", "hard": "red"}.get(
                rev.get("difficulty", ""), "white"
            )
            console.print(
                f"  [{diff_color}]● {rev['difficulty']}[/{diff_color}] "
                f"{rev['reviewed_at'][:10]} → next: {rev['next_review'][:10]} "
                f"(interval: {rev['interval_days']}d, ease: {rev['ease_factor']})"
            )

    # Relations
    rels = get_relations(conn, item_id)
    if rels["outgoing"] or rels["incoming"]:
        console.print("\n[bold]Relations:[/bold]")
        for r in rels["outgoing"]:
            console.print(f"  → [{r['relation_type']}] {r['target_title']}")
        for r in rels["incoming"]:
            console.print(f"  ← [{r['relation_type']}] {r['source_title']}")

    conn.close()


# ---------------------------------------------------------------------------
# relate
# ---------------------------------------------------------------------------

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
        console.print(f"[red]✗[/red] Source item not found: {source_id}")
        conn.close()
        return
    if not tgt:
        console.print(f"[red]✗[/red] Target item not found: {target_id}")
        conn.close()
        return

    add_relation(conn, source_id, target_id, relation_type)
    console.print(
        f"[green]✓[/green] Linked [cyan]{src['title']}[/cyan] "
        f"→[{relation_type}]→ [cyan]{tgt['title']}[/cyan]"
    )
    conn.close()


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------

@main.command()
def stats():
    """Show knowledge base statistics."""
    conn = _conn()
    init_db(conn)
    s = get_stats(conn)

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

    # Type distribution
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

    # Tag cloud (top 10)
    tag_cloud = get_tag_cloud(conn, limit=10)
    if tag_cloud:
        tags_str = "  ".join(f"[cyan]{tag}[/cyan]({count})" for tag, count in tag_cloud)
        console.print(f"\n[bold]Top Tags:[/bold]\n{tags_str}")

    conn.close()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@main.command("delete")
@click.argument("item_id")
@click.confirmation_option(prompt="Delete this item and all its reviews?")
def delete_cmd(item_id: str):
    """Delete a knowledge item."""
    conn = _conn()
    init_db(conn)
    item_id, item = _resolve_item(conn, item_id)
    if not item:
        console.print(f"[red]✗[/red] Item not found: {item_id}")
        conn.close()
        return
    if delete_item(conn, item_id):
        console.print(f"[green]✓[/green] Deleted: {item['title']}")
    else:
        console.print(f"[red]✗[/red] Failed to delete: {item_id}")
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_item(item: dict[str, Any]):
    """Pretty-print a single item."""
    console.print(Panel(
        f"[bold]{item['title']}[/bold]\n"
        f"[magenta]{item['type']}[/magenta]  "
        f"{' '.join(f'[cyan]#{t}[/cyan]' for t in item.get('tags', []))}\n"
        f"[dim]ID: {item['id']}[/dim]"
        + (f"\n[dim]URL: {item.get('source_url', '')}[/dim]" if item.get("source_url") else ""),
        border_style="blue",
        padding=(0, 1),
    ))
    if item.get("content"):
        content = item["content"][:500]
        if len(item["content"]) > 500:
            content += "..."
        console.print(f"\n{content}")


if __name__ == "__main__":
    main()
