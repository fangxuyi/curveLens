"""
Futures-options agreement classification.

Determines whether the futures curve and options surface are sending
consistent signals about directional risk.

States (from the spec):
  confirmed_upside_risk       futures + options both signal upside
  confirmed_downside_risk     futures + options both signal downside/demand risk
  non_directional_uncertainty high vol but mixed direction
  futures_only_repricing      futures moved but options IV unchanged
  options_only_repricing      IV moved but futures flat
  cross_market_disagreement   futures and options point opposite directions
  no_material_change          neither moved significantly
  insufficient_data           missing or failed inputs
"""
from __future__ import annotations

from typing import Optional

# Thresholds (approximate; tune after accumulating history)
_SLOPE_BACKWARDATION = -0.10   # $/month → backwardation = upside risk
_SLOPE_CONTANGO = 0.10         # $/month → contango = downside / supply glut
_RR_UPSIDE = 0.02              # risk reversal > 2% → call skew (upside bid)
_RR_DOWNSIDE = -0.02           # risk reversal < -2% → put skew (downside bid)


def classify(
    front_back_slope: Optional[float],    # from futures features
    contango_flag: Optional[bool],
    risk_reversal_25d: Optional[float],   # from option surface
    atm_iv: Optional[float],
    prior_atm_iv: Optional[float],        # prior day (may be None)
    prior_slope: Optional[float],
) -> dict:
    """
    Return a dict with:
      state             (one of the 8 states above)
      confidence        "high" / "medium" / "low"
      evidence          list of supporting signals
    """
    evidence: list[str] = []

    if front_back_slope is None or atm_iv is None:
        return {
            "state": "insufficient_data",
            "confidence": "low",
            "evidence": ["missing futures slope or ATM IV"],
        }

    # ── Futures signal ──
    if front_back_slope < _SLOPE_BACKWARDATION:
        futures_signal = "upside"
        evidence.append(f"backwardation: slope={front_back_slope:.2f}$/month")
    elif front_back_slope > _SLOPE_CONTANGO:
        futures_signal = "downside"
        evidence.append(f"contango: slope={front_back_slope:.2f}$/month")
    else:
        futures_signal = "neutral"
        evidence.append(f"flat curve: slope={front_back_slope:.2f}$/month")

    # ── Slope change ──
    if prior_slope is not None:
        slope_change = front_back_slope - prior_slope
        if abs(slope_change) > 0.05:
            evidence.append(f"slope moved {slope_change:+.2f}$/month vs prior day")

    # ── Options signal (risk reversal) ──
    if risk_reversal_25d is not None:
        if risk_reversal_25d > _RR_UPSIDE:
            options_signal = "upside"
            evidence.append(f"call skew: 25d RR={risk_reversal_25d:.1%}")
        elif risk_reversal_25d < _RR_DOWNSIDE:
            options_signal = "downside"
            evidence.append(f"put skew: 25d RR={risk_reversal_25d:.1%}")
        else:
            options_signal = "neutral"
            evidence.append(f"balanced skew: 25d RR={risk_reversal_25d:.1%}")
    else:
        options_signal = "unknown"

    # ── IV change ──
    iv_moved = False
    if prior_atm_iv is not None and prior_atm_iv > 0:
        iv_change_pct = (atm_iv - prior_atm_iv) / prior_atm_iv
        if abs(iv_change_pct) > 0.05:
            iv_moved = True
            evidence.append(f"ATM IV moved {iv_change_pct:+.1%} vs prior day")

    # ── Classify ──
    futures_moved = abs(front_back_slope) > 0.05 or (prior_slope is not None and abs(front_back_slope - prior_slope) > 0.05)

    if options_signal == "unknown":
        if futures_signal == "upside":
            state = "futures_only_repricing" if futures_moved else "no_material_change"
        elif futures_signal == "downside":
            state = "futures_only_repricing" if futures_moved else "no_material_change"
        else:
            state = "no_material_change"
        confidence = "low"

    elif futures_signal == options_signal == "upside":
        state = "confirmed_upside_risk"
        confidence = "high"

    elif futures_signal == options_signal == "downside":
        state = "confirmed_downside_risk"
        confidence = "high"

    elif futures_signal == "upside" and options_signal == "downside":
        state = "cross_market_disagreement"
        confidence = "medium"
        evidence.append("futures backwardated but options showing put skew")

    elif futures_signal == "downside" and options_signal == "upside":
        state = "cross_market_disagreement"
        confidence = "medium"
        evidence.append("futures in contango but options showing call skew")

    elif futures_signal == "neutral" and options_signal == "neutral":
        if iv_moved:
            state = "non_directional_uncertainty"
            confidence = "medium"
        else:
            state = "no_material_change"
            confidence = "high"

    elif futures_moved and not iv_moved:
        state = "futures_only_repricing"
        confidence = "medium"

    elif iv_moved and not futures_moved:
        state = "options_only_repricing"
        confidence = "medium"

    else:
        state = "non_directional_uncertainty"
        confidence = "low"

    return {
        "state": state,
        "confidence": confidence,
        "evidence": evidence,
        "inputs": {
            "front_back_slope": front_back_slope,
            "atm_iv": atm_iv,
            "risk_reversal_25d": risk_reversal_25d,
        },
    }
