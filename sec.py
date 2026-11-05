"""Long-run annual financials from SEC EDGAR (XBRL "company facts").

Returns pandas DataFrames shaped like yfinance's statements (Yahoo-style row names, one column
per fiscal-year-end Timestamp, newest first), so app.py can merge them with Yahoo data.

Only US-GAAP filers (10-K) are covered; foreign IFRS filers return None and the app falls back
to Yahoo. Responses are cached on disk (.cache/) to stay well within SEC's fair-access limits.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
TICKER_TTL = 7 * 86400
FACTS_TTL = 86400
_lock = threading.Lock()


def _user_agent() -> str | None:
    import os
    ua = os.environ.get("SEC_USER_AGENT")
    if ua:
        return ua
    cfg = ROOT / "config.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8")).get("sec_user_agent")
    return None


def _fetch(url: str, cache_file: Path, ttl: int):
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    ua = _user_agent()
    if not ua:
        return None
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Encoding": "gzip"})
    with _lock:  # serialize requests: SEC allows ~10/sec, we stay far below
        with urllib.request.urlopen(req, timeout=30) as f:
            body = f.read()
            if f.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        time.sleep(0.12)
    CACHE.mkdir(exist_ok=True)
    cache_file.write_bytes(body)
    return json.loads(body)


def cik_for(ticker: str) -> int | None:
    m = _fetch("https://www.sec.gov/files/company_tickers.json", CACHE / "company_tickers.json", TICKER_TTL)
    if not m:
        return None
    t = ticker.upper().replace(".", "-")
    for v in m.values():
        if v["ticker"].upper() == t:
            return int(v["cik_str"])
    return None


# ------------------------------------------------------------------ concept mapping
# Yahoo row name -> list of us-gaap concepts, tried in order *per fiscal year*
# (companies switch tags over time, e.g. SalesRevenueNet -> RevenueFromContractWithCustomer...).

FLOW = {
    "Total Revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                      "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                      "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"],
    "Cost Of Revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
                        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "Gross Profit": ["GrossProfit"],
    "Selling General And Administration": ["SellingGeneralAndAdministrativeExpense"],
    "Research And Development": ["ResearchAndDevelopmentExpense",
                                 "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "Operating Expense": ["OperatingExpenses"],
    "Operating Income": ["OperatingIncomeLoss"],
    "Interest Expense": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "Pretax Income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeignAndDomestic"],
    "Tax Provision": ["IncomeTaxExpenseBenefit"],
    "Net Income": ["NetIncomeLoss", "ProfitLoss"],
    "Net Income Common Stockholders": ["NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"],
    "Diluted Average Shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "Basic Average Shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "Diluted EPS": ["EarningsPerShareDiluted"],
    "Basic EPS": ["EarningsPerShareBasic"],
    # cash flow
    "Net Income From Continuing Operations": ["ProfitLoss", "NetIncomeLoss"],
    "Depreciation And Amortization": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                                      "DepreciationAndAmortization"],
    "Stock Based Compensation": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "Operating Cash Flow": ["NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "Investing Cash Flow": ["NetCashProvidedByUsedInInvestingActivities",
                            "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
    "Financing Cash Flow": ["NetCashProvidedByUsedInFinancingActivities",
                            "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
}
# outflows reported as positive "Payments..." in XBRL -> stored negative like Yahoo
OUTFLOW = {
    "Capital Expenditure": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "Purchase Of Business": ["PaymentsToAcquireBusinessesNetOfCashAcquired",
                             "PaymentsToAcquireBusinessesAndInterestInAffiliates"],
    "Cash Dividends Paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "PaymentsOfOrdinaryDividends"],
    "Repurchase Of Capital Stock": ["PaymentsForRepurchaseOfCommonStock"],
}
INSTANT = {
    "Cash And Cash Equivalents": ["CashAndCashEquivalentsAtCarryingValue", "Cash",
                                  "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "Other Short Term Investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                                     "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"],
    "Cash Cash Equivalents And Short Term Investments": ["CashCashEquivalentsAndShortTermInvestments"],
    "Receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                    "AccountsReceivableBilledAndUnbilledNetCurrent"],
    "Inventory": ["InventoryNet"],
    "Current Assets": ["AssetsCurrent"],
    "Net PPE": ["PropertyPlantAndEquipmentNet"],
    "Goodwill": ["Goodwill"],
    "_Intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet"],
    "Total Assets": ["Assets"],
    "Accounts Payable": ["AccountsPayableCurrent"],
    "Current Deferred Revenue": ["ContractWithCustomerLiabilityCurrent", "DeferredRevenueCurrent"],
    "Current Liabilities": ["LiabilitiesCurrent"],
    "Long Term Debt": ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "_LTD_Current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"],
    "_LTD_Total": ["LongTermDebt"],
    "_ShortBorrow": ["ShortTermBorrowings", "CommercialPaper", "DebtCurrent"],
    "_FinLease": ["FinanceLeaseLiability"],
    "Total Liabilities Net Minority Interest": ["Liabilities"],
    "_LiabEquity": ["LiabilitiesAndStockholdersEquity"],
    "_EquityInclNCI": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "Retained Earnings": ["RetainedEarningsAccumulatedDeficit"],
    "Stockholders Equity": ["StockholdersEquity"],
    "Ordinary Shares Number": ["CommonStockSharesOutstanding"],
}

# pieces used only to fill gaps when a company doesn't report the combined line
PARTS = {
    "_Depreciation": ["Depreciation"],
    "_AmortIntang": ["AmortizationOfIntangibleAssets"],
    "_Selling": ["SellingAndMarketingExpense"],
    "_GandA": ["GeneralAndAdministrativeExpense"],
}

ANNUAL_FORMS = ("10-K", "10-K/A", "10-KT")


def _facts_by_end(gaap: dict, concept: str, kind: str, with_filed: bool = False) -> dict:
    """{end-date: value} of annual facts for one concept, latest filing wins (restatements)."""
    c = gaap.get(concept)
    if not c:
        return {}
    out: dict[str, tuple[str, float]] = {}
    for unit, facts in c["units"].items():
        if unit not in ("USD", "shares", "USD/shares"):
            continue
        for f in facts:
            if f.get("form") not in ANNUAL_FORMS:
                continue
            if kind == "flow":
                if "start" not in f:
                    continue
                days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
                if not 340 <= days <= 380:
                    continue
            elif "start" in f:
                continue
            prev = out.get(f["end"])
            if prev is None or f["filed"] > prev[0]:
                out[f["end"]] = (f["filed"], float(f["val"]))
    return {k: v for k, (_, v) in out.items()} if not with_filed else out


# share counts / per-share values: converted to today's split-adjusted basis (like Yahoo's prices)
SHARE_ROWS = {"Diluted Average Shares", "Basic Average Shares", "Ordinary Shares Number"}
PER_SHARE_ROWS = {"Diluted EPS", "Basic EPS"}


def _split_factor(splits, filed: str) -> float:
    """Product of all stock splits after a filing date (values in that filing predate them)."""
    if splits is None or len(splits) == 0:
        return 1.0
    ts = pd.Timestamp(filed)
    idx = splits.index.tz_localize(None) if splits.index.tz is not None else splits.index
    later = splits.values[idx > ts]
    return float(later.prod()) if len(later) else 1.0


def _pick(gaap: dict, concepts: list[str], kind: str, ends: list[str], splits=None, adjust=None) -> dict[str, float]:
    per = [_facts_by_end(gaap, c, kind, with_filed=True) for c in concepts]
    res = {}
    for e in ends:
        for p in per:
            if e in p:
                filed, v = p[e]
                if adjust == "mul":
                    v *= _split_factor(splits, filed)
                elif adjust == "div":
                    v /= _split_factor(splits, filed)
                res[e] = v
                break
    return res


def annual_frames(ticker: str, max_years: int = 15, splits=None):
    """-> (income, balance, cashflow) DataFrames or None if SEC data unavailable."""
    try:
        cik = cik_for(ticker)
        if cik is None:
            return None
        data = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                      CACHE / f"facts_{cik}.json", FACTS_TTL)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    if not data:
        return None
    gaap = data.get("facts", {}).get("us-gaap")
    if not gaap:
        return None

    # fiscal-year ends = annual periods where revenue or net income was reported
    ends = set()
    for c in FLOW["Total Revenue"] + FLOW["Net Income"]:
        ends |= set(_facts_by_end(gaap, c, "flow"))
    if not ends:
        return None
    # SEC 10-Ks include 2 prior years of comparatives; keep only real fiscal year-ends
    # (drop ends within ~300 days of a later end, which come from odd transition periods)
    ends = sorted(ends, reverse=True)
    kept = []
    for e in ends:
        if all(abs((date.fromisoformat(k) - date.fromisoformat(e)).days) > 300 for k in kept):
            kept.append(e)
    ends = kept[:max_years]

    rows: dict[str, dict[str, float]] = {}
    def adj(name):
        return "mul" if name in SHARE_ROWS else "div" if name in PER_SHARE_ROWS else None

    for name, cs in FLOW.items():
        rows[name] = _pick(gaap, cs, "flow", ends, splits, adj(name))
    for name, cs in OUTFLOW.items():
        rows[name] = {k: -abs(v) for k, v in _pick(gaap, cs, "flow", ends).items()}
    for name, cs in INSTANT.items():
        rows[name] = _pick(gaap, cs, "instant", ends, splits, adj(name))
    for name, cs in PARTS.items():
        rows[name] = _pick(gaap, cs, "flow", ends)

    def r(name, e):
        return rows.get(name, {}).get(e)

    # ---- derived rows, computed the same way for every year
    for e in ends:
        rev, cogs = r("Total Revenue", e), r("Cost Of Revenue", e)
        if r("Gross Profit", e) is None and rev is not None and cogs is not None:
            rows["Gross Profit"][e] = rev - cogs
        if r("Depreciation And Amortization", e) is None and r("_Depreciation", e) is not None:
            rows["Depreciation And Amortization"][e] = r("_Depreciation", e) + (r("_AmortIntang", e) or 0)
        if r("Selling General And Administration", e) is None and r("_Selling", e) is not None and r("_GandA", e) is not None:
            rows["Selling General And Administration"][e] = r("_Selling", e) + r("_GandA", e)
        gp, opi = r("Gross Profit", e), r("Operating Income", e)
        if r("Operating Expense", e) is None and gp is not None and opi is not None:
            rows["Operating Expense"][e] = gp - opi
        op, dna = r("Operating Income", e), r("Depreciation And Amortization", e)
        if op is not None and dna is not None:
            rows.setdefault("EBITDA", {})[e] = op + dna
        if op is not None:
            rows.setdefault("EBIT", {})[e] = op
        ocf, capex = r("Operating Cash Flow", e), r("Capital Expenditure", e)
        if ocf is not None:
            rows.setdefault("Free Cash Flow", {})[e] = ocf + (capex or 0)
        cash, sti = r("Cash And Cash Equivalents", e), r("Other Short Term Investments", e)
        if r("Cash Cash Equivalents And Short Term Investments", e) is None and cash is not None:
            rows["Cash Cash Equivalents And Short Term Investments"][e] = cash + (sti or 0)
        gw, intang = r("Goodwill", e), r("_Intangibles", e)
        if gw is not None or intang is not None:
            rows.setdefault("Goodwill And Other Intangible Assets", {})[e] = (gw or 0) + (intang or 0)
        # total debt = long-term (incl. current portion) + short-term borrowings + finance leases
        ltd_total = r("_LTD_Total", e)
        if ltd_total is None and (r("Long Term Debt", e) is not None or r("_LTD_Current", e) is not None):
            ltd_total = (r("Long Term Debt", e) or 0) + (r("_LTD_Current", e) or 0)
        if ltd_total is not None or r("_ShortBorrow", e) is not None:
            rows.setdefault("Total Debt", {})[e] = (ltd_total or 0) + (r("_ShortBorrow", e) or 0) + (r("_FinLease", e) or 0)
        elif r("Total Assets", e) is not None:
            rows.setdefault("Total Debt", {})[e] = 0.0  # no debt tags at all -> debt-free
        if r("Total Liabilities Net Minority Interest", e) is None:
            le, eq = r("_LiabEquity", e), r("_EquityInclNCI", e) or r("Stockholders Equity", e)
            if le is not None and eq is not None:
                rows["Total Liabilities Net Minority Interest"][e] = le - eq
        ca, cl = r("Current Assets", e), r("Current Liabilities", e)
        if ca is not None and cl is not None:
            rows.setdefault("Working Capital", {})[e] = ca - cl
        if r("Stockholders Equity", e) is not None:
            rows.setdefault("Common Stock Equity", {})[e] = r("Stockholders Equity", e)

    cols = [pd.Timestamp(e) for e in ends]

    def frame(names):
        df = pd.DataFrame({n: {pd.Timestamp(k): v for k, v in rows.get(n, {}).items()} for n in names if rows.get(n)}).T
        return df.reindex(columns=cols) if not df.empty else df

    inc_names = list(FLOW)[:16] + ["EBITDA", "EBIT"]
    cf_names = list(FLOW)[16:] + list(OUTFLOW) + ["Free Cash Flow"]
    bal_names = [n for n in INSTANT if not n.startswith("_")] + [
        "Cash Cash Equivalents And Short Term Investments", "Goodwill And Other Intangible Assets",
        "Total Debt", "Working Capital", "Common Stock Equity"]
    return frame(inc_names), frame(dict.fromkeys(bal_names)), frame(cf_names)


# ------------------------------------------------------------------ recent filings

FILINGS_TTL = 6 * 3600
# forms worth showing to an investor (insider Form 4s and fund paperwork would drown these out)
KEY_FORMS = {
    "10-K": "Annual report", "10-Q": "Quarterly report", "8-K": "Major event / news",
    "20-F": "Annual report (foreign company)", "6-K": "Report (foreign company)", "40-F": "Annual report (Canada)",
    "DEF 14A": "Proxy statement (shareholder vote, pay)", "S-1": "Registration (new shares)",
    "10-K/A": "Annual report (amended)", "10-Q/A": "Quarterly report (amended)",
}


def recent_filings(ticker: str, limit: int = 12) -> list[dict]:
    """Most recent key filings with links to the document on sec.gov (works for any SEC filer)."""
    cik = cik_for(ticker)
    if not cik:
        return []
    sub = _fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", CACHE / f"submissions_{cik}.json", FILINGS_TTL)
    recent = ((sub or {}).get("filings") or {}).get("recent") or {}
    out = []
    for form, filed, acc, doc, desc in zip(recent.get("form", []), recent.get("filingDate", []),
                                           recent.get("accessionNumber", []), recent.get("primaryDocument", []),
                                           recent.get("primaryDocDescription", [])):
        if form not in KEY_FORMS:
            continue
        folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
        out.append({"form": form, "what": KEY_FORMS[form], "date": filed, "description": desc or None,
                    "url": f"{folder}/{doc}" if doc else folder + "/"})
        if len(out) >= limit:
            break
    return out
