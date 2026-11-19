/* Shared modal contract for the Dash-driven overlays (Összegzés, PIN,
 * Developer menü). Dash callbacks toggle their style.display; this module
 * watches that and adds: aria-hidden sync, focus into the dialog on open,
 * Tab trap, Escape = close button, focus restore to the opener on close.
 *
 * The ULD dialogs and Beállítások keep their own handler in uld_manager.js
 * (same contract). This one runs in the window capture phase, so when one of
 * these overlays sits on top of Beállítások (PIN / Developer opened from it)
 * its Escape/Tab wins and the settings handler underneath does not fire.
 */
(function () {
  "use strict";

  if (window.__fmModalA11yWired) return;
  window.__fmModalA11yWired = true;

  // Later entries are stacked above earlier ones (DOM order, same z-index).
  var MODALS = [
    { id: "summary-overlay", close: "summary-close", focus: "#summary-copy-btn" },
    { id: "dev-pin-overlay", close: "dev-pin-close", focus: "#dev-pin-input" },
    { id: "dev-overlay",     close: "dev-close",     focus: ".dev-tab-active, #dev-tab-ops" }
  ];

  var FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), '
    + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function isShown(el) {
    if (!el || !el.isConnected) return false;
    var d = el.style.display;
    return !!d && d !== "none";
  }

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  function topOpen() {
    for (var i = MODALS.length - 1; i >= 0; i--) {
      var el = document.getElementById(MODALS[i].id);
      if (isShown(el)) return { cfg: MODALS[i], el: el };
    }
    return null;
  }

  function ownerModal(node) {
    for (var i = 0; i < MODALS.length; i++) {
      var el = document.getElementById(MODALS[i].id);
      if (el && node && el.contains(node)) return el;
    }
    return null;
  }

  function onOpen(cfg, el) {
    el.setAttribute("aria-hidden", "false");
    var active = document.activeElement;
    if (active && active !== document.body && !el.contains(active)) {
      // PIN -> Developer: focus sits in the closing PIN dialog; inherit its opener.
      var owner = ownerModal(active);
      // The PIN observer may already have run its onClose (same commit), so
      // also read the opener it handed off.
      el._fmReturnFocus = (owner && (owner._fmReturnFocus || owner._fmLastReturn)) || active;
    }
    window.setTimeout(function () {
      if (!isShown(el) || el.contains(document.activeElement)) return;
      var target = (cfg.focus && el.querySelector(cfg.focus)) || focusables(el)[0];
      try { if (target) target.focus(); } catch (err) {}
    }, 40);
  }

  function onClose(el) {
    el.setAttribute("aria-hidden", "true");
    var back = el._fmReturnFocus;
    el._fmReturnFocus = null;
    el._fmLastReturn = back || null;
    if (!back || !back.isConnected || typeof back.focus !== "function") return;
    var active = document.activeElement;
    if (active && active !== document.body && !el.contains(active)) return;
    window.setTimeout(function () {
      var now = document.activeElement;
      if (now && now !== document.body && !el.contains(now)) return;
      try { back.focus(); } catch (err) {}
    }, 0);
  }

  function watch(cfg, attempt) {
    var el = document.getElementById(cfg.id);
    if (!el) {
      if ((attempt || 0) < 120) window.setTimeout(function () { watch(cfg, (attempt || 0) + 1); }, 500);
      return;
    }
    var shown = isShown(el);
    el.setAttribute("aria-hidden", shown ? "false" : "true");
    new MutationObserver(function () {
      var now = isShown(el);
      if (now === shown) return;
      shown = now;
      if (now) onOpen(cfg, el); else onClose(el);
    }).observe(el, { attributes: true, attributeFilter: ["style"] });
  }

  function trapTab(e, root) {
    var items = focusables(root);
    if (!items.length) { e.preventDefault(); return; }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (!root.contains(active)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && active === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  window.addEventListener("keydown", function (e) {
    if (e.key !== "Escape" && e.key !== "Tab") return;
    var top = topOpen();
    if (!top) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      var btn = document.getElementById(top.cfg.close);
      if (btn) btn.click();
      return;
    }
    e.stopPropagation();
    trapTab(e, top.el);
  }, true);

  function anyOpen() {
    if (topOpen()) return true;
    if (document.querySelector('[role="dialog"][aria-hidden="false"]')) return true;
    var settings = document.getElementById("settings-overlay");
    return isShown(settings);
  }

  window.FMModal = {
    anyOpen: anyOpen,
    ownTopOpen: function () { return !!topOpen(); }
  };

  MODALS.forEach(function (cfg) { watch(cfg, 0); });

  // ── Header KPI cards (Várható beérkező / Műszak): keyboard parity with hover.
  // Keyboard focus inside the wrapper opens the panel (.kpi-kbd-open); mouse
  // focus does not, so a click does not leave the panel stuck open.
  // Enter / Space on the card = click (pin toggle).
  var KPI_WRAPS = "#kpi-bec-wrapper, #kpi-muszak-wrapper";

  function isKeyboardFocus(el) {
    try { return el.matches(":focus-visible"); } catch (err) { return false; }
  }

  document.addEventListener("focusin", function (e) {
    var t = e.target;
    var wrap = t && t.closest && t.closest(KPI_WRAPS);
    if (wrap && isKeyboardFocus(t)) wrap.classList.add("kpi-kbd-open");
  });
  document.addEventListener("focusout", function (e) {
    var wrap = e.target && e.target.closest && e.target.closest(KPI_WRAPS);
    if (!wrap) return;
    if (e.relatedTarget && wrap.contains(e.relatedTarget)) return;
    wrap.classList.remove("kpi-kbd-open");
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    var t = e.target;
    if (!t || (t.id !== "kpi-bec-card" && t.id !== "kpi-muszak-card")) return;
    e.preventDefault();
    t.click();
  });
})();
