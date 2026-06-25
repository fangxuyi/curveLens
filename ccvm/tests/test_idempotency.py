from __future__ import annotations

"""
Milestone 1 exit criterion: three consecutive collection runs preserve
lineage without duplicates.
"""

from datetime import date
from pathlib import Path

from ccvm.collectors.csv_futures import CSVFuturesCollector
from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore


def test_three_consecutive_runs_no_duplicates(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)
    as_of = date(2024, 1, 2)

    # Run 1 — should collect 1 file
    r1 = collector.collect(as_of)
    assert r1["status"] == "success"
    assert r1["success"] == 1
    assert r1["skipped"] == 0
    count_after_run1 = manifest_db.get_manifest_entry_count()
    assert count_after_run1 == 1

    # Run 2 — same file, same SHA → must be skipped
    r2 = collector.collect(as_of)
    assert r2["skipped"] == 1
    assert r2["success"] == 0
    count_after_run2 = manifest_db.get_manifest_entry_count()
    assert count_after_run2 == count_after_run1, (
        f"Duplicate entry created on run 2: {count_after_run2} != {count_after_run1}"
    )

    # Run 3 — same file, same SHA → must be skipped again
    r3 = collector.collect(as_of)
    assert r3["skipped"] == 1
    assert r3["success"] == 0
    count_after_run3 = manifest_db.get_manifest_entry_count()
    assert count_after_run3 == count_after_run1, (
        f"Duplicate entry created on run 3: {count_after_run3} != {count_after_run1}"
    )

    # Three run records exist in collection_runs
    runs = manifest_db.get_run_history(collector.source_id)
    assert len(runs) == 3

    # No duplicate SHA-256 values in manifest
    assert not manifest_db.has_duplicate_sha256()


def test_two_different_dates_both_collected(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)

    r1 = collector.collect(date(2024, 1, 2))
    r2 = collector.collect(date(2024, 1, 3))
    assert r1["success"] == 1
    assert r2["success"] == 1
    assert manifest_db.get_manifest_entry_count() == 2
    assert not manifest_db.has_duplicate_sha256()


def test_reruns_of_multiple_dates_still_idempotent(
    futures_fixtures_dir: Path,
    raw_store: RawStore,
    manifest_db: ManifestDB,
):
    collector = CSVFuturesCollector(futures_fixtures_dir, raw_store, manifest_db)

    # Collect both dates
    collector.collect(date(2024, 1, 2))
    collector.collect(date(2024, 1, 3))
    assert manifest_db.get_manifest_entry_count() == 2

    # Re-run both three times each
    for _ in range(3):
        r1 = collector.collect(date(2024, 1, 2))
        r2 = collector.collect(date(2024, 1, 3))
        assert r1["skipped"] == 1
        assert r2["skipped"] == 1

    # Entry count unchanged
    assert manifest_db.get_manifest_entry_count() == 2
    # Six run_history entries for the re-runs plus the initial 2 = 8 total
    runs = manifest_db.get_run_history(collector.source_id)
    assert len(runs) == 8
    assert not manifest_db.has_duplicate_sha256()
