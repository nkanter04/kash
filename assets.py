"""Beyond stocks: trends for crypto, metals, energy, farm goods, currencies and broad funds (Yahoo Finance)."""
from __future__ import annotations

import threading
import time

import pandas as pd
import yfinance as yf

DATA_SECONDS = 300
GROUPS = [
    ("Crypto", [("BTC-USD", "Bitcoin"), ("ETH-USD", "Ethereum"), ("SOL-USD", "Solana"), ("XRP-USD", "XRP"),
                ("BNB-USD", "BNB"), ("DOGE-USD", "Dogecoin"), ("ADA-USD", "Cardano")]),
    ("Sectors", [("XLK", "Technology"), ("XLC", "Communication services"), ("XLY", "Consumer discretionary"), ("XLP", "Consumer staples"),
                 ("XLF", "Financials"), ("XLV", "Health care"), ("XLI", "Industrials"), ("XLE", "Energy"), ("XLB", "Materials"),
                 ("XLU", "Utilities"), ("XLRE", "Real estate")]),
    ("Metals", [("GC=F", "Gold"), ("SI=F", "Silver"), ("PL=F", "Platinum"), ("HG=F", "Copper")]),
    ("Energy", [("CL=F", "Crude oil (WTI)"), ("BZ=F", "Brent oil"), ("NG=F", "Natural gas")]),
    ("Farm", [("ZC=F", "Corn"), ("ZW=F", "Wheat")]),
    ("Currencies", [("DX-Y.NYB", "US dollar index"), ("EURUSD=X", "Euro in dollars"), ("JPY=X", "Dollar in yen")]),
    ("Markets", [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^RUT", "Russell 2000"), ("EFA", "Developed markets (EFA)"),
                 ("EEM", "Emerging markets (EEM)"), ("VNQ", "Real estate (VNQ)"), ("TLT", "Long Treasuries (TLT)")]),
]
NAMES = {s: n for _, items in GROUPS for s, n in items}
# needed elsewhere (Macro page) but not listed in the table
HIDDEN = ["^VIX", "^W5000"]
RATIOS = {
    "gold-silver": ("Gold / silver ratio", "GC=F", "SI=F", "Ounces of silver one ounce of gold buys. High readings have meant silver is cheap relative to gold."),
    "btc-gold": ("Bitcoin priced in gold", "BTC-USD", "GC=F", "Ounces of gold one bitcoin buys. Removes the US dollar from the comparison."),
    "copper-gold": ("Copper / gold ratio", "HG=F", "GC=F", "Rising means industrial demand is beating fear; often moves with growth expectations and bond yields."),
    "sp-gold": ("S&P 500 priced in gold", "^GSPC", "GC=F", "How many ounces of gold the S&P 500 is worth. Falling means gold is outrunning stocks."),
    "oil-gold": ("Oil / gold ratio", "CL=F", "GC=F", "Barrels-to-ounces value of oil against gold."),
}

_lock = threading.Lock()
_fetch_lock = threading.Lock()
_data: tuple[float, pd.DataFrame] | None = None


def frame() -> pd.DataFrame:
    """Daily closes for every asset (tz-naive dates, one column per symbol), cached for 5 minutes."""
    global _data
    with _lock:
        if _data and time.time() - _data[0] < DATA_SECONDS:
            return _data[1]
    with _fetch_lock:   # one download at a time; others wait and reuse it
        with _lock:
            if _data and time.time() - _data[0] < DATA_SECONDS:
                return _data[1]
        d = yf.download(list(NAMES) + HIDDEN, period="max", interval="1d", progress=False, auto_adjust=False, threads=True)["Close"]
        d.index = pd.to_datetime(d.index).tz_localize(None) if getattr(d.index, "tz", None) is not None else pd.to_datetime(d.index)
        d = d.dropna(how="all").sort_index()
        with _lock:
            _data = (time.time(), d)
        return d


def close(symbol: str) -> pd.Series | None:
    d = frame()
    return d[symbol].dropna() if symbol in d else None


def _back(s: pd.Series, when: pd.Timestamp) -> float | None:
    if when < s.index[0]:
        return None
    return float(s.asof(when))


def _chg(now: float, past: float | None) -> float | None:
    return now / past - 1 if past else None


def row(symbol: str, name: str, s: pd.Series) -> dict:
    last, today = float(s.iloc[-1]), s.index[-1]
    ytd = s[s.index < pd.Timestamp(today.year, 1, 1)]
    ath = float(s.max())
    tail = s.tail(180)
    step = max(1, len(tail) // 60)
    return {
        "symbol": symbol, "name": name, "price": last, "date": today.strftime("%Y-%m-%d"),
        "d1": _chg(last, float(s.iloc[-2])) if len(s) > 1 else None,
        "w1": _chg(last, _back(s, today - pd.Timedelta(days=7))),
        "m1": _chg(last, _back(s, today - pd.DateOffset(months=1))),
        "ytd": _chg(last, float(ytd.iloc[-1])) if len(ytd) else None,
        "y1": _chg(last, _back(s, today - pd.DateOffset(years=1))),
        "y5": _chg(last, _back(s, today - pd.DateOffset(years=5))),
        "ath": ath, "fromAth": last / ath - 1,
        "spark": [round(float(v), 6) for v in tail.iloc[::step]],
    }


def table() -> dict:
    d = frame()
    groups = []
    for label, items in GROUPS:
        rows = []
        for sym, name in items:
            s = d[sym].dropna() if sym in d else pd.Series(dtype=float)
            if len(s) > 1:
                rows.append(row(sym, name, s))
        if rows:
            groups.append({"name": label, "rows": rows})
    ratios = []
    for key, (title, a, b, what) in RATIOS.items():
        s = ratio_series(key)
        if s is not None and len(s) > 1:
            ratios.append({**row(key, title, s), "what": what, "fromAth": None, "ath": None})
    return {"groups": groups, "ratios": ratios, "asOf": int(time.time())}


def ratio_series(key: str) -> pd.Series | None:
    if key not in RATIOS:
        return None
    _, a, b, _ = RATIOS[key]
    d = frame()
    if a not in d or b not in d:
        return None
    x = d[a].dropna()
    y = d[b].reindex(x.index).ffill()    # crypto trades on weekends, metals don't
    r = (x / y).dropna()
    return r[r > 0]


def history(symbol: str) -> dict | None:
    is_ratio = symbol in RATIOS
    if not is_ratio and symbol not in NAMES:
        return None
    s = ratio_series(symbol) if is_ratio else close(symbol)
    if s is None or s.empty:
        return None
    return {"symbol": symbol, "name": RATIOS[symbol][0] if is_ratio else NAMES[symbol], "ratio": is_ratio,
            "points": [[int(ts.timestamp() * 1000), round(float(v), 6)] for ts, v in s.items()]}
