(function () {
    'use strict';
    var pending = new Map();
    var sequence = 0;

    function keyOf(id) {
        if (typeof id !== 'string') return '';
        try {
            var parsed = JSON.parse(id);
            var ordered = {};
            Object.keys(parsed).sort().forEach(function (key) { ordered[key] = parsed[key]; });
            return JSON.stringify(ordered);
        } catch (e) { return id; }
    }

    function operationButton(target) {
        return target && target.closest && target.closest('.badge-stored-empty, .badge-stored-active, .truck-store-btn, #refresh-btn');
    }

    // Button text is React-owned: never write textContent (it detaches
    // React's text node). Pending state = class + aria-busy (CSS spinner).
    // The refresh button's `disabled` is owned by the server
    // (update_loading_state -> refresh-btn.disabled); only store buttons are
    // locked here.
    var SAFETY_UNLOCK_MS = 60000;
    var NETWORK_UNLOCK_MS = 30000;

    function paint(entry, button) {
        if (!button || !button.isConnected) return;
        button.dataset.actionPending = '1';
        button.classList.add('action-pending');
        button.setAttribute('aria-busy', 'true');
        entry.buttons.add(button);
        if (entry.refresh) return;
        // Let React/Dash receive the first click before disabling its native target.
        setTimeout(function () { if (pending.get(entry.key) === entry) button.disabled = true; }, 0);
    }

    function release(entry) {
        if (pending.get(entry.key) !== entry) return;
        pending.delete(entry.key);
        clearTimeout(entry.safetyTimer);
        clearTimeout(entry.networkTimer);
        var status = document.getElementById('flow-action-status');
        if (status && (status.dataset.actionToken === entry.token || status.dataset.actionKey === entry.key)) status.remove();
        entry.buttons.forEach(function (button) {
            button.dataset.actionPending = '0';
            button.classList.remove('action-pending');
            button.removeAttribute('aria-busy');
            if (!entry.refresh) button.disabled = false;
        });
    }

    function reportError(message, entry) {
        var status = document.getElementById('flow-action-status');
        if (!status) {
            status = document.createElement('div');
            status.id = 'flow-action-status';
            status.className = 'error-toast';
            status.setAttribute('role', 'alert');
            var text = document.createElement('span');
            text.className = 'flow-action-status-text';
            var close = document.createElement('button');
            close.type = 'button';
            close.className = 'flow-action-status-close';
            close.setAttribute('aria-label', 'Bezárás');
            close.textContent = 'Bezár';
            close.style.cssText = 'flex:none;background:none;border:1px solid currentColor;border-radius:6px;color:inherit;font:inherit;padding:3px 9px;cursor:pointer';
            close.addEventListener('click', function () { status.remove(); });
            status.appendChild(text);
            status.appendChild(close);
            document.body.appendChild(status);
        }
        status.querySelector('.flow-action-status-text').textContent = message;
        status.dataset.actionToken = entry ? entry.token : '';
        status.dataset.actionKey = entry ? entry.key : '';
        status.style.cssText = 'position:fixed;right:20px;bottom:20px;left:auto;max-width:420px;margin:0;z-index:10000;display:flex;align-items:center;gap:12px';
    }

    document.addEventListener('click', function (event) {
        var button = operationButton(event.target);
        if (!button) return;
        var key = keyOf(button.id);
        if (!key) return;
        if (pending.has(key)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        var entry = {
            key: key, refresh: button.id === 'refresh-btn', buttons: new Set(),
            token: (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : Date.now() + '-' + (++sequence)),
            sent: false, requestDone: false
        };
        pending.set(key, entry);
        paint(entry, button);
        // Dash may queue a callback while the main thread is busy. A delayed
        // dispatch has an uncertain outcome, so report it without unlocking it.
        setTimeout(function () {
            if (!entry.sent && pending.get(key) === entry) {
                reportError('A művelet indítása késik. Várj a befejezésre; az állapot ellenőrzéséhez újratöltheted az oldalt.', entry);
            }
        }, 15000);
        // Never leave a button locked forever. A refresh is idempotent; for a
        // store toggle the card has re-rendered with its real state by now.
        entry.safetyTimer = setTimeout(function () {
            if (pending.get(key) !== entry) return;
            var refresh = entry.refresh;
            release(entry);
            if (!refresh) reportError('Az állapot nem igazolt. Nézd meg a kártyát, és ha kell, próbáld újra.', entry);
        }, SAFETY_UNLOCK_MS);
    }, true);

    var originalFetch = window.fetch;
    window.fetch = function (resource, options) {
        var url = typeof resource === 'string' ? resource : resource && resource.url;
        var payload;
        if (url && url.indexOf('/_dash-update-component') >= 0 && options && typeof options.body === 'string') {
            try { payload = JSON.parse(options.body); } catch (e) {}
        }
        var entry;
        if (payload && /store-action\.data|kézi-refresh-store\.data/.test(payload.output || '')) {
            (payload.changedPropIds || []).some(function (prop) {
                var key = keyOf(prop.slice(0, prop.lastIndexOf('.')));
                entry = pending.get(key);
                return !!entry;
            });
        }
        if (!entry) return originalFetch.apply(this, arguments);
        entry.sent = true;
        if (entry.refresh) {
            var clicked = (payload.inputs || []).find(function (input) { return input.id === 'refresh-btn'; });
            entry.manualCounter = clicked && clicked.value;
        }
        var amended = Object.assign({}, options);
        amended.headers = new Headers(options.headers || {});
        amended.headers.set('X-Flow-Action', entry.token);
        var context = this;
        // Same bytes and token on a connection retry: the server replays the first outcome.
        function send() { return originalFetch.call(context, resource, amended); }
        var request = send().catch(function () { return send(); });
        return request.then(function (response) {
            entry.requestDone = true;
            if (!response.ok && response.status !== 204) {
                release(entry);
                reportError('A művelet nem sikerült. Ellenőrizd a friss állapotot, majd próbáld újra.', entry);
            } else if (!entry.refresh) {
                release(entry);
            } else {
                // The request only starts the read; loading-state acknowledges its completion.
                entry.awaitingManual = true;
                var state = window._flowLoadingStatus;
                if (state && state.manual === entry.manualCounter && !state.source_busy) release(entry);
            }
            return response;
        }, function (error) {
            entry.requestDone = true;
            if (entry.refresh) {
                // Refresh is idempotent: unlock right away.
                release(entry);
                reportError('A frissítés indítása nem sikerült. Próbáld újra.', entry);
                throw error;
            }
            // An uncertain outcome must not become an accidental second toggle:
            // stay locked until the poll has re-rendered the real state.
            reportError('A kapcsolat megszakadt. Pár másodperc múlva a kártya a valós állapotot mutatja.', entry);
            entry.networkTimer = setTimeout(function () { release(entry); }, NETWORK_UNLOCK_MS);
            throw error;
        });
    };

    window.addEventListener('flow:loading-state', function (event) {
        var state = event.detail || {};
        var entry = pending.get('refresh-btn');
        if (!entry || !entry.requestDone || !entry.awaitingManual) return;
        if (state.manual === entry.manualCounter && !state.source_busy) release(entry);
    });
    window.addEventListener('flow:plate-focus', function (event) {
        if (window.dash_clientside && window.dash_clientside.set_props) {
            window.dash_clientside.set_props('cards-plate-focus', {data: (event.detail || {}).key || ''});
        }
    });

    // The key survives replacement of a card while its write awaits the shared lock.
    function attachObserver() {
        var area = document.querySelector('.inbound-main-shell');
        if (!area) { setTimeout(attachObserver, 150); return; }
        new MutationObserver(function (records) {
            if (!pending.size) return;
            records.forEach(function (record) {
                record.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    var buttons = Array.from(node.querySelectorAll('.badge-stored-empty, .badge-stored-active, .truck-store-btn'));
                    if (operationButton(node) === node) buttons.unshift(node);
                    buttons.forEach(function (button) {
                        var entry = pending.get(keyOf(button.id));
                        if (entry) paint(entry, button);
                    });
                });
            });
        }).observe(area, {childList: true, subtree: true});
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', attachObserver);
    else attachObserver();
})();
