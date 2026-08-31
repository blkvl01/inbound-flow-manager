/* Condensed sticky header on scroll.
   Toggles `hdr-mini` on <html> with hysteresis: scrolling past ENTER shrinks
   the sticky .app-header into a compact bar; scrolling back near the top
   restores the full header. All visuals live in style.css ("Condensed header
   on scroll" block) — this file flips the class, and owns the floating
   #hdr-top-btn "vissza a tetejére" button whose circular ring doubles as a
   scroll-progress indicator (stroke-dashoffset is driven here directly).

   Stability notes:
   - The class lives on <html>, not <body>, so Dash-side body class toggles
     (theme-light, flow-uld-mode, app-ready, ...) can never collide with it.
   - The header's flow footprint is constant (mini margin-bottom grows by
     --hdr-flow-comp, the lost height): the document below never moves and
     scrollHeight never changes during the morph — no per-frame page reflow,
     no scroll re-clamp. EXIT tracks the learned height delta so mini mode
     can't persist where the bar would no longer cover the reserved space.
   - Short pages never condense: there must be room to scroll past ENTER.
   - #hdr-top-btn is appended to <body>, NEVER inside the React/Dash-owned
     header subtree, so Dash re-renders can't orphan or duplicate it.
   - The header element is re-measured (ResizeObserver) so the existing
     --uld-sticky-top mechanism in uld_manager.js keeps working unchanged. */
(function () {
    'use strict';

    var EXIT_BASE = 95;     // scroll back above this → full header (relearned)
    var ENTER_BASE = 140;   // scroll past this → mini header (relearned)
    var exitY = EXIT_BASE;
    var enterY = ENTER_BASE;
    var mini = false;
    var fullH = 0;
    var ticking = false;
    var measureTimer = null;
    var morphTimer = null;
    var MORPH_MS = 560; // longest morph transition (0.46s) + settle margin

    var RING_R = 20;
    var RING_C = 2 * Math.PI * RING_R;

    var root = document.documentElement;
    var topBtn = null;
    var ringEl = null;

    function headerEl() { return document.querySelector('.app-header'); }

    function ensureTopBtn() {
        if (topBtn && document.body.contains(topBtn)) return;
        topBtn = document.createElement('button');
        topBtn.id = 'hdr-top-btn';
        topBtn.type = 'button';
        topBtn.setAttribute('aria-label', 'Vissza a tetejére');
        topBtn.title = 'Vissza a tetejére';
        topBtn.innerHTML =
            '<svg viewBox="0 0 46 46" aria-hidden="true">' +
              '<defs>' +
                '<linearGradient id="hdr-top-grad" x1="0" y1="1" x2="1" y2="0">' +
                  '<stop offset="0" stop-color="#ff6b35"/>' +
                  '<stop offset="0.55" stop-color="#7c72dc"/>' +
                  '<stop offset="1" stop-color="#00d4aa"/>' +
                '</linearGradient>' +
              '</defs>' +
              '<circle class="hdr-top-track" cx="23" cy="23" r="' + RING_R + '"/>' +
              '<circle class="hdr-top-ring" cx="23" cy="23" r="' + RING_R + '"' +
                ' stroke-dasharray="' + RING_C.toFixed(2) + '"' +
                ' stroke-dashoffset="' + RING_C.toFixed(2) + '"/>' +
              '<path class="hdr-top-arrow" d="M23 29.5 V17.5 M17.5 22.5 L23 17 L28.5 22.5"/>' +
            '</svg>';
        topBtn.addEventListener('click', function () {
            var smooth = !(window.matchMedia &&
                window.matchMedia('(prefers-reduced-motion: reduce)').matches);
            try { window.scrollTo({ top: 0, behavior: smooth ? 'smooth' : 'auto' }); }
            catch (e) { window.scrollTo(0, 0); }
        });
        document.body.appendChild(topBtn);
        ringEl = topBtn.querySelector('.hdr-top-ring');
    }

    function remeasure() {
        var el = headerEl();
        if (!el) return;
        var h = el.getBoundingClientRect().height;
        if (h <= 0) return;
        if (!mini) {
            fullH = h;
        } else if (fullH > 0) {
            var delta = Math.max(0, Math.ceil(fullH - h));
            // Feed the flow-footprint compensation: in mini mode the header's
            // bottom margin grows by exactly this much (see style.css), so
            // the document below never reflows during the morph.
            root.style.setProperty('--hdr-flow-comp', delta + 'px');
            // The constant flow footprint means content sits delta+18px below
            // the top; if mini persisted under y < delta+20 a standing gap
            // would show between the bar and the content, so the exit
            // threshold tracks the learned delta. The footprint also keeps
            // scrollHeight constant through the morph (no scroll re-clamp),
            // so a modest hysteresis band is enough.
            exitY = Math.max(40, delta + 20);
            enterY = Math.max(ENTER_BASE, exitY + 44);
        }
    }

    function setMini(on) {
        if (mini === on) return;
        mini = on;
        // hdr-morphing suspends backdrop blurs / layered shadows for the
        // duration of the morph (see the style.css kill-switch block) —
        // re-filtering a blur over a resizing box every frame is what
        // dropped the morph to 15-20 FPS.
        root.classList.add('hdr-morphing');
        root.classList.toggle('hdr-mini', on);
        if (morphTimer) clearTimeout(morphTimer);
        morphTimer = setTimeout(function () {
            root.classList.remove('hdr-morphing');
        }, MORPH_MS);
        if (measureTimer) clearTimeout(measureTimer);
        measureTimer = setTimeout(remeasure, 520); // after the morph settles
    }

    function update() {
        ticking = false;
        if (!headerEl()) return;
        var y = window.pageYOffset || root.scrollTop || 0;
        var max = Math.max(0, (root.scrollHeight || 0) - window.innerHeight);
        var p = max > 0 ? Math.min(1, Math.max(0, y / max)) : 0;
        if (ringEl) ringEl.style.strokeDashoffset = (RING_C * (1 - p)).toFixed(1);
        if (!mini) {
            if (y > enterY && max > enterY + 90) setMini(true);
        } else if (y < exitY) {
            setMini(false);
        }
    }

    function requestUpdate() {
        if (ticking) return;
        ticking = true;
        window.requestAnimationFrame(update);
    }

    // Suspend backdrop-filter blurs while the page is actively scrolling.
    // Re-computing every glass panel's blur against the content moving behind
    // it, every frame, is the dominant scroll-jank cost — the same reason
    // hdr-morphing suspends blurs while the header geometry animates. The class
    // is dropped a beat after scrolling settles, so the at-rest glass look is
    // unchanged; the blur is only skipped during the motion (where the eye
    // can't resolve it anyway). Lives on <html> next to hdr-morphing.
    var scrollFxTimer = null;
    function markScrolling() {
        if (!root.classList.contains('is-scrolling')) {
            root.classList.add('is-scrolling');
        }
        if (scrollFxTimer) clearTimeout(scrollFxTimer);
        scrollFxTimer = setTimeout(function () {
            root.classList.remove('is-scrolling');
        }, 140);
    }

    function onScroll() {
        markScrolling();
        requestUpdate();
    }

    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', function () { remeasure(); requestUpdate(); }, { passive: true });

    // Dash renders the layout async after page load. Use MutationObserver so the
    // header is found immediately when it appears in the DOM, instead of polling
    // blindly for up to 20 seconds (old approach: 250ms × 80 retries).
    (function init() {
        var el = headerEl();
        if (el) {
            ensureTopBtn();
            remeasure();
            if (window.ResizeObserver) {
                new ResizeObserver(function () { if (!mini) remeasure(); }).observe(el);
            }
            requestUpdate();
            return;
        }
        var root = document.getElementById('app-root') || document.body;
        var initObs = new MutationObserver(function (_, obs) {
            var found = headerEl();
            if (!found) return;
            obs.disconnect();
            ensureTopBtn();
            remeasure();
            if (window.ResizeObserver) {
                new ResizeObserver(function () { if (!mini) remeasure(); }).observe(found);
            }
            requestUpdate();
        });
        initObs.observe(root, { childList: true, subtree: true });
    })();
})();
