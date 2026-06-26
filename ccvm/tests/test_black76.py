"""Black-76 unit tests — all values hand-verified with known formulas."""
from __future__ import annotations

import math
import pytest
from ccvm.analytics.black76 import black76_price, black76_greeks, implied_vol

# Reference: Black-76 with F=100, K=100, T=1, r=0.05, σ=0.20
# Known values (via standard formula):
#   d1 = (0 + 0.5*0.04*1) / (0.20*1) = 0.02/0.20 = 0.10
#   d2 = 0.10 - 0.20 = -0.10
#   N(d1)=0.5398, N(d2)=0.4602
#   df = exp(-0.05) = 0.9512
#   Call = 0.9512*(100*0.5398 - 100*0.4602) ≈ 0.9512 * 7.96 ≈ 7.57

_F = 100.0
_K = 100.0
_T = 1.0
_R = 0.05
_S = 0.20


def test_atm_call_approx():
    price = black76_price(_F, _K, _T, _R, _S, "C")
    assert 7.0 < price < 8.5, f"ATM call price {price:.4f} out of expected range"


def test_call_put_parity():
    call = black76_price(_F, _K, _T, _R, _S, "C")
    put = black76_price(_F, _K, _T, _R, _S, "P")
    df = math.exp(-_R * _T)
    # C - P = df*(F - K)  →  ATM: C ≈ P
    assert abs(call - put) < 0.01


def test_deep_itm_call_approaches_intrinsic():
    price = black76_price(200.0, 100.0, _T, _R, 0.01, "C")
    df = math.exp(-_R * _T)
    intrinsic = df * (200.0 - 100.0)
    assert abs(price - intrinsic) < 2.0


def test_zero_tte_call_is_intrinsic():
    price = black76_price(110.0, 100.0, 0.0, _R, _S, "C")
    assert abs(price - 10.0) < 0.01


def test_zero_tte_put_otm_is_zero():
    price = black76_price(110.0, 100.0, 0.0, _R, _S, "P")
    assert price == pytest.approx(0.0, abs=0.01)


def test_greeks_call_delta_atm():
    g = black76_greeks(_F, _K, _T, _R, _S, "C")
    # ATM call delta ≈ 0.50 for Black-76
    assert 0.45 < g["delta"] < 0.58


def test_greeks_put_delta_atm_negative():
    g = black76_greeks(_F, _K, _T, _R, _S, "P")
    assert -0.58 < g["delta"] < -0.42


def test_greeks_vega_positive():
    g = black76_greeks(_F, _K, _T, _R, _S, "C")
    assert g["vega"] > 0


def test_greeks_gamma_positive():
    g = black76_greeks(_F, _K, _T, _R, _S, "C")
    assert g["gamma"] > 0


def test_implied_vol_round_trip():
    """IV of a priced option should recover the input vol."""
    for vol in [0.15, 0.25, 0.40, 0.80]:
        price = black76_price(_F, _K, _T, _R, vol, "C")
        recovered = implied_vol(price, _F, _K, _T, _R, "C")
        assert recovered is not None
        assert abs(recovered - vol) < 1e-4, f"vol={vol} → price={price:.4f} → recovered={recovered:.6f}"


def test_implied_vol_round_trip_put():
    for vol in [0.20, 0.35]:
        price = black76_price(_F, _K, _T, _R, vol, "P")
        recovered = implied_vol(price, _F, _K, _T, _R, "P")
        assert recovered is not None
        assert abs(recovered - vol) < 1e-4


def test_implied_vol_below_intrinsic_returns_none():
    df = math.exp(-_R * _T)
    # ITM call: F=120, K=100 → intrinsic ≈ df*20 ≈ 19.02
    result = implied_vol(1.0, 120.0, 100.0, _T, _R, "C")  # price=1 << intrinsic
    assert result is None


def test_implied_vol_otm_put():
    # OTM put: F=100, K=80 → low price
    price = black76_price(100.0, 80.0, _T, _R, 0.25, "P")
    recovered = implied_vol(price, 100.0, 80.0, _T, _R, "P")
    assert recovered is not None
    assert abs(recovered - 0.25) < 1e-4


def test_implied_vol_zero_tte_returns_none():
    result = implied_vol(5.0, 100.0, 100.0, 0.0, _R, "C")
    assert result is None
