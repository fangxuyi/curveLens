from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ccvm.collectors.cme_futures import CMEFuturesCollector, _month_str_to_contract
from ccvm.collectors.cme_options import (
    CMEOptionsCollector,
    _expiry_month_to_option_info,
    _third_friday,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cme"
TRADE_DATE = date(2024, 1, 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(content: bytes, status_code: int = 200) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.content = content
    resp.headers = {"content-type": "application/json"}
    resp.raise_for_status = MagicMock()
    return resp


def _futures_fixture_bytes() -> bytes:
    return (FIXTURES / "cme_cl_futures_20240102.json").read_bytes()


def _options_fixture_bytes() -> bytes:
    return (FIXTURES / "cme_lo_options_20240102.json").read_bytes()


# ---------------------------------------------------------------------------
# Unit: parsing helpers
# ---------------------------------------------------------------------------

def test_month_str_to_contract_feb24():
    delivery_month, contract_code, _ = _month_str_to_contract("FEB 24")
    assert delivery_month == "2024-02"
    assert contract_code == "CLG24"


def test_month_str_to_contract_dec24():
    delivery_month, contract_code, _ = _month_str_to_contract("DEC 24")
    assert delivery_month == "2024-12"
    assert contract_code == "CLZ24"


def test_month_str_to_contract_jan25():
    delivery_month, contract_code, _ = _month_str_to_contract("JAN 25")
    assert delivery_month == "2025-01"
    assert contract_code == "CLF25"


def test_third_friday_jan2024():
    # 3rd Friday of Jan 2024: Jan 5 is Friday, +14 = Jan 19
    result = _third_friday(2024, 1)
    assert result == date(2024, 1, 19)
    assert result.weekday() == 4  # Friday


def test_expiry_month_to_option_info_jan24():
    option_expiry, underlying_contract, und_delivery = _expiry_month_to_option_info("JAN 24")
    assert option_expiry == date(2024, 1, 19)
    assert underlying_contract == "CLG24"
    assert und_delivery == "2024-02"


def test_expiry_month_to_option_info_dec24_year_rollover():
    option_expiry, underlying_contract, und_delivery = _expiry_month_to_option_info("DEC 24")
    assert underlying_contract == "CLF25"
    assert und_delivery == "2025-01"


# ---------------------------------------------------------------------------
# CMEFuturesCollector.parse
# ---------------------------------------------------------------------------

def test_cme_futures_parse_good_response(tmp_path):
    collector = CMEFuturesCollector(
        raw_store=MagicMock(), manifest_db=MagicMock()
    )
    records = collector.parse(_futures_fixture_bytes(), TRADE_DATE)
    # TOTAL row has settle="-" and should be skipped; 12 real contracts remain
    assert len(records) == 12
    first = records[0]
    assert first["contract_code"] == "CLG24"
    assert first["delivery_month"] == "2024-02"
    assert first["settlement"] == 72.70
    assert first["exchange"] == "NYMEX"
    assert first["product"] == "CL"
    assert first["trade_date"] == "2024-01-02"


def test_cme_futures_parse_skips_invalid_settle(tmp_path):
    data = {"tradeDate": "01/02/2024", "settlements": [
        {"month": "FEB 24", "settle": "-", "estimatedVolume": "100", "openInterest": "200"},
        {"month": "MAR 24", "settle": "", "estimatedVolume": "50", "openInterest": "100"},
        {"month": "APR 24", "settle": "71.80", "estimatedVolume": "48,230", "openInterest": "98,765"},
    ]}
    collector = CMEFuturesCollector(raw_store=MagicMock(), manifest_db=MagicMock())
    records = collector.parse(json.dumps(data).encode(), TRADE_DATE)
    assert len(records) == 1
    assert records[0]["contract_code"] == "CLJ24"


# ---------------------------------------------------------------------------
# CMEFuturesCollector.collect — mocked HTTP
# ---------------------------------------------------------------------------

def test_cme_futures_collect_writes_raw_and_manifest(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEFuturesCollector(raw_store, manifest_db)

    content = _futures_fixture_bytes()
    mock_resp = _mock_response(content)

    with patch.object(collector, "_get", return_value=mock_resp):
        result = collector.collect(TRADE_DATE)

    assert result["status"] == "success"
    assert result["success"] == 1
    assert result["skipped"] == 0

    entries = manifest_db.get_manifest_entries(source_id="cme_wti_futures_settlement")
    assert len(entries) == 1
    assert entries[0]["trade_date"] == "2024-01-02"

    raw_files = [f for f in tmp_path.glob("raw/cme_wti_futures_settlement/**/*.json")
                 if not f.name.endswith(".meta.json")]
    assert len(raw_files) == 1


def test_cme_futures_collect_idempotent(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEFuturesCollector(raw_store, manifest_db)

    content = _futures_fixture_bytes()
    mock_resp = _mock_response(content)

    with patch.object(collector, "_get", return_value=mock_resp):
        r1 = collector.collect(TRADE_DATE)
    with patch.object(collector, "_get", return_value=mock_resp):
        r2 = collector.collect(TRADE_DATE)
    with patch.object(collector, "_get", return_value=mock_resp):
        r3 = collector.collect(TRADE_DATE)

    assert r1["success"] == 1
    assert r2["skipped"] == 1 and r2["success"] == 0
    assert r3["skipped"] == 1 and r3["success"] == 0

    entries = manifest_db.get_manifest_entries(source_id="cme_wti_futures_settlement")
    assert len(entries) == 1  # never duplicated

    runs = manifest_db.get_run_history(source_id="cme_wti_futures_settlement")
    assert len(runs) == 3


def test_cme_futures_collect_handles_empty_response(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEFuturesCollector(raw_store, manifest_db)

    empty = json.dumps({"tradeDate": "01/02/2024", "settlements": []}).encode()
    mock_resp = _mock_response(empty)

    with patch.object(collector, "_get", return_value=mock_resp):
        result = collector.collect(TRADE_DATE)

    assert result["status"] == "warning"
    assert result["success"] == 0
    entries = manifest_db.get_manifest_entries(source_id="cme_wti_futures_settlement")
    assert len(entries) == 0


def test_cme_futures_collect_handles_404(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEFuturesCollector(raw_store, manifest_db)

    http_err = httpx.HTTPStatusError(
        "404", request=MagicMock(), response=MagicMock(status_code=404)
    )

    with patch.object(collector, "_get", side_effect=http_err):
        result = collector.collect(TRADE_DATE)

    assert result["status"] == "warning"
    assert result["success"] == 0


# ---------------------------------------------------------------------------
# CMEOptionsCollector.parse
# ---------------------------------------------------------------------------

def test_cme_options_parse_good_response():
    collector = CMEOptionsCollector(raw_store=MagicMock(), manifest_db=MagicMock())
    records = collector.parse(_options_fixture_bytes(), TRADE_DATE)

    # settle="-" and settle="0.00" rows are skipped; 22 valid rows remain
    assert len(records) == 22

    calls = [r for r in records if r["call_put"] == "C"]
    puts = [r for r in records if r["call_put"] == "P"]
    assert len(calls) > 0
    assert len(puts) > 0

    jan_records = [r for r in records if r["option_expiry"] == "2024-01-19"]
    assert len(jan_records) > 0
    jan_call = next(r for r in jan_records if r["call_put"] == "C" and r["strike"] == 72.0)
    assert jan_call["underlying_contract"] == "CLG24"
    assert jan_call["underlying_delivery_month"] == "2024-02"
    assert jan_call["settlement"] == 2.10


def test_cme_options_parse_call_put_mapping():
    data = {"tradeDate": "01/02/2024", "settlements": [
        {"expirationDate": "JAN 24", "strike": "70.00", "type": "CALL", "settle": "3.41",
         "volume": "100", "openInterest": "500"},
        {"expirationDate": "JAN 24", "strike": "70.00", "type": "PUT", "settle": "0.71",
         "volume": "80", "openInterest": "400"},
    ]}
    collector = CMEOptionsCollector(raw_store=MagicMock(), manifest_db=MagicMock())
    records = collector.parse(json.dumps(data).encode(), TRADE_DATE)
    assert len(records) == 2
    assert {r["call_put"] for r in records} == {"C", "P"}


def test_cme_options_parse_skips_zero_settle():
    data = {"tradeDate": "01/02/2024", "settlements": [
        {"expirationDate": "JAN 24", "strike": "78.00", "type": "CALL", "settle": "0.00",
         "volume": "0", "openInterest": "120"},
        {"expirationDate": "JAN 24", "strike": "80.00", "type": "CALL", "settle": "-",
         "volume": "0", "openInterest": "45"},
        {"expirationDate": "JAN 24", "strike": "72.00", "type": "CALL", "settle": "2.10",
         "volume": "100", "openInterest": "500"},
    ]}
    collector = CMEOptionsCollector(raw_store=MagicMock(), manifest_db=MagicMock())
    records = collector.parse(json.dumps(data).encode(), TRADE_DATE)
    assert len(records) == 1
    assert records[0]["strike"] == 72.0


# ---------------------------------------------------------------------------
# CMEOptionsCollector.collect — mocked HTTP
# ---------------------------------------------------------------------------

def test_cme_options_collect_writes_raw_and_manifest(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEOptionsCollector(raw_store, manifest_db)

    content = _options_fixture_bytes()
    mock_resp = _mock_response(content)

    with patch.object(collector, "_get", return_value=mock_resp):
        result = collector.collect(TRADE_DATE)

    assert result["status"] == "success"
    assert result["success"] == 1

    entries = manifest_db.get_manifest_entries(source_id="cme_wti_option_settlement")
    assert len(entries) == 1

    raw_files = [f for f in tmp_path.glob("raw/cme_wti_option_settlement/**/*.json")
                 if not f.name.endswith(".meta.json")]
    assert len(raw_files) == 1


def test_cme_options_collect_idempotent(tmp_path):
    from ccvm.storage.manifest_db import ManifestDB
    from ccvm.storage.raw_store import RawStore

    raw_store = RawStore(tmp_path)
    manifest_db = ManifestDB(tmp_path / "manifest.duckdb")
    collector = CMEOptionsCollector(raw_store, manifest_db)

    content = _options_fixture_bytes()
    mock_resp = _mock_response(content)

    with patch.object(collector, "_get", return_value=mock_resp):
        r1 = collector.collect(TRADE_DATE)
    with patch.object(collector, "_get", return_value=mock_resp):
        r2 = collector.collect(TRADE_DATE)
    with patch.object(collector, "_get", return_value=mock_resp):
        r3 = collector.collect(TRADE_DATE)

    assert r1["success"] == 1
    assert r2["skipped"] == 1 and r2["success"] == 0
    assert r3["skipped"] == 1 and r3["success"] == 0

    entries = manifest_db.get_manifest_entries(source_id="cme_wti_option_settlement")
    assert len(entries) == 1

    runs = manifest_db.get_run_history(source_id="cme_wti_option_settlement")
    assert len(runs) == 3
