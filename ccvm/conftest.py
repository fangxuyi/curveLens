from __future__ import annotations

from pathlib import Path

import pytest

from ccvm.storage.manifest_db import ManifestDB
from ccvm.storage.raw_store import RawStore

FIXTURES_DIR = Path(__file__).parent / "tests" / "fixtures"


@pytest.fixture()
def raw_store(tmp_path: Path) -> RawStore:
    return RawStore(base_path=tmp_path)


@pytest.fixture()
def manifest_db(tmp_path: Path) -> ManifestDB:
    return ManifestDB(db_path=tmp_path / "manifests" / "manifest.duckdb")


@pytest.fixture()
def futures_fixtures_dir() -> Path:
    return FIXTURES_DIR / "futures"


@pytest.fixture()
def options_fixtures_dir() -> Path:
    return FIXTURES_DIR / "options"
