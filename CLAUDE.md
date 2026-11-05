# Kash: notes for Claude

Friendly stock analysis web app: FastAPI backend (`app.py`, `sec.py`, `rows.py`, `company.py`) and a vanilla JS
single-page frontend (`static/app.js`; the company tabs are in `static/company.js`). Public repo: github.com/nkanter04/kash. See README.md for features.

## Run and test

- Server: `.venv/Scripts/python -m uvicorn app:app --port 8050 --reload` (or `start-kash.bat`, or the
  `kash` entry in `.claude/launch.json`). `--reload` restarts on .py changes; static files are served
  fresh, so a browser refresh is enough for frontend edits.
- Tests: `.venv/Scripts/python -m pytest` runs everything (~20 s).
  `-m "not network"` runs only the offline unit tests (~5 s); `-m network` runs only the live-data checks.
  Run the tests after any change to `app.py`, `sec.py` or `rows.py`.
- Responses are cached in memory for 15 min per ticker, and SEC downloads on disk in `.cache/` (1 day for
  company facts). Restart the server to clear the in-memory cache when checking a data fix.

## Data gotchas (each of these was a real bug once)

- **Split basis:** yfinance closes (`auto_adjust=False`), share counts and EPS are already split-adjusted
  to today. SEC share counts are multiplied, and EPS divided, by the splits *after each fact's filing date*
  (`sec._split_factor`). Never apply a split factor to prices. The live test
  `test_eps_times_shares_matches_net_income` catches basis mix-ups.
- **Dividend yield:** Yahoo's `dividendYield` units are inconsistent; use `info["dividendYieldCalc"]`
  (dividendRate / price).
- **TTM:** sum four quarters for flows, but share counts use the latest quarter. Drop the TTM column when the
  latest quarter is the fiscal year-end (it would duplicate the FY column).
- **SEC tags change over time:** `_pick` falls back between concept names per year; the latest filing wins
  for restatements. Flows must span 340-380 days; instants have no `start`.
- **Total Debt (SEC years)** excludes operating leases, so it is lower than Yahoo's figure. This is intended.
- **Non-USD filers** (e.g. TSM, TWD): money text in the scorecard goes through `_short`, which reads the
  currency from `_CUR.code`.
- Foreign IFRS filers and ETFs have no US-GAAP SEC facts and fall back to Yahoo (~4 years).

## Conventions

- Scorecard (`app.scorecard`): 8 pillars, each skipped when its data is missing. A check's pass/fail and its
  text must use the same rounded condition. Annual windows: 5 years (6 fiscal-year columns); P/E compares
  with up to 10 years. Ratio tables start with a "Current" column.
- Every statement/ratio row in `rows.py` needs a plain-English `desc`: the friendly explanations are the
  point of the app.
- Price chart: 1D/1W/1M come from `/api/prices/{ticker}?range=` (intraday 2m/5m/30m bars, cached 60 s) and
  are plotted by bar number so nights and weekends leave no flat gaps; 1D change is vs the prior session's
  close. 6M and longer use the daily series from `/api/stock` (weekly before the last 10 years).
- Extended hours: 1D includes pre-market (4:00-9:30 ET) and after-hours (16:00-20:00 ET) bars, tagged per
  point in `sessions` and shaded on the chart; the header shows `info.extended`. Compute extended % changes
  yourself (Yahoo's `*ChangePercent` fields are in percent, unlike other fields), and outside regular hours
  measure from the official close (`regularMarketPrice` / `previousClose`), not the last 2-minute bar.
  The overnight session (20:00-4:00 ET) is not in Yahoo's data.
- Company tabs (Forecast, Fair value, Ownership, Dividends, Company) share one lazy `/api/company/{ticker}` call
  (cached 15 min, prefetched when a stock opens). Each section is fetched separately and is None/empty when
  Yahoo lacks it (ETFs, foreign listings); every tab must render a friendly empty state for that.
- Yahoo's long-term growth (`LTG`) estimate is blank for individual stocks; the fair value calculator uses
  next fiscal year's EPS growth plus the company's own 5-year EPS/FCF growth (lower of the two, capped 20%).
- **Foreign listings / share classes:** price-based ratios go through `PriceBasis`, which converts the quoted
  price to the statement currency at that date's FX rate (`fx_series`, Yahoo pair like `USDTWD=X`) and divides
  by `share_ratio` (statement shares per quoted share: TSM 5, BHP 2, BRK-B ~1/1500). The ratio needs
  `impliedSharesOutstanding` (all share classes); `sharesOutstanding` counts only one class (GOOGL, NVO).
  The live test `test_price_ratios_match_yahoo_across_listing_types` guards this.
- JS `cagr()` takes `[{v}, ...]` objects (as returned by `annualSeries`), not plain numbers.
- Frontend charts: Chart.js 4.4.1. Wait for the web font before drawing (Chart.js caches label widths).
- The SEC requires a contact email in the User-Agent: `config.json` (gitignored; template in
  `config.example.json`) or `SEC_USER_AGENT`. Never commit `config.json` or put an email in code.
- Not investment advice: keep the disclaimer in the footer and README.
