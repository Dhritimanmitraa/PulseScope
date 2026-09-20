-- =============================================================================
-- PulseScope: Relational Data Warehouse Schema & Advanced Financial Views
-- Target Database: PostgreSQL 14+
-- Authors: CoinDCX BI & Analytics Engineering
-- Description: Institutional-grade schema modeling for crypto trading, true FIFO
--              lot accounting, mark-to-market unrealised exposure, and incident triage.
-- =============================================================================

-- Drop views and tables if rebuilding schema
DROP VIEW IF EXISTS view_unrealised_exposure CASCADE;
DROP VIEW IF EXISTS view_fifo_open_lots CASCADE;
DROP VIEW IF EXISTS view_fifo_realised_pnl CASCADE;
DROP TABLE IF EXISTS fct_incident_queue CASCADE;
DROP TABLE IF EXISTS fct_trades CASCADE;
DROP TABLE IF EXISTS fct_orders CASCADE;
DROP TABLE IF EXISTS fct_market_candles CASCADE;
DROP TABLE IF EXISTS dim_assets CASCADE;
DROP TABLE IF EXISTS dim_users CASCADE;

-- -----------------------------------------------------------------------------
-- 1. DIMENSION TABLES
-- -----------------------------------------------------------------------------

-- Dimension: Users (Core trader personas & risk classification)
CREATE TABLE dim_users (
    user_id VARCHAR(32) PRIMARY KEY,
    signup_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    kyc_status VARCHAR(16) NOT NULL CHECK (kyc_status IN ('TIER_1', 'TIER_2', 'TIER_3')),
    persona VARCHAR(24) NOT NULL CHECK (persona IN ('RETAIL_SPECULATOR', 'SYSTEMATIC_TREND', 'HIGH_FREQUENCY_MAKER'))
);

-- Dimension: Assets (Supported crypto instruments with base valuation)
CREATE TABLE dim_assets (
    asset_id VARCHAR(16) PRIMARY KEY,
    symbol VARCHAR(16) NOT NULL UNIQUE,       -- e.g., 'BTC', 'ETH', 'SOL'
    base_currency VARCHAR(8) NOT NULL DEFAULT 'INR',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- -----------------------------------------------------------------------------
-- 2. FACT TABLES
-- -----------------------------------------------------------------------------

-- Fact: Market Candles (Historical 1m/1h reference for valuation & backtesting)
CREATE TABLE fct_market_candles (
    candle_id BIGSERIAL PRIMARY KEY,
    asset_id VARCHAR(16) NOT NULL REFERENCES dim_assets(asset_id),
    open_time TIMESTAMP WITH TIME ZONE NOT NULL,
    open_price NUMERIC(18, 4) NOT NULL CHECK (open_price > 0),
    high_price NUMERIC(18, 4) NOT NULL CHECK (high_price >= open_price),
    low_price NUMERIC(18, 4) NOT NULL CHECK (low_price <= high_price AND low_price > 0),
    close_price NUMERIC(18, 4) NOT NULL CHECK (close_price > 0),
    volume NUMERIC(24, 8) NOT NULL CHECK (volume >= 0),
    CONSTRAINT uq_asset_time UNIQUE (asset_id, open_time)
);

-- Fact: Orders (Trader intent, pre-execution staging & boundary limits)
CREATE TABLE fct_orders (
    order_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES dim_users(user_id),
    asset_id VARCHAR(16) NOT NULL REFERENCES dim_assets(asset_id),
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type VARCHAR(24) NOT NULL CHECK (order_type IN ('MARKET', 'LIMIT', 'STOP_LOSS_MARKET', 'TAKE_PROFIT_LIMIT')),
    stop_price NUMERIC(18, 4),
    target_price NUMERIC(18, 4),
    requested_qty NUMERIC(18, 8) NOT NULL CHECK (requested_qty > 0),
    status VARCHAR(16) NOT NULL CHECK (status IN ('FILLED', 'PARTIALLY_FILLED', 'CANCELLED', 'REJECTED')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Fact: Trades (Matched executions, fee allocations & slippage measurement)
CREATE TABLE fct_trades (
    trade_id VARCHAR(64) PRIMARY KEY,
    order_id VARCHAR(64) NOT NULL REFERENCES fct_orders(order_id),
    user_id VARCHAR(32) NOT NULL REFERENCES dim_users(user_id),
    asset_id VARCHAR(16) NOT NULL REFERENCES dim_assets(asset_id),
    side VARCHAR(4) NOT NULL CHECK (side IN ('BUY', 'SELL')),
    execution_price NUMERIC(18, 4) NOT NULL CHECK (execution_price > 0),
    executed_qty NUMERIC(18, 8) NOT NULL CHECK (executed_qty > 0),
    fee_inr NUMERIC(18, 4) NOT NULL CHECK (fee_inr >= 0),
    slippage_pct NUMERIC(8, 4) NOT NULL DEFAULT 0.0000, -- (Executed Price - Expected Price) / Expected Price
    executed_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- Fact: Incident & Anomaly Queue (Operational triage for market surveillance)
CREATE TABLE fct_incident_queue (
    incident_id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL REFERENCES dim_users(user_id),
    asset_id VARCHAR(16) NOT NULL REFERENCES dim_assets(asset_id),
    priority_level VARCHAR(4) NOT NULL CHECK (priority_level IN ('P0', 'P1', 'P2')),
    anomaly_category VARCHAR(32) NOT NULL CHECK (anomaly_category IN ('STOP_LOSS_SLIPPAGE', 'WASH_TRADE_DETECTED', 'VOLUME_SPIKE')),
    metric_value NUMERIC(18, 4) NOT NULL,
    baseline_value NUMERIC(18, 4) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'INVESTIGATING', 'RESOLVED')),
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- -----------------------------------------------------------------------------
-- 3. PRODUCTION INDEXES
-- -----------------------------------------------------------------------------
CREATE INDEX idx_trades_user_asset_time ON fct_trades (user_id, asset_id, executed_at, trade_id);
CREATE INDEX idx_trades_side ON fct_trades (side);
CREATE INDEX idx_trades_order ON fct_trades (order_id);
CREATE INDEX idx_orders_user_asset_time ON fct_orders (user_id, asset_id, created_at);
CREATE INDEX idx_candles_asset_time_desc ON fct_market_candles (asset_id, open_time DESC);
CREATE INDEX idx_incidents_priority_status ON fct_incident_queue (priority_level, status, detected_at);

-- -----------------------------------------------------------------------------
-- 4. VIEW: TRUE FIFO REALISED PnL ENGINE (view_fifo_realised_pnl)
-- Industrial lot-matching via cumulative interval overlapping window CTEs.
-- Resolves non-deterministic tie-breaks using (executed_at, trade_id).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW view_fifo_realised_pnl AS
WITH BuyLots AS (
    SELECT
        trade_id AS buy_trade_id,
        order_id AS buy_order_id,
        user_id,
        asset_id,
        execution_price AS buy_price,
        executed_qty AS buy_qty,
        fee_inr AS buy_fee,
        executed_at AS buy_time,
        -- Running inventory bounds on the buy accumulation timeline
        SUM(executed_qty) OVER (
            PARTITION BY user_id, asset_id 
            ORDER BY executed_at, trade_id
        ) - executed_qty AS buy_qty_start,
        SUM(executed_qty) OVER (
            PARTITION BY user_id, asset_id 
            ORDER BY executed_at, trade_id
        ) AS buy_qty_end
    FROM fct_trades
    WHERE side = 'BUY'
),
SellLots AS (
    SELECT
        trade_id AS sell_trade_id,
        order_id AS sell_order_id,
        user_id,
        asset_id,
        execution_price AS sell_price,
        executed_qty AS sell_qty,
        fee_inr AS sell_fee,
        executed_at AS sell_time,
        -- Running inventory bounds on the sell liquidation timeline
        SUM(executed_qty) OVER (
            PARTITION BY user_id, asset_id 
            ORDER BY executed_at, trade_id
        ) - executed_qty AS sell_qty_start,
        SUM(executed_qty) OVER (
            PARTITION BY user_id, asset_id 
            ORDER BY executed_at, trade_id
        ) AS sell_qty_end
    FROM fct_trades
    WHERE side = 'SELL'
),
MatchedIntervals AS (
    SELECT
        b.user_id,
        b.asset_id,
        b.buy_trade_id,
        s.sell_trade_id,
        b.buy_order_id,
        s.sell_order_id,
        b.buy_time,
        s.sell_time,
        b.buy_price,
        s.sell_price,
        -- Exact overlapping intersection of buy lot [start, end] and sell lot [start, end]
        LEAST(b.buy_qty_end, s.sell_qty_end) - GREATEST(b.buy_qty_start, s.sell_qty_start) AS matched_qty,
        -- Prorated exchange fees allocated to the matched fraction
        ROUND(b.buy_fee * (LEAST(b.buy_qty_end, s.sell_qty_end) - GREATEST(b.buy_qty_start, s.sell_qty_start)) / b.buy_qty, 4) AS allocated_buy_fee,
        ROUND(s.sell_fee * (LEAST(b.buy_qty_end, s.sell_qty_end) - GREATEST(b.buy_qty_start, s.sell_qty_start)) / s.sell_qty, 4) AS allocated_sell_fee
    FROM BuyLots b
    INNER JOIN SellLots s 
        ON b.user_id = s.user_id 
       AND b.asset_id = s.asset_id
       -- Interval intersection condition: A_start < B_end AND A_end > B_start
       AND b.buy_qty_start < s.sell_qty_end 
       AND b.buy_qty_end > s.sell_qty_start
)
SELECT
    m.user_id,
    u.persona,
    m.asset_id,
    a.symbol AS asset_symbol,
    m.buy_trade_id,
    m.sell_trade_id,
    m.buy_time,
    m.sell_time,
    m.buy_price,
    m.sell_price,
    m.matched_qty,
    -- Financial Gross Realised PnL: Matched Volume * (Exit Price - Entry Price)
    ROUND(m.matched_qty * (m.sell_price - m.buy_price), 4) AS gross_realised_pnl,
    -- Total Allocated Exchange Execution Fees (Maker/Taker)
    m.allocated_buy_fee,
    m.allocated_sell_fee,
    (m.allocated_buy_fee + m.allocated_sell_fee) AS total_allocated_fee,
    -- Financial Net Realised PnL: Gross Realised PnL - Total Allocated Fees
    ROUND(
        (m.matched_qty * (m.sell_price - m.buy_price)) - (m.allocated_buy_fee + m.allocated_sell_fee), 
        4
    ) AS net_realised_pnl,
    -- Return On Investment % on cost basis for this closed lot
    ROUND(
        (((m.sell_price - m.buy_price) / m.buy_price) * 100.0), 
        2
    ) AS return_pct,
    -- Lot Holding Duration (Minutes)
    ROUND(EXTRACT(EPOCH FROM (m.sell_time - m.buy_time)) / 60.0, 2) AS holding_duration_minutes
FROM MatchedIntervals m
JOIN dim_users u ON m.user_id = u.user_id
JOIN dim_assets a ON m.asset_id = a.asset_id;

-- -----------------------------------------------------------------------------
-- 5. VIEW: FIFO OPEN LOT BREAKDOWN (view_fifo_open_lots)
-- Tracks unliquidated buy lots remaining in inventory post-FIFO matching.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW view_fifo_open_lots AS
WITH TotalLiquidated AS (
    SELECT
        user_id,
        asset_id,
        COALESCE(SUM(executed_qty), 0.0) AS total_sold_qty
    FROM fct_trades
    WHERE side = 'SELL'
    GROUP BY user_id, asset_id
),
BuyLotsWithBounds AS (
    SELECT
        b.trade_id AS buy_trade_id,
        b.order_id AS buy_order_id,
        b.user_id,
        b.asset_id,
        b.execution_price AS buy_price,
        b.executed_qty AS initial_buy_qty,
        b.fee_inr AS buy_fee,
        b.executed_at AS buy_time,
        SUM(b.executed_qty) OVER (
            PARTITION BY b.user_id, b.asset_id 
            ORDER BY b.executed_at, b.trade_id
        ) - b.executed_qty AS buy_qty_start,
        SUM(b.executed_qty) OVER (
            PARTITION BY b.user_id, b.asset_id 
            ORDER BY b.executed_at, b.trade_id
        ) AS buy_qty_end,
        COALESCE(tl.total_sold_qty, 0.0) AS total_sold_qty
    FROM fct_trades b
    LEFT JOIN TotalLiquidated tl 
        ON b.user_id = tl.user_id 
       AND b.asset_id = tl.asset_id
    WHERE b.side = 'BUY'
)
SELECT
    user_id,
    asset_id,
    buy_trade_id,
    buy_time,
    buy_price,
    initial_buy_qty,
    -- Remaining open unliquidated quantity
    ROUND(
        buy_qty_end - GREATEST(buy_qty_start, total_sold_qty), 
        8
    ) AS remaining_open_qty,
    -- Cost basis tied up in this remaining lot
    ROUND(
        (buy_qty_end - GREATEST(buy_qty_start, total_sold_qty)) * buy_price, 
        4
    ) AS open_cost_basis,
    -- Prorated unamortized entry fee
    ROUND(
        buy_fee * ((buy_qty_end - GREATEST(buy_qty_start, total_sold_qty)) / initial_buy_qty), 
        4
    ) AS open_entry_fee
FROM BuyLotsWithBounds
-- Only select lots where buy_qty_end exceeds cumulative liquidated sales
WHERE buy_qty_end > total_sold_qty;

-- -----------------------------------------------------------------------------
-- 6. VIEW: MARK-TO-MARKET UNREALISED EXPOSURE (view_unrealised_exposure)
-- Aggregates open inventory per user/asset, values it at latest candle close,
-- and evaluates gross/net unrealised PnL and exposure drawdown.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW view_unrealised_exposure AS
WITH LatestMarketPrice AS (
    -- Get latest market valuation candle per asset using DISTINCT ON
    SELECT DISTINCT ON (asset_id)
        asset_id,
        close_price AS latest_market_price,
        open_time AS valuation_timestamp
    FROM fct_market_candles
    ORDER BY asset_id, open_time DESC
),
AggregatedOpenInventory AS (
    SELECT
        ol.user_id,
        ol.asset_id,
        SUM(ol.remaining_open_qty) AS total_open_qty,
        SUM(ol.open_cost_basis) AS total_open_cost_basis,
        SUM(ol.open_entry_fee) AS total_open_fees,
        -- Weighted average entry price across all open lots
        ROUND(SUM(ol.open_cost_basis) / NULLIF(SUM(ol.remaining_open_qty), 0), 4) AS weighted_avg_entry_price
    FROM view_fifo_open_lots ol
    GROUP BY ol.user_id, ol.asset_id
)
SELECT
    inv.user_id,
    u.persona,
    inv.asset_id,
    a.symbol AS asset_symbol,
    inv.total_open_qty,
    inv.weighted_avg_entry_price,
    lmp.latest_market_price,
    inv.total_open_cost_basis,
    -- Mark-To-Market Value: Open Qty * Current Market Price
    ROUND(inv.total_open_qty * lmp.latest_market_price, 4) AS mark_to_market_value,
    -- Gross Unrealised PnL: MTM Value - Cost Basis
    ROUND(
        (inv.total_open_qty * lmp.latest_market_price) - inv.total_open_cost_basis, 
        4
    ) AS gross_unrealised_pnl,
    -- Net Unrealised PnL: Gross Unrealised PnL - Entry Execution Fees
    ROUND(
        ((inv.total_open_qty * lmp.latest_market_price) - inv.total_open_cost_basis) - inv.total_open_fees, 
        4
    ) AS net_unrealised_pnl,
    -- Unrealised PnL % (Drawdown / Gain relative to cost basis)
    ROUND(
        (((lmp.latest_market_price - inv.weighted_avg_entry_price) / NULLIF(inv.weighted_avg_entry_price, 0)) * 100.0), 
        2
    ) AS unrealised_return_pct,
    lmp.valuation_timestamp
FROM AggregatedOpenInventory inv
JOIN dim_users u ON inv.user_id = u.user_id
JOIN dim_assets a ON inv.asset_id = a.asset_id
JOIN LatestMarketPrice lmp ON inv.asset_id = lmp.asset_id;
