/* Kash: site-wide pages (Screener, Compare, Earnings, Macro, Assets) and the watchlist.
   Loaded after app.js and uses its helpers (money, signed, pct, mult, esc, css, mk, baseOpts, spark...). */

/* ------------------------------------------------------------ watchlist & recent (per browser) */
function store(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
function save(key, v) { try { localStorage.setItem(key, JSON.stringify(v)); } catch {} }
const watch = {
  all: () => store("kash-watch", []),
  has: (s) => watch.all().includes(s),
  toggle: (s) => { const w = watch.all(), i = w.indexOf(s); if (i >= 0) w.splice(i, 1); else w.unshift(s); save("kash-watch", w); return i < 0; },
};
function rememberRecent(s) { const r = store("kash-recent", []).filter((x) => x !== s); r.unshift(s); save("kash-recent", r.slice(0, 10)); }
const starBtn = (s, cls = "") => `<button class="star ${cls} ${watch.has(s) ? "on" : ""}" data-star="${esc(s)}" title="Add to watchlist" aria-label="Toggle ${esc(s)} on the watchlist">${watch.has(s) ? "★" : "☆"}</button>`;
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-star]"); if (!b) return;
  e.preventDefault(); e.stopPropagation();
  const on = watch.toggle(b.dataset.star);
  document.querySelectorAll(`[data-star="${b.dataset.star}"]`).forEach((x) => { x.classList.toggle("on", on); x.textContent = on ? "★" : "☆"; });
});

/* ------------------------------------------------------------ shared bits */
function pageStart(title) { destroyCharts(); state.data = null; CUR = "USD"; document.title = `${title} · Kash`; window.scrollTo(0, 0); }
const pageLoading = (t) => `<div class="loading"><div class="spinner"></div>${esc(t)}</div>`;
const pageError = (e) => `<div class="error"><h3>That didn't load</h3><p class="sub">${esc(e.message || e)}</p></div>`;
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
const stockLink = (s) => `<a href="/stock/${encodeURIComponent(s)}" data-link><b>${esc(s)}</b></a>`;
const chgCell = (v, d = 1) => `<td class="${v == null ? "" : v >= 0 ? "up" : "down"}">${signed(v, d)}</td>`;
const n2 = (v, d = 1) => (v == null ? "–" : v.toFixed(d));
function fmtPrice(v) {
  if (v == null) return "–";
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
  return v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}
function ratingText(r) { return r == null ? "–" : `${r.toFixed(1)} ${r <= 1.5 ? "Strong buy" : r <= 2.5 ? "Buy" : r <= 3.5 ? "Hold" : "Sell"}`; }
function downloadRows(name, head, rows) {
  const csv = [head, ...rows].map((l) => l.map((x) => (/[",\n]/.test(String(x ?? "")) ? `"${String(x).replace(/"/g, '""')}"` : x ?? "")).join(",")).join("\n");
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

/* ------------------------------------------------------------ Screener */
const SCREEN_COLS = [
  ["cap", "Market cap", (r) => money(r.cap, 1), "Total value of all shares."],
  ["price", "Price", (r) => fmtPrice(r.price)],
  ["change", "Today", null],
  ["pe", "P/E", (r) => n2(r.pe), "Price ÷ last 12 months' earnings."],
  ["fpe", "Fwd P/E", (r) => n2(r.fpe), "Price ÷ next year's expected earnings."],
  ["pb", "P/B", (r) => n2(r.pb), "Price ÷ book value."],
  ["div", "Yield", (r) => (r.div ? pct(r.div, 2) : "–"), "Dividends over the last year ÷ price."],
  ["ch1y", "1Y", null],
  ["ma200", "vs 200-day", null, "How far the price is from its 200-day average."],
  ["fromHigh", "From high", null, "Distance below the 52-week high."],
  ["rating", "Analysts", (r) => ratingText(r.rating), "Average rating, where 1 is strong buy and 5 is sell."],
];
const SCREEN_PRESETS = [
  ["Bargains", "Low P/E on a solid size", { capMin: 2, peMax: 15, fpeMax: 15 }],
  ["Dividends", "Yield of 3% or more, not overpriced", { divMin: 3, peMax: 25, capMin: 2 }],
  ["Momentum", "Strong year, near highs", { ch1yMin: 30, above200: true, fromHighMin: -10, capMin: 2 }],
  ["Growth at a fair price", "Earnings expected to rise, modest multiple", { special: "garp", fpeMax: 25, capMin: 5 }],
  ["Beaten-down favorites", "Down 30%+, analysts still like them", { fromHighMax: -30, ratingMax: 2.2, capMin: 10 }],
  ["Mega caps", "Over $200B", { capMin: 200 }],
];
const SCREEN_FIELDS = [["capMin", "Market cap min ($B)"], ["peMax", "P/E max"], ["fpeMax", "Forward P/E max"], ["pbMax", "P/B max"], ["divMin", "Yield min (%)"],
  ["ch1yMin", "1Y change min (%)"], ["fromHighMin", "From high min (%)"], ["fromHighMax", "From high max (%)"], ["ratingMax", "Analyst rating max"]];
const scr = { rows: null, universe: "all", f: {}, sort: "cap", dir: -1, shown: 100, preset: null };

async function renderScreener() {
  pageStart("Screener");
  app.innerHTML = `<section class="home-head"><h1>Stock screener</h1><p class="sub">Filter US-listed companies worth $1B or more. Results update as you type.</p></section><div id="scrBody">${pageLoading("Loading stocks…")}</div>`;
  try { scr.rows = (await getJSON("/api/market/screener")).rows; } catch (e) { $("#scrBody").innerHTML = pageError(e); return; }
  const sectors = [...new Set(scr.rows.map((r) => r.sector).filter(Boolean))].sort();
  $("#scrBody").innerHTML = `
    <div class="card">
      <div class="row-between">
        <div class="seg" id="scrUni">${[["all", "All US"], ["sp500", "S&P 500"], ["ndx", "Nasdaq-100"], ["dow", "Dow 30"], ["watch", "Watchlist"]].map(([k, l]) => `<button data-u="${k}" class="${k === scr.universe ? "on" : ""}">${l}</button>`).join("")}</div>
        <div style="display:flex;gap:8px"><button class="btn" id="scrClear">Clear filters</button><button class="btn" id="scrCsv">⬇ CSV</button></div>
      </div>
      <div class="chips left presets" id="scrPresets">${SCREEN_PRESETS.map(([n, d], i) => `<button class="chip" data-p="${i}" data-tip="${esc(d)}">${esc(n)}</button>`).join("")}</div>
      <div class="filters">
        ${SCREEN_FIELDS.map(([k, l]) => `<label>${l}<input type="number" step="any" data-f="${k}" inputmode="decimal"></label>`).join("")}
        <label>Sector (S&P/Nasdaq-100)<select data-f="sector"><option value="">Any</option>${sectors.map((s) => `<option>${esc(s)}</option>`).join("")}</select></label>
        <label class="check-l"><input type="checkbox" data-f="above200"> Above 200-day average</label>
      </div>
    </div>
    <p class="muted" id="scrCount" style="margin:14px 2px 8px"></p>
    <div class="tbl-wrap"><table class="plain sortable"><thead><tr id="scrHead"></tr></thead><tbody id="scrRows"></tbody></table></div>
    <div style="text-align:center;margin-top:14px"><button class="btn" id="scrMore" hidden>Show more</button></div>`;
  syncScreenInputs();
  $("#scrUni").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; scr.universe = b.dataset.u; [...$("#scrUni").children].forEach((x) => x.classList.toggle("on", x === b)); drawScreener(true); };
  $("#scrPresets").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; scr.f = { ...SCREEN_PRESETS[b.dataset.p][2] }; scr.preset = +b.dataset.p; syncScreenInputs(); drawScreener(true); };
  $("#scrClear").onclick = () => { scr.f = {}; scr.preset = null; syncScreenInputs(); drawScreener(true); };
  $("#scrMore").onclick = () => { scr.shown += 100; drawScreener(); };
  $("#scrCsv").onclick = () => { const r = screenRows(); downloadRows("kash-screener.csv", ["Ticker", "Name", "Sector", "Market cap", "Price", "Today", "P/E", "Fwd P/E", "P/B", "Yield", "1Y", "vs 200-day", "From high", "Rating"], r.map((x) => [x.symbol, x.name, x.sector, x.cap, x.price, x.change, x.pe, x.fpe, x.pb, x.div, x.ch1y, x.ma200, x.fromHigh, x.rating])); };
  document.querySelectorAll(".filters [data-f]").forEach((el) => {
    el.oninput = () => {
      const k = el.dataset.f;
      const v = el.type === "checkbox" ? el.checked : el.value;
      if (v === "" || v === false) delete scr.f[k]; else scr.f[k] = el.type === "number" ? +v : v;
      scr.preset = null; drawScreener(true);
    };
  });
  drawScreener(true);
}
function syncScreenInputs() {
  document.querySelectorAll(".filters [data-f]").forEach((el) => { const v = scr.f[el.dataset.f]; if (el.type === "checkbox") el.checked = !!v; else el.value = v ?? ""; });
  document.querySelectorAll("#scrPresets button").forEach((b) => b.classList.toggle("on", +b.dataset.p === scr.preset));
}
function screenRows() {
  const f = scr.f, w = new Set(watch.all());
  const ok = (v, lo, hi) => (lo == null || (v != null && v >= lo)) && (hi == null || (v != null && v <= hi));
  const out = scr.rows.filter((r) => {
    if (scr.universe === "watch" ? !w.has(r.symbol) : scr.universe !== "all" && !r.indexes.includes(scr.universe)) return false;
    if (!ok(r.cap, f.capMin != null ? f.capMin * 1e9 : null, null)) return false;
    if (f.peMax != null && !(r.pe > 0 && r.pe <= f.peMax)) return false;
    if (f.fpeMax != null && !(r.fpe > 0 && r.fpe <= f.fpeMax)) return false;
    if (f.pbMax != null && !(r.pb > 0 && r.pb <= f.pbMax)) return false;
    if (!ok(r.div, f.divMin != null ? f.divMin / 100 : null, null)) return false;
    if (!ok(r.ch1y, f.ch1yMin != null ? f.ch1yMin / 100 : null, null)) return false;
    if (!ok(r.fromHigh, f.fromHighMin != null ? f.fromHighMin / 100 : null, f.fromHighMax != null ? f.fromHighMax / 100 : null)) return false;
    if (!ok(r.rating, null, f.ratingMax)) return false;
    if (f.sector && r.sector !== f.sector) return false;
    if (f.above200 && !(r.ma200 > 0)) return false;
    if (f.special === "garp" && !(r.pe > 0 && r.fpe > 0 && r.fpe < r.pe * 0.8)) return false;
    return true;
  });
  const k = scr.sort, d = scr.dir;
  out.sort((a, b) => (a[k] == null ? 1 : b[k] == null ? -1 : (a[k] - b[k]) * d));
  return out;
}
function drawScreener(reset) {
  if (!$("#scrRows")) return;
  if (reset) scr.shown = 100;
  const rows = screenRows();
  $("#scrCount").textContent = `${rows.length.toLocaleString()} ${rows.length === 1 ? "company matches" : "companies match"}.`;
  $("#scrHead").innerHTML = `<th class="l">Company</th>` + SCREEN_COLS.map(([k, l, , tip]) => `<th data-sort="${k}" ${tip ? `data-tip="${esc(tip)}"` : ""} class="${scr.sort === k ? "sorted" : ""}">${l}${scr.sort === k ? (scr.dir < 0 ? " ▼" : " ▲") : ""}</th>`).join("");
  $("#scrHead").onclick = (e) => { const t = e.target.closest("[data-sort]"); if (!t) return; const k = t.dataset.sort; if (scr.sort === k) scr.dir = -scr.dir; else { scr.sort = k; scr.dir = ["pe", "fpe", "pb", "rating"].includes(k) ? 1 : -1; } drawScreener(); };
  $("#scrRows").innerHTML = rows.slice(0, scr.shown).map((r) => `<tr><td class="l co">${starBtn(r.symbol)}${stockLink(r.symbol)}<div class="muted small nm">${esc(r.name || "")}</div></td>` +
    SCREEN_COLS.map(([k, , f]) => (f ? `<td>${f(r)}</td>` : chgCell(r[k]))).join("") + `</tr>`).join("") || `<tr><td colspan="12" class="muted" style="text-align:center;padding:30px">No companies match these filters.</td></tr>`;
  $("#scrMore").hidden = rows.length <= scr.shown;
}

/* ------------------------------------------------------------ Compare */
const cmp = { tickers: [], data: {}, range: "5Y" };
function renderCompare() {
  pageStart("Compare");
  const q = new URLSearchParams(location.search).get("t");
  if (q) cmp.tickers = [...new Set(q.toUpperCase().split(",").map((x) => x.trim()).filter(Boolean))].slice(0, 5);
  else if (!cmp.tickers.length) cmp.tickers = watch.all().slice(0, 3);
  if (!cmp.tickers.length) cmp.tickers = ["AAPL", "MSFT", "GOOGL"];
  app.innerHTML = `<section class="home-head"><h1>Compare stocks</h1><p class="sub">Up to five companies side by side. The best value on each line is highlighted.</p></section>
    <div class="card"><form id="cmpForm" class="cmp-form"><div id="cmpTags" class="chips left"></div><input id="cmpAdd" placeholder="Add a ticker, e.g. NVDA" autocomplete="off" spellcheck="false"><button class="btn">Add</button></form></div>
    <div id="cmpBody">${pageLoading("Comparing…")}</div>`;
  $("#cmpForm").onsubmit = (e) => { e.preventDefault(); const v = $("#cmpAdd").value.trim().toUpperCase(); $("#cmpAdd").value = ""; if (v && !cmp.tickers.includes(v) && cmp.tickers.length < 5) { cmp.tickers.push(v); loadCompare(); } };
  $("#cmpTags").onclick = (e) => { const b = e.target.closest("[data-x]"); if (!b) return; cmp.tickers = cmp.tickers.filter((t) => t !== b.dataset.x); loadCompare(); };
  loadCompare();
}
async function loadCompare() {
  history.replaceState({}, "", "/compare?t=" + cmp.tickers.join(","));
  $("#cmpTags").innerHTML = cmp.tickers.map((t) => `<span class="chip">${esc(t)} <button type="button" class="x" data-x="${esc(t)}" aria-label="Remove ${esc(t)}">×</button></span>`).join("");
  $("#cmpBody").innerHTML = pageLoading("Comparing…");
  const wanted = cmp.tickers.slice();
  await Promise.all(wanted.map(async (t) => {
    if (cache[t]) { cmp.data[t] = cache[t]; return; }
    try { cmp.data[t] = cache[t] = await getJSON("/api/stock/" + encodeURIComponent(t)); } catch { cmp.data[t] = null; }
  }));
  if (location.pathname !== "/compare" || wanted.join() !== cmp.tickers.join()) return;
  drawCompare();
}
const finLatest = (d, table, key) => { const r = (d.financials.annual[table] || []).find((x) => x.key === key); return r ? r.values.find((v) => v != null) ?? null : null; };
const finYears = (d, table, key) => { const f = d.financials.annual, cols = f.columns[table] || [], r = (f[table] || []).find((x) => x.key === key); if (!r) return []; return cols.map((c, i) => (c.date ? r.values[i] : undefined)).filter((v) => v !== undefined); };
function cagrOf(vals, n) { const v = vals.slice(0, n + 1); if (v.length <= 1 || v[0] == null || v[v.length - 1] == null || v[0] <= 0 || v[v.length - 1] <= 0) return null; return Math.pow(v[0] / v[v.length - 1], 1 / (v.length - 1)) - 1; }
const CMP_ROWS = [
  ["Size and price"],
  ["Market cap", (d) => d.info.marketCap, (v) => money(v, 1)],
  ["Price", (d) => d.info.currentPrice, fmtPrice],
  ["1-year return*", (d) => d.returns?.find((x) => x.period === "1Y")?.total, signed, 1],
  ["5-year return*", (d) => d.returns?.find((x) => x.period === "5Y")?.total, signed, 1],
  ["Valuation"],
  ["P/E", (d) => d.info.trailingPE, mult, -1],
  ["Forward P/E", (d) => d.info.forwardPE, mult, -1],
  ["Price / sales", (d) => d.info.priceToSalesTrailing12Months, mult, -1],
  ["EV / EBITDA", (d) => d.info.enterpriseToEbitda, mult, -1],
  ["Dividend yield", (d) => d.info.dividendYieldCalc, (v) => pct(v, 2), 1],
  ["Growth (5-year average)"],
  ["Revenue growth", (d) => cagrOf(finYears(d, "income", "revenue"), 5), signed, 1],
  ["Earnings per share growth", (d) => cagrOf(finYears(d, "income", "eps"), 5), signed, 1],
  ["Free cash flow growth", (d) => cagrOf(finYears(d, "cashflow", "fcf"), 5), signed, 1],
  ["Profitability"],
  ["Gross margin", (d) => finLatest(d, "income", "gross_margin"), pct, 1],
  ["Operating margin", (d) => finLatest(d, "income", "op_margin"), pct, 1],
  ["Net margin", (d) => finLatest(d, "income", "net_margin"), pct, 1],
  ["Return on invested capital", (d) => finYears(d, "ratios", "roic").find((v) => v != null), pct, 1],
  ["Financial strength"],
  ["Debt / EBITDA", (d) => finYears(d, "ratios", "debt_ebitda").find((v) => v != null), mult, -1],
  ["Piotroski score (0-9)", (d) => d.quality?.piotroski?.score, (v) => String(v), 1],
  ["Altman Z-score", (d) => d.quality?.altman?.score, (v) => v.toFixed(2), 1],
  ["Kash scorecard", (d) => (d.scorecard?.length ? d.scorecard.filter((c) => c.pass).length / d.scorecard.length : null), (v, d) => (d ? `${d.scorecard.filter((c) => c.pass).length}/${d.scorecard.length}` : pct(v, 0)), 1],
];
function drawCompare() {
  const ok = cmp.tickers.filter((t) => cmp.data[t]), bad = cmp.tickers.filter((t) => !cmp.data[t]);
  if (!ok.length) { $("#cmpBody").innerHTML = `<div class="error"><h3>Nothing to compare yet</h3><p class="sub">Add a ticker above.</p></div>`; return; }
  const body = CMP_ROWS.map((r) => {
    if (r.length === 1) return `<tr class="grp"><td colspan="${ok.length + 1}">${r[0]}</td></tr>`;
    const [label, get, show, better] = r;
    const vals = ok.map((t) => { const v = get(cmp.data[t]); return v == null || Number.isNaN(v) ? null : v; });
    const present = vals.filter((v) => v != null && (better !== -1 || v > 0));
    const best = better && present.length > 1 ? (better > 0 ? Math.max(...present) : Math.min(...present)) : null;
    return `<tr><td class="l">${label}</td>${vals.map((v, i) => `<td class="${best != null && v === best ? "best" : ""}">${v == null ? "–" : show(v, cmp.data[ok[i]])}</td>`).join("")}</tr>`;
  }).join("");
  $("#cmpBody").innerHTML = `${bad.length ? `<p class="note">Couldn't find: ${bad.map(esc).join(", ")}.</p>` : ""}
    <section><div class="card"><div class="row-between"><h3>Share price performance</h3><div class="seg" id="cmpR">${["1Y", "3Y", "5Y", "10Y"].map((r) => `<button data-r="${r}" class="${r === cmp.range ? "on" : ""}">${r}</button>`).join("")}</div></div>
      <div class="chart-box tall" style="margin-top:14px"><canvas id="cCmp"></canvas></div></div></section>
    <section><div class="tbl-wrap"><table class="plain cmp"><thead><tr><th class="l"></th>${ok.map((t) => `<th>${stockLink(t)}<div class="muted small">${esc((cmp.data[t].info.shortName || "").slice(0, 22))}</div></th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">*With dividends reinvested. Chart shows price only.</p></section>`;
  $("#cmpR").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; cmp.range = b.dataset.r; [...$("#cmpR").children].forEach((x) => x.classList.toggle("on", x === b)); chartCompare(ok); };
  chartCompare(ok);
}
const PALETTE = ["--c1", "--c2", "--c3", "--c4", "--ink-3", "--brand"];
function chartCompare(ok) {
  const yrs = +cmp.range.replace("Y", ""), start = Date.now() - yrs * 365.25 * 864e5;
  const datasets = ok.map((t, i) => {
    const s = cmp.data[t].prices.filter((p) => p[0] >= start), base = s[0]?.[1];
    return { label: t, data: s.map((p) => ({ x: p[0], y: (p[1] / base - 1) * 100 })), borderColor: css(PALETTE[i]), backgroundColor: css(PALETTE[i]), borderWidth: 2, pointRadius: 0, tension: 0.1 };
  });
  relineChart("cCmp", datasets, (v) => (v > 0 ? "+" : "") + v.toFixed(0) + "%", (y) => (y > 0 ? "+" : "") + y.toFixed(1) + "%");
}
function relineChart(id, datasets, tick, tipV, extra = {}) {
  const old = state.charts.find((c) => c.canvas.id === id);
  if (old) { old.destroy(); state.charts = state.charts.filter((c) => c !== old); }
  const multi = datasets.filter((d) => !d.helper).length > 1;
  mk(id, { type: "line", data: { datasets }, plugins: extra.plugins || [], options: baseOpts({
    plugins: { ...baseOpts().plugins, ...(extra.pluginOpts || {}),
      legend: { display: multi, position: "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 14, filter: (l) => !datasets[l.datasetIndex].helper } },
      tooltip: { ...baseOpts().plugins.tooltip, filter: (it) => !datasets[it.datasetIndex].helper, callbacks: {
        title: (it) => new Date(it[0].parsed.x).toLocaleDateString(undefined, extra.monthly ? { month: "short", year: "numeric" } : { dateStyle: "medium" }),
        label: (it) => (multi ? it.dataset.label + ": " : "") + tipV(it.parsed.y) } } },
    scales: {
      x: { type: "linear", grid: { display: false }, min: extra.min, max: extra.max, ticks: { maxTicksLimit: 7, callback: (v) => new Date(v).toLocaleDateString(undefined, extra.span > 1200 * 864e5 || !extra.span ? { year: "numeric" } : { month: "short", year: "2-digit" }) } },
      y: { type: extra.log ? "logarithmic" : "linear", grid: { color: css("--line") }, border: { display: false }, ticks: { maxTicksLimit: 7, callback: tick } },
    },
  }) });
}

/* ------------------------------------------------------------ Earnings */
const earn = { data: null, scope: "all" };
async function renderEarnings() {
  pageStart("Earnings");
  app.innerHTML = `<section class="home-head"><h1>Earnings calendar</h1><p class="sub">Companies worth $2B or more reporting in the next three weeks. Dates marked "est." aren't confirmed.</p></section>
    <div class="seg" id="earnScope">${[["all", "All"], ["sp500", "S&P 500"], ["watch", "Watchlist"]].map(([k, l]) => `<button data-s="${k}" class="${k === earn.scope ? "on" : ""}">${l}</button>`).join("")}</div><div id="earnBody">${pageLoading("Loading the calendar…")}</div>`;
  $("#earnScope").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; earn.scope = b.dataset.s; [...$("#earnScope").children].forEach((x) => x.classList.toggle("on", x === b)); drawEarnings(); };
  try { earn.data = await getJSON("/api/market/earnings"); } catch (e) { $("#earnBody").innerHTML = pageError(e); return; }
  drawEarnings();
}
function drawEarnings() {
  const box = $("#earnBody"); if (!box) return;
  const w = new Set(watch.all());
  const pick = (r) => (earn.scope === "all" ? true : earn.scope === "watch" ? w.has(r.symbol) : r.indexes.includes("sp500"));
  const days = earn.data.days.map((d) => ({ ...d, rows: d.rows.filter(pick) })).filter((d) => d.rows.length);
  box.innerHTML = days.length ? days.map((d) => `<section><h3 class="day">${new Date(d.date + "T12:00:00").toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })} <span class="muted">· ${d.rows.length}</span></h3>
    <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Company</th><th>Market cap</th><th>Price</th><th>P/E</th><th>Fwd P/E</th><th>1Y</th><th>Analysts</th></tr></thead><tbody>
    ${d.rows.slice(0, 12).map(earnRow).join("")}</tbody>${d.rows.length > 12 ? `<tbody hidden data-rest>${d.rows.slice(12).map(earnRow).join("")}</tbody>` : ""}</table></div>
    ${d.rows.length > 12 ? `<button class="btn more" data-more>Show all ${d.rows.length}</button>` : ""}</section>`).join("") : `<div class="card muted" style="margin-top:18px">Nothing scheduled${earn.scope === "watch" ? " for your watchlist" : ""} in the next three weeks.</div>`;
  box.onclick = (e) => { const b = e.target.closest("[data-more]"); if (!b) return; b.previousElementSibling.querySelector("[data-rest]").hidden = false; b.remove(); };
}
const earnRow = (r) => `<tr><td class="l co">${starBtn(r.symbol)}${stockLink(r.symbol)}${r.estimated ? ` <span class="muted small">est.</span>` : ""}<div class="muted small nm">${esc(r.name || "")}</div></td><td>${money(r.cap, 1)}</td><td>${fmtPrice(r.price)}</td><td>${n2(r.pe)}</td><td>${n2(r.fpe)}</td>${chgCell(r.ch1y)}<td>${ratingText(r.rating)}</td></tr>`;

/* ------------------------------------------------------------ Macro */
const mac = { data: null, range: "All" };
const MAC_FMT = { pct0: (v) => v.toFixed(0) + "%", pct1: (v) => v.toFixed(1) + "%", pct2: (v) => v.toFixed(2) + "%", x: (v) => v.toFixed(1) + "×", pctSigned: (v) => (v > 0 ? "+" : "") + v.toFixed(0) + "%", ptsSigned: (v) => (v > 0 ? "+" : "") + v.toFixed(2), pts: (v) => v.toFixed(2), num1: (v) => v.toFixed(1) };
const recessionBands = {
  id: "recessionBands",
  beforeDatasetsDraw(chart, _a, o) {
    const { ctx, chartArea: a, scales: { x } } = chart;
    ctx.save(); ctx.fillStyle = css("--soft");
    for (const [s, e] of o.spans || []) {
      if (e < x.min || s > x.max) continue;
      const x0 = Math.max(a.left, x.getPixelForValue(s)), x1 = Math.min(a.right, x.getPixelForValue(e));
      ctx.fillRect(x0, a.top, Math.max(1, x1 - x0), a.height);
    }
    ctx.restore();
  },
};
async function renderMacro() {
  pageStart("Macro");
  app.innerHTML = `<section class="home-head"><h1>Is the market expensive?</h1><p class="sub">Long-run valuation, recession and sentiment gauges for the whole US market.</p></section><div id="macBody">${pageLoading("Loading long-run data…")}</div>`;
  try { mac.data = await getJSON("/api/macro"); } catch (e) { $("#macBody").innerHTML = pageError(e); return; }
  drawMacro();
}
function drawMacro() {
  const box = $("#macBody"); if (!box) return;
  const d = mac.data, v = d.verdict, cards = (g) => d.cards.filter((c) => c.group === g);
  const section = (g, title, sub) => `<section><h2>${title}</h2><p class="sub">${sub}</p><div class="grid g2">${cards(g).map(macCard).join("")}</div></section>`;
  box.innerHTML = `
    ${v ? `<div class="card verdict-card tone-${v.tone}"><div><div class="k">Overall valuation</div><div class="big-v">${esc(v.label)}</div></div>
      <p>${v.over} of ${v.total} valuation models read overvalued${v.under ? ` and ${v.under} undervalued` : ""}. Combined, the market is about <b>${v.score > 0 ? "+" : ""}${v.score.toFixed(1)} standard deviations</b> from its historical norm. Expensive markets can stay expensive for years, so this says more about likely long-term returns than next month's direction.</p></div>` : ""}
    <div class="row-between mac-ctl"><span class="muted">Grey bands mark US recessions.</span><div class="seg" id="macR">${["10Y", "30Y", "All"].map((r) => `<button data-r="${r}" class="${r === mac.range ? "on" : ""}">${r}</button>`).join("")}</div></div>
    ${section("valuation", "Valuation", "How today's prices compare with history.")}
    ${section("recession", "Recession signals", "Indicators with a track record of flagging downturns.")}
    ${section("sentiment", "Sentiment", "How nervous or confident investors are. Extremes are often read as contrarian signals.")}
    ${d.curve ? `<section><h2>Treasury yield curve</h2><p class="sub">What the US government pays to borrow for different lengths of time. Normally longer loans pay more.</p><div class="card"><div class="chart-box"><canvas id="cCurve"></canvas></div></div></section>` : ""}
    ${d.economy.length ? `<section><h2>The economy now</h2><div class="econ">${d.economy.map((e) => `<div class="card"><div class="k">${esc(e.title)}</div><div class="v num">${MAC_FMT[e.fmt](e.value)}</div><div class="muted small">${esc(e.date)}${e.prev != null ? ` · ${MAC_FMT[e.fmt](e.prev)} a year ago` : ""}</div></div>`).join("")}</div></section>` : ""}
    <p class="note">Sources: Federal Reserve Economic Data (FRED), Robert Shiller via multpl.com, Cboe via Yahoo Finance.${d.failed.length ? ` Some indicators couldn't load right now.` : ""}</p>`;
  $("#macR").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; mac.range = b.dataset.r; [...$("#macR").children].forEach((x) => x.classList.toggle("on", x === b)); chartMacro(); };
  chartMacro();
  if (d.curve) mk("cCurve", { type: "line", data: { labels: d.curve.labels, datasets: [
    { label: d.curve.date, data: d.curve.now, borderColor: css("--brand-ink"), backgroundColor: css("--brand-ink"), borderWidth: 2.5, pointRadius: 4 },
    { label: "1 year ago", data: d.curve.year, borderColor: css("--c3"), backgroundColor: css("--c3"), borderWidth: 1.5, pointRadius: 2 },
    { label: "3 years ago", data: d.curve.three, borderColor: css("--ink-3"), backgroundColor: css("--ink-3"), borderWidth: 1.5, pointRadius: 2 }] },
    options: baseOpts({ scales: { x: { grid: { display: false } }, y: { grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => v.toFixed(1) + "%" } } } }) });
}
const macCard = (c) => `<div class="card"><div class="row-between mc-head"><h3>${esc(c.title)}${info(c.what)}</h3><span class="badge tone-${c.tone}">${esc(c.label)}</span></div>
  <div class="big-v num">${MAC_FMT[c.fmt](c.value)}</div><p class="sub mc-text">${esc(c.text)}</p><div class="chart-box short"><canvas id="m_${c.key}"></canvas></div><div class="muted small" style="margin-top:6px">${esc(c.source || "")}</div></div>`;
function chartMacro() {
  const d = mac.data, now = Date.now(), yrs = { "10Y": 10, "30Y": 30 }[mac.range], start = yrs ? now - yrs * 365.25 * 864e5 : null;
  for (const c of d.cards) {
    if (!c.series.length) continue;
    const cut = (s) => (start ? s.filter((p) => p[0] >= start) : s).map((p) => ({ x: p[0], y: p[1] }));
    const main = cut(c.series), col = css("--brand-ink");
    const helper = (data, color, dash = [4, 4]) => ({ data, borderColor: color, borderWidth: 1, borderDash: dash, pointRadius: 0, fill: false, helper: true });
    const sets = [{ label: c.title, data: main, borderColor: col, borderWidth: 1.8, pointRadius: 0, tension: 0 }];
    const ends = main.length ? [main[0].x, main[main.length - 1].x] : [];
    if (c.trend) {
      const t = cut(c.trend);
      sets.push(helper(t, css("--ink-3")));
      for (const n of [1, 2]) { const up = Math.exp(n * c.bands); sets.push(helper(t.map((p) => ({ x: p.x, y: p.y * up })), css("--bad") + (n === 2 ? "aa" : "66"), [2, 3])); sets.push(helper(t.map((p) => ({ x: p.x, y: p.y / up })), css("--good") + (n === 2 ? "aa" : "66"), [2, 3])); }
    } else if (c.average != null) sets.push(helper(ends.map((x) => ({ x, y: c.average })), css("--ink-3")));
    if (c.zero) sets.push(helper(ends.map((x) => ({ x, y: 0 })), css("--bad")));
    if (c.threshold != null) sets.push(helper(ends.map((x) => ({ x, y: c.threshold })), css("--bad")));
    relineChart("m_" + c.key, sets, (v) => (Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + "k" : +v.toFixed(2)), MAC_FMT[c.fmt], { plugins: [recessionBands], pluginOpts: { recessionBands: { spans: d.recessions } }, monthly: true, min: ends[0], max: ends[1], span: 1e15, log: c.log });
  }
}

/* ------------------------------------------------------------ Assets */
const ast = { data: null, sel: ["BTC-USD"], hist: {}, range: "1Y", group: "All" };
async function renderAssets() {
  pageStart("Assets");
  app.innerHTML = `<section class="home-head"><h1>Crypto, metals and more</h1><p class="sub">Trends across the things stocks get compared to. Click rows to chart them; pick several to compare.</p></section><div id="astBody">${pageLoading("Loading prices…")}</div>`;
  try { ast.data = await getJSON("/api/assets"); } catch (e) { $("#astBody").innerHTML = pageError(e); return; }
  drawAssets();
}
const AST_RANGES = { "1M": 31, "3M": 92, "6M": 183, "1Y": 366, "5Y": 1827, "10Y": 3653, Max: null };
function drawAssets() {
  const box = $("#astBody"); if (!box) return;
  const d = ast.data;
  const row = (r, flag) => `<tr data-a="${esc(r.symbol)}" class="${ast.sel.includes(r.symbol) ? "sel" : ""}"><td class="l"><b>${esc(r.name)}</b>${flag ? "" : `<div class="muted small">${esc(r.symbol)}</div>`}</td><td>${fmtPrice(r.price)}</td>${["d1", "w1", "m1", "ytd", "y1", "y5"].map((k) => chgCell(r[k])).join("")}${r.fromAth != null ? chgCell(r.fromAth, 0) : "<td>–</td>"}<td class="spark">${spark(r.spark)}</td></tr>`;
  const head = `<thead><tr><th class="l">Asset</th><th>Price</th><th>1D</th><th>1W</th><th>1M</th><th>YTD</th><th>1Y</th><th>5Y</th><th data-tip="Distance below its all-time high (closing prices).">From high</th><th></th></tr></thead>`;
  const groups = d.groups.filter((g) => ast.group === "All" || g.name === ast.group);
  box.innerHTML = `
    <div class="card"><div class="row-between"><div><h3 id="astTitle"></h3><div class="muted" id="astSub"></div></div>
      <div class="seg" id="astR">${Object.keys(AST_RANGES).map((r) => `<button data-r="${r}" class="${r === ast.range ? "on" : ""}">${r}</button>`).join("")}</div></div>
      <div class="chart-box tall" style="margin-top:14px"><canvas id="cAst"></canvas></div></div>
    <section><div class="seg" id="astG">${["All", ...d.groups.map((g) => g.name)].map((g) => `<button data-g="${esc(g)}" class="${g === ast.group ? "on" : ""}">${esc(g)}</button>`).join("")}</div>
      ${groups.map((g) => `<h3 class="day">${esc(g.name)}</h3><div class="tbl-wrap"><table class="plain pick">${head}<tbody>${g.rows.map((r) => row(r)).join("")}</tbody></table></div>`).join("")}</section>
    ${ast.group === "All" && d.ratios.length ? `<section><h2>Ratios</h2><p class="sub">One asset priced in another, which strips out dollar moves.</p><div class="tbl-wrap"><table class="plain pick">${head}<tbody>${d.ratios.map((r) => row(r, true)).join("")}</tbody></table></div></section>` : ""}
    <p class="note">Futures prices for commodities, closing prices from Yahoo Finance. Crypto trades every day; other markets don't.</p>`;
  box.onclick = (e) => {
    const g = e.target.closest("[data-g]"); if (g) { ast.group = g.dataset.g; drawAssets(); return; }
    const r = e.target.closest("[data-r]"); if (r) { ast.range = r.dataset.r; [...$("#astR").children].forEach((x) => x.classList.toggle("on", x === r)); chartAssets(); return; }
    const a = e.target.closest("[data-a]"); if (!a) return;
    const s = a.dataset.a, i = ast.sel.indexOf(s);
    if (i >= 0) { if (ast.sel.length > 1) ast.sel.splice(i, 1); } else { ast.sel.push(s); if (ast.sel.length > 6) ast.sel.shift(); }
    document.querySelectorAll("[data-a]").forEach((x) => x.classList.toggle("sel", ast.sel.includes(x.dataset.a)));
    chartAssets();
  };
  chartAssets();
}
async function chartAssets() {
  const sel = ast.sel.slice();
  await Promise.all(sel.map(async (s) => { if (!ast.hist[s]) { try { ast.hist[s] = await getJSON(`/api/assets/${encodeURIComponent(s)}/history`); } catch {} } }));
  if (!$("#cAst") || sel.join() !== ast.sel.join()) return;
  const have = sel.filter((s) => ast.hist[s]);
  if (!have.length) return;
  const days = AST_RANGES[ast.range], start = days ? Date.now() - days * 864e5 : null;
  const multi = have.length > 1;
  const datasets = have.map((s, i) => {
    let p = ast.hist[s].points; if (start) p = p.filter((x) => x[0] >= start);
    if (p.length > 1500) { const st = Math.ceil(p.length / 1500); p = p.filter((_, k) => k % st === 0 || k === p.length - 1); }
    const base = p[0]?.[1];
    return { label: ast.hist[s].name, data: p.map((x) => ({ x: x[0], y: multi ? (x[1] / base - 1) * 100 : x[1] })), borderColor: css(PALETTE[i % PALETTE.length]), backgroundColor: css(PALETTE[i % PALETTE.length]), borderWidth: 2, pointRadius: 0, tension: 0.1 };
  });
  const first = datasets[0].data, chg = first.length ? (multi ? first[first.length - 1].y / 100 : first[first.length - 1].y / first[0].y - 1) : null;
  $("#astTitle").textContent = multi ? "Compared" : ast.hist[have[0]].name;
  $("#astSub").innerHTML = multi ? "Percent change over the period" : `<span class="${chg >= 0 ? "up" : "down"}" style="font-weight:700">${signed(chg)}</span> over ${ast.range === "Max" ? "its full history" : ast.range}`;
  const span = first.length ? first[first.length - 1].x - first[0].x : 0;
  relineChart("cAst", datasets, multi ? (v) => (v > 0 ? "+" : "") + v.toFixed(0) + "%" : (v) => fmtPrice(v), multi ? (y) => (y > 0 ? "+" : "") + y.toFixed(1) + "%" : fmtPrice, { span, min: first[0]?.x, max: first[first.length - 1]?.x });
}

/* ------------------------------------------------------------ Portfolio (kept in this browser only) */
const folio = { all: () => store("kash-portfolio", []), set: (v) => save("kash-portfolio", v), quotes: {} };
async function renderPortfolio() {
  pageStart("Portfolio");
  app.innerHTML = `<section class="home-head"><h1>Portfolio</h1><p class="sub">Track what you own. Holdings are saved only in this browser.</p></section>
    <div class="card"><form id="pfForm" class="pf-form"><input id="pfSym" placeholder="Ticker" autocomplete="off" spellcheck="false" required><input id="pfShares" type="number" step="any" min="0" placeholder="Shares" required><input id="pfCost" type="number" step="any" min="0" placeholder="Average cost per share" required><button class="btn">Add holding</button></form></div>
    <div id="pfBody"></div>`;
  $("#pfForm").onsubmit = (e) => {
    e.preventDefault();
    const s = $("#pfSym").value.trim().toUpperCase(), shares = +$("#pfShares").value, cost = +$("#pfCost").value;
    if (!s || !(shares > 0)) return;
    const all = folio.all(), had = all.find((h) => h.s === s);
    if (had) { had.cost = (had.cost * had.shares + cost * shares) / (had.shares + shares); had.shares += shares; } else all.push({ s, shares, cost });
    folio.set(all); e.target.reset(); loadPortfolio();
  };
  $("#pfBody").onclick = (e) => { const b = e.target.closest("[data-rm]"); if (!b) return; folio.set(folio.all().filter((h) => h.s !== b.dataset.rm)); loadPortfolio(); };
  loadPortfolio();
}
async function loadPortfolio() {
  const all = folio.all(), box = $("#pfBody");
  if (!all.length) { box.innerHTML = `<div class="card muted" style="margin-top:18px">Add your first holding above to see its value, gains and mix.</div>`; return; }
  box.innerHTML = pageLoading("Getting prices…");
  let quotes;
  try { quotes = await getJSON("/api/quotes?symbols=" + encodeURIComponent(all.map((h) => h.s).join(","))); } catch (e) { box.innerHTML = pageError(e); return; }
  if (!$("#pfBody")) return;
  const q = Object.fromEntries(quotes.map((x) => [x.symbol, x]));
  const rows = all.map((h) => { const x = q[h.s] || {}, value = x.price != null ? x.price * h.shares : null, basis = h.cost * h.shares;
    const day = value != null && x.change != null ? value - value / (1 + x.change) : null; return { ...h, price: x.price, change: x.change, value, basis, day, gain: value != null ? value - basis : null, sector: x.sector }; });
  const have = rows.filter((r) => r.value != null), total = have.reduce((a, r) => a + r.value, 0), basis = have.reduce((a, r) => a + r.basis, 0), day = have.reduce((a, r) => a + (r.day || 0), 0);
  rows.sort((a, b) => (b.value ?? -1) - (a.value ?? -1));
  const up = (v) => (v >= 0 ? "up" : "down"), usd = (v) => (v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 }));
  box.innerHTML = `<div class="pills">${pill("Value", usd(total), "")}${pill("Today", `<span class="${up(day)}">${usd(day)}</span>`, total ? signed(day / (total - day), 2) : "")}${pill("Total gain", `<span class="${up(total - basis)}">${usd(total - basis)}</span>`, basis ? signed(total / basis - 1) : "")}</div>
    <section><div class="grid g-about"><div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Holding</th><th>Shares</th><th>Price</th><th>Today</th><th>Value</th><th>Gain</th><th>Weight</th><th></th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td class="l">${stockLink(r.s)}</td><td>${r.shares.toLocaleString()}</td><td>${fmtPrice(r.price)}</td>${chgCell(r.change, 2)}<td>${usd(r.value)}</td><td class="${r.gain == null ? "" : up(r.gain)}">${usd(r.gain)}${r.gain != null && r.basis ? ` <small>(${signed(r.gain / r.basis, 0)})</small>` : ""}</td><td>${r.value != null && total ? pct(r.value / total, 0) : "–"}</td><td><button class="x-btn" data-rm="${esc(r.s)}" aria-label="Remove ${esc(r.s)}">×</button></td></tr>`).join("")}</tbody></table></div>
    <div class="card"><h3>Mix</h3><div class="chart-box"><canvas id="cFolio"></canvas></div></div></div></section>`;
  const cols = ["--c1", "--c2", "--c3", "--c4", "--good", "--warn", "--ink-3", "--bad"];
  destroyCharts();
  mk("cFolio", { type: "doughnut", data: { labels: have.map((r) => r.s), datasets: [{ data: have.map((r) => r.value), backgroundColor: have.map((_, i) => css(cols[i % cols.length])), borderColor: css("--card"), borderWidth: 2 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { position: "bottom", labels: { usePointStyle: true, boxWidth: 8, color: css("--ink-2") } }, tooltip: { callbacks: { label: (it) => `${it.label}: ${usd(it.parsed)} (${pct(it.parsed / total, 0)})` } } } } });
}

const PAGES = { "/portfolio": renderPortfolio, "/screener": renderScreener, "/compare": renderCompare, "/earnings": renderEarnings, "/macro": renderMacro, "/assets": renderAssets };
