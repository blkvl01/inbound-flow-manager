/* Loading overlay lifecycle: one exit per hide, cancellable by a new load. */
(function () {
  "use strict";

  var overlay = null;
  var overlayObserver = null;
  var parentObserver = null;
  var parent = null;
  var exitTimer = 0;
  var generation = 0;
  var phase = "hidden";
  var sourceBusy = false;
  var motion = window.matchMedia("(prefers-reduced-motion: reduce)");

  function observeOverlay() {
    if (overlay && overlayObserver) {
      overlayObserver.observe(overlay, {
        attributes: true, attributeFilter: ["style", "class"], attributeOldValue: true
      });
    }
  }

  // Internal writes must not look like another external request to hide/show.
  function writeOverlay(fn) {
    if (!overlay) return;
    overlayObserver.disconnect();
    fn(overlay);
    observeOverlay();
  }

  function cancelExit(show) {
    clearTimeout(exitTimer);
    exitTimer = 0;
    generation++;
    phase = show ? "visible" : "hidden";
    writeOverlay(function (el) {
      el.classList.remove("loading-exit");
      el.style.pointerEvents = "";
      if (show) el.style.display = "flex";
    });
  }

  function finishExit(ticket, target) {
    if (ticket !== generation || overlay !== target || phase !== "exiting") return;
    exitTimer = 0;
    // Change logical state before writing the final display:none.
    phase = "hidden";
    writeOverlay(function (el) {
      el.style.display = "none";
      el.style.pointerEvents = "";
      el.classList.remove("loading-exit");
    });
  }

  function beginExit() {
    if (phase === "exiting") {
      // A repeated server hide is the same request, not a fresh animation.
      writeOverlay(function (el) { el.style.display = "flex"; });
      return;
    }
    if (phase === "hidden" || motion.matches) {
      cancelExit(false);
      return;
    }
    phase = "exiting";
    var ticket = ++generation;
    var target = overlay;
    writeOverlay(function (el) {
      el.style.display = "flex";
      el.style.pointerEvents = "none";
      el.classList.add("loading-exit");
    });
    exitTimer = setTimeout(function () { finishExit(ticket, target); }, 440);
  }

  function oldDisplay(style) {
    var match = /(?:^|;)\s*display\s*:\s*([^;]+)/i.exec(style || "");
    return match ? match[1].trim() : "";
  }

  function bindOverlay() {
    var next = document.getElementById("overlay");
    if (next === overlay) return !!next;
    clearTimeout(exitTimer);
    exitTimer = 0;
    generation++;
    if (overlayObserver) overlayObserver.disconnect();
    overlay = next;
    if (!overlay) return false;
    phase = overlay.style.display === "none" ? "hidden" : "visible";
    overlayObserver = new MutationObserver(function (mutations) {
      if (!overlay || !overlay.isConnected) { bindOverlay(); return; }
      var explicitlyShown = mutations.some(function (change) {
        return change.attributeName === "style" && oldDisplay(change.oldValue) === "none" && overlay.style.display !== "none";
      });
      var newMode = phase === "exiting" && mutations.some(function (change) {
        return change.attributeName === "class" && !overlay.classList.contains("loading-exit");
      });
      if (explicitlyShown || newMode) {
        cancelExit(true);
      } else if (overlay.style.display === "none") {
        // Loading/test visibility belongs to Dash. Busy state only cancels an
        // already running exit; it does not override an intentional test hide.
        beginExit();
      } else if (phase !== "exiting") {
        phase = "visible";
      }
    });
    observeOverlay();
    if (overlay.parentElement !== parent) {
      if (parentObserver) parentObserver.disconnect();
      parent = overlay.parentElement;
      if (parent) {
        // Only the overlay's direct parent is watched for replacement.
        parentObserver = new MutationObserver(bindOverlay);
        parentObserver.observe(parent, {childList: true});
      }
    }
    return true;
  }

  function start() {
    if (!bindOverlay()) setTimeout(start, 80);
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest && event.target.closest("#refresh-btn")) {
      bindOverlay();
      if (phase === "exiting") cancelExit(true);
    }
  }, true);

  window.addEventListener("flow:loading-state", function (event) {
    bindOverlay();
    var status = event.detail || {};
    sourceBusy = !!status.source_busy && status.overlay !== "test";
    if (sourceBusy && phase === "exiting") cancelExit(true);
  });

  function motionChanged() {
    if (!motion.matches || phase !== "exiting") return;
    if (sourceBusy) cancelExit(true);
    else finishExit(generation, overlay);
  }
  if (motion.addEventListener) motion.addEventListener("change", motionChanged);
  else if (motion.addListener) motion.addListener(motionChanged);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
