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
    """Run one SM-2 spaced repetition algorithm step.

    Implements the SuperMemo-2 algorithm to compute the next review interval
    and update the ease factor based on the user's difficulty rating.

    Args:
        difficulty: User's difficulty rating ('easy', 'medium', or 'hard').
        current_interval: Current interval in days before this review.
        ease_factor: Current ease factor (higher = longer intervals).
        repetitions: Number of successful reviews before this one.

    Returns:
        Dictionary containing:
            - interval_days: Next interval in days.
            - ease_factor: Updated ease factor.
            - repetitions: Updated repetition count.

    Raises:
        ValueError: If difficulty is not 'easy', 'medium', or 'hard'.
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
    """Compute the next review datetime based on interval.

    Args:
        interval_days: Number of days until next review.

    Returns:
        ISO-8601 formatted timestamp string in UTC timezone.
    """
    next_dt = datetime.now(timezone.utc) + timedelta(days=interval_days)
    return next_dt.isoformat()


def process_review(
    item_id: str,
    difficulty: str,
    latest_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a review and compute scheduling parameters for the next review.

    Orchestrates the SM-2 algorithm by extracting current state from the
    latest review (if any) and computing new scheduling parameters.

    Args:
        item_id: The unique identifier of the item being reviewed.
        difficulty: User's difficulty rating ('easy', 'medium', or 'hard').
        latest_review: The most recent review record, if this item has been
            reviewed before. If None, uses default first-review values.

    Returns:
        Dictionary containing all parameters needed to create a new review
        record: interval_days, ease_factor, repetitions, next_review,
        item_id, and difficulty.
    """
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
