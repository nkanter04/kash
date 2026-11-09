"""Offline tests for the Who's Trading parsers and return math."""
import io
import zipfile
from datetime import date

import numpy as np
import pytest

import disclosures as d

HOUSE = """Clerk of the House
Name: Hon. Jane Doe
T
ID Owner Asset Transaction
Type
Date Notification
Date
Amount Cap.
Gains >
$200?
SP Apple Inc. - Common Stock (AAPL) [ST]
S (partial) 03/01/2026 03/20/2026 $1,001 - $15,000
F S : New
S O : David Taylor Trust
NVIDIA Corporation - Common Stock (NVDA)
[ST]
P 02/10/2026 02/12/2026 $15,001 -
$50,000
Treasury Bill (6-Week, Matures
12/3/2026) [GS]
P 10/20/2026 10/20/2026 Over $50,000,000
* For the complete list of asset type abbreviations
"""


def test_house_ptr_rows():
    rows = d.parse_house_ptr(HOUSE)
    assert len(rows) == 3
    a, n, t = rows
    assert (a["ticker"], a["side"], a["owner"], a["trade_date"], a["low"], a["high"]) == ("AAPL", "sell", "Spouse", "2026-03-01", 1001, 15000)
    assert (n["ticker"], n["side"], n["owner"], n["high"]) == ("NVDA", "buy", "Self", 50000)
    assert t["ticker"] is None and t["asset_type"] == "GS" and t["low"] == t["high"] == 50_000_000


SENATE = """<table><thead><tr><th>#</th></tr></thead><tbody>
<tr><td>1</td><td>01/15/2026</td><td>Spouse</td><td><a href="x">BRK.B</a></td><td>Berkshire</td><td>Stock</td><td>Sale (Full)</td><td>$15,001 - $50,000</td><td>--</td></tr>
<tr><td>2</td><td>02/02/2026</td><td>Self</td><td>--</td><td>Some Bond</td><td>Corporate Bond</td><td>Purchase</td><td>$1,001 - $15,000</td><td>--</td></tr>
</tbody></table>"""


def test_senate_rows():
    a, b = d.parse_senate_ptr(SENATE)
    assert (a["ticker"], a["side"], a["owner"]) == ("BRK-B", "sell", "Spouse")
    assert b["ticker"] is None and b["side"] == "buy"


@pytest.mark.parametrize("raw,want", [("COOK TIMOTHY D", "Timothy D Cook"), ("Ho Michael", "Michael Ho"), ("Grafiti Group LLC", "Grafiti Group LLC")])
def test_names(raw, want):
    assert d.person_name(raw) == want


@pytest.mark.parametrize("text,want", [("$1,001 - $15,000", (1001, 15000)), ("Over $50,000,000", (50000000, 50000000)), ("", (None, None))])
def test_amounts(text, want):
    assert d.parse_amount(text) == want


def _zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL\tAFF10B5ONE\nA1\t31-JUL-2026\t4\t0000320193\tAPPLE INC\tAAPL\t1\nA3\t31-JUL-2026\t3\t0000320193\tAPPLE INC\tAAPL\t0\n")
        z.writestr("REPORTINGOWNER.tsv", "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\nA1\t0001\tCOOK TIMOTHY D\tOfficer\tCEO\nA1\t0002\tOTHER PERSON\tOfficer\t\n")
        z.writestr("NONDERIV_TRANS.tsv", "ACCESSION_NUMBER\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\tDIRECT_INDIRECT_OWNERSHIP\nA1\t29-JUL-2026\tS\t100\t200.5\tD\nA1\t29-JUL-2026\tA\t999\t0\tD\nA3\t29-JUL-2026\tP\t1\t1\tD\n")
    b.seek(0)
    return zipfile.ZipFile(b)


def test_sec_keeps_market_trades_and_credits_one_owner():
    people, trades = d.sec_rows(_zip())
    assert len(trades) == 1 and list(people) == ["i:1"]        # award excluded, Form 3 excluded, joint filer counted once
    t = trades[0]
    assert (t["side"], t["amount"], t["planned"], t["trade_date"], t["filed_date"]) == ("sell", 20050.0, 1, "2026-07-29", "2026-07-31")


def test_oge_page_pairs_rows_with_the_amount_block_and_fixes_ocr():
    page = """1 YES
TRIBOROUGH BROG & T 5% DUE ON 12/01/50 Purchase 1/2812025
2 YES
NVIDIA CORP Sale (Partial) 9/2/2025
I Page 2 of 21
Amount
$100.001 - $250,000
Over $5,000,000"""
    rows = d.parse_oge_page(page)
    assert [(r["side"], r["trade_date"], r["low"], r["high"]) for r in rows] == [("buy", "2025-01-28", 100001, 250000), ("sell", "2025-09-02", 5000000, 5000000)]


def test_oge_page_leaves_amounts_blank_if_they_dont_line_up():
    rows = d.parse_oge_page("A BOND Purchase 1/2/2025\nB BOND Purchase 1/3/2025\n$1,001 - $15,000")
    assert len(rows) == 2 and all(r["low"] is None for r in rows)


def test_forward_returns():
    dates = np.array(["2026-01-05", "2026-01-06", "2026-04-07"], dtype="datetime64[ns]")
    px = np.array([100.0, 110.0, 150.0])
    # weekend trade uses the next close
    assert d.forward_returns(np.datetime64("2026-01-03"), dates, px) == pytest.approx(0.5)
    assert d.forward_returns(np.datetime64("2026-01-05"), dates, px, 90) == pytest.approx(0.5)
    assert d.forward_returns(np.datetime64("2026-04-05"), dates, px, 90) is None  # outcome not known yet
    assert d.forward_returns(np.datetime64("2027-01-01"), dates, px) is None


def test_recent_quarters_exclude_current():
    q = d._quarters(5)
    current = f"{date.today().year}q{(date.today().month - 1) // 3 + 1}"
    assert current not in q and len(set(q)) == 5


@pytest.mark.parametrize("raw,want", [
    ("g fedcF IlINg S TATuS : New S ubHOlDINg O F : Charles Schwab IRA SP Walt Disney Company (DIS) [ST]", ("Walt Disney Company (DIS) [ST]", "Spouse")),
    ("Apple Inc. - Common Stock (AAPL) [ST]", ("Apple Inc. - Common Stock (AAPL) [ST]", None)),
])
def test_old_house_pdfs_keep_only_the_asset(raw, want):
    assert d.clean_house_asset(raw) == want


def test_clean_data_drops_impossible_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "CACHE", tmp_path)
    monkeypatch.setattr(d, "DB", tmp_path / "x.db")
    c = d.db()
    rows = [("A", "2025-01-01", "2025-01-10"), ("B", "0023-06-23", "2025-01-10"), ("C", "2025-03-01", "2025-01-10"), ("N/A", "2025-01-01", "2025-01-10"), ("D", "2099-01-01", None)]
    c.executemany("INSERT INTO trades(person,ticker,side,trade_date,filed_date) VALUES('p',?,'buy',?,?)", rows)
    c.commit()
    d.clean_data(c)
    assert [r[0] for r in c.execute("SELECT ticker FROM trades")] == ["A"]


def test_options_get_no_stock_return(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "CACHE", tmp_path)
    monkeypatch.setattr(d, "DB", tmp_path / "x.db")
    c = d.db()
    c.executemany("INSERT INTO trades(ticker,asset,asset_type,r_filed) VALUES(?,?,?,0.5)",
                  [("A", "Apple", "ST"), ("B", "Boeing Option Type: Call", "ST"), ("C", "Citi", "OP"), ("D", "Dell", "Stock Option")])
    d.clear_option_returns(c)
    assert [r[0] for r in c.execute("SELECT ticker FROM trades WHERE r_filed IS NOT NULL")] == ["A"]


def test_party_needs_more_than_a_matching_surname(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(d, "CACHE", tmp_path)
    monkeypatch.setattr(d, "DB", tmp_path / "x.db")
    c = d.db()
    c.executemany("INSERT INTO people(id,name,grp,state) VALUES(?,?,'congress',?)",
                  [("h:green:mark", "Mark Green", "TN07"), ("h:smith:thomas", "Thomas Smith", "OH01"), ("s:tuberville:thomas", "Thomas H Tuberville", None)])
    c.commit()
    roster = [{"name": {"last": "Green", "first": "Al"}, "id": {"bioguide": "G1"}, "terms": [{"type": "rep", "party": "Democrat", "state": "TX"}]},
              {"name": {"last": "Smith", "first": "Tom"}, "id": {"bioguide": "S1"}, "terms": [{"type": "rep", "party": "Republican", "state": "OH"}]},
              {"name": {"last": "Tuberville", "first": "Tommy"}, "id": {"bioguide": "T1"}, "terms": [{"type": "sen", "party": "Republican", "state": "AL"}]}]
    feeds = iter([json.dumps(roster).encode(), b"{}", b"[]"])
    monkeypatch.setattr(d, "_get", lambda *a, **k: next(feeds))
    d.load_legislators(c)
    party = {r["id"]: r["party"] for r in c.execute("SELECT id, party FROM people")}
    assert party == {"h:green:mark": None, "h:smith:thomas": "Republican", "s:tuberville:thomas": "Republican"}


@pytest.mark.parametrize("raw,want", [("Hon. Mark Dr", "Mark"), ("Dr. Ann", "Ann"), ("Drew", "Drew")])
def test_titles_are_removed(raw, want):
    assert d.strip_title(raw) == want

