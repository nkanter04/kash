# Kash: notes for Claude

Friendly stock analysis web app: FastAPI backend (`app.py`, `sec.py`, `rows.py`, `company.py`, `market.py`, `macro.py`, `assets.py`, `scores.py`) and a vanilla JS
single-page frontend (`static/app.js`; company tabs in `company.js`, home page in `market.js`, Screener/Compare/Earnings/
Portfolio/Macro/Assets plus the watchlist in `pages.js`; `index.html` calls `route()` after all four scripts load). Public repo: github.com/nkanter04/kash. See README.md for features.

## Run and test

- Server: `.venv/Scripts/python -m uvicorn app:app --port 8050 --reload` (or `start-kash.bat`, or the
  `kash` entry in `.claude/launch.json`). `--reload` restarts on .py changes; static files are served
  fresh, so a browser refresh is enough for frontend edits.
- On the owner's PC the server auto-starts hidden at login (Startup-folder shortcut to `run-kash-hidden.vbs`,
  log in `.cache/server.log`). Stop it with `stop-kash.ps1`/`.bat`: with `--reload` the process serving port 8050
  is a `spawn_main` child whose command line doesn't mention uvicorn, so the script also uses the PID the app
  writes to `.cache/server.pid` at startup. Restart it after stopping (run the .vbs) so the owner isn't left without it.
- `--reload` can hang after several backend edits (the log says "Reloading" but the old worker keeps serving). If new
  code doesn't take effect, restart: `stop-kash.ps1`, then launch `run-kash-hidden.vbs`.
- Shared online copy: `render.yaml` on Render. `app.hosted()` (RENDER or KASH_PASSWORD set) requires the Basic-auth
  password on everything except `/healthz`, serves nothing if hosted without a password, and returns 404 for `/api/people*`.
- Who's Trading: `disclosures.py` builds `.cache/disclosures.db` (`python disclosures.py build`, incremental); `people.py`
  queries it; `static/people.js` is the UI. The owner accepted the Senate/OGE acknowledgment for personal research only:
  never commit the database or serve it publicly. "Beat the S&P" is measured from the filing date (when a trade became public).
  SEC data lags to the last quarter's end. Trump's filings are OCR'd scans from a hand-kept list (`TRUMP_REPORTS`), partial.
- Tests: `.venv/Scripts/python -m pytest` runs everything (~45 s).
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
- Foreign IFRS filers and ETFs have no US-GAAP SEC facts and fall back to Yahoo (~4 years). So do companies moved
  to a new SEC filer with no annual filing yet (XOM became ExxonMobil Holdings Corp, CIK 2115436, in 2026).
- **Merged tickers:** after a merger SEC and Yahoo can describe different companies (SUPN, Nov 2026: SEC = legacy
  Indivior, Yahoo = legacy Supernus). `app.source_conflict` flags this when revenue AND diluted shares are both
  >25% apart for the latest shared fiscal year (revenue alone differs for banks). Flagged tickers set
  `info.dataConflict`: SEC annual history only, no merge/TTM/quarterly/scorecard/fair value, market cap and P/E hidden.
- **Cash and debt tags:** securities are often under `DebtSecuritiesAvailableForSale...Current` and convertible notes
  under `Convertible*NotesPayable*`, so check tags when a cash or debt figure looks too low.
- **ROIC** subtracts cash plus short-term investments, and is blank when invested capital is under 5% of total assets.

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
- Home page (`market.py`, `market.js`): index members come from Wikipedia (S&P 500, Dow 30, GICS sectors) and
  Nasdaq's list API (Nasdaq-100), cached a week in `.cache/members_*.json` with a stale-copy fallback; market cap
  and % change come from one paged Yahoo screener pull (US stocks > $1B, ~6 s, cached 2 min). The screener's
  `regularMarketChangePercent` is in percent. The heat map is a squarified treemap drawn as positioned `<a>` tiles.
- Big Investors (`funds.py`, `static/funds.js`, `/investors`): 13F holdings of the hand-kept `MANAGERS` list (each id is
  checked against a word in the SEC's name). CUSIPs map to tickers through the SEC's fails-to-deliver files, which write
  share classes without a separator (BRKB), so tickers are respelled to match `company_tickers.json` (BRK-B). 13F values
  are dollars since 2023, but some filers still report thousands: `parse_info_table` detects a median under $1 a share.
  Options (put/call rows) are stored but left out of portfolio percentages. Refreshes at startup when not hosted.
- Quality screener (`fundamentals.py`): summaries built with `sec.frames_from_facts`, the same code as the stock pages,
  merged into `/api/market/screener` rows. Share-count growth outside -20%..+60% a year is blanked (filings that switch
  between units and thousands, e.g. MCD). The build fetches splits from Yahoo in bulk; running it while the server is up
  once corrupted yfinance's own cache (`%LOCALAPPDATA%\py-yfinance\tkr-tz.db`, "database disk image is malformed"):
  stop the server, delete that file, restart.
- Both read `.cache/` first and fall back to the committed copies in `data/` (public SEC data), which is what the hosted
  site uses. After a rebuild run `python funds.py snapshot` and `python fundamentals.py snapshot`, then commit `data/`.
- Macro (`macro.py`): FRED CSVs and multpl tables cached on disk with stale fallback; each card builds separately.
  Buffett Indicator = Fed Z.1 corporate equities / GDP, extended to today with the Wilshire 5000.
- Screener universe: `market._universe` filters to major US exchanges in the query (OTC foreign lines crowd out real
  listings). Unit check: `fiftyTwoWeekChangePercent` and `regularMarketChangePercent` are percent;
  `fiftyDayAverageChangePercent`, `twoHundredDayAverageChangePercent` and `fiftyTwoWeekHigh/LowChangePercent` are fractions.
- P/E "normal" levels use medians, because one year of tiny earnings distorts an average (KO 2017).
- Watchlist, recent stocks and portfolio live in the browser's localStorage (`kash-watch`, `kash-recent`, `kash-portfolio`).
- JS `cagr()` takes `[{v}, ...]` objects (as returned by `annualSeries`), not plain numbers.
- Theme ("Ledger"): all colors are tokens at the top of `styles.css`, redefined for dark mode. Use `--brand` for
  fills with white text, `--brand-ink` for green *text* (lighter in dark mode), `--on-status` for text on
  --good/--bad fills, `--font` (IBM Plex Sans; charts and the gauge read it) and `--serif` (Source Serif 4,
  headings). Never hard-code a font or color in JS; read tokens with `css("--name")`.
- Frontend charts: Chart.js 4.4.1. Wait for the web font before drawing (Chart.js caches label widths).
- The SEC requires a contact email in the User-Agent: `config.json` (gitignored; template in
  `config.example.json`) or `SEC_USER_AGENT`. Never commit `config.json` or put an email in code.
- Not investment advice: keep the disclaimer in the footer and README.
