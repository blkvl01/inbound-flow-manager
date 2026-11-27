/* Poll gate: replaces the self-ticking dcc.Interval "poll" (now disabled).

   Every 5 s one cheap GET /api/poll-gate returns a signature of everything the
   Input("poll") callbacks react to (data_version, loading / refresh state,
   updater phase). Only when it changes does this bump poll.n_intervals, so
   the callbacks run exactly when they would have produced an update. A 60 s
   safety tick keeps the old behaviour as a floor if anything is ever missed.

   Before: 8 callbacks x every 5 s through dash-renderer, almost all answering
   no_update, cost 50-180 ms main-thread bursts (stuttered expand/scroll). */
(function () {
  "use strict";

  if (window.__flowPollGateWired) return;
  window.__flowPollGateWired = true;

  var CHECK_MS = 5000;
  var SAFETY_MS = 60000;
  var lastSig = null;
  var lastFire = 0;
  var ticks = 0;
  var inFlight = false;

  function fire() {
    var dc = window.dash_clientside;
    if (!dc || typeof dc.set_props !== "function") return false;
    ticks += 1;
    lastFire = Date.now();
    try {
      dc.set_props("poll", { n_intervals: ticks });
      return true;
    } catch (e) {
      return false;
    }
  }

  function check() {
    if (inFlight) return;
    inFlight = true;
    // A hung request must never leave inFlight set (that would stop polling).
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var guard = setTimeout(function () {
      if (ctrl) ctrl.abort();
      inFlight = false;
    }, 8000);
    fetch("/api/poll-gate", { cache: "no-store", credentials: "same-origin", signal: ctrl ? ctrl.signal : undefined })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        clearTimeout(guard);
        inFlight = false;
        var sig = payload && payload.sig;
        var due = Date.now() - lastFire >= SAFETY_MS;
        if (!sig) {
          // Server busy / restarting: keep the safety floor only.
          if (due) fire();
          return;
        }
        if (sig !== lastSig || due) {
          if (fire()) lastSig = sig;
        }
      })
      .catch(function () {
        clearTimeout(guard);
        inFlight = false;
        if (Date.now() - lastFire >= SAFETY_MS) fire();
      });
  }

  setInterval(check, CHECK_MS);
  // First check as soon as Dash can accept set_props.
  (function first(attempt) {
    if (window.dash_clientside && typeof window.dash_clientside.set_props === "function") {
      check();
    } else if (attempt < 100) {
      setTimeout(function () { first(attempt + 1); }, 100);
    }
  })(0);
})();
