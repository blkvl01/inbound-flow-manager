/* KPI page - TEMU stage-duration section.
 *
 * Daily / weekly stacked columns of the per-period AVERAGE milestone gap
 * (ATA-NOA … Outbound), mirroring the "TEMU KPI - BUD wNN" report, in the
 * KPI-page visual language. The summary cards describe a FOCUS selection of the
 * columns: single-click focuses one day/week, click-drag selects a span; the
 * cards aggregate that selection. Country/LMP pre-filter chips reuse the Volume
 * trend look. Optional per-stage focus, A↔B comparison, sticky table + XLSX.
 *
 * Focus changes patch the card VALUES in place (no card DOM rebuild → the border
 * ring / gradient drift / opening animation never restart); only the diagram is
 * re-rendered. English UI; durations written with units ("35h 4m").
 */
(function () {
  "use strict";

  var payload = null;
  var mode = "day";
  var scope = "ALL";
  var focusFrom = null, focusTo = null;   // inspected RANGE = the chart window (presets/dates/drag)
  var selectedKey = null;                  // single column selected WITHIN the window (click); null = aggregate window
  var focusedStage = null;                 // optional per-stage focus
  var compare = false, cmpA = null, cmpB = null;   // cmpA/cmpB = {from, to} period_keys (a RANGE) or null
  var cmpTarget = "a";                              // which slot the next chart pick fills
  var tableOpen = false;
  var suspectsOpen = false;
  var focusInit = false;

  var lastRenderSig = "", lastCountSig = "";
  var revealObserver = null, revealed = false, revealArmed = false, openingTimer = 0, lastOpenTs = 0;
  var lastExport = null, lastFlagExport = null;
  var dragging = false, dragStart = -1, dragEnd = -1, dragMoved = false;

  var STAGE_COLORS = {
    ata_noa: "255,0,0", noa_transfer: "255,208,0", transfer_customs: "0,176,80",
    customs: "0,176,240", outbound: "112,48,160"
  };
  var STAGE_HEX = { ata_noa: "FF0000", noa_transfer: "FFD000", transfer_customs: "00B050", customs: "00B0F0", outbound: "7030A0" };
  var STAGE_SHORT = {
    ata_noa: "ATA-NOA", noa_transfer: "NOA-Transfer", transfer_customs: "Transfer-Customs",
    customs: "Customs Clearance", outbound: "Outbound"
  };
  var STAGE_ROUTES = {
    ata_noa: "ATA → NOA", noa_transfer: "NOA → Transfer", transfer_customs: "Transfer → Customs",
    customs: "Customs start → end", outbound: "Customs → Departure"
  };
  var COUNTRY_NAMES = {
    DE: "Germany", FR: "France", NL: "Netherlands", BE: "Belgium", PL: "Poland",
    ES: "Spain", IT: "Italy", CZ: "Czech Republic", AT: "Austria", HU: "Hungary",
    GB: "United Kingdom", SK: "Slovakia", RO: "Romania", SE: "Sweden", DK: "Denmark",
    PT: "Portugal", HR: "Croatia", SI: "Slovenia", BG: "Bulgaria", GR: "Greece",
    LT: "Lithuania", LV: "Latvia", EE: "Estonia", FI: "Finland", UA: "Ukraine", RS: "Serbia"
  };

  function esc(v) { return String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;"); }
  function asNum(v) { var n = Number(v); return isFinite(n) ? n : 0; }
  function reduceMotion() { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  function durParts(hours) { var tm = Math.round(asNum(hours) * 60); if (tm < 0) tm = 0; return { h: Math.floor(tm / 60), m: tm % 60 }; }
  function fmtDur(hours) { var p = durParts(hours); return p.h > 0 ? (p.h + "h " + p.m + "m") : (p.m + "m"); }
  function fmtDurHtml(hours) {
    var p = durParts(hours);
    return p.h > 0 ? '<span class="kpi-temu-dur-hour">' + p.h + 'h</span><span class="kpi-temu-dur-min">' + p.m + 'm</span>'
                   : '<span class="kpi-temu-dur-hour">' + p.m + 'm</span>';
  }
  function fmtDurSigned(hours) { return (hours < 0 ? "−" : "+") + fmtDur(Math.abs(hours)); }
  function fmtAxis(hours) { return Math.round(asNum(hours)) + "h"; }
  function fmtDate(iso) { var p = String(iso || "").split("-"); return p.length === 3 ? (p[0] + "." + p[1] + "." + p[2]) : String(iso || ""); }

  function hexToRgb(hex) {
    hex = String(hex || "").replace("#", "");
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    var n = parseInt(hex, 16);
    if (!isFinite(n) || hex.length !== 6) return null;
    return ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255);
  }
  function halfVisible(entry) {
    if (!entry || !entry.isIntersecting) return false;
    if (entry.intersectionRatio >= 0.5) return true;
    var ir = entry.intersectionRect, th = (entry.rootBounds && entry.rootBounds.height) || window.innerHeight || document.documentElement.clientHeight || 0;
    return !!(th && ir && ir.height >= th * 0.55);
  }
  function elementInView(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect(), h = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!h || r.height <= 0) return false;
    var visible = Math.max(0, Math.min(r.bottom, h) - Math.max(r.top, 0));
    return visible >= r.height * 0.5 || visible >= h * 0.55;
  }

  // ── data ───────────────────────────────────────────────────────────────────
  function stages() {
    return (payload && Array.isArray(payload.stages) && payload.stages.length) ? payload.stages
      : Object.keys(STAGE_COLORS).map(function (k) { return { key: k, label: k }; });
  }
  function countries() { return (payload && Array.isArray(payload.countries)) ? payload.countries : []; }
  function lmps() { return (payload && Array.isArray(payload.lmps)) ? payload.lmps : []; }
  function quality() { return (payload && payload.quality) || {}; }
  function suspects() {
    var s = payload && payload.suspects;
    return (s && typeof s === "object") ? s : { total: 0, shown: 0, field_total: 0, items: [] };
  }
  function scopeLabel(sc) {
    if (sc === "ALL") return "All countries";
    if (sc === "Egyéb") return "Other";
    if (payload && payload.day && (sc in payload.day) && lmps().indexOf(sc) >= 0) return sc;
    var nm = COUNTRY_NAMES[sc];
    return nm ? (sc + " · " + nm) : sc;
  }
  function qualitySuffix() {
    var q = quality(), fixed = asNum(q.corrected_fields), blocked = asNum(q.blocked_gaps), checked = asNum(q.guarded_rows);
    var flagged = asNum(suspects().total);
    if (!asNum(q.total_rows)) return "";
    if (fixed || blocked || flagged) {
      var parts = [];
      if (fixed) parts.push(fixed + " date fixes");
      if (flagged) parts.push(flagged + " flagged");
      if (blocked) parts.push(blocked + " blocked gaps");
      return " · Date guard: " + parts.join(", ");
    }
    return checked ? (" · Date guard: checked " + checked) : " · Date guard: clean";
  }
  function stageColor(key) { return STAGE_COLORS[key] || "148,163,184"; }
  function stageThemeStyle(key) {
    var pairs = {
      ata_noa: ["182,42,48", "255,92,92"],
      noa_transfer: ["191,158,34", "255,211,74"],
      transfer_customs: ["34,147,92", "80,210,145"],
      customs: ["34,142,190", "92,204,240"],
      outbound: ["111,72,165", "166,116,216"]
    };
    var p = pairs[key] || [stageColor(key), stageColor(key)];
    return "--card-a-rgb:" + p[0] + ";--card-b-rgb:" + p[1] + ";--seg-rgb:" + stageColor(key);
  }
  function stageDisplayLabel(stage) { if (!stage) return ""; return STAGE_SHORT[stage.key] || stage.label || stage.key || ""; }
  function stageRoute(key) { return STAGE_ROUTES[key] || ""; }
  function stageLegendLabel(stage) { return "AVG / " + stageDisplayLabel(stage); }
  function stageByKey(key) { var l = stages(); for (var i = 0; i < l.length; i++) if (l[i].key === key) return l[i]; return null; }
  function activeStages() { var hit = focusedStage ? stageByKey(focusedStage) : null; return hit ? [hit] : stages(); }

  function trendCountryStyle() {
    try { return (window.__kpiTrendCountryStyle && window.__kpiTrendCountryStyle()) || { ordered: [], colors: {} }; }
    catch (e) { return { ordered: [], colors: {} }; }
  }
  function orderedCountries() {
    var avail = countries(), style = trendCountryStyle(), out = [];
    style.ordered.forEach(function (c) { if (avail.indexOf(c) >= 0 && out.indexOf(c) < 0) out.push(c); });
    avail.forEach(function (c) { if (out.indexOf(c) < 0) out.push(c); });
    return out;
  }
  function countryRgb(c) { var col = trendCountryStyle().colors[c]; return (col && hexToRgb(col)) || "245,179,74"; }
  function lmpCountry(lmp) { var m = /^TEMU\s+([A-Z]{2})\b/.exec(String(lmp || "")); return m ? m[1] : "Egyéb"; }

  function rawPeriods() {
    if (!payload || !payload[mode]) return [];
    var arr = payload[mode][scope] || payload[mode].ALL || [];
    return Array.isArray(arr) ? arr : [];
  }
  // The KPI counts COMPLETED periods only: the live column (today / current week) is
  // excluded from the chart, cards, table and averages alike.
  function hasMeasuredStage(p) {
    if (!p || asNum(p.count) <= 0) return false;
    var cnt = p.seg_cnt || {};
    var seg = p.segments || {};
    return stages().some(function (s) {
      return asNum(cnt[s.key]) > 0 || asNum(seg[s.key]) > 0;
    });
  }
  function hasCompleteStageData(p) {
    if (!p || asNum(p.count) <= 0) return false;
    var cnt = p.seg_cnt || {};
    return stages().every(function (s) { return asNum(cnt[s.key]) > 0; });
  }
  function hasCompletePeriodData(p) {
    if (!p || asNum(p.count) <= 0) return false;
    if (p.complete === true) return true;
    var expected = asNum(p.expected_days) || (mode === "week" ? 7 : 1);
    return asNum(p.day_count) >= expected;
  }
  function dateMs(iso) {
    var s = String(iso || "");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return NaN;
    var d = new Date(s + "T00:00:00Z");
    return d.getTime();
  }
  function temuDayIsoSet() {
    var rows = (payload && payload.day && (payload.day[scope] || payload.day.ALL)) || [];
    var out = {};
    if (!Array.isArray(rows)) return out;
    rows.forEach(function (p) {
      var iso = p && (p.d0 || p.period_key);
      if (iso && hasMeasuredStage(p)) out[iso] = true;
    });
    return out;
  }
  function eachIsoDay(from, to, fn) {
    var cur = dateMs(from), end = dateMs(to);
    if (!isFinite(cur) || !isFinite(end) || cur > end) return false;
    while (cur <= end) {
      var d = new Date(cur);
      var iso = d.getUTCFullYear() + "-" +
        String(d.getUTCMonth() + 1).padStart(2, "0") + "-" +
        String(d.getUTCDate()).padStart(2, "0");
      if (fn(iso) === false) return false;
      cur += 86400000;
    }
    return true;
  }
  function temuRangeHasEveryDay(from, to) {
    var days = temuDayIsoSet();
    return eachIsoDay(from, to, function (iso) { return !!days[iso]; });
  }
  function isContiguousRange(list) {
    if (!list || list.length <= 1) return true;
    var stepMs = (mode === "week" ? 7 : 1) * 86400000;
    for (var i = 1; i < list.length; i++) {
      var prev = dateMs(list[i - 1].d0 || list[i - 1].period_key);
      var cur = dateMs(list[i].d0 || list[i].period_key);
      if (!isFinite(prev) || !isFinite(cur) || Math.abs((cur - prev) - stepMs) > 1000) return false;
    }
    return true;
  }
  function hasCompleteTrailingWindow(n) {
    var l = periods();
    if (!n || l.length < n) return false;
    var slice = l.slice(l.length - n);
    var first = slice[0], last = slice[slice.length - 1];
    return slice.every(hasCompletePeriodData) && isContiguousRange(slice) &&
      temuRangeHasEveryDay(first.d0 || first.period_key, last.d1 || last.d0 || last.period_key);
  }
  function periods() { return rawPeriods().filter(isClosedPeriod).filter(hasMeasuredStage); }
  function indexOfKey(list, key) { for (var i = 0; i < list.length; i++) if (list[i].period_key === key) return i; return -1; }
  function periodByKey(key) { var l = periods(), i = indexOfKey(l, key); return i >= 0 ? l[i] : null; }
  function todayKey() { var l = periods(); for (var i = l.length - 1; i >= 0; i--) if (l[i].is_today) return l[i].period_key; return l.length ? l[l.length - 1].period_key : null; }
  function todayIso() {
    var d = new Date(), m = String(d.getMonth() + 1).padStart(2, "0"), day = String(d.getDate()).padStart(2, "0");
    return d.getFullYear() + "-" + m + "-" + day;
  }
  function periodEndIso(p) { return String((p && (p.d1 || p.d0 || p.period_key)) || ""); }
  function isClosedPeriod(p) {
    if (!p || p.is_today) return false;
    var end = periodEndIso(p);
    return !end || end < todayIso();
  }
  function closedPeriods(list) { return (list || []).filter(isClosedPeriod); }
  function ensureFocus() {
    var l = periods();
    if (!l.length) return;
    if (focusFrom != null && indexOfKey(l, focusFrom) < 0) focusFrom = null;
    if (focusTo != null && indexOfKey(l, focusTo) < 0) focusTo = null;
    if (focusFrom == null || focusTo == null) { focusFrom = null; focusTo = null; }
  }
  function focusedList() {
    var l = periods();
    if (!l.length) return [];
    ensureFocus();
    if (focusFrom == null || focusTo == null) return l;   // full range (default, like the trend)
    var a = indexOfKey(l, focusFrom), b = indexOfKey(l, focusTo);
    if (a < 0 || b < 0) return l;
    var lo = Math.min(a, b), hi = Math.max(a, b);
    return l.slice(lo, hi + 1);
  }
  function rangeIsFull() { ensureFocus(); return focusFrom == null || focusTo == null; }
  // What the CARDS describe: a single selected column, else the whole window.
  function cardList() {
    if (selectedKey != null) { var p = periodByKey(selectedKey); if (p) return [p]; }
    return focusedList();
  }
  function prevList(fl) {
    fl = fl || closedPeriods(cardList());
    var l = closedPeriods(periods());
    if (!fl.length) return [];
    var i = indexOfKey(l, fl[0].period_key);
    if (i <= 0) return [];
    return l.slice(Math.max(0, i - fl.length), i);
  }
  function aggregate(list) {
    var sSum = {}, sCnt = {}, count = 0;
    stages().forEach(function (s) { sSum[s.key] = 0; sCnt[s.key] = 0; });
    (list || []).forEach(function (p) {
      count += asNum(p.count);
      stages().forEach(function (s) { sSum[s.key] += asNum((p.seg_sum || {})[s.key]); sCnt[s.key] += asNum((p.seg_cnt || {})[s.key]); });
    });
    var avg = {}, total = 0;
    stages().forEach(function (s) { var a = sCnt[s.key] ? sSum[s.key] / sCnt[s.key] : 0; avg[s.key] = a; total += a; });
    return { avg: avg, total: total, count: count };
  }
  function metricOf(p) { return focusedStage ? asNum((p.segments || {})[focusedStage]) : asNum(p.total); }
  function aggMetric(agg) { return focusedStage ? asNum(agg.avg[focusedStage]) : asNum(agg.total); }
  function biggestStage(p) {
    var best = null, bv = -1;
    stages().forEach(function (s) { var v = asNum((p.segments || {})[s.key]); if (v > bv) { bv = v; best = s; } });
    var total = asNum(p.total);
    return best ? { key: best.key, label: stageDisplayLabel(best), val: bv, pct: total > 0 ? bv / total * 100 : 0 } : null;
  }
  function focusSpanText() {
    var fl = cardList();
    if (!fl.length) return "—";
    var a = fl[0], b = fl[fl.length - 1];
    if (fl.length === 1) return mode === "day" ? fmtDate(a.d0) : (a.key + " · " + fmtDate(a.d0) + " – " + fmtDate(a.d1));
    if (rangeIsFull() && selectedKey == null) return "Full range · " + fl.length + (mode === "day" ? " days" : " weeks");
    return (mode === "day" ? fmtDate(a.d0) : a.key) + "  →  " + (mode === "day" ? fmtDate(b.d1 || b.d0) : b.key) + "  (" + fl.length + ")";
  }

  // ── controls ────────────────────────────────────────────────────────────
  function renderToggle(host, attr, current, defs) {
    if (!host) return;
    var html = defs.map(function (d) {
      var active = current === d[0];
      return '<button type="button" class="kpi-temu-tg-btn' + (active ? " is-active" : "") + '" ' + attr + '="' + esc(d[0]) +
        '" role="tab" aria-selected="' + active + '">' + esc(d[1]) + '</button>';
    }).join("");
    if (host.innerHTML !== html) host.innerHTML = html;
  }
  function chipHtml(sc, label, rgb) {
    var active = scope === sc, crgb = rgb || "148,163,184";
    return '<button type="button" class="kpi-temu-chip' + (active ? " is-active" : "") + '" data-temu-scope="' + esc(sc) +
      '" style="--chip-rgb:' + crgb + '"><span class="kpi-temu-chip-dot"></span><span class="kpi-temu-chip-l">' + esc(label) + '</span></button>';
  }
  function renderControls() {
    var ch = document.getElementById("kpi-temu-compare");
    if (ch) {
      var chtml = '<button type="button" class="kpi-temu-tg-btn kpi-temu-cmp-btn' + (compare ? " is-active" : "") +
        '" data-temu-action="toggle-compare" aria-pressed="' + compare + '"><span class="kpi-temu-cmp-ico">⇄</span> Compare</button>';
      if (ch.innerHTML !== chtml) ch.innerHTML = chtml;
    }
    renderToggle(document.getElementById("kpi-temu-view"), "data-temu-view", mode, [["day", "Day"], ["week", "Week"]]);
    var fhost = document.getElementById("kpi-temu-filter");
    if (fhost) {
      var countryChips = chipHtml("ALL", "All countries", null) + orderedCountries().map(function (c) { return chipHtml(c, scopeLabel(c), countryRgb(c)); }).join("");
      var lmpList = lmps(), lmpRow = "";
      // LMP list: a single fade-edged, horizontally scrollable strip (no label, no wrap).
      if (lmpList.length) lmpRow = '<div class="kpi-temu-lmp-strip"><div class="kpi-temu-lmp-track">' +
        lmpList.map(function (l) { return chipHtml(l, l, countryRgb(lmpCountry(l))); }).join("") + '</div></div>';
      var html = '<div class="kpi-temu-filter-row is-country">' + countryChips + '</div>' + lmpRow;
      if (fhost.innerHTML !== html) fhost.innerHTML = html;
    }
    renderDates();
  }
  function dateInputsHtml() {
    var l = periods();
    var span = l.length ? { min: l[0].d0 || l[0].period_key, max: l[l.length - 1].d1 || l[l.length - 1].period_key } : { min: "", max: "" };
    var fl = focusedList(), full = rangeIsFull();
    var fromVal = (!full && fl.length) ? (fl[0].d0 || fl[0].period_key) : "";
    var toVal = (!full && fl.length) ? (fl[fl.length - 1].d1 || fl[fl.length - 1].period_key) : "";
    return '<span class="kpi-temu-dates-cap">Range</span>' +
      '<input type="date" class="kpi-temu-date" data-temu-date="from" aria-label="From" value="' + esc(fromVal) + '" min="' + esc(span.min) + '" max="' + esc(span.max) + '">' +
      '<span class="kpi-temu-date-sep">–</span>' +
      '<input type="date" class="kpi-temu-date" data-temu-date="to" aria-label="To" value="' + esc(toVal) + '" min="' + esc(span.min) + '" max="' + esc(span.max) + '">';
  }
  function renderDates() {
    var host = document.getElementById("kpi-temu-dates");
    if (!host) return;
    var html = dateInputsHtml();
    if (host.innerHTML !== html) host.innerHTML = html;
  }
  function weekPresets() {
    var weeks = (payload && payload.week && (payload.week[scope] || payload.week.ALL)) || [];
    return (Array.isArray(weeks) ? weeks : []).filter(function (w) {
      return w && w.period_key && isClosedPeriod(w) && hasCompletePeriodData(w) && hasCompleteStageData(w) &&
        temuRangeHasEveryDay(w.d0, w.d1);
    })
      .map(function (w) { return { id: "wk-" + w.period_key, label: w.key || w.period_key, from: w.d0, to: w.d1, cat: "week" }; });
  }
  function presetDefs() {
    var n = periods().length;
    var out = [{ id: "all", label: "Full range", cat: "all" }];
    var sets = mode === "day" ? [[7, "Last 7 days"], [14, "Last 14 days"], [30, "Last 30 days"]]
                              : [[4, "Last 4 weeks"], [8, "Last 8 weeks"], [12, "Last 12 weeks"]];
    sets.forEach(function (p) { if (n >= p[0] && hasCompleteTrailingWindow(p[0])) out.push({ id: "last" + p[0], n: p[0], label: p[1], cat: "last" }); });
    return out.concat(weekPresets());   // individual WXX shortcuts (mirrors the Volume trend)
  }
  function rangeForDates(from, to) {
    var l = periods();
    if (!l.length) return null;
    var f = from || "0000-01-01", t = to || "9999-12-31";
    if (f > t) { var tmp = f; f = t; t = tmp; }
    var inc = l.filter(function (p) { var d0 = p.d0 || p.period_key, d1 = p.d1 || p.period_key; return d1 >= f && d0 <= t; });
    return inc.length ? [inc[0].period_key, inc[inc.length - 1].period_key] : null;
  }
  function activePreset() {
    if (rangeIsFull()) return "all";
    var l = periods(), fl = focusedList();
    if (!fl.length) return "custom";
    if (fl[fl.length - 1].period_key === l[l.length - 1].period_key && fl[0].period_key !== fl[fl.length - 1].period_key) {
      return "last" + fl.length;   // span anchored to the latest period → "Last N"
    }
    var wk = weekPresets();
    for (var i = 0; i < wk.length; i++) {
      var r = rangeForDates(wk[i].from, wk[i].to);
      if (r && r[0] === focusFrom && r[1] === focusTo) return wk[i].id;
    }
    return "custom";
  }
  function renderRange() {
    var host = document.getElementById("kpi-temu-range");
    if (!host) return;
    var active = activePreset();
    var groups = { all: [], last: [], week: [] };
    presetDefs().forEach(function (p) { (groups[p.cat] || (groups[p.cat] = [])).push(p); });
    function chipFor(p) {
      return '<button type="button" class="kpi-temu-rchip is-cat-' + p.cat + (active === p.id ? " is-on" : "") +
        '" data-temu-action="preset" data-value="' + esc(p.id) + '">' + esc(p.label) + '</button>';
    }
    var order = ["all", "last", "week"].filter(function (c) { return groups[c] && groups[c].length; });
    var groupHtml = order.map(function (c) {
      return '<div class="kpi-temu-rgroup is-' + c + '">' + groups[c].map(chipFor).join("") + '</div>';
    }).join('<span class="kpi-temu-rsep" aria-hidden="true"></span>');
    var html = '<div class="kpi-temu-range-label">Inspected period</div>' +
      '<div class="kpi-temu-range-row">' + groupHtml + '</div>';
    if (host.innerHTML !== html) host.innerHTML = html;
  }
  function applyPreset(id) {
    var l = periods();
    if (!id || id === "all" || !l.length) { focusFrom = focusTo = null; return; }
    var m = /^last(\d+)$/.exec(id);
    if (m) { var n = Math.min(l.length, parseInt(m[1], 10)); var slice = l.slice(l.length - n); focusFrom = slice[0].period_key; focusTo = slice[slice.length - 1].period_key; return; }
    var wk = weekPresets();
    for (var i = 0; i < wk.length; i++) { if (wk[i].id === id) { applyDateRange(wk[i].from, wk[i].to); return; } }
  }
  function applyDateRange(from, to) {
    if (!from && !to) { focusFrom = focusTo = null; return; }
    var r = rangeForDates(from, to);
    if (!r) { focusFrom = focusTo = null; return; }
    focusFrom = r[0]; focusTo = r[1];
  }

  // ── summary (focused selection) ──────────────────────────────────────────
  function deltaHtml(cur, prevV) {
    if (prevV == null) {
      var pl = prevList();
      return '<div class="kpi-temu-delta is-none"><span class="kpi-temu-delta-base">no previous period</span></div>';
    }
    var diff = cur - prevV;
    var dir = diff < -0.0001 ? "down" : (diff > 0.0001 ? "up" : "flat");
    var good = dir === "down" ? "is-good" : (dir === "up" ? "is-bad" : "is-flat");
    var arrow = dir === "down" ? "▼" : (dir === "up" ? "▲" : "■");
    var pct = prevV ? (diff / prevV * 100) : 0;
    var n = closedPeriods(cardList()).length || cardList().length;
    var base = "vs previous " + n + " " + (mode === "day" ? (n > 1 ? "days" : "day") : (n > 1 ? "weeks" : "week"));
    return '<div class="kpi-temu-delta ' + good + '"><span class="kpi-temu-delta-main"><i>' + arrow + '</i>' + esc(fmtDurSigned(diff)) +
      ' <small>(' + (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(0) + '%)</small></span><span class="kpi-temu-delta-base">' + esc(base) + '</span></div>';
  }
  function summaryModel() {
    var rawFl = cardList(), fl = closedPeriods(rawFl), previous = prevList(fl);
    var agg = aggregate(fl), prevAgg = aggregate(previous);
    var cur = aggMetric(agg), prevV = previous.length ? aggMetric(prevAgg) : null;
    // Fastest / slowest use only completed periods from the inspected window.
    var list = closedPeriods(focusedList()), fast = null, slow = null;
    list.forEach(function (p) { var pv = metricOf(p); if (pv <= 0) return; if (!fast || pv < metricOf(fast)) fast = p; if (!slow || pv > metricOf(slow)) slow = p; });
    return { fl: rawFl, calcFl: fl, agg: agg, cur: cur, prevV: prevV, fast: fast, slow: slow };
  }
  function peakStageHtml(p) {
    var fs = focusedStage ? stageByKey(focusedStage) : null;
    var big = fs ? { key: fs.key, label: stageDisplayLabel(fs), val: metricOf(p) } : biggestStage(p);
    if (!big) return "";
    return '<span class="kpi-temu-peak-stage" style="--seg-rgb:' + stageColor(big.key) + '"><i></i>' +
      '<span class="kpi-temu-peak-stage-txt"><em>' + esc(big.label) + '</em></span>' +
      '<b class="kpi-temu-dur-split">' + fmtDurHtml(big.val) + '</b></span>';
  }
  function peakCardHtml(cls, title, p) {
    if (!p) return '<div class="kpi-temu-peak ' + cls + ' is-empty" aria-disabled="true"><span class="kpi-temu-peak-title">' + esc(title) + '</span><b class="kpi-temu-peak-na">—</b></div>';
    return '<div class="kpi-temu-peak ' + cls + '" aria-label="' + esc(title) + '">' +
      '<span class="kpi-temu-peak-title">' + esc(title) + '</span>' +
      '<span class="kpi-temu-peak-key">' + esc(p.key) + '</span>' +
      '<b class="kpi-temu-cu kpi-temu-peak-val kpi-temu-dur-split" data-cu="' + metricOf(p).toFixed(4) + '">' + fmtDurHtml(metricOf(p)) + '</b>' +
      peakStageHtml(p) + '</div>';
  }
  function stageCardHtml(s, fl, agg) {
    var v = asNum(agg.avg[s.key]);
    var share = agg.total > 0 ? v / agg.total * 100 : 0;
    var active = focusedStage === s.key;
    return '<button type="button" class="kpi-temu-stage-card' + (active ? ' is-focused' : '') + '" data-temu-stage-focus="' + esc(s.key) +
      '" aria-pressed="' + active + '" style="--seg-rgb:' + stageColor(s.key) + '">' +
      '<span class="kpi-temu-stage-card-name">' + esc(stageDisplayLabel(s)) + '</span>' +
      '<span class="kpi-temu-stage-card-route">' + esc(stageRoute(s.key)) + '</span>' +
      '<b class="kpi-temu-cu kpi-temu-stage-card-val kpi-temu-dur-split" data-cu="' + v.toFixed(4) + '">' + fmtDurHtml(v) + '</b>' +
      '<span class="kpi-temu-stage-card-share">' + share.toFixed(0) + '% of total</span></button>';
  }
  function renderSummary() {
    var host = document.getElementById("kpi-temu-summary");
    if (!host) return;
    var m = summaryModel();
    if (!m.fl.length) { host.innerHTML = ""; return; }
    var fs = focusedStage ? stageByKey(focusedStage) : null;
    var headLabel = fs ? ("Avg " + stageDisplayLabel(fs)) : "Avg total lead time";
    var headUnit = focusSpanText() + (fs ? " · focused stage" : " · ATA-OUTBOUND");
    var headTheme = fs ? (' style="' + stageThemeStyle(fs.key) + '"') : "";
    var stageCards = stages().map(function (s) { return stageCardHtml(s, m.fl, m.agg); }).join("");
    host.innerHTML =
      '<div class="kpi-temu-top">' +
        '<div class="kpi-temu-headline-main"' + headTheme + '>' +
          '<span class="kpi-temu-headline-label">' + esc(headLabel) + '</span>' +
          '<b class="kpi-temu-cu kpi-temu-headline-val kpi-temu-dur-split" data-cu="' + m.cur.toFixed(4) + '">' + fmtDurHtml(m.cur) + '</b>' +
          '<span class="kpi-temu-headline-unit">' + esc(headUnit) + '</span>' +
        '</div>' +
        peakCardHtml("is-fast", "Fastest period", m.fast) + peakCardHtml("is-slow", "Slowest period", m.slow) +
      '</div>' +
      '<div class="kpi-temu-stage-cards">' + stageCards + '</div>';
  }
  // Patch the card VALUES in place (no DOM rebuild → ring/sheen/opening persist).
  function patchSummary(animate) {
    var host = document.getElementById("kpi-temu-summary");
    if (!host || !host.querySelector(".kpi-temu-headline-main")) { renderSummary(); return; }
    var m = summaryModel();
    if (!m.fl.length) return;
    var fs = focusedStage ? stageByKey(focusedStage) : null;
    var hv = host.querySelector(".kpi-temu-headline-val");
    var hl = host.querySelector(".kpi-temu-headline-label");
    var hu = host.querySelector(".kpi-temu-headline-unit");
    var hc = host.querySelector(".kpi-temu-headline-main");
    if (hc) {
      if (fs) hc.setAttribute("style", stageThemeStyle(fs.key));
      else hc.removeAttribute("style");
    }
    if (hl) hl.textContent = fs ? ("Avg " + stageDisplayLabel(fs)) : "Avg total lead time";
    if (hu) hu.textContent = focusSpanText() + (fs ? " · focused stage" : " · ATA-OUTBOUND");
    if (hv) { hv.setAttribute("data-cu", m.cur.toFixed(4)); countUp(hv, m.cur, animate, 60); }
    // peaks (depend on metric → patch key/val/stage + focus-key)
    [["is-fast", m.fast], ["is-slow", m.slow]].forEach(function (pair) {
      var card = host.querySelector(".kpi-temu-peak." + pair[0]);
      var p = pair[1];
      if (!card) return;
      card.removeAttribute("disabled"); card.removeAttribute("data-temu-focus-key");
      if (!p) {
        card.classList.add("is-empty");
        card.setAttribute("aria-disabled", "true");
        var emptyK = card.querySelector(".kpi-temu-peak-key"); if (emptyK) emptyK.textContent = "—";
        var emptyV = card.querySelector(".kpi-temu-peak-val"); if (emptyV) { emptyV.setAttribute("data-cu", "0"); emptyV.textContent = "—"; }
        var emptySt = card.querySelector(".kpi-temu-peak-stage"); if (emptySt) emptySt.outerHTML = "";
        return;
      }
      card.classList.remove("is-empty"); card.removeAttribute("aria-disabled");
      var k = card.querySelector(".kpi-temu-peak-key"); if (k) k.textContent = p.key;
      var pv = card.querySelector(".kpi-temu-peak-val"); if (pv) { pv.setAttribute("data-cu", metricOf(p).toFixed(4)); countUp(pv, metricOf(p), animate, 120); }
      var st = card.querySelector(".kpi-temu-peak-stage"); if (st) st.outerHTML = peakStageHtml(p);
    });
    // stage cards (val + share + glow)
    stages().forEach(function (s, i) {
      var card = host.querySelector('.kpi-temu-stage-card[data-temu-stage-focus="' + s.key + '"]');
      if (!card) return;
      var v = asNum(m.agg.avg[s.key]), share = m.agg.total > 0 ? v / m.agg.total * 100 : 0;
      var vEl = card.querySelector(".kpi-temu-stage-card-val");
      var shEl = card.querySelector(".kpi-temu-stage-card-share");
      if (vEl) { vEl.setAttribute("data-cu", v.toFixed(4)); countUp(vEl, v, animate, 90 + i * 50); }
      if (shEl) shEl.textContent = share.toFixed(0) + "% of total";
      var on = focusedStage === s.key;
      card.classList.toggle("is-focused", on);
      card.setAttribute("aria-pressed", on);
    });
  }

  // ── chart ──────────────────────────────────────────────────────────────────
  function axisCeil(maxV) {
    var v = maxV * 1.08;
    if (v <= 6) return Math.ceil(v);
    if (v <= 12) return Math.ceil(v / 2) * 2;
    if (v <= 24) return Math.ceil(v / 4) * 4;
    if (v <= 48) return Math.ceil(v / 6) * 6;
    return Math.ceil(v / 12) * 12;
  }
  function chartHtml(list) {
    var maxV = 0; list.forEach(function (p) { maxV = Math.max(maxV, metricOf(p)); });
    var top = axisCeil(maxV || 1), ticks = 4, yaxis = "", gridlines = "";
    for (var t = ticks; t >= 0; t--) {
      yaxis += '<span class="kpi-temu-tick">' + esc(fmtAxis(top * t / ticks)) + '</span>';
      gridlines += '<i class="kpi-temu-gridline" style="bottom:' + (t / ticks * 100).toFixed(2) + '%"></i>';
    }
    var stageDefs = activeStages();
    var dense = list.length > 22;
    var aStart = compare ? cmpStartKey(cmpA) : null, bStart = compare ? cmpStartKey(cmpB) : null;
    var cols = list.map(function (p, i) {
      var total = metricOf(p);
      var barPct = total > 0 ? Math.max(1.5, total / top * 100) : 0;
      var seg = p.segments || {};
      var segHtml = stageDefs.map(function (s) {
        var v = asNum(seg[s.key]);
        if (v <= 0) return '<i class="kpi-temu-seg seg-' + esc(s.key) + '" style="height:0%;--seg-rgb:' + stageColor(s.key) + '"></i>';
        var pctOfBar = total > 0 ? (v / total * 100) : 0;
        var cls = "kpi-temu-seg seg-" + s.key + (pctOfBar < 5 ? " is-tiny" : (pctOfBar < 9 ? " is-slim" : "")) + (dense ? " is-dense" : "");
        return '<i class="' + esc(cls) + '" style="height:' + pctOfBar.toFixed(3) + '%;--seg-rgb:' + stageColor(s.key) +
          '" title="' + esc(stageDisplayLabel(s) + " (" + stageRoute(s.key) + "): " + fmtDur(v)) + '"><b>' + esc(fmtDur(v)) + '</b></i>';
      }).join("");
      var isFoc = !compare && selectedKey === p.period_key;
      var isA = compare && inCmpRange(p.period_key, cmpA), isB = compare && inCmpRange(p.period_key, cmpB);
      var muted = compare && (cmpA || cmpB) && !isA && !isB;
      var today = !!p.is_today;
      return '<div class="kpi-temu-col' + (isFoc ? " is-focus" : "") + (isA ? " is-cmp-a" : "") + (isB ? " is-cmp-b" : "") +
        (muted ? " is-muted" : "") + (today ? " is-today" : "") + '" role="button" tabindex="0" data-temu-i="' + i +
        '" data-temu-key="' + esc(p.period_key) + '" style="--col-i:' + i + '" title="' + esc(p.key + " · " + p.count + " shipments · " + fmtDur(total)) + '">' +
        (isA && p.period_key === aStart ? '<span class="kpi-temu-cmp-tag a">A</span>' : '') + (isB && p.period_key === bStart ? '<span class="kpi-temu-cmp-tag b">B</span>' : '') +
        (today ? '<span class="kpi-temu-today-tag">Today</span>' : '') +
        '<span class="kpi-temu-col-total" data-cu="' + total.toFixed(4) + '">' + esc(fmtDur(total)) + '</span>' +
        '<span class="kpi-temu-bar-track"><span class="kpi-temu-bar" style="height:' + barPct.toFixed(3) + '%">' + segHtml + '</span></span>' +
        '<span class="kpi-temu-col-label">' + esc(p.key) + '<i class="kpi-temu-col-count">' + esc(p.count) + ' pcs</i></span>' +
        '</div>';
    }).join("");
    return '<div class="kpi-temu-chart-inner">' +
      '<div class="kpi-temu-yaxis" aria-hidden="true">' + yaxis + '</div>' +
      '<div class="kpi-temu-plot"><div class="kpi-temu-gridlines" aria-hidden="true">' + gridlines + '</div>' +
        '<div class="kpi-temu-cols">' + cols + '</div>' +
        '<div class="kpi-temu-tooltip" id="kpi-temu-tooltip" aria-hidden="true"></div></div></div>';
  }
  function renderChartOnly(animate) {
    var section = document.getElementById("kpi-temu-section");
    var chartHost = document.getElementById("kpi-temu-chart");
    if (!chartHost) return;
    chartHost.innerHTML = chartHtml(focusedList());
    playFill(section, !!animate);
    lastCountSig = "";
    runCountsChart(chartHost, !!animate);
  }
  function renderLegend() {
    var host = document.getElementById("kpi-temu-legend");
    if (!host) return;
    var html = stages().map(function (s) {
      var active = focusedStage === s.key;
      return '<button type="button" class="kpi-temu-leg' + (active ? ' is-focused' : '') + '" data-temu-stage-focus="' + esc(s.key) +
        '" aria-pressed="' + active + '"><i style="background:rgb(' + stageColor(s.key) + ')"></i>' + esc(stageLegendLabel(s)) + '</button>';
    }).join("");
    if (host.innerHTML !== html) host.innerHTML = html;
  }

  // ── compare panel ────────────────────────────────────────────────────────
  // ── compare ranges (A / B are spans of periods, not single columns) ─────────
  function cmpRangePeriods(rng) {
    if (!rng) return [];
    var l = periods(), a = indexOfKey(l, rng.from), b = indexOfKey(l, rng.to);
    if (a < 0 || b < 0) return [];
    var lo = Math.min(a, b), hi = Math.max(a, b);
    return l.slice(lo, hi + 1);
  }
  function cmpStartKey(rng) { var ps = cmpRangePeriods(rng); return ps.length ? ps[0].period_key : null; }
  function inCmpRange(key, rng) {
    if (!rng) return false;
    var l = periods(), i = indexOfKey(l, key), a = indexOfKey(l, rng.from), b = indexOfKey(l, rng.to);
    if (i < 0 || a < 0 || b < 0) return false;
    return i >= Math.min(a, b) && i <= Math.max(a, b);
  }
  function cmpRangeLabel(rng) {
    var ps = cmpRangePeriods(rng);
    if (!ps.length) return "—";
    var a = ps[0], b = ps[ps.length - 1];
    if (ps.length === 1) return mode === "day" ? fmtDate(a.d0) : a.key;
    return (mode === "day" ? fmtDate(a.d0) : a.key) + " – " + (mode === "day" ? fmtDate(b.d1 || b.d0) : b.key);
  }
  function cmpSpanText(rng) {
    var n = cmpRangePeriods(rng).length;
    return n + " " + (mode === "day" ? (n === 1 ? "day" : "days") : (n === 1 ? "week" : "weeks"));
  }
  function setCmp(target, fromKey, toKey) {
    var rng = { from: fromKey, to: toKey };
    if (target === "b") cmpB = rng; else cmpA = rng;
    cmpTarget = (target === "a") ? "b" : "a";   // alternate so two quick picks fill A then B
  }
  function cmpSlotHtml(tag, rng) {
    var active = cmpTarget === tag;
    var ps = cmpRangePeriods(rng);
    if (!ps.length) {
      return '<button type="button" class="kpi-temu-cmp-slot is-' + tag + (active ? ' is-active' : '') + ' is-empty" data-temu-action="cmp-target-' + tag + '">' +
        '<span class="kpi-temu-cmp-slot-tag">' + tag.toUpperCase() + '</span>' +
        '<span class="kpi-temu-cmp-slot-pick">' + (active ? 'Drag bars to set ' + tag.toUpperCase() : 'Select ' + tag.toUpperCase()) + '</span></button>';
    }
    var agg = aggregate(ps);
    return '<button type="button" class="kpi-temu-cmp-slot is-' + tag + (active ? ' is-active' : '') + '" data-temu-action="cmp-target-' + tag + '">' +
      '<span class="kpi-temu-cmp-slot-tag">' + tag.toUpperCase() + '</span>' +
      '<span class="kpi-temu-cmp-slot-range">' + esc(cmpRangeLabel(rng)) + '</span>' +
      '<b class="kpi-temu-cmp-slot-val kpi-temu-dur-split">' + fmtDurHtml(agg.total) + '</b>' +
      '<span class="kpi-temu-cmp-slot-sub">' + esc(agg.count + ' pcs · ' + cmpSpanText(rng)) + '</span>' +
      '<span class="kpi-temu-cmp-slot-x" data-temu-action="cmp-clear-' + tag + '" role="button" aria-label="Clear ' + tag.toUpperCase() + '">✕</span></button>';
  }
  function renderPanel() {
    var host = document.getElementById("kpi-temu-detail");
    if (!host) return;
    if (!compare) { host.classList.remove("is-open", "is-compare"); host.innerHTML = ""; return; }
    var aPs = cmpRangePeriods(cmpA), bPs = cmpRangePeriods(cmpB);
    var aggA = aggregate(aPs), aggB = aggregate(bPs);
    var both = aPs.length && bPs.length;
    var overallWinner = both ? (aggA.total < aggB.total - 0.0001 ? "a" : (aggB.total < aggA.total - 0.0001 ? "b" : "flat")) : "";

    var slots = '<div class="kpi-temu-cmp-slots">' + cmpSlotHtml("a", cmpA) +
      '<button type="button" class="kpi-temu-cmp-swap" data-temu-action="cmp-swap" aria-label="Swap A and B" title="Swap A ⇄ B">⇄</button>' +
      cmpSlotHtml("b", cmpB) + '</div>';

    // Verdict (the "deeper analysis"): total Δ + biggest-moving stage.
    var verdict;
    if (!both) {
      verdict = '<aside class="kpi-temu-cmp-rail"><div class="kpi-temu-cmp-card kpi-temu-cmp-verdict is-hint">' +
        (aPs.length || bPs.length ? 'Pick the second range to compare' : 'Drag across bars, or click one, to set A and B — weeks or days') + '</div>';
      verdict += '</aside>';
    } else {
      var td = aggB.total - aggA.total, dir = td < -0.0001 ? "good" : (td > 0.0001 ? "bad" : "flat");
      var pct = aggA.total ? (td / aggA.total * 100) : 0;
      var mover = null, mv = 0;
      stages().forEach(function (s) { var d = asNum(aggB.avg[s.key]) - asNum(aggA.avg[s.key]); if (Math.abs(d) > Math.abs(mv)) { mv = d; mover = s; } });
      var word = dir === "good" ? "B faster" : (dir === "bad" ? "B slower" : "no change");
      verdict = '<div class="kpi-temu-cmp-verdict is-' + dir + '">' +
        '<div class="kpi-temu-cmp-vmain"><span class="kpi-temu-cmp-vlabel">Δ total lead time</span>' +
          '<b class="kpi-temu-cmp-vval kpi-temu-dur-split">' + (td < 0 ? '−' : (td > 0 ? '+' : '')) + fmtDurHtml(Math.abs(td)) + '</b>' +
          '<span class="kpi-temu-cmp-vpct">' + (pct >= 0 ? "+" : "−") + Math.abs(pct).toFixed(0) + '% · ' + word + '</span></div>' +
        (mover ? '<div class="kpi-temu-cmp-mover" style="--seg-rgb:' + stageColor(mover.key) + '"><i></i><span>Biggest change</span><em>' + esc(stageDisplayLabel(mover)) + '</em><b>' + esc(fmtDurSigned(mv)) + '</b></div>' : '') +
      '</div>';
    }

    if (both) {
      // The Δ total verdict is the result on its own — no separate "winner" card.
      verdict = '<aside class="kpi-temu-cmp-rail" data-winner="' + overallWinner + '">' +
        '<div class="kpi-temu-cmp-card kpi-temu-cmp-card-total">' + verdict + '</div>' +
      '</aside>';
    }

    // Per-stage compact comparison with twin proportional bars.
    var maxV = 1;
    stages().forEach(function (s) { maxV = Math.max(maxV, asNum(aggA.avg[s.key]), asNum(aggB.avg[s.key])); });
    var stageRows = stages().map(function (s) {
      var av = asNum(aggA.avg[s.key]), bv = asNum(aggB.avg[s.key]), d = bv - av;
      var dc = Math.abs(d) < 0.0001 ? "flat" : (d < 0 ? "good" : "bad");
      var aw = (av / maxV * 100).toFixed(2), bw = (bv / maxV * 100).toFixed(2);
      return '<div class="kpi-temu-cmp-st" style="--seg-rgb:' + stageColor(s.key) + '">' +
        '<span class="kpi-temu-cmp-st-name">' + esc(stageDisplayLabel(s)) + '</span>' +
        '<span class="kpi-temu-cmp-st-bars"><i class="a" style="width:' + (aPs.length ? aw : 0) + '%"></i><i class="b" style="width:' + (bPs.length ? bw : 0) + '%"></i></span>' +
        '<span class="kpi-temu-cmp-st-va">' + (aPs.length ? esc(fmtDur(av)) : "—") + '</span>' +
        '<span class="kpi-temu-cmp-st-vb">' + (bPs.length ? esc(fmtDur(bv)) : "—") + '</span>' +
        '<span class="kpi-temu-cmp-st-d is-' + dc + '">' + (both ? esc(fmtDurSigned(d)) : "—") + '</span></div>';
    }).join("");

    host.innerHTML =
      '<div class="kpi-temu-cmp-head"><b>Deep dive</b><span class="kpi-temu-cmp-head-sub">A vs B</span>' +
        '<button type="button" class="kpi-temu-detail-close" data-temu-action="clear-compare" aria-label="Clear comparison">✕</button></div>' +
      slots + verdict +
      '<div class="kpi-temu-cmp-stages"><div class="kpi-temu-cmp-st is-head"><span class="kpi-temu-cmp-st-name">Stage</span><span class="kpi-temu-cmp-st-bars"></span><span class="kpi-temu-cmp-st-va">A</span><span class="kpi-temu-cmp-st-vb">B</span><span class="kpi-temu-cmp-st-d">Δ</span></div>' + stageRows + '</div>';
    var stageBlock = host.querySelector(".kpi-temu-cmp-stages");
    var railBlock = host.querySelector(".kpi-temu-cmp-rail");
    if (stageBlock) stageBlock.setAttribute("data-winner", overallWinner);
    if (stageBlock && railBlock) {
      var body = document.createElement("div");
      body.className = "kpi-temu-cmp-body";
      stageBlock.parentNode.insertBefore(body, stageBlock);
      body.appendChild(stageBlock);
      body.appendChild(railBlock);
    }
    host.classList.add("is-open", "is-compare");
  }

  // ── table ──────────────────────────────────────────────────────────────────
  function buildExport(list) {
    var st = stages().map(function (s) { return { key: s.key, label: stageDisplayLabel(s), color: STAGE_HEX[s.key] || "94A3B8" }; });
    var rows = list.map(function (p) {
      var vals = {}; st.forEach(function (s) { vals[s.key] = asNum((p.segments || {})[s.key]); });
      return { label: p.key, date: p.d0 || p.period_key, dateEnd: p.d1 || p.d0 || p.period_key, count: asNum(p.count), vals: vals, total: asNum(p.total) };
    });
    var agg = aggregate(list), tvals = {}; st.forEach(function (s) { tvals[s.key] = asNum(agg.avg[s.key]); });
    return { kind: "table", title: "TEMU KPI", scopeLabel: scopeLabel(scope), modeLabel: mode === "day" ? "Daily" : "Weekly", periodType: mode,
      rangeLabel: list.length ? (list[0].key + " – " + list[list.length - 1].key) : "—", stages: st, rows: rows, totals: { vals: tvals, total: agg.total, count: agg.count } };
  }
  function renderTable() {
    var host = document.getElementById("kpi-temu-table");
    if (!host) return;
    var list = periods(); lastExport = buildExport(list);
    var st = stages(), agg = aggregate(list);
    var headCells = st.map(function (s) { return '<th class="kpi-temu-th-stage" style="--seg-rgb:' + stageColor(s.key) + '"><span class="kpi-temu-th-dot"></span>' + esc(stageDisplayLabel(s)) + '</th>'; }).join("");
    var fl = focusedList(), focKeys = {}; fl.forEach(function (p) { focKeys[p.period_key] = true; });
    var bodyRows = list.map(function (p) {
      var dateLbl = mode === "day" ? fmtDate(p.d0) : (p.key + " · " + fmtDate(p.d0) + "–" + fmtDate(p.d1));
      var cells = st.map(function (s) { return '<td class="num">' + esc(fmtDur(asNum((p.segments || {})[s.key]))) + '</td>'; }).join("");
      var cls = (focKeys[p.period_key] ? " is-focus" : "") + (p.is_today ? " is-today" : "");
      return '<tr class="kpi-temu-trow' + cls + '" data-temu-key="' + esc(p.period_key) + '"><th scope="row">' + esc(dateLbl) +
        (p.is_today ? '<span class="kpi-temu-trow-today">Today</span>' : '') + '</th>' + cells +
        '<td class="num total">' + esc(fmtDur(p.total)) + '</td><td class="num pcs">' + esc(p.count) + '</td></tr>';
    }).join("");
    var totalCells = st.map(function (s) { return '<td class="num">' + esc(fmtDur(asNum(agg.avg[s.key]))) + '</td>'; }).join("");
    host.innerHTML =
      '<div class="kpi-temu-table-drawer' + (tableOpen ? " is-open" : "") + '">' +
        '<div class="kpi-temu-table-bar">' +
          '<button type="button" class="kpi-temu-table-toggle" data-temu-action="toggle-table" aria-expanded="' + tableOpen + '"><span class="kpi-temu-table-title">Data table</span>' +
            '<span class="kpi-temu-table-sub">' + esc(scopeLabel(scope) + " · " + (mode === "day" ? "Daily" : "Weekly") + " · " + list.length + (mode === "day" ? " days" : " weeks")) + '</span></button>' +
          '<button type="button" class="kpi-temu-export" data-temu-action="export-xlsx">XLSX export</button>' +
        '</div>' +
        '<div class="kpi-temu-table-wrap' + (tableOpen ? "" : " is-collapsed") + '"><table class="kpi-temu-table"><thead><tr><th class="kpi-temu-th-period">' +
          (mode === "day" ? "Date" : "Week") + '</th>' + headCells + '<th class="num">Total</th><th class="num">Pcs</th></tr></thead><tbody>' + bodyRows +
          '</tbody><tfoot><tr><th scope="row">Average</th>' + totalCells + '<td class="num total">' + esc(fmtDur(agg.total)) + '</td><td class="num pcs">' + esc(agg.count) + '</td></tr></tfoot></table></div></div>';
  }

  // ── flagged records (data-entry warnings) ───────────────────────────────────
  function visibleRangeLabel(list) {
    list = list || focusedList();
    if (!list.length) return "—";
    var a = list[0], b = list[list.length - 1];
    return list.length === 1 ? a.key : (a.key + " – " + b.key);
  }
  function suspectMatchesScope(it) {
    if (scope === "ALL") return true;
    if (lmps().indexOf(scope) >= 0) return String(it.lmp || "") === scope;
    return String(it.country || "") === scope;
  }
  function visibleSuspectsModel() {
    var s = suspects(), all = Array.isArray(s.items) ? s.items : [];
    var fl = focusedList();
    if (!fl.length) return { total: 0, shown: 0, field_total: 0, items: [] };
    var start = fl[0].d0 || fl[0].period_key;
    var end = fl[fl.length - 1].d1 || fl[fl.length - 1].d0 || fl[fl.length - 1].period_key;
    var items = all.filter(function (it) {
      var day = String((it && it.day) || "");
      return day && day >= start && day <= end && suspectMatchesScope(it);
    });
    var fieldTotal = items.reduce(function (sum, it) { return sum + ((it.fields || []).length || 0); }, 0);
    return { total: items.length, shown: items.length, field_total: fieldTotal, items: items };
  }
  function buildSuspectExport(model) {
    var list = focusedList();
    return {
      kind: "flagged",
      title: "TEMU flagged records",
      scopeLabel: scopeLabel(scope),
      modeLabel: mode === "day" ? "Daily" : "Weekly",
      periodType: mode,
      rangeLabel: visibleRangeLabel(list),
      items: (model && model.items) || []
    };
  }

  var REASON_META = {
    "future date":          { cls: "is-future", short: "future date" },
    "before previous step": { cls: "is-order",  short: "out of order" },
    "after next step":      { cls: "is-order",  short: "out of order" },
    "implausible gap":      { cls: "is-gap",    short: "implausible gap" },
    "missing":              { cls: "is-gap",    short: "missing" }
  };
  function reasonMeta(r) { return REASON_META[r] || { cls: "is-gap", short: r || "" }; }
  // Flagged records use a CSS-grid layout (class prefix "ftg"/"ftc") — a real <table>
  // collides with the old flex-list rules (.kpi-temu-flag-row{display:flex}) and the
  // header/body column widths desync. Grid keeps every cell perfectly column-aligned.
  function flagCellHtml(ms) {
    if (!ms) return '<div class="ftc is-empty"><span class="ftc-dash">·</span></div>';
    if (!ms.suspect) {
      return '<div class="ftc">' + (ms.raw
        ? '<span class="ftc-time">' + esc(ms.raw) + '</span>'
        : '<span class="ftc-dash">·</span>') + '</div>';
    }
    var m = reasonMeta(ms.reason);
    return '<div class="ftc is-bad ' + m.cls + '" title="' + esc(ms.label + ": " + (ms.raw || "—") + " — " + m.short) + '">' +
      '<span class="ftc-time">' + esc(ms.raw || "—") + '</span>' +
      '<span class="ftc-why">' + esc(m.short) + '</span></div>';
  }
  function suspectRowHtml(it) {
    var chip = '<span class="ftg-chip" style="--chip-rgb:' + countryRgb(it.country || "") +
      '"><i></i>' + esc(it.country || "—") + '</span>';
    var cells = (it.milestones || []).map(flagCellHtml).join("");
    return '<div class="ftg-row">' +
      '<div class="ftg-id">' +
        '<div class="ftg-id-top">' + chip + '<span class="ftg-awb">' + esc(it.awb || "—") + '</span></div>' +
        '<div class="ftg-id-sub">' + esc(it.lmp || "") +
          (it.day ? '<span class="ftg-day">' + esc(fmtDate(it.day)) + '</span>' : "") + '</div>' +
      '</div>' + cells + '</div>';
  }
  function renderSuspects() {
    var host = document.getElementById("kpi-temu-suspects");
    if (!host) return;
    var s = visibleSuspectsModel(), items = Array.isArray(s.items) ? s.items : [];
    lastFlagExport = buildSuspectExport(s);
    if (!asNum(s.total)) { host.innerHTML = ""; return; }
    var steps = (items[0] && Array.isArray(items[0].milestones)) ? items[0].milestones : [];
    var headCells = steps.map(function (m, i) {
      return '<div class="ftg-h" style="--step-i:' + i + '"><span>' + esc(m.label) + '</span></div>';
    }).join("");
    var rows = items.map(suspectRowHtml).join("");
    var more = asNum(s.total) > asNum(s.shown)
      ? '<div class="ftg-more">+ ' + (asNum(s.total) - asNum(s.shown)) + ' more · newest ' + asNum(s.shown) + ' shown</div>' : "";
    host.innerHTML =
      '<div class="kpi-temu-flag-drawer' + (suspectsOpen ? " is-open" : "") + '">' +
        '<button type="button" class="kpi-temu-flag-bar" data-temu-action="toggle-suspects" aria-expanded="' + suspectsOpen + '">' +
          '<span class="kpi-temu-flag-warn" aria-hidden="true">⚠</span>' +
          '<span class="kpi-temu-flag-title">Flagged records</span>' +
          '<span class="kpi-temu-flag-count">' + asNum(s.total) + '</span>' +
          '<span class="kpi-temu-flag-sub">full milestone chain · the flagged step is excluded from the averages · fix at source</span>' +
        '</button>' +
        '<div class="kpi-temu-flag-wrap' + (suspectsOpen ? "" : " is-collapsed") + '">' +
          '<div class="ftg">' +
            '<div class="ftg-row ftg-head"><div class="ftg-id ftg-h-id">Shipment</div>' + headCells + '</div>' +
            rows +
          '</div>' + more +
        '</div>' +
      '</div>';
    var flagDrawer = host.querySelector(".kpi-temu-flag-drawer");
    var flagBar = host.querySelector(".kpi-temu-flag-bar");
    if (flagDrawer && flagBar && !flagDrawer.querySelector(".kpi-temu-flag-export")) {
      var exportBtn = document.createElement("button");
      exportBtn.type = "button";
      exportBtn.className = "kpi-temu-export kpi-temu-flag-export";
      exportBtn.setAttribute("data-temu-action", "export-suspects-xlsx");
      exportBtn.textContent = "Flagged XLSX";
      flagBar.parentNode.insertBefore(exportBtn, flagBar.nextSibling);
    }
  }

  // ── count-ups + fill ─────────────────────────────────────────────────────
  function setDur(el, hours) {
    if (!el) return;
    if (el.classList && el.classList.contains("kpi-temu-dur-split")) el.innerHTML = fmtDurHtml(hours);
    else el.textContent = fmtDur(hours);
  }
  function countUp(el, target, animate, delay) {
    if (!el) return;
    target = asNum(target);
    if (el.__temuRaf) { cancelAnimationFrame(el.__temuRaf); el.__temuRaf = 0; }
    if (!animate || reduceMotion()) { setDur(el, target); return; }
    var fromVal = asNum(el.__temuVal != null ? el.__temuVal : 0);
    if (fromVal === target) { setDur(el, target); el.__temuVal = target; return; }
    var dur = 620, start = 0, base = 0;
    function step(ts) {
      if (!start) { start = ts; base = ts + (delay || 0); }
      if (ts < base) { el.__temuRaf = requestAnimationFrame(step); return; }
      var t = Math.min(1, (ts - base) / dur), e = 1 - Math.pow(1 - t, 4);
      setDur(el, fromVal + (target - fromVal) * e);
      if (t < 1) el.__temuRaf = requestAnimationFrame(step); else { el.__temuRaf = 0; setDur(el, target); el.__temuVal = target; }
    }
    el.__temuVal = fromVal;
    el.__temuRaf = requestAnimationFrame(step);
  }
  function runCountsIn(root, animate, sel) {
    if (!root) return;
    var els = Array.prototype.slice.call(root.querySelectorAll(sel));
    els.forEach(function (el, i) { countUp(el, Number(el.getAttribute("data-cu")) || 0, animate, 80 + i * 45); });
  }
  function runCounts(root, animate) {
    if (!root) return;
    var sig = Array.prototype.slice.call(root.querySelectorAll(".kpi-temu-cu[data-cu], .kpi-temu-col-total[data-cu]"))
      .map(function (el) { return el.getAttribute("data-cu") || "0"; }).join("|") + "/" + mode + "/" + scope + "/" + focusFrom + "/" + focusTo + "/" + focusedStage;
    if (sig === lastCountSig) return;
    lastCountSig = sig;
    runCountsIn(root, animate, ".kpi-temu-cu[data-cu], .kpi-temu-col-total[data-cu]");
  }
  function runCountsChart(root, animate) { runCountsIn(root, animate, ".kpi-temu-col-total[data-cu]"); }
  function playFill(root, animate) {
    if (!root) return;
    var bars = root.querySelectorAll(".kpi-temu-bar");
    if (!animate || reduceMotion()) { bars.forEach(function (el) { el.style.transition = "none"; void el.offsetWidth; el.style.transition = ""; }); return; }
    bars.forEach(function (el) { el.dataset.h = el.style.height; el.style.transition = "none"; el.style.height = "0%"; });
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      bars.forEach(function (el, i) { el.style.transition = ""; el.style.transitionDelay = Math.min(i * 40, 560) + "ms"; el.style.height = el.dataset.h || el.style.height; });
    }); });
  }

  // ── reveal ─────────────────────────────────────────────────────────────────
  // On the KPI page the snap drives navigation: reveal ONLY the panel the user has
  // arrived on (active) — not on initial layout (when everything is briefly "in view"),
  // not while passing through. The kpi-segment-settled event drives the single, clean
  // opening. Off the KPI page (no view-kpi-mode), reveal freely.
  function mayRevealNow(section) {
    var b = document.body;
    if (!b || !b.classList.contains("view-kpi-mode")) return true;
    return !!(section && section.classList.contains("is-kpi-snap-active"));
  }
  function revealSection(section) {
    if (!section || revealed) return;
    revealed = true; revealArmed = false; lastOpenTs = Date.now();
    section.classList.remove("kpi-reveal-pending"); section.classList.add("is-revealed", "is-opening");
    if (openingTimer) clearTimeout(openingTimer);
    openingTimer = setTimeout(function () { section.classList.remove("is-opening"); openingTimer = 0; }, 2000);
    playFill(section, true); runCounts(section, true);
  }
  function ensureRevealObserver() {
    if (revealObserver || typeof IntersectionObserver === "undefined") return revealObserver;
    revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!halfVisible(entry)) return;
        // Don't reveal while a snap is animating, nor a panel that isn't the one the
        // user has arrived on — the settle event plays it once, cleanly. (Avoids the
        // chart popping in on load, then re-animating on settle.)
        if (window.__kpiSnapActive || !mayRevealNow(entry.target)) return;
        revealObserver.unobserve(entry.target); revealSection(entry.target);
      });
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    return revealObserver;
  }
  function armReveal() {
    var section = document.getElementById("kpi-temu-section");
    if (!section) return;
    if (reduceMotion() || revealed) { revealed = true; section.classList.add("is-revealed"); playFill(section, false); runCounts(section, false); return; }
    if (revealArmed) return;
    revealArmed = true; section.classList.add("kpi-reveal-pending");
    var obs = ensureRevealObserver();
    if (!obs || (elementInView(section) && !window.__kpiSnapActive && mayRevealNow(section))) setTimeout(function () { revealSection(section); }, 80); else obs.observe(section);
  }

  function render(animateIfVisible) {
    var section = document.getElementById("kpi-temu-section");
    var chartHost = document.getElementById("kpi-temu-chart");
    var subEl = document.getElementById("kpi-temu-subtitle");
    if (!section || !chartHost) return;
    ensureFocus();
    section.classList.toggle("is-comparing", compare);
    renderControls(); renderRange(); renderLegend();
    var list = periods();
    if (subEl) subEl.textContent = "Average stage durations by ATA " + (mode === "day" ? "day" : "week") + " · " + scopeLabel(scope) +
      (focusedStage ? (" · Focus: " + stageDisplayLabel(stageByKey(focusedStage))) : "") + (list.length ? "" : " · no data") + qualitySuffix();
    if (!list.length) {
      var sh = document.getElementById("kpi-temu-summary"); if (sh) sh.innerHTML = "";
      chartHost.innerHTML = '<div class="kpi-temu-empty">No TEMU stage-duration data.</div>';
      renderPanel(); var th = document.getElementById("kpi-temu-table"); if (th) th.innerHTML = "";
      renderSuspects();   // the flagged list can still matter when nothing is measurable
      section.classList.add("is-revealed"); return;
    }
    renderSummary(); chartHost.innerHTML = chartHtml(focusedList()); renderPanel(); renderTable(); renderSuspects();
    if (revealed) { playFill(section, !!animateIfVisible); runCounts(section, !!animateIfVisible); } else armReveal();
  }
  function rerender(animate) { lastRenderSig = ""; lastCountSig = ""; render(animate); }
  // Focus / stage-focus change: re-render ONLY the diagram + nav, patch card values.
  function focusUpdate() {
    renderRange(); renderDates(); renderLegend(); renderChartOnly(false); patchSummary(true); renderTable(); renderSuspects();
  }
  // Stage filter (e.g. NOA-Transfer): the OTHER stage colours slide down out of the
  // bars and fade away, then the chosen stage's bars rise up from the baseline. Pure
  // transform/opacity + one height transition → impressive but cheap on weak machines.
  function applyStageFocus(stKey) {
    var newStage = (focusedStage === stKey) ? null : stKey;
    var chartHost = document.getElementById("kpi-temu-chart");
    if (reduceMotion() || !chartHost || !chartHost.querySelector(".kpi-temu-seg")) {
      focusedStage = newStage; focusUpdate(); return;
    }
    chartHost.querySelectorAll(".kpi-temu-col .kpi-temu-seg").forEach(function (seg) {
      var survive = !newStage || seg.classList.contains("seg-" + newStage);
      seg.classList.add(survive ? "seg-morph-stay" : "seg-morph-drop");
    });
    chartHost.classList.add("is-stage-morph");
    setTimeout(function () {
      focusedStage = newStage;
      renderRange(); renderDates(); renderLegend(); renderChartOnly(false); patchSummary(true); renderTable(); renderSuspects();
      var ch = document.getElementById("kpi-temu-chart");
      if (!ch) return;
      ch.classList.remove("is-stage-morph");
      var bars = ch.querySelectorAll(".kpi-temu-bar");
      bars.forEach(function (b) { b.dataset.rh = b.style.height; b.style.transition = "none"; b.style.height = "0%"; });
      ch.classList.add("is-stage-rise");
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        bars.forEach(function (b, i) { b.style.transition = ""; b.style.transitionDelay = Math.min(i * 24, 300) + "ms"; b.style.height = b.dataset.rh || b.style.height; });
      }); });
      setTimeout(function () { ch.classList.remove("is-stage-rise"); }, 840);
    }, 250);
  }
  function playSwap() {
    if (reduceMotion()) return;
    ["kpi-temu-summary", "kpi-temu-chart", "kpi-temu-table"].forEach(function (id) {
      var el = document.getElementById(id); if (!el) return;
      el.classList.remove("kpi-temu-swap"); void el.offsetWidth; el.classList.add("kpi-temu-swap");
    });
  }

  // ── interaction ────────────────────────────────────────────────────────────
  function setRange(from, to) { focusFrom = from; focusTo = to; selectedKey = null; focusUpdate(); }
  function setSelected(key) { selectedKey = (selectedKey === key) ? null : key; focusUpdate(); }
  document.addEventListener("click", function (e) {
    var sec = e.target.closest && e.target.closest("#kpi-temu-section");
    if (!sec) return;
    var stageBtn = e.target.closest("[data-temu-stage-focus]");
    if (stageBtn) { applyStageFocus(stageBtn.getAttribute("data-temu-stage-focus")); return; }
    var fkBtn = e.target.closest("[data-temu-focus-key]");
    if (fkBtn && !fkBtn.disabled) { setSelected(fkBtn.getAttribute("data-temu-focus-key")); return; }
    var viewBtn = e.target.closest("[data-temu-view]");
    if (viewBtn) { var m = viewBtn.getAttribute("data-temu-view"); if (m && m !== mode) { mode = m; focusFrom = focusTo = null; cmpA = cmpB = null; cmpTarget = "a"; rerender(false); playSwap(); } return; }
    var chip = e.target.closest("[data-temu-scope]");
    if (chip) { var scv = chip.getAttribute("data-temu-scope"); if (scv === scope && scv !== "ALL") scv = "ALL"; if (scv !== scope) { scope = scv; cmpA = cmpB = null; cmpTarget = "a"; rerender(false); playSwap(); } return; }
    var action = e.target.closest("[data-temu-action]");
    if (action) {
      var act = action.getAttribute("data-temu-action");
      if (act === "preset") { applyPreset(action.getAttribute("data-value")); selectedKey = null; focusUpdate(); }
      else if (act === "toggle-compare") {
        // Entering/leaving compare must NOT replay the bar fill — only re-skin the chart.
        compare = !compare; cmpA = cmpB = null; cmpTarget = "a";
        sec.classList.toggle("is-comparing", compare);
        renderControls(); renderRange(); renderLegend(); renderChartOnly(false); renderPanel();
      }
      else if (act === "cmp-target-a") { cmpTarget = "a"; renderPanel(); }
      else if (act === "cmp-target-b") { cmpTarget = "b"; renderPanel(); }
      else if (act === "cmp-clear-a") { e.stopPropagation(); cmpA = null; cmpTarget = "a"; renderChartOnly(false); renderPanel(); }
      else if (act === "cmp-clear-b") { e.stopPropagation(); cmpB = null; cmpTarget = "b"; renderChartOnly(false); renderPanel(); }
      else if (act === "cmp-swap") { var _t = cmpA; cmpA = cmpB; cmpB = _t; renderChartOnly(false); renderPanel(); }
      else if (act === "toggle-table") { tableOpen = !tableOpen; renderTable(); var d = document.querySelector(".kpi-temu-table-drawer"); if (d && !reduceMotion()) { d.classList.remove("kpi-temu-swap"); void d.offsetWidth; d.classList.add("kpi-temu-swap"); } }
      else if (act === "toggle-suspects") { suspectsOpen = !suspectsOpen; renderSuspects(); var fd = document.querySelector(".kpi-temu-flag-drawer"); if (fd && !reduceMotion()) { fd.classList.remove("kpi-temu-swap"); void fd.offsetWidth; fd.classList.add("kpi-temu-swap"); } }
      else if (act === "export-xlsx") { e.preventDefault(); exportXlsx(action, lastExport); }
      else if (act === "export-suspects-xlsx") { e.preventDefault(); e.stopPropagation(); exportXlsx(action, lastFlagExport); }
      else if (act === "clear-compare") { cmpA = cmpB = null; cmpTarget = "a"; renderChartOnly(false); renderPanel(); renderRange(); }
      return;
    }
  });
  document.addEventListener("change", function (e) {
    var inp = e.target && e.target.closest && e.target.closest("[data-temu-date]");
    if (!inp || !inp.closest("#kpi-temu-section")) return;
    var fromEl = document.querySelector('#kpi-temu-section [data-temu-date="from"]');
    var toEl = document.querySelector('#kpi-temu-section [data-temu-date="to"]');
    applyDateRange(fromEl && fromEl.value, toEl && toEl.value);
    selectedKey = null;
    focusUpdate();
  });

  function colIndexFromEvent(e) { var el = e.target.closest && e.target.closest("[data-temu-i]"); return el ? parseInt(el.getAttribute("data-temu-i"), 10) : -1; }
  function paintDrag() {
    var cols = document.querySelectorAll("#kpi-temu-section .kpi-temu-col");
    var lo = Math.min(dragStart, dragEnd), hi = Math.max(dragStart, dragEnd);
    cols.forEach(function (c, i) { c.classList.toggle("in-brush", dragMoved && i >= lo && i <= hi); });
  }
  document.addEventListener("pointerdown", function (e) {
    var cols = e.target.closest && e.target.closest("#kpi-temu-section .kpi-temu-cols");
    if (!cols) return;
    var i = colIndexFromEvent(e); if (i < 0) return;
    dragging = true; dragStart = i; dragEnd = i; dragMoved = false;
  });
  document.addEventListener("pointermove", function (e) {
    if (dragging) { var i = colIndexFromEvent(e); if (i >= 0) { if (i !== dragStart) dragMoved = true; dragEnd = i; paintDrag(); } }
    if (e.target.closest && e.target.closest("#kpi-temu-section .kpi-temu-cols")) { var j = colIndexFromEvent(e); if (j >= 0) showTooltip(j); }
  });
  document.addEventListener("pointerup", function (e) {
    if (!dragging) return;
    dragging = false;
    var l = focusedList();
    var lo = Math.min(dragStart, dragEnd), hi = Math.max(dragStart, dragEnd);
    if (dragMoved && hi > lo && l[lo] && l[hi]) {
      if (compare) { setCmp(cmpTarget, l[lo].period_key, l[hi].period_key); renderChartOnly(false); renderPanel(); }
      else setRange(l[lo].period_key, l[hi].period_key);
    } else {
      var el = e.target.closest && e.target.closest("[data-temu-key]");
      if (el && el.closest("#kpi-temu-section .kpi-temu-cols")) {
        var key = el.getAttribute("data-temu-key");
        if (compare) { setCmp(cmpTarget, key, key); renderChartOnly(false); renderPanel(); }
        else setSelected(key);
      }
    }
    dragStart = dragEnd = -1; dragMoved = false;
    document.querySelectorAll("#kpi-temu-section .kpi-temu-col.in-brush").forEach(function (c) { c.classList.remove("in-brush"); });
  });
  function showTooltip(idx) {
    var tip = document.getElementById("kpi-temu-tooltip");
    if (!tip) return;
    var list = focusedList(), p = list[idx];
    if (!p) { tip.classList.remove("is-on"); return; }
    var lines = stages().map(function (s) {
      return '<div class="kpi-temu-tt-row" style="--seg-rgb:' + stageColor(s.key) + '"><i></i><span>' + esc(stageDisplayLabel(s)) + '</span><b>' + esc(fmtDur(asNum((p.segments || {})[s.key]))) + '</b></div>';
    }).join("");
    tip.innerHTML = '<div class="kpi-temu-tt-head">' + esc(p.key) + ' · ' + esc(p.count) + ' pcs</div>' + lines + '<div class="kpi-temu-tt-total"><span>Total</span><b>' + esc(fmtDur(p.total)) + '</b></div>';
    tip.classList.add("is-on");
    // Keep the popup fully inside the plot: clamp its centre by its own half-width.
    var plot = tip.parentNode.getBoundingClientRect();
    var colEl = document.querySelector('#kpi-temu-section .kpi-temu-col[data-temu-i="' + idx + '"]');
    if (colEl) {
      var r = colEl.getBoundingClientRect();
      var w = tip.offsetWidth || 196;
      var c = r.left - plot.left + r.width / 2;
      c = Math.max(w / 2 + 8, Math.min(plot.width - w / 2 - 8, c));
      tip.style.left = c + "px";
    }
  }
  document.addEventListener("pointerover", function (e) {
    var cols = e.target.closest && e.target.closest("#kpi-temu-section .kpi-temu-cols");
    if (!cols) return;
    var i = colIndexFromEvent(e); if (i >= 0) showTooltip(i);
  });
  document.addEventListener("pointerout", function (e) {
    var cols = e.target.closest && e.target.closest("#kpi-temu-section .kpi-temu-cols");
    if (!cols) return;
    if (e.relatedTarget && cols.contains(e.relatedTarget)) return;
    var tip = document.getElementById("kpi-temu-tooltip"); if (tip) tip.classList.remove("is-on");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var col = e.target.closest && e.target.closest("#kpi-temu-section .kpi-temu-col[data-temu-key]");
    if (!col) return;
    e.preventDefault();
    var key = col.getAttribute("data-temu-key");
    if (compare) { setCmp(cmpTarget, key, key); renderChartOnly(false); renderPanel(); }
    else setSelected(key);
  });

  // ── XLSX export ──────────────────────────────────────────────────────────
  function downloadBlob(blob, filename) {
    var url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = filename || "temu_kpi.xlsx";
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 0);
  }
  function filenameFromDisposition(header) {
    var fallback = "temu_kpi.xlsx";
    if (!header) return fallback;
    var utf = header.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf && utf[1]) { try { return decodeURIComponent(utf[1]); } catch (e) { return utf[1]; } }
    var plain = header.match(/filename="?([^";]+)"?/i);
    return plain && plain[1] ? plain[1] : fallback;
  }
  function exportXlsx(btn, exportPayload) {
    exportPayload = exportPayload || lastExport;
    var hasTableRows = exportPayload && Array.isArray(exportPayload.rows) && exportPayload.rows.length;
    var hasFlagRows = exportPayload && Array.isArray(exportPayload.items) && exportPayload.items.length;
    if (!hasTableRows && !hasFlagRows) return;
    var original = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.classList.add("is-loading"); btn.textContent = "Export..."; }
    fetch("/kpi-temu-export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(exportPayload) })
      .then(function (res) {
        if (!res.ok) return res.text().then(function (txt) { var msg = "Export failed"; try { var p = JSON.parse(txt); if (p && p.error) msg = p.error; } catch (e) { if (txt) msg = txt.slice(0, 240); } throw new Error(msg); });
        var filename = filenameFromDisposition(res.headers.get("Content-Disposition"));
        return res.blob().then(function (blob) { return { blob: blob, filename: filename }; });
      }).then(function (out) {
        downloadBlob(out.blob, out.filename);
        if (btn) { btn.classList.remove("is-loading"); btn.classList.add("is-done"); btn.textContent = "Downloaded"; setTimeout(function () { btn.classList.remove("is-done"); btn.disabled = false; btn.textContent = original || "XLSX export"; }, 900); }
      }).catch(function (err) {
        try { console.warn("TEMU XLSX export failed:", err && err.message ? err.message : err); } catch (e) {}
        if (btn) { btn.classList.remove("is-loading"); btn.classList.add("is-error"); btn.title = err && err.message ? err.message : "XLSX export error"; btn.textContent = "Error"; setTimeout(function () { btn.classList.remove("is-error"); btn.disabled = false; btn.title = ""; btn.textContent = original || "XLSX export"; }, 1200); }
      });
  }

  function signature() {
    var list = periods();
    var q = quality(), s = suspects();
    return [mode, scope, compare, focusFrom, focusTo, selectedKey, focusedStage, JSON.stringify(cmpA), JSON.stringify(cmpB), cmpTarget, tableOpen, suspectsOpen, countries().join(","), lmps().join(","),
      asNum(q.corrected_fields), asNum(q.blocked_gaps), asNum(q.guarded_rows), asNum(s.total), asNum(s.field_total),
      list.map(function (p) { return p.period_key + ":" + Math.round(asNum(p.total) * 60) + ":" + p.count + (p.is_today ? "T" : ""); }).join("|")
    ].join("/");
  }
  window.__renderKpiTemu = function (data) {
    payload = data || {};
    var validScopes = ["ALL"].concat(countries()).concat(lmps());
    if (scope !== "ALL" && validScopes.indexOf(scope) < 0) { scope = "ALL"; focusFrom = focusTo = null; selectedKey = null; }
    if (selectedKey != null && !periodByKey(selectedKey)) selectedKey = null;
    var sig = signature();
    if (sig === lastRenderSig && document.querySelector(".kpi-temu-col")) { armReveal(); return; }
    lastRenderSig = sig; lastCountSig = "";
    render(revealed);
  };
  // Play the staggered column opening exactly ONCE — the first time the user
  // lands on this panel — then never again. `revealed` is the latch and is NEVER
  // reset here, so scrolling away and back does NOT re-animate the columns (that
  // replay-on-every-return was the old behaviour). Two triggers cover both ways
  // of arriving, both guarded by `revealed`:
  //   • kpi-view-entered — the report subpage was just shown (scrolled to top,
  //     TEMU visible). Open immediately; we deliberately do NOT wait for the snap
  //     to mark the panel "active" — that race (the class lands a frame later, and
  //     the IntersectionObserver never re-fires for it) is why the opening used to
  //     start only after a full scroll to the bottom and back.
  //   • kpi-segment-settled — arriving at TEMU by scrolling in from another panel.
  function openOnce() {
    var section = document.getElementById("kpi-temu-section");
    if (!section || revealed || !document.querySelector(".kpi-temu-col")) return;
    if (!elementInView(section)) return;   // hidden subpage / not on screen yet
    if (reduceMotion()) {
      revealed = true; section.classList.add("is-revealed");
      playFill(section, false); runCounts(section, false); return;
    }
    revealSection(section);
  }
  window.addEventListener("kpi-view-entered", function () { armReveal(); openOnce(); });
  window.addEventListener("kpi-segment-settled", function (e) {
    if (e && e.detail && e.detail.id === "kpi-temu-section") openOnce();
  });
})();
