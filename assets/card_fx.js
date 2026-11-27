/* Lightweight card interactions.
   - Betarolva click: fixed ghost exit, so Dash can re-render immediately.
   - Remaining cards: transform-only FLIP animation after the new DOM lands.
   - Stat filters: local ripple feedback, no server wait.

   Motion rule (2026-11-27): a .priority-card never carries a CSS `animation`.
   Entry, filter pop-in and feedback pulses run through WAAPI (el.animate),
   which never touches the `animation` property. Earlier every transient
   class (card-expand-feedback, card-filter-in, plate-focus-pulse,
   card-action-feedback) replaced the base cardQuickIn animation; removing the
   class restarted cardQuickIn (opacity 0.72 + staggered nth-child delay), so
   every expand/collapse blinked the whole row. */
(function () {
  "use strict";

  var EXIT_MS = 320;
  var FLIP_MS = 340;
  var EXPAND_MS = 260;    // keep in sync with .card-details-section in style.css
  var COLLAPSE_MS = 200;
  var EASE_OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
  var EASE_IN_OUT = "cubic-bezier(0.4, 0, 0.2, 1)";
  var pendingRects = null;
  var pendingUntil = 0;
  var flipFrame = 0;
  var _plateStateRafPending = false;

  function _debouncedPlateState() {
    if (_plateStateRafPending) return;
    _plateStateRafPending = true;
    requestAnimationFrame(function () {
      _plateStateRafPending = false;
      applyPlateVisualState();
    });
  }

  function prefersReducedMotion() {
    return window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  // One-shot WAAPI animation. Never writes classes or the CSS `animation`
  // property, so nothing restarts when it ends.
  function playOnce(el, keyframes, opts) {
    if (!el || typeof el.animate !== "function" || prefersReducedMotion()) return null;
    try { return el.animate(keyframes, opts); } catch (e) { return null; }
  }

  function pulse(el) {
    return playOnce(el, [
      { filter: "brightness(1)" },
      { filter: "brightness(1.16)", offset: 0.38 },
      { filter: "brightness(1)" }
    ], { duration: 520, easing: EASE_OUT });
  }

  function cardEnter(card, order) {
    return playOnce(card, [
      { opacity: 0.72, transform: "translateY(3px)" },
      { opacity: 1, transform: "none" }
    ], { duration: 180, delay: Math.min(order, 8) * 24, easing: EASE_OUT, fill: "backwards" });
  }

  function cardFilterIn(card) {
    return playOnce(card, [
      { opacity: 0, transform: "translateY(8px) scale(0.97)" },
      { opacity: 1, transform: "none" }
    ], { duration: 260, easing: EASE_OUT, fill: "backwards" });
  }

  function cardKey(card, index) {
    return card && card.getAttribute("data-card-key") || ("idx:" + index);
  }

  function currentFilter() {
    var active = document.querySelector(".stat-item.stat-active");
    var map = {
      "stat-btn-all": "all",
      "stat-btn-athu": "at_hu",
      "stat-btn-drv": "driver",
      "stat-btn-sched": "scheduled",
      "stat-btn-ship": "shippable",
      "stat-btn-betarolt": "betarolt"
    };
    if (active && map[active.id]) {
      window._flowActiveFilter = map[active.id];
    }
    return window._flowActiveFilter || "all";
  }

  var plateHoverKey = "";
  var plateFocusKey = "";
  window._flowPlateFocusKey = "";
  var plateFocusLabel = "";
  var plateFocusColor = "";
  var plateFocusPill = null;
  var lastScrolledPlateKey = "";

  function plateKey(value) {
    return String(value || "").trim().replace(/\s+/g, " ").toUpperCase();
  }

  function focusKeyFor(el) {
    if (!el) return "";
    return plateKey(el.dataset.truckTurnKey || el.getAttribute("data-truck-turn-key") ||
      el.dataset.plateKey || el.getAttribute("data-plate-key"));
  }

  function basePlateKeyFor(el) {
    if (!el) return "";
    return plateKey(el.dataset.plateKey || el.getAttribute("data-plate-key") ||
      String(focusKeyFor(el)).split("|")[0]);
  }

  function focusMatches(el, key) {
    key = plateKey(key);
    if (!el || !key) return false;
    var turnKey = focusKeyFor(el);
    if (turnKey === key) return true;
    if (key.indexOf("|") < 0) return basePlateKeyFor(el) === key;
    return false;
  }

  function htmlEscape(value) {
    return String(value || "").replace(/[&<>"']/g, function (ch) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch];
    });
  }

  function sourcePlateInfo(source) {
    if (!source) return null;
    var key = focusKeyFor(source);
    if (!key) return null;
    return {
      key: key,
      label: source.dataset.focusLabel || source.getAttribute("data-focus-label") ||
        source.dataset.plate || source.getAttribute("data-plate") || key.split("|")[0] || key,
      color: source.dataset.plateColor || source.getAttribute("data-plate-color") || "#8b949e"
    };
  }

  function ensurePlateFocusPill() {
    if (plateFocusPill && document.body.contains(plateFocusPill)) return plateFocusPill;
    plateFocusPill = document.createElement("button");
    plateFocusPill.type = "button";
    plateFocusPill.className = "plate-focus-pill";
    plateFocusPill.title = "Kamion fókusz törlése";
    plateFocusPill.addEventListener("click", function () {
      clearPlateFocus(true);
    });
    document.body.appendChild(plateFocusPill);
    return plateFocusPill;
  }

  function updatePlateFocusPill() {
    var pill = ensurePlateFocusPill();
    if (!plateFocusKey) {
      if (pill.classList.contains("plate-focus-pill-visible")) pill.classList.remove("plate-focus-pill-visible");
      if (pill.innerHTML) pill.innerHTML = "";
      return;
    }
    var html =
      '<span class="plate-focus-dot"></span>' +
      '<span class="plate-focus-text">Kamion fókusz: <b>' + htmlEscape(plateFocusLabel || plateFocusKey) + '</b></span>' +
      '<span class="plate-focus-clear"><span class="fi fi-x" aria-hidden="true"></span></span>';
    pill.style.setProperty("--plate-focus-color", plateFocusColor || "#8b949e");
    // Rewrite only on change: every applyPlateVisualState() pass used to
    // replace the pill DOM, restarting its hover/press transitions.
    if (pill.innerHTML !== html) pill.innerHTML = html;
    pill.classList.add("plate-focus-pill-visible");
  }

  function pulsePlateMatches(key) {
    if (!key || prefersReducedMotion()) return;
    var cards = document.querySelectorAll("#cards-area .priority-card[data-plate-key]");
    for (var i = 0; i < cards.length; i++) {
      if (focusMatches(cards[i], key)) pulse(cards[i]);
    }
  }

  function scrollActiveTruckIntoView() {
    if (!plateFocusKey || plateFocusKey === lastScrolledPlateKey) return;
    var sidebar = document.getElementById("truck-sidebar");
    if (!sidebar) return;
    var active = sidebar.querySelector(".truck-sidebar-card.truck-sidebar-active[data-plate-key]");
    if (!active) return;
    lastScrolledPlateKey = plateFocusKey;
    setTimeout(function () {
      try {
        active.scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "center",
          inline: "nearest"
        });
      } catch (e) {
        active.scrollIntoView();
      }
    }, 70);
  }

  function setClass(el, cls, on) {
    if (el.classList.contains(cls) !== on) el.classList.toggle(cls, on);
  }

  function applyPlateVisualState() {
    var cards = document.querySelectorAll("#cards-area .priority-card[data-plate-key]");
    var hover = plateHoverKey && !plateFocusKey ? plateHoverKey : "";
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      // Toggle only what differs: an unchanged card gets no class write.
      var fm = !!plateFocusKey && focusMatches(card, plateFocusKey);
      var hm = !plateFocusKey && !!hover && focusMatches(card, hover);
      setClass(card, "plate-focus-match", fm);
      setClass(card, "plate-focus-dim", !!plateFocusKey && !fm);
      setClass(card, "plate-hover-match", hm);
      setClass(card, "plate-hover-dim", !plateFocusKey && !!hover && !hm);
    }

    var chips = document.querySelectorAll(".truck-focus-source[data-plate-key], .legend-truck-chip[data-plate-key]");
    for (var j = 0; j < chips.length; j++) {
      var chip = chips[j];
      var active = !!plateFocusKey && focusMatches(chip, plateFocusKey);
      var hovered = !plateFocusKey && !!hover && focusMatches(chip, hover);
      setClass(chip, "legend-truck-active", active);
      setClass(chip, "legend-truck-hover", hovered);
      setClass(chip, "truck-sidebar-active", active);
      setClass(chip, "truck-sidebar-hover", hovered);
    }
    setClass(document.body, "plate-focus-active", !!plateFocusKey);
    updatePlateFocusPill();
    scrollActiveTruckIntoView();
  }

  function setPlateHover(info) {
    var next = info ? info.key : "";
    if (plateHoverKey === next) return;
    plateHoverKey = next;
    applyPlateVisualState();
  }

  function clearPlateFocus(withPulse) {
    var oldKey = plateFocusKey;
    plateFocusKey = "";
    plateFocusLabel = "";
    plateFocusColor = "";
    lastScrolledPlateKey = "";
    applyPlateVisualState();
    if (oldKey) {
      window._flowPlateFocusKey = "";
      window.dispatchEvent(new CustomEvent("flow:plate-focus", { detail: { key: "" } }));
      requestAnimationFrame(function () {
        var active = document.activeElement;
        var card = active && active.closest && active.closest(".truck-sidebar-card[data-plate-key]");
        if (card && focusMatches(card, oldKey) && typeof active.blur === "function") {
          active.blur();
        }
      });
    }
    if (withPulse && oldKey) pulsePlateMatches(oldKey);
  }

  function setPlateFocus(info, forceClear) {
    if (!info || forceClear || plateFocusKey === info.key) {
      clearPlateFocus(false);
      return;
    }
    plateFocusKey = info.key;
    plateFocusLabel = info.label || info.key;
    plateFocusColor = info.color || "#8b949e";
    plateHoverKey = "";
    applyPlateVisualState();
    window._flowPlateFocusKey = plateFocusKey;
    window.dispatchEvent(new CustomEvent("flow:plate-focus", { detail: { key: plateFocusKey } }));
    pulsePlateMatches(plateFocusKey);
  }

  function nearestPlateSource(target, includeCard) {
    var source = target.closest(".inbound-uld-note, .truck-focus-source, .legend-truck-chip");
    if (source) return source;
    if (includeCard) {
      var card = target.closest(".priority-card[data-plate-key]");
      if (card && card.querySelector(".inbound-uld-note")) return card;
    }
    return null;
  }

  // animate === false: re-apply classes after a render (no FLIP, no layout
  // reads). Only a real filter switch (stat button) animates.
  function applyFlowFilter(filter, animate) {
    filter = filter || currentFilter();
    window._flowActiveFilter = filter;

    var area = document.getElementById("cards-area");
    if (!area) return;

    var cards = area.querySelectorAll(".priority-card[data-filter]");
    var reduced = animate === false || prefersReducedMotion();

    // FLIP: remember where the currently-visible cards are before the filter.
    var oldRects = new Map();
    if (!reduced) {
      for (var i = 0; i < cards.length; i++) {
        if (!cards[i].classList.contains("card-filter-hidden")) {
          oldRects.set(cardKey(cards[i], i), cards[i].getBoundingClientRect());
        }
      }
    }

    var visibleCount = 0;
    var newlyShown = [];
    for (var k = 0; k < cards.length; k++) {
      var c = cards[k];
      var tokens = (c.getAttribute("data-filter") || "all").split(/\s+/);
      var visible = tokens.indexOf(filter) !== -1;
      var wasHidden = c.classList.contains("card-filter-hidden");
      if (wasHidden === visible) c.classList.toggle("card-filter-hidden", !visible);
      if (visible) {
        visibleCount++;
        if (wasHidden) newlyShown.push(c);
      }
    }

    var empty = area.querySelector(".filter-empty-state");
    if (empty) {
      var disp = cards.length && visibleCount === 0 ? "block" : "none";
      if (empty.style.display !== disp) empty.style.display = disp;
    }

    if (reduced) return;

    // Newly-shown cards pop in; surviving cards slide from their old spot (FLIP).
    // Two phases: every rect is read first (one layout), then all writes.
    var vis = area.querySelectorAll(".priority-card[data-filter]:not(.card-filter-hidden)");
    var moves = [];
    for (var j = 0; j < vis.length; j++) {
      var card = vis[j];
      if (newlyShown.indexOf(card) !== -1) continue;
      var oldRect = oldRects.get(cardKey(card, j));
      if (!oldRect) continue;
      var newRect = card.getBoundingClientRect();
      var dx = oldRect.left - newRect.left;
      var dy = oldRect.top - newRect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      moves.push({ el: card, dx: dx, dy: dy });
    }
    newlyShown.forEach(cardFilterIn);
    moves.forEach(function (m) {
      playOnce(m.el, [
        { transform: "translate3d(" + m.dx + "px," + m.dy + "px,0)" },
        { transform: "none" }
      ], { duration: FLIP_MS, easing: EASE_OUT });
    });
  }

  window.applyFlowFilter = applyFlowFilter;

  function snapshotCards() {
    var cards = document.querySelectorAll("#cards-area .priority-card[data-card-key]:not(.card-filter-hidden)");
    var rects = new Map();
    for (var i = 0; i < cards.length; i++) {
      rects.set(cardKey(cards[i], i), cards[i].getBoundingClientRect());
    }
    return rects;
  }

  function animateGhost(card) {
    if (!card || prefersReducedMotion()) return;
    var rect = card.getBoundingClientRect();
    var ghost = card.cloneNode(true);
    ghost.classList.add("card-exit-ghost");
    ghost.querySelectorAll("[id]").forEach(function (el) { el.removeAttribute("id"); });
    ghost.removeAttribute("id");
    ghost.style.position = "fixed";
    ghost.style.left = rect.left + "px";
    ghost.style.top = rect.top + "px";
    ghost.style.width = rect.width + "px";
    ghost.style.height = rect.height + "px";
    ghost.style.margin = "0";
    ghost.style.zIndex = "9999";
    ghost.style.pointerEvents = "none";
    ghost.style.willChange = "transform, opacity, filter";
    document.body.appendChild(ghost);

    requestAnimationFrame(function () {
      ghost.classList.add("card-exit-ghost-run");
    });
    setTimeout(function () {
      if (ghost && ghost.parentNode) ghost.parentNode.removeChild(ghost);
    }, EXIT_MS + 80);
  }

  // The card is a React/Dash node: never removeChild it or insert siblings
  // into its parent (desyncs React's reconciliation -> a DIFFERENT card
  // vanishes). It only gets a transient class; the server render removes it
  // and runFlip slides the survivors from the snapshot (transform only).
  var STORE_SETTLE_MS = 8000;
  var STORE_MAX_WAIT_MS = 60000;

  function prepareStoreAnimation(card) {
    if (!card) return;
    var rects = snapshotCards();
    var started = performance.now();
    pendingRects = rects;
    pendingUntil = started + STORE_SETTLE_MS;
    animateGhost(card);
    card._fxExiting = true;
    card.classList.add("card-exiting");
    // Store failed / card kept by the server: give it back instead of
    // leaving an invisible slot. While the write is still in flight (the
    // button keeps .action-pending, interaction_stability.js) keep waiting,
    // so a slow shared-lock write does not make the card pop back early.
    function settle() {
      if (!card.isConnected || !card.classList.contains("card-exiting")) {
        card._fxExiting = false;
        return;
      }
      if (card.querySelector(".action-pending") && performance.now() - started < STORE_MAX_WAIT_MS) {
        if (pendingRects === rects) pendingUntil = performance.now() + STORE_SETTLE_MS;
        setTimeout(settle, 1000);
        return;
      }
      card._fxExiting = false;
      card.classList.remove("card-exiting");
      if (pendingRects === rects) pendingRects = null;
    }
    setTimeout(settle, STORE_SETTLE_MS);
  }

  function runFlip() {
    flipFrame = 0;
    if (!pendingRects || performance.now() > pendingUntil || prefersReducedMotion()) return;

    var cards = document.querySelectorAll("#cards-area .priority-card[data-card-key]:not(.card-filter-hidden)");
    // Read phase (one layout), then write phase.
    var moves = [];
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var oldRect = pendingRects.get(cardKey(card, i));
      if (!oldRect) continue;
      var newRect = card.getBoundingClientRect();
      var dx = oldRect.left - newRect.left;
      var dy = oldRect.top - newRect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      moves.push({ el: card, dx: dx, dy: dy });
    }
    if (!moves.length) return;
    moves.forEach(function (m) {
      m.el.classList.add("card-flipping");
      var anim = playOnce(m.el, [
        { transform: "translate3d(" + m.dx + "px," + m.dy + "px,0)" },
        { transform: "none" }
      ], { duration: FLIP_MS, easing: "cubic-bezier(0.22,1,0.36,1)" });
      var done = function () { m.el.classList.remove("card-flipping"); };
      if (anim) { anim.onfinish = done; anim.oncancel = done; } else { done(); }
    });
    pendingUntil = performance.now() + FLIP_MS + 80;
  }

  // Note delete is two-step: notes are shared by every user, so one stray
  // click must not remove one. The first click only arms the button (3 s);
  // it is stopped on window-capture, before React's root listener, so Dash
  // never sees it. The second click passes through and plays the collapse.
  window.addEventListener("click", function (e) {
    var del = e.target.closest && e.target.closest(".note-del-btn");
    if (!del) return;
    if (!del.classList.contains("is-armed")) {
      e.preventDefault();
      e.stopPropagation();
      document.querySelectorAll(".note-del-btn.is-armed").forEach(function (b) {
        if (b !== del) { b.classList.remove("is-armed"); b.setAttribute("aria-label", "Megjegyzés törlése"); }
      });
      del.classList.add("is-armed");
      del.setAttribute("aria-label", "Törlés megerősítése");
      clearTimeout(del._noteDisarm);
      del._noteDisarm = setTimeout(function () {
        del.classList.remove("is-armed");
        del.setAttribute("aria-label", "Megjegyzés törlése");
      }, 3000);
      return;
    }
    clearTimeout(del._noteDisarm);
    var row = del.closest(".note-row");
    if (row && !prefersReducedMotion()) row.classList.add("note-row-removing");
  }, { capture: true });

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".badge-stored-empty, .badge-stored-active");
    if (!btn) return;
    btn.disabled = true;
    if (btn.classList.contains("badge-stored-active") && currentFilter() === "betarolt") {
      window._flowActiveFilter = "betarolt";
    }
    var storeCard = btn.closest(".priority-card");
    // Green check "stamp" flash when storing (empty -> betárolva).
    if (storeCard && btn.classList.contains("badge-stored-empty") && !prefersReducedMotion()) {
      storeCard.classList.add("card-store-flash");
      pulse(storeCard);
    }
    prepareStoreAnimation(storeCard);
  }, { capture: true });

  // Grid read-failure panel: "Újrapróbálás" reuses the header refresh button,
  // so it gets the exact same manual-refresh path (and its disabled state).
  document.addEventListener("click", function (e) {
    var retry = e.target.closest("[data-action='refresh']");
    if (!retry) return;
    var refresh = document.getElementById("refresh-btn");
    if (refresh && !refresh.disabled) refresh.click();
  });

  document.addEventListener("click", function (e) {
    var truckBtn = e.target.closest(".truck-store-btn");
    if (!truckBtn) return;
    var truckCard = truckBtn.closest(".truck-sidebar-card");
    if (truckCard) pulse(truckCard);
  }, { capture: true });

  function initPlateInteractions() {
    document.addEventListener("mouseover", function (e) {
      var source = nearestPlateSource(e.target, false);
      if (!source) return;
      setPlateHover(sourcePlateInfo(source));
    });

    document.addEventListener("mouseout", function (e) {
      var source = nearestPlateSource(e.target, false);
      if (!source || source.contains(e.relatedTarget)) return;
      setPlateHover(null);
    });

    document.addEventListener("mousedown", function (e) {
      var source = nearestPlateSource(e.target, false);
      if (!source || prefersReducedMotion()) return;
      source.classList.remove("micro-tap");
      void source.offsetWidth;
      source.classList.add("micro-tap");
      setTimeout(function () { source.classList.remove("micro-tap"); }, 340);
    }, true);

    document.addEventListener("click", function (e) {
      if (e.target.closest(".truck-store-btn")) return;
      var chip = e.target.closest(".truck-focus-source, .legend-truck-chip");
      if (!chip) return;
      e.preventDefault();
      e.stopPropagation();
      setPlateFocus(sourcePlateInfo(chip), false);
    }, true);

    document.addEventListener("keydown", function (e) {
      var chip = e.target.closest && e.target.closest(".truck-focus-source, .legend-truck-chip");
      if (!chip || (e.key !== "Enter" && e.key !== " ")) return;
      if (e.target.closest(".truck-store-btn")) return;
      e.preventDefault();
      setPlateFocus(sourcePlateInfo(chip), false);
    }, true);

    document.addEventListener("dblclick", function (e) {
      if (e.target.closest(".truck-store-btn")) return;
      var source = nearestPlateSource(e.target, true);
      if (!source) return;
      e.preventDefault();
      e.stopPropagation();
      setPlateFocus(sourcePlateInfo(source), false);
    }, true);

    attachPlateObservers(0);
    applyPlateVisualState();
  }

  // Dash renders its layout after DOMContentLoaded: retry until the hosts
  // exist (same pattern as initCardFlipObserver), each attached once.
  // #cards-area mutations already re-run applyPlateVisualState there.
  var _plateObserved = {};
  function attachPlateObservers(attempt) {
    var missing = false;
    ["legend-bar", "truck-sidebar"].forEach(function (id) {
      if (_plateObserved[id]) return;
      var host = document.getElementById(id);
      if (!host) { missing = true; return; }
      new MutationObserver(_debouncedPlateState).observe(host, { childList: true, subtree: true });
      _plateObserved[id] = true;
    });
    if (missing && attempt < 200) {
      setTimeout(function () { attachPlateObservers(attempt + 1); }, 150);
    } else if (attempt > 0) {
      _debouncedPlateState();
    }
  }

  // ── Row detection: cards visually in the same row ─────────────────────
  // offsetTop is the layout position: the hover lift (translateY) and any
  // running FLIP transform do not move it. getBoundingClientRect() did, so a
  // hovered card sat 4 px above its row and the row sync randomly skipped
  // the peers (one card expanded, the stretched neighbours showed a void).
  function cardsInSameRow(card) {
    if (!card) return [];
    var area = document.getElementById("cards-area");
    if (!area) return [card];
    var top = card.offsetTop;
    var parent = card.parentElement;
    var all = area.querySelectorAll(
      ".priority-card[data-card-key]:not(.card-filter-hidden):not(.card-exiting)"
    );
    var out = [];
    for (var i = 0; i < all.length; i++) {
      var c = all[i];
      if (c.parentElement === parent && Math.abs(c.offsetTop - top) < 2) out.push(c);
    }
    return out;
  }

  // While a row grows or shrinks, the cards below slide under a still mouse:
  // each one passing the pointer used to flash its hover lift, glow and the
  // floating tooltip. The grid ignores the pointer for the resize window;
  // the clicked card keeps it (its own hover state stays stable).
  var resizeTimer = 0;
  var resizeOrigin = null;
  function markResizing(origin, ms) {
    var area = document.getElementById("cards-area");
    if (!area) return;
    if (resizeOrigin && resizeOrigin !== origin) resizeOrigin.classList.remove("fx-resize-origin");
    resizeOrigin = origin || null;
    if (resizeOrigin) resizeOrigin.classList.add("fx-resize-origin");
    area.classList.add("fx-resizing");
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      area.classList.remove("fx-resizing");
      if (resizeOrigin) resizeOrigin.classList.remove("fx-resize-origin");
      resizeOrigin = null;
    }, ms + 90);
  }

  function setCardExpanded(card, expanded) {
    card._fxExpanded = expanded;
    setClass(card, "card-expanded", expanded);
    var toggleEl = card.querySelector(".card-body-compact");
    if (toggleEl) toggleEl.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function toggleCardRow(card) {
    var willExpand = !card.classList.contains("card-expanded");
    var peers = cardsInSameRow(card);
    if (!peers.length) peers = [card];
    markResizing(card, willExpand ? EXPAND_MS : COLLAPSE_MS);
    for (var i = 0; i < peers.length; i++) setCardExpanded(peers[i], willExpand);
  }

  // ── Smooth <details> open/close (interruptible) ───────────────────────
  // Re-clicking mid-animation reverses from the current height instead of
  // being swallowed (the old _animating guard ignored the click).
  function animateDetailsToggle(details, willOpen) {
    if (!details) return;
    var intended = details._fxOpen != null ? details._fxOpen : details.hasAttribute("open");
    if (willOpen === intended) return;
    details._fxOpen = willOpen;
    // [open] stays on during the close animation (the content must render);
    // .is-open follows the intent at once, so the chevron turns on click.
    setClass(details, "is-open", willOpen);
    var running = details._fxAnim;
    var fromH = details.offsetHeight;
    if (running) {
      running.onfinish = null;
      running.cancel();
      details._fxAnim = null;
    }
    if (prefersReducedMotion() || typeof details.animate !== "function") {
      if (willOpen) details.setAttribute("open", "");
      else details.removeAttribute("open");
      details.style.overflow = "";
      return;
    }
    var toH;
    if (willOpen) {
      details.setAttribute("open", "");
      toH = details.offsetHeight;
    } else {
      details.removeAttribute("open");
      toH = details.offsetHeight;
      details.setAttribute("open", "");
    }
    details.style.overflow = "hidden";
    var anim = details.animate(
      [{ height: fromH + "px" }, { height: toH + "px" }],
      { duration: willOpen ? 240 : 190, easing: willOpen ? EASE_OUT : EASE_IN_OUT }
    );
    details._fxAnim = anim;
    anim.onfinish = function () {
      if (details._fxAnim !== anim) return;
      details._fxAnim = null;
      if (!willOpen) details.removeAttribute("open");
      details.style.overflow = "";
    };
  }

  // Inbound "Rakodás tételei": row-synced. Notes ("Megjegyzés"): this card only.
  document.addEventListener("click", function (e) {
    var summary = e.target.closest(".inbound-ready-block > summary, .card-notes-block > summary");
    if (!summary) return;
    var details = summary.parentNode;
    if (!details || details.tagName !== "DETAILS") return;
    e.preventDefault();
    e.stopPropagation();
    var intended = details._fxOpen != null ? details._fxOpen : details.hasAttribute("open");
    var willOpen = !intended;
    var card = details.closest(".priority-card");
    if (!details.classList.contains("inbound-ready-block")) {
      if (card) markResizing(card, willOpen ? 240 : 190);
      animateDetailsToggle(details, willOpen);
      return;
    }
    var peers = card ? cardsInSameRow(card) : [];
    if (!peers.length) peers = [card].filter(Boolean);
    if (card) markResizing(card, willOpen ? 240 : 190);
    peers.forEach(function (c) {
      var d = c.querySelector(".inbound-ready-block");
      if (d) animateDetailsToggle(d, willOpen);
    });
  }, true);

  // Card expand/collapse on click — row-synced. No FLIP here: the grid-row
  // transition already moves the rows below continuously; a FLIP on top
  // fought it (the first-frame delta was re-applied as a lagging transform).
  var TOGGLE_IGNORE = "button, summary, a, input, select, textarea, label, [contenteditable='true'], " +
    ".inbound-uld-note, .truck-focus-source, .legend-truck-chip, .plate-focus-pill, " +
    ".card-notes-block, .inbound-ready-list";
  document.addEventListener("click", function (e) {
    var card = e.target.closest(".priority-card");
    if (!card || !card.closest("#cards-area")) return;
    if (e.target.closest(TOGGLE_IGNORE)) return;
    if (card.classList.contains("card-exiting")) return;
    // The 2nd click of a double-click (plate focus on ULD cards) must not
    // collapse the card again: that was an open-close blink.
    if (e.detail > 1) return;
    // Selecting text (an AWB to copy) ends in a click: do not collapse.
    var sel = window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && sel.toString().trim() && card.contains(sel.anchorNode)) return;
    toggleCardRow(card);
    hideTip();
  });

  // Keyboard: Enter / Space on the focused compact body = the same toggle.
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter" && e.key !== " " && e.key !== "Spacebar") return;
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (!t || !t.classList || !t.classList.contains("card-body-compact")) return;
    if (!t.closest(".priority-card")) return;
    e.preventDefault();
    t.click();
  });

  // ── Floating quick-info tooltip ─────────────────────────────────────────
  var _floatTip = null;
  var _tipCard  = null;

  function ensureFloatTip() {
    if (!_floatTip) {
      _floatTip = document.createElement("div");
      _floatTip.className = "card-tooltip-float";
      document.body.appendChild(_floatTip);
    }
    return _floatTip;
  }

  function positionTip(tip, card) {
    var r  = card.getBoundingClientRect();
    var tw = Math.max(tip.offsetWidth || 200, 200);
    var th = tip.offsetHeight || 90;
    var left = r.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    var topBelow = r.bottom + 10;
    var topAbove = r.top - th - 10;
    var top = (topBelow + th > window.innerHeight - 8 && topAbove >= 8) ? topAbove : topBelow;
    tip.style.left = left + "px";
    tip.style.top  = top  + "px";
  }

  function buildTipHTML(card) {
    var plate    = (card.dataset.tipPlate    || "").trim();
    var driver   = (card.dataset.tipDriver   || "").trim();
    var progress = (card.dataset.tipProgress || "").trim();
    var color    = card.dataset.plateColor   || "#8b949e";
    if (!/^#[0-9a-fA-F]{3,8}$/.test(color)) color = "#8b949e";
    if (!plate && !driver && !progress) return "";
    var rows = [];
    if (plate)    rows.push('<div class="ftip-row"><span class="ftip-label">Rendszám</span><span class="ftip-val" style="color:' + color + '">' + htmlEscape(plate) + '</span></div>');
    if (driver)   rows.push('<div class="ftip-row"><span class="ftip-label">Sofőr</span><span class="ftip-val">' + htmlEscape(driver) + '</span></div>');
    if (progress) rows.push('<div class="ftip-row"><span class="ftip-label">Rakodás</span><span class="ftip-val">' + htmlEscape(progress) + '</span></div>');
    return rows.join("");
  }

  function isResizing() {
    var area = document.getElementById("cards-area");
    return !!(area && area.classList.contains("fx-resizing"));
  }

  function showTip(card) {
    // mouseenter (capture) fires for every child: same card = nothing to do.
    if (card === _tipCard) return;
    if (card.classList.contains("card-expanded") || isResizing()) return;
    var html = buildTipHTML(card);
    if (!html) return;
    var tip = ensureFloatTip();
    if (tip.innerHTML !== html) tip.innerHTML = html;
    tip.style.display = "block";
    positionTip(tip, card);
    _tipCard = card;
    requestAnimationFrame(function () {
      if (_tipCard === card) tip.classList.add("ftip-visible");
    });
  }

  function hideTip() {
    if (_floatTip) {
      _floatTip.classList.remove("ftip-visible");
      _tipCard = null;
    }
  }

  function initCardTooltips() {
    var area = document.getElementById("cards-area");
    if (!area) { setTimeout(initCardTooltips, 150); return; }

    area.addEventListener("mouseenter", function (e) {
      var card = e.target.closest(".priority-card");
      if (card) showTip(card);
    }, true);

    area.addEventListener("mouseleave", function (e) {
      var card = e.target.closest(".priority-card");
      if (!card) return;
      // Only hide when cursor actually leaves the card, not when moving between children
      if (!card.contains(e.relatedTarget)) {
        hideTip();
      }
    }, true);

    area.addEventListener("mousemove", function (e) {
      if (!_tipCard || !_floatTip || !_floatTip.classList.contains("ftip-visible")) return;
      var card = e.target.closest(".priority-card");
      if (card !== _tipCard) {
        hideTip();
        if (card) showTip(card);
      }
      // No reposition on move — position was set once when tip appeared
    });

    window.addEventListener("scroll", function () { if (_tipCard) hideTip(); }, { passive: true });
  }

  // ── Render observer ─────────────────────────────────────────────────────
  // React re-applies a card's className whenever the server changes it (new
  // priority colour, aged flag...). That silently dropped the client-only
  // classes: an open card snapped shut, a filtered-out card flashed visible
  // for one frame, a storing card popped back. Class records are repaired
  // synchronously (microtask, before paint), so the user never sees it.
  function repairCardClasses(card) {
    if (card._fxExpanded && !card.classList.contains("card-expanded")) {
      card.classList.add("card-expanded");
    }
    if (card._fxExiting && !card.classList.contains("card-exiting")) {
      card.classList.add("card-exiting");
    }
    var filter = window._flowActiveFilter || "all";
    var tokens = (card.getAttribute("data-filter") || "all").split(/\s+/);
    var hidden = tokens.indexOf(filter) === -1;
    if (card.classList.contains("card-filter-hidden") !== hidden) {
      card.classList.toggle("card-filter-hidden", hidden);
    }
  }

  function collectAddedCards(records) {
    var out = [];
    for (var i = 0; i < records.length; i++) {
      var added = records[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var n = added[j];
        if (n.nodeType !== 1) continue;
        if (n.classList.contains("priority-card")) {
          out.push(n);
        } else if (n.querySelectorAll) {
          var inner = n.querySelectorAll(".priority-card");
          for (var k = 0; k < inner.length; k++) out.push(inner[k]);
        }
      }
    }
    return out;
  }

  function initCardFlipObserver() {
    var area = document.getElementById("cards-area");
    if (!area) { setTimeout(initCardFlipObserver, 120); return; }
    // Only structural changes matter: text-only records (count-up, countdowns)
    // and our own class writes are ignored; bursts collapse into one frame.
    var renderFrame = 0;
    function isElementRecord(r) {
      if (r.type === "attributes") return r.attributeName !== "class";
      var i;
      for (i = 0; i < r.addedNodes.length; i++) if (r.addedNodes[i].nodeType === 1) return true;
      for (i = 0; i < r.removedNodes.length; i++) if (r.removedNodes[i].nodeType === 1) return true;
      return false;
    }
    var observer = new MutationObserver(function (records) {
      var structural = false;
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === "attributes" && r.attributeName === "class") {
          var t = r.target;
          if (t.classList && t.classList.contains("priority-card")) repairCardClasses(t);
          continue;
        }
        if (!structural && isElementRecord(r)) structural = true;
      }
      if (!structural) return;
      // New card nodes: filter state + one-shot entry before the first paint.
      var fresh = collectAddedCards(records);
      if (fresh.length) {
        var order = 0;
        for (var f = 0; f < fresh.length; f++) {
          repairCardClasses(fresh[f]);
          if (!fresh[f].classList.contains("card-filter-hidden")) cardEnter(fresh[f], order++);
        }
      }
      if (renderFrame) return;
      renderFrame = requestAnimationFrame(function () {
        renderFrame = 0;
        applyFlowFilter(currentFilter(), false);
        applyPlateVisualState();
        if (!pendingRects || performance.now() > pendingUntil) return;
        cancelAnimationFrame(flipFrame);
        flipFrame = 0;
        runFlip();
      });
    });
    observer.observe(area, {
      childList: true, subtree: true,
      attributes: true, attributeFilter: ["data-filter", "data-plate-key", "class"]
    });
    applyFlowFilter(currentFilter(), false);
  }

  function addRipple(e) {
    var btn = e.currentTarget;
    btn.classList.remove("btn-ripple");
    void btn.offsetWidth;
    btn.classList.add("btn-ripple");
  }

  function attachStatRipples() {
    var items = document.querySelectorAll(".stat-item");
    for (var i = 0; i < items.length; i++) {
      if (!items[i]._rippleAttached) {
        items[i].addEventListener("mousedown", addRipple);
        items[i]._rippleAttached = true;
      }
    }
  }

  function initStatRipples() {
    var bar = document.querySelector(".stats-bar");
    if (!bar) { setTimeout(initStatRipples, 120); return; }
    var observer = new MutationObserver(attachStatRipples);
    observer.observe(bar, { childList: true, subtree: true });
    attachStatRipples();
  }

  function init() {
    initStatRipples();
    initCardFlipObserver();
    initCardTooltips();
    initPlateInteractions();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
