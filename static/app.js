/* Kash frontend: plain JS + Chart.js. */
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const state = { data: null, tab: "overview", stmt: "income", period: "annual", range: "1Y", charts: [] };
const cache = {};

/* ------------------------------------------------------------ theme */
const THEME_KEY = "stockwise-theme";
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
try { applyTheme(localStorage.getItem(THEME_KEY)); } catch (e) {}
$("#themeBtn").onclick = () => {
  const dark = isDark();
  const next = dark ? "light" : "dark";
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
  if (state.data) renderStock();
};
function isDark() {
  const t = document.documentElement.dataset.theme;
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

/* ------------------------------------------------------------ formatting */
let CUR = "USD";
function sym() { return CUR === "USD" ? "$" : ""; }
function tail() { return CUR === "USD" ? "" : " " + CUR; }
function money(v, digits = 2) {
  if (v == null) return "–";
  if (v === 0) return sym() + "0" + tail();
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  let out;
  if (a >= 1e12) out = (a / 1e12).toFixed(digits) + "T";
  else if (a >= 1e9) out = (a / 1e9).toFixed(digits) + "B";
  else if (a >= 1e6) out = (a / 1e6).toFixed(digits === 2 ? 1 : digits) + "M";
  else if (a >= 1e3) out = (a / 1e3).toFixed(1) + "K";
  else out = a.toFixed(2);
  return s + sym() + out + tail();
}
function count(v) {
  if (v == null) return "–";
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(1) + "M";
  return v.toLocaleString();
}
const pct = (v, d = 1) => (v == null ? "–" : (v * 100).toFixed(d) + "%");
const signed = (v, d = 1) => {   // "+1.2%" / "-0.4%"; values that round to zero show as "0.0%", never "-0.0%"
  if (v == null) return "–";
  const r = +(v * 100).toFixed(d);
  return (r > 0 ? "+" : "") + (r === 0 ? 0 : r).toFixed(d) + "%";
};
const price = (v) => (v == null ? "–" : sym() + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + tail());
const mult = (v) => (v == null ? "–" : v.toFixed(1) + "×");
function fmt(v, f) {
  switch (f) {
    case "pct": return pct(v);
    case "growth": return signed(v);
    case "eps": return price(v);
    case "shares": return count(v);
    case "x": return mult(v);
    case "ratio": return v == null ? "–" : v.toFixed(2);
    default: return money(v);
  }
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ------------------------------------------------------------ routing */
function go(path) { history.pushState({}, "", path); route(); }
window.onpopstate = route;
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (a) { e.preventDefault(); go(a.getAttribute("href")); }
});
function route() {
  const m = location.pathname.match(/^\/stock\/([^/]+)/i);
  if (m) loadStock(decodeURIComponent(m[1]).toUpperCase());
  else renderHome();
}

/* ------------------------------------------------------------ search */
const q = $("#q"), sug = $("#suggest");
let sugItems = [], sugIdx = -1, sugTimer;
q.addEventListener("input", () => {
  clearTimeout(sugTimer);
  const v = q.value.trim();
  if (!v) { sug.hidden = true; return; }
  sugTimer = setTimeout(async () => {
    try {
      const r = await fetch("/api/search?q=" + encodeURIComponent(v));
      sugItems = await r.json();
    } catch (e) { sugItems = []; }
    sugIdx = sugItems.length ? 0 : -1;
    drawSuggest();
  }, 220);
});
function drawSuggest() {
  if (!sugItems.length) { sug.hidden = true; return; }
  sug.innerHTML = sugItems.map((s, i) =>
    `<li data-i="${i}" class="${i === sugIdx ? "active" : ""}"><b>${esc(s.symbol)}</b><span>${esc(s.name)}</span><small>${esc(s.exchange || "")}</small></li>`).join("");
  sug.hidden = false;
}
sug.addEventListener("mousedown", (e) => {
  const li = e.target.closest("li"); if (!li) return;
  pick(sugItems[+li.dataset.i].symbol);
});
q.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") { sugIdx = Math.min(sugIdx + 1, sugItems.length - 1); drawSuggest(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { sugIdx = Math.max(sugIdx - 1, 0); drawSuggest(); e.preventDefault(); }
  else if (e.key === "Enter") {
    const s = !sug.hidden && sugIdx >= 0 ? sugItems[sugIdx].symbol : q.value.trim();
    if (s) pick(s);
  } else if (e.key === "Escape") sug.hidden = true;
});
q.addEventListener("blur", () => setTimeout(() => (sug.hidden = true), 150));
function pick(sym) { sug.hidden = true; q.value = ""; q.blur(); go("/stock/" + encodeURIComponent(sym.toUpperCase())); }

/* ------------------------------------------------------------ home */
const POPULAR = [["AAPL", "Apple"], ["MSFT", "Microsoft"], ["NVDA", "Nvidia"], ["ACN", "Accenture"], ["KO", "Coca-Cola"],
  ["AMZN", "Amazon"], ["XOM", "Exxon Mobil"], ["COST", "Costco"], ["JPM", "JPMorgan"], ["TSLA", "Tesla"]];
// renderHome() lives in market.js

/* ------------------------------------------------------------ stock page */
async function loadStock(ticker) {
  destroyCharts();
  if (state.data?.ticker !== ticker) { state.tab = "overview"; }
  if (cache[ticker]) { state.data = cache[ticker]; return renderStock(); }
  app.innerHTML = `<div class="loading"><div class="spinner"></div>Crunching the numbers for ${esc(ticker)}…</div>`;
  try {
    const r = await fetch("/api/stock/" + encodeURIComponent(ticker));
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();
    cache[ticker] = d;
    if (!location.pathname.toUpperCase().endsWith("/" + ticker)) return; // navigated away
    state.data = d;
    // Chart.js caches label widths, so the web font must be ready before charts are drawn
    await document.fonts.load("12px " + css("--font")).catch(() => {});
    renderStock();
  } catch (e) {
    app.innerHTML = `<div class="error"><h2>Hmm, we couldn't load ${esc(ticker)}</h2><p class="sub">${esc(e.message)}</p><a href="/" data-link class="btn">Back home</a></div>`;
  }
}

function renderStock() {
  destroyCharts();
  const d = state.data, i = d.info;
  CUR = i.financialCurrency || i.currency || "USD";
  document.title = `${d.ticker} · ${i.shortName || i.longName || ""} · Kash`;
  const chg = i.regularMarketChange, chgp = i.regularMarketChangePercent;
  const cls = chg >= 0 ? "up" : "down";
  const lo = i.fiftyTwoWeekLow, hi = i.fiftyTwoWeekHigh, px = i.currentPrice;
  const pos = lo != null && hi > lo ? Math.min(100, Math.max(0, ((px - lo) / (hi - lo)) * 100)) : 50;
  const dy = i.dividendYieldCalc;

  // price uses trading currency; statements use financialCurrency
  const pxStr = (v) => { const c = CUR; CUR = i.currency || "USD"; const s = price(v); CUR = c; return s; };

  app.innerHTML = `
    <div class="head">
      <div class="ident">
        <div class="logo">${esc((d.ticker || "?")[0])}</div>
        <div>
          <h1>${esc(i.longName || i.shortName || d.ticker)}</h1>
          <div class="tags"><span class="tag"><b>${esc(d.ticker)}</b> · ${esc(i.fullExchangeName || i.exchange || "")}</span>
            ${i.sector ? `<span class="tag">${esc(i.sector)}</span>` : ""}${i.industry ? `<span class="tag">${esc(i.industry)}</span>` : ""}</div>
        </div>
      </div>
      <div class="price">
        <div class="big num">${pxStr(px)}</div>
        <div class="chg ${cls} num">${chg >= 0 ? "▲" : "▼"} ${pxStr(Math.abs(chg ?? 0))} (${(chgp ?? 0).toFixed(2)}%) <span class="muted" style="font-weight:500">today</span></div>
        ${extendedLine(i, pxStr)}
      </div>
    </div>
    <div class="card stats">
      ${stat("Market cap", money(i.marketCap))}
      ${stat("P/E ratio", mult(i.trailingPE), "Price ÷ last 12 months' earnings per share.")}
      ${stat("Forward P/E", mult(i.forwardPE), "Price ÷ analysts' estimate of next year's earnings per share.")}
      ${stat("Dividend yield", dy ? pct(dy, 2) : "None")}
      ${stat("Beta", i.beta != null ? i.beta.toFixed(2) : "–", "How much the stock swings vs. the market. 1 = same as the market; above 1 = bumpier.")}
      <div class="stat"><div class="k">52-week range</div>
        <div class="range"><i style="left:${pos}%"></i></div>
        <div class="range-l num"><span>${pxStr(lo)}</span><span>${pxStr(hi)}</span></div></div>
    </div>
    <nav class="tabs">
      ${[["overview", "Overview"], ["financials", "Financials"], ...Object.entries(COMPANY_TABS).map(([k, [label]]) => [k, label])]
        .map(([k, label]) => `<button class="tab ${state.tab === k ? "on" : ""}" data-tab="${k}">${label}</button>`).join("")}
    </nav>
    <div id="tabBody"></div>`;
  app.querySelectorAll("[data-tab]").forEach((b) => (b.onclick = () => { state.tab = b.dataset.tab; renderStock(); }));
  $(".tabs .tab.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  loadCompany(d.ticker).catch(() => {});   // start fetching company details in the background
  if (state.tab === "overview") renderOverview();
  else if (state.tab === "financials") renderFinancials();
  else if (COMPANY_TABS[state.tab]) renderCompanyTab(state.tab, COMPANY_TABS[state.tab][1]);
  else { state.tab = "overview"; renderOverview(); }
}
const stat = (k, v, tip) => `<div class="stat"><div class="k">${k}${tip ? info(tip) : ""}</div><div class="v num">${v}</div></div>`;
const info = (t) => `<span class="info" data-tip="${esc(t)}">i</span>`;

/* ------------------------------------------------------------ overview */
function annualSeries(table, key) {
  const fin = state.data.financials.annual;
  const cols = fin.columns[table] || [];
  const row = (fin[table] || []).find((r) => r.key === key);
  const out = [];
  cols.forEach((c, idx) => { if (c.date) out.push({ label: c.label, v: row ? row.values[idx] : null }); });
  return out.slice(0, CHART_YEARS).reverse(); // last N fiscal years, oldest -> newest
}
const CHART_YEARS = 10;

function renderOverview() {
  const d = state.data, i = d.info, body = $("#tabBody");
  const sc = d.scorecard || [];
  const passed = sc.filter((c) => c.pass).length;
  const hasFin = d.financials.annual.income.length > 0;

  const verdicts = ["Lots of red flags", "Lots of red flags", "Mixed picture", "Mixed picture", "Pretty solid", "Strong", "Very strong", "Excellent"];
  const ratio = sc.length ? passed / sc.length : 0;
  const verdict = verdicts[Math.round(ratio * 7)];

  body.innerHTML = `
    ${sc.length ? `<section>
      <h2>The scorecard</h2><p class="sub">Quick yes-or-no checks on what matters most. Hover any number elsewhere for an explanation.</p>
      <div class="card score">
        <div class="gauge">${gauge(passed, sc.length)}<div class="verdict">${verdict}</div><div class="muted">${passed} of ${sc.length} checks passed</div></div>
        <div class="checks">${sc.map((c) => `<div class="check ${c.pass ? "pass" : "fail"}"><div class="ic">${c.pass ? "✓" : "✕"}</div>
          <div><b>${esc(c.title)}</b><p>${esc(c.text)}</p></div></div>`).join("")}</div>
      </div></section>` : ""}

    <section>
      <div class="card">
        <div class="row-between">
          <div class="q-title"><div class="q-emoji">📈</div><div><h3>How has the stock done?</h3><div class="muted" id="perf"></div></div></div>
          <div class="seg" id="ranges">${["1D", "1W", "1M", "6M", "YTD", "1Y", "5Y", "10Y", "Max"].map((r) => `<button data-r="${r}" class="${r === state.range ? "on" : ""}">${r}</button>`).join("")}</div>
        </div>
        <div class="chart-box tall" style="margin-top:14px"><canvas id="cPrice"></canvas></div>
      </div>
    </section>

    ${hasFin ? `<section>
      <h2>The story in charts</h2><p class="sub">Four questions every investor should ask, answered with the company's own numbers.</p>
      <div class="grid g2">
        ${qcard("🌱", "Is it growing?", "Revenue and net income each fiscal year", "cGrowth", growthTake())}
        ${qcard("💰", "Is it profitable?", "How many cents of each sales dollar it keeps", "cMargins", marginTake())}
        ${qcard("💵", "Does it make real cash?", "Free cash flow vs. cash returned to shareholders", "cCash", cashTake())}
        ${qcard("🛡️", "Is it financially healthy?", "Cash on hand vs. total debt", "cHealth", healthTake())}
      </div>
    </section>

    <section>
      <h2>Is the price right?</h2><p class="sub">Compare today's price to the company's own history and to Wall Street's targets.</p>
      <div class="grid g2">
        ${qcard("🏷️", "P/E ratio over time", "Dollars paid for each $1 of yearly profit", "cPE", peTake())}
        <div class="card"><div class="q-title"><div class="q-emoji">🎯</div><div><h3>Analyst price targets</h3><div class="muted">Where ${i.numberOfAnalystOpinions ? Math.round(i.numberOfAnalystOpinions) + " analysts" : "analysts"} think the price is headed in 12 months</div></div></div>
          ${targetBar(i)}
          <div class="pills">
            ${pill("Consensus", (i.recommendationKey || "–").replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()), "")}
            ${pill("Avg. target", priceIn(i.targetMeanPrice, i), upside(i.targetMeanPrice, i.currentPrice))}
            ${pill("PEG ratio", (i.trailingPegRatio ?? i.pegRatio) != null ? (i.trailingPegRatio ?? i.pegRatio).toFixed(2) : "–", "P/E ÷ growth. Near 1 = fair")}
          </div></div>
      </div>
    </section>` : `<section><div class="card"><h3>No financial statements</h3><p class="sub" style="margin:0">This looks like a fund or a security without company financials, so only price data is shown.</p></div></section>`}

    <section>
      <h2>About ${esc(i.shortName || d.ticker)}</h2>
      <div class="grid g-about">
        <div class="card about"><p>${esc(i.longBusinessSummary || "No description available.")}</p></div>
        <div class="card"><dl class="facts">
          <dt>Sector</dt><dd>${esc(i.sector || "–")}</dd>
          <dt>Industry</dt><dd>${esc(i.industry || "–")}</dd>
          <dt>Country</dt><dd>${esc(i.country || "–")}</dd>
          <dt>Employees</dt><dd>${i.fullTimeEmployees ? Math.round(i.fullTimeEmployees).toLocaleString() : "–"}</dd>
          <dt>Owned by funds</dt><dd>${pct(i.heldPercentInstitutions)}</dd>
          <dt>Owned by insiders</dt><dd>${pct(i.heldPercentInsiders, 2)}</dd>
          <dt>Website</dt><dd>${i.website ? `<a href="${esc(i.website)}" target="_blank" rel="noopener">${esc(i.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))}</a>` : "–"}</dd>
        </dl></div>
      </div>
    </section>`;

  $("#ranges").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.range = b.dataset.r;
    $("#ranges").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    drawPrice();
  };
  drawPrice();
  if (hasFin) { drawGrowth(); drawMargins(); drawCash(); drawHealth(); drawPE(); }
}

function priceIn(v, i) { const c = CUR; CUR = i.currency || "USD"; const s = price(v); CUR = c; return s; }
function upside(t, p) { if (!t || !p) return ""; const u = t / p - 1; return `<span class="${u >= 0 ? "up" : "down"}">${signed(u, 0)} vs. today</span>`; }
const pill = (k, v, h) => `<div class="pill"><div class="k">${k}</div><div class="v num">${v}</div><div class="h muted">${h}</div></div>`;
const qcard = (e, t, s, id, take) => `<div class="card"><div class="q-title"><div class="q-emoji">${e}</div><div><h3>${t}</h3><div class="muted">${s}</div></div></div>
  <div class="chart-box" style="margin-top:14px"><canvas id="${id}"></canvas></div>${take ? `<div class="takeaway">${take}</div>` : ""}</div>`;

function gauge(n, total) {
  const r = 70, C = 2 * Math.PI * r, f = total ? n / total : 0;
  const col = f >= 0.7 ? css("--good") : f >= 0.45 ? css("--warn") : css("--bad");
  return `<svg viewBox="0 0 180 180"><circle cx="90" cy="90" r="${r}" fill="none" stroke="${css("--soft")}" stroke-width="16"/>
    <circle cx="90" cy="90" r="${r}" fill="none" stroke="${col}" stroke-width="16" stroke-linecap="round"
      stroke-dasharray="${C * f} ${C}" transform="rotate(-90 90 90)"/>
    <text x="90" y="98" text-anchor="middle" font-size="40" font-weight="800" fill="${css("--ink")}" font-family='${css("--font")}'>${n}/${total}</text></svg>`;
}

function targetBar(i) {
  const lo = i.targetLowPrice, hi = i.targetHighPrice, mean = i.targetMeanPrice, px = i.currentPrice;
  if (!lo || !hi || !px) return `<p class="sub" style="margin-top:20px">No analyst targets available.</p>`;
  const min = Math.min(lo, px) * 0.95, max = Math.max(hi, px) * 1.05;
  const at = (v) => ((v - min) / (max - min)) * 100;
  return `<div class="tbar"><div class="track"></div>
    <div class="mk" style="left:${at(lo)}%">Low ${priceIn(lo, i)}<i></i></div>
    <div class="mk" style="left:${at(hi)}%">High ${priceIn(hi, i)}<i></i></div>
    <div class="mk bottom" style="left:${at(mean)}%"><i></i>Avg ${priceIn(mean, i)}</div>
    <div class="mk now" style="left:${at(px)}%; top:${Math.abs(at(px) - at(lo)) < 14 || Math.abs(at(px) - at(hi)) < 14 ? 44 : 0}px">${Math.abs(at(px) - at(lo)) < 14 || Math.abs(at(px) - at(hi)) < 14 ? "<i></i>" : ""}Today ${priceIn(px, i)}${Math.abs(at(px) - at(lo)) < 14 || Math.abs(at(px) - at(hi)) < 14 ? "" : "<i></i>"}</div>
  </div>`;
}

/* ---- takeaways (plain-English one-liners under each chart) */
function cagr(arr) {
  const v = arr.map((x) => x.v).filter((x) => x != null);
  if (v.length < 2 || v[0] <= 0 || v[v.length - 1] <= 0) return null;
  return Math.pow(v[v.length - 1] / v[0], 1 / (v.length - 1)) - 1;
}
function growthTake() {
  const r = annualSeries("income", "revenue"), n = annualSeries("income", "net_income");
  const rg = cagr(r), ng = cagr(n);
  if (rg == null) return "";
  return `Sales grew <b>${signed(rg)}</b> a year${ng != null ? ` and profit grew <b>${signed(ng)}</b> a year` : ""} from ${r[0].label} to ${r[r.length - 1].label}.`
    + (ng != null && ng > rg + 0.01 ? " Profit is growing faster than sales: the business is getting more efficient." : "");
}
function marginTake() {
  const m = annualSeries("income", "net_margin").filter((x) => x.v != null);
  if (!m.length) return "";
  const last = m[m.length - 1].v, first = m[0].v;
  const trend = last - first > 0.01 ? "up from" : last - first < -0.01 ? "down from" : "steady vs.";
  return `Keeps <b>${(last * 100).toFixed(1)}¢</b> of profit per $1 of sales, ${trend} ${(first * 100).toFixed(1)}¢ in ${m[0].label}.`;
}
function cashTake() {
  const f = annualSeries("cashflow", "fcf"), dv = annualSeries("cashflow", "dividends"), bb = annualSeries("cashflow", "buybacks");
  const L = f.length - 1; if (L < 0 || f[L].v == null) return "";
  const ret = -((dv[L]?.v || 0) + (bb[L]?.v || 0));
  const share = f[L].v > 0 ? ret / f[L].v : null;
  return `Generated <b>${money(f[L].v, 1)}</b> of free cash in ${f[L].label}` +
    (ret > 0 && share != null ? ` and returned <b>${pct(share, 0)}</b> of it to shareholders through dividends and buybacks.` : ".");
}
function healthTake() {
  const c = annualSeries("balance", "cash_sti"), dt = annualSeries("balance", "total_debt");
  const L = c.length - 1; if (L < 0) return "";
  const cash = c[L].v || 0, debt = dt[L]?.v || 0;
  const de = state.data.financials.annual.ratios.find((r) => r.key === "debt_ebitda")?.values[0];
  if (cash >= debt) return `At the end of ${c[L].label}, it had <b>${money(cash - debt, 1)} more cash than debt</b>. A very comfortable position.`;
  return `At the end of ${c[L].label}, debt exceeded cash by <b>${money(debt - cash, 1)}</b>` + (de != null ? `, about <b>${de.toFixed(1)} years</b> of EBITDA${de < 3 ? ", which is manageable" : ", which is on the high side"}.` : ".");
}
function peTake() {
  const pe = state.data.financials.annual.ratios.find((r) => r.key === "pe");
  if (!pe) return "";
  const hist = pe.values.slice(1, CHART_YEARS + 1).filter((v) => v != null), now = pe.values[0];
  if (!hist.length || now == null) return "";
  const avg = hist.reduce((a, b) => a + b, 0) / hist.length;
  const diff = now / avg - 1;
  return `Now <b>${mult(now)}</b> vs. a ${hist.length}-year average of <b>${mult(avg)}</b>: ` +
    (diff < -0.05 ? `about ${pct(-diff, 0)} cheaper than usual.` : diff > 0.05 ? `about ${pct(diff, 0)} pricier than usual.` : "right around its normal level.");
}

/* ------------------------------------------------------------ charts */
function destroyCharts() { state.charts.forEach((c) => c.destroy()); state.charts = []; }
function baseOpts(extra = {}) {
  const ink3 = css("--ink-3"), line = css("--line");
  Chart.defaults.font.family = css("--font");
  Chart.defaults.color = ink3;
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 350 },
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 14 } },
      tooltip: { backgroundColor: css("--ink"), titleColor: css("--bg"), bodyColor: css("--bg"), padding: 10, cornerRadius: 10 },
    },
    scales: {
      x: { grid: { display: false }, border: { color: line } },
      y: { grid: { color: line }, border: { display: false } },
    },
    ...extra,
  };
}
function mk(id, cfg) { const el = document.getElementById(id); if (!el) return; state.charts.push(new Chart(el, cfg)); }
const bar = (label, data, color, extra = {}) => ({ type: "bar", label, data, backgroundColor: color, borderRadius: 6, maxBarThickness: 38, ...extra });

// "After hours: $350.78 ▲ $1.27 (+0.36%) · Nov 4, 7:59 PM EST" under the regular price
function extendedLine(i, pxStr) {
  const e = i.extended; if (!e) return "";
  const up = e.change >= 0;
  const when = e.time ? " · " + new Date(e.time).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric",
    minute: "2-digit", timeZoneName: "short", ...(i.exchangeTimezoneName ? { timeZone: i.exchangeTimezoneName } : {}) }) : "";
  return `<div class="ext num" data-tip="Trades outside regular hours (9:30 AM–4 PM ET). Fewer buyers and sellers, so prices can jump more.">
    ${esc(e.label)}: <b>${pxStr(e.price)}</b> <span class="${up ? "up" : "down"}">${up ? "▲" : "▼"} ${pxStr(Math.abs(e.change))} (${signed(e.pct, 2)})</span><span class="muted">${when}</span></div>`;
}

const INTRADAY = ["1D", "1W", "1M"];
const intradayCache = {};   // "AAPL|1D" -> { at, data }

async function drawPrice() {
  const d = state.data, rng = state.range;
  if (INTRADAY.includes(rng)) {
    const key = d.ticker + "|" + rng, hit = intradayCache[key];
    let data = hit && Date.now() - hit.at < 60e3 ? hit.data : null;
    if (!data) {
      $("#perf").textContent = "Loading…";
      try {
        const r = await fetch(`/api/prices/${encodeURIComponent(d.ticker)}?range=${rng}`);
        if (!r.ok) throw new Error();
        data = await r.json();
        intradayCache[key] = { at: Date.now(), data };
      } catch { data = null; }
      if (state.range !== rng || state.data !== d) return;   // user moved on while loading
    }
    if (data && data.points.length > 1) return plotPrice(data.points, data.base, rng, data.tz, data.baseIsPrevClose, data.sessions);
    $("#perf").textContent = "Intraday prices aren't available; showing daily closes.";
  }
  const pts = d.prices; if (!pts.length) return;
  const now = pts[pts.length - 1][0], day = 864e5;
  const spans = { "1D": 7, "1W": 7, "1M": 31, "6M": 183, "1Y": 366, "5Y": 1827, "10Y": 3660 };
  const start = rng === "YTD" ? Date.UTC(new Date(now).getUTCFullYear(), 0, 1)
    : rng === "Max" ? pts[0][0] : now - spans[rng] * day;
  const s = pts.filter((p) => p[0] >= start);
  plotPrice(s, s[0][1], rng, null, false);
}

const SESSION_NAME = { p: "Pre-market", a: "After hours" };

// Shades pre-market and after-hours stretches of the 1D chart and labels them.
const sessionBands = {
  id: "sessionBands",
  beforeDatasetsDraw(chart, _args, opts) {
    const sess = opts.sessions; if (!sess) return;
    const { ctx, chartArea: a, scales: { x } } = chart;
    ctx.save();
    ctx.beginPath(); ctx.rect(a.left, a.top, a.width, a.height); ctx.clip();
    for (let k = 0; k < sess.length;) {
      if (sess[k] === "r") { k++; continue; }
      let j = k; while (j + 1 < sess.length && sess[j + 1] === sess[k]) j++;
      const x0 = x.getPixelForValue(k - 0.5), x1 = x.getPixelForValue(j + 0.5);
      ctx.fillStyle = css("--soft"); ctx.globalAlpha = 0.9;
      ctx.fillRect(x0, a.top, x1 - x0, a.height);
      ctx.globalAlpha = 1; ctx.fillStyle = css("--ink-3");
      ctx.font = `600 11px ${css("--font")}`; ctx.textBaseline = "top";
      if (x1 - x0 > 70) ctx.fillText(SESSION_NAME[sess[k]], x0 + 8, a.top + 6);
      k = j + 1;
    }
    ctx.restore();
  },
};

// Intraday ranges (tz set) are drawn by bar number so nights and weekends don't leave flat gaps.
// sess (1D only): per-point session code, "p" pre-market / "r" regular / "a" after hours.
function plotPrice(s, base, rng, tz, baseIsPrevClose, sess = null) {
  const d = state.data, i = d.info, intraday = !!tz;
  if (sess && !sess.some((c) => c !== "r")) sess = null;
  const lastReg = sess ? sess.lastIndexOf("r") : s.length - 1;
  // Outside regular hours the official close (incl. the closing auction) can differ a little from the
  // last 2-minute bar; use it so the chart matches the header.
  const close = sess && i.marketState !== "REGULAR" ? i.regularMarketPrice : null;
  if (close && lastReg < 0) base = close;   // pre-market: vs. yesterday's official close
  else if (sess && lastReg >= 0 && i.previousClose) base = i.previousClose;   // same official close as the header
  // headline change uses the regular session (like the header); extended trading is reported beside it
  const ref = lastReg >= 0 ? close || s[lastReg][1] : s[s.length - 1][1];
  const ch = ref / base - 1;
  const col = ch >= 0 ? css("--good") : css("--bad");
  let when = rng === "YTD" ? "this year" : rng === "Max" ? "since " + new Date(s[0][0]).getFullYear()
    : rng === "1D" ? (baseIsPrevClose ? "since the previous close" : "today") : "over " + rng;
  if (sess && lastReg < 0) when = "pre-market, vs. the previous close";
  let extra = "";
  if (sess && lastReg >= 0 && lastReg < s.length - 1) {
    const ext = s[s.length - 1][1] / ref - 1;
    extra = ` · after hours <span class="${ext >= 0 ? "up" : "down"}" style="font-weight:700">${signed(ext, 2)}</span>`;
  }
  $("#perf").innerHTML = `<span class="${ch >= 0 ? "up" : "down"}" style="font-weight:700">${signed(ch, rng === "1D" ? 2 : 1)}</span> ${when}${extra}`;
  const old = state.charts.find((c) => c.canvas.id === "cPrice");
  if (old) { old.destroy(); state.charts = state.charts.filter((c) => c !== old); }

  const long = ["5Y", "10Y", "Max"].includes(rng);
  const tzOpt = tz ? { timeZone: tz } : {};
  const tickFmt = rng === "1D" ? { hour: "numeric", minute: "2-digit" }
    : rng === "1W" ? { weekday: "short", month: "short", day: "numeric" }
    : long ? { month: "short", year: "numeric" } : { month: "short", day: "numeric" };
  const tipFmt = intraday ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" } : { dateStyle: "medium" };
  const timeOf = (x) => intraday ? s[Math.max(0, Math.min(s.length - 1, Math.round(x)))][0] : x;
  const fmt = (t, o) => new Date(t).toLocaleString(undefined, { ...o, ...tzOpt });
  const xy = s.map((p, k) => ({ x: intraday ? k : p[0], y: p[1] }));
  const datasets = [{ data: xy, borderColor: col, borderWidth: 2, pointRadius: 0, fill: true, tension: intraday ? 0 : 0.15,
    segment: sess ? { borderColor: (c) => (sess[c.p1DataIndex] !== "r" ? col + "88" : undefined) } : undefined,
    backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, ctx.chart.height); g.addColorStop(0, col + "33"); g.addColorStop(1, col + "00"); return g; } }];
  if (rng === "1D" && baseIsPrevClose) {
    datasets.push({ label: "Previous close", data: [{ x: 0, y: base }, { x: s.length - 1, y: base }], borderColor: css("--ink-3"),
      borderWidth: 1, borderDash: [4, 4], pointRadius: 0, fill: false });
  }
  const minX = xy[0].x, maxX = xy[xy.length - 1].x;
  mk("cPrice", {
    type: "line",
    data: { datasets },
    plugins: [sessionBands],
    options: baseOpts({
      plugins: { ...baseOpts().plugins, legend: { display: false }, sessionBands: { sessions: sess },
        tooltip: { ...baseOpts().plugins.tooltip, filter: (it) => it.datasetIndex === 0,
          callbacks: { title: (it) => fmt(timeOf(it[0].parsed.x), tipFmt),
            label: (it) => priceIn(it.parsed.y, i) + (sess && SESSION_NAME[sess[it.dataIndex]] ? " · " + SESSION_NAME[sess[it.dataIndex]] : "") } } },
      scales: {
        x: { type: "linear", min: minX, max: maxX, grid: { display: false },
          ticks: { maxTicksLimit: rng === "1W" ? 5 : 7, callback: (v) => fmt(timeOf(v), tickFmt) } },
        y: { grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => priceIn(v, i).replace(/\.00/, "") } },
      },
    }),
  });
}

function drawGrowth() {
  const r = annualSeries("income", "revenue"), n = annualSeries("income", "net_income");
  mk("cGrowth", { data: { labels: r.map((x) => x.label), datasets: [bar("Revenue", r.map((x) => x.v), css("--c1")), bar("Net income", n.map((x) => x.v), css("--c2"))] },
    options: moneyOpts() });
}
function drawMargins() {
  const keys = [["gross_margin", "Gross", "--c1"], ["op_margin", "Operating", "--c3"], ["net_margin", "Net", "--c2"]];
  const labels = annualSeries("income", "revenue").map((x) => x.label);
  mk("cMargins", { type: "line", data: { labels, datasets: keys.map(([k, l, c]) => ({ label: l + " margin", data: annualSeries("income", k).map((x) => x.v),
    borderColor: css(c), backgroundColor: css(c), tension: 0.3, borderWidth: 2.5, pointRadius: 4 })) },
    options: baseOpts({ plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (it) => `${it.dataset.label}: ${pct(it.parsed.y)}` } } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => pct(v, 0) } } } }) });
}
function drawCash() {
  const f = annualSeries("cashflow", "fcf"), dv = annualSeries("cashflow", "dividends"), bb = annualSeries("cashflow", "buybacks");
  const neg = (a) => a.map((x) => (x.v == null ? null : -x.v));
  mk("cCash", { data: { labels: f.map((x) => x.label), datasets: [
    bar("Free cash flow", f.map((x) => x.v), css("--c1"), { stack: "a" }),
    bar("Dividends", neg(dv), css("--c3"), { stack: "b" }),
    bar("Buybacks", neg(bb), css("--c4"), { stack: "b" }),
  ] }, options: moneyOpts(true) });
}
function drawHealth() {
  const c = annualSeries("balance", "cash_sti"), dt = annualSeries("balance", "total_debt");
  mk("cHealth", { data: { labels: c.map((x) => x.label), datasets: [bar("Cash & investments", c.map((x) => x.v), css("--good")), bar("Total debt", dt.map((x) => x.v), css("--bad"))] },
    options: moneyOpts() });
}
function drawPE() {
  const fin = state.data.financials.annual, row = fin.ratios.find((r) => r.key === "pe"); if (!row) return;
  const n = CHART_YEARS + 1;
  const labels = fin.columns.ratios.slice(0, n).map((c) => c.label).reverse(), vals = row.values.slice(0, n).reverse();
  const hist = row.values.slice(1, n).filter((v) => v != null), avg = hist.reduce((a, b) => a + b, 0) / (hist.length || 1);
  mk("cPE", { data: { labels, datasets: [
    bar("P/E", vals, labels.map((l) => (l === "Current" ? css("--c1") : css("--c1") + "66"))),
    { type: "line", label: "Average", data: labels.map(() => avg), borderColor: css("--ink-3"), borderDash: [6, 5], borderWidth: 1.5, pointRadius: 0 },
  ] }, options: baseOpts({ plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (it) => `${it.dataset.label}: ${mult(it.parsed.y)}` } } },
    scales: { x: { grid: { display: false } }, y: { grid: { color: css("--line") }, border: { display: false }, beginAtZero: true, ticks: { callback: (v) => v + "×" } } } }) });
}
function moneyOpts(stacked = false) {
  return baseOpts({
    plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (it) => `${it.dataset.label}: ${money(it.parsed.y, 1)}` } } },
    scales: { x: { grid: { display: false }, stacked }, y: { stacked, grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => money(v, 0) } } },
  });
}

/* ------------------------------------------------------------ financials tab */
const STMTS = [["income", "Income"], ["balance", "Balance Sheet"], ["cashflow", "Cash Flow"], ["ratios", "Ratios"]];
const STMT_INTRO = {
  income: "The <b>income statement</b> shows money coming in (revenue), what it cost to earn it, and the profit left over.",
  balance: "The <b>balance sheet</b> is a snapshot of what the company owns (assets), what it owes (liabilities), and what's left for shareholders (equity).",
  cashflow: "The <b>cash flow statement</b> tracks actual cash moving in and out, which is harder to dress up than accounting profit.",
  ratios: "<b>Ratios</b> turn raw numbers into comparisons: how pricey the stock is, how efficient the business is, and how much debt it carries.",
};
function renderFinancials() {
  const body = $("#tabBody"), fin = state.data.financials[state.period];
  const cols = fin.columns?.[state.stmt] || [], rows = fin[state.stmt] || [];
  body.innerHTML = `
    <div class="fin-controls">
      <div class="seg" id="stmts">${STMTS.map(([k, l]) => `<button data-s="${k}" class="${k === state.stmt ? "on" : ""}">${l}</button>`).join("")}</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <div class="seg" id="periods">${[["annual", "Annual"], ["quarterly", "Quarterly"]].map(([k, l]) => `<button data-p="${k}" class="${k === state.period ? "on" : ""}">${l}</button>`).join("")}</div>
        <button class="btn" id="csv">⬇ CSV</button>
      </div>
    </div>
    <p class="sub">${STMT_INTRO[state.stmt]} Hover the <span class="info" style="cursor:default">i</span> next to any line for what it means.</p>
    ${rows.length ? `<div class="tbl-wrap"><table>
      <thead><tr><th>${CUR === "USD" ? "USD" : esc(CUR)}${state.stmt === "ratios" ? "" : ""}</th>${cols.map((c) => `<th>${esc(c.label)}</th>`).join("")}<th>Trend</th></tr></thead>
      <tbody>${rows.map((r) => `<tr class="${esc(r.style)} ${r.fmt === "growth" ? "growth" : ""}">
        <td>${esc(r.label)}${info(r.desc)}</td>
        ${r.values.map((v) => `<td class="num ${r.fmt === "growth" && v != null ? (r.key === "shares_change" ? (v <= 0 ? "up" : "down") : v >= 0 ? "up" : "down") : ""}">${fmt(v, r.fmt)}</td>`).join("")}
        <td class="spark">${spark(r.values)}</td></tr>`).join("")}</tbody>
    </table></div>` : `<div class="card"><p class="sub" style="margin:0">No ${state.period} data available for this statement.</p></div>`}
    <p class="note">${state.period === "quarterly" && state.stmt === "ratios" ? "Quarterly ratios annualize each quarter's results (×4), so seasonal businesses can look jumpy. " : ""}
      ${cols[0]?.label === "TTM" ? "TTM = trailing twelve months, the sum of the last four quarters. " : ""}${state.stmt === "ratios" && state.period === "annual" ? "“Current” uses today's price with the latest results. " : ""}
      Figures in ${esc(CUR)}. Source: ${esc(fin.source || "Yahoo Finance")}${state.period === "annual" && fin.source?.startsWith("SEC") ? " (share counts and per-share figures are split-adjusted to today)" : ""}. Negative numbers on the cash flow statement are cash going out.</p>`;
  $("#stmts").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.stmt = b.dataset.s; renderFinancials(); } };
  $("#periods").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.period = b.dataset.p; renderFinancials(); } };
  $("#csv").onclick = () => downloadCSV(cols, rows);
}
function spark(values) {
  const v = [...values].reverse().filter((x) => x != null);
  if (v.length < 2) return "";
  const W = 70, H = 22, min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * W},${H - 2 - ((x - min) / span) * (H - 4)}`).join(" ");
  const col = v[v.length - 1] >= v[0] ? css("--good") : css("--bad");
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
function downloadCSV(cols, rows) {
  const lines = [["Item", ...cols.map((c) => c.label)], ...rows.map((r) => [r.label, ...r.values.map((v) => (v == null ? "" : v))])];
  const csv = lines.map((l) => l.map((x) => (/[",\n]/.test(String(x)) ? `"${String(x).replace(/"/g, '""')}"` : x)).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `${state.data.ticker}-${state.stmt}-${state.period}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ------------------------------------------------------------ tooltips */
let tipEl;
document.addEventListener("mouseover", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t) { if (tipEl) tipEl.hidden = true; return; }
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "tip"; document.body.appendChild(tipEl); }
  tipEl.textContent = t.dataset.tip; tipEl.hidden = false;
  const r = t.getBoundingClientRect();
  const left = Math.min(window.innerWidth - 316, Math.max(8, r.left - 10));
  tipEl.style.left = left + "px";
  tipEl.style.top = (r.bottom + 8 + tipEl.offsetHeight > window.innerHeight ? r.top - tipEl.offsetHeight - 8 : r.bottom + 8) + "px";
});
window.addEventListener("scroll", () => { if (tipEl) tipEl.hidden = true; }, { passive: true });

// route() is called from index.html once app.js, company.js and market.js have all loaded
