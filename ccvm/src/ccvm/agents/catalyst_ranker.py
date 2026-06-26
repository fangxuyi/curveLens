"""
Deterministic catalyst relevance ranker.

Scores each CatalystEvent against the current futures curve to determine how
much delivery-window overlap exists. The model's extraction provides direction,
horizon, and magnitude — this module turns those into a numeric score without
further LLM involvement.

Scoring formula (0–100):
  temporal_score      (0–25)   event start within delivery window
  delivery_overlap    (0–25)   affected horizon maps to liquid curve expiries
  magnitude_score     (0–20)   high/medium/low
  source_quality      (0–15)   primary > secondary > other
  direction_clarity   (0–15)   clear direction vs unclear/two_sided

The final score is deterministic given the same inputs.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

_HORIZON_MONTHS: dict[str, int] = {
    "prompt_1m": 1,
    "prompt_3m": 3,
    "6m": 6,
    "12m": 12,
    "structural": 24,
}

_MAGNITUDE_SCORES: dict[str, int] = {
    "high": 20,
    "medium": 12,
    "low": 6,
    "unknown": 3,
}

_SOURCE_SCORES: dict[str, int] = {
    "primary": 15,
    "high_quality_secondary": 10,
    "other": 4,
}

_DIRECTION_SCORES: dict[str, int] = {
    "bullish_supply": 15,
    "bearish_demand": 15,
    "two_sided": 8,
    "unclear": 3,
}


def _months_until(target_ym: str, as_of_date: date) -> Optional[float]:
    """Return months from as_of_date to the first day of target_ym ('YYYY-MM')."""
    try:
        y, m = int(target_ym[:4]), int(target_ym[5:7])
        target = date(y, m, 1)
        delta = (target - as_of_date).days / 30.44
        return delta
    except (ValueError, TypeError):
        return None


def score(
    event: dict,
    as_of_date: date,
    front_delivery_month: Optional[str] = None,  # 'YYYY-MM' of front-month contract
    n_active_months: int = 12,
) -> dict:
    """
    Score a catalyst event dict against the current market.

    Returns the event dict augmented with:
      relevance_score    0–100
      relevance_breakdown  sub-scores
      relevance_rank     assigned later by rank_events()
    """
    # ── Temporal score (event starts within the rolling delivery window) ──
    effective_start = event.get("effective_start")
    temporal = 0
    if effective_start:
        try:
            start_date = date.fromisoformat(effective_start)
            days_ahead = (start_date - as_of_date).days
            if 0 <= days_ahead <= 30:
                temporal = 25        # prompt event: highest urgency
            elif 31 <= days_ahead <= 90:
                temporal = 18
            elif 91 <= days_ahead <= 180:
                temporal = 10
            elif -7 <= days_ahead < 0:
                temporal = 20       # started recently but ongoing
            elif days_ahead < -7:
                temporal = 5        # old news
        except ValueError:
            temporal = 5
    else:
        temporal = 8  # unknown start → partial credit

    # ── Delivery overlap (horizon vs front N expiries) ──
    horizon = event.get("affected_horizon", "prompt_1m")
    horizon_months = _HORIZON_MONTHS.get(horizon, 3)
    delivery_overlap = 0

    if front_delivery_month:
        months_to_front = _months_until(front_delivery_month, as_of_date) or 0
        # Overlap: does the event horizon cover the front month?
        if horizon_months >= max(1, months_to_front):
            delivery_overlap = 25
        elif horizon_months >= max(1, months_to_front) * 0.5:
            delivery_overlap = 15
        else:
            delivery_overlap = 8
    else:
        # No curve info: base on horizon only
        delivery_overlap = 20 if horizon_months <= 3 else (12 if horizon_months <= 6 else 6)

    # ── Magnitude ──
    magnitude = _MAGNITUDE_SCORES.get(event.get("magnitude", "unknown"), 3)

    # ── Source quality ──
    source_qual = _SOURCE_SCORES.get(event.get("source_quality", "other"), 4)

    # ── Direction clarity ──
    direction = _DIRECTION_SCORES.get(event.get("direction", "unclear"), 3)

    total = temporal + delivery_overlap + magnitude + source_qual + direction

    breakdown = {
        "temporal": temporal,
        "delivery_overlap": delivery_overlap,
        "magnitude": magnitude,
        "source_quality": source_qual,
        "direction": direction,
    }

    return {
        **event,
        "relevance_score": total,
        "relevance_breakdown": breakdown,
        "as_of_date": as_of_date.isoformat(),
    }


def rank_events(
    events: list[dict],
    as_of_date: date,
    front_delivery_month: Optional[str] = None,
    n_active_months: int = 12,
) -> list[dict]:
    """
    Score and rank a list of catalyst event dicts.
    Returns list sorted descending by relevance_score, with relevance_rank added.
    """
    scored = [score(e, as_of_date, front_delivery_month, n_active_months) for e in events]
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)
    for rank, e in enumerate(scored, start=1):
        e["relevance_rank"] = rank
    return scored
