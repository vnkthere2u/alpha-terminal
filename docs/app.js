/* AlphaTerminal dashboard — static, reads data/*.json written by the engine. */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const app = $("#app");
  const state = { note: null, family: "all", horizon: "all" };

  // ── DOM helper: text always via textContent (LLM/web content is untrusted) ──
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "style") n.setAttribute("style", v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    return n;
  }
  const svgEl = (tag, attrs) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    return n;
  };

  // ── Theme ──
  const root = document.documentElement;
  try { const t = localStorage.getItem("at-theme"); if (t) root.dataset.theme = t; } catch (e) { /* storage blocked */ }
  $("#themeBtn").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("at-theme", root.dataset.theme); } catch (e) { /* ignore */ }
    if (state.note) render(state.note);
  });

  // ── Formatting ──
  const fmtChg = (r, v) => {
    if (v == null) return "—";
    if (r.is_rate) return (v >= 0 ? "+" : "") + Math.round(v * 100) + "bp";
    if (r.is_level) return (v >= 0 ? "+" : "") + v.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  };
  const dirCell = (r, v) => {
    const td = el("td", { class: "num" });
    if (v == null) { td.textContent = "—"; return td; }
    const up = v > 0, flat = v === 0;
    td.appendChild(el("span", { class: flat ? "" : up ? "up" : "down" }, flat ? "" : up ? "▲ " : "▼ ", fmtChg(r, v)));
    return td;
  };
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");

  // ── Line chart with crosshair tooltip ──
  function drawLine(wrap, tip, series, opts) {
    const W = Math.max(260, Math.round(opts.width || 600)), H = opts.height || 180, P = { l: 40, r: 10, t: 10, b: 22 };
    const xs = series.map((d) => new Date(d[0]).getTime());
    const ys = series.map((d) => d[1]);
    let lo = Math.min(...ys, ...(opts.refs || []).map((r) => r.y)), hi = Math.max(...ys, ...(opts.refs || []).map((r) => r.y));
    if (opts.domain) { lo = opts.domain[0]; hi = opts.domain[1]; }
    const pad = (hi - lo) * 0.08 || 1; if (!opts.domain) { lo -= pad; hi += pad; }
    const X = (t) => P.l + ((t - xs[0]) / (xs[xs.length - 1] - xs[0] || 1)) * (W - P.l - P.r);
    const Y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.label || "chart" });
    const css = getComputedStyle(root);
    const c = (n) => css.getPropertyValue(n).trim();
    // grid + y ticks
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4, y = Y(v);
      svg.appendChild(svgEl("line", { x1: P.l, x2: W - P.r, y1: y, y2: y, stroke: c("--grid"), "stroke-width": 1 }));
      const t = svgEl("text", { x: P.l - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: c("--muted"), "font-family": "var(--mono)" });
      t.textContent = (opts.yfmt || ((x) => x.toFixed(1)))(v); svg.appendChild(t);
    }
    // x ticks: first, middle, last
    [0, Math.floor(xs.length / 2), xs.length - 1].forEach((i, k) => {
      const t = svgEl("text", { x: X(xs[i]), y: H - 6, "text-anchor": ["start", "middle", "end"][k], "font-size": 10, fill: c("--muted") });
      t.textContent = series[i][0].slice(0, 7); svg.appendChild(t);
    });
    // reference lines
    (opts.refs || []).forEach((r) => {
      svg.appendChild(svgEl("line", { x1: P.l, x2: W - P.r, y1: Y(r.y), y2: Y(r.y), stroke: c(r.strong ? "--ink-2" : "--axis"), "stroke-width": 1, "stroke-dasharray": r.dash ? "4 4" : "" }));
      if (r.label) { const t = svgEl("text", { x: P.l + 4, y: Y(r.y) - 4, "text-anchor": "start", "font-size": 10, fill: c("--muted") }); t.textContent = r.label; svg.appendChild(t); }
    });
    const d = series.map((p, i) => (i ? "L" : "M") + X(xs[i]).toFixed(1) + "," + Y(ys[i]).toFixed(1)).join("");
    svg.appendChild(svgEl("path", { d, fill: "none", stroke: c("--series-1"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    const last = svgEl("circle", { cx: X(xs[xs.length - 1]), cy: Y(ys[ys.length - 1]), r: 4, fill: c("--series-1"), stroke: c("--surface"), "stroke-width": 2 });
    svg.appendChild(last);
    const cross = svgEl("line", { y1: P.t, y2: H - P.b, stroke: c("--ink-2"), "stroke-width": 1, visibility: "hidden" });
    const dot = svgEl("circle", { r: 4, fill: c("--series-1"), stroke: c("--surface"), "stroke-width": 2, visibility: "hidden" });
    svg.appendChild(cross); svg.appendChild(dot);
    const hit = svgEl("rect", { x: P.l, y: 0, width: W - P.l - P.r, height: H, fill: "transparent", tabindex: 0 });
    svg.appendChild(hit);
    const show = (i) => {
      const x = X(xs[i]), y = Y(ys[i]);
      cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("visibility", "visible");
      tip.replaceChildren(el("b", null, (opts.tfmt || ((v) => v.toFixed(2)))(ys[i])), el("span", { class: "muted" }, "  " + series[i][0]));
      tip.style.display = "block";
      const rect = svg.getBoundingClientRect(), scale = rect.width / W;
      const left = Math.min(Math.max(x * scale - tip.offsetWidth / 2, 0), rect.width - tip.offsetWidth);
      tip.style.left = left + "px"; tip.style.top = Math.max(y * scale - 40, -8) + "px";
    };
    const hide = () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.style.display = "none"; };
    const nearest = (clientX) => {
      const rect = svg.getBoundingClientRect(), x = ((clientX - rect.left) / rect.width) * W;
      let best = 0, bd = Infinity;
      xs.forEach((t, i) => { const dd = Math.abs(X(t) - x); if (dd < bd) { bd = dd; best = i; } });
      return best;
    };
    let kIdx = xs.length - 1;
    hit.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", () => show(kIdx));
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") kIdx = Math.max(0, kIdx - 1); else if (e.key === "ArrowRight") kIdx = Math.min(xs.length - 1, kIdx + 1); else return;
      e.preventDefault(); show(kIdx);
    });
    wrap.querySelectorAll("svg").forEach((n) => n.remove());
    wrap.appendChild(svg);
  }

  // Charts are drawn at their real pixel width so axis text stays legible on every screen.
  const charts = [];
  function lineChart(series, opts) {
    opts = opts || {};
    const wrap = el("div", { class: "chart" });
    const tip = el("div", { class: "tip", role: "status" });
    wrap.appendChild(tip);
    if (!series || series.length < 2) { wrap.appendChild(el("p", { class: "muted small" }, "No history in this edition.")); return wrap; }
    charts.push(() => drawLine(wrap, tip, series, Object.assign({}, opts, { width: wrap.clientWidth || 600 })));
    return wrap;
  }
  function drawCharts() { charts.forEach((f) => f()); }
  let rz;
  window.addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(drawCharts, 150); });

  // ── Sections ──
  function section(title, ...kids) { return [el("h2", null, title), ...kids]; }

  function heroRow(n) {
    const a = n.analysis, q = n.quant, g = q.regime || {};
    const regimeCard = el("div", { class: "card" },
      el("p", { class: "label" }, "Regime"),
      el("div", { class: "hero" }, a ? a.regime.label : "AI synthesis unavailable"),
      a ? el("p", { class: "ink2", style: "margin:0 0 6px" }, a.regime.one_liner) : el("p", { class: "muted" }, "Quant signals below are still current."),
      a && a.regime.shift_vs_prior ? el("p", { class: "small muted", style: "margin:0" }, "vs prior: " + a.regime.shift_vs_prior) : null);
    const ra = g.risk_appetite;
    const raCard = el("div", { class: "card" },
      el("p", { class: "label" }, "Cross-asset risk appetite (0–100)"),
      ra ? el("div", { class: "hero" }, String(ra.value), el("span", { class: "small muted", style: "font-weight:400" }, `  1w ${ra.w1_ago} · 1m ${ra.m1_ago}`)) : el("p", { class: "muted" }, "n/a"),
      ra ? lineChart(ra.history, { height: 120, domain: [0, 100], refs: [{ y: 50, dash: true, label: "neutral" }], yfmt: (v) => v.toFixed(0), tfmt: (v) => v.toFixed(0) + "/100", label: "Risk appetite composite, 2 years" }) : null);
    const qd = g.quadrant, cv = g.curve, cr = g.credit, lq = g.liquidity;
    const kv = (k, v) => el("div", { class: "kv" }, el("span", { class: "muted" }, k), el("span", { style: "font-family:var(--mono);font-size:12px" }, v));
    const macroCard = el("div", { class: "card" },
      el("p", { class: "label" }, "Macro state"),
      qd ? el("div", { class: "hero", style: "font-size:22px" }, qd.now) : null,
      qd ? el("p", { class: "small muted", style: "margin:0 0 8px" }, `Market-implied quadrant (1m ago: ${qd.m1_ago}). Growth z ${qd.growth_z}, inflation z ${qd.inflation_z}.`) : null,
      cv ? kv("UST curve (1m)", `${cv.shape_1m}; 2s10s ${cv.s2s10_bp}bp`) : null,
      g.rates_decomp_3m ? kv("10Y 3m driver", `${g.rates_decomp_3m.driver} (real ${g.rates_decomp_3m.real_bp}bp, BE ${g.rates_decomp_3m.breakeven_bp}bp)`) : null,
      g.term_premium ? kv("Term premium", `${g.term_premium.value_bp}bp · 5y pctile ${g.term_premium.pct5y}`) : null,
      cr ? kv("HY OAS", `${cr.hy_oas_bp}bp (1m ${cr.hy_1m_bp >= 0 ? "+" : ""}${cr.hy_1m_bp}bp)`) : null,
      lq ? kv("Fed net liquidity", `$${lq.netliq_bn.toLocaleString()}bn (4w ${lq.chg_4w_bn >= 0 ? "+" : ""}${lq.chg_4w_bn.toLocaleString()})`) : null,
      g.vix_term != null ? kv("VIX3M / VIX", `${g.vix_term}${g.vix_term < 1 ? " · inverted" : ""}`) : null);
    return el("div", { class: "grid g3" }, regimeCard, raCard, macroCard);
  }

  function briefSections(a) {
    if (!a) return [];
    const out = [];
    out.push(...section("Bottom line", el("div", { class: "card" }, el("ul", { class: "clean" }, a.bottom_line.map((b) => el("li", null, b))))));
    if (a.developments.length) {
      out.push(...section("What matters", el("div", { class: "grid g2" }, a.developments.map((d) => el("div", { class: "card" },
        el("h3", null, d.headline), el("p", { class: "ink2", style: "margin:0 0 6px" }, d.what_happened),
        el("p", { style: "margin:0 0 4px" }, el("b", null, "So what: "), d.so_what),
        el("p", { class: "small ink2", style: "margin:0 0 4px" }, el("b", null, "2nd order: "), d.second_order),
        el("p", { class: "small muted", style: "margin:0" }, d.assets))))));
    }
    const cols = ["short", "medium", "long"].map((h) => {
      const items = a.trends.filter((t) => t.horizon === h);
      return el("div", { class: "card" }, el("p", { class: "label" }, { short: "Short · 1–4 weeks", medium: "Medium · 1–6 months", long: "Long · 6 months +" }[h]),
        items.length ? items.map((t) => el("div", { class: "trend " + t.status },
          el("span", { class: "tag" + (t.status === "emerging" ? " new" : "") }, cap(t.status)),
          el("b", null, t.title), el("p", { class: "small ink2", style: "margin:4px 0 2px" }, t.evidence),
          el("p", { class: "small", style: "margin:0" }, "→ " + t.read))) : el("p", { class: "muted small" }, "Nothing material."));
    });
    out.push(...section("Trends & regime shifts", el("div", { class: "grid g3" }, cols)));
    const hs = a.hidden_signal;
    out.push(el("div", { class: "grid g2", style: "margin-top:12px" },
      el("div", { class: "card" }, el("p", { class: "label" }, "Under the radar"), el("h3", null, hs.title), el("p", { class: "ink2", style: "margin:0 0 6px" }, hs.evidence), el("p", { class: "small", style: "margin:0" }, el("b", null, "Why it's missed: "), hs.why_consensus_misses)),
      el("div", { class: "card" }, el("p", { class: "label" }, "India / Gulf lens"), el("p", { style: "margin:0" }, a.india_gulf))));
    if (a.watch.length) out.push(...section("Watch — conditions that would change the picture", el("div", { class: "card" }, el("ul", { class: "clean" }, a.watch.map((w) => el("li", null, el("b", null, w.trigger), " → ", w.implication))))));
    return out;
  }

  function signalTable(q) {
    const fams = ["all", ...new Set(q.signals.map((s) => s.family))];
    const hors = ["all", "short", "medium", "long"];
    const holder = el("div");
    const draw = () => {
      const rows = q.signals.filter((s) => (state.family === "all" || s.family === state.family) && (state.horizon === "all" || s.horizon === state.horizon));
      const chips = el("div", { class: "chips" },
        fams.map((f) => el("button", { type: "button", "aria-pressed": String(state.family === f), onclick: () => { state.family = f; draw(); } }, cap(f))),
        el("span", { class: "muted small", style: "align-self:center;margin:0 4px" }, "|"),
        hors.map((h) => el("button", { type: "button", "aria-pressed": String(state.horizon === h), onclick: () => { state.horizon = h; draw(); } }, cap(h))));
      const table = el("table", null,
        el("thead", null, el("tr", null, ["Severity", "Signal", "Family", "Horizon", "Status"].map((h, i) => el("th", { class: i === 0 ? "num" : "" }, h)))),
        el("tbody", null, rows.map((s) => el("tr", null,
          el("td", { class: "num" }, el("span", { class: "sev" }, el("i", { style: `width:${Math.max(4, s.severity * 4)}px` }), s.severity.toFixed(1))),
          el("td", null, el("b", null, s.title), el("div", { class: "small ink2" }, s.detail)),
          el("td", null, s.family), el("td", null, s.horizon),
          el("td", null, el("span", { class: "tag" + (s.status === "new" ? " new" : "") }, s.status === "new" ? "New" : `Ongoing ${s.days_active}d`))))));
      holder.replaceChildren(chips, el("div", { class: "card scroll" }, rows.length ? table : el("p", { class: "muted" }, "No signals for this filter.")));
    };
    draw();
    return section(`Signal tape (${q.signals.length})`, holder);
  }

  function relationships(q) {
    if (!q.relationships || !q.relationships.length) return [];
    return section("Relationship monitor — 26-week rolling correlation vs 5-year norm",
      el("div", { class: "grid g3" }, q.relationships.map((r) => el("div", { class: "card" },
        el("div", { class: "rel-head" }, el("h3", null, r.name),
          el("span", { class: "status", style: r.state === "intact" ? "color:var(--muted)" : r.state === "weakening" ? "color:var(--serious)" : "color:var(--critical)" },
            r.state === "intact" ? "● Intact" : r.state === "weakening" ? "◐ Weakening" : r.state === "broken" ? "✕ Broken" : "⇅ Sign flip")),
        el("p", { class: "small muted", style: "margin:0 0 4px" }, `Now ${r.corr_26w >= 0 ? "+" : ""}${r.corr_26w} (13w ${r.corr_13w >= 0 ? "+" : ""}${r.corr_13w}) · norm ${r.baseline_median >= 0 ? "+" : ""}${r.baseline_median} · z ${r.z}`),
        r.history ? lineChart(r.history, { height: 110, domain: [-1, 1], refs: [{ y: 0 }, { y: r.baseline_median, dash: true, strong: true, label: "5y norm" }], yfmt: (v) => v.toFixed(1), tfmt: (v) => (v >= 0 ? "+" : "") + v.toFixed(2), label: r.name + " rolling correlation" }) : null,
        el("p", { class: "small ink2", style: "margin:6px 0 0" }, r.hint)))));
  }

  function positioning(q) {
    if (!q.positioning || !q.positioning.length) return [];
    const rows = q.positioning.map((p) => {
      const ext = p.pct3y >= 90 || p.pct3y <= 10;
      const tip = `${p.name}: net ${p.net.toLocaleString()} (${p.net_oi_pct}% OI), 3y pctile ${p.pct3y}, 4w ago ${p.pct3y_4w_ago}, as of ${p.asof}`;
      return el("div", { class: "bar-row", title: tip },
        el("span", null, p.name, el("span", { class: "muted" }, " · " + p.who)),
        el("div", { class: "bar-track", role: "img", "aria-label": tip },
          el("div", { class: "bar-zone", style: "left:0;width:10%" }), el("div", { class: "bar-zone", style: "left:90%;width:10%" }),
          el("div", { class: "bar-fill", style: `width:${p.pct3y}%` }),
          el("div", { class: "bar-tick", style: `left:calc(${p.pct3y_4w_ago}% - 1px)` })),
        el("span", { class: "num", style: "font-family:var(--mono);text-align:right" + (ext ? ";font-weight:700" : "") }, String(p.pct3y)));
    });
    return section("Positioning — CFTC net long as 3-year percentile",
      el("div", { class: "card" }, el("p", { class: "small muted", style: "margin:0 0 6px" }, "Bar = current percentile; tick = 4 weeks ago; shaded = crowded zones (<10, >90). Managed money for commodities, leveraged funds for financials."), rows));
  }

  function behaviourTable(q) {
    const rows = (q.behaviour || []).filter((b) => b.recent_recovery != null || b.open_days);
    if (!rows.length) return [];
    return section("How corrections are resolving (last 9 months vs 5-year base rate)",
      el("div", { class: "card scroll" }, el("table", null,
        el("thead", null, el("tr", null, ["Asset", "Episode", "Recovered <40d: recent", "Base rate", "Avg depth σ: recent", "Base", "Open now"].map((h, i) => el("th", { class: i > 1 ? "num" : "" }, h)))),
        el("tbody", null, rows.map((b) => el("tr", null, el("td", null, b.name), el("td", null, b.type),
          el("td", { class: "num" }, b.recent_recovery != null ? `${b.recent_recovery}% (n=${b.recent_done})` : "—"),
          el("td", { class: "num" }, `${b.base_recovery}% (n=${b.base_n})`),
          el("td", { class: "num" }, b.recent_depth != null ? String(b.recent_depth) : "—"),
          el("td", { class: "num" }, String(b.base_depth)),
          el("td", { class: "num" }, b.open_days ? `${b.open_days}d · ${b.open_depth}σ` : "—")))))));
  }

  function snapshotTable(q) {
    return section("Market tape",
      el("div", { class: "card scroll" }, el("table", null,
        el("thead", null, el("tr", null, ["Instrument", "Last", "1w", "1m", "3m", "12m", "5y pctile"].map((h, i) => el("th", { class: i ? "num" : "" }, h)))),
        el("tbody", null, q.snapshot.map((r) => el("tr", null, el("td", null, r.name), el("td", { class: "num" }, r.last_fmt),
          dirCell(r, r.w1), dirCell(r, r.m1), dirCell(r, r.m3), dirCell(r, r.y1), el("td", { class: "num" }, String(r.pct5y))))))));
  }

  function moreCharts(q) {
    const g = q.regime || {}, ind = q.india || {};
    const cards = [];
    if (g.liquidity && g.liquidity.history) cards.push(el("div", { class: "card" }, el("p", { class: "label" }, "Fed net liquidity, USD bn (balance sheet − TGA − RRP)"),
      lineChart(g.liquidity.history, { height: 160, yfmt: (v) => (v / 1000).toFixed(1) + "k", tfmt: (v) => "$" + Math.round(v).toLocaleString() + "bn", label: "Fed net liquidity" })));
    if (ind.nifty_usd_vs_em && ind.nifty_usd_vs_em.history) cards.push(el("div", { class: "card" }, el("p", { class: "label" }, "Nifty in USD relative to EM equities (indexed, 2y ago = 100)"),
      lineChart(ind.nifty_usd_vs_em.history, { height: 160, refs: [{ y: 100, dash: true }], yfmt: (v) => v.toFixed(0), tfmt: (v) => v.toFixed(1), label: "Nifty USD vs EEM" })));
    return cards.length ? section("Liquidity & India rotation", el("div", { class: "grid g2" }, cards)) : [];
  }

  function newsBlock(n) {
    if (!n.news) return [];
    return [el("details", { class: "card", style: "margin-top:12px" }, el("summary", null, "Source news facts used for this edition"), el("pre", { class: "news" }, n.news))];
  }

  function render(n) {
    const q = n.quant;
    charts.length = 0;
    $("#subtitle").textContent = `${n.mode === "weekly" ? "Weekly review" : "Daily brief"} · ${n.note_label} · data to ${q.asof} US close`;
    app.replaceChildren(heroRow(n), ...briefSections(n.analysis), ...signalTable(q), ...relationships(q),
      ...positioning(q), ...behaviourTable(q), ...moreCharts(q), ...snapshotTable(q), ...newsBlock(n));
    drawCharts();
    const caveats = [...((n.analysis && n.analysis.data_caveats) || []), ...(n.audit || [])];
    if (n.gaps && n.gaps.length) caveats.push("Missing data: " + n.gaps.join(", "));
    $("#foot").replaceChildren(
      caveats.length ? el("p", null, "Caveats: " + caveats.join(" · ")) : null,
      el("p", null, "Sources: Yahoo Finance (prices), FRED (rates, credit, liquidity), CFTC (positioning), news via Google News RSS, central-bank feeds and Gemini. " +
        (n.llm ? `Model ${n.llm.model} · ${n.llm.input_tokens.toLocaleString()} in / ${n.llm.output_tokens.toLocaleString()} out tokens · ${n.llm.grounded ? n.llm.searches + " Google searches" : "headlines only (no search grounding)"}. ` : "") +
        "For information only; not investment advice."));
  }

  async function load(path) {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) throw new Error(path + " " + r.status);
    return r.json();
  }

  async function init() {
    try {
      const [latest, idx] = await Promise.all([load("data/latest.json"), load("data/index.json").catch(() => ({ dates: [] }))]);
      state.note = latest;
      const sel = $("#dateSel");
      const dates = idx.dates && idx.dates.length ? idx.dates : [latest.note_date];
      dates.forEach((d, i) => sel.appendChild(el("option", { value: d }, i === 0 ? d + " (latest)" : d)));
      sel.value = latest.note_date;
      sel.addEventListener("change", async () => {
        try {
          state.note = sel.value === latest.note_date ? latest : await load(`data/history/${sel.value}.json`);
          state.family = "all"; state.horizon = "all";
          render(state.note);
        } catch (e) { app.replaceChildren(el("div", { class: "empty" }, "Could not load that edition.")); }
      });
      render(latest);
    } catch (e) {
      app.replaceChildren(el("div", { class: "empty" }, "No brief published yet. The first edition appears after the scheduled run."));
    }
  }
  init();
})();
