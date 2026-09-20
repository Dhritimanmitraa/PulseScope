# PulseScope: Senior BI & Analytics Engineering Interview Guide (CoinDCX)

**Role:** BI & Data Analytics Candidate (Cryptocurrency Exchange Operations)  
**Interviewer:** Analytics Lead / Senior BI Engineer, CoinDCX  
**Focus Areas:** SQL Window Internals, Star Schema Design, Statistical Surveillance, DAX Engine Mechanics, Exchange Economics  

---

## QUESTION 1: SQL Execution & FIFO Accounting Edge Cases

### The Interviewer's Question:
> *"In your `view_fifo_realised_pnl`, you implemented true FIFO lot-matching using cumulative window functions and an interval intersection join. Walk me through the query execution plan:*
> 1. *What happens in sub-second trading environments when multiple fills occur with identical timestamps (`executed_at`), and how did you prevent non-deterministic lot matching?*
> 2. *How does your interval condition `b.buy_qty_start < s.sell_qty_end AND b.buy_qty_end > s.sell_qty_start` handle a single large sell order that liquidates four distinct prior buy lots?*
> 3. *What is the computational complexity of that theta-join across 10 million trades, and how would you optimize it for production ETL?"*

### High-Scoring Candidate Response:

#### 1. Non-Deterministic Tie-Breaks on Sub-Second Timestamps
"In high-frequency or API trading, multiple orders execute within the same millisecond. If the window function orders solely by `ORDER BY executed_at`, PostgreSQL's window operator cannot guarantee a deterministic ordering among tied rows across query plans or index re-scans. This causes cumulative running totals (`buy_qty_start`, `buy_qty_end`) to shift arbitrarily between executions, resulting in mismatched cost bases.

To eliminate this non-determinism, I enforced a compound sorting key:
```sql
SUM(executed_qty) OVER (
    PARTITION BY user_id, asset_id 
    ORDER BY executed_at, trade_id
)
```
Because `trade_id` is a monotonically increasing surrogate primary key, the sort order is guaranteed to be strictly unique and deterministic across all executions."

#### 2. 1-to-Many Lot Splitting Across Interval Overlaps
"Rather than using procedural row-by-row cursor loops or recursive CTEs (which are slow and non-vectorized in PostgreSQL), my model treats trade quantities as continuous intervals on a cumulative volume timeline:
- Buy Lot $i$: $[B_{\text{start}}, B_{\text{end}}]$
- Sell Lot $j$: $[S_{\text{start}}, S_{\text{end}}]$

When a single large sell order of $1.0\text{ BTC}$ executes against four prior buy lots of $0.25\text{ BTC}$ each:
- The join condition `b.buy_qty_start < s.sell_qty_end AND b.buy_qty_end > s.sell_qty_start` evaluates to `TRUE` for all four buy rows because the sell interval $[0.0, 1.0]$ completely spans each individual buy interval ($[0.0, 0.25], [0.25, 0.50], [0.50, 0.75], [0.75, 1.00]$).
- For each matched slice, `LEAST(B_end, S_end) - GREATEST(B_start, S_start)` accurately outputs $0.25\text{ BTC}$.
- Execution fees are prorated by multiplying the lot's initial fee by $\frac{\text{matched\_qty}}{\text{executed\_qty}}$, ensuring exact accounting balance down to 4 decimal places with zero floating residue."

#### 3. Computational Complexity & Production Optimization
"A theta join with inequalities (`<` and `>`) cannot utilize standard Hash Joins; PostgreSQL must fall back to a Nested Loop Join or Merge Join within each partition. If a user has $N$ buys and $M$ sells, the naive worst-case comparison complexity is $O(N \times M)$ per `(user_id, asset_id)`. Over 10 million raw trades, evaluating this in a dynamic SQL view on every BI refresh causes query timeouts.

In production, I optimize this architecture through three steps:
1. **Composite B-Tree Indexing**: `CREATE INDEX idx_trades_fifo ON fct_trades (user_id, asset_id, side, executed_at, trade_id) INCLUDE (executed_qty, execution_price, fee_inr);` to enable Index-Only Scans.
2. **Incremental Ledger Materialization**: Instead of recalculating historical closed lots on every dashboard load, I schedule an hourly dbt or Airflow batch model that writes matched lots to a physical table (`fct_closed_lots_ledger`).
3. **Partition Watermarking**: Closed inventory is pruned; the query only processes open lots by tracking the running watermark `total_sold_qty` per user."

---

## QUESTION 2: Dimensional Data Modeling & Historical Immutability

### The Interviewer's Question:
> *"In cryptocurrency exchanges, asset prices fluctuate every second, and orders go through multiple status changes across different timestamps (`created_at`, `executed_at`, `closed_at`).*
> 1. *Why is it an anti-pattern to join historical trades directly to `fct_market_candles` to compute Realised PnL?*
> 2. *How does your model handle role-playing dates between order staging, trade execution, and lot liquidation in Power BI without introducing circular filter paths?"*

### High-Scoring Candidate Response:

#### 1. Historical Realised PnL vs. Mark-to-Market Valuation
"Joining trades to floating market candle tables to calculate Realised PnL violates the core financial accounting principle of **Transaction Immutability**:
- **Realised PnL is a closed accounting event**: Once a buy lot is matched against a sell execution, its cost basis and exit value are permanently locked. They depend strictly on the historical `execution_price` recorded in `fct_trades` at execution time and must never fluctuate with subsequent market swings.
- **Unrealised PnL is a floating Mark-to-Market (MTM) event**: Floating candle prices from `fct_market_candles` belong strictly in `view_unrealised_exposure` to value remaining unliquidated inventory (`view_fifo_open_lots`). 

If a BI engineer joins historical closed trades to market candles, any restatement or re-indexing of market candles will mutate audited historical revenue figures, which is a critical regulatory violation."

#### 2. Role-Playing Date Dimensions Without Bidirectional Cycles
"A single transaction lifecycle touches multiple timestamps:
- `fct_orders.created_at` (Trader Intent)
- `fct_trades.executed_at` (Exchange Execution)
- `view_fifo_realised_pnl.sell_time` (Financial Realisation Event)
- `fct_incident_queue.detected_at` (Surveillance Flag)

In Power BI, connecting all these timestamps directly to `dim_date` with active relationships creates ambiguous multi-path loops. I resolved this using standard Kimball role-playing design:
1. **Primary Active Relationship**: `dim_date[Date]` is connected via a single active `1:*` relationship to the primary realization timestamp for each specific fact (`view_fifo_realised_pnl[sell_time]` for PnL; `fct_trades[executed_at]` for volume).
2. **Inactive Relationships with `USERELATIONSHIP()`**: Secondary timestamps (such as entry order time) are modeled as inactive relationships and activated explicitly in DAX when needed:
   ```dax
   [Volume by Order Creation Date] = 
   CALCULATE (
       [Total Notional Volume],
       USERELATIONSHIP ( fct_orders[created_at], dim_date[Date] )
   )
   ```
This preserves single-direction filter propagation and eliminates circular ambiguity."

---

## QUESTION 3: Risk Surveillance & Statistical Anomaly Mathematics

### The Interviewer's Question:
> *"In `analytics_engine.py`, you detect P2 Volume Spikes using a rolling 24-hour Z-Score ($Z > 3.0$).*
> 1. *Why does a simple rolling 24-hour Z-Score generate persistent false positives during a structural regime shift (such as a Bitcoin ETF approval or major macro announcement)?*
> 2. *Mathematically, how would you tune the anomaly engine to distinguish between an inorganic flash spike (surveillance alert) and an organic market regime shift?"*

### High-Scoring Candidate Response:

#### 1. The Gaussian Fallacy & Window Lag During Regime Shifts
"The rolling Z-Score formula $Z_t = \frac{X_t - \mu_{24h}}{\sigma_{24h}}$ suffers from two major structural weaknesses in crypto microstructure:
1. **Heavy-Tailed, Non-Gaussian Distribution**: Crypto trading volume is log-normally distributed with extreme leptokurtosis (fat tails). Assuming a normal distribution and a static $3\sigma$ threshold generates excessive Type I errors (false positives) during normal volatility clustering.
2. **Window Contamination & Lag**: During a structural macro event (e.g., ETF approval), platform volume jumps from an average of $50\text{ BTC/hr}$ to $500\text{ BTC/hr}$ and stays elevated for days. Because the rolling window still carries low-volume hours from before the announcement, $\mu_{24h}$ remains low, causing **every single hour for the next 18 hours to score $Z > 3.0$**. The surveillance team suffers alert fatigue from continuous false positives on what is actually healthy, organic exchange business."

#### 2. Advanced Mathematical Tuning for Market Surveillance
"To make anomaly scoring production-grade, I implement three statistical refinements:

1. **Log-Transform Normalization**:
   Instead of raw volume $V_t$, compute the Z-Score on log volume:
   $$Y_t = \ln(V_t + 1)$$
   This compresses exponential skewness and brings the distribution closer to Gaussian behavior.

2. **Median Absolute Deviation (MAD) Instead of Standard Deviation**:
   Extreme outliers artificially inflate standard deviation $\sigma$, which paradoxically masks subsequent spikes. I replace mean and standard deviation with median and MAD:
   $$\text{Modified } Z_t = 0.6745 \times \frac{X_t - \text{Median}(X)}{\text{MAD}}$$
   where $\text{MAD} = \text{Median}(|X_t - \text{Median}(X)|)$. MAD is robust against outlier distortion up to a 50% breakdown point.

3. **Dual-Window Persistence Filter (Flash Spike vs. Regime Shift)**:
   - An **inorganic flash manipulation spike** (e.g. wash trading or pump-and-dump) spikes sharply in hour $t$ and immediately collapses in hour $t+1$.
   - An **organic regime shift** stays elevated across consecutive periods.
   
   I implement a persistence check:
   $$\text{Alert Triggered} = (Z_t > 3.0) \land (Z_{t+1} < 1.5)$$
   If $Z$ remains elevated for 3 consecutive hours, the engine classifies it as an `ORGANIC_REGIME_SHIFT`, suppresses individual P2 tickets, and adjusts the rolling baseline upward."

---

## QUESTION 4: Power BI & DAX Engine Performance Optimization

### The Interviewer's Question:
> *"Look at this common DAX pattern for calculating cumulative PnL:*
> ```dax
> Cumulative_PnL = 
> CALCULATE(
>     [Net Realised PnL],
>     FILTER(
>         ALLSELECTED(dim_date[Date]),
>         dim_date[Date] <= MAX(dim_date[Date])
>     )
> )
> ```
> *When deployed over a 50-million-row trade fact table, this visual takes 18 seconds to render and triggers memory allocation warnings. Why does this happen inside the VertiPaq Storage Engine and Formula Engine, and how do you rewrite it for sub-second response times?"*

### High-Scoring Candidate Response:

#### 1. Why `FILTER(ALLSELECTED(...))` Chokes the DAX Engine
"The performance bottleneck is caused by a breakdown in how Power BI delegates work between the **Storage Engine (VertiPaq)** and the **Formula Engine (FE)**:
1. **The Formula Engine Bottleneck**: `FILTER()` is an iterative row-by-row function. When wrapped inside `CALCULATE(..., FILTER(ALLSELECTED(...)))`, the query cannot be fulfilled entirely by the multi-threaded, in-memory VertiPaq Storage Engine.
2. **Context Transition & Cartesian Scans**: For every visible date coordinate in the area chart visual, the Formula Engine is forced to materialize an uncompressed temporary table of all dates up to that point and evaluate context transitions for each row.
3. On a 50-million row dataset, this causes millions of callback iterations between FE and SE, creating thread contention, exhausting cache limits, and inflating visual render time to 18 seconds."

#### 2. The High-Performance Optimization Strategy
"I optimize this at both the DAX layer and the Data Warehouse layer:

**Step 1: Rewriting with VertiPaq-Native Time Intelligence**
Instead of `FILTER()`, use set-based filters that VertiPaq evaluates natively using bitmapped scan operations:
```dax
[Cumulative Realised Net PnL (Optimized)] = 
CALCULATE (
    [Net Realised PnL],
    DATESBETWEEN (
        dim_date[Date],
        BLANK (),
        MAX ( dim_date[Date] )
    )
)
```
Or utilizing the modern DAX `WINDOW()` function (Power BI 2023+), which evaluates running totals in a single pass without scanning the entire table repeatedly.

**Step 2: Dimensional Pre-Aggregation (Aggregation Tables)**
For high-cardinality financial analytics, running running-totals over raw execution-level trade records is architectural malpractice. I implement an aggregated summary table in the warehouse:
`agg_daily_user_pnl` (`date_key`, `user_id`, `asset_id`, `daily_realised_pnl`, `daily_volume`).

By mapping Power BI Aggregations to `agg_daily_user_pnl`, the query scans **365 rows per user per year** instead of millions of trade rows. VertiPaq answers the cumulative PnL curve in under **80 milliseconds**."

---

## QUESTION 5: Product Sense, Exchange Economics & Risk Trade-Offs

### The Interviewer's Question:
> *"In your Executive Memo, you proved that enforcing a 5% stop-loss would have saved retail traders ₹14.3 Lakhs (a 77.7% reduction in drawdown). If automated stop-losses preserve retail capital so effectively, why doesn't CoinDCX simply mandate an automated 5% stop-loss on every retail trade by default? Walk me through the trade-offs between fee revenue, user churn, and exchange liquidity."*

### High-Scoring Candidate Response:

#### 1. The Core Tension: User Protection vs. Trader Autonomy
"While saving ₹14.3L looks unequivocally positive on a retrospective backtest, mandating a forced 5% stop-loss platform-wide creates severe unintended operational, product, and economic consequences:

1. **Market Noise & Whipsaw Liquidations**:
   Crypto assets like BTC and ETH have average intraday volatilities of 3% to 6%. In a volatile range, price frequently wicks down 5.1% to sweep liquidity before rallying 15% higher. If the exchange forcibly liquidates retail positions at the bottom of normal wicks, users are locked into real cash losses while watching the market rebound without them. This creates intense user anger and accusations of exchange price manipulation.

2. **The Liquidity Cascade & Slippage Death Spiral**:
   If an exchange aggregates hundreds of retail accounts into an identical 5% stop threshold, the order book develops a massive liquidity cluster at that price point. A minor market sell-off hitting that trigger dumps hundreds of Market Sell Orders simultaneously into the bid book. This exhausts available bids, causing catastrophic execution slippage (>5%) and triggering cascading liquidations down the book (a flash crash).

3. **Regulatory & Legal Classification**:
   An exchange is an **execution venue**, not a registered discretionary portfolio manager. Forcibly closing a user's position without their affirmative instruction breaches customer agreements and exposes the exchange to legal liability for forced liquidation of un-leveraged spot assets."

#### 2. The Exchange Economic Model (Fee Volume vs. Customer Lifetime Value)
"From a revenue standpoint:
- In the short term, unmanaged retail traders generate high churn, but their continuous re-funding generates transaction fees.
- However, our data proves that severe drawdown (-35%) leads to **account capitulation**, where users abandon the platform permanently after 60 days.
- A retail trader whose capital is preserved trades at a **3.8x monthly turnover velocity**, generating **62.1% more total exchange fees over a 12-month period** (₹2.95L vs ₹1.82L in our cohort)."

#### 3. Strategic Product Recommendation: Nudge Architecture
"Instead of an authoritarian mandatory stop, the optimal CoinDCX product strategy is **Choice Architecture / Behavioral Nudges**:
1. **One-Tap Smart Guardrail Default**: During order entry on volatile pairs, pre-fill a recommended 5% trailing stop with a toggle switch showing estimated capital preserved.
2. **Dynamic Volatility Warnings**: Display an in-app prompt if an unmanaged position experiences unrealised loss exceeding -10%: *'Positions past -10% have an 82% historical probability of reaching -25% drawdown.'*
3. **VIP Fee Tier Rebates**: Offer a 2 bps fee discount for retail users who maintain bracket orders on at least 80% of their trades.

This preserves trader agency, prevents liquidity cascading, increases Customer Lifetime Value (LTV), and grows exchange fee revenue sustainably."
