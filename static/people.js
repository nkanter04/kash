/* Kash: Who's Trading. Reported trades by members of Congress, the President and corporate insiders.
   Loaded after pages.js and uses its helpers. Returns are measured from when a trade became public. */

// the shared online copy has no disclosure data: drop the menu link there
fetch("/api/config").then((r) => r.json()).then((c) => { if (!c.people) document.querySelector('#mainnav a[href="/people"]')?.remove(); }).catch(() => {});

const ppl = { group: "all", cache: {} };
const GROUP_TABS = [["all", "Everyone"], ["congress", "Congress"], ["insider", "Corporate insiders"]];
const personLink = (id, name, party) => `<a href="/people/${encodeURIComponent(id)}" data-link><b>${esc(name)}</b></a>${party ? `<span class="party ${esc(party[0])}" title="${esc(party)}">${esc(party[0])}</span>` : ""}`;
const pts = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(1) + " pts");
const ptsCell = (v) => `<td class="${v == null ? "" : v >= 0 ? "up" : "down"}">${pts(v)}</td>`;
const usdShort = (v) => (v == null ? "–" : money(v, v >= 1e6 ? 1 : 0));
const amountText = (t) => (t.low == null ? "Not read" : t.grp === "insider" || t.low === t.high ? usdShort(t.low) : `${usdShort(t.low)} – ${usdShort(t.high)}`);
const dateShort = (s) => fmtDate(s, { month: "short", day: "numeric", year: "2-digit" });
const LAG_TIP = "Days between the trade and when it was publicly reported. Congress has at most 45 days under the STOCK Act.";
const PUBLIC_TIP = "How the stock did against the S&P 500 from the day this trade became public: the earliest anyone could have copied it.";

function tradeRow(t, showPerson) {
  const late = t.grp !== "insider" && t.lag > 45;
  const beat = t.r_filed != null ? t.r_filed - t.b_filed : null;
  return `<tr>
    ${showPerson ? `<td class="l co">${personLink(t.person, t.name, t.party)}<div class="muted small nm">${esc(t.grp === "insider" ? t.role : t.role)}</div></td>` : ""}
    <td class="l asset">${t.ticker ? stockLink(t.ticker) : ""}<div class="muted small nm">${esc(t.asset || "")}</div></td>
    <td class="l"><span class="side-${t.side}">${t.side === "buy" ? "Buy" : t.side === "sell" ? "Sell" : "Exchange"}</span>${t.planned ? `<div class="muted small" data-tip="Made under a pre-arranged 10b5-1 trading plan, so it says less about the insider's view.">Planned</div>` : ""}${t.owner && t.owner !== "Self" ? `<div class="muted small">${esc(t.owner)}</div>` : ""}</td>
    <td>${amountText(t)}</td>
    <td>${dateShort(t.trade_date)}</td>
    <td>${t.lag != null ? `<span class="${late ? "late-tag" : ""}">${t.lag}d</span>` : "–"}</td>
    ${t.side === "buy" ? chgCell(t.r_filed) + ptsCell(beat) : `<td>–</td><td>–</td>`}
    <td>${safeUrl(t.url) ? ext(t.url, "Filing") : ""}</td></tr>`;
}
const tradeHead = (showPerson) => `<thead><tr>${showPerson ? `<th class="l">Who</th>` : ""}<th class="l">Asset</th><th class="l">Action</th><th>Amount</th><th>Traded</th><th data-tip="${LAG_TIP}">Reported after</th><th data-tip="Stock's return since the trade became public. Buys only.">Return</th><th data-tip="${PUBLIC_TIP}">vs S&P</th><th></th></tr></thead>`;

async function renderPeople() {
  pageStart("Who's Trading");
  app.innerHTML = `<section class="home-head"><h1>Who's trading</h1><p class="sub" id="pplSub">Stock trades reported by members of Congress, the President and corporate insiders.</p></section>
    <div class="ppl-search"><input id="pplQ" placeholder="Search a person or ticker, e.g. Pelosi, Musk, NVDA" autocomplete="off" spellcheck="false"><ul class="ppl-results" id="pplHits" hidden></ul></div>
    <div class="seg" id="pplTabs" style="margin-top:12px">${GROUP_TABS.map(([k, l]) => `<button data-g="${k}" class="${k === ppl.group ? "on" : ""}">${l}</button>`).join("")}</div>
    <div id="pplBody">${pageLoading("Loading disclosures…")}</div>`;
  $("#pplTabs").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; ppl.group = b.dataset.g; [...$("#pplTabs").children].forEach((x) => x.classList.toggle("on", x === b)); loadPeople(); };
  let timer;
  $("#pplQ").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => searchPeople(e.target.value), 180); };
  $("#pplQ").onkeydown = (e) => { if (e.key === "Escape") $("#pplHits").hidden = true; };
  document.addEventListener("click", hideHits);
  loadPeople();
}
function hideHits(e) { const h = $("#pplHits"); if (h && !e.target.closest(".ppl-search")) h.hidden = true; }
async function searchPeople(q) {
  const box = $("#pplHits"); if (!box) return;
  if (q.trim().length < 2) { box.hidden = true; return; }
  let d; try { d = await getJSON("/api/people/search?q=" + encodeURIComponent(q)); } catch { return; }
  if ($("#pplQ").value !== q) return;
  const hits = [...d.people.map((p) => `<li><a href="/people/${encodeURIComponent(p.id)}" data-link><span><b>${esc(p.name)}</b> <small class="muted">${esc(p.role || "")}</small></span><small class="muted">${p.trades} trades</small></a></li>`),
    ...d.tickers.map((t) => `<li><a href="/stock/${encodeURIComponent(t.ticker)}" data-link><span><b>${esc(t.ticker)}</b> <small class="muted">${esc(t.asset || "")}</small></span><small class="muted">${t.trades} trades</small></a></li>`)];
  box.innerHTML = hits.join("") || `<li class="muted" style="padding:8px 10px">No matches.</li>`;
  box.hidden = false;
}
async function loadPeople() {
  const g = ppl.group, box = $("#pplBody"); if (!box) return;
  box.innerHTML = pageLoading("Loading disclosures…");
  let d;
  try { d = ppl.cache[g] || (ppl.cache[g] = await getJSON("/api/people" + (g === "all" ? "" : "?group=" + g))); } catch (e) { box.innerHTML = pageError(e); return; }
  if (ppl.group !== g || !$("#pplBody")) return;
  const s = d.status;
  if (!s.ready) { box.innerHTML = `<div class="card" style="margin-top:18px"><h3>The database isn't built yet</h3><p class="sub" style="margin:0">Run <code>python disclosures.py build</code> in the project folder.</p></div>`; return; }
  const c = s.counts, n = (k) => (c[k] ? c[k].people.toLocaleString() : 0);
  $("#pplSub").textContent = `${n("congress")} members of Congress and ${n("insider")} corporate insiders so far. Congress filed through ${s.through.congress ? fmtDate(s.through.congress) : "–"}; SEC data through ${s.through.insider ? fmtDate(s.through.insider) : "–"}.`;
  const leaders = d.leaders.length ? `<div class="card"><h3 data-tip="Members of Congress: average return of their stock buys over the year after each became public, minus the S&P 500. Needs 5+ buys in 3+ different stocks with a full year of results. Could be luck.">Best track records in Congress <span class="info">i</span></h3>
    <table class="plain mini"><tbody>${d.leaders.slice(0, 8).map((p) => `<tr><td class="l co">${personLink(p.id, p.name, p.party)}<div class="muted small nm">${esc(p.role || "")}</div></td><td class="muted small">${p.tracked_1y} buys</td>${ptsCell(p.alpha_1y)}</tr>`).join("")}</tbody></table></div>` : "";
  const congress = g !== "insider" && g !== "executive" && d.congress.length ? `<div class="card"><h3>Most bought by Congress <small class="muted">last 90 days</small></h3>
    <table class="plain mini"><tbody>${d.congress.slice(0, 8).map((x) => `<tr><td class="l co">${stockLink(x.ticker)}<div class="muted small nm">${esc(x.asset || "")}</div></td><td class="muted small">${x.people} ${x.people === 1 ? "member" : "members"}</td><td><span class="side-buy">${x.buys}</span> / <span class="side-sell">${x.sells}</span></td></tr>`).join("")}</tbody></table></div>` : "";
  const clusters = g !== "congress" && g !== "executive" && d.clusters.length ? `<div class="card"><h3 data-tip="Three or more different insiders buying the same stock on the open market within the latest 60 days of data, averaging $25,000+ each. Pre-planned trades excluded.">Cluster buying by insiders <span class="info">i</span></h3>
    <table class="plain mini"><tbody>${d.clusters.slice(0, 8).map((x) => `<tr><td class="l co">${stockLink(x.ticker)}<div class="muted small nm">${esc(x.asset || "")}</div></td><td class="muted small">${x.people} insiders</td><td>${usdShort(x.amount)}</td></tr>`).join("")}</tbody></table></div>` : "";
  const late = g !== "insider" && g !== "executive" && d.late.length ? `<div class="card"><h3 data-tip="Trades disclosed more than 45 days after the listed trade date, the usual STOCK Act limit. Not proof of a violation: new members, amendments and date errors in filings can all cause this.">Longest-delayed disclosures <span class="info">i</span></h3>
    <table class="plain mini"><tbody>${d.late.slice(0, 8).map((p) => `<tr><td class="l co">${personLink(p.id, p.name, p.party)}<div class="muted small nm">${esc(p.role || "")}</div></td><td><span class="late-tag">${p.late}</span> of ${p.trades}</td></tr>`).join("")}</tbody></table></div>` : "";
  const panels = [leaders, congress, clusters, late].filter(Boolean);
  box.innerHTML = `${panels.length ? `<section><div class="grid g2">${panels.join("")}</div></section>` : ""}
    <section><h2>Latest reported trades</h2><div class="tbl-wrap"><table class="plain">${tradeHead(true)}<tbody>${d.recent.map((t) => tradeRow(t, true)).join("")}</tbody></table></div>
    <p class="note ppl-note">Reported by the filers themselves, and may contain errors. Congress and the President report dollar ranges, so amounts are approximate. A strong record can be luck, and spouses or advisers may make these trades. Personal research only; not investment advice.</p></section>`;
}

/* ------------------------------------------------------------ one person */
const pplState = { filter: "all", data: null };
async function renderPerson(id) {
  pageStart("Who's Trading");
  app.innerHTML = `<div id="prsBody">${pageLoading("Loading their history…")}</div>`;
  let d;
  try { d = await getJSON("/api/people/" + encodeURIComponent(id)); } catch (e) { $("#prsBody").innerHTML = pageError(e); return; }
  if (!$("#prsBody")) return;
  pplState.data = d; pplState.filter = "all";
  document.title = `${d.person.name} · Kash`;
  drawPerson();
}
function drawPerson() {
  const box = $("#prsBody"), d = pplState.data; if (!box) return;
  const p = d.person, s = d.stats, f = pplState.filter;
  const isOfficial = p.grp !== "insider";
  const tracked = s.tracked || 0;
  const verdict = s.alpha_public == null ? "None of their reported buys could be priced, so there's no return to show."
    : `Buying when ${esc(p.name.split(" ")[0])}'s ${tracked} tracked purchases became public would have ${s.alpha_public >= 0 ? "beaten" : "trailed"} the S&P 500 by <b>${Math.abs(s.alpha_public * 100).toFixed(1)} points</b> on average so far.`
      + (s.alpha_trade != null ? ` Measured from the actual trade dates, the gap is ${pts(s.alpha_trade)}, so reporting delay ${s.alpha_trade - s.alpha_public > 0.005 ? `cost a copier about ${((s.alpha_trade - s.alpha_public) * 100).toFixed(1)} points` : "cost a copier little"}.` : "");
  const overlapPct = s.overlap && s.trades ? s.overlap / s.trades : 0;
  const visible = d.trades.filter((t) => f === "all" || t.side === f);
  box.innerHTML = `<section class="home-head"><a href="/people" data-link class="muted small">← Who's trading</a><h1>${esc(p.name)}${p.party ? ` <span class="party ${esc(p.party[0])}">${esc(p.party[0])}</span>` : ""}</h1>
      <p class="sub">${esc(p.role || "")}${p.state ? ` · ${esc(p.state)}` : ""}</p></section>
    <div class="pills">
      ${pill("Beat the S&P", `<span class="${(s.alpha_public ?? 0) >= 0 ? "up" : "down"}">${pts(s.alpha_public)}</span>`, "per buy, since it was public")}
      ${pill("After 1 year", `<span class="${(s.alpha_1y ?? 0) >= 0 ? "up" : "down"}">${pts(s.alpha_1y)}</span>`, s.tracked_1y ? `${s.tracked_1y} buys with a full year` : "not enough history")}
      ${pill("Win rate", s.win_rate != null ? pct(s.win_rate, 0) : "–", "of buys beat the S&P")}
      ${pill("Reports after", s.avg_lag != null ? Math.round(s.avg_lag) + " days" : "–", isOfficial ? `${s.late || 0} after 45+ days` : "average")}
      ${pill("Trades", (s.trades || 0).toLocaleString(), `${s.buys || 0} buys, ${s.sells || 0} sells`)}
    </div>
    <div class="takeaway" style="margin-top:14px">${verdict}${s.tracked_sells >= 3 && s.good_sells != null ? ` ${pct(s.good_sells, 0)} of their sales were followed by the stock lagging the S&P.` : ""}</div>
    ${p.id === "e:donaldjtrump" ? `<p class="note">Partial: read from scanned filings, mostly bonds, and missing the May 2026 reports. Many assets can't be priced.</p>` : ""}
    ${isOfficial && p.committees?.length ? `<section><h3 class="day">Committees</h3><div class="tags">${p.committees.map((c) => `<span class="tag">${esc(c.name)}</span>`).join("")}</div>
      ${s.overlap ? `<p class="takeaway" data-tip="Sector mapping is approximate. This shows where interests may overlap, and is not evidence of wrongdoing.">${s.overlap} of their trades (${pct(overlapPct, 0)}) were in sectors one of their committees oversees.</p>` : ""}</section>` : ""}
    ${d.top.length ? `<section><h3 class="day">Most traded</h3><div class="chips left">${d.top.map((t) => `<a class="chip" href="/stock/${encodeURIComponent(t.ticker)}" data-link>${esc(t.ticker)} <small>${t.n} ${t.n === 1 ? "trade" : "trades"}</small></a>`).join("")}</div></section>` : ""}
    <section><div class="seg filter" id="prsF">${[["all", "All"], ["buy", "Buys"], ["sell", "Sells"]].map(([k, l]) => `<button data-f="${k}" class="${k === f ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="tbl-wrap"><table class="plain">${tradeHead(false)}<tbody>${visible.slice(0, 300).map((t) => tradeRow(t, false)).join("") || `<tr><td class="muted">No trades.</td></tr>`}</tbody></table></div>
      ${visible.length > 300 ? `<p class="note">Showing the latest 300 of ${visible.length.toLocaleString()}.</p>` : ""}
      <p class="note ppl-note">Self-reported data that may contain errors. Dollar figures for officials are ranges. A strong record can be luck. Not investment advice.</p></section>`;
  $("#prsF").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; pplState.filter = b.dataset.f; drawPerson(); };
}

/* ------------------------------------------------------------ Ownership tab: officials in this stock */
async function loadOfficials(d) {
  let r; try { r = await getJSON("/api/people/stock/" + encodeURIComponent(d.ticker)); } catch { return; }
  const box = $("#polBox");
  if (!box || !r.trades.length || state.data !== d) return;
  box.innerHTML = `<section>${titled("Politicians trading this stock", `${r.people} ${r.people === 1 ? "official has" : "officials have"} reported trades. Returns are from when each trade became public.`)}
    <div class="tbl-wrap"><table class="plain">${tradeHead(true)}<tbody>${r.trades.slice(0, 10).map((t) => tradeRow(t, true)).join("")}</tbody></table></div></section>`;
}
