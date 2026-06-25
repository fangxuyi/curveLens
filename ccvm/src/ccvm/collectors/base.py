from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable


@dataclass
class CollectionItem:
    source_id: str
    trade_date: str
    identifier: str
    metadata: dict = field(default_factory=dict)


@dataclass
class RawPayload:
    content: bytes
    filename: str
    trade_date: str
    source_url: Optional[str] = None
    http_status: Optional[int] = None
    content_type: Optional[str] = None


@runtime_checkable
class Collector(Protocol):
    source_id: str

    def discover(self, as_of_date: date) -> list[CollectionItem]: ...
    def fetch(self, item: CollectionItem) -> RawPayload: ...
