"""
PulseScope: Crypto Trading Performance, Risk & Anomaly Analytics Platform
==========================================================================
Component: Python Ingestion, Simulation & Anomaly Engine (analytics_engine.py)
Target DB: PostgreSQL 14+ / Power BI Star Schema

This module provides institutional-grade synthetic data generation, real/synthetic
market candle ingestion, multi-threshold stop-loss backtesting, and real-time
risk anomaly triage (P0 Critical, P1 High, P2 Medium).
"""

import os
import json
import logging
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from scipy import stats
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("PulseScope.Engine")


# =============================================================================
# 1. MARKET CANDLE INGESTION (PUBLIC API + HIGH-FIDELITY SYNTHETIC FALLBACK)
# =============================================================================

def fetch_ohlcv_candles(
    symbol: str = "BTC",
    base_currency: str = "INR",
    interval: str = "1h",
    days: int = 30,
    base_price_inr: float = 5500000.0
) -> pd.DataFrame:
    """
    Fetches historical OHLCV market candles from public exchange endpoints (Binance/CoinDCX).
    If external network calls fail or are rate-limited, smoothly transitions to a
    stochastic Geometric Brownian Motion (GBM) with volatility jumps to ensure
    100% reproducible, resilient execution offline and online.
    """
    logger.info(f"Fetching market candles for {symbol}/{base_currency} (interval={interval}, days={days})...")
    
    candles = []
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)
    
    # Try public Binance API for BTCUSDT as global price discovery benchmark
    binance_symbol = f"{symbol}USDT"
    usdt_inr_rate = 88.50 # Nominal INR conversion peg for Indian market valuation
    
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": binance_symbol,
            "interval": interval,
            "startTime": int(start_time.timestamp() * 1000),
            "endTime": int(end_time.timestamp() * 1000),
            "limit": 1000
        }
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            raw_klines = resp.json()
            for k in raw_klines:
                o_time = datetime.fromtimestamp(k[0] / 1000.0)
                op = float(k[1]) * usdt_inr_rate
                hp = float(k[2]) * usdt_inr_rate
                lp = float(k[3]) * usdt_inr_rate
                cp = float(k[4]) * usdt_inr_rate
                vol = float(k[5])
                candles.append({
                    "asset_id": symbol,
                    "open_time": o_time,
                    "open_price": round(op, 4),
                    "high_price": round(hp, 4),
                    "low_price": round(lp, 4),
                    "close_price": round(cp, 4),
                    "volume": round(vol, 6)
                })
            logger.info(f"Successfully ingested {len(candles)} real market candles from public API.")
    except Exception as e:
        logger.warning(f"Public API endpoint unreachable ({e}). Activating high-fidelity stochastic generator.")

    # High-Fidelity Stochastic Generator Fallback (Geometric Brownian Motion + Volatility Clustering)
    if not candles:
        np.random.seed(42)
        total_hours = days * 24
        current_time = start_time
        current_price = base_price_inr
        
        # Microstructure volatility parameters
        mu = 0.0001        # Slight positive drift
        sigma = 0.008      # 0.8% hourly volatility standard deviation
        
        for _ in range(total_hours):
            shock = np.random.normal(mu, sigma)
            # Occasional structural jump / regime shift
            if np.random.rand() < 0.03:
                shock += np.random.choice([-0.035, 0.035])
                
            open_p = current_price
            close_p = open_p * (1.0 + shock)
            wick_high = abs(np.random.normal(0, 0.004))
            wick_low = abs(np.random.normal(0, 0.004))
            high_p = max(open_p, close_p) * (1.0 + wick_high)
            low_p = min(open_p, close_p) * (1.0 - wick_low)
            volume = np.random.exponential(12.5) + 1.2
            
            candles.append({
                "asset_id": symbol,
                "open_time": current_time,
                "open_price": round(open_p, 4),
                "high_price": round(high_p, 4),
                "low_price": round(low_p, 4),
                "close_price": round(close_p, 4),
                "volume": round(volume, 6)
            })
            current_price = close_p
            current_time += timedelta(hours=1)
            
        logger.info(f"Generated {len(candles)} synthetic OHLCV market candles via stochastic GBM.")

    df_candles = pd.DataFrame(candles)
    df_candles.sort_values(by="open_time", inplace=True)
    df_candles.reset_index(drop=True, inplace=True)
    return df_candles


# =============================================================================
# 2. USER DIMENSION & TRADING PERSONA GENERATION
# =============================================================================

def generate_synthetic_users(base_time: datetime = None) -> pd.DataFrame:
    """
    Generates representative trader cohort across 3 institutional archetypes:
    1. RETAIL_SPECULATOR (15 users): Low frequency, emotional holding, high drawdown.
    2. SYSTEMATIC_TREND (10 users): Strict momentum rules, trailing stops, target exits.
    3. HIGH_FREQUENCY_MAKER (5 users): High volume, tight spreads, sub-second quotes.
    """
    if base_time is None:
        base_time = datetime.now() - timedelta(days=35)
        
    users = []
    
    # 1. Retail Speculators (15 users)
    for i in range(1, 16):
        users.append({
            "user_id": f"USR_RET_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=np.random.randint(0, 10)),
            "kyc_status": np.random.choice(["TIER_1", "TIER_2", "TIER_3"], p=[0.2, 0.6, 0.2]),
            "persona": "RETAIL_SPECULATOR"
        })
        
    # 2. Systematic Trend Traders (10 users)
    for i in range(1, 11):
        users.append({
            "user_id": f"USR_SYS_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=np.random.randint(0, 8)),
            "kyc_status": np.random.choice(["TIER_2", "TIER_3"], p=[0.3, 0.7]),
            "persona": "SYSTEMATIC_TREND"
        })
        
    # 3. High-Frequency Market Makers (5 users)
    for i in range(1, 6):
        users.append({
            "user_id": f"USR_HFT_{i:03d}",
            "signup_timestamp": base_time + timedelta(days=np.random.randint(0, 5)),
            "kyc_status": "TIER_3",
            "persona": "HIGH_FREQUENCY_MAKER"
        })
        
    return pd.DataFrame(users)


# =============================================================================
# 3. SYNTHETIC ORDER & MATCHED TRADE ENGINE
# =============================================================================

def generate_synthetic_orders_and_trades(
    users_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    asset_id: str = "BTC"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generates relational Orders (intent) and matched Trades (fills).
    Simulates:
    - Retail: emotional top-buying and panic-selling, unmanaged losing inventory,
              taker fee = 0.20%, occasional market slippage.
    - Systematic: disciplined breakout buying with bracket stops/targets,
                  taker fee = 0.10%.
    - HFT: high-frequency 2-sided market making, limit orders, maker fee = 0.05%,
           injected micro-second circular wash-trading signatures for testing.
    """
    logger.info("Simulating multi-persona order intent staging and trade executions...")
    np.random.seed(101)
    
    orders = []
    trades = []
    
    order_seq = 500000
    trade_seq = 800000
    
    candle_records = candles_df.to_dict(orient="records")
    num_candles = len(candle_records)
    
    # -------------------------------------------------------------------------
    # PERSONA 1: RETAIL SPECULATORS
    # -------------------------------------------------------------------------
    retail_users = users_df[users_df["persona"] == "RETAIL_SPECULATOR"]["user_id"].tolist()
    for uid in retail_users:
        # Each retail trader makes 20-35 trades over the 30-day window
        num_trades = np.random.randint(20, 36)
        
        # Retail traders have positive accumulation bias (60% BUY, 40% SELL)
        for _ in range(num_trades):
            c_idx = np.random.randint(5, num_candles - 10)
            c = candle_records[c_idx]
            order_time = c["open_time"] + timedelta(minutes=np.random.randint(1, 55))
            
            side = np.random.choice(["BUY", "SELL"], p=[0.60, 0.40])
            # Retail order size: exponential distribution ~0.02 BTC
            qty = round(float(np.random.exponential(0.025) + 0.005), 4)
            
            # Slippage injection: standard retail market order has 0.1% - 0.4% slippage.
            # During volatile candles, slippage can spike > 3.0% (P0 Incident trigger)
            is_slippage_anomaly = np.random.rand() < 0.04 # 4% probability of critical slippage
            if is_slippage_anomaly:
                slippage_pct = round(float(np.random.uniform(0.031, 0.055)), 4) # >3%
            else:
                slippage_pct = round(float(np.random.uniform(0.0005, 0.0035)), 4)
                
            expected_price = c["close_price"]
            if side == "BUY":
                exec_price = round(expected_price * (1.0 + slippage_pct), 4)
            else:
                exec_price = round(expected_price * (1.0 - slippage_pct), 4)
                
            order_seq += 1
            trade_seq += 1
            oid = f"ORD_{order_seq}"
            tid = f"TRD_{trade_seq}"
            
            # Retail taker fee rate: 0.20% (0.0020)
            fee = round(exec_price * qty * 0.0020, 4)
            
            orders.append({
                "order_id": oid, "user_id": uid, "asset_id": asset_id, "side": side,
                "order_type": "MARKET", "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": order_time
            })
            
            trades.append({
                "trade_id": tid, "order_id": oid, "user_id": uid, "asset_id": asset_id,
                "side": side, "execution_price": exec_price, "executed_qty": qty,
                "fee_inr": fee, "slippage_pct": slippage_pct, "executed_at": order_time
            })

    # -------------------------------------------------------------------------
    # PERSONA 2: SYSTEMATIC TREND TRADERS
    # -------------------------------------------------------------------------
    sys_users = users_df[users_df["persona"] == "SYSTEMATIC_TREND"]["user_id"].tolist()
    for uid in sys_users:
        # Systematic traders execute on trend breakouts (40-60 trades per user)
        num_cycles = np.random.randint(18, 28)
        for _ in range(num_cycles):
            c_idx = np.random.randint(10, num_candles - 30)
            c_entry = candle_records[c_idx]
            entry_time = c_entry["open_time"] + timedelta(minutes=15)
            
            entry_price = round(c_entry["close_price"], 4)
            qty = round(float(np.random.uniform(0.02, 0.06)), 4)
            
            # Entry Order (BUY LIMIT)
            order_seq += 1
            trade_seq += 1
            entry_oid = f"ORD_{order_seq}"
            entry_tid = f"TRD_{trade_seq}"
            entry_fee = round(entry_price * qty * 0.0010, 4) # 0.10% fee
            
            orders.append({
                "order_id": entry_oid, "user_id": uid, "asset_id": asset_id, "side": "BUY",
                "order_type": "LIMIT", "stop_price": round(entry_price * 0.95, 4),
                "target_price": round(entry_price * 1.10, 4),
                "requested_qty": qty, "status": "FILLED", "created_at": entry_time
            })
            trades.append({
                "trade_id": entry_tid, "order_id": entry_oid, "user_id": uid, "asset_id": asset_id,
                "side": "BUY", "execution_price": entry_price, "executed_qty": qty,
                "fee_inr": entry_fee, "slippage_pct": 0.0002, "executed_at": entry_time
            })
            
            # Bracket Exit Execution (Look ahead 48 hours to find stop or target hit)
            exit_triggered = False
            exit_price = entry_price
            exit_time = entry_time + timedelta(hours=24)
            exit_type = "LIMIT"
            
            stop_price = entry_price * 0.95 # 5% strict stop
            target_price = entry_price * 1.08 # 8% target
            
            for future_c in candle_records[c_idx+1 : c_idx+40]:
                if future_c["low_price"] <= stop_price:
                    exit_price = round(stop_price * 0.9985, 4) # Stop market slippage
                    exit_time = future_c["open_time"] + timedelta(minutes=30)
                    exit_type = "STOP_LOSS_MARKET"
                    exit_triggered = True
                    break
                elif future_c["high_price"] >= target_price:
                    exit_price = round(target_price, 4)
                    exit_time = future_c["open_time"] + timedelta(minutes=20)
                    exit_type = "TAKE_PROFIT_LIMIT"
                    exit_triggered = True
                    break
                    
            if not exit_triggered:
                # Liquidate at end of holding window
                c_exit = candle_records[min(c_idx+40, num_candles-1)]
                exit_price = round(c_exit["close_price"], 4)
                exit_time = c_exit["open_time"]
                exit_type = "MARKET"
                
            order_seq += 1
            trade_seq += 1
            exit_oid = f"ORD_{order_seq}"
            exit_tid = f"TRD_{trade_seq}"
            exit_fee = round(exit_price * qty * 0.0010, 4)
            
            orders.append({
                "order_id": exit_oid, "user_id": uid, "asset_id": asset_id, "side": "SELL",
                "order_type": exit_type, "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": exit_time
            })
            trades.append({
                "trade_id": exit_tid, "order_id": exit_oid, "user_id": uid, "asset_id": asset_id,
                "side": "SELL", "execution_price": exit_price, "executed_qty": qty,
                "fee_inr": exit_fee, "slippage_pct": 0.0004, "executed_at": exit_time
            })

    # -------------------------------------------------------------------------
    # PERSONA 3: HIGH-FREQUENCY MARKET MAKERS
    # -------------------------------------------------------------------------
    hft_users = users_df[users_df["persona"] == "HIGH_FREQUENCY_MAKER"]["user_id"].tolist()
    for uid in hft_users:
        # High volume of small round-trip limit orders (250-400 executions)
        num_hft_trades = np.random.randint(250, 400)
        
        # Inject occasional P1 circular wash-trade patterns (under 5 seconds)
        wash_trade_target_indices = set(np.random.choice(range(10, num_hft_trades - 10), size=3, replace=False))
        
        for t_i in range(num_hft_trades):
            c_idx = np.random.randint(0, num_candles)
            c = candle_records[c_idx]
            t_time = c["open_time"] + timedelta(seconds=int(np.random.uniform(0, 3590)))
            
            # Tight spread around mid price
            mid_price = c["close_price"]
            spread = mid_price * 0.0004 # 4 bps spread
            qty = round(float(np.random.uniform(0.002, 0.015)), 4)
            side = np.random.choice(["BUY", "SELL"])
            exec_price = round(mid_price - (spread/2) if side == "BUY" else mid_price + (spread/2), 4)
            
            order_seq += 1
            trade_seq += 1
            oid = f"ORD_{order_seq}"
            tid = f"TRD_{trade_seq}"
            fee = round(exec_price * qty * 0.0005, 4) # 0.05% maker fee
            
            orders.append({
                "order_id": oid, "user_id": uid, "asset_id": asset_id, "side": side,
                "order_type": "LIMIT", "stop_price": None, "target_price": None,
                "requested_qty": qty, "status": "FILLED", "created_at": t_time
            })
            trades.append({
                "trade_id": tid, "order_id": oid, "user_id": uid, "asset_id": asset_id,
                "side": side, "execution_price": exec_price, "executed_qty": qty,
                "fee_inr": fee, "slippage_pct": 0.0000, "executed_at": t_time
            })
            
            # Check if this index is chosen for an explicit P1 wash-trade injection
            if t_i in wash_trade_target_indices:
                opposite_side = "SELL" if side == "BUY" else "BUY"
                delta_seconds = float(np.random.uniform(0.8, 3.8)) # Strictly < 5.0 seconds
                wash_time = t_time + timedelta(seconds=delta_seconds)
                
                order_seq += 1
                trade_seq += 1
                wash_oid = f"ORD_{order_seq}"
                wash_tid = f"TRD_{trade_seq}"
                
                orders.append({
                    "order_id": wash_oid, "user_id": uid, "asset_id": asset_id,
                    "side": opposite_side, "order_type": "LIMIT", "stop_price": None,
                    "target_price": None, "requested_qty": qty, "status": "FILLED",
                    "created_at": wash_time
                })
                trades.append({
                    "trade_id": wash_tid, "order_id": wash_oid, "user_id": uid,
                    "asset_id": asset_id, "side": opposite_side, "execution_price": exec_price,
                    "executed_qty": qty, "fee_inr": fee, "slippage_pct": 0.0000,
                    "executed_at": wash_time
                })

    df_orders = pd.DataFrame(orders).sort_values("created_at").reset_index(drop=True)
    df_trades = pd.DataFrame(trades).sort_values("executed_at").reset_index(drop=True)
    
    logger.info(f"Generated {len(df_orders)} total orders and {len(df_trades)} matched trades.")
    return df_orders, df_trades


# =============================================================================
# 4. MULTI-THRESHOLD STOP-LOSS BACKTEST ENGINE
# =============================================================================

def run_multi_threshold_stop_loss_backtest(
    trades_df: pd.DataFrame,
    candles_df: pd.DataFrame,
    thresholds: list[float] = [0.02, 0.05, 0.10]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Backtests automated stop-loss protection rules (2%, 5%, 10%) on retail buy entries
    against subsequent 48-hour OHLCV candle low prices.
    
    Returns:
    1. Detailed lot-by-lot simulation table.
    2. Executive summary matrix comparing Unmanaged vs. 2%, 5%, 10% stops.
    """
    logger.info("Executing multi-threshold stop-loss diagnostic simulation...")
    
    # Filter for retail buy entries
    retail_buys = trades_df[
        (trades_df["side"] == "BUY") & 
        (trades_df["user_id"].str.startswith("USR_RET_"))
    ].copy()
    
    candle_records = candles_df.to_dict(orient="records")
    simulation_rows = []
    
    for _, trade in retail_buys.iterrows():
        entry_price = trade["execution_price"]
        entry_time = trade["executed_at"]
        qty = trade["executed_qty"]
        entry_fee = trade["fee_inr"]
        
        # Subsequent 48 hours of candles
        forward_candles = [
            c for c in candle_records 
            if entry_time <= c["open_time"] <= entry_time + timedelta(hours=48)
        ]
        
        # Calculate actual unmanaged outcome (min low price over 48h)
        if forward_candles:
            min_future_price = min(c["low_price"] for c in forward_candles)
            terminal_price = forward_candles[-1]["close_price"]
        else:
            min_future_price = entry_price
            terminal_price = entry_price
            
        unmanaged_max_drawdown_pct = round(((min_future_price - entry_price) / entry_price) * 100.0, 2)
        # Unmanaged terminal PnL if closed at window end
        unmanaged_pnl = round(qty * (terminal_price - entry_price) - (entry_fee * 2), 2)
        
        lot_sim = {
            "trade_id": trade["trade_id"],
            "user_id": trade["user_id"],
            "entry_time": entry_time,
            "entry_price": entry_price,
            "qty": qty,
            "unmanaged_max_drawdown_pct": unmanaged_max_drawdown_pct,
            "unmanaged_pnl": unmanaged_pnl
        }
        
        # Test each stop-loss threshold
        for sl in thresholds:
            sl_pct_label = int(sl * 100)
            stop_trigger_price = entry_price * (1.0 - sl)
            
            # Find earliest candle breaching the stop trigger
            breach_candle = next((c for c in forward_candles if c["low_price"] <= stop_trigger_price), None)
            
            if breach_candle is not None:
                # Executed exit with 0.25% market stop slippage
                exit_price = stop_trigger_price * 0.9975
                exit_fee = exit_price * qty * 0.0020
                sl_pnl = round(qty * (exit_price - entry_price) - (entry_fee + exit_fee), 2)
                
                lot_sim[f"sl_{sl_pct_label}_triggered"] = True
                lot_sim[f"sl_{sl_pct_label}_pnl"] = sl_pnl
                lot_sim[f"sl_{sl_pct_label}_exit_time"] = breach_candle["open_time"]
                lot_sim[f"sl_{sl_pct_label}_saved_capital"] = round(sl_pnl - unmanaged_pnl, 2)
            else:
                # Stop was not hit; trade survived the drawdown and captured terminal close
                lot_sim[f"sl_{sl_pct_label}_triggered"] = False
                lot_sim[f"sl_{sl_pct_label}_pnl"] = unmanaged_pnl
                lot_sim[f"sl_{sl_pct_label}_exit_time"] = None
                lot_sim[f"sl_{sl_pct_label}_saved_capital"] = 0.00
                
        simulation_rows.append(lot_sim)
        
    sim_df = pd.DataFrame(simulation_rows)
    
    # Generate Executive Summary Benchmark Table
    summary_metrics = []
    
    # Baseline: Unmanaged
    total_unmanaged_pnl = sim_df["unmanaged_pnl"].sum()
    avg_unmanaged_dd = sim_df["unmanaged_max_drawdown_pct"].mean()
    win_rate_unmanaged = (sim_df["unmanaged_pnl"] > 0).mean() * 100.0
    
    summary_metrics.append({
        "Strategy": "Unmanaged Retail Baseline",
        "Total_Realised_PnL_INR": round(total_unmanaged_pnl, 2),
        "Avg_Max_Drawdown_Pct": round(avg_unmanaged_dd, 2),
        "Win_Rate_Pct": round(win_rate_unmanaged, 2),
        "Capital_Preserved_INR": 0.00,
        "Stop_Trigger_Rate_Pct": 0.00
    })
    
    for sl in thresholds:
        sl_pct_label = int(sl * 100)
        pnl_col = f"sl_{sl_pct_label}_pnl"
        trig_col = f"sl_{sl_pct_label}_triggered"
        
        tot_pnl = sim_df[pnl_col].sum()
        trig_rate = sim_df[trig_col].mean() * 100.0
        win_rate = (sim_df[pnl_col] > 0).mean() * 100.0
        preserved = tot_pnl - total_unmanaged_pnl
        
        # Max drawdown for stop-loss is bounded by the stop threshold
        capped_dd = sim_df["unmanaged_max_drawdown_pct"].apply(lambda d: max(d, -sl * 100.0)).mean()
        
        summary_metrics.append({
            "Strategy": f"Automated {sl_pct_label}% Stop-Loss",
            "Total_Realised_PnL_INR": round(tot_pnl, 2),
            "Avg_Max_Drawdown_Pct": round(capped_dd, 2),
            "Win_Rate_Pct": round(win_rate, 2),
            "Capital_Preserved_INR": round(preserved, 2),
            "Stop_Trigger_Rate_Pct": round(trig_rate, 2)
        })
        
    summary_df = pd.DataFrame(summary_metrics)
    logger.info("Multi-threshold stop-loss diagnostic completed successfully.")
    return sim_df, summary_df


# =============================================================================
# 5. RISK ANOMALY DETECTION & INCIDENT PRIORITISATION (P0 / P1 / P2)
# =============================================================================

def detect_anomalies_and_prioritise(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame = None
) -> pd.DataFrame:
    """
    Evaluates exchange trade/order logs and generates prioritized incident tickets
    strictly matching CoinDCX operational triage criteria:
    
    - P0 (Critical): Stop-Loss / Market order execution slippage > 3.00%
    - P1 (High): Circular / Wash trading (opposite sides, identical qty within 5s)
    - P2 (Medium): Volume anomaly (rolling 24h Z-score > 3.00)
    
    Safe implementation invariant: DataFrame is never mutated in-place; timestamp
    operations are executed on isolated series/copies to prevent index corruption.
    """
    logger.info("Executing risk surveillance and incident triage engine...")
    incidents = []
    
    # -------------------------------------------------------------------------
    # 1. P0 DETECTION: STOP-LOSS & EXECUTION SLIPPAGE ANOMALIES (> 3.0%)
    # -------------------------------------------------------------------------
    # Scan trades where slippage exceeds the 3% critical boundary
    p0_slippage_trades = trades_df[trades_df["slippage_pct"] > 0.0300].copy()
    
    for _, row in p0_slippage_trades.iterrows():
        slippage_val = float(row["slippage_pct"]) * 100.0
        incidents.append({
            "user_id": row["user_id"],
            "asset_id": row["asset_id"],
            "priority_level": "P0",
            "anomaly_category": "STOP_LOSS_SLIPPAGE",
            "metric_value": round(slippage_val, 2),
            "baseline_value": 2.00, # Exchange baseline SLA: slippage must remain <= 2.0%
            "status": "OPEN",
            "detected_at": row["executed_at"],
            "description": (
                f"Critical execution slippage of {slippage_val:.2f}% detected on trade {row['trade_id']} "
                f"(Order {row['order_id']}). Breaches institutional SLA of 2.00%."
            )
        })
    logger.info(f"P0 Surveillance: {len(p0_slippage_trades)} critical slippage incidents flagged.")

    # -------------------------------------------------------------------------
    # 2. P1 DETECTION: CIRCULAR WASH TRADING (Execution interval <= 5.0 seconds)
    # -------------------------------------------------------------------------
    # Sort a copy by user and timestamp to evaluate consecutive trade pairs
    trades_sorted = trades_df.sort_values(by=["user_id", "asset_id", "executed_at"]).reset_index(drop=True)
    
    # Vectorized / pairwise loop across adjacent transactions per user
    for i in range(len(trades_sorted) - 1):
        curr = trades_sorted.iloc[i]
        nxt = trades_sorted.iloc[i + 1]
        
        if (curr["user_id"] == nxt["user_id"] and 
            curr["asset_id"] == nxt["asset_id"] and 
            curr["side"] != nxt["side"] and 
            abs(curr["executed_qty"] - nxt["executed_qty"]) < 1e-5):
            
            delta_seconds = abs((nxt["executed_at"] - curr["executed_at"]).total_seconds())
            if delta_seconds <= 5.0:
                incidents.append({
                    "user_id": curr["user_id"],
                    "asset_id": curr["asset_id"],
                    "priority_level": "P1",
                    "anomaly_category": "WASH_TRADE_DETECTED",
                    "metric_value": round(delta_seconds, 2),
                    "baseline_value": 30.00, # Normal human re-order threshold: 30.0s
                    "status": "OPEN",
                    "detected_at": nxt["executed_at"],
                    "description": (
                        f"Potential circular wash trade: {curr['user_id']} executed opposing "
                        f"{curr['side']}/{nxt['side']} of {curr['executed_qty']:.4f} {curr['asset_id']} "
                        f"within {delta_seconds:.2f}s (Threshold: <= 5.0s)."
                    )
                })
    logger.info(f"P1 Surveillance: Circular wash trading checks completed.")

    # -------------------------------------------------------------------------
    # 3. P2 DETECTION: STATISTICAL ROLLING VOLUME SPIKES (Z-SCORE > 3.0)
    # -------------------------------------------------------------------------
    # Safe non-mutating copy for time-series resampling
    ts_df = trades_df[["executed_at", "executed_qty", "asset_id"]].copy()
    ts_df.rename(columns={"executed_at": "timestamp"}, inplace=True)
    
    # Resample by 1-hour aggregate volume
    hourly_vol = ts_df.set_index("timestamp").resample("1h")["executed_qty"].sum().fillna(0.0)
    
    # 24-hour rolling window parameters
    rolling_mean = hourly_vol.rolling(window=24, min_periods=6).mean()
    rolling_std = hourly_vol.rolling(window=24, min_periods=6).std()
    
    # Compute statistical Z-scores
    z_scores = (hourly_vol - rolling_mean) / (rolling_std + 1e-6)
    
    # Flag hours exceeding the 3-sigma statistical barrier
    spike_hours = z_scores[z_scores > 3.00]
    
    for ts, z_val in spike_hours.items():
        actual_vol = float(hourly_vol.loc[ts])
        mean_vol = float(rolling_mean.loc[ts])
        incidents.append({
            "user_id": "PLATFORM_AGGREGATE",
            "asset_id": "BTC",
            "priority_level": "P2",
            "anomaly_category": "VOLUME_SPIKE",
            "metric_value": round(float(z_val), 2),
            "baseline_value": 3.00,
            "status": "OPEN",
            "detected_at": ts,
            "description": (
                f"Platform volume anomaly detected at {ts}: {actual_vol:.2f} BTC "
                f"(Baseline Mean: {mean_vol:.2f} BTC, Z-Score: {z_val:.2f} > 3.00)."
            )
        })
    logger.info(f"P2 Surveillance: {len(spike_hours)} statistical volume spike anomalies detected.")

    df_incidents = pd.DataFrame(incidents)
    if not df_incidents.empty:
        df_incidents.sort_values(by=["priority_level", "detected_at"], inplace=True)
        df_incidents.reset_index(drop=True, inplace=True)
    return df_incidents


# =============================================================================
# 6. RELATIONAL DATA WAREHOUSE SEED & CSV EXPORT
# =============================================================================

def export_data_warehouse_csvs(output_dir: str = "./data") -> dict[str, pd.DataFrame]:
    """
    Executes the full end-to-end ingestion and simulation pipeline, formatting
    all relational tables strictly matching `schema.sql` and exporting to CSV.
    """
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Initializing full PulseScope Data Warehouse pipeline into {output_dir}...")
    
    # 1. Assets Dimension
    assets_df = pd.DataFrame([
        {"asset_id": "BTC", "symbol": "BTC", "base_currency": "INR", "is_active": True},
        {"asset_id": "ETH", "symbol": "ETH", "base_currency": "INR", "is_active": True},
        {"asset_id": "SOL", "symbol": "SOL", "base_currency": "INR", "is_active": True}
    ])
    
    # 2. Market Candles Fact
    candles_df = fetch_ohlcv_candles(symbol="BTC", days=30, base_price_inr=5500000.0)
    
    # 3. Users Dimension
    users_df = generate_synthetic_users()
    
    # 4. Orders and Trades Facts
    orders_df, trades_df = generate_synthetic_orders_and_trades(users_df, candles_df, asset_id="BTC")
    
    # 5. Incident Queue Fact
    incidents_df = detect_anomalies_and_prioritise(trades_df, orders_df)
    
    # Export all tables to CSV
    tables = {
        "dim_assets": assets_df,
        "dim_users": users_df,
        "fct_market_candles": candles_df,
        "fct_orders": orders_df,
        "fct_trades": trades_df,
        "fct_incident_queue": incidents_df
    }
    
    for name, df in tables.items():
        file_path = os.path.join(output_dir, f"{name}.csv")
        df.to_csv(file_path, index=False)
        logger.info(f"Exported {name} -> {file_path} ({len(df)} records)")
        
    # Also run stop-loss backtest diagnostic and export summary
    sim_df, summary_df = run_multi_threshold_stop_loss_backtest(trades_df, candles_df)
    sim_df.to_csv(os.path.join(output_dir, "diagnostic_stop_loss_lots.csv"), index=False)
    summary_df.to_csv(os.path.join(output_dir, "diagnostic_stop_loss_summary.csv"), index=False)
    logger.info("Exported stop-loss backtest diagnostic tables.")
    
    return tables


# =============================================================================
# 7. MAIN PIPELINE EXECUTION ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print(" PULSESCOPE: CRYPTO TRADING ANALYTICS & ANOMALY PIPELINE")
    print("=" * 80)
    
    warehouse_tables = export_data_warehouse_csvs(output_dir="./data")
    
    print("\n--- Pipeline Execution Summary ---")
    for tbl_name, tbl_df in warehouse_tables.items():
        print(f"Table: {tbl_name:<20} | Rows: {len(tbl_df):<8} | Columns: {len(tbl_df.columns)}")
        
    print("\n--- Incident Triage Queue (Top 5 Incidents) ---")
    incidents = warehouse_tables["fct_incident_queue"]
    if not incidents.empty:
        print(incidents[["priority_level", "anomaly_category", "user_id", "metric_value", "baseline_value"]].head())
    else:
        print("No open incidents detected.")
    print("=" * 80)
