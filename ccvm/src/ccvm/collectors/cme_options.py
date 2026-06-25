from __future__ import annotations

import calendar
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone

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
    if not value or value.strip() in ("-", "0.00", ""):
        return None
    try:
        return float(value.replace(",", "").strip())
    except ValueError:
        return None


def _third_friday(year: int, month: int) -> date:
    """Return the 3rd Friday of the given month."""
    first_day = date(year, month, 1)
    days_until_friday = (4 - first_day.weekday()) % 7
    first_friday_day = 1 + days_until_friday
    return date(year, month, first_friday_day + 14)


def _expiry_month_to_option_info(expiry_str: str) -> tuple[date, str, str]:
    """
    'JAN 24' -> (option_expiry, underlying_contract, underlying_delivery_month)

    LO options on CL: the option with expirationDate 'JAN 24' expires on the
    3rd Friday of January 2024, and its underlying is the NEXT month's futures
    (CLG24 = Feb 2024).
    """
    parts = expiry_str.strip().split()
    exp_month = MONTH_NAME_TO_NUM[parts[0].upper()]
    year_2d = int(parts[1])
    exp_year = 2000 + year_2d

    option_expiry = _third_friday(exp_year, exp_month)

    # Underlying = next calendar month's futures
    und_month = exp_month % 12 + 1
    und_year = exp_year + (1 if exp_month == 12 else 0)
    und_delivery_month = f"{und_year:04d}-{und_month:02d}"
    und_year_2d = str(und_year)[2:]
    und_letter = MONTH_NUM_TO_LETTER[und_month]
    underlying_contract = f"CL{und_letter}{und_year_2d}"

    return option_expiry, underlying_contract, und_delivery_month


class CMEOptionsCollector:
    """Tier-1 public bootstrap: fetches WTI (LO) options daily settlement JSON from CME."""

    source_id = "cme_wti_option_settlement"
    _BASE_URL = (
        "https://www.cmegroup.com/CmeWS/mvc/Settlements/options"
        "/tradeDate/{date}/productCode/LO/code/OOF/pageSize/500/isProtected"
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
            settle = _parse_float(row.get("settle", ""))
            if settle is None:
                continue
            strike = _parse_float(row.get("strike", ""))
            if strike is None or strike <= 0:
                continue
            expiry_str = row.get("expirationDate", "")
            if not expiry_str:
                continue
            call_put_raw = row.get("type", "").upper()
            if call_put_raw == "CALL":
                call_put = "C"
            elif call_put_raw == "PUT":
                call_put = "P"
            else:
                logger.warning("Unknown option type %r — skipping", call_put_raw)
                continue
            try:
                option_expiry, underlying_contract, underlying_delivery_month = (
                    _expiry_month_to_option_info(expiry_str)
                )
            except (KeyError, IndexError, ValueError) as exc:
                logger.warning("Could not parse expirationDate %r: %s — skipping", expiry_str, exc)
                continue

            records.append({
                "trade_date": as_of_date.isoformat(),
                "option_expiry": option_expiry.isoformat(),
                "underlying_contract": underlying_contract,
                "underlying_delivery_month": underlying_delivery_month,
                "strike": strike,
                "call_put": call_put,
                "settlement": settle,
                "volume": _parse_num(row.get("volume")),
                "open_interest": _parse_num(row.get("openInterest")),
                "exercise_style": "American",
                "settlement_style": "Futures",
                "contract_multiplier": 1000,
                "source_id": self.source_id,
            })
        return records

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)
        success = warning = failure = skipped = 0
        filename = f"cme_lo_options_{as_of_date.strftime('%Y%m%d')}.json"

        urls = self.discover(as_of_date)
        for url in urls:
            try:
                content, meta = self.fetch(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.warning("CME returned 404 for options %s — no data for this date", as_of_date)
                    warning += 1
                    self.manifest_db.complete_run(run_id, "warning", 0, warning, 0, 0,
                                                  notes=f"HTTP 404 for {as_of_date}")
                    return {"run_id": run_id, "status": "warning", "success": 0,
                            "warning": 1, "failure": 0, "skipped": 0}
                logger.error("HTTP error fetching CME options for %s: %s", as_of_date, exc)
                failure += 1
                self.manifest_db.complete_run(run_id, "failed", 0, 0, failure, 0, notes=str(exc))
                return {"run_id": run_id, "status": "failed", "success": 0,
                        "warning": 0, "failure": 1, "skipped": 0}
            except httpx.HTTPError as exc:
                logger.error("Network error fetching CME options for %s: %s", as_of_date, exc)
                failure += 1
                self.manifest_db.complete_run(run_id, "failed", 0, 0, failure, 0, notes=str(exc))
                return {"run_id": run_id, "status": "failed", "success": 0,
                        "warning": 0, "failure": 1, "skipped": 0}

            try:
                records = self.parse(content, as_of_date)
            except (json.JSONDecodeError, Exception) as exc:
                logger.error("Failed to parse CME options response: %s", exc)
                self.raw_store.persist(content, self.source_id, filename,
                                       trade_date=as_of_str, **meta)
                warning += 1
                self.manifest_db.complete_run(run_id, "warning", 0, warning, 0, 0,
                                              notes=f"Parse error: {exc}")
                return {"run_id": run_id, "status": "warning", "success": 0,
                        "warning": 1, "failure": 0, "skipped": 0}

            if not records:
                logger.warning("CME returned empty options settlements for %s", as_of_date)
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
                    "Collected CME options for %s: %d records -> %s",
                    as_of_date, len(records), raw_path,
                )
                success += 1

        status = "success" if failure == 0 and warning == 0 else (
            "warning" if success > 0 or skipped > 0 else "failed"
        )
        self.manifest_db.complete_run(run_id, status, success, warning, failure, skipped)
        return {"run_id": run_id, "status": status, "success": success,
                "warning": warning, "failure": failure, "skipped": skipped}
