/* Shift hourly kg chart — a single clean curve showing how the shift unfolded,
 * with a light y-axis scale (gridline bands) so magnitudes read at a glance.
 *
 * Fed by the `kpi-muszak-hourly-store` payload (Dash clientside callback calls
 * window.__renderShiftHourlyChart(payload)):
 *   { current: {name, range, hours[12]}, prev: {name, range, hours[12]} }
 * Each hour: { label:"HH", kg, active, current }.
 *
 * Navigation: ‹/› arrows (on BOTH the mini KPI card and the panel header, kept in
 * sync) step one shift back and forth (current ⇄ previous, max one step). The card
 * value/label/range follow the displayed shift too, so card and chart move together.
 */
(function () {
    'use strict';
    if (window.__kpiChartInit) return;
    window.__kpiChartInit = true;

    var SVG_W = 360, SVG_H = 140;
    var PAD_L = 26, PAD_R = 14, PAD_T = 16, PAD_B = 16;
    var INNER_W = SVG_W - PAD_L - PAD_R;
    var INNER_H = SVG_H - PAD_T - PAD_B;

    var state = { current: null, prev: null, view: 'current', mode: 'inbound' };
    var observedValues = new WeakSet();
    var openDrawTimer = null;
    var accents = {
        inbound:  { a: '#ff9a4d', b: '#ff7a3c', c: '#ffb066', fill: '#ff7a3c' },
        outbound: { a: '#c084fc', b: '#a855f7', c: '#ddd6fe', fill: '#a855f7' }
    };

    function fmtKg(kg) {
        var nkg = Math.round(Number(kg) || 0);
        return String(nkg).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' kg';
    }
    function payloadKg(data, hours) {
        var raw = data && data.kg;
        var n = Number(raw);
        if (raw !== null && raw !== undefined && isFinite(n)) return n;
        return (hours || []).reduce(function (s, h) { return s + (Number(h.kg) || 0); }, 0);
    }
    // Compact, adaptive axis label: 500 → "500", 2500 → "2.5k", 30000 → "30k".
    function kCompact(v) {
        v = Math.round(v);
        if (v >= 10000) return Math.round(v / 1000) + 'k';
        if (v >= 1000)  return (Math.round(v / 100) / 10) + 'k';
        return '' + v;
    }
    // Round axis ceiling so the top gridline is a clean number with headroom.
    function niceMax(v) {
        if (!(v > 0)) return 1000;
        var pow = Math.pow(10, Math.floor(Math.log10(v)));
        var n = v / pow;
        var f = n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 7.5 ? 7.5 : 10;
        return f * pow;
    }

    function smoothPath(pts, minY, maxY) {
        if (!pts.length) return '';
        if (pts.length === 1) return 'M' + pts[0].x + ',' + pts[0].y;
        var t = 0.16;
        var clampY = function (y) {
            if (typeof minY === 'number' && y < minY) return minY;
            if (typeof maxY === 'number' && y > maxY) return maxY;
            return y;
        };
        var d = 'M' + pts[0].x.toFixed(2) + ',' + pts[0].y.toFixed(2);
        for (var i = 0; i < pts.length - 1; i++) {
            var p0 = pts[i - 1] || pts[i];
            var p1 = pts[i];
            var p2 = pts[i + 1];
            var p3 = pts[i + 2] || p2;
            var c1x = p1.x + (p2.x - p0.x) * t, c1y = clampY(p1.y + (p2.y - p0.y) * t);
            var c2x = p2.x - (p3.x - p1.x) * t, c2y = clampY(p2.y - (p3.y - p1.y) * t);
            d += 'C' + c1x.toFixed(2) + ',' + c1y.toFixed(2) + ' ' +
                 c2x.toFixed(2) + ',' + c2y.toFixed(2) + ' ' +
                 p2.x.toFixed(2) + ',' + p2.y.toFixed(2);
        }
        return d;
    }

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function setText(id, text) {
        var el = document.getElementById(id);
        if (!el) return;
        text = String(text == null ? '' : text);
        if (el.textContent === text) return;
        el.textContent = text;
        el.classList.remove('kpi-value-changing');
        void el.offsetWidth;
        el.classList.add('kpi-value-changing');
    }
    function activeData() { return state.view === 'prev' ? state.prev : state.current; }
    function activeAccent() { return accents[state.mode] || accents.inbound; }

    function syncModeUi() {
        var outbound = state.mode === 'outbound';
        var wrapper = document.getElementById('kpi-muszak-wrapper');
        if (wrapper) wrapper.classList.toggle('kpi-muszak-mode-outbound', outbound);
        var inboundBtn = document.getElementById('kpi-mode-inbound');
        var outboundBtn = document.getElementById('kpi-mode-outbound');
        if (inboundBtn) inboundBtn.classList.toggle('is-active', !outbound);
        if (outboundBtn) outboundBtn.classList.toggle('is-active', outbound);
    }

    function normalizedHours(hours) {
        var out = Array.isArray(hours) ? hours.slice() : [];
        if (out.length < 2) return out;
        var first = parseInt(out[0] && out[0].label, 10);
        var last = parseInt(out[out.length - 1] && out[out.length - 1].label, 10);
        if (first >= 18 && first <= 20 && last >= 8 && last <= 10) {
            out.reverse();
        }
        return out;
    }

    function geometry(hours) {
        var n = hours.length;
        var rawMax = 0;
        hours.forEach(function (h) { var kg = Number(h.kg) || 0; if (kg > rawMax) rawMax = kg; });
        var scaleMax = niceMax(rawMax);
        var xOf = function (i) { return PAD_L + (n === 1 ? INNER_W / 2 : (i / (n - 1)) * INNER_W); };
        var yOf = function (kg) { return PAD_T + INNER_H * (1 - (Number(kg) || 0) / scaleMax); };
        var pts = hours.map(function (h, i) { return { x: xOf(i), y: yOf(h.kg), kg: Number(h.kg) || 0, h: h, i: i }; });
        return { n: n, xOf: xOf, yOf: yOf, pts: pts, baseY: PAD_T + INNER_H, scaleMax: scaleMax };
    }

    // Horizontal gridline bands + y-axis labels (0 · mid · top).
    function buildGrid(g) {
        var levels = [0, 0.5, 1];
        var x1 = PAD_L, x2 = SVG_W - PAD_R;
        var bands = '', lines = '', labels = '';
        for (var i = 0; i < levels.length - 1; i++) {
            var yTop = g.yOf(levels[i + 1] * g.scaleMax);
            var yBot = g.yOf(levels[i] * g.scaleMax);
            bands += '<rect class="kpi-hourly-band band-' + i + '" x="' + x1 + '" y="' + yTop.toFixed(2) +
                     '" width="' + (x2 - x1) + '" height="' + (yBot - yTop).toFixed(2) + '"/>';
        }
        levels.forEach(function (lv) {
            var val = lv * g.scaleMax;
            var y = g.yOf(val);
            lines += '<line class="kpi-hourly-grid' + (lv === 0 ? ' is-base' : '') + '" x1="' + x1 +
                     '" y1="' + y.toFixed(2) + '" x2="' + x2 + '" y2="' + y.toFixed(2) + '"/>';
            if (lv > 0) {
                labels += '<text class="kpi-hourly-ylabel" x="' + (PAD_L - 5) + '" y="' + y.toFixed(2) +
                          '" text-anchor="end" dominant-baseline="middle">' + esc(kCompact(val)) + '</text>';
            }
        });
        return bands + lines + labels;
    }

    function buildSvg(hours) {
        var ac = activeAccent();
        var g = geometry(hours);
        var pts = g.pts, n = g.n;
        var line = smoothPath(pts, PAD_T, g.baseY);
        var area = line + ' L' + pts[n - 1].x.toFixed(2) + ',' + g.baseY.toFixed(2) +
                   ' L' + pts[0].x.toFixed(2) + ',' + g.baseY.toFixed(2) + ' Z';

        var firstFuture = -1, currentIdx = -1;
        for (var i = 0; i < n; i++) { if (firstFuture < 0 && !hours[i].active) firstFuture = i; if (hours[i].current) currentIdx = i; }

        var futureRect = '';
        if (firstFuture >= 0) {
            var fx = firstFuture === 0 ? PAD_L : (g.xOf(firstFuture) + g.xOf(firstFuture - 1)) / 2;
            futureRect = '<rect class="kpi-hourly-futurezone" x="' + fx.toFixed(2) + '" y="' + PAD_T +
                         '" width="' + (SVG_W - PAD_R - fx).toFixed(2) + '" height="' + INNER_H + '"/>';
        }
        var nowMarker = '';
        if (currentIdx >= 0) {
            var nx = g.xOf(currentIdx);
            nowMarker = '<line class="kpi-hourly-now" x1="' + nx.toFixed(2) + '" y1="' + (PAD_T - 4) +
                        '" x2="' + nx.toFixed(2) + '" y2="' + g.baseY + '"/>';
        }

        // Vertex dots for every recorded hour + a brighter halo on the latest one,
        // so the curve reads as real data points rather than a bare line.
        var dots = '', lastActive = -1;
        for (var d = 0; d < n; d++) { if (hours[d].active) lastActive = d; }
        for (var d2 = 0; d2 < n; d2++) {
            if (!hours[d2].active || d2 === lastActive) continue;
            dots += '<circle class="kpi-hourly-dot" cx="' + pts[d2].x.toFixed(2) +
                    '" cy="' + pts[d2].y.toFixed(2) + '" r="2.1"/>';
        }
        var peak = '';
        if (lastActive >= 0) {
            var px = pts[lastActive].x.toFixed(2), py = pts[lastActive].y.toFixed(2);
            peak = '<circle class="kpi-hourly-peak-halo" cx="' + px + '" cy="' + py + '" r="6.5" fill="' + ac.b + '"/>' +
                   '<circle class="kpi-hourly-peak" cx="' + px + '" cy="' + py + '" r="3.6" fill="' + ac.c + '" stroke="' + ac.a + '"/>';
        }

        return '' +
            '<svg class="kpi-hourly-svg" viewBox="0 0 ' + SVG_W + ' ' + SVG_H + '" role="img">' +
              '<defs>' +
                '<linearGradient id="kpiHourlyGrad" x1="0" y1="0" x2="0" y2="1">' +
                  '<stop offset="0%"  stop-color="' + ac.fill + '" stop-opacity="0.46"/>' +
                  '<stop offset="55%" stop-color="' + ac.fill + '" stop-opacity="0.14"/>' +
                  '<stop offset="100%" stop-color="' + ac.fill + '" stop-opacity="0"/>' +
                '</linearGradient>' +
                '<linearGradient id="kpiHourlyStroke" x1="0" y1="0" x2="1" y2="0">' +
                  '<stop offset="0%"   stop-color="' + ac.a + '"/>' +
                  '<stop offset="55%"  stop-color="' + ac.b + '"/>' +
                  '<stop offset="100%" stop-color="' + ac.c + '"/>' +
                '</linearGradient>' +
              '</defs>' +
              buildGrid(g) +
              futureRect +
              '<path class="kpi-hourly-glow" d="' + line + '" fill="none"/>' +
              '<path class="kpi-hourly-area" d="' + area + '" fill="url(#kpiHourlyGrad)"/>' +
              '<path class="kpi-hourly-line" d="' + line + '" fill="none"/>' +
              dots +
              peak +
              nowMarker +
              '<line class="kpi-hourly-guide" x1="0" y1="' + PAD_T + '" x2="0" y2="' + g.baseY + '" style="opacity:0"/>' +
              '<circle class="kpi-hourly-hoverdot" r="3.4" cx="0" cy="0" style="opacity:0"/>' +
            '</svg>';
    }

    function buildAxis(hours) {
        return '<div class="kpi-hourly-axis">' + hours.map(function (h) {
            var cls = 'kpi-hourly-h' + (h.active ? '' : ' is-future') + (h.current ? ' is-current' : '');
            return '<span class="' + cls + '">' + esc(h.label) + '</span>';
        }).join('') + '</div>';
    }

    function updateCard(data, hours) {
        var total = payloadKg(data, hours);
        setText('kpi-muszak-label', (data && data.name) || 'Műszak');
        setText('kpi-muszak-value', total > 0 ? fmtKg(total) : '–');
        setText('kpi-muszak-sub', (data && data.range) || '');
    }

    function paint(host, data, drawDelayMs) {
        var hours = normalizedHours(data && data.hours);
        setText('kpi-hourly-title', data ? (data.range ? (data.name + ' · ' + data.range) : data.name) : 'Műszak');
        updateCard(data, hours);
        if (!hours.length) { host.innerHTML = '<div class="kpi-hourly-empty">Nincs óránkénti adat</div>'; return; }
        host.innerHTML = buildSvg(hours) + '<div class="kpi-hourly-tip" style="opacity:0"></div>' + buildAxis(hours);
        attachValueObservers();
        animateDraw(host, drawDelayMs || 0);
        wireHover(host, hours);
        updateNavButtons();
    }

    function repaintActiveCardSoon() {
        setTimeout(function () {
            var data = activeData();
            updateCard(data, normalizedHours(data && data.hours));
        }, 80);
    }

    function animateDraw(host, delayMs) {
        var lineEl = host.querySelector('.kpi-hourly-line');
        var glowEl = host.querySelector('.kpi-hourly-glow');
        var areaEl = host.querySelector('.kpi-hourly-area');
        if (lineEl && typeof lineEl.getTotalLength === 'function') {
            try {
                var len = lineEl.getTotalLength();
                // Headroom + sane fallback: getTotalLength can under-measure a curved
                // path (or return 0 before layout). An exact dasharray then leaves the
                // final segment undrawn at offset 0 — the "line stops short of the last
                // point, only the dots show" bug. Padding guarantees the dash always
                // covers the whole stroke; we also snap to a clean solid line once the
                // draw-on completes so the resting state never depends on dash math.
                if (!len || !isFinite(len)) len = 4000;
                var dash = Math.ceil(len + 120);
                [lineEl, glowEl].forEach(function (el) {
                    if (!el) return;
                    el.classList.remove('is-drawn');
                    el.style.transition = 'none';
                    el.style.strokeDasharray = dash;
                    el.style.strokeDashoffset = dash;
                });
                if (areaEl) {
                    areaEl.classList.remove('is-shown');
                    areaEl.style.transition = 'none';
                    areaEl.style.opacity = '0';
                }
                void lineEl.getBoundingClientRect();
                if (openDrawTimer) {
                    clearTimeout(openDrawTimer);
                    openDrawTimer = null;
                }
                if (host.__kpiHourlySolidTimer) {
                    clearTimeout(host.__kpiHourlySolidTimer);
                    host.__kpiHourlySolidTimer = 0;
                }
                var start = function () {
                    requestAnimationFrame(function () {
                        requestAnimationFrame(function () {
                            [lineEl, glowEl].forEach(function (el) {
                                if (!el) return;
                                el.style.transition = '';
                                el.classList.add('is-drawn');
                                el.style.strokeDashoffset = '0';
                            });
                            if (areaEl) {
                                areaEl.style.transition = '';
                                areaEl.style.opacity = '';
                                areaEl.classList.add('is-shown');
                            }
                            // Once the 4s draw-on has finished, drop the dash entirely so
                            // the line is a guaranteed-solid stroke (no sub-pixel gap).
                            host.__kpiHourlySolidTimer = setTimeout(function () {
                                [lineEl, glowEl].forEach(function (el) {
                                    if (!el || !el.isConnected) return;
                                    el.style.strokeDasharray = 'none';
                                    el.style.strokeDashoffset = '0';
                                });
                            }, 4200);
                        });
                    });
                };
                if (delayMs > 0) {
                    openDrawTimer = setTimeout(start, delayMs);
                } else {
                    start();
                }
            } catch (e) { /* detached node — ignore */ }
        }
    }

    function pulseValue(el) {
        if (!el) return;
        el.classList.remove('kpi-value-changing');
        void el.offsetWidth;
        el.classList.add('kpi-value-changing');
    }

    function attachValueObservers() {
        document.querySelectorAll('.kpi-value, .kpi-hourly-title').forEach(function (el) {
            if (observedValues.has(el)) return;
            observedValues.add(el);
            var obs = new MutationObserver(function () { pulseValue(el); });
            obs.observe(el, { childList: true, characterData: true, subtree: true });
        });
    }

    function wireHover(host, hours) {
        var svg = host.querySelector('.kpi-hourly-svg');
        var guide = host.querySelector('.kpi-hourly-guide');
        var dot = host.querySelector('.kpi-hourly-hoverdot');
        var tip = host.querySelector('.kpi-hourly-tip');
        if (!svg || !guide || !dot || !tip) return;
        var g = geometry(hours);

        function show(idx) {
            var p = g.pts[idx];
            guide.setAttribute('x1', p.x.toFixed(2)); guide.setAttribute('x2', p.x.toFixed(2));
            guide.style.opacity = '1';
            dot.setAttribute('cx', p.x.toFixed(2)); dot.setAttribute('cy', p.y.toFixed(2));
            dot.style.opacity = '1';
            dot.classList.toggle('is-future', !p.h.active);
            tip.innerHTML = '<b>' + esc(p.h.label) + ':00</b>' +
                (p.h.active ? ('<span>' + esc(fmtKg(p.kg)) + '</span>') : '<span class="muted">még nincs adat</span>');
            var svgRect = svg.getBoundingClientRect(), hostRect = host.getBoundingClientRect();
            if (svgRect.width && svgRect.height) {
                tip.style.left = (svgRect.left - hostRect.left + (p.x / SVG_W) * svgRect.width) + 'px';
                tip.style.top  = (svgRect.top  - hostRect.top  + (p.y / SVG_H) * svgRect.height) + 'px';
            }
            tip.style.opacity = '1';
        }
        function hide() { guide.style.opacity = '0'; dot.style.opacity = '0'; tip.style.opacity = '0'; }
        function move(e) {
            var rect = svg.getBoundingClientRect();
            if (!rect.width) return;
            var vbX = (e.clientX - rect.left) / rect.width * SVG_W;
            var t = g.n === 1 ? 0 : (vbX - PAD_L) / INNER_W;
            var idx = Math.max(0, Math.min(g.n - 1, Math.round(t * (g.n - 1))));
            show(idx);
        }
        svg.addEventListener('mousemove', move);
        svg.addEventListener('mouseleave', hide);
    }

    function replayOpenDraw(delayMs) {
        var now = Date.now();
        if (replayOpenDraw._last && now - replayOpenDraw._last < 1200) return;
        replayOpenDraw._last = now;
        var host = document.getElementById('kpi-muszak-chart');
        if (!host || !host.querySelector('.kpi-hourly-line')) return;
        animateDraw(host, delayMs || 0);
    }

    function drawKey() {
        var data = activeData() || {};
        return state.mode + '|' + state.view + '|' + (data.name || '') + '|' + (data.range || '');
    }

    function replayOpenDrawOnce(delayMs) {
        var wrapper = document.getElementById('kpi-muszak-wrapper');
        if (!wrapper) return;
        var key = drawKey();
        if (wrapper.getAttribute('data-kpi-open-draw-key') === key) return;
        wrapper.setAttribute('data-kpi-open-draw-key', key);
        replayOpenDraw(delayMs);
    }

    function resetOpenDrawOnce() {
        var wrapper = document.getElementById('kpi-muszak-wrapper');
        if (wrapper && !wrapper.classList.contains('kpi-muszak-pinned')) {
            wrapper.removeAttribute('data-kpi-open-draw-key');
        }
    }

    function updateNavButtons() {
        var hasPrev = !!(state.prev && Array.isArray(state.prev.hours) && state.prev.hours.length);
        var showPrev = state.view === 'current' && hasPrev;   // ‹ steps back
        var showNext = state.view === 'prev';                 // › returns
        document.querySelectorAll('.kpi-shift-prev').forEach(function (b) { b.classList.toggle('is-hidden', !showPrev); });
        document.querySelectorAll('.kpi-shift-next').forEach(function (b) { b.classList.toggle('is-hidden', !showNext); });
    }

    function switchView(view, dir) {
        if (view === state.view) return;
        var host = document.getElementById('kpi-muszak-chart');
        if (!host) { state.view = view; return; }
        host.classList.add('is-redrawing');
        setTimeout(function () {
            state.view = view;
            paint(host, activeData(), 1000);
            requestAnimationFrame(function () {
                requestAnimationFrame(function () { host.classList.remove('is-redrawing'); });
            });
        }, 180);
    }

    // Delegated nav clicks for every .kpi-shift-nav (card arrows + panel arrows).
    // stopPropagation keeps a card-arrow click from also toggling the card's pin.
    document.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest('.kpi-shift-nav');
        if (!btn || btn.classList.contains('is-hidden')) return;
        e.preventDefault();
        e.stopPropagation();
        var dir = btn.getAttribute('data-shift-dir');
        if (dir === 'prev') switchView('prev', 'prev');
        else switchView('current', 'next');
    }, true);

    // Only one KPI panel open at a time: hovering one un-pins the other, so the
    // last-interacted card is the only one whose panel stays open (no overlap).
    document.addEventListener('mouseover', function (e) {
        if (!e.target || !e.target.closest) return;
        var muszakWrapper = e.target.closest('#kpi-muszak-wrapper');
        var muszakCard = e.target.closest('#kpi-muszak-card');
        if (muszakWrapper) {
            var b = document.getElementById('kpi-bec-wrapper');
            if (b) b.classList.remove('kpi-bec-pinned');
            if (muszakCard && (!e.relatedTarget || !muszakCard.contains(e.relatedTarget))) {
                replayOpenDrawOnce(1000);
            }
        } else if (e.target.closest('#kpi-bec-wrapper')) {
            var m = document.getElementById('kpi-muszak-wrapper');
            if (m) m.classList.remove('kpi-muszak-pinned');
        }
    });

    document.addEventListener('pointerenter', function (e) {
        if (e.target && e.target.id === 'kpi-muszak-card') {
            replayOpenDrawOnce(1000);
        }
    }, true);

    function copyFeedback(btn) {
        if (!btn) return;
        var old = btn.textContent;
        btn.textContent = '✓';
        btn.classList.add('is-copied');
        setTimeout(function () {
            btn.textContent = old || '⧉';
            btn.classList.remove('is-copied');
        }, 1200);
    }

    function copyText(text, btn) {
        text = String(text || '').trim();
        if (!text) return;
        var done = function () { copyFeedback(btn); };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done).catch(function () {});
        } else {
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            try { document.execCommand('copy'); done(); } catch (err) {}
            document.body.removeChild(ta);
        }
    }

    // Rich clipboard write: HTML table (for Excel/Outlook) + plain-text fallback.
    function copyRich(html, text, btn) {
        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], { type: 'text/html' }),
                'text/plain': new Blob([text], { type: 'text/plain' })
            })]).then(function () { copyFeedback(btn); }).catch(function () { copyText(text, btn); });
        } else {
            copyText(text, btn);
        }
    }

    // Title-case a Hungarian shift name: "nappali műszak" -> "Nappali Műszak".
    function titleCaseHu(s) {
        return String(s || '').toLowerCase().replace(/(^|\s)(\S)/g, function (_, sp, ch) {
            return sp + ch.toUpperCase();
        });
    }

    // Műszak copy: the CURRENTLY displayed switch (IN/OUT) + shift, as a plain
    // value line, e.g. "INBOUND Nappali Műszak: 84 848 kg".
    function buildShiftCopyText() {
        var data = activeData();
        if (!data) return '';
        var hours = normalizedHours(data.hours);
        var total = payloadKg(data, hours);
        var modeLabel = state.mode === 'outbound' ? 'OUTBOUND' : 'INBOUND';
        var name = titleCaseHu(data.name || 'Műszak');
        return modeLabel + ' ' + name + ': ' + fmtKg(total);
    }

    // Várható beérkező copy: a colour-coded table (coloured header + highlighted
    // total row) built from the JSON payload the server puts on data-copy-text.
    function copyBecTable(btn) {
        var raw = btn.getAttribute('data-copy-text') || btn.dataset.copyText || '';
        var payload;
        try { payload = JSON.parse(raw); } catch (e) { payload = null; }
        if (!payload || !payload.rows) { copyText(raw, btn); return; }

        var HEAD_BG = '#4f46e5', HEAD_FG = '#ffffff';   // colour 1 — header
        var TOTAL_BG = '#ede9fe', TOTAL_FG = '#312e81';  // colour 2 — total row
        var cols = ['Státusz', 'kg', 'ULD', 'PLT'];
        var alignOf = function (i) { return i === 0 ? 'left' : 'right'; };

        var html = '<table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;font-size:13px;">';
        html += '<tr>' + cols.map(function (c, i) {
            return '<th style="background:' + HEAD_BG + ';color:' + HEAD_FG + ';font-weight:800;'
                + 'border:1px solid #c7d2fe;padding:7px 12px;text-align:' + alignOf(i) + ';">' + esc(c) + '</th>';
        }).join('') + '</tr>';
        payload.rows.forEach(function (r) {
            var cells = [r.label, fmtKg(r.kg), fmtKg(r.uld), fmtKg(r.plt)];
            html += '<tr>' + cells.map(function (c, i) {
                return '<td style="border:1px solid #e5e7eb;padding:6px 12px;color:#111827;'
                    + 'text-align:' + alignOf(i) + ';' + (i === 0 ? 'font-weight:600;' : '') + '">' + esc(c) + '</td>';
            }).join('') + '</tr>';
        });
        var totals = ['Összesen', fmtKg(payload.total), fmtKg(payload.uld_total), fmtKg(payload.plt_total)];
        html += '<tr>' + totals.map(function (c, i) {
            return '<td style="background:' + TOTAL_BG + ';color:' + TOTAL_FG + ';font-weight:800;'
                + 'border:1px solid #c7d2fe;padding:7px 12px;text-align:' + alignOf(i) + ';">' + esc(c) + '</td>';
        }).join('') + '</tr></table>';

        var lines = [cols.join('\t')];
        payload.rows.forEach(function (r) {
            lines.push([r.label, fmtKg(r.kg), fmtKg(r.uld), fmtKg(r.plt)].join('\t'));
        });
        lines.push(totals.join('\t'));
        copyRich(html, lines.join('\r\n'), btn);
    }

    document.addEventListener('click', function (e) {
        var modeBtn = e.target.closest && e.target.closest('.kpi-mode-btn');
        if (modeBtn) {
            e.preventDefault();
            e.stopPropagation();
            var mode = modeBtn.id === 'kpi-mode-outbound' ? 'outbound' : 'inbound';
            state.mode = mode;
            syncModeUi();
            updateNavButtons();
            if (window.dash_clientside && typeof window.dash_clientside.set_props === 'function') {
                window.dash_clientside.set_props('kpi-muszak-mode-store', { data: mode });
            }
            return;
        }
        var copyBtn = e.target.closest && e.target.closest('.kpi-copy-btn');
        if (copyBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (copyBtn.id === 'kpi-muszak-copy') {
                copyText(buildShiftCopyText(), copyBtn);
            } else if (copyBtn.id === 'kpi-bec-copy') {
                copyBecTable(copyBtn);
            } else {
                copyText(copyBtn.getAttribute('data-copy-text') || copyBtn.dataset.copyText || '', copyBtn);
            }
        }
    }, true);

    document.addEventListener('pointerleave', function (e) {
        if (e.target && e.target.id === 'kpi-muszak-wrapper') {
            resetOpenDrawOnce();
        }
    }, true);

    document.addEventListener('click', function (e) {
        if (e.target && e.target.closest && e.target.closest('#kpi-muszak-card')) {
            setTimeout(function () { replayOpenDrawOnce(1000); }, 60);
        }
    }, true);

    window.__renderShiftHourlyChart = function (payload) {
        if (payload && (payload.current || payload.prev)) {
            state.mode = payload.mode === 'outbound' ? 'outbound' : 'inbound';
            state.current = payload.current || state.current;
            state.prev = payload.prev || state.prev;
        }
        syncModeUi();
        var host = document.getElementById('kpi-muszak-chart');
        if (!host) return;
        if (state.view === 'prev' && !(state.prev && state.prev.hours && state.prev.hours.length)) {
            state.view = 'current';
        }
        paint(host, activeData());
        repaintActiveCardSoon();
        attachValueObservers();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachValueObservers);
    } else {
        attachValueObservers();
    }
})();
