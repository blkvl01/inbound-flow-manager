/* Header composition bar — hover plate at the END of the bar.
 * On hovering a status segment, fills #kpi-comp-plate with that status's % + kg
 * (parsed from the segment's data-tooltip) and reveals it. Hidden when the pointer
 * leaves the whole composition card. Pure read-only; no server roundtrip. */
(function () {
  "use strict";

  var COLORS = {
    felveve: "#4a9eff", ertesito: "#ff6b35", megerkezett: "#00d4aa", szemles: "#ef4444"
  };

  function plate() { return document.getElementById("kpi-comp-plate"); }

  function keyOf(seg) {
    var m = String(seg.className || "").match(/kpi-chart-(felveve|ertesito|megerkezett|szemles)\b/);
    return m ? m[1] : "";
  }

  function fill(seg) {
    var p = plate();
    if (!p) return;
    var tip = seg.getAttribute("data-tooltip") || "";
    // Format: "Felvéve: 1 234 kg (45%) | ULD … / PLT …"
    var m = /^\s*(.+?):\s*(.+?)\s*\((\d+)%\)/.exec(tip);
    if (!m) { p.classList.remove("is-on"); return; }
    var key = keyOf(seg);
    p.style.setProperty("--plate-accent", COLORS[key] || "#cbd5e1");
    p.innerHTML =
      '<span class="kpi-comp-plate-dot" aria-hidden="true"></span>' +
      '<b class="kpi-comp-plate-pct">' + m[3] + '%</b>' +
      '<span class="kpi-comp-plate-kg">' + m[2] + '</span>';
    p.classList.add("is-on");
  }

  function hide() {
    var p = plate();
    if (p) p.classList.remove("is-on");
  }

  document.addEventListener("pointerover", function (e) {
    var seg = e.target.closest && e.target.closest(".kpi-chart-segment");
    if (seg) fill(seg);
  });

  document.addEventListener("pointerout", function (e) {
    var card = e.target.closest && e.target.closest(".kpi-composition-card");
    if (!card) return;
    // Still inside the card (moving between segments) → keep the plate.
    if (e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".kpi-composition-card")) return;
    hide();
  });
})();
