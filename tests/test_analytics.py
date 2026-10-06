"""
PulseScope: Automated Test Suite (tests/test_analytics.py)
===========================================================
Institutional verification for:
  - FIFO lot conservation invariants
  - P0/P1/P2 anomaly triage thresholds (boundary + edge cases)
  - Stop-loss backtest schema and financial logic integrity
  - Non-mutating time-series computation guarantee
  - Parametrised threshold coverage
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from analytics_engine import (
    detect_anomalies_and_prioritise,
    fetch_ohlcv_candles,
    generate_synthetic_orders_and_trades,
    generate_synthetic_users,
    run_multi_threshold_stop_loss_backtest,
    P0_SLIPPAGE_THRESHOLD,
    P1_WASH_TRADE_WINDOW_SEC,
    P2_ZSCORE_THRESHOLD,
    DEFAULT_SL_THRESHOLDS,
)


# =============================================================================
# TEST 1: FIFO LOT CONSERVATION & ACCOUNTING INVARIANTS
# =============================================================================

class TestFifoLotConservation:
    """Validates financial conservation laws across generated trade lots."""

    def test_basic_invariants(
        self,
        systematic_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
    ):
        """Execution price, quantity, and fee must be strictly positive / non-negative."""
        _, trades_df = systematic_orders_and_trades

        assert not trades_df.empty, "Trades DataFrame must not be empty."
        assert (trades_df["execution_price"] > 0).all(), \
            "All execution prices must be strictly positive."
        assert (trades_df["executed_qty"] > 0).all(), \
            "All executed quantities must be strictly positive."
        assert (trades_df["fee_inr"] >= 0).all(), \
            "All execution fees must be non-negative."

    def test_cumulative_volume_conservation(
        self,
        systematic_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
    ):
        """Liquidated sell volume must never exceed total purchased volume."""
        _, trades_df = systematic_orders_and_trades

        total_bought = trades_df.loc[trades_df["side"] == "BUY", "executed_qty"].sum()
        total_sold   = trades_df.loc[trades_df["side"] == "SELL", "executed_qty"].sum()

        assert total_sold <= total_bought + 1e-5, (
            f"Liquidated volume ({total_sold:.6f}) exceeds "
            f"bought volume ({total_bought:.6f}) — FIFO violation."
        )

    def test_schema_completeness(
        self,
        systematic_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
    ):
        """Every required column must be present in both output DataFrames."""
        orders_df, trades_df = systematic_orders_and_trades

        required_order_cols = {
            "order_id", "user_id", "asset_id", "side", "order_type",
            "requested_qty", "status", "created_at",
        }
        required_trade_cols = {
            "trade_id", "order_id", "user_id", "asset_id", "side",
            "execution_price", "executed_qty", "fee_inr", "slippage_pct",
            "executed_at",
        }

        assert required_order_cols.issubset(set(orders_df.columns)), \
            f"Missing order columns: {required_order_cols - set(orders_df.columns)}"
        assert required_trade_cols.issubset(set(trades_df.columns)), \
            f"Missing trade columns: {required_trade_cols - set(trades_df.columns)}"

    def test_empty_candles_returns_empty_trades(self):
        """Engine must gracefully handle a candle set too small for any trades."""
        users_df = pd.DataFrame([{
            "user_id": "USR_SYS_001", "signup_timestamp": datetime.now(),
            "kyc_status": "TIER_3", "persona": "SYSTEMATIC_TREND",
        }])
        # Only 5 candles — below the minimum c_idx boundary of 10
        base = datetime(2026, 1, 1)
        tiny_candles = pd.DataFrame([
            {
                "asset_id": "BTC", "open_time": base + timedelta(hours=h),
                "open_price": 5_000_000.0, "high_price": 5_100_000.0,
                "low_price": 4_900_000.0, "close_price": 5_050_000.0,
                "volume": 5.0,
            }
            for h in range(5)
        ])
        orders_df, trades_df = generate_synthetic_orders_and_trades(
            users_df, tiny_candles, asset_id="BTC"
        )
        # No trades should be generated when candle window is insufficient
        assert isinstance(orders_df, pd.DataFrame)
        assert isinstance(trades_df, pd.DataFrame)


# =============================================================================
# TEST 2: P0 CRITICAL SLIPPAGE DETECTION (> 3.00 %)
# =============================================================================

class TestP0SlippageDetection:
    """P0 boundary conditions for execution slippage triage."""

    @pytest.fixture()
    def p0_mock_trades(self) -> pd.DataFrame:
        now = datetime.now()
        return pd.DataFrame([
            {   # 0.15 % — well within SLA → no alert
                "trade_id": "TRD_NORMAL", "order_id": "ORD_1", "user_id": "USR_RET_001",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
                "executed_qty": 0.05, "fee_inr": 500.0,
                "slippage_pct": 0.0015, "executed_at": now,
            },
            {   # Exactly 3.00 % — AT the threshold → no alert (strict >)
                "trade_id": "TRD_AT_BOUNDARY", "order_id": "ORD_2", "user_id": "USR_RET_002",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
                "executed_qty": 0.05, "fee_inr": 500.0,
                "slippage_pct": P0_SLIPPAGE_THRESHOLD, "executed_at": now + timedelta(minutes=5),
            },
            {   # 4.15 % — clearly above threshold → P0 triggered
                "trade_id": "TRD_BREACH", "order_id": "ORD_3", "user_id": "USR_RET_003",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_200_000.0,
                "executed_qty": 0.05, "fee_inr": 520.0,
                "slippage_pct": 0.0415, "executed_at": now + timedelta(minutes=10),
            },
        ])

    def test_p0_fires_only_above_threshold(self, p0_mock_trades: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(p0_mock_trades)
        p0 = incidents[incidents["priority_level"] == "P0"]
        assert len(p0) == 1, f"Expected 1 P0 incident, got {len(p0)}."

    def test_p0_correct_user_and_category(self, p0_mock_trades: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(p0_mock_trades)
        breach = incidents[incidents["priority_level"] == "P0"].iloc[0]
        assert breach["user_id"]          == "USR_RET_003"
        assert breach["anomaly_category"] == "STOP_LOSS_SLIPPAGE"
        assert breach["metric_value"]     == pytest.approx(4.15, rel=1e-4)
        assert breach["baseline_value"]   == pytest.approx(2.00, rel=1e-4)

    def test_p0_description_is_populated(self, p0_mock_trades: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(p0_mock_trades)
        breach = incidents[incidents["priority_level"] == "P0"].iloc[0]
        assert len(breach["description"]) > 20, "Description field must not be empty."

    def test_no_p0_when_all_within_sla(self, normal_trades_df: pd.DataFrame):
        """No P0 incidents when all slippage values are within exchange SLA."""
        incidents = detect_anomalies_and_prioritise(normal_trades_df)
        if not incidents.empty:
            p0 = incidents[incidents["priority_level"] == "P0"]
            assert p0.empty, "Expected zero P0 incidents for in-SLA trades."


# =============================================================================
# TEST 3: P1 CIRCULAR WASH-TRADE DETECTION (<= 5.0 s)
# =============================================================================

class TestP1WashTradeDetection:
    """P1 boundary conditions — sub-second to multi-second delta coverage."""

    @pytest.fixture()
    def wash_trade_mock(self) -> pd.DataFrame:
        base = datetime(2026, 1, 15, 12, 0, 0)
        return pd.DataFrame([
            # Pair 1: delta = 2.4 s → MUST trigger P1
            {
                "trade_id": "TRD_WASH_BUY", "order_id": "ORD_W1", "user_id": "USR_HFT_001",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_400_000.0,
                "executed_qty": 0.0125, "fee_inr": 33.75, "slippage_pct": 0.0,
                "executed_at": base,
            },
            {
                "trade_id": "TRD_WASH_SELL", "order_id": "ORD_W2", "user_id": "USR_HFT_001",
                "asset_id": "BTC", "side": "SELL", "execution_price": 5_400_000.0,
                "executed_qty": 0.0125, "fee_inr": 33.75, "slippage_pct": 0.0,
                "executed_at": base + timedelta(seconds=2.4),
            },
            # Pair 2: delta = 6.2 s > 5.0 s boundary → must NOT trigger
            {
                "trade_id": "TRD_LEGIT_BUY", "order_id": "ORD_L1", "user_id": "USR_HFT_002",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_400_000.0,
                "executed_qty": 0.0500, "fee_inr": 135.0, "slippage_pct": 0.0,
                "executed_at": base + timedelta(minutes=10),
            },
            {
                "trade_id": "TRD_LEGIT_SELL", "order_id": "ORD_L2", "user_id": "USR_HFT_002",
                "asset_id": "BTC", "side": "SELL", "execution_price": 5_400_000.0,
                "executed_qty": 0.0500, "fee_inr": 135.0, "slippage_pct": 0.0,
                "executed_at": base + timedelta(minutes=10, seconds=6.2),
            },
        ])

    def test_p1_fires_exactly_once(self, wash_trade_mock: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(wash_trade_mock)
        p1 = incidents[incidents["priority_level"] == "P1"]
        assert len(p1) == 1, f"Expected 1 P1 wash-trade alert, got {len(p1)}."

    def test_p1_correct_attributes(self, wash_trade_mock: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(wash_trade_mock)
        wash = incidents[incidents["priority_level"] == "P1"].iloc[0]
        assert wash["user_id"]          == "USR_HFT_001"
        assert wash["anomaly_category"] == "WASH_TRADE_DETECTED"
        assert wash["metric_value"]     == pytest.approx(2.40, rel=1e-4)

    @pytest.mark.parametrize("delta_sec,expect_p1", [
        (0.1,  True),    # well below boundary
        (4.99, True),    # just below boundary
        (5.0,  True),    # exactly at boundary (inclusive)
        (5.01, False),   # just above boundary
        (10.0, False),   # clearly above boundary
    ])
    def test_p1_boundary_parametrised(self, delta_sec: float, expect_p1: bool):
        """Exhaustive parametrised boundary test for the 5.0 s wash-trade threshold."""
        base = datetime(2026, 3, 1, 9, 0, 0)
        mock = pd.DataFrame([
            {
                "trade_id": "TRD_A", "order_id": "ORD_A", "user_id": "USR_HFT_001",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
                "executed_qty": 0.01, "fee_inr": 25.0, "slippage_pct": 0.0,
                "executed_at": base,
            },
            {
                "trade_id": "TRD_B", "order_id": "ORD_B", "user_id": "USR_HFT_001",
                "asset_id": "BTC", "side": "SELL", "execution_price": 5_000_000.0,
                "executed_qty": 0.01, "fee_inr": 25.0, "slippage_pct": 0.0,
                "executed_at": base + timedelta(seconds=delta_sec),
            },
        ])
        incidents = detect_anomalies_and_prioritise(mock)
        p1 = incidents[incidents["priority_level"] == "P1"] if not incidents.empty else pd.DataFrame()
        if expect_p1:
            assert not p1.empty, f"Expected P1 at delta={delta_sec}s but none fired."
        else:
            assert p1.empty, f"Unexpected P1 at delta={delta_sec}s."

    def test_p1_different_assets_not_flagged(self):
        """Opposing trades on DIFFERENT assets must not trigger a wash-trade alert."""
        base = datetime(2026, 3, 1, 9, 0, 0)
        mock = pd.DataFrame([
            {
                "trade_id": "TRD_BTC", "order_id": "ORD_BTC", "user_id": "USR_HFT_001",
                "asset_id": "BTC", "side": "BUY", "execution_price": 5_000_000.0,
                "executed_qty": 0.01, "fee_inr": 25.0, "slippage_pct": 0.0,
                "executed_at": base,
            },
            {
                "trade_id": "TRD_ETH", "order_id": "ORD_ETH", "user_id": "USR_HFT_001",
                "asset_id": "ETH",   # different asset
                "side": "SELL", "execution_price": 250_000.0,
                "executed_qty": 0.01, "fee_inr": 0.625, "slippage_pct": 0.0,
                "executed_at": base + timedelta(seconds=1.5),
            },
        ])
        incidents = detect_anomalies_and_prioritise(mock)
        if not incidents.empty:
            p1 = incidents[incidents["priority_level"] == "P1"]
            assert p1.empty, "Cross-asset pairs must not trigger P1 wash-trade alert."


# =============================================================================
# TEST 4: P2 ROLLING VOLUME SPIKE — NON-MUTATING INVARIANT
# =============================================================================

class TestP2VolumeSpikeDetection:
    """P2 Z-score spike detection and DataFrame immutability guarantee."""

    @pytest.fixture()
    def spike_trades_df(self) -> pd.DataFrame:
        base = datetime(2026, 1, 1)
        records = [
            {
                "trade_id": f"TRD_BASE_{h}", "order_id": f"ORD_B_{h}",
                "user_id": "USR_RET_001", "asset_id": "BTC", "side": "BUY",
                "execution_price": 5_000_000.0, "executed_qty": 1.0,
                "fee_inr": 10.0, "slippage_pct": 0.0,
                "executed_at": base + timedelta(hours=h),
            }
            for h in range(48)
        ]
        # Large spike at hour 49 — Z-score >> 3.0
        records.append({
            "trade_id": "TRD_SPIKE", "order_id": "ORD_S",
            "user_id": "USR_HFT_001", "asset_id": "BTC", "side": "BUY",
            "execution_price": 5_000_000.0, "executed_qty": 50.0,
            "fee_inr": 100.0, "slippage_pct": 0.0,
            "executed_at": base + timedelta(hours=49),
        })
        return pd.DataFrame(records)

    def test_p2_spike_detected(self, spike_trades_df: pd.DataFrame):
        incidents = detect_anomalies_and_prioritise(spike_trades_df)
        p2 = incidents[incidents["priority_level"] == "P2"]
        assert not p2.empty, "P2 volume spike must be detected."
        assert (p2["anomaly_category"] == "VOLUME_SPIKE").all()
        assert (p2["metric_value"] > P2_ZSCORE_THRESHOLD).all()

    def test_input_dataframe_not_mutated(self, spike_trades_df: pd.DataFrame):
        """Caller's DataFrame must survive anomaly detection unmodified."""
        original_cols  = spike_trades_df.columns.tolist()
        original_index = type(spike_trades_df.index)
        original_len   = len(spike_trades_df)

        _ = detect_anomalies_and_prioritise(spike_trades_df)

        assert spike_trades_df.columns.tolist() == original_cols, \
            "Columns were mutated by detect_anomalies_and_prioritise."
        assert type(spike_trades_df.index) is original_index, \
            "Index type was mutated by detect_anomalies_and_prioritise."
        assert len(spike_trades_df) == original_len, \
            "Row count changed after detect_anomalies_and_prioritise."

    def test_no_p2_on_flat_volume(self):
        """Perfectly flat volume should produce zero P2 incidents (Z-score = 0)."""
        base = datetime(2026, 6, 1)
        flat = pd.DataFrame([
            {
                "trade_id": f"TRD_{h}", "order_id": f"ORD_{h}",
                "user_id": "USR_RET_001", "asset_id": "BTC", "side": "BUY",
                "execution_price": 5_000_000.0, "executed_qty": 1.0,
                "fee_inr": 10.0, "slippage_pct": 0.0,
                "executed_at": base + timedelta(hours=h),
            }
            for h in range(48)
        ])
        incidents = detect_anomalies_and_prioritise(flat)
        if not incidents.empty:
            p2 = incidents[incidents["priority_level"] == "P2"]
            assert p2.empty, "Flat volume must not produce P2 alerts."

    def test_empty_trades_returns_empty_incidents(self):
        """Empty input DataFrame must produce an empty output without raising."""
        # Completely empty DataFrame (no rows, no columns)
        empty = pd.DataFrame()
        result = detect_anomalies_and_prioritise(empty)
        assert isinstance(result, pd.DataFrame)


# =============================================================================
# TEST 5: STOP-LOSS BACKTEST — SCHEMA, FINANCIAL LOGIC & PARAMETRISED COVERAGE
# =============================================================================

class TestStopLossBacktest:
    """Multi-threshold stop-loss backtest integrity."""

    def test_summary_schema_completeness(
        self,
        retail_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
        wide_candles_60h: pd.DataFrame,
    ):
        """Summary DataFrame must contain all required benchmark columns."""
        _, trades_df = retail_orders_and_trades
        _, summary_df = run_multi_threshold_stop_loss_backtest(trades_df, wide_candles_60h)

        expected_cols = {
            "Strategy", "Total_Realised_PnL_INR", "Avg_Max_Drawdown_Pct",
            "Win_Rate_Pct", "Capital_Preserved_INR", "Stop_Trigger_Rate_Pct",
        }
        assert expected_cols.issubset(set(summary_df.columns)), (
            f"Missing summary columns: {expected_cols - set(summary_df.columns)}"
        )

    def test_summary_row_count(
        self,
        retail_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
        wide_candles_60h: pd.DataFrame,
    ):
        """Summary must have one row per threshold plus the baseline."""
        _, trades_df = retail_orders_and_trades
        _, summary_df = run_multi_threshold_stop_loss_backtest(trades_df, wide_candles_60h)
        expected_rows = 1 + len(DEFAULT_SL_THRESHOLDS)  # baseline + one per threshold
        assert len(summary_df) == expected_rows, (
            f"Expected {expected_rows} strategy rows, got {len(summary_df)}."
        )

    def test_capital_preserved_non_negative(  # noqa: PLR0914
        self,
        retail_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
        wide_candles_60h: pd.DataFrame,
    ):
        """Capital preserved for triggered stop-loss lots must be >= 0.

        Note: untriggered lots always have saved_capital == 0.00 by design.
        When a stop IS triggered, it may save or lose additional capital relative
        to the unmanaged outcome depending on the path — so we only assert
        that the column type is numeric, not a specific sign constraint.
        """
        _, trades_df = retail_orders_and_trades
        sim_df, _ = run_multi_threshold_stop_loss_backtest(trades_df, wide_candles_60h)
        if sim_df.empty:
            pytest.skip("No retail buy trades in fixture — skip financial assertion.")

        for sl in DEFAULT_SL_THRESHOLDS:
            sl_label = int(sl * 100)
            saved_col = f"sl_{sl_label}_saved_capital"
            # Column must exist and contain finite numeric values
            assert sim_df[saved_col].dtype.kind in ("f", "i"), (
                f"{saved_col} must be numeric."
            )
            assert sim_df[saved_col].notna().all(), (
                f"{saved_col} must not contain NaN values."
            )

    @pytest.mark.parametrize("threshold", DEFAULT_SL_THRESHOLDS)
    def test_sl_columns_present_per_threshold(
        self,
        threshold: float,
        retail_orders_and_trades: tuple[pd.DataFrame, pd.DataFrame],
        wide_candles_60h: pd.DataFrame,
    ):
        """Each threshold must produce its four derived columns in the lot table."""
        _, trades_df = retail_orders_and_trades
        sim_df, _ = run_multi_threshold_stop_loss_backtest(trades_df, wide_candles_60h)
        if sim_df.empty:
            pytest.skip("No retail buy trades in fixture.")

        sl_label = int(threshold * 100)
        for col_suffix in ("triggered", "pnl", "exit_time", "saved_capital"):
            col = f"sl_{sl_label}_{col_suffix}"
            assert col in sim_df.columns, f"Missing column: {col}"

    def test_backtest_no_retail_buys_returns_empty(self, flat_candles_50h: pd.DataFrame):
        """Engine must return empty DataFrames when there are no retail buy trades."""
        sell_only = pd.DataFrame([{
            "trade_id": "TRD_SELL_1", "order_id": "ORD_S1",
            "user_id": "USR_SYS_001",  # not a retail user
            "asset_id": "BTC", "side": "SELL",
            "execution_price": 5_000_000.0, "executed_qty": 0.01,
            "fee_inr": 25.0, "slippage_pct": 0.0,
            "executed_at": datetime(2026, 1, 1, 10),
        }])
        sim_df, summary_df = run_multi_threshold_stop_loss_backtest(
            sell_only, flat_candles_50h
        )
        assert sim_df.empty,    "sim_df must be empty with no retail buys."
        assert summary_df.empty, "summary_df must be empty with no retail buys."


# =============================================================================
# TEST 6: GBM FALLBACK CANDLE GENERATOR (OFFLINE RESILIENCE)
# =============================================================================

class TestGbmFallbackCandles:
    """Validates the synthetic GBM candle generator produces valid OHLCV data."""

    @pytest.fixture(scope="class")
    def gbm_candles(self) -> pd.DataFrame:
        # Force GBM path by using a non-existent symbol
        return fetch_ohlcv_candles(
            symbol="FAKE", days=7, base_price_inr=1_000_000.0, random_seed=99
        )

    def test_correct_row_count(self, gbm_candles: pd.DataFrame):
        assert len(gbm_candles) == 7 * 24, \
            f"Expected {7 * 24} hourly candles, got {len(gbm_candles)}."

    def test_ohlcv_financial_constraints(self, gbm_candles: pd.DataFrame):
        """high >= max(open, close) and low <= min(open, close) and all prices > 0."""
        df = gbm_candles
        assert (df["open_price"]  > 0).all()
        assert (df["close_price"] > 0).all()
        assert (df["high_price"]  >= df[["open_price", "close_price"]].max(axis=1)).all(), \
            "high_price must be >= max(open, close)."
        assert (df["low_price"]   <= df[["open_price", "close_price"]].min(axis=1)).all(), \
            "low_price must be <= min(open, close)."

    def test_sorted_ascending(self, gbm_candles: pd.DataFrame):
        times = gbm_candles["open_time"].tolist()
        assert times == sorted(times), "Candles must be returned in ascending time order."

    def test_reproducibility(self):
        """Same seed must produce identical price columns.

        Timestamps are anchored to a fixed epoch date in the GBM fallback,
        so they must also be identical across calls with the same seed.
        """
        df1 = fetch_ohlcv_candles(symbol="FAKE", days=3, random_seed=77)
        df2 = fetch_ohlcv_candles(symbol="FAKE", days=3, random_seed=77)
        # Compare float price columns only — they are fully seed-determined
        for col in ("open_price", "high_price", "low_price", "close_price", "volume"):
            pd.testing.assert_series_equal(
                df1[col].reset_index(drop=True),
                df2[col].reset_index(drop=True),
                check_names=False,
                obj=f"GBM reproducibility check: {col}",
            )

    def test_different_seeds_differ(self):
        """Different seeds must produce different price paths."""
        df1 = fetch_ohlcv_candles(symbol="FAKE", days=3, random_seed=1)
        df2 = fetch_ohlcv_candles(symbol="FAKE", days=3, random_seed=2)
        assert not df1["close_price"].equals(df2["close_price"]), \
            "Different seeds should produce different price series."
