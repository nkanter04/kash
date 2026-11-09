"""Stock analysis backend.

Pulls quotes, price history and financial statements from Yahoo Finance (via yfinance),
reshapes them into friendly, labeled tables with plain-English explanations, and computes
a simple scorecard. Run with:  python -m uvicorn app:app --port 8050
"""
from __future__ import annotations

import math
import os
import statistics
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import assets as asset_data
import company
import macro
import market
import scores
import sec
from rows import BALANCE_ROWS, CASHFLOW_ROWS, INCOME_ROWS, RATIO_ROWS

STATIC = Path(__file__).parent / "static"
CACHE_SECONDS = 15 * 60
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()

PID_FILE = Path(__file__).parent / ".cache" / "server.pid"


def _warm_caches():
    """Load the slow shared datasets in the background so the first visitor doesn't wait."""
    for job in (market.quotes_all, asset_data.frame, macro.overview):
        try:
            job()
        except Exception:  # noqa: BLE001
            pass


@asynccontextmanager
async def lifespan(_app):
    """Record the serving process's PID so stop-kash.ps1 can stop it (with --reload it's a child process
    whose command line doesn't mention uvicorn)."""
    PID_FILE.parent.mkdir(exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    threading.Thread(target=_warm_caches, daemon=True).start()
    yield
    try:
        if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID_FILE.unlink()
    except OSError:
        pass


app = FastAPI(title="Kash", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2000)


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """Make browsers revalidate the page and static files, so an update shows on a normal refresh."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------- helpers

def clean(v):
    """Make a value JSON-safe (NaN/inf -> None, numpy -> python)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def growth(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def get(df: pd.DataFrame, names, col):
    """First non-null value among candidate row names for a column."""
    if df is None or df.empty:
        return None
    if isinstance(names, str):
        names = [names]
    for n in names:
        if n in df.index:
            v = clean(df.at[n, col])
            if v is not None:
                return v
    return None


def ttm_frame(q: pd.DataFrame) -> pd.Series | None:
    """Sum the most recent four quarters of a flow statement (None if fewer than 4)."""
    if q is None or q.empty or q.shape[1] < 4:
        return None
    last4 = q.iloc[:, :4]
    # only keep rows where all 4 quarters exist
    ok = last4.notna().all(axis=1)
    out = last4[ok].sum(axis=1)
    # share counts are levels, not flows: use the latest quarter instead of a sum
    for row in ("Diluted Average Shares", "Basic Average Shares"):
        if row in out.index:
            out[row] = last4.at[row, last4.columns[0]]
    return out


class PriceBook:
    """Look up the split-adjusted (not dividend-adjusted) close at any date."""

    def __init__(self, hist: pd.DataFrame):
        self.hist = hist
        splits = hist["Stock Splits"] if "Stock Splits" in hist else pd.Series(dtype=float)
        self.splits = splits[splits > 0]

    def price_at(self, date) -> float | None:
        if self.hist.empty:
            return None
        ts = pd.Timestamp(date)
        idx = self.hist.index
        if idx.tz is not None:
            ts = ts.tz_localize(idx.tz) if ts.tzinfo is None else ts.tz_convert(idx.tz)
        sub = self.hist.loc[:ts]
        if sub.empty:
            return None
        # yfinance closes (auto_adjust=False) are already split-adjusted to today's share basis,
        # the same basis as Yahoo's share counts/EPS and our split-normalized SEC figures
        return clean(sub["Close"].iloc[-1])


# ---------------------------------------------------------------- price basis (foreign listings)

# quotes in a currency's minor unit (London pence etc.) -> (major currency, divisor)
SUBUNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
FX_SECONDS = 86400
_fx: dict[str, tuple[float, pd.Series | None]] = {}


def fx_series(frm: str, to: str) -> pd.Series | None:
    """Daily rate: units of `to` per 1 `frm` (Yahoo pair 'FROMTO=X'), tz-naive index. Cached for a day."""
    pair = f"{frm}{to}=X"
    with _lock:
        hit = _fx.get(pair)
        if hit and time.time() - hit[0] < FX_SECONDS:
            return hit[1]
    try:
        s = yf.Ticker(pair).history(period="max", interval="1d")["Close"].dropna()
        s.index = s.index.tz_localize(None) if s.index.tz is not None else s.index
        s = s if not s.empty else None
    except Exception:  # noqa: BLE001
        s = None
    with _lock:
        _fx[pair] = (time.time(), s)
    return s


def share_ratio(stmt_shares: float | None, info: dict) -> float:
    """Statement shares per quoted share: 5 for TSM (1 ADR = 5 shares), 2 for BHP, ~1500 for BRK-B, else 1."""
    quoted = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
    if not quoted and info.get("marketCap") and info.get("currentPrice"):
        quoted = info["marketCap"] / info["currentPrice"]
    if not stmt_shares or not quoted:
        return 1.0
    r = stmt_shares / quoted
    if abs(r - 1) < 0.25:   # ordinary share-count drift (buybacks, diluted vs. outstanding): not an ADR ratio
        return 1.0
    return float(f"{r:.2g}")   # ADR ratios are round numbers: 4.99 -> 5, 1499.6 -> 1500


class PriceBasis:
    """Turns a quoted share price into the statements' currency and share basis.

    Ratios like P/E divide market cap (price x statement shares) by statement figures. For a foreign
    listing such as TSM the price is USD per ADR while the statements are TWD per ordinary share, so
    the price must be converted at that date's exchange rate and divided by the ADR ratio.
    """

    def __init__(self, info: dict, stmt_shares: float | None, fx_lookup=fx_series):
        cur = info.get("currency")
        self.scale = 1.0
        if cur in SUBUNITS:
            cur, div = SUBUNITS[cur]
            self.scale = 1 / div
        fin = info.get("financialCurrency") or cur
        self.fx = fx_lookup(cur, fin) if cur and fin and cur != fin else None
        self.missing_fx = bool(cur and fin and cur != fin and self.fx is None)
        self.ratio = share_ratio(stmt_shares, info)

    def convert(self, price: float | None, date=None) -> float | None:
        if price is None or self.missing_fx:
            return None   # better no ratio than a ratio off by the exchange rate
        rate = 1.0
        if self.fx is not None:
            ts = pd.Timestamp(date).tz_localize(None) if date is not None else self.fx.index[-1]
            rate = float(self.fx.asof(ts)) if ts >= self.fx.index[0] else float(self.fx.iloc[0])
        return price * self.scale * rate / self.ratio


# ---------------------------------------------------------------- statements

def build_table(rows, frames: dict, columns: list[dict], context: dict) -> list[dict]:
    """Turn row specs into [{key,label,desc,fmt,values:[...]}] aligned with columns."""
    out = []
    computed: dict[str, list] = {}
    for spec in rows:
        vals = []
        for i, c in enumerate(columns):
            if "calc" in spec:
                v = spec["calc"](computed, i, c, context)
            else:
                v = c["get"](spec["src"])
                if v is not None and spec.get("negate"):
                    v = -v
            vals.append(clean(v))
        computed[spec["key"]] = vals
        if spec.get("hidden"):
            continue
        if all(v is None for v in vals):
            continue
        out.append({
            "key": spec["key"],
            "label": spec["label"],
            "desc": spec.get("desc", ""),
            "fmt": spec.get("fmt", "money"),
            "style": spec.get("style", ""),
            "values": vals,
        })
    return out


def make_columns(df: pd.DataFrame, period: str, ttm: pd.Series | None = None, keep=None):
    cols = []
    if ttm is not None:
        cols.append({"label": "TTM", "date": None,
                     "get": (lambda s, t=ttm: _series_get(t, s))})
    for c in df.columns:
        if keep is not None and c not in keep:
            continue
        label = f"FY {c.year}" if period == "annual" else c.strftime("%b %Y")
        cols.append({"label": label, "date": c.strftime("%Y-%m-%d"),
                     "get": (lambda s, col=c: get(df, s, col))})
    return cols


def _series_get(series: pd.Series, names):
    if isinstance(names, str):
        names = [names]
    for n in names:
        if n in series.index:
            v = clean(series[n])
            if v is not None:
                return v
    return None


def merge_sec(sec_df: pd.DataFrame | None, yahoo_df: pd.DataFrame | None) -> pd.DataFrame:
    """SEC values win cell-by-cell; Yahoo fills rows/years SEC lacks. Columns newest first."""
    if sec_df is None or sec_df.empty:
        return yahoo_df
    if yahoo_df is None or yahoo_df.empty:
        return sec_df
    y = yahoo_df.copy()
    # snap Yahoo dates onto SEC fiscal-year ends that are within a few weeks (52/53-week years)
    rename = {}
    for c in y.columns:
        near = [sc for sc in sec_df.columns if abs((sc - c).days) <= 20]
        if near:
            rename[c] = near[0]
    y = y.rename(columns=rename)
    y = y.loc[:, ~y.columns.duplicated()]
    out = sec_df.combine_first(y)
    return out[sorted(out.columns, reverse=True)]


def source_conflict(sec_income: pd.DataFrame | None, yahoo_income: pd.DataFrame | None) -> bool:
    """True when SEC and Yahoo are describing different companies, as can happen for a while after a merger
    (SUPN in Nov 2026: the SEC filer was legacy Indivior, Yahoo still had legacy Supernus).

    Compares the latest fiscal year both have. Revenue AND diluted shares must both be >25% apart: revenue alone
    differs by definition for banks and insurers, and shares alone differ for odd share classes.
    """
    if sec_income is None or sec_income.empty or yahoo_income is None or yahoo_income.empty:
        return False
    for sc in sorted(sec_income.columns, reverse=True):
        near = [yc for yc in yahoo_income.columns if abs((sc - yc).days) <= 20]
        if not near:
            continue
        pairs = [(get(sec_income, n, sc), get(yahoo_income, n, near[0])) for n in ("Total Revenue", "Diluted Average Shares")]
        if any(a is None or b is None or a <= 0 or b <= 0 for a, b in pairs):
            continue
        return all(max(a, b) / min(a, b) > 1.25 for a, b in pairs)
    return False


EMPTY_STATEMENTS = {"columns": [], "income": [], "balance": [], "cashflow": [], "ratios": []}


def statements(t: yf.Ticker, prices: PriceBook, info: dict, period: str, sec_frames=None, conflict=False) -> dict:
    if period == "annual" and conflict:   # SEC and Yahoo disagree about who this is: use the SEC filer alone
        inc, bal, cf = sec_frames
        inc_ttm = cf_ttm = None
    elif period == "annual":
        inc, bal, cf = t.income_stmt, t.balance_sheet, t.cashflow
        if sec_frames:
            inc, bal, cf = (merge_sec(a, b) for a, b in zip(sec_frames, (inc, bal, cf)))
        qinc, qcf = t.quarterly_income_stmt, t.quarterly_cashflow
        inc_ttm, cf_ttm = ttm_frame(qinc), ttm_frame(qcf)
        # if the latest quarter IS the fiscal year end, TTM just duplicates the FY column
        if qinc is not None and not qinc.empty and inc is not None and not inc.empty                 and qinc.columns[0] <= inc.columns.max() + pd.Timedelta(days=20):   # Yahoo vs SEC dates differ by days
            inc_ttm = cf_ttm = None
    else:
        inc, bal, cf = t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow
        inc_ttm = cf_ttm = None

    if inc is None or inc.empty:
        return {"columns": [], "income": [], "balance": [], "cashflow": [], "ratios": []}

    # drop sparse columns (Yahoo often returns an almost-empty oldest period)
    keep = [c for c in inc.columns if get(inc, ["Total Revenue", "Net Income"], c) is not None]
    keep = sorted(keep, reverse=True)

    inc_cols = make_columns(inc, period, inc_ttm, keep)
    cf_keep = [c for c in cf.columns if c in keep] if cf is not None and not cf.empty else []
    cf_cols = make_columns(cf, period, cf_ttm, cf_keep) if cf_keep else []
    bal_keep = [c for c in bal.columns if c in keep] if bal is not None and not bal.empty else []
    bal_cols = make_columns(bal, period, None, bal_keep) if bal_keep else []

    # balance sheet for TTM column = latest quarter balance sheet
    qbal = bal if conflict else t.quarterly_balance_sheet if period == "annual" else bal
    latest_q = (max(qbal.columns) if conflict else qbal.columns[0]) if qbal is not None and not qbal.empty else None

    # prices are quoted per listed share in the trading currency; statements may differ (ADRs)
    stmt_shares = (get(qbal, ["Ordinary Shares Number", "Share Issued"], latest_q) if latest_q is not None else None) \
        or get(inc, ["Diluted Average Shares", "Basic Average Shares"], inc.columns.max())
    basis = PriceBasis(info, stmt_shares if not conflict else None)

    ctx = {"period": period, "info": info}
    income = build_table(INCOME_ROWS, {}, inc_cols, ctx)
    balance = build_table(BALANCE_ROWS, {}, bal_cols, ctx)
    cashflow = build_table(CASHFLOW_ROWS, {}, cf_cols, ctx)

    # ---- ratios: one column per income column, mixing all three statements
    ratio_cols = []
    src_cols = list(inc_cols)
    if period == "annual" and src_cols and src_cols[0]["date"] is not None:
        # no separate TTM column: "Current" ratios use today's price on the latest fiscal year
        src_cols.insert(0, {"label": "TTM", "date": None, "get": src_cols[0]["get"], "_cf": cf_cols[0]["get"] if cf_cols else None})
    for c in src_cols:
        date = c["date"]
        if date is None:  # TTM -> live price & latest balance sheet
            # after a merger today's price and the old filer's statements don't belong together
            price = None if conflict else basis.convert(clean(info.get("currentPrice") or info.get("regularMarketPrice")))
            bget = (lambda s, col=latest_q: get(qbal, s, col)) if latest_q is not None else (lambda s: None)
            if c.get("_cf"):
                cget = c["_cf"]
            else:
                cget = cf_cols[0]["get"] if cf_cols and cf_cols[0]["date"] is None else (lambda s: None)
            label = "Current"
        else:
            price = basis.convert(prices.price_at(date), date)
            ts = pd.Timestamp(date)
            bget = (lambda s, col=ts: get(bal, s, col)) if ts in bal.columns else (lambda s: None)
            cget = (lambda s, col=ts: get(cf, s, col)) if cf is not None and ts in cf.columns else (lambda s: None)
            label = c["label"]
        ratio_cols.append({
            "label": label, "date": date, "price": price,
            "inc": c["get"], "bal": bget, "cf": cget,
            "annualize": 4 if (period == "quarterly") else 1,
        })
    ratios = build_ratios(ratio_cols)

    def strip(cols):
        return [{"label": c["label"], "date": c["date"]} for c in cols]

    return {
        "columns": {"income": strip(inc_cols), "balance": strip(bal_cols),
                    "cashflow": strip(cf_cols), "ratios": [{"label": c["label"], "date": c["date"]} for c in ratio_cols]},
        "income": income, "balance": balance, "cashflow": cashflow, "ratios": ratios,
    }


def build_ratios(cols) -> list[dict]:
    out = []
    for spec in RATIO_ROWS:
        vals = [clean(spec["calc"](c)) for c in cols]
        if all(v is None for v in vals):
            continue
        out.append({"key": spec["key"], "label": spec["label"], "desc": spec["desc"],
                    "fmt": spec["fmt"], "style": spec.get("style", ""), "values": vals})
    return out


# ---------------------------------------------------------------- scorecard

def series_of(table, key):
    for r in table:
        if r["key"] == key:
            return r["values"]
    return None


def scorecard(fin: dict, info: dict) -> list[dict]:
    """Plain-English pass/fail checks, in the spirit of a report card."""
    _CUR.code = info.get("financialCurrency") or info.get("currency")
    inc, bal, cf, rat = fin["income"], fin["balance"], fin["cashflow"], fin["ratios"]
    cols = fin["columns"]["income"] if fin["columns"] else []
    # annual-only values (skip TTM column)
    off = 1 if cols and cols[0]["label"] == "TTM" else 0
    checks = []

    def annual(table, key):
        s = series_of(table, key)
        return list(s[off:off + 6]) if s else []  # last 6 fiscal years = a 5-year window

    # 1. Growth
    rev = [v for v in annual(inc, "revenue") if v is not None]
    if len(rev) >= 2:
        yrs = len(rev) - 1
        cagr = (rev[0] / rev[-1]) ** (1 / yrs) - 1 if rev[-1] and rev[0] > 0 and rev[-1] > 0 else None
        if cagr is not None:
            ok = round(cagr, 3) >= 0.05
            checks.append({
                "key": "growth", "title": "Growing sales",
                "pass": ok,
                "value": cagr, "fmt": "pct",
                "text": f"Revenue grew about {cagr*100:.1f}% per year over the last {yrs} years."
                        + (" That's healthy growth." if ok else " That's slower than the ~5% we'd like to see."),
            })

    # 2. Profitability
    nm = info.get("profitMargins")
    if nm is None:
        nmarg = annual(inc, "net_margin")
        nm = nmarg[0] if nmarg else None
    if nm is not None:
        checks.append({
            "key": "profit", "title": "Profitable",
            "pass": nm >= 0.10, "value": nm, "fmt": "pct",
            "text": f"Keeps {nm*100:.1f}¢ of profit from every $1 of sales."
                    + (" Strong margins." if nm >= 0.10 else (" Profitable, but margins are thin." if nm > 0 else " Currently losing money.")),
        })

    # 3. Cash generation
    fcf = [v for v in annual(cf, "fcf") if v is not None]
    if fcf:
        pos = sum(1 for v in fcf if v > 0)
        checks.append({
            "key": "cash", "title": "Generates cash",
            "pass": pos == len(fcf), "value": fcf[0], "fmt": "money",
            "text": f"Produced positive free cash flow in {pos} of the last {len(fcf)} years."
                    + (" Cash keeps coming in after paying for investments." if pos == len(fcf) else " Cash generation has been inconsistent."),
        })

    # 4. Return on invested capital (5-year average; ratio column 0 is "Current")
    roic = [v for v in (series_of(rat, "roic") or [])[1:6] if v is not None]
    if roic:
        avg = sum(roic) / len(roic)
        ok = round(avg, 3) >= 0.10
        checks.append({
            "key": "roic", "title": "Uses capital well",
            "pass": ok, "value": avg, "fmt": "pct",
            "text": f"Earned about {avg*100:.1f}¢ a year on every $1 invested in the business "
                    f"(average return on invested capital over {len(roic)} years)."
                    + (" A sign of a strong business." if ok else " Below the ~10% that marks a high-quality business."),
        })

    # 5. Financial health
    cash = info.get("totalCash")
    debt = info.get("totalDebt")
    cr = info.get("currentRatio")
    if cash is not None and debt is not None:
        net = cash - debt
        ok = net >= 0 or (cr is not None and cr >= 1.0 and debt < 3 * (info.get("ebitda") or 0))
        checks.append({
            "key": "health", "title": "Solid balance sheet",
            "pass": bool(ok), "value": net, "fmt": "money",
            "text": (f"Has more cash than debt today (net cash of {_short(net)})." if net >= 0
                     else f"Today it owes {_short(-net)} more in debt than it holds in cash. "
                          + ("Earnings cover it comfortably." if ok else "That's a lot relative to earnings.")),
        })

    # 6. Valuation vs its own history
    pe_all = series_of(rat, "pe") or []
    pe_hist = [v for v in pe_all[1:11] if v is not None and v > 0]  # skip "Current"; last 10 years
    pe_now = info.get("trailingPE")
    if pe_now and pe_hist:
        avg = statistics.median(pe_hist)   # a median: one year of tiny earnings shouldn't define "normal"
        checks.append({
            "key": "value", "title": "Fairly priced",
            "pass": pe_now <= avg * 1.05, "value": pe_now, "fmt": "x",
            "text": f"Trades at {pe_now:.1f}× earnings vs. its {len(pe_hist)}-year median of {avg:.1f}×."
                    + (" Cheaper than usual." if pe_now < avg * 0.95 else (" About its normal price." if pe_now <= avg * 1.05 else " Pricier than usual.")),
        })

    # 7. Shareholder returns
    shares = [v for v in annual(inc, "shares") if v is not None]
    dy = info.get("dividendYieldCalc")
    if len(shares) >= 2:
        chg = shares[0] / shares[-1] - 1
        rewarding = chg < -0.005 or (dy or 0) > 0.01
        parts = []
        if chg < -0.005:
            parts.append(f"bought back stock (share count down {abs(chg)*100:.1f}%)")
        elif chg > 0.02:
            parts.append(f"issued new shares (share count up {chg*100:.1f}%)")
        if dy:
            parts.append(f"pays a {dy*100:.2f}% dividend")
        text = ("It " + " and ".join(parts) + ".") if parts else "Share count is flat and there's no dividend."
        checks.append({"key": "returns", "title": "Rewards shareholders", "pass": rewarding,
                       "value": chg, "fmt": "pct", "text": text})

    # 8. Analyst outlook
    tgt, px = info.get("targetMeanPrice"), info.get("currentPrice")
    n = info.get("numberOfAnalystOpinions")
    if tgt and px:
        up = tgt / px - 1
        checks.append({
            "key": "analysts", "title": "Analysts see upside",
            "pass": up >= 0.10, "value": up, "fmt": "pct",
            "text": f"{int(n) if n else 'Covering'} analysts' average target is ${tgt:,.2f}, "
                    f"{abs(up)*100:.0f}% {'above' if up >= 0 else 'below'} today's price.",
        })
    return checks


_CUR = threading.local()


def _short(v):
    cur = getattr(_CUR, "code", "USD") or "USD"
    pre, post = ("$", "") if cur == "USD" else ("", f" {cur}")
    a = abs(v)
    for d, s in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= d:
            return f"{pre}{v/d:.1f}{s}{post}"
    return f"{pre}{v:,.0f}{post}"


# ---------------------------------------------------------------- endpoints

INFO_KEYS = [
    "longName", "shortName", "symbol", "exchange", "fullExchangeName", "currency", "financialCurrency",
    "sector", "industry", "country", "website", "fullTimeEmployees", "longBusinessSummary",
    "currentPrice", "regularMarketPrice", "previousClose", "regularMarketChange", "regularMarketChangePercent",
    "marketCap", "enterpriseValue", "trailingPE", "forwardPE", "pegRatio", "trailingPegRatio", "priceToBook",
    "priceToSalesTrailing12Months", "enterpriseToEbitda", "trailingEps", "forwardEps", "beta",
    "dividendRate", "dividendYield", "payoutRatio", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "fiftyDayAverage", "twoHundredDayAverage", "averageVolume", "sharesOutstanding", "impliedSharesOutstanding",
    "profitMargins", "grossMargins", "operatingMargins", "returnOnEquity", "returnOnAssets",
    "revenueGrowth", "earningsGrowth", "totalCash", "totalDebt", "currentRatio", "debtToEquity",
    "freeCashflow", "totalRevenue", "ebitda", "targetMeanPrice", "targetHighPrice", "targetLowPrice",
    "targetMedianPrice", "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
    "earningsTimestamp", "exDividendDate", "quoteType", "heldPercentInstitutions", "heldPercentInsiders",
    "marketState", "regularMarketTime", "preMarketPrice", "preMarketTime", "postMarketPrice", "postMarketTime",
    "exchangeTimezoneName",
]


def extended_quote(info: dict) -> dict | None:
    """The pre-market or after-hours price to show beside the regular one (None during regular hours).

    The change is computed from the regular price, because Yahoo's *ChangePercent fields mix units.
    """
    state, reg = info.get("marketState"), info.get("regularMarketPrice") or info.get("currentPrice")
    if not reg or state == "REGULAR":
        return None
    pre, post = info.get("preMarketPrice"), info.get("postMarketPrice")
    reg_t = info.get("regularMarketTime") or 0
    if state == "PRE" and pre:
        label, p, t = "Pre-market", pre, info.get("preMarketTime")
    elif post and (info.get("postMarketTime") or 0) >= reg_t:
        label, p, t = "After hours", post, info.get("postMarketTime")
    else:
        return None
    return {"label": label, "price": p, "change": p - reg, "pct": p / reg - 1,
            "time": int(t * 1000) if t else None}


def _stock_extras(hist: pd.DataFrame, annual: dict, info: dict, conflict: bool) -> dict:
    """Quality scores and total return. Each is optional: a failure just hides that card."""
    out: dict = {"quality": {}, "returns": []}
    if annual.get("income") and not conflict:
        try:
            # both scores were built for industrial companies; bank balance sheets don't fit them
            if info.get("sector") != "Financial Services":
                out["quality"] = {"piotroski": scores.piotroski(annual), "altman": scores.altman(annual, info.get("sector"))}
        except Exception:  # noqa: BLE001
            pass
    try:
        if "Adj Close" in hist:
            out["returns"] = scores.total_returns(hist["Adj Close"], market.spy_adjusted())
    except Exception:  # noqa: BLE001
        pass
    return out


def load(ticker: str) -> dict:
    ticker = ticker.upper().strip()
    with _lock:
        hit = _cache.get(ticker)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]

    t = yf.Ticker(ticker)
    try:
        raw = t.info or {}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not reach Yahoo Finance: {e}")
    if not raw or (raw.get("regularMarketPrice") is None and raw.get("currentPrice") is None):
        raise HTTPException(404, f"No data found for '{ticker}'.")
    info = {k: clean(raw.get(k)) if not isinstance(raw.get(k), str) else raw.get(k) for k in INFO_KEYS}
    if info.get("currentPrice") is None:
        info["currentPrice"] = info.get("regularMarketPrice")
    # Yahoo's dividendYield units are inconsistent (sometimes %, sometimes fraction): derive it
    rate, px = info.get("dividendRate"), info.get("currentPrice")
    info["dividendYieldCalc"] = rate / px if rate and px else None
    info["extended"] = extended_quote(info)

    hist = t.history(period="max", interval="1d", auto_adjust=False, actions=True)
    prices = PriceBook(hist)
    price_series = []
    if not hist.empty:
        closes = hist["Close"].dropna()
        # daily for the last 10 years, weekly before that (keeps the payload small)
        cutoff = closes.index[-1] - pd.Timedelta(days=3660)
        closes = pd.concat([closes[closes.index < cutoff].iloc[::5], closes[closes.index >= cutoff]])
        price_series = [[int(ts.timestamp() * 1000), round(float(v), 4)] for ts, v in closes.items()]

    try:
        sec_frames = sec.annual_frames(ticker, splits=prices.splits)
    except Exception:  # noqa: BLE001  (SEC is a bonus; never break the page over it)
        sec_frames = None
    conflict = bool(sec_frames) and source_conflict(sec_frames[0], t.income_stmt)
    if conflict:
        try:
            info["dataConflict"] = sec.filer_info(ticker)
        except Exception:  # noqa: BLE001
            info["dataConflict"] = {}
    annual = statements(t, prices, info, "annual", sec_frames, conflict=conflict)
    annual["source"] = ("SEC EDGAR 10-K filings" if conflict else "SEC EDGAR 10-K filings + Yahoo Finance") if sec_frames else "Yahoo Finance"
    extras = _stock_extras(hist, annual, info, conflict)
    quarterly = {**EMPTY_STATEMENTS, "source": "SEC EDGAR 10-K filings (annual only)"} if conflict else statements(t, prices, info, "quarterly")

    data = {
        "ticker": ticker,
        "info": info,
        "prices": price_series,
        "financials": {"annual": annual, "quarterly": quarterly},
        "scorecard": scorecard(annual, info) if annual["income"] and not conflict else [],
        **extras,
        "updated": int(time.time()),
    }
    with _lock:
        _cache[ticker] = (time.time(), data)
    return data


@app.get("/api/stock/{ticker}")
def stock(ticker: str):
    return load(ticker)


# short ranges use intraday bars: (yfinance period, interval)
INTRADAY = {"1D": ("5d", "2m"), "1W": ("5d", "5m"), "1M": ("1mo", "30m")}
INTRADAY_SECONDS = 60
_intraday: dict[tuple[str, str], tuple[float, dict]] = {}


def session_of(index: pd.DatetimeIndex) -> pd.Series:
    """Label each bar 'pre', 'regular' or 'after' (US hours; other exchanges are all 'regular')."""
    out = np.full(len(index), "regular", dtype=object)
    if index.tz is None or str(index.tz) != "America/New_York":
        return out
    minutes = index.hour * 60 + index.minute
    out[minutes < 9 * 60 + 30] = "pre"
    out[minutes >= 16 * 60] = "after"
    return out


def intraday(ticker: str, rng: str) -> dict:
    """Intraday closes for a short range. 1D = the latest session, measured from the prior session's close."""
    ticker, rng = ticker.upper().strip(), rng.upper()
    if rng not in INTRADAY:
        raise HTTPException(400, f"Unknown range '{rng}'. Use one of {', '.join(INTRADAY)}.")
    key = (ticker, rng)
    with _lock:
        hit = _intraday.get(key)
        if hit and time.time() - hit[0] < INTRADAY_SECONDS:
            return hit[1]
    period, interval = INTRADAY[rng]
    try:
        # 1D includes pre-market (4:00-9:30 ET) and after-hours (16:00-20:00 ET) trading
        hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False, prepost=rng == "1D")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not reach Yahoo Finance: {e}")
    closes = hist["Close"].dropna() if not hist.empty else pd.Series(dtype=float)
    if closes.empty:
        raise HTTPException(404, f"No intraday prices for '{ticker}'.")
    sessions = session_of(closes.index)
    base = None
    if rng == "1D":
        days = closes.index.normalize()
        last, before = days == days[-1], days < days[-1]
        # measured from the prior session's regular close, not its last after-hours trade
        prev_regular = closes[before & (sessions == "regular")]
        base = clean(prev_regular.iloc[-1]) if len(prev_regular) else None
        closes, sessions = closes[last], sessions[last]
    data = {
        "range": rng,
        "tz": str(closes.index.tz) if closes.index.tz is not None else "UTC",
        "points": [[int(ts.timestamp() * 1000), round(float(v), 4)] for ts, v in closes.items()],
        "sessions": [s[0] for s in sessions],   # "p"re-market, "r"egular, "a"fter hours
        "base": base if base is not None else round(float(closes.iloc[0]), 4),
        "baseIsPrevClose": base is not None,
    }
    with _lock:
        _intraday[key] = (time.time(), data)
    return data


@app.get("/api/prices/{ticker}")
def prices(ticker: str, range: str):  # noqa: A002  (query parameter name)
    return intraday(ticker, range)


_company: dict[str, tuple[float, dict]] = {}


@app.get("/api/company/{ticker}")
def company_info(ticker: str):
    """Profile, news, filings, analyst forecasts, ownership, dividends and fair value inputs."""
    ticker = ticker.upper().strip()
    with _lock:
        hit = _company.get(ticker)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = company.company_data(ticker)
    with _lock:
        _company[ticker] = (time.time(), data)
    return data


def _safe(fn, what):
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load {what}: {e}")


@app.get("/api/market/screener")
def market_screener():
    return _safe(market.screener, "the screener")


@app.get("/api/quotes")
def stock_quotes(symbols: str):
    return _safe(lambda: market.quotes([s.strip().upper() for s in symbols.split(",") if s.strip()]), "quotes")


@app.get("/api/market/movers")
def market_movers():
    return _safe(market.movers, "market movers")


@app.get("/api/market/earnings")
def market_earnings():
    return _safe(market.earnings_calendar, "the earnings calendar")


@app.get("/api/peers/{ticker}")
def stock_peers(ticker: str):
    return _safe(lambda: market.peers(ticker.upper().strip()), "peers")


@app.get("/api/macro")
def macro_overview():
    return _safe(macro.overview, "macro data")


@app.get("/api/assets")
def assets_table():
    return _safe(asset_data.table, "asset prices")


@app.get("/api/assets/{symbol}/history")
def asset_history(symbol: str):
    d = _safe(lambda: asset_data.history(symbol), "asset history")
    if d is None:
        raise HTTPException(404, f"Unknown asset '{symbol}'.")
    return d


@app.get("/api/market/summary")
def market_summary():
    """Major index levels (S&P 500, Nasdaq, Dow, Russell 2000) with today's change."""
    try:
        return market.summary()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load index levels: {e}")


@app.get("/api/market/heatmap/{index}")
def market_heatmap(index: str):
    """Index members grouped by sector with market cap and today's % change (index: sp500, ndx, dow)."""
    if index not in market.INDEXES:
        raise HTTPException(404, f"Unknown index '{index}'. Use one of {', '.join(market.INDEXES)}.")
    try:
        return market.heatmap(index)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load the heat map: {e}")


@app.get("/api/search")
def search(q: str):
    q = q.strip()
    if not q:
        return []
    try:
        res = yf.Search(q, max_results=8, news_count=0).quotes
    except Exception:  # noqa: BLE001
        return []
    return [
        {"symbol": r.get("symbol"), "name": r.get("longname") or r.get("shortname"),
         "exchange": r.get("exchDisp"), "type": r.get("typeDisp")}
        for r in res if r.get("quoteType") in ("EQUITY", "ETF")
    ]


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
@app.get("/screener")
@app.get("/compare")
@app.get("/portfolio")
@app.get("/earnings")
@app.get("/macro")
@app.get("/assets")
@app.get("/stock/{ticker}")
def index(ticker: str | None = None):
    return FileResponse(STATIC / "index.html")
