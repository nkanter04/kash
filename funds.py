"""Big Investors: what well-known money managers hold, from the 13F reports they file with the SEC each quarter.

`python funds.py build` creates .cache/funds.db (incremental; a few minutes the first time). A copy in data/funds.db
is used when the cache has none, so a hosted copy can show the same data. 13F reports are public records.

Limits worth knowing (and shown in the UI): reports arrive up to 45 days after the quarter ends, list only US-listed
stocks and options (no shorts, bonds, cash or foreign listings), and values are as of the quarter's last day.
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

import sec

ROOT = Path(__file__).parent
DB = sec.CACHE / "funds.db"
SNAPSHOT = ROOT / "data" / "funds.db"
QUARTERS = 8
REFRESH_DAYS = 3

# (SEC filer id, investor, firm, a word that must appear in the SEC's name for that id: guards against a wrong id)
MANAGERS = [
    (1067983, "Warren Buffett", "Berkshire Hathaway", "BERKSHIRE"),
    (1336528, "Bill Ackman", "Pershing Square", "PERSHING"),
    (1649339, "Michael Burry", "Scion Asset Management", "SCION"),
    (1536411, "Stanley Druckenmiller", "Duquesne Family Office", "DUQUESNE"),
    (1656456, "David Tepper", "Appaloosa", "APPALOOSA"),
    (1061768, "Seth Klarman", "Baupost Group", "BAUPOST"),
    (1709323, "Li Lu", "Himalaya Capital", "HIMALAYA"),
    (1549575, "Mohnish Pabrai", "Dalal Street", "DALAL"),
    (1569205, "Terry Smith", "Fundsmith", "FUNDSMITH"),
    (1112520, "Chuck Akre", "Akre Capital", "AKRE"),
    (1096343, "Tom Gayner", "Markel", "MARKEL"),
    (1647251, "Chris Hohn", "TCI Fund Management", "TCI"),
    (1489933, "David Einhorn", "Greenlight Capital (DME Capital Management)", "DME CAPITAL"),
    (1040273, "Dan Loeb", "Third Point", "THIRD POINT"),
    (921669, "Carl Icahn", "Icahn Capital", "ICAHN"),
    (1029160, "George Soros", "Soros Fund Management", "SOROS"),
    (1350694, "Ray Dalio (founder)", "Bridgewater Associates", "BRIDGEWATER"),
    (949509, "Howard Marks", "Oaktree Capital", "OAKTREE"),
    (1166559, "Bill Gates", "Gates Foundation Trust", "GATES"),
    (1167483, "Chase Coleman", "Tiger Global", "TIGER"),
    (1135730, "Philippe Laffont", "Coatue Management", "COATUE"),
    (1061165, "Stephen Mandel", "Lone Pine Capital", "LONE PINE"),
    (1103804, "Andreas Halvorsen", "Viking Global", "VIKING"),
    (1541617, "Brad Gerstner", "Altimeter Capital", "ALTIMETER"),
    (1697748, "Cathie Wood", "ARK Invest", "ARK"),
    (1345471, "Nelson Peltz", "Trian Fund Management", "TRIAN"),
    (1791786, "Paul Singer", "Elliott Management", "ELLIOTT"),
    (1517137, "Jeff Smith", "Starboard Value", "STARBOARD"),
    (1418814, "Mason Morfit", "ValueAct Capital", "VALUEACT"),
    (1035674, "John Paulson", "Paulson & Co.", "PAULSON"),
    (1720792, "Ruane Cunniff team", "Ruane, Cunniff & Goldfarb (Sequoia Fund)", "RUANE"),
    (1484150, "Nick Train", "Lindsell Train", "LINDSELL"),
    (1553733, "Glenn Greenberg", "Brave Warrior Advisors", "BRAVE WARRIOR"),
    (1056831, "Bruce Berkowitz", "Fairholme Capital", "FAIRHOLME"),
    (1036325, "Chris Davis", "Davis Selected Advisers", "DAVIS"),
    (807985, "Mason Hawkins", "Southeastern Asset Management", "SOUTHEASTERN"),
    (915191, "Prem Watsa", "Fairfax Financial", "FAIRFAX"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS managers(cik INTEGER PRIMARY KEY, investor TEXT, firm TEXT, sec_name TEXT, rank INTEGER);
CREATE TABLE IF NOT EXISTS filings(cik INTEGER, period TEXT, filed TEXT, acc TEXT, value REAL, positions INTEGER, PRIMARY KEY(cik, period));
CREATE TABLE IF NOT EXISTS holdings(cik INTEGER, period TEXT, cusip TEXT, kind TEXT, ticker TEXT, name TEXT, cls TEXT, value REAL, shares REAL,
  PRIMARY KEY(cik, period, cusip, kind));
CREATE INDEX IF NOT EXISTS holdings_ticker ON holdings(ticker, period);
CREATE TABLE IF NOT EXISTS cusips(cusip TEXT PRIMARY KEY, ticker TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def path() -> Path | None:
    return DB if DB.exists() else SNAPSHOT if SNAPSHOT.exists() else None


def db(write: bool = False) -> sqlite3.Connection:
    p = DB if write else path()
    c = sqlite3.connect(p, timeout=30)
    c.row_factory = sqlite3.Row
    return c


# ------------------------------------------------------------------ SEC downloads

def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": sec._user_agent(), "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with sec._lock:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                time.sleep(0.13)
            return body
        except urllib.error.HTTPError as e:      # the SEC answers 503/429 when briefly overloaded
            if e.code not in (429, 503) or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def parse_info_table(xml: bytes, filed: str) -> list[dict]:
    """A 13F "information table" -> one row per security (and per put/call), summed across the filer's sub-managers."""
    root = ET.fromstring(xml)
    for el in root.iter():                       # filers use different namespace prefixes: drop them
        el.tag = el.tag.rsplit("}", 1)[-1]
    scale = 1000 if filed < "2023-01-03" else 1  # values were reported in thousands until 2023
    out: dict[tuple, dict] = {}
    for t in root.iter("infoTable"):
        def text(tag):
            e = t.find(".//" + tag)
            return (e.text or "").strip() if e is not None else ""
        cusip = text("cusip").upper().zfill(9)
        kind = text("putCall").title()
        if text("sshPrnamtType").upper() != "SH" and not kind:
            continue                              # bonds are listed by principal amount, not shares
        try:
            value, shares = float(text("value") or 0) * scale, float(text("sshPrnamt") or 0)
        except ValueError:
            continue
        row = out.setdefault((cusip, kind), {"cusip": cusip, "kind": kind, "name": text("nameOfIssuer"),
                                             "cls": text("titleOfClass"), "value": 0.0, "shares": 0.0})
        row["value"] += value
        row["shares"] += shares
    rows = list(out.values())
    # some filers still report in thousands: a typical holding "worth" under $1 a share gives that away
    prices = [r["value"] / r["shares"] for r in rows if not r["kind"] and r["shares"] > 0]
    if scale == 1 and prices and statistics.median(prices) < 1:
        for r in rows:
            r["value"] *= 1000
    return rows


def parse_ftd(text: str) -> dict[str, str]:
    """An SEC fails-to-deliver file (SETTLEMENT DATE|CUSIP|SYMBOL|...) -> {cusip: ticker}."""
    out = {}
    for line in text.splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 3 and len(p[1]) == 9 and p[2].strip():
            out[p[1].upper()] = p[2].strip().upper().replace(".", "-").replace("/", "-")
    return out


def load_cusips(c: sqlite3.Connection, log=print, months: int = 8) -> None:
    """CUSIP -> ticker from the SEC's twice-monthly fails-to-deliver lists (almost every traded stock shows up within months)."""
    done = {r[0] for r in c.execute("SELECT k FROM meta WHERE k LIKE 'ftd:%'")}
    y, m = date.today().year, date.today().month
    names = []
    for _ in range(months):
        names += [f"cnsfails{y}{m:02d}b", f"cnsfails{y}{m:02d}a"]
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    for name in reversed(names):                  # oldest first, so the newest ticker for a CUSIP wins
        if f"ftd:{name}" in done:
            continue
        try:
            z = zipfile.ZipFile(io.BytesIO(_get(f"https://www.sec.gov/files/data/fails-deliver-data/{name}.zip")))
        except urllib.error.HTTPError:
            continue                              # not published yet
        m_ = parse_ftd(z.read(z.namelist()[0]).decode("latin-1"))
        c.executemany("INSERT OR REPLACE INTO cusips VALUES(?,?)", m_.items())
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (f"ftd:{name}", str(len(m_))))
        c.commit()
        log(f"  {name}: {len(m_)} tickers")


def _norm(name: str) -> str:
    n = re.sub(r"[^A-Z0-9 ]", "", name.upper())
    n = re.sub(r"\b(INC|CORP|CORPORATION|CO|COMPANY|LTD|PLC|HOLDINGS|HLDGS|HLDG|GROUP|GRP|THE|NEW|DEL|CL A|CL B|CLASS A|COM|NV|SA|LP)\b", "", n)
    return re.sub(r"\s+", "", n)


def assign_tickers(c: sqlite3.Connection) -> None:
    c.execute("UPDATE holdings SET ticker = (SELECT ticker FROM cusips WHERE cusips.cusip = holdings.cusip) WHERE ticker IS NULL")
    # fall back to an exact (normalized) company-name match against the SEC's ticker list
    try:
        tick = json.loads((sec.CACHE / "company_tickers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tick = {}
    by_name: dict[str, str] = {}
    for v in tick.values():
        by_name.setdefault(_norm(v["title"]), v["ticker"].upper())
    # the fails-to-deliver lists write share classes without a separator (BRKB): use the SEC's spelling (BRK-B)
    known = {v["ticker"].upper() for v in tick.values()}
    for (t,) in c.execute("SELECT DISTINCT ticker FROM holdings WHERE ticker IS NOT NULL").fetchall():
        fixed = f"{t[:-1]}-{t[-1]}"
        if known and t not in known and len(t) >= 3 and fixed in known:
            c.execute("UPDATE holdings SET ticker=? WHERE ticker=?", (fixed, t))
    for r in c.execute("SELECT DISTINCT cusip, name FROM holdings WHERE ticker IS NULL").fetchall():
        t = by_name.get(_norm(r["name"]))
        if t:
            c.execute("UPDATE holdings SET ticker=? WHERE cusip=? AND ticker IS NULL", (t, r["cusip"]))
    c.commit()


def build(log=print) -> None:
    if not sec._user_agent():
        raise SystemExit("Set the SEC contact email first (config.json or SEC_USER_AGENT).")
    DB.parent.mkdir(exist_ok=True)
    if not DB.exists() and SNAPSHOT.exists():
        DB.write_bytes(SNAPSHOT.read_bytes())     # start from the shipped copy, then add what's new
    c = db(write=True)
    c.executescript(SCHEMA)
    c.execute("INSERT OR REPLACE INTO meta VALUES('building', ?)", (str(time.time()),))
    c.commit()
    load_cusips(c, log)
    for rank, (cik, investor, firm, word) in enumerate(MANAGERS):
        try:
            sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))
            if word not in sub["name"].upper():
                log(f"  SKIPPED {investor}: SEC id {cik} is '{sub['name']}'")
                continue
            c.execute("INSERT OR REPLACE INTO managers VALUES(?,?,?,?,?)", (cik, investor, firm, sub["name"], rank))
            r = sub["filings"]["recent"]
            seen, todo = set(), []
            for form, acc, filed, period in zip(r["form"], r["accessionNumber"], r["filingDate"], r["reportDate"]):
                if form == "13F-HR" and period not in seen and len(seen) < QUARTERS:   # newest first; skip amendments
                    seen.add(period)
                    todo.append((acc, filed, period))
            have = {x[0] for x in c.execute("SELECT period FROM filings WHERE cik=?", (cik,))}
            for acc, filed, period in todo:
                if period in have:
                    continue
                folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
                items = json.loads(_get(folder + "/index.json"))["directory"]["item"]
                xml = next((i["name"] for i in items if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()), None)
                if not xml:
                    continue
                rows = parse_info_table(_get(f"{folder}/{xml}"), filed)
                stocks = [x for x in rows if not x["kind"]]
                c.executemany("INSERT OR REPLACE INTO holdings VALUES(?,?,?,?,NULL,?,?,?,?)",
                              [(cik, period, x["cusip"], x["kind"], x["name"], x["cls"], x["value"], x["shares"]) for x in rows])
                c.execute("INSERT OR REPLACE INTO filings VALUES(?,?,?,?,?,?)",
                          (cik, period, filed, acc, sum(x["value"] for x in stocks), len(stocks)))
                c.commit()
            # keep only the latest quarters
            c.execute("DELETE FROM holdings WHERE cik=? AND period NOT IN (SELECT period FROM filings WHERE cik=? ORDER BY period DESC LIMIT ?)", (cik, cik, QUARTERS))
            c.execute("DELETE FROM filings WHERE cik=? AND period NOT IN (SELECT period FROM filings WHERE cik=? ORDER BY period DESC LIMIT ?)", (cik, cik, QUARTERS))
            c.commit()
            log(f"  {investor}: {len(todo)} quarters ({len(todo) - len(have & {t[2] for t in todo})} new)")
        except (urllib.error.URLError, TimeoutError, ET.ParseError, KeyError, ValueError) as e:
            log(f"  {investor}: {type(e).__name__}: {e}")
    assign_tickers(c)
    c.execute("INSERT OR REPLACE INTO meta VALUES('built', ?)", (time.strftime("%Y-%m-%d"),))
    c.execute("DELETE FROM meta WHERE k='building'")
    c.commit()
    n = c.execute("SELECT COUNT(*), SUM(ticker IS NOT NULL) FROM holdings").fetchone()
    log(f"Done: {n[0]} holdings, {n[1]} matched to a ticker")
    c.close()
    _cache.clear()


def refresh_if_stale(log=print) -> None:
    """Called at server start on the owner's computer: new filings land around the 45th day after each quarter."""
    try:
        if DB.exists():
            c = db()
            meta = {r["k"]: r["v"] for r in c.execute("SELECT * FROM meta WHERE k IN ('built','building')")}
            c.close()
            if "building" in meta and time.time() - float(meta["building"]) < 3600:
                return
            if meta.get("built") and (date.today() - date.fromisoformat(meta["built"])).days < REFRESH_DAYS:
                return
        build(log)
    except Exception as e:  # noqa: BLE001
        log(f"Big Investors refresh failed: {e}")


# ------------------------------------------------------------------ queries

_cache: dict = {}


def _cached(key, fn):
    p = path()
    stamp = (str(p), p.stat().st_mtime) if p else None
    if _cache.get("stamp") != stamp:
        _cache.clear()
        _cache["stamp"] = stamp
    if key not in _cache:
        _cache[key] = fn()
    return _cache[key]


def _periods(c, cik) -> list[str]:
    return [r[0] for r in c.execute("SELECT period FROM filings WHERE cik=? ORDER BY period DESC", (cik,))]


def _positions(c, cik: int) -> list[dict]:
    """Latest holdings with each one's share of the portfolio and the change since the previous report."""
    ps = _periods(c, cik)
    if not ps:
        return []
    cur = [dict(r) for r in c.execute("SELECT cusip, kind, ticker, name, cls, value, shares FROM holdings WHERE cik=? AND period=? ORDER BY value DESC", (cik, ps[0]))]
    prev = {(r["cusip"], r["kind"]): r for r in c.execute("SELECT cusip, kind, ticker, name, cls, value, shares FROM holdings WHERE cik=? AND period=?", (cik, ps[1]))} if len(ps) > 1 else None
    total = sum(r["value"] for r in cur if not r["kind"]) or 1
    for r in cur:
        r["pct"] = r["value"] / total if not r["kind"] else None
        p = prev.get((r["cusip"], r["kind"])) if prev is not None else None
        if prev is None:
            r["act"], r["chg"] = None, None
        elif p is None or not p["shares"]:
            r["act"], r["chg"] = "new", None
        else:
            ch = r["shares"] / p["shares"] - 1
            r["act"], r["chg"] = ("add" if ch > 0.01 else "trim" if ch < -0.01 else "hold"), ch
    if prev is not None:
        held = {(r["cusip"], r["kind"]) for r in cur}
        for k, p in prev.items():
            if k not in held:
                cur.append({**dict(p), "pct": None, "act": "sold", "chg": -1.0, "prevValue": p["value"], "value": 0.0, "shares": 0.0})
    return cur


def _manager_row(c, m) -> dict:
    f = c.execute("SELECT period, filed, value, positions FROM filings WHERE cik=? ORDER BY period DESC LIMIT 1", (m["cik"],)).fetchone()
    return {"cik": m["cik"], "investor": m["investor"], "firm": m["firm"], **(dict(f) if f else {})}


def overview() -> dict:
    def make():
        c = db()
        managers, popular, moves = [], {}, {}
        newest = c.execute("SELECT MAX(period) FROM filings").fetchone()[0]
        for m in c.execute("SELECT * FROM managers ORDER BY rank").fetchall():
            row = _manager_row(c, m)
            if "period" not in row:
                continue
            pos = _positions(c, m["cik"])
            stocks = [p for p in pos if not p["kind"] and p["act"] != "sold"]
            row["top"] = [{"ticker": p["ticker"], "name": p["name"], "pct": p["pct"]} for p in stocks[:5]]
            row["top10"] = sum(p["pct"] for p in stocks[:10])
            managers.append(row)
            # someone who stopped filing (Scion deregistered in 2025) stays listed but out of the "what they're buying" tallies
            row["stale"] = (date.fromisoformat(newest) - date.fromisoformat(row["period"])).days > 200
            if row["stale"]:
                continue
            for p in pos:
                if p["kind"] or not p["ticker"]:
                    continue
                who = {"cik": m["cik"], "investor": m["investor"], "pct": p["pct"]}
                if p["act"] != "sold":
                    e = popular.setdefault(p["ticker"], {"ticker": p["ticker"], "name": p["name"], "holders": 0, "value": 0.0, "weight": 0.0})
                    e["holders"] += 1
                    e["value"] += p["value"]
                    e["weight"] += p["pct"]
                if p["act"] in ("new", "add", "trim", "sold"):
                    e = moves.setdefault(p["ticker"], {"ticker": p["ticker"], "name": p["name"], "new": [], "add": [], "trim": [], "sold": []})
                    e[p["act"]].append(who)
        meta = {r["k"]: r["v"] for r in c.execute("SELECT * FROM meta WHERE k='built'")}
        c.close()
        for e in popular.values():
            e["avg"] = e["weight"] / e["holders"]
        buys = sorted((e for e in moves.values() if e["new"] or e["add"]),
                      key=lambda e: (-(len(e["new"]) * 2 + len(e["add"]) - len(e["trim"]) - 2 * len(e["sold"])), -len(e["new"])))[:12]
        sells = sorted((e for e in moves.values() if e["sold"] or e["trim"]),
                       key=lambda e: (-(len(e["sold"]) * 2 + len(e["trim"]) - len(e["add"]) - 2 * len(e["new"])), -len(e["sold"])))[:12]
        slim = lambda e: {"ticker": e["ticker"], "name": e["name"], **{k: [w["investor"] for w in e[k]] for k in ("new", "add", "trim", "sold")}}  # noqa: E731
        return {"ready": True, "built": meta.get("built"), "managers": managers,
                "popular": sorted(popular.values(), key=lambda e: (-e["holders"], -e["weight"]))[:15],
                "conviction": sorted((e for e in popular.values() if e["holders"] >= 3), key=lambda e: -e["avg"])[:10],
                "buys": [slim(e) for e in buys], "sells": [slim(e) for e in sells]}
    return _cached("overview", make) if path() else {"ready": False}


def manager(cik: int) -> dict | None:
    def make():
        c = db()
        m = c.execute("SELECT * FROM managers WHERE cik=?", (cik,)).fetchone()
        if not m:
            return None
        out = {"manager": _manager_row(c, m), "positions": _positions(c, cik),
               "history": [dict(r) for r in c.execute("SELECT period, filed, value, positions FROM filings WHERE cik=? ORDER BY period", (cik,))]}
        out["manager"]["secName"] = m["sec_name"]
        c.close()
        return out
    return _cached(("m", cik), make) if path() else None


def for_stock(ticker: str) -> dict:
    """Which of the tracked investors hold this stock, as of each one's latest report."""
    def make():
        c = db()
        rows = []
        for m in c.execute("SELECT * FROM managers ORDER BY rank").fetchall():
            for p in _positions(c, m["cik"]):
                if p["ticker"] == ticker and not p["kind"]:
                    period = _periods(c, m["cik"])[0]
                    hist = [dict(r) for r in c.execute("SELECT period, SUM(shares) shares FROM holdings WHERE cik=? AND ticker=? AND kind='' GROUP BY period ORDER BY period", (m["cik"], ticker))]
                    rows.append({"cik": m["cik"], "investor": m["investor"], "firm": m["firm"], "period": period, "value": p["value"],
                                 "shares": p["shares"], "pct": p["pct"], "act": p["act"], "chg": p["chg"], "history": hist})
        c.close()
        rows.sort(key=lambda r: (r["act"] == "sold", -(r["pct"] or 0)))
        return {"holders": rows, "tracked": len(MANAGERS)}
    return _cached(("s", ticker), make) if path() else {"holders": [], "tracked": 0}


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        build()
    elif sys.argv[1:2] == ["snapshot"]:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        src, dst = sqlite3.connect(DB), sqlite3.connect(SNAPSHOT)
        src.backup(dst)
        dst.execute("VACUUM")
        dst.close()
        src.close()
        print(f"Copied to {SNAPSHOT} ({SNAPSHOT.stat().st_size / 1e6:.1f} MB)")
    else:
        print("usage: python funds.py build | snapshot")
