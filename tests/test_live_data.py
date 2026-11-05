"""Accuracy checks against live Yahoo Finance + SEC EDGAR data.

Slow (downloads data) and need internet plus an SEC contact email in config.json.
Run with:  pytest -m network
"""
import pandas as pd
import pytest
import yfinance as yf

import app
import sec

pytestmark = pytest.mark.network

needs_sec = pytest.mark.skipif(not sec._user_agent(), reason="no SEC contact email in config.json")


@pytest.fixture(scope="module")
def stocks():
    cache = {}

    def get(ticker):
        if ticker not in cache:
            cache[ticker] = app.load(ticker)
        return cache[ticker]
    return get


def by_year(fin, table, key):
    """{'FY 2019': value, ...} for one row of an annual table."""
    cols = fin["columns"][table]
    row = app.series_of(fin[table], key) or []
    return {c["label"]: v for c, v in zip(cols, row) if v is not None and c["label"].startswith("FY")}


@needs_sec
@pytest.mark.parametrize("ticker", ["AAPL", "ACN", "KO", "NVDA"])
def test_sec_gives_long_history(stocks, ticker):
    fin = stocks(ticker)["financials"]["annual"]
    assert "SEC" in fin["source"]
    assert len(by_year(fin, "income", "revenue")) >= 12


@needs_sec
@pytest.mark.parametrize("ticker", ["AAPL", "NVDA", "ACN"])
def test_eps_times_shares_matches_net_income(stocks, ticker):
    """Split-invariant check: if shares and EPS were on different split bases, this breaks by 2-40x.

    10% slack because some companies' diluted EPS legitimately counts extra income (e.g. Accenture
    before ~2016 included noncontrolling holders of exchangeable shares: about +6%).
    """
    fin = stocks(ticker)["financials"]["annual"]
    eps, shares, ni = (by_year(fin, "income", k) for k in ("eps", "shares", "net_income"))
    years = [y for y in eps if y in shares and y in ni and ni[y] > 0]
    assert len(years) >= 10
    for y in years:
        assert eps[y] * shares[y] / ni[y] == pytest.approx(1, abs=0.10), y


@needs_sec
def test_aapl_historical_pe_is_plausible(stocks):
    """Regression test: a split bug once made AAPL's FY2018-19 P/E look like ~70x."""
    pe = by_year(stocks("AAPL")["financials"]["annual"], "ratios", "pe")
    assert 15 <= pe["FY 2019"] <= 21
    assert 13 <= pe["FY 2018"] <= 20


@needs_sec
@pytest.mark.parametrize("ticker", ["ACN", "AAPL"])
def test_sec_revenue_matches_yahoo(stocks, ticker):
    ours = by_year(stocks(ticker)["financials"]["annual"], "income", "revenue")
    yahoo = yf.Ticker(ticker).income_stmt
    matched = 0
    for col in yahoo.columns:
        v = app.get(yahoo, "Total Revenue", col)
        label = f"FY {col.year}"  # ACN (Aug) and AAPL (Sep) fiscal years end in the labelled year
        if v and label in ours:
            assert ours[label] == pytest.approx(v, rel=0.005), label
            matched += 1
    assert matched >= 3


@pytest.mark.parametrize("ticker", ["AAPL", "TSM"])
def test_page_data_is_complete(stocks, ticker):
    d = stocks(ticker)
    assert d["prices"] and d["info"]["currentPrice"]
    assert len(d["scorecard"]) == 8
    dy = d["info"]["dividendYieldCalc"]
    assert dy is None or 0 <= dy < 0.15   # once showed 33% from Yahoo's unit mix-up


def test_foreign_filer_falls_back_to_yahoo(stocks):
    fin = stocks("TSM")["financials"]["annual"]
    assert fin["source"] == "Yahoo Finance"
    assert len(by_year(fin, "income", "revenue")) >= 3


@pytest.mark.parametrize("rng,min_points", [("1D", 50), ("1W", 200), ("1M", 150)])
def test_intraday_prices_are_dense(rng, min_points):
    d = app.intraday("AAPL", rng)
    assert len(d["points"]) >= min_points
    times = [t for t, _ in d["points"]]
    assert times == sorted(times)
    if rng == "1D":
        assert d["baseIsPrevClose"]           # 1D change is measured from the prior session's close
        days = {pd.Timestamp(t, unit="ms").tz_localize("UTC").tz_convert(d["tz"]).date() for t in times}
        assert len(days) == 1                 # only the latest session
