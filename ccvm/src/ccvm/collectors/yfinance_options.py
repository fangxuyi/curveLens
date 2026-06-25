"""
WTI options chain collector via yfinance.

Uses the CL=F (front-month WTI continuous) ticker to retrieve the live options chain.
Settlement prices are last-trade prices, not official CME settlements — suitable for
bootstrap/prototype use only. For production, use CME DataMine or a licensed feed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

from ..storage.manifest_db import ManifestDB
from ..storage.raw_store import RawStore

logger = logging.getLogger(__name__)

MONTH_LETTERS = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}


def _delivery_month_for_expiry(expiry_date: date) -> tuple[str, str]:
    """
    WTI options expire ~20th of month M, underlying futures = delivery month M+1.
    Returns (underlying_contract, underlying_delivery_month).
    """
    und_month = expiry_date.month % 12 + 1
    und_year = expiry_date.year + (1 if expiry_date.month == 12 else 0)
    letter = MONTH_LETTERS[und_month]
    year_2d = str(und_year)[2:]
    return f"CL{letter}{year_2d}", f"{und_year:04d}-{und_month:02d}"


class YFinanceOptionsCollector:
    """
    Tier-1 bootstrap options collector using yfinance CL=F options chain.

    Limitations:
    - Prices are last-trade, not official CME settlement prices.
    - Only covers front 3-5 expiries (liquidity drops off quickly).
    - Open interest may be stale intraday.
    For official settlements use CME DataMine (Tier-2 licensed).
    """

    source_id = "yfinance_wti_options"

    def __init__(self, raw_store: RawStore, manifest_db: ManifestDB,
                 max_expiries: int = 5) -> None:
        self.raw_store = raw_store
        self.manifest_db = manifest_db
        self.max_expiries = max_expiries

    def fetch_and_parse(self, as_of_date: date) -> list[dict]:
        tk = yf.Ticker("CL=F")
        expirations = tk.options
        if not expirations:
            logger.warning("No options expirations available for CL=F")
            return []

        records: list[dict] = []
        for exp_str in expirations[: self.max_expiries]:
            exp_date = date.fromisoformat(exp_str)
            if exp_date <= as_of_date:
                logger.debug("Skipping expired expiry %s", exp_str)
                continue

            underlying_contract, underlying_delivery_month = _delivery_month_for_expiry(exp_date)

            try:
                chain = tk.option_chain(exp_str)
            except Exception as exc:
                logger.warning("Could not fetch chain for %s: %s", exp_str, exc)
                continue

            for cp_label, df in [("C", chain.calls), ("P", chain.puts)]:
                for _, row in df.iterrows():
                    strike = row.get("strike")
                    last = row.get("lastPrice")
                    volume = row.get("volume")
                    oi = row.get("openInterest")

                    if pd.isna(strike) or strike <= 0:
                        continue
                    if pd.isna(last) or last < 0:
                        continue

                    records.append({
                        "trade_date": as_of_date.isoformat(),
                        "option_expiry": exp_str,
                        "underlying_contract": underlying_contract,
                        "underlying_delivery_month": underlying_delivery_month,
                        "strike": round(float(strike), 4),
                        "call_put": cp_label,
                        "settlement": round(float(last), 4),
                        "volume": int(volume) if volume is not None and not pd.isna(volume) else None,
                        "open_interest": int(oi) if oi is not None and not pd.isna(oi) else None,
                        "exercise_style": "American",
                        "settlement_style": "Futures",
                        "contract_multiplier": 1000,
                        "source_id": self.source_id,
                        "price_note": "last_trade_not_official_settlement",
                    })

            logger.info("  Expiry %s: %d calls + %d puts (underlying %s)",
                        exp_str, len(chain.calls), len(chain.puts), underlying_contract)

        return records

    def collect(self, as_of_date: date) -> dict:
        run_id = str(uuid.uuid4())
        as_of_str = as_of_date.isoformat()
        self.manifest_db.start_run(run_id, self.source_id, as_of_str)
        filename = f"yf_cl_options_{as_of_date.strftime('%Y%m%d')}.json"

        try:
            records = self.fetch_and_parse(as_of_date)
        except Exception as exc:
            logger.error("Failed to fetch yfinance options for %s: %s", as_of_date, exc)
            self.manifest_db.complete_run(run_id, "failed", 0, 0, 1, 0, notes=str(exc))
            return {"run_id": run_id, "status": "failed", "success": 0,
                    "warning": 0, "failure": 1, "skipped": 0}

        if not records:
            note = f"No options data for {as_of_date}"
            logger.warning(note)
            self.manifest_db.complete_run(run_id, "warning", 0, 1, 0, 0, notes=note)
            return {"run_id": run_id, "status": "warning", "success": 0,
                    "warning": 1, "failure": 0, "skipped": 0}

        content = json.dumps({
            "source": self.source_id,
            "trade_date": as_of_str,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "record_count": len(records),
            "caveat": "last_trade_prices_not_official_cme_settlements",
            "settlements": records,
        }, indent=2).encode()

        sha256 = hashlib.sha256(content).hexdigest()
        if self.manifest_db.sha256_exists(sha256):
            logger.info("Skipping %s — identical content already stored", filename)
            self.manifest_db.complete_run(run_id, "success", 0, 0, 0, 1)
            return {"run_id": run_id, "status": "success", "success": 0,
                    "warning": 0, "failure": 0, "skipped": 1}

        raw_path, sha256_written, byte_size = self.raw_store.persist(
            content=content,
            source_id=self.source_id,
            filename=filename,
            trade_date=as_of_str,
            source_url="yfinance CL=F options chain",
        )
        self.manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": self.source_id,
            "raw_path": str(raw_path),
            "sha256": sha256_written,
            "byte_size": byte_size,
            "retrieved_at": datetime.now(timezone.utc),
            "trade_date": as_of_str,
            "source_url": "yfinance CL=F options chain",
            "collection_run_id": run_id,
        })
        logger.info("Stored %d option records for %s -> %s", len(records), as_of_date, raw_path)
        self.manifest_db.complete_run(run_id, "success", 1, 0, 0, 0)
        return {"run_id": run_id, "status": "success", "success": 1,
                "warning": 0, "failure": 0, "skipped": 0,
                "records": len(records)}
