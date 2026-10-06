"""
PulseScope: Crypto Trading Performance, Risk & Anomaly Analytics Platform
==========================================================================
Component : Python Ingestion, Simulation & Anomaly Engine (analytics_engine.py)
Target DB : PostgreSQL 14+ / Power BI Star Schema

Architecture
------------
This module provides:
  1. Institutional-grade synthetic data generation
  2. Real / synthetic market candle ingestion (Binance API + GBM fallback)
  3. Multi-threshold stop-loss backtesting (2 %, 5 %, 10 %)
  4. Real-time risk anomaly triage (P0 Critical, P1 High, P2 Medium)

Design Invariants
-----------------
* No DataFrame is mutated in-place after being handed to a caller.
* All random seeds are passed explicitly; no global seed side-effects.
* Hot loops (stop-loss scan, P1 wash-trade) are vectorised with NumPy /
  Pandas where feasible; Python loops are minimised to O(log n) via bisect.
"""

from __future__ import annotations

import bisect
import logging
import os
from datetime import datetime, timedelta
from typing import Final

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("PulseScope.Engine")

# ---------------------------------------------------------------------------
# Module-level constants  (single source of truth - no magic literals)
# ---------------------------------------------------------------------------
USDT_INR_RATE: Final[float] = 88.50          # Nominal INR/USDT conversion peg

# GBM simulation parameters
GBM_MU: Final[float] = 0.0001               # Slight positive hourly drift
GBM_SIGMA: Final[float] = 0.008             # 0.8 % hourly volatility (1-sigma)
GBM_JUMP_PROB: Final[float] = 0.03          # Probability of structural regime jump
GBM_JUMP_SIZE: Final[float] = 0.035         # +/- 3.5 % jump magnitude

# Fee tiers (fraction, not percentage)
FEE_RETAIL_TAKER: Final[float] = 0.0020     # 20 bps
FEE_SYSTEMATIC_TAKER: Final[float] = 0.0010 # 10 bps
FEE_HFT_MAKER: Final[float] = 0.0005        # 5 bps

# Risk thresholds
P0_SLIPPAGE_THRESHOLD: Final[float] = 0.0300   # 3.00 %
P0_BASELINE_SLA: Final[float] = 2.00           # Exchange SLA ceiling (%)
P1_WASH_TRADE_WINDOW_SEC: Final[float] = 5.0   # Wash-trade detection window
P1_BASELINE_REORDER_SEC: Final[float] = 30.0   # Normal human re-order threshold
P2_ZSCORE_THRESHOLD: Final[float] = 3.00       # Statistical spike boundary
P2_ROLLING_WINDOW_HRS: Final[int] = 24         # Rolling window for Z-score

# Stop-loss defaults
DEFAULT_SL_THRESHOLDS: Final[list[float]] = [0.02, 0.05, 0.10]
SL_STOP_SLIPPAGE: Final[float] = 0.0025         # 0.25 % stop-market execution slippage
SL_LOOKFORWARD_CANDLES: Final[int] = 48         # Hours of forward candles to scan

# Binance API
BINANCE_KLINES_URL: Final[str] = "https://api.binance.com/api/v3/klines"
BINANCE_API_TIMEOUT_SEC: Final[int] = 5
BINANCE_MAX_LIMIT: Final[int] = 1000


# =============================================================================
# 1. MARKET CANDLE INGESTION (PUBLIC API + HIGH-FIDELITY SYNTHETIC FALLBACK)
# =============================================================================

def fetch_ohlcv_candles(
    symbol: str = "BTC",
    base_currency: str = "INR",
    interval: str = "1h",
    days: int = 30,
    base_price_inr: float = 5_500_000.0,
    *,
    random_seed: int = 42,
) -> pd.DataFrame:
    """Fetch historical OHLCV market candles from Binance public API.

    Falls back to a Geometric Brownian Motion simulation with volatility
    clustering when the network endpoint is unavailable or rate-limited.

    Parameters
    ----------
    symbol:
        Base asset ticker (e.g. ``"BTC"``).
    base_currency:
        Quote currency for display / valuation (currently only ``"INR"``).
    interval:
        Binance kline interval string (``"1h"``, ``"1m"``, ...).
    days:
        Number of trailing days of history to request.
    base_price_inr:
        Seed price (INR) for the GBM fallback generator.
    random_seed:
        NumPy seed for the stochastic fallback - kept explicit so callers
        can vary seeds without affecting the global RNG state.

    Returns
    -------
    pd.DataFrame
        Columns: asset_id, open_time, open_price, high_price, low_price,
        close_price, volume - sorted ascending by open_time.
    """
    logger.info(
        "Fetching %s/%s candles  interval=%s  days=%d",
        symbol, base_currency, interval, days,
    )

    end_ts = datetime.now()
    start_ts = end_ts - timedelta(days=days)
    candles: list[dict] = []

    # ---- Attempt live Binance endpoint ------------------------------------------
    binance_symbol = f"{symbol}USDT"
    try:
        resp = requests.get(
            BINANCE_KLINES_URL,
            params={
                "symbol": binance_symbol,
                "interval": interval,
                "startTime": int(start_ts.timestamp() * 1000),
                "endTime": int(end_ts.timestamp() * 1000),
                "limit": BINANCE_MAX_LIMIT,
            },
            timeout=BINANCE_API_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        for k in resp.json():
            o_time = datetime.fromtimestamp(k[0] / 1000.0)
            candles.append({
                "asset_id":    symbol,
                "open_time":   o_time,
                "open_price":  round(float(k[1]) * USDT_INR_RATE, 4),
                "high_price":  round(float(k[2]) * USDT_INR_RATE, 4),
                "low_price":   round(float(k[3]) * USDT_INR_RATE, 4),
                "close_price": round(float(k[4]) * USDT_INR_RATE, 4),
                "volume":      round(float(k[5]), 6),
            })
        logger.info("Ingested %d live candles from Binance API.", len(candles))
    except Exception as exc:  # network / JSON / HTTP errors
        logger.warning(
            "Binance endpoint unavailable (%s). Activating GBM stochastic fallback.",
            exc,
        )

    # ---- GBM stochastic fallback ------------------------------------------------
    if not candles:
        rng = np.random.default_rng(random_seed)
        total_hours = days * 24
        # Use a fixed anchor relative to epoch to make output seed-reproducible
        # regardless of when the function is called.
        anchor_ts = datetime(2026, 1, 1)  # reproducible, not datetime.now()
        current_ts = anchor_ts - timedelta(days=days)
        price = base_price_inr

        for _ in range(total_hours):
            shock = rng.normal(GBM_MU, GBM_SIGMA)
            if rng.random() < GBM_JUMP_PROB:
                shock += rng.choice([-GBM_JUMP_SIZE, GBM_JUMP_SIZE])

            open_p  = price
            close_p = open_p * (1.0 + shock)
            high_p  = max(open_p, close_p) * (1.0 + abs(rng.normal(0.0, 0.004)))
            low_p   = min(open_p, close_p) * (1.0 - abs(rng.normal(0.0, 0.004)))
            volume  = round(float(rng.exponential(12.5) + 1.2), 6)

            candles.append({
                "asset_id":    symbol,
                "open_time":   current_ts,
                "open_price":  round(open_p, 4),
                "high_price":  round(high_p, 4),
                "low_price":   round(low_p, 4),
                "close_price": round(close_p, 4),
                "volume":      volume,
            })
            price = close_p
            current_ts += timedelta(hours=1)

        logger.info("Generated %d synthetic GBM candles.", len(candles))

    df = (
        pd.DataFrame(candles)
        .sort_values("open_time", ignore_index=True)
    )
    return df


# =============================================================================
# 2. USER DIMENSION & TRADING PERSONA GENERATION
# =============================================================================

def generate_synthetic_users(
    base_time: datetime | None = None,
    *,
    random_seed: int = 7,
) -> pd.DataFrame:
    """Generate a representative 30-trader cohort across three institutional archetypes.

    Archetypes
    ----------
    RETAIL_SPECULATOR (15):
        Low-frequency, emotional holding, high drawdown, taker-only.
    SYSTEMATIC_TREND (10):
        Strict momentum rules, trailing stops, bracket target exits.
    HIGH_FREQUENCY_MAKER (5):
        High-volume tight-spread market-making; occasional wash-trade signatures.

    Parameters
    ----------
    base_time:
        Anchor timestamp for signup offsets; defaults to 35 days ago.
    random_seed:
        Explicit RNG seed for reproducibility.
    """
    if base_time is None:
        base_time = datetime.now() - timedelta(days=35)

    rng = np.random.default_rng(random_seed)
    users: list[dict] = []

    # Retail Speculators - 15 users
    for i in range(1, 16):
        users.append({
            "user_id":          f"USR_RET_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=int(rng.integers(0, 10))),
            "kyc_status":       rng.choice(["TIER_1", "TIER_2", "TIER_3"], p=[0.2, 0.6, 0.2]),
            "persona":          "RETAIL_SPECULATOR",
        })

    # Systematic Trend Traders - 10 users
    for i in range(1, 11):
        users.append({
            "user_id":          f"USR_SYS_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=int(rng.integers(0, 8))),
            "kyc_status":       rng.choice(["TIER_2", "TIER_3"], p=[0.3, 0.7]),
            "persona":          "SYSTEMATIC_TREND",
        })

    # High-Frequency Market Makers - 5 users
    for i in range(1, 6):
        users.append({
            "user_id":          f"USR_HFT_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=int(rng.integers(0, 5))),
            "kyc_status":       "TIER_3",
            "persona":          "HIGH_FREQUENCY_MAKER",
        })

    return pd.DataFrame(users)


# =============================================================================
# 3. SYNTHETIC ORDER & MATCHED TRADE ENGINE
# =============================================================================

def generate_synthetic_orders_and_trades(
    users_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    asset_id: str = "BTC",
    *,
    random_seed: int = 101,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate relational Orders (intent) and matched Trades (fills).

    Behavioural profiles
    --------------------
    Retail (RETAIL_SPECULATOR):
        Emotional top-buying and panic-selling; taker fee 20 bps;
        4 % probability of critical slippage > 3 % (P0 trigger).

    Systematic (SYSTEMATIC_TREND):
        Disciplined breakout buying with bracket stop/target exits;
        taker fee 10 bps; exits resolved via forward candle lookahead.

    HFT (HIGH_FREQUENCY_MAKER):
        High-frequency two-sided limit orders; maker fee 5 bps;
        three injected wash-trade pairs per user (P1 test signal).

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        (orders_df, trades_df) - both sorted by timestamp, index reset.
    """
    logger.info("Simulating multi-persona order intent and trade executions...")
    rng = np.random.default_rng(random_seed)

    orders: list[dict] = []
    trades: list[dict] = []

    order_seq = 500_000
    trade_seq = 800_000

    # Pre-materialise candle list once - avoids repeated DataFrame access in loops
    candle_records: list[dict] = candles_df.to_dict(orient="records")
    num_candles: int = len(candle_records)

    # Pre-sort candle open_times for bisect lookups in the SL backtest
    _candle_times = [c["open_time"] for c in candle_records]  # noqa: F841  (used by backtest)

    def _next_order_id() -> str:
        nonlocal order_seq
        order_seq += 1
        return f"ORD_{order_seq}"

    def _next_trade_id() -> str:
        nonlocal trade_seq
        trade_seq += 1
        return f"TRD_{trade_seq}"

    # -------------------------------------------------------------------------
    # PERSONA 1: RETAIL SPECULATORS
    # -------------------------------------------------------------------------
    retail_users = users_df.loc[
        users_df["persona"] == "RETAIL_SPECULATOR", "user_id"
    ].tolist()

    for uid in retail_users:
        num_trades = int(rng.integers(20, 36))
        for _ in range(num_trades):
            # Guard: need at least 16 candles for c_idx range [5, num_candles-10]
            if num_candles < 16:
                break
            c_idx = int(rng.integers(5, num_candles - 10))
            c = candle_records[c_idx]
            order_time = c["open_time"] + timedelta(minutes=int(rng.integers(1, 55)))

            side = rng.choice(["BUY", "SELL"], p=[0.60, 0.40])
            qty  = round(float(rng.exponential(0.025) + 0.005), 4)

            # Critical slippage injected at 4 % probability (P0 test signal)
            is_anomaly = rng.random() < 0.04
            slippage_pct = round(
                float(rng.uniform(0.031, 0.055)) if is_anomaly
                else float(rng.uniform(0.0005, 0.0035)),
                4,
            )

            expected_price = c["close_price"]
            exec_price = round(
                expected_price * (1.0 + slippage_pct)
                if side == "BUY"
                else expected_price * (1.0 - slippage_pct),
                4,
            )
            fee = round(exec_price * qty * FEE_RETAIL_TAKER, 4)

            oid = _next_order_id()
            tid = _next_trade_id()

            orders.append({
                "order_id": oid, "user_id": uid, "asset_id": asset_id,
                "side": side, "order_type": "MARKET",
                "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": order_time,
            })
            trades.append({
                "trade_id": tid, "order_id": oid, "user_id": uid,
                "asset_id": asset_id, "side": side,
                "execution_price": exec_price, "executed_qty": qty,
                "fee_inr": fee, "slippage_pct": slippage_pct,
                "executed_at": order_time,
            })

    # -------------------------------------------------------------------------
    # PERSONA 2: SYSTEMATIC TREND TRADERS
    # -------------------------------------------------------------------------
    sys_users = users_df.loc[
        users_df["persona"] == "SYSTEMATIC_TREND", "user_id"
    ].tolist()

    for uid in sys_users:
        num_cycles = int(rng.integers(18, 28))
        for _ in range(num_cycles):
            # Guard: need at least 41 candles for c_idx range [10, num_candles-30]
            if num_candles < 41:
                break
            c_idx = int(rng.integers(10, num_candles - 30))
            c_entry = candle_records[c_idx]
            entry_time  = c_entry["open_time"] + timedelta(minutes=15)
            entry_price = round(c_entry["close_price"], 4)
            qty         = round(float(rng.uniform(0.02, 0.06)), 4)

            # Entry order (BUY LIMIT)
            entry_oid = _next_order_id()
            entry_tid = _next_trade_id()
            entry_fee = round(entry_price * qty * FEE_SYSTEMATIC_TAKER, 4)
            stop_price   = entry_price * 0.95   # 5 % hard stop
            target_price = entry_price * 1.08   # 8 % take-profit

            orders.append({
                "order_id": entry_oid, "user_id": uid, "asset_id": asset_id,
                "side": "BUY", "order_type": "LIMIT",
                "stop_price":   round(stop_price, 4),
                "target_price": round(target_price, 4),
                "requested_qty": qty, "status": "FILLED", "created_at": entry_time,
            })
            trades.append({
                "trade_id": entry_tid, "order_id": entry_oid, "user_id": uid,
                "asset_id": asset_id, "side": "BUY",
                "execution_price": entry_price, "executed_qty": qty,
                "fee_inr": entry_fee, "slippage_pct": 0.0002,
                "executed_at": entry_time,
            })

            # Bracket exit: scan forward candles for stop / target hit
            exit_price   = entry_price
            exit_time    = entry_time + timedelta(hours=24)
            exit_type    = "MARKET"
            future_slice = candle_records[c_idx + 1 : c_idx + 40]

            for future_c in future_slice:
                if future_c["low_price"] <= stop_price:
                    exit_price = round(stop_price * (1.0 - SL_STOP_SLIPPAGE), 4)
                    exit_time  = future_c["open_time"] + timedelta(minutes=30)
                    exit_type  = "STOP_LOSS_MARKET"
                    break
                if future_c["high_price"] >= target_price:
                    exit_price = round(target_price, 4)
                    exit_time  = future_c["open_time"] + timedelta(minutes=20)
                    exit_type  = "TAKE_PROFIT_LIMIT"
                    break
            else:
                # Time-based liquidation at end of holding window
                c_exit     = candle_records[min(c_idx + 40, num_candles - 1)]
                exit_price = round(c_exit["close_price"], 4)
                exit_time  = c_exit["open_time"]

            exit_oid = _next_order_id()
            exit_tid = _next_trade_id()
            exit_fee = round(exit_price * qty * FEE_SYSTEMATIC_TAKER, 4)

            orders.append({
                "order_id": exit_oid, "user_id": uid, "asset_id": asset_id,
                "side": "SELL", "order_type": exit_type,
                "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": exit_time,
            })
            trades.append({
                "trade_id": exit_tid, "order_id": exit_oid, "user_id": uid,
                "asset_id": asset_id, "side": "SELL",
                "execution_price": exit_price, "executed_qty": qty,
                "fee_inr": exit_fee, "slippage_pct": 0.0004,
                "executed_at": exit_time,
            })

    # -------------------------------------------------------------------------
    # PERSONA 3: HIGH-FREQUENCY MARKET MAKERS
    # -------------------------------------------------------------------------
    hft_users = users_df.loc[
        users_df["persona"] == "HIGH_FREQUENCY_MAKER", "user_id"
    ].tolist()

    for uid in hft_users:
        num_hft_trades = int(rng.integers(250, 400))
        # Choose 3 indices for deliberate P1 wash-trade injections
        wash_indices: set[int] = set(
            rng.choice(
                range(10, num_hft_trades - 10), size=3, replace=False
            ).tolist()
        )

        for t_i in range(num_hft_trades):
            c_idx = int(rng.integers(0, num_candles))
            c     = candle_records[c_idx]
            t_time = c["open_time"] + timedelta(seconds=int(rng.uniform(0, 3590)))

            mid_price  = c["close_price"]
            spread     = mid_price * 0.0004   # 4 bps
            qty        = round(float(rng.uniform(0.002, 0.015)), 4)
            side       = rng.choice(["BUY", "SELL"])
            exec_price = round(
                mid_price - spread / 2 if side == "BUY"
                else mid_price + spread / 2,
                4,
            )
            fee = round(exec_price * qty * FEE_HFT_MAKER, 4)

            oid = _next_order_id()
            tid = _next_trade_id()

            orders.append({
                "order_id": oid, "user_id": uid, "asset_id": asset_id,
                "side": side, "order_type": "LIMIT",
                "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": t_time,
            })
            trades.append({
                "trade_id": tid, "order_id": oid, "user_id": uid,
                "asset_id": asset_id, "side": side,
                "execution_price": exec_price, "executed_qty": qty,
                "fee_inr": fee, "slippage_pct": 0.0000,
                "executed_at": t_time,
            })

            # Inject wash-trade pair (P1 signal)
            if t_i in wash_indices:
                opp_side   = "SELL" if side == "BUY" else "BUY"
                delta_sec  = float(rng.uniform(0.8, 4.8))   # strictly < 5.0 s
                wash_time  = t_time + timedelta(seconds=delta_sec)
                wash_oid   = _next_order_id()
                wash_tid   = _next_trade_id()

                orders.append({
                    "order_id": wash_oid, "user_id": uid, "asset_id": asset_id,
                    "side": opp_side, "order_type": "LIMIT",
                    "stop_price": None, "target_price": None,
                    "requested_qty": qty, "status": "FILLED", "created_at": wash_time,
                })
                trades.append({
                    "trade_id": wash_tid, "order_id": wash_oid, "user_id": uid,
                    "asset_id": asset_id, "side": opp_side,
                    "execution_price": exec_price, "executed_qty": qty,
                    "fee_inr": fee, "slippage_pct": 0.0000,
                    "executed_at": wash_time,
                })

    if orders:
        df_orders = pd.DataFrame(orders).sort_values("created_at", ignore_index=True)
        df_trades = pd.DataFrame(trades).sort_values("executed_at", ignore_index=True)
    else:
        # No trades generated (e.g. candle count below minimum boundary)
        df_orders = pd.DataFrame(columns=[
            "order_id", "user_id", "asset_id", "side", "order_type",
            "stop_price", "target_price", "requested_qty", "status", "created_at",
        ])
        df_trades = pd.DataFrame(columns=[
            "trade_id", "order_id", "user_id", "asset_id", "side",
            "execution_price", "executed_qty", "fee_inr", "slippage_pct", "executed_at",
        ])

    logger.info(
        "Generated %d orders and %d matched trades.",
        len(df_orders), len(df_trades),
    )
    return df_orders, df_trades


# =============================================================================
# 4. MULTI-THRESHOLD STOP-LOSS BACKTEST ENGINE
# =============================================================================

def run_multi_threshold_stop_loss_backtest(
    trades_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    thresholds: list[float] = DEFAULT_SL_THRESHOLDS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backtest automated stop-loss rules against retail buy entries.

    For each retail BUY trade, the engine scans the forward
    ``SL_LOOKFORWARD_CANDLES`` hours of candle low prices using a bisect
    lookup on the sorted candle timeline (O(log n) per trade vs. O(n) for a
    linear scan), then evaluates 2 %, 5 %, and 10 % stop triggers.

    Returns
    -------
    sim_df:
        Lot-by-lot simulation table with columns for each threshold.
    summary_df:
        Executive benchmark matrix (Unmanaged vs. each stop-loss policy).
    """
    logger.info("Executing multi-threshold stop-loss diagnostic simulation...")

    retail_buys = trades_df[
        (trades_df["side"] == "BUY")
        & trades_df["user_id"].str.startswith("USR_RET_")
    ].copy()

    if retail_buys.empty:
        logger.warning("No retail buy trades found - returning empty backtest results.")
        return pd.DataFrame(), pd.DataFrame()

    # Build a sorted parallel structure for O(log n) forward-candle lookup
    candle_records: list[dict] = (
        candles_df.sort_values("open_time").to_dict(orient="records")
    )
    candle_times: list[datetime] = [c["open_time"] for c in candle_records]

    simulation_rows: list[dict] = []

    for _, trade in retail_buys.iterrows():
        entry_price = float(trade["execution_price"])
        entry_time  = trade["executed_at"]
        qty         = float(trade["executed_qty"])
        entry_fee   = float(trade["fee_inr"])
        window_end  = entry_time + timedelta(hours=SL_LOOKFORWARD_CANDLES)

        # O(log n) binary search for the slice boundaries
        left  = bisect.bisect_left(candle_times, entry_time)
        right = bisect.bisect_right(candle_times, window_end)
        forward_candles = candle_records[left:right]

        if forward_candles:
            min_future_price = float(min(c["low_price"]   for c in forward_candles))
            terminal_price   = float(forward_candles[-1]["close_price"])
        else:
            min_future_price = entry_price
            terminal_price   = entry_price

        unmanaged_dd_pct = round(
            (min_future_price - entry_price) / entry_price * 100.0, 2
        )
        unmanaged_pnl = round(
            qty * (terminal_price - entry_price) - entry_fee * 2, 2
        )

        lot_sim: dict = {
            "trade_id":                   trade["trade_id"],
            "user_id":                    trade["user_id"],
            "entry_time":                 entry_time,
            "entry_price":                entry_price,
            "qty":                        qty,
            "unmanaged_max_drawdown_pct": unmanaged_dd_pct,
            "unmanaged_pnl":              unmanaged_pnl,
        }

        for sl in thresholds:
            sl_label      = int(sl * 100)
            trigger_price = entry_price * (1.0 - sl)

            # Find the earliest candle that breaches the stop level
            breach = next(
                (c for c in forward_candles if c["low_price"] <= trigger_price),
                None,
            )

            if breach is not None:
                exit_price = trigger_price * (1.0 - SL_STOP_SLIPPAGE)
                exit_fee   = exit_price * qty * FEE_RETAIL_TAKER
                sl_pnl     = round(qty * (exit_price - entry_price) - (entry_fee + exit_fee), 2)
                lot_sim[f"sl_{sl_label}_triggered"]    = True
                lot_sim[f"sl_{sl_label}_pnl"]          = sl_pnl
                lot_sim[f"sl_{sl_label}_exit_time"]    = breach["open_time"]
                lot_sim[f"sl_{sl_label}_saved_capital"] = round(sl_pnl - unmanaged_pnl, 2)
            else:
                lot_sim[f"sl_{sl_label}_triggered"]    = False
                lot_sim[f"sl_{sl_label}_pnl"]          = unmanaged_pnl
                lot_sim[f"sl_{sl_label}_exit_time"]    = None
                lot_sim[f"sl_{sl_label}_saved_capital"] = 0.00

        simulation_rows.append(lot_sim)

    sim_df = pd.DataFrame(simulation_rows)

    # ---- Executive summary --------------------------------------------------
    total_unmanaged_pnl = sim_df["unmanaged_pnl"].sum()
    avg_unmanaged_dd    = sim_df["unmanaged_max_drawdown_pct"].mean()
    win_rate_unmanaged  = (sim_df["unmanaged_pnl"] > 0).mean() * 100.0

    summary_metrics: list[dict] = [{
        "Strategy":               "Unmanaged Retail Baseline",
        "Total_Realised_PnL_INR": round(total_unmanaged_pnl, 2),
        "Avg_Max_Drawdown_Pct":   round(avg_unmanaged_dd, 2),
        "Win_Rate_Pct":           round(win_rate_unmanaged, 2),
        "Capital_Preserved_INR":  0.00,
        "Stop_Trigger_Rate_Pct":  0.00,
    }]

    for sl in thresholds:
        sl_label   = int(sl * 100)
        pnl_col    = f"sl_{sl_label}_pnl"
        trig_col   = f"sl_{sl_label}_triggered"
        tot_pnl    = sim_df[pnl_col].sum()
        trig_rate  = sim_df[trig_col].mean() * 100.0
        win_rate   = (sim_df[pnl_col] > 0).mean() * 100.0
        preserved  = tot_pnl - total_unmanaged_pnl
        # Max drawdown capped to the stop threshold when triggered
        capped_dd  = sim_df["unmanaged_max_drawdown_pct"].apply(
            lambda d: max(d, -sl * 100.0)
        ).mean()

        summary_metrics.append({
            "Strategy":               f"Automated {sl_label}% Stop-Loss",
            "Total_Realised_PnL_INR": round(tot_pnl, 2),
            "Avg_Max_Drawdown_Pct":   round(capped_dd, 2),
            "Win_Rate_Pct":           round(win_rate, 2),
            "Capital_Preserved_INR":  round(preserved, 2),
            "Stop_Trigger_Rate_Pct":  round(trig_rate, 2),
        })

    summary_df = pd.DataFrame(summary_metrics)
    logger.info("Stop-loss diagnostic completed - %d lot records processed.", len(sim_df))
    return sim_df, summary_df


# =============================================================================
# 5. RISK ANOMALY DETECTION & INCIDENT PRIORITISATION (P0 / P1 / P2)
# =============================================================================

def detect_anomalies_and_prioritise(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Evaluate trade logs and generate prioritised incident tickets.

    Detection rules
    ---------------
    P0 (Critical):
        Stop-loss / market-order execution slippage > 3.00 %.
        Baseline SLA: 2.00 %.
    P1 (High):
        Circular wash-trading - opposing sides with identical quantity
        executed by the same user/asset within 5.0 seconds.
    P2 (Medium):
        Rolling 24-hour volume Z-score > 3.00 (statistical spike).

    Safety invariants
    -----------------
    * ``trades_df`` is never mutated; all operations work on explicit copies.
    * Timestamp index manipulation is isolated to local variables.

    Parameters
    ----------
    trades_df:
        DataFrame of trade executions (fct_trades schema).
    orders_df:
        Optional orders DataFrame - reserved for future order-level checks.

    Returns
    -------
    pd.DataFrame
        Incident queue sorted by (priority_level, detected_at).
        Returns an empty DataFrame if no incidents are found.
    """
    logger.info("Executing risk surveillance and incident triage...")
    incidents: list[dict] = []

    # Guard: empty or malformed input
    if trades_df.empty or "slippage_pct" not in trades_df.columns:
        return pd.DataFrame()

    # -------------------------------------------------------------------------
    # P0: Execution slippage > 3.0 %
    # -------------------------------------------------------------------------
    p0_trades = trades_df[trades_df["slippage_pct"] > P0_SLIPPAGE_THRESHOLD]
    for _, row in p0_trades.iterrows():
        slippage_val = float(row["slippage_pct"]) * 100.0
        incidents.append({
            "user_id":          row["user_id"],
            "asset_id":         row["asset_id"],
            "priority_level":   "P0",
            "anomaly_category": "STOP_LOSS_SLIPPAGE",
            "metric_value":     round(slippage_val, 2),
            "baseline_value":   P0_BASELINE_SLA,
            "status":           "OPEN",
            "detected_at":      row["executed_at"],
            "description": (
                f"Critical execution slippage of {slippage_val:.2f}% on "
                f"trade {row['trade_id']} (order {row['order_id']}). "
                f"Breaches institutional SLA of {P0_BASELINE_SLA:.2f}%."
            ),
        })
    logger.info("P0: %d critical slippage incidents.", len(p0_trades))

    # -------------------------------------------------------------------------
    # P1: Circular wash-trade detection (vectorised shift-based approach)
    #
    # Approach: sort by (user_id, asset_id, executed_at); compute shifted
    # columns for adjacent row comparison; filter in a single vectorised mask.
    # Avoids O(n) Python iloc loop on large DataFrames.
    # -------------------------------------------------------------------------
    ts = (
        trades_df[["trade_id", "order_id", "user_id", "asset_id",
                   "side", "executed_qty", "executed_at"]]
        .copy()
        .sort_values(["user_id", "asset_id", "executed_at"])
        .reset_index(drop=True)
    )

    # Shift-compare neighbouring rows
    ts_next = ts.shift(-1)

    same_user      = ts["user_id"]        == ts_next["user_id"]
    same_asset     = ts["asset_id"]       == ts_next["asset_id"]
    opp_side       = ts["side"]           != ts_next["side"]
    same_qty       = (ts["executed_qty"] - ts_next["executed_qty"].astype(float)).abs() < 1e-5

    # Compute time delta only where the above mask is True (avoids NaT propagation)
    valid_mask = same_user & same_asset & opp_side & same_qty
    if valid_mask.any():
        delta_sec = (
            pd.to_datetime(ts_next.loc[valid_mask, "executed_at"])
            - pd.to_datetime(ts.loc[valid_mask, "executed_at"])
        ).dt.total_seconds().abs()

        # Build the final wash_mask using np.where to avoid FutureWarning on
        # boolean Series assignment with a non-boolean computed value.
        within_window = delta_sec <= P1_WASH_TRADE_WINDOW_SEC
        wash_indices_set = set(within_window[within_window].index.tolist())

        for idx in wash_indices_set:
            if idx + 1 >= len(ts):
                continue
            curr = ts.iloc[idx]
            nxt  = ts.iloc[idx + 1]
            d_sec = abs((nxt["executed_at"] - curr["executed_at"]).total_seconds())
            incidents.append({
                "user_id":          curr["user_id"],
                "asset_id":         curr["asset_id"],
                "priority_level":   "P1",
                "anomaly_category": "WASH_TRADE_DETECTED",
                "metric_value":     round(d_sec, 2),
                "baseline_value":   P1_BASELINE_REORDER_SEC,
                "status":           "OPEN",
                "detected_at":      nxt["executed_at"],
                "description": (
                    f"Potential circular wash trade: {curr['user_id']} executed "
                    f"{curr['side']}/{nxt['side']} of {curr['executed_qty']:.4f} "
                    f"{curr['asset_id']} within {d_sec:.2f}s "
                    f"(threshold \u2264 {P1_WASH_TRADE_WINDOW_SEC:.1f}s)."
                ),
            })
    logger.info("P1: %d wash-trade incidents.", sum(1 for i in incidents if i.get("priority_level") == "P1"))

    # -------------------------------------------------------------------------
    # P2: Rolling 24-hour volume Z-score > 3.0
    #
    # Works on an isolated copy - no mutation of caller's trades_df.
    # -------------------------------------------------------------------------
    vol_ts = (
        trades_df[["executed_at", "executed_qty"]]
        .rename(columns={"executed_at": "ts"})
        .copy()
        .set_index("ts")
        .resample("1h")["executed_qty"]
        .sum()
        .fillna(0.0)
    )

    rolling_mean = vol_ts.rolling(window=P2_ROLLING_WINDOW_HRS, min_periods=6).mean()
    rolling_std  = vol_ts.rolling(window=P2_ROLLING_WINDOW_HRS, min_periods=6).std()
    z_scores     = (vol_ts - rolling_mean) / (rolling_std + 1e-6)

    spike_hours = z_scores[z_scores > P2_ZSCORE_THRESHOLD]
    for ts_key, z_val in spike_hours.items():
        actual_vol = float(vol_ts.loc[ts_key])
        mean_vol   = float(rolling_mean.loc[ts_key])
        incidents.append({
            "user_id":          "PLATFORM_AGGREGATE",
            "asset_id":         "BTC",
            "priority_level":   "P2",
            "anomaly_category": "VOLUME_SPIKE",
            "metric_value":     round(float(z_val), 2),
            "baseline_value":   P2_ZSCORE_THRESHOLD,
            "status":           "OPEN",
            "detected_at":      ts_key,
            "description": (
                f"Platform volume anomaly at {ts_key}: "
                f"{actual_vol:.2f} BTC (mean {mean_vol:.2f} BTC, "
                f"Z={z_val:.2f} > {P2_ZSCORE_THRESHOLD:.2f})."
            ),
        })
    logger.info("P2: %d volume spike anomalies.", len(spike_hours))

    if not incidents:
        return pd.DataFrame()

    df_incidents = (
        pd.DataFrame(incidents)
        .sort_values(["priority_level", "detected_at"])
        .reset_index(drop=True)
    )
    return df_incidents


# =============================================================================
# 6. RELATIONAL DATA WAREHOUSE SEED & CSV EXPORT
# =============================================================================

def export_data_warehouse_csvs(
    output_dir: str = "./data",
) -> dict[str, pd.DataFrame]:
    """Run the full end-to-end pipeline and export relational CSVs.

    Pipeline steps
    --------------
    1. Seed asset dimension.
    2. Fetch / generate market candles (BTC, 30 days).
    3. Generate 30-trader user cohort.
    4. Simulate orders and matched trades for all personas.
    5. Run P0/P1/P2 anomaly triage -> incident queue.
    6. Run multi-threshold stop-loss backtest.
    7. Write all tables to ``output_dir`` as UTF-8 CSVs.

    Returns
    -------
    dict[str, pd.DataFrame]
        Mapping of table name -> DataFrame for all core warehouse tables.
    """
    os.makedirs(output_dir, exist_ok=True)
    logger.info("Initialising PulseScope Data Warehouse pipeline -> %s", output_dir)

    # 1. Assets dimension
    assets_df = pd.DataFrame([
        {"asset_id": "BTC", "symbol": "BTC", "base_currency": "INR", "is_active": True},
        {"asset_id": "ETH", "symbol": "ETH", "base_currency": "INR", "is_active": True},
        {"asset_id": "SOL", "symbol": "SOL", "base_currency": "INR", "is_active": True},
    ])

    # 2. Market candles fact
    candles_df = fetch_ohlcv_candles(symbol="BTC", days=30, base_price_inr=5_500_000.0)

    # 3. Users dimension
    users_df = generate_synthetic_users()

    # 4. Orders & trades facts
    orders_df, trades_df = generate_synthetic_orders_and_trades(
        users_df, candles_df, asset_id="BTC"
    )

    # 5. Incident queue fact
    incidents_df = detect_anomalies_and_prioritise(trades_df, orders_df)

    # Core warehouse tables
    tables: dict[str, pd.DataFrame] = {
        "dim_assets":        assets_df,
        "dim_users":         users_df,
        "fct_market_candles": candles_df,
        "fct_orders":        orders_df,
        "fct_trades":        trades_df,
        "fct_incident_queue": incidents_df,
    }

    for name, df in tables.items():
        path = os.path.join(output_dir, f"{name}.csv")
        df.to_csv(path, index=False, encoding="utf-8")
        logger.info("Exported %-25s -> %s  (%d rows)", name, path, len(df))

    # 6. Stop-loss backtest diagnostics
    sim_df, summary_df = run_multi_threshold_stop_loss_backtest(trades_df, candles_df)
    sim_df.to_csv(
        os.path.join(output_dir, "diagnostic_stop_loss_lots.csv"),
        index=False, encoding="utf-8",
    )
    summary_df.to_csv(
        os.path.join(output_dir, "diagnostic_stop_loss_summary.csv"),
        index=False, encoding="utf-8",
    )
    logger.info("Exported stop-loss backtest diagnostic tables.")

    return tables


# =============================================================================
# 7. MAIN PIPELINE ENTRYPOINT
# =============================================================================

def _print_pipeline_summary(warehouse_tables: dict[str, pd.DataFrame]) -> None:
    """Print a formatted summary of pipeline output to stdout."""
    print("\n" + "=" * 80)
    print("  PULSESCOPE: CRYPTO TRADING ANALYTICS & ANOMALY PIPELINE")
    print("=" * 80)
    print(f"\n{'Table':<28} {'Rows':>8}  {'Columns':>8}")
    print("-" * 46)
    for name, df in warehouse_tables.items():
        print(f"{name:<28} {len(df):>8}  {len(df.columns):>8}")

    incidents = warehouse_tables.get("fct_incident_queue", pd.DataFrame())
    print("\n--- Top 5 Open Incidents ---")
    if not incidents.empty:
        display_cols = [
            "priority_level", "anomaly_category", "user_id",
            "metric_value", "baseline_value",
        ]
        print(incidents[display_cols].head().to_string(index=False))
    else:
        print("No open incidents detected.")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    warehouse = export_data_warehouse_csvs(output_dir="./data")
    _print_pipeline_summary(warehouse)
