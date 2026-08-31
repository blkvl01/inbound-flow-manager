/* Loading screen: card tilt + shine on mousemove, smooth exit animation. */
(function () {
  "use strict";

  /* ─── Card tilt + cursor shine (first-load only) ─── */
  var rafPending = false;
  var lastEv = null;

  function applyTilt() {
    rafPending = false;
    var overlay = document.getElementById("overlay");
    if (!overlay || !overlay.classList.contains("loading-first")) return;
    var card = overlay.querySelector(".l-card");
    if (!card || !lastEv) return;

    var r = card.getBoundingClientRect();
    var dx = Math.max(-1, Math.min(1, (lastEv.clientX - (r.left + r.width  / 2)) / (r.width  / 2)));
    var dy = Math.max(-1, Math.min(1, (lastEv.clientY - (r.top  + r.height / 2)) / (r.height / 2)));

    card.style.transform =
      "perspective(700px) rotateY(" + (dx * 5).toFixed(2) +
      "deg) rotateX(" + (-dy * 3.5).toFixed(2) + "deg)";

    card.style.setProperty("--sx", ((lastEv.clientX - r.left) / r.width  * 100).toFixed(1) + "%");
    card.style.setProperty("--sy", ((lastEv.clientY - r.top)  / r.height * 100).toFixed(1) + "%");
  }

  document.addEventListener("mousemove", function (e) {
    lastEv = e;
    if (!rafPending) { rafPending = true; requestAnimationFrame(applyTilt); }
  }, { passive: true });

  document.addEventListener("mouseleave", function () {
    var overlay = document.getElementById("overlay");
    if (!overlay) return;
    var card = overlay.querySelector(".l-card");
    if (!card) return;
    card.style.transform = "";
    card.style.setProperty("--sx", "50%");
    card.style.setProperty("--sy", "50%");
  });

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
