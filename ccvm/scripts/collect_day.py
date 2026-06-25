#!/usr/bin/env python
"""Run CCVM data collection for a given date.

Usage:
    python scripts/collect_day.py --date 2024-01-02 --source csv_futures
    python scripts/collect_day.py --date 2024-01-02 --source all
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# Add src to path when run directly
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ccvm.collectors.csv_futures import CSVFuturesCollector
from ccvm.collectors.eia import EIACollector
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MANIFEST_DB_PATH = DATA_DIR / "manifests" / "manifest.duckdb"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "futures"


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect CCVM data for a given date")
    parser.add_argument("--date", required=True, help="Trade date (YYYY-MM-DD)")
    parser.add_argument(
        "--source",
        choices=["csv_futures", "eia", "all"],
        default="all",
        help="Which collector(s) to run",
    )
    args = parser.parse_args()

    try:
        as_of = date.fromisoformat(args.date)
    except ValueError:
        print(f"ERROR: invalid date format {args.date!r}; expected YYYY-MM-DD")
        sys.exit(1)

    raw_store = RawStore(DATA_DIR)
    manifest_db = ManifestDB(MANIFEST_DB_PATH)

    results = {}

    if args.source in ("csv_futures", "all"):
        collector = CSVFuturesCollector(FIXTURES_DIR, raw_store, manifest_db)
        result = collector.collect(as_of)
        results["csv_futures"] = result
        print(f"[csv_futures] {result}")

    if args.source in ("eia", "all"):
        collector = EIACollector(raw_store, manifest_db)
        result = collector.collect(as_of)
        results["eia"] = result
        print(f"[eia]         {result}")

    any_failure = any(r.get("status") == "failed" for r in results.values())
    sys.exit(1 if any_failure else 0)


if __name__ == "__main__":
    main()
