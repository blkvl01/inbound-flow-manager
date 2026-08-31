/* Full-width volume-trend chart for the KPI page.

   Data model ported from the Reggeli Riport report (date-bucketed kg per LMP /
   per AWB prefix, day/week views, multi-select filtering), re-rendered as an
   animated SVG in the Flow Manager visual language.

   Breakdown (3 filters): LMP / Countries / Prefix.   View: Day / Week.
   Plus an inspected period range (presets + from/to dates).
   Fed by the `trend` slice of kpi-page-chart-store via window.__renderKpiTrend. */
(function () {
  "use strict";

  if (window.__kpiTrendInit) return;
  window.__kpiTrendInit = true;

  var SVG_W = 1180, SVG_H = 300;
  var PAD = { l: 58, r: 18, t: 8, b: 34 };
  var IW = SVG_W - PAD.l - PAD.r;
  var IH = SVG_H - PAD.t - PAD.b;
  var BASE_Y = PAD.t + IH;
  var DRAW_MS = 1620;       // line draw-on duration (must match CSS transition)
  var STAGGER_MS = 92;
  var MAX_OPENING_SPREAD = 2300;
  var FILTER_DRAW_MS = 780;
  var DOTS_MAX_POINTS = 14; // above this, drop per-point dots (rely on hover)

  var SERIES_HUES = [
    24, 211, 272, 151, 48, 324, 188, 4, 94, 236, 294, 170,
    36, 346, 202, 126, 14, 258, 314, 70, 222, 144, 356, 282,
    196, 106, 334, 44, 244, 160, 8, 302, 82, 216, 134, 352,
    268, 184, 58, 320, 230, 116, 18, 288, 156, 340, 205, 52
  ];
  var BREAKDOWNS = [
    { id: "lmp", label: "LMP" },
    { id: "country", label: "Countries" },
    { id: "prefix", label: "Prefix" }
  ];
  var MODES = [
    { id: "week", label: "Week" },
    { id: "day", label: "Day" }
  ];
  var COUNTRY_NAMES = {
    AT: "Austria", CZ: "Czech Republic", HU: "Hungary", SI: "Slovenia",
    SK: "Slovakia", HR: "Croatia", UA: "Ukraine", RO: "Romania",
    PL: "Poland", DE: "Germany", RS: "Serbia", BG: "Bulgaria",
    GR: "Greece", IT: "Italy", FR: "France", NL: "Netherlands",
    "Egyéb": "Other"
  };
  // Each country owns a base hue; its LMPs get distinct shades of it — the chart,
  // the chips, the table and the XLSX export all read these. BG = red per the brief;
  // the rest are spread for separation, and unknown codes hash to a stable hue.
  var COUNTRY_HUES = {
    BG: 0, DE: 14, RO: 30, AT: 47, NL: 72, SI: 96, IT: 110, HU: 134,
    MD: 168, SK: 192, UA: 212, GR: 226, FR: 244, CZ: 260, PL: 286, HR: 320, RS: 344
  };
  // Shared, deliberately neutral colour for every series with no country (IHERB,
  // OMNIVA…) — same for all of them, set apart from the saturated country hues.
  var NO_COUNTRY_COLOR = "#9aa7be";
  var data = null;
  var state = {
    mode: "week",
    breakdown: "lmp",
    selected: { lmp: null, country: null, prefix: null },
    search: { lmp: "", country: "", prefix: "" },
    range: { from: null, to: null }, // ISO yyyy-mm-dd, null = full span
    tableOpen: true,
    focus: []                         // Ctrl+click solo set (series shown in isolation)
  };
  var lastSig = "";
  var lastDataContentSig = "";
  var lastExportPayload = null;
  var lastRenderMax = 0;   // Y-axis ceiling of the last drawn chart (rescale detection)
  var currentDataToken = null;    // stable trend signature whose opening reveal is tracked
  var revealedToken = null;       // token whose opening draw-on has actually played
  var pendingRevealToken = null;  // owe a reveal once the section scrolls into view
  var pendingMorphRatio = 0;      // Y-scale morph factor for the next render (filter recalc)
  var pendingMorphDelay = 0;      // delay before the Y-scale morph starts
  var _tableSwap = false;         // animate the table content on the next renderTable
  var sectionObserver = null;
  var sectionVisible = false;
  var MORPH_MS = 560;
  var trendClipSeq = 0;
  // The Y-scale morph rides on transform-box:view-box; if the renderer lacks it we
  // fall back to the soft swap so a removal never animates from a wrong origin.
  var MORPH_OK = (typeof CSS !== "undefined" && CSS.supports && CSS.supports("transform-box", "view-box"));

  // ── helpers ──────────────────────────────────────────────────────────────
  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function asNum(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function hslToHex(h, s, l) {
    h = ((h % 360) + 360) % 360;
    s /= 100;
    l /= 100;
    var c = (1 - Math.abs(2 * l - 1)) * s;
    var x = c * (1 - Math.abs((h / 60) % 2 - 1));
    var m = l - c / 2;
    var r = 0, g = 0, b = 0;
    if (h < 60) { r = c; g = x; }
    else if (h < 120) { r = x; g = c; }
    else if (h < 180) { g = c; b = x; }
    else if (h < 240) { g = x; b = c; }
    else if (h < 300) { r = x; b = c; }
    else { r = c; b = x; }
    function toHex(v) {
      return Math.round((v + m) * 255).toString(16).padStart(2, "0");
    }
    return "#" + toHex(r) + toHex(g) + toHex(b);
  }
  function seriesColor(index) {
    var hue = SERIES_HUES[index % SERIES_HUES.length] + Math.floor(index / SERIES_HUES.length) * 11;
    var sat = 78 + (index % 3) * 5;
    var light = [56, 50, 62, 46][index % 4];
    return hslToHex(hue, sat, light);
  }
  function reduced() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function fmtKg(v) {
    var n = Math.round(asNum(v));
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " kg";
  }
  function fmtCompact(v) {
    var n = asNum(v);
    if (n >= 1000) return (Math.round(n / 100) / 10).toString().replace(".", ",") + " t";
    return Math.round(n) + " kg";
  }
  function tightMax(v) {
    v = asNum(v);
    if (v <= 0) return 1000;
    return v * 1.035;
  }
  function parseCountry(label) {
    label = String(label || "");
    var m = label.match(/^TEMU\s+([A-Z]{2})(?:\b|-)/);
    if (m) return m[1];
    m = label.match(/^[A-Z0-9]+\s+([A-Z]{2})(?:\b|-)/);
    if (m) return m[1];
    return "Egyéb";
  }
  function countryLabel(code) {
    if (code === "EgyĂ©b" || code === "Egyéb") return "Other";
    var name = COUNTRY_NAMES[code];
    return name && name !== code ? (code + " · " + name) : code;
  }
  function isoShift(iso, days) {
    var d = new Date(iso + "T00:00:00");
    d.setDate(d.getDate() + days);
    return d.getFullYear() + "-" +
      String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0");
  }
  function fmtDayLabel(iso) {
    if (!iso) return "";
    var p = iso.split("-");
    return p.length === 3 ? p[1] + "." + p[2] + "." : iso;
  }
  function dayIsoSet() {
    var set = (data && data.day && data.day.lmp) || null;
    var out = {};
    if (!set || !set.rows) return out;
    set.rows.forEach(function (row) {
      var iso = row.d0 || row.d1 || "";
      if (iso) out[iso] = true;
    });
    return out;
  }
  function eachIsoDay(from, to, fn) {
    if (!from || !to) return false;
    var d = new Date(from + "T00:00:00");
    var end = new Date(to + "T00:00:00");
    if (isNaN(d.getTime()) || isNaN(end.getTime()) || d > end) return false;
    while (d <= end) {
      var iso = d.getFullYear() + "-" +
        String(d.getMonth() + 1).padStart(2, "0") + "-" +
        String(d.getDate()).padStart(2, "0");
      if (fn(iso) === false) return false;
      d.setDate(d.getDate() + 1);
    }
    return true;
  }
  function rangeHasEveryDay(from, to) {
    var days = dayIsoSet();
    return eachIsoDay(from, to, function (iso) { return !!days[iso]; });
  }

  // ── data access ──────────────────────────────────────────────────────────
  function srcDim() { return state.breakdown === "prefix" ? "prefix" : "lmp"; }
  function rawSet() {
    if (!data || !data[state.mode]) return { cols: [], rows: [] };
    return data[state.mode][srcDim()] || { cols: [], rows: [] };
  }
  function countryHue(code) {
    if (Object.prototype.hasOwnProperty.call(COUNTRY_HUES, code)) return COUNTRY_HUES[code];
    var h = 0;
    for (var i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) % 360;
    return h;
  }
  function countryOf(name, breakdown) {
    return breakdown === "country" ? name : parseCountry(name);
  }
  // Group a breakdown's series by country → an ordered list + a per-series colour.
  // Country groups are ordered by volume (the no-country "Egyéb" group always last);
  // within a group each LMP gets a lighter→darker shade of the country's hue, so a
  // family reads as one colour while staying individually distinguishable.
  var _cgCache = { key: null, val: null };
  function countryGrouping(breakdown) {
    var key = state.mode + "|" + breakdown + "|" + lastSig;
    if (_cgCache.key === key) return _cgCache.val;
    var dim = breakdown === "prefix" ? "prefix" : "lmp";
    var set = (data && data[state.mode] && data[state.mode][dim]) || { cols: [], rows: [] };
    var totals = {}, names;
    if (breakdown === "country") {
      set.rows.forEach(function (row) {
        set.cols.forEach(function (col) {
          var c = parseCountry(col);
          totals[c] = (totals[c] || 0) + (row.vals[col] || 0);
        });
      });
      names = Object.keys(totals);
    } else {
      names = set.cols.slice();
      names.forEach(function (n) { totals[n] = 0; });
      set.rows.forEach(function (row) {
        names.forEach(function (n) { totals[n] += (row.vals[n] || 0); });
      });
    }
    var groups = {};
    names.forEach(function (n) {
      var c = countryOf(n, breakdown);
      (groups[c] = groups[c] || []).push(n);
    });
    var groupTotal = {};
    Object.keys(groups).forEach(function (c) {
      groups[c].sort(function (a, b) { return (totals[b] || 0) - (totals[a] || 0); });
      groupTotal[c] = groups[c].reduce(function (s, n) { return s + (totals[n] || 0); }, 0);
    });
    var groupOrder = Object.keys(groups).sort(function (a, b) {
      if (a === "Egyéb" && b !== "Egyéb") return 1;
      if (b === "Egyéb" && a !== "Egyéb") return -1;
      return groupTotal[b] - groupTotal[a];
    });
    var ordered = [], colors = {};
    groupOrder.forEach(function (c) {
      var arr = groups[c];
      if (c === "Egyéb") {
        arr.forEach(function (n) { ordered.push(n); colors[n] = NO_COUNTRY_COLOR; });
        return;
      }
      var hue = countryHue(c), n = arr.length;
      arr.forEach(function (name, i) {
        var t = n <= 1 ? 0.5 : i / (n - 1);
        ordered.push(name);
        colors[name] = hslToHex(hue, 82 - t * 14, 64 - t * 26);
      });
    });
    _cgCache = { key: key, val: { ordered: ordered, colors: colors } };
    return _cgCache.val;
  }
  function allSeries(breakdown) {
    breakdown = breakdown || state.breakdown;
    if (breakdown === "prefix") {
      var set = (data && data[state.mode] && data[state.mode].prefix) || { cols: [] };
      return set.cols.slice();
    }
    return countryGrouping(breakdown).ordered;
  }
  function colorMap(breakdown) {
    breakdown = breakdown || state.breakdown;
    if (breakdown === "prefix") {
      var map = {};
      allSeries(breakdown).forEach(function (name, i) { map[name] = seriesColor(i); });
      return map;
    }
    return countryGrouping(breakdown).colors;
  }
  function displayNameFor(breakdown, name) {
    return breakdown === "country" ? countryLabel(name) : String(name || "");
  }
  function searchText(breakdown) {
    return String(state.search[breakdown || state.breakdown] || "").trim().toLowerCase();
  }
  function searchMatches(breakdown, name) {
    var q = searchText(breakdown);
    return !q || displayNameFor(breakdown, name).toLowerCase().indexOf(q) >= 0;
  }
  function visibleSeries(breakdown) {
    breakdown = breakdown || state.breakdown;
    return allSeries(breakdown).filter(function (name) {
      return searchMatches(breakdown, name);
    });
  }
  function dashLength(el) {
    var len = 0;
    try { len = el.getTotalLength(); } catch (e) { len = 0; }
    // Generous headroom: getTotalLength can UNDER-measure a smooth cubic path, and
    // if the dash is shorter than the real stroke the tail never gets covered — so
    // the last bit pops in when the dash is cleared (the "snaps to the last segment"
    // bug). Over-sizing guarantees the whole stroke is covered end to end, and lets
    // us leave the dash in place (it still reads as a solid line) instead of risking
    // a mid-transition clear.
    return len ? Math.max(1, Math.ceil(len * 1.15 + 400)) : 0;
  }
  // Earliest/latest ISO date across the finest (day) dataset.
  function dayBounds() {
    var set = (data && data.day && data.day.lmp) || null;
    if (!set || !set.rows.length) {
      set = (data && data.week && data.week.lmp) || null;
    }
    if (!set || !set.rows.length) return null;
    var first = set.rows[0], last = set.rows[set.rows.length - 1];
    return { min: first.d0 || first.d1, max: last.d1 || last.d0 };
  }

  // Initialise / re-validate the selection for a breakdown. Crucially this only
  // seeds defaults when the breakdown has never been touched (null); an explicit
  // empty array (after "None") stays empty.
  function ensureSelection(breakdown) {
    var all = allSeries(breakdown);
    var cur = state.selected[breakdown];
    if (cur == null) {
      state.selected[breakdown] = all.slice();
    } else {
      state.selected[breakdown] = cur.filter(function (v) { return all.indexOf(v) >= 0; });
    }
    return state.selected[breakdown];
  }

  function rangeActive() { return !!(state.range.from || state.range.to); }
  function inRange(row) {
    if (!rangeActive()) return true;
    var f = state.range.from || "0000-01-01";
    var t = state.range.to || "9999-12-31";
    var r0 = row.d0 || row.d1 || "";
    var r1 = row.d1 || row.d0 || "";
    return r1 >= f && r0 <= t;
  }

  function buildPayload() {
    var set = rawSet();
    var bk = state.breakdown;
    var selected = ensureSelection(bk);
    var q = searchText(bk);
    if (q) {
      var visibleMap = {};
      visibleSeries(bk).forEach(function (name) { visibleMap[name] = true; });
      selected = selected.filter(function (name) { return !!visibleMap[name]; });
    }
    if (!set.rows.length || !selected.length) return { cols: [], rows: [], total: 0 };

    var cols = selected.slice();
    var rows = set.rows.filter(inRange).map(function (row) {
      var vals = {};
      if (bk === "country") {
        cols.forEach(function (c) { vals[c] = 0; });
        set.cols.forEach(function (col) {
          var c = parseCountry(col);
          if (vals.hasOwnProperty(c)) vals[c] += (row.vals[col] || 0);
        });
      } else {
        cols.forEach(function (c) { vals[c] = row.vals[c] || 0; });
      }
      return { key: row.key, d0: row.d0, d1: row.d1, vals: vals };
    });

    // Drop series with no volume in the visible window (declutters automatically).
    cols = cols.filter(function (col) {
      return rows.some(function (row) { return (row.vals[col] || 0) > 0; });
    });
    var tot = {};
    cols.forEach(function (c) { tot[c] = rows.reduce(function (s, r) { return s + (r.vals[c] || 0); }, 0); });
    if (bk === "prefix") {
      cols.sort(function (a, b) { return tot[b] - tot[a]; });
    } else {
      // Country-grouped order — keeps the chart, chips, table and export aligned.
      var orderIdx = {};
      allSeries(bk).forEach(function (n, i) { orderIdx[n] = i; });
      cols.sort(function (a, b) {
        var ia = orderIdx[a] == null ? 1e9 : orderIdx[a];
        var ib = orderIdx[b] == null ? 1e9 : orderIdx[b];
        return ia - ib;
      });
    }
    var total = cols.reduce(function (s, c) { return s + tot[c]; }, 0);
    return { cols: cols, rows: rows, total: total };
  }

  // ── controls ───────────────────────────────────────────────────────────────
  function renderCtl(host, items, current, action) {
    if (!host) return;
    host.innerHTML = items.map(function (it) {
      return '<button class="kpi-trend-btn' + (it.id === current ? " is-active" : "") +
        '" data-trend-action="' + action + '" data-value="' + esc(it.id) + '">' +
        esc(it.label) + '</button>';
    }).join("");
  }
  function renderControls() {
    renderCtl(document.getElementById("kpi-trend-breakdown"), BREAKDOWNS, state.breakdown, "set-breakdown");
    renderCtl(document.getElementById("kpi-trend-view"), MODES, state.mode, "set-mode");
  }
  function renderTitle() {
    var title = document.getElementById("kpi-trend-title");
    if (!title) return;
    var modeLabel = state.mode === "day" ? "Daily" : "Weekly";
    var bk = (BREAKDOWNS.filter(function (b) { return b.id === state.breakdown; })[0] || {}).label || "";
    title.textContent = modeLabel + " trend · " + bk;
  }

  // ── Inspected period range filter ───────────────────────────────────────────
  function rangePresets() {
    var b = dayBounds();
    if (!b) return [];
    var list = [{ id: "all", label: "Full range", from: null, to: null, cat: "all" }];
    [
      { id: "d7", label: "Last 1 week", from: isoShift(b.max, -6), to: b.max, cat: "last" },
      { id: "d14", label: "Last 2 weeks", from: isoShift(b.max, -13), to: b.max, cat: "last" },
      { id: "d30", label: "Last 30 days", from: isoShift(b.max, -29), to: b.max, cat: "last" }
    ].forEach(function (p) {
      if (rangeHasEveryDay(p.from, p.to)) list.push(p);
    });
    var wd = data && data.week && data.week.lmp;
    if (wd && wd.rows) {
      wd.rows.forEach(function (r) {
        if (rangeHasEveryDay(r.d0, r.d1)) {
          list.push({ id: "wk-" + r.key, label: r.key, from: r.d0, to: r.d1, cat: "week" });
        }
      });
    }
    return list;
  }
  function presetActive(p) {
    return (state.range.from || null) === (p.from || null) &&
           (state.range.to || null) === (p.to || null);
  }
  function activeRangeLabel() {
    var presets = rangePresets();
    for (var i = 0; i < presets.length; i++) {
      if (presetActive(presets[i])) return presets[i].label;
    }
    if (state.range.from || state.range.to) {
      return fmtDayLabel(state.range.from || "") + " - " + fmtDayLabel(state.range.to || "");
    }
    return "Full range";
  }
  function renderRange() {
    var host = document.getElementById("kpi-trend-range");
    if (!host) return;
    var b = dayBounds();
    if (!b) { host.innerHTML = ""; return; }
    var presets = rangePresets();
    var groups = { all: [], last: [], week: [] };
    presets.forEach(function (p) { (groups[p.cat] || (groups[p.cat] = [])).push(p); });
    function chipFor(p) {
      return '<button class="kpi-trend-rchip is-cat-' + (p.cat || "all") + (presetActive(p) ? " is-on" : "") +
        '" data-trend-action="range-preset" data-value="' + esc(p.id) + '">' + esc(p.label) + '</button>';
    }
    var chips = ["all", "last", "week"].filter(function (c) { return groups[c] && groups[c].length; })
      .map(function (c) { return '<div class="kpi-trend-rgroup is-' + c + '">' + groups[c].map(chipFor).join("") + '</div>'; })
      .join('<span class="kpi-trend-rsep" aria-hidden="true"></span>');
    host.innerHTML =
      '<div class="kpi-trend-range-label">Inspected period</div>' +
      '<div class="kpi-trend-range-row">' +
        '<div class="kpi-trend-rchips">' + chips + '</div>' +
        '<div class="kpi-trend-dates">' +
          '<input class="kpi-trend-date" type="date" data-trend-action="range-from" ' +
            'min="' + b.min + '" max="' + b.max + '" value="' + (state.range.from || "") + '">' +
          '<span class="kpi-trend-date-sep">–</span>' +
          '<input class="kpi-trend-date" type="date" data-trend-action="range-to" ' +
            'min="' + b.min + '" max="' + b.max + '" value="' + (state.range.to || "") + '">' +
        '</div>' +
      '</div>';
  }

  // ── series chip filter ──────────────────────────────────────────────────────
  function renderFilter() {
    var host = document.getElementById("kpi-trend-filter");
    if (!host) return;
    var bk = state.breakdown;
    var all = allSeries(bk);
    var selected = ensureSelection(bk);
    var colors = colorMap(bk);
    var withSearch = all.length > 10;
    var dispName = function (name) { return displayNameFor(bk, name); };
    var visible = visibleSeries(bk);

    var head =
      '<div class="kpi-trend-filter-head">' +
        '<div class="kpi-trend-filter-meta">' + visible.filter(function (name) { return selected.indexOf(name) >= 0; }).length +
          ' / ' + all.length + ' series</div>' +
        '<div class="kpi-trend-filter-actions">' +
          '<button class="kpi-trend-mini" data-trend-action="select-all">All</button>' +
          '<button class="kpi-trend-mini" data-trend-action="clear-all">None</button>' +
        '</div>' +
      '</div>';
    var q = state.search[bk] || "";
    var searchHtml = withSearch
      ? '<div class="kpi-trend-search-wrap' + (q ? " has-value" : "") + '">' +
          '<input class="kpi-trend-search" type="text" placeholder="Search..." value="' +
            esc(q) + '" data-trend-action="search">' +
          '<button class="kpi-trend-search-clear" type="button" data-trend-action="search-clear" ' +
            'tabindex="-1" aria-label="Clear search" title="Clear search"></button>' +
        '</div>'
      : '';
    var chips = visible.length
      ? visible.map(function (name) {
          var on = selected.indexOf(name) >= 0;
          return '<button class="kpi-trend-chip' + (on ? " is-on" : "") +
            '" data-trend-action="toggle" data-value="' + esc(name) + '" style="--chip:' +
            (colors[name] || "#94a3b8") + '"><span class="kpi-trend-chip-dot"></span>' +
            '<span class="kpi-trend-chip-l">' + esc(dispName(name)) + '</span></button>';
        }).join("")
      : '<div class="kpi-trend-empty-chip">No matches</div>';
    host.innerHTML = head + searchHtml +
      '<div class="kpi-trend-chipslider">' +
        '<button type="button" class="kpi-trend-slide-btn is-prev" data-trend-action="chip-scroll" data-dir="-1" tabindex="-1" aria-label="Scroll series left">‹</button>' +
        '<div class="kpi-trend-chiprow">' + chips + '</div>' +
        '<button type="button" class="kpi-trend-slide-btn is-next" data-trend-action="chip-scroll" data-dir="1" tabindex="-1" aria-label="Scroll series right">›</button>' +
      '</div>';
  }

  function buildExportPayload(payload) {
    var colors = colorMap(state.breakdown);
    var dispName = function (n) { return state.breakdown === "country" ? countryLabel(n) : n; };
    var modeLabel = state.mode === "day" ? "Daily" : "Weekly";
    var bk = (BREAKDOWNS.filter(function (b) { return b.id === state.breakdown; })[0] || {}).label || state.breakdown;
    var columns = payload.cols.map(function (col) {
      return { id: col, label: dispName(col), color: colors[col] || "#94a3b8" };
    });
    var rows = payload.rows.map(function (row) {
      var vals = {};
      var total = 0;
      payload.cols.forEach(function (col) {
        var v = Math.round(asNum(row.vals[col] || 0));
        vals[col] = v;
        total += v;
      });
      return { key: row.key, d0: row.d0 || "", d1: row.d1 || "", vals: vals, total: total };
    });
    return {
      title: "Inbound trend",
      mode: state.mode,
      modeLabel: modeLabel,
      breakdown: state.breakdown,
      breakdownLabel: bk,
      rangeLabel: activeRangeLabel(),
      unit: "kg",
      generatedAt: localExportTimestamp(),
      columns: columns,
      rows: rows,
      total: Math.round(asNum(payload.total || 0))
    };
  }

  function localExportTimestamp() {
    var d = new Date();
    var pad = function (n) { return String(n).padStart(2, "0"); };
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
      " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  function renderTable(payload) {
    var host = document.getElementById("kpi-trend-table");
    if (!host) return;
    if (!payload.cols.length || !payload.rows.length) {
      lastExportPayload = null;
      host.innerHTML = "";
      return;
    }
    var exportPayload = buildExportPayload(payload);
    lastExportPayload = exportPayload;
    var firstHead = state.mode === "day" ? "Day" : "Week";
    var rangeText = exportPayload.rangeLabel + " · " + exportPayload.modeLabel + " · " + exportPayload.breakdownLabel;
    var rowCount = exportPayload.rows.length;
    var colCount = exportPayload.columns.length;
    var headerCells = exportPayload.columns.map(function (col) {
      return '<th class="kpi-trend-table-series" style="--series:' + esc(col.color) + '">' +
        '<span class="kpi-trend-table-dot"></span><span>' + esc(col.label) + '</span></th>';
    }).join("");
    // Largest row total → drives the in-cell data bars (Excel-style), so periods
    // can be scanned for magnitude at a glance.
    var maxRowTotal = exportPayload.rows.reduce(function (m, row) {
      return Math.max(m, asNum(row.total));
    }, 0) || 1;
    var bodyRows = exportPayload.rows.map(function (row) {
      // The row's peak series — highlighted so the dominant carrier per period pops.
      var peak = 0;
      exportPayload.columns.forEach(function (col) {
        var v = asNum(row.vals[col.id] || 0);
        if (v > peak) peak = v;
      });
      var cells = exportPayload.columns.map(function (col) {
        var v = asNum(row.vals[col.id] || 0);
        if (!v) return '<td class="num"><span class="muted">–</span></td>';
        var isPeak = peak > 0 && v === peak;
        return '<td class="num' + (isPeak ? " is-peak" : "") + '"' +
          (isPeak ? ' style="--series:' + esc(col.color) + '"' : "") + '>' + esc(fmtKg(v)) + '</td>';
      }).join("");
      var pct = Math.max(2, Math.round((asNum(row.total) / maxRowTotal) * 100));
      var dateLbl = row.d0 === row.d1 ? fmtDayLabel(row.d0) : (fmtDayLabel(row.d0) + " - " + fmtDayLabel(row.d1));
      var dateMeta = state.mode !== "day" && dateLbl && dateLbl !== row.key
        ? '<span class="date-range">' + esc(dateLbl) + '</span>'
        : "";
      return '<tr><th scope="row"><span class="kpi-trend-row-key">' + esc(row.key) + '</span>' + dateMeta + '</th>' +
        cells +
        '<td class="num total"><span class="kpi-trend-total-bar" style="--p:' + pct + '%"></span>' +
        '<span class="kpi-trend-total-val">' + esc(fmtKg(row.total)) + '</span></td></tr>';
    }).join("");
    var colTotals = exportPayload.columns.map(function (col) {
      return exportPayload.rows.reduce(function (s, row) { return s + asNum(row.vals[col.id] || 0); }, 0);
    });
    // No peak highlight on the total row (per request): it's a sum, not a per-period
    // comparison, so the coloured "biggest column" stripe doesn't belong here.
    var totals = exportPayload.columns.map(function (col, i) {
      return '<td class="num">' + esc(fmtKg(colTotals[i])) + '</td>';
    }).join("");
    host.innerHTML =
      '<div class="kpi-trend-table-drawer' + (state.tableOpen ? " is-open" : "") + '">' +
      '<div class="kpi-trend-data-head">' +
        '<button class="kpi-trend-table-toggle" data-trend-action="toggle-table" type="button" aria-expanded="' +
          (state.tableOpen ? "true" : "false") + '">' +
          '<span class="kpi-trend-toggle-icon"></span>' +
          '<span><span class="kpi-trend-data-kicker">Table · current view</span>' +
          '<strong>' + esc(rangeText) + '</strong></span>' +
        '</button>' +
        '<div class="kpi-trend-table-actions">' +
          '<span class="kpi-trend-table-count">' + esc(rowCount + " rows · " + colCount + " columns") + '</span>' +
          '<button class="kpi-trend-export" data-trend-action="export-xlsx" type="button">XLSX export</button>' +
        '</div>' +
      '</div>' +
      '<div class="kpi-trend-table-wrap' + (_tableSwap && state.tableOpen ? " kpi-trend-table-swap" : "") +
        '"><table class="kpi-trend-data-table">' +
        '<thead><tr><th>' + esc(firstHead) + '</th>' + headerCells + '<th>Total</th></tr></thead>' +
        '<tbody>' + bodyRows + '</tbody>' +
        '<tfoot><tr><th>Total</th>' + totals +
        '<td class="num total">' + esc(fmtKg(exportPayload.total)) + '</td></tr></tfoot>' +
      '</table></div>' +
      '</div>';
    _tableSwap = false;
  }

  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename || "kpi_trend.xlsx";
    document.body.appendChild(a);
    a.click();
    setTimeout(function () {
      URL.revokeObjectURL(url);
      a.remove();
    }, 0);
  }

  function filenameFromDisposition(header) {
    var fallback = "kpi_trend.xlsx";
    if (!header) return fallback;
    var utf = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf && utf[1]) {
      try { return decodeURIComponent(utf[1]); } catch (e) { return utf[1]; }
    }
    var plain = header.match(/filename="?([^";]+)"?/i);
    return plain && plain[1] ? plain[1] : fallback;
  }

  function exportXlsx(btn) {
    if (!lastExportPayload || !lastExportPayload.rows.length) return;
    var original = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-loading");
      btn.textContent = "Export...";
    }
    fetch("/kpi-trend-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastExportPayload)
    }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (txt) {
          var msg = "Export failed";
          try {
            var parsed = JSON.parse(txt);
            if (parsed && parsed.error) msg = parsed.error;
          } catch (e) {
            if (txt) msg = txt.slice(0, 240);
          }
          throw new Error(msg);
        });
      }
      var filename = filenameFromDisposition(res.headers.get("Content-Disposition"));
      return res.blob().then(function (blob) { return { blob: blob, filename: filename }; });
    }).then(function (out) {
      downloadBlob(out.blob, out.filename);
      if (btn) {
        btn.classList.remove("is-loading");
        btn.classList.add("is-done");
        btn.textContent = "Letöltve";
        setTimeout(function () {
          btn.classList.remove("is-done");
          btn.disabled = false;
          btn.textContent = original || "XLSX export";
        }, 900);
      }
    }).catch(function (err) {
      try { console.warn("KPI trend XLSX export failed:", err && err.message ? err.message : err); } catch (e) {}
      if (btn) {
        btn.classList.remove("is-loading");
        btn.classList.add("is-error");
        btn.title = err && err.message ? err.message : "XLSX export hiba";
        btn.textContent = "Hiba";
        setTimeout(function () {
          btn.classList.remove("is-error");
          btn.disabled = false;
          btn.title = "";
          btn.textContent = original || "XLSX export";
        }, 1200);
      }
    });
  }

  // ── SVG geometry ───────────────────────────────────────────────────────────
  function smoothPath(pts) {
    if (!pts.length) return "";
    if (pts.length === 1) return "M" + pts[0].x.toFixed(2) + "," + pts[0].y.toFixed(2);
    var d = "M" + pts[0].x.toFixed(2) + "," + pts[0].y.toFixed(2);
    var t = 0.16;
    var clamp = function (y) { return Math.max(PAD.t, Math.min(BASE_Y, y)); };
    for (var i = 0; i < pts.length - 1; i++) {
      var p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
      var c1x = p1.x + (p2.x - p0.x) * t, c1y = clamp(p1.y + (p2.y - p0.y) * t);
      var c2x = p2.x - (p3.x - p1.x) * t, c2y = clamp(p2.y - (p3.y - p1.y) * t);
      d += "C" + c1x.toFixed(2) + "," + c1y.toFixed(2) + " " +
        c2x.toFixed(2) + "," + c2y.toFixed(2) + " " + p2.x.toFixed(2) + "," + p2.y.toFixed(2);
    }
    return d;
  }
  function xAt(i, n) { return PAD.l + (n <= 1 ? IW / 2 : (i / (n - 1)) * IW); }

  function captureExitSeries(host, removed) {
    if (!host || !removed || !removed.length) return [];
    var gone = {};
    removed.forEach(function (name) { gone[name] = true; });
    var clones = [];
    host.querySelectorAll(".kpi-trend-series[data-col]").forEach(function (g) {
      if (gone[g.getAttribute("data-col")]) clones.push(g.cloneNode(true));
    });
    return clones;
  }

  function playExitSeries(host, clones) {
    if (!clones || !clones.length || reduced()) return;
    var svg = host.querySelector(".kpi-trend-svg");
    if (!svg) return;
    clones.forEach(function (g) {
      g.classList.add("kpi-trend-exit");
      g.classList.remove("is-entering", "is-done");
      svg.appendChild(g);
      g.querySelectorAll(".kpi-trend-dot").forEach(function (dot) {
        dot.style.opacity = "0";
      });
      var paths = g.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow");
      paths.forEach(function (el) {
        var dashLen = dashLength(el);
        if (!dashLen) return;
        el.style.transition = "none";
        el.style.strokeDasharray = dashLen + " " + dashLen;
        el.style.strokeDashoffset = "0";
      });
      void g.getBoundingClientRect();
      requestAnimationFrame(function () {
        paths.forEach(function (el) {
          el.style.transition = "stroke-dashoffset " + FILTER_DRAW_MS + "ms cubic-bezier(0.55, 0.02, 0.58, 0.92)";
          var parts = String(el.style.strokeDasharray || "").split(" ");
          el.style.strokeDashoffset = parts[0] || "9999";
        });
      });
      setTimeout(function () { if (g.parentNode) g.parentNode.removeChild(g); }, FILTER_DRAW_MS + 140);
    });
  }

  function renderChart(payload, effect, reason) {
    var host = document.getElementById("kpi-trend-chart");
    if (!host) return;
    if (!payload.cols.length || !payload.rows.length) {
      host.innerHTML = '<div class="kpi-trend-chart-empty">' +
        (data ? "No data for the selected filters" : "Loading trend...") + '</div>';
      return;
    }
    var exitClones = effect && effect.type === "filter" ? captureExitSeries(host, effect.removed) : [];
    var entering = {};
    if (effect && effect.type === "filter" && effect.added) {
      effect.added.forEach(function (name) { entering[name] = true; });
    }
    var colors = colorMap(state.breakdown);
    var n = payload.rows.length;
    var manyLines = payload.cols.length > 9;
    var showDots = n <= DOTS_MAX_POINTS;
    var maxV = 0;
    payload.rows.forEach(function (r) {
      payload.cols.forEach(function (c) { if ((r.vals[c] || 0) > maxV) maxV = r.vals[c] || 0; });
    });
    maxV = tightMax(maxV);
    lastRenderMax = maxV;

    var series = payload.cols.map(function (col) {
      var pts = [];
      var byIndex = {};
      payload.rows.forEach(function (row, i) {
        var raw = row.vals[col];
        var v = asNum(raw || 0);
        if (!raw || v <= 0) return;
        var p = { rowIndex: i, x: xAt(i, n), y: PAD.t + IH * (1 - v / maxV), v: v };
        pts.push(p);
        byIndex[i] = p;
      });
      var total = pts.reduce(function (s, p) { return s + p.v; }, 0);
      var peak = pts.reduce(function (m, p) { return Math.max(m, p.v); }, 0);
      return { col: col, color: colors[col] || "#94a3b8", pts: pts, byIndex: byIndex, total: total, peak: peak };
    }).filter(function (s) { return s.pts.length; });

    var gridSvg = "";
    for (var g = 0; g <= 4; g++) {
      var gy = PAD.t + IH * g / 4;
      gridSvg += '<line class="kpi-trend-grid' + (g === 4 ? " base" : "") + '" x1="' + PAD.l +
        '" y1="' + gy.toFixed(2) + '" x2="' + (SVG_W - PAD.r) + '" y2="' + gy.toFixed(2) + '"/>';
      if (g < 4) {
        gridSvg += '<text class="kpi-trend-ylabel" x="' + (PAD.l - 9) + '" y="' + (gy + 4).toFixed(2) +
          '" text-anchor="end">' + esc(fmtCompact(maxV * (4 - g) / 4)) + '</text>';
      }
    }
    var step = Math.max(1, Math.ceil(n / 9));
    var xSvg = "";
    payload.rows.forEach(function (row, i) {
      if (i % step !== 0 && i !== n - 1) return;
      xSvg += '<text class="kpi-trend-xlabel" x="' + xAt(i, n).toFixed(2) + '" y="' + (BASE_Y + 19) +
        '" text-anchor="middle">' + esc(row.key) + '</text>';
    });

    var clipId = "kpiTrendReveal" + (++trendClipSeq);
    var clipWidth = SVG_W - PAD.l;
    var defs = '<clipPath id="' + clipId + '"><rect class="kpi-trend-reveal-clip" x="' +
      PAD.l + '" y="0" width="' + clipWidth + '" height="' + SVG_H + '"/></clipPath>';
    var areaSvg = "";
    if (series.length === 1) {
      var s0 = series[0];
      defs += '<linearGradient id="kpiTrendArea" x1="0" y1="0" x2="0" y2="1">' +
        '<stop offset="0%" stop-color="' + s0.color + '" stop-opacity="0.28"/>' +
        '<stop offset="72%" stop-color="' + s0.color + '" stop-opacity="0.05"/>' +
        '<stop offset="100%" stop-color="' + s0.color + '" stop-opacity="0"/></linearGradient>';
      var ap = smoothPath(s0.pts) + " L" + s0.pts[s0.pts.length - 1].x.toFixed(2) + "," + BASE_Y.toFixed(2) +
        " L" + s0.pts[0].x.toFixed(2) + "," + BASE_Y.toFixed(2) + " Z";
      areaSvg = '<path class="kpi-trend-area" d="' + ap + '" fill="url(#kpiTrendArea)"/>';
    }

    var seriesSvg = series.map(function (s, si) {
      var d = smoothPath(s.pts);
      var dots = showDots ? s.pts.map(function (p) {
        return '<circle class="kpi-trend-dot" cx="' + p.x.toFixed(2) + '" cy="' + p.y.toFixed(2) +
          '" r="2.6" stroke="' + s.color + '"/>';
      }).join("") : "";
      return '<g class="kpi-trend-series' + (entering[s.col] ? " is-entering" : "") +
        '" data-si="' + si + '" data-col="' + esc(s.col) + '" data-total="' + s.total.toFixed(2) +
        '" data-peak="' + s.peak.toFixed(2) + '">' +
        '<path class="kpi-trend-line-glow' + (manyLines ? " dim" : "") + '" d="' + d + '" stroke="' + s.color + '"/>' +
        '<path class="kpi-trend-line" d="' + d + '" stroke="' + s.color + '"/>' + dots + '</g>';
    }).join("");

    host.innerHTML =
      '<div class="kpi-trend-stage">' +
        '<svg class="kpi-trend-svg" viewBox="0 0 ' + SVG_W + ' ' + SVG_H +
          '" role="img" preserveAspectRatio="xMidYMid meet">' +
          '<defs>' + defs + '</defs>' +
          gridSvg + xSvg +
          '<g class="kpi-trend-scale" clip-path="url(#' + clipId + ')">' + areaSvg + seriesSvg + '</g>' +
          '<line class="kpi-trend-cross" x1="0" y1="' + PAD.t + '" x2="0" y2="' + BASE_Y + '" style="opacity:0"/>' +
          '<g class="kpi-trend-hover-dots"></g>' +
        '</svg>' +
        '<div class="kpi-trend-tip" style="opacity:0"></div>' +
      '</div>';

    // ── animation dispatch ──────────────────────────────────────────────────
    // filter  → only the entering line draws (add/select-all)
    // data    → opening reveal, but only when scrolled into view AND the data token
    //           is new (re-delivery of the same data never replays — fixes the
    //           "opening animation sometimes restarts" bug)
    // (else)  → user view change (mode/breakdown/range) always draws on
    var visible = sectionObserver ? sectionVisible : true;
    if (reduced()) {
      solidifyLines(host);
      if (reason === "data") revealedToken = currentDataToken;
    } else if (effect && effect.type === "filter") {
      animateFilterLines(host);
    } else if (reason === "data") {
      if (currentDataToken === revealedToken) {
        solidifyLines(host);                       // same data re-delivered → no replay
      } else if (visible) {
        drawOnLines(host);
        revealedToken = currentDataToken;
        pendingRevealToken = null;
      } else {
        hideLines(host);
        pendingRevealToken = currentDataToken;     // play it once it scrolls into view
      }
    } else if (reason === "search") {
      solidifyLines(host);
    } else {
      drawOnLines(host);
    }
    if (pendingMorphRatio) {
      morphScale(host, pendingMorphRatio, pendingMorphDelay || 0);
      pendingMorphRatio = 0;
      pendingMorphDelay = 0;
    }
    playExitSeries(host, exitClones);
    wireHover(host, series, payload.rows);
    applyFocus();
  }

  // Snap every line to its final (solid) state — used for the no-animation paths.
  function trendRevealRect(host) {
    return host && host.querySelector ? host.querySelector(".kpi-trend-reveal-clip") : null;
  }

  function setTrendReveal(host, progress) {
    var rect = trendRevealRect(host);
    if (!rect) return;
    var p = Math.max(0, Math.min(1, Number(progress) || 0));
    rect.setAttribute("width", ((SVG_W - PAD.l) * p).toFixed(2));
  }

  function cancelTrendReveal(host) {
    if (!host) return;
    if (host.__kpiTrendRevealRaf) {
      cancelAnimationFrame(host.__kpiTrendRevealRaf);
      host.__kpiTrendRevealRaf = 0;
    }
  }

  function animateTrendReveal(host, duration, done) {
    cancelTrendReveal(host);
    setTrendReveal(host, 0);
    var start = 0;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      var eased = 1 - Math.pow(1 - p, 3);
      setTrendReveal(host, eased);
      if (p < 1) {
        host.__kpiTrendRevealRaf = requestAnimationFrame(step);
      } else {
        host.__kpiTrendRevealRaf = 0;
        setTrendReveal(host, 1);
        if (done) done();
      }
    }
    host.__kpiTrendRevealRaf = requestAnimationFrame(step);
  }

  function solidifyLines(host) {
    var stage = host.querySelector(".kpi-trend-stage");
    host.__kpiTrendDrawGen = (host.__kpiTrendDrawGen || 0) + 1;  // invalidate any in-flight draw timer
    cancelTrendReveal(host);
    setTrendReveal(host, 1);
    host.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow").forEach(function (el) {
      el.style.transition = "";
      el.style.strokeDasharray = "";
      el.style.strokeDashoffset = "0";
    });
    if (stage) stage.classList.remove("is-opening-lines");
    if (stage) stage.classList.add("is-done");
  }

  function trendGroupPaths(g) {
    return g ? Array.from(g.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow")) : [];
  }

  function primaryTrendPath(g) {
    return g ? (g.querySelector(".kpi-trend-line") || g.querySelector(".kpi-trend-line-glow")) : null;
  }

  function prepareTrendGroupDash(g) {
    var paths = trendGroupPaths(g);
    var dashLen = dashLength(primaryTrendPath(g));
    if (!dashLen) return null;
    paths.forEach(function (el) {
      el.style.transition = "none";
      el.style.strokeDasharray = dashLen + " " + dashLen;
      el.style.strokeDashoffset = String(dashLen);
    });
    return { group: g, paths: paths, dashLen: dashLen };
  }

  function revealRankedGroups(groups) {
    return Array.from(groups || []).map(function (g, i) {
      return {
        group: g,
        index: i,
        total: Number(g.getAttribute("data-total")) || 0,
        peak: Number(g.getAttribute("data-peak")) || 0
      };
    }).sort(function (a, b) {
      if (b.peak !== a.peak) return b.peak - a.peak;
      if (b.total !== a.total) return b.total - a.total;
      return a.index - b.index;
    });
  }

  function openingDelay(rank, count) {
    if (count <= 1) return 0;
    var spread = Math.min(MAX_OPENING_SPREAD, Math.max(900, (count - 1) * STAGGER_MS));
    var t = rank / (count - 1);
    return Math.round(Math.pow(t, 1.18) * spread);
  }

  // Park the lines in their pre-draw (hidden) state so a later drawOnLines reveals
  // them cleanly — used while the section is still scrolled out of view.
  function hideLines(host) {
    var stage = host.querySelector(".kpi-trend-stage");
    host.__kpiTrendDrawGen = (host.__kpiTrendDrawGen || 0) + 1;  // invalidate any in-flight draw timer
    if (stage) {
      stage.classList.remove("is-done");
      stage.classList.add("is-opening-lines");
    }
    cancelTrendReveal(host);
    setTrendReveal(host, 0);
    host.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow").forEach(function (el) {
      el.style.transition = "";
      el.style.strokeDasharray = "";
      el.style.strokeDashoffset = "0";
    });
  }

  // Only the freshly-added series draws itself in; the rest stay put. Existing
  // lines are left completely untouched (already solid, no dash) so nothing else
  // can be stranded mid-dash, and each entering line snaps to a clean solid stroke
  // when its draw-on ends — the resting state never depends on dash length, which
  // is what used to leave a line short of its last point.
  function animateFilterLines(host) {
    var stage = host.querySelector(".kpi-trend-stage");
    if (stage) stage.classList.add("is-done");
    var groups = host.querySelectorAll(".kpi-trend-series");
    var prepared = Array.from(groups).filter(function (g) {
      return g.classList.contains("is-entering");
    }).map(prepareTrendGroupDash).filter(Boolean);
    requestAnimationFrame(function () {
      prepared.forEach(function (item, i) {
        var g = item.group;
        var delay = Math.min(i * 36, 260);
        item.paths.forEach(function (el) {
          el.style.transition = "stroke-dashoffset " + FILTER_DRAW_MS + "ms cubic-bezier(0.22, 1, 0.36, 1) " + delay + "ms";
          el.style.strokeDashoffset = "0";
        });
        setTimeout(function () {
          g.classList.add("is-done");
          item.paths.forEach(function (el) {
            el.style.transition = "";
            el.style.strokeDasharray = "";
            el.style.strokeDashoffset = "0";
          });
        }, delay + FILTER_DRAW_MS + 80);
      });
    });
  }

  // The full staggered opening reveal: every line draws itself on, each series
  // starting a little after the previous (STAGGER_MS) so they come in one-by-one.
  //
  // Snap-proof contract (do NOT "tidy" this back to clearing the dash mid-run):
  //   1. The dash is generous (dashLength) so the whole stroke is covered — at
  //      offset 0 the line is fully solid and stays solid; we never clear the dash
  //      while a transition is in flight (clearing mid-run = the tail popping in).
  //   2. A per-draw generation token guards the finish timer, so a stale timer from
  //      an earlier reveal can't cut a newer one short ("doesn't go all the way").
  //   3. The transition runs to completion on its own; the timer only flips the
  //      stage to is-done (area/dots fade-in) — it does not yank the stroke solid.
  function drawOnLines(host) {
    var stage = host.querySelector(".kpi-trend-stage");
    var groups = host.querySelectorAll(".kpi-trend-series");
    var gen = (host.__kpiTrendDrawGen = (host.__kpiTrendDrawGen || 0) + 1);
    var maxDelay = 0;
    if (stage) {
      stage.classList.remove("is-done");
      stage.classList.add("is-opening-lines");
    }
    cancelTrendReveal(host);
    setTrendReveal(host, 0);
    var prepared = revealRankedGroups(groups).map(function (item) {
      var dash = prepareTrendGroupDash(item.group);
      if (!dash) return null;
      dash.rank = item;
      return dash;
    }).filter(Boolean);
    requestAnimationFrame(function () {
      if (host.__kpiTrendDrawGen !== gen) return;   // superseded before it began
      setTrendReveal(host, 1);
      // Distribute the stagger EVENLY across every series (0 → spread) instead of a
      // fixed per-line step with a hard cap — that cap made all the low-volume lines
      // past ~index 11 start together. Now each line, big or small, gets its own
      // distinct slot so they come in one by one. Spread grows with the line count
      // but is capped so the whole reveal stays ~1.5s + draw.
      var n = prepared.length;
      prepared.forEach(function (item, i) {
        var delay = openingDelay(i, n);
        maxDelay = Math.max(maxDelay, delay);
        item.paths.forEach(function (el) {
          if (!el.style.strokeDasharray) return;
          el.style.transition = "stroke-dashoffset " + DRAW_MS + "ms cubic-bezier(0.22, 1, 0.36, 1) " + delay + "ms";
          el.style.strokeDashoffset = "0";
        });
      });
      // Only reveal area/dots once the draw has fully finished; never clears the
      // dash (the generous dash keeps every line solid on its own).
      setTimeout(function () {
        if (host.__kpiTrendDrawGen !== gen) return;  // a newer draw owns the host now
        host.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow").forEach(function (el) {
          el.style.strokeDashoffset = "0";
        });
        if (stage) {
          stage.classList.remove("is-opening-lines");
          stage.classList.add("is-done");
        }
      }, maxDelay + DRAW_MS + 120);
    });
  }

  // Smoothly morph the just-rendered (new-scale) lines from the old Y-scale. The
  // scale <g> is rendered at the final positions, started compressed/expanded to
  // the previous scale (scaleY r about the baseline), then eased to identity —
  // GPU-cheap, so the remaining lines glide to the recalculated scale.
  function morphScale(host, r, delay) {
    var g = host.querySelector(".kpi-trend-scale");
    if (!g || !isFinite(r) || r <= 0) return;
    if (host.__kpiTrendMorphTimer) {
      clearTimeout(host.__kpiTrendMorphTimer);
      host.__kpiTrendMorphTimer = 0;
    }
    if (host.__kpiTrendMorphCleanup) {
      clearTimeout(host.__kpiTrendMorphCleanup);
      host.__kpiTrendMorphCleanup = 0;
    }
    g.style.transition = "none";
    g.style.transform = "scaleY(" + r + ")";
    void g.getBoundingClientRect();
    function run() {
      g.style.transition = "transform " + MORPH_MS + "ms cubic-bezier(0.22, 1, 0.36, 1)";
      g.style.transform = "scaleY(1)";
      host.__kpiTrendMorphCleanup = setTimeout(function () {
        if (g) {
          g.style.transition = "";
          g.style.transform = "";
        }
        host.__kpiTrendMorphCleanup = 0;
      }, MORPH_MS + 90);
    }
    delay = Math.max(0, Number(delay) || 0);
    if (delay) {
      host.__kpiTrendMorphTimer = setTimeout(function () {
        host.__kpiTrendMorphTimer = 0;
        requestAnimationFrame(run);
      }, delay);
    } else {
      requestAnimationFrame(run);
    }
  }

  // ── Ctrl+click focus (solo) — works from the chips and from the lines ────────
  function applyFocus() {
    var host = document.getElementById("kpi-trend-chart");
    var hasFocus = state.focus.length > 0;
    if (host) {
      host.classList.toggle("has-focus", hasFocus);
      host.querySelectorAll(".kpi-trend-series[data-col]").forEach(function (g) {
        var inFocus = state.focus.indexOf(g.getAttribute("data-col")) >= 0;
        g.classList.toggle("is-unfocused", hasFocus && !inFocus);
        g.classList.toggle("is-focused", hasFocus && inFocus);
      });
    }
    var fh = document.getElementById("kpi-trend-filter");
    if (fh) {
      fh.querySelectorAll(".kpi-trend-chip[data-value]").forEach(function (c) {
        c.classList.toggle("is-focused", hasFocus && state.focus.indexOf(c.getAttribute("data-value")) >= 0);
      });
    }
  }
  function toggleFocus(name) {
    if (!name) return;
    var sel = ensureSelection(state.breakdown);
    var fi = state.focus.indexOf(name);
    if (fi >= 0) state.focus.splice(fi, 1); else state.focus.push(name);
    if (state.focus.indexOf(name) >= 0 && sel.indexOf(name) < 0) {
      sel.push(name);   // focusing a hidden series → bring its line in first
      renderAll(false, { type: "filter", removed: [], added: [name] });
    } else {
      applyFocus();
    }
  }
  function clearFocus() {
    if (!state.focus.length) return;
    state.focus = [];
    applyFocus();
  }

  function playSectionOpening(section) {
    if (!section) return;
    section.classList.remove("kpi-reveal-pending");
    section.classList.add("is-revealed");
    section.classList.add("is-opening");
    clearTimeout(section.__kpiTrendOpeningTimer);
    section.__kpiTrendOpeningTimer = setTimeout(function () {
      section.classList.remove("is-opening");
    }, 1500);
  }

  function trendInView() {
    var s = document.getElementById("kpi-trend-section");
    if (!s) return false;
    var r = s.getBoundingClientRect();
    var vh = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!vh || r.width <= 0 || r.height <= 0) return false;
    // Half-visible gate: at least 50% of the section on screen, or (tall section)
    // its visible slice filling most of the viewport.
    var visible = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    return visible >= r.height * 0.5 || visible >= vh * 0.55;
  }
  function trendHalfVisible(entry) {
    if (!entry || !entry.isIntersecting) return false;
    if (entry.intersectionRatio >= 0.5) return true;
    var ir = entry.intersectionRect;
    var th = (entry.rootBounds && entry.rootBounds.height) ||
      window.innerHeight || document.documentElement.clientHeight || 0;
    return !!(th && ir && ir.height >= th * 0.55);
  }

  // Play the deferred opening reveal the moment the section is at least half visible.
  function setupSectionObserver() {
    if (sectionObserver || typeof IntersectionObserver === "undefined") return;
    var section = document.getElementById("kpi-trend-section");
    if (!section) return;
    section.classList.add("kpi-reveal-pending");
    sectionObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var half = trendHalfVisible(e);
        sectionVisible = half;
        // Play the control/opening entrance ONCE — re-entering the viewport on scroll
        // must not replay the button animations (only the gated line draw-on may run).
        if (half && !e.target.classList.contains("is-revealed")) playSectionOpening(e.target);
        if (half && pendingRevealToken && pendingRevealToken === currentDataToken) {
          var host = document.getElementById("kpi-trend-chart");
          if (host && host.querySelector(".kpi-trend-line")) {
            drawOnLines(host);
            revealedToken = currentDataToken;
            pendingRevealToken = null;
          }
        }
      });
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    sectionObserver.observe(section);
  }

  // Re-arm the trend's opening reveal when the KPI view is (re)entered, so the
  // draw-on plays on a plain page switch — not only after a data refresh.
  window.addEventListener("kpi-view-entered", function () {
    setTimeout(function () {
      if (!data) return;
      revealedToken = null;
      pendingRevealToken = null;
      var section = document.getElementById("kpi-trend-section");
      if (section) {
        section.classList.remove("is-revealed", "is-opening", "kpi-reveal-pending");
        if (trendInView()) { sectionVisible = true; playSectionOpening(section); }
        else { section.classList.add("kpi-reveal-pending"); }
      }
      renderAll(false, null, "data");
    }, 60);
  });

  function setHighlight(si) {
    var host = document.getElementById("kpi-trend-chart");
    if (!host) return;
    host.querySelectorAll(".kpi-trend-series").forEach(function (g) {
      if (si == null) { g.style.opacity = ""; g.style.filter = ""; return; }
      var on = String(si) === g.getAttribute("data-si");
      g.style.opacity = on ? "1" : "0.1";
      g.style.filter = on ? "" : "saturate(0.55)";
    });
  }

  function wireHover(host, series, rows) {
    var svg = host.querySelector(".kpi-trend-svg");
    var tip = host.querySelector(".kpi-trend-tip");
    var cross = host.querySelector(".kpi-trend-cross");
    var hoverDots = host.querySelector(".kpi-trend-hover-dots");
    if (!svg || !tip || !cross || !hoverDots) return;
    var n = rows.length;
    var dispName = function (nm) { return state.breakdown === "country" ? countryLabel(nm) : nm; };

    function idxAt(ev) {
      var r = svg.getBoundingClientRect();
      if (!r.width) return -1;
      var x = (ev.clientX - r.left) / r.width * SVG_W;
      var best = 0, bd = Infinity;
      for (var i = 0; i < n; i++) { var d = Math.abs(xAt(i, n) - x); if (d < bd) { bd = d; best = i; } }
      return best;
    }
    function show(idx) {
      var cx = xAt(idx, n);
      cross.setAttribute("x1", cx.toFixed(2));
      cross.setAttribute("x2", cx.toFixed(2));
      cross.style.opacity = "1";
      var focusOn = state.focus.length > 0;
      var items = series.map(function (s) {
        var p = s.byIndex && s.byIndex[idx];
        if (!p) return null;
        return { name: s.col, color: s.color, value: p.v, y: p.y };
      }).filter(function (it) {
        return it && it.value > 0 && (!focusOn || state.focus.indexOf(it.name) >= 0);
      }).sort(function (a, b) { return b.value - a.value; });
      hoverDots.innerHTML = items.map(function (it) {
        return '<circle cx="' + cx.toFixed(2) + '" cy="' + it.y.toFixed(2) +
          '" r="4" fill="#0b1322" stroke="' + it.color + '" stroke-width="2.2"/>';
      }).join("");
      var top = items.slice(0, 10);
      tip.innerHTML =
        '<div class="kpi-trend-tip-h">' + esc(rows[idx].key) + '</div>' +
        (top.length ? top.map(function (it) {
          return '<div class="kpi-trend-tip-row"><span class="kpi-trend-tip-l">' +
            '<i style="background:' + it.color + '"></i>' +
            '<span class="kpi-trend-tip-t">' + esc(dispName(it.name)) + '</span></span>' +
            '<b>' + esc(fmtKg(it.value)) + '</b></div>';
        }).join("") : '<div class="kpi-trend-tip-row"><span class="kpi-trend-tip-l">No data</span></div>');
      var leftPct = cx / SVG_W * 100;
      tip.style.left = leftPct + "%";
      tip.classList.toggle("flip", leftPct > 60);
      tip.style.opacity = "1";
    }
    function hide() { cross.style.opacity = "0"; tip.style.opacity = "0"; hoverDots.innerHTML = ""; }
    // Coalesce mousemoves to one paint per frame — without this every pixel of
    // cursor travel rebuilds the hover dots + tooltip (heavy with many series).
    var hoverRaf = 0, hoverX = 0;
    svg.addEventListener("mousemove", function (ev) {
      hoverX = ev.clientX;
      if (hoverRaf) return;
      hoverRaf = requestAnimationFrame(function () {
        hoverRaf = 0;
        var i = idxAt({ clientX: hoverX });
        if (i >= 0) show(i);
      });
    });
    svg.addEventListener("mouseleave", function () {
      if (hoverRaf) { cancelAnimationFrame(hoverRaf); hoverRaf = 0; }
      hide();
    });
    // Ctrl+click a line → solo/focus it (accumulates); a plain click clears focus.
    svg.addEventListener("click", function (ev) {
      if (ev.ctrlKey || ev.metaKey) {
        var rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        var idx = idxAt(ev);
        if (idx < 0) return;
        var cy = (ev.clientY - rect.top) / rect.height * SVG_H;
        var best = null, bd = Infinity;
        series.forEach(function (s) {
          var p = s.byIndex && s.byIndex[idx];
          if (!p) return;
          var dy = Math.abs(p.y - cy);
          if (dy < bd) { bd = dy; best = s; }
        });
        if (best) { toggleFocus(best.col); ev.preventDefault(); }
      } else if (state.focus.length) {
        clearFocus();
      }
    });
  }

  // ── orchestration ──────────────────────────────────────────────────────────
  function clampRange() {
    var b = dayBounds();
    if (!b) return;
    if (state.range.from && state.range.from < b.min) state.range.from = b.min;
    if (state.range.to && state.range.to > b.max) state.range.to = b.max;
    if (state.range.from && state.range.to && state.range.from > state.range.to) {
      var t = state.range.from; state.range.from = state.range.to; state.range.to = t;
    }
  }
  function renderAll(swap, effect, reason) {
    // Any full rebuild supersedes an in-flight series-retract (poll, mode swap…).
    if (removeTimer) { clearTimeout(removeTimer); removeTimer = null; }
    pendingRemoval = false;
    if (!MODES.some(function (m) { return m.id === state.mode; })) state.mode = MODES[0].id;
    if (!BREAKDOWNS.some(function (b) { return b.id === state.breakdown; })) state.breakdown = "lmp";
    renderControls();
    renderTitle();
    renderRange();
    renderFilter();
    var payload = buildPayload();
    var host = document.getElementById("kpi-trend-chart");
    if (host && swap && !reduced()) {
      host.classList.remove("is-swapping");
      void host.offsetWidth;
      host.classList.add("is-swapping");
    }
    _tableSwap = reason !== "data";   // user-driven change → animate the table content
    renderChart(payload, effect || null, reason);
    renderTable(payload);
  }

  function signature(trend) {
    if (!trend) return "";
    try {
      var parts = [];
      ["day", "week"].forEach(function (m) {
        ["lmp", "prefix"].forEach(function (d) {
          var s = trend[m] && trend[m][d];
          parts.push(m + d + (s ? s.cols.length + ":" + s.rows.length : "0"));
        });
      });
      return parts.join("|");
    } catch (e) { return String(Math.random()); }
  }

  function contentSignature(trend) {
    if (!trend) return "";
    try {
      var parts = [];
      ["day", "week"].forEach(function (m) {
        ["lmp", "prefix"].forEach(function (d) {
          var s = trend[m] && trend[m][d];
          if (!s) {
            parts.push(m + ":" + d + ":0");
            return;
          }
          var cols = Array.isArray(s.cols) ? s.cols : [];
          var rows = Array.isArray(s.rows) ? s.rows : [];
          parts.push(m + ":" + d + ":" + cols.join(","));
          rows.forEach(function (row) {
            var vals = row && row.vals ? row.vals : {};
            parts.push([
              row && row.key,
              row && row.d0,
              row && row.d1,
              cols.map(function (c) { return Math.round(asNum(vals[c] || 0)); }).join(",")
            ].join("="));
          });
        });
      });
      return parts.join("|");
    } catch (e) {
      return String(Date.now());
    }
  }

  // ── single-series removal: line retracts right→left, THEN the chart recalcs ──
  // The spec: deselecting a series should not snap the rest to a new scale while
  // its line is still on screen. So we (1) retract just that line in place on the
  // current chart, then (2) rebuild at the new scale — masked by the soft swap
  // only when the Y-axis actually shifts (otherwise nothing moves → seamless).
  var REMOVE_RETRACT_MS = 560;
  var removeTimer = null;
  var pendingRemoval = false;

  function payloadMax(payload) {
    var mv = 0;
    (payload.rows || []).forEach(function (r) {
      (payload.cols || []).forEach(function (c) {
        if ((r.vals[c] || 0) > mv) mv = r.vals[c] || 0;
      });
    });
    return tightMax(mv);
  }

  function queueScaleMorph(oldMax, newMax, delay) {
    if (reduced() || !MORPH_OK) return false;
    oldMax = Number(oldMax) || 0;
    newMax = Number(newMax) || 0;
    if (oldMax <= 0 || newMax <= 0) return false;
    var ratio = newMax / oldMax;
    if (Math.abs(ratio - 1) <= 0.015) return false;
    pendingMorphRatio = ratio;
    pendingMorphDelay = Math.max(0, Number(delay) || 0);
    return true;
  }

  function startSeriesRetract(col) {
    var host = document.getElementById("kpi-trend-chart");
    if (!host) return false;
    var target = null;
    host.querySelectorAll(".kpi-trend-series[data-col]").forEach(function (g) {
      if (g.getAttribute("data-col") === col) target = g;
    });
    if (!target) return false;
    target.classList.add("kpi-trend-exit");
    target.style.pointerEvents = "none";
    target.querySelectorAll(".kpi-trend-dot").forEach(function (d) { d.style.opacity = "0"; });
    var preparedExit = prepareTrendGroupDash(target);
    if (!preparedExit) return false;
    preparedExit.paths.forEach(function (el) {
      el.style.transition = "none";
      el.style.strokeDashoffset = "0";
    });
    requestAnimationFrame(function () {
      preparedExit.paths.forEach(function (el) {
        el.style.transition = "stroke-dashoffset " + REMOVE_RETRACT_MS + "ms cubic-bezier(0.55, 0.02, 0.58, 0.92)";
        el.style.strokeDashoffset = String(preparedExit.dashLen);
      });
    });
    return true;
    var animated = false;
    target.querySelectorAll(".kpi-trend-line, .kpi-trend-line-glow").forEach(function (el) {
      var dashLen = dashLength(el);
      if (!dashLen) return;
      el.style.transition = "none";
      el.style.strokeDasharray = dashLen + " " + dashLen;
      el.style.strokeDashoffset = "0";
      void el.getBoundingClientRect();
      el.style.transition = "stroke-dashoffset " + REMOVE_RETRACT_MS + "ms cubic-bezier(0.55, 0.02, 0.58, 0.92)";
      el.style.strokeDashoffset = String(dashLen);   // grows the offset → line peels back from the right
      animated = true;
    });
    return animated;
  }

  function finishRemoval() {
    if (removeTimer) { clearTimeout(removeTimer); removeTimer = null; }
    if (!pendingRemoval) return;
    pendingRemoval = false;
    var oldMax = lastRenderMax;
    var payload = buildPayload();
    var mv = payloadMax(payload);
    var r = oldMax ? mv / oldMax : 1;
    var scaleChanged = Math.abs(r - 1) > 0.015;
    if (queueScaleMorph(oldMax, mv, 0)) {
      renderAll(false, { type: "filter", removed: [], added: [] });
    } else {
      // No transform-box support → soft swap mask (or seamless when nothing moves).
      renderAll(!reduced() && scaleChanged, { type: "filter", removed: [], added: [] });
    }
  }

  function removeSeries(value) {
    var sel = ensureSelection(state.breakdown);
    var ix = sel.indexOf(value);
    if (ix < 0) return;
    sel.splice(ix, 1);
    renderFilter();                       // the chip flips off immediately
    if (reduced()) { renderAll(false, { type: "filter", removed: [], added: [] }); return; }
    if (!startSeriesRetract(value)) {     // line not found → just recalc
      renderAll(true, { type: "filter", removed: [], added: [] });
      return;
    }
    pendingRemoval = true;
    removeTimer = setTimeout(finishRemoval, REMOVE_RETRACT_MS + 40);
  }

  // ── events ───────────────────────────────────────────────────────────────
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest && ev.target.closest("[data-trend-action]");
    if (!btn) return;
    var action = btn.getAttribute("data-trend-action");
    if (action === "search" || action === "range-from" || action === "range-to") return;
    var value = btn.getAttribute("data-value") || "";
    // A second click mid-retract: settle the previous removal first so the DOM is
    // consistent before this action reads/rebuilds it.
    if (pendingRemoval) finishRemoval();
    var swap = true;
    var effect = null;
    if (action === "export-xlsx") {
      exportXlsx(btn);
      return;
    } else if (action === "search-clear") {
      // X next to the search → wipe it, redraw the full chip set + chart, keep focus.
      if (!state.search[state.breakdown]) return;
      state.search[state.breakdown] = "";
      renderAll(false, null, "search");
      var sf = document.querySelector(".kpi-trend-search");
      if (sf) { sf.focus(); try { sf.setSelectionRange(0, 0); } catch (e) {} }
      return;
    } else if (action === "toggle-table") {
      // Animate open/close in place (CSS handles the drawer transition).
      state.tableOpen = !state.tableOpen;
      var drawer = document.querySelector(".kpi-trend-table-drawer");
      if (drawer) {
        drawer.classList.toggle("is-open", state.tableOpen);
        var tgl = drawer.querySelector(".kpi-trend-table-toggle");
        if (tgl) tgl.setAttribute("aria-expanded", state.tableOpen ? "true" : "false");
      } else {
        renderTable(buildPayload());
      }
      return;
    } else if (action === "set-mode") {
      if (state.mode === value) return;
      state.mode = value; state.focus = [];
    } else if (action === "set-breakdown") {
      if (state.breakdown === value) return;
      state.breakdown = value; state.focus = [];
    } else if (action === "toggle") {
      if (ev.ctrlKey || ev.metaKey) { toggleFocus(value); return; }   // Ctrl+click = solo/focus
      if (state.focus.length) { clearFocus(); return; }               // plain click exits focus first
      var bk = state.breakdown, sel = ensureSelection(bk), ix = sel.indexOf(value);
      if (ix >= 0) {
        removeSeries(value);   // retract right→left, then recalc (own render path)
        return;
      }
      var oldMaxAdd = lastRenderMax;
      sel.push(value);
      queueScaleMorph(oldMaxAdd, payloadMax(buildPayload()), FILTER_DRAW_MS + 140);
      effect = { type: "filter", removed: [], added: [value] };
      swap = false;
    } else if (action === "select-all") {
      var currentAll = visibleSeries(state.breakdown);
      var beforeAll = ensureSelection(state.breakdown).slice();
      var oldMaxAll = lastRenderMax;
      state.selected[state.breakdown] = currentAll.slice();
      var addedAll = currentAll.filter(function (v) { return beforeAll.indexOf(v) < 0; });
      queueScaleMorph(oldMaxAll, payloadMax(buildPayload()),
        FILTER_DRAW_MS + Math.min(Math.max(0, addedAll.length - 1) * 36, 260) + 140);
      effect = {
        type: "filter",
        removed: [],
        added: addedAll
      };
      swap = false;
    } else if (action === "clear-all") {
      var beforeClear = ensureSelection(state.breakdown).slice();
      state.selected[state.breakdown] = [];
      effect = { type: "filter", removed: beforeClear, added: [] };
      swap = false;
    } else if (action === "range-preset") {
      var p = rangePresets().filter(function (x) { return x.id === value; })[0];
      if (p) {
        state.range = { from: p.from, to: p.to }; state.focus = [];
        if (p.id !== "all") state.mode = "day";
      }
    } else if (action === "chip-scroll") {
      var srow = btn.parentNode && btn.parentNode.querySelector(".kpi-trend-chiprow");
      if (srow) {
        var sdir = parseInt(btn.getAttribute("data-dir"), 10) || 1;
        srow.scrollBy({ left: sdir * Math.max(180, srow.clientWidth * 0.72), behavior: reduced() ? "auto" : "smooth" });
      }
      return;
    } else { return; }
    renderAll(swap, effect);
  });
  document.addEventListener("input", function (ev) {
    var inp = ev.target.closest && ev.target.closest("[data-trend-action='search']");
    if (!inp) return;
    var pos = inp.selectionStart == null ? inp.value.length : inp.selectionStart;
    state.search[state.breakdown] = inp.value || "";
    if (pendingRemoval) finishRemoval();
    renderAll(false, null, "search");
    var rf = document.querySelector(".kpi-trend-search");
    if (rf) {
      rf.focus();
      var safePos = Math.min(pos, rf.value.length);
      rf.setSelectionRange(safePos, safePos);
    }
  });
  document.addEventListener("change", function (ev) {
    var inp = ev.target.closest && ev.target.closest("[data-trend-action^='range-']");
    if (!inp) return;
    var which = inp.getAttribute("data-trend-action") === "range-from" ? "from" : "to";
    state.range[which] = inp.value || null;
    state.focus = [];
    if (state.range.from || state.range.to) state.mode = "day";
    clampRange();
    renderAll(true);
  });
  // Legend hover → isolate that line.
  document.addEventListener("mouseover", function (ev) {
    var item = ev.target.closest && ev.target.closest(".kpi-trend-leg-item[data-si]");
    if (item) setHighlight(item.getAttribute("data-si"));
  });
  document.addEventListener("mouseout", function (ev) {
    var item = ev.target.closest && ev.target.closest(".kpi-trend-leg-item[data-si]");
    if (item) setHighlight(null);
  });
  window.addEventListener("resize", function () {
    if (document.body.classList.contains("view-kpi-mode") && data) renderAll(false, null, "data");
  });

  window.__renderKpiTrend = function (trend, updated) {
    setupSectionObserver();
    if (!trend || !trend.day) {
      if (!data) { renderControls(); renderTitle(); renderChart({ cols: [], rows: [] }, null, "data"); }
      return;
    }
    var sig = signature(trend);
    var contentSig = contentSignature(trend);
    var structural = sig !== lastSig;
    if (data && contentSig && contentSig === lastDataContentSig && sig === lastSig) {
      return;
    }
    data = trend;
    lastSig = sig;
    lastDataContentSig = contentSig;
    // Use a stable chart-structure token for reveal tracking. The server updated
    // stamp can change on every poll, and that must not replay the draw animation.
    currentDataToken = sig;
    if (structural) {
      ["lmp", "country", "prefix"].forEach(function (bk) {
        if (state.selected[bk] != null) ensureSelection(bk);
      });
      clampRange();
    }
    renderAll(false, null, "data");
  };

  // Shared with the TEMU KPI section: the country breakdown's order + colours so its
  // country chips read identically to this chart's (same hue + same volume order).
  window.__kpiTrendCountryStyle = function () {
    try {
      if (!data) return { ordered: [], colors: {} };
      var g = countryGrouping("country");
      return { ordered: g.ordered.slice(), colors: Object.assign({}, g.colors) };
    } catch (e) { return { ordered: [], colors: {} }; }
  };
})();
