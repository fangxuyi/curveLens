"""
Scenario engine for WTI curve and volatility shocks.

Generates three standard scenarios (bull / base / bear) plus an optional
event scenario. Each scenario produces:
  - A shocked futures curve (settlement per contract)
  - Implied vol shifts per expiry
  - Estimated P&L impact on a $1/bbl position across the curve
  - Confirmation and invalidation triggers

Scenarios are stored as JSON alongside the daily report.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

import pyarrow as pa


@dataclass
class ScenarioShock:
    name: str                        # "bull" | "base" | "bear" | "event"
    description: str
    curve_shift_usd: float           # parallel shift $/bbl (+ = up)
    curve_tilt: float                # $/month × position in curve (steepens/flattens)
    vol_shift_pct: float             # absolute shift in IV (+ = up)
    confirmation_triggers: list[str] = field(default_factory=list)
    invalidation_triggers: list[str] = field(default_factory=list)


@dataclass
class ScenarioResult:
    name: str
    description: str
    shocked_settlements: list[dict]  # {contract_code, delivery_month, base, shocked, diff}
    vol_shifts: list[dict]           # {option_expiry, base_atm_iv, shocked_iv}
    front_month_impact: float        # $/bbl change at front month
    curve_pnl_estimate: float        # sum of shocks × 1 contract each
    confirmation_triggers: list[str]
    invalidation_triggers: list[str]
    as_of_date: str


_STANDARD_SHOCKS = [
    ScenarioShock(
        name="bull",
        description="Supply disruption or OPEC cut drives prompt rally",
        curve_shift_usd=+5.0,
        curve_tilt=-0.20,  # backwardation steepens: near > far
        vol_shift_pct=+0.05,
        confirmation_triggers=[
            "front-month settles above prior 30-day high",
            "25-delta call IV rises ≥ 3pp vs put IV",
            "EIA crude draw > 3mb",
        ],
        invalidation_triggers=[
            "front-month settles below prior week close",
            "curve shifts to contango > $1/month",
            "EIA build > 2mb for two consecutive weeks",
        ],
    ),
    ScenarioShock(
        name="base",
        description="Gradual supply rebalance, range-bound prices",
        curve_shift_usd=0.0,
        curve_tilt=0.0,
        vol_shift_pct=0.0,
        confirmation_triggers=[
            "front-month stays within ±$3 of current settle for 5 sessions",
            "ATM IV unchanged ±2pp",
        ],
        invalidation_triggers=[
            "front-month moves > $5 in either direction",
            "ATM IV moves > 5pp in either direction",
        ],
    ),
    ScenarioShock(
        name="bear",
        description="Demand weakness or supply glut drives sell-off",
        curve_shift_usd=-5.0,
        curve_tilt=+0.15,  # contango steepens: far > near
        vol_shift_pct=+0.08,
        confirmation_triggers=[
            "front-month settles below prior 30-day low",
            "25-delta put IV rises ≥ 5pp vs call IV (put skew)",
            "EIA build > 4mb",
        ],
        invalidation_triggers=[
            "front-month rallies > $4 from current settle",
            "curve moves into backwardation",
            "OPEC+ announces emergency cut",
        ],
    ),
]


def apply_shock(
    contracts: list[dict],      # [{contract_code, delivery_month, settlement, curve_position}]
    shock: ScenarioShock,
    as_of_date: date,
) -> list[dict]:
    results = []
    for c in contracts:
        pos = c.get("curve_position", 1)
        base = c.get("settlement") or 0.0
        shocked = base + shock.curve_shift_usd + shock.curve_tilt * (pos - 1)
        results.append({
            "contract_code": c["contract_code"],
            "delivery_month": c["delivery_month"],
            "curve_position": pos,
            "base_settlement": round(base, 3),
            "shocked_settlement": round(shocked, 3),
            "diff": round(shocked - base, 3),
        })
    return results


def apply_vol_shock(
    expiry_ivs: list[dict],    # [{option_expiry, atm_iv}]
    shock: ScenarioShock,
) -> list[dict]:
    results = []
    for ev in expiry_ivs:
        base_iv = ev.get("atm_iv") or 0.0
        shocked_iv = max(0.01, base_iv + shock.vol_shift_pct)
        results.append({
            "option_expiry": ev["option_expiry"],
            "base_atm_iv": round(base_iv, 4),
            "shocked_iv": round(shocked_iv, 4),
            "diff_pp": round((shocked_iv - base_iv) * 100, 2),
        })
    return results


def generate(
    gold_futures: pa.Table,
    gold_options: Optional[pa.Table],
    as_of_date: date,
    extra_shocks: Optional[list[ScenarioShock]] = None,
) -> list[ScenarioResult]:
    """
    Generate scenarios from gold-layer features.
    """
    # Extract contracts
    fd = gold_futures.to_pydict()
    contracts = [
        {
            "contract_code": fd["contract_code"][i],
            "delivery_month": fd["delivery_month"][i],
            "settlement": fd["settlement"][i],
            "curve_position": fd["curve_position"][i],
        }
        for i in range(len(fd["trade_date"]))
    ]
    contracts.sort(key=lambda x: x["curve_position"])

    # Extract per-expiry ATM IV
    expiry_ivs: list[dict] = []
    if gold_options is not None and len(gold_options) > 0:
        od = gold_options.to_pydict()
        seen: dict[str, float] = {}
        for i in range(len(od["trade_date"])):
            exp = od["option_expiry"][i]
            iv = od["atm_iv"][i]
            if exp and iv is not None and exp not in seen:
                seen[exp] = iv
        expiry_ivs = [{"option_expiry": k, "atm_iv": v}
                      for k, v in sorted(seen.items())]

    shocks = list(_STANDARD_SHOCKS) + (extra_shocks or [])
    results: list[ScenarioResult] = []

    for shock in shocks:
        shocked_settlements = apply_shock(contracts, shock, as_of_date)
        vol_shifts = apply_vol_shock(expiry_ivs, shock)

        front_impact = shocked_settlements[0]["diff"] if shocked_settlements else 0.0
        curve_pnl = sum(s["diff"] for s in shocked_settlements)

        results.append(ScenarioResult(
            name=shock.name,
            description=shock.description,
            shocked_settlements=shocked_settlements,
            vol_shifts=vol_shifts,
            front_month_impact=round(front_impact, 3),
            curve_pnl_estimate=round(curve_pnl, 3),
            confirmation_triggers=shock.confirmation_triggers,
            invalidation_triggers=shock.invalidation_triggers,
            as_of_date=as_of_date.isoformat(),
        ))

    return results


def to_dict(result: ScenarioResult) -> dict:
    return asdict(result)
