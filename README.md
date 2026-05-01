# Knowledge Forge

Personal knowledge retention engine. Combines spaced repetition with knowledge graphs to make sure you never forget what you learn.

Tracks papers, techniques, concepts, and lecture notes. Surfaces review items at optimal intervals using spaced repetition algorithms.

## Usage

```bash
# Add a knowledge item
knowledge-forge add --title "Attention Is All You Need" --type paper --tags "nlp,transformer,attention"

# Add from a paper
knowledge-forge add-paper --url "https://arxiv.org/abs/1706.03762"

# Review queue
knowledge-forge review [--limit 10]

# Mark reviewed (easy/medium/hard)
knowledge-forge rate ITEM_ID --difficulty medium

# Search knowledge base
knowledge-forge search "transformer attention"

# Stats
knowledge-forge stats

# Import from Obsidian
knowledge-forge import --source obsidian --path ~/Obsidian/RhendyVault/02_knowledge/
```

## Features

- **Multi-format input:** Papers (arXiv), notes (Obsidian), concepts, techniques
- **Spaced repetition:** SM-2 algorithm for optimal review scheduling
- **Knowledge graph:** Connected concepts with relationship mapping
- **Smart tagging:** Auto-suggest tags from content
- **Review dashboard:** Due items, retention stats, streak tracking
- **Obsidian integration:** Import/export with vault
- **Search:** Full-text + semantic search across all items

## Schema

```json
{
  "id": "uuid",
  "title": "Attention Is All You Need",
  "type": "paper|concept|technique|note|lecture",
  "content": "...",
  "source_url": "https://arxiv.org/abs/1706.03762",
  "tags": ["nlp", "transformer", "attention"],
  "related": ["uuid1", "uuid2"],
  "review": {
    "next_review": "2026-05-05",
    "interval_days": 4,
    "ease_factor": 2.5,
    "repetitions": 2,
    "last_difficulty": "medium"
  },
  "created_at": "2026-05-01",
  "updated_at": "2026-05-01"
}
```

## License

MIT
