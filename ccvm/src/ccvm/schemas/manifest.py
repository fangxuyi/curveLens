from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str
    raw_path: str
    sha256: str
    byte_size: int
    retrieved_at: datetime
    trade_date: Optional[str] = None
    source_url: Optional[str] = None
    http_status: Optional[int] = None
    content_type: Optional[str] = None
    collection_run_id: str


class CollectionRun(BaseModel):
    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime
    completed_at: Optional[datetime] = None
    source_id: str
    as_of_date: str
    status: Literal["running", "success", "warning", "failed"] = "running"
    success_count: int = 0
    warning_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    notes: Optional[str] = None
