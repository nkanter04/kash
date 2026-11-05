"""Fast offline tests for the data-handling helpers (no network)."""
import math

import pandas as pd
import pytest

import app
import sec


# ---------------------------------------------------------------- basic helpers

def test_clean_makes_values_json_safe():
    assert app.clean(None) is None
    assert app.clean(float("nan")) is None
    assert app.clean(math.inf) is None
    assert app.clean("abc") == "abc"
    assert app.clean(3) == 3.0


def test_div_and_growth_guard_against_zero_and_missing():
    assert app.div(1, 0) is None
    assert app.div(None, 2) is None
    assert app.growth(110, 100) == pytest.approx(0.10)
    # growth from a negative base is measured against its size, so improving is positive
    assert app.growth(-50, -100) == pytest.approx(0.50)
    assert app.growth(5, 0) is None


def test_ttm_sums_flows_but_not_share_counts():
    cols = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"])
    q = pd.DataFrame(
        [[10, 20, 30, 40], [100, 101, 102, 103]],
        index=["Total Revenue", "Diluted Average Shares"], columns=cols, dtype=float,
    )
    ttm = app.ttm_frame(q)
    assert ttm["Total Revenue"] == 100
    assert ttm["Diluted Average Shares"] == 100  # latest quarter, not the sum


def test_ttm_needs_four_quarters():
    q = pd.DataFrame([[1, 2, 3]], index=["Total Revenue"],
                     columns=pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31"]))
    assert app.ttm_frame(q) is None


@pytest.mark.parametrize("code,value,expected", [
    ("USD", 3.2e12, "$3.2T"),
    ("USD", -4.5e9, "$-4.5B"),
    ("TWD", 3.2e12, "3.2T TWD"),
])
def test_short_money_respects_currency(code, value, expected):
    app._CUR.code = code
    assert app._short(value) == expected


# ---------------------------------------------------------------- split handling

def _splits():
    # NVDA-style: 4-for-1 in July 2021, 10-for-1 in June 2024 (tz-aware like yfinance)
    idx = pd.DatetimeIndex(["2021-07-20", "2024-06-10"]).tz_localize("America/New_York")
    return pd.Series([4.0, 10.0], index=idx)


@pytest.mark.parametrize("filed,factor", [
    ("2020-02-20", 40.0),   # before both splits
    ("2022-03-18", 10.0),   # between them
    ("2025-02-26", 1.0),    # after both
])
def test_split_factor_counts_only_later_splits(filed, factor):
    assert sec._split_factor(_splits(), filed) == factor


def test_split_factor_without_splits():
    assert sec._split_factor(None, "2020-01-01") == 1.0
    assert sec._split_factor(pd.Series(dtype=float), "2020-01-01") == 1.0


# ---------------------------------------------------------------- SEC fact parsing

def _fact(val, end, filed, start=None, form="10-K"):
    f = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        f["start"] = start
    return f


GAAP = {
    "Revenues": {"units": {"USD": [
        _fact(100, "2020-12-31", "2021-02-01", start="2020-01-01"),
        _fact(105, "2020-12-31", "2022-02-01", start="2020-01-01"),          # restated later: wins
        _fact(30, "2020-12-31", "2021-02-01", start="2020-10-01"),           # a quarter: ignored
        _fact(999, "2021-06-30", "2021-08-01", start="2020-07-01", form="10-Q"),  # not annual
    ]}},
    "SalesRevenueNet": {"units": {"USD": [
        _fact(90, "2019-12-31", "2020-02-01", start="2019-01-01"),           # older tag name
    ]}},
    "Assets": {"units": {"USD": [
        _fact(500, "2020-12-31", "2021-02-01"),
        _fact(1, "2020-12-31", "2021-02-01", start="2020-01-01"),            # has a start: not an instant
    ]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [
        _fact(8.0, "2020-12-31", "2021-02-01", start="2020-01-01"),
    ]}},
}


def test_facts_keep_annual_values_and_latest_restatement():
    assert sec._facts_by_end(GAAP, "Revenues", "flow") == {"2020-12-31": 105}
    assert sec._facts_by_end(GAAP, "Assets", "instant") == {"2020-12-31": 500}
    assert sec._facts_by_end(GAAP, "Missing", "flow") == {}


def test_pick_falls_back_to_older_tag_per_year():
    got = sec._pick(GAAP, ["Revenues", "SalesRevenueNet"], "flow", ["2020-12-31", "2019-12-31"])
    assert got == {"2020-12-31": 105, "2019-12-31": 90}


def test_pick_converts_eps_to_todays_share_basis():
    idx = pd.DatetimeIndex(["2021-07-20"]).tz_localize("America/New_York")
    splits = pd.Series([4.0], index=idx)
    got = sec._pick(GAAP, ["EarningsPerShareDiluted"], "flow", ["2020-12-31"], splits=splits, adjust="div")
    assert got == {"2020-12-31": 2.0}


# ---------------------------------------------------------------- merging SEC with Yahoo

def test_merge_prefers_sec_and_snaps_nearby_yahoo_dates():
    sec_df = pd.DataFrame({pd.Timestamp("2024-09-28"): [100.0, None]},
                          index=["Total Revenue", "Net Income"])
    yahoo = pd.DataFrame({pd.Timestamp("2024-09-30"): [999.0, 20.0],   # 2 days off: same year
                          pd.Timestamp("2025-09-27"): [120.0, 25.0]},  # a year SEC lacks
                         index=["Total Revenue", "Net Income"])
    out = app.merge_sec(sec_df, yahoo)
    assert list(out.columns) == [pd.Timestamp("2025-09-27"), pd.Timestamp("2024-09-28")]
    assert out.at["Total Revenue", pd.Timestamp("2024-09-28")] == 100   # SEC wins
    assert out.at["Net Income", pd.Timestamp("2024-09-28")] == 20       # Yahoo fills the gap
    assert out.at["Total Revenue", pd.Timestamp("2025-09-27")] == 120


def test_merge_handles_missing_sources():
    y = pd.DataFrame({pd.Timestamp("2024-12-31"): [1.0]}, index=["Total Revenue"])
    assert app.merge_sec(None, y) is y
    assert app.merge_sec(y, None) is y


# ---------------------------------------------------------------- scorecard rules

def _fin(revenue, roic, fcf, shares, pe, net_margin=0.2):
    """A minimal annual financials dict, newest year first (ratios start with 'Current')."""
    years = len(revenue)
    row = lambda key, vals: {"key": key, "values": vals}
    return {
        "columns": {"income": [{"label": f"FY {2026 - i}"} for i in range(years)]},
        "income": [row("revenue", revenue), row("net_margin", [net_margin] * years), row("shares", shares)],
        "balance": [],
        "cashflow": [row("fcf", fcf)],
        "ratios": [row("pe", [None] + pe), row("roic", [None] + roic)],
    }


INFO = {"currentPrice": 100, "trailingPE": 20, "profitMargins": 0.2, "totalCash": 50, "totalDebt": 10,
        "targetMeanPrice": 120, "numberOfAnalystOpinions": 10, "dividendYieldCalc": 0.02}


def test_scorecard_has_eight_pillars_when_data_is_complete():
    fin = _fin(revenue=[161, 146, 133, 121, 110, 100], roic=[0.2] * 5, fcf=[10] * 6,
               shares=[95, 96, 97, 98, 99, 100], pe=[25] * 10)
    checks = app.scorecard(fin, INFO)
    assert [c["key"] for c in checks] == ["growth", "profit", "cash", "roic", "health", "value", "returns", "analysts"]
    assert all(c["pass"] for c in checks)


def test_growth_pass_and_text_agree_at_the_threshold():
    # exactly ~5%/yr: the check must pass AND the text must not say "slower"
    rev = [100 * 1.05 ** (5 - i) for i in range(6)]
    c = next(c for c in app.scorecard(_fin(rev, [0.2] * 5, [1] * 6, [1] * 6, [20] * 10), INFO) if c["key"] == "growth")
    assert c["pass"] and "slower" not in c["text"]


def test_roic_pillar_uses_five_year_average():
    fin = _fin([100] * 6, roic=[0.12, 0.08, 0.08, 0.08, 0.08], fcf=[1] * 6, shares=[1] * 6, pe=[20] * 10)
    c = next(c for c in app.scorecard(fin, INFO) if c["key"] == "roic")
    assert c["value"] == pytest.approx(0.088)
    assert not c["pass"]


def test_scorecard_skips_pillars_without_data():
    fin = _fin([100] * 6, roic=[None] * 5, fcf=[1] * 6, shares=[1] * 6, pe=[20] * 10)
    keys = [c["key"] for c in app.scorecard(fin, INFO)]
    assert "roic" not in keys and len(keys) == 7


def test_intraday_rejects_unknown_range():
    with pytest.raises(app.HTTPException) as e:
        app.intraday("AAPL", "5Y")
    assert e.value.status_code == 400


# ---------------------------------------------------------------- extended hours

def test_sessions_follow_us_market_hours():
    idx = pd.DatetimeIndex(["2026-11-04 04:00", "2026-11-04 09:28", "2026-11-04 09:30",
                            "2026-11-04 15:58", "2026-11-04 16:00", "2026-11-04 19:58"]).tz_localize("America/New_York")
    assert list(app.session_of(idx)) == ["pre", "pre", "regular", "regular", "after", "after"]
    tokyo = idx.tz_convert("Asia/Tokyo")
    assert set(app.session_of(tokyo)) == {"regular"}   # other exchanges: no US session labels


BASE_Q = {"regularMarketPrice": 100.0, "regularMarketTime": 1000}


@pytest.mark.parametrize("extra,expected", [
    ({"marketState": "REGULAR", "postMarketPrice": 101, "postMarketTime": 2000}, None),
    ({"marketState": "POST", "postMarketPrice": 101, "postMarketTime": 2000}, ("After hours", 0.01)),
    ({"marketState": "PREPRE", "postMarketPrice": 99, "postMarketTime": 2000}, ("After hours", -0.01)),
    ({"marketState": "PRE", "preMarketPrice": 102, "preMarketTime": 3000}, ("Pre-market", 0.02)),
    ({"marketState": "POST", "postMarketPrice": 101, "postMarketTime": 500}, None),   # stale: older than the close
])
def test_extended_quote(extra, expected):
    q = app.extended_quote({**BASE_Q, **extra})
    if expected is None:
        assert q is None
    else:
        assert q["label"] == expected[0]
        assert q["pct"] == pytest.approx(expected[1])


# ---------------------------------------------------------------- foreign listings / share classes

@pytest.mark.parametrize("stmt,info,ratio", [
    (25.9e9, {"impliedSharesOutstanding": 5.186e9}, 5.0),        # TSM: 1 ADR = 5 ordinary shares
    (5.07e9, {"impliedSharesOutstanding": 2.54e9}, 2.0),         # BHP: 1 ADR = 2 shares
    (1.4317e6, {"impliedSharesOutstanding": 2.1407e9, "sharesOutstanding": 1.408e9}, 0.00067),  # BRK-B vs class A
    (12.29e9, {"impliedSharesOutstanding": 12.294e9, "sharesOutstanding": 5.888e9}, 1.0),       # GOOGL: all classes
    (14.4e9, {"sharesOutstanding": 14.53e9}, 1.0),               # ordinary drift is not an ADR ratio
    (None, {"sharesOutstanding": 1e9}, 1.0),
])
def test_share_ratio(stmt, info, ratio):
    assert app.share_ratio(stmt, info) == pytest.approx(ratio)


def _fx(frm, to):
    assert (frm, to) == ("USD", "TWD")
    return pd.Series([30.0, 32.0], index=pd.DatetimeIndex(["2024-01-02", "2025-01-02"]))


def test_price_basis_converts_adr_price_to_statement_basis():
    b = app.PriceBasis({"currency": "USD", "financialCurrency": "TWD", "impliedSharesOutstanding": 5e9}, 25e9, fx_lookup=_fx)
    assert b.convert(100.0, "2024-06-30") == pytest.approx(100 * 30 / 5)    # rate in force on that date
    assert b.convert(100.0, "2020-01-01") == pytest.approx(100 * 30 / 5)    # before FX history: earliest rate
    assert b.convert(100.0) == pytest.approx(100 * 32 / 5)                  # "Current": latest rate


def test_price_basis_handles_pence_and_missing_fx():
    pence = app.PriceBasis({"currency": "GBp", "financialCurrency": "GBP"}, None, fx_lookup=lambda a, b: None)
    assert pence.convert(250.0) == pytest.approx(2.5)
    no_fx = app.PriceBasis({"currency": "USD", "financialCurrency": "DKK"}, None, fx_lookup=lambda a, b: None)
    assert no_fx.convert(100.0) is None      # no ratio beats a ratio that is off by the exchange rate
    us = app.PriceBasis({"currency": "USD", "financialCurrency": "USD"}, 1e9, fx_lookup=_fx)
    assert us.convert(100.0, "2024-06-30") == 100.0
