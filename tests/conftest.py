"""
PulseScope — Shared Pytest Fixtures (tests/conftest.py)
=======================================================
Centralized fixtures consumed across all test modules.
Keeping fixture construction here avoids duplicated boilerplate and makes
test isolation explicit.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from analytics_engine import (
    generate_synthetic_users,
    generate_synthetic_orders_and_trades,
    fetch_ohlcv_candles,
)


# ---------------------------------------------------------------------------
# Minimal candle DataFrames
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def flat_candles_50h() -> pd.DataFrame:
    """50-hour flat candles — price stable at ₹5 000 000.

    Used for deterministic FIFO and stop-loss tests where we want
    predictable entry/exit prices without trend noise.
    """
    base = datetime(2026, 1, 1)
    return pd.DataFrame([
        {
            "asset_id":    "BTC",
            "open_time":   base + timedelta(hours=h),
            "open_price":  5_000_000.0,
            "high_price":  5_100_000.0,
            "low_price":   4_900_000.0,
            "close_price": 5_050_000.0,
            "volume":      10.0,
        }
        for h in range(50)
    ])


@pytest.fixture(scope="session")
def wide_candles_60h() -> pd.DataFrame:
    """60-hour candles with a very wide wick — low = ₹5 000 000 (5% below open).

    Ensures every 5 % stop-loss is triggered during backtests.
    """
    base = datetime(2026, 1, 1)
    return pd.DataFrame([
        {
            "asset_id":    "BTC",
            "open_time":   base + timedelta(hours=h),
            "open_price":  5_500_000.0,
            "high_price":  5_600_000.0,
            "low_price":   5_000_000.0,   # well below any 2/5/10 % stop
            "close_price": 5_100_000.0,
            "volume":      15.0,
        }
        for h in range(60)
    ])


# ---------------------------------------------------------------------------
# User DataFrames
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def single_systematic_user() -> pd.DataFrame:
    return pd.DataFrame([{
        "user_id":          "USR_TEST_001",
        "signup_timestamp": datetime.now(),
        "kyc_status":       "TIER_3",
        "persona":          "SYSTEMATIC_TREND",
    }])


@pytest.fixture(scope="session")
def single_retail_user() -> pd.DataFrame:
    return pd.DataFrame([{
        "user_id":          "USR_RET_001",
        "signup_timestamp": datetime.now(),
        "kyc_status":       "TIER_1",
        "persona":          "RETAIL_SPECULATOR",
    }])


# ---------------------------------------------------------------------------
# Pre-computed orders / trades for integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def systematic_orders_and_trades(
    single_systematic_user: pd.DataFrame,
    flat_candles_50h: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return generate_synthetic_orders_and_trades(
        single_systematic_user, flat_candles_50h, asset_id="BTC"
    )


@pytest.fixture(scope="session")
def retail_orders_and_trades(
    single_retail_user: pd.DataFrame,
    wide_candles_60h: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return generate_synthetic_orders_and_trades(
        single_retail_user, wide_candles_60h, asset_id="BTC"
    )


# ---------------------------------------------------------------------------
# Minimal mock DataFrames for unit-level anomaly detection tests
# ---------------------------------------------------------------------------

@pytest.fixture()
def normal_trades_df() -> pd.DataFrame:
    """Three trades — all within SLA, no wash pattern."""
    now = datetime.now()
    return pd.DataFrame([
        {
            "trade_id": "TRD_N1", "order_id": "ORD_N1", "user_id": "USR_RET_001",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
            "executed_qty": 0.05, "fee_inr": 500.0,
            "slippage_pct": 0.0015,
            "executed_at": now,
        },
        {
            "trade_id": "TRD_N2", "order_id": "ORD_N2", "user_id": "USR_RET_002",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
            "executed_qty": 0.05, "fee_inr": 500.0,
            "slippage_pct": 0.0200,
            "executed_at": now + timedelta(minutes=5),
        },
    ])
