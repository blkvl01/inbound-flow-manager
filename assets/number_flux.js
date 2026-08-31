/* Number count-up: tweens any [data-countup] element's text toward its target
   whenever the value changes (fresh render = fresh element = counts from 0).
   - Self-guarded: never restarts an in-flight tween for the same target, so the
     MutationObserver watching its own textContent writes can't loop.
   - prefers-reduced-motion: sets the final value with no animation.
   Dash auto-serves every file in assets/, so no registration needed. */
(function () {
  "use strict";

  var DURATION = 520;

  function reducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

  function tween(el) {
    var target = parseInt(el.getAttribute("data-countup"), 10);
    if (isNaN(target)) return;
    if (el.__cuValue === target) return; // already showing / animating to this

    var from = (typeof el.__cuValue === "number") ? el.__cuValue : 0;
    el.__cuValue = target;

    if (reducedMotion() || from === target) {
      el.textContent = String(target);
      return;
    }

    var start = 0;
    if (el.__cuRaf) cancelAnimationFrame(el.__cuRaf);

    function step(now) {
      if (!start) start = now;
      var t = Math.min(1, (now - start) / DURATION);
      el.textContent = String(Math.round(from + (target - from) * easeOutCubic(t)));
      if (t < 1) {
        el.__cuRaf = requestAnimationFrame(step);
      } else {
        el.textContent = String(target);
        el.__cuRaf = 0;
      }
    }
    el.__cuRaf = requestAnimationFrame(step);
  }

  function scan() {
    var nodes = document.querySelectorAll("[data-countup]");
    for (var i = 0; i < nodes.length; i++) tween(nodes[i]);
  }

  var pending = 0;
  function schedule() {
    if (pending) return;
    pending = requestAnimationFrame(function () { pending = 0; scan(); });
  }

  function attach() {
    scan();
    if (!window.MutationObserver) return;
    var area = document.getElementById("cards-area") || document.body;
    new MutationObserver(schedule).observe(area, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
