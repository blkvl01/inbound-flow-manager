/* Standalone KPI page charts: two shift-comparison line charts fed by
   kpi-page-chart-store. Keeps the reporting page separate from the operational
   inbound/ULD switch while reusing the existing KPI cache payload. */
(function () {
  "use strict";

  if (window.__kpiPageInit) return;
  window.__kpiPageInit = true;

  var SVG_W = 760;
  var SVG_H = 260;
  var PAD_L = 52;
  var PAD_R = 22;
  var PAD_T = 26;
  var PAD_B = 36;
  var INNER_W = SVG_W - PAD_L - PAD_R;
  var INNER_H = SVG_H - PAD_T - PAD_B;

  var accents = {
    inbound: {
      current: "#ff8a3d",
      currentSoft: "#ffc08a",
      prev: "#60a5fa",
      fill: "#ff8a3d"
    },
    outbound: {
      current: "#a78bfa",
      currentSoft: "#ddd6fe",
      prev: "#34d399",
      fill: "#a78bfa"
    }
  };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtKg(value) {
    var n = Math.round(Number(value) || 0);
    if (!n) return "-";
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " kg";
  }

  function asNumber(value) {
    var n = Number(value);
    return isFinite(n) ? n : 0;
  }

  function fmtKgFull(value) {
    var n = Math.round(asNumber(value));
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " kg";
  }

  function fmtUnit(value, unit) {
    var n = asNumber(value);
    if (!n) return "-";
    return String(Math.round(n * 100) / 100).replace(".", ",") + " " + unit;
  }

  function fmtPercent(value) {
    return String(Math.round(asNumber(value) * 1000) / 10).replace(".", ",") + "%";
  }

  function fmtDate(value) {
    var parts = String(value || "").split("-");
    if (parts.length === 3) return parts[1] + "." + parts[2];
    return String(value || "");
  }

  function dateKey(date) {
    var y = date.getFullYear();
    var m = String(date.getMonth() + 1).padStart(2, "0");
    var d = String(date.getDate()).padStart(2, "0");
    return y + "-" + m + "-" + d;
  }

  function shiftedDateKey(deltaDays) {
    var base = new Date();
    return dateKey(new Date(base.getFullYear(), base.getMonth(), base.getDate() + deltaDays));
  }

  function arrivalRowClass(value) {
    var raw = String(value || "").slice(0, 10);
    if (!raw) return "";
    var today = new Date();
    var todayRaw = dateKey(today);
    var prev = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    var next = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
    if (raw === todayRaw) return " is-today";
    if (raw === dateKey(prev) || raw === dateKey(next)) return " is-near-day";
    return "";
  }

  function compact(value) {
    var n = Math.round(Number(value) || 0);
    if (n >= 10000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (Math.round(n / 100) / 10) + "k";
    return String(n);
  }

  function compactDayVal(value) {
    var n = Math.round(Number(value) || 0);
    if (!n) return "-";
    var s;
    if (n >= 10000) s = Math.round(n / 1000) + "k";
    else if (n >= 1000) s = (Math.round(n / 100) / 10) + "k";
    else s = String(n);
    return s + " " + flowDayMetricLabel();
  }

  function niceMax(value) {
    var v = Number(value) || 0;
    if (v <= 0) return 1000;
    var pow = Math.pow(10, Math.floor(Math.log10(v)));
    var n = v / pow;
    var f = n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 7.5 ? 7.5 : 10;
    return f * pow;
  }

  function normalizedHours(hours) {
    var out = Array.isArray(hours) ? hours.slice() : [];
    if (out.length < 2) return out;
    var first = parseInt(out[0] && out[0].label, 10);
    var last = parseInt(out[out.length - 1] && out[out.length - 1].label, 10);
    if (first >= 18 && first <= 20 && last >= 8 && last <= 10) out.reverse();
    return out;
  }

  function totalHours(hours) {
    return (hours || []).reduce(function (sum, h) {
      return sum + (Number(h && h.kg) || 0);
    }, 0);
  }

  function setText(id, value) {
    var el = document.getElementById(id);
    if (!el) return;
    var text = String(value == null ? "" : value);
    if (el.textContent === text) return;
    el.textContent = text;
    el.classList.remove("kpi-page-value-pop");
    void el.offsetWidth;
    el.classList.add("kpi-page-value-pop");
  }

  function formatCountValue(value, fmt) {
    if (fmt === "kg-dash") return fmtKg(value);
    if (fmt === "ratio") return fmtPercent(value);
    if (fmt === "unit") return fmtUnit(value, "kg");
    return fmtKgFull(value);
  }

  var countObserver = null;
  // Reveal animations must only fire once the element is genuinely on screen: at
  // least half of it visible, or — for a segment taller than the viewport — once
  // it dominates the viewport. Used for the synchronous "already visible" path.
  function elementInView(el) {
    if (!el || !el.getClientRects || !el.getClientRects().length) return false;
    var r = el.getBoundingClientRect();
    var vh = window.innerHeight || document.documentElement.clientHeight || 0;
    if (!vh || r.height <= 0) return false;
    var visible = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
    return visible >= r.height * 0.5 || visible >= vh * 0.55;
  }
  // IntersectionObserver gate: half of the target intersecting, or (tall segment)
  // its visible slice filling most of the viewport. Pairs with HALF_THRESHOLDS.
  function halfVisible(entry) {
    if (!entry || !entry.isIntersecting) return false;
    if (entry.intersectionRatio >= 0.5) return true;
    var ir = entry.intersectionRect;
    var th = (entry.rootBounds && entry.rootBounds.height) ||
      window.innerHeight || document.documentElement.clientHeight || 0;
    return !!(th && ir && ir.height >= th * 0.55);
  }
  var HALF_THRESHOLDS = [0, 0.25, 0.5, 0.75, 1];

  function ensureCountObserver() {
    if (countObserver || typeof IntersectionObserver === "undefined") return countObserver;
    countObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!halfVisible(entry)) return;
        countObserver.unobserve(entry.target);
        startQueuedCountUp(entry.target);
      });
    }, { threshold: HALF_THRESHOLDS });
    return countObserver;
  }

  function cancelCountUp(el) {
    if (!el) return;
    if (el.__kpiCountTimer) {
      clearTimeout(el.__kpiCountTimer);
      el.__kpiCountTimer = 0;
    }
    if (el.__kpiCountRaf) {
      cancelAnimationFrame(el.__kpiCountRaf);
      el.__kpiCountRaf = 0;
    }
    el.__kpiCountRunning = false;
  }

  function queueCountUp(el, target, fmt, delay, force) {
    if (!el) return;
    target = asNumber(target);
    fmt = fmt || "kg";
    var finalText = formatCountValue(target, fmt);
    var stableKey = el.getAttribute("data-count-key") || el.id || "";
    var stableSig = finalText + "|" + fmt;
    if (!force && stableKey && countLastByKey[stableKey] === stableSig) {
      cancelCountUp(el);
      el.textContent = finalText;
      el.__kpiCountTarget = target;
      el.__kpiCountFmt = fmt;
      return;
    }
    if (!force && el.__kpiCountTarget === target && el.__kpiCountFmt === fmt) {
      if (!el.__kpiCountRunning && el.textContent !== finalText) el.textContent = finalText;
      return;
    }
    el.__kpiCountTarget = target;
    el.__kpiCountFmt = fmt;
    el.__kpiCountKey = stableKey;
    el.__kpiCountSig = stableSig;
    el.__kpiCountDelay = Math.max(0, delay || 0);
    if (prefersReducedMotion()) {
      el.textContent = finalText;
      el.__kpiCountRunning = false;
      if (stableKey) countLastByKey[stableKey] = stableSig;
      return;
    }
    var obs = ensureCountObserver();
    if (!obs || elementInView(el)) startQueuedCountUp(el);
    else obs.observe(el);
  }

  function startQueuedCountUp(el) {
    if (!el) return;
    var target = asNumber(el.__kpiCountTarget);
    var fmt = el.__kpiCountFmt || "kg";
    var delay = Math.max(0, el.__kpiCountDelay || 0);
    var stableKey = el.__kpiCountKey || el.getAttribute("data-count-key") || el.id || "";
    var stableSig = el.__kpiCountSig || (target + "|" + fmt);
    cancelCountUp(el);
    var finalText = formatCountValue(target, fmt);
    if (prefersReducedMotion() || target <= 0) {
      el.textContent = finalText;
      el.__kpiCountRunning = false;
      if (stableKey) countLastByKey[stableKey] = stableSig;
      return;
    }
    if (stableKey) countLastByKey[stableKey] = stableSig;
    el.__kpiCountRunning = true;
    el.classList.remove("kpi-page-value-pop");
    void el.offsetWidth;
    el.classList.add("kpi-page-value-pop");
    el.textContent = formatCountValue(0, fmt);
    el.__kpiCountTimer = setTimeout(function () {
      var start = null;
      var dur = fmt === "unit" ? 780 : 920;
      function step(ts) {
        if (start === null) start = ts;
        var p = Math.min(1, (ts - start) / dur);
        var eased = 1 - Math.pow(1 - p, 3);
        el.textContent = formatCountValue(target * eased, fmt);
        if (p < 1) {
          el.__kpiCountRaf = requestAnimationFrame(step);
        } else {
          el.textContent = finalText;
          el.__kpiCountRaf = 0;
          el.__kpiCountRunning = false;
        }
      }
      el.__kpiCountRaf = requestAnimationFrame(step);
    }, delay);
  }

  function setKgValue(id, value, delay) {
    var el = document.getElementById(id);
    if (!el) return;
    queueCountUp(el, Math.round(asNumber(value)), "kg-dash", delay || 0, false);
  }

  function pointsFor(hours, labels, maxKg) {
    var map = {};
    (hours || []).forEach(function (h) {
      map[String(h && h.label)] = Number(h && h.kg) || 0;
    });
    var baseY = PAD_T + INNER_H;
    var n = Math.max(labels.length, 1);
    return labels.map(function (label, i) {
      var kg = Object.prototype.hasOwnProperty.call(map, String(label)) ? map[String(label)] : 0;
      var y = PAD_T + INNER_H * (1 - kg / maxKg);
      y = Math.max(PAD_T, Math.min(baseY, y));
      return {
        label: label,
        kg: kg,
        x: PAD_L + (n === 1 ? INNER_W / 2 : (i / (n - 1)) * INNER_W),
        y: y
      };
    }).concat(labels.length ? [] : [{ label: "", kg: 0, x: PAD_L, y: baseY }]);
  }

  function smoothPath(points, minY, maxY) {
    if (!points.length) return "";
    if (points.length === 1) return "M" + points[0].x + "," + points[0].y;
    var d = "M" + points[0].x.toFixed(2) + "," + points[0].y.toFixed(2);
    var t = 0.16;
    var clampY = function (y) {
      if (typeof minY === "number" && y < minY) return minY;
      if (typeof maxY === "number" && y > maxY) return maxY;
      return y;
    };
    for (var i = 0; i < points.length - 1; i++) {
      var p0 = points[i - 1] || points[i];
      var p1 = points[i];
      var p2 = points[i + 1];
      var p3 = points[i + 2] || p2;
      var c1x = p1.x + (p2.x - p0.x) * t;
      var c1y = clampY(p1.y + (p2.y - p0.y) * t);
      var c2x = p2.x - (p3.x - p1.x) * t;
      var c2y = clampY(p2.y - (p3.y - p1.y) * t);
      d += "C" + c1x.toFixed(2) + "," + c1y.toFixed(2) + " " +
        c2x.toFixed(2) + "," + c2y.toFixed(2) + " " +
        p2.x.toFixed(2) + "," + p2.y.toFixed(2);
    }
    return d;
  }

  function grid(maxKg) {
    var levels = arguments.length > 1 && Array.isArray(arguments[1]) ? arguments[1] : [0, 0.5, 1];
    var out = "";
    levels.forEach(function (level) {
      var y = PAD_T + INNER_H * (1 - level);
      out += '<line class="kpi-page-grid-line' + (level === 0 ? " base" : "") +
        '" x1="' + PAD_L + '" y1="' + y.toFixed(2) + '" x2="' + (SVG_W - PAD_R) +
        '" y2="' + y.toFixed(2) + '"/>';
      if (level > 0) {
        out += '<text class="kpi-page-y-label" x="' + (PAD_L - 10) + '" y="' +
          y.toFixed(2) + '" text-anchor="end" dominant-baseline="middle">' +
          esc(compact(maxKg * level)) + '</text>';
      }
    });
    return out;
  }

  function axis(labels) {
    if (!labels.length) return "";
    return '<div class="kpi-page-axis">' + labels.map(function (label) {
      return '<span>' + esc(label) + '</span>';
    }).join("") + '</div>';
  }

  function dots(points, cls, limitActive) {
    var out = "";
    points.forEach(function (p, index) {
      if (limitActive && index % 2 !== 0 && index !== points.length - 1) return;
      out += '<circle class="' + cls + '" cx="' + p.x.toFixed(2) + '" cy="' +
        p.y.toFixed(2) + '" r="3.2"/>';
    });
    return out;
  }

  function compactShiftValue(value) {
    var n = Math.round(Number(value) || 0);
    if (n >= 10000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (Math.round(n / 100) / 10).toString().replace(".", ",") + "k";
    return String(n);
  }

  function flowValueLabels(points, hours, hasFuture, splitIdx, peakIdx, baseY) {
    var out = "";
    points.forEach(function (p, i) {
      var future = hasFuture && i >= splitIdx;
      var zero = !p.kg;
      var nearTop = p.y < PAD_T + 24;
      var text = compactShiftValue(p.kg);
      var textW = Math.max(24, text.length * 6.4 + 13);
      var x = clamp(p.x, PAD_L + textW / 2, SVG_W - PAD_R - textW / 2);
      var y = nearTop ? Math.min(baseY - 10, p.y + 20) : Math.max(PAD_T + 12, p.y - 13);
      var cls = "kpi-page-value-label" +
        (future ? " is-future" : "") +
        (zero ? " is-zero" : "") +
        (i === peakIdx ? " is-peak" : "") +
        (hours[i] && hours[i].current ? " is-current-hour" : "");
      out += '<g class="' + cls + '" data-hour-index="' + i + '" transform="translate(' +
        x.toFixed(2) + ' ' + y.toFixed(2) + ')">' +
        '<rect x="' + (-textW / 2).toFixed(2) + '" y="-9.5" width="' + textW.toFixed(2) +
          '" height="17" rx="7.5"/>' +
        '<text x="0" y="0" text-anchor="middle" dominant-baseline="middle">' +
          esc(text) +
        '</text>' +
      '</g>';
    });
    return out;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  // Which shift each chart is showing, and the last payload (so a card click can
  // re-draw without waiting for the server poll).
  var viewState = { inbound: "current", outbound: "current" };
  var flowData = { inbound: null, outbound: null };
  var trackingPayload = null;
  var trackingDetailGroup = null;
  var trackingUnitMode = "kg";
  var countLastByKey = {};
  var trackingGroupSeq = 0;
  var trackingCardSeq = 0;
  var flowChartClipSeq = 0;
  var flowChartStartedSig = {};
  var flowChartPendingSig = {};
  var flowRenderSig = {};      // per-flow data fingerprint → skip full SVG rebuild when unchanged
  var flowMode = "shift";
  var flowDayRange = 7;
  var flowDayMetric = "kg";
  var flowDayPayload = null;
  var flowDaySelected = -1;
  var flowDayRenderSig = "";
  var flowDayTransitioning = false;
  var flowDaySummaryLast = {};
  var lastTrackingSig = "";    // tracking payload fingerprint → skip the 14-card DOM rebuild
  var lastKpiReplayAt = 0;
  var replayKpiTimer = 0;

  function prefersReducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function metricCard(key, view) {
    return document.querySelector(
      '.kpi-page-metric[data-flow="' + key + '"][data-view="' + view + '"]');
  }

  function flowPayloadSignature(data) {
    data = data || {};
    function sideSig(side) {
      var s = data[side] || {};
      var hours = normalizedHours(s.hours);
      return [
        s.name || "",
        s.range || "",
        Math.round(asNumber(s.kg)),
        hours.map(function (h) {
          return [
            h && h.label,
            Math.round(asNumber(h && h.kg)),
            h && h.active === false ? "0" : "1",
            h && h.current ? "1" : "0"
          ].join(":");
        }).join(",")
      ].join("/");
    }
    return sideSig("current") + "||" + sideSig("prev");
  }

  function trackingPayloadSignature(payload) {
    payload = payload || {};
    var sat = payload.current_warehouse_saturation || {};
    var wa = payload.weekly_avg || {};
    var sr = payload.shift_rates || {};
    var arrivals = Array.isArray(payload.expected_arrivals) ? payload.expected_arrivals : [];
    return [
      Math.round(asNumber(sat.kg)),
      Math.round(asNumber(sat.capacity)),
      Math.round(asNumber(sat.ratio) * 10000),
      Math.round(asNumber(payload.to_transfer_12h_kg)),
      Math.round(asNumber(payload.to_release_12h_kg)),
      Math.round(asNumber(payload.ata_more_than_12h_not_transferred_kg)),
      Math.round(asNumber(payload.kg_per_parcel) * 100),
      Math.round(asNumber(payload.kg_per_colli) * 100),
      Math.round(asNumber(payload.kg_per_awb) * 100),
      Math.round(asNumber(payload.active_warehouse_items)),
      Math.round(asNumber((wa.inbound || {}).kg)),
      Math.round(asNumber((wa.outbound || {}).kg)),
      Math.round(asNumber((sr.inbound || {}).count_per_h) * 10),
      Math.round(asNumber((sr.inbound || {}).kg_per_h)),
      Math.round(asNumber((sr.outbound || {}).count_per_h) * 10),
      Math.round(asNumber((sr.outbound || {}).kg_per_h)),
      arrivals.map(function (r) {
        return [
          r && r.date,
          Math.round(asNumber(r && r.expected_arrivals_kg)),
          Math.round(asNumber(r && r.already_arrived_kg)),
          Math.round(asNumber(r && r.already_transferred_kg)),
          Math.round(asNumber(r && r.remaining_expected_arrivals_kg)),
          Math.round(asNumber(r && r.remaining_expected_parcel_kg)),
          Math.round(asNumber(r && r.remaining_expected_colli_kg))
        ].join(":");
      }).join("|")
    ].join("||");
  }

  function syncActiveMetric(key) {
    ["current", "prev"].forEach(function (view) {
      var card = metricCard(key, view);
      if (card) card.classList.toggle("is-active-view", viewState[key] === view);
    });
  }

  function renderFlow(key, data) {
    var host = document.getElementById("kpi-page-" + key + "-chart");
    if (!host) return;
    data = data || {};
    flowData[key] = data;
    var current = data.current || {};
    var prev = data.prev || {};
    var curHours = normalizedHours(current.hours);
    var prevHours = normalizedHours(prev.hours);

    var curTotal = asNumber(current.kg) || totalHours(curHours);
    var prevTotal = asNumber(prev.kg) || totalHours(prevHours);
    setKgValue("kpi-page-" + key + "-current-kg", curTotal, key === "inbound" ? 140 : 240);
    setKgValue("kpi-page-" + key + "-prev-kg", prevTotal, key === "inbound" ? 230 : 330);
    setText("kpi-page-" + key + "-current-range", current.range || current.name || "");
    setText("kpi-page-" + key + "-prev-range", prev.range || prev.name || "");


    // Disable the "Előző" card when there's no previous shift to show.
    var prevCard = metricCard(key, "prev");
    if (prevCard) prevCard.classList.toggle("is-empty", !prevHours.length);
    if (viewState[key] === "prev" && !prevHours.length) viewState[key] = "current";

    var dataSig = flowPayloadSignature(data);
    if (flowRenderSig[key] === dataSig &&
        host.__kpiDrawnView === viewState[key] &&
        host.firstChild) {
      syncActiveMetric(key);
      return;
    }
    flowRenderSig[key] = dataSig;
    drawFlow(key);
  }

  function flowDaySignature(payload) {
    var days = payload && Array.isArray(payload.days) ? payload.days : [];
    return flowDayRange + "||" + flowDayMetric + "||" + days.map(function (day) {
      var bands = Array.isArray(day && day.bands) ? day.bands : [];
      var inboundKg = 0, outboundKg = 0, inboundParcel = 0, outboundParcel = 0, inboundColli = 0, outboundColli = 0;
      bands.forEach(function (band) {
        inboundKg += asNumber(band && band.inbound_kg);
        outboundKg += asNumber(band && band.outbound_kg);
        inboundParcel += asNumber(band && band.inbound_parcel);
        outboundParcel += asNumber(band && band.outbound_parcel);
        inboundColli += asNumber(band && band.inbound_colli);
        outboundColli += asNumber(band && band.outbound_colli);
      });
      return [day && day.date, Math.round(inboundKg), Math.round(outboundKg),
        Math.round(inboundParcel), Math.round(outboundParcel),
        Math.round(inboundColli), Math.round(outboundColli)].join(":");
    }).join("|");
  }

  function flowDayRows(payload) {
    var days = payload && Array.isArray(payload.days) ? payload.days.slice() : [];
    if (!days.length && payload && Array.isArray(payload.bands)) {
      days = [{ date: payload.date || "", weekday: payload.weekday || "", bands: payload.bands }];
    }
    days = days.slice(Math.max(0, days.length - flowDayRange));
    return days.map(function (day) {
      var bands = Array.isArray(day && day.bands) ? day.bands : [];
      var inboundKg = 0, outboundKg = 0, inboundParcel = 0, outboundParcel = 0, inboundColli = 0, outboundColli = 0;
      bands.forEach(function (band) {
        inboundKg += asNumber(band && band.inbound_kg);
        outboundKg += asNumber(band && band.outbound_kg);
        inboundParcel += asNumber(band && band.inbound_parcel);
        outboundParcel += asNumber(band && band.outbound_parcel);
        inboundColli += asNumber(band && band.inbound_colli);
        outboundColli += asNumber(band && band.outbound_colli);
      });
      var inbound = flowDayMetric === "parcel" ? inboundParcel : (flowDayMetric === "colli" ? inboundColli : inboundKg);
      var outbound = flowDayMetric === "parcel" ? outboundParcel : (flowDayMetric === "colli" ? outboundColli : outboundKg);
      return {
        date: String(day && day.date || ""),
        label: fmtDate(day && day.date),
        inbound: inbound,
        outbound: outbound,
        inboundKg: inboundKg,
        outboundKg: outboundKg,
        inboundParcel: inboundParcel,
        outboundParcel: outboundParcel,
        inboundColli: inboundColli,
        outboundColli: outboundColli
      };
    });
  }

  function flowDayMetricLabel() {
    if (flowDayMetric === "parcel") return "parcel";
    if (flowDayMetric === "colli") return "colli";
    return "kg";
  }

  function fmtFlowDayValue(value) {
    if (flowDayMetric === "kg") return fmtKg(value);
    var n = Math.round(asNumber(value));
    if (!n) return "-";
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " " + flowDayMetricLabel();
  }

  function setFlowSummaryValue(id, value) {
    var el = document.getElementById(id);
    if (!el) return;
    var target = Math.max(0, asNumber(value));
    var finalText = fmtFlowDayValue(target);
    var sig = flowDayMetric + "|" + Math.round(target);
    if (flowDaySummaryLast[id] === sig) {
      if (el.textContent !== finalText) el.textContent = finalText;
      return;
    }
    flowDaySummaryLast[id] = sig;
    if (el.__flowSummaryRaf) cancelAnimationFrame(el.__flowSummaryRaf);
    el.classList.remove("is-counting");
    void el.offsetWidth;
    el.classList.add("is-counting");
    if (prefersReducedMotion() || target <= 0) {
      el.textContent = finalText;
      return;
    }
    var start = null;
    var from = asNumber(el.__flowSummaryValue);
    var dur = 840;
    function step(ts) {
      if (start === null) start = ts;
      var p = Math.min(1, (ts - start) / dur);
      var eased = 1 - Math.pow(1 - p, 3);
      var current = from + (target - from) * eased;
      el.textContent = fmtFlowDayValue(current);
      if (p < 1) el.__flowSummaryRaf = requestAnimationFrame(step);
      else {
        el.__flowSummaryRaf = 0;
        el.__flowSummaryValue = target;
        el.textContent = finalText;
        setTimeout(function () { el.classList.remove("is-counting"); }, 160);
      }
    }
    el.__flowSummaryValue = target;
    el.__flowSummaryRaf = requestAnimationFrame(step);
  }

  function flowDayTotals(rows) {
    return rows.reduce(function (acc, row) {
      acc.inbound += asNumber(row && row.inbound);
      acc.outbound += asNumber(row && row.outbound);
      return acc;
    }, { inbound: 0, outbound: 0 });
  }

  function pointsFromRows(rows, key, maxKg) {
    var baseY = PAD_T + INNER_H;
    var n = Math.max(rows.length, 1);
    return rows.map(function (row, i) {
      var kg = asNumber(row && row[key]);
      var y = PAD_T + INNER_H * (1 - kg / maxKg);
      y = Math.max(PAD_T, Math.min(baseY, y));
      return {
        label: row.label,
        date: row.date,
        kg: kg,
        x: PAD_L + (n === 1 ? INNER_W / 2 : (i / (n - 1)) * INNER_W),
        y: y
      };
    });
  }

  // Trailing 3-day moving-average points (causal window, never looks ahead).
  function maPoints(rows, key, win, maxKg) {
    var n = Math.max(rows.length, 1);
    var baseY = PAD_T + INNER_H;
    return rows.map(function (_, i) {
      var start = Math.max(0, i - win + 1);
      var sum = 0;
      for (var j = start; j <= i; j++) sum += asNumber(rows[j][key]);
      var kg = sum / (i - start + 1);
      var y = PAD_T + INNER_H * (1 - (maxKg > 0 ? kg / maxKg : 0));
      return { x: PAD_L + (n === 1 ? INNER_W / 2 : (i / (n - 1)) * INNER_W), y: Math.max(PAD_T, Math.min(baseY, y)), kg: kg };
    });
  }

  function setFlowMode(mode) {
    mode = mode === "day" ? "day" : "shift";
    if (flowMode === mode) return;
    flowMode = mode;
    flowDayTransitioning = mode === "day";
    syncFlowModeControls();
    if (mode === "day") {
      var dayHost = document.getElementById("kpi-flow-day-chart");
      if (dayHost) dayHost.innerHTML = "";
      flowDayRenderSig = "";
      setTimeout(function () {
        renderDayFlow(flowDayPayload, true, true);
        flowDayTransitioning = false;
      }, prefersReducedMotion() ? 0 : 240);
    } else {
      flowDaySelected = -1;
      flowDayTransitioning = false;
      // Reset draw-on dedup so shift charts replay their opening animation.
      flowChartStartedSig = {};
      flowRenderSig = {};
      setTimeout(function () {
        // Re-draw from the cached payload, not the host element — passing the DOM
        // node as `data` left data.current undefined → both charts went empty.
        ["inbound", "outbound"].forEach(function (k) {
          renderFlow(k, flowData[k]);
        });
      }, prefersReducedMotion() ? 0 : 60);
    }
  }

  window.__setKpiFlowMode = function (mode) {
    setFlowMode(mode);
  };

  window.__getKpiFlowMode = function () {
    return flowMode;
  };

  function syncFlowModeControls() {
    var section = document.querySelector(".kpi-flow-section");
    if (section) section.classList.toggle("is-day-mode", flowMode === "day");
    document.querySelectorAll(".kpi-flow-mode-btn[data-flow-mode]").forEach(function (btn) {
      var on = btn.getAttribute("data-flow-mode") === flowMode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    var modeCtl = document.getElementById("kpi-flow-mode-control");
    if (modeCtl) {
      modeCtl.style.setProperty("--flow-mode-index", flowMode === "day" ? "1" : "0");
      modeCtl.classList.remove("is-morphing");
      void modeCtl.offsetWidth;
      modeCtl.classList.add("is-morphing");
      clearTimeout(modeCtl.__kpiFlowMorphTimer);
      modeCtl.__kpiFlowMorphTimer = setTimeout(function () {
        modeCtl.classList.remove("is-morphing");
      }, 360);
    }
    syncFlowPeriodControl();
    syncFlowUnitControl();
  }

  function syncFlowPeriodControl() {
    document.querySelectorAll(".kpi-flow-period-btn[data-flow-days]").forEach(function (btn) {
      var on = String(flowDayRange) === btn.getAttribute("data-flow-days");
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    var ctl = document.getElementById("kpi-flow-period-control");
    if (ctl) {
      ctl.style.setProperty("--flow-period-index", flowDayRange === 14 ? "1" : "0");
      ctl.classList.remove("is-morphing");
      void ctl.offsetWidth;
      ctl.classList.add("is-morphing");
      clearTimeout(ctl.__kpiFlowPeriodMorphTimer);
      ctl.__kpiFlowPeriodMorphTimer = setTimeout(function () {
        ctl.classList.remove("is-morphing");
      }, 320);
    }
  }

  function syncFlowUnitControl() {
    document.querySelectorAll(".kpi-flow-unit-btn[data-flow-unit]").forEach(function (btn) {
      var on = btn.getAttribute("data-flow-unit") === flowDayMetric;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    var ctl = document.getElementById("kpi-flow-unit-control");
    if (ctl) {
      var opts = ["kg", "colli", "parcel"];
      ctl.style.setProperty("--flow-unit-index", String(Math.max(0, opts.indexOf(flowDayMetric))));
      ctl.classList.remove("is-morphing");
      void ctl.offsetWidth;
      ctl.classList.add("is-morphing");
      clearTimeout(ctl.__kpiFlowUnitMorphTimer);
      ctl.__kpiFlowUnitMorphTimer = setTimeout(function () {
        ctl.classList.remove("is-morphing");
      }, 320);
    }
  }

  function updateDaySummary(rows, idx) {
    if (!rows.length) {
      setText("kpi-flow-day-inbound-total-value", "-");
      setText("kpi-flow-day-inbound-avg-value", "-");
      setText("kpi-flow-day-outbound-total-value", "-");
      setText("kpi-flow-day-outbound-avg-value", "-");
      return;
    }
    var selected = idx >= 0 && idx < rows.length;
    var totals = selected
      ? { inbound: asNumber(rows[idx].inbound), outbound: asNumber(rows[idx].outbound) }
      : flowDayTotals(rows);
    var divisor = selected ? 1 : Math.max(1, rows.length);
    var meta = selected
      ? ((rows[idx].label || rows[idx].date || "Selected day") + " selected")
      : (rows.length + " day total");
    var avgMeta = selected ? "Selected day value" : "Average per day";
    setFlowSummaryValue("kpi-flow-day-inbound-total-value", totals.inbound);
    setFlowSummaryValue("kpi-flow-day-inbound-avg-value", totals.inbound / divisor);
    setFlowSummaryValue("kpi-flow-day-outbound-total-value", totals.outbound);
    setFlowSummaryValue("kpi-flow-day-outbound-avg-value", totals.outbound / divisor);
    setText("kpi-flow-day-inbound-total-meta", meta);
    setText("kpi-flow-day-outbound-total-meta", meta);
    setText("kpi-flow-day-inbound-avg-meta", avgMeta);
    setText("kpi-flow-day-outbound-avg-meta", avgMeta);
    document.querySelectorAll(".kpi-flow-day-stat").forEach(function (el) {
      el.classList.toggle("is-selected", selected);
    });
  }

  function renderDayFlow(payload, force, animateOpening) {
    flowDayPayload = payload || flowDayPayload || {};
    var host = document.getElementById("kpi-flow-day-chart");
    if (!host) return;
    var rows = flowDayRows(flowDayPayload);
    if (flowDaySelected >= rows.length) flowDaySelected = -1;
    updateDaySummary(rows, flowDaySelected);
    var sig = flowDaySignature(flowDayPayload);
    if (!force && flowDayRenderSig === sig && host.firstChild) return;
    flowDayRenderSig = sig;

    if (!rows.length) {
      host.innerHTML = '<div class="kpi-page-chart-empty">No daily data</div>';
      return;
    }

    var maxKg = 0;
    rows.forEach(function (row) {
      maxKg = Math.max(maxKg, asNumber(row.inbound), asNumber(row.outbound));
    });
    maxKg = niceMax(maxKg);
    var inPts = pointsFromRows(rows, "inbound", maxKg);
    var outPts = pointsFromRows(rows, "outbound", maxKg);
    var inPath = smoothPath(inPts, PAD_T, PAD_T + INNER_H);
    var outPath = smoothPath(outPts, PAD_T, PAD_T + INNER_H);
    // 3-day trailing moving average lines (only drawn when ≥4 data points)
    var inMaPath = rows.length >= 4 ? smoothPath(maPoints(rows, "inbound",  3, maxKg), PAD_T, PAD_T + INNER_H) : "";
    var outMaPath = rows.length >= 4 ? smoothPath(maPoints(rows, "outbound", 3, maxKg), PAD_T, PAD_T + INNER_H) : "";
    var baseY = PAD_T + INNER_H;
    var gidIn = "kpiFlowDayInGrad";
    var gidOut = "kpiFlowDayOutGrad";
    var clipIn = "kpiFlowDayRevealIn-" + (++flowChartClipSeq);
    var clipOut = "kpiFlowDayRevealOut-" + flowChartClipSeq;
    var sel = flowDaySelected >= 0 && rows[flowDaySelected] ? flowDaySelected : -1;
    var tipIdx = sel >= 0 ? sel : rows.length - 1;

    function area(path, pts) {
      if (!pts.length) return "";
      return path + " L" + pts[pts.length - 1].x.toFixed(2) + "," + baseY.toFixed(2) +
        " L" + pts[0].x.toFixed(2) + "," + baseY.toFixed(2) + " Z";
    }
    function dayDots(pts, cls) {
      return pts.map(function (p, i) {
        return '<circle class="kpi-flow-day-dot ' + cls + (i === sel ? " is-selected" : "") +
          '" data-day-index="' + i + '" cx="' + p.x.toFixed(2) + '" cy="' + p.y.toFixed(2) + '" r="' +
          (i === sel ? "4.6" : "3.2") + '"/>';
      }).join("");
    }
    function dayValueLabels(pts, isIn, otherPts) {
      var cls = isIn ? "inbound" : "outbound";
      var halfW = 15;
      return pts.map(function (p, i) {
        var val = compactDayVal(asNumber(isIn ? rows[i].inbound : rows[i].outbound));
        var other = otherPts && otherPts[i];
        // Label goes above if this series is higher on screen (smaller y) than the other
        var goAbove = other ? (p.y <= other.y) : isIn;
        var ly = goAbove ? p.y - 13 : p.y + 15;
        if (goAbove && ly < PAD_T + 3) ly = p.y + 14;
        if (!goAbove && ly > baseY - 8) ly = p.y - 13;
        // Horizontal anchor: clamp first/last point labels inside the clip rect
        var anchor = "middle";
        var lx = p.x;
        if (lx < PAD_L + halfW) { anchor = "start"; lx = PAD_L + 4; }
        else if (lx > PAD_L + INNER_W - halfW) { anchor = "end"; lx = PAD_L + INNER_W - 2; }
        return '<text class="kpi-flow-day-val-label ' + cls + '" data-day-index="' + i +
          '" x="' + lx.toFixed(2) + '" y="' + ly.toFixed(2) +
          '" text-anchor="' + anchor + '" dominant-baseline="middle">' + esc(val) + '</text>';
      }).join("");
    }

    host.innerHTML =
      '<div class="kpi-flow-day-stage">' +
        '<svg class="kpi-flow-day-svg" viewBox="0 0 ' + SVG_W + ' ' + (PAD_T + INNER_H + 8) + '" overflow="hidden" role="img">' +
          '<defs>' +
            '<clipPath id="' + clipIn + '"><rect class="kpi-flow-day-reveal inbound" x="' + PAD_L +
              '" y="0" width="' + (SVG_W - PAD_L) + '" height="' + (baseY + 5) + '"/></clipPath>' +
            '<clipPath id="' + clipOut + '"><rect class="kpi-flow-day-reveal outbound" x="' + PAD_L +
              '" y="0" width="' + (SVG_W - PAD_L) + '" height="' + (baseY + 5) + '"/></clipPath>' +
            '<linearGradient id="' + gidIn + '" x1="0" y1="0" x2="0" y2="1">' +
              '<stop offset="0%" stop-color="#ff8a3d" stop-opacity="0.22"/>' +
              '<stop offset="100%" stop-color="#ff8a3d" stop-opacity="0"/>' +
            '</linearGradient>' +
            '<linearGradient id="' + gidOut + '" x1="0" y1="0" x2="0" y2="1">' +
              '<stop offset="0%" stop-color="#a78bfa" stop-opacity="0.20"/>' +
              '<stop offset="100%" stop-color="#a78bfa" stop-opacity="0"/>' +
            '</linearGradient>' +
          '</defs>' +
          grid(maxKg, [0, 0.25, 0.5, 0.75, 1]) +
          '<rect class="kpi-flow-day-hover-band" x="-100" y="' + (PAD_T + 4) + '" width="20" height="' + (INNER_H - 22) + '" rx="4"/>' +
          '<g class="kpi-flow-day-data kpi-flow-day-series inbound" clip-path="url(#' + clipIn + ')">' +
            '<path class="kpi-flow-day-area inbound" d="' + area(inPath, inPts) + '" fill="url(#' + gidIn + ')"/>' +
            '<path class="kpi-flow-day-line glow inbound" data-flow-line="inbound-glow" d="' + inPath + '"/>' +
            '<path class="kpi-flow-day-line inbound" data-flow-line="inbound" d="' + inPath + '"/>' +
            (inMaPath ? '<path class="kpi-flow-day-ma inbound" d="' + inMaPath + '"/>' : '') +
            dayDots(inPts, "inbound") +
            dayValueLabels(inPts, true, outPts) +
          '</g>' +
          '<g class="kpi-flow-day-data kpi-flow-day-series outbound" clip-path="url(#' + clipOut + ')">' +
            '<path class="kpi-flow-day-area outbound" d="' + area(outPath, outPts) + '" fill="url(#' + gidOut + ')"/>' +
            '<path class="kpi-flow-day-line glow outbound" data-flow-line="outbound-glow" d="' + outPath + '"/>' +
            '<path class="kpi-flow-day-line outbound" data-flow-line="outbound" d="' + outPath + '"/>' +
            (outMaPath ? '<path class="kpi-flow-day-ma outbound" d="' + outMaPath + '"/>' : '') +
            dayDots(outPts, "outbound") +
            dayValueLabels(outPts, false, inPts) +
          '</g>' +
          '<line class="kpi-flow-day-selected-line" x1="' + inPts[tipIdx].x.toFixed(2) +
            '" x2="' + inPts[tipIdx].x.toFixed(2) + '" y1="' + (PAD_T - 4) +
            '" y2="' + (baseY - 16).toFixed(2) + '" style="opacity:' + (sel >= 0 ? "1" : "0") + '"/>' +
        '</svg>' +
        '<div class="kpi-flow-day-tip" style="left:' + (inPts[tipIdx].x / SVG_W * 100) +
          '%;opacity:' + (sel >= 0 ? "1" : "0") + '">' +
          '<b>' + esc(rows[tipIdx].label || rows[tipIdx].date) + '</b>' +
          '<span><i class="inbound"></i>' + esc(fmtFlowDayValue(rows[tipIdx].inbound)) + '</span>' +
          '<span><i class="outbound"></i>' + esc(fmtFlowDayValue(rows[tipIdx].outbound)) + '</span>' +
        '</div>' +
      '</div>' +
      axis(rows.map(function (row) { return row.label; })) +
      '<div class="kpi-flow-day-legend">' +
        '<span class="inbound"><i></i>INBOUND</span>' +
        '<span class="outbound"><i></i>OUTBOUND</span>' +
        '<button class="kpi-flow-day-export" type="button" title="Exportálás Excel-be">XLSX</button>' +
      '</div>';

    if (animateOpening) animateDayFlow(host);
    else solidifyDayFlow(host);
    wireDayHover(host, rows, inPts, outPts);
  }

  function animateDayFlow(host) {
    var rects = host.querySelectorAll(".kpi-flow-day-reveal");
    if (!rects.length || prefersReducedMotion()) {
      solidifyDayFlow(host);
      return;
    }
    if (host.__flowDaySolidTimer) {
      clearTimeout(host.__flowDaySolidTimer);
      host.__flowDaySolidTimer = 0;
    }
    rects.forEach(function (rect) {
      rect.setAttribute("width", "0");
    });
    host.querySelectorAll(".kpi-flow-day-line").forEach(function (line) {
      line.style.transition = "none";
      line.style.strokeDasharray = "";
      line.style.strokeDashoffset = "0";
    });
    function reveal(rect, duration) {
      var start = 0;
      function step(ts) {
        if (!start) start = ts;
        var p = Math.min(1, (ts - start) / duration);
        var eased = 1 - Math.pow(1 - p, 4);
        rect.setAttribute("width", ((SVG_W - PAD_L) * eased).toFixed(2));
        if (p < 1) requestAnimationFrame(step);
        else rect.setAttribute("width", String(SVG_W - PAD_L));
      }
      requestAnimationFrame(step);
    }
    setTimeout(function () {
      var inbound = host.querySelector(".kpi-flow-day-reveal.inbound");
      var outbound = host.querySelector(".kpi-flow-day-reveal.outbound");
      if (inbound) reveal(inbound, 1580);
      if (outbound) setTimeout(function () { reveal(outbound, 1720); }, 420);
      host.__flowDaySolidTimer = setTimeout(function () {
        host.__flowDaySolidTimer = 0;
        solidifyDayFlow(host);
      }, 2500);
    }, 260);
  }

  function solidifyDayFlow(host) {
    if (host && host.__flowDaySolidTimer) {
      clearTimeout(host.__flowDaySolidTimer);
      host.__flowDaySolidTimer = 0;
    }
    var rects = host && host.querySelectorAll ? host.querySelectorAll(".kpi-flow-day-reveal") : [];
    rects.forEach(function (rect) {
      rect.setAttribute("width", String(SVG_W - PAD_L));
    });
    if (host && host.querySelectorAll) {
      host.querySelectorAll(".kpi-flow-day-line").forEach(function (line) {
        line.style.transition = "";
        line.style.strokeDasharray = "";
        line.style.strokeDashoffset = "0";
      });
    }
  }

  function wireDayHover(host, rows, inPts, outPts) {
    var svg = host.querySelector("svg");
    if (!svg) return;
    function idxFromEvent(event) {
      var rect = svg.getBoundingClientRect();
      if (!rect.width) return -1;
      var x = (event.clientX - rect.left) / rect.width * SVG_W;
      var idx = 0, best = Infinity;
      inPts.forEach(function (p, i) {
        var d = Math.abs(p.x - x);
        if (d < best) { best = d; idx = i; }
      });
      return idx;
    }
    function paintSelection(idx, persistent) {
      if (idx < 0 || idx >= rows.length) return;
      var line = host.querySelector(".kpi-flow-day-selected-line");
      var tip = host.querySelector(".kpi-flow-day-tip");
      var band = host.querySelector(".kpi-flow-day-hover-band");
      var x = inPts[idx].x;
      if (line) {
        line.setAttribute("x1", x.toFixed(2));
        line.setAttribute("x2", x.toFixed(2));
        line.style.opacity = persistent ? "1" : "0.65";
      }
      if (band) {
        band.setAttribute("x", (x - 10).toFixed(2));
        band.style.opacity = persistent ? "0" : "1";
      }
      if (tip) {
        tip.style.left = (x / SVG_W * 100) + "%";
        tip.style.opacity = persistent ? "1" : "0.92";
        tip.innerHTML = '<b>' + esc(rows[idx].label || rows[idx].date) + '</b>' +
          '<span><i class="inbound"></i>' + esc(fmtFlowDayValue(rows[idx].inbound)) + '</span>' +
          '<span><i class="outbound"></i>' + esc(fmtFlowDayValue(rows[idx].outbound)) + '</span>';
      }
      host.querySelectorAll(".kpi-flow-day-val-label").forEach(function (lbl) {
        lbl.classList.toggle("is-active", Number(lbl.getAttribute("data-day-index")) === idx);
      });
      host.querySelectorAll(".kpi-flow-day-dot").forEach(function (dot) {
        var dotIdx = Number(dot.getAttribute("data-day-index"));
        var isSel = persistent ? dotIdx === idx : (flowDaySelected >= 0 && dotIdx === flowDaySelected);
        var isHov = !persistent && dotIdx === idx;
        dot.classList.toggle("is-selected", isSel);
        dot.classList.toggle("is-hovered", isHov);
        dot.setAttribute("r", isSel ? "4.6" : (isHov ? "4.2" : "3.2"));
      });
    }
    function restoreSelection() {
      if (flowDaySelected >= 0) {
        paintSelection(flowDaySelected, true);
        return;
      }
      var line = host.querySelector(".kpi-flow-day-selected-line");
      var tip = host.querySelector(".kpi-flow-day-tip");
      var band = host.querySelector(".kpi-flow-day-hover-band");
      if (line) line.style.opacity = "0";
      if (tip) tip.style.opacity = "0";
      if (band) band.style.opacity = "0";
      host.querySelectorAll(".kpi-flow-day-dot.is-hovered").forEach(function (dot) {
        dot.classList.remove("is-hovered");
        if (!dot.classList.contains("is-selected")) dot.setAttribute("r", "3.2");
      });
      host.querySelectorAll(".kpi-flow-day-val-label.is-active").forEach(function (lbl) {
        lbl.classList.remove("is-active");
      });
    }
    svg.addEventListener("mousemove", function (event) {
      var idx = idxFromEvent(event);
      if (idx >= 0) paintSelection(idx, false);
    });
    svg.addEventListener("mouseleave", restoreSelection);
    svg.addEventListener("click", function (event) {
      var idx = idxFromEvent(event);
      if (idx < 0 || idx >= rows.length) return;
      flowDaySelected = flowDaySelected === idx ? -1 : idx;
      updateDaySummary(rows, flowDaySelected);
      restoreSelection();
    });
  }

  function exportDayFlowXlsx(btn) {
    if (!flowDayPayload) return;
    var days = Array.isArray(flowDayPayload.days) ? flowDayPayload.days : [];
    if (flowDayPayload.bands && !days.length) days = [flowDayPayload];
    days = days.slice(Math.max(0, days.length - flowDayRange));
    if (!days.length) return;

    var exportRows = days.map(function (day) {
      var bands = Array.isArray(day && day.bands) ? day.bands : [];
      var inKg = 0, outKg = 0, inPcs = 0, outPcs = 0, inColli = 0, outColli = 0;
      bands.forEach(function (b) {
        inKg += asNumber(b && b.inbound_kg);
        outKg += asNumber(b && b.outbound_kg);
        inPcs += asNumber(b && b.inbound_pcs);
        outPcs += asNumber(b && b.outbound_pcs);
        inColli += asNumber(b && b.inbound_colli);
        outColli += asNumber(b && b.outbound_colli);
      });
      return {
        date: String(day && day.date || ""),
        label: fmtDate(day && day.date),
        inbound_kg: Math.round(inKg),
        outbound_kg: Math.round(outKg),
        inbound_parcel: Math.round(inPcs),
        outbound_parcel: Math.round(outPcs),
        inbound_colli: Math.round(inColli),
        outbound_colli: Math.round(outColli)
      };
    });

    var original = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.classList.add("is-loading"); btn.textContent = "Export..."; }

    fetch("/kpi-flow-day-export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows: exportRows, metric: flowDayMetric, range: flowDayRange })
    }).then(function (res) {
      if (!res.ok) {
        return res.text().then(function (txt) {
          var msg = "Export hiba";
          try { var p = JSON.parse(txt); if (p && p.error) msg = p.error; } catch (e) { if (txt) msg = txt.slice(0, 200); }
          throw new Error(msg);
        });
      }
      var cd = res.headers.get("Content-Disposition") || "";
      var m = cd.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
      var filename = (m && m[1] ? decodeURIComponent(m[1].replace(/"/g, "")) : null) || "kpi_flow_day.xlsx";
      return res.blob().then(function (blob) { return { blob: blob, filename: filename }; });
    }).then(function (out) {
      var url = URL.createObjectURL(out.blob);
      var a = document.createElement("a");
      a.href = url; a.download = out.filename; a.style.display = "none";
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(url); if (a.parentNode) a.parentNode.removeChild(a); }, 2000);
      if (btn) {
        btn.classList.remove("is-loading"); btn.classList.add("is-done"); btn.textContent = "Letöltve";
        setTimeout(function () { btn.classList.remove("is-done"); btn.disabled = false; btn.textContent = original || "XLSX"; }, 900);
      }
    }).catch(function (err) {
      try { console.warn("Day flow XLSX export hiba:", err && err.message ? err.message : err); } catch (e) {}
      if (btn) {
        btn.classList.remove("is-loading"); btn.classList.add("is-error");
        btn.title = err && err.message ? err.message : "Hiba";
        btn.textContent = "Hiba";
        setTimeout(function () { btn.classList.remove("is-error"); btn.disabled = false; btn.title = ""; btn.textContent = original || "XLSX"; }, 1200);
      }
    });
  }

  // Draw the shift currently selected for `key` (full swap, not an overlay).
  function drawFlow(key) {
    var host = document.getElementById("kpi-page-" + key + "-chart");
    if (!host) return;
    var data = flowData[key] || {};
    var current = data.current || {};
    var prev = data.prev || {};
    var ac = accents[key] || accents.inbound;
    var view = viewState[key];
    var series = view === "prev" ? (data.prev || {}) : (data.current || {});
    var hours = normalizedHours(series.hours);
    syncActiveMetric(key);

    var labels = hours
      .map(function (h) { return String(h && h.label); })
      .filter(function (v) { return v; });

    if (!labels.length) {
      host.innerHTML = '<div class="kpi-page-chart-empty">No hourly data</div>';
      return;
    }

    // Colour belongs to the bound, not the shift: inbound is always orange,
    // outbound always purple — switching to the previous shift keeps the hue.
    var strokeA = ac.current;
    var strokeB = ac.currentSoft;
    var fillC = ac.fill;

    var maxKg = 0;
    hours.forEach(function (h) {
      var kg = Number(h && h.kg) || 0;
      if (kg > maxKg) maxKg = kg;
    });
    maxKg = niceMax(maxKg);
    var pts = pointsFor(hours, labels, maxKg);
    var baseY = PAD_T + INNER_H;

    // Split the curve at the first not-yet-happened hour: the past is a solid,
    // filled line, the future a faded dashed ghost — so the inactive stretch
    // clearly reads as "upcoming" rather than a flat grey block.
    var splitIdx = -1;
    if (view === "current") {
      for (var fi = 0; fi < hours.length; fi++) {
        if (hours[fi] && hours[fi].active === false) { splitIdx = fi; break; }
      }
    }
    var hasFuture = splitIdx >= 1 && splitIdx < pts.length;
    var activePts = hasFuture ? pts.slice(0, splitIdx) : pts;
    var futurePts = hasFuture ? pts.slice(splitIdx - 1) : [];

    var activePath = smoothPath(activePts, PAD_T, baseY);
    var futurePath = hasFuture ? smoothPath(futurePts, PAD_T, baseY) : "";
    var areaPath = activePath + " L" + activePts[activePts.length - 1].x.toFixed(2) + "," +
      baseY.toFixed(2) + " L" + activePts[0].x.toFixed(2) + "," + baseY.toFixed(2) + " Z";

    // Peak (highest recorded hour) + "now" divider.
    var peakIdx = -1, peakVal = -1, currentIdx = -1;
    pts.forEach(function (p, i) {
      var isActive = !hasFuture || i < splitIdx;
      if (isActive && p.kg > peakVal) { peakVal = p.kg; peakIdx = i; }
      if (hours[i] && hours[i].current) currentIdx = i;
    });

    var gid = "kpiPageGrad-" + key;
    var sid = "kpiPageStroke-" + key;
    var hid = "kpiPageHatch-" + key;

    var futureZone = "";
    if (hasFuture && pts[splitIdx]) {
      var startX = pts[splitIdx].x;
      var futureWidth = Math.max(0, PAD_L + INNER_W - startX);
      if (futureWidth > 0) {
        futureZone =
          '<rect class="kpi-page-futurezone" x="' + startX.toFixed(2) + '" y="' + PAD_T +
            '" width="' + futureWidth.toFixed(2) + '" height="' + INNER_H + '"/>' +
          '<rect class="kpi-page-futurehatch" x="' + startX.toFixed(2) + '" y="' + PAD_T +
            '" width="' + futureWidth.toFixed(2) + '" height="' + INNER_H +
            '" fill="url(#' + hid + ')"/>';
      }
    }

    var nowLine = "";
    if (view === "current" && currentIdx >= 0 && pts[currentIdx]) {
      var nx = pts[currentIdx].x.toFixed(2);
      nowLine = '<line class="kpi-page-nowline" x1="' + nx + '" y1="' + (PAD_T - 6) +
        '" x2="' + nx + '" y2="' + baseY.toFixed(2) + '"/>';
    }

    var peakMark = "";
    if (peakIdx >= 0 && pts[peakIdx] && peakVal > 0) {
      var pxk = pts[peakIdx].x.toFixed(2), pyk = pts[peakIdx].y.toFixed(2);
      peakMark =
        '<circle class="kpi-page-peak-halo" cx="' + pxk + '" cy="' + pyk + '" r="9" fill="' + fillC + '"/>' +
        '<circle class="kpi-page-peak" cx="' + pxk + '" cy="' + pyk + '" r="4.2" fill="#fff" stroke="' + strokeA + '"/>';
    }

    // Solid dots for recorded hours, hollow for upcoming ones; the peak hour is
    // drawn as its own emphasised marker, so skip its plain dot.
    var dotMarkup = "";
    pts.forEach(function (p, i) {
      var future = hasFuture && i >= splitIdx;
      if (!future && i === peakIdx) return;
      var cls = future ? "kpi-page-dot future" : "kpi-page-dot current";
      dotMarkup += '<circle class="' + cls + '" cx="' + p.x.toFixed(2) +
        '" cy="' + p.y.toFixed(2) + '" r="3.2"/>';
    });
    var valueLabelMarkup = flowValueLabels(pts, hours, hasFuture, splitIdx, peakIdx, baseY);

    var clipId = "kpiPageReveal-" + (++flowChartClipSeq);
    var revealW = SVG_W - PAD_L;
    // The opening draw-on is a one-time flourish per *chart instance*: it must
    // play when the chart first scrolls into view, on a shift (current↔prev)
    // switch, and on a fresh KPI-view entry — but NOT every time the live current
    // hour's kg ticks on the 2s poll. Keying the replay on a structural signature
    // (flow + view + which shift) instead of per-hour kg stops the line redrawing
    // itself every poll (the "animation restarts by itself" bug). Live kg changes
    // still rebuild the SVG paths, then snap solid with no replay.
    var revealSig = [
      key,
      view,
      current.range || current.name || "",
      prev.range || prev.name || ""
    ].join("||");

    host.innerHTML =
      '<div class="kpi-page-chart-stage' + (view === "prev" ? " is-prev-view" : "") + '">' +
        '<svg class="kpi-page-chart-svg" viewBox="0 0 ' + SVG_W + ' ' + SVG_H + '" role="img">' +
          '<defs>' +
            '<clipPath id="' + clipId + '"><rect class="kpi-page-reveal-clip" x="' + PAD_L +
              '" y="0" width="' + revealW + '" height="' + SVG_H + '"/></clipPath>' +
            '<linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">' +
              '<stop offset="0%" stop-color="' + fillC + '" stop-opacity="0.34"/>' +
              '<stop offset="60%" stop-color="' + fillC + '" stop-opacity="0.10"/>' +
              '<stop offset="100%" stop-color="' + fillC + '" stop-opacity="0"/>' +
            '</linearGradient>' +
            '<linearGradient id="' + sid + '" x1="0" y1="0" x2="1" y2="0">' +
              '<stop offset="0%" stop-color="' + strokeA + '"/>' +
              '<stop offset="100%" stop-color="' + strokeB + '"/>' +
            '</linearGradient>' +
            '<pattern id="' + hid + '" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">' +
              '<line class="kpi-page-hatch-line" x1="0" y1="0" x2="0" y2="8"/>' +
            '</pattern>' +
          '</defs>' +
          grid(maxKg) +
          '<g class="kpi-page-data-layer" clip-path="url(#' + clipId + ')">' +
            futureZone +
            '<path class="kpi-page-area" d="' + areaPath + '" fill="url(#' + gid + ')"/>' +
            '<path class="kpi-page-line kpi-page-line-glow" d="' + activePath + '" stroke="' + strokeA + '"/>' +
            (futurePath ? '<path class="kpi-page-line kpi-page-line-future" d="' + futurePath + '" stroke="' + strokeB + '"/>' : "") +
            '<path class="kpi-page-line kpi-page-line-current" d="' + activePath + '" stroke="url(#' + sid + ')"/>' +
            nowLine +
            dotMarkup +
            valueLabelMarkup +
            peakMark +
          '</g>' +
          '<line class="kpi-page-hover-line" x1="0" y1="' + PAD_T + '" x2="0" y2="' + baseY + '" style="opacity:0"/>' +
          '<circle class="kpi-page-hover-dot current" r="4" cx="0" cy="0" style="opacity:0"/>' +
        '</svg>' +
        '<div class="kpi-page-tip" style="opacity:0"></div>' +
        '<div class="kpi-page-point-detail" style="opacity:0"></div>' +
      '</div>' +
      axis(labels);

    host.__kpiDrawnView = view;
    host.__kpiDrawnSig = revealSig;
    animate(host, revealSig);
    wireHover(host, labels, pts, hours, view);
  }

  function switchFlowView(key, view) {
    if (!key || !view || viewState[key] === view) return;
    var data = flowData[key] || {};
    if (view === "prev" && !(data.prev && normalizedHours(data.prev.hours).length)) return;
    viewState[key] = view;
    drawFlow(key);
  }

  // Click a metric card → swap the chart to that shift.
  document.addEventListener("click", function (event) {
    var flowModeBtn = event.target.closest && event.target.closest(".kpi-flow-mode-btn[data-flow-mode]");
    if (flowModeBtn) {
      event.preventDefault();
      setFlowMode(flowModeBtn.getAttribute("data-flow-mode"));
      return;
    }
    var periodBtn = event.target.closest && event.target.closest(".kpi-flow-period-btn[data-flow-days]");
    if (periodBtn) {
      event.preventDefault();
      flowDayRange = periodBtn.getAttribute("data-flow-days") === "14" ? 14 : 7;
      flowDaySelected = -1;
      syncFlowPeriodControl();
      renderDayFlow(flowDayPayload, true, flowMode === "day");
      return;
    }
    var unitBtn = event.target.closest && event.target.closest(".kpi-flow-unit-btn[data-flow-unit]");
    if (unitBtn) {
      event.preventDefault();
      var unit = unitBtn.getAttribute("data-flow-unit") || "kg";
      flowDayMetric = unit === "parcel" || unit === "colli" ? unit : "kg";
      flowDaySelected = -1;
      syncFlowUnitControl();
      renderDayFlow(flowDayPayload, true, flowMode === "day");
      return;
    }
    var exportBtn = event.target.closest && event.target.closest(".kpi-flow-day-export");
    if (exportBtn) {
      event.preventDefault();
      exportDayFlowXlsx(exportBtn);
      return;
    }
    var card = event.target.closest && event.target.closest(".kpi-page-metric[data-flow]");
    if (!card) return;
    switchFlowView(card.getAttribute("data-flow"), card.getAttribute("data-view"));
  });

  // Only animate the shift line once it has scrolled into view; until then leave it
  // parked hidden so the draw-on plays when the user actually reaches it.
  var pageChartObserver = null;
  var pagePending = {};
  function ensurePageChartObserver() {
    if (pageChartObserver || typeof IntersectionObserver === "undefined") return;
    pageChartObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        var sig = flowChartPendingSig[e.target.id];
        if (halfVisible(e) && sig) {
          flowChartPendingSig[e.target.id] = "";
          playShiftDraw(e.target, sig);
        }
      });
    }, { threshold: HALF_THRESHOLDS });
  }
  function pageInView(host) {
    var r = host.getBoundingClientRect();
    var vh = window.innerHeight || document.documentElement.clientHeight || 0;
    return r.bottom > 0 && r.top < vh;
  }

  function shiftRevealRect(host) {
    return host && host.querySelector ? host.querySelector(".kpi-page-reveal-clip") : null;
  }

  function setShiftReveal(host, progress) {
    var rect = shiftRevealRect(host);
    if (!rect) return;
    var p = Math.max(0, Math.min(1, Number(progress) || 0));
    rect.setAttribute("width", ((SVG_W - PAD_L) * p).toFixed(2));
  }

  function cancelShiftReveal(host) {
    if (!host) return;
    if (host.__kpiShiftRevealRaf) {
      cancelAnimationFrame(host.__kpiShiftRevealRaf);
      host.__kpiShiftRevealRaf = 0;
    }
    if (host.__kpiAreaTimer) {
      clearTimeout(host.__kpiAreaTimer);
      host.__kpiAreaTimer = 0;
    }
    if (host.__kpiStageTimer) {
      clearTimeout(host.__kpiStageTimer);
      host.__kpiStageTimer = 0;
    }
  }

  function solidifyShiftDraw(host) {
    var stage = host.querySelector(".kpi-page-chart-stage");
    cancelShiftReveal(host);
    setShiftReveal(host, 1);
    host.querySelectorAll(".kpi-page-line").forEach(function (el) {
      el.style.transition = "";
      el.style.strokeDasharray = "";
      el.style.strokeDashoffset = "0";
    });
    var area = host.querySelector(".kpi-page-area");
    if (area) area.classList.add("is-shown");
    if (stage) stage.classList.add("is-line-complete");
  }

  function hideShiftLine(host) {
    cancelShiftReveal(host);
    setShiftReveal(host, 0);
    host.querySelectorAll(".kpi-page-line").forEach(function (el) {
      el.style.transition = "";
      el.style.strokeDasharray = "";
      el.style.strokeDashoffset = "0";
    });
  }
  function animate(host, chartSig) {
    var stage = host.querySelector(".kpi-page-chart-stage");
    if (stage) stage.classList.remove("is-line-complete");
    if (prefersReducedMotion()) {
      flowChartStartedSig[host.id] = chartSig;
      solidifyShiftDraw(host);
      return;
    }
    ensurePageChartObserver();
    if (host.id && flowChartStartedSig[host.id] === chartSig) {
      solidifyShiftDraw(host);
      return;
    }
    if (pageChartObserver && host.id && !elementInView(host)) {
      hideShiftLine(host);
      flowChartPendingSig[host.id] = chartSig;
      pageChartObserver.observe(host);
      return;
    }
    playShiftDraw(host, chartSig);
  }
  function playShiftDraw(host, chartSig) {
    var stage = host.querySelector(".kpi-page-chart-stage");
    flowChartStartedSig[host.id] = chartSig;
    cancelShiftReveal(host);
    if (stage) stage.classList.remove("is-line-complete");
    host.querySelectorAll(".kpi-page-line").forEach(function (el) {
      el.style.transition = "";
      el.style.strokeDasharray = "";
      el.style.strokeDashoffset = "0";
    });
    var area = host.querySelector(".kpi-page-area");
    if (area) area.classList.remove("is-shown");
    setShiftReveal(host, 0);
    var start = 0;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min(1, (ts - start) / 1500);
      var eased = 1 - Math.pow(1 - p, 3);
      setShiftReveal(host, eased);
      if (p < 1) {
        host.__kpiShiftRevealRaf = requestAnimationFrame(step);
      } else {
        host.__kpiShiftRevealRaf = 0;
        setShiftReveal(host, 1);
      }
    }
    host.__kpiShiftRevealRaf = requestAnimationFrame(step);
    if (area) host.__kpiAreaTimer = setTimeout(function () { area.classList.add("is-shown"); }, 180);
    if (stage) {
      host.__kpiStageTimer = setTimeout(function () { stage.classList.add("is-line-complete"); }, 1580);
    }
  }

  function wireHover(host, labels, pts, hours, view) {
    var stage = host.querySelector(".kpi-page-chart-stage");
    var svg = host.querySelector("svg");
    var tip = host.querySelector(".kpi-page-tip");
    var line = host.querySelector(".kpi-page-hover-line");
    var curDot = host.querySelector(".kpi-page-hover-dot.current");
    var detail = host.querySelector(".kpi-page-point-detail");
    if (!stage || !svg || !tip || !line || !curDot) return;

    var seriesTotal = (hours || []).reduce(function (s, h) {
      return s + (Number(h && h.kg) || 0);
    }, 0);
    var pinned = -1;

    function clearLabelHover() {
      host.querySelectorAll(".kpi-page-value-label.is-hovered").forEach(function (el) {
        el.classList.remove("is-hovered");
      });
    }

    function idxFromEvent(event) {
      var rect = svg.getBoundingClientRect();
      if (!rect.width) return -1;
      var x = (event.clientX - rect.left) / rect.width * SVG_W;
      var idx = 0, best = Infinity;
      pts.forEach(function (p, i) {
        var d = Math.abs(p.x - x);
        if (d < best) { best = d; idx = i; }
      });
      return idx;
    }

    function hide() {
      if (pinned >= 0) return;
      tip.style.opacity = "0";
      line.style.opacity = "0";
      curDot.style.opacity = "0";
      clearLabelHover();
    }

    function showHover(idx) {
      var cp = pts[idx];
      clearLabelHover();
      var label = host.querySelector('.kpi-page-value-label[data-hour-index="' + idx + '"]');
      if (label) label.classList.add("is-hovered");
      line.setAttribute("x1", cp.x.toFixed(2));
      line.setAttribute("x2", cp.x.toFixed(2));
      line.style.opacity = "1";
      curDot.setAttribute("cx", cp.x.toFixed(2));
      curDot.setAttribute("cy", cp.y.toFixed(2));
      curDot.style.opacity = "1";
      tip.innerHTML = '<b>' + esc(fmtKg(cp.kg)) + '</b>' +
        '<span>' + esc(labels[idx] || "") + ':00</span>';
      tip.style.left = (cp.x / SVG_W * 100) + "%";
      tip.style.opacity = "1";
    }

    // Click a point → pin a richer breakdown (share of shift + running total).
    function openDetail(idx) {
      if (!detail) return;
      if (pinned === idx) { closeDetail(); return; }
      pinned = idx;
      var cp = pts[idx];
      var cumulative = 0;
      for (var i = 0; i <= idx; i++) cumulative += Number((hours[i] || {}).kg) || 0;
      var hourObj = hours[idx] || {};
      var share = seriesTotal > 0 ? (cp.kg / seriesTotal) : 0;
      var cumShare = seriesTotal > 0 ? (cumulative / seriesTotal) : 0;
      var stateText = hourObj.current ? "Current hour"
        : (hourObj.active ? "Closed hour" : "No data yet");
      detail.innerHTML =
        '<button class="kpi-page-detail-close" aria-label="Close">×</button>' +
        '<div class="kpi-page-detail-h">' + esc(labels[idx] || "") + ':00 · ' + esc(stateText) + '</div>' +
        '<div class="kpi-page-detail-row"><span>This hour</span><b>' + esc(fmtKg(cp.kg)) + '</b></div>' +
        '<div class="kpi-page-detail-row"><span>Of shift</span><b>' + fmtPercent(share) + '</b></div>' +
        '<div class="kpi-page-detail-row"><span>Cumulative</span><b>' + esc(fmtKg(cumulative)) + ' · ' + fmtPercent(cumShare) + '</b></div>';
      detail.style.left = Math.max(8, Math.min(92, cp.x / SVG_W * 100)) + "%";
      detail.style.opacity = "1";
      detail.classList.add("is-open");
      showHover(idx);
      line.style.opacity = "1";
    }

    function closeDetail() {
      pinned = -1;
      if (detail) { detail.style.opacity = "0"; detail.classList.remove("is-open"); }
      hide();
    }

    svg.addEventListener("click", function (event) {
      var idx = idxFromEvent(event);
      if (idx >= 0) openDetail(idx);
    });
    if (detail) {
      detail.addEventListener("click", function (event) {
        if (event.target.closest(".kpi-page-detail-close")) closeDetail();
      });
    }
  }

  function trackingValue(value, fmt, key) {
    var n = asNumber(value);
    fmt = fmt || "kg";
    return '<strong class="kpi-tracking-value" data-count="' + n + '" data-fmt="' + esc(fmt) +
      '" data-count-key="' + esc(key || "") + '">' +
      esc(formatCountValue(n, fmt)) + '</strong>';
  }

  function trackingCard(label, value, extraClass, fmt) {
    var cardDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    var key = "tracking:" + (extraClass || label || trackingCardSeq);
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-card ' + esc(extraClass || "") +
      '" style="--kpi-card-delay:' + cardDelay.toFixed(3) + 's">' +
      '<span>' + esc(label) + '</span>' +
      trackingValue(value, fmt || "kg", key) +
      '</div>';
  }

  function trackingDatedCard(label, value, extraClass, fmt, dateLabel) {
    var cardDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    var key = "tracking:" + (extraClass || label || trackingCardSeq);
    var dateKeyId = key + ":today-date";
    fmt = fmt || "kg";
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-card ' + esc(extraClass || "") +
      '" style="--kpi-card-delay:' + cardDelay.toFixed(3) + 's">' +
      '<span>' + esc(label) + '</span>' +
      trackingValue(value, fmt, key) +
      '<small class="kpi-tracking-date" data-date-key="' + esc(dateKeyId) + '">' + esc(dateLabel) + '</small>' +
      '</div>';
  }

  function trackingPreviousCard(label, value, extraClass, fmt, dateLabel) {
    var cardDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    var key = "tracking:yesterday-" + (extraClass || label || trackingCardSeq);
    fmt = fmt || "kg";
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-card previous-day ' + esc(extraClass || "") +
      '" style="--kpi-card-delay:' + cardDelay.toFixed(3) + 's">' +
      '<span>' + esc(label) + '</span>' +
      trackingValue(value, fmt, key) +
      '<small class="kpi-tracking-date" data-date-key="' + esc(key) + ':date">' + esc(dateLabel) + '</small>' +
      '</div>';
  }

  function trackingPreviousSet(label, cards) {
    return '<div class="kpi-tracking-previous-set">' +
      '<div class="kpi-tracking-previous-head"><span>' + esc(label) + '</span></div>' +
      '<div class="kpi-tracking-previous-grid">' + cards.join("") + '</div>' +
      '</div>';
  }

  function trackingCompositionValueText(valueKg, mode, payload) {
    var kg = Math.max(0, asNumber(valueKg));
    mode = mode || trackingUnitMode || "kg";
    payload = payload || {};
    if (mode === "parcel") {
      var perParcel = asNumber(payload.kg_per_parcel);
      if (perParcel > 0) return String(Math.round(kg / perParcel)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " parcel";
      return "-";
    }
    if (mode === "colli") {
      var perColli = asNumber(payload.kg_per_colli);
      if (perColli > 0) return String(Math.round(kg / perColli)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " colli";
      return "-";
    }
    return fmtKgFull(kg);
  }

  function trackingCompositionValueAttrs(valueKg, payload) {
    return ' data-val-kg="' + esc(trackingCompositionValueText(valueKg, "kg", payload)) +
      '" data-val-parcel="' + esc(trackingCompositionValueText(valueKg, "parcel", payload)) +
      '" data-val-colli="' + esc(trackingCompositionValueText(valueKg, "colli", payload)) + '"';
  }

  function trackingUnitModeControl(mode) {
    mode = mode || trackingUnitMode || "kg";
    var opts = ["kg", "colli", "parcel"];
    var buttons = opts.map(function (opt) {
      var on = opt === mode;
      return '<button type="button" class="kpi-unit-mode-btn' + (on ? " is-on" : "") +
        '" data-unit-mode="' + esc(opt) + '" aria-pressed="' + (on ? "true" : "false") + '">' +
        esc(opt) + '</button>';
    }).join("");
    return '<div class="kpi-unit-mode" data-mode="' + esc(mode) + '" style="--unit-mode-index:' +
      Math.max(0, opts.indexOf(mode)) + '">' + buttons + '</div>';
  }

  // Mini status-composition diagram — structured rows: colored stripe | label | % | selected unit.
  function trackingCompositionDiagram(comp, payload) {
    comp = comp || {};
    payload = payload || {};
    var segs = [
      { key: "felveve",     label: "Transfer",   val: Math.max(0, asNumber(comp.felveve)) },
      { key: "ertesito",    label: "NOA",         val: Math.max(0, asNumber(comp.ertesito)) },
      { key: "megerkezett", label: "ATA",         val: Math.max(0, asNumber(comp.megerkezett)) },
      { key: "szemles",     label: "Inspection",  val: Math.max(0, asNumber(comp.szemles)) }
    ];
    var total = segs.reduce(function (s, x) { return s + x.val; }, 0);
    var offset = 0;
    var bar = segs.map(function (x) {
      var pct = total > 0 ? (x.val / total * 100) : 0;
      if (pct <= 0) {
        return '<i class="kpi-comp-seg seg-' + x.key + '" data-comp-key="' + esc(x.key) +
          '" style="width:0%;display:none" title="' + esc(x.label + ": 0 kg · 0%") + '"></i>' +
          '<span class="kpi-comp-seg-pct seg-' + x.key + '" data-comp-key="' + esc(x.key) +
          '" style="--seg-mid:0%;display:none" aria-hidden="true">0%</span>';
      }
      var start = offset;
      offset += pct;
      var mid = start + pct / 2;
      var pctLabel = pct.toFixed(0) + "%";
      var sizeClass = pct < 7 ? " is-tiny" : (pct < 12 ? " is-small" : "");
      return '<i class="kpi-comp-seg seg-' + x.key + '" data-comp-key="' + esc(x.key) +
        '" style="width:' + pct.toFixed(2) + '%" ' +
        'title="' + esc(x.label + ": " + formatCountValue(x.val, "kg") + " · " + pctLabel) + '"></i>' +
        '<span class="kpi-comp-seg-pct seg-' + x.key + sizeClass + '" data-comp-key="' + esc(x.key) +
        '" style="--seg-mid:' + mid.toFixed(2) +
        '%" aria-hidden="true">' + esc(pctLabel) + '</span>';
    }).join("");
    if (!bar) bar = '<i class="kpi-comp-seg seg-empty" style="width:100%"></i>';
    var rows = segs.map(function (x) {
      var pct = total > 0 ? (x.val / total * 100) : 0;
      var pctTxt = pct > 0 ? pct.toFixed(0) + "%" : "—";
      return '<div class="kpi-comp-row"' + trackingCompositionValueAttrs(x.val, payload) + '>' +
        '<i class="kpi-comp-row-stripe seg-' + x.key + '"></i>' +
        '<span class="kpi-comp-row-label">' + esc(x.label) + '</span>' +
        '<b class="kpi-comp-row-pct">' + esc(pctTxt) + '</b>' +
        '<em class="kpi-comp-row-val">' + esc(trackingCompositionValueText(x.val, trackingUnitMode, payload)) + '</em>' +
        '</div>';
    }).join("");
    var compDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-comp" style="--kpi-card-delay:' + compDelay.toFixed(3) + 's">' +
      '<div class="kpi-tracking-comp-bar">' + bar + '</div>' +
      trackingUnitModeControl(trackingUnitMode) +
      '<div class="kpi-tracking-comp-rows">' + rows + '</div>' +
      '</div>';
  }

  // Each group is one click target (cards = result indicators on top, detail
  // opens in the shared dropdown below).
  function trackingGroup(title, tone, group, cards) {
    var groupDelay = 0.10 + Math.min(trackingGroupSeq * 0.075, 0.38);
    trackingGroupSeq += 1;
    return '<section class="kpi-tracking-group ' + esc(tone || "") + '" data-group="' + esc(group) +
      '" role="button" tabindex="0" title="Open breakdown" style="--kpi-group-delay:' +
      groupDelay.toFixed(3) + 's">' +
      '<div class="kpi-tracking-group-head"><span>' + esc(title) + '</span></div>' +
      '<div class="kpi-tracking-group-grid">' + cards.join("") + '</div>' +
      '</section>';
  }

  // Returns the SVG path `d` string for the saturation arc fill (48×48 viewBox).
  // Returns '' when ratio is 0.
  function satArcFillPath(ratio) {
    var pct = clamp(asNumber(ratio) * 100, 0, 100);
    var r = 17, cx = 24, cy = 24;
    var top = "M " + cx + " " + (cy - r);
    if (pct >= 100) {
      return top + " A " + r + " " + r + " 0 1 1 " + cx + " " + (cy + r) + " A " + r + " " + r + " 0 1 1 " + cx + " " + (cy - r);
    }
    if (pct <= 0) return "";
    var ang = -Math.PI / 2 + 2 * Math.PI * pct / 100;
    return top + " A " + r + " " + r + " 0 " + (pct > 50 ? 1 : 0) + " 1 " +
      (cx + r * Math.cos(ang)).toFixed(3) + " " + (cy + r * Math.sin(ang)).toFixed(3);
  }

  function satArcSvg(ratio, color) {
    var r = 17, cx = 24, cy = 24;
    var top = "M " + cx + " " + (cy - r);
    var track = top + " A " + r + " " + r + " 0 1 1 " + cx + " " + (cy + r) + " A " + r + " " + r + " 0 1 1 " + cx + " " + (cy - r);
    var fillD = satArcFillPath(ratio);
    return '<svg class="kpi-sat-arc" viewBox="0 0 48 48" aria-hidden="true">' +
      '<path class="kpi-sat-arc-track" d="' + track + '"/>' +
      (fillD ? '<path class="kpi-sat-arc-fill" d="' + esc(fillD) + '" style="stroke:' + esc(color) + '"/>' : '') +
      "</svg>";
  }

  function saturationStatus(ratio) {
    var pct = clamp(asNumber(ratio) * 100, 0, 100);
    if (pct >= 95) return { label: "Critical saturation", color: "#fb7185", cls: "critical" };
    if (pct >= 85) return { label: "High saturation", color: "#f97316", cls: "high" };
    if (pct >= 70) return { label: "Elevated saturation", color: "#facc15", cls: "elevated" };
    return { label: "Normal saturation", color: "#34d399", cls: "normal" };
  }

  function saturationCard(saturation, capacity) {
    var status = saturationStatus(saturation.ratio);
    var cardDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-card saturation saturation-' + esc(status.cls) +
      '" style="--kpi-card-delay:' + cardDelay.toFixed(3) + 's">' +
      satArcSvg(saturation.ratio, status.color) +
      '<span>Current warehouse saturation</span>' +
      trackingValue(saturation.ratio, "ratio", "tracking:warehouse-saturation") +
      '<em class="kpi-saturation-kg">' + esc(fmtKgFull(saturation.kg) + (capacity ? " / " + fmtKgFull(capacity) : "")) + '</em>' +
      '<small>' + esc(status.label) + '</small>' +
      '</div>';
  }

  function capacityProgressBar(saturation, capacity, activeItems) {
    var status = saturationStatus(saturation.ratio);
    var pct = clamp(asNumber(saturation.ratio) * 100, 0, 100);
    var cardDelay = 0.16 + Math.min(trackingCardSeq * 0.078, 1.02);
    trackingCardSeq += 1;
    return '<div class="kpi-tracking-card kpi-capacity-card" style="--kpi-card-delay:' + cardDelay.toFixed(3) + 's;--sat-color:' + esc(status.color) + '">' +
      '<span>Active items</span>' +
      '<strong class="kpi-capacity-awb-count">' + esc(String(asNumber(activeItems)) + " AWB") + '</strong>' +
      '<div class="kpi-capacity-bar-track"><i class="kpi-capacity-bar-fill" style="--sat-pct:' + pct.toFixed(1) + '%"></i></div>' +
      '<small class="kpi-capacity-pct-label">' + pct.toFixed(1) + '% capacity</small>' +
    '</div>';
  }

  function trackingDayRow(payload, key) {
    var rows = Array.isArray(payload.expected_arrivals) ? payload.expected_arrivals : [];
    for (var i = 0; i < rows.length; i += 1) {
      if (String(rows[i] && rows[i].date || "").slice(0, 10) === key) return rows[i] || {};
    }
    return {};
  }

  function trackingRowTotals(row) {
    row = row || {};
    return {
      expected: asNumber(row.expected_arrivals_kg),
      arrived: asNumber(row.already_arrived_kg),
      transferred: asNumber(row.already_transferred_kg),
      remaining: asNumber(row.remaining_expected_arrivals_kg),
      remainingParcel: asNumber(row.remaining_expected_parcel_kg),
      remainingColli: asNumber(row.remaining_expected_colli_kg)
    };
  }

  function trackingLiveSnapshot(payload) {
    payload = payload || {};
    var todayKey = shiftedDateKey(0);
    var yesterdayKey = shiftedDateKey(-1);
    return {
      todayKey: todayKey,
      yesterdayKey: yesterdayKey,
      todayLabel: fmtDate(todayKey),
      yesterdayLabel: fmtDate(yesterdayKey),
      today: trackingRowTotals(trackingDayRow(payload, todayKey)),
      yesterday: trackingRowTotals(trackingDayRow(payload, yesterdayKey))
    };
  }

  // KPI tracking cards show the current live day only. The daily table still
  // contains the surrounding days; aggregation belongs in the drilldown, not in
  // the summary cards.
  function trackingTotals(payload) {
    return trackingLiveSnapshot(payload).today;
  }

  // ── Count-up numbers inside the dropdown ─────────────────────────────────
  function thousands(n) {
    return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  }
  function formatCU(n, fmt) {
    if (fmt === "pct") return (Math.round(n * 10) / 10).toString().replace(".", ",") + "%";
    if (fmt === "unit") return (Math.round(n * 100) / 100).toString().replace(".", ",") + " kg";
    if (fmt === "int") return thousands(n);
    if (fmt === "items") return thousands(n) + " items";
    if (fmt === "colli") return thousands(n) + " colli";
    if (fmt === "awb") return "≈ " + thousands(n) + " AWB";
    if (fmt === "kg_day") return thousands(n) + " kg/day";
    if (fmt === "perh_cnt") return (Math.round(n * 10) / 10).toString().replace(".", ",") + " items/h";
    if (fmt === "perh_kg") return thousands(n) + " kg/h";
    return fmtKgFull(n);
  }
  function cu(value, fmt) {
    var n = asNumber(value);
    return '<b class="kpi-cu" data-cu="' + n + '" data-fmt="' + fmt + '">' + esc(formatCU(n, fmt)) + '</b>';
  }
  function runCountUps(host, animate) {
    if (!host) return;
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    host.querySelectorAll(".kpi-cu").forEach(function (el) {
      var target = Number(el.getAttribute("data-cu")) || 0;
      var fmt = el.getAttribute("data-fmt") || "kg";
      if (!animate || reduce || target === 0) { el.textContent = formatCU(target, fmt); return; }
      var dur = 720, start = null;
      function step(ts) {
        if (start === null) start = ts;
        var t = Math.min(1, (ts - start) / dur);
        var eased = 1 - Math.pow(1 - t, 3);
        el.textContent = formatCU(target * eased, fmt);
        if (t < 1) requestAnimationFrame(step);
        else el.textContent = formatCU(target, fmt);
      }
      requestAnimationFrame(step);
    });
  }

  function statCell(label, value, fmt) {
    return '<div class="kpi-detail-cell"><span>' + esc(label) + '</span>' + cu(value, fmt) + '</div>';
  }
  // A cell that carries two count-up values (e.g. colli + kg, or db/h + kg/h).
  function dualCell(label, aHtml, bHtml, cls) {
    return '<div class="kpi-detail-cell kpi-detail-dual ' + esc(cls || "") + '">' +
      '<span>' + esc(label) + '</span>' +
      '<div class="kpi-detail-dual-vals">' + aHtml + '<i class="kpi-detail-dual-sep"></i>' + bHtml + '</div></div>';
  }
  function opCell(label, value, mx, cls) {
    var w = clamp(value / (mx || 1) * 100, 0, 100).toFixed(1);
    return '<div class="kpi-detail-cell ' + cls + '"><span>' + esc(label) + '</span>' + cu(value, "kg") +
      '<div class="kpi-detail-prop"><i style="width:' + w + '%"></i></div></div>';
  }
  function dayRows(payload, cols) {
    var rows = (payload.expected_arrivals || []).slice().sort(function (a, b) {
      return String(a.date).localeCompare(String(b.date));
    });
    var maxFirst = rows.reduce(function (m, r) { return Math.max(m, asNumber(r[cols[0].field])); }, 1);
    return rows.map(function (r) {
      var fill = clamp(asNumber(r[cols[0].field]) / maxFirst * 100, 0, 100).toFixed(1);
      var cells = cols.map(function (c) {
        var v = asNumber(r[c.field]);
        return '<td>' + esc(v ? fmtKgFull(v) : "–") + '</td>';
      }).join("");
      return '<tr class="' + arrivalRowClass(r.date).trim() + '" style="--row-fill:' + fill + '%">' +
        '<td class="kpi-rowbar-day">' + esc(fmtDate(r.date)) + '</td>' + cells + '</tr>';
    }).join("");
  }

  function detailTable(headers, body, extraClass) {
    return '<table class="kpi-detail-rows ' + esc(extraClass || "") + '"><thead><tr>' +
      headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join("") +
      '</tr></thead><tbody>' + body + '</tbody></table>';
  }

  function trackingGroupDetailHtml(group) {
    var payload = trackingPayload || {};
    if (group === "warehouse") {
      var sat = payload.current_warehouse_saturation || {};
      var cap = asNumber(sat.capacity), used = asNumber(sat.kg);
      var free = Math.max(0, cap - used);
      var pct = clamp(asNumber(sat.ratio) * 100, 0, 100);
      var status = saturationStatus(sat.ratio);
      var wa = payload.weekly_avg || {};
      var waIn = wa.inbound || {}, waOut = wa.outbound || {};
      var sr = payload.shift_rates || {};
      var srIn = sr.inbound || {}, srOut = sr.outbound || {};
      var freeAwb = free / 2200;  // átlag 2,2 t / AWB
      return '<div class="kpi-detail-title">Warehouse saturation · ' + esc(status.label) + '</div>' +
        '<div class="kpi-detail-satbar saturation-' + esc(status.cls) +
          '" style="--sat-pct:' + pct.toFixed(1) + '%;--sat-color:' + esc(status.color) + '"><i></i></div>' +
        '<div class="kpi-detail-cells">' +
          statCell("Occupied", used, "kg") +
          statCell("Free", free, "kg") +
          statCell("Capacity", cap, "kg") +
          statCell("Utilization", pct, "pct") +
          statCell("Active items", payload.active_warehouse_items, "items") +
        '</div>' +
        '<div class="kpi-detail-subtitle">Capacity and flow</div>' +
        '<div class="kpi-detail-cells kpi-detail-extra">' +
          '<div class="kpi-detail-cell wh-free"><span>AWB room at 2.2 t avg</span>' +
            cu(freeAwb, "awb") + '</div>' +
          statCell("7-day inbound avg", waIn.kg, "kg_day") +
          statCell("7-day outbound avg", waOut.kg, "kg_day") +
          dualCell("Shift inbound rate", cu(srIn.count_per_h, "perh_cnt"), cu(srIn.kg_per_h, "perh_kg"), "wh-in") +
          dualCell("Shift outbound rate", cu(srOut.count_per_h, "perh_cnt"), cu(srOut.kg_per_h, "perh_kg"), "wh-out") +
        '</div>';
    }
    if (group === "operations") {
      var t = asNumber(payload.to_transfer_12h_kg);
      var r = asNumber(payload.to_release_12h_kg);
      var l = asNumber(payload.ata_more_than_12h_not_transferred_kg);
      var mx = Math.max(t, r, l, 1);
      return '<div class="kpi-detail-title">Next 12 hours</div>' +
        '<div class="kpi-detail-cells kpi-detail-ops">' +
          opCell("To transfer (NOA + ATA)", t, mx, "op-transfer") +
          opCell("To release (releasable)", r, mx, "op-release") +
          opCell("ATA > 12h, not transferred", l, mx, "op-late") +
        '</div>';
    }
    if (group === "arrivals") {
      var live = trackingLiveSnapshot(payload);
      var body = dayRows(payload, [
        { field: "expected_arrivals_kg" },
        { field: "already_arrived_kg" },
        { field: "already_transferred_kg" },
        { field: "remaining_expected_arrivals_kg" },
        { field: "remaining_expected_parcel_kg" },
        { field: "remaining_expected_colli_kg" }
      ]);
      return '<div class="kpi-detail-title">Expected arrivals · daily</div>' +
        detailTable([
          "Day",
          "Expected arrivals",
          "Already arrived",
          "Already transferred",
          "Remaining expected arrivals",
          "Remaining expected arrival (Based on parcel)",
          "Remaining expected arrival (Based on colli)"
        ], body, "kpi-detail-rows-wide") +
        '<div class="kpi-detail-foot">' + esc(live.todayLabel) + ' expected ' +
        cu(live.today.expected, "kg") + ' · yesterday ' + esc(live.yesterdayLabel) + ' ' +
        cu(live.yesterday.expected, "kg") + '</div>';
    }
    if (group === "forecast") {
      var body2 = dayRows(payload, [
        { field: "remaining_expected_parcel_kg" },
        { field: "remaining_expected_colli_kg" }
      ]);
      return '<div class="kpi-detail-title">Unit ratios · daily</div>' +
        detailTable(["Day", "Parcel-based", "Colli-based"], body2, "kpi-detail-rows-forecast");
    }
    return "";
  }

  function markActiveGroup(group) {
    document.querySelectorAll(".kpi-tracking-group[data-group]").forEach(function (g) {
      g.classList.toggle("is-detail-active", g.getAttribute("data-group") === group);
    });
  }

  function fillTrackingDetail(host, group) {
    host.innerHTML = '<button class="kpi-detail-close" aria-label="Close">×</button>' +
      '<div class="kpi-detail-body">' + trackingGroupDetailHtml(group) + '</div>';
  }

  function openTrackingGroup(group) {
    var host = document.getElementById("kpi-tracking-detail");
    if (!host) return;
    if (trackingDetailGroup === group) { closeTrackingDetail(); return; }
    if (!trackingGroupDetailHtml(group)) return;
    if (trackingCloseTimer) { clearTimeout(trackingCloseTimer); trackingCloseTimer = null; }
    var switching = !!trackingDetailGroup;
    trackingDetailGroup = group;
    host.className = "kpi-tracking-detail is-open group-" + group;
    fillTrackingDetail(host, group);
    markActiveGroup(group);
    var body = host.querySelector(".kpi-detail-body");
    if (body) {
      if (switching) { body.classList.remove("kpi-detail-swap"); void body.offsetWidth; body.classList.add("kpi-detail-swap"); }
      runCountUps(body, true);
    }
    setTimeout(function () {
      try {
        host.scrollIntoView({ behavior: prefersReducedMotion() ? "auto" : "smooth", block: "nearest" });
      } catch (e) {
        host.scrollIntoView();
      }
    }, 60);
  }

  var trackingCloseTimer = null;
  function closeTrackingDetail() {
    var host = document.getElementById("kpi-tracking-detail");
    trackingDetailGroup = null;
    document.querySelectorAll(".kpi-tracking-group.is-detail-active").forEach(function (g) {
      g.classList.remove("is-detail-active");
    });
    if (!host) return;
    if (trackingCloseTimer) { clearTimeout(trackingCloseTimer); trackingCloseTimer = null; }
    var wipe = function () { host.className = "kpi-tracking-detail"; host.innerHTML = ""; };
    if (!host.classList.contains("is-open") || prefersReducedMotion()) { wipe(); return; }
    // Collapse as the exact reverse of the open: keep is-open (so all the open
    // visuals stay) and add is-closing, which replays the opening keyframe in
    // reverse. Re-opening clears is-closing (openTrackingGroup rewrites the
    // className), so the timer only wipes if we're still mid-close.
    host.classList.add("is-closing");
    trackingCloseTimer = setTimeout(function () {
      trackingCloseTimer = null;
      if (host.classList.contains("is-closing")) wipe();
    }, 320);
  }

  var trackingObserver = null;
  var trackingOpened = false;
  var trackingRevealPending = false;
  var trackingLastValueSig = "";
  function runTrackingCardCountUps(summary, force) {
    if (!summary) return;
    var values = Array.from(summary.querySelectorAll(".kpi-tracking-value[data-count]"));
    var sig = values.map(function (el) {
      return (el.getAttribute("data-count") || "") + ":" + (el.getAttribute("data-fmt") || "");
    }).join("|");
    if (!force && sig && sig === trackingLastValueSig) return;
    if (sig) trackingLastValueSig = sig;
    values.forEach(function (el, i) {
      queueCountUp(
        el,
        Number(el.getAttribute("data-count")) || 0,
        el.getAttribute("data-fmt") || "kg",
        230 + Math.min(i * 42, 520),
        !!force
      );
    });
  }

  function revealTrackingSection(section) {
    if (!section) return;
    trackingRevealPending = false;
    if (trackingOpened) {
      section.classList.remove("kpi-reveal-pending");
      section.classList.add("is-revealed");
      runTrackingCardCountUps(document.getElementById("kpi-tracking-summary"), false);
      return;
    }
    section.classList.remove("kpi-reveal-pending");
    section.classList.add("is-revealed");
    section.classList.add("is-opening");
    clearTimeout(section.__kpiOpeningTimer);
    section.__kpiOpeningTimer = setTimeout(function () {
      section.classList.remove("is-opening");
    }, 2100);
    trackingOpened = true;
    runTrackingCardCountUps(document.getElementById("kpi-tracking-summary"), true);
  }

  function ensureTrackingObserver() {
    if (trackingObserver || typeof IntersectionObserver === "undefined") return trackingObserver;
    trackingObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!halfVisible(entry)) return;
        trackingObserver.unobserve(entry.target);
        revealTrackingSection(entry.target);
      });
    }, { threshold: HALF_THRESHOLDS });
    return trackingObserver;
  }

  function armTrackingReveal() {
    var section = document.querySelector(".kpi-tracking-section");
    var summary = document.getElementById("kpi-tracking-summary");
    if (!section || !summary) return;
    if (prefersReducedMotion()) {
      section.classList.add("is-revealed");
      runTrackingCardCountUps(summary, false);
      return;
    }
    if (trackingOpened) {
      section.classList.add("is-revealed");
      runTrackingCardCountUps(summary, false);
      return;
    }
    if (trackingRevealPending) return;
    section.classList.add("kpi-reveal-pending");
    var obs = ensureTrackingObserver();
    if (!obs || elementInView(section)) {
      trackingRevealPending = true;
      setTimeout(function () { revealTrackingSection(section); }, 80);
    } else {
      trackingRevealPending = true;
      obs.observe(section);
    }
  }

  // Every card value, keyed by the same data-count-key its <strong> carries — so a
  // poll refresh can patch the numbers in place. The card set never changes, so
  // there is no reason to rebuild the summary DOM each poll (that full innerHTML
  // swap, with the section's backdrop-filter, was the warehouse-tracking flicker).
  function trackingValueList(payload) {
    var sat = payload.current_warehouse_saturation || {};
    var capacity = asNumber(sat.capacity);
    var live = trackingLiveSnapshot(payload);
    var totals = live.today;
    return [
      ["tracking:warehouse-saturation", sat.ratio, "ratio"],
      ["tracking:free-capacity", Math.max(0, capacity - asNumber(sat.kg)), "kg"],
      ["tracking:capacity-used", sat.ratio, "ratio"],
      ["tracking:transfer", payload.to_transfer_12h_kg, "kg"],
      ["tracking:release", payload.to_release_12h_kg, "kg"],
      ["tracking:late", payload.ata_more_than_12h_not_transferred_kg, "kg"],
      ["tracking:expected", totals.expected, "kg"],
      ["tracking:arrived", totals.arrived, "kg"],
      ["tracking:transferred", totals.transferred, "kg"],
      ["tracking:remaining", totals.remaining, "kg"],
      ["tracking:yesterday-expected", live.yesterday.expected, "kg"],
      ["tracking:yesterday-arrived", live.yesterday.arrived, "kg"],
      ["tracking:yesterday-transferred", live.yesterday.transferred, "kg"],
      ["tracking:yesterday-remaining", live.yesterday.remaining, "kg"],
      ["tracking:unit ratio-big kg-parcel", payload.kg_per_parcel, "unit"],
      ["tracking:unit ratio-big kg-colli", payload.kg_per_colli, "unit"],
      ["tracking:unit ratio-big kg-awb", payload.kg_per_awb, "unit"]
    ];
  }

  function trackingDateValueList(payload) {
    var live = trackingLiveSnapshot(payload);
    return [
      ["tracking:expected:today-date", live.todayLabel],
      ["tracking:arrived:today-date", live.todayLabel],
      ["tracking:transferred:today-date", live.todayLabel],
      ["tracking:remaining:today-date", live.todayLabel],
      ["tracking:yesterday-expected:date", live.yesterdayLabel],
      ["tracking:yesterday-arrived:date", live.yesterdayLabel],
      ["tracking:yesterday-transferred:date", live.yesterdayLabel],
      ["tracking:yesterday-remaining:date", live.yesterdayLabel]
    ];
  }

  // Patch the summary numbers + saturation chrome without touching the DOM tree.
  // Returns false if the expected elements aren't there (forces a full rebuild).
  function updateTrackingValues(payload) {
    var summary = document.getElementById("kpi-tracking-summary");
    if (!summary) return false;
    var list = trackingValueList(payload);
    var ok = true;
    list.forEach(function (item) {
      var el = summary.querySelector('.kpi-tracking-value[data-count-key="' + item[0] + '"]');
      if (!el) { ok = false; return; }
      var n = asNumber(item[1]);
      var txt = formatCountValue(n, item[2]);
      // Only touch the DOM when the value actually changed — redundant writes still
      // force a repaint under the section's backdrop-filter (flicker).
      if (el.textContent !== txt) {
        el.setAttribute("data-count", n);
        el.textContent = txt;
      }
      cancelCountUp(el);
      el.__kpiCountTarget = n;
      el.__kpiCountFmt = item[2];
      var stableKey = el.getAttribute("data-count-key") || "";
      if (stableKey) countLastByKey[stableKey] = txt + "|" + item[2];
    });
    if (!ok) return false;
    trackingDateValueList(payload).forEach(function (item) {
      var dateEl = summary.querySelector('.kpi-tracking-date[data-date-key="' + item[0] + '"]');
      if (dateEl && dateEl.textContent !== item[1]) dateEl.textContent = item[1];
    });
    var sat = payload.current_warehouse_saturation || {};
    var capacity = asNumber(sat.capacity);
    var status = saturationStatus(sat.ratio);
    var pct = clamp(asNumber(sat.ratio) * 100, 0, 100);
    var card = summary.querySelector(".kpi-tracking-card.saturation");
    if (card) {
      var cls = "kpi-tracking-card saturation saturation-" + status.cls;
      if (card.className !== cls) card.className = cls;
      var em = card.querySelector(".kpi-saturation-kg");
      var emTxt = fmtKgFull(sat.kg) + (capacity ? " / " + fmtKgFull(capacity) : "");
      if (em && em.textContent !== emTxt) em.textContent = emTxt;
      var small = card.querySelector("small");
      if (small && small.textContent !== status.label) small.textContent = status.label;
      // Patch the progress arc fill without rebuilding the SVG.
      var arcFill = card.querySelector(".kpi-sat-arc-fill");
      if (arcFill) {
        var newFillD = satArcFillPath(sat.ratio);
        if (newFillD) { arcFill.setAttribute("d", newFillD); arcFill.style.stroke = status.color; }
        else { arcFill.setAttribute("d", ""); }
      }
    }
    // Urgency highlight: pulse the "late" card amber when items are overdue.
    var lateValEl = summary.querySelector('.kpi-tracking-value[data-count-key="tracking:late"]');
    if (lateValEl) {
      var lateCard = lateValEl.closest(".kpi-tracking-card");
      if (lateCard) lateCard.classList.toggle("is-urgent", asNumber(payload.ata_more_than_12h_not_transferred_kg) > 0);
    }
    // Update composition bar segments, unit selector and rows in-place (no rebuild needed).
    var comp = payload.status_composition || {};
    var compKeys = ["felveve", "ertesito", "megerkezett", "szemles"];
    var compLabels = { felveve: "Transfer", ertesito: "NOA", megerkezett: "ATA", szemles: "Inspection" };
    var compTotal = compKeys.reduce(function (s, k) { return s + Math.max(0, asNumber(comp[k])); }, 0);
    var compBar = summary.querySelector(".kpi-tracking-comp-bar");
    var compRows = summary.querySelector(".kpi-tracking-comp-rows");
    if (compBar) {
      var segs = compBar.querySelectorAll(".kpi-comp-seg");
      var pctLabels = compBar.querySelectorAll(".kpi-comp-seg-pct");
      var segArr = Array.prototype.slice.call(segs);
      var pctOffset = 0;
      compKeys.forEach(function (k, i) {
        var seg = segArr[i];
        var pctLabelEl = compBar.querySelector('.kpi-comp-seg-pct[data-comp-key="' + k + '"]');
        if (!seg) return;
        var val = Math.max(0, asNumber(comp[k]));
        var pct = compTotal > 0 ? (val / compTotal * 100) : 0;
        if (pct > 0) {
          var mid = pctOffset + pct / 2;
          pctOffset += pct;
          seg.style.width = pct.toFixed(2) + "%";
          seg.style.display = "";
          if (pctLabelEl) {
            pctLabelEl.style.display = "";
            pctLabelEl.style.setProperty("--seg-mid", mid.toFixed(2) + "%");
            pctLabelEl.textContent = pct.toFixed(0) + "%";
            pctLabelEl.classList.toggle("is-tiny", pct < 7);
            pctLabelEl.classList.toggle("is-small", pct >= 7 && pct < 12);
          }
        } else {
          seg.style.display = "none";
          if (pctLabelEl) pctLabelEl.style.display = "none";
        }
        seg.title = compLabels[k] + ": " + fmtKgFull(val) + " · " + pct.toFixed(0) + "%";
      });
    }
    if (compRows) {
      var rowEls = compRows.querySelectorAll(".kpi-comp-row");
      var rowArr = Array.prototype.slice.call(rowEls);
      compKeys.forEach(function (k, i) {
        var row = rowArr[i];
        if (!row) return;
        var val = Math.max(0, asNumber(comp[k]));
        var pct = compTotal > 0 ? (val / compTotal * 100) : 0;
        var pctEl = row.querySelector(".kpi-comp-row-pct");
        var valEl = row.querySelector(".kpi-comp-row-val");
        var pctTxt = pct > 0 ? pct.toFixed(0) + "%" : "—";
        if (pctEl && pctEl.textContent !== pctTxt) pctEl.textContent = pctTxt;
        var valTxt = trackingCompositionValueText(val, trackingUnitMode, payload);
        row.setAttribute("data-val-kg", trackingCompositionValueText(val, "kg", payload));
        row.setAttribute("data-val-parcel", trackingCompositionValueText(val, "parcel", payload));
        row.setAttribute("data-val-colli", trackingCompositionValueText(val, "colli", payload));
        if (valEl && valEl.textContent !== valTxt) valEl.textContent = valTxt;
      });
    }
    syncTrackingUnitMode(summary);
    // Update standalone capacity progress bar in-place.
    var capCard = summary.querySelector(".kpi-capacity-card");
    if (capCard) {
      var newSatColor = status.color;
      if (capCard.style.getPropertyValue("--sat-color") !== newSatColor) capCard.style.setProperty("--sat-color", newSatColor);
      var fillEl = capCard.querySelector(".kpi-capacity-bar-fill");
      if (fillEl) {
        var fillPStr = pct.toFixed(1) + "%";
        if (fillEl.style.getPropertyValue("--sat-pct") !== fillPStr) fillEl.style.setProperty("--sat-pct", fillPStr);
      }
      var awbEl = capCard.querySelector(".kpi-capacity-awb-count");
      if (awbEl) {
        var awbTxt = String(asNumber(payload.active_warehouse_items)) + " AWB";
        if (awbEl.textContent !== awbTxt) awbEl.textContent = awbTxt;
      }
      var pctSmall = capCard.querySelector(".kpi-capacity-pct-label");
      if (pctSmall) {
        var pctSmallTxt = pct.toFixed(1) + "% capacity";
        if (pctSmall.textContent !== pctSmallTxt) pctSmall.textContent = pctSmallTxt;
      }
    }
    return true;
  }

  function selectTrackingUnitMode(mode, event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (mode !== "kg" && mode !== "parcel" && mode !== "colli") mode = "kg";
    trackingUnitMode = mode;
    var summary = document.getElementById("kpi-tracking-summary");
    animateTrackingUnitMode(summary || document, mode);
  }

  function animateTrackingUnitMode(root, mode) {
    root = root || document;
    var values = Array.prototype.slice.call(root.querySelectorAll(".kpi-comp-row-val"));
    if (!values.length) {
      applyTrackingUnitMode(root, mode);
      return;
    }
    values.forEach(function (el, i) {
      el.style.setProperty("--unit-val-delay", (i * 34) + "ms");
      el.classList.remove("is-entering");
      el.classList.add("is-leaving");
    });
    clearTimeout(root.__kpiUnitValueTimer);
    root.__kpiUnitValueTimer = setTimeout(function () {
      applyTrackingUnitMode(root, mode);
      values.forEach(function (el, i) {
        el.style.setProperty("--unit-val-delay", (i * 38 + 24) + "ms");
        el.classList.remove("is-leaving");
        el.classList.add("is-entering");
      });
      clearTimeout(root.__kpiUnitValueInTimer);
      root.__kpiUnitValueInTimer = setTimeout(function () {
        values.forEach(function (el) {
          el.classList.remove("is-entering", "is-leaving");
        });
      }, 330);
    }, 115);
  }

  function applyTrackingUnitMode(root, mode) {
    root = root || document;
    mode = mode || "kg";
    var control = root.querySelector(".kpi-unit-mode");
    if (!control) return;
    var opts = ["kg", "colli", "parcel"];
    var idx = Math.max(0, opts.indexOf(mode));
    control.setAttribute("data-mode", mode);
    control.style.setProperty("--unit-mode-index", String(idx));
    control.classList.remove("is-morphing");
    void control.offsetWidth;
    control.classList.add("is-morphing");
    clearTimeout(control.__kpiUnitMorphTimer);
    control.__kpiUnitMorphTimer = setTimeout(function () {
      control.classList.remove("is-morphing");
    }, 280);
    Array.prototype.slice.call(control.querySelectorAll(".kpi-unit-mode-btn")).forEach(function (btn) {
      var on = btn.getAttribute("data-unit-mode") === mode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      if (!btn.__kpiUnitModeBound) {
        btn.__kpiUnitModeBound = true;
        btn.onclick = function (event) {
          selectTrackingUnitMode(btn.getAttribute("data-unit-mode"), event);
          return false;
        };
      }
    });
    Array.prototype.slice.call(root.querySelectorAll(".kpi-comp-row")).forEach(function (row) {
      var val = row.getAttribute("data-val-" + mode);
      var valEl = row.querySelector(".kpi-comp-row-val");
      if (valEl && val) valEl.textContent = val;
    });
  }

  function syncTrackingUnitMode(root) {
    root = root || document;
    var control = root.querySelector(".kpi-unit-mode");
    var mode = (control && control.getAttribute("data-mode")) || trackingUnitMode || "kg";
    if (mode !== "kg" && mode !== "parcel" && mode !== "colli") mode = "kg";
    trackingUnitMode = mode;
    applyTrackingUnitMode(root, mode);
  }

  function syncOpenTrackingDetail() {
    if (!trackingDetailGroup) return;
    var host = document.getElementById("kpi-tracking-detail");
    if (host && trackingGroupDetailHtml(trackingDetailGroup)) {
      fillTrackingDetail(host, trackingDetailGroup);
      markActiveGroup(trackingDetailGroup);
      runCountUps(host.querySelector(".kpi-detail-body"), false);
    } else {
      closeTrackingDetail();
    }
  }

  function renderTracking(payload) {
    payload = payload || {};
    var summary = document.getElementById("kpi-tracking-summary");
    if (!summary) return;
    trackingPayload = payload;
    var sig = trackingPayloadSignature(payload);
    if (sig && sig === lastTrackingSig && summary.firstChild) {
      armTrackingReveal();
      return;
    }
    // Structure is fixed, so once it's been built just patch the numbers in place
    // (no DOM rebuild → no flicker). Only build the full tree the first time or if
    // it was wiped / the elements went missing.
    if (summary.firstChild && updateTrackingValues(payload)) {
      lastTrackingSig = sig;
      syncOpenTrackingDetail();
      armTrackingReveal();
      return;
    }
    lastTrackingSig = sig;

    var saturation = payload.current_warehouse_saturation || {};
    var capacity = asNumber(saturation.capacity);
    var live = trackingLiveSnapshot(payload);
    trackingGroupSeq = 0;
    trackingCardSeq = 0;

    summary.innerHTML = [
      trackingGroup("Warehouse", "warehouse", "warehouse", [
        saturationCard(saturation, capacity),
        capacityProgressBar(saturation, capacity, asNumber(payload.active_warehouse_items))
      ]),
      trackingGroup("12h operations", "operations", "operations", [
        trackingCard("To transfer (NOA + ATA)", payload.to_transfer_12h_kg, "transfer", "kg"),
        trackingCard("To release (releasable)", payload.to_release_12h_kg, "release", "kg"),
        trackingCard("ATA > 12h, not transferred", payload.ata_more_than_12h_not_transferred_kg, "late span-full", "kg")
      ]),
      trackingGroup("Expected arrivals", "arrivals", "arrivals", [
        trackingDatedCard("Based on colli expected arrivals", live.today.expected, "expected", "kg", live.todayLabel),
        trackingDatedCard("Already arrived", live.today.arrived, "arrived", "kg", live.todayLabel),
        trackingDatedCard("Already transferred", live.today.transferred, "transferred", "kg", live.todayLabel),
        trackingDatedCard("Remaining expected", live.today.remaining, "remaining", "kg", live.todayLabel),
        trackingPreviousSet(live.yesterdayLabel + " previous day", [
          trackingPreviousCard("Expected arrivals", live.yesterday.expected, "expected", "kg", live.yesterdayLabel),
          trackingPreviousCard("Already arrived", live.yesterday.arrived, "arrived", "kg", live.yesterdayLabel),
          trackingPreviousCard("Already transferred", live.yesterday.transferred, "transferred", "kg", live.yesterdayLabel),
          trackingPreviousCard("Remaining expected", live.yesterday.remaining, "remaining", "kg", live.yesterdayLabel)
        ])
      ]),
      trackingGroup("Unit ratios", "forecast", "forecast", [
        trackingCompositionDiagram(payload.status_composition, payload),
        trackingCard("Kg / parcel", payload.kg_per_parcel, "unit ratio-big kg-parcel", "unit"),
        trackingCard("Kg / colli", payload.kg_per_colli, "unit ratio-big kg-colli", "unit"),
        trackingCard("Kg / AWB", payload.kg_per_awb, "unit ratio-big kg-awb", "unit")
      ])
    ].join("");

    syncTrackingUnitMode(summary);
    // Initial urgency state after a full rebuild.
    var lateEl = summary.querySelector('.kpi-tracking-value[data-count-key="tracking:late"]');
    if (lateEl) {
      var lateCardEl = lateEl.closest(".kpi-tracking-card");
      if (lateCardEl) lateCardEl.classList.toggle("is-urgent", asNumber(payload.ata_more_than_12h_not_transferred_kg) > 0);
    }
    // Keep an open dropdown in sync after a full rebuild (no re-animation).
    syncOpenTrackingDetail();
    armTrackingReveal();
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest(".kpi-detail-close")) {
      closeTrackingDetail();
      return;
    }
    var modeBtn = event.target.closest && event.target.closest(".kpi-unit-mode-btn[data-unit-mode]");
    if (modeBtn) {
      selectTrackingUnitMode(modeBtn.getAttribute("data-unit-mode") || "kg", event);
      return;
    }
    var grp = event.target.closest && event.target.closest(".kpi-tracking-group[data-group]");
    if (grp) openTrackingGroup(grp.getAttribute("data-group"));
  });

  // Keyboard activation for the role=button KPI elements (metric cards + groups).
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    var el = event.target;
    if (!el || !el.classList) return;
    if (el.classList.contains("kpi-page-metric") && el.getAttribute("data-flow")) {
      event.preventDefault();
      switchFlowView(el.getAttribute("data-flow"), el.getAttribute("data-view"));
    } else if (el.classList.contains("kpi-flow-mode-btn") && el.getAttribute("data-flow-mode")) {
      event.preventDefault();
      setFlowMode(el.getAttribute("data-flow-mode"));
    } else if (el.classList.contains("kpi-flow-period-btn") && el.getAttribute("data-flow-days")) {
      event.preventDefault();
      flowDayRange = el.getAttribute("data-flow-days") === "14" ? 14 : 7;
      flowDaySelected = -1;
      syncFlowPeriodControl();
      renderDayFlow(flowDayPayload, true, flowMode === "day");
    } else if (el.classList.contains("kpi-flow-unit-btn") && el.getAttribute("data-flow-unit")) {
      event.preventDefault();
      var unit = el.getAttribute("data-flow-unit") || "kg";
      flowDayMetric = unit === "parcel" || unit === "colli" ? unit : "kg";
      flowDaySelected = -1;
      syncFlowUnitControl();
      renderDayFlow(flowDayPayload, true, flowMode === "day");
    } else if (el.classList.contains("kpi-tracking-group") && el.getAttribute("data-group")) {
      event.preventDefault();
      openTrackingGroup(el.getAttribute("data-group"));
    }
  });

  window.__renderKpiPageCharts = function (payload) {
    if (!payload) return;
    var updated = document.getElementById("kpi-page-updated");
    if (updated) updated.textContent = payload.updated ? ("Updated: " + payload.updated) : "";
    renderFlow("inbound", payload.inbound);
    renderFlow("outbound", payload.outbound);
    renderDayFlow(payload.time_bands, false);
    syncFlowModeControls();
    renderTracking(payload.tracking);
    if (window.__renderKpiTrend) window.__renderKpiTrend(payload.trend, payload.updated);
    if (window.__renderKpiTemu) window.__renderKpiTemu(payload.temu_stages);
    if (window.__renderKpiBands) window.__renderKpiBands(payload.time_bands);
  };

  // Re-play the opening animations (line draw-on, kg count-up, tracking reveal)
  // each time the KPI view is entered. app.py's view-mode clientside callback
  // dispatches "kpi-view-entered"; the card entrance itself is CSS tied to
  // body.view-kpi-mode (auto-replays). Resetting the dedup tokens here makes the
  // next render redraw the lines instead of snapping them solid — so the opening
  // plays on a plain page switch, not only after a data refresh.
  function replayKpiPageOpening() {
    var now = Date.now();
    if (now - lastKpiReplayAt < 1800) return;
    lastKpiReplayAt = now;
    flowChartStartedSig = {};
    flowRenderSig = {};
    countLastByKey = {};
    trackingLastValueSig = "";
    if (flowData.inbound) renderFlow("inbound", flowData.inbound);
    if (flowData.outbound) renderFlow("outbound", flowData.outbound);
    if (flowDayPayload) renderDayFlow(flowDayPayload, true);
    var section = document.querySelector(".kpi-tracking-section");
    if (section) section.classList.remove("is-revealed", "is-opening", "kpi-reveal-pending");
    trackingOpened = false;
    trackingRevealPending = false;
    if (trackingPayload) armTrackingReveal();
  }
  window.addEventListener("kpi-view-entered", function () {
    if (replayKpiTimer) clearTimeout(replayKpiTimer);
    replayKpiTimer = setTimeout(function () {
      replayKpiTimer = 0;
      replayKpiPageOpening();
    }, 60);
  });
})();
