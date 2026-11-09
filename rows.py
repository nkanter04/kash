"""Row definitions for the financial statements.

Each row has a friendly label, a plain-English description (shown on hover), the Yahoo
field name(s) to read, and a display format: money, eps, shares, pct, x (multiple), ratio.
Rows with "calc" are derived from rows above them. Columns are ordered newest -> oldest.
"""


def _lag(i, col, ctx):
    """Index of the comparison period for growth: prior year (annual) or same quarter last year."""
    if col["label"] == "TTM":
        return None
    return i + (4 if ctx["period"] == "quarterly" else 1)


def growth_of(key):
    def f(computed, i, col, ctx):
        j = _lag(i, col, ctx)
        vals = computed.get(key) or []
        if j is None or j >= len(vals):
            return None
        cur, prev = vals[i], vals[j]
        if cur is None or prev is None or prev == 0:
            return None
        return (cur - prev) / abs(prev)
    return f


def ratio_of(num, den):
    def f(computed, i, col, ctx):
        a = (computed.get(num) or [None] * (i + 1))[i]
        b = (computed.get(den) or [None] * (i + 1))[i]
        if a is None or b is None or b == 0:
            return None
        return a / b
    return f


def minus(a, b):
    def f(computed, i, col, ctx):
        x = (computed.get(a) or [None] * (i + 1))[i]
        y = (computed.get(b) or [None] * (i + 1))[i]
        if x is None or y is None:
            return None
        return x - y
    return f


INCOME_ROWS = [
    {"key": "revenue", "label": "Revenue", "src": ["Total Revenue", "Operating Revenue"], "style": "bold",
     "desc": "All the money the company brought in from selling its products and services."},
    {"key": "revenue_growth", "label": "Revenue Growth", "calc": growth_of("revenue"), "fmt": "growth",
     "desc": "How much sales grew compared with the same period a year earlier."},
    {"key": "cogs", "label": "Cost of Revenue", "src": ["Cost Of Revenue", "Reconciled Cost Of Revenue"],
     "desc": "The direct cost of making the product or delivering the service (materials, labor, etc.)."},
    {"key": "gross_profit", "label": "Gross Profit", "src": "Gross Profit", "style": "bold",
     "desc": "Revenue minus the direct cost of sales. What's left to pay for everything else."},
    {"key": "sga", "label": "Selling, General & Admin", "src": "Selling General And Administration",
     "desc": "Overhead: salaries for sales and office staff, marketing, rent and other running costs."},
    {"key": "rnd", "label": "Research & Development", "src": "Research And Development",
     "desc": "Spending on inventing and improving products."},
    {"key": "opex", "label": "Operating Expenses", "src": "Operating Expense",
     "desc": "Total day-to-day costs of running the business, beyond the direct cost of sales."},
    {"key": "op_income", "label": "Operating Income", "src": ["Operating Income", "Total Operating Income As Reported"], "style": "bold",
     "desc": "Profit from the core business, before interest and taxes. A good read on how well the business itself performs."},
    {"key": "interest", "label": "Interest Expense", "src": ["Interest Expense", "Interest Expense Non Operating"], "negate": True,
     "desc": "What the company paid in interest on its borrowings."},
    {"key": "pretax", "label": "Pretax Income", "src": "Pretax Income",
     "desc": "Profit after all expenses and interest, but before income taxes."},
    {"key": "tax", "label": "Income Tax", "src": "Tax Provision",
     "desc": "Income taxes owed on the period's profit."},
    {"key": "net_income", "label": "Net Income", "src": ["Net Income Common Stockholders", "Net Income"], "style": "bold",
     "desc": "The bottom line: profit left for shareholders after every expense, interest and tax."},
    {"key": "net_income_growth", "label": "Net Income Growth", "calc": growth_of("net_income"), "fmt": "growth",
     "desc": "How much the bottom line grew versus a year earlier."},
    {"key": "shares", "label": "Shares Outstanding (Diluted)", "src": ["Diluted Average Shares", "Basic Average Shares"], "fmt": "shares",
     "desc": "Number of shares the profit is split across, including options that could become shares. Falling = buybacks."},
    {"key": "shares_change", "label": "Shares Change", "calc": growth_of("shares"), "fmt": "growth", "invert": True,
     "desc": "Change in share count. Negative means the company bought back stock, giving each share a bigger slice."},
    {"key": "eps", "label": "EPS (Diluted)", "src": ["Diluted EPS", "Basic EPS"], "fmt": "eps", "style": "bold",
     "desc": "Earnings per share: net income divided by shares. The profit that belongs to each share you own."},
    {"key": "eps_growth", "label": "EPS Growth", "calc": growth_of("eps"), "fmt": "growth",
     "desc": "How fast per-share profit is growing."},
    {"key": "gross_margin", "label": "Gross Margin", "calc": ratio_of("gross_profit", "revenue"), "fmt": "pct", "style": "sub",
     "desc": "Gross profit as a share of revenue. Higher means more pricing power."},
    {"key": "op_margin", "label": "Operating Margin", "calc": ratio_of("op_income", "revenue"), "fmt": "pct", "style": "sub",
     "desc": "Operating income as a share of revenue: how much of each sales dollar the core business keeps."},
    {"key": "net_margin", "label": "Profit Margin", "calc": ratio_of("net_income", "revenue"), "fmt": "pct", "style": "sub",
     "desc": "Net income as a share of revenue: cents of profit per dollar of sales."},
    {"key": "ebitda", "label": "EBITDA", "src": ["EBITDA", "Normalized EBITDA"],
     "desc": "Earnings before interest, taxes, depreciation and amortization. A rough proxy for operating cash profit."},
    {"key": "ebitda_margin", "label": "EBITDA Margin", "calc": ratio_of("ebitda", "revenue"), "fmt": "pct", "style": "sub",
     "desc": "EBITDA as a share of revenue."},
]

BALANCE_ROWS = [
    {"key": "cash", "label": "Cash & Equivalents", "src": "Cash And Cash Equivalents",
     "desc": "Money in the bank and very short-term, very safe investments."},
    {"key": "sti", "label": "Short-Term Investments", "src": "Other Short Term Investments",
     "desc": "Investments that can be turned into cash within a year."},
    {"key": "cash_sti", "label": "Cash & Short-Term Investments", "src": ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"], "style": "bold",
     "desc": "All the readily available cash the company has."},
    {"key": "receivables", "label": "Receivables", "src": ["Receivables", "Accounts Receivable"],
     "desc": "Money customers owe the company for things already delivered."},
    {"key": "inventory", "label": "Inventory", "src": "Inventory",
     "desc": "Products and materials on hand, waiting to be sold."},
    {"key": "current_assets", "label": "Total Current Assets", "src": "Current Assets", "style": "bold",
     "desc": "Everything the company expects to turn into cash within a year."},
    {"key": "ppe", "label": "Property, Plant & Equipment", "src": "Net PPE",
     "desc": "Buildings, machinery, equipment and land, after depreciation."},
    {"key": "goodwill", "label": "Goodwill & Intangibles", "src": ["Goodwill And Other Intangible Assets", "Goodwill"],
     "desc": "The premium paid for acquired companies plus things like patents and brands."},
    {"key": "total_assets", "label": "Total Assets", "src": "Total Assets", "style": "bold",
     "desc": "Everything the company owns."},
    {"key": "payables", "label": "Accounts Payable", "src": ["Accounts Payable", "Payables"],
     "desc": "Bills the company owes its suppliers."},
    {"key": "deferred_rev", "label": "Deferred Revenue", "src": "Current Deferred Revenue",
     "desc": "Cash customers paid in advance for work not yet delivered."},
    {"key": "current_liab", "label": "Total Current Liabilities", "src": "Current Liabilities", "style": "bold",
     "desc": "Bills and debts due within a year."},
    {"key": "lt_debt", "label": "Long-Term Debt", "src": ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"],
     "desc": "Borrowings due after more than a year."},
    {"key": "total_debt", "label": "Total Debt", "src": "Total Debt", "style": "bold",
     "desc": "All borrowings: bonds, loans and other short- and long-term debt (operating lease obligations not included)."},
    {"key": "total_liab", "label": "Total Liabilities", "src": "Total Liabilities Net Minority Interest", "style": "bold",
     "desc": "Everything the company owes to others."},
    {"key": "retained", "label": "Retained Earnings", "src": "Retained Earnings",
     "desc": "All the profit the company has kept over its lifetime instead of paying out."},
    {"key": "equity", "label": "Shareholders' Equity", "src": ["Stockholders Equity", "Common Stock Equity"], "style": "bold",
     "desc": "What's left for shareholders if every asset were sold and every debt paid: assets minus liabilities."},
    {"key": "net_cash", "label": "Net Cash (Debt)", "calc": minus("cash_sti", "total_debt"), "style": "bold",
     "desc": "Cash minus debt. Positive means the company could pay off all its debt today."},
    {"key": "working_capital", "label": "Working Capital", "src": "Working Capital",
     "desc": "Current assets minus current liabilities: a cushion for paying near-term bills."},
    {"key": "share_count", "label": "Shares Outstanding", "src": ["Ordinary Shares Number", "Share Issued"], "fmt": "shares",
     "desc": "Shares in existence at the end of the period."},
    {"key": "bvps", "label": "Book Value per Share", "calc": ratio_of("equity", "share_count"), "fmt": "eps",
     "desc": "Shareholders' equity divided by shares: the accounting value behind each share."},
]

CASHFLOW_ROWS = [
    {"key": "cf_net_income", "label": "Net Income", "src": ["Net Income From Continuing Operations", "Net Income"],
     "desc": "Profit from the income statement, the starting point for cash flow."},
    {"key": "dna", "label": "Depreciation & Amortization", "src": ["Depreciation And Amortization", "Depreciation Amortization Depletion"],
     "desc": "Non-cash charge for wearing out assets. Added back because no cash actually left."},
    {"key": "sbc", "label": "Stock-Based Compensation", "src": "Stock Based Compensation",
     "desc": "Employee pay in shares instead of cash. Added back, but it dilutes shareholders."},
    {"key": "wc_change", "label": "Change in Working Capital", "src": "Change In Working Capital",
     "desc": "Cash tied up in (negative) or released from (positive) receivables, inventory and payables."},
    {"key": "ocf", "label": "Operating Cash Flow", "src": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], "style": "bold",
     "desc": "Cash actually generated by running the business."},
    {"key": "ocf_growth", "label": "Operating Cash Flow Growth", "calc": growth_of("ocf"), "fmt": "growth",
     "desc": "Change in operating cash flow versus a year earlier."},
    {"key": "capex", "label": "Capital Expenditures", "src": ["Capital Expenditure", "Purchase Of PPE"],
     "desc": "Cash spent on buildings, equipment and other long-lived assets."},
    {"key": "acquisitions", "label": "Acquisitions", "src": ["Purchase Of Business", "Net Business Purchase And Sale"],
     "desc": "Cash spent buying other companies."},
    {"key": "icf", "label": "Investing Cash Flow", "src": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"], "style": "bold",
     "desc": "Net cash spent on (negative) or received from (positive) investments."},
    {"key": "dividends", "label": "Dividends Paid", "src": ["Cash Dividends Paid", "Common Stock Dividend Paid"],
     "desc": "Cash paid out to shareholders as dividends."},
    {"key": "buybacks", "label": "Share Buybacks", "src": ["Repurchase Of Capital Stock", "Common Stock Payments"],
     "desc": "Cash spent buying back the company's own shares."},
    {"key": "debt_net", "label": "Debt Issued (Repaid)", "src": ["Net Issuance Payments Of Debt", "Net Long Term Debt Issuance"],
     "desc": "New borrowing minus debt repaid."},
    {"key": "fcf_fin", "label": "Financing Cash Flow", "src": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"], "style": "bold",
     "desc": "Net cash from (positive) or returned to (negative) lenders and shareholders."},
    {"key": "fcf", "label": "Free Cash Flow", "src": "Free Cash Flow", "style": "bold hl",
     "desc": "Operating cash flow minus capital expenditures: the cash the business truly throws off, free to pay dividends, buy back stock or pay debt."},
    {"key": "fcf_growth", "label": "Free Cash Flow Growth", "calc": growth_of("fcf"), "fmt": "growth",
     "desc": "Change in free cash flow versus a year earlier."},
]


# ---------------------------------------------------------------- ratios
# c = {"price", "inc"(names), "bal"(names), "cf"(names), "annualize"}

def _shares(c):
    return c["bal"](["Ordinary Shares Number", "Share Issued"]) or c["inc"](["Diluted Average Shares", "Basic Average Shares"])


def _mcap(c):
    s = _shares(c)
    return c["price"] * s if c["price"] and s else None


def _flow(c, src, which="inc"):
    v = c[which](src)
    return v * c["annualize"] if v is not None else None


def _div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def _ev(c):
    m = _mcap(c)
    if m is None:
        return None
    debt = c["bal"]("Total Debt") or 0
    cash = c["bal"](["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]) or 0
    return m + debt - cash


def _pos(x):
    return x if x is not None and x > 0 else None


NI = ["Net Income Common Stockholders", "Net Income"]
REV = ["Total Revenue", "Operating Revenue"]
EQ = ["Stockholders Equity", "Common Stock Equity"]


def _roic(c):
    op = _flow(c, ["Operating Income", "Total Operating Income As Reported"])
    pretax, tax = c["inc"]("Pretax Income"), c["inc"]("Tax Provision")
    rate = _div(tax, pretax)
    rate = rate if rate is not None and 0 <= rate <= 0.5 else 0.21
    cash = c["bal"](["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]) or 0
    invested = (c["bal"]("Total Debt") or 0) + (c["bal"](EQ) or 0) - cash
    assets = c["bal"]("Total Assets")
    # almost no invested capital (e.g. negative equity offset by debt) gives absurd results like 800%: show nothing
    if op is None or invested <= 0 or (assets and invested < 0.05 * assets):
        return None
    return op * (1 - rate) / invested


RATIO_ROWS = [
    {"key": "mcap", "label": "Market Cap", "fmt": "money", "style": "bold", "calc": _mcap,
     "desc": "What the whole company is worth on the stock market: share price × shares."},
    {"key": "ev", "label": "Enterprise Value", "fmt": "money", "calc": _ev,
     "desc": "Market cap plus debt minus cash: the price to buy the whole business, debts and all."},
    {"key": "price", "label": "Share Price", "fmt": "eps", "calc": lambda c: c["price"],
     "desc": "Stock price at the end of the period. For foreign listings (ADRs) it is converted to the "
             "reporting currency and to one ordinary share, so it matches the statements."},
    {"key": "pe", "label": "P/E Ratio", "fmt": "x", "style": "bold", "calc": lambda c: _pos(_div(_mcap(c), _pos(_flow(c, NI)))),
     "desc": "Price ÷ earnings. How many dollars you pay for $1 of yearly profit. Lower can mean cheaper."},
    {"key": "ps", "label": "P/S Ratio", "fmt": "x", "calc": lambda c: _div(_mcap(c), _flow(c, REV)),
     "desc": "Price ÷ sales. Useful when a company isn't yet profitable."},
    {"key": "pb", "label": "P/B Ratio", "fmt": "x", "calc": lambda c: _pos(_div(_mcap(c), c["bal"](EQ))),
     "desc": "Price ÷ book value (shareholders' equity)."},
    {"key": "pfcf", "label": "P/FCF Ratio", "fmt": "x", "calc": lambda c: _pos(_div(_mcap(c), _pos(_flow(c, "Free Cash Flow", "cf")))),
     "desc": "Price ÷ free cash flow. How many dollars you pay for $1 of yearly free cash."},
    {"key": "ev_ebitda", "label": "EV/EBITDA", "fmt": "x", "calc": lambda c: _pos(_div(_ev(c), _pos(_flow(c, ["EBITDA", "Normalized EBITDA"])))),
     "desc": "Enterprise value ÷ EBITDA. A valuation that accounts for debt; popular for comparing companies."},
    {"key": "fcf_yield", "label": "FCF Yield", "fmt": "pct", "calc": lambda c: _div(_flow(c, "Free Cash Flow", "cf"), _mcap(c)),
     "desc": "Free cash flow ÷ market cap. Like an 'interest rate' the business earns for owners at today's price."},
    {"key": "div_yield", "label": "Dividend Yield", "fmt": "pct",
     "calc": lambda c: _div(-(_flow(c, ["Cash Dividends Paid", "Common Stock Dividend Paid"], "cf") or 0) or None, _mcap(c)),
     "desc": "Yearly dividends ÷ market cap: the cash return from dividends alone."},
    {"key": "payout", "label": "Payout Ratio", "fmt": "pct",
     "calc": lambda c: _div(-(_flow(c, ["Cash Dividends Paid", "Common Stock Dividend Paid"], "cf") or 0) or None, _pos(_flow(c, NI))),
     "desc": "Share of profit paid out as dividends. Very high (>80%) can be hard to sustain."},
    {"key": "buyback_yield", "label": "Buyback Yield", "fmt": "pct",
     "calc": lambda c: _div(-(_flow(c, ["Repurchase Of Capital Stock", "Common Stock Payments"], "cf") or 0) or None, _mcap(c)),
     "desc": "Cash spent on buybacks ÷ market cap."},
    {"key": "roe", "label": "Return on Equity (ROE)", "fmt": "pct", "style": "bold", "calc": lambda c: _div(_flow(c, NI), _pos(c["bal"](EQ))),
     "desc": "Profit ÷ shareholders' equity. How well management turns owners' money into profit. 15%+ is strong."},
    {"key": "roa", "label": "Return on Assets (ROA)", "fmt": "pct", "calc": lambda c: _div(_flow(c, NI), c["bal"]("Total Assets")),
     "desc": "Profit ÷ total assets. How efficiently everything the company owns produces profit."},
    {"key": "roic", "label": "Return on Invested Capital", "fmt": "pct", "calc": _roic,
     "desc": "After-tax operating profit ÷ money invested by lenders and shareholders. Above ~10% suggests a quality business."},
    {"key": "de", "label": "Debt / Equity", "fmt": "ratio", "calc": lambda c: _div(c["bal"]("Total Debt"), _pos(c["bal"](EQ))),
     "desc": "Debt for every $1 of shareholders' equity. Under 1 is generally comfortable."},
    {"key": "debt_ebitda", "label": "Debt / EBITDA", "fmt": "ratio", "calc": lambda c: _div(c["bal"]("Total Debt"), _pos(_flow(c, ["EBITDA", "Normalized EBITDA"]))),
     "desc": "Years of EBITDA it would take to pay off all debt. Under 3 is generally manageable."},
    {"key": "current_ratio", "label": "Current Ratio", "fmt": "ratio", "calc": lambda c: _div(c["bal"]("Current Assets"), c["bal"]("Current Liabilities")),
     "desc": "Short-term assets ÷ short-term bills. Above 1 means it can cover what's due this year."},
    {"key": "interest_cover", "label": "Interest Coverage", "fmt": "x",
     "calc": lambda c: _div(c["inc"](["Operating Income", "EBIT"]), _pos(c["inc"](["Interest Expense", "Interest Expense Non Operating"]))),
     "desc": "Operating profit ÷ interest expense. How many times over the company can pay its interest bill."},
]
