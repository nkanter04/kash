"""Company details beyond the financial statements: profile, news, analyst forecasts, ownership,
dividends and the inputs for the fair value calculator.

Everything comes from Yahoo Finance (via yfinance) except the filings list (SEC EDGAR, sec.py).
Each section is fetched independently: if Yahoo has no data for one (common for ETFs and foreign
listings), that section is None and the rest of the page still works.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

import sec


def _num(v):
    """JSON-safe number (NaN/inf/NA -> None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _day(v) -> str | None:
    """Any date-like value -> 'YYYY-MM-DD'."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, timezone.utc).date().isoformat()
        return pd.Timestamp(v).date().isoformat()
    except (ValueError, TypeError):
        return None


def _safe(fn):
    """Run one section; a Yahoo hiccup or missing table should never break the page."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ profile

def profile(t: yf.Ticker, info: dict) -> dict:
    officers = [
        {"name": o.get("name"), "title": o.get("title"), "age": o.get("age"), "pay": _num(o.get("totalPay"))}
        for o in (info.get("companyOfficers") or [])[:8]
    ]
    cal = _safe(lambda: t.calendar) or {}
    earnings = cal.get("Earnings Date") or []
    if not isinstance(earnings, list):
        earnings = [earnings]
    place = ", ".join(x for x in (info.get("city"), info.get("state"), info.get("country")) if x)
    return {
        "officers": officers,
        "address": ", ".join(x for x in (info.get("address1"), place, info.get("zip")) if x) or None,
        "phone": info.get("phone"),
        "dates": {
            "earnings": _day(earnings[0]) if earnings else _day(info.get("earningsTimestamp")),
            "earningsEstimate": _num(cal.get("Earnings Average")),
            "revenueEstimate": _num(cal.get("Revenue Average")),
            "exDividend": _day(cal.get("Ex-Dividend Date")) or _day(info.get("exDividendDate")),
            "dividendPaid": _day(cal.get("Dividend Date")),
        },
        "trading": {k: _num(info.get(k)) for k in (
            "open", "dayLow", "dayHigh", "volume", "averageVolume", "sharesOutstanding", "floatShares",
            "sharesShort", "shortPercentOfFloat", "shortRatio")},
    }


def news(t: yf.Ticker, limit: int = 8) -> list[dict]:
    out = []
    for item in (t.news or [])[:limit]:
        c = item.get("content") or item
        url = ((c.get("clickThroughUrl") or {}).get("url") or (c.get("canonicalUrl") or {}).get("url")
               or c.get("link"))
        if not (c.get("title") and url):
            continue
        out.append({"title": c["title"], "url": url, "summary": c.get("summary"),
                    "publisher": (c.get("provider") or {}).get("displayName") or c.get("publisher"),
                    "date": c.get("pubDate") or _day(c.get("providerPublishTime"))})
    return out


# ------------------------------------------------------------------ analysts

PERIODS = {"0q": "This quarter", "+1q": "Next quarter", "0y": "This fiscal year", "+1y": "Next fiscal year"}


def _estimates(df: pd.DataFrame | None, ago_col: str) -> list[dict]:
    if df is None or df.empty:
        return []
    rows = []
    for p, label in PERIODS.items():
        if p in df.index:
            r = df.loc[p]
            rows.append({"period": label, "avg": _num(r.get("avg")), "low": _num(r.get("low")),
                         "high": _num(r.get("high")), "analysts": _num(r.get("numberOfAnalysts")),
                         "yearAgo": _num(r.get(ago_col)), "growth": _num(r.get("growth"))})
    return rows


def forecast(t: yf.Ticker, info: dict) -> dict:
    rec = _safe(lambda: t.recommendations)
    ratings = None
    if rec is not None and not rec.empty:
        r = rec.iloc[0]
        ratings = {k: int(r.get(k) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}

    hist = _safe(lambda: t.earnings_history)
    history = [] if hist is None or hist.empty else [
        {"quarter": _day(q), "actual": _num(r.get("epsActual")), "estimate": _num(r.get("epsEstimate")),
         "surprise": _num(r.get("surprisePercent"))}
        for q, r in hist.iterrows()
    ]

    ud = _safe(lambda: t.upgrades_downgrades)
    changes = [] if ud is None or ud.empty else [
        {"date": _day(d), "firm": r.get("Firm"), "from": r.get("FromGrade") or None, "to": r.get("ToGrade"),
         "action": r.get("Action"), "target": _num(r.get("currentPriceTarget")) or None,
         "priorTarget": _num(r.get("priorPriceTarget")) or None}
        for d, r in ud.sort_index(ascending=False).head(12).iterrows()
    ]

    # Yahoo's long-term (LTG) estimate is blank for individual stocks, so use next fiscal year's EPS growth
    growth = _safe(lambda: t.growth_estimates)
    next_year = _num(growth.at["+1y", "stockTrend"]) if growth is not None and "+1y" in growth.index else None

    return {
        "eps": _estimates(_safe(lambda: t.earnings_estimate), "yearAgoEps"),
        "revenue": _estimates(_safe(lambda: t.revenue_estimate), "yearAgoRevenue"),
        "history": history,
        "ratings": ratings,
        "consensus": info.get("recommendationKey"),
        "target": {k: _num(info.get(k)) for k in ("targetLowPrice", "targetMeanPrice", "targetMedianPrice",
                                                  "targetHighPrice", "numberOfAnalystOpinions")},
        "changes": changes,
        "nextYearGrowth": next_year,
    }


# ------------------------------------------------------------------ ownership & dividends

def _kind(text: str) -> str:
    text = (text or "").lower()
    if text.startswith("purchase"):
        return "buy"
    if text.startswith("sale"):
        return "sell"
    return "other"   # stock awards, gifts, option conversions: not open-market trades


def ownership(t: yf.Ticker) -> dict:
    major = _safe(lambda: t.major_holders)
    held = {}
    if major is not None and not major.empty:
        col = major.columns[0]
        held = {k: _num(major.at[k, col]) for k in major.index}

    inst = _safe(lambda: t.institutional_holders)
    institutions = [] if inst is None or inst.empty else [
        {"holder": r.get("Holder"), "pct": _num(r.get("pctHeld")), "shares": _num(r.get("Shares")),
         "value": _num(r.get("Value")), "change": _num(r.get("pctChange")), "date": _day(r.get("Date Reported"))}
        for _, r in inst.head(10).iterrows()
    ]

    ins = _safe(lambda: t.insider_transactions)
    insiders, summary = [], {"buyValue": 0.0, "sellValue": 0.0, "buys": 0, "sells": 0}
    if ins is not None and not ins.empty:
        cutoff = pd.Timestamp(datetime.now() - timedelta(days=182))
        for _, r in ins.iterrows():
            kind, when = _kind(r.get("Text")), pd.Timestamp(r.get("Start Date"))
            value = _num(r.get("Value"))
            if kind != "other" and when >= cutoff:
                summary[kind + "s"] += 1
                summary[kind + "Value"] += value or 0
            if len(insiders) < 15:
                insiders.append({"date": _day(when), "insider": str(r.get("Insider") or "").title(),
                                 "position": r.get("Position"), "kind": kind, "text": r.get("Text") or None,
                                 "shares": _num(r.get("Shares")), "value": value})
    return {
        "insidersPct": held.get("insidersPercentHeld"),
        "institutionsPct": held.get("institutionsPercentHeld"),
        "institutionsCount": held.get("institutionsCount"),
        "institutions": institutions,
        "insiders": insiders,
        "insiderSummary": summary,
    }


def dividends(t: yf.Ticker, info: dict) -> dict | None:
    d = t.dividends
    if d is None or d.empty:
        return None
    d = d.sort_index()
    this_year = datetime.now().year
    by_year = d.groupby(d.index.year).agg(["sum", "count"])
    years = [{"year": int(y), "amount": round(float(r["sum"]), 4), "payments": int(r["count"]),
              "partial": int(y) == this_year}
             for y, r in by_year.iterrows()]
    full = [y for y in years if not y["partial"]]
    for prev, cur in zip(full, full[1:]):
        cur["growth"] = cur["amount"] / prev["amount"] - 1 if prev["amount"] else None
    # consecutive years in which the yearly total went up (counting back from the last full year)
    streak = 0
    for prev, cur in zip(reversed(full[:-1]), reversed(full)):
        if cur["year"] == prev["year"] + 1 and cur["amount"] > prev["amount"] * 1.001:
            streak += 1
        else:
            break
    recent = [{"date": _day(ts), "amount": round(float(v), 4)} for ts, v in d.tail(8)[::-1].items()]
    five_avg = _num(info.get("fiveYearAvgDividendYield"))
    # funds have no dividendRate: fall back to what was paid over the last 12 months
    now = pd.Timestamp.now(tz=d.index.tz)
    rate = _num(info.get("dividendRate")) or _num(info.get("trailingAnnualDividendRate"))
    if not rate:
        rate = round(float(d[d.index > now - pd.Timedelta(days=365)].sum()), 4) or None
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    return {
        "years": years[-15:],
        "recent": recent,
        "streak": streak,
        "rate": rate,
        "yield": rate / price if rate and price else None,
        "payoutRatio": _num(info.get("payoutRatio")),
        "fiveYearAvgYield": five_avg / 100 if five_avg is not None else None,   # Yahoo gives percent here
    }


# ------------------------------------------------------------------ fair value inputs

def fair_value_inputs(info: dict, fc: dict | None) -> dict:
    shares = _num(info.get("sharesOutstanding"))
    fcf = _num(info.get("freeCashflow"))
    # FCF is in the reporting currency and per *listed* share; for ADRs like TSM (TWD statements, USD price,
    # 1 ADR = 5 shares) that doesn't line up with the price, so leave it out
    same_currency = (info.get("financialCurrency") or info.get("currency")) == info.get("currency")
    return {
        "eps": _num(info.get("trailingEps")),
        "forwardEps": _num(info.get("forwardEps")),
        "fcfPerShare": fcf / shares if fcf is not None and shares and same_currency else None,
        "analystGrowth": (fc or {}).get("nextYearGrowth"),
    }


# ------------------------------------------------------------------ everything

def company_data(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = _safe(lambda: t.info) or {}
    fc = _safe(lambda: forecast(t, info))
    return {
        "ticker": ticker,
        "profile": _safe(lambda: profile(t, info)),
        "news": _safe(lambda: news(t)) or [],
        "filings": _safe(lambda: sec.recent_filings(ticker)) or [],
        "forecast": fc,
        "ownership": _safe(lambda: ownership(t)),
        "dividends": _safe(lambda: dividends(t, info)),
        "fairValue": fair_value_inputs(info, fc),
    }
