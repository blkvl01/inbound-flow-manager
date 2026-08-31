/* Inbound "Beérkezhető" card — Ctrl+click a status sub-card to open a small, movable
 * panel listing the AWB prefixes (first 3 digits) in that status, with db + kg + a
 * proportional bar. Data comes from window.__becPrefixes (set by a clientside callback
 * from the kpi-bec-prefixes-store), so it renders instantly with no server roundtrip.
 */
(function () {
  "use strict";

  var STATUS = {
    felveve:     { label: "Felvéve",     color: "#4a9eff", rgb: "74,158,255" },
    ertesito:    { label: "Értesítő",    color: "#ff6b35", rgb: "255,107,53" },
    megerkezett: { label: "Megérkezett", color: "#00d4aa", rgb: "0,212,170" },
    szemles:     { label: "Szemlés",     color: "#ef4444", rgb: "239,68,68" }
  };

  var panel = null;
  var dragOff = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtKg(n) {
    return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " kg";
  }

  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement("div");
    panel.className = "bec-prefix-panel";
    document.body.appendChild(panel);

    panel.addEventListener("pointerdown", function (e) {
      var head = e.target.closest && e.target.closest(".bec-prefix-head");
      if (!head || (e.target.closest && e.target.closest(".bec-prefix-close"))) return;
      var r = panel.getBoundingClientRect();
      dragOff = { x: e.clientX - r.left, y: e.clientY - r.top };
      panel.classList.add("is-dragging");
      try { panel.setPointerCapture(e.pointerId); } catch (x) {}
    });
    panel.addEventListener("pointermove", function (e) {
      if (!dragOff) return;
      var x = Math.max(6, Math.min(window.innerWidth - panel.offsetWidth - 6, e.clientX - dragOff.x));
      var y = Math.max(6, Math.min(window.innerHeight - panel.offsetHeight - 6, e.clientY - dragOff.y));
      panel.style.left = x + "px";
      panel.style.top = y + "px";
      panel.style.right = "auto";
    });
    function endDrag() { dragOff = null; panel.classList.remove("is-dragging"); }
    panel.addEventListener("pointerup", endDrag);
    panel.addEventListener("pointercancel", endDrag);
    panel.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".bec-prefix-close")) { hide(); return; }
      var row = e.target.closest && e.target.closest(".bec-prefix-row");
      if (row) {
        var item = row.closest(".bec-prefix-item");
        if (item) item.classList.toggle("is-expanded");
      }
    });
    return panel;
  }

  function hide() { if (panel) panel.classList.remove("is-open"); }

  function open(status, anchor) {
    var meta = STATUS[status];
    if (!meta) return;
    var data = (window.__becPrefixes && window.__becPrefixes[status]) || [];
    var p = ensurePanel();
    p.style.setProperty("--bec-accent", meta.color);
    p.style.setProperty("--bec-accent-rgb", meta.rgb);

    var maxKg = data.reduce(function (m, r) { return Math.max(m, Number(r.kg) || 0); }, 0) || 1;
    var total = data.reduce(function (a, r) { return a + (Number(r.kg) || 0); }, 0);
    var rows = data.map(function (r) {
      var w = Math.max(2, (Number(r.kg) || 0) / maxKg * 100);
      var uld = Number(r.uld) || 0, plt = Number(r.plt) || 0, tot = (uld + plt) || 1;
      var uldKg = Number(r.uld_kg) || 0, pltKg = Number(r.plt_kg) || 0, totKg = uldKg + pltKg;
      var uw = totKg > 0 ? uldKg / totKg * 100 : uld / tot * 100;
      var pw = totKg > 0 ? pltKg / totKg * 100 : plt / tot * 100;
      return '<div class="bec-prefix-item">' +
        '<button type="button" class="bec-prefix-row">' +
          '<span class="bec-prefix-code">' + esc(r.prefix) + '</span>' +
          '<span class="bec-prefix-bar"><i style="width:' + w.toFixed(1) + '%"></i></span>' +
          '<span class="bec-prefix-count">' + (r.count || 0) + ' db</span>' +
          '<span class="bec-prefix-kg">' + fmtKg(r.kg) + '</span>' +
          '<span class="bec-prefix-chev" aria-hidden="true">▸</span>' +
        '</button>' +
        '<div class="bec-prefix-detail">' +
          '<span class="bec-prefix-split">' +
            (uld ? '<i class="uld" style="width:' + uw.toFixed(1) + '%"></i>' : '') +
            (plt ? '<i class="plt" style="width:' + pw.toFixed(1) + '%"></i>' : '') +
          '</span>' +
          '<span class="bec-prefix-tag uld">ULD <b>' + uld + '</b> · ' + fmtKg(uldKg) + '</span>' +
          '<span class="bec-prefix-tag plt">PLT <b>' + plt + '</b> · ' + fmtKg(pltKg) + '</span>' +
        '</div>' +
      '</div>';
    }).join("");
    if (!rows) rows = '<div class="bec-prefix-empty">Nincs tétel ebben a státuszban.</div>';

    p.innerHTML =
      '<div class="bec-prefix-head">' +
        '<span class="bec-prefix-dot" aria-hidden="true"></span>' +
        '<span class="bec-prefix-title">' + esc(meta.label) + ' — prefix bontás</span>' +
        '<span class="bec-prefix-sub">' + data.length + ' prefix · ' + fmtKg(total) + '</span>' +
        '<button type="button" class="bec-prefix-close" aria-label="Bezárás">✕</button>' +
      '</div>' +
      '<div class="bec-prefix-body">' + rows + '</div>';

    if (!p.classList.contains("is-open") || !p.style.left) {
      var ar = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
      var x = ar ? Math.min(window.innerWidth - 320, ar.right + 12) : (window.innerWidth - 340);
      var y = ar ? Math.max(8, ar.top) : 90;
      p.style.left = Math.max(6, x) + "px";
      p.style.top = y + "px";
      p.style.right = "auto";
    }
    p.classList.add("is-open");
  }

  // Capture phase + stopImmediatePropagation so the Ctrl+click does NOT also trigger
  // the sub-card's own Dash n_clicks (ULD/PLT reel toggle).
  document.addEventListener("click", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    var card = e.target.closest && e.target.closest(".kpi-sub-card");
    if (!card) return;
    var m = String(card.className || "").match(/kpi-sub-(felveve|ertesito|megerkezett|szemles)\b/);
    if (!m) return;
    e.preventDefault();
    e.stopPropagation();
    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
    open(m[1], card);
  }, true);

  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
})();
