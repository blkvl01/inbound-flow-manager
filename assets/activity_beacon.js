/* UI-interaction beacon for the developer-menu activity log.
   Delegated click listener: known buttons are reported to /api/log/ui via
   sendBeacon (fire-and-forget, never blocks the UI). Server-side actions
   (Betárolva, megjegyzés, ULD műveletek, settings mentés) are logged on the
   server — this file only covers clientside-only interactions (filters,
   flow switch, refresh, modals). Also closes the settings modal when
   the Developer entry is clicked (that modal is JS-owned, see uld_manager.js). */
(function () {
    'use strict';

    var MAP = {
        'stat-btn-all': 'filter_all',
        'stat-btn-athu': 'filter_athu',
        'stat-btn-drv': 'filter_driver',
        'stat-btn-sched': 'filter_sched',
        'stat-btn-ship': 'filter_ship',
        'stat-btn-betarolt': 'filter_betarolt',
        'flow-inbound': 'flow_inbound',
        'flow-uld': 'flow_uld',
        'refresh-btn': 'refresh_click',
        'summary-btn': 'summary_open',
        'settings-btn': 'settings_open',
        'uld-view-active-btn': 'uld_view_active',
        'uld-view-dispatched-btn': 'uld_view_dispatched',
        'hdr-top-btn': 'scroll_top'
    };

    function send(action) {
        try {
            var payload = JSON.stringify({ action: action });
            if (navigator.sendBeacon) {
                navigator.sendBeacon('/api/log/ui', new Blob([payload], { type: 'application/json' }));
            } else {
                fetch('/api/log/ui', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payload,
                    keepalive: true
                }).catch(function () {});
            }
        } catch (e) { /* logging must never break the UI */ }
    }

    document.addEventListener('click', function (ev) {
        var el = ev.target && ev.target.closest ? ev.target.closest('button[id]') : null;
        if (!el) return;

        if (el.id === 'dev-open-btn') {
            // The settings modal is JS-owned (uld_manager.js); hide it so the
            // Dash-managed dev overlays are not stacked under/over it.
            var settings = document.getElementById('settings-overlay');
            if (settings) {
                settings.style.display = 'none';
                settings.setAttribute('aria-hidden', 'true');
            }
            return;
        }

        var action = MAP[el.id];
        if (action) send(action);
    }, true);
})();
