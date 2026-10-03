(function () {
    'use strict';
    var states = {};
    var host = null;
    var controlsObserver = null;
    var mountObserver = null;

    function bindHost() {
        var next = document.getElementById('refresh-info');
        if (next === host && host && host.isConnected) return;
        if (controlsObserver) controlsObserver.disconnect();
        if (mountObserver) mountObserver.disconnect();
        host = next;
        if (!window.MutationObserver) return;
        if (!host) {
            // Dash mounts asynchronously. This temporary observer only looks
            // for the host after element additions, and is released on mount.
            mountObserver = new MutationObserver(function (records) {
                var added = records.some(function (record) {
                    return Array.prototype.some.call(record.addedNodes, function (node) { return node.nodeType === 1; });
                });
                if (added && document.getElementById('refresh-info')) { bindHost(); paint(); }
            });
            mountObserver.observe(document.documentElement, { childList: true, subtree: true });
            return;
        }
        controlsObserver = new MutationObserver(function (records) {
            var changed = records.some(function (record) {
                if (record.type === 'attributes') return true;
                return Array.prototype.some.call(record.addedNodes, function (node) { return node.nodeType === 1; }) ||
                    Array.prototype.some.call(record.removedNodes, function (node) { return node.nodeType === 1; });
            });
            // Ignore feedback/countup text written in existing elements.
            if (changed) paint();
        });
        controlsObserver.observe(host, {
            childList: true, subtree: true, attributes: true,
            attributeFilter: ['data-source-kind', 'data-source-feedback', 'data-source-action']
        });
        mountObserver = new MutationObserver(function () {
            if (!host || !host.isConnected) { bindHost(); paint(); }
        });
        // Direct-child observation catches replacement of the host, header or
        // app shell. Unrelated KPI descendants never reach this observer.
        for (var ancestor = host.parentElement; ancestor; ancestor = ancestor.parentElement) {
            mountObserver.observe(ancestor, { childList: true });
        }
    }
    function paint() {
        if (!host || !host.isConnected) bindHost();
        if (!host) return;
        host.querySelectorAll('[data-source-kind]').forEach(function (button) {
            var state = states[button.dataset.sourceKind];
            button.disabled = !!(state && state.busy);
            button.setAttribute('aria-busy', button.disabled ? 'true' : 'false');
        });
        host.querySelectorAll('[data-source-feedback]').forEach(function (node) {
            var state = states[node.dataset.sourceFeedback];
            var message = state ? state.message : '';
            if (node.textContent !== message) node.textContent = message;
            node.classList.toggle('is-error', !!(state && state.error));
        });
    }
    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-source-kind]');
        if (!button) return;
        if (button.dataset.sourceAction === 'settings') {
            var settings = document.getElementById('settings-btn');
            if (settings) settings.click();
            return;
        }
        var kind = button.dataset.sourceKind;
        var key = kind;
        if (states[key] && states[key].busy) return;
        var state = { busy: true, message: '', error: false };
        states[key] = state;
        paint();
        fetch('/api/sources/open', {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind: kind })
        }).then(function (response) {
            return response.text().then(function (body) {
                try { return JSON.parse(body); }
                catch (error) {
                    console.error('Source open response could not be read', response.status, error);
                    return { ok: false, error: 'A megnyitás nem sikerült.' };
                }
            });
        }).then(function (result) {
            state.message = result.message || result.error || 'A megnyitás nem sikerült.';
            state.error = !result.ok;
        }).catch(function () {
            state.message = 'A Flow Manager nem érhető el. Próbáld újra.';
            state.error = true;
        }).finally(function () {
            paint();
            setTimeout(function () {
                if (states[key] !== state) return;
                state.busy = false; paint();
            }, 10000);
            setTimeout(function () {
                if (states[key] !== state) return;
                state.message = ''; paint();
            }, 180000);
        });
    });
    bindHost();
    paint();
}());
