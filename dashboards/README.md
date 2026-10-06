# PulseScope Power BI Workspace

This directory is designated for the compiled **`PulseScope.pbix`** dashboard binary.

---

## 3-Minute Quickstart: Building `PulseScope.pbix` in Power BI Desktop

1. **Open Power BI Desktop**  
   Launch Power BI Desktop on your workstation.

2. **Ingest Data Sources (`./data/`)**  
   Click **Get Data > Text/CSV** and import the following generated tables from `../data/`:
   - `dim_assets.csv`
   - `dim_users.csv`
   - `fct_trades.csv`
   - `fct_orders.csv`
   - `fct_incident_queue.csv`
   - `diagnostic_stop_loss_summary.csv`
   - `diagnostic_stop_loss_lots.csv`

3. **Verify Data Types**  
   In Power Query:
   - Ensure all `*_at` and `*_time` columns are set to **Date/Time**.
   - Ensure `price`, `qty`, `fee_inr`, and `metric_value` are set to **Decimal Number**.

4. **Generate Calendar Dimension (`dim_date`)**  
   Go to **Modeling > New Table** and paste the dynamic DAX calendar script from [`docs/power_bi_specification.md`](../docs/power_bi_specification.md#2-dynamic-calendar-dimension-dax-calculated-table).  
   Mark `dim_date` as a **Date Table** using `Date` as the unique key.

5. **Establish Star Schema Relationships**  
   In the **Model View**, verify that `1:*` single-direction relationships match the table catalog in [`docs/power_bi_specification.md`](../docs/power_bi_specification.md#12-table-relationship-catalog).

6. **Add the DAX Measure Library**  
   Create a blank table `_Measures` and copy the DAX definitions for:
   - `[Total Notional Volume]`
   - `[Total Fees INR]`
   - `[Platform Fee Take Rate (bps)]`
   - `[Cumulative Realised Net PnL]`
   - `[MTM Portfolio Value]`
   - `[Capital Preserved 5% SL (INR)]`
   - `[Retail Drawdown Mitigation Rate %]`
   - `[P0 Critical Incidents]`, `[P1 High Incidents]`, `[P2 Medium Incidents]`

7. **Save File**  
   Save the file as **`PulseScope.pbix`** in this directory:
   `dashboards/PulseScope.pbix`

8. **Commit with Git (or Git LFS)**  
   If the `.pbix` file is under 50 MB, commit directly:
   ```bash
   git add dashboards/PulseScope.pbix
   git commit -m "feat: add compiled Power BI desktop file PulseScope.pbix"
   git push origin main
   ```
   *(If over 50 MB, use `git lfs track "*.pbix"` before pushing).*
