/* Loading screen: smooth exit animation without continuous pointer work. */
(function () {
  "use strict";

  /* ─── Smooth exit via MutationObserver ─── */
  function watchOverlay() {
    var el = document.getElementById("overlay");
    if (!el) { setTimeout(watchOverlay, 80); return; }

    el._lWasVisible = el.style.display !== "none";

    new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.attributeName !== "style") return;
        var t = m.target;
        var nowHidden = t.style.display === "none";

        if (nowHidden && t._lWasVisible && !t._lExiting) {
          t._lExiting = true;
          t.style.display = "flex";
          t.style.pointerEvents = "none";
          t.classList.add("loading-exit");
          setTimeout(function () {
            t.style.display = "none";
            t.style.pointerEvents = "";
            t.classList.remove("loading-exit");
            t._lExiting = false;
          }, 440);
        }

        t._lWasVisible = !nowHidden;
      });
    }).observe(el, { attributes: true, attributeFilter: ["style"] });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watchOverlay);
  } else {
    watchOverlay();
  }
})();
