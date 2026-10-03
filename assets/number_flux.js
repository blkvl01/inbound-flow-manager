/* Card count-up. Observe structural/target changes in the card area only;
   text written by the tween never schedules another scan. */
(function () {
  "use strict";
  var DURATION = 520;
  var area = null;
  var observer = null;
  var frame = 0;
  var candidates = new Set();
  var active = new Set();
  var motion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");

  function inactive() {
    return document.hidden || (document.body &&
      (document.body.classList.contains("view-kpi-mode") || document.body.classList.contains("view-tv-mode")));
  }
  function finish(el) {
    if (el.__cuRaf) cancelAnimationFrame(el.__cuRaf);
    el.__cuRaf = 0;
    if (el.isConnected && typeof el.__cuValue === "number") el.textContent = String(el.__cuValue);
    active.delete(el);
  }
  function tween(el) {
    if (!el.isConnected) return;
    var target = parseInt(el.getAttribute("data-countup"), 10);
    if (isNaN(target) || el.__cuValue === target) return;
    var from = el.__cuRaf ? parseInt(el.textContent, 10) : el.__cuValue;
    from = typeof from === "number" && isFinite(from) ? from : 0;
    if (el.__cuRaf) cancelAnimationFrame(el.__cuRaf);
    el.__cuValue = target;
    if (inactive() || (motion && motion.matches) || from === target) { finish(el); return; }
    active.add(el);
    var start = 0;
    function step(now) {
      if (!el.isConnected || inactive() || (motion && motion.matches)) { finish(el); return; }
      if (!start) start = now;
      var progress = Math.min(1, (now - start) / DURATION);
      var value = String(Math.round(from + (target - from) * (1 - Math.pow(1 - progress, 3))));
      if (el.textContent !== value) el.textContent = value;
      if (progress < 1) el.__cuRaf = requestAnimationFrame(step);
      else finish(el);
    }
    el.__cuRaf = requestAnimationFrame(step);
  }
  function collect(node) {
    if (!node || node.nodeType !== 1) return;
    if (node.matches("[data-countup]")) candidates.add(node);
    node.querySelectorAll("[data-countup]").forEach(function (el) { candidates.add(el); });
  }
  function schedule() {
    if (frame || !candidates.size) return;
    frame = requestAnimationFrame(function () {
      frame = 0;
      candidates.forEach(tween);
      candidates.clear();
    });
  }
  function attach() {
    var next = document.getElementById("cards-area");
    if (!next) { setTimeout(attach, 300); return; }
    if (next === area) return;
    if (observer) observer.disconnect();
    area = next;
    collect(area);
    schedule();
    if (!window.MutationObserver) return;
    observer = new MutationObserver(function (records) {
      records.forEach(function (record) {
        if (record.type === "attributes") candidates.add(record.target);
        else record.addedNodes.forEach(collect);
      });
      schedule();
    });
    observer.observe(area, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-countup"] });
  }
  function visibilityChanged() {
    if (inactive()) active.forEach(finish);
    else attach();
  }
  function setup() {
    attach();
    document.addEventListener("visibilitychange", visibilityChanged);
    if (window.MutationObserver && document.body) {
      new MutationObserver(visibilityChanged).observe(document.body, { attributes: true, attributeFilter: ["class"] });
    }
    if (motion && motion.addEventListener) motion.addEventListener("change", visibilityChanged);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
