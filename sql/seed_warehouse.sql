-- =============================================================================
-- PulseScope: Relational Data Warehouse Seeding Script
-- Target Database: PostgreSQL 14+
-- Description: Ingests the pipeline-generated CSV data into PostgreSQL warehouse tables.
-- =============================================================================

-- Execute inside psql shell or client with appropriate permissions
\copy dim_assets (asset_id, symbol, base_currency, is_active) FROM './data/dim_assets.csv' WITH (FORMAT csv, HEADER true);
\copy dim_users (user_id, signup_timestamp, kyc_status, persona) FROM './data/dim_users.csv' WITH (FORMAT csv, HEADER true);
\copy fct_market_candles (asset_id, open_time, open_price, high_price, low_price, close_price, volume) FROM './data/fct_market_candles.csv' WITH (FORMAT csv, HEADER true);
\copy fct_orders (order_id, user_id, asset_id, side, order_type, stop_price, target_price, requested_qty, status, created_at) FROM './data/fct_orders.csv' WITH (FORMAT csv, HEADER true);
\copy fct_trades (trade_id, order_id, user_id, asset_id, side, execution_price, executed_qty, fee_inr, slippage_pct, executed_at) FROM './data/fct_trades.csv' WITH (FORMAT csv, HEADER true);
\copy fct_incident_queue (user_id, asset_id, priority_level, anomaly_category, metric_value, baseline_value, status, detected_at) FROM './data/fct_incident_queue.csv' WITH (FORMAT csv, HEADER true);

-- Verification Queries
SELECT 'dim_assets' AS table_name, count(*) AS row_count FROM dim_assets
UNION ALL
SELECT 'dim_users', count(*) FROM dim_users
UNION ALL
SELECT 'fct_market_candles', count(*) FROM fct_market_candles
UNION ALL
SELECT 'fct_orders', count(*) FROM fct_orders
UNION ALL
SELECT 'fct_trades', count(*) FROM fct_trades
UNION ALL
SELECT 'fct_incident_queue', count(*) FROM fct_incident_queue;
