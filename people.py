"""Queries behind the Who's Trading pages. All read from the local database built by disclosures.py.

"Beat the S&P" numbers measure from the day a trade became PUBLIC (the filing date), because that's when anyone else
could have copied it. Amounts for Congress and the President are reported as ranges, so dollar figures use midpoints.
"""
from __future__ import annotations

import json
import re

import disclosures

GROUPS = {"congress": "Congress", "executive": "President", "insider": "Corporate insiders"}
TRADE_COLS = """t.id, t.person, p.name, p.grp, p.role, p.party, t.ticker, t.asset, t.asset_type, t.side, t.trade_date, t.filed_date, t.low, t.high, t.amount,
  t.owner, t.planned, t.url, t.overlap, t.r_filed, t.b_filed, t.r_trade, t.b_trade, t.r365, t.b365,
  CAST(julianday(t.filed_date) - julianday(t.trade_date) AS INTEGER) AS lag"""


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _group(where: str, group: str | None, args: list) -> str:
    if group in GROUPS:
        args.append(group)
        return where + " AND p.grp=?"
    return where


def status() -> dict:
    if not disclosures.DB.exists():
        return {"ready": False}
    c = disclosures.db()
    counts = {r["grp"]: {"people": r["n"], "trades": r["t"]} for r in c.execute(
        "SELECT p.grp, COUNT(DISTINCT p.id) n, COUNT(t.id) t FROM people p JOIN trades t ON t.person=p.id GROUP BY p.grp")}
    meta = {r["k"]: r["v"] for r in c.execute("SELECT * FROM meta")}
    through = {r["grp"]: r["d"] for r in c.execute("SELECT p.grp, MAX(t.filed_date) d FROM trades t JOIN people p ON p.id=t.person GROUP BY p.grp")}
    return {"ready": bool(counts), "counts": counts, "built": meta.get("built"), "through": through}


STATS = """
  COUNT(*) AS trades,
  SUM(t.side='buy') AS buys, SUM(t.side='sell') AS sells,
  AVG(CASE WHEN t.side='buy' AND t.r_filed IS NOT NULL THEN t.r_filed - t.b_filed END) AS alpha_public,
  AVG(CASE WHEN t.side='buy' AND t.r_trade IS NOT NULL THEN t.r_trade - t.b_trade END) AS alpha_trade,
  AVG(CASE WHEN t.side='buy' AND t.r365 IS NOT NULL THEN t.r365 - t.b365 END) AS alpha_1y,
  SUM(t.side='buy' AND t.r365 IS NOT NULL) AS tracked_1y,
  SUM(t.side='buy' AND t.r_filed IS NOT NULL) AS tracked,
  AVG(CASE WHEN t.side='buy' AND t.r_filed IS NOT NULL THEN t.r_filed > t.b_filed END) AS win_rate,
  AVG(CASE WHEN t.side='sell' AND t.r_trade IS NOT NULL THEN t.r_trade < t.b_trade END) AS good_sells,
  SUM(t.side='sell' AND t.r_trade IS NOT NULL) AS tracked_sells,
  AVG(julianday(t.filed_date) - julianday(t.trade_date)) AS avg_lag,
  SUM(julianday(t.filed_date) - julianday(t.trade_date) > 45) AS late,
  MAX(t.filed_date) AS last_filed, MIN(t.trade_date) AS first_trade,
  SUM(CASE WHEN t.side='buy' THEN t.amount ELSE 0 END) AS bought, SUM(CASE WHEN t.side='sell' THEN t.amount ELSE 0 END) AS sold,
  SUM(t.overlap) AS overlap
"""


def overview(group: str | None = None) -> dict:
    """The landing page. These scans take many seconds on the full data, so the update job stores them in advance."""
    row = disclosures.db().execute("SELECT v FROM meta WHERE k=?", (f"overview:{group or 'all'}",)).fetchone()
    return json.loads(row[0]) if row else compute_overview(group)


def precompute(c=None) -> None:
    c = c or disclosures.db()
    for g in (None, *GROUPS):
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"overview:{g or 'all'}", json.dumps(compute_overview(g))))
    c.commit()


def compute_overview(group: str | None = None) -> dict:
    c = disclosures.db()
    a: list = []
    recent = _rows(c.execute(f"SELECT {TRADE_COLS} FROM trades t JOIN people p ON p.id=t.person WHERE t.side IN ('buy','sell') AND t.ticker IS NOT NULL" + _group("", group, a).replace(" AND p.grp=?", " AND p.grp=?") + " ORDER BY t.filed_date DESC, t.amount DESC LIMIT 40", a))
    # Congress only, 5+ buys with a known 1-year outcome across 3+ different stocks. Corporate insiders mostly buy their own
    # company, so one great stock would top the list: that measures the stock, not the person's judgment.
    leaders = [] if group in ("insider", "executive") else _rows(c.execute(f"""SELECT p.id, p.name, p.grp, p.role, p.party, {STATS} FROM trades t JOIN people p ON p.id=t.person
        WHERE t.ticker IS NOT NULL AND p.grp='congress' GROUP BY p.id
        HAVING tracked_1y >= 5 AND COUNT(DISTINCT CASE WHEN t.side='buy' AND t.r365 IS NOT NULL THEN t.ticker END) >= 3
        ORDER BY alpha_1y DESC LIMIT 12"""))
    congress = _rows(c.execute("""SELECT t.ticker, MAX(t.asset) asset, COUNT(DISTINCT t.person) people, SUM(t.side='buy') buys, SUM(t.side='sell') sells
        FROM trades t JOIN people p ON p.id=t.person
        WHERE p.grp='congress' AND t.ticker IS NOT NULL AND t.side IN ('buy','sell') AND t.filed_date >= date((SELECT MAX(filed_date) FROM trades WHERE doc LIKE 'h%' OR doc LIKE 's%'), '-90 days')
        GROUP BY t.ticker HAVING buys > 0 ORDER BY people DESC, buys DESC LIMIT 10"""))
    # several different insiders buying on the open market within the latest 60 days of data
    clusters = _rows(c.execute("""SELECT t.ticker, MAX(t.asset) asset, COUNT(DISTINCT t.person) people, SUM(t.amount) amount, MAX(t.filed_date) last
        FROM trades t JOIN people p ON p.id=t.person
        WHERE p.grp='insider' AND t.side='buy' AND t.planned=0 AND t.filed_date >= date((SELECT MAX(filed_date) FROM trades t2 JOIN people p2 ON p2.id=t2.person WHERE p2.grp='insider'), '-60 days')
        GROUP BY t.ticker HAVING COUNT(DISTINCT t.person) >= 3 AND SUM(t.amount) >= 100000
          AND SUM(t.amount) / COUNT(DISTINCT t.person) >= 25000     -- smaller amounts are typically employee purchase plans
        ORDER BY COUNT(DISTINCT t.person) DESC, SUM(t.amount) DESC LIMIT 10"""))
    late = _rows(c.execute(f"""SELECT p.id, p.name, p.grp, p.role, p.party, {STATS} FROM trades t JOIN people p ON p.id=t.person
        WHERE p.grp='congress' GROUP BY p.id HAVING late > 0 ORDER BY late DESC LIMIT 8"""))
    return {"status": status(), "recent": recent, "leaders": leaders, "congress": congress, "clusters": clusters, "late": late}


def search(q: str) -> dict:
    q = q.strip()
    if len(q) < 2:
        return {"people": [], "tickers": []}
    c = disclosures.db()
    like = "%" + "%".join(re.split(r"\s+", q)) + "%"
    people = _rows(c.execute("""SELECT p.id, p.name, p.grp, p.role, p.party, COUNT(t.id) trades FROM people p JOIN trades t ON t.person=p.id
        WHERE p.name LIKE ? GROUP BY p.id ORDER BY (p.grp='insider'), trades DESC LIMIT 15""", (like,)))
    tickers = _rows(c.execute("""SELECT t.ticker, MAX(t.asset) asset, COUNT(*) trades FROM trades t WHERE t.ticker LIKE ? GROUP BY t.ticker ORDER BY LENGTH(t.ticker), trades DESC LIMIT 5""", (q.upper() + "%",)))
    return {"people": people, "tickers": tickers}


def person(pid: str) -> dict | None:
    c = disclosures.db()
    p = c.execute("SELECT * FROM people WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    stats = dict(c.execute(f"SELECT {STATS} FROM trades t WHERE t.person=? AND (t.ticker IS NOT NULL OR t.side IN ('buy','sell'))", (pid,)).fetchone())
    trades = _rows(c.execute(f"SELECT {TRADE_COLS} FROM trades t JOIN people p ON p.id=t.person WHERE t.person=? ORDER BY t.trade_date DESC LIMIT 1500", (pid,)))
    top = _rows(c.execute("""SELECT t.ticker, MAX(t.asset) asset, COUNT(*) n, SUM(t.side='buy') buys, SUM(t.side='sell') sells,
          SUM(CASE WHEN t.side='buy' THEN t.amount ELSE -t.amount END) net FROM trades t WHERE t.person=? AND t.ticker IS NOT NULL GROUP BY t.ticker ORDER BY n DESC LIMIT 8""", (pid,)))
    info = dict(p)
    info["committees"] = json.loads(p["committees"]) if p["committees"] else []
    return {"person": info, "stats": stats, "trades": trades, "top": top}


def for_stock(ticker: str) -> dict:
    """Officials' and insiders' trades in one stock."""
    if not disclosures.DB.exists():
        return {"trades": [], "people": 0}
    c = disclosures.db()
    rows = _rows(c.execute(f"SELECT {TRADE_COLS} FROM trades t JOIN people p ON p.id=t.person WHERE t.ticker=? AND p.grp!='insider' AND t.side IN ('buy','sell') ORDER BY t.trade_date DESC LIMIT 30", (ticker,)))
    return {"trades": rows, "people": len({r["person"] for r in rows})}
