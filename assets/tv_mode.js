/**
 * tv_mode.js — Flow Manager TV mód v4
 * Dark glass, FM-stílushoz illeszkedő, nagy rakodáskártyák és műszakriport.
 */

(function () {
  "use strict";

  /* ──────────────────────────────────────────────────────────────
     NAPSZAK GRADIENT
  ────────────────────────────────────────────────────────────── */

  const _DAYPART = [
    { from:  0, to:  5, b1:"255,138,61",  b2:"167,139,250", b3:"74,158,255",  op:[0.28,0.22,0.18] },
    { from:  5, to:  7, b1:"255,160,80",  b2:"255,200,100", b3:"167,139,250", op:[0.32,0.26,0.20] },
    { from:  7, to: 11, b1:"255,138,61",  b2:"74,158,255",  b3:"167,139,250", op:[0.30,0.24,0.20] },
    { from: 11, to: 15, b1:"255,200,60",  b2:"74,158,255",  b3:"100,220,180", op:[0.28,0.22,0.20] },
    { from: 15, to: 18, b1:"255,138,61",  b2:"255,100,100", b3:"167,139,250", op:[0.32,0.22,0.20] },
    { from: 18, to: 20, b1:"255,80,80",   b2:"200,100,255", b3:"255,138,61",  op:[0.30,0.24,0.22] },
    { from: 20, to: 22, b1:"167,139,250", b2:"74,100,255",  b3:"255,138,61",  op:[0.28,0.22,0.18] },
    { from: 22, to: 24, b1:"74,100,255",  b2:"167,139,250", b3:"255,138,61",  op:[0.25,0.20,0.16] },
  ];

  function _getDaypart() {
    const h = new Date().getHours();
    return _DAYPART.find(d => h >= d.from && h < d.to) || _DAYPART[0];
  }

  function _applyGradient() {
    const dp = _getDaypart();
    const r  = document.documentElement;
    r.style.setProperty("--tv-blob1",    dp.b1);
    r.style.setProperty("--tv-blob2",    dp.b2);
    r.style.setProperty("--tv-blob3",    dp.b3);
    r.style.setProperty("--tv-blob1-op", dp.op[0]);
    r.style.setProperty("--tv-blob2-op", dp.op[1]);
    r.style.setProperty("--tv-blob3-op", dp.op[2]);
  }

  /* ──────────────────────────────────────────────────────────────     COUNT-UP (rAF easing + flash)
  ────────────────────────────────────────────────────────────── */

  const _rafMap = new Map();
  let _cuKey = 0;

  function _countUp(el, from, to, ms, fmt) {
    if (!el) return;
    if (!el._cuKey) el._cuKey = ++_cuKey;
    const key = el._cuKey;
    if (_rafMap.has(key)) cancelAnimationFrame(_rafMap.get(key));
    const start = performance.now();
    const diff  = to - from;
    const targetRgb = _countTargetRgb(el);
    const startRgb = [255, 255, 255];
    if (targetRgb) {
      el.classList.add("tv-counting-color");
      el.style.color = "rgb(255,255,255)";
    }
    function step(now) {
      const t    = Math.min((now - start) / ms, 1);
      const ease = t < 0.5 ? 2*t*t : -1+(4-2*t)*t;
      el.textContent = fmt ? fmt(from + diff * ease) : _fmtNum(from + diff * ease);
      if (targetRgb) {
        const c = _mixRgb(startRgb, targetRgb, ease);
        el.style.color = `rgb(${c[0]},${c[1]},${c[2]})`;
      }
      if (t < 1) {
        _rafMap.set(key, requestAnimationFrame(step));
      } else {
        el.textContent = fmt ? fmt(to) : _fmtNum(to);
        _rafMap.delete(key);
        if (targetRgb) {
          el.style.color = `rgb(${targetRgb[0]},${targetRgb[1]},${targetRgb[2]})`;
          el.classList.remove("tv-counting-color");
        } else if (Math.abs(diff) > 0.5) {
          el.classList.add("tv-val-flash");
          setTimeout(() => el.classList.remove("tv-val-flash"), 800);
        }
      }
    }
    _rafMap.set(key, requestAnimationFrame(step));
  }

  function _fmtNum(v) { return Math.round(v).toLocaleString("hu-HU"); }

  function _esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function _hexRgb(hex) {
    const m = String(hex || "").trim().replace("#", "");
    if (m.length !== 6) return null;
    const n = parseInt(m, 16);
    return Number.isFinite(n) ? [(n >> 16) & 255, (n >> 8) & 255, n & 255] : null;
  }

  function _countTargetRgb(el) {
    const map = {
      "tv-hdr-in-kg": "#ff8a3d",
      "tv-hdr-out-kg": "#a78bfa",
      "tv-hdr-issued": "#4ade80",
      "tv-hdr-waiting": "#60a5fa",
      "tv-hdr-wh-active": "#22d3ee",
      "tv-hdr-wh-kg": "#22d3ee",
      "tv-prev-kg": "#ff8a3d",
    };
    return el && el.closest && el.closest(".tv-header") ? _hexRgb(map[el.id]) : null;
  }

  function _mixRgb(a, b, t) {
    const f = Math.max(0, Math.min(1, t || 0));
    return [
      Math.round(a[0] + (b[0] - a[0]) * f),
      Math.round(a[1] + (b[1] - a[1]) * f),
      Math.round(a[2] + (b[2] - a[2]) * f),
    ];
  }

  function _setNum(elId, newVal) {
    const el = document.getElementById(elId);
    if (!el) return;
    newVal = Number(newVal) || 0;
    const prev = parseFloat(el.dataset.prev || "0");
    if (String(el.dataset.prev || "") === String(newVal) && el.textContent) {
      el.textContent = _fmtNum(newVal);
      return;
    }
    _countUp(el, prev, newVal, 900);
    el.dataset.prev = newVal;
  }

  function _setText(elId, val) {
    const el = document.getElementById(elId);
    if (el && el.textContent !== String(val)) el.textContent = val;
  }

  function _setHtml(elId, html) {
    const el = document.getElementById(elId);
    if (el && el.innerHTML !== html) el.innerHTML = html;
  }

  function _parseTvNumberText(text) {
    const raw = String(text || "").replace(/\s/g, "").replace(/[^\d,.-]/g, "").replace(",", ".");
    const n = parseFloat(raw);
    return Number.isFinite(n) ? n : null;
  }

  function _animateTextNumber(el, delay, duration, fmt) {
    if (!el) return;
    const finalText = (el.dataset.tvFinal || el.textContent || "").trim();
    const target = _parseTvNumberText(finalText);
    if (target === null) return;
    if (_reduceMotion()) {
      el.textContent = finalText;
      return;
    }
    if (el._tvNumTimer) clearTimeout(el._tvNumTimer);
    if (el._tvNumRaf) cancelAnimationFrame(el._tvNumRaf);
    el.dataset.tvFinal = finalText;
    el.textContent = fmt ? fmt(0) : _fmtNum(0);
    el._tvNumTimer = setTimeout(() => {
      const t0 = performance.now();
      const d = duration || 900;
      const render = typeof fmt === "function" ? fmt : _fmtNum;
      const step = now => {
        // Math.max 0: az első rAF-hívás timestampje (now) ritkán megelőzheti a
        // setTimeout-ban mért t0-t (frame-ütemezési jitter) → negatív t nélküle
        // negatív e-t (és egy pillanatra negatív számot) adna induláskor.
        const t = Math.max(0, Math.min((now - t0) / d, 1));
        const e = 1 - Math.pow(1 - t, 4);
        el.textContent = t >= 1 ? finalText : render(target * e);
        if (t < 1) el._tvNumRaf = requestAnimationFrame(step);
        else {
          el._tvNumRaf = null;
          el._tvNumTimer = null;
        }
      };
      el._tvNumRaf = requestAnimationFrame(step);
    }, delay || 0);
  }

  function _typeText(el, delay, speed) {
    if (!el) return;
    const finalText = (el.dataset.tvTypeText || el.textContent || "").trim();
    if (!finalText || _reduceMotion()) {
      el.textContent = finalText;
      return;
    }
    if (el._tvTypeTimer) clearTimeout(el._tvTypeTimer);
    el.dataset.tvTypeText = finalText;
    el.textContent = "";
    el.classList.add("tv-typing");
    const chars = Array.from(finalText);
    let i = 0;
    const tick = () => {
      el.textContent = chars.slice(0, i).join("");
      i++;
      if (i <= chars.length) {
        el._tvTypeTimer = setTimeout(tick, speed || 18);
      } else {
        el.classList.remove("tv-typing");
        el._tvTypeTimer = null;
      }
    };
    el._tvTypeTimer = setTimeout(tick, delay || 0);
  }

  function _setStagger(root, selector, base) {
    const items = root ? Array.from(root.querySelectorAll(selector)) : [];
    items.forEach((el, i) => el.style.setProperty("--tv-i", String(i + (base || 0))));
    return items;
  }

  /* ──────────────────────────────────────────────────────────────
     REFRESH COUNTDOWN (10 perces arc)
  ────────────────────────────────────────────────────────────── */

  const _REFRESH_MS = 10 * 60 * 1000;
  let _lastRefreshTime = Date.now();

  function _updateRefreshArc() {
    const arc    = document.getElementById("tv-refresh-arc");
    const minEl  = document.getElementById("tv-refresh-min");
    if (!arc) return;

    const elapsed  = Date.now() - _lastRefreshTime;
    const fraction = Math.min(elapsed / _REFRESH_MS, 1);
    const r        = 18;
    const circ     = 2 * Math.PI * r;
    const dash     = circ * (1 - fraction);

    arc.style.strokeDasharray  = `${circ.toFixed(2)}`;
    arc.style.strokeDashoffset = `${(circ - circ * fraction).toFixed(2)}`;

    const remSec  = Math.max(0, Math.ceil((_REFRESH_MS - elapsed) / 1000));
    const remMin  = Math.ceil(remSec / 60);
    if (minEl) minEl.textContent = remSec > 60 ? `${remMin}` : `${remSec}`;
  }

  /* ──────────────────────────────────────────────────────────────
     TRUCK PAGINATION
  ────────────────────────────────────────────────────────────── */

  const TRUCKS_PER_PAGE = 12;
  let _truckPage  = 0;
  let _truckPages = 1;
  let _truckData  = [];
  let _truckSig   = "";
  let _showTestCard = false;
  let _showTestReport = false;

  // Kimaxolt teszt kartyak - T gombbal kapcsolhato, a fo allapotokat egyben mutatja.
  const _TEST_TRUCKS = [
    {
      plate: "TESZT-READY", lmps: ["AT HU PACK", "TEMU RO POST"], glabs_ids: ["HG99001-TEST", "HG99002-TEST"], forwarded: true,
      ready: 8, total: 9, active: 9, pending: 1,
      driver_here: true, driver_checkin: "08:00",
      ramp: "16", loader: "K. Zoli",
      resting: false, rest_until: "", is_closed: false,
      statuses: [
        { label: "Kiadható", count: 8 },
        { label: "Mi történik", count: 1 },
      ],
      units: [
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Mi történik", ready: false },
      ],
    },
    {
      plate: "TESZT-MIX", lmps: ["MEEST MD PACK", "4PX EURO"], glabs_ids: ["HG99003-TEST"], forwarded: false,
      ready: 3, total: 8, active: 8, pending: 5,
      driver_here: false, driver_checkin: "",
      resting: false, rest_until: "", is_closed: false,
      statuses: [
        { label: "Kiadható", count: 3 },
        { label: "Felvéve", count: 2 },
        { label: "Értesítő", count: 1 },
        { label: "Megérkezett", count: 1 },
        { label: "Szemlés", count: 1 },
      ],
      units: [
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Felvéve", ready: false },
        { status: "Felvéve", ready: false }, { status: "Értesítő", ready: false },
        { status: "Megérkezett", ready: false }, { status: "Szemlés", ready: false },
      ],
    },
    {
      plate: "TESZT-REST", lmps: ["TEMU RO PACK"], glabs_ids: ["HG99004-TEST"], forwarded: true,
      ready: 5, total: 7, active: 7, pending: 2,
      driver_here: false, driver_checkin: "09:35",
      resting: true, rest_until: "11:45", is_closed: false,
      statuses: [
        { label: "Kiadható", count: 5 },
        { label: "Vámkez alatt", count: 2 },
      ],
      units: [
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Vámkez alatt", ready: false },
        { status: "Vámkez alatt", ready: false },
      ],
    },
    {
      plate: "TESZT-CLOSED", lmps: ["AT HU PACK"], glabs_ids: ["HG99005-TEST"], forwarded: false,
      ready: 6, total: 6, active: 0, pending: 0,
      driver_here: true, driver_checkin: "07:20",
      resting: false, rest_until: "", is_closed: true,
      statuses: [
        { label: "Kiadható", count: 6 },
      ],
      units: [
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
        { status: "Kiadható", ready: true }, { status: "Kiadható", ready: true },
      ],
    },
  ];

  const _TEST_REPORT = {
    shift_name: "Teszt nappali m\u0171szak",
    shift_range: "08:00-20:00",
    elapsed_h: 6.8,
    warmup: false,
    fixed_loadings: [
      { plate: "TESZT-READY", glabs_id: "HG99001-TEST", ready: 8, total: 9, ramp: "16", loader: "K. Zoli", reason: "fél felett + közel", driver_here: true },
      { plate: "TESZT-MIX", glabs_id: "HG99003-TEST", ready: 6, total: 6, ramp: "20", loader: "Gazsó", reason: "kész, sofőr nélkül", driver_here: false },
      { plate: "TESZT-RAMP", glabs_id: "HG99005-TEST", ready: 5, total: 7, ramp: "18", loader: "N. Adam", reason: "rampa elokeszitve", driver_here: true },
      { plate: "TESZT-NEXT", glabs_id: "HG99007-TEST", ready: 4, total: 8, ramp: "22", loader: "B. Aron", reason: "kovetkezo hullam", driver_here: false },
      { plate: "TESZT-SOON", glabs_id: "HG99009-TEST", ready: 3, total: 5, ramp: "14", loader: "M. Peter", reason: "sofor uton", driver_here: false },
      { plate: "TESZT-LAST", glabs_id: "HG99011-TEST", ready: 2, total: 4, ramp: "11", loader: "T. Mate", reason: "zaras elott", driver_here: true },
    ],
    out: {
      kg: 286420,
      colli: 9148,
      pallets: 316,
      parcels: 12890,
      trucks: 23,
      loadings: 31,
      rows: 186,
      loading_time_min: 34,
      loading_time_count: 24,
      kg_per_h: 42433,
      colli_per_h: 1355,
      pallets_per_h: 46.8,
      parcels_per_h: 1910,
      loadings_per_h: 4.6,
      ref_kg_per_h: 39500,
      ref_kg_kind: "big_avg",
      ref_loadings_per_h: 3.9,
      ref_loadings_kind: "big_avg",
      avg_shift_count: 18,
      prev_kg: 474000,
      hourly: [
        { label: "08", kg: 18200, active: true },
        { label: "09", kg: 26650, active: true },
        { label: "10", kg: 35100, active: true },
        { label: "11", kg: 42180, active: true },
        { label: "12", kg: 38440, active: true },
        { label: "13", kg: 48600, active: true, current: true },
        { label: "14", kg: 0, active: false },
        { label: "15", kg: 0, active: false },
        { label: "16", kg: 0, active: false },
        { label: "17", kg: 0, active: false },
        { label: "18", kg: 0, active: false },
        { label: "19", kg: 0, active: false },
      ],
      active_trucks: 4,
      issued_trucks: 23,
      waiting_trucks: 3,
      driver_here_trucks: 2,
    },
    inb: {
      kg: 163800,
      count: 428,
      kg_per_h: 24267,
      bec_total_kg: 418500,
      bec_count: 972,
    },
  };

  function _cloneJson(obj) {
    return obj ? JSON.parse(JSON.stringify(obj)) : obj;
  }

  function _getEffReport(rep) {
    return _showTestReport ? _cloneJson(_TEST_REPORT) : rep;
  }

  function _getEffData() { return _showTestCard ? [..._TEST_TRUCKS, ..._truckData] : _truckData; }
  function _getEffPages() { return Math.max(1, Math.ceil(_getEffData().length / TRUCKS_PER_PAGE)); }

  function _truckTier(t) {
    if (t.resting)   return "tv-tier-rest";
    if (!t.total)    return "tv-tier-dim";
    const pct = t.ready / t.total;
    if (pct >= 0.75) return "tv-tier-green";
    if (pct >= 0.50) return "tv-tier-blue";
    if (pct >= 0.25) return "tv-tier-amber";
    return "tv-tier-dim";
  }

  function _statusLabel(label) {
    const text = String(label || "").replace(/\s+/g, " ").trim();
    const key = text.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    if (key === "mitortenik") return "Mi történik";
    return text || "Státusz nélkül";
  }

  function _driverTruckSvgInner() {
    return `<svg viewBox="-6 -4 140 68" aria-hidden="true" focusable="false">` +
        `<g class="tv-truck-shadow"><ellipse cx="64" cy="51" rx="53" ry="4.4"/></g>` +
        `<path class="tv-truck-cargo" d="M9 13.5c0-3.2 2.6-5.8 5.8-5.8h68.7c3.2 0 5.8 2.6 5.8 5.8v25.8H9V13.5z"/>` +
        `<path class="tv-truck-cargo-top" d="M14 8h69.4c2.8 0 5.1 2.1 5.7 4.8H9.6C10.2 10.1 11.9 8 14 8z"/>` +
        `<path class="tv-truck-rib" d="M22 9v29M36 9v29M50 9v29M64 9v29M78 9v29"/>` +
        `<path class="tv-truck-landing" d="M82 39v8M87 39v8"/>` +
        `<path class="tv-truck-coupler" d="M88.5 39.2h11.5l-3.8 4.8h-7.7z"/>` +
        `<path class="tv-truck-cab" d="M96 24.5h13.4c2.9 0 5.7 1.5 7.2 4l6.6 10.8c.8 1.3 1.2 2.8 1.2 4.3v2.2H96V24.5z"/>` +
        `<path class="tv-truck-hood" d="M111.5 34.8h10.6c1.3 0 2.3 1 2.3 2.3v8.7h-19.1v-4.2c0-3.8 2.5-6.8 6.2-6.8z"/>` +
        `<path class="tv-truck-window" d="M101.5 27.8h7.3c1.2 0 2.2.6 2.9 1.6l3.5 5.8h-13.7v-7.4z"/>` +
        `<path class="tv-truck-grille" d="M119.4 38.3h4.8M119.4 41.7h4.8"/>` +
        `<path class="tv-truck-bumper" d="M7.5 45.8h117.5"/>` +
        `<circle class="tv-truck-wheel" cx="26" cy="45.6" r="7.3"/>` +
        `<circle class="tv-truck-wheel" cx="79" cy="45.6" r="7.3"/>` +
        `<circle class="tv-truck-wheel" cx="101.5" cy="45.6" r="7.3"/>` +
        `<circle class="tv-truck-wheel" cx="116" cy="45.6" r="7.3"/>` +
        `<circle class="tv-truck-hub" cx="26" cy="45.6" r="3.0"/>` +
        `<circle class="tv-truck-hub" cx="79" cy="45.6" r="3.0"/>` +
        `<circle class="tv-truck-hub" cx="101.5" cy="45.6" r="3.0"/>` +
        `<circle class="tv-truck-hub" cx="116" cy="45.6" r="3.0"/>` +
        `<path class="tv-truck-light" d="M122.6 38.6h3.6"/>` +
        `<path class="tv-truck-marker" d="M15 16h6M75 16h6M99.5 38h3"/>` +
      `</svg>`;
  }

  function _loadInfoIcon(kind) {
    if (kind === "ramp") {
      return `<svg class="tv-load-ico" viewBox="0 0 32 32" aria-hidden="true" focusable="false">` +
        `<path d="M5 22h22" class="tv-load-ico-line"/>` +
        `<path d="M8 21V11c0-1.2 1-2.2 2.2-2.2h11.6c1.2 0 2.2 1 2.2 2.2v10" class="tv-load-ico-line"/>` +
        `<path d="M11 21v-5.6c0-.8.6-1.4 1.4-1.4h7.2c.8 0 1.4.6 1.4 1.4V21" class="tv-load-ico-fill"/>` +
        `<path d="M12 12h8M14 16h4" class="tv-load-ico-detail"/>` +
      `</svg>`;
    }
    return `<svg class="tv-load-ico" viewBox="0 0 32 32" aria-hidden="true" focusable="false">` +
      `<circle cx="16" cy="10.4" r="4.2" class="tv-load-ico-fill"/>` +
      `<path d="M7.4 25.2c1.2-5 4.2-7.5 8.6-7.5s7.4 2.5 8.6 7.5" class="tv-load-ico-line"/>` +
      `<path d="M11.8 21.5h8.4" class="tv-load-ico-detail"/>` +
    `</svg>`;
  }

  function _loadInfoTile(kind, value) {
    const text = String(value || "").trim();
    if (!text) return "";
    const label = kind === "ramp" ? "RÁMPA" : "MV";
    return `<div class="tv-load-card tv-load-card-${kind}" title="${label}: ${_esc(text)}">` +
      _loadInfoIcon(kind) +
      `<div class="tv-load-card-text">` +
        `<span>${label}</span>` +
        `<b>${_esc(text)}</b>` +
      `</div>` +
    `</div>`;
  }

  // Sofőr-állapot chip — a rámpa/MV chipekkel EGYSÉGES megjelenésű, feliratozott
  // kártya (nem csupasz, középen lebegő ikon). A részletes kamion-illusztráció az
  // ikon-slotban marad, az állapot pedig szövegként is kiolvasható TV-távolságból.
  function _driverTile(state, sub) {
    const words = { here: "Bejelentkezve", rest: "Pihenőn", away: "Nincs itt" };
    const titles = {
      here: "Sofőr bejelentkezve" + (sub ? ` · ${sub}` : ""),
      rest: "Sofőr pihenőn" + (sub ? ` · ${sub}` : ""),
      away: "Sofőr még nincs bejelentkezve",
    };
    // Kompakt, kétsoros chip (SOFŐR + állapotszó) — keskeny marad, így a
    // SOFŐR+RÁMPA+MV chipek egy sorban elférnek a rövidebb kártyákon is. A pontos
    // idő (bejelentkezés / pihenő vége) a tooltipben marad.
    return `<div class="tv-load-card tv-load-card-driver tv-drv-${state}" title="${_esc(titles[state])}" role="img" aria-label="${_esc(titles[state])}">` +
      `<span class="tv-drv-truck">${_driverTruckSvgInner()}</span>` +
      `<div class="tv-load-card-text">` +
        `<span>SOFŐR</span>` +
        `<b>${words[state]}</b>` +
      `</div>` +
    `</div>`;
  }

  function _buildTruckCard(t) {
    const tier = _truckTier(t);
    const total = Math.max(0, t.total || 0);
    const ready = Math.max(0, t.ready || 0);
    const unitCount = Math.max(total, (Array.isArray(t.units) ? t.units.length : 0), 1);
    const active = Math.max(0, t.active || 0);
    const pending = Math.max(0, active - ready);
    // "Kiadásra vár" CSAK akkor helytálló, ha MINDEN aktív tétel kiadható
    // (ready >= total). Részben kész rakodás (pl. 4/5, egy tétel még szállítás
    // alatt) NEM vár kiadásra — az "Folyamatban". Így a szöveg sosem mond mást,
    // mint amit az X/Y számláló mutat.
    const allReady = total > 0 && ready >= total;
    const status = t.is_closed
      ? "Kiadva"
      : t.resting
        ? "Pihenőn"
        : allReady
          ? "Kiadásra vár"
          : (ready > 0 || pending > 0)
            ? "Folyamatban"
            : "Előkészítés";

    // Sofőr állapot: feliratozott chip (részletes kamion ikon + állapotszöveg),
    // a rámpa/MV chipekkel egységes. Színkód: zöld = bejelentkezve, narancs =
    // pihenő, szürke = nincs még bejelentkezve.
    let driverTile;
    if (t.resting) {
      driverTile = _driverTile("rest", t.rest_until ? `${t.rest_until}-ig` : "");
    } else if (t.driver_here) {
      driverTile = _driverTile("here", t.driver_checkin || "");
    } else {
      driverTile = _driverTile("away", "");
    }

    // Hierarchia: rendszám > GLABS id (kiemelt sor) > LMP (chipek)
    const glabsLine = (t.glabs_ids || []).join(" · ") || "&mdash;";
    const lmpList = t.lmps || [];
    const lmpChips = lmpList.length
      ? `<div class="tv-truck-glabs tv-ticker-wrap"><div class="tv-ticker-inner">` +
        lmpList.map(l => `<span class="tv-glabs-chip">${l}</span>`).join("") +
        `</div></div>`
      : "";
    const loadTags = `<div class="tv-load-tags">` +
      driverTile +
      _loadInfoTile("ramp", t.ramp) +
      _loadInfoTile("leader", t.loader) +
      `</div>`;

    // Kiadhatóság-számláló szín: minden kiadható = zöld, kevesebb = sárga, nagyon kevés = piros.
    const ratio = total > 0 ? ready / total : 0;
    const pctCls = total === 0 ? "" : (ready >= total ? "tv-pct-green" : ratio >= 0.5 ? "tv-pct-yellow" : "tv-pct-red");

    // Tov. eng. badge — a rendszám/GLABS/LMP blokk és az X/Y számláló KÖZÉ kerül
    // (a fejléc-sor üres középső felületére).
    const fwdBadge = t.forwarded
      ? `<span class="tv-fwd-badge" title="Tovább engedett tételek a rakodásban"><svg class="tv-fwd-ico" viewBox="0 0 14 14" aria-hidden="true"><path d="M1.5 2.5L6 7l-4.5 4.5M6.5 2.5L11 7l-4.5 4.5"/></svg>Tov. eng.</span>`
      : "";

    const pills = [];
    (t.statuses || []).slice(0, 4).forEach(s => {
      const rawLabel = s.label || "Státusz nélkül";
      const label = _statusLabel(rawLabel);
      const key = _statusClass(rawLabel);
      pills.push(`<span class="tv-stat-pill ${key}">${_esc(s.count)} ${_esc(label)}</span>`);
    });
    if (!pills.length)
      pills.push(`<span class="tv-stat-pill tv-pill-empty">Státusz nélkül</span>`);

    // Kiadhatósági HERO: az X/Y a kártya legnagyobb, középre igazított eleme
    // (ez a legfontosabb operatív szám), alatta vastag készültségi sáv. A hero
    // flex-szel kitölti a fejléc és a chip-sáv közti teret -> nincs holt középső
    // rész, és a chip-sáv + pillek MINDEN kártyán azonos alapvonalon állnak.
    const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(ready / total * 100))) : 0;
    return `
    <div class="tv-truck-card ${tier}${t.is_closed ? " tv-truck-closed" : ""}">
      <div class="tv-truck-top">
        <div class="tv-truck-id">
          <div class="tv-truck-plate">${t.plate}</div>
          <div class="tv-truck-lmp">${glabsLine}</div>
          ${lmpChips}
        </div>
        ${fwdBadge}
      </div>
      <div class="tv-truck-hero ${pctCls}">
        <div class="tv-truck-hero-head">
          <span class="tv-truck-hero-big">${ready}<i>/${total}</i></span>
          <span class="tv-truck-hero-status">${status}</span>
        </div>
        <div class="tv-truck-hero-bar"><div class="tv-truck-hero-fill" style="--fill:${pct}%; width:${pct}%"></div></div>
      </div>
      <div class="tv-truck-meta">
        ${loadTags}
      </div>
      <div class="tv-truck-pills tv-ticker-wrap"><div class="tv-ticker-inner">${pills.join("")}</div></div>
    </div>`;
  }

  function _statusClass(label) {
    const s = String(label || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    if (s.includes("felvéve") || s.includes("felveve")) return "tv-pill-status-felveve";
    if (s.includes("értesítő") || s.includes("ertesito")) return "tv-pill-status-ertesito";
    if (s.includes("megérkezett") || s.includes("megerkezett")) return "tv-pill-status-megerkezett";
    if (s.includes("szeml")) return "tv-pill-status-szemles";
    if (s.includes("mi t") || s.includes("mitort")) return "tv-pill-status-mitortenik";
    if (s.includes("vám") || s.includes("vam")) return "tv-pill-status-vamkez";
    if (s.includes("kiadhat") || s.includes("kiadás")) return "tv-pill-ready";
    return "tv-pill-active";
  }

  function _renderPageDots() {
    const dotsEl = document.getElementById("tv-page-dots");
    if (!dotsEl) return;
    const pages = _getEffPages();
    if (pages <= 1) { dotsEl.innerHTML = ""; return; }
    dotsEl.innerHTML = Array.from({ length: pages }, (_, i) =>
      `<span class="tv-dot${i === _truckPage ? " tv-dot-filled" : ""}" data-page="${i}"></span>`
    ).join("");
    dotsEl.querySelectorAll(".tv-dot[data-page]").forEach(dot => {
      dot.addEventListener("click", () => {
        const p = parseInt(dot.dataset.page, 10);
        if (p !== _truckPage) { _truckPage = p; _renderTruckPage(true); }
      });
    });
  }

  function _renderTruckPage(animate) {
    const grid = document.getElementById("tv-trucks-grid");
    if (!grid) return;

    const eff = _getEffData();
    if (!eff.length) {
      grid.innerHTML = '<div class="tv-no-trucks">Nincs aktív rakodás</div>';
      return;
    }

    const effPages = _getEffPages();
    if (_truckPage >= effPages) _truckPage = 0;

    const start = _truckPage * TRUCKS_PER_PAGE;
    const slice = eff.slice(start, start + TRUCKS_PER_PAGE);
    const html  = slice.map(_buildTruckCard).join("");

    // A sorok számát a TÉNYLEGES kártyaszám adja (4 oszlop), de a sormagasság fix,
    // így 8 kártya ugyanakkora marad, mint 12 kártya. Inline `important`, mert
    // a CSS-ben !important van.
    const rowCount = Math.max(1, Math.ceil(slice.length / 4));
    const cs = window.getComputedStyle ? getComputedStyle(grid) : null;
    const gap = cs ? (parseFloat(cs.rowGap || cs.gap || "0") || 0) : 0;
    const available = grid.clientHeight || 0;
    const fallbackRow = rowCount >= 3 ? 244 : 268;
    const fitRow = available > 0
      ? Math.floor((available - gap * (rowCount - 1) - 8) / rowCount)
      : fallbackRow;
    const rowHeight = Math.max(218, Math.min(rowCount >= 3 ? 252 : 276, fitRow || fallbackRow));
    grid.classList.toggle("tv-grid-three-rows", rowCount >= 3);
    grid.style.setProperty("grid-template-rows", `repeat(${rowCount}, minmax(0, ${rowHeight}px))`, "important");
    grid.style.setProperty("align-content", rowCount >= 3 ? "start" : "center", "important");

    if (animate) {
      grid.classList.add("tv-grid-exit");
      setTimeout(() => {
        grid.innerHTML = html;
        grid.classList.remove("tv-grid-exit");
        void grid.offsetWidth;
        grid.classList.add("tv-grid-enter");
        setTimeout(() => grid.classList.remove("tv-grid-enter"), 500);
        setTimeout(_initTickers, 80);
      }, 270);
    } else {
      grid.innerHTML = html;
      setTimeout(_initTickers, 80);
    }
    _renderPageDots();
  }

  function _advanceTruckPage() {
    const pages = _getEffPages();
    if (pages <= 1) return;
    _truckPage = (_truckPage + 1) % pages;
    _renderTruckPage(true);
  }

  // Egy oldal 5–10 perc között látszik (ciklusonként újrasorsolt véletlen idő).
  function _scheduleTruckPage() {
    const ms = (5 + Math.random() * 5) * 60 * 1000;
    _pagTimer = setTimeout(() => {
      if (_truckPages > 1) _advanceTruckPage();
      _scheduleTruckPage();
    }, ms);
  }

  function _updateTrucks(trucks) {
    if (!Array.isArray(trucks)) return false;
    const active = trucks.filter(t => !t.is_closed);
    _truckData  = active;
    _truckPages = Math.max(1, Math.ceil(_truckData.length / TRUCKS_PER_PAGE));
    if (_truckPage >= _truckPages) _truckPage = 0;

    const sig = _truckData.map(t =>
      `${t.plate}:${t.ready}:${t.total}:${t.resting?1:0}:${t.driver_here?1:0}:${t.driver_checkin||""}:${t.rest_until||""}:${t.ramp||""}:${t.loader||""}:${t.forwarded?1:0}:${JSON.stringify(t.lmps||[])}:${JSON.stringify(t.glabs_ids||[])}:${JSON.stringify(t.statuses||[])}:${JSON.stringify(t.units||[])}`
    ).join("|");

    if (_truckSig !== sig) {
      _truckSig = sig;
      _renderTruckPage(false);
      _setText("tv-out-summary", "");
      return true;
    }

    _setText("tv-out-summary", "");
    return false;
  }

  /* ──────────────────────────────────────────────────────────────
     AUTO-TICKER (overflow pill/LMP sorok oda-vissza csúsztatása)
  ────────────────────────────────────────────────────────────── */

  // A vízszintes ticker ciklusa 9s (lásd tvTicker keyframe). A staggert NEGATÍV
  // animation-delay adja: minden slider más fázispontról indul → SOHA nem mozognak
  // egyszerre, és nem fixen lépcsőzve, hanem szétszórva a teljes cikluson.
  const _TICK_CYCLE = 9;
  function _tickerize(el, idx) {
    if (!el) return;
    const inner = el.querySelector(".tv-ticker-inner");
    if (!inner) return;
    const overflow = inner.scrollWidth - el.offsetWidth;
    if (overflow <= 4) {
      el.classList.remove("tv-ticking");
      inner.style.removeProperty("--tk-dist");
      inner.style.animationDelay = "";
      return;
    }
    inner.style.setProperty("--tk-dist", `-${overflow}px`);
    // 1.7s-es prím-szerű lépés szétszórja a fázisokat a 9s cikluson belül.
    const phase = ((typeof idx === "number" ? idx : 0) * 1.7) % _TICK_CYCLE;
    inner.style.animationDelay = `-${phase.toFixed(2)}s`;
    el.classList.add("tv-ticking");
  }

  function _initTickers() {
    document.querySelectorAll(".tv-ticker-wrap").forEach((el, i) => _tickerize(el, i));
  }

  const _TV_OPEN_DELAY = 500;
  const _TV_REFRESH_OPEN_MIN_MS = 28_000;
  let _tvHasOpsPayload = false;
  let _lastOpsPayload = null;
  let _tvOpeningGate = {
    ops: { initialDone: false, lastRefreshAt: 0 },
    report: { initialDone: false, lastRefreshAt: 0 },
  };

  function _openingClass(reason) {
    return reason === "refresh" ? "tv-open-refresh" : reason === "enter" ? "tv-open-enter" : "tv-open-view";
  }

  function _resetOpeningGate() {
    _tvHasOpsPayload = false;
    _tvOpeningGate = {
      ops: { initialDone: false, lastRefreshAt: 0 },
      report: { initialDone: false, lastRefreshAt: 0 },
    };
  }

  function _allowOpening(kind, reason) {
    const gate = _tvOpeningGate[kind];
    if (!gate) return false;
    if (reason === "pin" || reason === "silent" || reason === "truck-page") return false;
    const now = Date.now();
    if (reason === "initial" || reason === "enter") {
      if (gate.initialDone) return false;
      gate.initialDone = true;
      gate.lastRefreshAt = now;
      return true;
    }
    if (reason === "refresh") {
      if (!gate.initialDone) {
        gate.initialDone = true;
        gate.lastRefreshAt = now;
        return true;
      }
      if (now - gate.lastRefreshAt < _TV_REFRESH_OPEN_MIN_MS) return false;
      gate.lastRefreshAt = now;
      return true;
    }
    if (reason === "view" || reason === "auto") {
      gate.initialDone = true;
      gate.lastRefreshAt = now;
      return true;
    }
    return false;
  }

  function _clearOpeningTimers() {
    ["tv-page-ops", "tv-page-report"].forEach(id => {
      const page = document.getElementById(id);
      if (!page) return;
      if (id === "tv-page-report") _tvReportOpeningSeq++;
      if (page._tvOpenStartTimer) {
        clearTimeout(page._tvOpenStartTimer);
        page._tvOpenStartTimer = null;
      }
      if (page._tvOpenTimer) {
        clearTimeout(page._tvOpenTimer);
        page._tvOpenTimer = null;
      }
      if (page._tvReportPhaseTimers) {
        page._tvReportPhaseTimers.forEach(clearTimeout);
        page._tvReportPhaseTimers = [];
      }
      page.classList.remove("tv-opening-pending", "tv-opening", "tv-open-refresh", "tv-open-view", "tv-open-enter");
    });
  }

  function _scheduleOpening(page, reason, activeMs, startFn, primeFn) {
    if (!page || !page.classList.contains("is-active")) return;
    if (page._tvOpenStartTimer) clearTimeout(page._tvOpenStartTimer);
    if (page._tvOpenTimer) clearTimeout(page._tvOpenTimer);
    page.classList.remove("tv-opening", "tv-open-refresh", "tv-open-view", "tv-open-enter");
    page.classList.add("tv-opening-pending");
    if (typeof primeFn === "function") primeFn();
    const delay = _reduceMotion() ? 0 : _TV_OPEN_DELAY;
    page._tvOpenStartTimer = setTimeout(() => {
      page._tvOpenStartTimer = null;
      if (!page.classList.contains("is-active")) return;
      page.classList.remove("tv-opening-pending");
      void page.offsetWidth;
      page.classList.add("tv-opening", _openingClass(reason));
      startFn();
      page._tvOpenTimer = setTimeout(() => {
        page.classList.remove("tv-opening", "tv-open-refresh", "tv-open-view", "tv-open-enter");
        page._tvOpenTimer = null;
      }, activeMs);
    }, delay);
  }

  function _runOpsOpening(reason) {
    const page = document.getElementById("tv-page-ops");
    if (!page || !page.classList.contains("is-active")) return;
    if (!_allowOpening("ops", reason)) {
      page.classList.remove("tv-opening-pending");
      return;
    }
    _scheduleOpening(page, reason, 1900, () => {
      _setStagger(page, ".tv-truck-card", 0).forEach((card, i) => {
        _typeText(card.querySelector(".tv-truck-plate"), 190 + i * 95, 14);
      });
    });
  }

  /* ──────────────────────────────────────────────────────────────
     MŰSZAK SPARKLINE-OK (header — óránkénti in/out, jelzésszerű)
  ────────────────────────────────────────────────────────────── */

  function _fmtKgShort(v) {
    v = Math.round(v || 0);
    if (v >= 10000) return Math.round(v / 1000) + "k";
    if (v >= 1000)  return (v / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(v);
  }

  // A műszak MIND a 12 óráját fixen kirajzolja; a vonal csak az eltelt órákig megy,
  // a még el nem ért (jövő) órák területe halványan jelölve + "most" függőleges vonal.
  function _sparkSvg(buckets, cls) {
    const all = (buckets || []).slice(0, 12);
    const W = 336, H = 82, padX = 18, padTop = 28, padBot = 19;
    const baseY = H - padBot;
    if (!all.length) {
      return `<svg class="tv-spark ${cls} is-empty" viewBox="0 0 ${W} ${H}">` +
             `<line class="tv-spark-base" x1="${padX}" y1="${baseY}" x2="${W - padX}" y2="${baseY}"/>` +
             `<text class="tv-spark-empty" x="${W / 2}" y="${H / 2 + 2}">nincs adat</text></svg>`;
    }
    const innerW = W - padX * 2, innerH = H - padTop - padBot;
    const n = all.length;
    const x = i => n === 1 ? W / 2 : padX + innerW * (i / (n - 1));
    const rawMaxKg = Math.max(0, ...all.filter(b => b.active).map(b => b.kg || 0));
    const maxKg = Math.max(1000, rawMaxKg);
    const y = v => padTop + innerH * (1 - (v || 0) / maxKg);
    let lastActive = -1;
    all.forEach((b, i) => { if (b.active) lastActive = i; });

    let future = "";
    if (lastActive < n - 1) {
      const fx = x(Math.max(lastActive, 0));
      future = `<rect class="tv-spark-future" x="${fx.toFixed(1)}" y="${padTop.toFixed(1)}" width="${(W - padX - fx).toFixed(1)}" height="${(baseY - padTop).toFixed(1)}"/>`;
    }
    let nowLine = "";
    if (lastActive >= 0 && lastActive < n - 1) {
      const nx = x(lastActive);
      nowLine = `<line class="tv-spark-now" x1="${nx.toFixed(1)}" y1="${padTop.toFixed(1)}" x2="${nx.toFixed(1)}" y2="${baseY.toFixed(1)}"/>`;
    }
    // Vonal + area CSAK az eltelt órákra (0..lastActive — ezek folytonosak).
    const lp = [];
    for (let i = 0; i <= lastActive; i++) lp.push([x(i), y(all[i].kg)]);
    const line = lp.map((c, i) => (i ? "L" : "M") + c[0].toFixed(1) + " " + c[1].toFixed(1)).join(" ");
    const area = lp.length
      ? `M${lp[0][0].toFixed(1)} ${baseY.toFixed(1)} ` +
        lp.map(c => "L" + c[0].toFixed(1) + " " + c[1].toFixed(1)).join(" ") +
        ` L${lp[lp.length - 1][0].toFixed(1)} ${baseY.toFixed(1)} Z`
      : "";
    // Óracímke MINDEN órán (jövő halványabb); pont + értékcímke csak az aktívon.
    const marks = all.map((b, i) => {
      const cx = x(i);
      const hr = `<text class="tv-spark-hr${b.active ? "" : " is-future"}" x="${cx.toFixed(1)}" y="${(H - 5).toFixed(1)}">${b.label}</text>`;
      if (!b.active) return hr;
      const cy = y(b.kg);
      const cur = b.current ? " tv-spark-cur" : "";
      return `<circle class="tv-spark-dot${cur}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${b.current ? 4.4 : 3}"/>` +
             `<text class="tv-spark-val" x="${cx.toFixed(1)}" y="${(cy - 8).toFixed(1)}">${_fmtKgShort(b.kg)}</text>` + hr;
    }).join("");
    return `<svg class="tv-spark ${cls}" viewBox="0 0 ${W} ${H}">` +
           future +
           `<line class="tv-spark-base" x1="${padX}" y1="${baseY.toFixed(1)}" x2="${W - padX}" y2="${baseY.toFixed(1)}"/>` +
           (area ? `<path class="tv-spark-area" d="${area}"/>` : "") +
           (line ? `<path class="tv-spark-line" d="${line}"/>` : "") +
           nowLine + marks + `</svg>`;
  }

  function _paintSpark(id, buckets, cls) {
    const el = document.getElementById(id);
    if (!el) return;
    const sig = JSON.stringify((buckets || []).map(b => [b.label, b.kg, b.active ? 1 : 0, b.current ? 1 : 0]));
    if (el.dataset.sig === sig) return;   // ne rajzoljon újra változatlan adatra
    el.dataset.sig = sig;
    el.innerHTML = _sparkSvg(buckets, cls);
  }

  function _renderShiftCharts(d) {
    _paintSpark("tv-hdr-chart-in",  d.shift_hourly,           "tv-spark-in");
    _paintSpark("tv-hdr-chart-out", d.outbound_shift_hourly,  "tv-spark-out");
  }

  /* ──────────────────────────────────────────────────────────────
     OPS RENDER
  ────────────────────────────────────────────────────────────── */

  function _renderOps(d) {
    const firstOpsPayload = !_tvHasOpsPayload;
    _tvHasOpsPayload = true;
    _lastOpsPayload = d || null;

    _setNum("tv-hdr-out-kg",   d.shift_out_kg  || 0);
    _setNum("tv-hdr-in-kg",    d.shift_kg      || 0);
    _setNum("tv-hdr-issued",   d.issued_trucks  || 0);
    _setNum("tv-hdr-waiting",  d.waiting_trucks || 0);
    _setNum("tv-hdr-wh-active", d.warehouse_active_items || 0);
    _setNum("tv-hdr-wh-kg",     d.warehouse_active_kg || 0);

    _setText("tv-shift-name", d.shift_name || "");
    _setText("tv-prev-name",  d.prev_shift_name || "Előző műszak");
    _setNum("tv-prev-kg",     d.prev_shift_kg || 0);

    _renderShiftCharts(d);

    // Mark refresh time
    _lastRefreshTime = Date.now();
    _updateRefreshArc();

    const trucksChanged = _updateTrucks(d.trucks || []);
    _renderReport(d.report);
    if (_curPage === 0 && firstOpsPayload) {
      setTimeout(() => _runOpsOpening("initial"), 120);
    } else if (_curPage === 0 && trucksChanged) {
      setTimeout(() => _runOpsOpening("refresh"), 120);
    }

    const ts = document.getElementById("tv-last-refresh");
    if (ts) ts.textContent = new Date().toLocaleTimeString("hu-HU");

    _renderFreshness(d.freshness);
  }

  // Sync-stall / stale-data warning. The TV keeps showing the last GOOD snapshot
  // when the background read is stuck; this banner makes that visible (a frozen
  // TV otherwise looks alive because the clock is client-side). Also warns when
  // the last successful refresh is much older than the 10-minute interval, even
  // if the backend has not flagged a hard stall yet.
  function _fmtAge(sec) {
    const s = Math.max(0, Math.round(sec || 0));
    if (s < 90) return "kevesebb mint 1 perce";
    const m = Math.round(s / 60);
    if (m < 60) return m + " perce";
    const h = Math.floor(m / 60), mm = m % 60;
    return mm ? `${h} ó ${mm} p-e` : `${h} órája`;
  }

  function _renderFreshness(f) {
    const el = document.getElementById("tv-stale-banner");
    if (!el) return;
    f = f || {};
    const age = (typeof f.age_seconds === "number") ? f.age_seconds : null;
    // Soft threshold: two missed intervals (~22 perc) also raises the warning.
    const tooOld = age != null && age > 22 * 60;
    const on = !!f.stalled || tooOld;
    if (!on) {
      if (el.getAttribute("data-on") !== "0") {
        el.setAttribute("data-on", "0");
        el.innerHTML = "";
      }
      return;
    }
    const ageTxt = age != null ? _fmtAge(age) : "ismeretlen ideje";
    const warnIco =
      `<svg class="tv-stale-ico" viewBox="0 0 24 24" aria-hidden="true">` +
      `<path d="M12 3.2 22 20H2L12 3.2z"/>` +
      `<path class="tv-stale-ico-bang" d="M12 9.5v4.6M12 16.7v.2"/>` +
      `</svg>`;
    el.innerHTML =
      warnIco +
      `<span class="tv-stale-txt">Szinkron elakadt — az adat ${_esc(ageTxt)} frissült</span>`;
    el.setAttribute("data-on", "1");
  }

  /* ──────────────────────────────────────────────────────────────
     MŰSZAK RIPORT — speedométerek + összegzés + óradiagram
     Outbound-vezérelt; minimális inbound. A header és az alsó sáv FIX,
     csak a .tv-main vált a RAKODÁSOK ⇄ RIPORT oldal között.
  ────────────────────────────────────────────────────────────── */

  // Gauge geometria: 270°-os nyitott speedométer (135° → 405°), alul nyit.
  const _GA_START = 135, _GA_SWEEP = 270;

  function _deg2pt(cx, cy, r, deg) {
    const a = deg * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  }
  function _arcPath(cx, cy, r, startDeg, endDeg) {
    const [x0, y0] = _deg2pt(cx, cy, r, startDeg);
    const [x1, y1] = _deg2pt(cx, cy, r, endDeg);
    const large = ((endDeg - startDeg) % 360 + 360) % 360 > 180 ? 1 : 0;
    return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  }
  // Felfelé kerekítés "szép" skálamaximumra (1 / 2 / 2.5 / 5 / 10 ×10ⁿ).
  function _niceCeil(v) {
    if (!(v > 0)) return 1;
    const exp  = Math.floor(Math.log10(v));
    const base = Math.pow(10, exp);
    const n    = v / base;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10;
    return step * base;
  }
  function _reduceMotion() {
    return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // TV riport v2: fix speedométerek (nagy kg/óra + kis rakodás/óra), stabil nagy
  // átlag mint félskála/referencia (fehér ref-tick + állapotszín). A colli/parcel/
  // paletta érték- és ütem-sorok a két szöveges panelben vannak (_renderOutboundPanel).
  const _GMETRICS = {
    kg:       { label: "", name: "KG / ÓRA", unit: "kilogramm / óra", theme: "purple",
                fmt: v => _fmtNum(v) },
    loadings: { label: "RAKODÁSI ÜTEM", name: "RAKODÁS / ÓRA", unit: "rakodás / óra", theme: "purple",
                fmt: v => (Math.round((Number(v) || 0) * 10) / 10).toLocaleString("hu-HU") },
  };

  /* ── Report state ───────────────────────────────────────────── */

  let _reportData = null;

  function _fmtH(h) {
    h = Math.max(0, Number(h) || 0);
    return h.toFixed(1).replace(".", ",") + " ó";
  }

  // Óránkénti outbound kiadás — csak az aktuális műszak vonala.
  function _reportChart(o) {
    const host = document.getElementById("tv-report-chart");
    if (!host) return;
    const cur = (o.hourly || []).slice(0, 12);
    let lastActive = -1;
    cur.forEach((b, i) => { if (b.active) lastActive = i; });
    // A diagramot a konténer VALÓDI pixelméretében rajzoljuk (viewBox = tényleges
    // px), és preserveAspectRatio="xMidYMid meet"-tel jelenítjük meg. Így a skálázás
    // 1:1 / egyenletes — sem a vonal, sem a pöttyök, sem a px-ben megadott feliratok
    // nem nyúlnak/torzulnak. (A korábbi fix 1000×300 viewBox + preserveAspectRatio
    // ="none" volt a "megnyújtott" hatás oka a magas cellában.) A 16px levonás a
    // .tv-report-chart vízszintes/függőleges paddingja; 0-méret esetén stabil
    // fallback (a rögzített 1920×1080 stage-en mért tipikus méret).
    const cw = host.clientWidth || 0, ch = host.clientHeight || 0;
    const W = (cw > 200) ? Math.round(cw - 16) : 1040;
    const H = (ch > 120) ? Math.round(ch - 16) : 432;
    const padL = 86, padR = 74, padTop = 36, padBot = 46;
    const baseY = H - padBot, innerW = W - padL - padR, innerH = H - padTop - padBot;
    if (!cur.length) { host.innerHTML = `<div class="tv-rc-empty">nincs műszakadat</div>`; return; }
    const n = cur.length;
    const x = i => n === 1 ? (padL + innerW / 2) : padL + innerW * (i / (n - 1));
    const rawMaxKg = Math.max(0,
      ...cur.filter(b => b.active).map(b => b.kg || 0));
    const axisMax = _niceCeil(Math.max(1000, rawMaxKg));
    const y = v => padTop + innerH * (1 - (v || 0) / axisMax);
    // Vízszintes rácsvonalak (4 sáv).
    let grid = "";
    for (let g = 0; g <= 4; g++) {
      const gy = padTop + innerH * (g / 4);
      grid += `<line class="tv-rc-grid" x1="${padL}" y1="${gy.toFixed(1)}" x2="${W - padR}" y2="${gy.toFixed(1)}"/>`;
      const gv = Math.round(axisMax * (1 - g / 4));
      grid += `<text class="tv-rc-axis" x="${padL - 12}" y="${(gy + 5).toFixed(1)}" text-anchor="end">${_fmtKgShort(gv)}</text>`;
    }

    // Jövő-tartomány + "most" vonal.
    let future = "", nowLine = "";
    if (lastActive < n - 1) {
      const fx = x(Math.max(lastActive, 0));
      const fw = W - padR - fx;
      future = `<rect class="tv-rc-future" x="${fx.toFixed(1)}" y="${padTop}" width="${fw.toFixed(1)}" height="${(baseY - padTop).toFixed(1)}"/>` +
        (fw > 220 ? `<text class="tv-rc-future-label" x="${(fx + fw / 2).toFixed(1)}" y="${(padTop + innerH * 0.48).toFixed(1)}" text-anchor="middle">HÁTRALÉVŐ MŰSZAK</text>` : "");
    }
    if (lastActive >= 0 && lastActive < n - 1) {
      const nx = x(lastActive);
      const ny = y(cur[lastActive].kg);
      nowLine = `<line class="tv-rc-now" x1="${nx.toFixed(1)}" y1="${padTop}" x2="${nx.toFixed(1)}" y2="${baseY.toFixed(1)}"/>` +
        `<circle class="tv-rc-now-dot" cx="${nx.toFixed(1)}" cy="${ny.toFixed(1)}" r="4.8"/>`;
    }

    const lp = [];
    for (let i = 0; i <= lastActive; i++) lp.push([x(i), y(cur[i].kg)]);
    const line = lp.map((c, i) => (i ? "L" : "M") + c[0].toFixed(1) + " " + c[1].toFixed(1)).join(" ");
    const area = lp.length
      ? `M${lp[0][0].toFixed(1)} ${baseY.toFixed(1)} ` +
        lp.map(c => "L" + c[0].toFixed(1) + " " + c[1].toFixed(1)).join(" ") +
        ` L${lp[lp.length - 1][0].toFixed(1)} ${baseY.toFixed(1)} Z`
      : "";

    const marks = cur.map((b, i) => {
      const cx = x(i);
      const hrAnchor = i === 0 ? "start" : (i === n - 1 ? "end" : "middle");
      const hrX = i === 0 ? cx - 2 : (i === n - 1 ? cx + 2 : cx);
      const hr = `<text class="tv-rc-hr${b.active ? "" : " is-future"}" style="text-anchor:${hrAnchor}" x="${hrX.toFixed(1)}" y="${(H - 12).toFixed(1)}">${b.label}</text>`;
      if (!b.active) return hr;
      const cy = y(b.kg);
      const cur2 = b.current ? " is-cur" : "";
      const valAnchor = i === 0 ? "start" : (i === n - 1 ? "end" : "middle");
      const valX = i === 0 ? cx + 7 : (i === n - 1 ? cx - 7 : cx);
      return `<circle class="tv-rc-dot${cur2}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${b.current ? 6 : 4}"/>` +
             `<text class="tv-rc-val" style="text-anchor:${valAnchor}" x="${valX.toFixed(1)}" y="${(cy - 11).toFixed(1)}">${_fmtKgShort(b.kg)}</text>` + hr;
    }).join("");

    const sig = JSON.stringify(cur.map(b => [b.label, b.kg, b.active ? 1 : 0, b.current ? 1 : 0]))
              + "|" + Math.round(axisMax) + "|" + W + "x" + H;
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    host.innerHTML =
      `<svg class="tv-rc-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">` +
        `<defs>` +
          `<linearGradient id="tvRcLineGrad" x1="0%" y1="0%" x2="100%" y2="0%">` +
            `<stop offset="0%" stop-color="#7c3aed"/><stop offset="52%" stop-color="#a78bfa"/><stop offset="100%" stop-color="#f0e7ff"/>` +
          `</linearGradient>` +
          `<linearGradient id="tvRcAreaGrad" x1="0%" y1="0%" x2="0%" y2="100%">` +
            `<stop offset="0%" stop-color="#a78bfa" stop-opacity="0.34"/><stop offset="72%" stop-color="#7c3aed" stop-opacity="0.08"/><stop offset="100%" stop-color="#7c3aed" stop-opacity="0"/>` +
          `</linearGradient>` +
          `<linearGradient id="tvRcFutureGrad" x1="0%" y1="0%" x2="100%" y2="0%">` +
            `<stop offset="0%" stop-color="#050916" stop-opacity="0.52"/><stop offset="12%" stop-color="#050916" stop-opacity="0.36"/><stop offset="100%" stop-color="#050916" stop-opacity="0.22"/>` +
          `</linearGradient>` +
          `<clipPath id="tvRcRevealClip"><rect class="tv-rc-reveal" x="${padL}" y="0" width="${innerW}" height="${H}"/></clipPath>` +
        `</defs>` +
        grid + future +
        `<line class="tv-rc-base" x1="${padL}" y1="${baseY}" x2="${W - padR}" y2="${baseY}"/>` +
        `<g clip-path="url(#tvRcRevealClip)">` +
          (area ? `<path class="tv-rc-area" d="${area}" fill="url(#tvRcAreaGrad)"/>` : "") +
          (line ? `<path class="tv-rc-glow" d="${line}"/>` : "") +
          (line ? `<path class="tv-rc-line" d="${line}" stroke="url(#tvRcLineGrad)"/>` : "") +
          nowLine + marks +
        `</g>` +
      `</svg>`;
  }

  function _buildGaugeSkeleton(host, cfg) {
    const small = !!cfg.small;
    const cx = small ? 118 : 142, cy = small ? 118 : 142, r = small ? 86 : 108;
    const vb = small ? "0 0 236 188" : "0 0 284 226";
    const track = _arcPath(cx, cy, r, _GA_START, _GA_START + _GA_SWEEP);
    const gradId = `${host.id || "tv-gauge"}-${cfg.theme || "purple"}-grad`;
    const shineId = `${gradId}-shine`;
    const glowId = `${gradId}-glow`;
    const stops = cfg.theme === "orange"
      ? [["0%", "#9a3412"], ["58%", "#fb923c"], ["100%", "#fed7aa"]]
      : [["0%", "#4c1d95"], ["58%", "#7c3aed"], ["100%", "#ddd6fe"]];
    let ticks = "";
    const tickCount = small ? 18 : 30;
    for (let i = 0; i <= tickCount; i++) {
      const f = i / tickCount;
      const deg = _GA_START + f * _GA_SWEEP;
      const major = i % (small ? 6 : 5) === 0;
      const outer = r + (major ? 5 : 3);
      const inner = r - (major ? 17 : 9);
      const [x1, y1] = _deg2pt(cx, cy, outer, deg);
      const [x2, y2] = _deg2pt(cx, cy, inner, deg);
      ticks += `<line class="tv-g-tick ${major ? "is-major" : "is-minor"}" style="--tv-i:${i}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`;
    }
    const zones = "";
    const trackSegs = "";
    const tipX = cx + r - (small ? 18 : 20), tipY = cy;
    const tailX = cx - (small ? 17 : 21), tailY = cy;
    host.innerHTML =
      `<div class="tv-g-title"><div class="tv-g-eyebrow"></div><div class="tv-g-name"></div></div>` +
      `<svg class="tv-g-svg" viewBox="${vb}" preserveAspectRatio="xMidYMid meet" aria-hidden="true">` +
        `<defs>` +
          `<linearGradient id="${gradId}" x1="0%" y1="100%" x2="100%" y2="0%">` +
            stops.map(s => `<stop offset="${s[0]}" stop-color="${s[1]}"/>`).join("") +
          `</linearGradient>` +
          `<linearGradient id="${shineId}" x1="0%" y1="0%" x2="0%" y2="100%">` +
            `<stop offset="0%" stop-color="rgba(255,255,255,0.45)"/><stop offset="100%" stop-color="rgba(255,255,255,0)"/>` +
          `</linearGradient>` +
          `<filter id="${glowId}" x="-45%" y="-45%" width="190%" height="190%">` +
            `<feGaussianBlur stdDeviation="${small ? 2.6 : 3.4}" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>` +
          `</filter>` +
        `</defs>` +
        zones +
        `<path class="tv-g-track" d="${track}"/>` +
        trackSegs +
        `<path class="tv-g-val" d="${track}" style="stroke:url(#${gradId})"/>` +
        `<path class="tv-g-val-shine" d="${track}" style="stroke:url(#${shineId})"/>` +
        ticks +
        `<line class="tv-g-ref" x1="0" y1="0" x2="0" y2="0" style="display:none"/>` +
        `<text class="tv-g-scale tv-g-scale-min" x="${(cx - r + 8).toFixed(1)}" y="${(cy + 20).toFixed(1)}">0</text>` +
        `<text class="tv-g-scale tv-g-scale-mid" x="${cx}" y="${(cy - r - 12).toFixed(1)}" text-anchor="middle">0</text>` +
        `<text class="tv-g-scale tv-g-scale-max" x="${(cx + r - 8).toFixed(1)}" y="${(cy + 20).toFixed(1)}" text-anchor="end">0</text>` +
        `<g class="tv-g-needle">` +
          `<path class="tv-g-needle-shadow" d="M${tailX.toFixed(1)} ${tailY.toFixed(1)} L${(cx - 7).toFixed(1)} ${(cy + 10).toFixed(1)} L${tipX.toFixed(1)} ${tipY.toFixed(1)} L${(cx - 7).toFixed(1)} ${(cy - 10).toFixed(1)} Z"/>` +
          `<path class="tv-g-needle-body" d="M${tailX.toFixed(1)} ${tailY.toFixed(1)} L${(cx - 6).toFixed(1)} ${(cy + 8).toFixed(1)} L${tipX.toFixed(1)} ${tipY.toFixed(1)} L${(cx - 6).toFixed(1)} ${(cy - 8).toFixed(1)} Z"/>` +
          `<path class="tv-g-needle-spine" d="M${(cx + 2).toFixed(1)} ${cy.toFixed(1)} L${tipX.toFixed(1)} ${tipY.toFixed(1)}"/>` +
        `</g>` +
        `<circle class="tv-g-hub-outer" cx="${cx}" cy="${cy}" r="${small ? 12 : 15}"/><circle class="tv-g-hub" cx="${cx}" cy="${cy}" r="${small ? 7 : 9}"/>` +
      `</svg>` +
      `<div class="tv-g-readout"><div class="tv-g-value" data-v="0">0</div><div class="tv-g-unit"></div><div class="tv-g-delta"></div></div>`;
    host._geo = { cx, cy, r };
    host._len = r * _GA_SWEEP * Math.PI / 180;
    host._frac = 0;
    ["tv-g-val", "tv-g-val-shine"].forEach(cls => {
      const path = host.querySelector("." + cls);
      if (!path) return;
      path.style.strokeDasharray = host._len.toFixed(2);
      path.style.strokeDashoffset = host._len.toFixed(2);
    });
    _gaugeNeedleShapeV2(host, 0);
  }

  function _renderGauge(host, cfg) {
    if (!host) return;
    const small = !!cfg.small;
    host.classList.toggle("tv-gauge-small", small);
    host.classList.toggle("tv-gauge-orange", cfg.theme === "orange");
    host.classList.toggle("tv-gauge-purple", cfg.theme !== "orange");
    const sig = `${cfg.key}|${small ? 1 : 0}|${cfg.theme || "purple"}`;
    if (host.dataset.gkey !== sig) {
      if (host._graf) cancelAnimationFrame(host._graf);
      _buildGaugeSkeleton(host, cfg);
      host.dataset.gkey = sig;
    }
    host.querySelector(".tv-g-unit").textContent = cfg.unit;
    host.querySelector(".tv-g-eyebrow").textContent = cfg.label;
    host.querySelector(".tv-g-name").textContent = cfg.name || cfg.unit;

    // Skála: a nagy történeti átlag a fix felező. Poll közben nem tágul az aktuális
    // tempó után; ha 2x átlag fölé megyünk, a tű a végállásnál clampel.
    const value = Number(cfg.value) || 0;
    const ref = Number(cfg.ref) || 0;
    const midpoint = ref > 0 ? ref : 0;
    const max = midpoint > 0
      ? Math.max(midpoint * 2, 1)
      : (_niceCeil(Math.max(value, cfg.scale || 0, 0) * 1.32) || 1);
    const frac = Math.max(0, Math.min(1, value / max));
    const minEl = host.querySelector(".tv-g-scale-min");
    const midEl = host.querySelector(".tv-g-scale-mid");
    const maxEl = host.querySelector(".tv-g-scale-max");
    if (minEl) minEl.textContent = "0";
    // A mid felirat a skála VALÓDI közepe — a referenciát a saját tick-je jelöli,
    // nem a tengelyfeliratot hamisítjuk hozzá.
    if (midEl) midEl.textContent = cfg.fmt(midpoint > 0 ? midpoint : (max / 2));
    if (maxEl) maxEl.textContent = cfg.fmt(max);

    // Referencia-tick az íven.
    const refEl = host.querySelector(".tv-g-ref");
    if (refEl) {
      if (ref > 0) {
        const geo = host._geo;
        const rdeg = _GA_START + Math.max(0, Math.min(1, ref / max)) * _GA_SWEEP;
        const [ox, oy] = _deg2pt(geo.cx, geo.cy, geo.r + 6, rdeg);
        const [ix, iy] = _deg2pt(geo.cx, geo.cy, geo.r - 19, rdeg);
        refEl.setAttribute("x1", ox.toFixed(1)); refEl.setAttribute("y1", oy.toFixed(1));
        refEl.setAttribute("x2", ix.toFixed(1)); refEl.setAttribute("y2", iy.toFixed(1));
        refEl.style.display = "";
      } else {
        refEl.style.display = "none";
      }
    }

    // Állapotszín a REFERENCIÁHOZ mérve: ≥95% zöld (átlagtempó = rendben), 70–95%
    // sárga, alatta piros. Felfutásnál / referencia nélkül neutrális — a régi
    // frac-küszöb (0.68/0.38 az avg*2 skálán) az átlagtempót is sárgára festette.
    const dEl = host.querySelector(".tv-g-delta");
    host.classList.remove("tv-g-ok", "tv-g-warn", "tv-g-low", "tv-g-neutral");
    if (cfg.prime) {
      host.classList.add("tv-g-neutral");
      dEl.textContent = "";
      dEl.className = "tv-g-delta is-flat";
      dEl.style.display = "none";
    } else if (cfg.warmup) {
      host.classList.add("tv-g-neutral");
      dEl.textContent = "műszak felfutás";
      dEl.className = "tv-g-delta is-flat";
      dEl.style.display = "";
    } else if (ref > 0) {
      const ratio = value / ref;
      const state = ratio >= 0.95 ? "tv-g-ok" : ratio >= 0.7 ? "tv-g-warn" : "tv-g-low";
      host.classList.add(state);
      const refName = cfg.refKind === "big_avg" ? "nagy átlag" : (cfg.refKind === "avg" ? "műszak óraátlag" : "referencia");
      dEl.textContent = `${Math.round(ratio * 100)}% · ${refName} ${cfg.fmt(ref)}/ó`;
      dEl.className = "tv-g-delta " + (state === "tv-g-ok" ? "is-up" : state === "tv-g-warn" ? "is-flat" : "is-down");
      dEl.style.display = "";
    } else {
      host.classList.add("tv-g-neutral");
      dEl.textContent = "";
      dEl.className = "tv-g-delta is-flat";
      dEl.style.display = "none";
    }
    _animateGauge(host, frac, cfg);
  }

  function _gaugeNeedleShapeV2(host, frac) {
    const geo = host && host._geo;
    if (!geo) return;
    const body = host.querySelector(".tv-g-needle-body");
    const shadow = host.querySelector(".tv-g-needle-shadow");
    const spine = host.querySelector(".tv-g-needle-spine");
    if (!body || !shadow || !spine) return;
    const a = _GA_START + Math.max(0, Math.min(1, frac || 0)) * _GA_SWEEP;
    const tip = _deg2pt(geo.cx, geo.cy, geo.r + 1, a);
    const tail = _deg2pt(geo.cx, geo.cy, 20, a + 180);
    const p1 = _deg2pt(geo.cx, geo.cy, 8, a + 90);
    const p2 = _deg2pt(geo.cx, geo.cy, 8, a - 90);
    const s1 = _deg2pt(geo.cx, geo.cy, 11, a + 90);
    const s2 = _deg2pt(geo.cx, geo.cy, 11, a - 90);
    body.setAttribute("d", `M${tail[0].toFixed(1)} ${tail[1].toFixed(1)} L${p1[0].toFixed(1)} ${p1[1].toFixed(1)} L${tip[0].toFixed(1)} ${tip[1].toFixed(1)} L${p2[0].toFixed(1)} ${p2[1].toFixed(1)} Z`);
    shadow.setAttribute("d", `M${tail[0].toFixed(1)} ${tail[1].toFixed(1)} L${s1[0].toFixed(1)} ${s1[1].toFixed(1)} L${tip[0].toFixed(1)} ${tip[1].toFixed(1)} L${s2[0].toFixed(1)} ${s2[1].toFixed(1)} Z`);
    spine.setAttribute("d", `M${geo.cx.toFixed(1)} ${geo.cy.toFixed(1)} L${tip[0].toFixed(1)} ${tip[1].toFixed(1)}`);
  }

  function _animateGauge(host, toFrac, cfg) {
    const valArc = host.querySelector(".tv-g-val");
    const shineArc = host.querySelector(".tv-g-val-shine");
    const needle = host.querySelector(".tv-g-needle");
    const valEl = host.querySelector(".tv-g-value");
    if (!valArc || !needle || !valEl) return;
    const len = host._len, geo = host._geo;
    const fromFrac = host._frac || 0;
    const fromVal = parseFloat(valEl.dataset.v || "0");
    const toVal = Number(cfg.value) || 0;
    if (cfg.fromZero) {
      host._frac = 0;
      valEl.dataset.v = "0";
    }
    const startFrac = cfg.fromZero ? 0 : fromFrac;
    const startVal = cfg.fromZero ? 0 : fromVal;
    if (host._graf) cancelAnimationFrame(host._graf);
    if (cfg.animate === false || (!cfg.fromZero && Math.abs(fromFrac - toFrac) < 0.0001 && Math.abs(fromVal - toVal) < 0.0001)) {
      const finalOff = (len * (1 - toFrac)).toFixed(2);
      host._frac = toFrac;
      host._graf = null;
      valEl.dataset.v = String(toVal);
      valArc.style.strokeDashoffset = finalOff;
      if (shineArc) shineArc.style.strokeDashoffset = finalOff;
      needle.removeAttribute("transform");
      _gaugeNeedleShapeV2(host, toFrac);
      valEl.textContent = cfg.fmt(toVal);
      return;
    }
    const dur = _reduceMotion() ? 0 : 1550;
    const t0 = performance.now();
    function step(now) {
      // Math.max 0: lásd _animateTextNumber — frame-jitter miatt now < t0 is
      // előfordulhat, negatív t nélküle negatív e-t (villanásnyi negatív
      // érték/tű-túllendülés) adna a tween indulásakor.
      const t = dur ? Math.max(0, Math.min((now - t0) / dur, 1)) : 1;
      const e = 1 - Math.pow(1 - t, 5);
      const f = startFrac + (toFrac - startFrac) * e;
      const off = (len * (1 - f)).toFixed(2);
      valArc.style.strokeDashoffset = off;
      if (shineArc) shineArc.style.strokeDashoffset = off;
      needle.removeAttribute("transform");
      _gaugeNeedleShapeV2(host, f);
      valEl.textContent = cfg.fmt(startVal + (toVal - startVal) * e);
      if (t < 1) {
        host._graf = requestAnimationFrame(step);
      } else {
        const finalOff = (len * (1 - toFrac)).toFixed(2);
        host._frac = toFrac;
        host._graf = null;
        valEl.dataset.v = String(toVal);
        valArc.style.strokeDashoffset = finalOff;
        if (shineArc) shineArc.style.strokeDashoffset = finalOff;
        needle.removeAttribute("transform");
        _gaugeNeedleShapeV2(host, toFrac);
        valEl.textContent = cfg.fmt(toVal);
      }
    }
    host._graf = requestAnimationFrame(step);
  }

  function _renderGaugesNow(opts) {
    if (!_reportData) return;
    opts = opts || {};
    const only = opts.only || "all";
    const o = _reportData.out || {};
    const warmup = !!_reportData.warmup;
    if (only === "all" || only === "kg") {
      _renderGauge(document.getElementById("tv-gauge-kg"),
      Object.assign({ key: "kg", value: o.kg_per_h || 0, scale: o.kg_per_h || 0,
        ref: o.ref_kg_per_h || 0, refKind: o.ref_kg_kind || "none", warmup,
        name: "KG / ÓRA", animate: opts.animate !== false, fromZero: !!opts.fromZero }, _GMETRICS.kg));
    }
    if (only === "all") {
      _renderOutboundPanel(document.getElementById("tv-gauge-colli"), o, "totals");
      _renderFixedLoadings(document.getElementById("tv-gauge-parcel"), _reportData.fixed_loadings || []);
    }
    if (only === "all" || only === "loadings") {
      _renderGauge(document.getElementById("tv-gauge-loadings"),
      Object.assign({ key: "loadings", value: o.loadings_per_h || 0, scale: Math.max(o.loadings_per_h || 0, 1),
        ref: o.ref_loadings_per_h || 0, refKind: o.ref_loadings_kind || "none", warmup,
        small: true, animate: opts.animate !== false, fromZero: !!opts.fromZero }, _GMETRICS.loadings));
    }
  }

  function _primeReportGaugesAtZero() {
    if (!_reportData) return;
    const o = _reportData.out || {};
    _renderGauge(document.getElementById("tv-gauge-kg"),
      Object.assign({ key: "kg", value: 0, scale: o.kg_per_h || 0,
        ref: o.ref_kg_per_h || 0, refKind: o.ref_kg_kind || "none", warmup: false,
        prime: true, animate: false, fromZero: false }, _GMETRICS.kg));
    _renderGauge(document.getElementById("tv-gauge-loadings"),
      Object.assign({ key: "loadings", value: 0, scale: Math.max(o.loadings_per_h || 0, 1),
        ref: o.ref_loadings_per_h || 0, refKind: o.ref_loadings_kind || "none", warmup: false,
        prime: true, small: true, animate: false, fromZero: false }, _GMETRICS.loadings));
  }

  function _renderOutboundPanel(host, o, mode) {
    if (!host) return;
    const rv = v => {
      v = Number(v) || 0;
      return v >= 100 ? _fmtNum(v) : (Math.round(v * 10) / 10).toLocaleString("hu-HU");
    };
    // A kg/óra NINCS a rates-panelben — azt a nagy gauge mutatja (redundancia ki);
    // helyette a paletta kap ütem-sort, a totals pedig paletta összesent.
    const rows = mode === "totals"
      ? [
          ["KIADOTT KG", _fmtNum(o.kg || 0), "kg"],
          ["KIADOTT COLLI", _fmtNum(o.colli || 0), "colli"],
          ["KIADOTT PALETTA", _fmtNum(o.pallets || 0), "plt"],
        ]
      : [
          ["COLLI / ÓRA", rv(o.colli_per_h), "colli/h"],
          ["PARCEL / ÓRA", rv(o.parcels_per_h), "parcel/h"],
          ["PALETTA / ÓRA", rv(o.pallets_per_h), "plt/h"],
        ];
    const sig = mode + "|" + rows.map(r => `${r[0]}:${r[1]}:${r[2]}`).join("|");
    if (host.dataset.sig === sig) return;
    if (host._graf) cancelAnimationFrame(host._graf);
    host.className = `tv-out-panel tv-out-panel-${mode}`;
    host.removeAttribute("data-gkey");
    host.dataset.sig = sig;
    host.innerHTML =
      `<div class="tv-out-panel-title">${mode === "totals" ? "KIADOTT MENNYISÉG" : "ÓRÁNKÉNTI ÜTEM"}</div>` +
      `<div class="tv-out-panel-grid">` +
        rows.map(r =>
          `<div class="tv-out-panel-row">` +
            (r[0] ? `<span class="tv-card-kicker">${r[0]}</span>` : "") +
            `<div class="tv-card-value"><b>${r[1]}</b>` +
            (r[2] ? `<small>${r[2]}</small>` : "") +
            `</div>` +
          `</div>`
        ).join("") +
      `</div>`;
  }

  const _FIXED_LOADINGS_PAGE_SIZE = 4;
  const _FIXED_LOADINGS_INTERVAL_MS = 3600;
  const _FIXED_LOADINGS_SWAP_MS = 760;

  function _clearFixedLoadingsTimer(host) {
    if (!host) return;
    if (host._flTimer) {
      clearInterval(host._flTimer);
      host._flTimer = null;
    }
    if (host._flSwapTimer) {
      clearTimeout(host._flSwapTimer);
      host._flSwapTimer = null;
    }
    host._flBusy = false;
  }

  function _fixedLoadingsCanRun() {
    const page = document.getElementById("tv-page-report");
    return !page || page.classList.contains("is-active");
  }

  function _fixedLoadingRowHtml(r) {
    // Csak helytálló, olvasható infó: rendszám + GLABS id + rámpa/MV + X/Y.
    // LMP kikerült (zsúfolt, levágott sor volt), a "reason" szöveget a sofőr
    // mini-kamion + az X/Y számláló váltja ki.
    const bits = [];
    if (r.ramp) bits.push(`<span>Rámpa ${_esc(r.ramp)}</span>`);
    if (r.loader) bits.push(`<span>${_esc(r.loader)}</span>`);
    const sub = bits.length ? bits.join("<i></i>") : "<span>várható</span>";
    // Sofőr-állapot: a rakodások lapi kamion miniatűr változata (zöld =
    // bejelentkezve, szürke = még nincs itt). Csak "here"/"away" fordulhat elő,
    // mert a pihenős rakodás nem kerül be a következő-rakodások listába.
    const drvState = r.driver_here ? "here" : "away";
    const drvTitle = r.driver_here ? "Sofőr bejelentkezve" : "Sofőr még nincs bejelentkezve";
    const driverMini =
      `<span class="tv-fixed-drv tv-drv-${drvState}" title="${_esc(drvTitle)}" role="img" aria-label="${_esc(drvTitle)}">` +
        _driverTruckSvgInner() +
      `</span>`;
    return `<div class="tv-fixed-loading-row">` +
      `<div class="tv-fixed-loading-main">` +
        `<span class="tv-fixed-plate">${_esc(r.plate || "-")}</span>` +
        `<span class="tv-fixed-glabs">${_esc(r.glabs_id || "-")}</span>` +
      `</div>` +
      `<div class="tv-fixed-loading-side"><b>${_esc(r.ready || 0)}/${_esc(r.total || 0)}</b></div>` +
      `<div class="tv-fixed-loading-meta">${driverMini}<span class="tv-fixed-meta-txt">${sub}</span></div>` +
    `</div>`;
  }

  function _fixedLoadingsPageHtml(pageRows) {
    return pageRows.map(_fixedLoadingRowHtml).join("");
  }

  function _fixedLoadingsDots(count, idx) {
    if (count <= 1) return "";
    return `<div class="tv-fixed-dots">` +
      Array.from({ length: count }, (_, i) =>
        `<span class="tv-fixed-dot${i === idx ? " is-active" : ""}"></span>`
      ).join("") +
    `</div>`;
  }

  function _updateFixedLoadingsDots(host) {
    const dots = host.querySelectorAll(".tv-fixed-dot");
    dots.forEach((dot, i) => dot.classList.toggle("is-active", i === host._flIdx));
  }

  function _showFixedLoadingsPage(host, nextIdx, animate) {
    if (!host || !host.isConnected) { _clearFixedLoadingsTimer(host); return; }
    const pages = host._flPages || [];
    const count = pages.length;
    if (count <= 1) return;
    const list = host.querySelector(".tv-fixed-list");
    if (!list) return;
    const idx = ((nextIdx % count) + count) % count;
    if (idx === host._flIdx && list.querySelector(".tv-fixed-page")) return;
    const html = _fixedLoadingsPageHtml(pages[idx]);
    if (!animate || _reduceMotion()) {
      list.innerHTML = `<div class="tv-fixed-page is-active">${html}</div>`;
      host._flIdx = idx;
      _updateFixedLoadingsDots(host);
      return;
    }
    if (host._flBusy) return;
    host._flBusy = true;
    const current = list.querySelector(".tv-fixed-page.is-active");
    const incoming = document.createElement("div");
    incoming.className = "tv-fixed-page is-entering";
    incoming.innerHTML = html;
    list.appendChild(incoming);
    void incoming.offsetWidth;
    if (current) {
      current.classList.remove("is-active");
      current.classList.add("is-leaving");
    }
    incoming.classList.remove("is-entering");
    incoming.classList.add("is-active");
    host._flIdx = idx;
    _updateFixedLoadingsDots(host);
    if (host._flSwapTimer) clearTimeout(host._flSwapTimer);
    host._flSwapTimer = setTimeout(() => {
      if (current && current.parentNode) current.parentNode.removeChild(current);
      host._flBusy = false;
      host._flSwapTimer = null;
    }, _FIXED_LOADINGS_SWAP_MS);
  }

  function _startFixedLoadingsCarousel(host) {
    if (!host || _reduceMotion() || !_fixedLoadingsCanRun()) return;
    const pages = host._flPages || [];
    if (pages.length <= 1 || host._flTimer) return;
    host._flTimer = setInterval(() => {
      if (!host.isConnected || !_fixedLoadingsCanRun()) {
        _clearFixedLoadingsTimer(host);
        return;
      }
      _showFixedLoadingsPage(host, (host._flIdx || 0) + 1, true);
    }, _FIXED_LOADINGS_INTERVAL_MS);
  }

  function _renderFixedLoadings(host, rows) {
    if (!host) return;
    const list = Array.isArray(rows) ? rows.slice() : [];
    const sig = JSON.stringify(list.map(r => [
      r.plate || "", r.glabs_id || "", r.ready || 0, r.total || 0,
      r.ramp || "", r.loader || "", r.reason || "", r.driver_here ? 1 : 0,
      Array.isArray(r.lmps) ? r.lmps.join("|") : ""
    ]));
    if (host._flSig === sig) {
      _startFixedLoadingsCarousel(host);
      return;
    }
    _clearFixedLoadingsTimer(host);
    if (host._graf) cancelAnimationFrame(host._graf);
    host.className = "tv-fixed-loadings";
    host.removeAttribute("data-gkey");
    host.dataset.sig = sig;
    host._flSig = sig;

    const pageSize = Math.max(1, Math.min(_FIXED_LOADINGS_PAGE_SIZE, list.length || 1));
    const pages = [];
    for (let i = 0; i < list.length; i += pageSize) {
      const page = list.slice(i, i + pageSize);
      if (page.length < pageSize && list.length > pageSize) {
        page.push(...list.slice(0, pageSize - page.length));
      }
      pages.push(page);
    }
    host._flPages = pages;
    host._flIdx = 0;

    const body = list.length
      ? `<div class="tv-fixed-page is-active${pages.length <= 1 ? " is-static" : ""}">${_fixedLoadingsPageHtml(pages[0])}</div>`
      : `<div class="tv-fixed-empty">Nincs következő rakodás</div>`;
    host.innerHTML =
      `<div class="tv-fixed-title">KÖVETKEZŐ RAKODÁSOK</div>` +
      `<div class="tv-fixed-list">${body}</div>` +
      _fixedLoadingsDots(pages.length, 0);
    _startFixedLoadingsCarousel(host);
  }

  function _renderReportStats(o) {
    const host = document.getElementById("tv-report-stats");
    if (!host) return;
    const loadings = Number(o.loadings) || 0;
    const rows = Number(o.rows) || 0;
    const kg = Number(o.kg) || 0;
    const itemsPerLoading = loadings > 0 ? rows / loadings : 0;
    const kgPerLoading = loadings > 0 ? kg / loadings : 0;
    const loadingTime = Number(o.loading_time_min) || 0;
    const items = [
      { val: _fmtNum(loadings), sub: "kiadott rakodás" },
      { val: itemsPerLoading > 0 ? (Math.round(itemsPerLoading * 10) / 10).toLocaleString("hu-HU") : "0", sub: "tétel/rakodás" },
      { val: _fmtNum(Math.round(kgPerLoading)), sub: "kg/rakodás" },
      { val: loadingTime > 0 ? _fmtNum(Math.round(loadingTime)) : "0", sub: "perc/rakodás" },
    ];
    const sig = items.map(it => `${it.cls || ""}:${it.val}:${it.sub}`).join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    host.innerHTML = items.map(it => {
      return `<div class="tv-rs-item ${it.cls || ""}">` +
        `<div class="tv-rs-val"><b>${it.val}</b></div>` +
        `<div class="tv-rs-sub">${it.sub}</div>` +
      `</div>`;
    }).join("");
  }

  function _renderReportInbound(ib) {
    const host = document.getElementById("tv-report-inb");
    if (!host) return;
    const kg = Number(ib.kg) || 0;
    const count = Number(ib.count) || 0;
    const becKg = Number(ib.bec_total_kg) || 0;
    const becCount = Number(ib.bec_count) || 0;
    const avgKg = count > 0 ? Math.round(kg / count) : 0;
    const pendingAvg = becCount > 0 ? Math.round(becKg / becCount) : 0;
    const sig = [kg, count, becKg, becCount, avgKg, pendingAvg].join("|");
    if (host.dataset.sig === sig) return;
    host.dataset.sig = sig;
    host.innerHTML =
      `<div class="tv-inb-hero">` +
        `<div class="tv-inb-lbl">beérkező mennyiség</div>` +
        `<div class="tv-inb-val tv-inb-kg"><b>${_fmtNum(kg)}</b><small>kg</small></div>` +
      `</div>` +
      `<div class="tv-inb-grid">` +
        `<div class="tv-inb-fig"><div class="tv-inb-lbl">rögzített tétel</div><div class="tv-inb-val"><b>${_fmtNum(count)}</b><small>db</small></div></div>` +
        `<div class="tv-inb-fig"><div class="tv-inb-lbl">átlag tételenként</div><div class="tv-inb-val"><b>${_fmtNum(avgKg)}</b><small>kg/db</small></div></div>` +
        `<div class="tv-inb-fig"><div class="tv-inb-lbl">még várható</div><div class="tv-inb-val tv-inb-bec"><b>${_fmtNum(becKg)}</b><small>kg</small></div></div>` +
        `<div class="tv-inb-fig"><div class="tv-inb-lbl">várható átlag</div><div class="tv-inb-val tv-inb-bec"><b>${_fmtNum(pendingAvg)}</b><small>kg/db</small></div></div>` +
      `</div>`;
  }

  function _drawReportChartOpening(delay) {
    const chart = document.getElementById("tv-report-chart");
    if (!chart || _reduceMotion()) return;
    if (chart._tvChartTimers) {
      chart._tvChartTimers.forEach(clearTimeout);
    }
    chart._tvChartTimers = [];
    const chartLater = (fn, ms) => {
      const timer = setTimeout(fn, ms || 0);
      chart._tvChartTimers.push(timer);
    };
    const reveal = chart.querySelector(".tv-rc-reveal");
    if (reveal) {
      reveal.classList.remove("is-sweeping");
      reveal.style.animationDelay = `${delay || 0}ms`;
      void reveal.getBoundingClientRect();
      reveal.classList.add("is-sweeping");
    }
    const lines = chart.querySelectorAll(".tv-rc-line, .tv-rc-glow");
    lines.forEach(line => {
      if (line && line.getTotalLength) {
        const len = Math.max(1, line.getTotalLength());
        line.style.transition = "none";
        line.style.strokeDasharray = `${len} ${len}`;
        line.style.strokeDashoffset = String(len);
        chartLater(() => {
          line.style.transition = "stroke-dashoffset 1680ms cubic-bezier(0.19,1,0.22,1)";
          line.style.strokeDashoffset = "0";
        }, delay || 0);
      }
    });
    if (!reveal && !lines.length) {
      return;
    }
    const area = chart.querySelector(".tv-rc-area");
    if (area) {
      area.classList.remove("tv-rc-area-open");
      void area.getBoundingClientRect();
      area.style.setProperty("--tv-i", "7");
      area.style.setProperty("animation-delay", `${(delay || 0) + 220}ms`, "important");
      area.classList.add("tv-rc-area-open");
    }
    _setStagger(chart, ".tv-rc-dot", 9);
    _setStagger(chart, ".tv-rc-val", 9);
  }

  function _runReportOpening(reason) {
    const page = document.getElementById("tv-page-report");
    if (!page || !page.classList.contains("is-active")) return;
    if (!_allowOpening("report", reason)) {
      page.classList.remove("tv-opening-pending");
      return;
    }
    if (page._tvReportPhaseTimers) {
      page._tvReportPhaseTimers.forEach(clearTimeout);
    }
    page._tvReportPhaseTimers = [];
    const token = ++_tvReportOpeningSeq;
    page._tvReportOpeningToken = token;
    // activeMs = meddig marad fent a .tv-opening. A leghosszabb opening-höz kötött
    // animáció (óránkénti diagram pöttyök ~4.4-4.5s 12-13 oszlopnál) BIZTOSAN
    // fusson le, MIELŐTT a class lekerül — különben a végén beugranak. 4100 → 4900.
    _scheduleOpening(page, reason, 4900, () => {
      if (page._tvReportOpeningToken !== token) return;
      const later = (delay, fn) => {
        const timer = setTimeout(() => {
          if (page.classList.contains("is-active") && page._tvReportOpeningToken === token) fn();
        }, _reduceMotion() ? 0 : delay);
        page._tvReportPhaseTimers.push(timer);
      };

      // A --tv-i CSAK fázison BELÜLI (lokális) lépcső. A fázis KEZDŐ késleltetését a
      // CSS per-szelektor base delay-e adja (out-totals 1.02s / out-rates 1.34s /
      // rs-item 1.66s / inbound 2.72s) — ezek egybeesnek a lenti count-up időkkel
      // (1080/1380/1700/2840 ms). KORÁBBI BUG: itt globális kumulatív bázisok
      // (15/20/25/40) voltak, amik RÁADÓDtak a CSS base-re (a régi egy-képletes
      // sémából maradtak). Így az inbound 2.72s+40*0.05 = 4.72s-re csúszott, TÚL a
      // nyíló ablakon → a strip végig opacity:0 maradt, majd a végén beugrott, a
      // konténer-reveal pedig desyncbe került a szám count-uppal. Lokális 0 = szinkron.
      _setStagger(page, ".tv-report-head", 0);
      _setStagger(page, "#tv-gauge-kg", 1);
      _setStagger(page, "#tv-gauge-loadings", 9);
      _setStagger(page, ".tv-out-panel-totals .tv-out-panel-row", 0);
      _setStagger(page, ".tv-fixed-loading-row", 0);
      _setStagger(page, ".tv-rs-item", 0);
      _setStagger(page, ".tv-report-hourly", 33);
      _setStagger(page, ".tv-inb-hero, .tv-inb-fig", 0);

      // A count-up csak azután induljon, hogy a kártya saját tvReportGaugeFocusIn
      // belépője (delay+duration) LEZÁRULT — különben a rAF tween (ease-out, gyors
      // eleje) már javában fut, amíg a kártya még opacity 0→1 közt fakad be, és mire
      // láthatóvá válik, a szám/tű már 80%+-ban kész — úgy tűnik, mintha a mérő nem
      // 0-ról indulna. kg kártya: 0.16s+0.88s=1.04s, loadings: 0.78s+0.84s=1.62s;
      // mindkettőhöz +300ms (user kérés), hogy a belépő láthatóan lezáruljon, mielőtt
      // a számláló elkezd mozogni.
      later(1580, () => _renderGaugesNow({ only: "kg", animate: true, fromZero: true }));
      later(2200, () => _renderGaugesNow({ only: "loadings", animate: true, fromZero: true }));

      page.querySelectorAll(".tv-out-panel-totals .tv-out-panel-row b").forEach((el, i) => {
        _animateTextNumber(el, 1080 + i * 90, 720);
      });
      page.querySelectorAll(".tv-rs-val b").forEach((el, i) => {
        _animateTextNumber(el, 1700 + i * 75, 680);
      });
      page.querySelectorAll(".tv-inb-val b").forEach((el, i) => {
        _animateTextNumber(el, 2840 + i * 70, 650);
      });
      _drawReportChartOpening(2420);
    }, _primeReportGaugesAtZero);
  }

  function _reportSignature(rep) {
    if (!rep) return "";
    const o = rep.out || {};
    const ib = rep.inb || {};
    return JSON.stringify({
      shift: [rep.shift_name || "", rep.shift_range || ""],
      out: [
        o.kg || 0, o.colli || 0, o.parcels || 0, o.pallets || 0, o.loadings || 0,
        o.rows || 0, o.loading_time_min || 0
      ],
      inb: [ib.kg || 0, ib.count || 0, ib.bec_total_kg || 0, ib.bec_count || 0],
      hourly: (o.hourly || []).map(b => [b.label, b.kg || 0, b.active ? 1 : 0, b.current ? 1 : 0]),
      fixed: (rep.fixed_loadings || []).map(r => [
        r.plate || "", r.glabs_id || "", r.ready || 0, r.total || 0,
        r.ramp || "", r.loader || "", r.reason || ""
      ])
    });
  }

  let _lastReportSig = "";
  function _renderReport(rep) {
    const eff = _getEffReport(rep);
    if (!eff) return;
    const sig = (_showTestReport ? "test|" : "live|") + _reportSignature(eff);
    const changed = sig !== _lastReportSig;
    _reportData = eff;
    const o = eff.out || {};
    const ib = eff.inb || {};
    _setText("tv-report-shift", `${eff.shift_name || ""}${eff.shift_range ? " · " + eff.shift_range : ""} · eltelt ${_fmtH(eff.elapsed_h)}`);
    // Élő rakodás-kontextus a riport fejlécében (a 35s riport-idő alatt se vesszen
    // el a "most mi történik" kép).
    const shiftIssuedTrucks = Number(o.issued_trucks ?? o.trucks) || Number(o.trucks) || 0;
    const shiftLoadings = Number(o.loadings) || 0;
    const shiftRows = Number(o.rows) || 0;
    _setHtml("tv-report-live",
      `<span class="tv-rl"><b>${_fmtNum(shiftIssuedTrucks)}</b> kamion kiadva</span>` +
      `<span class="tv-rl-sep"></span>` +
      `<span class="tv-rl"><b>${_fmtNum(shiftLoadings)}</b> rakodás kiadva</span>` +
      `<span class="tv-rl-sep"></span>` +
      `<span class="tv-rl"><b>${_fmtNum(shiftRows)}</b> tétel kiadva</span>`);
    // A kg/óra a nagy gauge-on van — itt a műszak eddigi csúcsórája ad kontextust.
    const pk = (o.hourly || []).reduce(
      (a, b) => (b && b.active && (b.kg || 0) > (a.kg || 0)) ? b : a, { kg: 0 });
    _setText("tv-report-hourly-sub",
      pk.kg > 0 ? `csúcsóra ${pk.label} · ${_fmtNum(pk.kg)} kg` : "");
    _renderGaugesNow({ animate: false, fromZero: false });
    if (!changed && _lastReportSig) return false;
    _lastReportSig = sig;
    _renderReportStats(o);
    _reportChart(o);
    _renderReportInbound(ib);
    return changed;
  }

  /* ──────────────────────────────────────────────────────────────
     OLDALVÁLTÁS — RAKODÁSOK ⇄ MŰSZAK RIPORT
  ────────────────────────────────────────────────────────────── */

  const _PAGES = ["tv-page-ops", "tv-page-report"];
  const _PILLS = [["tv-pg-ops", 0], ["tv-pg-report", 1]];
  const _PAGE_DWELL = 35000;
  let _curPage = 0;
  let _pagePinned = false;
  let _pageTimer = null;
  let _tvReportOpeningSeq = 0;

  function _applyPage(reason) {
    reason = reason || "view";
    const shouldAnimate = reason !== "pin" && reason !== "silent";
    _PAGES.forEach((id, i) => {
      const el = document.getElementById(id);
      if (!el) return;
      const active = i === _curPage;
      el.classList.remove("tv-page-entering", "tv-page-leaving");
      if (el._tvOpenStartTimer) {
        clearTimeout(el._tvOpenStartTimer);
        el._tvOpenStartTimer = null;
      }
      if (!active) {
        if (id === "tv-page-report") {
          _tvReportOpeningSeq++;
          _clearFixedLoadingsTimer(document.getElementById("tv-gauge-parcel"));
        }
        if (el._tvOpenTimer) {
          clearTimeout(el._tvOpenTimer);
          el._tvOpenTimer = null;
        }
        if (el._tvReportPhaseTimers) {
          el._tvReportPhaseTimers.forEach(clearTimeout);
          el._tvReportPhaseTimers = [];
        }
        el.classList.remove("tv-opening-pending", "tv-opening", "tv-open-refresh", "tv-open-view", "tv-open-enter");
      } else if (shouldAnimate) {
        el.classList.add("tv-opening-pending");
      } else {
        el.classList.remove("tv-opening-pending", "tv-opening", "tv-open-refresh", "tv-open-view", "tv-open-enter");
      }
      el.classList.toggle("is-active", active);
      if (el._tvPageAnimTimer) clearTimeout(el._tvPageAnimTimer);
      if (active && id === "tv-page-report" && _reportData) {
        setTimeout(() => _renderFixedLoadings(document.getElementById("tv-gauge-parcel"), _reportData.fixed_loadings || []), 0);
      }
      if (active && shouldAnimate && id !== "tv-page-report") {
        void el.offsetWidth;
        el.classList.add("tv-page-entering");
      } else if (!active && shouldAnimate) {
        el.classList.add("tv-page-leaving");
      }
      el._tvPageAnimTimer = setTimeout(() => {
        el.classList.remove("tv-page-entering", "tv-page-leaving");
        el._tvPageAnimTimer = null;
      }, 950);
    });
    _PILLS.forEach(([id, i]) => {
      const b = document.getElementById(id);
      if (b) b.classList.toggle("is-active", i === _curPage);
    });
    const sw = document.getElementById("tv-page-switch");
    if (sw) sw.classList.toggle("is-pinned", _pagePinned);
    if (_curPage === 1) {
      if (!shouldAnimate) _renderGaugesNow({ animate: false, fromZero: false });
      if (shouldAnimate) setTimeout(() => _runReportOpening(reason), 80);
    }
    if (_curPage === 0 && shouldAnimate && !(reason === "enter" && !_tvHasOpsPayload)) {
      setTimeout(() => _runOpsOpening(reason), 120);
    }
  }

  function _resetPageTimer() {
    if (_pageTimer) clearTimeout(_pageTimer);
    _pageTimer = setTimeout(function tick() {
      if (!_pagePinned) { _curPage = (_curPage + 1) % _PAGES.length; _applyPage("auto"); }
      _pageTimer = setTimeout(tick, _PAGE_DWELL);
    }, _PAGE_DWELL);
  }

  function _setPage(idx, pin) {
    idx = ((idx % _PAGES.length) + _PAGES.length) % _PAGES.length;
    if (typeof pin === "boolean") _pagePinned = pin;
    _curPage = idx;
    _applyPage("view");
    _resetPageTimer();
  }

  function _wirePageSwitch() {
    _PILLS.forEach(([id, i]) => {
      const b = document.getElementById(id);
      if (!b || b._wired) return;
      b._wired = true;
      b.addEventListener("click", () => {
        if (i === _curPage) {            // ugyanaz az oldal → pin ki/be kapcsol
          _pagePinned = !_pagePinned;
          _applyPage("pin");
          _resetPageTimer();
        } else {
          _setPage(i, true);             // másik oldal → odavált és rögzít
        }
      });
    });
  }

  /* ──────────────────────────────────────────────────────────────     CLOCK
  ────────────────────────────────────────────────────────────── */

  const _DAYS   = ["vasárnap","hétfő","kedd","szerda","csütörtök","péntek","szombat"];
  const _MONTHS = ["jan.","feb.","már.","ápr.","máj.","jún.","júl.","aug.","szep.","okt.","nov.","dec."];

  function _tickClock() {
    const now = new Date();
    const hh  = String(now.getHours()).padStart(2,"0");
    const mm  = String(now.getMinutes()).padStart(2,"0");
    const ss  = String(now.getSeconds()).padStart(2,"0");
    _setText("tv-clock", `${hh}:${mm}:${ss}`);
    _setText("tv-date",  `${_DAYS[now.getDay()]}, ${now.getFullYear()}. ${_MONTHS[now.getMonth()]} ${now.getDate()}.`);
    _updateRefreshArc();
    if (hh==="00" && mm==="01" && ss==="00") setTimeout(()=>location.reload(),5000);
  }

  /* ──────────────────────────────────────────────────────────────
     FETCH
  ────────────────────────────────────────────────────────────── */

  const _TV_UPDATE_POLL_MS = 20_000;
  const _TV_UPDATE_CONFIRM_MS = 3_500;
  const _TV_UPDATE_SIG_KEY = "flow-manager-tv-update-signature";
  const _TV_UPDATE_REOPEN_KEY = "flow-manager-tv-update-reopen";
  let _tvVersionSig = "";
  let _tvVersionCandidate = "";
  let _tvVersionCandidateSeen = 0;
  let _tvUpdateConfirmTimer = null;
  let _tvUpdateReloading = false;

  function _sessionGet(key) {
    try { return sessionStorage.getItem(key) || ""; } catch (_) { return ""; }
  }

  function _sessionSet(key, value) {
    try { sessionStorage.setItem(key, value); } catch (_) {}
  }

  function _sessionRemove(key) {
    try { sessionStorage.removeItem(key); } catch (_) {}
  }

  function _fetchOps() {
    fetch("/api/tv/ops")
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (!d) return;
        if (d.status === "loading") return;
        _renderOps(d);
      })
      .catch(()=>{});
  }

  /* ──────────────────────────────────────────────────────────────
     TIMERS
  ────────────────────────────────────────────────────────────── */

  function _reloadForTvUpdate(sig) {
    if (_tvUpdateReloading) return;
    _tvUpdateReloading = true;
    _sessionSet(_TV_UPDATE_SIG_KEY, sig);
    _sessionSet(_TV_UPDATE_REOPEN_KEY, "1");

    const url = new URL(location.href);
    url.searchParams.set("_tvv", String(sig || Date.now()).slice(0, 12));
    setTimeout(() => location.replace(url.toString()), 650);
  }

  function _checkTvUpdate() {
    if (!document.body.classList.contains("view-tv-mode") || _tvUpdateReloading) return;
    fetch(`/api/tv/version?t=${Date.now()}`, { cache: "no-store" })
      .then(r => r.ok ? r.json() : null)
      .then(v => {
        if (!v || !v.signature) return;
        const sig = String(v.signature);
        const reloadedSig = _sessionGet(_TV_UPDATE_SIG_KEY);

        if (!_tvVersionSig) {
          _tvVersionSig = sig;
          if (reloadedSig === sig) _sessionRemove(_TV_UPDATE_SIG_KEY);
          return;
        }

        if (sig === _tvVersionSig) {
          _tvVersionCandidate = "";
          _tvVersionCandidateSeen = 0;
          return;
        }

        if (_tvVersionCandidate !== sig) {
          _tvVersionCandidate = sig;
          _tvVersionCandidateSeen = 1;
          clearTimeout(_tvUpdateConfirmTimer);
          _tvUpdateConfirmTimer = setTimeout(_checkTvUpdate, _TV_UPDATE_CONFIRM_MS);
          return;
        }

        _tvVersionCandidateSeen += 1;
        if (_tvVersionCandidateSeen >= 2) _reloadForTvUpdate(sig);
      })
      .catch(()=>{});
  }

  let _timers    = [];
  let _pagTimer  = null;

  function _clearAll() {
    _timers.forEach(clearInterval);
    _timers = [];
    [_pagTimer, _pageTimer].forEach(t => { if (t) clearTimeout(t); });
    _pagTimer = _pageTimer = null;
    clearTimeout(_tvUpdateConfirmTimer);
    _tvUpdateConfirmTimer = null;
    _clearOpeningTimers();
  }

  function _iv(fn, ms) { const t = setInterval(fn, ms); _timers.push(t); }

  /* ──────────────────────────────────────────────────────────────
     ENTER / EXIT
  ────────────────────────────────────────────────────────────── */

  function _onKey(e) {
    if (e.altKey && (e.key === "t" || e.key === "T")) return;
    if (e.key === "Escape") { document.getElementById("tv-exit-btn")?.click(); return; }
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { _advanceTruckPage(); return; }
    if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      const pages = _getEffPages();
      if (pages > 1) {
        _truckPage = (_truckPage - 1 + pages) % pages;
        _renderTruckPage(true);
      }
      return;
    }
    if (e.key === "t" || e.key === "T") {
      if (e.repeat) return;
      _showTestCard = !_showTestCard;
      _showTestReport = _showTestCard;
      _lastReportSig = "";
      _truckPage = 0;
      _renderTruckPage(false);
      _renderReport(_lastOpsPayload ? _lastOpsPayload.report : null);
      if (_curPage === 1) {
        _runReportOpening("view");
      } else if (_curPage === 0) {
        _runOpsOpening("view");
      }
      return;
    }
    if (e.key === "r" || e.key === "R") {       // RAKODÁSOK ⇄ RIPORT, rögzítve
      _setPage(_curPage === 0 ? 1 : 0, true);
      return;
    }
    if (e.key === "p" || e.key === "P") {       // auto-váltás szünet ki/be
      _pagePinned = !_pagePinned;
      _applyPage("pin");
      _resetPageTimer();
      return;
    }
  }

  function _buildRefreshFab() {
    const fab = document.getElementById("tv-refresh-fab");
    if (!fab || fab.querySelector("svg")) return;
    fab.innerHTML =
      `<svg class="tv-refresh-svg" viewBox="0 0 44 44">` +
        `<circle class="tv-refresh-track" cx="22" cy="22" r="18"/>` +
        `<circle id="tv-refresh-arc" class="tv-refresh-arc" cx="22" cy="22" r="18"/>` +
      `</svg>` +
      `<div id="tv-refresh-min" class="tv-refresh-min"></div>`;
  }

  // A fix 1920×1080 színpadot egységesen a képernyőre skálázza (középre igazítva),
  // így SEMMI nem adaptív — ablakos és teljes képernyős nézetben is azonos arányok.
  function _fitStage() {
    const stage = document.getElementById("tv-stage");
    if (!stage) return;
    const sw = window.innerWidth, sh = window.innerHeight;
    const scale = Math.min(sw / 1920, sh / 1080);
    const tx = Math.round((sw - 1920 * scale) / 2);
    const ty = Math.round((sh - 1080 * scale) / 2);
    stage.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  }

  function _enter() {
    _clearAll();
    _resetOpeningGate();
    const ovl = document.getElementById("tv-overlay");
    if (!ovl) return;
    ovl.style.display = "flex";
    document.body.classList.add("view-tv-mode");
    _fitStage();
    window.addEventListener("resize", _fitStage);

    _buildRefreshFab();
    _applyGradient();
    _truckPage = 0; _truckSig = "";
    _lastRefreshTime = Date.now();
    _tvVersionSig = ""; _tvVersionCandidate = ""; _tvVersionCandidateSeen = 0; _tvUpdateReloading = false;

    // Oldalváltás: RAKODÁSOK alapból, auto-rotáció 35 mp, R/P + pillek vezérlik.
    _curPage = 0; _pagePinned = false;
    _wirePageSwitch();
    _applyPage("enter");
    _resetPageTimer();

    _tickClock();
    _iv(_tickClock, 1000);
    _iv(_applyGradient, 2*60*1000);

    _fetchOps();
    _iv(_fetchOps, 30_000);

    _checkTvUpdate();
    _iv(_checkTvUpdate, _TV_UPDATE_POLL_MS);
    _scheduleTruckPage();   // oldal 5–10 perc között
    document.addEventListener("keydown", _onKey);
  }

  function _exit() {
    _clearAll();
    const ovl = document.getElementById("tv-overlay");
    if (ovl) ovl.style.display = "none";
    document.body.classList.remove("view-tv-mode");
    document.removeEventListener("keydown", _onKey);
    window.removeEventListener("resize", _fitStage);
  }

  window.__tvModeEnter = _enter;
  window.__tvModeExit  = _exit;

  function _wireTvOpenShortcut() {
    if (window.__tvOpenShortcutWired) return;
    window.__tvOpenShortcutWired = true;
    document.addEventListener("keydown", function (e) {
      if (!e.altKey || e.ctrlKey || e.metaKey || (e.key || "").toLowerCase() !== "t") return;
      if (document.body.classList.contains("view-tv-mode")) return;
      const btn = document.getElementById("tv-mode-open-btn");
      if (!btn) return;
      e.preventDefault();
      btn.click();
    });
  }

  function _restoreTvAfterUpdateReload() {
    if (_sessionGet(_TV_UPDATE_REOPEN_KEY) !== "1") return;
    let tries = 0;
    const open = () => {
      if (document.body.classList.contains("view-tv-mode")) {
        _sessionRemove(_TV_UPDATE_REOPEN_KEY);
        return;
      }
      const btn = document.getElementById("tv-mode-open-btn");
      if (btn) {
        _sessionRemove(_TV_UPDATE_REOPEN_KEY);
        btn.click();
        return;
      }
      tries += 1;
      if (tries < 80) setTimeout(open, 250);
    };
    open();
  }

  function _bootTvHooks() {
    _wireTvOpenShortcut();
    _restoreTvAfterUpdateReload();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", _bootTvHooks);
  else _bootTvHooks();

})();
