"""
Futures curve analytics (gold layer features).

Computed from a silver futures PyArrow table for a single trade date.

Features per contract:
  - return_1d             daily return vs prior_settle (if provided)
  - spread_to_next        price difference to the next-month contract
  - butterfly             2×mid - front - back (for 3 adjacent contracts)
  - days_to_expiry        (carried from silver)
  - curve_position        (carried from silver)

Curve-level summaries:
  - front_back_slope      (back[-1] - front[0]) / n_contracts
  - contango_flag         True if curve is upward sloping overall
  - front_settlement      settlement of front-month contract
  - total_contracts       count of PASS+WARN contracts used
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pyarrow as pa

_SCHEMA = pa.schema([
    pa.field("trade_date", pa.string()),
    pa.field("contract_code", pa.string()),
    pa.field("delivery_month", pa.string()),
    pa.field("settlement", pa.float64()),
    pa.field("curve_position", pa.int32()),
    pa.field("days_to_expiry", pa.int32()),
    pa.field("return_1d", pa.float64()),         # None if no prior settle
    pa.field("spread_to_next", pa.float64()),    # None for last contract
    pa.field("butterfly", pa.float64()),          # None for first and last
    pa.field("front_back_slope", pa.float64()),  # same for all rows (curve-level)
    pa.field("contango_flag", pa.bool_()),
    pa.field("source_id", pa.string()),
])


def compute(
    silver: pa.Table,
    as_of_date: date,
    prior_silver: Optional[pa.Table] = None,
) -> pa.Table:
    """
    Compute futures curve features from a silver futures table.
    prior_silver: yesterday's silver table (for 1-day returns); may be None.
    """
    # Filter to PASS and WARN rows only
    d = silver.to_pydict()
    n = len(d["trade_date"])

    # Build prior settle lookup: contract_code → settlement
    prior_settle: dict[str, float] = {}
    if prior_silver is not None:
        pd = prior_silver.to_pydict()
        for i in range(len(pd["trade_date"])):
            if pd.get("silver_status", ["PASS"] * len(pd["trade_date"]))[i] not in ("FAIL",):
                code = pd["contract_code"][i]
                s = pd["settlement"][i]
                if code and s is not None:
                    prior_settle[code] = s

    # Collect valid rows sorted by curve_position
    valid: list[dict] = []
    for i in range(n):
        status = d.get("silver_status", ["PASS"] * n)[i]
        if status == "FAIL":
            continue
        valid.append({
            "contract_code": d["contract_code"][i],
            "delivery_month": d["delivery_month"][i],
            "settlement": d["settlement"][i],
            "curve_position": d["curve_position"][i],
            "days_to_expiry": d.get("days_to_expiry", [None] * n)[i],
            "source_id": d["source_id"][i],
        })
    valid.sort(key=lambda x: x["curve_position"])

    m = len(valid)
    if m == 0:
        return pa.table({f.name: [] for f in _SCHEMA}, schema=_SCHEMA)

    settlements = [v["settlement"] for v in valid]

    # Curve-level slope
    if m >= 2:
        slope = (settlements[-1] - settlements[0]) / max(m - 1, 1)
        contango = settlements[-1] > settlements[0]
    else:
        slope = 0.0
        contango = False

    rows: dict[str, list] = {f.name: [] for f in _SCHEMA}

    for j, v in enumerate(valid):
        code = v["contract_code"]
        settle = v["settlement"]

        # 1-day return
        prior = prior_settle.get(code)
        ret_1d = ((settle - prior) / prior) if (prior and prior > 0) else None

        # Spread to next
        spread = (settlements[j + 1] - settle) if j < m - 1 else None

        # Butterfly: −settle[j-1] + 2*settle[j] − settle[j+1]
        if 0 < j < m - 1:
            butterfly = -settlements[j - 1] + 2 * settle - settlements[j + 1]
        else:
            butterfly = None

        rows["trade_date"].append(as_of_date.isoformat())
        rows["contract_code"].append(code)
        rows["delivery_month"].append(v["delivery_month"])
        rows["settlement"].append(settle)
        rows["curve_position"].append(v["curve_position"])
        rows["days_to_expiry"].append(v["days_to_expiry"])
        rows["return_1d"].append(ret_1d)
        rows["spread_to_next"].append(spread)
        rows["butterfly"].append(butterfly)
        rows["front_back_slope"].append(slope)
        rows["contango_flag"].append(contango)
        rows["source_id"].append(v["source_id"])

    return pa.table(rows, schema=_SCHEMA)
