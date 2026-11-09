"""Standard academic quality scores and total-return figures, computed from data Kash already has."""
from __future__ import annotations

import pandas as pd


def _by_year(fin: dict, table: str, key: str) -> list:
    """Values of one row for real fiscal years only (no TTM/Current), newest first."""
    cols = fin["columns"][table] if fin.get("columns") else []
    row = next((r for r in fin.get(table, []) if r["key"] == key), None)
    if not row:
        return []
    return [v for c, v in zip(cols, row["values"]) if c.get("date")]


def _two(fin, table, key):
    v = _by_year(fin, table, key)
    return (v[0], v[1]) if len(v) >= 2 else (None, None)


def _ratio(a, b):
    return a / b if a is not None and b else None


def piotroski(fin: dict) -> dict | None:
    """Piotroski F-score: nine yes/no tests of profitability, financial strength and efficiency (latest vs prior year)."""
    ni, ni0 = _two(fin, "income", "net_income")
    assets, assets0 = _two(fin, "balance", "total_assets")
    ocf, _ = _two(fin, "cashflow", "ocf")
    debt, debt0 = _two(fin, "balance", "lt_debt")
    ca, ca0 = _two(fin, "balance", "current_assets")
    cl, cl0 = _two(fin, "balance", "current_liab")
    shares, shares0 = _two(fin, "income", "shares")
    gm, gm0 = _two(fin, "income", "gross_margin")
    rev, rev0 = _two(fin, "income", "revenue")
    roa, roa0 = _ratio(ni, assets), _ratio(ni0, assets0)
    lev, lev0 = _ratio(debt or 0, assets) if assets else None, _ratio(debt0 or 0, assets0) if assets0 else None
    cur, cur0 = _ratio(ca, cl), _ratio(ca0, cl0)
    turn, turn0 = _ratio(rev, assets), _ratio(rev0, assets0)

    def cmp(a, b, up=True):
        if a is None or b is None:
            return None
        return a > b if up else a < b

    tests = [
        ("Profitable", ni > 0 if ni is not None else None),
        ("Positive operating cash flow", ocf > 0 if ocf is not None else None),
        ("Return on assets improved", cmp(roa, roa0)),
        ("Cash flow exceeds reported profit", ocf > ni if ocf is not None and ni is not None else None),
        ("Debt load fell", cmp(lev, lev0, up=False) if lev != lev0 else (True if lev is not None else None)),
        ("Short-term liquidity improved", cmp(cur, cur0)),
        ("Didn't issue new shares", shares <= shares0 * 1.01 if shares and shares0 else None),
        ("Gross margin improved", cmp(gm, gm0)),
        ("Sales per dollar of assets improved", cmp(turn, turn0)),
    ]
    known = [(t, bool(ok)) for t, ok in tests if ok is not None]
    if len(known) < 7:
        return None
    score = sum(ok for _, ok in known)
    # scale up when one or two tests can't be run, so the 0-9 reading stays comparable
    scaled = round(score * 9 / len(known))
    label, tone = ("Strong", "good") if scaled >= 7 else ("Weak", "bad") if scaled <= 3 else ("Average", "neutral")
    return {"score": scaled, "raw": score, "tested": len(known), "label": label, "tone": tone,
            "tests": [{"name": t, "pass": ok} for t, ok in known]}


def altman(fin: dict, sector: str | None) -> dict | None:
    """Altman Z-score: bankruptcy risk from five balance-sheet ratios. Not valid for banks, insurers or REITs."""
    if sector in ("Financial Services", "Real Estate"):
        return None
    get = lambda t, k: (_by_year(fin, t, k) or [None])[0]
    ta, tl = get("balance", "total_assets"), get("balance", "total_liab")
    wc, re, ebit, sales = get("balance", "working_capital"), get("balance", "retained"), get("income", "op_income"), get("income", "revenue")
    mv = get("ratios", "mcap")
    if not ta or not tl or None in (wc, re, ebit, sales, mv):
        return None
    z = 1.2 * wc / ta + 1.4 * re / ta + 3.3 * ebit / ta + 0.6 * mv / tl + sales / ta
    label, tone = ("Safe", "good") if z > 2.99 else ("Distress", "bad") if z < 1.81 else ("Grey zone", "neutral")
    return {"score": round(z, 2), "label": label, "tone": tone}


PERIODS = [("1Y", 1), ("3Y", 3), ("5Y", 5), ("10Y", 10)]


def total_returns(adj: pd.Series, benchmark: pd.Series | None = None) -> list[dict]:
    """Return with dividends reinvested over standard periods, against an optional benchmark."""
    if adj is None or len(adj) < 30:
        return []
    def clean(s):
        s = s.dropna()
        if getattr(s.index, "tz", None) is not None:
            s = s.tz_localize(None)
        return s
    adj = clean(adj)
    bench = clean(benchmark) if benchmark is not None and len(benchmark) else None
    last_day, out = adj.index[-1], []
    def stats(s, start, years):
        total = float(s.iloc[-1]) / float(s.asof(start)) - 1
        return total, (1 + total) ** (1 / years) - 1 if years >= 1 else total
    for label, years in PERIODS:
        start = last_day - pd.DateOffset(years=years)
        if start < adj.index[0]:
            continue
        total, cagr = stats(adj, start, years)
        row = {"period": label, "total": total, "cagr": cagr, "value10k": round(10000 * (1 + total))}
        if bench is not None and start >= bench.index[0]:
            row["benchTotal"], row["benchCagr"] = stats(bench, start, years)
        out.append(row)
    years = (last_day - adj.index[0]).days / 365.25
    if years >= 11:
        total, cagr = stats(adj, adj.index[0], years)
        row = {"period": "Since listing", "total": total, "cagr": cagr, "value10k": round(10000 * (1 + total))}
        if bench is not None and adj.index[0] >= bench.index[0]:
            row["benchTotal"], row["benchCagr"] = stats(bench, adj.index[0], years)
        out.append(row)
    return out
