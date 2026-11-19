/* Lightweight card interactions.
   - Betarolva click: fixed ghost exit, so Dash can re-render immediately.
   - Remaining cards: transform-only FLIP animation after the new DOM lands.
   - Stat filters: local ripple feedback, no server wait. */
(function () {
  "use strict";

  var EXIT_MS = 320;
  var FLIP_MS = 340;
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
      pill.classList.remove("plate-focus-pill-visible");
      pill.innerHTML = "";
      return;
    }
    pill.style.setProperty("--plate-focus-color", plateFocusColor || "#8b949e");
    pill.innerHTML =
      '<span class="plate-focus-dot"></span>' +
      '<span class="plate-focus-text">Kamion fókusz: <b>' + htmlEscape(plateFocusLabel || plateFocusKey) + '</b></span>' +
      '<span class="plate-focus-clear"><span class="fi fi-x" aria-hidden="true"></span></span>';
    pill.classList.add("plate-focus-pill-visible");
  }

  function pulsePlateMatches(key) {
    if (!key || prefersReducedMotion()) return;
    var cards = document.querySelectorAll("#cards-area .priority-card[data-plate-key]");
    for (var i = 0; i < cards.length; i++) {
      if (!focusMatches(cards[i], key)) continue;
      cards[i].classList.remove("plate-focus-pulse");
      void cards[i].offsetWidth;
      cards[i].classList.add("plate-focus-pulse");
      (function (card) {
        setTimeout(function () { card.classList.remove("plate-focus-pulse"); }, 680);
      })(cards[i]);
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
      chip.classList.toggle("legend-truck-active", !!plateFocusKey && focusMatches(chip, plateFocusKey));
      chip.classList.toggle("legend-truck-hover", !plateFocusKey && !!hover && focusMatches(chip, hover));
      chip.classList.toggle("truck-sidebar-active", !!plateFocusKey && focusMatches(chip, plateFocusKey));
      chip.classList.toggle("truck-sidebar-hover", !plateFocusKey && !!hover && focusMatches(chip, hover));
    }
    document.body.classList.toggle("plate-focus-active", !!plateFocusKey);
    updatePlateFocusPill();
    scrollActiveTruckIntoView();
  }

  function setPlateHover(info) {
    var next = info ? info.key : "";
    if (plateHoverKey === next) return;
    plateHoverKey = next;
    applyPlateVisualState();
  }

  function clearPlateFocus(pulse) {
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
    if (pulse && oldKey) pulsePlateMatches(oldKey);
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
    // Two phases: every rect is read first (one layout, which also restarts the
    // pop-in animation), then all writes.
    newlyShown.forEach(function (el) { el.classList.remove("card-filter-in"); });
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
    newlyShown.forEach(function (el) {
      el.classList.add("card-filter-in");
      setTimeout(function () { el.classList.remove("card-filter-in"); }, 380);
    });
    moves.forEach(function (m) {
      m.el.style.transition = "none";
      m.el.style.transform = "translate3d(" + m.dx + "px," + m.dy + "px,0)";
      m.el.style.willChange = "transform";
    });
    if (!moves.length) return;
    requestAnimationFrame(function () {
      moves.forEach(function (m) {
        m.el.style.transition = "transform 340ms cubic-bezier(0.22,1,0.36,1)";
        m.el.style.transform = "";
      });
      setTimeout(function () {
        moves.forEach(function (m) { m.el.style.transition = ""; m.el.style.willChange = ""; });
      }, 380);
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
    card.classList.add("card-exiting");
    // Store failed / card kept by the server: give it back instead of
    // leaving an invisible slot. While the write is still in flight (the
    // button keeps .action-pending, interaction_stability.js) keep waiting,
    // so a slow shared-lock write does not make the card pop back early.
    function settle() {
      if (!card.isConnected || !card.classList.contains("card-exiting")) return;
      if (card.querySelector(".action-pending") && performance.now() - started < STORE_MAX_WAIT_MS) {
        if (pendingRects === rects) pendingUntil = performance.now() + STORE_SETTLE_MS;
        setTimeout(settle, 1000);
        return;
      }
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
      m.el.style.transition = "none";
      m.el.style.transform = "translate3d(" + m.dx + "px," + m.dy + "px,0)";
      m.el.style.willChange = "transform";
    });
    requestAnimationFrame(function () {
      moves.forEach(function (m) {
        m.el.style.transition = "transform " + FLIP_MS + "ms cubic-bezier(0.22,1,0.36,1)";
        m.el.style.transform = "";
      });
      setTimeout(function () {
        moves.forEach(function (m) {
          m.el.classList.remove("card-flipping");
          m.el.style.transition = "";
          m.el.style.willChange = "";
        });
      }, FLIP_MS + 40);
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
    // Green check "stamp" flash when storing (empty → betárolva).
    if (storeCard && btn.classList.contains("badge-stored-empty") && !prefersReducedMotion()) {
      storeCard.classList.add("card-store-flash");
      storeCard.classList.add("card-action-feedback");
      setTimeout(function () { storeCard.classList.remove("card-action-feedback"); }, 620);
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
    if (truckCard && !prefersReducedMotion()) {
      truckCard.classList.add("card-action-feedback");
      setTimeout(function () { truckCard.classList.remove("card-action-feedback"); }, 620);
    }
  }, { capture: true });

  // ── Row detection: cards visually in the same row ─────────────────────
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

  function cardsInSameRow(card) {
    if (!card) return [];
    var area = document.getElementById("cards-area");
    if (!area) return [card];
    var all = Array.from(area.querySelectorAll(
      ".priority-card[data-card-key]:not(.card-filter-hidden):not(.card-exiting)"
    ));
    var top = card.getBoundingClientRect().top;
    var TOL = 4;
    return all.filter(function (c) {
      return Math.abs(c.getBoundingClientRect().top - top) < TOL;
    });
  }

  // ── Smooth <details> open/close animation ─────────────────────────────
  function animateDetailsToggle(details, willOpen) {
    if (!details || details._animating) return;
    var currentlyOpen = details.hasAttribute("open");
    if (willOpen === currentlyOpen) return;
    if (prefersReducedMotion()) {
      if (willOpen) details.setAttribute("open", "");
      else details.removeAttribute("open");
      return;
    }
    details._animating = true;
    var fromH, toH;
    if (willOpen) {
      fromH = details.offsetHeight;
      details.setAttribute("open", "");
      toH = details.offsetHeight;
    } else {
      fromH = details.offsetHeight;
      details.removeAttribute("open");
      toH = details.offsetHeight;
      details.setAttribute("open", "");
    }
    details.style.overflow = "hidden";
    details.style.willChange = "height";
    var anim = details.animate(
      [{ height: fromH + "px" }, { height: toH + "px" }],
      {
        duration: willOpen ? 320 : 260,
        easing: willOpen ? "cubic-bezier(0.22,1,0.36,1)" : "cubic-bezier(0.4,0,0.2,1)"
      }
    );
    anim.onfinish = function () {
      if (!willOpen) details.removeAttribute("open");
      details.style.overflow = "";
      details.style.willChange = "";
      details._animating = false;
    };
    anim.oncancel = function () {
      details.style.overflow = "";
      details.style.willChange = "";
      details._animating = false;
    };
  }

  // ── Inbound "Rakodás tételei" — row-synced smooth toggle ──────────────
  document.addEventListener("click", function (e) {
    var summary = e.target.closest(".inbound-ready-block > summary");
    if (!summary) return;
    var details = summary.parentNode;
    if (!details || !details.classList.contains("inbound-ready-block")) return;
    e.preventDefault();
    e.stopPropagation();
    var willOpen = !details.hasAttribute("open");
    var card = details.closest(".priority-card");
    var peers = card ? cardsInSameRow(card) : [];
    if (!peers.length) peers = [card].filter(Boolean);
    peers.forEach(function (c) {
      var d = c.querySelector(".inbound-ready-block");
      if (d) animateDetailsToggle(d, willOpen);
    });
  }, true);

  // Card expand/collapse on click — row-synced
  document.addEventListener("click", function (e) {
    var card = e.target.closest(".priority-card");
    if (!card) return;
    // Skip if clicking interactive elements inside the card
    if (e.target.closest("button, summary, a, input, select, .inbound-uld-note, .truck-focus-source, .legend-truck-chip, .plate-focus-pill")) return;
    if (card.classList.contains("card-exiting")) return;
    var willExpand = !card.classList.contains("card-expanded");
    var peers = cardsInSameRow(card);
    if (!peers.length) peers = [card];
    var oldRects = prefersReducedMotion() ? null : snapshotCards();
    peers.forEach(function (p) {
      p.classList.toggle("card-expanded", willExpand);
      var toggleEl = p.querySelector(".card-body-compact");
      if (toggleEl) toggleEl.setAttribute("aria-expanded", willExpand ? "true" : "false");
      if (!prefersReducedMotion()) {
        p.classList.remove("card-expand-feedback");
        void p.offsetWidth;
        p.classList.add("card-expand-feedback");
        setTimeout(function () { p.classList.remove("card-expand-feedback"); }, 460);
      }
    });
    if (oldRects && !prefersReducedMotion()) {
      pendingRects = oldRects;
      pendingUntil = performance.now() + 1000;
      cancelAnimationFrame(flipFrame);
      flipFrame = requestAnimationFrame(runFlip);
    }
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

  function showTip(card) {
    // mouseenter (capture) fires for every child: same card = nothing to do.
    if (card === _tipCard) return;
    if (card.classList.contains("card-expanded")) return;
    var html = buildTipHTML(card);
    if (!html) return;
    var tip = ensureFloatTip();
    tip.innerHTML = html;
    tip.style.display = "block";
    positionTip(tip, card);
    _tipCard = card;
    requestAnimationFrame(function () { tip.classList.add("ftip-visible"); });
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

  function initCardFlipObserver() {
    var area = document.getElementById("cards-area");
    if (!area) { setTimeout(initCardFlipObserver, 120); return; }
    // Only structural changes matter: text-only records (count-up, countdowns)
    // and our own class writes are ignored; bursts collapse into one frame.
    var renderFrame = 0;
    function isElementRecord(r) {
      if (r.type === "attributes") return true;
      var i;
      for (i = 0; i < r.addedNodes.length; i++) if (r.addedNodes[i].nodeType === 1) return true;
      for (i = 0; i < r.removedNodes.length; i++) if (r.removedNodes[i].nodeType === 1) return true;
      return false;
    }
    var observer = new MutationObserver(function (records) {
      if (renderFrame || !records.some(isElementRecord)) return;
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
      attributes: true, attributeFilter: ["data-filter", "data-plate-key"]
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
