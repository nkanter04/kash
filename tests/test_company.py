"""Offline tests for company.py (profile, ownership, dividends, fair value inputs) and SEC filings."""
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest

import company
import sec


def _divs(pairs):
    idx = pd.DatetimeIndex([d for d, _ in pairs]).tz_localize("America/New_York")
    return pd.Series([v for _, v in pairs], index=idx)


def test_dividend_streak_needs_consecutive_years():
    # Apple-style: paid in the 1990s, paused, restarted in 2012 and raised every year since
    pays = [("1994-05-01", 0.03), ("1995-05-01", 0.03)] + [(f"{y}-05-01", 0.1 + 0.01 * (y - 2012)) for y in range(2012, 2026)]
    d = company.dividends(SimpleNamespace(dividends=_divs(pays)), {"dividendRate": 0.24, "currentPrice": 10})
    assert d["streak"] == 13               # 2013..2025 vs. the year before; the 1995 -> 2012 jump doesn't count
    assert d["yield"] == pytest.approx(0.024)


def test_dividend_partial_year_and_growth():
    y = datetime.now().year
    pays = [(f"{y - 2}-03-01", 1.0), (f"{y - 1}-03-01", 1.1), (f"{y}-03-01", 0.6)]
    d = company.dividends(SimpleNamespace(dividends=_divs(pays)), {"dividendRate": 1.2})
    assert [r["partial"] for r in d["years"]] == [False, False, True]
    assert d["years"][1]["growth"] == pytest.approx(0.10)
    assert "growth" not in d["years"][2]   # no growth figure for the unfinished year


def test_fund_dividend_rate_falls_back_to_last_12_months():
    now = pd.Timestamp.now().normalize()
    pays = [(str((now - pd.Timedelta(days=d)).date()), 1.5) for d in (20, 110, 200, 290, 380)]
    d = company.dividends(SimpleNamespace(dividends=_divs(pays)), {"currentPrice": 600})
    assert d["rate"] == pytest.approx(6.0)  # 4 payments in the last year, not the 5th
    assert d["yield"] == pytest.approx(0.01)


def test_no_dividends():
    assert company.dividends(SimpleNamespace(dividends=pd.Series(dtype=float)), {}) is None


@pytest.mark.parametrize("text,kind", [("Sale at price 339.62 per share.", "sell"),
                                       ("Purchase at price 12.00 per share.", "buy"),
                                       ("Stock Award(s) at price 0.00 per share.", "other"), (None, "other")])
def test_insider_trade_kinds(text, kind):
    assert company._kind(text) == kind


def test_fair_value_inputs_skip_fcf_when_currencies_differ():
    us = {"trailingEps": 8.0, "freeCashflow": 100e9, "sharesOutstanding": 10e9, "currency": "USD", "financialCurrency": "USD"}
    assert company.fair_value_inputs(us, {"nextYearGrowth": 0.1}) == {
        "eps": 8.0, "forwardEps": None, "fcfPerShare": 10.0, "analystGrowth": 0.1}
    adr = {**us, "financialCurrency": "TWD"}   # e.g. TSM: TWD cash flow, USD price, 1 ADR = 5 shares
    assert company.fair_value_inputs(adr, None)["fcfPerShare"] is None


def test_recent_filings_keeps_key_forms_with_links(monkeypatch):
    sub = {"filings": {"recent": {
        "form": ["4", "10-Q", "8-K", "SC 13G", "10-K"],
        "filingDate": ["2026-11-01", "2026-10-30", "2026-10-29", "2026-10-01", "2026-09-30"],
        "accessionNumber": ["0001-26-1", "0000320193-26-000100", "0000320193-26-000099", "x", "0000320193-26-000090"],
        "primaryDocument": ["f4.xml", "aapl-20260927.htm", "ex99.htm", "a.htm", "aapl-10k.htm"],
        "primaryDocDescription": ["", "10-Q", "8-K", "", "10-K"]}}}
    monkeypatch.setattr(sec, "cik_for", lambda t: 320193)
    monkeypatch.setattr(sec, "_fetch", lambda url, cache, ttl: sub)
    got = sec.recent_filings("AAPL", limit=2)
    assert [f["form"] for f in got] == ["10-Q", "8-K"]   # Form 4 and 13G skipped; limit respected
    assert got[0]["url"] == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000100/aapl-20260927.htm"
