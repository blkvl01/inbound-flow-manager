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
      '<span class="plate-focus-clear">×</span>';
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

  function applyPlateVisualState() {
    var cards = document.querySelectorAll("#cards-area .priority-card[data-plate-key]");
    var hover = plateHoverKey && !plateFocusKey ? plateHoverKey : "";
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      card.classList.remove("plate-hover-match", "plate-hover-dim", "plate-focus-match", "plate-focus-dim");
      if (plateFocusKey) {
        card.classList.add(focusMatches(card, plateFocusKey) ? "plate-focus-match" : "plate-focus-dim");
      } else if (hover) {
        card.classList.add(focusMatches(card, hover) ? "plate-hover-match" : "plate-hover-dim");
      }
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

  function applyFlowFilter(filter) {
    filter = filter || currentFilter();
    window._flowActiveFilter = filter;

    var area = document.getElementById("cards-area");
    if (!area) return;

    var cards = area.querySelectorAll(".priority-card[data-filter]");
    var reduced = prefersReducedMotion();

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
      c.classList.toggle("card-filter-hidden", !visible);
      if (visible) {
        visibleCount++;
        if (wasHidden) newlyShown.push(c);
      }
    }

    var empty = area.querySelector(".filter-empty-state");
    if (empty) empty.style.display = cards.length && visibleCount === 0 ? "block" : "none";

    if (reduced) return;

    // Newly-shown cards pop in; surviving cards slide from their old spot (FLIP).
    var vis = area.querySelectorAll(".priority-card[data-filter]:not(.card-filter-hidden)");
    for (var j = 0; j < vis.length; j++) {
      var card = vis[j];
      if (newlyShown.indexOf(card) !== -1) {
        card.classList.remove("card-filter-in");
        void card.offsetWidth; // restart the animation
        card.classList.add("card-filter-in");
        (function (el) {
          setTimeout(function () { el.classList.remove("card-filter-in"); }, 380);
        })(card);
        continue;
      }
      var oldRect = oldRects.get(cardKey(card, j));
      if (!oldRect) continue;
      var newRect = card.getBoundingClientRect();
      var dx = oldRect.left - newRect.left;
      var dy = oldRect.top - newRect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      card.style.transition = "none";
      card.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
      card.style.willChange = "transform";
      (function (el) {
        requestAnimationFrame(function () {
          el.style.transition = "transform 340ms cubic-bezier(0.22,1,0.36,1)";
          el.style.transform = "";
          setTimeout(function () { el.style.transition = ""; el.style.willChange = ""; }, 380);
        });
      })(card);
    }
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

  function animateLayoutAfterRemoval(oldRects, removedCard) {
    if (!oldRects || !removedCard || prefersReducedMotion()) return;
    var parent = removedCard.parentNode;
    if (!parent) return;

    var ph = document.createElement("div");
    ph.className = "card-removal-placeholder";
    ph.style.width = removedCard.offsetWidth + "px";
    ph.style.height = removedCard.offsetHeight + "px";
    ph.style.minHeight = "0";
    ph.style.pointerEvents = "none";
    parent.insertBefore(ph, removedCard);
    parent.removeChild(removedCard);

    var cards = parent.querySelectorAll(".priority-card[data-card-key]:not(.card-filter-hidden)");
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var oldRect = oldRects.get(cardKey(card, i));
      if (!oldRect) continue;
      var newRect = card.getBoundingClientRect();
      var dx = oldRect.left - newRect.left;
      var dy = oldRect.top - newRect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      card.classList.add("card-flipping");
      card.style.transition = "none";
      card.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
      card.style.willChange = "transform";
      (function (el) {
        requestAnimationFrame(function () {
          el.style.transition = "transform " + FLIP_MS + "ms cubic-bezier(0.22,1,0.36,1)";
          el.style.transform = "";
          setTimeout(function () {
            el.classList.remove("card-flipping");
            el.style.transition = "";
            el.style.willChange = "";
          }, FLIP_MS + 40);
        });
      })(card);
    }

    requestAnimationFrame(function () {
      ph.style.width = "0";
      ph.style.height = "0";
      ph.style.opacity = "0";
      ph.style.transform = "scale(0.96)";
    });
    setTimeout(function () {
      if (ph && ph.parentNode) ph.parentNode.removeChild(ph);
    }, FLIP_MS + 80);
  }

  function prepareStoreAnimation(card) {
    if (!card) return;
    var oldRects = snapshotCards();
    pendingRects = oldRects;
    pendingUntil = performance.now() + 1000;
    card.classList.add("card-exiting");
    animateGhost(card);
    requestAnimationFrame(function () {
      animateLayoutAfterRemoval(oldRects, card);
    });
  }

  function runFlip() {
    flipFrame = 0;
    if (!pendingRects || performance.now() > pendingUntil || prefersReducedMotion()) return;

    var cards = document.querySelectorAll("#cards-area .priority-card[data-card-key]:not(.card-filter-hidden)");
    var animated = false;
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var oldRect = pendingRects.get(cardKey(card, i));
      if (!oldRect) continue;

      var newRect = card.getBoundingClientRect();
      var dx = oldRect.left - newRect.left;
      var dy = oldRect.top - newRect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;

      animated = true;
      card.classList.add("card-flipping");
      card.style.transition = "none";
      card.style.transform = "translate3d(" + dx + "px," + dy + "px,0)";
      card.style.willChange = "transform";

      (function (el) {
        requestAnimationFrame(function () {
          el.style.transition = "transform " + FLIP_MS + "ms cubic-bezier(0.22,1,0.36,1)";
          el.style.transform = "";
          setTimeout(function () {
            el.classList.remove("card-flipping");
            el.style.transition = "";
            el.style.willChange = "";
          }, FLIP_MS + 40);
        });
      })(card);
    }
    if (animated) pendingUntil = performance.now() + FLIP_MS + 80;
  }

  // Note delete: play an optimistic collapse on the row; Dash re-renders
  // and removes it server-side. Purely visual feedback during the roundtrip.
  document.addEventListener("click", function (e) {
    var del = e.target.closest(".note-del-btn");
    if (!del) return;
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

    var area = document.getElementById("cards-area");
    if (area) {
      var observer = new MutationObserver(_debouncedPlateState);
      observer.observe(area, { childList: true, subtree: true });
    }
    var legend = document.getElementById("legend-bar");
    if (legend) {
      var legendObserver = new MutationObserver(_debouncedPlateState);
      legendObserver.observe(legend, { childList: true, subtree: true });
    }
    var truckSidebar = document.getElementById("truck-sidebar");
    if (truckSidebar) {
      var truckObserver = new MutationObserver(_debouncedPlateState);
      truckObserver.observe(truckSidebar, { childList: true, subtree: true });
    }
    applyPlateVisualState();
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

    window.addEventListener("scroll", function () { hideTip(); }, { passive: true });
  }

  function initCardFlipObserver() {
    var area = document.getElementById("cards-area");
    if (!area) { setTimeout(initCardFlipObserver, 120); return; }
    var observer = new MutationObserver(function () {
      applyFlowFilter(currentFilter());
      applyPlateVisualState();
      if (!pendingRects || performance.now() > pendingUntil) return;
      cancelAnimationFrame(flipFrame);
      flipFrame = requestAnimationFrame(runFlip);
    });
    observer.observe(area, { childList: true, subtree: true });
    applyFlowFilter(currentFilter());
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
