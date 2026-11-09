"""Who's Trading queries against a small throwaway database."""
import pytest

import disclosures
import people


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(disclosures, "CACHE", tmp_path)
    monkeypatch.setattr(disclosures, "DB", tmp_path / "t.db")
    return disclosures.db()


def add_person(c, pid, name, grp="congress", party="D"):
    c.execute("INSERT INTO people(id,name,grp,role,party) VALUES(?,?,?,?,?)", (pid, name, grp, "U.S. Representative", party))


def add_trade(c, pid, ticker="NVDA", side="buy", trade="2025-01-01", filed="2025-01-20", amount=8000, r_filed=0.2, b_filed=0.1, r_trade=0.3, b_trade=0.1, r365=None, b365=None, planned=0, doc="h1"):
    c.execute("""INSERT INTO trades(person,ticker,asset,asset_type,side,trade_date,filed_date,low,high,amount,owner,planned,url,doc,r_filed,b_filed,r_trade,b_trade,r365,b365)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (pid, ticker, ticker + " Inc", "ST", side, trade, filed, 1001, 15000, amount, "Self", planned, "https://x.gov/1", doc, r_filed, b_filed, r_trade, b_trade, r365, b365))


def test_person_measures_from_public_date_and_counts_late_reports(conn):
    add_person(conn, "h:a:b", "Ann Bee")
    add_trade(conn, "h:a:b")                                                  # 19 days: on time
    add_trade(conn, "h:a:b", trade="2025-01-01", filed="2025-03-15", r_filed=0.0, b_filed=0.1, r_trade=0.2, b_trade=0.1)  # 73 days: late
    add_trade(conn, "h:a:b", side="sell", r_filed=None, b_filed=None, r_trade=-0.1, b_trade=0.1)
    conn.commit()
    d = people.person("h:a:b")
    s = d["stats"]
    assert s["trades"] == 3 and s["buys"] == 2 and s["sells"] == 1
    assert s["alpha_public"] == pytest.approx(0.0)            # (+10 and -10 points)
    assert s["alpha_trade"] == pytest.approx(0.15)            # sells never enter buy returns
    assert s["win_rate"] == pytest.approx(0.5) and s["late"] == 1 and s["good_sells"] == 1
    assert people.person("nobody") is None


def test_leaderboard_needs_five_full_year_buys_and_ranks_by_one_year_edge(conn):
    add_person(conn, "h:good:x", "Good")
    add_person(conn, "h:better:x", "Better")
    add_person(conn, "h:lucky:x", "Lucky One-Hit")
    add_person(conn, "h:one:x", "One Stock")
    add_person(conn, "i:1", "Company Insider", grp="insider", party=None)
    for i in range(5):
        add_trade(conn, "h:good:x", ticker=f"G{i}", r365=0.2, b365=0.1)
        add_trade(conn, "h:better:x", ticker=f"B{i}", r365=0.4, b365=0.1)
        add_trade(conn, "h:one:x", ticker="ONE", r365=5.0, b365=0.1)
        add_trade(conn, "i:1", ticker=f"I{i}", r365=30.0, b365=0.1, doc="x")
    add_trade(conn, "h:lucky:x", r365=9.0, b365=0.1)
    conn.commit()
    d = people.overview()
    assert [p["name"] for p in d["leaders"]] == ["Better", "Good"]      # one-stock winners and insiders are excluded
    assert d["leaders"][0]["alpha_1y"] == pytest.approx(0.3)
    assert people.overview("insider")["leaders"] == []


def test_clusters_need_three_different_insiders_and_skip_planned_trades(conn):
    for i in range(3):
        add_person(conn, f"i:{i}", f"Insider {i}", grp="insider", party=None)
        add_trade(conn, f"i:{i}", ticker="AAA", amount=50000, doc="x")
    add_person(conn, "i:9", "Planner", grp="insider", party=None)
    for i in range(3):
        add_trade(conn, "i:9", ticker="BBB", amount=90000, doc="x")
    add_trade(conn, "i:1", ticker="CCC", amount=10000, planned=1, doc="x")
    for i in range(30):                                                # employee-plan sized: many people, small amounts
        add_person(conn, f"i:p{i}", f"Employee {i}", grp="insider", party=None)
        add_trade(conn, f"i:p{i}", ticker="PLAN", amount=21000, doc="x")
    conn.commit()
    c = people.overview("insider")["clusters"]
    assert [(x["ticker"], x["people"]) for x in c] == [("AAA", 3)]


def test_group_filter_search_and_stock_lookup(conn):
    add_person(conn, "h:p:n", "Nancy Pelosi")
    add_person(conn, "i:1", "Elon Musk", grp="insider", party=None)
    add_trade(conn, "h:p:n", ticker="AAPL")
    add_trade(conn, "i:1", ticker="TSLA", doc="x")
    conn.commit()
    assert {t["name"] for t in people.overview()["recent"]} == {"Nancy Pelosi", "Elon Musk"}
    assert {t["name"] for t in people.overview("congress")["recent"]} == {"Nancy Pelosi"}
    assert [p["name"] for p in people.search("pelosi")["people"]] == ["Nancy Pelosi"]
    assert people.search("AAP")["tickers"][0]["ticker"] == "AAPL"
    assert people.for_stock("AAPL")["people"] == 1
    assert people.for_stock("TSLA")["trades"] == []             # corporate insiders are shown elsewhere on the page


def test_status_before_any_data(tmp_path, monkeypatch):
    monkeypatch.setattr(disclosures, "DB", tmp_path / "missing.db")
    assert people.status() == {"ready": False}


def test_overview_is_served_from_the_stored_copy(conn, monkeypatch):
    add_person(conn, "h:p:n", "Nancy Pelosi")
    add_trade(conn, "h:p:n", ticker="AAPL")
    conn.commit()
    people.precompute(conn)
    monkeypatch.setattr(people, "compute_overview", lambda g=None: pytest.fail("should read the stored copy"))
    assert [t["ticker"] for t in people.overview()["recent"]] == ["AAPL"]
    assert people.overview("insider")["recent"] == []


def test_daily_refresh_skips_when_current_or_already_running(conn, monkeypatch):
    from datetime import date
    import time
    ran = []
    monkeypatch.setattr(disclosures, "build", lambda log=None: ran.append(1))
    conn.execute("INSERT INTO meta VALUES('built',?)", (date.today().isoformat(),)); conn.commit()
    assert disclosures.refresh_if_stale() is False
    conn.execute("UPDATE meta SET v='2020-01-01' WHERE k='built'")
    conn.execute("INSERT INTO meta VALUES('building',?)", (str(time.time()),)); conn.commit()
    assert disclosures.refresh_if_stale() is False
    conn.execute("DELETE FROM meta WHERE k='building'"); conn.commit()
    assert disclosures.refresh_if_stale() is True and ran == [1]

