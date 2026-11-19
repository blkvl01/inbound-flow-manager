// Header "Következő: m:ss" countdown.
// The server (update_countdown) renders the span with data-next-at (epoch ms)
// on its 10 s tick; this ticks the text every second on the client, so the
// seconds run smoothly instead of jumping by 10. Text-only write, on change.
(function () {
  'use strict';
  if (window.__refreshCountdownInit) return;
  window.__refreshCountdownInit = true;

  function tick() {
    if (document.hidden) return;
    var els = document.querySelectorAll('.countdown-text[data-next-at]');
    if (!els.length) return;
    var now = Date.now();
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var at = Number(el.getAttribute('data-next-at'));
      if (!isFinite(at) || at <= 0) continue;
      var rem = Math.max(0, Math.round((at - now) / 1000));
      var m = Math.floor(rem / 60);
      var s = rem % 60;
      var text = 'Következő: ' + m + ':' + (s < 10 ? '0' : '') + s;
      if (el.textContent !== text) el.textContent = text;
    }
  }

  setInterval(tick, 1000);
})();
