"""Market overview for the home page: major index levels and a heat map of index members.

Members (which companies are in each index, and their sector):
  - S&P 500 and Dow 30: Wikipedia's constituent tables (GICS sectors).
  - Nasdaq-100: Nasdaq's own published list; sectors come from the S&P 500 table, or Yahoo for the
    handful of members that aren't in the S&P 500 (e.g. foreign ADRs).
  Cached on disk for a week; index changes are rare.
Size and today's move: Yahoo's equity screener, which returns market cap and % change for ~250 stocks per
call. Cached in memory for 2 minutes.
"""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.request
from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE = Path(__file__).parent / ".cache"
MEMBERS_TTL = 7 * 86400
QUOTES_TTL = 120
UA = {"User-Agent": "Kash/1.0 (personal stock research app)"}
_lock = threading.Lock()
_mem: dict[str, tuple[float, object]] = {}

INDEXES = {"sp500": "S&P 500", "ndx": "Nasdaq-100", "dow": "Dow 30"}
SUMMARY = [("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq"), ("^DJI", "Dow Jones"), ("^RUT", "Russell 2000")]


def _cached(key: str, ttl: float, fn):
    with _lock:
        hit = _mem.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _mem[key] = (time.time(), val)
    return val


def _get(url: str, headers=None) -> str:
    req = urllib.request.Request(url, headers=headers or UA)
    with urllib.request.urlopen(req, timeout=30) as f:
        return f.read().decode("utf-8")


def yahoo_symbol(s: str) -> str:
    return str(s).strip().upper().replace(".", "-")   # BRK.B -> BRK-B


# ------------------------------------------------------------------ members

def _wiki_table(url: str, need: tuple[str, ...]) -> pd.DataFrame:
    for t in pd.read_html(io.StringIO(_get(url))):
        if all(c in t.columns for c in need):
            return t
    raise ValueError(f"no table with columns {need} at {url}")


def _sp500() -> list[dict]:
    t = _wiki_table("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol", "Security", "GICS Sector"))
    return [{"symbol": yahoo_symbol(r["Symbol"]), "name": r["Security"], "sector": r["GICS Sector"],
             "industry": r.get("GICS Sub-Industry")} for _, r in t.iterrows()]


def _dow() -> list[dict]:
    t = _wiki_table("https://en.wikipedia.org/wiki/List_of_Dow_Jones_Industrial_Average_companies", ("Symbol", "Company", "Sector"))
    return [{"symbol": yahoo_symbol(r["Symbol"]), "name": r["Company"], "sector": r["Sector"], "industry": None}
            for _, r in t.iterrows()]


def _ndx() -> list[dict]:
    body = _get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                {"User-Agent": "Mozilla/5.0 (Kash personal research)", "Accept": "application/json"})
    rows = json.loads(body)["data"]["data"]["rows"]
    sectors = {m["symbol"]: m for m in members("sp500")}
    out = []
    for r in rows:
        sym = yahoo_symbol(r["symbol"])
        known = sectors.get(sym)
        sector = known["sector"] if known else _yahoo_sector(sym)
        name = (known or {}).get("name") or r.get("companyName", sym).replace(" Common Stock", "").replace(" American Depositary Shares", "")
        out.append({"symbol": sym, "name": name, "sector": sector or "Other", "industry": (known or {}).get("industry")})
    return out


# Yahoo's sector names -> GICS names used by the S&P 500 table, so all heat maps group the same way
YAHOO_TO_GICS = {"Technology": "Information Technology", "Consumer Cyclical": "Consumer Discretionary",
                 "Consumer Defensive": "Consumer Staples", "Healthcare": "Health Care",
                 "Financial Services": "Financials", "Basic Materials": "Materials"}


def _yahoo_sector(sym: str) -> str | None:
    try:
        s = yf.Ticker(sym).info.get("sector")
    except Exception:  # noqa: BLE001
        return None
    return YAHOO_TO_GICS.get(s, s)


SOURCES = {"sp500": _sp500, "dow": _dow, "ndx": _ndx}


def members(index: str) -> list[dict]:
    """Index members with sectors, cached on disk for a week (falls back to a stale copy if a source is down)."""
    path = CACHE / f"members_{index}.json"
    if path.exists() and time.time() - path.stat().st_mtime < MEMBERS_TTL:
        return json.loads(path.read_text(encoding="utf-8"))
    try:
        rows = SOURCES[index]()
        if len(rows) < 25:
            raise ValueError("member list looks incomplete")
    except Exception:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        raise
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows), encoding="utf-8")
    return rows


# ------------------------------------------------------------------ quotes

def _universe() -> dict[str, dict]:
    """Market cap and today's % change for US stocks above ~$1B, largest first (a few screener calls)."""
    from yfinance import EquityQuery
    q = EquityQuery("and", [EquityQuery("gt", ["intradaymarketcap", 1e9]), EquityQuery("eq", ["region", "us"])])
    out, offset = {}, 0
    while True:
        r = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=250, offset=offset)
        for x in r.get("quotes", []):
            out[x["symbol"]] = {"cap": x.get("marketCap"), "change": x.get("regularMarketChangePercent"),
                                "price": x.get("regularMarketPrice"), "name": x.get("shortName")}
        offset += 250
        if not r.get("quotes") or offset >= min(r.get("total", 0), 4000):
            break
    return out


def heatmap(index: str) -> dict:
    """Members grouped by sector, each with market cap (size) and today's % change (color)."""
    if index not in INDEXES:
        raise KeyError(index)
    quotes = _cached("universe", QUOTES_TTL, _universe)
    sectors: dict[str, list] = {}
    missing = []
    for m in members(index):
        q = quotes.get(m["symbol"])
        if not q or not q.get("cap"):
            missing.append(m["symbol"])
            continue
        change = q["change"] / 100 if q.get("change") is not None else None   # screener gives percent
        sectors.setdefault(m["sector"] or "Other", []).append(
            {"symbol": m["symbol"], "name": m["name"], "cap": q["cap"], "change": change, "price": q.get("price")})
    groups = []
    for name, items in sectors.items():
        items.sort(key=lambda x: -x["cap"])
        cap = sum(x["cap"] for x in items)
        weighted = [x for x in items if x["change"] is not None]
        wcap = sum(x["cap"] for x in weighted)
        groups.append({"name": name, "cap": cap, "items": items,
                       "change": sum(x["cap"] * x["change"] for x in weighted) / wcap if wcap else None})
    groups.sort(key=lambda g: -g["cap"])
    return {"index": index, "label": INDEXES[index], "asOf": int(time.time()), "sectors": groups, "missing": missing}


def summary() -> list[dict]:
    """Major index levels with today's change and a one-month line."""
    def fetch():
        d = yf.download([s for s, _ in SUMMARY], period="1mo", interval="1d", progress=False, auto_adjust=False)["Close"]
        out = []
        for sym, label in SUMMARY:
            s = d[sym].dropna() if sym in d else pd.Series(dtype=float)
            if len(s) < 2:
                continue
            out.append({"symbol": sym, "label": label, "price": float(s.iloc[-1]),
                        "change": float(s.iloc[-1] / s.iloc[-2] - 1), "spark": [round(float(v), 2) for v in s]})
        return out
    return _cached("summary", QUOTES_TTL, fetch)
