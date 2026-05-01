"""SM-2 spaced repetition scheduler for Knowledge Forge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


MIN_EASE_FACTOR = 1.3
DEFAULT_EASE_FACTOR = 2.5
DEFAULT_INTERVAL = 1.0


def sm2(
    difficulty: str,
    current_interval: float = DEFAULT_INTERVAL,
    ease_factor: float = DEFAULT_EASE_FACTOR,
    repetitions: int = 0,
) -> dict[str, float | int]:
    """Run one SM-2 step and return new scheduling parameters.

    Returns dict with: interval_days, ease_factor, repetitions
    """
    if difficulty == "easy":
        ease_factor = ease_factor + 0.15
        repetitions += 1
        if repetitions <= 1:
            interval = 1.0
        else:
            interval = current_interval * ease_factor
    elif difficulty == "medium":
        repetitions += 1
        if repetitions <= 1:
            interval = 1.0
        elif repetitions == 2:
            interval = 6.0
        else:
            interval = current_interval * ease_factor
    elif difficulty == "hard":
        interval = 1.0
        ease_factor = max(MIN_EASE_FACTOR, ease_factor - 0.2)
        repetitions = 0
    else:
        raise ValueError(f"Invalid difficulty: {difficulty}. Must be easy, medium, or hard.")

    return {
        "interval_days": round(interval, 2),
        "ease_factor": round(ease_factor, 2),
        "repetitions": repetitions,
    }


def compute_next_review(interval_days: float) -> str:
    """Compute the next review datetime as ISO-8601 string."""
    next_dt = datetime.now(timezone.utc) + timedelta(days=interval_days)
    return next_dt.isoformat()


def process_review(
    item_id: str,
    difficulty: str,
    latest_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a review for an item. Returns parameters for new review record."""
    if latest_review is None:
        current_interval = DEFAULT_INTERVAL
        ease_factor = DEFAULT_EASE_FACTOR
        repetitions = 0
    else:
        current_interval = latest_review.get("interval_days", DEFAULT_INTERVAL)
        ease_factor = latest_review.get("ease_factor", DEFAULT_EASE_FACTOR)
        repetitions = latest_review.get("repetitions", 0)

    result = sm2(difficulty, current_interval, ease_factor, repetitions)
    result["next_review"] = compute_next_review(result["interval_days"])
    result["item_id"] = item_id
    result["difficulty"] = difficulty
    return result
