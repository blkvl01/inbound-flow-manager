/* Radial spotlight effect for .kpi-card elements.
   Tracks pointer position and sets --x / --y CSS custom properties
   so the ::before / ::after radial gradients follow the cursor. */
(function () {
  function attach() {
    document.addEventListener("pointermove", function (ev) {
      document.querySelectorAll(".kpi-card").forEach(function (card) {
        var r = card.getBoundingClientRect();
        card.style.setProperty("--x", ev.clientX - r.left);
        card.style.setProperty("--y", ev.clientY - r.top);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
