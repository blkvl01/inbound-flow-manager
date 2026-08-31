/* flow_glass_control.js — click-driven SPRING physics for the Shift/Day control.

   NO pointer drag (removed — drag was the source of the jumping). The mode is
   switched by clicking, handled by kpi_page.js::setFlowMode which writes
   --flow-mode-index (0 = Shift, 1 = Day) on #kpi-flow-mode-control. We simply
   OBSERVE that variable and run a real spring for the thumb — so it can never
   jump (start + target are both known fixed slots).

   The spring drives CSS custom properties consumed by style.css:
     --fg-tx      px translateX of the thumb window
     --fg-pp      background-position % of the FIXED foil (counter-tracks --fg-tx
                  so the pattern stays anchored to the control → it flows THROUGH
                  the window instead of riding with it)
     --fg-sx/--fg-sy  squash-and-stretch (volume-preserving)
     --fg-origin  transform-origin x (trailing edge → stretch leads the motion)
     --fg-energy  0..1 speed signal → motion glow / brightness

   rAF runs ONLY while a switch is settling, then stops and hands control back to
   CSS (no idle cost — honours the "no idle animation" rule).
*/
(function () {
  "use strict";

  /* ── Spring tuning ──────────────────────────────────────────────────────── */
  var K = 240, C = 14, MASS = 1;   // position spring — C↓ → more underdamped bounce
  var STRETCH_MAX = 0.22;          // max scaleX gain on fast travel
  var STRETCH_VG  = 0.0012;        // |vel px/s| → stretch
  var ENERGY_VG   = 0.0011;        // |vel px/s| → glow energy 0..1
  var SETTLE_POS  = 0.3, SETTLE_VEL = 2.5;
  // foil anchor: slot 0 → 8%, slot 1 → 92% (16% head-room so even max overshoot
  // keeps background-position inside [0,100] → the foil never shows an edge gap).
  var PP_BASE = 8, PP_SPAN = 84;

  /* ── State ──────────────────────────────────────────────────────────────── */
  var ctl = null, thumbW = 0, padPx = 3;
  var pos = 0, vel = 0, target = 0;
  var raf = 0, lastMs = 0, lastIdx = 0;
  var originStr = "0%";
  var obs = null, _attached = false;

  function rm() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function measure() {
    if (!ctl) return;
    var r = ctl.getBoundingClientRect();
    if (r.width < 20) return;
    var p = parseFloat(getComputedStyle(ctl).getPropertyValue("--flow-control-pad"));
    padPx = isNaN(p) ? 3 : p;
    thumbW = (r.width - padPx * 2) / 2;
  }

  function readIdx() {
    if (!ctl) return 0;
    // Read the INLINE style (kpi_page.js sets it via style.setProperty) — cheap,
    // no forced style recalc, so our own per-frame var writes don't thrash.
    var v = parseFloat(ctl.style.getPropertyValue("--flow-mode-index"));
    return v >= 0.5 ? 1 : 0;
  }

  function apply() {
    var w = thumbW || 1;
    var sx = 1 + Math.min(STRETCH_MAX, Math.abs(vel) * STRETCH_VG);
    var sy = 1 - (sx - 1) * 0.62;                 // volume-preserving squash
    var pp = PP_BASE + PP_SPAN * (pos / w);       // foil anchor %  (slot 8%→92%)
    var energy = Math.min(1, Math.abs(vel) * ENERGY_VG);
    ctl.style.setProperty("--fg-tx", pos.toFixed(2) + "px");
    ctl.style.setProperty("--fg-pp", pp.toFixed(3) + "%");
    ctl.style.setProperty("--fg-sx", sx.toFixed(4));
    ctl.style.setProperty("--fg-sy", sy.toFixed(4));
    ctl.style.setProperty("--fg-origin", originStr);
    ctl.style.setProperty("--fg-energy", energy.toFixed(3));
  }

  function clearVars() {
    if (!ctl) return;
    ["--fg-tx", "--fg-pp", "--fg-sx", "--fg-sy", "--fg-origin", "--fg-energy"]
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
      pos = target; vel = 0; apply();
      raf = 0; lastMs = 0;
      // Hand back to CSS: drop the physics override, then clear vars one frame
      // later so the CSS transition does not fire a 0-length tween.
      ctl.classList.remove("fg-physics");
      requestAnimationFrame(clearVars);
      return;
    }
    raf = requestAnimationFrame(tick);
  }

  function springTo(toIdx) {
    measure();
    if (thumbW < 8) { lastIdx = toIdx; return; }   // not laid out yet

    var newTarget = toIdx * thumbW;
    if (!raf) {
      // not mid-flight → start from the slot we were resting in
      pos = lastIdx * thumbW;
      vel = 0;
    }
    // direction-locked stretch origin (trailing edge anchored → stretch leads).
    // Set ONCE per launch (no per-frame flip → no jitter).
    originStr = (newTarget - pos) >= 0 ? "0%" : "100%";
    target = newTarget;
    lastIdx = toIdx;

    if (rm()) {                                    // reduced motion → snap
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      ctl.classList.remove("fg-physics");
      clearVars();
      return;
    }

    ctl.classList.add("fg-physics");
    apply();                                        // lock first frame synchronously
    if (!raf) { lastMs = 0; raf = requestAnimationFrame(tick); }
  }

  function onMutation() {
    if (!ctl) return;
    var idx = readIdx();
    if (idx === lastIdx) return;                    // unrelated mutation, or our own var writes
    springTo(idx);
  }

  /* ── Init / lifecycle ──────────────────────────────────────────────────── */
  function init() {
    var el = document.getElementById("kpi-flow-mode-control");
    if (!el) return;
    if (ctl === el && _attached) return;
    detach();
    ctl = el;
    _attached = true;
    measure();
    lastIdx = readIdx();
    obs = new MutationObserver(onMutation);
    obs.observe(ctl, { attributes: true, attributeFilter: ["style", "class"] });
    window.addEventListener("resize", onResize, { passive: true });
  }

  function detach() {
    if (obs) { obs.disconnect(); obs = null; }
    if (raf) { cancelAnimationFrame(raf); raf = 0; lastMs = 0; }
    if (ctl) { ctl.classList.remove("fg-physics"); clearVars(); }
    window.removeEventListener("resize", onResize);
    ctl = null; _attached = false; thumbW = 0;
  }

  function onResize() { if (ctl) measure(); }

  window.addEventListener("kpi-view-entered", function () {
    _attached = false;
    setTimeout(init, 60);
  });

  function boot() {
    if (document.body && document.body.classList.contains("view-kpi-mode")) init();
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { setTimeout(boot, 120); });
  } else {
    setTimeout(boot, 120);
  }
})();
