from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# RawStore
# ---------------------------------------------------------------------------

def test_raw_store_persist_writes_file(raw_store: RawStore):
    content = b"trade_date,settlement\n2024-01-02,72.70\n"
    path, sha256, byte_size = raw_store.persist(
        content=content,
        source_id="test_source",
        filename="test.csv",
        trade_date="2024-01-02",
    )
    assert path.exists()
    assert path.read_bytes() == content
    assert byte_size == len(content)


def test_raw_store_persist_writes_meta_sidecar(raw_store: RawStore):
    content = b"hello"
    path, sha256, _ = raw_store.persist(
        content=content,
        source_id="src",
        filename="data.csv",
        trade_date="2024-01-02",
        source_url="http://example.com",
        http_status=200,
        content_type="text/csv",
    )
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["sha256"] == sha256
    assert meta["byte_size"] == len(content)
    assert meta["source_url"] == "http://example.com"
    assert meta["http_status"] == 200
    assert meta["content_type"] == "text/csv"


def test_raw_store_sha256_correct(raw_store: RawStore):
    content = b"test content for sha"
    path, sha256, _ = raw_store.persist(content=content, source_id="s", filename="f.txt")
    expected = hashlib.sha256(content).hexdigest()
    assert sha256 == expected


def test_raw_store_different_content_different_path(raw_store: RawStore):
    content1 = b"day one"
    content2 = b"day two"
    path1, _, _ = raw_store.persist(content=content1, source_id="s", filename="f1.csv")
    path2, _, _ = raw_store.persist(content=content2, source_id="s", filename="f2.csv")
    assert path1 != path2


# ---------------------------------------------------------------------------
# ManifestDB
# ---------------------------------------------------------------------------

def test_manifest_db_creates_tables(manifest_db: ManifestDB):
    # If init succeeded, tables exist; verify by querying
    count = manifest_db.get_manifest_entry_count()
    assert count == 0


def test_manifest_db_sha256_not_found_initially(manifest_db: ManifestDB):
    assert not manifest_db.sha256_exists("abc123")


def test_manifest_db_sha256_found_after_insert(manifest_db: ManifestDB):
    sha = "a" * 64
    run_id = str(uuid.uuid4())
    manifest_db.start_run(run_id, "test", "2024-01-02")
    manifest_db.insert_manifest_entry({
        "entry_id": str(uuid.uuid4()),
        "source_id": "test",
        "raw_path": "/tmp/x.csv",
        "sha256": sha,
        "byte_size": 100,
        "retrieved_at": NOW,
        "trade_date": "2024-01-02",
        "collection_run_id": run_id,
    })
    assert manifest_db.sha256_exists(sha)


def test_manifest_db_start_and_complete_run(manifest_db: ManifestDB):
    run_id = str(uuid.uuid4())
    manifest_db.start_run(run_id, "test_source", "2024-01-02")
    runs = manifest_db.get_run_history("test_source")
    assert len(runs) == 1
    assert runs[0]["status"] == "running"

    manifest_db.complete_run(run_id, "success", 5, 0, 0, 2)
    runs = manifest_db.get_run_history("test_source")
    assert runs[0]["status"] == "success"
    assert runs[0]["success_count"] == 5
    assert runs[0]["skipped_count"] == 2
    assert runs[0]["completed_at"] is not None


def test_manifest_db_get_run_history_ordered(manifest_db: ManifestDB):
    for i in range(3):
        rid = str(uuid.uuid4())
        manifest_db.start_run(rid, "s", f"2024-01-0{i + 1}")
        manifest_db.complete_run(rid, "success", 1, 0, 0, 0)
    runs = manifest_db.get_run_history("s")
    assert len(runs) == 3
    dates = [r["as_of_date"] for r in runs]
    assert dates == sorted(dates)


def test_manifest_db_entry_count(manifest_db: ManifestDB):
    run_id = str(uuid.uuid4())
    manifest_db.start_run(run_id, "s", "2024-01-02")
    for i in range(3):
        manifest_db.insert_manifest_entry({
            "entry_id": str(uuid.uuid4()),
            "source_id": "s",
            "raw_path": f"/tmp/{i}.csv",
            "sha256": "a" * 63 + str(i),
            "byte_size": 100 + i,
            "retrieved_at": NOW,
            "collection_run_id": run_id,
        })
    assert manifest_db.get_manifest_entry_count() == 3


def test_manifest_db_no_duplicate_sha256(manifest_db: ManifestDB):
    assert not manifest_db.has_duplicate_sha256()
