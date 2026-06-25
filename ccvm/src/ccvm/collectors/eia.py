from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore
from .base import CollectionItem, RawPayload

logger = logging.getLogger(__name__)

EIA_BASE_URL = "https://api.eia.gov/v2"


class EIACollector:
    """Fetches weekly crude oil stock data from EIA Open Data APIv2."""

    source_id = "eia_api_v2"

    def __init__(
        self,
        raw_store: RawStore,
        manifest_db: ManifestDB,
        api_key: Optional[str] = None,
    ) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.api_key = api_key or os.environ.get("EIA_API_KEY", "")

    def discover(self, as_of_date: date) -> list[CollectionItem]:
        return [
            CollectionItem(
                source_id=self.source_id,
                trade_date=as_of_date.isoformat(),
                identifier=f"eia_crude_stocks_weekly_{as_of_date.strftime('%Y%m%d')}.json",
            )
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def _fetch_raw(self, api_key: str) -> bytes:
        params = {
            "api_key": api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[product][]": "EPC0",
            "facets[duoarea][]": "NUS",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": "10",
        }
        response = httpx.get(
            f"{EIA_BASE_URL}/petroleum/stoc/wstk/data/",
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content

    def fetch(self, item: CollectionItem) -> RawPayload:
        content = self._fetch_raw(self.api_key)
        return RawPayload(
            content=content,
            filename=item.identifier,
            trade_date=item.trade_date,
            source_url=f"{EIA_BASE_URL}/petroleum/stoc/wstk/data/",
            http_status=200,
            content_type="application/json",
        )

    def collect(self, as_of_date: date) -> dict:
        if not self.api_key:
            logger.warning("EIA_API_KEY not set — skipping EIA collection")
            return {"run_id": None, "status": "skipped", "success": 0, "warning": 0, "failure": 0, "skipped": 1}

        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)

        items = self.discover(as_of_date)
        success = warning = failure = skipped = 0

        for item in items:
            try:
                payload = self.fetch(item)
                sha256 = hashlib.sha256(payload.content).hexdigest()

                if self.manifest_db.sha256_exists(sha256):
                    logger.debug("Skipping %s — identical content already in manifest", item.identifier)
                    skipped += 1
                    continue

                raw_path, sha256_written, byte_size = self.raw_store.persist(
                    content=payload.content,
                    source_id=self.source_id,
                    filename=payload.filename,
                    trade_date=as_of_str,
                    source_url=payload.source_url,
                    http_status=payload.http_status,
                    content_type=payload.content_type,
                )
                self.manifest_db.insert_manifest_entry(
                    {
                        "entry_id": str(uuid.uuid4()),
                        "source_id": self.source_id,
                        "raw_path": str(raw_path),
                        "sha256": sha256_written,
                        "byte_size": byte_size,
                        "retrieved_at": datetime.now(timezone.utc),
                        "trade_date": as_of_str,
                        "source_url": payload.source_url,
                        "http_status": payload.http_status,
                        "content_type": payload.content_type,
                        "collection_run_id": run_id,
                    }
                )
                logger.info("Collected EIA data -> %s", raw_path)
                success += 1

            except Exception as exc:
                logger.error("Failed to collect EIA data: %s", exc)
                failure += 1

        status = "failed" if (failure > 0 and success == 0) else ("warning" if failure > 0 else "success")
        self.manifest_db.complete_run(run_id, status, success, warning, failure, skipped)
        return {
            "run_id": run_id, "status": status,
            "success": success, "warning": warning, "failure": failure, "skipped": skipped,
        }
