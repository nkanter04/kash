"""Long-history fundamentals for the screener: up to 15 fiscal years per company from SEC filings, boiled down to a
handful of quality measures (growth, returns on capital, consistency, share count).

`python fundamentals.py build` creates .cache/fundamentals.json (about 25 minutes: one SEC download per company).
A copy in data/fundamentals.json is used when the cache has none, so a hosted copy can show the same numbers.
The numbers come from the same code as the stock pages (sec.frames_from_facts), so the two always agree.
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

import sec

ROOT = Path(__file__).parent
FILE = sec.CACHE / "fundamentals.json"
SNAPSHOT = ROOT / "data" / "fundamentals.json"
_mem: dict = {"key": None, "data": None}


def load() -> dict:
    """-> {"built": iso date, "rows": {symbol: summary}} or {} when nothing has been built."""
    f = FILE if FILE.exists() else SNAPSHOT
    if not f.exists():
        return {}
    key = (str(f), f.stat().st_mtime)
    if _mem["key"] != key:
        _mem["data"], _mem["key"] = json.loads(f.read_text(encoding="utf-8")), key
    return _mem["data"]


# ------------------------------------------------------------------ one company

def _cagr(new, old, years):
    if new is None or old is None or new <= 0 or old <= 0 or years <= 0:
        return None
    return (new / old) ** (1 / years) - 1


def _roic(op, pretax, tax, cash, debt, equity, assets):
    """Same rule as rows._roic: after-tax operating profit over debt + equity - cash."""
    rate = tax / pretax if tax is not None and pretax else None
    rate = rate if rate is not None and 0 <= rate <= 0.5 else 0.21
    invested = (debt or 0) + (equity or 0) - (cash or 0)
    if op is None or invested <= 0 or (assets and invested < 0.05 * assets):
        return None
    return op * (1 - rate) / invested


def summarize(frames) -> dict | None:
    """Annual statement frames (newest year first) -> the screener's quality measures."""
    if not frames:
        return None
    inc, bal, cf = frames
    if inc.empty:
        return None
    cols = list(inc.columns)

    def row(df, *names):
        for n in names:
            if n in df.index:
                return [None if pd.isna(v) else float(v) for v in df.loc[n].reindex(cols)]
        return [None] * len(cols)

    rev, ni, op = row(inc, "Total Revenue"), row(inc, "Net Income"), row(inc, "Operating Income")
    gp, eps, sh = row(inc, "Gross Profit"), row(inc, "Diluted EPS"), row(inc, "Diluted Average Shares")
    pretax, tax, ebitda = row(inc, "Pretax Income"), row(inc, "Tax Provision"), row(inc, "EBITDA")
    fcf, divs = row(cf, "Free Cash Flow"), row(cf, "Cash Dividends Paid")
    cash = row(bal, "Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents")
    debt, eq, assets = row(bal, "Total Debt"), row(bal, "Stockholders Equity", "Common Stock Equity"), row(bal, "Total Assets")
    n = len(cols)
    if rev[0] is None and ni[0] is None:
        return None
    roic = [_roic(op[i], pretax[i], tax[i], cash[i], debt[i], eq[i], assets[i]) for i in range(n)]

    def growth(s, years):
        return _cagr(s[0], s[years], years) if n > years else None

    def share_growth(years):
        g = growth(sh, years)      # a jump this large means a filing switched units (millions vs. units), not real change
        return g if g is not None and -0.2 < g < 0.6 else None

    def div(a, b):
        return a / b if a is not None and b else None

    def known(s, k=10):
        return [v for v in s[:k] if v is not None]

    r10, r5 = known(roic), known(roic, 5)
    ups = [rev[i] > rev[i + 1] for i in range(min(10, n - 1)) if rev[i] is not None and rev[i + 1] is not None]
    fcf6 = known(fcf, 6)
    out = {
        "yrs": n, "fy": cols[0].strftime("%Y-%m-%d"),
        "rev5": growth(rev, 5), "rev10": growth(rev, 10), "eps5": growth(eps, 5), "eps10": growth(eps, 10),
        "sh5": share_growth(5), "sh10": share_growth(10),
        "gm": div(gp[0], rev[0]), "om": div(op[0], rev[0]), "nm": div(ni[0], rev[0]),
        "roic": roic[0], "roic5": sum(r5) / len(r5) if r5 else None,
        "roic10": statistics.median(r10) if len(r10) >= 8 else None,
        "roicMin": min(r10) if len(r10) >= 8 else None,
        "roic15": sum(v >= 0.15 for v in r10) if len(r10) >= 8 else None,
        "revUp": sum(ups) if len(ups) >= 8 else None,
        "prof": sum(v > 0 for v in known(ni)) if len(known(ni)) >= 8 else None,
        "fcfPos": sum(v > 0 for v in known(fcf)) if len(known(fcf)) >= 8 else None,
        "de": div(debt[0], eq[0]) if eq[0] and eq[0] > 0 else None,
        "rs": [None if v is None else round(v, 3) for v in reversed(roic[:10])],
    }
    # quality score: the scorecard's business checks (the price-based ones need live prices and stay on the stock page)
    net = (cash[0] or 0) - debt[0] if debt[0] is not None else None
    checks = [
        None if out["rev5"] is None else round(out["rev5"], 3) >= 0.05,
        None if out["nm"] is None else out["nm"] >= 0.10,
        None if not fcf6 else all(v > 0 for v in fcf6),
        None if out["roic5"] is None else round(out["roic5"], 3) >= 0.10,
        None if net is None else net >= 0 or (ebitda[0] is not None and debt[0] < 3 * ebitda[0]),
        None if out["sh5"] is None else out["sh5"] <= 0.005 or bool(divs[0]),
    ]
    scored = [c for c in checks if c is not None]
    out["qs"], out["qn"] = (sum(scored), len(scored)) if len(scored) >= 4 else (None, len(scored))
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


# ------------------------------------------------------------------ build

def _facts(cik: int):
    """Company facts without filling the disk cache (thousands of companies would take gigabytes)."""
    f = sec.CACHE / f"facts_{cik}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 7 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    req = urllib.request.Request(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                                 headers={"User-Agent": sec._user_agent(), "Accept-Encoding": "gzip"})
    with sec._lock:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        time.sleep(0.12)
    return json.loads(body)


def fetch_splits(symbols: list[str], log=print) -> dict[str, pd.Series]:
    """Stock splits since 2008 for many tickers, in batches (SEC share counts must be restated to today's basis)."""
    import yfinance as yf
    out: dict[str, pd.Series] = {}
    for i in range(0, len(symbols), 150):
        batch = symbols[i:i + 150]
        for attempt in range(4):
            try:
                df = yf.download(batch, start="2008-01-01", interval="1mo", actions=True, auto_adjust=False,
                                 group_by="ticker", progress=False, threads=False)
                break
            except Exception as e:  # noqa: BLE001
                log(f"  splits batch {i}: {e}; retrying")
                time.sleep(30 * (attempt + 1))
        else:
            raise RuntimeError("Yahoo would not return split history")
        for s in batch:
            try:
                col = df[s]["Stock Splits"]
            except KeyError:
                continue
            col = col[col > 0]
            if len(col):
                out[s] = col
        log(f"  splits {min(i + 150, len(symbols))}/{len(symbols)}")
        time.sleep(1.5)
    return out


def build(symbols: list[str] | None = None, log=print) -> dict:
    import market
    if not sec._user_agent():
        raise SystemExit("Set the SEC contact email first (config.json or SEC_USER_AGENT).")
    symbols = symbols or [s for s, q in market.quotes_all().items() if q.get("cap")]
    log(f"{len(symbols)} companies")
    splits = fetch_splits(symbols, log)
    rows, skipped = {}, 0
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=800)
    for i, sym in enumerate(symbols, 1):
        try:
            cik = sec.cik_for(sym)
            data = _facts(cik) if cik else None
            frames = sec.frames_from_facts(data, 15, splits.get(sym)) if data else None
            s = summarize(frames)
            if s and pd.Timestamp(s["fy"]) >= cutoff:      # an old last year means the filer changed (merger, new CIK)
                rows[sym] = s
            else:
                skipped += 1
        except urllib.error.HTTPError as e:
            skipped += 1
            if e.code == 429:
                log("  SEC asked us to slow down; pausing 10 minutes")
                time.sleep(600)
        except Exception as e:  # noqa: BLE001
            skipped += 1
            log(f"  {sym}: {type(e).__name__}: {e}")
        if i % 100 == 0:
            log(f"  {i}/{len(symbols)} done, {len(rows)} with SEC history")
    out = {"built": time.strftime("%Y-%m-%d"), "rows": rows}
    FILE.parent.mkdir(exist_ok=True)
    FILE.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    log(f"Saved {len(rows)} companies ({skipped} without usable SEC history) to {FILE}")
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build(sys.argv[2:] or None)
    elif sys.argv[1:2] == ["snapshot"]:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(FILE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Copied to {SNAPSHOT}")
    else:
        print("usage: python fundamentals.py build [TICKER ...] | snapshot")
