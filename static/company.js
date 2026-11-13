/* Kash: company tabs (Forecast, Fair value, Ownership, Dividends, Company).
   Loaded after app.js and uses its helpers (money, price, pct, esc, mk, baseOpts, annualSeries...).
   The data comes from /api/company/{ticker}, fetched once per stock and shared by all five tabs. */

const companyCache = {};   // ticker -> Promise of /api/company data

function loadCompany(ticker) {
  if (!companyCache[ticker]) {
    companyCache[ticker] = fetch("/api/company/" + encodeURIComponent(ticker))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .catch((e) => { delete companyCache[ticker]; throw e; });
  }
  return companyCache[ticker];
}

// Render a company tab once its data arrives (ignoring it if the user has moved on).
async function renderCompanyTab(tab, render) {
  const d = state.data, body = $("#tabBody");
  body.innerHTML = `<div class="loading"><div class="spinner"></div>Gathering company details…</div>`;
  let c;
  try { c = await loadCompany(d.ticker); } catch (e) {
    if (state.tab === tab && state.data === d) body.innerHTML = `<div class="error"><h3>Couldn't load company details</h3><p class="sub">${esc(e.message)}</p></div>`;
    return;
  }
  if (state.tab !== tab || state.data !== d) return;
  body.innerHTML = render(c, d) || "";
  if (render.after) render.after(c, d);
}

const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : null);
const ext = (u, text) => (safeUrl(u) ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${text}</a>` : text);
const fmtDate = (s, opts = { month: "short", day: "numeric", year: "numeric" }) =>
  s ? new Date(s.length === 10 ? s + "T12:00:00" : s).toLocaleDateString(undefined, opts) : "–";
function ago(s) {
  if (!s) return "";
  const m = (Date.now() - new Date(s)) / 6e4;
  if (m < 60) return Math.max(1, Math.round(m)) + " min ago";
  if (m < 1440) return Math.round(m / 60) + " h ago";
  if (m < 10080) return Math.round(m / 1440) + " d ago";
  return fmtDate(s);
}
const empty = (title, text) => `<section><div class="card"><h3>${title}</h3><p class="sub" style="margin:0">${text}</p></div></section>`;
const titled = (t, s) => `<h2>${t}</h2>${s ? `<p class="sub">${s}</p>` : ""}`;
const pxOf = (v) => priceIn(v, state.data.info);   // amounts in the trading currency (prices, EPS, dividends)

/* ------------------------------------------------------------ Forecast */
const RATING_KEYS = [["strongBuy", "Strong buy", "--good", 1], ["buy", "Buy", "--good", 0.55], ["hold", "Hold", "--warn", 0.8],
  ["sell", "Sell", "--bad", 0.55], ["strongSell", "Strong sell", "--bad", 1]];
const ACTIONS = { up: ["Upgrade", "good"], down: ["Downgrade", "bad"], init: ["Initiated", ""], main: ["Maintained", ""], reit: ["Reiterated", ""] };

function renderForecast(c, d) {
  const f = c.forecast, i = d.info;
  if (!f || (!f.eps.length && !f.history.length && !f.ratings)) return empty("No analyst coverage", "Wall Street analysts don't publish forecasts for this security.");
  const r = f.ratings, total = r ? Object.values(r).reduce((a, b) => a + b, 0) : 0;
  const buyShare = total ? (r.strongBuy + r.buy) / total : null;
  const beats = f.history.filter((h) => h.actual != null && h.estimate != null);
  const nBeat = beats.filter((h) => h.actual >= h.estimate).length;

  const estTable = (rows, isEps) => `<div class="tbl-wrap"><table class="plain"><thead><tr>
      <th>Period</th><th>Estimate</th><th>Range (low – high)</th><th>Year ago</th><th>Growth</th><th>Analysts</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td>${e.period}</td><td><b>${isEps ? pxOf(e.avg) : money(e.avg)}</b></td>
        <td>${isEps ? pxOf(e.low) + " – " + pxOf(e.high) : money(e.low) + " – " + money(e.high)}</td>
        <td>${isEps ? pxOf(e.yearAgo) : money(e.yearAgo)}</td>
        <td class="${(e.growth ?? 0) >= 0 ? "up" : "down"}">${signed(e.growth)}</td><td>${e.analysts != null ? Math.round(e.analysts) : "–"}</td></tr>`).join("")}
      </tbody></table></div>`;

  return `
    <section>${titled("What analysts think", "Ratings from Wall Street analysts who cover the stock, and where they think the price is headed.")}
      <div class="grid g2">
        <div class="card"><h3>Buy, hold or sell?</h3>
          ${r && total ? `<div class="ratingbar">${RATING_KEYS.map(([k, , col, op]) => r[k] ? `<i style="width:${(r[k] / total) * 100}%;background:${css(col)};opacity:${op}" title="${r[k]}"></i>` : "").join("")}</div>
          <div class="legend-row">${RATING_KEYS.map(([k, label, col, op]) => `<span><i style="background:${css(col)};opacity:${op}"></i>${label} <b>${r[k]}</b></span>`).join("")}</div>
          <div class="takeaway"><b>${pct(buyShare, 0)}</b> of ${total} analysts rate it a buy. The consensus is <b>${esc((f.consensus || "–").replace("_", " "))}</b>.
            Analysts lean optimistic in general, so a buy rating is common; many holds or sells stand out more.</div>` : `<p class="muted">No ratings available.</p>`}
        </div>
        <div class="card"><h3>12-month price targets</h3>
          ${targetBar(i)}
          <div class="pills">
            ${pill("Low", pxOf(f.target.targetLowPrice), upside(f.target.targetLowPrice, i.currentPrice))}
            ${pill("Average", pxOf(f.target.targetMeanPrice), upside(f.target.targetMeanPrice, i.currentPrice))}
            ${pill("High", pxOf(f.target.targetHighPrice), upside(f.target.targetHighPrice, i.currentPrice))}
          </div></div>
      </div>
    </section>

    ${beats.length ? `<section>${titled("Beat or miss?", "Actual earnings per share vs. what analysts expected, for recent quarters.")}
      <div class="card"><div class="chart-box"><canvas id="cBeats"></canvas></div>
        <div class="takeaway">Beat analysts' estimate in <b>${nBeat} of the last ${beats.length}</b> quarters.
          ${nBeat === beats.length ? "Companies often guide expectations low, so steady small beats are normal; a miss would be the surprise." : "Misses tend to move the price more than beats."}</div></div>
    </section>` : ""}

    ${f.eps.length ? `<section>${titled("Earnings per share estimates", "Analysts' average forecast and the spread between the lowest and highest.")}${estTable(f.eps, true)}</section>` : ""}
    ${f.revenue.length ? `<section>${titled("Revenue estimates")}${estTable(f.revenue, false)}</section>` : ""}

    ${f.changes.length ? `<section>${titled("Recent rating changes", "The latest calls from individual analysts and their price targets.")}
      <div class="tbl-wrap"><table class="plain"><thead><tr><th>Date</th><th class="l">Firm</th><th class="l">Action</th><th class="l">Rating</th><th>Price target</th></tr></thead><tbody>
      ${f.changes.map((x) => { const [act, cls] = ACTIONS[x.action] || [x.action || "–", ""];
        const tgt = x.target ? (x.priorTarget && x.priorTarget !== x.target ? `${pxOf(x.priorTarget)} → <b>${pxOf(x.target)}</b>` : `<b>${pxOf(x.target)}</b>`) : "–";
        return `<tr><td>${fmtDate(x.date)}</td><td class="l">${esc(x.firm)}</td><td class="l"><span class="badge ${cls}">${act}</span></td>
          <td class="l">${x.from && x.from !== x.to ? esc(x.from) + " → " : ""}<b>${esc(x.to || "–")}</b></td><td>${tgt}</td></tr>`; }).join("")}
      </tbody></table></div></section>` : ""}
    <p class="note">Estimates and ratings from Yahoo Finance. Analysts can be wrong, and often are.</p>`;
}
renderForecast.after = (c) => {
  const h = (c.forecast?.history || []).filter((x) => x.actual != null && x.estimate != null);
  if (!h.length) return;
  mk("cBeats", { data: { labels: h.map((x) => fmtDate(x.quarter, { month: "short", year: "numeric" })),
    datasets: [bar("Estimate", h.map((x) => x.estimate), css("--ink-3") + "88"),
      bar("Actual", h.map((x) => x.actual), h.map((x) => (x.actual >= x.estimate ? css("--good") : css("--bad"))))] },
    options: baseOpts({ plugins: { ...baseOpts().plugins, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (it) => `${it.dataset.label}: ${pxOf(it.parsed.y)}` } } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => pxOf(v) } } } }) });
};

/* ------------------------------------------------------------ Fair value */
// value today = price you could pay now and still earn `ret` a year, if per-share earnings (or FCF)
// grow at `g` for `years` and the stock then trades at `multiple` times them.
function fairValue(base, g, years, multiple, ret) {
  const future = base * Math.pow(1 + g, years) * multiple;
  return { future, value: future / Math.pow(1 + ret, years) };
}
const impliedReturn = (future, price, years) => (future > 0 && price > 0 ? Math.pow(future / price, 1 / years) - 1 : null);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const avgOf = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);

function fvDefaults(c, d, mode) {
  const fv = c.fairValue || {}, i = d.info;
  const base = mode === "eps" ? fv.eps : fv.fcfPerShare;
  const histSeries = mode === "eps" ? annualSeries("income", "eps") : annualSeries("cashflow", "fcf");
  const span = histSeries.slice(-6).filter((x) => x.v != null);   // up to the last 5 years
  const hist = cagr(span), histYears = span.length - 1;
  const candidates = [hist, mode === "eps" ? fv.analystGrowth : null].filter((v) => v != null && isFinite(v));
  const growth = candidates.length ? clamp(Math.min(...candidates), 0, 0.2) : 0.05;
  const past = annualSeries("ratios", mode === "eps" ? "pe" : "pfcf").map((x) => x.v).filter((v) => v != null && v > 0 && v < 200);
  const now = mode === "eps" ? i.trailingPE : (base > 0 ? i.currentPrice / base : null);
  const pastAvg = medianOf(past);
  const multiple = clamp(Math.round(Math.min(...[pastAvg, now].filter((v) => v != null && v > 0), 25)), 8, 35);
  return { base, growth, multiple, ret: 0.1, years: 5, hist, histYears, analyst: fv.analystGrowth, pastAvg, pastN: past.length, now };
}

const fvState = { mode: "eps", inputs: null, ticker: null };

function renderFairValue(c, d) {
  if (d.info.dataConflict) return empty("Fair value isn't available right now", "This company recently merged or changed tickers, so its earnings, cash flow and share count don't describe one business yet. The calculator will work again once combined results are reported.");
  const fv = c.fairValue || {};
  const eps = fv.eps > 0, fcf = fv.fcfPerShare > 0;
  if (!eps && !fcf) return empty("Can't estimate a fair value", "This calculator works from profits or free cash flow per share, and this company currently has neither (or it's a fund). Valuing a business that isn't profitable yet depends almost entirely on guesses about the far future.");
  if (fvState.ticker !== d.ticker) { fvState.ticker = d.ticker; fvState.mode = eps ? "eps" : "fcf"; fvState.inputs = null; }
  if (fvState.mode === "fcf" && !fcf) fvState.mode = "eps";
  if (!fvState.inputs) fvState.inputs = fvDefaults(c, d, fvState.mode);
  const isEps = fvState.mode === "eps", m = isEps ? "P/E" : "P/FCF";
  const x = fvState.inputs;
  const src = [x.hist != null ? `its ${isEps ? "EPS" : "free cash flow"} grew ${signed(x.hist)} a year over the last ${x.histYears} years` : null,
    isEps && x.analyst != null ? `analysts expect ${signed(x.analyst)} next year` : null].filter(Boolean).join(" and ");
  return `
    <section>${titled("What could it be worth?", "A simple, EverythingMoney-style estimate. Pick how fast the business grows, what the market might pay for it in a few years, and the yearly return you want. Kash works out the most you could pay today.")}
      <div class="fv-grid">
        <div class="card">
          <div class="row-between"><h3>Your assumptions</h3>
            <div class="seg" id="fvMode"><button data-m="eps" class="${isEps ? "on" : ""}" ${eps ? "" : "disabled"}>Earnings</button><button data-m="fcf" class="${isEps ? "" : "on"}" ${fcf ? "" : "disabled"}>Free cash flow</button></div></div>
          <p class="muted" style="margin:8px 0 0">Starting point: <b>${pxOf(x.base)}</b> ${isEps ? "earnings per share (last 12 months)" : "free cash flow per share (last 12 months)"}.</p>
          ${slider("g", "Growth per year", x.growth, 0, 0.4, 0.005, (v) => pct(v, 1), src ? `Starts at the lower of: ${src} (capped at 20%).` : "No growth history, so it starts at 5%.")}
          ${slider("years", "Years", x.years, 3, 10, 1, (v) => v + " years", "How far ahead to project.")}
          ${slider("multiple", `${m} at the end`, x.multiple, 5, 50, 1, (v) => v + "×", x.pastAvg ? `Starts at the lower of its ${x.pastN}-year median (${mult(x.pastAvg)}) and today's (${mult(x.now)}), capped at 25×.` : x.now ? `Starts at today's ${m} (${mult(x.now)}), capped at 25×.` : "What investors might pay for each $1 then.")}
          ${slider("ret", "Return you want", x.ret, 0.04, 0.2, 0.005, (v) => pct(v, 1) + " a year", "The stock market has averaged roughly 10% a year over the long run.")}
          <button class="btn" id="fvReset" style="margin-top:6px">Reset to defaults</button>
        </div>
        <div class="card" id="fvOut"></div>
      </div>
    </section>
    <p class="note">This is arithmetic on your assumptions, not a prediction, and not investment advice. Small changes in growth or the ending ${m} move the answer a lot.</p>`;
}
const slider = (key, label, v, min, max, step, show, hint) => `<div class="slider">
  <label for="fv_${key}"><span>${label}</span><b id="fv_${key}_v" class="num">${show(v)}</b></label>
  <input type="range" id="fv_${key}" data-k="${key}" min="${min}" max="${max}" step="${step}" value="${v}">
  ${hint ? `<div class="muted hint">${hint}</div>` : ""}</div>`;

renderFairValue.after = (c, d) => {
  if (!$("#fvOut")) return;
  const shows = { g: (v) => pct(v, 1), years: (v) => v + " years", multiple: (v) => v + "×", ret: (v) => pct(v, 1) + " a year" };
  const keyMap = { g: "growth", years: "years", multiple: "multiple", ret: "ret" };
  document.querySelectorAll(".slider input").forEach((el) => {
    el.oninput = () => { const k = el.dataset.k, v = +el.value; fvState.inputs[keyMap[k]] = v; $(`#fv_${k}_v`).textContent = shows[k](v); drawFairValue(d); };
  });
  $("#fvMode").onclick = (e) => { const b = e.target.closest("button"); if (!b || b.disabled) return; fvState.mode = b.dataset.m; fvState.inputs = null; renderStock(); };
  $("#fvReset").onclick = () => { fvState.inputs = null; renderStock(); };
  drawFairValue(d);
};

function drawFairValue(d) {
  const x = fvState.inputs, px = d.info.currentPrice, m = fvState.mode === "eps" ? "P/E" : "P/FCF";
  const { future, value } = fairValue(x.base, x.growth, x.years, x.multiple, x.ret);
  const diff = value / px - 1, irr = impliedReturn(future, px, x.years);
  const verdict = diff > 0.15 ? ["Looks undervalued", "up"] : diff < -0.15 ? ["Looks overvalued", "down"] : ["Around fair value", ""];
  const cases = [["Conservative", x.growth * 0.67, x.multiple * 0.85], ["Your case", x.growth, x.multiple], ["Optimistic", Math.min(0.4, x.growth * 1.33), x.multiple * 1.15]]
    .map(([n, g, mm]) => [n, fairValue(x.base, g, x.years, mm, x.ret).value, g, mm]);
  const rows = Array.from({ length: x.years }, (_, k) => [k + 1, x.base * Math.pow(1 + x.growth, k + 1)]);
  $("#fvOut").innerHTML = `
    <h3>Fair value estimate</h3>
    <div class="fv-big num">${pxOf(value)}</div>
    <div class="fv-verdict"><span class="badge ${verdict[1] === "up" ? "good" : verdict[1] === "down" ? "bad" : ""}">${verdict[0]}</span>
      <span class="muted">today's price is ${pxOf(px)} (${diff >= 0 ? "about " + pct(diff, 0) + " below" : "about " + pct(-diff, 0) + " above"} this estimate)</span></div>
    <div class="takeaway">If you buy at ${pxOf(px)} and these assumptions come true, you'd earn about <b class="${(irr ?? 0) >= x.ret ? "up" : "down"}">${irr != null ? pct(irr, 1) : "–"} a year</b>
      over ${x.years} years (before dividends), vs. the ${pct(x.ret, 1)} you asked for.</div>
    <div class="takeaway"><b>What's priced in:</b> to earn your ${pct(x.ret, 1)} from today's price, ${fvState.mode === "eps" ? "earnings" : "free cash flow"} per share would have to grow about <b>${pct(Math.pow((px * Math.pow(1 + x.ret, x.years)) / (x.multiple * x.base), 1 / x.years) - 1, 1)} a year</b> for ${x.years} years.</div>
    <div class="pills">${cases.map(([n, v, g, mm]) => pill(n, pxOf(v), `${pct(g, 1)} growth · ${mm.toFixed(0)}× ${m}`)).join("")}</div>
    <div class="tbl-wrap" style="margin-top:14px"><table class="plain"><thead><tr><th class="l">Year</th><th>${fvState.mode === "eps" ? "EPS" : "FCF / share"}</th><th>Price at ${x.multiple}×</th></tr></thead><tbody>
      ${rows.map(([y, v]) => `<tr><td class="l">${new Date().getFullYear() + y}</td><td>${pxOf(v)}</td><td>${y === x.years ? "<b>" + pxOf(v * x.multiple) + "</b>" : "–"}</td></tr>`).join("")}
    </tbody></table></div>`;
}

/* ------------------------------------------------------------ Ownership */
function renderOwnership(c, d) {
  const o = c.ownership;
  if (!o || (!o.institutions.length && !o.insiders.length && o.institutionsPct == null)) return empty("No ownership data", "Yahoo Finance doesn't have holder information for this security.");
  const s = o.insiderSummary || {};
  const take = s.buys || s.sells
    ? `In the last 6 months insiders ${s.buys ? `<b>bought ${money(s.buyValue)}</b> (${s.buys} trades)` : "<b>bought nothing</b>"} and ${s.sells ? `<b>sold ${money(s.sellValue)}</b> (${s.sells} trades)` : "<b>sold nothing</b>"} on the open market. ` +
      (s.buyValue > s.sellValue ? "Insiders buying with their own money is usually a good sign." : "Selling is common (executives are often paid in stock), so it matters less than buying.")
    : "No open-market buying or selling by insiders in the last 6 months.";
  return `
    <div id="fundBox"></div>
    <div id="polBox"></div>
    <section>${titled("Who owns the stock?", "Big investment firms (institutions) vs. the company's own executives and directors (insiders).")}
      <div class="pills">
        ${pill("Institutions own", pct(o.institutionsPct), o.institutionsCount ? Math.round(o.institutionsCount).toLocaleString() + " firms" : "")}
        ${pill("Insiders own", pct(o.insidersPct, 2), "executives & directors")}
      </div>
      <div class="takeaway" style="margin-top:14px">${take}</div>
    </section>
    ${o.institutions.length ? `<section>${titled("Largest institutional holders", "From their latest quarterly filings.")}
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Holder</th><th>Owns</th><th>Shares</th><th>Value</th><th>Change</th><th>As of</th></tr></thead><tbody>
      ${o.institutions.map((h) => `<tr><td class="l">${esc(h.holder)}</td><td>${pct(h.pct, 2)}</td><td>${count(h.shares)}</td><td>${money(h.value)}</td>
        <td class="${(h.change ?? 0) >= 0 ? "up" : "down"}">${signed(h.change)}</td><td>${fmtDate(h.date)}</td></tr>`).join("")}
      </tbody></table></div></section>` : ""}
    ${o.insiders.length ? `<section>${titled("Recent insider transactions", "Buys and sells reported by executives and directors.")}
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Date</th><th class="l">Insider</th><th class="l">Type</th><th>Shares</th><th>Value</th></tr></thead><tbody>
      ${o.insiders.map((t) => `<tr><td class="l">${fmtDate(t.date)}</td><td class="l"><b>${esc(t.insider)}</b><div class="muted small">${esc(t.position || "")}</div></td>
        <td class="l"><span class="badge ${t.kind === "buy" ? "good" : t.kind === "sell" ? "bad" : ""}">${t.kind === "buy" ? "Buy" : t.kind === "sell" ? "Sell" : "Other"}</span>
          <div class="muted small wrap-text">${esc(t.text || "Award, gift or option exercise")}</div></td>
        <td>${count(t.shares)}</td><td>${t.value ? money(t.value) : "–"}</td></tr>`).join("")}
      </tbody></table></div></section>` : ""}`;
}

renderOwnership.after = (c, d) => { loadFundHolders(d); loadOfficials(d); };

/* ------------------------------------------------------------ Dividends */
function renderDividends(c, d) {
  const v = c.dividends, i = d.info;
  if (!v || !v.years.length) return empty("No dividend", `${esc(i.shortName || d.ticker)} doesn't pay a dividend. Many growing companies reinvest their profits or buy back shares instead.`);
  const full = v.years.filter((y) => !y.partial);
  const last6 = full.slice(-6), g5 = last6.length >= 2 && last6.every((y, k) => !k || y.year === last6[k - 1].year + 1) ? cagr(last6.map((y) => ({ v: y.amount }))) : null;
  const paysNow = !!v.rate;
  return `
    <section>${titled("Dividends", "Cash the company pays shareholders, usually every quarter.")}
      <div class="pills">
        ${pill("Yearly dividend", paysNow ? pxOf(v.rate) : "None now", "per share")}
        ${pill("Yield", paysNow ? pct(v.yield ?? i.dividendYieldCalc, 2) : "–", v.fiveYearAvgYield ? "5-yr avg " + pct(v.fiveYearAvgYield, 2) : "")}
        ${pill("Payout ratio", pct(v.payoutRatio, 0), "of profits paid out")}
        ${pill("Raised", v.streak ? v.streak + (v.streak === 1 ? " year" : " years") : "–", "in a row")}
      </div>
    </section>
    <section><div class="card"><div class="q-title"><div class="q-emoji">💸</div><div><h3>Dividends per share each year</h3><div class="muted">${v.years.some((y) => y.partial) ? "This year so far is shown lighter" : ""}</div></div></div>
      <div class="chart-box" style="margin-top:14px"><canvas id="cDiv"></canvas></div>
      <div class="takeaway">${[g5 != null ? `Grew <b>${signed(g5)}</b> a year over the last 5 years.` : "",
        v.streak >= 10 ? `Raised it <b>${v.streak} years in a row</b>: a dependable payer.` : v.streak ? `Raised it ${v.streak} year${v.streak > 1 ? "s" : ""} in a row.` : "Hasn't raised it every year recently.",
        v.payoutRatio > 0.9 ? "It pays out most of its profit, which leaves little room to keep raising it." : v.payoutRatio != null ? `It pays out ${pct(v.payoutRatio, 0)} of profit, leaving room to keep paying.` : ""].join(" ")}</div></div>
    </section>
    <section>${titled("Recent payments")}
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Ex-dividend date</th><th>Amount</th></tr></thead><tbody>
      ${v.recent.map((p) => `<tr><td class="l">${fmtDate(p.date)}</td><td>${pxOf(p.amount)}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="note">Amounts are adjusted for stock splits. Own the stock before the ex-dividend date to receive the payment.</p></section>`;
}
renderDividends.after = (c) => {
  const y = c.dividends?.years || []; if (!y.length) return;
  const col = css("--c2");
  mk("cDiv", { data: { labels: y.map((x) => x.year + (x.partial ? " (so far)" : "")),
    datasets: [bar("Dividends per share", y.map((x) => x.amount), y.map((x) => (x.partial ? col + "66" : col)))] },
    options: baseOpts({ plugins: { ...baseOpts().plugins, legend: { display: false }, tooltip: { ...baseOpts().plugins.tooltip, callbacks: { label: (it) => pxOf(it.parsed.y) } } },
      scales: { x: { grid: { display: false } }, y: { grid: { color: css("--line") }, border: { display: false }, ticks: { callback: (v) => pxOf(v) } } } }) });
};

/* ------------------------------------------------------------ Company */
function renderCompany(c, d) {
  const p = c.profile || {}, i = d.info, dt = p.dates || {}, tr = p.trading || {};
  const row = (k, v) => `<dt>${k}</dt><dd>${v ?? "–"}</dd>`;
  const vol = tr.volume && tr.averageVolume ? ` <span class="muted">(${pct(tr.volume / tr.averageVolume, 0)} of usual)</span>` : "";
  return `
    <section>${titled("At a glance")}
      <div class="grid g3">
        <div class="card"><h3>Key dates</h3><dl class="facts">
          ${row("Next earnings", dt.earnings ? fmtDate(dt.earnings) + (dt.earningsEstimate != null ? `<div class="muted small">Expected EPS ${pxOf(dt.earningsEstimate)}</div>` : "") : "–")}
          ${row("Ex-dividend", fmtDate(dt.exDividend))}
          ${row("Dividend paid", fmtDate(dt.dividendPaid))}
        </dl></div>
        <div class="card"><h3>Trading</h3><dl class="facts">
          ${row("Open", pxOf(tr.open))}
          ${row("Day range", tr.dayLow != null ? pxOf(tr.dayLow) + " – " + pxOf(tr.dayHigh) : "–")}
          ${row("Volume", count(tr.volume) + vol)}
          ${row("Shares out", count(tr.sharesOutstanding))}
          ${row("Short interest", tr.shortPercentOfFloat != null ? pct(tr.shortPercentOfFloat, 2) + ` of float${info("Shares borrowed and sold by investors betting the price falls. Above ~10% is high.")}` : "–")}
        </dl></div>
        <div class="card"><h3>Contact</h3><dl class="facts">
          ${row("Headquarters", esc(p.address || "–"))}
          ${row("Phone", esc(p.phone || "–"))}
          ${row("Website", i.website ? ext(i.website, esc(i.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""))) : "–")}
          ${row("Employees", i.fullTimeEmployees ? Math.round(i.fullTimeEmployees).toLocaleString() : "–")}
        </dl></div>
      </div>
    </section>
    ${p.officers?.length ? `<section>${titled("Leadership")}
      <div class="tbl-wrap"><table class="plain"><thead><tr><th class="l">Name</th><th class="l">Role</th><th>Age</th><th>Yearly pay${info("Salary and bonus from the latest proxy filing; stock awards are often much larger.")}</th></tr></thead><tbody>
      ${p.officers.map((o) => `<tr><td class="l"><b>${esc(o.name)}</b></td><td class="l wrap-text">${esc(o.title)}</td><td>${o.age ?? "–"}</td><td>${o.pay ? money(o.pay) : "–"}</td></tr>`).join("")}
      </tbody></table></div></section>` : ""}
    <section class="grid g2 news-grid">
      <div>${titled("Latest news")}
        ${c.news.length ? `<ul class="news card">${c.news.map((n) => `<li>${ext(n.url, `<span class="t">${esc(n.title)}</span>`)}
          <div class="m">${esc(n.publisher || "")}${n.date ? " · " + ago(n.date) : ""}</div>${n.summary ? `<p>${esc(n.summary)}</p>` : ""}</li>`).join("")}</ul>` : `<div class="card muted">No recent news.</div>`}
      </div>
      <div>${titled("SEC filings", "Official reports the company files with regulators.")}
        ${c.filings.length ? `<ul class="news card">${c.filings.map((f) => `<li>${ext(f.url, `<span class="badge">${esc(f.form)}</span> <span class="t">${esc(f.what)}</span>`)}
          <div class="m">Filed ${fmtDate(f.date)}${f.description && f.description !== f.form ? " · " + esc(f.description) : ""}</div></li>`).join("")}</ul>` : `<div class="card muted">No SEC filings found (common for funds and some foreign listings).</div>`}
      </div>
    </section>`;
}

const COMPANY_TABS = {
  forecast: ["Forecast", renderForecast],
  value: ["Fair value", renderFairValue],
  owners: ["Ownership", renderOwnership],
  dividends: ["Dividends", renderDividends],
  company: ["Company", renderCompany],
};
