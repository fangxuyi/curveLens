from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

MONTH_NAME_TO_NUM = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MONTH_NUM_TO_LETTER = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cmegroup.com/",
    "Origin": "https://www.cmegroup.com",
}


def _parse_num(value: str | None) -> int | None:
    if not value or value.strip() in ("-", ""):
        return None
    try:
        return int(value.replace(",", "").strip())
    except ValueError:
        return None


def _parse_float(value: str | None) -> float | None:
    if not value or value.strip() in ("-", ""):
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _month_str_to_contract(month_str: str) -> tuple[str, str, str]:
    """'FEB 24' -> (delivery_month='2024-02', contract_code='CLG24', year_2digit='24')"""
    parts = month_str.strip().split()
    month_num = MONTH_NAME_TO_NUM[parts[0].upper()]
    year_2d = int(parts[1])
    year_4d = 2000 + year_2d
    delivery_month = f"{year_4d:04d}-{month_num:02d}"
    letter = MONTH_NUM_TO_LETTER[month_num]
    contract_code = f"CL{letter}{parts[1]}"
    return delivery_month, contract_code, parts[1]


class CMEFuturesCollector:
    """Tier-1 public bootstrap: fetches WTI (CL) daily settlement JSON from CME."""

    source_id = "cme_wti_futures_settlement"
    _BASE_URL = (
        "https://www.cmegroup.com/CmeWS/mvc/Settlements/futures"
        "/tradeDate/{date}/productCode/CL/code/fut/pageSize/50/isProtected"
    )

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db

    def discover(self, as_of_date: date) -> list[str]:
        return [self._BASE_URL.format(date=as_of_date.strftime("%Y%m%d"))]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    def _get(self, url: str) -> httpx.Response:
        with httpx.Client(timeout=30, headers=_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return resp

    def fetch(self, url: str) -> tuple[bytes, dict]:
        resp = self._get(url)
        return resp.content, {
            "http_status": resp.status_code,
            "content_type": resp.headers.get("content-type", "application/json"),
            "source_url": url,
        }

    def parse(self, content: bytes, as_of_date: date) -> list[dict]:
        data = json.loads(content)
        settlements = data.get("settlements") or data.get("data", [])
        records: list[dict] = []
        for row in settlements:
            settle_raw = row.get("settle", "")
            settle = _parse_float(settle_raw)
            if settle is None or settle <= 0:
                continue
            month_str = row.get("month", "")
            if not month_str:
                continue
            try:
                delivery_month, contract_code, _ = _month_str_to_contract(month_str)
            except (KeyError, IndexError, ValueError):
                logger.warning("Could not parse month field %r — skipping", month_str)
                continue
            records.append({
                "trade_date": as_of_date.isoformat(),
                "exchange": "NYMEX",
                "product": "CL",
                "contract_code": contract_code,
                "delivery_month": delivery_month,
                "settlement": settle,
                "volume": _parse_num(row.get("estimatedVolume")),
                "open_interest": _parse_num(row.get("openInterest")),
                "currency": "USD",
                "price_unit": "USD/BBL",
                "source_id": self.source_id,
            })
        return records

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)
        success = warning = failure = skipped = 0
        filename = f"cme_cl_futures_{as_of_date.strftime('%Y%m%d')}.json"

        urls = self.discover(as_of_date)
        for url in urls:
            try:
                content, meta = self.fetch(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("CME returned 404 for %s — no data for this date", as_of_date)
                    warning += 1
                    self.manifest_db.complete_run(
                        run_id, "warning", 0, warning, 0, 0,
                        notes=f"HTTP 404 for {as_of_date}",
                    )
                    return {"run_id": run_id, "status": "warning", "success": 0,
                            "warning": 1, "failure": 0, "skipped": 0}
                logger.error("HTTP error fetching CME futures for %s: %s", as_of_date, exc)
                failure += 1
                self.manifest_db.complete_run(run_id, "failed", 0, 0, failure, 0,
                                              notes=str(exc))
                return {"run_id": run_id, "status": "failed", "success": 0,
                        "warning": 0, "failure": 1, "skipped": 0}
            except httpx.HTTPError as exc:
                logger.error("Network error fetching CME futures for %s: %s", as_of_date, exc)
                failure += 1
                self.manifest_db.complete_run(run_id, "failed", 0, 0, failure, 0,
                                              notes=str(exc))
                return {"run_id": run_id, "status": "failed", "success": 0,
                        "warning": 0, "failure": 1, "skipped": 0}

            # Validate we got usable records
            try:
                records = self.parse(content, as_of_date)
            except (json.JSONDecodeError, Exception) as exc:
                logger.error("Failed to parse CME futures response: %s", exc)
                # Still persist the raw payload for investigation
                self.raw_store.persist(content, self.source_id, filename,
                                       trade_date=as_of_str, **meta)
                warning += 1
                self.manifest_db.complete_run(run_id, "warning", 0, warning, 0, 0,
                                              notes=f"Parse error: {exc}")
                return {"run_id": run_id, "status": "warning", "success": 0,
                        "warning": 1, "failure": 0, "skipped": 0}

            if not records:
                logger.warning("CME returned empty settlements for %s", as_of_date)
                warning += 1
                self.manifest_db.complete_run(run_id, "warning", 0, warning, 0, 0,
                                              notes="Empty settlements list")
                return {"run_id": run_id, "status": "warning", "success": 0,
                        "warning": 1, "failure": 0, "skipped": 0}

            sha256 = hashlib.sha256(content).hexdigest()
            if self.manifest_db.sha256_exists(sha256):
                logger.debug("Skipping %s — identical content already in manifest", filename)
                skipped += 1
            else:
                raw_path, sha256_written, byte_size = self.raw_store.persist(
                    content=content,
                    source_id=self.source_id,
                    filename=filename,
                    trade_date=as_of_str,
                    source_url=meta["source_url"],
                    http_status=meta["http_status"],
                    content_type=meta["content_type"],
                )
                self.manifest_db.insert_manifest_entry({
                    "entry_id": str(uuid.uuid4()),
                    "source_id": self.source_id,
                    "raw_path": str(raw_path),
                    "sha256": sha256_written,
                    "byte_size": byte_size,
                    "retrieved_at": datetime.now(timezone.utc),
                    "trade_date": as_of_str,
                    "source_url": meta["source_url"],
                    "http_status": meta["http_status"],
                    "content_type": meta["content_type"],
                    "collection_run_id": run_id,
                })
                logger.info(
                    "Collected CME futures for %s: %d contracts -> %s",
                    as_of_date, len(records), raw_path,
                )
                success += 1

        status = "success" if failure == 0 and warning == 0 else (
            "warning" if success > 0 or skipped > 0 else "failed"
        )
        self.manifest_db.complete_run(run_id, status, success, warning, failure, skipped)
        return {"run_id": run_id, "status": status, "success": success,
                "warning": warning, "failure": failure, "skipped": skipped}
