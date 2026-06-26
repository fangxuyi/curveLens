"""
Persistent store for catalyst events.

Events are stored as newline-delimited JSON in:
  data/gold/events/event_date=YYYY-MM-DD/events.jsonl

Each line is one scored CatalystEvent dict.
Deduplication is done by event_id — if an event with the same event_id
already exists in the file, the incoming event is dropped (no overwrite).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional


class CatalystStore:
    def __init__(self, base_path: Path) -> None:
        self.base_path = Path(base_path)

    def _path(self, event_date: date) -> Path:
        return (
            self.base_path
            / "gold"
            / "events"
            / f"event_date={event_date.isoformat()}"
            / "events.jsonl"
        )

    def save(self, events: list[dict], event_date: date) -> int:
        """
        Append events to the JSONL file for event_date.
        Returns the number of newly written events (0 if all were duplicates).
        """
        path = self._path(event_date)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing event_ids
        existing_ids: set[str] = set()
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        obj = json.loads(line)
                        eid = obj.get("event_id")
                        if eid:
                            existing_ids.add(eid)
                    except json.JSONDecodeError:
                        pass

        written = 0
        with path.open("a") as f:
            for event in events:
                eid = event.get("event_id", "")
                if eid and eid in existing_ids:
                    continue
                f.write(json.dumps(event) + "\n")
                if eid:
                    existing_ids.add(eid)
                written += 1

        return written

    def load(self, event_date: date) -> list[dict]:
        """Load all events for a given date."""
        path = self._path(event_date)
        if not path.exists():
            return []
        events = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def load_range(self, start: date, end: date) -> list[dict]:
        """Load events from a date range [start, end] inclusive."""
        from datetime import timedelta
        events = []
        d = start
        while d <= end:
            events.extend(self.load(d))
            d += timedelta(days=1)
        return events
