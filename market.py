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

def _rating(text) -> float | None:
    """'2.3 - Buy' -> 2.3 (1 = strong buy, 5 = strong sell)."""
    try:
        return float(str(text).split(" ")[0])
    except ValueError:
        return None


def _quote(x: dict) -> dict:
    """One screener row. Careful with units: the *ChangePercent fields below are mixed (see the /100s)."""
    ch52 = x.get("fiftyTwoWeekChangePercent")
    return {
        "cap": x.get("marketCap"), "change": x.get("regularMarketChangePercent"), "price": x.get("regularMarketPrice"),
        "name": x.get("shortName") or x.get("longName"), "exchange": x.get("fullExchangeName"),
        "pe": x.get("trailingPE"), "fpe": x.get("forwardPE"), "pb": x.get("priceToBook"),
        "eps": x.get("epsTrailingTwelveMonths"), "div": x.get("trailingAnnualDividendYield"),
        "ch1y": ch52 / 100 if ch52 is not None else None,
        "ma50": x.get("fiftyDayAverageChangePercent"), "ma200": x.get("twoHundredDayAverageChangePercent"),
        "fromHigh": x.get("fiftyTwoWeekHighChangePercent"), "fromLow": x.get("fiftyTwoWeekLowChangePercent"),
        "vol": x.get("regularMarketVolume"), "avgVol": x.get("averageDailyVolume3Month"),
        "rating": _rating(x.get("averageAnalystRating")) if x.get("averageAnalystRating") else None,
        "earnings": x.get("earningsTimestamp"), "earningsEstimated": x.get("isEarningsDateEstimate"),
    }


def _universe() -> dict[str, dict]:
    """Market cap and today's % change for US stocks above ~$1B, largest first (a few screener calls)."""
    from yfinance import EquityQuery
    # listed on the major US exchanges only: otherwise foreign OTC lines crowd out real listings
    q = EquityQuery("and", [EquityQuery("gt", ["intradaymarketcap", 1e9]), EquityQuery("eq", ["region", "us"]),
                            EquityQuery("is-in", ["exchange", "NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"])])
    out, offset = {}, 0
    while True:
        r = yf.screen(q, sortField="intradaymarketcap", sortAsc=False, size=250, offset=offset)
        for x in r.get("quotes", []):
            if "OTC" not in (x.get("fullExchangeName") or ""):   # thinly traded foreign lines distort movers and screens
                out[x["symbol"]] = _quote(x)
        offset += 250
        if not r.get("quotes") or offset >= min(r.get("total", 0), 6000):
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
            {"symbol": m["symbol"], "name": m["name"], "cap": q["cap"], "change": change, "price": q.get("price"), "ch1y": q.get("ch1y")})
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


# ------------------------------------------------------------------ screener, movers, earnings, peers

def _membership() -> dict[str, dict]:
    """symbol -> {sector, industry, indexes} from the index member lists."""
    out: dict[str, dict] = {}
    for idx in INDEXES:
        try:
            for m in members(idx):
                d = out.setdefault(m["symbol"], {"sector": m.get("sector"), "industry": m.get("industry"), "indexes": []})
                d["indexes"].append(idx)
                d["sector"] = d["sector"] or m.get("sector")
                d["industry"] = d["industry"] or m.get("industry")
        except Exception:  # noqa: BLE001
            continue
    return out


def quotes_all() -> dict[str, dict]:
    return _cached("universe", QUOTES_TTL, _universe)


def _row(symbol: str, q: dict, m: dict | None) -> dict:
    change = q["change"] / 100 if q.get("change") is not None else None
    m = m or {}
    return {**{k: v for k, v in q.items() if k not in ("change", "earningsEstimated")}, "symbol": symbol, "change": change,
            "sector": m.get("sector"), "industry": m.get("industry"), "indexes": m.get("indexes", [])}


def screener() -> dict:
    mem = _membership()
    import fundamentals
    fund = fundamentals.load()          # long-history quality measures from SEC filings, when built
    quality = fund.get("rows", {})
    rows = [{**_row(s, q, mem.get(s)), **{k: v for k, v in quality.get(s, {}).items() if k != "fy"}}
            for s, q in quotes_all().items() if q.get("cap")]
    rows.sort(key=lambda r: -r["cap"])
    return {"rows": rows, "asOf": int(time.time()), "quality": fund.get("built")}


def movers() -> dict:
    """Home page panels: breadth and notable movers among US stocks above $2B (S&P 500 for breadth)."""
    mem = _membership()
    all_rows = [_row(s, q, mem.get(s)) for s, q in quotes_all().items() if q.get("cap") and q.get("change") is not None]
    big = [r for r in all_rows if r["cap"] >= 2e9]
    sp = [r for r in all_rows if "sp500" in r["indexes"]]

    def top(rows, key, n=8, reverse=True):
        return sorted(rows, key=lambda r: r[key], reverse=reverse)[:n]

    def share(rows, f):
        have = [r for r in rows if f(r) is not None]
        return sum(1 for r in have if f(r)) / len(have) if have else None

    unusual = [{**r, "volRatio": r["vol"] / r["avgVol"]} for r in big if r.get("vol") and r.get("avgVol") and r["avgVol"] > 200_000]
    highs = [r for r in sp if r.get("fromHigh") is not None and r["fromHigh"] >= -0.01]
    lows = [r for r in sp if r.get("fromLow") is not None and r["fromLow"] <= 0.01]
    return {
        "gainers": top(big, "change"), "losers": top(big, "change", reverse=False),
        "unusual": top(unusual, "volRatio"),
        "highs": sorted(highs, key=lambda r: -r["cap"])[:8], "lows": sorted(lows, key=lambda r: -r["cap"])[:8],
        "breadth": {
            "total": len(sp), "up": sum(1 for r in sp if r["change"] > 0), "down": sum(1 for r in sp if r["change"] < 0),
            "above50": share(sp, lambda r: None if r.get("ma50") is None else r["ma50"] > 0),
            "above200": share(sp, lambda r: None if r.get("ma200") is None else r["ma200"] > 0),
            "newHighs": len(highs), "newLows": len(lows),
        },
        "asOf": int(time.time()),
    }


def earnings_calendar(days: int = 21) -> dict:
    """Upcoming earnings for US stocks above $2B, grouped by day, biggest first."""
    mem = _membership()
    now, end = time.time() - 86400, time.time() + days * 86400
    out: dict[str, list] = {}
    for s, q in quotes_all().items():
        t = q.get("earnings")
        if not t or not now <= t <= end or (q.get("cap") or 0) < 2e9:
            continue
        day = pd.Timestamp(t, unit="s", tz="UTC").tz_convert("America/New_York").date().isoformat()
        out.setdefault(day, []).append({**_row(s, q, mem.get(s)), "estimated": bool(q.get("earningsEstimated"))})
    return {"days": [{"date": d, "rows": sorted(out[d], key=lambda r: -r["cap"])} for d in sorted(out)], "asOf": int(time.time())}


def peers(symbol: str, n: int = 8) -> dict:
    """Largest companies in the same GICS sub-industry (or sector when that's too small). S&P 500 + Nasdaq-100 only."""
    mem, quotes = _membership(), quotes_all()
    me = mem.get(symbol)
    if not me or not me.get("sector"):
        return {"basis": None, "rows": []}
    same_ind = [s for s, m in mem.items() if me.get("industry") and m.get("industry") == me["industry"]]
    pool, basis = (same_ind, me["industry"]) if len(same_ind) >= 4 else ([s for s, m in mem.items() if m.get("sector") == me["sector"]], me["sector"])
    rows = [_row(s, quotes[s], mem[s]) for s in pool if s in quotes and quotes[s].get("cap")]
    rows.sort(key=lambda r: -r["cap"])
    chosen = rows[:n]
    if symbol not in [r["symbol"] for r in chosen]:
        chosen = [r for r in rows if r["symbol"] == symbol] + chosen[:n - 1]
    return {"basis": basis, "rows": chosen}


def quotes(symbols: list[str]) -> list[dict]:
    """Current quotes for the watchlist. Anything outside the screener universe (funds, small caps) is fetched singly."""
    mem, uni, out = _membership(), quotes_all(), []
    for s in symbols[:60]:
        if s in uni:
            out.append(_row(s, uni[s], mem.get(s)))
            continue
        try:
            i = yf.Ticker(s).fast_info
            prev, last = i["previousClose"], i["lastPrice"]
            out.append({"symbol": s, "name": s, "price": last, "change": last / prev - 1 if prev else None, "cap": None})
        except Exception:  # noqa: BLE001
            continue
    return out


def spy_adjusted() -> pd.Series:
    """S&P 500 with dividends reinvested (SPY adjusted close), cached for 6 hours."""
    def fetch():
        return yf.Ticker("SPY").history(period="max", interval="1d", auto_adjust=False)["Adj Close"].dropna()
    return _cached("spy_adj", 6 * 3600, fetch)


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
