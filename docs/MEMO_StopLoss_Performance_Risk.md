# INTERNAL MEMORANDUM

**TO:** BI & Analytics Leadership, CoinDCX  
**FROM:** Analytics Intern Candidate  
**DATE:** September 20, 2026  
**SUBJECT:** Retail Trader Capital Preservation via Stop-Loss Automation & Exchange Slippage Anomalies  

### Executive Summary
Analysis of 100,000+ transaction executions across three representative trader personas indicates that retail accounts suffer severe asymmetric drawdown due to lack of risk discipline. Backtesting trailing stop-loss logic against 1-minute OHLCV market candles demonstrates that automated risk intervention preserves significant retail liquidity while expanding exchange fee generation.

---

### Key Findings

1. **The Cost of Unmanaged Drawdown:**
   Retail speculators exhibited an average maximum drawdown of **-34.8%**, with **62%** of unmanaged losing positions remaining open past a -15% unrealised loss threshold. This locks capital and dampens trading velocity.

2. **The 5% Stop-Loss Sweet Spot:**
   Simulating a 5% stop-loss threshold across all retail long entries reduced total retail losses by **77.7%** (saving ₹14.3L across the test cohort). While 2% stops resulted in excessive whipsaw liquidations, a 5% threshold allowed normal market volatility while shielding against structural market breakdowns.

3. **Execution Slippage & Incident Prioritisation:**
   During volume spike events ($Z\text{-score} > 3.0$), market-order stop-losses suffered execution slippage exceeding **4.5%**. Implementing a **P0 incident alert** for slippage $>3\%$ identifies exchange liquidity deficits before user escalation.

| Metric Comparison | Unmanaged Retail | With 5% Stop-Loss | Variance |
| :--- | :--- | :--- | :--- |
| **Total Realised Net PnL** | -₹18.4 Lakhs | -₹4.1 Lakhs | +₹14.3 L |
| **Average Maximum Drawdown** | -34.8% | -11.2% | +23.6% |
| **Capital Turnover Rate** | 1.4x / month | 3.8x / month | +171% |
| **Total Exchange Fees Paid** | ₹1.82 Lakhs | ₹2.95 Lakhs | +62.1% |

---

### Strategic Recommendations

1. **In-App Smart Stop-Loss Prompts:** Introduce a one-tap 5% risk guardrail default during high-volatility order entry. This preserves retail trading capital, increasing customer lifetime value and long-term trading velocity.
2. **Automated P0 Slippage Alerts:** Operationalise the P0 Incident Queue to trigger liquidity provider re-routing whenever stop-loss slippage crosses 2.5% on major pairs.
