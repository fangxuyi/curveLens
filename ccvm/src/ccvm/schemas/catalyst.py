from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CatalystEvent(BaseModel):
    event_id: str
    event_type: Literal[
        "inventory_release", "outage", "opec", "sanctions",
        "refinery", "weather", "macro_demand", "other"
    ]
    title: str
    published_at: datetime
    effective_start: Optional[str] = None
    effective_end: Optional[str] = None
    commodity: str = "crude_oil"
    region: Optional[str] = None
    direction: Literal[
        "bullish_supply", "bearish_demand", "two_sided", "unclear"
    ] = "unclear"
    magnitude: Literal["low", "medium", "high", "unknown"] = "unknown"
    affected_horizon: Optional[Literal[
        "prompt_1m", "prompt_3m", "6m", "12m", "structural"
    ]] = None
    source_quality: Literal["primary", "high_quality_secondary", "other"] = "other"
    source_id: str
    evidence: List[str] = Field(default_factory=list)

    @classmethod
    def make_event_id(cls, event_type: str, title: str, published_at: str, source_id: str) -> str:
        payload = json.dumps(
            {"event_type": event_type, "title": title, "published_at": published_at, "source_id": source_id},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]
