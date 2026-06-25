from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FundamentalObservation(BaseModel):
    series_id: str
    period: str  # e.g. "2024-01-05" or "2024-W01"
    release_timestamp: datetime
    vintage_timestamp: datetime
    value: float
    unit: str
    geography: str
    source_id: str
    retrieved_at: datetime
