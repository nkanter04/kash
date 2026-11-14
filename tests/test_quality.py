"""Offline tests for the Big Investors data (funds.py) and the screener's long-history measures (fundamentals.py)."""
import pandas as pd
import pytest

import fundamentals
import funds

INFO_TABLE = b"""<?xml version="1.0"?>
<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer><ns1:titleOfClass>COM</ns1:titleOfClass><ns1:cusip>037833100</ns1:cusip>
    <ns1:value>%(v1)s</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>1000</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
  <ns1:infoTable><ns1:nameOfIssuer>APPLE INC</ns1:nameOfIssuer><ns1:titleOfClass>COM</ns1:titleOfClass><ns1:cusip>037833100</ns1:cusip>
    <ns1:value>%(v1)s</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>500</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
  <ns1:infoTable><ns1:nameOfIssuer>TESLA INC</ns1:nameOfIssuer><ns1:titleOfClass>COM</ns1:titleOfClass><ns1:cusip>88160R101</ns1:cusip>
    <ns1:value>%(v2)s</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>2000</ns1:sshPrnamt><ns1:sshPrnamtType>SH</ns1:sshPrnamtType></ns1:shrsOrPrnAmt><ns1:putCall>Put</ns1:putCall></ns1:infoTable>
  <ns1:infoTable><ns1:nameOfIssuer>SOME BOND</ns1:nameOfIssuer><ns1:titleOfClass>NOTE</ns1:titleOfClass><ns1:cusip>123456AB1</ns1:cusip>
    <ns1:value>900000</ns1:value><ns1:shrsOrPrnAmt><ns1:sshPrnamt>900000</ns1:sshPrnamt><ns1:sshPrnamtType>PRN</ns1:sshPrnamtType></ns1:shrsOrPrnAmt></ns1:infoTable>
</ns1:informationTable>"""


def _table(v1, v2, filed="2026-08-14"):
    return {(r["cusip"], r["kind"]): r for r in funds.parse_info_table(INFO_TABLE % {b"v1": v1, b"v2": v2}, filed)}


def test_info_table_sums_sub_managers_and_separates_options_and_bonds():
    rows = _table(b"200000", b"500000")
    assert set(rows) == {("037833100", ""), ("88160R101", "Put")}        # the bond is dropped
    assert rows[("037833100", "")]["shares"] == 1500 and rows[("037833100", "")]["value"] == 400000


def test_info_table_values_in_thousands_are_scaled():
    assert _table(b"200", b"500")[("037833100", "")]["value"] == 400000            # under $1 a share: reported in thousands
    assert _table(b"200", b"500", "2022-08-15")[("037833100", "")]["value"] == 400000  # before 2023 thousands was the rule


def test_ftd_file_maps_cusips_to_tickers():
    text = "SETTLEMENT DATE|CUSIP|SYMBOL|QUANTITY (FAILS)|DESCRIPTION|PRICE\n20261001|037833100|AAPL|100|APPLE INC|250.00\n20261001|084670702|BRK.B|5|BERKSHIRE|480.00\nTrailer record count 2\n"
    assert funds.parse_ftd(text) == {"037833100": "AAPL", "084670702": "BRK-B"}


@pytest.fixture
def fund_db(tmp_path, monkeypatch):
    monkeypatch.setattr(funds, "DB", tmp_path / "funds.db")
    monkeypatch.setattr(funds, "SNAPSHOT", tmp_path / "none.db")
    funds._cache.clear()
    c = funds.db(write=True)
    c.executescript(funds.SCHEMA)
    c.execute("INSERT INTO managers VALUES(1,'Test Investor','Test Firm','TEST FIRM LLC',0)")
    c.executemany("INSERT INTO filings VALUES(1,?,?,?,?,?)", [("2026-03-31", "2026-05-15", "a", 300.0, 3), ("2026-06-30", "2026-08-14", "b", 400.0, 3)])
    h = [("2026-03-31", "C1", "", "AAA", 100.0, 10), ("2026-03-31", "C2", "", "BBB", 100.0, 10), ("2026-03-31", "C3", "", "CCC", 100.0, 10),
         ("2026-06-30", "C1", "", "AAA", 200.0, 20), ("2026-06-30", "C2", "", "BBB", 100.0, 5), ("2026-06-30", "C4", "", "DDD", 100.0, 10),
         ("2026-06-30", "C4", "Put", "DDD", 999.0, 10)]
    c.executemany("INSERT INTO holdings VALUES(1,?,?,?,?,?,'COM',?,?)", [(p, cu, k, t, t + " INC", v, s) for p, cu, k, t, v, s in h])
    c.commit()
    c.close()
    yield
    funds._cache.clear()


def test_positions_show_weights_and_changes_since_last_report(fund_db):
    pos = {(p["ticker"], p["kind"]): p for p in funds.manager(1)["positions"]}
    assert pos[("AAA", "")]["act"] == "add" and pos[("AAA", "")]["chg"] == pytest.approx(1.0)
    assert pos[("BBB", "")]["act"] == "trim" and pos[("DDD", "")]["act"] == "new" and pos[("CCC", "")]["act"] == "sold"
    assert pos[("AAA", "")]["pct"] == pytest.approx(0.5)       # options are left out of the portfolio total
    assert pos[("DDD", "Put")]["pct"] is None


def test_overview_and_stock_lookup(fund_db):
    o = funds.overview()
    assert o["ready"] and o["managers"][0]["positions"] == 3 and o["managers"][0]["top"][0]["ticker"] == "AAA"
    assert [e["ticker"] for e in o["buys"]][0] == "DDD"         # a new position outranks an addition
    s = funds.for_stock("AAA")["holders"][0]
    assert s["investor"] == "Test Investor" and [h["shares"] for h in s["history"]] == [10, 20]
    assert funds.manager(999) is None


def _frames(years=11, rev_growth=1.10, shares=None):
    cols = [pd.Timestamp(f"{2025 - i}-12-31") for i in range(years)]
    rev = [1000 / rev_growth ** i for i in range(years)]
    sh = shares or [100 + i for i in range(years)]              # newest first: the count shrinks toward today
    inc = pd.DataFrame({"Total Revenue": rev, "Gross Profit": [r * 0.5 for r in rev], "Operating Income": [r * 0.25 for r in rev],
                        "Pretax Income": [r * 0.25 for r in rev], "Tax Provision": [r * 0.05 for r in rev], "Net Income": [r * 0.2 for r in rev],
                        "Diluted EPS": [r * 0.2 / s for r, s in zip(rev, sh)], "Diluted Average Shares": sh, "EBITDA": [r * 0.3 for r in rev]}, index=cols).T
    bal = pd.DataFrame({"Cash And Cash Equivalents": [100.0] * years, "Total Debt": [200.0] * years, "Stockholders Equity": [r * 0.6 for r in rev],
                        "Total Assets": [r * 1.2 for r in rev]}, index=cols).T
    cf = pd.DataFrame({"Free Cash Flow": [r * 0.15 for r in rev], "Cash Dividends Paid": [-10.0] * years}, index=cols).T
    return inc, bal, cf


def test_summary_measures_growth_returns_and_consistency():
    s = fundamentals.summarize(_frames())
    assert s["yrs"] == 11 and s["rev10"] == pytest.approx(0.10, abs=1e-3) and s["rev5"] == pytest.approx(0.10, abs=1e-3)
    assert s["revUp"] == 10 and s["prof"] == 10 and s["fcfPos"] == 10 and s["roic15"] == 10
    assert s["nm"] == pytest.approx(0.2) and s["sh10"] < 0 and s["eps10"] > s["rev10"]     # buybacks lift per-share growth
    assert s["roic"] == pytest.approx(1000 * 0.25 * 0.8 / (200 + 600 - 100), abs=1e-3)
    assert s["qs"] == 6 and s["qn"] == 6 and len(s["rs"]) == 10


def test_summary_leaves_blanks_for_short_histories_and_bad_share_counts():
    short = fundamentals.summarize(_frames(years=4))
    assert short["rev10"] is None and short["roic10"] is None and short["roic15"] is None and short["rev5"] is None
    units = fundamentals.summarize(_frames(shares=[100.0] * 5 + [100000.0] * 6))   # a filing switched from thousands to units
    assert units["sh10"] is None and units["sh5"] is None
    assert fundamentals.summarize(None) is None


def test_member_lists_fall_back_to_the_shipped_copy(tmp_path, monkeypatch):
    import json
    import market
    monkeypatch.setattr(market, "CACHE", tmp_path / "cache")
    def refused():
        raise OSError("HTTP Error 401")
    monkeypatch.setitem(market.SOURCES, "dow", refused)
    rows = market.members("dow")
    assert len(rows) >= 25 and {"symbol", "name", "sector"} <= set(rows[0])
    assert json.loads((tmp_path / "cache" / "members_dow.json").read_text()) == rows   # kept, so the next call doesn't retry
