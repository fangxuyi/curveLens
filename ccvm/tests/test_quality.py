from __future__ import annotations

import csv
from pathlib import Path

import pytest

from ccvm.validation.quality import check_futures_settlements, check_option_settlements

FIXTURES = Path(__file__).parent / "fixtures"


def load_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


FUTURES_GOOD_1 = FIXTURES / "futures" / "wti_futures_20240102.csv"
FUTURES_GOOD_2 = FIXTURES / "futures" / "wti_futures_20240103.csv"
BAD_DUPLICATE = FIXTURES / "futures" / "bad_duplicate.csv"
BAD_WRONG_DELIVERY = FIXTURES / "futures" / "bad_wrong_delivery.csv"

OPTIONS_GOOD_1 = FIXTURES / "options" / "wti_options_20240102.csv"
OPTIONS_GOOD_2 = FIXTURES / "options" / "wti_options_20240103.csv"
BAD_SPARSE = FIXTURES / "options" / "bad_sparse_options.csv"
BAD_WRONG_UNDERLYING = FIXTURES / "options" / "bad_wrong_underlying.csv"


# ---------------------------------------------------------------------------
# Futures quality checks (Milestone 0 exit criterion)
# ---------------------------------------------------------------------------

def test_good_futures_date1_passes():
    records = load_csv(FUTURES_GOOD_1)
    result = check_futures_settlements(records, "test")
    assert result.status == "PASS", f"Expected PASS, got {result.status}. Failed: {result.checks_failed}"


def test_good_futures_date2_passes():
    records = load_csv(FUTURES_GOOD_2)
    result = check_futures_settlements(records, "test")
    assert result.status == "PASS", f"Expected PASS, got {result.status}. Failed: {result.checks_failed}"


def test_bad_duplicate_fails():
    records = load_csv(BAD_DUPLICATE)
    result = check_futures_settlements(records, "test")
    assert result.status == "FAIL", f"Expected FAIL, got {result.status}"
    assert any("duplicate" in c for c in result.checks_failed)


def test_bad_wrong_delivery_fails():
    records = load_csv(BAD_WRONG_DELIVERY)
    result = check_futures_settlements(records, "test")
    assert result.status == "FAIL", f"Expected FAIL, got {result.status}"
    assert any("mismatch" in c for c in result.checks_failed)


# ---------------------------------------------------------------------------
# Options quality checks (Milestone 0 exit criterion)
# ---------------------------------------------------------------------------

def test_good_options_date1_passes():
    futures = load_csv(FUTURES_GOOD_1)
    options = load_csv(OPTIONS_GOOD_1)
    result = check_option_settlements(options, futures, "test")
    assert result.status == "PASS", f"Expected PASS, got {result.status}. Failed: {result.checks_failed}, Warned: {result.checks_warned}"


def test_good_options_date2_passes():
    futures = load_csv(FUTURES_GOOD_2)
    options = load_csv(OPTIONS_GOOD_2)
    result = check_option_settlements(options, futures, "test")
    assert result.status == "PASS", f"Expected PASS, got {result.status}. Failed: {result.checks_failed}"


def test_sparse_options_warns():
    futures = load_csv(FUTURES_GOOD_1)
    options = load_csv(BAD_SPARSE)
    result = check_option_settlements(options, futures, "test")
    assert result.status == "WARN", f"Expected WARN, got {result.status}. Failed: {result.checks_failed}"
    assert any("sparse" in c or "strikes" in c for c in result.checks_warned)


def test_wrong_underlying_fails():
    futures = load_csv(FUTURES_GOOD_1)
    options = load_csv(BAD_WRONG_UNDERLYING)
    result = check_option_settlements(options, futures, "test")
    assert result.status == "FAIL", f"Expected FAIL, got {result.status}"
    assert any("underlying" in c for c in result.checks_failed)
