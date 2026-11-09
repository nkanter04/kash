"""Offline tests for the macro, assets, quality-score and screener logic."""
import numpy as np
import pandas as pd
import pytest

import assets
import macro
import market
import scores


def _series(values, start="2000-01-31", freq="ME"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq), dtype=float)


def test_parse_multpl_handles_odd_characters_and_commas():
    html = "<table><tr><th>Date</th><th>Value</th></tr><tr><td>Nov 6, 2026</td><td>â€  25.14</td></tr><tr><td>Oct 1, 2026</td><td>1,234.5%</td></tr></table>"
    s = macro.parse_multpl(html)
    assert list(s.values) == [1234.5, 25.14]              # oldest first
    assert s.index[-1] == pd.Timestamp("2026-11-06")


def test_percentile_is_share_of_history_at_or_below():
    assert macro.percentile(_series([1, 2, 3, 4])) == 1.0
    assert macro.percentile(_series([4, 3, 2, 1])) == 0.25


def test_log_trend_z_flags_a_value_far_above_trend():
    x = np.exp(np.linspace(0, 3, 240)) * (1 + 0.05 * np.sin(np.arange(240)))
    x[-1] *= 2
    trend, sd, z = macro.log_trend_z(_series(x))
    assert z > 2 and sd > 0 and len(trend) == 240


@pytest.mark.parametrize("z,label,tone", [(2, "Strongly overvalued", "bad"), (1, "Overvalued", "warn"), (0, "Fairly valued", "neutral"), (-1, "Undervalued", "good")])
def test_valuation_labels(z, label, tone):
    assert macro.valuation_label(z) == (label, tone)


def test_verdict_averages_only_valuation_models():
    cards = [{"group": "valuation", "z": 2.0, "tone": "bad"}, {"group": "valuation", "z": 0.0, "tone": "neutral"}, {"group": "sentiment", "z": 9, "tone": "bad"}]
    v = macro.verdict(cards)
    assert v["score"] == 1.0 and v["over"] == 1 and v["total"] == 2


def _daily(values, end="2026-11-06"):
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)), dtype=float)


def test_asset_row_returns():
    s = _daily(np.linspace(50, 100, 700))
    r = assets.row("X", "Thing", s)
    assert r["price"] == 100 and r["fromAth"] == 0
    assert r["d1"] == pytest.approx(100 / s.iloc[-2] - 1)
    assert r["y5"] is None                                # not enough history
    assert r["ytd"] > 0 and len(r["spark"]) <= 90


def test_ratio_fills_weekends_for_assets_that_only_trade_weekdays(monkeypatch):
    idx = pd.date_range("2026-11-02", periods=7, freq="D")
    d = pd.DataFrame({"BTC-USD": [100.0] * 7, "GC=F": [50, 50, 50, 50, 50, np.nan, np.nan]}, index=idx)
    monkeypatch.setattr(assets, "frame", lambda: d)
    s = assets.ratio_series("btc-gold")
    assert len(s) == 7 and (s == 2).all()
    assert assets.ratio_series("nope") is None


def _fin(**rows):
    n = len(next(iter(rows.values()))[1])
    cols = [{"label": f"FY {2026 - i}", "date": f"{2026 - i}-12-31"} for i in range(n)]
    tables = {"income": [], "balance": [], "cashflow": [], "ratios": []}
    for key, (table, vals) in rows.items():
        tables[table].append({"key": key, "values": vals})
    return {"columns": {t: cols for t in tables}, **tables}


GOOD = dict(net_income=("income", [120, 100]), total_assets=("balance", [1000, 1000]), ocf=("cashflow", [150, 110]),
            lt_debt=("balance", [100, 150]), current_assets=("balance", [300, 250]), current_liab=("balance", [100, 100]),
            shares=("income", [90, 100]), gross_margin=("income", [0.5, 0.4]), revenue=("income", [800, 700]))


def test_piotroski_perfect_score():
    p = scores.piotroski(_fin(**GOOD))
    assert p["score"] == 9 and p["label"] == "Strong"


def test_piotroski_counts_failures():
    bad = {**GOOD, "net_income": ("income", [-10, 100]), "shares": ("income", [130, 100])}
    p = scores.piotroski(_fin(**bad))
    assert p["raw"] < 9
    assert not next(t for t in p["tests"] if t["name"] == "Didn't issue new shares")["pass"]


def test_piotroski_needs_enough_data():
    assert scores.piotroski(_fin(net_income=("income", [1, 2]))) is None


def test_altman_zones_and_excludes_financials():
    fin = _fin(total_assets=("balance", [1000]), total_liab=("balance", [400]), working_capital=("balance", [200]), retained=("balance", [300]),
               op_income=("income", [150]), revenue=("income", [1000]), mcap=("ratios", [2000]))
    a = scores.altman(fin, "Technology")
    assert a["score"] == pytest.approx(1.2 * 0.2 + 1.4 * 0.3 + 3.3 * 0.15 + 0.6 * 5 + 1.0, abs=0.01) and a["label"] == "Safe"
    assert scores.altman(fin, "Financial Services") is None


def test_total_returns_against_benchmark():
    s = _daily(np.linspace(100, 200, 1400))
    r = scores.total_returns(s, _daily(np.linspace(100, 150, 1400)))
    one = r[0]
    assert one["period"] == "1Y" and one["value10k"] > 10000 and one["benchTotal"] < one["total"]
    assert [x["period"] for x in r] == ["1Y", "3Y", "5Y"]    # only periods the history covers


def test_quote_units_are_normalized():
    q = market._quote({"marketCap": 5e9, "regularMarketChangePercent": -1.5, "fiftyTwoWeekChangePercent": 27.0,
                       "fiftyDayAverageChangePercent": 0.02, "averageAnalystRating": "2.3 - Buy"})
    assert q["ch1y"] == pytest.approx(0.27) and q["ma50"] == 0.02 and q["rating"] == 2.3
    assert market._row("X", q, None)["change"] == pytest.approx(-0.015)


def test_screener_helpers_group_earnings_and_peers(monkeypatch):
    now = pd.Timestamp.now(tz="UTC").timestamp()
    uni = {
        "AAA": {"cap": 5e9, "change": 1.0, "earnings": now + 86400, "name": "A"},
        "BBB": {"cap": 9e9, "change": -1.0, "earnings": now + 86400, "name": "B"},
        "CCC": {"cap": 1e9, "change": 0.0, "earnings": now + 86400, "name": "C"},          # too small
        "DDD": {"cap": 9e9, "change": 0.0, "earnings": now + 90 * 86400, "name": "D"},     # too far out
    }
    mem = {s: {"sector": "Tech", "industry": "Chips", "indexes": ["sp500"]} for s in ("AAA", "BBB", "CCC", "DDD")}
    monkeypatch.setattr(market, "quotes_all", lambda: uni)
    monkeypatch.setattr(market, "_membership", lambda: mem)
    e = market.earnings_calendar()
    assert [r["symbol"] for d in e["days"] for r in d["rows"]] == ["BBB", "AAA"]
    p = market.peers("AAA", n=2)
    assert p["basis"] == "Chips" and "AAA" in [r["symbol"] for r in p["rows"]] and len(p["rows"]) == 2
    assert market.peers("ZZZ") == {"basis": None, "rows": []}
