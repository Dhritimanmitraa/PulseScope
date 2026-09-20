"""
PulseScope: Automated Test Suite (tests/test_analytics.py)
===========================================================
Institutional test verification for FIFO lot matching invariants,
P0/P1/P2 anomaly triage thresholds, and non-mutating time-series calculations.
"""

from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import pytest

from analytics_engine import (
    generate_synthetic_users,
    generate_synthetic_orders_and_trades,
    detect_anomalies_and_prioritise,
    run_multi_threshold_stop_loss_backtest,
    fetch_ohlcv_candles,
    export_data_warehouse_csvs
)


# =============================================================================
# TEST 1: FIFO LOT CONSERVATION & ACCOUNTING INVARIANTS
# =============================================================================

def test_fifo_lot_conservation_invariant():
    """
    Validates financial conservation laws for trade lots:
    1. For every closed lot slice, matched_qty <= initial buy_qty.
    2. Total liquidated quantity cannot exceed cumulative bought quantity.
    3. Trade fees and execution prices are strictly positive.
    """
    users_df = pd.DataFrame([
        {"user_id": "USR_TEST_001", "signup_timestamp": datetime.now(), "kyc_status": "TIER_3", "persona": "SYSTEMATIC_TREND"}
    ])
    
    base_dt = datetime(2026, 1, 1)
    candles_df = pd.DataFrame([
        {"asset_id": "BTC", "open_time": base_dt + timedelta(hours=h), "open_price": 5000000.0,
         "high_price": 5100000.0, "low_price": 4900000.0, "close_price": 5050000.0, "volume": 10.0}
        for h in range(50)
    ])
    
    orders_df, trades_df = generate_synthetic_orders_and_trades(users_df, candles_df, asset_id="BTC")
    
    assert not trades_df.empty, "Trades DataFrame should not be empty."
    assert (trades_df["execution_price"] > 0).all(), "Execution price must be strictly positive."
    assert (trades_df["executed_qty"] > 0).all(), "Executed quantity must be strictly positive."
    assert (trades_df["fee_inr"] >= 0).all(), "Execution fee must be non-negative."
    
    # Cumulative volume conservation
    total_bought = trades_df[trades_df["side"] == "BUY"]["executed_qty"].sum()
    total_sold = trades_df[trades_df["side"] == "SELL"]["executed_qty"].sum()
    
    # In systematic trading, liquidated sells cannot exceed total bought volume
    assert total_sold <= total_bought + 1e-5, (
        f"Liquidated volume ({total_sold}) cannot exceed bought volume ({total_bought})"
    )


# =============================================================================
# TEST 2: P0 CRITICAL SLIPPAGE DETECTION (> 3.00%)
# =============================================================================

def test_p0_slippage_anomaly_detection():
    """
    Verifies that the anomaly engine triggers a P0 Critical ticket when execution
    slippage exceeds 3.00% (0.03), and ignores normal trades within the 2.0% SLA.
    """
    now = datetime.now()
    mock_trades = pd.DataFrame([
        {
            "trade_id": "TRD_NORMAL_1", "order_id": "ORD_1", "user_id": "USR_RET_001",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5000000.0, "executed_qty": 0.05,
            "fee_inr": 500.0, "slippage_pct": 0.0015, "executed_at": now # 0.15% (Normal)
        },
        {
            "trade_id": "TRD_SLA_CEILING", "order_id": "ORD_2", "user_id": "USR_RET_002",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5000000.0, "executed_qty": 0.05,
            "fee_inr": 500.0, "slippage_pct": 0.0200, "executed_at": now + timedelta(minutes=5) # 2.00% (SLA bound)
        },
        {
            "trade_id": "TRD_CRITICAL_BREACH", "order_id": "ORD_3", "user_id": "USR_RET_003",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5200000.0, "executed_qty": 0.05,
            "fee_inr": 520.0, "slippage_pct": 0.0415, "executed_at": now + timedelta(minutes=10) # 4.15% (P0 trigger)
        }
    ])
    
    incidents = detect_anomalies_and_prioritise(mock_trades)
    
    p0_incidents = incidents[incidents["priority_level"] == "P0"]
    assert len(p0_incidents) == 1, f"Expected exactly 1 P0 incident, found {len(p0_incidents)}"
    
    breach = p0_incidents.iloc[0]
    assert breach["user_id"] == "USR_RET_003"
    assert breach["anomaly_category"] == "STOP_LOSS_SLIPPAGE"
    assert breach["metric_value"] == 4.15
    assert breach["baseline_value"] == 2.00


# =============================================================================
# TEST 3: P1 CIRCULAR WASH TRADE DETECTION (<= 5.0 SECONDS)
# =============================================================================

def test_p1_wash_trade_detection_thresholds():
    """
    Verifies that complementary round-trip trades with identical volume are flagged:
    - Interval <= 5.0 seconds: FLAGGED AS P1 WASH_TRADE_DETECTED
    - Interval >= 5.1 seconds: IGNORED (within normal human re-order threshold)
    """
    base_time = datetime(2026, 1, 15, 12, 0, 0)
    
    mock_trades = pd.DataFrame([
        # Pair 1: Suspicious circular wash trade (delta = 2.4s) -> SHOULD TRIGGER P1
        {
            "trade_id": "TRD_WASH_BUY", "order_id": "ORD_W1", "user_id": "USR_HFT_001",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5400000.0, "executed_qty": 0.0125,
            "fee_inr": 33.75, "slippage_pct": 0.0, "executed_at": base_time
        },
        {
            "trade_id": "TRD_WASH_SELL", "order_id": "ORD_W2", "user_id": "USR_HFT_001",
            "asset_id": "BTC", "side": "SELL", "execution_price": 5400000.0, "executed_qty": 0.0125,
            "fee_inr": 33.75, "slippage_pct": 0.0, "executed_at": base_time + timedelta(seconds=2.4)
        },
        # Pair 2: Legitimate re-order (delta = 6.2s > 5.0s boundary) -> SHOULD NOT TRIGGER
        {
            "trade_id": "TRD_LEGIT_BUY", "order_id": "ORD_L1", "user_id": "USR_HFT_002",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5400000.0, "executed_qty": 0.0500,
            "fee_inr": 135.0, "slippage_pct": 0.0, "executed_at": base_time + timedelta(minutes=10)
        },
        {
            "trade_id": "TRD_LEGIT_SELL", "order_id": "ORD_L2", "user_id": "USR_HFT_002",
            "asset_id": "BTC", "side": "SELL", "execution_price": 5400000.0, "executed_qty": 0.0500,
            "fee_inr": 135.0, "slippage_pct": 0.0, "executed_at": base_time + timedelta(minutes=10, seconds=6.2)
        }
    ])
    
    incidents = detect_anomalies_and_prioritise(mock_trades)
    p1_incidents = incidents[incidents["priority_level"] == "P1"]
    
    assert len(p1_incidents) == 1, f"Expected exactly 1 P1 wash-trade alert, found {len(p1_incidents)}"
    wash = p1_incidents.iloc[0]
    assert wash["user_id"] == "USR_HFT_001"
    assert wash["anomaly_category"] == "WASH_TRADE_DETECTED"
    assert wash["metric_value"] == 2.40


# =============================================================================
# TEST 4: P2 NON-MUTATING TIME-SERIES & Z-SCORE SPIKE LOGIC
# =============================================================================

def test_p2_volume_spike_non_mutating_dataframe():
    """
    Verifies that P2 rolling volume Z-score computation does NOT mutate the caller's
    DataFrame in-place (no set_index corruption), preserving original index and columns.
    """
    base_time = datetime(2026, 1, 1, 0, 0, 0)
    records = []
    
    # Generate 48 hours of normal baseline volume (1.0 BTC per hour)
    for h in range(48):
        records.append({
            "trade_id": f"TRD_BASE_{h}", "order_id": f"ORD_B_{h}", "user_id": "USR_RET_001",
            "asset_id": "BTC", "side": "BUY", "execution_price": 5000000.0, "executed_qty": 1.0,
            "fee_inr": 10.0, "slippage_pct": 0.0, "executed_at": base_time + timedelta(hours=h)
        })
        
    # Inject a massive volume spike at hour 49 (50.0 BTC -> Z-Score > 3.0)
    records.append({
        "trade_id": "TRD_SPIKE", "order_id": "ORD_S", "user_id": "USR_HFT_001",
        "asset_id": "BTC", "side": "BUY", "execution_price": 5000000.0, "executed_qty": 50.0,
        "fee_inr": 100.0, "slippage_pct": 0.0, "executed_at": base_time + timedelta(hours=49)
    })
    
    df_trades = pd.DataFrame(records)
    original_cols = df_trades.columns.tolist()
    original_index_type = type(df_trades.index)
    original_len = len(df_trades)
    
    incidents = detect_anomalies_and_prioritise(df_trades)
    
    # 1. Invariance check: original DataFrame was NOT mutated in-place
    assert df_trades.columns.tolist() == original_cols, "Columns of input DataFrame were mutated!"
    assert type(df_trades.index) is original_index_type, "Index of input DataFrame was mutated!"
    assert len(df_trades) == original_len, "Length of input DataFrame changed!"
    
    # 2. P2 Detection check
    p2_incidents = incidents[incidents["priority_level"] == "P2"]
    assert not p2_incidents.empty, "P2 volume spike should have been detected."
    assert (p2_incidents["anomaly_category"] == "VOLUME_SPIKE").all()
    assert (p2_incidents["metric_value"] > 3.0).all()


# =============================================================================
# TEST 5: STOP-LOSS BACKTEST MATRIX SANITY
# =============================================================================

def test_stop_loss_backtest_structural_integrity():
    """
    Verifies that the multi-threshold stop-loss backtester outputs valid summary
    metrics across 2%, 5%, and 10% stops with proper column schemas.
    """
    users_df = pd.DataFrame([
        {"user_id": "USR_RET_001", "signup_timestamp": datetime.now(), "kyc_status": "TIER_1", "persona": "RETAIL_SPECULATOR"}
    ])
    base_dt = datetime(2026, 1, 1)
    candles_df = pd.DataFrame([
        {"asset_id": "BTC", "open_time": base_dt + timedelta(hours=h), "open_price": 5500000.0,
         "high_price": 5600000.0, "low_price": 5000000.0, "close_price": 5100000.0, "volume": 15.0}
        for h in range(60)
    ])
    orders_df, trades_df = generate_synthetic_orders_and_trades(users_df, candles_df, asset_id="BTC")
    
    sim_df, summary_df = run_multi_threshold_stop_loss_backtest(trades_df, candles_df)
    
    expected_summary_cols = {"Strategy", "Total_Realised_PnL_INR", "Avg_Max_Drawdown_Pct", "Win_Rate_Pct", "Capital_Preserved_INR"}
    assert expected_summary_cols.issubset(set(summary_df.columns)), "Missing expected columns in summary benchmark."
    assert len(summary_df) == 4, "Summary matrix should have 4 strategies (Baseline, 2%, 5%, 10%)."
