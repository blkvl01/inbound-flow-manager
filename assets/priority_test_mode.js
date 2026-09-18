(function () {
  "use strict";

  if (window.__priorityTestModeWired) return;
  window.__priorityTestModeWired = true;

  var active = false;

  function isEditable(target) {
    if (!target || !target.closest) return false;
    return !!target.closest("input, textarea, select, [contenteditable='true'], [role='textbox']");
  }

  function isPrioritySurface() {
    var body = document.body;
    return body &&
      !body.classList.contains("view-tv-mode") &&
      !body.classList.contains("view-kpi-mode") &&
      !body.classList.contains("flow-uld-mode") &&
      !body.classList.contains("flow-outbound-mode");
  }

  function publish(attempt) {
    attempt = attempt || 0;
    document.body.classList.toggle("priority-test-mode", active);
    var overlay = document.getElementById("overlay");
    if (active && overlay) overlay.style.display = "none";
    if (!window.dash_clientside || typeof window.dash_clientside.set_props !== "function") {
      if (attempt < 40) {
        window.setTimeout(function () { publish(attempt + 1); }, 100);
      }
      return;
    }
    window.dash_clientside.set_props("priority-test-mode", { data: active });
    window.dash_clientside.set_props("active-filter", { data: "all" });
  }

  document.addEventListener("keydown", function (event) {
    if (event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
    if ((event.key || "").toLowerCase() !== "t") return;
    if (isEditable(event.target) || !isPrioritySurface()) return;
    event.preventDefault();
    active = !active;
    publish();
  }, true);

})();
