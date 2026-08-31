/* KPI page snap controller.
 *
 * Converts the KPI page from free vertical scrolling into one-panel-at-a-time
 * navigation. It also arms per-block reveal units so staggered openings start
 * when the block itself becomes visible, not when its parent section first enters.
 */
(function () {
  "use strict";

  if (window.__kpiSnapInit) return;
  window.__kpiSnapInit = true;

  var WHEEL_THRESHOLD = 180;
  var TOUCH_THRESHOLD = 48;
  var SNAP_LOCK_MS = 620;
  var SEGMENT_EDGE_TOLERANCE = 18;
  var wheelAcc = 0;
  var wheelTimer = 0;
  var snapLockedUntil = 0;
  var touchStartY = 0;
  var touchStartX = 0;
  var touchTracking = false;
  var revealObserver = null;
  var mutationObserver = null;
  var refreshTimer = 0;
  var alignTimer = 0;

  var REVEAL_SELECTORS = [
    ".kpi-page-head",
    "#kpi-trend-section .kpi-trend-head",
    "#kpi-trend-section .kpi-trend-range",
    "#kpi-trend-section .kpi-trend-filter",
    "#kpi-trend-section .kpi-trend-chart-shell",
    "#kpi-trend-section .kpi-trend-table-panel",
    "#kpi-temu-section .kpi-temu-head",
    "#kpi-temu-section .kpi-temu-filter",
    "#kpi-temu-section .kpi-temu-range",
    "#kpi-temu-section .kpi-temu-headline-main",
    "#kpi-temu-section .kpi-temu-peak",
    "#kpi-temu-section .kpi-temu-stage-card",
    "#kpi-temu-section .kpi-temu-chart-shell",
    "#kpi-temu-section .kpi-temu-suspects-panel",
    "#kpi-temu-section .kpi-temu-table-panel",
    ".kpi-flow-section .kpi-page-chart-card",
    ".kpi-tracking-section .kpi-tracking-head",
    ".kpi-tracking-section .kpi-tracking-group",
    ".kpi-tracking-section .kpi-tracking-detail",
    ".kpi-bands-section .kpi-bands-head",
    ".kpi-bands-section .kpi-bands-chip",
    ".kpi-bands-section .kpi-bands-chart",
    ".kpi-bands-section .kpi-bands-card"
  ].join(",");

  function isKpiMode() {
    return document.body && document.body.classList.contains("view-kpi-mode");
  }

  function page() {
    return document.getElementById("kpi-page");
  }

  function segments() {
    var host = page();
    if (!host) return [];
    return Array.prototype.slice.call(host.querySelectorAll(".kpi-snap-section"))
      .filter(function (el) {
        return !!(el && el.getClientRects && el.getClientRects().length);
      });
  }

  function viewportH() {
    return window.innerHeight || document.documentElement.clientHeight || 1;
  }

  function snapOffset() {
    var header = document.querySelector(".app-header");
    if (!header || !header.getBoundingClientRect) return 8;
    var r = header.getBoundingClientRect();
    return Math.max(8, Math.ceil(Math.max(0, r.bottom)) + 10);
  }

  function snapViewportH() {
    return Math.max(320, viewportH() - snapOffset() - 8);
  }

  function syncSnapMetrics() {
    var offset = snapOffset();
    document.documentElement.classList.toggle("view-kpi-mode-root", isKpiMode());
    document.documentElement.style.setProperty("--kpi-snap-offset", offset + "px");
    document.documentElement.style.setProperty("--kpi-snap-visible-h", Math.max(320, viewportH() - offset - 8) + "px");
  }

  function activeIndex(list) {
    list = list || segments();
    if (!list.length) return -1;
    // At the very bottom of the page the LAST panel can't scroll its top up to the
    // snap anchor, so the top<=anchor test would pick the second-to-last. Treat the
    // last panel as active once the page is scrolled to the bottom and it's visible.
    var docEl = document.documentElement;
    if ((window.innerHeight + window.scrollY) >= (docEl.scrollHeight - 4)) {
      var lastR = list[list.length - 1].getBoundingClientRect();
      if (lastR.top < viewportH() && lastR.bottom > snapOffset()) return list.length - 1;
    }
    var anchor = snapOffset() + 4;
    for (var j = 0; j < list.length; j++) {
      var jr = list[j].getBoundingClientRect();
      if (jr.top <= anchor && jr.bottom > anchor) return j;
    }
    var best = 0;
    var bestDist = Infinity;
    list.forEach(function (el, i) {
      var r = el.getBoundingClientRect();
      var dist = Math.abs(r.top - anchor);
      if (dist < bestDist) {
        best = i;
        bestDist = dist;
      }
    });
    return best;
  }

  function scrollToSegment(index) {
    try { window.dispatchEvent(new CustomEvent("kpi-segment-changing")); } catch (e) {}
    var list = segments();
    if (!list.length) return;
    markBgActivity();
    index = Math.max(0, Math.min(list.length - 1, index));
    var target = list[index];
    snapLockedUntil = Date.now() + SNAP_LOCK_MS;
    // Section scripts watch this so their reveal/opening doesn't fire mid-scroll
    // (which looked like the chart "popping in", then re-animating on settle).
    window.__kpiSnapActive = true;
    target.classList.add("is-kpi-snap-target");
    list.forEach(function (el, i) {
      el.classList.toggle("is-kpi-snap-active", i === index);
    });
    syncSnapMetrics();
    function targetTop() {
      return Math.max(0, window.scrollY + target.getBoundingClientRect().top - snapOffset());
    }
    var top = targetTop();
    try {
      window.scrollTo({
        top: Math.max(0, top),
        behavior: reduceMotion() ? "auto" : "smooth",
      });
    } catch (e) {
      target.scrollIntoView();
    }
    setTimeout(function () {
      target.classList.remove("is-kpi-snap-target");
    }, SNAP_LOCK_MS + 80);
    setTimeout(function () {
      syncSnapMetrics();
      var corrected = targetTop();
      if (Math.abs(window.scrollY - corrected) > 3) {
        window.scrollTo({
          top: corrected,
          behavior: reduceMotion() ? "auto" : "smooth"
        });
      }
      // Settled on this panel — let its section play its opening NOW (clean single
      // run, not mid-scroll). Section scripts opt in by id/section.
      window.__kpiSnapActive = false;
      try { window.dispatchEvent(new CustomEvent("kpi-segment-settled", { detail: { id: target.id, section: target } })); }
      catch (e) {}
    }, reduceMotion() ? 40 : SNAP_LOCK_MS + 20);
  }

  function activeSegment(list) {
    list = list || segments();
    if (!list.length) return null;
    for (var i = 0; i < list.length; i++) {
      if (list[i].classList.contains("is-kpi-snap-active")) return list[i];
    }
    var idx = activeIndex(list);
    return idx >= 0 ? list[idx] : null;
  }

  function realignActiveSegment(delay) {
    clearTimeout(alignTimer);
    alignTimer = setTimeout(function () {
      alignTimer = 0;
      if (!isKpiMode()) return;
      if (Date.now() < snapLockedUntil) {
        realignActiveSegment(Math.max(80, snapLockedUntil - Date.now() + 60));
        return;
      }
      syncSnapMetrics();
      var target = activeSegment();
      if (!target) return;
      var desired = Math.max(0, window.scrollY + target.getBoundingClientRect().top - snapOffset());
      if (Math.abs(window.scrollY - desired) > 4) {
        window.scrollTo({ top: desired, behavior: reduceMotion() ? "auto" : "smooth" });
      }
    }, delay == null ? 180 : delay);
  }

  function reduceMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // Freeze the moving KPI background during scroll/snap interaction (and a short
  // idle tail after), so the only thing compositing while the user scrolls is the
  // scroll itself — keeps the portable .exe renderer smooth. The blobs resume
  // their slow drift from where they froze once interaction settles.
  var bgPauseTimer = 0;
  function markBgActivity() {
    if (!isKpiMode()) return;
    document.body.classList.add("kpi-bg-paused");
    clearTimeout(bgPauseTimer);
    bgPauseTimer = setTimeout(function () {
      document.body.classList.remove("kpi-bg-paused");
    }, 320);
  }

  function isEditableTarget(target) {
    if (!target || !target.closest) return false;
    return !!target.closest("input, textarea, select, [contenteditable='true']");
  }

  function canScrollInside(target, deltaY) {
    if (!target || !target.closest || Math.abs(deltaY) < 1) return false;
    var node = target;
    var host = page();
    while (node && node !== document.body && node !== host) {
      if (node.classList && node.classList.contains("kpi-snap-section")) return false;
      var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
      var oy = style ? style.overflowY : "";
      var scrollable = /(auto|scroll|overlay)/.test(oy) && node.scrollHeight > node.clientHeight + 2;
      if (scrollable) {
        var max = node.scrollHeight - node.clientHeight;
        if (deltaY > 0 && node.scrollTop < max - 1) return true;
        if (deltaY < 0 && node.scrollTop > 1) return true;
      }
      node = node.parentElement;
    }
    return false;
  }

  // A section taller than the viewport is revealed in discrete "pages" (≈ one
  // visible viewport per gesture) instead of native free-scrolling — so EVERY wheel
  // gesture is a deterministic, snappy move and the snap stays active the whole way
  // down the page. Returns true if it paged (and therefore handled the gesture).
  function pageWithinActiveSection(dir) {
    var list = segments();
    var idx = activeIndex(list);
    if (idx < 0) return false;
    var r = list[idx].getBoundingClientRect();
    var top = snapOffset();
    var vh = viewportH();
    var step = Math.max(140, (vh - top) * 0.86);
    if (dir > 0 && r.bottom > vh + SEGMENT_EDGE_TOLERANCE) {
      var room = r.bottom - vh;                         // still hidden below
      snapLockedUntil = Date.now() + SNAP_LOCK_MS;
      window.scrollTo({ top: window.scrollY + Math.min(step, room + 2), behavior: reduceMotion() ? "auto" : "smooth" });
      return true;
    }
    if (dir < 0 && r.top < top - SEGMENT_EDGE_TOLERANCE) {
      var roomUp = top - r.top;                         // hidden above the snap line
      snapLockedUntil = Date.now() + SNAP_LOCK_MS;
      window.scrollTo({ top: Math.max(0, window.scrollY - Math.min(step, roomUp + 2)), behavior: reduceMotion() ? "auto" : "smooth" });
      return true;
    }
    return false;
  }

  function wheelToPanel(event) {
    if (!isKpiMode()) return;
    markBgActivity();
    var host = page();
    if (!host) return;
    var insidePage = host.contains(event.target);
    var insideHeader = event.target && event.target.closest && event.target.closest(".app-header");
    if (!insidePage && !insideHeader && event.target !== document.body && event.target !== document.documentElement) return;
    if (Math.abs(event.deltaX || 0) > Math.abs(event.deltaY || 0)) return;
    if (insidePage && canScrollInside(event.target, event.deltaY)) return;
    event.preventDefault();
    if (Date.now() < snapLockedUntil) return;
    wheelAcc += event.deltaY || 0;
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(function () { wheelAcc = 0; }, 170);
    if (Math.abs(wheelAcc) < WHEEL_THRESHOLD) return;
    var dir = wheelAcc > 0 ? 1 : -1;
    wheelAcc = 0;
    // Reveal the rest of a tall section first; only snap to the neighbour once it's
    // fully shown — this keeps the snap "active" everywhere instead of going free.
    if (pageWithinActiveSection(dir)) return;
    var list = segments();
    var idx = activeIndex(list);
    if (idx < 0) return;
    scrollToSegment(idx + dir);
  }

  function keyToPanel(event) {
    if (!isKpiMode() || isEditableTarget(event.target)) return;
    var dir = 0;
    if (event.key === "ArrowDown" || event.key === "PageDown" || event.key === " ") dir = 1;
    else if (event.key === "ArrowUp" || event.key === "PageUp") dir = -1;
    else if (event.key === "Home") {
      event.preventDefault();
      scrollToSegment(0);
      return;
    } else if (event.key === "End") {
      var list = segments();
      event.preventDefault();
      scrollToSegment(list.length - 1);
      return;
    }
    if (!dir) return;
    event.preventDefault();
    if (Date.now() < snapLockedUntil) return;
    scrollToSegment(activeIndex() + dir);
  }

  function touchStart(event) {
    if (!isKpiMode() || !event.touches || event.touches.length !== 1) return;
    var host = page();
    if (!host || !host.contains(event.target)) return;
    touchTracking = true;
    touchStartY = event.touches[0].clientY;
    touchStartX = event.touches[0].clientX;
  }

  function touchMove(event) {
    if (!touchTracking || !event.touches || event.touches.length !== 1) return;
    var dy = touchStartY - event.touches[0].clientY;
    var dx = touchStartX - event.touches[0].clientX;
    if (Math.abs(dx) > Math.abs(dy)) return;
    if (canScrollInside(event.target, dy)) return;
    event.preventDefault();
  }

  function touchEnd(event) {
    if (!touchTracking) return;
    touchTracking = false;
    var changed = event.changedTouches && event.changedTouches[0];
    if (!changed || Date.now() < snapLockedUntil) return;
    var dy = touchStartY - changed.clientY;
    var dx = touchStartX - changed.clientX;
    if (Math.abs(dx) > Math.abs(dy) || Math.abs(dy) < TOUCH_THRESHOLD) return;
    scrollToSegment(activeIndex() + (dy > 0 ? 1 : -1));
  }

  function visibleEnough(entry) {
    if (!entry || !entry.isIntersecting) return false;
    if (entry.intersectionRatio >= 0.34) return true;
    var ir = entry.intersectionRect;
    var rootH = (entry.rootBounds && entry.rootBounds.height) || viewportH();
    return !!(ir && rootH && ir.height >= rootH * 0.22);
  }

  function markVisible(el) {
    if (!el || el.classList.contains("is-kpi-unit-visible")) return;
    el.classList.add("is-kpi-unit-visible");
    if (revealObserver) revealObserver.unobserve(el);
  }

  function collectRevealUnits() {
    var host = page();
    if (!host) return [];
    var raw = Array.prototype.slice.call(host.querySelectorAll(REVEAL_SELECTORS));
    var out = [];
    raw.forEach(function (el) {
      if (!el || !el.getClientRects || !el.getClientRects().length) return;
      if (out.indexOf(el) >= 0) return;
      out.push(el);
    });
    var sectionSeq = new Map();
    out.forEach(function (el) {
      var sec = el.closest(".kpi-snap-section") || host;
      var seq = sectionSeq.get(sec) || 0;
      sectionSeq.set(sec, seq + 1);
      el.classList.add("kpi-reveal-unit");
      el.style.setProperty("--kpi-unit-delay", Math.min(seq * 48, 280) + "ms");
    });
    return out;
  }

  function refreshRevealUnits(resetVisible) {
    var host = page();
    if (!host) return;
    document.body.classList.add("kpi-snap-ready");
    var units = collectRevealUnits();
    if (resetVisible && !reduceMotion()) {
      units.forEach(function (el) {
        el.classList.remove("is-kpi-unit-visible");
      });
    }
    if (reduceMotion() || typeof IntersectionObserver === "undefined") {
      units.forEach(markVisible);
      return;
    }
    if (!revealObserver) {
      revealObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (visibleEnough(entry)) markVisible(entry.target);
        });
      }, {
        root: null,
        rootMargin: "-8% 0px -10% 0px",
        threshold: [0, 0.18, 0.34, 0.5, 0.75, 1]
      });
    }
    units.forEach(function (el) {
      if (el.classList.contains("is-kpi-unit-visible")) return;
      var r = el.getBoundingClientRect();
      var visible = Math.max(0, Math.min(r.bottom, viewportH()) - Math.max(r.top, snapOffset()));
      if (visible >= Math.min(r.height * 0.34, snapViewportH() * 0.42)) markVisible(el);
      else revealObserver.observe(el);
    });
  }

  function scheduleRevealRefresh(resetVisible) {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      refreshTimer = 0;
      refreshRevealUnits(!!resetVisible);
    }, 80);
  }

  function setupMutationObserver() {
    if (mutationObserver) return;
    var host = page();
    if (!host || typeof MutationObserver === "undefined") return;
    mutationObserver = new MutationObserver(function () {
      scheduleRevealRefresh(false);
    });
    mutationObserver.observe(host, { childList: true, subtree: true });
  }

  var lastActiveIdx = -1;
  function syncActiveClass() {
    if (!isKpiMode()) return;
    syncSnapMetrics();
    var list = segments();
    var idx = activeIndex(list);
    list.forEach(function (el, i) {
      el.classList.toggle("is-kpi-snap-active", i === idx);
    });
    // Robustness net: if the active panel changed by free-scroll/resize/keyboard
    // (NOT a snap animation — scrollToSegment already fires its own settle), let the
    // panel play its opening so a chart can never get stuck unrevealed. Section
    // scripts dedupe rapid repeats.
    if (idx >= 0 && idx !== lastActiveIdx) {
      lastActiveIdx = idx;
      if (!window.__kpiSnapActive) {
        var t = list[idx];
        try { window.dispatchEvent(new CustomEvent("kpi-segment-settled", { detail: { id: t.id, section: t } })); } catch (e) {}
      }
    }
    buildDots(list);
    updateDots(idx, list);
  }

  // ── panel-dots navigator (orientation aid) ──────────────────────────────────
  var dotsRail = null;
  var SECTION_NAMES = {
    "kpi-tracking-section": "KPI Tracking",
    "kpi-flow-section": "SHIFT VOLUME",
    "kpi-bands-section": "TIME BANDS",
    "kpi-temu-section": "TEMU KPI",
    "kpi-trend-section": "VOLUME TREND"
  };
  function sectionName(el, i) {
    for (var k in SECTION_NAMES) { if (el && el.classList && el.classList.contains(k)) return SECTION_NAMES[k]; }
    return "Panel " + (i + 1);
  }
  function buildDots(list) {
    list = list || segments();
    if (!list.length) return;
    if (!dotsRail) {
      dotsRail = document.createElement("div");
      dotsRail.className = "kpi-snap-dots";
      dotsRail.addEventListener("click", function (e) {
        var d = e.target.closest && e.target.closest("[data-snap-dot]");
        if (d) scrollToSegment(parseInt(d.getAttribute("data-snap-dot"), 10) || 0);
      });
      document.body.appendChild(dotsRail);
    }
    // Recolour to match the active subpage environment: a cool cyan→blue ramp on
    // the Tracking subpage, a warm green→amber→red ramp on the Riport subpage.
    var report = !!(document.body && document.body.classList.contains("kpi-sub-report"));
    var sig = list.length + (report ? "R" : "T");
    if (dotsRail.dataset.sig !== sig) {
      var n = list.length;
      // [top]→[bottom] endpoints per palette (cyan→blue tracking, violet→blue report).
      var c0 = report ? [162, 142, 250] : [126, 232, 226];
      var c1 = report ? [90, 110, 240]  : [56, 122, 214];
      dotsRail.innerHTML = list.map(function (el, i) {
        var nm = sectionName(el, i);
        var t = n > 1 ? i / (n - 1) : 0;
        var r = Math.round(c0[0] + (c1[0] - c0[0]) * t);
        var g = Math.round(c0[1] + (c1[1] - c0[1]) * t);
        var b = Math.round(c0[2] + (c1[2] - c0[2]) * t);
        return '<button type="button" class="kpi-snap-dot" data-snap-dot="' + i +
          '" style="--dot-bg: rgb(' + r + ',' + g + ',' + b + ')" title="' + nm +
          '" aria-label="' + nm + '"><span class="kpi-snap-dot-label">' + nm + '</span></button>';
      }).join("");
      dotsRail.dataset.sig = sig;
      dotsRail.dataset.count = String(list.length);
    }
  }
  function updateDots(idx, list) {
    if (!dotsRail) return;
    list = list || segments();
    var dots = dotsRail.querySelectorAll(".kpi-snap-dot");
    for (var i = 0; i < dots.length; i++) dots[i].classList.toggle("is-active", i === idx);
    // Intra-panel progress (how far through a tall active section we've paged).
    var prog = 0;
    if (idx >= 0 && list[idx]) {
      var r = list[idx].getBoundingClientRect();
      var top = snapOffset();
      var scrollable = Math.max(1, r.height - (viewportH() - top));
      prog = Math.max(0, Math.min(1, (top - r.top) / scrollable));
    }
    dotsRail.style.setProperty("--snap-progress", prog.toFixed(3));
  }

  function setup() {
    document.addEventListener("wheel", wheelToPanel, { passive: false, capture: true });
    document.addEventListener("keydown", keyToPanel, true);
    document.addEventListener("touchstart", touchStart, { passive: true, capture: true });
    document.addEventListener("touchmove", touchMove, { passive: false, capture: true });
    document.addEventListener("touchend", touchEnd, { passive: true, capture: true });
    window.addEventListener("resize", function () {
      syncSnapMetrics();
      scheduleRevealRefresh(false);
      if (isKpiMode()) setTimeout(syncActiveClass, 120);
    });
    window.addEventListener("scroll", function () {
      if (!isKpiMode()) return;
      markBgActivity();
      syncSnapMetrics();
      clearTimeout(window.__kpiSnapScrollTimer);
      window.__kpiSnapScrollTimer = setTimeout(syncActiveClass, 90);
    }, { passive: true });
    document.addEventListener("visibilitychange", function () {
      document.body.classList.toggle("kpi-bg-hidden", document.hidden);
    });
    // Re-arm the snap + reveal observers when the KPI page (or a subpage) is shown.
    // resetVisible is FALSE: a unit that has already played its opening stays
    // revealed — openings run ONCE, never replay on re-show / scroll-back.
    function onKpiShown() {
      setTimeout(function () {
        syncSnapMetrics();
        setupMutationObserver();
        refreshRevealUnits(false);
        syncActiveClass();
      }, 120);
    }
    window.addEventListener("kpi-view-entered", onKpiShown);
    // Dispatched on a subpage switch to an ALREADY-opened subpage: snap/dots only,
    // no chart-module replay (the chart modules don't listen to this event).
    window.addEventListener("kpi-subview-shown", onKpiShown);
    setTimeout(function () {
      syncSnapMetrics();
      setupMutationObserver();
      refreshRevealUnits(false);
      syncActiveClass();
    }, 180);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", setup, { once: true });
  } else {
    setup();
  }
})();
