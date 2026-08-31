/* KPI page - time-band section.
 *
 * Chart: hourly columns inside the four operational bands.
 * Detail cards: always the four operational bands; selected chart hours roll up
 * into their parent band card. Selection changes never replay the opening.
 */
(function () {
  "use strict";

  var payload = null;
  var metric = "kg";
  var selected = Object.create(null);
  var dayIndex = 0;
  var lastRenderSig = "";
  var lastCountSig = "";
  var revealObserver = null;
  var revealed = false;
  var revealArmed = false;
  var openingTimer = 0;
  var lastOpenTs = 0;
  var fillSettleTimer = 0;

  var DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  function esc(v) {
    return String(v == null ? "" : v)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function asNum(v) {
    var n = Number(v);
    return isFinite(n) ? n : 0;
  }

  function group(n) {
    return String(Math.round(asNum(n))).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }

  function unit() {
    if (metric === "colli") return "COLLI";
    if (metric === "parcel") return "PARCEL";
    return "KG";
  }

  function fmtFull(n) {
    return group(n) + " " + unit();
  }

  function fmtValueOnly(n) {
    return group(n);
  }

  function fmtCompact(n) {
    n = asNum(n);
    if (n >= 1000000) return (n / 1000000).toFixed(1).replace(".0", "") + "M";
    if (n >= 10000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(".0", "") + "k";
    return n ? group(n) : "";
  }

  function pct(part, whole) {
    whole = asNum(whole);
    if (whole <= 0) return 0;
    return Math.max(0, Math.min(100, asNum(part) / whole * 100));
  }

  function barPct(part, whole) {
    var p = pct(part, whole);
    return asNum(part) > 0 ? Math.max(4, p) : 0;
  }

  function reduceMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function elementInView(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    var h = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!h || r.height <= 0) return false;
    // Only animate once at least half the section is visible (or, when taller than
    // the viewport, once its visible slice dominates the viewport).
    var visible = Math.max(0, Math.min(r.bottom, h) - Math.max(r.top, 0));
    return visible >= r.height * 0.5 || visible >= h * 0.55;
  }
  function halfVisible(entry) {
    if (!entry || !entry.isIntersecting) return false;
    if (entry.intersectionRatio >= 0.5) return true;
    var ir = entry.intersectionRect;
    var th = (entry.rootBounds && entry.rootBounds.height) ||
      window.innerHeight || document.documentElement.clientHeight || 0;
    return !!(th && ir && ir.height >= th * 0.55);
  }

  function dateLabel(dateStr) {
    var parts = String(dateStr || "").split("-");
    if (parts.length !== 3) return "";
    var d = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    if (isNaN(d.getTime())) return parts.join(".");
    return parts[0] + "." + parts[1] + "." + parts[2] + " / " + DAY_NAMES[d.getDay()];
  }

  function numOrNull(v) {
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function padHour(h) {
    return String(Number(h) || 0).padStart(2, "0");
  }

  function metricKey() {
    if (metric === "colli") return "colli";
    if (metric === "parcel") return "parcel";
    return "kg";
  }

  function copySideMetric(src, dst, side, key, allowGeneric) {
    var directKey = side + "_" + key;
    var n = numOrNull(dst[directKey]);
    if (n == null) n = numOrNull(src && src[directKey]);
    if (n == null && src && typeof src[side] === "object") n = numOrNull(src[side][key]);
    if (n == null && allowGeneric) n = numOrNull(src && src[key]);
    dst[directKey] = n == null ? 0 : n;
  }

  function normalizeSide(src, dst, side, allowGeneric) {
    copySideMetric(src, dst, side, "kg", allowGeneric);
    copySideMetric(src, dst, side, "count", allowGeneric);
    copySideMetric(src, dst, side, "colli", allowGeneric);
    copySideMetric(src, dst, side, "parcel", allowGeneric);
  }

  function normalizeBand(src, role) {
    var out = Object.assign({}, src || {});
    normalizeSide(src, out, "inbound", role === "inbound" || !role);
    normalizeSide(src, out, "outbound", role === "outbound");
    if (Array.isArray(out.hours)) {
      out.hours = out.hours.map(function (h) { return normalizeBand(h, role); });
    }
    return out;
  }

  function bandRange(item) {
    var start = numOrNull(item && item.start);
    var end = numOrNull(item && item.end);
    if (start != null && end != null) return { start: start, end: end, key: padHour(start) + "-" + padHour(end) };
    var raw = String((item && (item.key || item.label)) || "");
    var m = raw.match(/(\d{1,2})\D+(\d{1,2})/);
    if (m) {
      start = Number(m[1]);
      end = Number(m[2]);
      if (end === 0 && start > 0) end = 24;
      return { start: start, end: end, key: padHour(start) + "-" + padHour(end) };
    }
    return null;
  }

  function listByRange(list) {
    var map = Object.create(null);
    (list || []).forEach(function (item) {
      var r = bandRange(item);
      if (r) map[r.key] = item;
    });
    return map;
  }

  function mergeLegacyHourLists(inBand, outBand, h0, h1) {
    var inHours = listByRange((inBand && inBand.hours) || []);
    var outHours = listByRange((outBand && outBand.hours) || []);
    var hours = [];
    for (var h = h0; h < h1; h++) {
      var key = padHour(h) + "-" + padHour(h + 1);
      var ih = inHours[key] || {};
      var oh = outHours[key] || {};
      var merged = {
        key: key,
        label: (ih.label || oh.label || key),
        start: h,
        end: h + 1,
        shift: (ih.shift || oh.shift || ((h >= 8 && h < 20) ? "day" : "night")),
        active: !!(ih.active || oh.active || inBand && inBand.active || outBand && outBand.active),
        current: !!(ih.current || oh.current),
      };
      normalizeSide(ih, merged, "inbound", true);
      normalizeSide(oh, merged, "outbound", true);
      hours.push(merged);
    }
    return hours;
  }

  function mergeLegacyBands(inbound, outbound) {
    var inMap = listByRange(inbound || []);
    var outMap = listByRange(outbound || []);
    var ranges = {};
    [[0, 4], [4, 8], [8, 12], [12, 16], [16, 20], [20, 24]].forEach(function (r) {
      ranges[padHour(r[0]) + "-" + padHour(r[1])] = { start: r[0], end: r[1] };
    });
    Object.keys(inMap).concat(Object.keys(outMap)).forEach(function (key) {
      var r = bandRange(inMap[key] || outMap[key]);
      if (r) ranges[r.key] = { start: r.start, end: r.end };
    });
    return Object.keys(ranges).map(function (key) {
      var r = ranges[key];
      var ib = inMap[key] || {};
      var ob = outMap[key] || {};
      var band = {
        key: key,
        label: ib.label || ob.label || key,
        start: r.start,
        end: r.end,
        shift: ib.shift || ob.shift || ((r.start >= 8 && r.start < 20) ? "day" : "night"),
        active: !!(ib.active || ob.active),
        current: !!(ib.current || ob.current),
      };
      normalizeSide(ib, band, "inbound", true);
      normalizeSide(ob, band, "outbound", true);
      band.hours = mergeLegacyHourLists(ib, ob, r.start, r.end);
      return band;
    }).sort(function (a, b) { return asNum(a.start) - asNum(b.start); });
  }

  function normalizeDay(day) {
    if (!day || typeof day !== "object") return day;
    if (Array.isArray(day.bands)) {
      var cloned = Object.assign({}, day);
      cloned.bands = day.bands.map(function (band) { return normalizeBand(band); });
      return cloned;
    }
    var inbound = day.inbound || day.time_bands_inbound;
    var outbound = day.outbound || day.time_bands_outbound;
    if (Array.isArray(inbound) || Array.isArray(outbound)) {
      return Object.assign({}, day, {
        bands: mergeLegacyBands(inbound || [], outbound || []),
      });
    }
    return day;
  }

  function normalizePayload(data) {
    if (!data || typeof data !== "object") return {};
    var out = Object.assign({}, data);
    if (Array.isArray(data.days) && data.days.length) {
      out.days = data.days.map(normalizeDay);
      var idx = numOrNull(out.today_index);
      if (idx != null && out.days[idx]) {
        out.bands = out.days[idx].bands;
        out.date = out.days[idx].date || out.date;
        out.weekday = out.days[idx].weekday || out.weekday;
      }
      return out;
    }
    return normalizeDay(out);
  }

  function days() {
    if (payload && Array.isArray(payload.days) && payload.days.length) return payload.days;
    return payload ? [payload] : [];
  }

  function clampDayIndex() {
    var list = days();
    if (!list.length) {
      dayIndex = 0;
      return 0;
    }
    dayIndex = Math.max(0, Math.min(list.length - 1, Number(dayIndex) || 0));
    return dayIndex;
  }

  function currentDay() {
    var list = days();
    if (!list.length) return payload || {};
    return list[clampDayIndex()] || list[list.length - 1] || {};
  }

  function todayIndex() {
    var list = days();
    var idx = Number(payload && payload.today_index);
    if (isFinite(idx) && idx >= 0 && idx < list.length) return idx;
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].is_today) return i;
    }
    return Math.max(0, list.length - 1);
  }

  function updateDayNav() {
    var list = days();
    var idx = clampDayIndex();
    var label = document.getElementById("kpi-bands-day-label");
    var select = document.getElementById("kpi-bands-day-select");
    var prev = document.querySelector('[data-band-day="prev"]');
    var next = document.querySelector('[data-band-day="next"]');
    var day = currentDay();
    if (label) {
      label.innerHTML = day.is_today ? '<b>Today</b>' : '<b class="is-history">Previous</b>';
    }
    if (select) {
      var options = list.map(function (item, i) {
        var text = dateLabel(item.date) || item.date || ("Day " + (i + 1));
        if (item.is_today) text += " / Today";
        return '<option value="' + i + '">' + esc(text) + '</option>';
      }).join("");
      if (select.innerHTML !== options) select.innerHTML = options;
      select.value = String(idx);
      select.disabled = list.length <= 1;
      select.classList.toggle("is-disabled", list.length <= 1);
    }
    if (prev) {
      prev.disabled = idx <= 0;
      prev.classList.toggle("is-disabled", idx <= 0);
    }
    if (next) {
      next.disabled = idx >= list.length - 1;
      next.classList.toggle("is-disabled", idx >= list.length - 1);
    }
  }

  function rangeLabel(item) {
    var start = item && item.start;
    var end = item && item.end;
    if (start != null && end != null) {
      return String(start).padStart(2, "0") + "-" + String(end).padStart(2, "0") + "h";
    }
    return String((item && item.label) || "").replace(/[–—]/g, "-") + "h";
  }

  function hourTickLabelRaw(item) {
    var start = Number(item && item.start);
    if (isFinite(start)) return (start % 24) + ":00";
    var label = String((item && item.label) || "").replace(/[â€“â€”]/g, "-");
    var m = label.match(/^0?(\d{1,2})(?:-|h|:)/i);
    return m ? (Number(m[1]) % 24) + ":00" : label;
  }

  function hourTickLabel(item) {
    var start = Number(item && item.start);
    if (isFinite(start)) return (start % 24) + ":00";
    var label = String((item && item.label) || "");
    var m = label.match(/^0?(\d{1,2})/);
    return m ? (Number(m[1]) % 24) + ":00" : label;
  }

  function val(item, side) {
    var key = metricKey();
    var direct = numOrNull(item && item[side + "_" + key]);
    if (direct != null) return direct;
    if (item && typeof item[side] === "object") {
      direct = numOrNull(item[side][key]);
      if (direct != null) return direct;
    }
    return side === "inbound" ? asNum(item && item[key]) : 0;
  }

  function total(item) {
    return val(item, "inbound") + val(item, "outbound");
  }

  function selectionKeys() {
    return Object.keys(selected).filter(function (key) { return selected[key]; }).sort();
  }

  function selectionCount() {
    return selectionKeys().length;
  }

  function hasSelection() {
    return selectionCount() > 0;
  }

  function selectedSig() {
    return selectionKeys().join(",");
  }

  function flattenHours(bands) {
    var hours = [];
    (bands || []).forEach(function (band) {
      var parentLabel = rangeLabel(band);
      var source = Array.isArray(band.hours) && band.hours.length ? band.hours : [band];
      source.forEach(function (hour) {
        var h = Object.assign({}, hour);
        h.parent_key = band.key;
        h.parent_label = parentLabel;
        h.key = h.key || band.key;
        h.label = rangeLabel(h);
        h.active = !!h.active;
        h.current = !!h.current;
        hours.push(h);
      });
    });
    return hours;
  }

  function sumItems(items) {
    return (items || []).reduce(function (acc, item) {
      acc.inbound_kg += asNum(item.inbound_kg);
      acc.inbound_colli += asNum(item.inbound_colli);
      acc.inbound_parcel += asNum(item.inbound_parcel);
      acc.outbound_kg += asNum(item.outbound_kg);
      acc.outbound_colli += asNum(item.outbound_colli);
      acc.outbound_parcel += asNum(item.outbound_parcel);
      return acc;
    }, {
      inbound_kg: 0, inbound_colli: 0, inbound_parcel: 0,
      outbound_kg: 0, outbound_colli: 0, outbound_parcel: 0
    });
  }

  function bandDetail(band) {
    var hours = Array.isArray(band.hours) && band.hours.length ? band.hours : [band];
    var selectedHours = hours.filter(function (h) { return !!selected[h.key]; });
    var usingSelection = hasSelection();
    var source = usingSelection ? selectedHours : hours;
    var fullSums = sumItems(hours);
    var sums = sumItems(source);
    sums.key = band.key;
    sums.label = rangeLabel(band);
    sums.start = band.start;
    sums.end = band.end;
    sums.shift = band.shift || (band.start >= 8 && band.start < 20 ? "day" : "night");
    sums.active = !!band.active;
    sums.current = !!band.current;
    sums.selected_count = selectedHours.length;
    sums.is_muted = usingSelection && !selectedHours.length;
    sums.full_inbound_kg = fullSums.inbound_kg;
    sums.full_inbound_colli = fullSums.inbound_colli;
    sums.full_inbound_parcel = fullSums.inbound_parcel;
    sums.full_outbound_kg = fullSums.outbound_kg;
    sums.full_outbound_colli = fullSums.outbound_colli;
    sums.full_outbound_parcel = fullSums.outbound_parcel;
    sums.scope_label = usingSelection
      ? (selectedHours.length ? selectedHours.map(hourTickLabel).join(", ") : "No selected hour")
      : "Full band";
    return sums;
  }

  function signature(p) {
    var day = currentDay();
    var bands = (day && day.bands) || [];
    var hours = flattenHours(bands);
    return [
      day && day.date,
      dayIndex,
      metric,
      selectedSig(),
      hours.map(function (h) {
        return [
          h.key,
          Math.round(asNum(h.inbound_kg)),
          Math.round(asNum(h.inbound_colli)),
          Math.round(asNum(h.inbound_parcel)),
          Math.round(asNum(h.outbound_kg)),
          Math.round(asNum(h.outbound_colli)),
          Math.round(asNum(h.outbound_parcel)),
          h.current ? 1 : 0,
          h.active ? 1 : 0
        ].join(":");
      }).join("|")
    ].join("/");
  }

  function countUp(el, target, fmt, animate, delay) {
    if (!el) return;
    target = asNum(target);
    if (el.__kpiBandsRaf) {
      cancelAnimationFrame(el.__kpiBandsRaf);
      el.__kpiBandsRaf = 0;
    }
    if (!animate || reduceMotion() || target === 0) {
      el.textContent = fmt(target);
      return;
    }
    var duration = 760;
    var start = 0;
    var base = 0;
    function step(ts) {
      if (!start) {
        start = ts;
        base = ts + (delay || 0);
      }
      if (ts < base) {
        el.__kpiBandsRaf = requestAnimationFrame(step);
        return;
      }
      var t = Math.min(1, (ts - base) / duration);
      var eased = 1 - Math.pow(1 - t, 4);
      el.textContent = fmt(target * eased);
      if (t < 1) {
        el.__kpiBandsRaf = requestAnimationFrame(step);
      } else {
        el.__kpiBandsRaf = 0;
        el.textContent = fmt(target);
      }
    }
    el.__kpiBandsRaf = requestAnimationFrame(step);
  }

  function summaryTotals(details) {
    function fullVal(item, side) {
      if (metric === "colli") return asNum(item["full_" + side + "_colli"]);
      if (metric === "parcel") return asNum(item["full_" + side + "_parcel"]);
      return asNum(item["full_" + side + "_kg"]);
    }
    var selectedDetails = hasSelection() ? details.filter(function (d) { return d.selected_count > 0; }) : details;
    if (!selectedDetails.length) selectedDetails = details;
    var info = selectedDetails.reduce(function (acc, d) {
      var inV = val(d, "inbound");
      var outV = val(d, "outbound");
      acc.inbound += inV;
      acc.outbound += outV;
      return acc;
    }, {
      inbound: 0, outbound: 0,
      peak: { label: "-", weight: 0 },
      peakIn: { label: "-", weight: 0 },
      peakOut: { label: "-", weight: 0 },
      lowIn: null, lowOut: null
    });
    details.forEach(function (d) {
      var inV = fullVal(d, "inbound");
      var outV = fullVal(d, "outbound");
      var weight = inV + outV;
      if (weight > info.peak.weight) info.peak = { label: d.label, weight: weight };
      // Peak / quietest cards stay fixed for the day: selecting/focusing an hour
      // must not recalculate them from the temporary selection.
      if (inV > info.peakIn.weight) info.peakIn = { label: d.label, weight: inV };
      if (outV > info.peakOut.weight) info.peakOut = { label: d.label, weight: outV };
      // Quietest = the band with the lowest NON-zero volume (the weakest active period).
      if (inV > 0 && (!info.lowIn || inV < info.lowIn.weight)) info.lowIn = { label: d.label, weight: inV };
      if (outV > 0 && (!info.lowOut || outV < info.lowOut.weight)) info.lowOut = { label: d.label, weight: outV };
    });
    if (!info.lowIn) info.lowIn = { label: "-", weight: 0 };
    if (!info.lowOut) info.lowOut = { label: "-", weight: 0 };
    return info;
  }

  function bandSelectionRanges(hours) {
    var byParent = Object.create(null);
    (hours || []).forEach(function (h, i) {
      var key = h.parent_key || h.key;
      if (!byParent[key]) byParent[key] = { key: key, label: h.parent_label, start: i, end: i, total: 0, selected: 0 };
      byParent[key].end = i;
      byParent[key].total += 1;
      if (selected[h.key]) byParent[key].selected += 1;
    });
    return Object.keys(byParent).map(function (key) { return byParent[key]; })
      .filter(function (item) { return item.total > 1 && item.selected === item.total; });
  }

  function chartHtml(hours, maxVal) {
    var activeSelection = hasSelection();
    var segmentRanges = bandSelectionRanges(hours);
    var segmentKeys = Object.create(null);
    segmentRanges.forEach(function (seg) { segmentKeys[seg.key] = true; });
    var cols = hours.map(function (h, i) {
      var inV = val(h, "inbound");
      var outV = val(h, "outbound");
      var isSegmentMember = !!segmentKeys[h.parent_key || h.key];
      var isSelected = !!selected[h.key] && !isSegmentMember;
      var muted = activeSelection && !isSelected;
      var bars = "";
      bars += '<button type="button" class="kpi-bands-bar inbound" data-h="' + barPct(inV, maxVal).toFixed(2) +
        '" style="height:0%;--band-i:' + i + '" tabindex="-1" aria-hidden="true">' +
        '<span class="kpi-bands-barval">' + esc(fmtCompact(inV)) + '</span></button>';
      bars += '<button type="button" class="kpi-bands-bar outbound" data-h="' + barPct(outV, maxVal).toFixed(2) +
        '" style="height:0%;--band-i:' + i + '" tabindex="-1" aria-hidden="true">' +
        '<span class="kpi-bands-barval">' + esc(fmtCompact(outV)) + '</span></button>';
      var hShift = h.shift || ((h.start >= 8 && h.start < 20) ? "day" : "night");
      return '<button type="button" class="kpi-bands-col' +
        (h.current ? " is-current" : "") +
        (h.active ? "" : " is-future") +
        (isSelected ? " is-selected" : "") +
        (isSegmentMember ? " is-segment-member" : "") +
        ((h.start % 4) === 0 ? " is-band-start" : "") +
        (hShift === "night" ? " is-night" : " is-day") +
        (muted && !isSegmentMember ? " is-muted" : "") +
        '" data-hour-key="' + esc(h.key) + '" title="' + esc(rangeLabel(h)) + '">' +
        '<span class="kpi-bands-band-chip">' + esc(h.parent_label) + '</span>' +
        '<span class="kpi-bands-bars">' + bars + '</span>' +
        '<span class="kpi-bands-col-label">' + esc(hourTickLabel(h)) +
        (h.current ? '<i class="kpi-bands-col-now">Now</i>' : "") + '</span>' +
        '</button>';
    }).join("");
    var segs = segmentRanges.map(function (seg) {
      var left = (seg.start / Math.max(1, hours.length) * 100).toFixed(4);
      var width = ((seg.end - seg.start + 1) / Math.max(1, hours.length) * 100).toFixed(4);
      return '<span class="kpi-bands-segment-ring" style="--seg-left:' + left + '%;--seg-width:' +
        width + '%" aria-hidden="true"><i>' + esc(seg.label || "") + '</i></span>';
    }).join("");

    return '<div class="kpi-bands-chart-inner">' +
      '<div class="kpi-bands-scale" aria-hidden="true"><i></i><i></i><i></i></div>' +
      '<div class="kpi-bands-cols">' + cols + segs + '</div>' +
      '<div class="kpi-bands-legend">' +
        '<span class="kpi-bands-leg in"><i></i>Inbound</span>' +
        '<span class="kpi-bands-leg out"><i></i>Outbound</span>' +
        '<span class="kpi-bands-leg-unit">' + esc(unit()) + '</span>' +
      '</div></div>';
  }

  function rowHtml(side, label, value, maxVal) {
    return '<div class="kpi-bands-row ' + side + '">' +
      '<span class="kpi-bands-row-label">' + esc(label) + '</span>' +
      '<div class="kpi-bands-row-bar"><i data-w="' + barPct(value, maxVal).toFixed(2) + '" style="width:0%"></i></div>' +
      '<span class="kpi-bands-row-val">' + esc(fmtFull(value)) + '</span>' +
      '</div>';
  }

  function gridHtml(details, maxVal, dayWeight) {
    return details.map(function (d, i) {
      var inV = val(d, "inbound");
      var outV = val(d, "outbound");
      var share = pct(total(d), dayWeight);
      var dShift = d.shift || ((d.start >= 8 && d.start < 20) ? "day" : "night");
      var shiftChip = '<span class="kpi-bands-shift-chip is-' + dShift + '">' +
        (dShift === "day" ? "Day" : "Night") + '</span>';
      return '<div class="kpi-bands-card' +
        (d.current ? " is-current" : "") +
        (d.active ? "" : " is-future") +
        (d.selected_count ? " is-selected" : "") +
        (d.is_muted ? " is-muted" : "") +
        " is-" + dShift +
        '" data-band-key="' + esc(d.key) + '" role="button" tabindex="0"' +
        ' title="Select this band" style="--band-card-i:' + i + '">' +
        '<div class="kpi-bands-card-head">' +
          '<span class="kpi-bands-card-range">' + esc(d.label) + '</span>' +
          shiftChip +
        '</div>' +
        rowHtml("inbound", "Inbound", inV, maxVal) +
        rowHtml("outbound", "Outbound", outV, maxVal) +
        '<div class="kpi-bands-card-foot">' +
          '<span class="kpi-bands-share">' + Math.round(share) + '% of day</span>' +
          (d.current ? '<span class="kpi-bands-card-now">Now</span>' : "") +
        '</div>' +
        '</div>';
    }).join("");
  }

  function summaryHtml(info) {
    var scope = hasSelection() ? selectionCount() + " selected hour" + (selectionCount() > 1 ? "s" : "") : "All bands";
    function valueHtml(value, cls) {
      return '<span class="kpi-bands-value' + (cls ? " " + cls : "") + '">' +
        '<b class="kpi-bands-cu" data-cu="' + value + '">' + esc(fmtValueOnly(value)) + '</b>' +
        '<small>' + esc(unit()) + '</small></span>';
    }
    function totalChip(side, label, value) {
      return '<div class="kpi-bands-chip ' + side + '"><span>' + esc(label) + '</span>' +
        valueHtml(value, "") + '<em>' + esc(scope) + '</em></div>';
    }
    // Grouped card: one category (Peak / Quietest) split into inbound + outbound rows.
    // Compact aligned mini-grid: side label · value (prominent) · hour-band.
    function groupRow(side, label, peak) {
      return '<div class="kpi-bands-grow ' + side + '">' +
        '<span class="kpi-bands-grow-label">' + esc(label) + '</span>' +
        valueHtml((peak && peak.weight) || 0, "kpi-bands-grow-val") +
        '<span class="kpi-bands-grow-when">' + esc((peak && peak.label) || "-") + '</span>' +
        '</div>';
    }
    function groupCard(cls, title, inPeak, outPeak) {
      return '<div class="kpi-bands-group ' + cls + '">' +
        '<span class="kpi-bands-group-title">' + esc(title) + '</span>' +
        groupRow("in", "Inbound", inPeak) +
        groupRow("out", "Outbound", outPeak) +
        '</div>';
    }
    return '' +
      totalChip("in", "Inbound", info.inbound) +
      totalChip("out", "Outbound", info.outbound) +
      groupCard("peak", "Peak", info.peakIn, info.peakOut) +
      groupCard("quiet", "Quietest", info.lowIn, info.lowOut);
  }

  function playFill(root, animate) {
    if (!root) return;
    var bars = root.querySelectorAll(".kpi-bands-bar[data-h]");
    var rows = root.querySelectorAll(".kpi-bands-row-bar > i[data-w]");
    var immediate = !animate || reduceMotion();
    if (fillSettleTimer) {
      clearTimeout(fillSettleTimer);
      fillSettleTimer = 0;
    }
    if (immediate) {
      var animateRowsOnly = !animate && !reduceMotion();
      // Snap to final size with no grow-from-0. On a selection re-render the chart
      // is rebuilt; without this the bars would regrow on every click and swamp the
      // selection "pop". The opening reveal (animate=true) still grows them.
      bars.forEach(function (el) {
        el.style.transition = "none";
        el.style.transitionDelay = "0ms";
        el.style.height = el.getAttribute("data-h") + "%";
      });
      rows.forEach(function (el) {
        el.style.transition = animateRowsOnly ? "" : "none";
        el.style.transitionDelay = "0ms";
        el.style.width = animateRowsOnly ? "0%" : el.getAttribute("data-w") + "%";
      });
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          bars.forEach(function (el) { el.style.transition = ""; });
          rows.forEach(function (el, i) {
            el.style.transition = animateRowsOnly ? "" : "none";
            el.style.transitionDelay = animateRowsOnly ? Math.min(i * 24, 260) + "ms" : "0ms";
            el.style.width = el.getAttribute("data-w") + "%";
          });
        });
      });
      return;
    }
    bars.forEach(function (el) {
      el.style.transition = "none";
      el.style.transitionDelay = "0ms";
      el.style.height = "0%";
    });
    rows.forEach(function (el) {
      el.style.transition = "none";
      el.style.transitionDelay = "0ms";
      el.style.width = "0%";
    });
    var set = function () {
      bars.forEach(function (el, i) {
        el.style.transition = "";
        el.style.transitionDelay = Math.min(i * 42, 460) + "ms";
        el.style.height = el.getAttribute("data-h") + "%";
      });
      rows.forEach(function (el, i) {
        el.style.transition = "";
        el.style.transitionDelay = Math.min(120 + i * 22, 520) + "ms";
        el.style.width = el.getAttribute("data-w") + "%";
      });
    };
    requestAnimationFrame(function () {
      requestAnimationFrame(set);
    });
    fillSettleTimer = setTimeout(function () {
      fillSettleTimer = 0;
      bars.forEach(function (el) {
        if (Number(el.getAttribute("data-h")) > 0) el.style.height = el.getAttribute("data-h") + "%";
      });
      rows.forEach(function (el) {
        if (Number(el.getAttribute("data-w")) > 0) el.style.width = el.getAttribute("data-w") + "%";
      });
    }, 1800);
  }

  function runCounts(root, animate) {
    if (!root) return;
    var els = Array.from(root.querySelectorAll(".kpi-bands-cu"));
    var sig = els.map(function (el) { return el.getAttribute("data-cu") || "0"; }).join("|") + "/" + metric + "/" + selectedSig();
    if (sig === lastCountSig && !animate) return;
    if (sig === lastCountSig && animate) return;
    lastCountSig = sig;
    els.forEach(function (el, i) {
      countUp(el, Number(el.getAttribute("data-cu")) || 0, fmtValueOnly, animate, 110 + i * 80);
    });
  }

  function mayRevealNow(section) {
    var b = document.body;
    if (!b || !b.classList.contains("view-kpi-mode")) return true;
    return !!(section && section.classList.contains("is-kpi-snap-active"));
  }
  function revealSection(section) {
    if (!section || revealed) return;
    revealed = true;
    revealArmed = false;
    lastOpenTs = Date.now();
    section.classList.remove("kpi-reveal-pending");
    section.classList.add("is-revealed", "is-opening");
    if (openingTimer) clearTimeout(openingTimer);
    openingTimer = setTimeout(function () {
      section.classList.remove("is-opening");
      openingTimer = 0;
    }, 1900);
    playFill(section, true);
    runCounts(section, true);
  }

  function ensureRevealObserver() {
    if (revealObserver || typeof IntersectionObserver === "undefined") return revealObserver;
    revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!halfVisible(entry)) return;
        // Defer to the snap settle so the opening plays once, after arrival (and not
        // for a panel that isn't the one the user is on).
        if (window.__kpiSnapActive || !mayRevealNow(entry.target)) return;
        revealObserver.unobserve(entry.target);
        revealSection(entry.target);
      });
    }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
    return revealObserver;
  }

  function armReveal() {
    var section = document.querySelector(".kpi-bands-section");
    if (!section) return;
    if (reduceMotion()) {
      revealed = true;
      section.classList.add("is-revealed");
      playFill(section, false);
      runCounts(section, false);
      return;
    }
    if (revealed) {
      section.classList.add("is-revealed");
      playFill(section, false);
      runCounts(section, false);
      return;
    }
    if (revealArmed) return;
    revealArmed = true;
    section.classList.add("kpi-reveal-pending");
    var obs = ensureRevealObserver();
    if (!obs || (elementInView(section) && !window.__kpiSnapActive && mayRevealNow(section))) {
      setTimeout(function () { revealSection(section); }, 80);
    } else {
      obs.observe(section);
      setTimeout(function () {
        if (!revealed && mayRevealNow(section) && elementInView(section) && !window.__kpiSnapActive) {
          if (revealObserver) {
            try { revealObserver.unobserve(section); } catch (e) {}
          }
          revealSection(section);
        }
      }, 520);
    }
  }

  function render(animateIfVisible) {
    var section = document.querySelector(".kpi-bands-section");
    var chartHost = document.getElementById("kpi-bands-chart");
    var gridHost = document.getElementById("kpi-bands-grid");
    var sumHost = document.getElementById("kpi-bands-summary");
    var subEl = document.getElementById("kpi-bands-subtitle");
    if (!section || !chartHost || !gridHost || !sumHost) return;

    var day = currentDay();
    var bands = (day && Array.isArray(day.bands)) ? day.bands : [];
    var hours = flattenHours(bands);
    updateDayNav();
    if (!hours.length || !bands.length) {
      sumHost.innerHTML = "";
      chartHost.innerHTML = '<div class="kpi-bands-empty">No time-band data available.</div>';
      gridHost.innerHTML = "";
      section.classList.add("is-revealed");
      return;
    }

    var details = bands.map(bandDetail);
    var maxVal = 1;
    var dayWeight = 0;
    hours.forEach(function (h) {
      maxVal = Math.max(maxVal, val(h, "inbound"), val(h, "outbound"));
      dayWeight += total(h);
    });
    var detailMax = Math.max(maxVal, details.reduce(function (m, d) {
      return Math.max(m, val(d, "inbound"), val(d, "outbound"));
    }, 1));
    var info = summaryTotals(details);

    if (subEl) {
      subEl.textContent = "Hourly chart with 4-hour band cards / " +
        dateLabel(day.date) + (day.is_today ? " / Today marker active" : " / Historical view");
    }
    sumHost.innerHTML = summaryHtml(info);
    chartHost.innerHTML = chartHtml(hours, maxVal);
    gridHost.innerHTML = gridHtml(details, detailMax, dayWeight);

    if (revealed) {
      playFill(section, !!animateIfVisible);
      runCounts(section, !!animateIfVisible);
    } else {
      armReveal();
    }
  }

  function selectHour(key, additive) {
    if (!key) return;
    if (additive) {
      if (selected[key]) delete selected[key];
      else selected[key] = true;
      return;
    }
    if (selectionCount() > 1 || selected[key]) {
      selected = Object.create(null);
      return;
    }
    selected = Object.create(null);
    selected[key] = true;
  }

  // The hour-keys that make up one 4-hour band — so clicking a band card selects
  // exactly the chart columns inside that band.
  function bandHourKeys(bandKey) {
    var day = currentDay();
    var bands = (day && day.bands) || [];
    for (var i = 0; i < bands.length; i++) {
      if (String(bands[i].key) !== String(bandKey)) continue;
      var hrs = Array.isArray(bands[i].hours) && bands[i].hours.length ? bands[i].hours : [bands[i]];
      return hrs.map(function (h) { return h.key || bands[i].key; });
    }
    return [];
  }

  function selectBand(bandKey, additive) {
    var keys = bandHourKeys(bandKey);
    if (!keys.length) return;
    if (additive) {
      var allOn = keys.every(function (k) { return selected[k]; });
      keys.forEach(function (k) { if (allOn) delete selected[k]; else selected[k] = true; });
      return;
    }
    // Toggle: if the selection is already exactly this band, clear it; else make
    // the selection this band's hours.
    var cur = selectionKeys();
    var sameSet = cur.length === keys.length && keys.every(function (k) { return selected[k]; });
    selected = Object.create(null);
    if (!sameSet) keys.forEach(function (k) { selected[k] = true; });
  }

  document.addEventListener("click", function (e) {
    var target = e.target.closest && e.target.closest("[data-hour-key]");
    if (target && target.closest(".kpi-bands-section")) {
      selectHour(target.getAttribute("data-hour-key"), !!(e.ctrlKey || e.metaKey));
      lastRenderSig = "";
      render(false);
      playBandsSelectionMotion();
      return;
    }

    var bandCard = e.target.closest && e.target.closest(".kpi-bands-card[data-band-key]");
    if (bandCard && bandCard.closest(".kpi-bands-section")) {
      selectBand(bandCard.getAttribute("data-band-key"), !!(e.ctrlKey || e.metaKey));
      lastRenderSig = "";
      render(false);
      playBandsSelectionMotion();
      return;
    }

    var dayBtn = e.target.closest && e.target.closest("[data-band-day]");
    if (dayBtn && dayBtn.closest(".kpi-bands-section")) {
      var dir = dayBtn.getAttribute("data-band-day");
      var nextIndex = dayIndex + (dir === "prev" ? -1 : 1);
      var clamped = Math.max(0, Math.min(days().length - 1, nextIndex));
      if (clamped !== dayIndex) {
        dayIndex = clamped;
        selected = Object.create(null);
        lastRenderSig = "";
        lastCountSig = "";
        render(true);
        playBandsSwap();
      }
      return;
    }

    var btn = e.target.closest && e.target.closest(".kpi-bands-tg-btn");
    if (!btn) return;
    var group = btn.closest(".kpi-bands-toggle");
    if (!group) return;
    var changed = false;
    if (group.getAttribute("data-toggle") === "metric") {
      var m = btn.getAttribute("data-metric");
      if (m && m !== metric) { metric = m; changed = true; }
    }
    if (!changed) return;
    group.querySelectorAll(".kpi-bands-tg-btn").forEach(function (b) {
      b.classList.toggle("is-active", b === btn);
    });
    lastRenderSig = "";
    render(true);
    playBandsSwap();
  });

  document.addEventListener("change", function (e) {
    var select = e.target && e.target.closest && e.target.closest("#kpi-bands-day-select");
    if (!select || !select.closest(".kpi-bands-section")) return;
    var nextIndex = Number(select.value);
    if (!isFinite(nextIndex)) return;
    var clamped = Math.max(0, Math.min(days().length - 1, nextIndex));
    if (clamped === dayIndex) return;
    dayIndex = clamped;
    selected = Object.create(null);
    lastRenderSig = "";
    lastCountSig = "";
    render(true);
    playBandsSwap();
  });

  // Cohesive view-change motion: the chart, the band cards and the summary chips
  // all glide in together (not just the bars), so a metric switch reads as one
  // smooth transition rather than a hard content swap.
  function playBandsSwap() {
    if (reduceMotion()) return;
    ["kpi-bands-summary", "kpi-bands-chart", "kpi-bands-grid"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      el.classList.remove("kpi-bands-swap");
      void el.offsetWidth;
      el.classList.add("kpi-bands-swap");
    });
  }

  function playBandsSelectionMotion() {
    if (reduceMotion()) return;
    var chart = document.getElementById("kpi-bands-chart");
    var grid = document.getElementById("kpi-bands-grid");
    [chart, grid].forEach(function (el) {
      if (!el) return;
      el.classList.remove("kpi-bands-selection-change");
      void el.offsetWidth;
      el.classList.add("kpi-bands-selection-change");
    });
  }

  // Keyboard activation for the role=button hour columns and band cards.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " ") return;
    var el = e.target;
    if (!el || !el.closest || !el.closest(".kpi-bands-section")) return;
    var col = el.closest && el.closest("[data-hour-key]");
    var card = el.closest && el.closest(".kpi-bands-card[data-band-key]");
    if (col) {
      e.preventDefault();
      selectHour(col.getAttribute("data-hour-key"), !!(e.ctrlKey || e.metaKey));
      lastRenderSig = "";
      render(false);
      playBandsSelectionMotion();
    } else if (card) {
      e.preventDefault();
      selectBand(card.getAttribute("data-band-key"), !!(e.ctrlKey || e.metaKey));
      lastRenderSig = "";
      render(false);
      playBandsSelectionMotion();
    }
  });

  window.__renderKpiBands = function (data) {
    var keepDate = currentDay() && currentDay().date;
    payload = normalizePayload(data || {});
    var list = days();
    var preferred = todayIndex();
    if (keepDate) {
      for (var i = 0; i < list.length; i++) {
        if (list[i] && list[i].date === keepDate) {
          preferred = i;
          break;
        }
      }
    }
    dayIndex = Math.max(0, Math.min(list.length - 1, preferred));
    var sig = signature(payload);
    if (sig === lastRenderSig && document.querySelector(".kpi-bands-col")) {
      armReveal();
      return;
    }
    lastRenderSig = sig;
    lastCountSig = "";
    render(revealed);
  };

  window.addEventListener("kpi-view-entered", function () {
    // Reset per-KPI-session reveal gate so the opening plays on first visit.
    revealed = false;
    armReveal();
  });

  // Replay the staggered opening when the user snaps onto this panel.
  function replayOpening() {
    var section = document.querySelector(".kpi-bands-section");
    if (!section || reduceMotion() || !document.querySelector(".kpi-bands-col")) return;
    if (Date.now() - lastOpenTs < 750) return;   // dedupe rapid double-settles
    revealed = false;
    revealArmed = false;
    if (openingTimer) { clearTimeout(openingTimer); openingTimer = 0; }
    section.classList.remove("is-revealed", "is-opening");
    section.classList.add("kpi-reveal-pending");
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { revealSection(section); });
    });
  }
  window.addEventListener("kpi-segment-settled", function (e) {
    var sec = e && e.detail && e.detail.section;
    if (!sec || !sec.classList || !sec.classList.contains("kpi-bands-section")) return;
    // Only replay if the opening hasn't played yet this KPI session.
    if (!revealed) replayOpening();
  });
  // Pre-hide the bands content as soon as any snap navigation starts so the chips/cards
  // are invisible during the scroll approach (not just after settle).
  window.addEventListener("kpi-segment-changing", function () {
    var section = document.querySelector(".kpi-bands-section");
    if (!section || reduceMotion()) return;
    if (section.classList.contains("is-revealed") || section.classList.contains("is-opening")) {
      if (openingTimer) { clearTimeout(openingTimer); openingTimer = 0; }
      section.classList.remove("is-revealed", "is-opening");
      section.classList.add("kpi-reveal-pending");
    }
  });
})();
