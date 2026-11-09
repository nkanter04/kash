"""Is the overall market expensive? Valuation, recession and sentiment gauges for the Macro page.

Sources: FRED (Federal Reserve Bank of St. Louis) CSV downloads, multpl.com monthly tables (Shiller's long-run
S&P 500 data back to 1871) and Yahoo for the VIX and Wilshire 5000. Files are cached on disk (6-12 hours) and a
stale copy is used if a source is down. Each indicator is built separately, so one failure only hides that card.
"""
from __future__ import annotations

import io
import re
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import assets

CACHE = Path(__file__).parent / ".cache"
FRED_TTL = 6 * 3600
MULTPL_TTL = 12 * 3600
OVERVIEW_TTL = 3600
_lock = threading.Lock()
_overview: tuple[float, dict] | None = None


def _download(url: str, ua: str) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": ua}), timeout=30) as f:
        return f.read()


def _cached_text(name: str, ttl: float, fetch) -> str:
    path = CACHE / name
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_text(encoding="utf-8")
    try:
        text = fetch()
    except Exception:
        if path.exists():
            return path.read_text(encoding="utf-8")   # stale beats nothing
        raise
    CACHE.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def fred(series: str) -> pd.Series:
    text = _cached_text(f"fred_{series}.csv", FRED_TTL, lambda: _download(
        f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", "Kash/1.0 (personal stock research app)").decode("utf-8"))
    d = pd.read_csv(io.StringIO(text), na_values=["."])
    s = pd.Series(pd.to_numeric(d.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(d.iloc[:, 0])).dropna()
    return s.sort_index()


def parse_multpl(html: str) -> pd.Series:
    t = pd.read_html(io.StringIO(html))[0]
    values = t.iloc[:, 1].astype(str).map(lambda x: re.findall(r"-?\d[\d,]*\.?\d*", x)[-1].replace(",", ""))
    return pd.Series(values.astype(float).values, index=pd.to_datetime(t.iloc[:, 0])).sort_index()


def multpl(slug: str) -> pd.Series:
    text = _cached_text(f"multpl_{slug}.html", MULTPL_TTL, lambda: _download(
        f"https://www.multpl.com/{slug}/table/by-month", "Mozilla/5.0 (Kash personal research)").decode("utf-8"))
    return parse_multpl(text)


# ------------------------------------------------------------------ statistics

def percentile(s: pd.Series, value: float | None = None) -> float:
    """Share of history at or below the value (0-1)."""
    v = s.iloc[-1] if value is None else value
    return float((s <= v).mean())


def log_trend_z(s: pd.Series) -> tuple[pd.Series, float, float]:
    """Fit a straight line to log(series) over time -> (trend series, std of residuals, latest z-score)."""
    x = (s.index - s.index[0]).days.to_numpy(dtype=float)
    y = np.log(s.to_numpy(dtype=float))
    b, a = np.polyfit(x, y, 1)
    fit = a + b * x
    sd = float((y - fit).std())
    return pd.Series(np.exp(fit), index=s.index), sd, float((y[-1] - fit[-1]) / sd)


def monthly(s: pd.Series) -> pd.Series:
    return s.resample("ME").last().dropna()


def points(s: pd.Series, digits: int = 3) -> list:
    return [[int(ts.timestamp() * 1000), round(float(v), digits)] for ts, v in s.items()]


def valuation_label(z: float) -> tuple[str, str]:
    if z >= 1.5:
        return "Strongly overvalued", "bad"
    if z >= 0.75:
        return "Overvalued", "warn"
    if z <= -0.75:
        return "Undervalued", "good"
    return "Fairly valued", "neutral"


def pct_z(p: float) -> float:
    """Map a historical percentile to a z-like score, so percentile-based models share one scale."""
    p = min(max(p, 0.001), 0.999)
    # logistic approximation of the normal quantile (accurate enough for 4 buckets)
    return float(np.log(p / (1 - p)) / 1.702)


def card(key, group, title, value, fmt, label, tone, text, what, series=None, **extra) -> dict:
    return {"key": key, "group": group, "title": title, "value": value, "fmt": fmt, "label": label, "tone": tone,
            "text": text, "what": what, "series": series or [], **extra}


# ------------------------------------------------------------------ valuation models

def buffett() -> dict:
    mv = fred("NCBEILQ027S") / 1000            # market value of US corporate equities, $M -> $B (quarterly, Fed Z.1)
    gdp = fred("GDP")                          # $B, quarterly
    # both are dated at the start of the quarter; the equity value is measured at its end
    ratio = (mv / gdp.reindex(mv.index).ffill()).dropna() * 100
    ratio.index = ratio.index + pd.offsets.QuarterEnd(0)
    ratio = ratio[ratio.index >= "1951-01-01"]
    # bring it up to today: move the latest quarter by the Wilshire 5000 and use the newest GDP
    w = assets.close("^W5000")
    if w is not None and not w.empty and w.index[-1] > ratio.index[-1]:
        base = float(w.asof(ratio.index[-1]))
        now = ratio.iloc[-1] * float(w.iloc[-1]) / base * float(gdp.reindex([mv.index[-1]]).ffill().iloc[0]) / float(gdp.iloc[-1])
        ratio = pd.concat([ratio, pd.Series([now], index=[w.index[-1]])])
    trend, sd, z = log_trend_z(ratio)
    label, tone = valuation_label(z)
    avg = float(ratio.mean())
    return card("buffett", "valuation", "Buffett Indicator", float(ratio.iloc[-1]), "pct0", label, tone,
                f"US stocks are worth about {ratio.iloc[-1]:.0f}% of the economy's yearly output. The long-run average is "
                f"{avg:.0f}%, and today is {z:+.1f} standard deviations from the long-run trend.",
                "Total value of US stocks divided by GDP. Warren Buffett called it probably the best single measure of where valuations stand.",
                points(ratio, 1), trend=points(trend, 1), bands=round(sd, 4), average=round(avg, 1), percentile=percentile(ratio), z=z,
                source="Federal Reserve Z.1 and BEA via FRED; latest value estimated with the Wilshire 5000")


def cape() -> dict:
    s = multpl("shiller-pe")
    p, avg = percentile(s), float(s.mean())
    z = pct_z(p)
    label, tone = valuation_label(z)
    return card("cape", "valuation", "Shiller P/E (CAPE)", float(s.iloc[-1]), "x", label, tone,
                f"The S&P 500 costs {s.iloc[-1]:.1f}× its average inflation-adjusted earnings of the last 10 years. "
                f"The average since 1871 is {avg:.1f}×; it has only been higher {100 * (1 - p):.0f}% of the time.",
                "Price divided by 10 years of inflation-adjusted earnings, which smooths out booms and busts.",
                points(s, 2), average=round(avg, 2), percentile=p, z=z, source="Robert Shiller's data via multpl.com")


def pe() -> dict:
    s = multpl("s-p-500-pe-ratio")
    p, avg = percentile(s), float(s.mean())
    z = pct_z(p)
    label, tone = valuation_label(z)
    return card("pe", "valuation", "S&P 500 P/E", float(s.iloc[-1]), "x", label, tone,
                f"The S&P 500 trades at {s.iloc[-1]:.1f}× the last 12 months of earnings, against a long-run average of {avg:.1f}×.",
                "The market's price compared with one year of company earnings.",
                points(s, 2), average=round(avg, 2), percentile=p, z=z, source="multpl.com")


def mean_reversion() -> dict:
    s = multpl("inflation-adjusted-s-p-500")
    trend, sd, z = log_trend_z(s)
    label, tone = valuation_label(z)
    gap = s.iloc[-1] / trend.iloc[-1] - 1
    return card("reversion", "valuation", "Mean reversion", float(gap * 100), "pctSigned", label, tone,
                f"After inflation, the S&P 500 sits {abs(gap) * 100:.0f}% {'above' if gap >= 0 else 'below'} its 150-year growth trend ({z:+.1f} standard deviations).",
                "Stocks have grown at a fairly steady real rate for 150 years. Far above that line, future returns have tended to be weaker.",
                points(s, 1), trend=points(trend, 1), bands=round(sd, 4), z=z, percentile=percentile((np.log(s) - np.log(trend))), log=True,
                source="Robert Shiller's data via multpl.com")


def earnings_yield_gap() -> dict:
    ey = monthly(multpl("s-p-500-earnings-yield"))
    ten = monthly(fred("DGS10"))
    gap = (ey - ten.reindex(ey.index)).dropna()
    # recent history is the fair comparison: the gap has been structurally different across rate regimes
    p = percentile(gap)
    v = float(gap.iloc[-1])
    z = -pct_z(p)
    label, tone = valuation_label(z)
    return card("eygap", "valuation", "Earnings yield vs. bonds", v, "ptsSigned", label, tone,
                f"Stocks' earnings yield is {ey.iloc[-1]:.2f}% and the 10-year Treasury pays {ten.iloc[-1]:.2f}%. "
                + ("Stocks yield less than safe bonds, so investors get no extra income for taking risk." if v < 0
                   else f"Stocks yield {v:.2f} points more than safe bonds."),
                "Earnings per dollar of stock price minus the risk-free 10-year Treasury yield. Lower means stocks are pricier relative to bonds.",
                points(gap, 2), percentile=p, z=z, zero=True, source="multpl.com and FRED")


# ------------------------------------------------------------------ recession and sentiment

def yield_curve() -> dict:
    s = fred("T10Y3M")
    v = float(s.iloc[-1])
    inverted_days = int((s.tail(365) < 0).sum())
    if v < 0:
        label, tone, text = "Inverted", "bad", f"Short-term rates are above long-term rates by {-v:.2f} points. An inverted curve has preceded most US recessions."
    elif inverted_days > 30:
        label, tone, text = "Recently inverted", "warn", f"The curve is positive at {v:.2f}, but was inverted {inverted_days} days in the past year. Recessions have often started after it turns positive again."
    else:
        label, tone, text = "Normal", "good", f"Long-term rates are {v:.2f} points above short-term rates, the normal shape."
    return card("curve", "recession", "Yield curve (10Y − 3M)", v, "ptsSigned", label, tone, text,
                "The gap between 10-year and 3-month Treasury yields. When it goes negative, recessions have often followed within about 18 months.",
                points(monthly(s), 2), zero=True, source="FRED")


def sahm() -> dict:
    s = fred("SAHMREALTIME")
    v = float(s.iloc[-1])
    label, tone = ("Triggered", "bad") if v >= 0.5 else ("Watch", "warn") if v >= 0.3 else ("Not triggered", "good")
    return card("sahm", "recession", "Sahm Rule", v, "pts", label, tone,
                f"The indicator is {v:.2f}; a reading of 0.50 or more has signalled the early part of a recession.",
                "Rises when the 3-month average unemployment rate climbs 0.5 points above its low of the past year.",
                points(s, 2), threshold=0.5, source="FRED")


def unemployment() -> dict:
    s = fred("UNRATE")
    chg = float(s.iloc[-1] - s.iloc[-13])
    label, tone = ("Rising", "warn") if chg >= 0.3 else ("Falling", "good") if chg <= -0.3 else ("Stable", "neutral")
    return card("unrate", "recession", "Unemployment", float(s.iloc[-1]), "pct1", label, tone,
                f"Unemployment is {s.iloc[-1]:.1f}%, {abs(chg):.1f} points {'higher' if chg >= 0 else 'lower'} than a year ago.",
                "Share of the labor force looking for work.", points(s, 1), source="FRED")


def vix() -> dict:
    s = assets.close("^VIX")
    if s is None or s.empty:
        raise ValueError("no VIX data")
    v = float(s.iloc[-1])
    label, tone = ("Panic", "bad") if v >= 35 else ("Fearful", "warn") if v >= 25 else ("Complacent", "warn") if v < 14 else ("Neutral", "neutral")
    return card("vix", "sentiment", "VIX fear index", v, "num1", label, tone,
                f"Options traders are pricing in about {v / np.sqrt(12):.1f}% moves over the next month. Long-run average is {s.mean():.1f}.",
                "Expected S&P 500 volatility over the next 30 days. High means fear; very low can mean complacency.",
                points(monthly(s), 1), average=round(float(s.mean()), 1), percentile=percentile(s), source="Cboe via Yahoo Finance")


def junk_spread() -> dict:
    s = fred("BAMLH0A0HYM2")
    v = float(s.iloc[-1])
    label, tone = ("Stress", "bad") if v >= 8 else ("Cautious", "warn") if v >= 5 else ("Optimistic", "warn") if v < 3.5 else ("Neutral", "neutral")
    return card("hy", "sentiment", "Junk bond spread", v, "pts", label, tone,
                f"Risky company bonds pay {v:.2f} points more than Treasuries. Very low spreads mean investors aren't demanding much pay for risk; a spike means fear.",
                "The extra yield on below-investment-grade bonds versus Treasuries.", points(monthly(s), 2), source="ICE BofA via FRED")


def consumer() -> dict:
    s = fred("UMCSENT")
    p = percentile(s)
    label, tone = ("Very pessimistic", "good") if p <= 0.1 else ("Pessimistic", "neutral") if p <= 0.35 else ("Optimistic", "warn") if p >= 0.75 else ("Neutral", "neutral")
    return card("umich", "sentiment", "Consumer sentiment", float(s.iloc[-1]), "num1", label, tone,
                f"The University of Michigan index is {s.iloc[-1]:.1f}, {"among the lowest readings" if p <= 0.02 else f"higher than only {p * 100:.0f}% of readings"} since 1952. Extreme gloom has often been a contrarian buy signal.",
                "A monthly survey of how Americans feel about their finances and the economy.", points(s, 1), percentile=p, source="University of Michigan via FRED")


# ------------------------------------------------------------------ economy snapshot

def _yoy(s: pd.Series, n: int) -> pd.Series:
    return (s / s.shift(n) - 1).dropna() * 100


def economy() -> list[dict]:
    out = []
    specs = [
        ("cpi", "Inflation (CPI, yearly)", lambda: _yoy(fred("CPIAUCSL"), 12), "pct1"),
        ("gdp", "Real GDP growth (yearly)", lambda: _yoy(fred("GDPC1"), 4), "pct1"),
        ("fed", "Fed funds rate", lambda: fred("FEDFUNDS"), "pct2"),
        ("t10", "10-year Treasury", lambda: monthly(fred("DGS10")), "pct2"),
        ("t2", "2-year Treasury", lambda: monthly(fred("DGS2")), "pct2"),
        ("mtg", "30-year mortgage", lambda: monthly(fred("MORTGAGE30US")), "pct2"),
        ("m2", "Money supply (M2, yearly)", lambda: _yoy(fred("M2SL"), 12), "pct1"),
    ]
    for key, title, get, fmt in specs:
        try:
            s = get()
            out.append({"key": key, "title": title, "value": float(s.iloc[-1]), "prev": float(s.iloc[-13]) if len(s) > 13 else None,
                        "fmt": fmt, "date": s.index[-1].strftime("%b %Y"), "series": points(s.tail(240), 2)})
        except Exception:  # noqa: BLE001
            continue
    return out


def recessions() -> list:
    s = fred("USREC")
    s = s[s.index >= "1871-01-01"]
    spans, start = [], None
    for ts, v in s.items():
        if v == 1 and start is None:
            start = ts
        elif v == 0 and start is not None:
            spans.append([int(start.timestamp() * 1000), int(ts.timestamp() * 1000)])
            start = None
    if start is not None:
        spans.append([int(start.timestamp() * 1000), int(s.index[-1].timestamp() * 1000)])
    return spans


def treasury_curve() -> dict | None:
    """Treasury yields across maturities: today against 1 and 3 years ago."""
    terms = [("1M", "DGS1MO"), ("3M", "DGS3MO"), ("6M", "DGS6MO"), ("1Y", "DGS1"), ("2Y", "DGS2"), ("5Y", "DGS5"), ("10Y", "DGS10"), ("20Y", "DGS20"), ("30Y", "DGS30")]
    data = {t: fred(s) for t, s in terms}
    latest = min(s.index[-1] for s in data.values())

    def at(when):
        return [round(float(data[t].asof(when)), 2) for t, _ in terms]
    return {"labels": [t for t, _ in terms], "now": at(latest), "year": at(latest - pd.DateOffset(years=1)),
            "three": at(latest - pd.DateOffset(years=3)), "date": latest.strftime("%b %d, %Y")}


BUILDERS = [buffett, cape, pe, mean_reversion, earnings_yield_gap, yield_curve, sahm, unemployment, vix, junk_spread, consumer]


def verdict(cards: list[dict]) -> dict | None:
    models = [c for c in cards if c["group"] == "valuation" and c.get("z") is not None]
    if not models:
        return None
    score = float(np.mean([c["z"] for c in models]))
    over = sum(1 for c in models if c["tone"] in ("bad", "warn"))
    under = sum(1 for c in models if c["tone"] == "good")
    label, tone = valuation_label(score)
    return {"label": label, "tone": tone, "score": round(score, 2), "over": over, "under": under, "total": len(models)}


def overview() -> dict:
    global _overview
    with _lock:
        if _overview and time.time() - _overview[0] < OVERVIEW_TTL:
            return _overview[1]
    cards, failed = [], []
    for build in BUILDERS:
        try:
            cards.append(build())
        except Exception:  # noqa: BLE001  (one bad source shouldn't hide the rest)
            failed.append(build.__name__)
    try:
        spans = recessions()
    except Exception:  # noqa: BLE001
        spans = []
    try:
        curve = treasury_curve()
    except Exception:  # noqa: BLE001
        curve = None
    data = {"cards": cards, "verdict": verdict(cards), "economy": economy(), "curve": curve, "recessions": spans, "failed": failed, "asOf": int(time.time())}
    with _lock:
        _overview = (time.time(), data)
    return data
