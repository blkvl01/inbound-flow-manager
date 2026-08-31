/* kpi_subview_switch.js - spring thumb for the KPI Tracking / Riport header switch.

   The Dash callback writes --kpi-subview-index (0 = Tracking, 1 = Riport).
   This file observes that index and drives a short spring on the shared thumb,
   matching the KPI page's existing segmented-control physics without adding
   idle animation cost.
*/
(function () {
  "use strict";

  var K = 260, C = 15, MASS = 1;
  var STRETCH_MAX = 0.20;
  var STRETCH_VG = 0.0011;
  var ENERGY_VG = 0.00115;
  var SETTLE_POS = 0.25, SETTLE_VEL = 2.2;
  var PP_BASE = 10, PP_SPAN = 80;

  var ctl = null, thumbW = 0, padPx = 3;
  var pos = 0, vel = 0, target = 0;
  var raf = 0, lastMs = 0, lastIdx = 0;
  var originStr = "0%";
  var obs = null, attached = false;

  function reducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function measure() {
    if (!ctl) return;
    var r = ctl.getBoundingClientRect();
    if (r.width < 20) return;
    var p = parseFloat(getComputedStyle(ctl).getPropertyValue("--kpi-subview-pad"));
    padPx = isNaN(p) ? 3 : p;
    thumbW = (r.width - padPx * 2) / 2;
  }

  function readIdx() {
    if (!ctl) return 0;
    var v = parseFloat(ctl.style.getPropertyValue("--kpi-subview-index"));
    return v >= 0.5 ? 1 : 0;
  }

  function apply() {
    var w = thumbW || 1;
    var sx = 1 + Math.min(STRETCH_MAX, Math.abs(vel) * STRETCH_VG);
    var sy = 1 - (sx - 1) * 0.58;
    var pp = PP_BASE + PP_SPAN * (pos / w);
    var energy = Math.min(1, Math.abs(vel) * ENERGY_VG);
    ctl.style.setProperty("--ks-tx", pos.toFixed(2) + "px");
    ctl.style.setProperty("--ks-pp", pp.toFixed(3) + "%");
    ctl.style.setProperty("--ks-sx", sx.toFixed(4));
    ctl.style.setProperty("--ks-sy", sy.toFixed(4));
    ctl.style.setProperty("--ks-origin", originStr);
    ctl.style.setProperty("--ks-energy", energy.toFixed(3));
  }

  function clearVars() {
    if (!ctl) return;
    ["--ks-tx", "--ks-pp", "--ks-sx", "--ks-sy", "--ks-origin", "--ks-energy"]
      .forEach(function (p) { ctl.style.removeProperty(p); });
  }

  function tick(ms) {
    var dt = lastMs ? Math.min((ms - lastMs) / 1000, 0.04) : 0.016;
    lastMs = ms;
    var f = K * (target - pos) - C * vel;
    vel += (f / MASS) * dt;
    pos += vel * dt;
    apply();
    if (Math.abs(target - pos) < SETTLE_POS && Math.abs(vel) < SETTLE_VEL) {
      pos = target;
      vel = 0;
      apply();
      raf = 0;
      lastMs = 0;
      ctl.classList.remove("ks-physics");
      ctl.classList.add("is-landing");
      clearTimeout(ctl.__kpiSubLandingTimer);
      ctl.__kpiSubLandingTimer = setTimeout(function () {
        if (ctl) ctl.classList.remove("is-landing");
      }, 420);
      requestAnimationFrame(clearVars);
      return;
    }
    raf = requestAnimationFrame(tick);
  }

  function springTo(toIdx) {
    measure();
    if (thumbW < 8) {
      lastIdx = toIdx;
      return;
    }
    var newTarget = toIdx * thumbW;
    if (!raf) {
      pos = lastIdx * thumbW;
      vel = 0;
    }
    originStr = (newTarget - pos) >= 0 ? "0%" : "100%";
    target = newTarget;
    lastIdx = toIdx;

    if (reducedMotion()) {
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      ctl.classList.remove("ks-physics");
      clearVars();
      return;
    }

    ctl.classList.add("ks-physics");
    apply();
    if (!raf) {
      lastMs = 0;
      raf = requestAnimationFrame(tick);
    }
  }

  function onMutation() {
    if (!ctl) return;
    var idx = readIdx();
    if (idx === lastIdx) return;
    springTo(idx);
  }

  function detach() {
    if (obs) {
      obs.disconnect();
      obs = null;
    }
    if (raf) {
      cancelAnimationFrame(raf);
      raf = 0;
      lastMs = 0;
    }
    if (ctl) {
      ctl.classList.remove("ks-physics");
      clearVars();
    }
    window.removeEventListener("resize", onResize);
    ctl = null;
    attached = false;
    thumbW = 0;
  }

  function onResize() {
    if (!ctl) return;
    measure();
  }

  function init() {
    var el = document.querySelector(".kpi-subview-switch");
    if (!el) return;
    if (ctl === el && attached) return;
    detach();
    ctl = el;
    attached = true;
    if (!ctl.style.getPropertyValue("--kpi-subview-index")) {
      ctl.style.setProperty(
        "--kpi-subview-index",
        document.body.classList.contains("kpi-sub-report") ? "1" : "0"
      );
    }
    measure();
    lastIdx = readIdx();
    obs = new MutationObserver(onMutation);
    obs.observe(ctl, { attributes: true, attributeFilter: ["style", "class"] });
    window.addEventListener("resize", onResize, { passive: true });
  }

  function boot() {
    if (document.body && document.body.classList.contains("view-kpi-mode")) init();
  }

  window.addEventListener("kpi-view-entered", function () {
    attached = false;
    setTimeout(init, 60);
  });
  window.addEventListener("kpi-subview-shown", function () {
    setTimeout(init, 30);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { setTimeout(boot, 120); });
  } else {
    setTimeout(boot, 120);
  }
})();
