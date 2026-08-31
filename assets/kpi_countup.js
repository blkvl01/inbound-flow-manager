/* KPI count-up: when a .kpi-countup element's value changes (IN/OUT toggle or a
   data refresh), it quickly moves to the new value (<= ~1.2s) and re-formats as
   "N kg" with space-grouped thousands - matching _fmt_kg().
   Dash can update these values while a previous animation is still running, so
   external text changes interrupt and retarget the animation. */
(function () {
  "use strict";

  var DUR = 1200; // <= 2s, per request

  function reduced() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function parseNum(s) {
    var d = String(s == null ? "" : s).replace(/[^0-9]/g, "");
    return d ? parseInt(d, 10) : 0;
  }
  function fmt(n) {
    // toLocaleString('hu-HU') can group thousands with a non-breaking space;
    // normalise to a plain space so it matches the server-rendered _fmt_kg().
    return Math.round(n).toLocaleString("hu-HU").replace(/\u00a0/g, " ") + " kg";
  }
  function isBlankValue(txt) {
    return txt === "" || txt === "-" || txt === "\u2013";
  }

  function writeValue(el, text) {
    el.__kpInternalWrite = true;
    el.textContent = text;
    setTimeout(function () {
      el.__kpInternalWrite = false;
    }, 0);
  }

  function animate(el, target, from) {
    el.__kpAnim = true;
    el.__kpTarget = target;
    if (el.__kpRaf) cancelAnimationFrame(el.__kpRaf);
    from = Math.max(0, Number(from) || 0);
    if (reduced()) {
      writeValue(el, fmt(target));
      el.__kpAnim = false;
      el.__kpLast = target;
      return;
    }
    var t0 = 0;
    function step(now) {
      if (!t0) t0 = now;
      var p = Math.min(1, (now - t0) / DUR);
      var eased = 1 - Math.pow(1 - p, 3);
      var v = from + (target - from) * eased;
      writeValue(el, fmt(v));
      if (p < 1) {
        el.__kpRaf = requestAnimationFrame(step);
      } else {
        writeValue(el, fmt(target));
        el.__kpRaf = 0;
        el.__kpAnim = false;
        el.__kpLast = target;
      }
    }
    el.__kpRaf = requestAnimationFrame(step);
  }

  function replayOpening() {
    if (replayOpening.done) return;
    replayOpening.done = true;
    document.querySelectorAll(".kpi-countup").forEach(function (el) {
      var target = parseNum(el.textContent);
      if (target > 0) animate(el, target, 0);
    });
  }

  function check(el) {
    if (el.__kpInternalWrite) return;
    var txt = (el.textContent || "").trim();
    if (isBlankValue(txt)) {
      el.__kpLast = 0;
      el.__kpTarget = 0;
      return;
    }
    var target = parseNum(txt);
    if (target === el.__kpTarget && (el.__kpAnim || target === el.__kpLast)) return;
    var from = el.__kpAnim
      ? parseNum(el.textContent)
      : (typeof el.__kpLast === "number" ? el.__kpLast : target);
    if (target <= 0) {
      if (el.__kpRaf) cancelAnimationFrame(el.__kpRaf);
      el.__kpRaf = 0;
      el.__kpAnim = false;
      el.__kpLast = 0;
      el.__kpTarget = 0;
      writeValue(el, "\u2013");
      return;
    }
    animate(el, target, from);
  }

  function attach() {
    var els = document.querySelectorAll(".kpi-countup");
    if (!els.length) { setTimeout(attach, 300); return; }
    els.forEach(function (el) {
      check(el);
      if (window.MutationObserver) {
        new MutationObserver(function () { check(el); })
          .observe(el, { childList: true, characterData: true, subtree: true });
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
  if (document.body && document.body.classList.contains("app-ready")) {
    setTimeout(replayOpening, 180);
  } else if (window.MutationObserver && document.body) {
    new MutationObserver(function (mutations, obs) {
      if (document.body.classList.contains("app-ready")) {
        obs.disconnect();
        setTimeout(replayOpening, 180);
      }
    }).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  }
})();
