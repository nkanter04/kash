/* Kash home page: major indexes, an index heat map (squarified treemap) and quick picks.
   Loaded after app.js and uses its helpers (esc, money, signed, pct, css, destroyCharts, POPULAR). */

const HEAT_INDEXES = [["sp500", "S&P 500"], ["ndx", "Nasdaq-100"], ["dow", "Dow 30"]];
const home = { index: "sp500", maps: {}, summary: null, when: "day" };   // maps: index -> { at, data }

function renderHome() {
  destroyCharts();
  state.data = null;
  document.title = "Kash";
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  app.innerHTML = `
    <section class="home-head"><h1>Markets</h1><p class="sub">${today}</p></section>
    <div class="idx-row" id="idxRow">${SUMMARY_SLOTS.map(() => `<div class="card idx-card skeleton"></div>`).join("")}</div>
    <div id="breadthBox"></div>
    <div id="watchBox"></div>
    <section><div class="card">
      <div class="row-between">
        <div><h3>Heat map</h3><div class="muted" id="hmNote">Each box is a company: size shows its market value, color shows today's move.</div></div>
        <div class="seg" id="hmSeg">${HEAT_INDEXES.map(([k, l]) => `<button data-i="${k}" class="${k === home.index ? "on" : ""}">${l}</button>`).join("")}</div>
      </div>
      <div class="seg hm-when" id="hmWhen">${[["day", "Today"], ["year", "1 year"]].map(([k, l]) => `<button data-w="${k}" class="${k === home.when ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="heatmap" id="heatmap"></div>
      <div class="hm-legend" id="hmLegend"></div>
    </div></section>
    <div id="moversBox"></div>
    <section><h2>Look up a company</h2><p class="sub">Search at the top of the page (press / from anywhere), or start here.</p>
      ${store("kash-recent", []).length ? `<div class="k-label">Recently viewed</div><div class="chips left" style="margin-bottom:14px">${store("kash-recent", []).slice(0, 8).map((s) => `<a class="chip" href="/stock/${encodeURIComponent(s)}" data-link>${esc(s)}</a>`).join("")}</div><div class="k-label">Popular</div>` : ""}
      <div class="chips left">${POPULAR.map(([s, n]) => `<a class="chip" href="/stock/${s}" data-link>${s} <small>${n}</small></a>`).join("")}</div>
    </section>`;
  $("#hmWhen").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    home.when = b.dataset.w;
    $("#hmWhen").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    const m = home.maps[home.index]; if (m) drawHeatmap(m.data);
  };
  drawLegend();
  $("#hmSeg").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    home.index = b.dataset.i;
    $("#hmSeg").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
    loadHeatmap();
  };
  loadSummary();
  loadHeatmap();
  loadMovers();
  loadWatchlist();
}
function drawLegend() {
  const m = home.when === "year" ? 10 : 1;   // the color scale is 10x wider for 1-year moves
  $("#hmLegend").innerHTML = [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03].map((v) => `<span style="background:${heatColor(v)};color:${heatText(v)}">${v > 0 ? "+" : ""}${(v * 100 * m).toFixed(0)}%</span>`).join("");
}

/* ------------------------------------------------------------ breadth, movers, watchlist */
async function loadMovers() {
  let d;
  try { d = await getJSON("/api/market/movers"); } catch { return; }
  if (!$("#moversBox")) return;
  const b = d.breadth, upPct = b.total ? (b.up / b.total) * 100 : 50;
  $("#breadthBox").innerHTML = `<div class="card breadth">
    <div class="bar-wrap"><div class="row-between small-row"><span class="up"><b>${b.up}</b> up</span><b class="muted">S&P 500 today</b><span class="down"><b>${b.down}</b> down</span></div>
      <div class="bbar"><i style="width:${upPct}%"></i></div></div>
    ${[["Above 50-day avg", pct(b.above50, 0)], ["Above 200-day avg", pct(b.above200, 0)], ["Near 52-week high", b.newHighs], ["Near 52-week low", b.newLows]].map(([k, v]) => `<div class="stat-mini"><div class="k">${k}</div><div class="v num">${v}</div></div>`).join("")}</div>`;
  const list = (title, rows, right) => `<div class="card"><h3>${title}</h3><table class="plain mini"><tbody>${rows.map((r) => `<tr><td class="l co">${stockLink(r.symbol)}<div class="muted small nm">${esc(r.name || "")}</div></td><td>${fmtPrice(r.price)}</td>${right(r)}</tr>`).join("") || `<tr><td class="muted">None today</td></tr>`}</tbody></table></div>`;
  const chg = (r) => chgCell(r.change);
  $("#moversBox").innerHTML = `<section><h2>Movers</h2><p class="sub">US-listed companies worth $2B or more.</p><div class="grid g2 movers">
    ${list("Top gainers", d.gainers, chg)}${list("Top losers", d.losers, chg)}
    ${list("Unusual volume", d.unusual, (r) => `<td><b>${r.volRatio.toFixed(1)}×</b> normal</td>`)}${list("Largest near 52-week highs", d.highs, chg)}
  </div></section>`;
}
async function loadWatchlist() {
  const w = watch.all(), box = $("#watchBox");
  if (!box || !w.length) return;
  let rows;
  try { rows = await getJSON("/api/quotes?symbols=" + encodeURIComponent(w.join(","))); } catch { return; }
  if (!$("#watchBox")) return;
  $("#watchBox").innerHTML = `<section><div class="row-between"><h2>Your watchlist</h2><a class="btn" href="/compare?t=${w.slice(0, 5).map(encodeURIComponent).join(",")}" data-link>Compare</a></div>
    <div class="watch-grid">${rows.map((r) => `<a class="card watch-card" href="/stock/${encodeURIComponent(r.symbol)}" data-link><div class="row-between"><b>${esc(r.symbol)}</b>${starBtn(r.symbol)}</div><div class="v num">${fmtPrice(r.price)}</div><div class="num ${(r.change ?? 0) >= 0 ? "up" : "down"}">${signed(r.change, 2)}</div></a>`).join("")}</div></section>`;
}
const SUMMARY_SLOTS = [0, 1, 2, 3];

/* ------------------------------------------------------------ index cards */
async function loadSummary() {
  let rows = home.summary && Date.now() - home.summary.at < 120e3 ? home.summary.rows : null;
  if (!rows) {
    try { rows = await (await fetch("/api/market/summary")).json(); home.summary = { at: Date.now(), rows }; } catch { rows = []; }
  }
  const el = $("#idxRow"); if (!el || !Array.isArray(rows)) return;
  if (!rows.length) { el.innerHTML = `<div class="card muted">Index levels aren't available right now.</div>`; return; }
  el.innerHTML = rows.map((r) => {
    const up = r.change >= 0;
    return `<a class="card idx-card" href="/stock/${encodeURIComponent(r.symbol)}" data-link>
      <div class="k">${esc(r.label)}</div>
      <div class="v num">${r.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
      <div class="num ${up ? "up" : "down"}" style="font-weight:700">${up ? "▲" : "▼"} ${signed(r.change, 2)} <span class="muted" style="font-weight:500">today</span></div>
      ${sparkLine(r.spark, up)}</a>`;
  }).join("");
}
function sparkLine(v, up) {
  if (!v || v.length < 2) return "";
  const W = 200, H = 36, min = Math.min(...v), span = Math.max(...v) - min || 1;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * W).toFixed(1)},${(H - 3 - ((x - min) / span) * (H - 6)).toFixed(1)}`).join(" ");
  return `<svg class="idx-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    <polyline points="${pts}" fill="none" stroke="${up ? css("--good") : css("--bad")}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

/* ------------------------------------------------------------ heat map */
// Diverging color: neutral at 0%, full green/red at ±3%. Text flips to the on-status color on strong fills.
function heatColor(ch) {
  if (ch == null) return "var(--hm-zero)";
  const p = Math.min(Math.abs(ch) / 0.03, 1);
  return `color-mix(in srgb, var(${ch >= 0 ? "--good" : "--bad"}) ${Math.round(12 + p * 78)}%, var(--hm-zero))`;
}
const heatText = (ch) => (ch != null && Math.abs(ch) >= 0.012 ? "var(--on-status)" : "var(--ink)");

async function loadHeatmap() {
  const idx = home.index, box = $("#heatmap"); if (!box) return;
  const hit = home.maps[idx];
  if (hit && Date.now() - hit.at < 120e3) return drawHeatmap(hit.data);
  box.innerHTML = `<div class="loading"><div class="spinner"></div>Loading ${esc(HEAT_INDEXES.find((x) => x[0] === idx)[1])}…</div>`;
  try {
    const r = await fetch("/api/market/heatmap/" + idx);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    home.maps[idx] = { at: Date.now(), data: await r.json() };
  } catch (e) {
    if (home.index === idx && $("#heatmap")) $("#heatmap").innerHTML = `<div class="error"><h3>Couldn't load the heat map</h3><p class="sub">${esc(e.message)}</p></div>`;
    return;
  }
  if (home.index === idx) drawHeatmap(home.maps[idx].data);
}

function drawHeatmap(d) {
  const box = $("#heatmap"); if (!box) return;
  if (!box.clientWidth) return;   // hidden page: redrawn by the resize handler once it has a size
  const W = box.clientWidth, H = W < 640 ? 520 : Math.round(Math.min(680, Math.max(480, W * 0.55)));
  box.style.height = H + "px";
  const sectors = squarify(d.sectors.map((s) => ({ value: s.cap, s })), { x: 0, y: 0, w: W, h: H });
  let html = "";
  for (const { it, x, y, w, h } of sectors) {
    const s = it.s, head = h > 44 && w > 70 ? 18 : 0;
    html += `<div class="hm-sector" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px">`;
    if (head) html += `<div class="hd" data-tip="${esc(s.name)}: ${signed(s.change, 2)} today (weighted by size)">${esc(s.name)} <span class="${(s.change ?? 0) >= 0 ? "up" : "down"}">${signed(s.change, 1)}</span></div>`;
    const tiles = squarify(s.items.map((i) => ({ value: i.cap, i })), { x: 0, y: head, w, h: h - head });
    for (const t of tiles) {
      const i = t.it.i, small = Math.min(t.w, t.h);
      const yr = home.when === "year", shown = yr ? i.ch1y : i.change, tone = shown == null ? null : yr ? shown / 10 : shown;
      const fs = Math.max(9, Math.min(26, Math.sqrt(t.w * t.h) / 5.5));
      const label = t.w > 28 && t.h > 16 ? `<b style="font-size:${fs}px">${esc(i.symbol)}</b>${t.h > fs * 2.6 && t.w > 40 ? `<span style="font-size:${Math.max(9, fs * 0.62)}px">${signed(shown, yr ? 0 : 1)}</span>` : ""}` : "";
      const tip = `${i.name} (${i.symbol})\n${price(i.price)} · ${signed(i.change, 2)} today · ${signed(i.ch1y)} in a year\nMarket value ${money(i.cap)}`;
      html += `<a class="tile" href="/stock/${encodeURIComponent(i.symbol)}" data-link data-tip="${esc(tip)}"
        style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;background:${heatColor(tone)};color:${heatText(tone)}">${small > 12 ? label : ""}</a>`;
    }
    html += `</div>`;
  }
  box.innerHTML = html;
  drawLegend();
  const n = d.sectors.reduce((a, s) => a + s.items.length, 0);
  const up = d.sectors.reduce((a, s) => a + s.items.filter((i) => (i.change ?? 0) > 0).length, 0);
  const when = new Date(d.asOf * 1000).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  $("#hmNote").innerHTML = `${esc(d.label)}: <b class="up">${up} up</b>, <b class="down">${n - up} down</b> today. Size shows market value, color shows the move. Updated ${when}.`;
}

// Squarified treemap (Bruls, Huizing & van Wijk): lays out items (largest first) in rows that keep
// boxes as close to square as possible. Returns [{it, x, y, w, h}] in the rectangle's coordinates.
function squarify(items, rect) {
  const out = [];
  const list = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  const total = list.reduce((a, b) => a + b.value, 0);
  if (!total || rect.w <= 0 || rect.h <= 0) return out;
  let { x, y, w, h } = rect;
  const scale = (w * h) / total;
  const nodes = list.map((it) => ({ it, a: it.value * scale }));
  let row = [];
  const worst = (r, side) => {
    const s = r.reduce((a, b) => a + b.a, 0), mx = Math.max(...r.map((q) => q.a)), mn = Math.min(...r.map((q) => q.a));
    return Math.max((side * side * mx) / (s * s), (s * s) / (side * side * mn));
  };
  const place = () => {
    const s = row.reduce((a, b) => a + b.a, 0);
    if (w >= h) {   // a column on the left
      const cw = s / h; let cy = y;
      for (const r of row) { const rh = r.a / cw; out.push({ it: r.it, x, y: cy, w: cw, h: rh }); cy += rh; }
      x += cw; w -= cw;
    } else {        // a row along the top
      const ch = s / w; let cx = x;
      for (const r of row) { const rw = r.a / ch; out.push({ it: r.it, x: cx, y, w: rw, h: ch }); cx += rw; }
      y += ch; h -= ch;
    }
    row = [];
  };
  while (nodes.length) {
    const side = Math.min(w, h), n = nodes[0];
    if (!row.length || worst([...row, n], side) <= worst(row, side)) { row.push(n); nodes.shift(); } else place();
  }
  if (row.length) place();
  return out;
}

let heatResize;
window.addEventListener("resize", () => {
  clearTimeout(heatResize);
  heatResize = setTimeout(() => { const m = home.maps[home.index]; if ($("#heatmap") && m) drawHeatmap(m.data); }, 150);
});
