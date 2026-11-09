"""Who's Trading: a local database of legally reported trades, with what they returned.

Sources (all official): House Clerk periodic transaction reports (PDF), Senate eFD reports (the site requires an
acknowledgment that the reports won't be used commercially, for credit ratings or for soliciting money; accepted here
for personal research), OGE Form 278-T executive-branch reports (PDF) and SEC Forms 3/4/5 quarterly data sets.

The Ethics in Government Act bars commercial use of Congressional and executive reports. The database lives in
.cache/ and must never be committed or served publicly without legal review.

Run  python disclosures.py build  to build or update it (incremental; the first run takes a while).
"""
from __future__ import annotations

import csv
import http.cookiejar
import io
import json
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

CACHE = Path(__file__).parent / ".cache"
DB = CACHE / "disclosures.db"
BROWSER = {"User-Agent": "Mozilla/5.0 (Kash personal research)"}
FIRST_YEAR = 2022
SEC_QUARTERS = 12
LATE_DAYS = 45          # STOCK Act: report within 30 days of notice and no later than 45 days after the trade

SCHEMA = """
CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY, name TEXT, grp TEXT, role TEXT, party TEXT, state TEXT, committees TEXT);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY, person TEXT, ticker TEXT, asset TEXT, asset_type TEXT, side TEXT,
  trade_date TEXT, filed_date TEXT, low REAL, high REAL, amount REAL, owner TEXT, planned INTEGER, url TEXT, doc TEXT,
  r_trade REAL, b_trade REAL, r_filed REAL, b_filed REAL, r90 REAL, b90 REAL, r365 REAL, b365 REAL, overlap INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS docs(doc TEXT PRIMARY KEY, source TEXT, status TEXT, n INTEGER);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS t_person ON trades(person);
CREATE INDEX IF NOT EXISTS t_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS t_filed ON trades(filed_date);
"""


def db() -> sqlite3.Connection:
    CACHE.mkdir(exist_ok=True)
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def _get(url: str, headers=None, tries: int = 3) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or BROWSER), timeout=60) as f:
                return f.read()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


# ------------------------------------------------------------------ small parsers

def us_date(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def parse_amount(text: str) -> tuple[float | None, float | None]:
    """'$1,001 - $15,000' -> (1001, 15000); 'Over $50,000,000' -> (50000000, 50000000)."""
    nums = [float(x.replace(",", "")) for x in re.findall(r"\$\s*([\d,]+)", text or "")]
    if not nums:
        return None, None
    return nums[0], nums[1] if len(nums) > 1 else nums[0]


def mid(low, high):
    return None if low is None else (low + high) / 2


COMPANY = re.compile(r"\b(LLC|L\.?L\.?C|LP|L\.P|INC|CORP|TRUST|FUND|CAPITAL|HOLDINGS|PARTNERS|LTD|GROUP|MANAGEMENT|BANK|FOUNDATION|ADVISORS|VENTURES|PLC|CO)\b\.?", re.I)


def person_name(raw: str) -> str:
    """SEC names read 'LAST FIRST M'; show 'First M Last'. Companies and funds keep their order."""
    raw = re.sub(r"\s+", " ", (raw or "").strip())
    if COMPANY.search(raw):
        return raw.title() if raw.isupper() else raw
    parts = raw.split(" ")
    if len(parts) < 2:
        return raw.title()
    return " ".join(p.capitalize() if p.isupper() or p.islower() else p for p in parts[1:] + parts[:1])


def strip_title(n: str) -> str:
    n = re.sub(r"^(Hon\.?|Mr\.?|Mrs\.?|Ms\.?|Dr\.?)\s+", "", (n or "").strip())
    return re.sub(r"\s+(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)(?=\s|$)", "", n)      # "Mark Dr" -> "Mark"


def norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


# ------------------------------------------------------------------ House

HOUSE_ROW = re.compile(
    r"(?P<asset>[^\n].*?\[(?P<type>[A-Z]{2,3})\])\s*\n?\s*(?:[A-Z]\s*:[^\n]*\n)?\s*"
    r"(?P<tx>P|S|E)(?:\s*\((?P<partial>partial)\))?\s+(?P<d>\d{2}/\d{2}/\d{4})\s*(?P<n>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<amt>Over \$[\d,]+|\$[\d,]+\s*-?\s*(?:\n\s*)?(?:\$[\d,]+)?)", re.S)
OWNER = {"SP": "Spouse", "JT": "Joint", "DC": "Child"}


def clean_house_asset(asset: str) -> tuple[str, str | None]:
    """Older PDFs run page headers and the account name into the next asset. Keep the real asset; also recover its owner code."""
    if not re.search(r"O\s*F\s*:|ePort|Clerk of the House|ilInG|\bS\s*TATuS", asset, re.I):
        return asset, None
    codes = list(re.finditer(r"\b(SP|JT|DC)\s", asset))
    if codes:
        return asset[codes[-1].end():].strip(), OWNER[codes[-1][1]]
    return asset.rsplit(":", 1)[-1].strip(), None


def parse_house_ptr(text: str) -> list[dict]:
    """Turn the text of a House PTR PDF into trades. Scanned paper filings have no text and return []."""
    text = text.replace("\x00", "")
    start = text.find("Gains >")
    body = text[start:] if start >= 0 else text
    body = re.sub(r"^Gains >\s*\n?\$200\?\s*\n?", "", body)
    # footnotes and repeated page headers sit between rows ("F S: New", "S O: account name", "D: description", "C: comment")
    body = "\n".join(l for l in body.split("\n") if not re.match(r"\s*(F\s*S|S\s*O|D|C|L)\s*:", l)
                     and not re.match(r"\s*(ID Owner Asset|Type$|Date Notification|Date$|Amount Cap|Gains >|\$200\?|Filing ID|Clerk of the House|\* For the complete)", l))
    out = []
    for m in HOUSE_ROW.finditer(body):
        asset = re.sub(r"\s+", " ", m["asset"]).strip()
        owner = "Self"
        asset, recovered = clean_house_asset(asset)
        first = re.match(r"(SP|JT|DC)\s+(.*)", asset)
        if first:
            owner, asset = OWNER[first[1]], first[2]
        owner = recovered or owner
        tick = re.search(r"\(([A-Z][A-Z.\-]{0,5})\)\s*\[", asset)
        low, high = parse_amount(m["amt"])
        out.append({"asset": asset, "ticker": tick[1].replace(".", "-") if tick else None, "asset_type": m["type"],
                    "side": {"P": "buy", "S": "sell", "E": "exchange"}[m["tx"]], "trade_date": us_date(m["d"]),
                    "low": low, "high": high, "owner": owner})
    return out


def _house_index(year: int) -> list[dict]:
    b = _get(f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip")
    z = zipfile.ZipFile(io.BytesIO(b))
    xml = z.read(f"{year}FD.xml").decode("utf-8", "ignore")
    out = []
    for item in re.findall(r"<Member>(.*?)</Member>", xml, re.S):
        g = lambda tag: (re.search(f"<{tag}>(.*?)</{tag}>", item, re.S) or [None, ""])[1].strip()
        if g("FilingType") == "P":
            out.append({"last": g("Last"), "first": g("First"), "state": g("StateDst"), "year": g("Year"), "filed": us_date(g("FilingDate")), "doc": g("DocID")})
    return out


def _fetch_house(item: dict) -> tuple[dict, list[dict] | None, str]:
    import pypdf
    url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{item['year']}/{item['doc']}.pdf"
    try:
        r = pypdf.PdfReader(io.BytesIO(_get(url)))
        text = "\n".join((p.extract_text() or "") for p in r.pages)
    except Exception as e:  # noqa: BLE001
        return item, None, f"error: {e!r}"[:80]
    if len(text.strip()) < 80:
        return item, [], "scanned"
    return item, parse_house_ptr(text), "ok"


def build_house(c, log=print):
    done = {r[0] for r in c.execute("SELECT doc FROM docs WHERE source='house' AND status IN ('ok','scanned')")}
    items = []
    for y in range(FIRST_YEAR, date.today().year + 1):
        try:
            items += _house_index(y)
        except Exception as e:  # noqa: BLE001
            log(f"house index {y} failed: {e!r}")
    todo = [i for i in items if i["doc"] not in done]
    log(f"house: {len(items)} reports, {len(todo)} to fetch")
    with ThreadPoolExecutor(4) as ex:
        for n, (item, rows, status) in enumerate(ex.map(_fetch_house, todo), 1):
            if rows:
                pid = "h:" + norm(item["last"]) + ":" + norm(strip_title(item["first"]).split(" ")[0])
                c.execute("INSERT OR IGNORE INTO people(id,name,grp,role,state) VALUES(?,?,?,?,?)",
                          (pid, f"{strip_title(item['first'])} {item['last']}".strip(), "congress", "U.S. Representative", item["state"]))
                url = f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{item['year']}/{item['doc']}.pdf"
                c.executemany("INSERT INTO trades(person,ticker,asset,asset_type,side,trade_date,filed_date,low,high,amount,owner,planned,url,doc) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                              [(pid, r["ticker"], r["asset"], r["asset_type"], r["side"], r["trade_date"], item["filed"], r["low"], r["high"], mid(r["low"], r["high"]), r["owner"], url, "h" + item["doc"]) for r in rows if r["trade_date"]])
            c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (item["doc"], "house", status, len(rows or [])))
            if n % 100 == 0:
                c.commit(); log(f"house {n}/{len(todo)}")
    c.commit()


# ------------------------------------------------------------------ Senate

SENATE = "https://efdsearch.senate.gov"


def _senate_session():
    """Open a session and tick the site's acknowledgment (accepted by the owner for personal research)."""
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = list(BROWSER.items())
    html_ = op.open(f"{SENATE}/search/home/", timeout=60).read().decode("utf8", "ignore")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html_)[1]
    body = urllib.parse.urlencode({"prohibition_agreement": "1", "csrfmiddlewaretoken": token}).encode()
    req = urllib.request.Request(f"{SENATE}/search/home/", data=body, headers={**BROWSER, "Referer": f"{SENATE}/search/home/"})
    op.open(req, timeout=60).read()
    return op, jar


def parse_senate_ptr(html_: str) -> list[dict]:
    """Rows of the transaction table on a Senate PTR page. Scanned filings have no table and return []."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html_, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", x)).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 8 or not re.match(r"\d{2}/\d{2}/\d{4}", cells[1]):
            continue
        low, high = parse_amount(cells[7])
        kind = cells[6].lower()
        tick = cells[3].strip()
        out.append({"trade_date": us_date(cells[1]), "owner": cells[2].title(), "ticker": None if tick in ("--", "") else tick.replace(".", "-"),
                    "asset": cells[4], "asset_type": cells[5], "side": "buy" if "purchase" in kind else "sell" if "sale" in kind else "exchange",
                    "low": low, "high": high})
    return out


def build_senate(c, log=print):
    op, jar = _senate_session()
    csrf = next(x.value for x in jar if x.name in ("csrftoken", "csrf"))
    rows, start = [], 0
    while True:
        form = urllib.parse.urlencode({"start": start, "length": 100, "report_types": "[11]", "filer_types": "[1]", "submitted_start_date": f"01/01/{FIRST_YEAR} 00:00:00",
                                       "submitted_end_date": "", "candidate_state": "", "senator_state": "", "office_id": "", "first_name": "", "last_name": "", "csrfmiddlewaretoken": csrf,
                                       "draw": 1, "order[0][column]": 4, "order[0][dir]": "desc"}).encode()
        req = urllib.request.Request(f"{SENATE}/search/report/data/", data=form, headers={**BROWSER, "Referer": f"{SENATE}/search/", "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest"})
        d = json.loads(op.open(req, timeout=60).read())
        rows += d["data"]
        start += 100
        if start >= d["recordsFiltered"] or not d["data"]:
            break
        time.sleep(0.4)
    done = {r[0] for r in c.execute("SELECT doc FROM docs WHERE source='senate' AND status IN ('ok','scanned')")}
    log(f"senate: {len(rows)} reports, {len(rows) - len(done)} to fetch")
    n = 0
    for first, last, _filer, link, filed in rows:
        m = re.search(r'href="(/search/view/([^/]+)/([^/"]+)/)"', link)
        if not m or m[3] in done:
            continue
        n += 1
        if m[2] != "ptr":
            c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (m[3], "senate", "scanned", 0))
            continue
        try:
            trades = parse_senate_ptr(op.open(SENATE + m[1], timeout=60).read().decode("utf8", "ignore"))
        except Exception as e:  # noqa: BLE001
            log(f"senate doc failed: {e!r}"[:100]); time.sleep(2); continue
        first_clean, last_clean = strip_title(first).strip(), last.strip().split(",")[0]
        pid = "s:" + norm(last_clean) + ":" + norm(first_clean.split(" ")[0])
        if trades:
            c.execute("INSERT OR IGNORE INTO people(id,name,grp,role) VALUES(?,?,?,?)", (pid, f"{first_clean} {last_clean}", "congress", "U.S. Senator"))
            f = us_date(filed)
            c.executemany("INSERT INTO trades(person,ticker,asset,asset_type,side,trade_date,filed_date,low,high,amount,owner,planned,url,doc) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                          [(pid, t["ticker"], t["asset"], t["asset_type"], t["side"], t["trade_date"], f, t["low"], t["high"], mid(t["low"], t["high"]), t["owner"], SENATE + m[1], "s" + m[3]) for t in trades if t["trade_date"]])
        c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (m[3], "senate", "ok", len(trades)))
        if n % 50 == 0:
            c.commit(); log(f"senate {n}")
        time.sleep(0.25)
    c.commit()


# ------------------------------------------------------------------ SEC Forms 3/4/5

def _quarters(n: int) -> list[str]:
    y, q = date.today().year, (date.today().month - 1) // 3 + 1
    out = []
    for _ in range(n):
        q -= 1
        if q == 0:
            y, q = y - 1, 4
        out.append(f"{y}q{q}")
    return out


def _tsv(z: zipfile.ZipFile, name: str):
    return csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8", errors="ignore"), delimiter="\t", quoting=csv.QUOTE_NONE)


def _dmy(s: str) -> str | None:
    try:
        return datetime.strptime((s or "").strip().title(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def sec_rows(z: zipfile.ZipFile) -> tuple[dict, list[dict]]:
    """Open-market buys (P) and sells (S) of a data-set zip -> (people, trades). Grants and exercises aren't trading decisions."""
    subs = {r["ACCESSION_NUMBER"]: r for r in _tsv(z, "SUBMISSION.tsv") if r["DOCUMENT_TYPE"] in ("4", "4/A", "5", "5/A")}
    owners: dict[str, dict] = {}
    for r in _tsv(z, "REPORTINGOWNER.tsv"):
        owners.setdefault(r["ACCESSION_NUMBER"], r)     # joint filers report one trade: credit the first owner only
    people, trades = {}, []
    for r in _tsv(z, "NONDERIV_TRANS.tsv"):
        if r["TRANS_CODE"] not in ("P", "S"):
            continue
        a, s, o = r["ACCESSION_NUMBER"], subs.get(r["ACCESSION_NUMBER"]), owners.get(r["ACCESSION_NUMBER"])
        if not s or not o:
            continue
        try:
            shares, price = float(r["TRANS_SHARES"]), float(r["TRANS_PRICEPERSHARE"] or 0)
        except ValueError:
            continue
        ticker = (s["ISSUERTRADINGSYMBOL"] or "").strip().upper().replace(".", "-")
        if not re.fullmatch(r"[A-Z][A-Z0-9\-]{0,5}", ticker) or ticker in ("NONE", "NA") or shares <= 0 or price <= 0:
            continue
        pid = "i:" + o["RPTOWNERCIK"].lstrip("0")
        rel = o["RPTOWNER_RELATIONSHIP"] or ""
        role = o["RPTOWNER_TITLE"] or ("Director" if "Director" in rel else "10% owner" if "TenPercent" in rel else "Insider")
        people[pid] = (pid, person_name(o["RPTOWNERNAME"]), "insider", f"{role}, {s['ISSUERNAME'].title()}")
        amount = shares * price
        cik = s["ISSUERCIK"].lstrip("0")
        trades.append({"person": pid, "ticker": ticker, "asset": s["ISSUERNAME"].title(), "asset_type": "ST", "side": "buy" if r["TRANS_CODE"] == "P" else "sell",
                       "trade_date": _dmy(r["TRANS_DATE"]), "filed_date": _dmy(s["FILING_DATE"]), "low": amount, "high": amount, "amount": amount, "owner": "Self" if r["DIRECT_INDIRECT_OWNERSHIP"] == "D" else "Indirect",
                       "planned": 1 if s.get("AFF10B5ONE") == "1" else 0, "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{a.replace('-', '')}/", "doc": a})
    return people, [t for t in trades if t["trade_date"] and t["filed_date"]]


def build_sec(c, log=print):
    import sec as sec_mod
    ua = {"User-Agent": sec_mod._user_agent()}
    page = _get("https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets", ua).decode("utf8", "ignore")
    links = {re.search(r"(\d{4}q\d)_form345", l)[1]: l for l in re.findall(r'href="([^"]+form345[^"]*\.zip)"', page, re.I)}
    done = {r[0] for r in c.execute("SELECT doc FROM docs WHERE source='sec'")}
    for q in _quarters(SEC_QUARTERS):
        if q in done or q not in links:
            continue
        u = links[q] if links[q].startswith("http") else "https://www.sec.gov" + links[q]
        z = zipfile.ZipFile(io.BytesIO(_get(u, ua)))
        people, trades = sec_rows(z)
        c.executemany("INSERT OR REPLACE INTO people(id,name,grp,role) VALUES(?,?,?,?)", people.values())
        c.executemany("INSERT INTO trades(person,ticker,asset,asset_type,side,trade_date,filed_date,low,high,amount,owner,planned,url,doc) VALUES(:person,:ticker,:asset,:asset_type,:side,:trade_date,:filed_date,:low,:high,:amount,:owner,:planned,:url,:doc)", trades)
        c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (q, "sec", "ok", len(trades)))
        c.commit()
        log(f"sec {q}: {len(trades)} trades")
        time.sleep(0.3)


# ------------------------------------------------------------------ executive branch (OGE 278-T PDFs)

OGE = "https://extapps2.oge.gov/201/Presiden.nsf/PAS+Index/"
WH = "https://www.whitehouse.gov/wp-content/uploads/"
# OGE's index has no machine-readable links, so these are the President's reports located by hand. Partial: the May 2026
# reports covering Q1 2026 aren't in this list. Add a URL here and rebuild to include another one.
TRUMP_REPORTS = [
    ("2025-08-12", OGE + "E9C024E25C9E4B2085258CEB006E7E23/$FILE/Donald-J-Trump-08.12.2025-278T.pdf"),
    ("2025-08-12", OGE + "024D876AAE518C5085258CEB006E7E2E/$FILE/Donald-J-Trump-08.12.2025-278T(2).pdf"),
    ("2025-08-12", OGE + "5315A095A2EE1B9185258CEB006E7E36/$FILE/Donald-J-Trump-08.12.2025-278T(3).pdf"),
    ("2025-09-03", WH + "2025/09/President-Donald-J.-Trump-Periodic-Transaction-Report-9.3.25-4.pdf"),
    ("2025-10-17", OGE + "AA799A2729B4D1BE85258D430031A320/$FILE/Donald J. Trump 10.17.2025 278-T.pdf"),
    ("2025-10-20", OGE + "2C89623DC721F62585258D430031A32A/$FILE/Donald J. Trump 10.20.2025 278-T.pdf"),
    ("2025-10-20", OGE + "18353894FE440B3685258D430031A337/$FILE/Donald J. Trump 10.20.2025 278-T (2).pdf"),
    ("2025-11-14", WH + "2025/11/President-Donald-J.-Trump-Periodic-Transaction-Report-11.14.25.pdf"),
    ("2026-01-14", WH + "2026/01/President-Donald-J.-Trump-Periodic-Transaction-Report-1.14.2026-.pdf"),
    ("2026-02-26", OGE + "BCF60D94B8F1E59285258DB000347F61/$FILE/Donald J. Trump 2.26.2026 278-T (2).pdf"),
]
OGE_ROW = re.compile(r"^(?P<d>.+?)\s+(?P<t>purchase|sale)\s*(?:\((?:partial|full)\))?\s+(?P<date>\d{1,2}/\d{1,2}[/1]\d{2,4})\s*$", re.I)
OGE_AMT = re.compile(r"^(Over\s*)?\$[\d.,\s]+(?:[-•–]\s*\$[\d.,\s]+)?$", re.I)


def _ocr_date(text: str) -> str | None:
    """'1/2812025' -> 2025-01-28 (scanners read a slash as a 1)."""
    m = re.match(r"(\d{1,2})/(\d{1,2})[/1](\d{4}|\d{2})$", text)
    if not m:
        return None
    y = int(m[3]) if len(m[3]) == 4 else 2000 + int(m[3])
    try:
        return date(y, int(m[1]), int(m[2])).isoformat()
    except ValueError:
        return None


def _ocr_amount(text: str) -> tuple[float | None, float | None]:
    if text.lower().startswith("over"):
        v = float(re.sub(r"\D", "", text) or 0)
        return v, v
    parts = [re.sub(r"\D", "", p) for p in re.split(r"[-•–]", text) if re.search(r"\d", p)]
    return (float(parts[0]), float(parts[-1])) if parts else (None, None)


def parse_oge_page(text: str) -> list[dict]:
    """One page of a Form 278-T scan. Rows come first and their amounts follow as a block in the same order;
    if the counts don't match, the amounts are left blank rather than risk pairing them wrongly."""
    rows, amounts = [], []
    for line in text.replace("§", "$").splitlines():
        line = line.strip()
        m = OGE_ROW.match(line)
        if m:
            when = _ocr_date(m["date"])
            if when:
                rows.append({"asset": re.sub(r"\s+", " ", m["d"]), "side": "buy" if m["t"].lower() == "purchase" else "sell", "trade_date": when})
        elif OGE_AMT.match(line):
            amounts.append(_ocr_amount(line))
    if len(amounts) == len(rows):
        for r, (low, high) in zip(rows, amounts):
            r.update(low=low, high=high)
    else:
        for r in rows:
            r.update(low=None, high=None)
    return rows


def build_oge(c, resolve, log=print):
    import pypdf
    done = {r[0] for r in c.execute("SELECT doc FROM docs WHERE source='oge'")}
    pid = "e:donaldjtrump"
    c.execute("INSERT OR IGNORE INTO people(id,name,grp,role) VALUES(?,?,?,?)", (pid, "Donald J. Trump", "executive", "President of the United States"))
    seen = {(r["asset"], r["trade_date"], r["side"]) for r in c.execute("SELECT asset,trade_date,side FROM trades WHERE person=?", (pid,))}
    for filed, url in TRUMP_REPORTS:
        if url in done:
            continue
        safe = urllib.parse.quote(url, safe=":/$()%-.")
        try:
            pages = pypdf.PdfReader(io.BytesIO(_get(safe))).pages
            rows = [x for p in pages for x in parse_oge_page(p.extract_text() or "")]
        except Exception as e:  # noqa: BLE001
            log(f"oge failed {url[-40:]}: {e!r}"[:140]); continue
        added = 0
        for t in rows:
            k = (t["asset"], t["trade_date"], t["side"])
            if k in seen:
                continue
            seen.add(k); added += 1
            tk = resolve(t["asset"])
            c.execute("INSERT INTO trades(person,ticker,asset,asset_type,side,trade_date,filed_date,low,high,amount,owner,planned,url,doc) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                      (pid, tk, t["asset"], "ST" if tk else "OT", t["side"], t["trade_date"], filed, t["low"], t["high"], mid(t["low"], t["high"]), "Self", safe, url[-60:]))
        c.execute("INSERT OR REPLACE INTO docs VALUES(?,?,?,?)", (url, "oge", "ok", added))
        c.commit()
        log(f"oge {filed}: {len(rows)} rows, {added} new")
        time.sleep(0.3)


def name_resolver():
    """Match a company name in a filing to its ticker using the screener universe (exact normalized match)."""
    import market
    drop = re.compile(r"\b(inc|corp|corporation|co|company|ltd|plc|the|class [a-z]|common stock|com|new|holdings?|group)\b")
    key = lambda s: re.sub(r"[^a-z0-9]", "", drop.sub(" ", (s or "").lower()))
    names = {key(q.get("name")): s for s, q in market.quotes_all().items() if q.get("name")}
    return lambda text: names.get(key(text))


# ------------------------------------------------------------------ legislators: party, state, committees

COMMITTEE_SECTORS = {
    "HSAS": ["Industrials"], "SSAS": ["Industrials"],                                   # Armed Services
    "HSBA": ["Financials"], "SSBK": ["Financials"],                                     # Financial Services / Banking
    "HSIF": ["Energy", "Health Care", "Communication Services", "Utilities"],           # Energy & Commerce
    "SSEG": ["Energy", "Utilities"], "SSHR": ["Health Care"], "SSFI": ["Health Care"],  # Energy; HELP; Finance
    "SSCM": ["Communication Services", "Industrials"], "HSAG": ["Consumer Staples", "Materials"], "SSAF": ["Consumer Staples", "Materials"],
    "HSPW": ["Industrials"], "HSSY": ["Information Technology"],
}


def load_legislators(c, log=print):
    try:
        cur = json.loads(_get("https://unitedstates.github.io/congress-legislators/legislators-current.json"))
        members = json.loads(_get("https://unitedstates.github.io/congress-legislators/committee-membership-current.json"))
        names = {x["thomas_id"] + "": x["name"] for x in json.loads(_get("https://unitedstates.github.io/congress-legislators/committees-current.json")) if x.get("thomas_id")}
    except Exception as e:  # noqa: BLE001
        log(f"legislators failed: {e!r}"); return
    by_bio: dict[str, list[str]] = {}
    for code, rows in members.items():
        if len(code) == 4:
            for r in rows:
                by_bio.setdefault(r["bioguide"], []).append(code)
    for x in cur:
        term = x["terms"][-1]
        prefix = "h:" if term["type"] == "rep" else "s:"
        last, first = norm(x["name"]["last"]), norm(x["name"]["first"])
        nick = norm(x["name"].get("nickname", ""))
        codes = by_bio.get(x["id"]["bioguide"], [])
        info = (term.get("party"), json.dumps([{"code": k, "name": names.get(k, k)} for k in codes]))
        same_last = sum(1 for y in cur if y["terms"][-1]["type"] == term["type"] and norm(y["name"]["last"]) == last)
        for fn in {first, nick} - {""}:
            c.execute("UPDATE people SET party=?, committees=? WHERE id=?", (*info, f"{prefix}{last}:{fn}"))
        # a filing's "Thomas H" is the roster's "Tommy": allow that only when surname, first initial and (for the House) state
        # all agree. Surname alone once labeled former Rep. Mark Green (R-TN) with current Rep. Al Green's party.
        pattern = f"{prefix}{last}:{first[0]}%"
        if same_last == 1:
            c.execute("UPDATE people SET party=?, committees=? WHERE id LIKE ? AND party IS NULL AND (state IS NULL OR state LIKE ?)", (*info, pattern, term["state"] + "%"))
        c.execute("UPDATE people SET state=? WHERE id LIKE ? AND state IS NULL", (term["state"], pattern))
    c.commit()


def clean_data(c):
    """Remove rows whose dates can't be right (typos in filings) and non-tickers."""
    today = date.today().isoformat()
    c.execute("DELETE FROM trades WHERE trade_date < '2005-01-01' OR trade_date > ? OR (filed_date IS NOT NULL AND trade_date > filed_date)", (today,))
    c.execute("DELETE FROM trades WHERE ticker IN ('N/A','NA','NONE')")
    c.commit()


def mark_overlap(c):
    """Flag trades in a sector overseen by one of the member's committees. Uses GICS sectors from the index lists."""
    import market
    sector = {s: m.get("sector") for s, m in market._membership().items()}
    c.execute("UPDATE trades SET overlap=0")
    for p in c.execute("SELECT id, committees FROM people WHERE grp='congress' AND committees IS NOT NULL").fetchall():
        watched = {s for x in json.loads(p["committees"]) for s in COMMITTEE_SECTORS.get(x["code"], [])}
        if not watched:
            continue
        ids = [(t["id"],) for t in c.execute("SELECT id, ticker FROM trades WHERE person=? AND ticker IS NOT NULL", (p["id"],)) if sector.get(t["ticker"]) in watched]
        c.executemany("UPDATE trades SET overlap=1 WHERE id=?", ids)
    c.commit()


# ------------------------------------------------------------------ returns

def forward_returns(trade_day: np.datetime64, dates: np.ndarray, px: np.ndarray, days: int | None = None):
    """Return from the close on/after a day to `days` later, or to today when days is None. None if not yet known."""
    i = int(np.searchsorted(dates, trade_day))
    if i >= len(px):
        return None
    if days is None:
        return float(px[-1] / px[i] - 1)
    target = trade_day + np.timedelta64(days, "D")
    if target > dates[-1]:
        return None
    return float(px[min(np.searchsorted(dates, target), len(px) - 1)] / px[i] - 1)


def wait_for_yahoo(log=print, every: int = 600, tries: int = 36) -> bool:
    """After a rate limit, make one small request every 10 minutes until Yahoo answers again (up to 6 hours)."""
    import yfinance as yf
    for i in range(tries):
        try:
            if yf.download("SPY", period="5d", progress=False, auto_adjust=True, threads=False)["Close"].dropna().shape[0]:
                return True
        except Exception:  # noqa: BLE001
            pass
        log(f"Yahoo is rate limiting; waiting ({i + 1}/{tries})")
        time.sleep(every)
    return False


def fetch_prices(tickers: list[str], start: str, log=print) -> pd.DataFrame:
    """Daily total-return prices, gently: one request at a time, saved per batch so an interruption can resume."""
    import yfinance as yf
    folder = CACHE / "prices"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    for old in folder.glob("*.pkl"):
        if not old.name.startswith(stamp):
            old.unlink()
    frames = []
    for b, i in enumerate(range(0, len(tickers), 100)):
        chunk, path = tickers[i:i + 100], folder / f"{stamp}_{b}_{len(tickers)}.pkl"
        if path.exists():
            frames.append(pd.read_pickle(path))
            continue
        d = yf.download(chunk, start=start, progress=False, auto_adjust=True, threads=False)["Close"]
        d = d if isinstance(d, pd.DataFrame) else d.to_frame(chunk[0])
        if d.dropna(how="all").empty:     # the whole batch failed: that's a rate limit, not 100 delisted stocks
            if not wait_for_yahoo(log):
                raise RuntimeError("Yahoo kept rate limiting")
            d = yf.download(chunk, start=start, progress=False, auto_adjust=True, threads=False)["Close"]
            d = d if isinstance(d, pd.DataFrame) else d.to_frame(chunk[0])
        d.to_pickle(path)
        frames.append(d)
        log(f"prices {min(i + 100, len(tickers))}/{len(tickers)}")
        time.sleep(3)
    return pd.concat(frames, axis=1)


def clear_option_returns(c):
    """An option's payoff isn't the stock's return, so scoring it as one would mislead."""
    c.execute("""UPDATE trades SET r_trade=NULL,b_trade=NULL,r_filed=NULL,b_filed=NULL,r90=NULL,b90=NULL,r365=NULL,b365=NULL
                 WHERE asset_type IN ('OP','Stock Option') OR asset LIKE '%Option Type%'""")


def compute_returns(c, log=print):
    import market
    if not wait_for_yahoo(log):
        raise RuntimeError("Yahoo kept rate limiting")
    universe = set(market.quotes_all())
    congress = {r[0] for r in c.execute("SELECT DISTINCT t.ticker FROM trades t JOIN people p ON p.id=t.person WHERE t.ticker IS NOT NULL AND p.grp!='insider'")}
    insider = {r[0] for r in c.execute("SELECT DISTINCT t.ticker FROM trades t JOIN people p ON p.id=t.person WHERE t.ticker IS NOT NULL AND p.grp='insider' AND t.side='buy'")}
    tickers = sorted((congress | (insider & universe)) | {"SPY"})
    log(f"prices for {len(tickers)} tickers")
    px = fetch_prices(tickers, f"{FIRST_YEAR}-01-01", log)
    px.index = pd.to_datetime(px.index).tz_localize(None) if getattr(px.index, "tz", None) is not None else pd.to_datetime(px.index)
    spy = px["SPY"].dropna()      # auto_adjust=True, so dividends are included on both sides of the comparison
    if spy.empty:
        raise RuntimeError("no S&P 500 prices; try again later")
    bd, bp = spy.index.values.astype("datetime64[ns]"), spy.values.astype(float)
    arrays = {}
    for t in px.columns:
        s = px[t].dropna()
        if len(s):
            arrays[t] = (s.index.values.astype("datetime64[ns]"), s.values.astype(float))
    upd = []
    for r in c.execute("SELECT id,ticker,trade_date,filed_date FROM trades WHERE ticker IS NOT NULL").fetchall():
        a = arrays.get(r["ticker"])
        if not a:
            continue
        td, fd = np.datetime64(r["trade_date"]), np.datetime64(r["filed_date"] or r["trade_date"])
        if td < a[0][0]:
            continue
        upd.append((forward_returns(td, *a), forward_returns(td, bd, bp), forward_returns(fd, *a), forward_returns(fd, bd, bp),
                    forward_returns(fd, *a, 90), forward_returns(fd, bd, bp, 90), forward_returns(fd, *a, 365), forward_returns(fd, bd, bp, 365), r["id"]))
    c.executemany("UPDATE trades SET r_trade=?,b_trade=?,r_filed=?,b_filed=?,r90=?,b90=?,r365=?,b365=? WHERE id=?", upd)
    clear_option_returns(c)
    c.commit()
    log(f"returns for {len(upd)} trades")


def build(log=print):
    c = db()
    for name, fn in (("sec", build_sec), ("house", build_house), ("senate", build_senate)):
        try:
            fn(c, log)
        except Exception as e:  # noqa: BLE001
            log(f"{name} failed: {e!r}")
    try:
        build_oge(c, name_resolver(), log)
    except Exception as e:  # noqa: BLE001
        log(f"oge failed: {e!r}")
    clean_data(c)
    load_legislators(c, log)
    mark_overlap(c)
    try:
        compute_returns(c, log)
        c.execute("INSERT OR REPLACE INTO meta VALUES('built',?)", (date.today().isoformat(),))
    except Exception as e:  # noqa: BLE001  (trades still update; returns are retried on the next run)
        log(f"returns failed: {e!r}")
    c.commit()
    import people
    people.precompute(c)
    c.execute("DELETE FROM meta WHERE k='building'")
    c.commit()
    log("done")


def refresh_if_stale(log=lambda m: None) -> bool:
    """Daily update, run in the background by the server. Skips if already current or another update is in progress."""
    if not DB.exists():
        return False        # the first build is a deliberate, long, manual step
    c = db()
    meta = {r["k"]: r["v"] for r in c.execute("SELECT * FROM meta WHERE k IN ('built','building')")}
    if meta.get("built") == date.today().isoformat() or (meta.get("building") and time.time() - float(meta["building"]) < 6 * 3600):
        return False
    c.execute("INSERT OR REPLACE INTO meta VALUES('building',?)", (str(time.time()),))
    c.commit()
    build(log)
    return True


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "build":
    build(lambda m: print(time.strftime("%H:%M:%S"), m, flush=True))
