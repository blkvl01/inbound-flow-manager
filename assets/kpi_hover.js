/* The spotlight only follows the card under the pointer. Coalesce high-rate
   pointer events before measuring and writing, rather than touching every KPI. */
(function () {
  "use strict";
  var frame = 0;
  var card = null;
  var pointerX = 0;
  var pointerY = 0;
  var motion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");

  function update() {
    frame = 0;
    if (!card || !card.isConnected || document.hidden || (motion && motion.matches)) return;
    var bounds = card.getBoundingClientRect();
    card.style.setProperty("--x", pointerX - bounds.left);
    card.style.setProperty("--y", pointerY - bounds.top);
  }
  function attach() {
    document.addEventListener("pointermove", function (event) {
      if (document.hidden || (motion && motion.matches)) return;
      card = event.target && event.target.closest && event.target.closest(".kpi-card");
      if (!card) return;
      pointerX = event.clientX;
      pointerY = event.clientY;
      if (!frame) frame = requestAnimationFrame(update);
    }, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden && frame) cancelAnimationFrame(frame);
      frame = 0;
      card = null;
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attach, { once: true });
  else attach();
})();
