"""Offline tests: merged-ticker detection, SEC cash/debt tag coverage and the ROIC definition."""
from datetime import date, timedelta

import pandas as pd
import pytest

import app
import rows
import sec


def _frame(end, revenue, shares):
    return pd.DataFrame({pd.Timestamp(end): [revenue, shares]}, index=["Total Revenue", "Diluted Average Shares"])


@pytest.mark.parametrize("sec_vals,yahoo_vals,expected", [
    ((1239e6, 128e6), (719e6, 56.5e6), True),     # SUPN, Nov 2026: SEC = Indivior, Yahoo = old Supernus
    ((100e9, 2.8e9), (180e9, 2.8e9), False),      # a bank: revenue defined differently, same shares
    ((50e9, 12e9), (50e9, 6e9), False),           # shares differ (share classes), same revenue
    ((50e9, 12e9), (51e9, 12.1e9), False),        # ordinary small differences
])
def test_source_conflict(sec_vals, yahoo_vals, expected):
    s = _frame("2025-12-31", *sec_vals)
    y = _frame("2026-01-03", *yahoo_vals)         # dates within 20 days are the same fiscal year
    assert app.source_conflict(s, y) is expected


def test_source_conflict_needs_an_overlapping_year():
    assert app.source_conflict(_frame("2025-12-31", 10, 10), _frame("2023-12-31", 99, 99)) is False
    assert app.source_conflict(None, _frame("2023-12-31", 99, 99)) is False


def _gaap(extra):
    def annual(c, val):
        return {c: {"units": {"USD": [{"val": val, "end": "2022-12-31", "filed": "2023-02-01", "form": "10-K"}]}}}
    g = {"Assets": annual("Assets", 1000)["Assets"], "NetIncomeLoss": {"units": {"USD": [
        {"val": 10, "start": "2022-01-01", "end": "2022-12-31", "filed": "2023-02-01", "form": "10-K"}]}}}
    for c, v in extra.items():
        g.update(annual(c, v))
    return g


@pytest.mark.parametrize("extra,cash,debt", [
    # Supernus-style: current convertible notes and securities under the newer AFS tag
    ({"CashAndCashEquivalentsAtCarryingValue": 75, "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent": 180,
      "ConvertibleNotesPayableCurrent": 402}, 255, 402),
    # noncurrent convertible, tagged on its own
    ({"CashAndCashEquivalentsAtCarryingValue": 50, "ConvertibleLongTermNotesPayable": 379}, 50, 379),
    # ordinary debt tag wins; a separately tagged convertible isn't double counted
    ({"CashAndCashEquivalentsAtCarryingValue": 50, "LongTermDebt": 500, "ConvertibleLongTermNotesPayable": 200}, 50, 500),
    # only the combined convertible tag
    ({"CashAndCashEquivalentsAtCarryingValue": 50, "ConvertibleNotesPayable": 300}, 50, 300),
])
def test_sec_counts_securities_and_convertible_notes(monkeypatch, extra, cash, debt):
    monkeypatch.setattr(sec, "cik_for", lambda t: 1)
    monkeypatch.setattr(sec, "_fetch", lambda *a: {"facts": {"us-gaap": _gaap(extra)}})
    _, bal, _ = sec.annual_frames("X")
    col = pd.Timestamp("2022-12-31")
    assert bal.at["Cash Cash Equivalents And Short Term Investments", col] == cash
    assert bal.at["Total Debt", col] == debt


def _ctx(op, debt, equity, cash, sti_total, assets):
    bal = {"Total Debt": debt, "Stockholders Equity": equity, "Cash And Cash Equivalents": cash,
           "Cash Cash Equivalents And Short Term Investments": sti_total, "Total Assets": assets}

    def lookup(names):
        for n in ([names] if isinstance(names, str) else names):
            if bal.get(n) is not None:
                return bal[n]
        return None
    inc = lambda names: None if names in ("Pretax Income", "Tax Provision") else op
    return {"inc": inc, "bal": lookup, "cf": lambda n: None, "annualize": 1, "price": None}


def test_roic_subtracts_cash_and_securities():
    # after-tax op income 79 (21% default) on 500 debt + 600 equity - 300 cash & securities = 800
    assert rows._roic(_ctx(100, 500, 600, 50, 300, 2000)) == pytest.approx(79 / 800)


def test_roic_hidden_when_invested_capital_is_tiny():
    # Indivior-style: debt 319, equity -98, cash 195 -> 26 invested on 1,201 of assets (2%): would read as 800%+
    assert rows._roic(_ctx(262, 319, -98, 195, 195, 1201)) is None


def test_roic_kept_for_buyback_companies_with_negative_equity():
    # McDonald's-style: negative equity, but debt funds real assets
    assert rows._roic(_ctx(100, 400, -40, 10, 10, 550)) == pytest.approx(79 / 350)


def _names(monkeypatch, former):
    monkeypatch.setattr(sec, "cik_for", lambda t: 1)
    monkeypatch.setattr(sec, "_fetch", lambda *a: {"name": "New Co", "formerNames": former})


def test_filer_info_reports_a_recent_rename(monkeypatch):
    recent = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00.000Z"
    _names(monkeypatch, [{"name": "Older Co", "to": "2015-01-01T00:00:00.000Z"}, {"name": "Old Co", "to": recent}])
    assert sec.filer_info("X") == {"name": "New Co", "formerName": "Old Co", "renamed": recent[:10]}


def test_filer_info_ignores_an_old_rename(monkeypatch):
    _names(monkeypatch, [{"name": "Old Co", "to": "2015-01-01T00:00:00.000Z"}])
    assert sec.filer_info("X") == {"name": "New Co"}
