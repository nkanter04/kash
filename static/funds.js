/* Kash: Big Investors. What well-known money managers hold, from the 13F reports they file with the SEC each quarter.
   Loaded after pages.js and uses its helpers. */

const FUND_NOTE = `From 13F reports filed with the SEC. They arrive up to 45 days after each quarter ends, so positions may have changed since.
  They list US-listed stocks only: no short positions, bonds, cash or foreign shares. Options are shown separately and left out of the percentages. Not investment advice.`;
const ACTS = { new: ["New", "side-buy"], add: ["Added", "side-buy"], trim: ["Trimmed", "side-sell"], sold: ["Sold all", "side-sell"], hold: ["No change", "muted"] };
const fundLink = (cik, name) => `<a href="/investors/${cik}" data-link><b>${esc(name)}</b></a>`;
const quarter = (p) => (p ? `Q${Math.ceil(+p.slice(5, 7) / 3)} ${p.slice(0, 4)}` : "–");
const holdingName = (p) => (p.ticker ? stockLink(p.ticker) : `<b>${esc(p.name)}</b>`) + `<div class="muted small nm">${esc(p.ticker ? p.name : p.cls || "")}</div>`;
const actCell = (p) => {
  if (!p.act) return `<td class="muted">–</td>`;
  const [label, cls] = ACTS[p.act];
  const by = (p.act === "add" || p.act === "trim") && p.chg != null ? ` ${Math.abs(p.chg) >= 9.995 ? "10×+" : Math.abs(p.chg * 100).toFixed(0) + "%"}` : "";
  return `<td><span class="${cls}">${label}${by}</span></td>`;
};
const names = (list, n = 3) => esc(list.slice(0, n).join(", ")) + (list.length > n ? ` +${list.length - n}` : "");

async function renderFunds() {
  pageStart("Big Investors");
  app.innerHTML = `<section class="home-head"><h1>Big investors</h1><p class="sub" id="fundSub">What well-known investors own, and what they bought and sold last quarter.</p></section>
    <div id="fundBody">${pageLoading("Loading holdings…")}</div>`;
  let d; try { d = await getJSON("/api/funds"); } catch (e) { $("#fundBody").innerHTML = pageError(e); return; }
  const box = $("#fundBody"); if (!box) return;
  if (!d.ready) { box.innerHTML = `<div class="card" style="margin-top:18px"><h3>The holdings aren't downloaded yet</h3><p class="sub" style="margin:0">Run <code>python funds.py build</code> in the project folder.</p></div>`; return; }
  const latest = d.managers.map((m) => m.period).sort().pop();
  $("#fundSub").textContent = `${d.managers.length} well-known investors, from their quarterly SEC reports. Newest report: ${quarter(latest)}. Updated ${fmtDate(d.built)}.`;
  const moveRow = (x, a, b) => `<tr><td class="l co">${stockLink(x.ticker)}<div class="muted small nm">${esc(x.name)}</div></td>
    <td class="l small wrap-text">${x[a].length ? `<span class="${ACTS[a][1]}">${ACTS[a][0]}:</span> ${names(x[a])}` : ""}${x[a].length && x[b].length ? "<br>" : ""}${x[b].length ? `<span class="${ACTS[b][1]}">${ACTS[b][0]}:</span> ${names(x[b])}` : ""}</td></tr>`;
  box.innerHTML = `<section><div class="grid g2">
      <div class="card"><h3 data-tip="Stocks that the most tracked investors newly bought or added to in their latest report, net of those selling.">Most bought last quarter <span class="info">i</span></h3>
        <table class="plain mini"><tbody>${d.buys.slice(0, 8).map((x) => moveRow(x, "new", "add")).join("")}</tbody></table></div>
      <div class="card"><h3 data-tip="Stocks that the most tracked investors sold out of or trimmed in their latest report, net of those buying.">Most sold last quarter <span class="info">i</span></h3>
        <table class="plain mini"><tbody>${d.sells.slice(0, 8).map((x) => moveRow(x, "sold", "trim")).join("")}</tbody></table></div>
      <div class="card"><h3>Most widely owned</h3>
        <table class="plain mini"><tbody>${d.popular.slice(0, 8).map((x) => `<tr><td class="l co">${stockLink(x.ticker)}<div class="muted small nm">${esc(x.name)}</div></td><td class="muted small">${x.holders} investors</td><td>${money(x.value, 1)}</td></tr>`).join("")}</tbody></table></div>
      <div class="card"><h3 data-tip="Stocks held by at least 3 tracked investors, ranked by the average share of their portfolios. A big position says more than a small one.">Biggest bets <span class="info">i</span></h3>
        <table class="plain mini"><tbody>${d.conviction.slice(0, 8).map((x) => `<tr><td class="l co">${stockLink(x.ticker)}<div class="muted small nm">${esc(x.name)}</div></td><td class="muted small">${x.holders} investors</td><td data-tip="Average share of each holder's portfolio">${pct(x.avg, 1)} avg</td></tr>`).join("")}</tbody></table></div>
    </div></section>
    <section><h2>The investors</h2>
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Investor</th><th data-tip="Value of the US stocks in the latest report. Not the whole firm.">Portfolio</th><th>Stocks</th><th data-tip="Share of the portfolio in the 10 largest holdings. Higher means more concentrated.">Top 10</th><th class="l">Largest holdings</th><th>Report</th></tr></thead>
      <tbody>${d.managers.map((m) => `<tr><td class="l co">${fundLink(m.cik, m.investor)}<div class="muted small nm">${esc(m.firm)}</div></td>
        <td>${money(m.value, 1)}</td><td>${m.positions}</td><td>${pct(m.top10, 0)}</td>
        <td class="l small">${m.top.map((t) => `${t.ticker ? `<a href="/stock/${encodeURIComponent(t.ticker)}" data-link>${esc(t.ticker)}</a>` : esc(t.name)} <span class="muted">${pct(t.pct, 0)}</span>`).join(" · ")}</td>
        <td class="muted small">${m.stale ? `<span class="late-tag" data-tip="No newer report: this investor has stopped filing, so the holdings are old.">${quarter(m.period)}</span>` : quarter(m.period)}</td></tr>`).join("")}</tbody></table></div>
      <p class="note">${FUND_NOTE}</p></section>`;
}

const fundState = { d: null, filter: "all", shown: 60 };
async function renderFund(cik) {
  pageStart("Investor");
  app.innerHTML = pageLoading("Loading holdings…");
  let d; try { d = await getJSON("/api/funds/" + encodeURIComponent(cik)); } catch (e) { app.innerHTML = pageError(e); return; }
  Object.assign(fundState, { d, filter: "all", shown: 60 });
  document.title = `${d.manager.investor} · Kash`;
  drawFund();
}
function drawFund() {
  const { d, filter } = fundState, m = d.manager;
  const stocks = d.positions.filter((p) => !p.kind), options = d.positions.filter((p) => p.kind && p.act !== "sold");
  const held = stocks.filter((p) => p.act !== "sold");
  const n = (a) => stocks.filter((p) => p.act === a).length;
  const top = held[0];
  const rows = filter === "all" ? held : stocks.filter((p) => p.act === filter);
  const h = d.history, first = h[0], last = h[h.length - 1];
  app.innerHTML = `<section class="home-head"><p class="sub" style="margin:22px 0 0"><a href="/investors" data-link>← Big investors</a></p>
      <h1 style="margin-top:6px">${esc(m.investor)}</h1><p class="sub">${esc(m.firm)} · ${quarter(m.period)} report, filed ${fmtDate(m.filed)}</p></section>
    <div class="pills" style="margin-top:16px">
      ${pill("Portfolio", money(m.value, 1), "US stocks reported")}
      ${pill("Stocks", m.positions, "positions")}
      ${pill("Top 10", pct(held.slice(0, 10).reduce((s, p) => s + p.pct, 0), 0), "of the portfolio")}
      ${pill("Last quarter", `${n("new")} new · ${n("sold")} sold`, `${n("add")} added · ${n("trim")} trimmed`)}
    </div>
    ${top ? `<div class="takeaway" style="margin-top:14px">Largest holding: <b>${esc(top.ticker || top.name)}</b> at ${pct(top.pct, 1)} of the portfolio.
      ${h.length > 1 ? `Reported value went from ${money(first.value, 1)} (${quarter(first.period)}) to ${money(last.value, 1)}, which reflects both price changes and trading.` : ""}</div>` : ""}
    <section><div class="seg filter" id="fundF">${[["all", `Holdings (${held.length})`], ["new", `New (${n("new")})`], ["add", `Added (${n("add")})`], ["trim", `Trimmed (${n("trim")})`], ["sold", `Sold all (${n("sold")})`]]
      .map(([k, l]) => `<button data-f="${k}" class="${k === filter ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Stock</th><th data-tip="Share of the reported stock portfolio.">% of portfolio</th><th>Value</th><th>Shares</th><th data-tip="Change in the number of shares since the previous quarterly report.">Since last report</th></tr></thead>
      <tbody>${rows.slice(0, fundState.shown).map((p) => `<tr><td class="l co">${holdingName(p)}</td>
        <td>${p.pct != null ? `<div class="pbar"><i style="width:${Math.min(100, (p.pct / (held[0]?.pct || 1)) * 100)}%"></i><span>${pct(p.pct, 1)}</span></div>` : "–"}</td>
        <td>${p.act === "sold" ? `<span class="muted" data-tip="Value in the previous report">${money(p.prevValue, 1)}</span>` : money(p.value, 1)}</td><td>${p.act === "sold" ? "–" : count(p.shares)}</td>${actCell(p)}</tr>`).join("") || `<tr><td class="muted l" colspan="5">Nothing in this group last quarter.</td></tr>`}</tbody></table></div>
      ${rows.length > fundState.shown ? `<div style="text-align:center;margin-top:14px"><button class="btn" id="fundMore">Show more</button></div>` : ""}</section>
    ${options.length ? `<section><h2>Options</h2><p class="sub">Puts profit when a stock falls and calls when it rises. The value shown is that of the underlying shares, not what was paid.</p>
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Stock</th><th class="l">Type</th><th>Underlying value</th><th>Since last report</th></tr></thead>
      <tbody>${options.slice(0, 40).map((p) => `<tr><td class="l co">${holdingName(p)}</td><td class="l"><span class="${p.kind === "Put" ? "side-sell" : "side-buy"}">${esc(p.kind)}</span></td><td>${money(p.value, 1)}</td>${actCell(p)}</tr>`).join("")}</tbody></table></div></section>` : ""}
    <section><p class="note">${FUND_NOTE} ${ext(`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${m.cik}&type=13F-HR`, "See the filings at the SEC")}.</p></section>`;
  $("#fundF").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; fundState.filter = b.dataset.f; fundState.shown = 60; drawFund(); };
  if ($("#fundMore")) $("#fundMore").onclick = () => { fundState.shown += 200; drawFund(); };
}

/* ------------------------------------------------------------ Ownership tab: big investors in this stock */
async function loadFundHolders(d) {
  let r; try { r = await getJSON("/api/funds/stock/" + encodeURIComponent(d.ticker)); } catch { return; }
  const box = $("#fundBox");
  if (!box || !r.holders.length || state.data !== d) return;
  const held = r.holders.filter((h) => h.act !== "sold");
  box.innerHTML = `<section>${titled("Well-known investors", `${held.length} of the ${r.tracked} investors Kash follows ${held.length === 1 ? "holds" : "hold"} this stock, as of each one's latest quarterly report.`)}
    <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Investor</th><th data-tip="Share of that investor's reported stock portfolio.">% of their portfolio</th><th>Value</th><th>Shares</th><th>Since last report</th><th data-tip="Shares held over the last 8 quarterly reports.">Trend</th><th>Report</th></tr></thead>
    <tbody>${r.holders.slice(0, 15).map((h) => `<tr><td class="l co">${fundLink(h.cik, h.investor)}<div class="muted small nm">${esc(h.firm)}</div></td>
      <td>${h.act === "sold" ? "–" : pct(h.pct, 1)}</td><td>${h.act === "sold" ? "–" : money(h.value, 1)}</td><td>${h.act === "sold" ? "–" : count(h.shares)}</td>${actCell(h)}
      <td>${spark(h.history.map((x) => x.shares).reverse())}</td><td class="muted small">${quarter(h.period)}</td></tr>`).join("")}</tbody></table></div>
    <p class="note">Reports arrive up to 45 days after each quarter ends, so these positions may have changed.</p></section>`;
}
