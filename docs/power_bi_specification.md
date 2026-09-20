# PulseScope: Power BI Semantic Model & DAX Implementation Specification

**Document Version:** 1.0.0 (Production)  
**Target Environment:** Microsoft Power BI Desktop / Power BI Service (Import Mode or DirectQuery on PostgreSQL)  
**Author:** CoinDCX BI & Analytics Engineering  

---

## 1. Star Schema Architecture & Data Model

### 1.1 Model Topology
PulseScope utilizes an institutional-grade **Constellation Star Schema**. Dimension tables cleanly filter execution facts, financial accounting views, and surveillance anomaly queues through **one-to-many (1:\*) single-direction relationships**.

```
                           ┌────────────────────────┐
                           │        dim_date        │
                           │  (Role-Playing Time)   │
                           └───────────┬────────────┘
                                       │ 1
                         ┌─────────────┼─────────────┐
                         │ *           │ *           │ *
                         ▼             ▼             ▼
┌──────────────┐ 1    ┌──────────────┐ 1    ┌──────────────────────┐
│  dim_users   ├─────►│  fct_trades  │◄─────┤      dim_assets      │
│ (Persona/KYC)│      │ (Executions) │      │ (BTC, ETH, SOL, INR) │
└──────┬───────┘      └──────────────┘      └──────────┬───────────┘
       │ 1                                             │ 1
       ├─────────────────────┬─────────────────────────┤
       │ *                   │ *                       │ *
       ▼                     ▼                         ▼
┌──────────────────┐  ┌──────────────────────┐  ┌─────────────────────────┐
│ view_fifo_pnl    │  │ view_unrealised_exp  │  │   fct_incident_queue    │
│ (Closed Lots)    │  │ (Open Inventory MTM) │  │  (P0 / P1 / P2 Triage)  │
└──────────────────┘  └──────────────────────┘  └─────────────────────────┘
```

### 1.2 Table Relationship Catalog
| Primary Table (1) | Primary Key | Foreign Table (*) | Foreign Key | Cardinality | Cross Filter | Enforce Referential Integrity |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `dim_users` | `user_id` | `fct_orders` | `user_id` | 1 : Many | Single (`dim_users` filters `fct_orders`) | Yes |
| `dim_users` | `user_id` | `fct_trades` | `user_id` | 1 : Many | Single (`dim_users` filters `fct_trades`) | Yes |
| `dim_users` | `user_id` | `view_fifo_realised_pnl` | `user_id` | 1 : Many | Single (`dim_users` filters `view_fifo_pnl`) | Yes |
| `dim_users` | `user_id` | `view_unrealised_exposure` | `user_id` | 1 : Many | Single (`dim_users` filters `view_unrealised`) | Yes |
| `dim_users` | `user_id` | `fct_incident_queue` | `user_id` | 1 : Many | Single (`dim_users` filters `fct_incident_queue`) | No (handles platform aggregate) |
| `dim_assets` | `asset_id` | `fct_orders` | `asset_id` | 1 : Many | Single (`dim_assets` filters `fct_orders`) | Yes |
| `dim_assets` | `asset_id` | `fct_trades` | `asset_id` | 1 : Many | Single (`dim_assets` filters `fct_trades`) | Yes |
| `dim_assets` | `asset_id` | `view_fifo_realised_pnl` | `asset_id` | 1 : Many | Single (`dim_assets` filters `view_fifo_pnl`) | Yes |
| `dim_assets` | `asset_id` | `view_unrealised_exposure` | `asset_id` | 1 : Many | Single (`dim_assets` filters `view_unrealised`) | Yes |
| `dim_assets` | `asset_id` | `fct_incident_queue` | `asset_id` | 1 : Many | Single (`dim_assets` filters `fct_incident_queue`) | Yes |
| `dim_date` | `Date` | `fct_trades` | `DateKey` | 1 : Many | Single (`dim_date` filters `fct_trades`) | Yes |
| `dim_date` | `Date` | `view_fifo_realised_pnl` | `DateKey` | 1 : Many | Single (`dim_date` filters `view_fifo_pnl`) | Yes |
| `dim_date` | `Date` | `fct_incident_queue` | `DateKey` | 1 : Many | Single (`dim_date` filters `fct_incident_queue`) | Yes |
| `fct_orders` | `order_id` | `fct_trades` | `order_id` | 1 : Many | Single (`fct_orders` filters `fct_trades`) | Yes |

---

## 2. Dynamic Calendar Dimension (DAX Calculated Table)

Create this table in Power BI by selecting **Modeling > New Table**:

```dax
dim_date = 
VAR MinDate = DATE(2026, 1, 1)
VAR MaxDate = DATE(2026, 12, 31)
RETURN
    ADDCOLUMNS (
        CALENDAR ( MinDate, MaxDate ),
        "DateKey", INT(FORMAT([Date], "YYYYMMDD")),
        "Year", YEAR ( [Date] ),
        "Quarter", "Q" & FORMAT ( [Date], "Q" ),
        "YearQuarter", FORMAT ( [Date], "YYYY" ) & "-Q" & FORMAT ( [Date], "Q" ),
        "MonthNumber", MONTH ( [Date] ),
        "MonthName", FORMAT ( [Date], "MMMM" ),
        "MonthShort", FORMAT ( [Date], "MMM" ),
        "YearMonth", FORMAT ( [Date], "YYYY-MM" ),
        "DayOfMonth", DAY ( [Date] ),
        "DayOfWeekNumber", WEEKDAY ( [Date], 2 ), -- 1 = Monday, 7 = Sunday
        "DayOfWeekName", FORMAT ( [Date], "DDDD" ),
        "DayOfWeekShort", FORMAT ( [Date], "DDD" ),
        "IsWeekend", IF ( WEEKDAY ( [Date], 2 ) IN { 6, 7 }, 1, 0 ),
        "TradingWeek", "W" & FORMAT ( WEEKNUM ( [Date], 2 ), "00" )
    )
```

In Power BI, mark `dim_date` as a **Date Table** with the `Date` column as the primary timestamp key.

---

## 3. Production DAX Measure Library

Organize these measures into dedicated measure display folders within a disconnected table named `_Measures`.

### 3.1 Financial Volumes, Turnover & Fee Take Rate

```dax
-- 1. Total Notional Traded Volume (INR)
[Total Notional Volume] = 
SUMX (
    fct_trades,
    fct_trades[execution_price] * fct_trades[executed_qty]
)

-- 2. Total Exchange Execution Fees (INR)
[Total Fees INR] = 
SUM ( fct_trades[fee_inr] )

-- 3. Exchange Fee Take Rate (in Basis Points: bps)
-- 1 bps = 0.01% = 0.0001
[Platform Fee Take Rate (bps)] = 
DIVIDE ( [Total Fees INR], [Total Notional Volume], 0 ) * 10000

-- 4. Total Matched Trade Executions
[Total Trades Count] = 
COUNTROWS ( fct_trades )

-- 5. Total Cumulative Closed Trade Volume
[Total Matched Qty] = 
SUM ( view_fifo_realised_pnl[matched_qty] )
```

### 3.2 True FIFO Realised PnL & Time Intelligence

```dax
-- 6. Gross Realised PnL (Pre-Fee Closed Lots)
[Gross Realised PnL] = 
SUM ( view_fifo_realised_pnl[gross_realised_pnl] )

-- 7. Net Realised PnL (True FIFO Net of Maker/Taker Execution Fees)
[Net Realised PnL] = 
SUM ( view_fifo_realised_pnl[net_realised_pnl] )

-- 8. Cumulative Net Realised PnL (Time-Series Area Curve)
-- Computes unbroken running equity curve across filtered calendar timeline
[Cumulative Realised Net PnL] = 
VAR MaxVisibleDate = MAX ( dim_date[Date] )
RETURN
    CALCULATE (
        [Net Realised PnL],
        FILTER (
            ALLSELECTED ( dim_date[Date] ),
            dim_date[Date] <= MaxVisibleDate
        )
    )

-- 9. Realised Return on Investment %
[Realised ROI %] = 
VAR TotalCostBasis = 
    SUMX (
        view_fifo_realised_pnl,
        view_fifo_realised_pnl[matched_qty] * view_fifo_realised_pnl[buy_price]
    )
RETURN
    DIVIDE ( [Net Realised PnL], TotalCostBasis, 0 ) * 100
```

### 3.3 Mark-to-Market (MTM) Unrealised Exposure

```dax
-- 10. Total Mark-to-Market Portfolio Valuation (INR)
[MTM Portfolio Value] = 
SUM ( view_unrealised_exposure[mark_to_market_value] )

-- 11. Total Open Position Cost Basis (INR)
[Total Open Cost Basis] = 
SUM ( view_unrealised_exposure[total_open_cost_basis] )

-- 12. Net Unrealised PnL (Mark-to-Market Net of Entry Fees)
[Net Unrealised PnL] = 
SUM ( view_unrealised_exposure[net_unrealised_pnl] )

-- 13. Portfolio Unrealised Return %
[Unrealised Return %] = 
DIVIDE ( [Net Unrealised PnL], [Total Open Cost Basis], 0 ) * 100
```

### 3.4 Stop-Loss Diagnostic & Capital Preservation Metrics

```dax
-- 14. Unmanaged Retail Realised PnL Baseline
[Unmanaged Retail PnL Baseline] = 
CALCULATE (
    SUM ( diagnostic_stop_loss_summary[Total_Realised_PnL_INR] ),
    diagnostic_stop_loss_summary[Strategy] = "Unmanaged Retail Baseline"
)

-- 15. Backtested 5% Stop-Loss PnL
[Backtested 5% Stop-Loss PnL] = 
CALCULATE (
    SUM ( diagnostic_stop_loss_summary[Total_Realised_PnL_INR] ),
    diagnostic_stop_loss_summary[Strategy] = "Automated 5% Stop-Loss"
)

-- 16. Capital Preserved with 5% Stop-Loss (Absolute INR Saved)
[Capital Preserved 5% SL (INR)] = 
CALCULATE (
    SUM ( diagnostic_stop_loss_summary[Capital_Preserved_INR] ),
    diagnostic_stop_loss_summary[Strategy] = "Automated 5% Stop-Loss"
)

-- 17. Retail Drawdown Mitigation Rate %
-- Quantifies the percentage reduction in retail loss via automated risk bounds
[Retail Drawdown Mitigation Rate %] = 
VAR BaselineLoss = ABS ( [Unmanaged Retail PnL Baseline] )
VAR SavedCapital = [Capital Preserved 5% SL (INR)]
RETURN
    DIVIDE ( SavedCapital, BaselineLoss, 0 ) * 100

-- 18. Stop-Loss Trigger Liquidation Rate %
[Stop-Loss Trigger Rate %] = 
CALCULATE (
    MAX ( diagnostic_stop_loss_summary[Stop_Trigger_Rate_Pct] ),
    diagnostic_stop_loss_summary[Strategy] = "Automated 5% Stop-Loss"
)
```

### 3.5 Operational Incident Prioritisation & Surveillance

```dax
-- 19. Total Open Surveillance Incidents
[Open Incidents Total] = 
CALCULATE (
    COUNTROWS ( fct_incident_queue ),
    fct_incident_queue[status] = "OPEN"
)

-- 20. P0 Critical Incidents (Slippage > 3% / SLA Breach)
[P0 Critical Incidents] = 
CALCULATE (
    COUNTROWS ( fct_incident_queue ),
    fct_incident_queue[priority_level] = "P0",
    fct_incident_queue[status] = "OPEN"
)

-- 21. P1 High Incidents (Wash Trading <= 5s)
[P1 High Incidents] = 
CALCULATE (
    COUNTROWS ( fct_incident_queue ),
    fct_incident_queue[priority_level] = "P1",
    fct_incident_queue[status] = "OPEN"
)

-- 22. P2 Medium Incidents (Rolling 24h Z-Score Volume Spikes > 3.0)
[P2 Medium Incidents] = 
CALCULATE (
    COUNTROWS ( fct_incident_queue ),
    fct_incident_queue[priority_level] = "P2",
    fct_incident_queue[status] = "OPEN"
)

-- 23. Average P0 Slippage Magnitude %
[Avg P0 Slippage %] = 
CALCULATE (
    AVERAGE ( fct_incident_queue[metric_value] ),
    fct_incident_queue[priority_level] = "P0"
)
```

---

## 4. Visual Specification & Field Mapping (3 Pages)

### PAGE 1: EXECUTIVE & PnL KPI COCKPIT
*Target Audience: VP of Product, Chief Risk Officer, Head of Analytics*

| Visual Type | Component Title | Field / Measure Mappings | Formatting & Conditional Logic |
| :--- | :--- | :--- | :--- |
| **Card (KPI)** | Gross Traded Volume | Value: `[Total Notional Volume]` | Display units: Auto (₹ Cr / L), 2 decimals |
| **Card (KPI)** | Net Exchange Fees Paid | Value: `[Total Fees INR]` | Prefix: ₹, formatted with comma separation |
| **Card (KPI)** | Realised Net PnL | Value: `[Net Realised PnL]` | Callout Color: Green if $>0$, Red if $<0$ |
| **Card (KPI)** | Open Inventory MTM | Value: `[MTM Portfolio Value]` | Display units: Auto (₹ L), Subtitle: "Open MTM Value" |
| **Area Chart** | Cumulative Realised Net PnL by Persona | **X-Axis:** `dim_date[Date]`<br>**Y-Axis:** `[Cumulative Realised Net PnL]`<br>**Legend:** `dim_users[persona]` | **Colors:**<br>• Retail: `#E15759` (Crimson - deep drawdown)<br>• Systematic: `#4E79A7` (Blue - step-growth)<br>• HFT: `#59A14F` (Green - steady linear capture) |
| **Donut Chart** | Traded Volume by Asset | **Legend:** `dim_assets[symbol]`<br>**Values:** `[Total Notional Volume]` | Data labels: Category + Percent of Total |
| **100% Stacked Bar**| Fee Contribution by Persona | **Y-Axis:** `dim_users[kyc_status]`<br>**X-Axis:** `[Total Fees INR]`<br>**Legend:** `dim_users[persona]` | Tooltip: `[Total Fees INR]`, `[Platform Fee Take Rate (bps)]` |

---

### PAGE 2: STOP-LOSS DIAGNOSTIC & RISK BENCHMARK
*Target Audience: Risk Committee, Quantitative Analysts, Algorithmic Trading Operations*

| Visual Type | Component Title | Field / Measure Mappings | Formatting & Conditional Logic |
| :--- | :--- | :--- | :--- |
| **Single-Select Slicer**| Select Stop-Loss Threshold | **Field:** `diagnostic_stop_loss_summary[Strategy]` | Selection: Radio buttons (`2%`, `5%`, `10%`, `Unmanaged`) |
| **Dual Clustered Bar** | Unmanaged vs Backtested Net PnL | **Y-Axis:** `diagnostic_stop_loss_summary[Strategy]`<br>**X-Axis:** `SUM(diagnostic_stop_loss_summary[Total_Realised_PnL_INR])` | Conditional color: Green if $PnL \ge 0$, Red if $<0$ |
| **Scatter Plot** | Drawdown vs Win Rate Curve | **X-Axis:** `Avg_Max_Drawdown_Pct`<br>**Y-Axis:** `Win_Rate_Pct`<br>**Details:** `Strategy`<br>**Size:** `Capital_Preserved_INR` | Reference line on X-axis at `-5.0%` risk tolerance |
| **Smart Card** | Capital Preserved Callout | **Value:** `[Capital Preserved 5% SL (INR)]`<br>**Secondary:** `[Retail Drawdown Mitigation Rate %]` | "Automated 5% stop intervention reduces total retail loss by 77.7%, preserving ₹14.3L" |
| **Table Grid** | Cohort Simulation Diagnostic | **Columns:**<br>• `trade_id`<br>• `user_id`<br>• `entry_time`<br>• `entry_price`<br>• `unmanaged_max_drawdown_pct`<br>• `sl_5_triggered`<br>• `sl_5_saved_capital` | Data bars on `sl_5_saved_capital` (Green gradient) |

---

### PAGE 3: INCIDENT PRIORITISATION COMMAND QUEUE
*Target Audience: Market Surveillance, Exchange SRE, Compliance Officers*

| Visual Type | Component Title | Field / Measure Mappings | Formatting & Conditional Logic |
| :--- | :--- | :--- | :--- |
| **Card (KPI)** | Total Open Incidents | Value: `[Open Incidents Total]` | Neutral Slate Grey (`#495057`) |
| **Card (KPI)** | P0 Critical (Slippage > 3%) | Value: `[P0 Critical Incidents]` | Alert Red (`#D90429`), Bold Callout |
| **Card (KPI)** | P1 High (Wash Trade < 5s) | Value: `[P1 High Incidents]` | Warning Amber (`#F77F00`) |
| **Card (KPI)** | P2 Medium (Volume Z-Score > 3.0) | Value: `[P2 Medium Incidents]` | Informational Blue (`#277DA1`) |
| **Interactive Grid Table** | Real-Time Incident Surveillance Queue | **Columns:**<br>• `fct_incident_queue[incident_id]`<br>• `fct_incident_queue[priority_level]`<br>• `fct_incident_queue[anomaly_category]`<br>• `fct_incident_queue[user_id]`<br>• `fct_incident_queue[asset_id]`<br>• `fct_incident_queue[metric_value]`<br>• `fct_incident_queue[baseline_value]`<br>• `fct_incident_queue[detected_at]`<br>• `fct_incident_queue[status]` | **Conditional Background on Priority Level:**<br>• P0: Background `#FFD6D6`, Font `#990000`<br>• P1: Background `#FFEAA7`, Font `#B7791F`<br>• P2: Background `#E2E8F0`, Font `#2D3748` |
| **Donut Chart** | Incident Distribution by Category | **Legend:** `anomaly_category`<br>**Values:** `COUNT(incident_id)` | Categories: `STOP_LOSS_SLIPPAGE`, `WASH_TRADE_DETECTED`, `VOLUME_SPIKE` |
| **Column Chart** | P0 Slippage Metric vs SLA Baseline | **X-Axis:** `user_id`<br>**Y-Axis:** `metric_value`<br>**Constant Line:** `2.0%` (Institutional SLA) | Bar colors highlighted if metric exceeds 3.0% threshold |
