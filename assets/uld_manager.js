(function () {
    'use strict';

    if (window.__uldManagerInitialized) return;
    window.__uldManagerInitialized = true;
    window.__uldCreateInFlight = false;

    // ── toast ────────────────────────────────────────────────────────────────

    function _uldToast(msg, type) {
        var container = document.getElementById('uld-toast');
        if (!container) return;
        var item = document.createElement('div');
        item.className = 'uld-toast-item uld-toast-' + (type || 'ok');
        item.textContent = msg;
        container.appendChild(item);
        setTimeout(function () {
            item.classList.add('uld-toast-fade');
            setTimeout(function () { if (item.parentNode) item.parentNode.removeChild(item); }, 320);
        }, 2400);
    }

    // ── Dash store trigger / set_props ──────────────────────────────────────

    function setStore(id, value) {
        try {
            if (window.dash_clientside && typeof window.dash_clientside.set_props === 'function') {
                window.dash_clientside.set_props(id, { data: value });
                return true;
            }
        } catch (e) { /* noop */ }
        return false;
    }

    var _refreshTimer = null;
    function bumpTriggerSoon(delay) {
        if (_refreshTimer) clearTimeout(_refreshTimer);
        _refreshTimer = setTimeout(function () {
            var ts = Date.now();
            if (!setStore('uld-trigger-store', ts)) {
                // dash_clientside not ready yet — retry with backoff
                var attempts = 0;
                var retry = [300, 600, 1200];
                function tryAgain() {
                    if (setStore('uld-trigger-store', Date.now())) return;
                    if (attempts < retry.length) setTimeout(tryAgain, retry[attempts++]);
                }
                setTimeout(tryAgain, retry[attempts++]);
            }
            // Safety-net: clean up optimistic duplicates after Dash/React renders
            setTimeout(cleanupOptimisticDuplicates, 450);
            setTimeout(cleanupOptimisticDuplicates, 950);
        }, delay == null ? 900 : delay);
    }
    function bumpTrigger() { bumpTriggerSoon(0); }

    var apiQueue = Promise.resolve();
    var uldUiState = {
        mode: 'idle',
        lockedStackek: new Set(),
        pendingCreates: 0
    };
    var createWithUldsInFlight = {
        key: '',
        opId: ''
    };
    var deletedStackSuppressions = {
        pending: new Set(),
        recent: new Map(),
        observer: null,
        observerTarget: null
    };

    function cleanupTörlésdStackSuppressions() {
        var now = Date.now();
        deletedStackSuppressions.recent.forEach(function (expiresAt, stackId) {
            if (expiresAt <= now) deletedStackSuppressions.recent.delete(stackId);
        });
    }

    function isStackTörlésSuppressed(stackId) {
        if (!stackId) return false;
        cleanupTörlésdStackSuppressions();
        return deletedStackSuppressions.pending.has(stackId) || deletedStackSuppressions.recent.has(stackId);
    }

    function removeSuppressedStackCards(stackId) {
        if (!stackId) return;
        var removed = false;
        document.querySelectorAll('.uld-stack[data-stack-id="' + cssEsc(stackId) + '"]').forEach(function (card) {
            if (card.parentNode) {
                card.parentNode.removeChild(card);
                removed = true;
            }
        });
        uldUiState.lockedStackek.delete(stackId);
        if (typeof selectedStackek !== 'undefined') selectedStackek.delete(stackId);
        if (removed) reconcileSelection();
    }

    function sweepSuppressedStackCards() {
        cleanupTörlésdStackSuppressions();
        deletedStackSuppressions.pending.forEach(removeSuppressedStackCards);
        deletedStackSuppressions.recent.forEach(function (_expiresAt, stackId) {
            removeSuppressedStackCards(stackId);
        });
    }

    function ensureTörlésdStackObserver() {
        var grid = document.getElementById('uld-stacks-grid');
        if (!grid || typeof MutationObserver !== 'function') return;
        if (deletedStackSuppressions.observerTarget === grid) return;
        if (deletedStackSuppressions.observer) {
            deletedStackSuppressions.observer.disconnect();
        }
        deletedStackSuppressions.observer = new MutationObserver(function () {
            sweepSuppressedStackCards();
        });
        deletedStackSuppressions.observer.observe(grid, { childList: true });
        deletedStackSuppressions.observerTarget = grid;
    }

    function markStackTörlésPending(stackId) {
        if (!stackId) return;
        deletedStackSuppressions.pending.add(stackId);
        ensureTörlésdStackObserver();
        removeSuppressedStackCards(stackId);
    }

    function markStackTörlésSucceeded(stackId) {
        if (!stackId) return;
        deletedStackSuppressions.pending.delete(stackId);
        deletedStackSuppressions.recent.set(stackId, Date.now() + 10000);
        ensureTörlésdStackObserver();
        removeSuppressedStackCards(stackId);
        setTimeout(sweepSuppressedStackCards, 250);
        setTimeout(sweepSuppressedStackCards, 1200);
        setTimeout(sweepSuppressedStackCards, 4000);
        setTimeout(cleanupTörlésdStackSuppressions, 10500);
    }

    function clearStackTörlésSuppression(stackId) {
        if (!stackId) return;
        deletedStackSuppressions.pending.delete(stackId);
        deletedStackSuppressions.recent.delete(stackId);
    }

    function guardActionButton(btn, busyLabel) {
        if (!btn || btn.dataset.uldActionBusy === '1') return false;
        btn.dataset.uldActionBusy = '1';
        btn.disabled = true;
        btn.classList.add('uld-op-busy');
        // Only swap the label when a busy label is supplied. Buttons with rich
        // inner markup (e.g. target chips = name span + count span, or × buttons)
        // pass null here; touching textContent would flatten their two spans into
        // one text node ("Menzies2"), which renderSelectionTargets won't repaint
        // when the selection is unchanged (sig stays equal). So leave them alone.
        if (busyLabel) {
            if (!btn.dataset.uldIdleLabel) btn.dataset.uldIdleLabel = btn.textContent || '';
            btn.dataset.uldBusyLabelApplied = '1';
            btn.textContent = busyLabel;
        }
        return true;
    }

    function releaseActionButton(btn) {
        if (!btn) return;
        btn.dataset.uldActionBusy = '0';
        btn.disabled = false;
        btn.classList.remove('uld-op-busy');
        if (btn.dataset.uldBusyLabelApplied === '1' && btn.dataset.uldIdleLabel) {
            btn.textContent = btn.dataset.uldIdleLabel;
            btn.dataset.uldBusyLabelApplied = '0';
        }
    }

    function setUldMode(mode) {
        uldUiState.mode = mode || 'idle';
        if (!document.body) return;
        ['idle', 'dragging', 'syncing', 'conflict', 'refreshing'].forEach(function (name) {
            document.body.classList.toggle('uld-state-' + name, uldUiState.mode === name);
        });
    }

    function stackIdOf(stackEl) {
        return stackEl && stackEl.dataset ? (stackEl.dataset.stackId || '') : '';
    }

    function stackLocked(stackEl) {
        var id = stackIdOf(stackEl);
        return !!(id && uldUiState.lockedStackek.has(id));
    }

    function lockStackEls(stackEls) {
        (stackEls || []).forEach(function (stack) {
            var id = stackIdOf(stack);
            if (!id) return;
            uldUiState.lockedStackek.add(id);
            stack.classList.add('uld-stack-op-locked', 'uld-stack-syncing');
        });
        setUldMode(uldUiState.lockedStackek.size ? 'syncing' : 'idle');
    }

    function unlockStackEls(stackEls) {
        (stackEls || []).forEach(function (stack) {
            var id = stackIdOf(stack);
            if (id) uldUiState.lockedStackek.delete(id);
            stack.classList.remove('uld-stack-op-locked', 'uld-stack-syncing');
        });
        setUldMode(uldUiState.lockedStackek.size ? 'syncing' : 'idle');
    }

    function enqueueApi(task) {
        var run = apiQueue.then(task, task);
        apiQueue = run.catch(function () {});
        return run;
    }

    function setCreateButtonBusy(btn, busy) {
        btn = btn || document.getElementById('uld-new-stack-btn');
        if (!btn) return;
        if (!btn.dataset.uldIdleLabel) btn.dataset.uldIdleLabel = btn.textContent || '+ Új stack';
        btn.disabled = !!busy;
        btn.dataset.uldBusy = busy ? '1' : '0';
        btn.classList.toggle('uld-op-busy', !!busy);
        btn.textContent = busy ? 'Mentés...' : btn.dataset.uldIdleLabel;
    }

    function stackCreateBusy() {
        return !!window.__uldCreateInFlight || uldUiState.pendingCreates > 0 || !!createWithUldsInFlight.key;
    }

    function setCreateControlsBusy(busy, activeBtn) {
        document.querySelectorAll('#uld-new-stack-btn, .uld-selection-newstack-btn').forEach(function (btn) {
            if (!btn || btn === activeBtn) return;
            if (busy) {
                btn.dataset.uldCreateLocked = '1';
                btn.disabled = true;
                btn.classList.add('uld-op-busy');
            } else if (btn.dataset.uldCreateLocked === '1') {
                btn.dataset.uldCreateLocked = '0';
                btn.disabled = false;
                btn.classList.remove('uld-op-busy');
            }
        });
    }

    function captureUldDom() {
        var grid = document.getElementById('uld-stacks-grid');
        var list = document.querySelector('.uld-list');
        return {
            grid: grid ? grid.innerHTML : null,
            list: list ? list.innerHTML : null
        };
    }

    function restoreUldDom(snapshot) {
        if (!snapshot) return;
        var grid = document.getElementById('uld-stacks-grid');
        var list = document.querySelector('.uld-list');
        if (grid && snapshot.grid !== null) grid.innerHTML = snapshot.grid;
        if (list && snapshot.list !== null) list.innerHTML = snapshot.list;
        reconcileSelection();
    }

    function stackRevision(stackEl) {
        var value = stackEl && stackEl.dataset ? parseInt(stackEl.dataset.stackRevision || '0', 10) : 0;
        return Number.isFinite(value) ? value : 0;
    }

    function bumpStackRevision(stackEl) {
        if (!stackEl || !stackEl.dataset) return;
        stackEl.dataset.stackRevision = String(stackRevision(stackEl) + 1);
    }

    function applyRollback(opts) {
        if (opts && typeof opts.rollback === 'function') {
            try { opts.rollback(); } catch (err) { console.warn('[ULD] rollback error:', err); }
        }
    }

    // ── Flask API wrapper ───────────────────────────────────────────────────

    // ── countdown tick ──────────────────────────────────────────────────────

    function uldApiCall(payload, onSuccess, opts) {
        opts = opts || {};
        return enqueueApi(function () {
            var stableClientOpId = newClientOpId('mutation');
            var conflictRetries = Math.max(0, Number(opts.retryOnConflict || 0));
            var transientRetries = Math.max(0, Number(opts.retryTransient || 0));

            function waitBeforeRetry(ms) {
                return new Promise(function (resolve) { setTimeout(resolve, ms); });
            }

            function attempt(conflictsLeft, transientsLeft) {
                var requestPayload = (typeof payload === 'function') ? payload() : Object.assign({}, payload || {});
                requestPayload = requestPayload || {};
                if (!requestPayload.client_op_id) requestPayload.client_op_id = stableClientOpId;
                return fetch('/api/uld/stack', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(requestPayload),
                })
                .then(function (r) {
                    return r.json().catch(function () { return {}; }).then(function (data) {
                        data._status = r.status;
                        return data;
                    });
                })
                .then(function (data) {
                    if (data && data.ok) {
                        // Dash/React owns the stack DOM.  Direct reconciliation is
                        // opt-in only for legacy non-structural flows; normal ULD
                        // mutations render from the authoritative backend state.
                        if (opts.applyState === true && data.stack_state) {
                            applyServerStackState(data.stack_state);
                        }
                        if (typeof onSuccess === 'function') onSuccess(data);
                        // A stack-tagság változása a jobb oldali ULD-listát is
                        // érinti. Ezt még a stack trigger előtt jelezzük, így a
                        // Dash callback nem választhatja a gyors, csak stackeket
                        // újrarajzoló ágat. Korábban emiatt csak a következő poll
                        // vagy fájlfigyelő-frissítés után jelent meg a színezés.
                        if (opts.listDirty === true) {
                            setStore('uld-list-dirty-store', Date.now());
                        }
                        if (opts.refresh !== false) bumpTriggerSoon(opts.refreshDelay);
                        if (opts.flip) scheduleFlip(opts.flip);
                        return data;
                    }

                    if (data && data.conflict && conflictsLeft > 0 && data.stack_state) {
                        // Updating revision data is safe: no React-owned child node is
                        // inserted/removed.  The payload factory then rebuilds the
                        // precondition against the fresh server revision.
                        applyServerRevisionState(data.stack_state);
                        setUldMode('conflict');
                        return waitBeforeRetry(90).then(function () {
                            return attempt(conflictsLeft - 1, transientsLeft);
                        });
                    }
                    if (data && data._status === 423 && !data.locked && transientsLeft > 0) {
                        return waitBeforeRetry(180).then(function () {
                            return attempt(conflictsLeft, transientsLeft - 1);
                        });
                    }

                    setUldMode(data && data.conflict ? 'conflict' : 'idle');
                    applyRollback(opts);
                    if (typeof opts.onError === 'function' && opts.onError(data) === true) {
                        bumpTrigger();
                        return data;
                    }
                    var msg = data && data.conflict
                        ? 'A stack közben megváltozott - az aktuális állapot betöltve'
                        : ((data && data.error) ? data.error : 'A művelet sikertelen');
                    _uldToast(msg, 'error');
                    bumpTrigger();
                    return data;
                })
                .catch(function (err) {
                    if (transientsLeft > 0) {
                        return waitBeforeRetry(220).then(function () {
                            return attempt(conflictsLeft, transientsLeft - 1);
                        });
                    }
                    console.warn('[ULD] API error:', err);
                    setUldMode('idle');
                    applyRollback(opts);
                    _uldToast('Kapcsolati hiba - az aktuális állapot újratöltődik', 'error');
                    bumpTrigger();
                });
            }

            return attempt(conflictRetries, transientRetries).finally(function () {
                if (typeof opts.done === 'function') opts.done();
            });
        });
    }

    function updateCountdowns() {
        var now = Date.now();
        document.querySelectorAll('.uld-countdown[data-expiry]').forEach(function (el) {
            var expiry = new Date(el.dataset.expiry).getTime();
            var diff   = (expiry - now) / 1000;
            var abs    = Math.abs(diff);
            var totalM = Math.max(0, Math.ceil(abs / 60));
            var h      = Math.floor(totalM / 60);
            var m      = totalM % 60;
            var text   = h > 0 ? (h + 'ó ' + m + 'p') : (m + 'p');

            var cdTime  = el.querySelector('.uld-cd-time');
            var cdLabel = el.querySelector('.uld-cd-label');
            if (cdTime)  cdTime.textContent  = text;
            if (cdLabel) cdLabel.textContent = diff >= 0 ? 'Hátra' : 'Lejárt';

            var status = diff <= 0        ? 'expired'
                       : diff <= 6*3600  ? 'critical'
                       : diff <= 12*3600 ? 'warning'
                                         : 'ok';
            el.className = el.className.replace(/uld-cd-\w+/g, '').trim();
            el.classList.add('uld-countdown', 'uld-cd-' + status);
        });
    }
    setInterval(updateCountdowns, 30000);

// ── drag & drop + multi-select ─────────────────────────────────────────

    var selectedUlds = new Set();
    var selectedStackek = new Set();
    var dispatchedSelected = new Set();   // stack ids selected in the Kiküldött nézet
    var dragState = null;
    var lastDragVisualAt = 0;

    var pendingManualStack = null;
    var kéziGhaOptions = ['Menzies', 'AS Cargo', 'Celebi'];

    function kéziField(id) {
        return document.getElementById(id);
    }

    function pad2(value) {
        return String(value).padStart(2, '0');
    }

    function localDateTimeValue(date) {
        return date.getFullYear() + '-' + pad2(date.getMonth() + 1) + '-' + pad2(date.getDate()) +
            'T' + pad2(date.getHours()) + ':' + pad2(date.getMinutes());
    }

    function setManualTimeFromDate(date) {
        var safeDate = date && !Number.isNaN(date.getTime()) ? date : new Date();
        var now = new Date();
        if (safeDate.getTime() > now.getTime()) safeDate = now;
        var picker = kéziField('uld-manual-datetime');
        if (picker) {
            var max = localDateTimeValue(now);
            picker.max = max;
            picker.value = localDateTimeValue(safeDate);
        }
    }

    function clearManualDuplicate() {
        var box = kéziField('uld-manual-duplicate');
        if (!box) return;
        box.classList.remove('uld-manual-duplicate-show', 'uld-manual-error-show');
        box.innerHTML = '';
        ['uld-manual-stack', 'uld-manual-gha', 'uld-manual-uld'].forEach(function (id) {
            var field = kéziField(id);
            if (field) field.classList.remove('uld-manual-input-error');
        });
        document.querySelectorAll('.uld-manual-field-error').forEach(function (el) { el.remove(); });
    }

    function showManualRequiredError(fieldIds) {
        var box = kéziField('uld-manual-duplicate');
        var labels = {
            'uld-manual-stack': 'Stack',
            'uld-manual-gha': 'GHA',
            'uld-manual-uld': 'ULD'
        };
        var missingNames = [];
        (fieldIds || []).forEach(function (id) {
            var field = kéziField(id);
            if (!field) return;
            field.classList.add('uld-manual-input-error');
            missingNames.push(labels[id] || 'Mező');
            var wrapper = field.closest('.uld-manual-field');
            if (wrapper && !wrapper.querySelector('.uld-manual-field-error[data-for="' + id + '"]')) {
                var msg = document.createElement('div');
                msg.className = 'uld-manual-field-error';
                msg.dataset.for = id;
                msg.textContent = (labels[id] || 'Ez a mező') + ' kötelező, de üres';
                wrapper.appendChild(msg);
            }
        });
        if (box) {
            box.innerHTML =
                '<div class="uld-manual-duplicate-head">' +
                    '<span class="uld-manual-duplicate-kicker">Hiba</span>' +
                    '<strong>Hiányzó kötelező mező: ' + _esc(missingNames.join(', ')) + '</strong>' +
                '</div>';
            box.classList.add('uld-manual-duplicate-show', 'uld-manual-error-show');
            box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        var first = fieldIds && fieldIds.length ? kéziField(fieldIds[0]) : null;
        if (first && typeof first.focus === 'function') first.focus();
        _uldToast('Kötelező mező üres', 'error');
    }

    function populateManualStackSelect() {
        var select = kéziField('uld-manual-stack');
        if (!select) return;
        select.innerHTML = '';
        var newOpt = document.createElement('option');
        newOpt.value = '__new__';
        newOpt.textContent = '+ Új stack';
        select.appendChild(newOpt);
        stackElements().forEach(function (stackEl) {
            var opt = document.createElement('option');
            opt.value = stackIdOf(stackEl);
            opt.textContent = stackEl.dataset.stackName || 'Stack';
            opt.dataset.revision = String(stackRevision(stackEl));
            opt.dataset.gha = stackEl.dataset.gha || '';
            if (isPreparedStack(stackEl) || stackLocked(stackEl)) {
                opt.disabled = true;
                opt.textContent += ' (locked)';
            }
            select.appendChild(opt);
        });
    }

    function resetManualFields() {
        ['uld-manual-awb', 'uld-manual-gha', 'uld-manual-uld', 'uld-manual-datetime'].forEach(function (id) {
            var el = kéziField(id);
            if (el) el.value = '';
        });
        setManualTimeFromDate(new Date());
        clearManualDuplicate();
    }

    function openManualModal() {
        populateManualStackSelect();
        pendingManualStack = { stackId: '__new__', stackName: 'Új stack', revision: 0 };
        var modal = document.getElementById('uld-manual-modal');
        if (!modal) return;
        var title = document.getElementById('uld-manual-title');
        if (title) title.textContent = 'Kézi ULD hozzáadása';
        resetManualFields();
        updateManualGhaMode();
        modal.classList.remove('uld-manual-modal-closing');
        modal.classList.add('uld-manual-modal-show');
        modal.setAttribute('aria-hidden', 'false');
        setTimeout(function () {
            var awb = kéziField('uld-manual-awb');
            if (awb) awb.focus();
        }, 80);
    }

    function closeManualModal() {
        var modal = document.getElementById('uld-manual-modal');
        if (modal) {
            modal.classList.add('uld-manual-modal-closing');
            setTimeout(function () {
                modal.classList.remove('uld-manual-modal-show', 'uld-manual-modal-closing', 'uld-manual-modal-saving', 'uld-manual-modal-morphing');
                modal.setAttribute('aria-hidden', 'true');
            }, 220);
        }
        pendingManualStack = null;
    }

    function selectedManualStack() {
        var select = kéziField('uld-manual-stack');
        var value = select ? (select.value || '__new__') : '__new__';
        if (value === '__new__') return { stackId: '__new__', stackName: 'Új stack', revision: 0, gha: '' };
        var opt = select ? select.options[select.selectedIndex] : null;
        var stackEl = stackElById(value);
        return {
            stackId: value,
            stackName: opt ? opt.textContent.replace(' (locked)', '') : 'Stack',
            revision: opt && opt.dataset ? parseInt(opt.dataset.revision || '0', 10) || 0 : currentStackRevision(value),
            gha: (opt && opt.dataset && opt.dataset.gha) || (stackEl && stackEl.dataset.gha) || ''
        };
    }

    function updateManualGhaMode() {
        var selected = selectedManualStack();
        var ghaInput = kéziField('uld-manual-gha');
        var note = kéziField('uld-manual-gha-note');
        var modal = document.getElementById('uld-manual-modal');
        var locked = selected.stackId !== '__new__' && !!selected.gha;
        if (ghaInput) {
            ghaInput.disabled = locked;
            ghaInput.classList.toggle('uld-manual-input-locked', locked);
            if (locked) {
                ghaInput.value = selected.gha;
                ghaInput.placeholder = '';
            } else if (selected.stackId === '__new__') {
                ghaInput.placeholder = 'Menzies, AS Cargo vagy Celebi';
            } else {
                ghaInput.placeholder = 'Üres stack - válassz GHA-t';
            }
        }
        if (note) {
            note.textContent = locked
                ? ('GHA is set by the selected stack: ' + selected.gha)
                : (selected.stackId === '__new__'
                    ? 'Új stacknél a GHA határozza meg a stack csoportját.'
                    : 'This stack has no GHA yet; choose it here.');
        }
        if (modal) {
            modal.classList.toggle('uld-manual-gha-locked', locked);
            modal.classList.toggle('uld-manual-existing-stack', selected.stackId !== '__new__');
        }
    }

    function normalizeManualGha(value) {
        var raw = String(value || '').trim();
        var low = raw.toLowerCase();
        var found = kéziGhaOptions.find(function (name) {
            return name.toLowerCase() === low || name.toLowerCase().indexOf(low) === 0;
        });
        return found || raw;
    }

    function kéziDateIso() {
        var picker = kéziField('uld-manual-datetime');
        var value = picker ? String(picker.value || '').trim() : '';
        if (!value) return '';
        var date = new Date(value);
        if (Number.isNaN(date.getTime())) return null;
        if (date.getTime() > Date.now()) return 'future';
        setManualTimeFromDate(date);
        return date.toISOString();
    }

    function readManualPayload() {
        var selected = selectedManualStack();
        var awb = ((kéziField('uld-manual-awb') || {}).value || '').replace(/\D/g, '');
        var gha = selected.stackId !== '__new__' && selected.gha
            ? selected.gha
            : normalizeManualGha((kéziField('uld-manual-gha') || {}).value || '');
        var uld = normalizeUld((kéziField('uld-manual-uld') || {}).value || '');
        return {
            action: 'add_manual',
            stack_id: selected.stackId,
            stack_revision: selected.revision,
            awb: awb,
            gha: gha,
            am_time: kéziDateIso(),
            uld_number: uld
        };
    }

    function formatManualDate(value) {
        if (!value) return 'nincs megadva';
        var dt = new Date(value);
        if (Number.isNaN(dt.getTime())) return value;
        return dt.toLocaleString('hu-HU', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    function showManualDuplicate(duplicate) {
        var box = kéziField('uld-manual-duplicate');
        if (!box || !duplicate) return false;
        var awbs = Array.isArray(duplicate.awbs) && duplicate.awbs.length ? duplicate.awbs.join(', ') : '-';
        var reason = duplicate.match_type === 'awb' ? 'AWB egyezés' : 'ULD egyezés';
        box.innerHTML =
            '<div class="uld-manual-duplicate-head">' +
                '<span class="uld-manual-duplicate-kicker">Már szerepel</span>' +
                '<strong>' + _esc(reason) + '</strong>' +
            '</div>' +
            '<div class="uld-manual-duplicate-grid">' +
                '<span>ULD</span><b>' + _esc(duplicate.uld_number || '-') + '</b>' +
                '<span>AWB</span><b>' + _esc(awbs) + '</b>' +
                '<span>GHA</span><b>' + _esc(duplicate.gha || '-') + '</b>' +
                '<span>Átadás ideje</span><b>' + _esc(formatManualDate(duplicate.am_time)) + '</b>' +
                '<span>Lejárat</span><b>' + _esc(formatManualDate(duplicate.expiry_time)) + '</b>' +
                '<span>Stack</span><b>' + _esc(duplicate.stack_name || 'nincs stackben') + '</b>' +
            '</div>';
        box.classList.add('uld-manual-duplicate-show');
        box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        return true;
    }

    function animateManualFly(targetStack, payload, onDone) {
        var modal = document.getElementById('uld-manual-modal');
        var dialog = modal ? modal.querySelector('.uld-manual-dialog') : null;
        var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!dialog || reduceMotion) {
            // No flight — still tear the modal down and run the continuation so the
            // flow finishes identically to the animated path.
            if (modal) {
                modal.classList.remove('uld-manual-modal-show', 'uld-manual-modal-closing', 'uld-manual-modal-saving', 'uld-manual-modal-morphing');
                modal.setAttribute('aria-hidden', 'true');
            }
            pendingManualStack = null;
            if (typeof onDone === 'function') onDone();
            return;
        }
        var rect = dialog.getBoundingClientRect();
        var stackColumn = targetStack ? targetStack.querySelector('.uld-stack-col') : null;
        var targetRect = (stackColumn || targetStack) ? (stackColumn || targetStack).getBoundingClientRect() : null;
        var finalW = 112;
        var finalH = 38;
        var finalHátra = targetRect ? (targetRect.left + Math.min(Math.max(12, targetRect.width * .50 - finalW / 2), Math.max(12, targetRect.width - finalW - 12))) : (rect.left + rect.width / 2 - finalW / 2);
        var finalTop = targetRect ? (targetRect.top + Math.min(Math.max(12, targetRect.height * .18), Math.max(12, targetRect.height - finalH - 12))) : (rect.top - 72);
        var flyer = document.createElement('div');
        flyer.className = 'uld-manual-flyer uld-manual-morph-card';
        flyer.innerHTML =
            '<span class="uld-manual-morph-kicker">Kézi ULD</span>' +
            '<span class="uld-manual-morph-uld"></span>' +
            '<span class="uld-manual-morph-meta"></span>';
        var uldText = normalizeUld((payload && payload.uld_number) || (kéziField('uld-manual-uld') || {}).value || '') || 'ULD';
        var ghaText = (payload && payload.gha) || '';
        var awbText = (payload && payload.awb) || '';
        var uldEl = flyer.querySelector('.uld-manual-morph-uld');
        var metaEl = flyer.querySelector('.uld-manual-morph-meta');
        if (uldEl) uldEl.textContent = uldText;
        if (metaEl) metaEl.textContent = ghaText ? (ghaText + (awbText ? ' · AWB ' + awbText : '')) : (awbText ? 'AWB ' + awbText : '');
        flyer.style.left = rect.left + 'px';
        flyer.style.top = rect.top + 'px';
        flyer.style.width = rect.width + 'px';
        flyer.style.height = rect.height + 'px';
        document.body.appendChild(flyer);
        if (modal) modal.classList.add('uld-manual-modal-morphing');
        requestAnimationFrame(function () {
            flyer.classList.add('uld-manual-morph-flight');
            flyer.style.left = finalHátra + 'px';
            flyer.style.top = finalTop + 'px';
            flyer.style.width = finalW + 'px';
            flyer.style.height = finalH + 'px';
            if (targetStack) targetStack.classList.add('uld-manual-target-pulse');
        });
        setTimeout(function () {
            flyer.classList.add('uld-manual-morph-landed');
            if (targetStack) targetStack.classList.remove('uld-manual-target-pulse');
        }, 720);
        setTimeout(function () {
            flyer.remove();
            if (modal) {
                modal.classList.remove('uld-manual-modal-show', 'uld-manual-modal-closing', 'uld-manual-modal-saving', 'uld-manual-modal-morphing');
                modal.setAttribute('aria-hidden', 'true');
            }
            pendingManualStack = null;
            if (typeof onDone === 'function') onDone();
        }, 940);
    }

    function submitManualModal() {
        var payload = readManualPayload();
        var selected = selectedManualStack();
        // AWB is opcionális for kézi ULDs.
        clearManualDuplicate();
        var missingRequired = [];
        if (!payload.stack_id) missingRequired.push('uld-manual-stack');
        if (!payload.uld_number) missingRequired.push('uld-manual-uld');
        if (selected.stackId === '__new__' && !payload.gha) missingRequired.push('uld-manual-gha');
        if (missingRequired.length) {
            showManualRequiredError(missingRequired);
            return;
        }
        if (!selected.gha && kéziGhaOptions.indexOf(payload.gha) === -1) {
            _uldToast('A GHA csak ez lehet: Menzies, AS Cargo, Celebi', 'error');
            return;
        }
        if (payload.am_time === null) {
            _uldToast('Az átadás ideje üresen hagyható, vagy teljes dátumot és időpontot adj meg', 'error');
            return;
        }
        if (payload.am_time === 'future') {
            _uldToast('Az átadás ideje nem lehet későbbi a jelenlegi időnél', 'error');
            return;
        }
        var btn = document.getElementById('uld-manual-save');
        var modal = document.getElementById('uld-manual-modal');
        if (btn && !guardActionButton(btn, 'Mentés...')) return;
        if (modal) modal.classList.add('uld-manual-modal-saving');
        uldApiCall(payload, function (data) {
            var newStackId = data && data.stack && data.stack.id;
            var targetStackId = payload.stack_id === '__new__' ? newStackId : payload.stack_id;
            // Dash renders the authoritative server state. Remember the target as
            // expanded before that render; if an existing target is already present,
            // open it immediately so the fly animation still has a visible endpoint.
            var target = stackElById(targetStackId);
            if (targetStackId) {
                forceStackekCollapsed = false;
                expandedStackek.add(targetStackId);
                if (target && target.classList.contains('uld-stack-collapsible') &&
                    !target.classList.contains('uld-stack-expanded')) {
                    expandStackEl(target, false, false);
                }
            }
            animateManualFly(target, payload);
            // A kézi ULD is a brand-new list entry; signal a full render so it
            // shows in the ULD list at once instead of after the file watcher delay.
            setStore('uld-list-dirty-store', Date.now());
            _uldToast(payload.uld_number + ' hozzáadva', 'ok');
        }, {
            refreshDelay: 120,
            onError: function (data) {
                if (data && data.duplicate) {
                    showManualDuplicate(data.duplicate);
                    _uldToast('Ez a tétel már szerepel - a részletek lent láthatók', 'info');
                    return true;
                }
                return false;
            },
            done: function () {
                if (modal) modal.classList.remove('uld-manual-modal-saving');
                if (btn) releaseActionButton(btn);
            }
        });
    }

    var pendingEditRow = null;

    function ensureEditModal() {
        var modal = document.getElementById('uld-edit-modal');
        if (modal) return modal;
        modal = document.createElement('div');
        modal.id = 'uld-edit-modal';
        modal.className = 'uld-manual-modal uld-edit-modal';
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML =
            '<div class="uld-manual-dialog uld-edit-dialog">' +
                '<div class="uld-manual-head">' +
                    '<div>' +
                        '<div class="uld-manual-kicker">ULD szerkesztése</div>' +
                        '<div class="uld-manual-title" id="uld-edit-title">ULD szerkesztése</div>' +
                    '</div>' +
                    '<button type="button" id="uld-edit-close" class="uld-manual-close" title="Bezárás">X</button>' +
                '</div>' +
                '<div class="uld-edit-grid">' +
                    '<label class="uld-manual-field"><span>AWB <em>kötelező</em></span><input id="uld-edit-awb" class="uld-manual-input" inputmode="numeric" autocomplete="off"></label>' +
                    '<label class="uld-manual-field"><span>ULD <em>kötelező</em></span><input id="uld-edit-uld" class="uld-manual-input" autocomplete="off" spellcheck="false"></label>' +
                    '<label class="uld-manual-field"><span>GHA <em>kötelező</em></span><select id="uld-edit-gha" class="uld-manual-input uld-manual-select">' +
                        '<option value="">GHA választása</option>' +
                        '<option value="AS Cargo">AS Cargo</option>' +
                        '<option value="Menzies">Menzies</option>' +
                        '<option value="Celebi">Celebi</option>' +
                    '</select></label>' +
                '</div>' +
                '<div id="uld-edit-audit" class="uld-edit-audit"></div>' +
                '<div id="uld-edit-message" class="uld-manual-duplicate" aria-live="polite"></div>' +
                '<div class="uld-manual-actions">' +
                    '<button type="button" id="uld-edit-cancel" class="uld-manual-cancel">Mégse</button>' +
                    '<button type="button" id="uld-edit-save" class="uld-manual-save">Mentés</button>' +
                '</div>' +
            '</div>';
        document.body.appendChild(modal);
        return modal;
    }

    function editField(id) {
        return document.getElementById(id);
    }

    function showEditMessage(title, detail, type) {
        var box = editField('uld-edit-message');
        if (!box) return;
        box.innerHTML =
            '<div class="uld-manual-duplicate-head">' +
                '<span class="uld-manual-duplicate-kicker">' + _esc(type === 'error' ? 'Hiba' : 'Info') + '</span>' +
                '<strong>' + _esc(title || '') + '</strong>' +
            '</div>' +
            (detail ? '<div class="uld-edit-message-detail">' + _esc(detail) + '</div>' : '');
        box.classList.add('uld-manual-duplicate-show');
        box.classList.toggle('uld-manual-error-show', type === 'error');
    }

    function clearEditMessage() {
        var box = editField('uld-edit-message');
        if (!box) return;
        box.innerHTML = '';
        box.classList.remove('uld-manual-duplicate-show', 'uld-manual-error-show');
        ['uld-edit-awb', 'uld-edit-uld', 'uld-edit-gha'].forEach(function (id) {
            var field = editField(id);
            if (field) field.classList.remove('uld-manual-input-error');
        });
    }

    function rowEditPayload(row) {
        var ds = row && row.dataset ? row.dataset : {};
        var gha = (ds.gha || '').trim();
        if (kéziGhaOptions.indexOf(gha) === -1) gha = '';
        return {
            uld: normalizeUld(ds.uld || ''),
            awb: (ds.awbs || '').split(',').map(function (v) { return v.trim(); }).filter(Boolean).join(', '),
            gha: gha,
            szerkesztveAt: ds.szerkesztveAt || '',
            szerkesztveBy: ds.szerkesztveBy || '',
            changeSummary: ds.changeSummary || ''
        };
    }

    // ISO timestamp → "YYYY.MM.DD HH:mm" (drops the noisy seconds/microseconds).
    function fmtAuditDate(iso) {
        var s = (iso || '').trim();
        if (!s) return '';
        var d = new Date(s);
        if (isNaN(d.getTime())) return s;
        var p = function (n) { return n < 10 ? '0' + n : '' + n; };
        return d.getFullYear() + '.' + p(d.getMonth() + 1) + '.' + p(d.getDate()) +
               ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }

    function formatStackCreatedMeta(stack) {
        stack = stack || {};
        var by = String(stack.created_by || '').trim();
        var at = fmtAuditDate(String(stack.created_at || '').trim());
        return 'Készítette: ' + (by || 'ismeretlen') + ' - ' + (at || 'ismeretlen idő');
    }

    function formatUldEditAudit(payload) {
        payload = payload || {};
        var by = (payload.szerkesztveBy || '').trim();
        var at = fmtAuditDate((payload.szerkesztveAt || '').trim());
        var change = (payload.changeSummary || payload.change_summary || '').trim();
        if (!by && !at) return 'Nincs korábbi kézi szerkesztés ehhez az ULD-hez.';
        return 'Last edit: ' + (by || 'ismeretlen') + ' - ' + (at || 'ismeretlen idő') +
               (change ? '; ' + change : '');
    }

    function openUldEditModal(row) {
        var payload = rowEditPayload(row);
        var modal = ensureEditModal();
        pendingEditRow = row;
        clearEditMessage();
        var title = editField('uld-edit-title');
        if (title) title.textContent = payload.uld ? (payload.uld + ' szerkesztése') : 'ULD szerkesztése';
        var awb = editField('uld-edit-awb');
        var uld = editField('uld-edit-uld');
        var gha = editField('uld-edit-gha');
        var audit = editField('uld-edit-audit');
        if (awb) awb.value = payload.awb;
        if (uld) {
            uld.value = payload.uld;
            uld.dataset.originalUld = payload.uld;
        }
        if (gha) gha.value = payload.gha;
        if (audit) audit.textContent = formatUldEditAudit(payload);
        modal.classList.remove('uld-manual-modal-closing');
        modal.classList.add('uld-manual-modal-show');
        modal.setAttribute('aria-hidden', 'false');
        setTimeout(function () { if (awb) awb.focus(); }, 80);
    }

    function closeUldEditModal() {
        var modal = document.getElementById('uld-edit-modal');
        if (!modal) return;
        modal.classList.add('uld-manual-modal-closing');
        modal.setAttribute('aria-hidden', 'true');
        setTimeout(function () {
            modal.classList.remove('uld-manual-modal-show', 'uld-manual-modal-closing', 'uld-manual-modal-saving');
        }, 220);
        pendingEditRow = null;
    }

    function readEditPayload() {
        var uld = editField('uld-edit-uld');
        var awb = editField('uld-edit-awb');
        var gha = editField('uld-edit-gha');
        return {
            action: 'edit_uld',
            uld_number: normalizeUld((uld && uld.dataset.originalUld) || (pendingEditRow && pendingEditRow.dataset.uld) || ''),
            new_uld_number: normalizeUld((uld && uld.value) || ''),
            awb: ((awb && awb.value) || '').trim(),
            gha: (gha && gha.value) || ''
        };
    }

    // Repaint a list row's GHA-derived colors in place. The hue is computed from
    // the same fixed palette the server uses (ghaColorFor), so the optimistic
    // update lands on the exact color the re-render will produce — no lag, no jump.
    function applyRowGhaColor(row, gha) {
        var color = ghaColorFor(gha);
        var ghaMeta = row.querySelector('.uld-meta-gha');
        if (ghaMeta) {
            if (color) ghaMeta.style.setProperty('--gha-color', color);
            else ghaMeta.style.removeProperty('--gha-color');
        }
        var stackBadge = row.querySelector('.uld-in-stack-badge');
        if (stackBadge) {
            if (color) {
                stackBadge.style.setProperty('--stack-gha-color', color);
                stackBadge.style.setProperty('--stack-gha-bg', color + '22');
                stackBadge.style.setProperty('--stack-gha-border', color + '55');
            } else {
                stackBadge.style.removeProperty('--stack-gha-color');
                stackBadge.style.removeProperty('--stack-gha-bg');
                stackBadge.style.removeProperty('--stack-gha-border');
            }
        }
        if (color && row.classList.contains('uld-row-in-stack')) {
            row.style.setProperty('--stack-accent', color);
            row.style.setProperty('--stack-accent-bg', color + '14');
        }
        // Drives the edit highlight glow so it matches the (possibly new) GHA hue,
        // even for rows that aren't in a stack and carry no --stack-accent.
        if (color) row.style.setProperty('--edit-pulse-accent', color);
        else row.style.removeProperty('--edit-pulse-accent');
    }

    // Az ULD-lista React/Dash tulajdonú gyerekelemeihez nem nyúlunk kézzel.
    // Csak állapotosztályokat és data attribútumot állítunk: ez azonnali,
    // villódzásmentes visszajelzést ad, a szerveres render pedig rögtön utána
    // létrehozza a végleges stack-jelvényt és hiteles állapotot.
    function markRowsStackPending(ulds) {
        (ulds || []).forEach(function (uld) {
            var row = rowForUld(uld);
            if (!row) return;
            row.classList.add('uld-row-stack-pending');
            row.classList.remove('uld-row-stack-confirmed');
        });
    }

    function clearRowsStackPending(ulds) {
        (ulds || []).forEach(function (uld) {
            var row = rowForUld(uld);
            if (!row) return;
            row.classList.remove('uld-row-stack-pending');
        });
    }

    function confirmRowsStackMembership(ulds, stackId) {
        (ulds || []).forEach(function (uld) {
            var row = rowForUld(uld);
            if (!row) return;
            row.classList.remove('uld-row-stack-pending');
            row.classList.add('uld-row-in-stack', 'uld-row-stack-confirmed');
            if (stackId) row.dataset.stackId = stackId;
            applyRowGhaColor(row, row.dataset.gha || '');
            clearTimeout(row._uldStackConfirmedTimer);
            row._uldStackConfirmedTimer = setTimeout(function () {
                row.classList.remove('uld-row-stack-confirmed');
            }, 520);
        });
    }

    function applyEditedRow(payload) {
        var row = pendingEditRow;
        if (!row || !payload) return;
        var oldUld = row.dataset.uld || '';
        var reverted = !!payload.reverted;
        row.dataset.uld = payload.new_uld_number;
        row.dataset.awbs = payload.awb;
        row.dataset.gha = payload.gha;
        row.dataset.szerkesztve = reverted ? '0' : '1';
        row.dataset.szerkesztveAt = reverted ? '' : (payload.szerkesztve_at || payload.szerkesztveAt || '');
        row.dataset.szerkesztveBy = reverted ? '' : (payload.szerkesztve_by || payload.szerkesztveBy || '');
        row.dataset.changeSummary = reverted ? '' : (payload.change_summary || payload.changeSummary || '');
        var num = row.querySelector('.uld-row-num');
        if (num) num.textContent = payload.new_uld_number;
        var awbMeta = row.querySelector('.uld-meta-awb');
        if (awbMeta) awbMeta.textContent = payload.awb || 'nincs AWB';
        var ghaMeta = row.querySelector('.uld-meta-gha');
        if (ghaMeta) ghaMeta.textContent = payload.gha;
        // Instant, deterministic GHA recolor (CSS transitions morph the hue smoothly).
        applyRowGhaColor(row, payload.gha);
        // IMPORTANT: do NOT hand-inject the "szerk." tag here. It is a NEW child of a
        // Dash/React-owned row; for a GHA/AWB edit the React key (uld number) is stable,
        // so the row reconciles in place and the next server render inserts ITS OWN tag
        // — leaving two "szerk." badges. The re-render (bumped right after) owns the tag.
        // We only refresh the tooltip on a tag that already exists. On a revert the
        // server re-render removes the tag (React-owned) cleanly — we must NOT hand-
        // remove it here, so we just leave it for the reconcile and skip the title.
        var szerkesztveTag = row.querySelector('.uld-szerkesztve-tag');
        if (szerkesztveTag && !reverted) szerkesztveTag.title = formatUldEditAudit({ szerkesztveBy: row.dataset.szerkesztveBy, szerkesztveAt: row.dataset.szerkesztveAt });
        if (oldUld && selectedUlds.has(oldUld)) {
            selectedUlds.delete(oldUld);
            selectedUlds.add(payload.new_uld_number);
        }
        // One-shot highlight pulse so the change reads clearly. The class is reset by
        // the upcoming server reconcile; submit's refreshDelay outlasts the animation.
        row.classList.remove('uld-row-szerkesztve-pulse');
        void row.offsetWidth;  // force reflow so a re-edit restarts the animation
        row.classList.add('uld-row-szerkesztve-pulse');
    }

    function submitUldEditModal() {
        var payload = readEditPayload();
        clearEditMessage();
        var missing = [];
        if (!payload.awb.replace(/\D/g, '')) missing.push('AWB');
        if (!payload.new_uld_number) missing.push('ULD');
        if (kéziGhaOptions.indexOf(payload.gha) === -1) missing.push('GHA');
        if (missing.length) {
            ['uld-edit-awb', 'uld-edit-uld', 'uld-edit-gha'].forEach(function (id) {
                var field = editField(id);
                if (!field) return;
                var label = id === 'uld-edit-awb' ? 'AWB' : (id === 'uld-edit-uld' ? 'ULD' : 'GHA');
                field.classList.toggle('uld-manual-input-error', missing.indexOf(label) !== -1);
            });
            showEditMessage('Hiányzó vagy hibás mező: ' + missing.join(', '), 'Az üres vagy hiányos adatok miatt a módosítás nem lett elmentve.', 'error');
            _uldToast('A szerkesztéshez minden kötelező mezőt tölts ki', 'error');
            return;
        }
        payload.awb = payload.awb.split(/[,\n;]/).map(function (v) { return v.replace(/\D/g, ''); }).filter(Boolean).join(', ');
        var save = editField('uld-edit-save');
        var modal = document.getElementById('uld-edit-modal');
        if (save && !guardActionButton(save, 'Mentés...')) return;
        if (modal) modal.classList.add('uld-manual-modal-saving');
        uldApiCall(payload, function (data) {
            var szerkesztve = (data && data.szerkesztve_uld) || {};
            applyEditedRow(Object.assign({}, payload, {
                szerkesztve_at: szerkesztve.szerkesztve_at || '',
                szerkesztve_by: szerkesztve.szerkesztve_by || '',
                change_summary: szerkesztve.change_summary || '',
                reverted: !!szerkesztve.reverted
            }));
            setStore('uld-list-dirty-store', Date.now());
            _uldToast(payload.new_uld_number + (szerkesztve.reverted ? ' visszaállítva' : ' frissítve'), 'ok');
            closeUldEditModal();
        }, {
            // Long enough for the optimistic recolor + highlight pulse to play out
            // before the server re-render reconciles the row (which adds the "szerk."
            // tag and resets transient classes). The visible content is already correct
            // optimistically, so this delay is invisible — it only gates the reconcile.
            refreshDelay: 380,
            onError: function (data) {
                showEditMessage('A szerkesztés nem menthető', (data && data.error) || 'Ismeretlen hiba', 'error');
                return true;
            },
            done: function () {
                if (modal) modal.classList.remove('uld-manual-modal-saving');
                if (save) releaseActionButton(save);
            }
        });
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('#uld-new-stack-btn');
        if (!btn || btn.disabled) return;
        e.preventDefault();
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
        if (stackCreateBusy() || btn.dataset.uldBusy === '1') {
            _uldToast('Stack létrehozása folyamatban', 'info');
            return;
        }
        var selectedForNewStack = Array.from(selectedUlds);
        if (selectedForNewStack.length) {
            createStackThenHozzáadás(selectedForNewStack, btn);
            return;
        }
        window.__uldCreateInFlight = true;
        uldUiState.pendingCreates += 1;
        setCreateButtonBusy(btn, true);
        setCreateControlsBusy(true, btn);
        setUldMode('syncing');
        window.__uldHighlightNextNewStack = true;
        uldApiCall(
            { action: 'create', client_op_id: newClientOpId('create-empty') },
            function () { _uldToast('Új stack létrehozva', 'ok'); },
            {
                refreshDelay: 120,
                done: function () {
                    uldUiState.pendingCreates = Math.max(0, uldUiState.pendingCreates - 1);
                    window.__uldCreateInFlight = false;
                    setCreateButtonBusy(btn, uldUiState.pendingCreates > 0);
                    setCreateControlsBusy(uldUiState.pendingCreates > 0, btn);
                    if (!uldUiState.lockedStackek.size && !uldUiState.pendingCreates) setUldMode('idle');
                }
            }
        );
    }, true);

    // Match server-side _normalize_uld_numbers: uppercase + strip non-alphanumeric.
    // Server stores stacks using normalized form, so optimistic .uld-si items must match.
    function normalizeUld(s) {
        var conf = {
            'А':'A','В':'B','С':'C','Е':'E','Н':'H','К':'K','М':'M','О':'O','Р':'P','Т':'T','Х':'X','У':'Y',
            'а':'A','в':'B','с':'C','е':'E','н':'H','к':'K','м':'M','о':'O','р':'P','т':'T','х':'X','у':'Y',
            'Ο':'O','ο':'O','Ι':'I','І':'I','і':'I'
        };
        return String(s == null ? '' : s).toUpperCase().replace(/[АВСЕНКМОРТХУавсенкмортхуΟοΙІі]/g, function (ch) {
            return conf[ch] || ch;
        }).replace(/[^A-Z0-9]/g, '');
    }

    function cssEsc(s) {
        if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(s);
        return String(s).replace(/["\\]/g, '\\$&');
    }

    function uldElements() {
        return Array.from(document.querySelectorAll('.uld-row[data-uld], .uld-si[data-uld]'));
    }

    function uldElementsFor(ulds) {
        var wanted = new Set(ulds);
        return uldElements().filter(function (el) { return wanted.has(el.dataset.uld); });
    }

    function stackElements() {
        return Array.from(document.querySelectorAll('.uld-stack[data-stack-id]'));
    }

    // ── Stack accordion (collapse / expand) ─────────────────────────────────
    // Each non-empty stack collapses to just its header so ~4 fit on screen.
    // Clicking the header toggles it open with a smooth height animation and
    // scrolls it into the grid viewport. Multiple stacks may be open at once.
    // The open set is remembered across Dash re-renders so a stack stays open
    // after a poll/refresh.
    var STACK_VISIBLE_TARGET = 4;   // how many collapsed headers fit before scroll
    var expandedStackek = new Set();

    function stackBody(stackEl) {
        return stackEl ? stackEl.querySelector('.uld-stack-body') : null;
    }

    function stackBodyOpenHeight(body) {
        if (!body) return 0;
        // In focus mode the stacks panel owns most of the width and the ULD items
        // flow into multiple columns, so give each open body a much taller cap —
        // a 15–18 item stack then fits with little or no internal scroll.
        var focus = document.body && document.body.classList.contains('uld-stacks-focus');
        var frac = focus ? 0.66 : 0.42;
        var floorPx = focus ? 240 : 180;
        var viewportCap = Math.max(floorPx, Math.floor(window.innerHeight * frac));
        return Math.min(body.scrollHeight, viewportCap);
    }

    function applyStackBodyScroll(body) {
        if (!body) return;
        var openHeight = stackBodyOpenHeight(body);
        body.style.maxHeight = openHeight + 'px';
        body.style.overflowY = body.scrollHeight > openHeight + 1 ? 'auto' : 'hidden';
        body.style.overflowX = 'hidden';
    }

    // Bring a freshly-opened stack into view ONLY when its header is actually
    // off-screen (above the top or at/below the bottom edge). When the stack is
    // already visible we never scroll — that's what used to make the stack above
    // "half disappear". The grid itself is the single scroller; bodies grow to
    // their natural height and the whole list scrolls as one.
    function scrollStackIntoView(stackEl) {
        var grid = document.getElementById('uld-stacks-grid');
        if (!grid || !stackEl) return;
        requestAnimationFrame(function () {
            var g = grid.getBoundingClientRect();
            var s = stackEl.getBoundingClientRect();
            if (s.top < g.top + 2 || s.top > g.bottom - 48) {
                grid.scrollTo({ top: grid.scrollTop + (s.top - g.top) - 8, behavior: 'smooth' });
            }
        });
    }

    function expandStackEl(stackEl, animate, scroll) {
        var body = stackBody(stackEl);
        if (!body) return;
        stackEl.classList.add('uld-stack-expanded');
        if (animate === false) {
            applyStackBodyScroll(body);
            return;
        }
        body.style.overflowY = 'hidden';
        body.style.maxHeight = stackBodyOpenHeight(body) + 'px';
        var done = function (ev) {
            if (ev && ev.target !== body) return;
            body.removeEventListener('transitionend', done);
            // Keep a stable cap and let the body scroll internally. Releasing to
            // natural height makes large stacks bleed into the next card.
            if (stackEl.classList.contains('uld-stack-expanded')) applyStackBodyScroll(body);
        };
        body.addEventListener('transitionend', done);
        setTimeout(done, 420); // safety net if transitionend never fires
        if (scroll !== false) scrollStackIntoView(stackEl);
    }

    function collapseStackEl(stackEl, animate) {
        var body = stackBody(stackEl);
        if (!body) return;
        if (animate === false) {
            stackEl.classList.remove('uld-stack-expanded');
            body.style.maxHeight = '';   // fall back to the CSS collapsed (0) height
            body.style.overflowY = '';
            body.style.overflowX = '';
            return;
        }
        // Lock the current rendered height, reflow, then animate down to 0.
        body.style.maxHeight = Math.round(body.getBoundingClientRect().height) + 'px';
        void body.offsetHeight;
        stackEl.classList.remove('uld-stack-expanded');
        body.style.maxHeight = '0px';
        body.style.overflowY = 'hidden';
        body.style.overflowX = 'hidden';
    }

    function toggleStackAccordion(stackEl) {
        if (!stackEl || !stackEl.classList.contains('uld-stack-collapsible')) return;
        forceStackekCollapsed = false;
        var id = stackEl.dataset.stackId;
        if (stackEl.classList.contains('uld-stack-expanded')) {
            if (id) expandedStackek.delete(id);
            collapseStackEl(stackEl, true);
        } else {
            if (id) expandedStackek.add(id);
            expandStackEl(stackEl, true, true);
        }
    }

    // ── Bulk stack tools: expand/collapse all + focus mode ──────────────────
    // "Mind kinyit" opens every collapsible stack AND switches the ULD page into
    // focus mode: the stacks panel widens, items reflow into columns, the ULD list
    // shrinks. Collapsing all leaves focus mode again. The state survives Dash
    // re-renders because expandedStackek + the body class are both persistent.
    var stacksFocusMode = false;
    var forceStackekCollapsed = false;

    function collapsibleStackEls() {
        return stackElements().filter(function (s) {
            return s.classList.contains('uld-stack-collapsible');
        });
    }

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    function visibleTransitionEls() {
        var viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
        var els = [];
        ['.uld-stacks-panel', '.uld-list-panel'].forEach(function (sel) {
            var el = document.querySelector(sel);
            if (el) els.push(el);
        });
        stackElements().forEach(function (el) {
            var r = el.getBoundingClientRect();
            if (!viewportH || (r.bottom >= -80 && r.top <= viewportH + 80)) els.push(el);
        });
        return els;
    }

    function captureTransitionRects(els) {
        var rects = new Map();
        els.forEach(function (el) {
            var r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) rects.set(el, r);
        });
        return rects;
    }

    function animateFocusRects(firstRects) {
        if (!firstRects || !firstRects.size || !Element.prototype.animate || prefersReducedMotion()) return;
        var body = document.body;
        if (body) body.classList.add('uld-stacks-focus-transitioning');
        requestAnimationFrame(function () {
            firstRects.forEach(function (first, el) {
                if (!el.isConnected) return;
                var last = el.getBoundingClientRect();
                if (!last.width || !last.height) return;
                var dx = first.left - last.left;
                var dy = first.top - last.top;
                var sx = Math.max(0.82, Math.min(1.18, first.width / last.width));
                var sy = Math.max(0.86, Math.min(1.14, first.height / last.height));
                if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.01 && Math.abs(sy - 1) < 0.01) return;
                var oldOrigin = el.style.transformOrigin;
                el.style.transformOrigin = 'top left';
                var anim = el.animate([
                    { transform: 'translate(' + dx + 'px,' + dy + 'px) scale(' + sx + ',' + sy + ')', opacity: 0.92 },
                    { transform: 'translate(0,0) scale(1,1)', opacity: 1 }
                ], {
                    duration: 480,
                    easing: 'cubic-bezier(.16,1,.3,1)',
                    fill: 'both'
                });
                anim.addEventListener('finish', function () { el.style.transformOrigin = oldOrigin; }, { once: true });
                anim.addEventListener('cancel', function () { el.style.transformOrigin = oldOrigin; }, { once: true });
            });
            setTimeout(function () {
                if (body) body.classList.remove('uld-stacks-focus-transitioning');
            }, 560);
        });
    }

    function remeasureOpenStackBodiesSoon() {
        requestAnimationFrame(function () {
            collapsibleStackEls().forEach(function (s) {
                if (s.classList.contains('uld-stack-expanded')) {
                    var body = stackBody(s);
                    if (body) applyStackBodyScroll(body);
                }
            });
        });
    }

    function setStacksFocus(on, animate) {
        var next = !!on;
        var changed = stacksFocusMode !== next || (document.body && document.body.classList.contains('uld-stacks-focus') !== next);
        var firstRects = (changed && animate !== false && !prefersReducedMotion()) ? captureTransitionRects(visibleTransitionEls()) : null;
        stacksFocusMode = next;
        if (document.body) document.body.classList.toggle('uld-stacks-focus', stacksFocusMode);
        if (firstRects) animateFocusRects(firstRects);
        remeasureOpenStackBodiesSoon();
    }

    // Bulk open/close used to jank because every stack animated its max-height in
    // the SAME frame — three tall bodies (25/10/3) relaying out together drops
    // frames on a weak renderer. The fix is to keep the smooth height transition
    // (that's the nice "opening" feel the operators want) but STAGGER it: only one
    // body animates at a time, so each open/close stays buttery.
    var _bulkTimers = [];
    function _clearBulkTimers() {
        _bulkTimers.forEach(clearTimeout);
        _bulkTimers = [];
    }
    var BULK_STEP_MS = 95;   // gap between consecutive stack animations
    var BULK_OPEN_DELAY_MS = 120;
    var STACK_BODY_ANIM_MS = 380;

    function expandMindStackek() {
        var els = collapsibleStackEls();
        _clearBulkTimers();
        forceStackekCollapsed = false;
        setStacksFocus(true, true);       // widen layout first, then open
        var step = 0;
        els.forEach(function (s) {
            var id = s.dataset.stackId;
            if (id) expandedStackek.add(id);
            if (s.classList.contains('uld-stack-expanded')) return;
            _bulkTimers.push(setTimeout(function () {
                // Skip if the user toggled/closed it again in the meantime.
                if (id && expandedStackek.has(id)) expandStackEl(s, true, false);
            }, BULK_OPEN_DELAY_MS + step * BULK_STEP_MS));
            step += 1;
        });
        refreshStackToolButtons();
        _uldToast(els.length ? ('Mind nyitva (' + els.length + ' stack)') : 'Nincs nyitható stack', els.length ? 'ok' : 'info');
    }

    function collapseAllStacks() {
        var els = collapsibleStackEls();
        _clearBulkTimers();
        forceStackekCollapsed = true;
        expandedStackek.clear();
        setStacksFocus(false, true);
        var step = 0;
        els.forEach(function (s) {
            var id = s.dataset.stackId;
            if (id) expandedStackek.delete(id);
            if (!s.classList.contains('uld-stack-expanded')) return;
            _bulkTimers.push(setTimeout(function () {
                if (!(id && expandedStackek.has(id))) collapseStackEl(s, true);
            }, step * BULK_STEP_MS));
            step += 1;
        });
        _bulkTimers.push(setTimeout(function () {
            expandedStackek.clear();
            collapsibleStackEls().forEach(function (s) {
                collapseStackEl(s, false);
            });
            refreshStackToolButtons();
        }, Math.max(0, step - 1) * BULK_STEP_MS + STACK_BODY_ANIM_MS + 40));
        refreshStackToolButtons();
    }

    function allStackekExpanded() {
        var els = collapsibleStackEls();
        if (!els.length) return false;
        return els.every(function (s) { return s.classList.contains('uld-stack-expanded'); });
    }

    function toggleExpandMindStackek() {
        // The button toggles the deliberate "expanded view" (focus) mode ONLY. It must
        // not react to stacks that merely happen to be open (individually, or auto-opened
        // by a search) — that incidental state is independent of this view mode.
        if (stacksFocusMode) collapseAllStacks();
        else expandMindStackek();
    }

    function selectAllStacks() {
        var els = stackElements();
        selectedUlds.clear();
        els.forEach(function (s) {
            if (s.dataset.stackId) selectedStackek.add(s.dataset.stackId);
        });
        applySelectionClasses();
        refreshStackToolButtons();
        _uldToast(els.length ? (els.length + ' stack kijelölve') : 'Nincs kijelölhető stack', els.length ? 'ok' : 'info');
    }

    function allStackekSelected() {
        var els = stackElements();
        if (!els.length) return false;
        return els.every(function (s) { return selectedStackek.has(s.dataset.stackId); });
    }

    function toggleSelectMindStackek() {
        if (allStackekSelected()) {
            clearSelection();
        } else {
            selectAllStacks();
        }
        refreshStackToolButtons();
    }

    // Keep the two toolbar buttons' labels/active state in sync with the live
    // stack/selection state (also after Dash re-renders the stack grid).
    function refreshStackToolButtons() {
        var expandBtn = document.getElementById('uld-expand-all-btn');
        if (expandBtn) {
            // Locked to the focus-mode flag only — a stack being open on its own (search
            // auto-open, single toggle) must NOT flip this button to the active state.
            var expanded = stacksFocusMode;
            expandBtn.textContent = expanded ? 'Mind zárása' : 'Mind nyitása';
            expandBtn.classList.toggle('uld-stack-tool-active', !!expanded);
            expandBtn.setAttribute('aria-pressed', expanded ? 'true' : 'false');
        }
        var selBtn = document.getElementById('uld-select-all-stacks-btn');
        if (selBtn) {
            var allSel = allStackekSelected();
            selBtn.textContent = allSel ? 'Kijelölés törlése' : 'Mind kijelölése';
            selBtn.classList.toggle('uld-stack-tool-active', !!allSel);
            selBtn.setAttribute('aria-pressed', allSel ? 'true' : 'false');
        }
    }

    // The grid height is CSS-driven now (sticky panel max-height + flex scroll):
    // the panel grows with the open content up to the viewport, then scrolls —
    // no JS-toggled cap, so opening/closing never makes the list jump or compress
    // the other stacks. We only clear any stale inline height left by older code.
    function capStackGridHeight() {
        var grid = document.getElementById('uld-stacks-grid');
        if (grid && grid.style.maxHeight) grid.style.maxHeight = '';
    }

    // Re-apply accordion state after every render (no animation on reapply).
    function setupStackCollapse() {
        // In focus mode every (re-rendered) collapsible stack should stay open,
        // including ones that appeared since the last expand-all.
        if (stacksFocusMode && !forceStackekCollapsed) {
            collapsibleStackEls().forEach(function (s) {
                if (s.dataset.stackId) expandedStackek.add(s.dataset.stackId);
            });
        }
        stackElements().forEach(function (stackEl) {
            var body = stackBody(stackEl);
            if (!body) return;
            if (!stackEl.classList.contains('uld-stack-collapsible')) {
                // Empty / non-collapsible stacks are always open (drop hint shown).
                stackEl.classList.remove('uld-stack-expanded');
                body.style.maxHeight = '';
                body.style.overflowY = '';
                body.style.overflowX = '';
                return;
            }
            var id = stackEl.dataset.stackId;
            // A search hit ALWAYS opens its stack so the matched ULD is visible — even
            // after "Mind összecsuk" (forceStackekCollapsed). Searching is an explicit
            // "find this ULD" intent that must override the collapsed view state.
            var hasSearchHit = stackEl.classList.contains('uld-stack-search-hit');
            var wantOpen = hasSearchHit || (!forceStackekCollapsed && id && expandedStackek.has(id));
            var prevTr = body.style.transition;
            body.style.transition = 'none';
            if (wantOpen) expandStackEl(stackEl, false);
            else collapseStackEl(stackEl, false);
            void body.offsetHeight; // reflow so the restore doesn't animate
            body.style.transition = prevTr;
        });
        capStackGridHeight();
        refreshStackToolButtons();
    }

    var _stackCollapseTimer = null;
    function setupStackCollapseSoon(delay) {
        if (_stackCollapseTimer) clearTimeout(_stackCollapseTimer);
        _stackCollapseTimer = setTimeout(setupStackCollapse, delay == null ? 60 : delay);
    }
    window.addEventListener('resize', function () {
        setupStackCollapseSoon(120);
        setTimeout(refreshTouchbarFades, 140);
    });

    function applySelectionClasses() {
        uldElements().forEach(function (el) {
            el.classList.toggle('uld-selected', selectedUlds.has(el.dataset.uld));
        });
        stackElements().forEach(function (el) {
            var selected = selectedStackek.has(el.dataset.stackId);
            el.classList.toggle('uld-stack-selected', selected);
            var btn = el.querySelector('.uld-stack-select');
            if (btn) btn.textContent = selected ? 'Kijelölve' : 'Kijelölés';
        });
        updateSelectionBar();
        refreshStackToolButtons();
    }

    var _lastSelectionPulseSig = '';
    function updateSelectionBar() {
        var bar = document.getElementById('uld-selection-bar');
        if (!bar) return;
        var stackMode = selectedStackek.size > 0;
        var count = stackMode ? selectedStackek.size : selectedUlds.size;
        var wasActive = bar.classList.contains('uld-selection-active');
        bar.classList.toggle('uld-selection-active', count > 0);
        bar.classList.toggle('uld-stack-selection-mode', stackMode);
        if (document.body) document.body.classList.toggle('uld-selection-bar-visible', count > 0);
        var label = bar.querySelector('.uld-selection-count');
        if (label) label.textContent = String(count);
        var labelText = bar.querySelector('.uld-selection-label');
        if (labelText) labelText.textContent = stackMode ? 'stacks selected' : 'ULD kijelölve';
        var pulseSig = (stackMode ? 'S' : 'U') + ':' + count;
        if (!count) {
            _lastSelectionPulseSig = '';
        } else if (pulseSig !== _lastSelectionPulseSig) {
            bar.classList.remove('uld-selection-pulse');
            void bar.offsetWidth;
            bar.classList.add('uld-selection-pulse');
            setTimeout(function () { bar.classList.remove('uld-selection-pulse'); }, 280);
            _lastSelectionPulseSig = pulseSig;
        }
        if (stackMode) renderSelectedStackTargets();
        else renderSelectionTargets();
        updateSelectionPrepButton(stackMode);
        // Flash bar when first appearing
        if (count > 0 && !wasActive) {
            bar.classList.remove('uld-selection-flash');
            void bar.offsetWidth;
            bar.classList.add('uld-selection-flash');
            setTimeout(function () { bar.classList.remove('uld-selection-flash'); }, 600);
        }
    }

    // Selection-bar "Összekészít(ve)" button reflects the prepared state of the
    // currently selected stacks: if every selected non-empty stack is already
    // prepared, the button shows the active "✓ Összekészítve" state and toggles
    // them OFF; otherwise it marks the unprepared ones. Keeps the bar button in
    // sync with the per-stack prep buttons.
    function updateSelectionPrepButton(stackMode) {
        var btn = document.querySelector('.uld-selection-stack-prep-btn');
        if (!btn) return;
        if (!stackMode) {
            btn.classList.remove('uld-selection-prep-active');
            btn.textContent = 'Kész';
            btn.dataset.uldIdleLabel = btn.textContent;
            btn.dataset.prepMode = 'prepare';
            btn.title = 'Kijelölt stackek készre jelölése';
            return;
        }
        var sel = selectedStackElements().filter(function (s) {
            return s.querySelectorAll('.uld-si[data-uld]').length > 0;
        });
        var prepared = sel.filter(isPreparedStack);
        var allPrepared = sel.length > 0 && prepared.length === sel.length;
        btn.classList.toggle('uld-selection-prep-active', allPrepared);
        btn.textContent = allPrepared ? 'Kész' : 'Készre jelöl';
        btn.dataset.uldIdleLabel = btn.textContent;
        btn.dataset.prepMode = allPrepared ? 'unprepare' : 'prepare';
        btn.title = allPrepared
            ? 'Kész jelölés törlése from selected stacks'
            : 'Kijelölt stackek készre jelölése';
    }

    function selectedGhaSet() {
        var ghas = new Set();
        selectedUlds.forEach(function (u) {
            var g = uldGha(u);
            if (g) ghas.add(g);
        });
        return ghas;
    }

    function chipCompatible(stackGhaName, selGhas) {
        // empty stack (no GHA) → always compatible
        if (!stackGhaName) return true;
        // selected ULDs have no GHAs known → assume compatible
        if (!selGhas.size) return true;
        // single matching GHA
        return selGhas.size === 1 && selGhas.has(stackGhaName);
    }

    var _lastTargetsSig = '';
    var _selectionRenderInFlight = false;
    function renderSelectedStackTargets() {
        var container = document.getElementById('uld-selection-targets');
        if (!container) return;
        var stacks = stackElements().filter(function (s) { return selectedStackek.has(s.dataset.stackId); });
        var sig = 'STACKS|' + stacks.map(function (s) {
            return s.dataset.stackId + ':' + (s.dataset.stackName || 'Stack') + ':' + s.querySelectorAll('.uld-si[data-uld]').length;
        }).join(';');
        if (sig === _lastTargetsSig) return;
        _lastTargetsSig = sig;
        if (!stacks.length) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = stacks.map(function (s) {
            var name = s.dataset.stackName || 'Stack';
            var count = s.querySelectorAll('.uld-si[data-uld]').length;
            return '<span class="uld-target-chip uld-stack-target-chip">'
                + '<span class="uld-target-chip-name">' + _esc(name) + '</span>'
                + '<span class="uld-target-chip-count">' + count + '</span>'
                + '</span>';
        }).join('');
    }

    function renderSelectionTargets() {
        if (_selectionRenderInFlight) return;
        var container = document.getElementById('uld-selection-targets');
        if (!container) return;
        if (!selectedUlds.size) {
            if (_lastTargetsSig !== '') {
                _selectionRenderInFlight = true;
                container.innerHTML = '';
                _lastTargetsSig = '';
                setTimeout(function () { _selectionRenderInFlight = false; }, 0);
            }
            return;
        }
        var stacks = Array.from(document.querySelectorAll('.uld-stack[data-stack-id]'));
        var selGhas = selectedGhaSet();
        var selGhaSig = Array.from(selGhas).sort().join(',');
        if (!stacks.length) {
            var emptySig = 'EMPTY|' + selGhaSig;
            if (_lastTargetsSig === emptySig) return;
            _selectionRenderInFlight = true;
            container.innerHTML = '<span class="uld-targets-empty">Nincs aktív stack - hozz létre újat</span>';
            _lastTargetsSig = emptySig;
            setTimeout(function () { _selectionRenderInFlight = false; }, 0);
            return;
        }
        var entries = stacks.map(function (s, idx) {
            var sg = stackGha(s);
            var prepped = isPreparedStack(s);
            return {
                el: s,
                id: s.dataset.stackId,
                name: s.dataset.stackName || 'Stack',
                gha: sg,
                compat: !prepped && chipCompatible(sg, selGhas),
                prepared: prepped,
                count: s.querySelectorAll('.uld-si[data-uld]').length,
                order: idx
            };
        });
        entries.sort(function (a, b) {
            if (a.compat !== b.compat) return a.compat ? -1 : 1;
            return a.order - b.order;
        });
        var sig = selGhaSig + '||' + entries.map(function (e) {
            return e.id + ':' + e.name + ':' + e.count + ':' + (e.compat ? '1' : '0') + ':' + (e.prepared ? 'P' : '') + ':' + (e.gha || '');
        }).join(';');
        if (sig === _lastTargetsSig) return;  // idempotent — prevents observer loop
        _selectionRenderInFlight = true;
        _lastTargetsSig = sig;
        var existing = {};
        Array.from(container.querySelectorAll('.uld-target-chip')).forEach(function (el) {
            existing[el.dataset.stackId] = el;
        });
        var seenIds = new Set();
        var prevSibling = null;
        entries.forEach(function (e) {
            seenIds.add(e.id);
            var chip = existing[e.id];
            var reused = !!chip;
            if (!chip) {
                chip = document.createElement('button');
                chip.type = 'button';
                chip.dataset.stackId = e.id;
                chip.style.animation = '';
            }
            chip.className = 'uld-target-chip'
                + (e.compat ? '' : ' uld-target-chip-disabled')
                + (e.prepared ? ' uld-target-chip-prepared' : '');
            chip.dataset.stackName = e.name;
            chip.title = e.prepared
                ? ('Készre jelölt stack – előbb töröld a kész jelölést: ' + e.name)
                : (e.compat
                    ? ('Move selected ULDs: ' + e.name)
                    : ('Incompatible GHA: ' + (e.gha || '?') + ' vs ' + Array.from(selGhas).join(', ')));
            var col = '';
            try {
                col = (e.el && e.el.style && e.el.style.getPropertyValue('--stack-gha-border')) || '';
                col = col.replace(/77$/, '').trim();
            } catch (err) { col = ''; }
            if (e.gha && col) {
                chip.style.setProperty('--chip-color', col);
                chip.style.setProperty('--chip-bg', col + '1a');
                chip.style.setProperty('--chip-border', col + '55');
                chip.style.setProperty('--chip-bg-hover', col + '28');
                chip.style.setProperty('--chip-border-hover', col + '99');
                chip.style.setProperty('--chip-glow', col + '40');
            } else {
                ['--chip-color','--chip-bg','--chip-border','--chip-bg-hover','--chip-border-hover','--chip-glow']
                    .forEach(function (p) { chip.style.removeProperty(p); });
            }
            chip.innerHTML =
                '<span class="uld-target-chip-name">' + _esc(e.name) + '</span>' +
                '<span class="uld-target-chip-count">' + e.count + '</span>';
            // Insert/move in place without disturbing hover state
            var desiredPrev = prevSibling;
            var actualPrev = chip.previousElementSibling;
            if (!reused || chip.parentNode !== container || actualPrev !== desiredPrev) {
                container.insertBefore(chip, desiredPrev ? desiredPrev.nextSibling : container.firstChild);
            }
            prevSibling = chip;
        });
        // Remove stale chips (and any non-chip nodes like the empty hint)
        Array.from(container.children).forEach(function (el) {
            if (!el.classList || !el.classList.contains('uld-target-chip') || !seenIds.has(el.dataset.stackId)) {
                el.remove();
            }
        });
        setTimeout(function () { _selectionRenderInFlight = false; }, 0);
    }

    function _esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
        });
    }

    function animateUldTransferToStack(ulds, targetStack, originEl) {
        if (!targetStack || !ulds || !ulds.length) return;
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        var targetSurface = targetStack.querySelector('.uld-stack-col') || targetStack;
        var targetRect = targetSurface.getBoundingClientRect();
        if (!targetRect.width || !targetRect.height) return;
        targetStack.classList.remove('uld-transfer-target-pulse');
        void targetStack.offsetWidth;
        targetStack.classList.add('uld-transfer-target-pulse');
        setTimeout(function () { targetStack.classList.remove('uld-transfer-target-pulse'); }, 980);

        ulds.slice(0, 6).forEach(function (uld, idx) {
            var sourceEl = rowForUld(uld) || stackItemForUld(uld) || originEl;
            var sourceRect = sourceEl ? sourceEl.getBoundingClientRect() : null;
            if (!sourceRect) return;
            var info = sourceInfo(uld);
            var ghost = document.createElement('div');
            ghost.className = 'uld-transfer-ghost';
            ghost.dataset.prefix = (info.prefix || String(uld || '').slice(0, 3) || 'ULD').toUpperCase();
            ghost.textContent = uld;
            var width = Math.min(Math.max(sourceRect.width || 126, 112), 178);
            ghost.style.left = sourceRect.left + 'px';
            ghost.style.top = (sourceRect.top + Math.min(6, Math.max(0, sourceRect.height - 34) / 2)) + 'px';
            ghost.style.width = width + 'px';
            document.body.appendChild(ghost);

            var endX = targetRect.left + Math.min(Math.max(12, targetRect.width * 0.50 - width / 2), Math.max(12, targetRect.width - width - 12));
            var endY = targetRect.top + Math.min(18 + (idx % 4) * 9, Math.max(18, targetRect.height - 36));
            var dx = endX - sourceRect.left;
            var dy = endY - sourceRect.top;
            ghost.style.transitionDelay = (idx * 28) + 'ms';
            requestAnimationFrame(function () {
                ghost.classList.add('uld-transfer-ghost-flying');
                ghost.style.transform = 'translate3d(' + dx + 'px,' + dy + 'px,0) scale(.72)';
            });
            setTimeout(function () { ghost.remove(); }, 980 + idx * 28);
        });
    }

    function createWithUldsKey(ulds) {
        return Array.from(new Set((ulds || []).map(normalizeUld).filter(Boolean))).sort().join('|');
    }

    function newClientOpId(prefix) {
        return (prefix || 'op') + '-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
    }

    function createStackThenHozzáadás(ulds, triggerBtn) {
        if (!ulds || !ulds.length) return;
        var opKey = createWithUldsKey(ulds);
        if (!opKey) return;
        if (stackCreateBusy()) {
            _uldToast(createWithUldsInFlight.key === opKey ? 'Stack létrehozása folyamatban' : 'Másik stack mentése folyamatban', 'info');
            return;
        }
        var gha = draggedGha(ulds);
        if (gha === '__MIXED__') {
            _uldToast('Több GHA nem kerülhet egy stackbe', 'error');
            return;
        }
        if (!gha) {
            _uldToast('Ismeretlen GHA - nem tehető stackbe', 'error');
            return;
        }
        if (triggerBtn && !guardActionButton(triggerBtn, 'Mentés...')) return;
        var opId = newClientOpId('create-ulds');
        createWithUldsInFlight.key = opKey;
        createWithUldsInFlight.opId = opId;
        window.__uldCreateInFlight = true;
        uldUiState.pendingCreates += 1;
        setCreateControlsBusy(true, triggerBtn);
        window.__uldHighlightNextNewStack = true;
        setUldMode('syncing');
        markRowsStackPending(ulds);
        uldApiCall(
            { action: 'create_with_ulds', uld_numbers: ulds, client_op_id: opId },
            function (data) {
                var createdStackId = data && data.stack ? data.stack.id : '';
                var targetStack = data && data.stack ? stackElById(data.stack.id) : null;
                confirmRowsStackMembership(ulds, createdStackId);
                animateUldTransferToStack(ulds, targetStack, triggerBtn);
                _uldToast(ulds.length + ' ULD új stackbe', 'ok');
            },
            {
                listDirty: true,
                refreshDelay: 0,
                retryTransient: 2,
                rollback: function () { clearRowsStackPending(ulds); },
                done: function () {
                    if (createWithUldsInFlight.opId === opId) {
                        createWithUldsInFlight.key = '';
                        createWithUldsInFlight.opId = '';
                    }
                    uldUiState.pendingCreates = Math.max(0, uldUiState.pendingCreates - 1);
                    window.__uldCreateInFlight = false;
                    setCreateControlsBusy(uldUiState.pendingCreates > 0, triggerBtn);
                    releaseActionButton(triggerBtn);
                    if (!uldUiState.lockedStackek.size && !uldUiState.pendingCreates) setUldMode('idle');
                }
            }
        );
        // Clear immediately (mirrors the add-to-existing-stack path) so the
        // selection and the floating bar vanish at once — the operator gets a
        // clean slate and can't confuse leftover selection with the new stack.
        clearSelection();
    }

    function _highlightNewlyCreatedStackek() {
        if (!window.__uldHighlightNextNewStack) return;
        var stacks = document.querySelectorAll('.uld-stack[data-stack-id]:not([data-highlight-seen="1"])');
        if (!stacks.length) return;
        var newest = stacks[stacks.length - 1];
        if (!newest) return;
        // Mark all current stacks as seen so we only highlight once.
        document.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (s) {
            s.setAttribute('data-highlight-seen', '1');
        });
        window.__uldHighlightNextNewStack = false;
        newest.classList.add('uld-stack-just-created');
        setTimeout(function () { newest.classList.remove('uld-stack-just-created'); }, 900);
    }

    function reconcileSelection() {
        cleanupRowStackBadges();
        var visible = new Set(uldElements().map(function (el) { return el.dataset.uld; }));
        Array.from(selectedUlds).forEach(function (uld) {
            if (!visible.has(uld)) selectedUlds.delete(uld);
            // Drop any ULD that has since landed in a prepared stack (e.g., another
            // user toggled prepared while we had it selected).
            else if (uldIsInPreparedStack(uld)) selectedUlds.delete(uld);
        });
        var visibleStackek = new Set(stackElements().map(function (el) { return el.dataset.stackId; }));
        Array.from(selectedStackek).forEach(function (stackId) {
            if (!visibleStackek.has(stackId)) selectedStackek.delete(stackId);
        });
        applySelectionClasses();
    }

    function clearSelection() {
        selectedUlds.clear();
        selectedStackek.clear();
        applySelectionClasses();
    }

    function toggleStackSelection(stackEl) {
        var stackId = stackEl && stackEl.dataset ? stackEl.dataset.stackId : '';
        if (!stackId) return;
        selectedUlds.clear();
        if (selectedStackek.has(stackId)) selectedStackek.delete(stackId);
        else selectedStackek.add(stackId);
        applySelectionClasses();
    }

    function toggleSelection(el) {
        var uld = el && el.dataset ? el.dataset.uld : '';
        if (!uld) return;
        // Prepared-locked ULDs cannot be selected — user clarified.
        if (uldIsInPreparedStack(uld)) {
            _uldToast('Kész stack - nem jelölhető ki', 'info');
            return;
        }
        selectedStackek.clear();
        if (selectedUlds.has(uld)) selectedUlds.delete(uld);
        else {
            selectedUlds.add(uld);
            warnNearMatchSelection(el);
        }
        applySelectionClasses();
    }

    function selectOnly(el) {
        var uld = el && el.dataset ? el.dataset.uld : '';
        if (!uld) return;
        if (uldIsInPreparedStack(uld)) {
            _uldToast('Kész stack - nem jelölhető ki', 'info');
            return;
        }
        selectedStackek.clear();
        selectedUlds.clear();
        selectedUlds.add(uld);
        warnNearMatchSelection(el);
        applySelectionClasses();
    }

    function warnNearMatchSelection(el) {
        if (!el || !el.dataset || el.dataset.searchMatchKind !== 'near') return;
        var requested = el.dataset.searchRequested || '';
        _uldToast('Közeli találat kijelölve - keresve: ' + requested, 'info');
    }

    function visibleOrderedSelection(src) {
        var ulds = uldElements()
            .filter(function (el) { return selectedUlds.has(el.dataset.uld); })
            .map(function (el) { return el.dataset.uld; });
        if (src && !selectedUlds.has(src.dataset.uld)) ulds = [src.dataset.uld];
        return Array.from(new Set(ulds));
    }

    function stackDraggedUlds(ulds) {
        return ulds.filter(function (uld) {
            return !!document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]');
        });
    }

    function snapshotRects(ulds) {
        var rects = {};
        ulds.forEach(function (uld) {
            var stackItem = document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]');
            var listItem  = document.querySelector('.uld-row[data-uld="' + cssEsc(uld) + '"]');
            var el = stackItem || listItem;
            if (!el) return;
            var r = el.getBoundingClientRect();
            rects[uld] = { left: r.left, top: r.top };
        });
        return rects;
    }

    function destinationFor(uld) {
        return document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]')
            || document.querySelector('.uld-row[data-uld="' + cssEsc(uld) + '"]');
    }

    function runFlip(rects) {
        Object.keys(rects || {}).forEach(function (uld) {
            var el = destinationFor(uld);
            if (!el) return;
            var from = rects[uld];
            var to = el.getBoundingClientRect();
            var dx = from.left - to.left;
            var dy = from.top - to.top;
            if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
            el.classList.add('uld-flip-moving');
            el.style.transition = 'none';
            el.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
            requestAnimationFrame(function () {
                el.style.transition = 'transform .34s cubic-bezier(.22,1,.36,1), opacity .24s ease, box-shadow .24s ease';
                el.style.transform = '';
                setTimeout(function () {
                    el.style.transition = '';
                    el.classList.remove('uld-flip-moving');
                }, 380);
            });
        });
        reconcileSelection();
    }

    function scheduleFlip(rects) {
        setTimeout(function () { runFlip(rects); }, 120);
        setTimeout(function () { runFlip(rects); }, 320);
    }

    function markMoving(ulds, cls) {
        uldElementsFor(ulds).forEach(function (el) { el.classList.add(cls); });
    }

    function stackItems(stackEl) {
        return Array.from(stackEl.querySelectorAll('.uld-si[data-uld]'));
    }

    function stackCol(stackEl) {
        return stackEl ? stackEl.querySelector('.uld-stack-col') : null;
    }

    function stackDropItems(stackEl, movingUlds) {
        var moving = new Set(movingUlds || []);
        return stackItems(stackEl).filter(function (el) {
            return !moving.has(el.dataset.uld || '');
        });
    }

    function stackUsesGridPlacement(stackEl) {
        var col = stackCol(stackEl);
        if (!col || !window.getComputedStyle) return false;
        var cs = window.getComputedStyle(col);
        if (!cs || String(cs.display || '').indexOf('grid') === -1) return false;
        var cols = String(cs.gridTemplateColumns || '').trim();
        if (!cols || cols === 'none') return false;
        return cols.split(/\s+/).filter(Boolean).length > 1;
    }

    function visualRowsForItems(items) {
        var entries = items.map(function (el, index) {
            return { el: el, index: index, rect: el.getBoundingClientRect() };
        }).filter(function (entry) {
            return entry.rect && entry.rect.width > 0 && entry.rect.height > 0;
        }).sort(function (a, b) {
            return (a.rect.top - b.rect.top) || (a.rect.left - b.rect.left) || (a.index - b.index);
        });
        var rows = [];
        entries.forEach(function (entry) {
            var matched = null;
            for (var i = 0; i < rows.length; i += 1) {
                if (Math.abs(entry.rect.top - rows[i].top) <= 8) {
                    matched = rows[i];
                    break;
                }
            }
            if (!matched) {
                matched = { top: entry.rect.top, bottom: entry.rect.bottom, items: [] };
                rows.push(matched);
            }
            matched.top = Math.min(matched.top, entry.rect.top);
            matched.bottom = Math.max(matched.bottom, entry.rect.bottom);
            matched.items.push(entry);
        });
        rows.sort(function (a, b) { return a.top - b.top; });
        rows.forEach(function (row) {
            row.items.sort(function (a, b) { return (a.rect.left - b.rect.left) || (a.index - b.index); });
        });
        return rows;
    }

    function gridDropPlacement(items, clientX, clientY) {
        var rows = visualRowsForItems(items);
        if (!rows.length) return { index: 0, targetItem: null, below: false, edge: 'above' };
        if (!isFinite(clientX)) clientX = rows[0].items[0].rect.left;
        var row = rows[rows.length - 1];
        for (var r = 0; r < rows.length; r += 1) {
            var midY = rows[r].top + (rows[r].bottom - rows[r].top) / 2;
            if (clientY < midY) {
                row = rows[r];
                break;
            }
        }
        for (var i = 0; i < row.items.length; i += 1) {
            var entry = row.items[i];
            if (clientX < entry.rect.left + entry.rect.width / 2) {
                return { index: entry.index, targetItem: entry.el, below: false, edge: 'before' };
            }
        }
        var last = row.items[row.items.length - 1];
        return { index: Math.min(items.length, last.index + 1), targetItem: last.el, below: true, edge: 'after' };
    }

    function dropPlacement(stackEl, clientX, clientY, movingUlds) {
        if (Array.isArray(clientY) || typeof movingUlds === 'undefined') {
            movingUlds = clientY;
            clientY = clientX;
            clientX = NaN;
        }
        var items = stackDropItems(stackEl, movingUlds);
        if (!items.length) return { index: 0, targetItem: null, below: false, edge: 'above' };
        if (stackUsesGridPlacement(stackEl)) return gridDropPlacement(items, clientX, clientY);
        var index = items.length;
        for (var i = 0; i < items.length; i += 1) {
            var rect = items[i].getBoundingClientRect();
            if (clientY < rect.top + rect.height / 2) {
                index = i;
                break;
            }
        }
        if (index < items.length) {
            return { index: index, targetItem: items[index], below: false, edge: 'above' };
        }
        return { index: items.length, targetItem: items[items.length - 1], below: true, edge: 'below' };
    }

    function rowForUld(uld) {
        // Try raw form first, then normalized — row may be rendered with either depending on context.
        var sel = document.querySelector('.uld-row[data-uld="' + cssEsc(uld) + '"]');
        if (sel) return sel;
        var n = normalizeUld(uld);
        if (n && n !== uld) {
            return document.querySelector('.uld-row[data-uld="' + cssEsc(n) + '"]');
        }
        return null;
    }
    function stackItemForUld(uld) {
        // Server normalizes ULD numbers — stack items use normalized data-uld.
        var n = normalizeUld(uld);
        return document.querySelector('.uld-si[data-uld="' + cssEsc(n || uld) + '"]')
            || (n && n !== uld ? document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]') : null);
    }
    function stackItemsForUld(uld) {
        var n = normalizeUld(uld);
        var result = [];
        var byNorm = document.querySelectorAll('.uld-si[data-uld="' + cssEsc(n || uld) + '"]');
        byNorm.forEach(function (el) { result.push(el); });
        if (n && n !== uld) {
            var byRaw = document.querySelectorAll('.uld-si[data-uld="' + cssEsc(uld) + '"]');
            byRaw.forEach(function (el) { if (result.indexOf(el) === -1) result.push(el); });
        }
        return result;
    }

    // ── "Összekészítve" lock helpers ───────────────────────────────────────
    // Prepared (összekészítve) stacks lock the ULDs inside them — no drag in/out,
    // no reorder, no × remove, no chip add, no inclusion in "+ Új stack" batch,
    // no selection. The buttons "Összekészítve" toggle and × delete still work.
    function isPreparedStack(stackEl) {
        if (!stackEl || !stackEl.classList) return false;
        return stackEl.classList.contains('uld-stack-prepared')
            || stackEl.getAttribute('data-prepared') === '1';
    }
    function uldIsInPreparedStack(uld) {
        var si = stackItemForUld(uld);
        if (!si) return false;
        var stk = si.closest('.uld-stack[data-stack-id]');
        return isPreparedStack(stk);
    }
    function elementIsLockedByPrepared(el) {
        if (!el) return false;
        var stack = el.closest('.uld-stack[data-stack-id]');
        if (isPreparedStack(stack)) return true;
        if (el.dataset && el.dataset.uld) return uldIsInPreparedStack(el.dataset.uld);
        return false;
    }

    var ULD_PREFIX_COLORS = {
        PMC: '#6366f1',
        PMD: '#8b5cf6',
        PAG: '#ec4899',
        PAP: '#f43f5e',
        LD3: '#f97316',
        LD7: '#fb923c',
        LD1: '#eab308',
        AKE: '#06b6d4',
        AKH: '#0ea5e9',
        DQF: '#10b981',
        RKN: '#14b8a6',
        NKA: '#3b82f6',
        NKB: '#60a5fa',
        HMA: '#a78bfa',
        HMJ: '#c084fc'
    };

    function prefixColorFor(prefix) {
        return ULD_PREFIX_COLORS[String(prefix || '').slice(0, 3).toUpperCase()] || '#64748b';
    }

    function prefixStyle(color) {
        var safe = /^#[0-9a-f]{6}$/i.test(color || '') ? color : '#64748b';
        return 'background:' + safe + '22;color:' + safe + ';border:1px solid ' + safe + '55';
    }

    // Fixed brand color per GHA — mirrors _GHA_FIXED_COLORS in app.py so client
    // rendered tables/popups match the server-side stack colors.
    var GHA_FIXED_COLORS = { 'menzies': '#ffd60a', 'celebi': '#ff6b35', 'as cargo': '#32ade6' };
    function ghaColorFor(gha) {
        return GHA_FIXED_COLORS[String(gha || '').trim().toLowerCase()] || '';
    }
    // Black or white text for readable contrast on a solid hex background.
    function contrastText(hex) {
        var c = String(hex || '').replace('#', '');
        if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
        if (c.length !== 6) return '#ffffff';
        var r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? '#1a1a1a' : '#ffffff';
    }
    // Inline style for a GHA badge tinted with the handler's fixed color.
    function ghaBadgeStyle(gha) {
        var col = ghaColorFor(gha);
        if (!col) return '';
        return ' style="color:' + col + ';background:' + col + '22;border-color:' + col + '66;"';
    }
    // Single uniform GHA name for a stack element (server data-gha, else items).
    function stackGhaName(stackEl, items) {
        var g = (stackEl && stackEl.dataset && stackEl.dataset.gha || '').trim();
        if (g) return g;
        var found = '';
        (items || []).forEach(function (it) {
            var ig = (it.gha || '').trim();
            if (ig) { if (!found) found = ig; else if (found !== ig) found = '__MIXED__'; }
        });
        return found === '__MIXED__' ? '' : found;
    }

    // Inline tint for the round order chip (matches the Python render).
    function indexStyle(color) {
        var safe = /^#[0-9a-f]{6}$/i.test(color || '') ? color : '#64748b';
        return 'color:' + safe + ';border-color:' + safe + '88;background:' + safe + '1f';
    }

    function sourceInfo(uld) {
        var el = rowForUld(uld) || document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]');
        var ds = el && el.dataset ? el.dataset : {};
        var cls = el && el.className ? String(el.className) : '';
        var status = ds.status || '';
        if (!status) {
            var m = cls.match(/uld-status-([a-z]+)/) || cls.match(/uld-(?:row|si)-status-([a-z]+)/) || cls.match(/uld-si-([a-z]+)/);
            status = m ? m[1] : 'inactive';
        }
        var prefix = (ds.prefix || uld.slice(0, 3) || 'ULD').toUpperCase();
        // Expiry: prefer data-expiry on the element; fall back to the .uld-countdown[data-expiry] inside the row.
        var expiry = ds.expiry || '';
        if (!expiry && el) {
            var cd = el.querySelector ? el.querySelector('.uld-countdown[data-expiry]') : null;
            if (cd) expiry = cd.dataset.expiry || '';
        }
        return {
            uld: uld,
            prefix: prefix,
            prefixColor: ds.prefixColor || prefixColorFor(prefix),
            gha: ds.gha || '',
            awbs: ds.awbs || '',
            status: status || 'inactive',
            active: status !== 'inactive',
            expiry: expiry,
            am: ds.am || '',
            szerkesztve: ds.szerkesztve === '1',
            szerkesztveAt: ds.szerkesztveAt || '',
            szerkesztveBy: ds.szerkesztveBy || '',
            changeSummary: ds.changeSummary || ''
        };
    }

    function makeStackItem(uld, source) {
        var info = source || sourceInfo(uld);
        // Server stores normalized ULDs — optimistic items must use the same form
        // or React will treat optimistic vs server-rendered as different items → duplicates.
        var normUld = normalizeUld(info.uld);
        var displayUld = info.uld;
        var item = document.createElement('div');
        item.className = 'uld-si uld-si-' + info.status + (info.active ? '' : ' uld-si-inactive') + ' uld-optimistic';
        item.draggable = true;
        item.dataset.uld = normUld || displayUld;
        item.dataset.prefix = info.prefix;
        item.dataset.prefixColor = info.prefixColor || prefixColorFor(info.prefix);
        item.dataset.gha = info.gha;
        item.dataset.awbs = info.awbs;
        item.dataset.status = info.status;
        if (info.expiry) item.dataset.expiry = info.expiry;
        if (info.am) item.dataset.am = info.am;
        item.dataset.szerkesztve = info.szerkesztve ? '1' : '0';
        item.dataset.szerkesztveAt = info.szerkesztveAt || '';
        item.dataset.szerkesztveBy = info.szerkesztveBy || '';
        item.dataset.changeSummary = info.changeSummary || info.change_summary || '';
        item.innerHTML =
            '<span class="uld-si-index" style="' + indexStyle(item.dataset.prefixColor) + '"></span>' +
            '<div class="uld-si-top">' +
                '<div class="uld-si-title-line">' +
                    '<span class="uld-si-num" title="Dupla kattintás az átnevezéshez">' + _esc(displayUld) + '</span>' +
                    (info.active ? '' : '<span class="uld-si-inactive-badge">inaktív</span>') +
                    (info.status === 'kézi' ? '<span class="uld-si-kézi-tag" title="Kézzel hozzáadott ULD">kézi</span>' : '') +
                    (info.szerkesztve ? '<span class="uld-si-szerkesztve-tag" title="' + _esc(formatUldEditAudit(info)) + '">szerkesztve</span>' : '') +
                '</div>' +
                '<span class="uld-si-pos"></span>' +
            '</div>' +
            '<button class="uld-si-remove" title="Eltávolítás stackből" data-uld="' + _esc(item.dataset.uld) + '">×</button>';
        return item;
    }

    function makeServerStackItem(info) {
        var item = makeStackItem(info.uld, {
            uld: info.uld,
            prefix: (info.prefix || String(info.uld || '').slice(0, 3) || 'ULD').toUpperCase(),
            prefixColor: info.prefix_color || prefixColorFor(info.prefix || info.uld),
            gha: info.gha || '',
            awbs: Array.isArray(info.awbs) ? info.awbs.join(', ') : (info.awbs || ''),
            status: info.status || 'inactive',
            active: info.active !== false,
            expiry: info.expiry || '',
            am: info.am || '',
            szerkesztve: !!info.is_szerkesztve,
            szerkesztveAt: info.szerkesztve_at || '',
            szerkesztveBy: info.szerkesztve_by || '',
            changeSummary: info.change_summary || ''
        });
        item.classList.remove('uld-optimistic');
        var pos = item.querySelector('.uld-si-pos');
        if (pos) {
            pos.textContent = info.position_label || '';
            pos.classList.toggle('uld-si-pos-empty', !pos.textContent);
        }
        return item;
    }

    function makeServerStackCard(stack) {
        var card = document.createElement('div');
        // Accordion structure must mirror Python _make_stack_column so the
        // collapse logic works on optimistically-created cards too.
        card.className = 'uld-stack uld-stack-accordion';
        card.dataset.stackId = stack.id || '';
        card.innerHTML =
            '<div class="uld-stack-hdr">' +
                '<span class="uld-stack-caret" aria-hidden="true">▸</span>' +
                '<span class="uld-stack-name" title="Dupla kattintás az átnevezéshez"></span>' +
                '<span class="uld-stack-cnt"></span>' +
                '<div class="uld-stack-actions">' +
                    '<button class="uld-stack-act uld-stack-select" title="Stack kijelölése">Kijelölés</button>' +
                    '<button class="uld-stack-act uld-stack-prep-btn uld-stack-prep-empty" title="Stack készre jelölése"></button>' +
                    '<button class="uld-stack-act uld-stack-dispatch-btn uld-stack-dispatch-disabled" title="Üres stack - előbb adj hozzá ULD-t" disabled>Kiadás</button>' +
                    '<button class="uld-stack-act uld-stack-ico uld-stack-print" title="Nyomtatás" aria-label="Nyomtatás"></button>' +
                    '<button class="uld-stack-act uld-stack-ico uld-stack-copy" title="Másolás vágólapra" aria-label="Másolás"></button>' +
                    '<button class="uld-stack-act uld-stack-ico uld-stack-del" title="Stack törlése" aria-label="Stack törlése"></button>' +
                '</div>' +
            '</div>' +
            '<div class="uld-stack-body">' +
                '<div class="uld-stack-created-meta"></div>' +
                '<div class="uld-stack-col"></div>' +
            '</div>';
        return card;
    }

    function updateStackActionControls(stackEl, prepared, hasItems) {
        if (!stackEl) return;
        prepared = !!prepared;
        hasItems = !!hasItems;
        stackEl.dataset.prepared = prepared ? '1' : '0';
        stackEl.classList.toggle('uld-stack-prepared', prepared);
        var actions = stackEl.querySelector('.uld-stack-actions');
        var prepBtn = stackEl.querySelector('.uld-stack-prep-btn');
        if (!prepBtn && actions) {
            prepBtn = document.createElement('button');
            prepBtn.className = 'uld-stack-act uld-stack-prep-btn uld-stack-prep-empty';
            var printAnchor = actions.querySelector('.uld-stack-dispatch-btn, .uld-stack-print, .uld-stack-copy, .uld-stack-del');
            actions.insertBefore(prepBtn, printAnchor || null);
        }
        if (prepBtn) {
            prepBtn.dataset.stackId = stackEl.dataset.stackId || '';
            prepBtn.disabled = !hasItems && !prepared;
            // ✓ stays as long as the stack is prepared (matches server render).
            prepBtn.textContent = prepared ? 'Kész' : 'Készre jelöl';
            prepBtn.dataset.uldIdleLabel = prepBtn.textContent;
            prepBtn.className = 'uld-stack-act uld-stack-prep-btn ' + (prepared ? 'uld-stack-prep-active' : ('uld-stack-prep-empty' + (!hasItems ? ' uld-stack-prep-disabled' : '')));
            prepBtn.title = prepared
                ? 'Kész jelölés törlése'
                : (!hasItems ? 'Üres stack - előbb adj hozzá ULD-t' : 'Stack készre jelölése');
        }
        var dispatchBtn = stackEl.querySelector('.uld-stack-dispatch-btn');
        if (!dispatchBtn && actions) {
            dispatchBtn = document.createElement('button');
            dispatchBtn.className = 'uld-stack-act uld-stack-dispatch-btn';
            var dAnchor = actions.querySelector('.uld-stack-print, .uld-stack-copy, .uld-stack-del');
            actions.insertBefore(dispatchBtn, dAnchor || null);
        }
        if (dispatchBtn) {
            dispatchBtn.dataset.stackId = stackEl.dataset.stackId || '';
            dispatchBtn.textContent = 'Kiadás';
            dispatchBtn.disabled = !hasItems;
            dispatchBtn.className = 'uld-stack-act uld-stack-dispatch-btn' + (!hasItems ? ' uld-stack-dispatch-disabled' : '');
            dispatchBtn.title = hasItems ? 'Stack kiadása autóra' : 'Üres stack - előbb adj hozzá ULD-t';
        }
        var printBtn = stackEl.querySelector('.uld-stack-print');
        if (!printBtn && actions) {
            printBtn = document.createElement('button');
            printBtn.className = 'uld-stack-act uld-stack-ico uld-stack-print';
            var copyAnchor = actions.querySelector('.uld-stack-copy, .uld-stack-del');
            actions.insertBefore(printBtn, copyAnchor || null);
        }
        if (printBtn) {
            printBtn.textContent = '';
            printBtn.className = 'uld-stack-act uld-stack-ico uld-stack-print';
            printBtn.title = 'Nyomtatás';
        }
        var copyBtn = stackEl.querySelector('.uld-stack-copy');
        if (!copyBtn && actions) {
            copyBtn = document.createElement('button');
            copyBtn.className = 'uld-stack-act uld-stack-ico uld-stack-copy';
            var delAnchor = actions.querySelector('.uld-stack-del');
            actions.insertBefore(copyBtn, delAnchor || null);
        }
        if (copyBtn) {
            copyBtn.textContent = '';
            copyBtn.className = 'uld-stack-act uld-stack-ico uld-stack-copy';
            copyBtn.title = 'Másolás vágólapra';
        }
        var delBtn = stackEl.querySelector('.uld-stack-del');
        if (delBtn) {
            delBtn.textContent = '';
            delBtn.className = 'uld-stack-act uld-stack-ico uld-stack-del';
        }
    }

    function applyServerStackCard(stack) {
        if (!stack || !stack.id) return null;
        var grid = document.getElementById('uld-stacks-grid');
        if (!grid) return null;
        var items = Array.isArray(stack.items_top_to_bottom) ? stack.items_top_to_bottom : [];
        var card = grid.querySelector('.uld-stack[data-stack-id="' + cssEsc(stack.id) + '"]');
        if (!card) {
            // Never hand-build an *empty* stack card. An optimistic empty card has
            // no purpose (only stacks-with-ULDs need a fly-animation target), and a
            // client-made empty node inside the React-managed grid has no server
            // twin to dedup against — it ends up "glued" to a sibling card (same
            // data-stack-id), so selecting / moving / deleting one hits both. Let
            // Dash/React own empty stacks; only cards carrying ULDs get an optimistic
            // card here.
            if (!items.length) return null;
            card = makeServerStackCard(stack);
            // Mark client-built cards so the stale-card sweep can safely drop them
            // (they are not React-owned), while leaving Dash-owned cards for React.
            card.classList.add('uld-stack-clientmade');
            grid.appendChild(card);
        }

        card.dataset.stackId = stack.id;
        card.dataset.stackName = stack.display_name || stack.name || 'Stack';
        card.dataset.rawStackName = stack.name || 'Stack';
        card.dataset.stackRevision = String(stack.revision || 0);
        card.dataset.createdAt = stack.created_at || '';
        card.dataset.createdBy = stack.created_by || '';
        card.dataset.gha = stack.single_gha || '';
        try { card.dataset.stackJson = JSON.stringify(items); } catch (e) { card.dataset.stackJson = '[]'; }
        updateStackActionControls(card, !!stack.prepared, items.length > 0);
        if (stack.gha_color) {
            card.style.setProperty('--stack-gha-border', stack.gha_color + '77');
            card.style.setProperty('--stack-gha-glow', stack.gha_color + '18');
        } else {
            card.style.removeProperty('--stack-gha-border');
            card.style.removeProperty('--stack-gha-glow');
        }

        var nameEl = card.querySelector('.uld-stack-name');
        if (nameEl) nameEl.textContent = stack.display_name || stack.name || 'Stack';
        var ghaEl = card.querySelector('.uld-stack-gha-badge');
        if (ghaEl) {
            ghaEl.textContent = stack.single_gha || '';
            ghaEl.style.display = stack.single_gha ? '' : 'none';
            if (stack.gha_color) {
                ghaEl.style.setProperty('--gha-color', stack.gha_color);
                ghaEl.style.setProperty('--gha-bg', stack.gha_color + '22');
                ghaEl.style.setProperty('--gha-border', stack.gha_color + '55');
            } else {
                ghaEl.style.removeProperty('--gha-color');
                ghaEl.style.removeProperty('--gha-bg');
                ghaEl.style.removeProperty('--gha-border');
            }
        }
        var delBtn = card.querySelector('.uld-stack-del');
        if (delBtn) {
            delBtn.dataset.stackId = stack.id;
            delBtn.textContent = '×';
        }
        var createdMeta = card.querySelector('.uld-stack-created-meta');
        if (createdMeta) createdMeta.textContent = formatStackCreatedMeta(stack);

        var col = stackCol(card);
        if (col) {
            col.innerHTML = '';
            items.forEach(function (info) {
                col.appendChild(makeServerStackItem(info));
            });
        }
        refreshStackMeta(card);
        card.classList.remove('uld-stack-syncing', 'uld-stack-op-locked');
        return card;
    }

    function refreshVisibleRowBadgesFromState(stacks) {
        var byUld = {};
        (stacks || []).forEach(function (stack) {
            (stack.items_top_to_bottom || []).forEach(function (item) {
                byUld[item.uld] = {
                    name: stack.display_name || stack.name || 'Stack',
                    color: stack.gha_color || ''
                };
            });
        });
        document.querySelectorAll('.uld-row[data-uld]').forEach(function (row) {
            var entry = byUld[row.dataset.uld];
            if (entry) {
                row.style.setProperty('--stack-accent', entry.color || 'rgba(99,102,241,.55)');
                row.style.setProperty('--stack-accent-bg', entry.color ? entry.color + '14' : 'rgba(99,102,241,.06)');
                setRowStackBadge(row.dataset.uld, entry.name);
            } else {
                row.style.removeProperty('--stack-accent');
                row.style.removeProperty('--stack-accent-bg');
                clearRowStackBadge(row.dataset.uld);
            }
        });
    }

    function applyServerRevisionState(state) {
        if (!state || !Array.isArray(state.stacks)) return;
        state.stacks.forEach(function (stack) {
            if (!stack || !stack.id) return;
            var card = stackElById(stack.id);
            if (!card) return;
            card.dataset.stackRevision = String(stack.revision || 0);
            card.dataset.gha = stack.single_gha || card.dataset.gha || '';
            card.dataset.prepared = stack.prepared ? '1' : '0';
        });
    }

    function applyServerStackState(state) {
        if (!state || !Array.isArray(state.stacks)) return;
        var grid = document.getElementById('uld-stacks-grid');
        if (!grid) return;
        var seen = new Set();
        state.stacks.forEach(function (stack) {
            if (stack && stack.id && isStackTörlésSuppressed(stack.id)) return;
            var card = applyServerStackCard(stack);
            if (card) seen.add(stack.id);
            if (stack && stack.id) uldUiState.lockedStackek.delete(stack.id);
        });
        // Hide (never hand-remove) the React-owned "create a stack" placeholder once
        // a real stack card exists. Removing a React-owned node by hand desyncs
        // React — its next reconciliation calls removeChild on an already-detached
        // node and throws mid-commit, which is exactly what left orphan/duplicate
        // empty cards behind. Hiding keeps the node React-owned so React removes it
        // cleanly on the imminent re-render. Only hide when a card is actually shown,
        // so an empty "+ Új stack" (no optimistic card) still shows the hint until
        // Dash renders the new empty card.
        if (grid.querySelector('.uld-stack[data-stack-id]')) {
            grid.querySelectorAll('.uld-stacks-empty').forEach(function (el) { el.style.display = 'none'; });
        }
        grid.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (card) {
            if (seen.has(card.dataset.stackId)) return;
            // A card the server no longer reports. If WE created it optimistically
            // (not yet React-owned) it's safe to drop outright; otherwise just hide
            // it and let the imminent Dash re-render unmount the real node — removing
            // a React-owned node by hand desyncs React and can take a sibling with it.
            if (card.classList.contains('uld-optimistic') || card.classList.contains('uld-stack-clientmade')) {
                card.remove();
            } else {
                card.classList.add('uld-stack-gone');
            }
        });
        if (!state.stacks.length && !grid.querySelector('.uld-stack[data-stack-id]')) {
            var existingEmpty = grid.querySelector('.uld-stacks-empty');
            if (existingEmpty) {
                existingEmpty.style.display = '';   // unhide the React-owned hint
            } else {
                var empty = document.createElement('div');
                empty.className = 'uld-stacks-empty';
                empty.textContent = 'Hozz létre stacket a gombbal.';
                grid.appendChild(empty);
            }
        }
        refreshVisibleRowBadgesFromState(state.stacks);
        cleanupRowStackBadges();
        reconcileSelection();
        setUldMode(uldUiState.lockedStackek.size ? 'syncing' : 'idle');
    }

    function setRowStackBadge(uld, stackName) {
        var row = rowForUld(uld);
        if (!row) return;
        row.classList.add('uld-row-in-stack');
        var meta = row.querySelector('.uld-row-meta');
        if (!meta) return;
        var badges = Array.from(meta.querySelectorAll('.uld-in-stack-badge'));
        var badge = badges[0];
        badges.slice(1).forEach(function (extra) { extra.remove(); });
        if (!badge) {
            badge = document.createElement('span');
            badge.className = 'uld-in-stack-badge';
            meta.insertBefore(badge, meta.firstChild);
        }
        badge.textContent = stackName || 'Stack';
    }

    function clearRowStackBadge(uld) {
        var row = rowForUld(uld);
        if (!row) return;
        row.classList.remove('uld-row-in-stack');
        row.querySelectorAll('.uld-in-stack-badge').forEach(function (badge) {
            if (badge.parentNode) badge.parentNode.removeChild(badge);
        });
    }

    function cleanupRowStackBadges() {
        document.querySelectorAll('.uld-row[data-uld]').forEach(function (row) {
            var badges = Array.from(row.querySelectorAll('.uld-in-stack-badge'));
            badges.slice(1).forEach(function (extra) { extra.remove(); });
            if (!badges.length) row.classList.remove('uld-row-in-stack');
        });
    }

    // Defensive: walk the stack DOM and reapply prepared classes to list rows
    // and to ULD items inside prepared stacks. Survives Dash re-renders that
    // race ahead of the server's prepared-state write.
    function applyPreparedFromDom() {
        var preparedUlds = new Set();
        document.querySelectorAll('.uld-stack[data-prepared="1"]').forEach(function (stackEl) {
            stackEl.classList.add('uld-stack-prepared');
            stackEl.querySelectorAll('.uld-si[data-uld]').forEach(function (si) {
                preparedUlds.add(si.dataset.uld);
                si.dataset.prepared = '1';
            });
        });
        document.querySelectorAll('.uld-stack:not([data-prepared="1"])').forEach(function (stackEl) {
            if (stackEl.dataset.prepared === '0') stackEl.classList.remove('uld-stack-prepared');
        });
        document.querySelectorAll('.uld-row[data-uld]').forEach(function (row) {
            var inPrepared = preparedUlds.has(row.dataset.uld);
            row.classList.toggle('uld-row-prepared', inPrepared);
            row.dataset.prepared = inPrepared ? '1' : '0';
        });
    }

    function defaultStackName(name) {
        var value = String(name || '').trim().toLowerCase();
        return !value || /^stack\s+\d+$/.test(value) || /^gha\s+\d+$/.test(value);
    }

    function stackGhaFromItems(stackEl) {
        var ghas = new Set();
        stackItems(stackEl).forEach(function (el) {
            var gha = (el.dataset.gha || '').trim();
            if (gha) ghas.add(gha);
        });
        return ghas.size === 1 ? Array.from(ghas)[0] : '';
    }

    function displayNameForStack(stackEl) {
        if (!stackEl) return 'Stack';
        var raw = stackEl.dataset.rawStackName || stackEl.dataset.stackName || 'Stack';
        var singleGha = stackGhaFromItems(stackEl);
        if (singleGha && defaultStackName(raw)) {
            var current = (stackEl.dataset.stackName || '').trim();
            if (current && current !== raw && current.indexOf(singleGha) === 0) return current;
            return singleGha;
        }
        return raw || 'Stack';
    }

    function refreshStackDisplayName(stackEl) {
        if (!stackEl) return 'Stack';
        var name = displayNameForStack(stackEl);
        stackEl.dataset.stackName = name;
        var label = stackEl.querySelector('.uld-stack-name');
        if (label) label.textContent = name;
        return name;
    }

    function refreshStackPositions(stackEl) {
        var items = stackItems(stackEl);
        items.forEach(function (el, idx) {
            var isTop = idx === 0;
            var isBottom = idx === items.length - 1 && items.length > 1;
            var text = isTop ? 'top' : (isBottom ? 'bottom' : '');
            var top = el.querySelector('.uld-si-top') || el;
            var pos = el.querySelector('.uld-si-pos');
            if (!pos) {
                pos = document.createElement('span');
                pos.className = 'uld-si-pos';
                top.appendChild(pos);
            }
            pos.textContent = text;
            pos.classList.toggle('uld-si-pos-empty', !text);
            // Keep the round order chip numbered + oriented (matches Python render).
            var chip = el.querySelector('.uld-si-index');
            if (chip) {
                chip.textContent = String(idx + 1);
                chip.classList.toggle('uld-si-index-top', isTop);
                chip.classList.toggle('uld-si-index-bottom', isBottom);
                chip.title = isTop ? ((idx + 1) + '. - stack teteje')
                    : (isBottom ? ((idx + 1) + '. - stack alja')
                        : ((idx + 1) + '. felülről'));
            }
        });
    }

    function refreshStackMeta(stackEl) {
        if (!stackEl) return;
        refreshStackPositions(stackEl);
        var items = stackItems(stackEl);
        var countEl = stackEl.querySelector('.uld-stack-cnt');
        if (countEl) {
            countEl.textContent = items.length ? (items.length + ' db') : '';
            countEl.style.display = items.length ? '' : 'none';
        }
        updateStackActionControls(stackEl, isPreparedStack(stackEl), items.length > 0);
        var singleGha = stackGhaFromItems(stackEl);
        stackEl.dataset.gha = singleGha || '';
        var ghaEl = stackEl.querySelector('.uld-stack-gha-badge');
        if (ghaEl) {
            ghaEl.textContent = singleGha || '';
            ghaEl.style.display = singleGha ? '' : 'none';
            if (!singleGha) {
                ghaEl.style.removeProperty('--gha-color');
                ghaEl.style.removeProperty('--gha-bg');
                ghaEl.style.removeProperty('--gha-border');
                stackEl.style.removeProperty('--stack-gha-border');
                stackEl.style.removeProperty('--stack-gha-glow');
            }
        }
        // Only non-empty stacks collapse; empty ones stay open (drop hint).
        stackEl.classList.toggle('uld-stack-collapsible', items.length > 0);
        if (stackEl.classList.contains('uld-stack-accordion')) setupStackCollapseSoon(0);
        var col = stackCol(stackEl);
        if (col && !items.length && !col.querySelector('.uld-stack-empty-hint')) {
            var hint = document.createElement('div');
            hint.className = 'uld-stack-empty-hint';
            hint.textContent = 'Üres - húzz ide ULD-t';
            col.appendChild(hint);
        }
        if (col && items.length) {
            var staleHint = col.querySelector('.uld-stack-empty-hint');
            if (staleHint) staleHint.remove();
        }
        var json = items.map(function (el, idx) {
            return {
                pos: idx + 1,
                uld: el.dataset.uld || '',
                gha: el.dataset.gha || '',
                awbs: (el.dataset.awbs || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean),
                active: !el.classList.contains('uld-si-inactive'),
                expiry: el.dataset.expiry || '',
                am: el.dataset.am || ''
            };
        });
        stackEl.dataset.stackJson = JSON.stringify(json);
        refreshStackDisplayName(stackEl);
        if (stackEl.classList.contains('uld-stack-expanded')) {
            applyStackBodyScroll(stackBody(stackEl));
        }
    }

    function removeExistingStackItems(ulds) {
        var touched = new Set();
        ulds.forEach(function (uld) {
            stackItemsForUld(uld).forEach(function (el) {
                var stack = el.closest('.uld-stack[data-stack-id]');
                if (stack) touched.add(stack);
                el.remove();
            });
        });
        touched.forEach(function (stack) {
            refreshStackMeta(stack);
        });
    }

    // Remove duplicate STACK CARDS that share a data-stack-id. This happens when an
    // optimistic client-made card (.uld-stack-clientmade, appended by hand on the
    // create path) survives next to the Dash/React-owned card the server re-render
    // produces for the same stack — React never knew about the hand-appended node,
    // so reconciliation can leave it behind. Two cards with the same id then "move
    // together" (selection/drag is keyed by id) and "delete together". Keep one card
    // per id, preferring the React-owned (non-clientmade) card; among equals keep the
    // last in document order (the freshest server re-render) and drop the strays.
    function cleanupDuplicateStackCards() {
        var grid = document.getElementById('uld-stacks-grid');
        if (!grid) return;
        var byId = {};
        grid.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (card) {
            var id = card.dataset.stackId;
            if (!id) return;
            (byId[id] = byId[id] || []).push(card);
        });
        Object.keys(byId).forEach(function (id) {
            var cards = byId[id];
            if (cards.length <= 1) return;
            var keeper = null;
            cards.forEach(function (c) {
                if (!keeper) { keeper = c; return; }
                var keeperClient = keeper.classList.contains('uld-stack-clientmade');
                var cardClient   = c.classList.contains('uld-stack-clientmade');
                // Prefer a real (Dash-owned) card over a client-made one; among cards
                // of equal kind the later one in document order wins.
                if (keeperClient && !cardClient) keeper = c;
                else if (keeperClient === cardClient) keeper = c;
            });
            cards.forEach(function (c) {
                if (c !== keeper && c.parentNode) c.parentNode.removeChild(c);
            });
        });
    }

    // Remove optimistic items that have a server-rendered counterpart in the same stack.
    // Groups items by NORMALIZED uld so raw/normalized variants don't slip through.
    function cleanupOptimisticDuplicates() {
        cleanupDuplicateStackCards();
        // Defensive: an *empty* client-made stack card is always stale now that we
        // never build optimistic empty cards (real empty stacks are Dash/React-owned).
        // Drop any that linger so they can't sit "glued" to a real card and move /
        // select / delete with it. Client-made cards that carry ULDs are left alone —
        // they're the legitimate optimistic fly-animation targets the dedup handles.
        document.querySelectorAll('.uld-stack-clientmade').forEach(function (card) {
            if (!card.querySelector('.uld-si[data-uld]') && card.parentNode) {
                card.parentNode.removeChild(card);
            }
        });
        cleanupRowStackBadges();
        document.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (stackEl) {
            var byUld = {};
            stackEl.querySelectorAll('.uld-si[data-uld]').forEach(function (el) {
                var uld = normalizeUld(el.dataset.uld) || el.dataset.uld;
                if (!byUld[uld]) byUld[uld] = { optimistic: [], real: [] };
                if (el.classList.contains('uld-optimistic')) byUld[uld].optimistic.push(el);
                else byUld[uld].real.push(el);
            });
            var changed = false;
            Object.keys(byUld).forEach(function (uld) {
                var entry = byUld[uld];
                // Real wins over optimistic — remove all optimistic duplicates.
                if (entry.real.length > 0 && entry.optimistic.length > 0) {
                    entry.optimistic.forEach(function (el) { el.remove(); });
                    changed = true;
                }
                // Multiple optimistic for same ULD (raw vs normalized variant) — keep first.
                if (entry.real.length === 0 && entry.optimistic.length > 1) {
                    entry.optimistic.slice(1).forEach(function (el) { el.remove(); });
                    changed = true;
                }
                // Multiple real for same ULD (server bug or race) — keep first.
                if (entry.real.length > 1) {
                    entry.real.slice(1).forEach(function (el) { el.remove(); });
                    changed = true;
                }
            });
            // Remove stale "empty" hint when real or optimistic ULD items are present
            var col = stackCol(stackEl);
            if (col) {
                var allItems = stackItems(stackEl);
                var hint = col.querySelector('.uld-stack-empty-hint');
                if (allItems.length > 0 && hint) {
                    hint.remove();
                    changed = true;
                }
            }
            if (changed) refreshStackMeta(stackEl);
        });
        // Cross-stack dedup: a ULD must live in at most one stack.
        var globalByUld = {};
        document.querySelectorAll('.uld-si[data-uld]').forEach(function (el) {
            var key = normalizeUld(el.dataset.uld) || el.dataset.uld;
            if (!globalByUld[key]) globalByUld[key] = [];
            globalByUld[key].push(el);
        });
        Object.keys(globalByUld).forEach(function (key) {
            var arr = globalByUld[key];
            if (arr.length <= 1) return;
            // Prefer non-optimistic, then earliest in document order.
            arr.sort(function (a, b) {
                var ao = a.classList.contains('uld-optimistic') ? 1 : 0;
                var bo = b.classList.contains('uld-optimistic') ? 1 : 0;
                if (ao !== bo) return ao - bo;
                return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) ? -1 : 1;
            });
            var touched = new Set();
            arr.slice(1).forEach(function (el) {
                var s = el.closest('.uld-stack[data-stack-id]');
                if (s) touched.add(s);
                el.remove();
            });
            touched.forEach(refreshStackMeta);
        });
    }

    function optimisticHozzáadásToStack(ulds, stackEl, insertAt, stackName) {
        var col = stackCol(stackEl);
        if (!col) return;
        var sourceByUld = {};
        ulds.forEach(function (uld) { sourceByUld[uld] = sourceInfo(uld); });
        removeExistingStackItems(ulds);
        var hint = col.querySelector('.uld-stack-empty-hint');
        if (hint) hint.remove();
        var refItems = stackItems(stackEl);
        var ref = insertAt == null ? refItems[0] : refItems[Math.max(0, Math.min(insertAt, refItems.length))];
        ulds.forEach(function (uld) {
            var item = makeStackItem(uld, sourceByUld[uld]);
            col.insertBefore(item, ref || null);
            setRowStackBadge(uld, displayNameForStack(stackEl) || stackName);
        });
        // Keep the stack open so the just-added ULD is visible immediately.
        if (stackEl.dataset.stackId) {
            forceStackekCollapsed = false;
            expandedStackek.add(stackEl.dataset.stackId);
        }
        refreshStackMeta(stackEl);
        var displayName = displayNameForStack(stackEl) || stackName;
        ulds.forEach(function (uld) { setRowStackBadge(uld, displayName); });
        reconcileSelection();
    }

    function optimisticRemoveFromStackek(ulds) {
        removeExistingStackItems(ulds);
        ulds.forEach(clearRowStackBadge);
        reconcileSelection();
    }

    function stackItemIn(stackEl, uld) {
        return stackEl ? stackEl.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]') : null;
    }

    function sameStackMove(stackEl, ulds) {
        return !!(stackEl && ulds.length && ulds.every(function (uld) {
            var el = stackItemIn(stackEl, uld);
            return !!(el && el.closest('.uld-stack[data-stack-id]') === stackEl);
        }));
    }

    function sourceStackForUld(uld) {
        var item = document.querySelector('.uld-si[data-uld="' + cssEsc(uld) + '"]');
        return item ? item.closest('.uld-stack[data-stack-id]') : null;
    }

    function sourceStackIdsFor(ulds) {
        var ids = [];
        var seen = new Set();
        (ulds || []).forEach(function (uld) {
            var stack = sourceStackForUld(uld);
            var id = stackIdOf(stack);
            if (id && !seen.has(id)) {
                seen.add(id);
                ids.push(id);
            }
        });
        return ids;
    }

    function stackElsFromIds(ids) {
        return (ids || []).map(function (id) {
            return document.querySelector('.uld-stack[data-stack-id="' + cssEsc(id) + '"]');
        }).filter(Boolean);
    }

    function stackElById(id) {
        return id ? document.querySelector('.uld-stack[data-stack-id="' + cssEsc(id) + '"]') : null;
    }

    function currentStackRevision(id) {
        var stack = stackElById(id);
        return stack ? stackRevision(stack) : undefined;
    }

    function itemsBySourceStack(ulds) {
        var grouped = {};
        (ulds || []).forEach(function (uld) {
            var stack = sourceStackForUld(uld);
            var id = stackIdOf(stack);
            if (!id) return;
            if (!grouped[id]) grouped[id] = [];
            grouped[id].push(uld);
        });
        return grouped;
    }

    function stackRevisionsById(ids) {
        var revisions = {};
        stackElsFromIds(ids).forEach(function (stack) {
            revisions[stackIdOf(stack)] = stackRevision(stack);
        });
        return revisions;
    }

    function stackDropPayload(stackId, ulds, position, sourceStackIds) {
        // One authoritative move primitive handles list, one-source and
        // multi-source drops. The backend removes these ULDs from every previous
        // stack under one lock, so a stale source revision cannot force the user
        // to repeat the drag while prepared-stack protection still applies.
        return {
            action: 'add_from_list',
            uld_numbers: ulds,
            stack_id: stackId,
            stack_revision: currentStackRevision(stackId),
            position: position
        };
    }

    function reorderStackPayload(stackId, orderTopDown) {
        return {
            action: 'reorder_stack',
            ulds: bottomToTop(orderTopDown),
            stack_id: stackId,
            stack_revision: currentStackRevision(stackId)
        };
    }

    function removeFromStackekPayload(groupedByStack, sourceIds) {
        return {
            action: 'remove_from_stack',
            items_by_stack: groupedByStack,
            stack_revisions: stackRevisionsById(sourceIds)
        };
    }

    function removeOneFromStackPayload(stackId, uldNum) {
        return {
            action: 'remove_from_stack',
            stack_id: stackId,
            uld_numbers: [uldNum],
            stack_revision: currentStackRevision(stackId)
        };
    }

    function deleteStackPayload(stackId, revision) {
        var payload = {
            action: 'delete',
            stack_id: stackId
        };
        var stackRev = revision == null ? currentStackRevision(stackId) : revision;
        if (stackRev !== undefined) payload.stack_revision = stackRev;
        return payload;
    }

    function stackOrderTopDown(stackEl) {
        return stackItems(stackEl).map(function (el) { return el.dataset.uld; }).filter(Boolean);
    }

    function buildMovedOrderTopDown(stackEl, movingUlds, insertAt) {
        var moving = new Set(movingUlds);
        var order = stackOrderTopDown(stackEl).filter(function (uld) { return !moving.has(uld); });
        var at = insertAt == null ? 0 : Math.max(0, Math.min(insertAt, order.length));
        order.splice.apply(order, [at, 0].concat(movingUlds));
        return Array.from(new Set(order));
    }

    function applyStackOrderTopDown(stackEl, orderTopDown) {
        var col = stackCol(stackEl);
        if (!col) return;
        var byUld = {};
        stackItems(stackEl).forEach(function (el) {
            byUld[el.dataset.uld] = el;
        });
        orderTopDown.forEach(function (uld) {
            if (byUld[uld]) col.appendChild(byUld[uld]);
        });
        refreshStackMeta(stackEl);
        reconcileSelection();
    }

    function bottomToTop(orderTopDown) {
        return orderTopDown.slice().reverse();
    }

    function markSyncing(stackEls, ulds) {
        (stackEls || []).forEach(function (stack) {
            if (!stack) return;
            var id = stackIdOf(stack);
            if (id) uldUiState.lockedStackek.add(id);
            stack.classList.add('uld-stack-syncing', 'uld-stack-op-locked');
        });
        uldElementsFor(ulds || []).forEach(function (el) {
            el.classList.add('uld-si-syncing');
        });
        setUldMode('syncing');
    }

    function clearSyncingSoon(stackEls, ulds, delay) {
        setTimeout(function () {
            unlockStackEls(stackEls || []);
            uldElementsFor(ulds || []).forEach(function (el) {
                el.classList.remove('uld-si-syncing');
            });
        }, delay == null ? 320 : delay);
    }

    function selectUldsByElements(elements, append) {
        selectedStackek.clear();
        if (!append) selectedUlds.clear();
        elements.forEach(function (el) {
            if (el.dataset && el.dataset.uld && !uldIsInPreparedStack(el.dataset.uld)) {
                selectedUlds.add(el.dataset.uld);
            }
        });
        reconcileSelection();
    }

    function selectByAttr(attr, value, append) {
        selectUldsByElements(uldElements().filter(function (el) {
            return (el.dataset[attr] || '') === value;
        }), append);
    }

    function selectAllVisibleRows() {
        var rows = Array.from(document.querySelectorAll('.uld-row[data-uld]'));
        var hozzáadva = 0;
        selectedStackek.clear();
        selectedUlds.clear();
        rows.forEach(function (row) {
            var uld = row.dataset.uld || '';
            if (!uld || uldIsInPreparedStack(uld)) return;
            selectedUlds.add(uld);
            hozzáadva += 1;
        });
        applySelectionClasses();
        _uldToast(hozzáadva ? (hozzáadva + ' ULD kijelölve') : 'Nincs kijelölhető ULD', hozzáadva ? 'ok' : 'info');
    }

    function isInteractiveTarget(target) {
        return !!target.closest('button, input, textarea, select, .uld-stack-actions');
    }

    // ── GHA restriction helpers ─────────────────────────────────────────────

    function stackGha(stackEl) {
        // Prefer the data-gha set by the server render (from single-GHA stack)
        var gha = (stackEl.dataset.gha || '').trim();
        if (gha && gha !== '—') return gha;
        // Fall back to the first genuinely non-empty item. querySelector used to
        // stop on an empty data-gha and miss valid later items.
        var found = '';
        stackEl.querySelectorAll('.uld-si[data-gha]').forEach(function (item) {
            var value = (item.dataset.gha || '').trim();
            if (!found && value && value !== '—') found = value;
        });
        return found;
    }

    function uldGha(uld) {
        var found = '';
        document.querySelectorAll('[data-uld="' + cssEsc(uld) + '"][data-gha]').forEach(function (el) {
            var value = (el.dataset.gha || '').trim();
            if (!found && value && value !== '—') found = value;
        });
        if (found) return found;
        var stackItem = stackItemForUld(uld);
        var stack = stackItem ? stackItem.closest('.uld-stack[data-stack-id]') : null;
        return stack ? stackGha(stack) : '';
    }

    function draggedGha(ulds) {
        var found = '';
        for (var i = 0; i < ulds.length; i++) {
            var gha = uldGha(ulds[i]);
            if (!gha) return '';
            if (found && found !== gha) return '__MIXED__';
            found = gha;
        }
        return found;
    }

    function ghaAllowed(stackEl, draggedUlds) {
        var dragGha = draggedGha(draggedUlds);
        if (!dragGha || dragGha === '__MIXED__') return false;
        var sgha = stackGha(stackEl);
        return !sgha || sgha === dragGha;
    }

    function ghaBlockMessage(stackEl, draggedUlds) {
        var dragGha = draggedGha(draggedUlds);
        if (dragGha === '__MIXED__') return 'Több GHA nem kerülhet egy stackbe';
        if (!dragGha) return 'Ismeretlen GHA - nem tehető stackbe';
        var sgha = stackGha(stackEl);
        return 'GHA elteres: stack = ' + (sgha || '?') + ', ULD = ' + dragGha;
    }

    // ── TK restriction helpers ──────────────────────────────────────────────
    // A "TK"-ra végződő ULD-ket külön kell tartani: TK ULD csak TK-only stackbe,
    // nem TK ULD csak nem-TK stackbe kerülhet. Csak a suffix számít.

    function uldIsTk(uld) {
        return String(uld || '').trim().toUpperCase().endsWith('TK');
    }

    function stackIsTk(stackEl) {
        if (stackEl.dataset && stackEl.dataset.tk === '1') return true;  // server-marked TK stack
        var item = stackEl.querySelector('.uld-si[data-uld]');
        return item ? uldIsTk(item.dataset.uld) : null;  // null = üres stack
    }

    function draggedTk(ulds) {
        if (!ulds || !ulds.length) return null;
        var first = uldIsTk(ulds[0]);
        for (var i = 1; i < ulds.length; i++) {
            if (uldIsTk(ulds[i]) !== first) return '__MIXED__';
        }
        return first;  // boolean
    }

    function tkAllowed(stackEl, draggedUlds) {
        var dt = draggedTk(draggedUlds);
        if (dt === '__MIXED__') return false;
        if (dt === null) return true;
        var st = stackIsTk(stackEl);
        return st === null || st === dt;
    }

    function tkBlockMessage(stackEl, draggedUlds) {
        var dt = draggedTk(draggedUlds);
        if (dt === '__MIXED__') return 'TK és nem TK ULD nem kerülhet egy stackbe';
        return dt ? 'TK-suffix ULD must be placed in a separate TK stack'
                  : 'Only TK-suffix ULDs can be placed in a TK stack';
    }

    // Combined drop gate: both the GHA and the TK rule must pass.
    function moveAllowed(stackEl, draggedUlds) {
        return ghaAllowed(stackEl, draggedUlds) && tkAllowed(stackEl, draggedUlds);
    }

    function moveBlockMessage(stackEl, draggedUlds) {
        if (!ghaAllowed(stackEl, draggedUlds)) return ghaBlockMessage(stackEl, draggedUlds);
        return tkBlockMessage(stackEl, draggedUlds);
    }

    function clearGhaBlockedClasses() {
        document.querySelectorAll('.uld-stack.uld-gha-blocked').forEach(function (el) {
            el.classList.remove('uld-gha-blocked');
        });
    }

    function clearDragClasses() {
        document.body.classList.remove('uld-drag-active', 'uld-return-target');
        document.querySelectorAll('.uld-dragging, .uld-moving-out, .uld-returning-list').forEach(function (el) {
            el.classList.remove('uld-dragging', 'uld-moving-out', 'uld-returning-list');
        });
        clearDropIndicators();
        clearGhaBlockedClasses();
        updateDropPreview();
        if (!uldUiState.lockedStackek.size) setUldMode('idle');
    }

    function updateDropPreview(targetEl, count, mode) {
        var preview = document.getElementById('uld-drop-preview');
        if (!preview) {
            preview = document.createElement('div');
            preview.id = 'uld-drop-preview';
            preview.className = 'uld-drop-preview';
            document.body.appendChild(preview);
        }
        if (!targetEl || !count) {
            preview.classList.remove('uld-drop-preview-show');
            return;
        }
        var r = targetEl.getBoundingClientRect();
        preview.textContent = mode === 'return'
            ? count + ' ULDs back to the list'
            : count + ' ULDs will be placed here';
        preview.style.left = Math.min(window.innerWidth - 220, Math.max(12, r.left + 12)) + 'px';
        preview.style.top = Math.max(12, r.top - 34) + 'px';
        preview.classList.add('uld-drop-preview-show');
    }

    // NOTE: the old "Ide kerül" drop slot was removed. It was a real flow element
    // inserted between items, so it shifted the rects of the items below it and made
    // dropPlacement oscillate (jitter) during dragover. The insertion point is now
    // shown only by the layout-neutral .uld-si-drop-above/.uld-si-drop-below edge
    // line (and .uld-drop-over for empty/whole-stack targets), which never moves the
    // items — so the measurement stays stable.

    document.addEventListener('click', function (e) {
        var item = e.target.closest('.uld-row[data-uld], .uld-si[data-uld]');
        if (!item || isInteractiveTarget(e.target) || e.button !== 0) {
            if (e.button === 0 && !item && !isInteractiveTarget(e.target) && e.target.closest('.uld-area') && !e.ctrlKey && !e.metaKey) {
                clearSelection();
            }
            return;
        }
        if (e.ctrlKey || e.metaKey) {
            e.preventDefault();
            toggleSelection(item);
        } else {
            selectOnly(item);
        }
    }, true);

    function settingsField(id) {
        return document.getElementById(id);
    }

    function setSettingsMessage(text, type) {
        var box = settingsField('settings-message');
        if (!box) return;
        box.textContent = text || '';
        box.classList.toggle('settings-error', type === 'error');
        box.classList.toggle('settings-ok', type === 'ok');
    }

    function closeSettingsModal() {
        var modal = settingsField('settings-overlay');
        if (!modal) return;
        modal.style.display = 'none';
        modal.setAttribute('aria-hidden', 'true');
        setSettingsMessage('', '');
    }

    function populateSettings(data) {
        var settings = data && data.settings ? data.settings : {};
        var ecomm = settingsField('settings-ecomm-path');
        var pallets = settingsField('settings-pallets-path');
        var sharedState = settingsField('settings-shared-state-path');
        var mins = settingsField('settings-refresh-minutes');
        var ecommActive = settingsField('settings-ecomm-active');
        var palletsActive = settingsField('settings-pallets-active');
        var ecommStatus = settingsField('settings-ecomm-status');
        var palletsStatus = settingsField('settings-pallets-status');
        var sharedStateStatus = settingsField('settings-shared-state-status');
        var sharedStateActive = settingsField('settings-shared-state-active');
        if (ecomm) ecomm.value = settings.ecomm_file || '';
        if (pallets) pallets.value = settings.pallets_file || '';
        if (sharedState) sharedState.value = settings.shared_state_dir || '';
        if (mins) mins.value = settings.refresh_interval_minutes || 10;
        if (ecommActive) ecommActive.textContent = settings.active_ecomm_file ? ('Most hasznalt: ' + settings.active_ecomm_file) : '';
        if (palletsActive) palletsActive.textContent = settings.active_pallets_file ? ('Most hasznalt: ' + settings.active_pallets_file) : '';
        if (sharedStateActive) sharedStateActive.textContent = settings.active_shared_state_dir ? ('Aktív: ' + settings.active_shared_state_dir) : '';
        if (ecommStatus) ecommStatus.classList.toggle('settings-status-ok', !!settings.ecomm_file);
        if (palletsStatus) palletsStatus.classList.toggle('settings-status-ok', !!settings.pallets_file);
        if (sharedStateStatus) sharedStateStatus.classList.toggle('settings-status-ok', !!(settings.shared_state_dir_exists || settings.active_shared_state_dir));
    }

    function openSettingsModal() {
        var modal = settingsField('settings-overlay');
        if (!modal) return;
        modal.style.display = 'flex';
        modal.setAttribute('aria-hidden', 'false');
        setSettingsMessage('Beállítások betöltése...', '');
        fetch('/api/settings', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.ok) throw new Error((data && data.error) || 'Beállítások nem olvashatók');
                populateSettings(data);
                setSettingsMessage('A mentett útvonalak újraindítás után lépnek életbe.', '');
            })
            .catch(function (err) {
                setSettingsMessage(err && err.message ? err.message : 'Beállítások betöltése sikertelen', 'error');
            });
    }

    function browseSettingsFile(kind, btn) {
        kind = kind === 'pallets' ? 'pallets' : kind === 'shared_state' ? 'shared_state' : 'ecomm';
        var fieldId = kind === 'pallets' ? 'settings-pallets-path' : kind === 'shared_state' ? 'settings-shared-state-path' : 'settings-ecomm-path';
        var field = settingsField(fieldId);
        var oldText = btn ? btn.textContent : '';
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Tallozas...';
        }
        setSettingsMessage('Fajlvalaszto megnyitasa...', '');
        fetch('/api/settings/pick', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ kind: kind, current_path: field ? field.value : '' })
        })
            .then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok || !data.ok) throw new Error((data && data.error) || 'Fajlvalasztas sikertelen');
                    return data;
                });
            })
            .then(function (data) {
                if (data.cancelled) {
                    setSettingsMessage('Fajlvalasztas megszakitva.', '');
                    return;
                }
                if (field && data.path) field.value = data.path;
                setSettingsMessage('Fajl kivalasztva. A Mentes gombbal rogzitheted.', 'ok');
            })
            .catch(function (err) {
                setSettingsMessage(err && err.message ? err.message : 'Fajlvalasztas sikertelen', 'error');
            })
            .finally(function () {
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = oldText || 'Fajl kivalasztasa';
                }
            });
    }

    function saveSettingsModal() {
        var save = settingsField('settings-save');
        var payload = {
            ecomm_file: (settingsField('settings-ecomm-path') || {}).value || '',
            pallets_file: (settingsField('settings-pallets-path') || {}).value || '',
            shared_state_dir: (settingsField('settings-shared-state-path') || {}).value || '',
            refresh_interval_minutes: (settingsField('settings-refresh-minutes') || {}).value || 10
        };
        if (save) {
            save.disabled = true;
            save.textContent = 'Mentés...';
        }
        setSettingsMessage('Mentés folyamatban...', '');
        fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
            .then(function (r) {
                return r.json().then(function (data) {
                    if (!r.ok || !data.ok) throw new Error((data && data.error) || 'Mentés sikertelen');
                    return data;
                });
            })
            .then(function (data) {
                populateSettings(data);
                setSettingsMessage((data && data.message) || 'Beállítások mentve. Újraindítás szükséges.', 'ok');
            })
            .catch(function (err) {
                setSettingsMessage(err && err.message ? err.message : 'Mentés sikertelen', 'error');
            })
            .finally(function () {
                if (save) {
                    save.disabled = false;
                    save.textContent = 'Mentés';
                }
            });
    }

    // Topmost open ULD modal (in stacking priority). A modal is "open" when its
    // aria-hidden attribute is "false" (set by every open*Modal). Each entry maps
    // Escape→close and Enter→primary action so the whole app has one consistent
    // keyboard contract for question/confirm dialogs.
    function uldTopOpenModal() {
        var modals = [
            { id: 'settings-overlay',   close: closeSettingsModal, confirm: saveSettingsModal },
            { id: 'uld-edit-modal',     close: closeUldEditModal, confirm: submitUldEditModal },
            { id: 'uld-manual-modal',   close: closeManualModal,   confirm: submitManualModal },
            { id: 'uld-dispatch-modal', close: closeKiadásModal,  confirm: confirmKiadásModal },
            { id: 'uld-delete-modal',   close: closeTörlésModal,    confirm: confirmTörlésModal },
            { id: 'uld-info-modal',     close: closeUldInfoModal,   confirm: null }
        ];
        for (var i = 0; i < modals.length; i++) {
            var el = document.getElementById(modals[i].id);
            if (el && el.getAttribute('aria-hidden') === 'false') return modals[i];
        }
        return null;
    }

    document.addEventListener('keydown', function (e) {
        // A modal open → Escape closes IT (not the selection underneath).
        if (e.key === 'Escape' && !uldTopOpenModal()) clearSelection();
    });

    document.addEventListener('scroll', function (e) {
        if (e.target && e.target.classList && e.target.classList.contains('uld-dlist-touchbar')) {
            updateTouchbarFade(e.target);
        }
    }, true);

    var _selectionReconcileTimer = null;
    function queueSelectionReconcile() {
        if (_selectionReconcileTimer) clearTimeout(_selectionReconcileTimer);
        _selectionReconcileTimer = setTimeout(function () {
            // Apply prepared classes FIRST so the subsequent reconcile sees
            // the newly-prepared ULDs and drops them from selection.
            applyPreparedFromDom();
            reconcileSelection();
            _highlightNewlyCreatedStackek();
            refreshTouchbarFades();
        }, 80);
    }

    var _selectionObserver = new MutationObserver(queueSelectionReconcile);

    // Debounced cleanup: avoids running on every single DOM mutation during a React re-render burst
    var _optimisticCleanupTimer = null;
    var _optimisticObserver = new MutationObserver(function () {
        if (_optimisticCleanupTimer) clearTimeout(_optimisticCleanupTimer);
        _optimisticCleanupTimer = setTimeout(function () {
            cleanupOptimisticDuplicates();
            applyPreparedFromDom();
            _highlightNewlyCreatedStackek();
            setupStackCollapse();
            scrollMatchedChipsIntoView();
            refreshTouchbarFades();
            applyDispatchedSelection();
        }, 80);
    });

    // Retry-based observer init: Dash scripts often load after DOMContentLoaded fires,
    // and the ULD view elements may not exist yet if the user starts on the inbound page.
    // Page transition: when uld-list children change after pagination click, animate rows in
    var _pageTransitionObserver = new MutationObserver(function (mutations) {
        var changed = mutations.some(function (m) { return m.type === 'childList'; });
        if (!changed) return;
        var list = document.getElementById('uld-list');
        if (!list) return;
        if (list.classList.contains('uld-page-turning')) {
            list.classList.remove('uld-page-turning');
            list.classList.remove('uld-page-in');
            void list.offsetWidth; // reflow
            list.classList.add('uld-page-in');
            setTimeout(function () { list.classList.remove('uld-page-in'); }, 600);
        }
    });

    // Arm the staggered row entrance for the NEXT uld-list re-render. Reuses the
    // pagination machinery so filter / search / GHA changes get the same smooth
    // fade-in instead of snapping. Background-poll re-renders never call this, so
    // they stay still (no gratuitous re-animation on idle refreshes).
    function markUldListTransition() {
        var list = document.getElementById('uld-list');
        if (list) list.classList.add('uld-page-turning');
    }

    var _initRetries = 0;
    function initUldObservers() {
        var area = document.getElementById('uld-area');
        var grid = document.getElementById('uld-stacks-grid');
        var list = document.getElementById('uld-list');
        if (!area && !grid) {
            if (++_initRetries < 60) setTimeout(initUldObservers, 400); // retry up to 24s
            return;
        }
        _initRetries = 0;
        if (area) _selectionObserver.observe(area, { childList: true, subtree: true });
        if (grid) _optimisticObserver.observe(grid, { childList: true, subtree: true });
        if (list) _pageTransitionObserver.observe(list, { childList: true });
        initStickyOffset();
        reconcileSelection();
        setupStackCollapseSoon(0);
        refreshTouchbarFades();
    }

    // Dynamically sync sticky top offset with the top header height (Flow Manager + KPI).
    var _stickyOffsetObs = null;
    function updateStickyOffset() {
        var header = document.querySelector('.app-header');
        if (!header) return;
        var h = Math.round(header.getBoundingClientRect().height);
        if (h <= 0) return;
        document.documentElement.style.setProperty('--uld-sticky-top', (h + 12) + 'px');
    }
    function initStickyOffset() {
        updateStickyOffset();
        if (!_stickyOffsetObs) {
            var header = document.querySelector('.app-header');
            if (header && window.ResizeObserver) {
                _stickyOffsetObs = new ResizeObserver(function () { updateStickyOffset(); });
                _stickyOffsetObs.observe(header);
            }
            window.addEventListener('resize', updateStickyOffset);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initUldObservers);
    } else {
        initUldObservers();
    }

    document.addEventListener('dragstart', function (e) {
        var src = e.target.closest('.uld-row[data-uld], .uld-si[data-uld]');
        if (!src) return;
        // Refuse to start a drag on any ULD that's locked by a prepared stack —
        // it's part of an "összekészítve" set and must not move.
        if (elementIsLockedByPrepared(src)) {
            e.preventDefault();
            _uldToast('Kész stack – előbb töröld a kész jelölést', 'info');
            return;
        }
        if (!selectedUlds.has(src.dataset.uld)) {
            selectedUlds.clear();
            selectedUlds.add(src.dataset.uld);
        }
        reconcileSelection();

        var ulds = visibleOrderedSelection(src);
        // Defensive: filter out any locked ULDs even if they snuck into the selection.
        var lockedFiltered = ulds.filter(function (u) { return !uldIsInPreparedStack(u); });
        if (!lockedFiltered.length) {
            e.preventDefault();
            _uldToast('Kész stack tételei nem mozgathatók', 'info');
            return;
        }
        if (lockedFiltered.length !== ulds.length) {
            _uldToast('Néhány ULD készre van jelölve - csak a többit mozgatom', 'info');
            ulds = lockedFiltered;
        }
        dragState = {
            ulds: ulds,
            stackUlds: stackDraggedUlds(ulds),
            rects: snapshotRects(ulds),
        };
        e.dataTransfer.setData('text/plain', ulds[0] || src.dataset.uld);
        e.dataTransfer.setData('application/x-uld-list', JSON.stringify(ulds));
        e.dataTransfer.effectAllowed = 'move';
        markMoving(ulds, 'uld-dragging');
        document.body.classList.add('uld-drag-active');
        setUldMode('dragging');
    }, true);

    document.addEventListener('dragend', function () {
        setTimeout(function () {
            if (!dragState || !dragState.dropHandled) clearDragClasses();
            dragState = null;
        }, 80);
    }, true);

    // Clear all drop indicators (items + stack)
    function clearDropIndicators() {
        document.querySelectorAll('.uld-stack.uld-drop-over').forEach(function (el) {
            el.classList.remove('uld-drop-over');
        });
        document.querySelectorAll('.uld-si.uld-si-drop-above, .uld-si.uld-si-drop-below, .uld-si.uld-si-drop-before, .uld-si.uld-si-drop-after').forEach(function (el) {
            el.classList.remove('uld-si-drop-above', 'uld-si-drop-below', 'uld-si-drop-before', 'uld-si-drop-after');
        });
    }

    document.addEventListener('dragover', function (e) {
        var stackEl = e.target.closest('.uld-stack[data-stack-id]');
        if (!stackEl) return;
        if (stackLocked(stackEl)) {
            clearDropIndicators();
            e.dataTransfer.dropEffect = 'none';
            updateDropPreview();
            return;
        }

        // Prepared (összekészítve) target stack rejects all drops.
        if (isPreparedStack(stackEl)) {
            clearDropIndicators();
            document.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (el) {
                el.classList.toggle('uld-gha-blocked', el === stackEl);
            });
            e.preventDefault();
            e.dataTransfer.dropEffect = 'none';
            updateDropPreview();
            return;
        }
        // GHA + TK restriction: block drop if dragged ULD(s) don't match the stack
        if (dragState && !moveAllowed(stackEl, dragState.ulds)) {
            clearDropIndicators();
            document.querySelectorAll('.uld-stack[data-stack-id]').forEach(function (el) {
                el.classList.toggle('uld-gha-blocked', el === stackEl);
            });
            e.preventDefault();
            e.dataTransfer.dropEffect = 'none';
            updateDropPreview();
            return; // don't preventDefault → browser shows "not allowed" cursor
        }
        stackEl.classList.remove('uld-gha-blocked');

        // Auto-open a collapsed stack while dragging over it so the drop lands
        // precisely (no scroll — that would yank the target from under the cursor).
        if (stackEl.classList.contains('uld-stack-collapsible') &&
            !stackEl.classList.contains('uld-stack-expanded')) {
            if (stackEl.dataset.stackId) {
                forceStackekCollapsed = false;
                expandedStackek.add(stackEl.dataset.stackId);
            }
            expandStackEl(stackEl, true, false);
            capStackGridHeight();
        }

        document.body.classList.remove('uld-return-target');
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        var now = performance.now();
        if (now - lastDragVisualAt < 32) return;
        lastDragVisualAt = now;

        var movingUlds = dragState ? dragState.ulds : [];
        var placement = dropPlacement(stackEl, e.clientX, e.clientY, movingUlds);
        var targetItem = placement.targetItem;

        // Clear previous indicators on OTHER stacks
        document.querySelectorAll('.uld-stack.uld-drop-over').forEach(function (el) {
            if (el !== stackEl) el.classList.remove('uld-drop-over');
        });
        document.querySelectorAll('.uld-si.uld-si-drop-above, .uld-si.uld-si-drop-below, .uld-si.uld-si-drop-before, .uld-si.uld-si-drop-after').forEach(function (el) {
            if (el !== targetItem) el.classList.remove('uld-si-drop-above', 'uld-si-drop-below', 'uld-si-drop-before', 'uld-si-drop-after');
        });

        if (targetItem && stackEl.contains(targetItem)) {
            stackEl.classList.remove('uld-drop-over');
            targetItem.classList.toggle('uld-si-drop-below', placement.edge === 'below');
            targetItem.classList.toggle('uld-si-drop-above', placement.edge === 'above');
            targetItem.classList.toggle('uld-si-drop-before', placement.edge === 'before');
            targetItem.classList.toggle('uld-si-drop-after', placement.edge === 'after');
            updateDropPreview(targetItem, dragState ? dragState.ulds.length : 1, 'stack');
        } else {
            stackEl.classList.add('uld-drop-over');
            updateDropPreview(stackEl, dragState ? dragState.ulds.length : 1, 'stack');
        }
    }, true);

    document.addEventListener('dragover', function (e) {
        if (!dragState || !dragState.stackUlds.length) return;
        if (e.target.closest('.uld-stack[data-stack-id]')) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        var now = performance.now();
        if (now - lastDragVisualAt < 32) return;
        lastDragVisualAt = now;
        document.body.classList.add('uld-return-target');
        clearDropIndicators();
        var listPanel = document.querySelector('.uld-list-panel') || document.body;
        updateDropPreview(listPanel, dragState.stackUlds.length, 'return');
    }, true);

    document.addEventListener('dragleave', function (e) {
        var stackEl = e.target.closest('.uld-stack[data-stack-id]');
        if (stackEl && !stackEl.contains(e.relatedTarget)) {
            stackEl.classList.remove('uld-drop-over', 'uld-gha-blocked');
            stackEl.querySelectorAll('.uld-si.uld-si-drop-above, .uld-si.uld-si-drop-below, .uld-si.uld-si-drop-before, .uld-si.uld-si-drop-after').forEach(function (el) {
                el.classList.remove('uld-si-drop-above', 'uld-si-drop-below', 'uld-si-drop-before', 'uld-si-drop-after');
            });
            updateDropPreview();
        }
    }, true);

    document.addEventListener('drop', function (e) {
        var stackEl = e.target.closest('.uld-stack[data-stack-id]');
        if (!stackEl) return;
        e.preventDefault();

        var ulds;
        try { ulds = JSON.parse(e.dataTransfer.getData('application/x-uld-list') || '[]'); }
        catch (err) { ulds = []; }
        if (!ulds.length) ulds = [e.dataTransfer.getData('text/plain')].filter(Boolean);
        var stackId  = stackEl.dataset.stackId;
        var stackName = stackEl.dataset.stackName || 'stack';
        if (!ulds.length || !stackId) { clearDropIndicators(); return; }
        if (stackLocked(stackEl)) {
            _uldToast('Stack mentése folyamatban', 'info');
            clearDropIndicators();
            return;
        }
        // Prepared (összekészítve) target — refuse all drops.
        if (isPreparedStack(stackEl)) {
            _uldToast('Kész stackhez nem adható új ULD', 'info');
            clearDropIndicators();
            clearGhaBlockedClasses();
            return;
        }
        // Defensive: refuse if any of the dragged ULDs is currently in a prepared stack.
        var lockedDragged = ulds.filter(function (u) { return uldIsInPreparedStack(u); });
        if (lockedDragged.length) {
            _uldToast('Kész ULD nem mozgatható', 'info');
            clearDropIndicators();
            return;
        }

        // Final GHA + TK check on drop (prevents race with fast drags)
        if (!moveAllowed(stackEl, ulds)) {
            _uldToast(moveBlockMessage(stackEl, ulds), 'error');
            clearDropIndicators();
            clearGhaBlockedClasses();
            return;
        }

        // Remove the drop slot + indicators BEFORE measuring. The slot is a real
        // element inserted into the stack column, so its height shifts the rects of
        // the items below it. Computing dropPlacement with the slot present skews the
        // clientY→index mapping by one row (drop lands in the wrong place, or an item
        // appears not to move). Clearing first settles the layout to exactly what the
        // user will see, and keeps the slot out of the rollback DOM snapshot.
        clearDropIndicators();
        var sourceStackIds = sourceStackIdsFor(ulds).filter(function (id) { return id !== stackId; });
        var sourceStackEls = stackElsFromIds(sourceStackIds);
        if (sourceStackEls.some(stackLocked)) {
            _uldToast('A forrásstack mentése folyamatban', 'info');
            clearDropIndicators();
            clearDragClasses();
            return;
        }
        if (dragState) dragState.dropHandled = true;
        var involvedStackEls = [stackEl].concat(sourceStackEls);

        var placement = dropPlacement(stackEl, e.clientX, e.clientY, ulds);
        var insertAt = placement.index;
        if (sameStackMove(stackEl, ulds)) {
            var newOrderTopDown = buildMovedOrderTopDown(stackEl, ulds, insertAt);
            markSyncing([stackEl], ulds);
            uldApiCall(
                function () { return reorderStackPayload(stackId, newOrderTopDown); },
                function () { _uldToast('Sorrend frissítve: ' + stackName, 'info'); },
                {
                    refreshDelay: 0,
                    retryTransient: 2,
                    done: function () { clearSyncingSoon([stackEl], ulds, 900); }
                }
            );
        } else {
            markSyncing(involvedStackEls, ulds);
            markRowsStackPending(ulds);
            uldApiCall(
                function () { return stackDropPayload(stackId, ulds, insertAt, sourceStackIds); },
                function () {
                    confirmRowsStackMembership(ulds, stackId);
                    _uldToast(ulds.length + ' ULD áthelyezve ide: ' + stackName, 'ok');
                },
                {
                    listDirty: true,
                    refreshDelay: 0,
                    retryOnConflict: 2,
                    retryTransient: 2,
                    rollback: function () { clearRowsStackPending(ulds); },
                    done: function () { clearSyncingSoon(involvedStackEls, ulds, 900); }
                }
            );
        }
        clearDropIndicators();
        setTimeout(clearDragClasses, 260);
    }, true);

    document.addEventListener('drop', function (e) {
        var overStack = e.target.closest('.uld-stack[data-stack-id]');
        if (overStack || !dragState || !dragState.stackUlds.length) return;
        e.preventDefault();
        var ulds = dragState.stackUlds;
        var groupedByStack = itemsBySourceStack(ulds);
        var sourceIds = Object.keys(groupedByStack);
        var touchedStackList = stackElsFromIds(sourceIds);
        if (touchedStackList.some(stackLocked)) {
            _uldToast('Stack mentése folyamatban', 'info');
            clearDropIndicators();
            clearDragClasses();
            return;
        }
        if (touchedStackList.some(isPreparedStack)) {
            _uldToast('Kész stack nem mozgatható', 'info');
            clearDropIndicators();
            clearDragClasses();
            return;
        }
        dragState.dropHandled = true;
        markSyncing(touchedStackList, ulds);
        markMoving(ulds, 'uld-returning-list');
        uldApiCall(
            function () { return removeFromStackekPayload(groupedByStack, sourceIds); },
            function () { _uldToast(ulds.length + ' ULD eltávolítva a stackből', 'ok'); },
            {
                listDirty: true,
                refreshDelay: 0,
                retryOnConflict: 2,
                retryTransient: 2,
                done: function () { clearSyncingSoon(touchedStackList, ulds, 900); }
            }
        );
        clearDropIndicators();
        setTimeout(clearDragClasses, 260);
    }, true);

    // ── ULD rename (double-click a stack item's number) ─────────────────────

    function startUldRename(numEl) {
        var item = numEl.closest('.uld-si[data-uld]');
        var stack = numEl.closest('.uld-stack[data-stack-id]');
        if (!item || !stack) return;
        if (isPreparedStack(stack)) {
            _uldToast('Készre jelölt stack – előbb töröld a kész jelölést', 'info');
            return;
        }
        if (item.querySelector('.uld-si-rename-input')) return; // already editing
        var oldUld = item.dataset.uld || numEl.textContent.trim();
        var current = numEl.textContent.trim() || oldUld;
        var input = document.createElement('input');
        input.type = 'text';
        input.value = current;
        input.className = 'uld-si-rename-input';
        input.spellcheck = false;
        input.setAttribute('autocomplete', 'off');
        var wasDraggable = item.draggable;
        item.draggable = false;            // don't let drag hijack the text caret
        numEl.style.display = 'none';
        numEl.parentNode.insertBefore(input, numEl.nextSibling);
        input.focus();
        input.select();

        var done = false;
        function finish(save) {
            if (done) return;
            done = true;
            item.draggable = wasDraggable;
            var raw = String(input.value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
            if (input.parentNode) input.parentNode.removeChild(input);
            numEl.style.display = '';
            if (!save || !raw || raw === normalizeUld(oldUld)) return;
            // Optimistic: show the new number at once; the server render reconciles.
            numEl.textContent = raw;
            uldApiCall(
                { action: 'rename_uld', uld_number: oldUld, new_uld_number: raw },
                function () {
                    _uldToast(oldUld + ' → ' + raw, 'ok');
                },
                {
                    listDirty: true,
                    refreshDelay: 120,
                    onError: function () { numEl.textContent = current; return false; }
                }
            );
        }
        input.addEventListener('blur', function () { finish(true); });
        input.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
            else if (ev.key === 'Escape') { ev.preventDefault(); finish(false); }
            ev.stopPropagation();
        });
        input.addEventListener('click', function (ev) { ev.stopPropagation(); });
    }

    // ── stack rename (double-click) ─────────────────────────────────────────

    document.addEventListener('dblclick', function (e) {
        var uldNumEl = e.target.closest('.uld-si .uld-si-num');
        if (uldNumEl) {
            e.preventDefault();
            e.stopPropagation();
            startUldRename(uldNumEl);
            return;
        }
        var nameEl = e.target.closest('.uld-stack-name');
        if (!nameEl) return;
        var stack = nameEl.closest('.uld-stack[data-stack-id]');
        if (!stack) return;
        var stackId = stack.dataset.stackId;
        var current = nameEl.textContent.trim();
        var input = document.createElement('input');
        input.type  = 'text';
        input.value = current;
        input.className = 'uld-stack-name-input';
        nameEl.replaceWith(input);
        input.focus();
        input.select();

        var committed = false;
        function commit() {
            if (committed) return;
            committed = true;
            var newName = input.value.trim() || current;
            var span = document.createElement('span');
            span.className   = 'uld-stack-name';
            span.textContent = newName;
            span.title = 'Dupla kattintás az átnevezéshez';
            input.replaceWith(span);
            if (newName !== current) {
                var rollbackRevision = stackRevision(stack);
                stack.dataset.stackName = newName;
                stack.dataset.rawStackName = newName;
                bumpStackRevision(stack);
                uldApiCall(
                    function () {
                        return { action: 'rename', stack_id: stackId, name: newName, stack_revision: currentStackRevision(stackId) };
                    },
                    null,
                    { rollback: function () {
                        stack.dataset.stackName = current;
                        stack.dataset.rawStackName = current;
                        stack.dataset.stackRevision = String(rollbackRevision);
                        var label = stack.querySelector('.uld-stack-name');
                        if (label) label.textContent = current;
                    } }
                );
            _uldToast('Átnevezve: ' + newName, 'info');
            }
        }
        input.addEventListener('blur',  commit);
        input.addEventListener('keydown', function (ev) {
            if (ev.key === 'Enter')  { ev.preventDefault(); commit(); }
            if (ev.key === 'Escape') { input.value = current; commit(); }
        });
    });

    // ── stack print / copy ──────────────────────────────────────────────────

    function _esc(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function printUldStack(stackEl) {
        var name = (stackEl.querySelector('.uld-stack-name') || {}).textContent || 'Stack';
        name = name.trim();
        var items = [];
        try { items = JSON.parse(stackEl.dataset.stackJson || '[]'); } catch (e) { items = []; }
        if (!items.length) {
            _uldToast('Üres stack - nincs mit nyomtatni', 'error');
            return;
        }

        var now = new Date().toLocaleString('hu-HU');
        var rows = items.map(function (it) {
            var awb = Array.isArray(it.awbs) ? it.awbs.join(', ') : (it.awbs || '—');
            var inactive = it.active === false ? ' <span class="inactive">(inaktív)</span>' : '';
            return '<tr><td class="sn">' + _esc(it.pos) + '</td>'
                 + '<td class="uld">' + _esc(it.uld) + inactive + '</td>'
                 + '<td class="gha">' + _esc(it.gha || '—') + '</td>'
                 + '<td class="awb">' + _esc(awb || '—') + '</td></tr>';
        }).join('');

        var html = '<!DOCTYPE html><html lang="hu"><head><meta charset="UTF-8">'
            + '<title>' + _esc(name) + '</title>'
            + '<style>'
            + 'body{margin:0;padding:24px 28px;font-family:"Segoe UI",Arial,sans-serif;color:#111;}'
            + 'h1{font-size:18px;font-weight:800;margin:0 0 4px;letter-spacing:.5px;}'
            + '.meta{font-size:11px;color:#6b7280;margin-bottom:18px;}'
            + 'table{width:100%;border-collapse:collapse;font-size:13px;}'
            + 'thead tr{background:#f3f4f6;}'
            + 'th{text-align:left;padding:8px 10px;font-size:10px;font-weight:700;'
            + '   text-transform:uppercase;letter-spacing:.5px;color:#6b7280;border-bottom:2px solid #e5e7eb;}'
            + 'td{padding:9px 10px;border-bottom:1px solid #f3f4f6;vertical-align:middle;}'
            + 'td.sn{width:36px;font-weight:700;color:#9ca3af;font-size:12px;}'
            + 'td.uld{font-family:monospace;font-size:14px;font-weight:800;letter-spacing:.5px;}'
            + '.inactive{font-family:"Segoe UI",Arial,sans-serif;font-size:10px;font-weight:700;color:#9ca3af;letter-spacing:0;}'
            + 'td.gha{font-size:12px;color:#4f46e5;}'
            + 'td.awb{font-family:monospace;font-size:12px;}'
            + 'tr:last-child td{border-bottom:none;}'
            + '.footer{margin-top:20px;font-size:10px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px;}'
            + '@media print{body{padding:12px 16px;}}'
            + '</style></head><body>'
            + '<h1>' + _esc(name) + '</h1>'
            + '<div class="meta">Nyomtatva: ' + _esc(now) + ' &nbsp;·&nbsp; ' + items.length + ' ULD</div>'
            + '<table><thead><tr><th>#</th><th>ULD szám</th><th>GHA</th><th>AWB</th></tr></thead>'
            + '<tbody>' + rows + '</tbody></table>'
            + '<div class="footer">Flow Manager &nbsp;·&nbsp; HGL Group Hungary Ecommerce</div>'
            + '</body></html>';

        var win = window.open('', '_blank', 'width=640,height=580');
        if (!win) { _uldToast('Engedélyezd a felugró ablakokat.', 'error'); return; }
        win.document.write(html);
        win.document.close();
        win.focus();
        setTimeout(function () { win.print(); }, 300);
        _uldToast(name + ' - nyomtatás', 'info');
    }

    function copyUldStack(stackEl) {
        var name = (stackEl.querySelector('.uld-stack-name') || {}).textContent || 'Stack';
        name = name.trim();
        var items = [];
        try { items = JSON.parse(stackEl.dataset.stackJson || '[]'); } catch (e) { items = []; }
        if (!items.length) {
            _uldToast('Üres stack - nincs mit másolni', 'error');
            return;
        }

        function fmtExpiry(iso) {
            if (!iso) return '';
            var d = new Date(iso);
            if (isNaN(d.getTime())) return '';
            var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
            return pad(d.getMonth() + 1) + '.' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
        }
        function fmtItemExpiry(it) {
            // Prefer the precomputed expiry; otherwise derive from am_time + 48h on the client.
            if (it.expiry) {
                var s = fmtExpiry(it.expiry);
                if (s) return s;
            }
            if (it.am) {
                var amDate = new Date(it.am);
                if (!isNaN(amDate.getTime())) {
                    var derived = new Date(amDate.getTime() + 48 * 3600 * 1000);
                    return fmtExpiry(derived.toISOString());
                }
            }
            return '';
        }

        var rows = [['#', 'ULD szám', 'GHA', 'AWB', 'Lejárat']].concat(items.map(function (it) {
            var awb = Array.isArray(it.awbs) ? it.awbs.join(', ') : (it.awbs || '');
            var uld = it.active === false ? it.uld + ' (inaktív)' : it.uld;
            return [it.pos, uld, it.gha || '', awb, fmtItemExpiry(it)];
        }));

        var originalHeader = rows[0] || [];
        var originalDataRows = rows.slice(1);
        rows = [[name, '', '', '', ''], originalHeader].concat(originalDataRows);

        function cleanCell(value) {
            return String(value == null ? '' : value).replace(/[\t\r\n]+/g, ' ').trim();
        }

        var text = rows.map(function (row) {
            return row.map(cleanCell).join('\t');
        }).join('\r\n');

        // GHA-bound header color (Menzies = sárga, Celebi = narancs, …); the
        // explicit text/background colors keep the table readable in both light
        // and dark Outlook (which would otherwise auto-invert untinted cells).
        var ghaColor = ghaColorFor(stackGhaName(stackEl, items));
        var titleBg = ghaColor || '#111827';
        var titleFg = ghaColor ? contrastText(ghaColor) : '#ffffff';
        var headBg = ghaColor || '#1f2937';
        var headFg = ghaColor ? contrastText(ghaColor) : '#ffffff';
        var borderCol = '#334155';

        // Inline styles on every cell so pasting into Excel/Outlook preserves
        // center alignment and a high-contrast border around every cell.
        var CELL_TH_STYLE = 'border:1.5px solid ' + borderCol + '; background:' + headBg + '; color:' + headFg + '; '
            + 'font-weight:800; font-family:"Segoe UI",Arial,sans-serif; padding:8px 12px; '
            + 'text-align:center; vertical-align:middle; letter-spacing:.4px; font-size:12px;';
        var TITLE_TH_STYLE = 'border:1.5px solid ' + borderCol + '; background:' + titleBg + '; color:' + titleFg + '; '
            + 'font-weight:900; font-family:"Segoe UI",Arial,sans-serif; padding:9px 12px; '
            + 'text-align:center; vertical-align:middle; font-size:14px;';
        var CELL_TD_STYLE = 'border:1.5px solid ' + borderCol + '; padding:7px 12px; '
            + 'font-family:"Segoe UI",Arial,sans-serif; font-size:13px; color:#111827; background:#ffffff; '
            + 'text-align:center; vertical-align:middle;';
        var TABLE_STYLE = 'border-collapse:collapse; border:2px solid ' + borderCol + '; '
            + 'font-family:"Segoe UI",Arial,sans-serif;';
        var titleRowHtml = '<tr><th colspan="' + originalHeader.length + '" style="' + TITLE_TH_STYLE + '">'
            + _esc(cleanCell(name)) + '</th></tr>';
        var headerRowHtml = '<tr>' + originalHeader.map(function (cell) {
            return '<th style="' + CELL_TH_STYLE + '">' + _esc(cleanCell(cell)) + '</th>';
        }).join('') + '</tr>';
        var bodyRowsHtml = originalDataRows.map(function (row) {
            return '<tr>' + row.map(function (cell) {
                return '<td style="' + CELL_TD_STYLE + '">' + _esc(cleanCell(cell)) + '</td>';
            }).join('') + '</tr>';
        }).join('');
        var html = '<html><body><table style="' + TABLE_STYLE + '"><thead>'
            + titleRowHtml + headerRowHtml + '</thead><tbody>'
            + bodyRowsHtml + '</tbody></table></body></html>';

        function fallbackMásolásTable() {
            var holder = document.createElement('div');
            holder.style.position = 'fixed';
            holder.style.left = '-9999px';
            holder.style.top = '0';
            holder.innerHTML = html;
            document.body.appendChild(holder);
            var range = document.createRange();
            range.selectNodeContents(holder);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            try {
                var ok = document.execCommand('copy');
                if (!ok) throw new Error('copy failed');
                _uldToast(name + ' – táblázat másolva (' + items.length + ' sor)', 'ok');
            } catch (e) {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(function () {
                        _uldToast(name + ' – táblázat másolva (' + items.length + ' sor)', 'ok');
                    }).catch(function () {
                        _uldToast('A másolás sikertelen', 'error');
                        bumpTrigger();
                    });
                } else {
                    _uldToast('A másolás sikertelen', 'error');
                    bumpTrigger();
                }
            } finally {
                sel.removeAllRanges();
                document.body.removeChild(holder);
            }
        }

        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], { type: 'text/html' }),
                'text/plain': new Blob([text], { type: 'text/plain' })
            })]).then(function () {
                _uldToast(name + ' – vágólapra másolva (' + items.length + ' sor)', 'ok');
            }).catch(function () {
                fallbackMásolásTable();
            });
        } else {
            fallbackMásolásTable();
        }
    }

    function selectedStackElements() {
        return stackElements().filter(function (s) { return selectedStackek.has(s.dataset.stackId); });
    }

    function stackPayload(stackEl) {
        var name = (stackEl.querySelector('.uld-stack-name') || {}).textContent || stackEl.dataset.stackName || 'Stack';
        var items = [];
        try { items = JSON.parse(stackEl.dataset.stackJson || '[]'); } catch (e) { items = []; }
        return { name: String(name).trim() || 'Stack', items: items, gha: stackGhaName(stackEl, items) };
    }

    function dispatchStackElNow(stackEl, dispatchRendszám, done) {
        if (!stackEl) return false;
        var stackId = stackIdOf(stackEl);
        var stackName = stackEl.dataset.stackName || 'stack';
        var items = stackSnapshotItems(stackEl);
        if (!stackId || !items.length) return false;
        var dispatchRevision = stackRevision(stackEl);
        var removed = items.map(function (it) { return it.uld; }).filter(Boolean);

        stackEl.classList.add('uld-stack-dispatching');
        uldUiState.lockedStackek.delete(stackId);
        removed.forEach(function (uld) {
            selectedUlds.delete(uld);
            clearRowStackBadge(uld);
            var row = rowForUld(uld);
            if (row) row.classList.add('uld-row-dispatching');
        });

        uldApiCall(
            { action: 'dispatched', stack_id: stackId, stack_revision: dispatchRevision, dispatch_plate: dispatchRendszám || '' },
            function () {
                _uldToast(stackName + ' kiküldve', 'ok');
            },
            {
                applyState: false,
                listDirty: true,
                refreshDelay: 380,
                rollback: function () {
                    stackEl.classList.remove('uld-stack-dispatching');
                    removed.forEach(function (uld) {
                        var row = rowForUld(uld);
                        if (row) row.classList.remove('uld-row-dispatching');
                    });
                },
                done: done
            }
        );
        return true;
    }

    function printUldStackek(stackEls) {
        var payloads = (stackEls || []).map(stackPayload).filter(function (p) { return p.items.length; });
        if (!payloads.length) {
            _uldToast('Nincs nyomtatható stack a kijelölésben', 'error');
            return;
        }
        var now = new Date().toLocaleString('hu-HU');
        var pages = payloads.map(function (p) {
            var rows = p.items.map(function (it) {
                var awb = Array.isArray(it.awbs) ? it.awbs.join(', ') : (it.awbs || '—');
                var inactive = it.active === false ? ' <span class="inactive">(inaktív)</span>' : '';
                return '<tr><td class="sn">' + _esc(it.pos) + '</td>'
                     + '<td class="uld">' + _esc(it.uld) + inactive + '</td>'
                     + '<td class="gha">' + _esc(it.gha || '—') + '</td>'
                     + '<td class="awb">' + _esc(awb || '—') + '</td></tr>';
            }).join('');
            return '<section class="stack-page">'
                + '<h1>' + _esc(p.name) + '</h1>'
                + '<div class="meta">Nyomtatva: ' + _esc(now) + ' &nbsp;·&nbsp; ' + p.items.length + ' ULD</div>'
                + '<table><thead><tr><th>#</th><th>ULD szám</th><th>GHA</th><th>AWB</th></tr></thead>'
                + '<tbody>' + rows + '</tbody></table>'
                + '<div class="footer">Flow Manager &nbsp;·&nbsp; HGL Group Hungary Ecommerce</div>'
                + '</section>';
        }).join('');
        var html = '<!DOCTYPE html><html lang="hu"><head><meta charset="UTF-8">'
            + '<title>Kijelölt stackek</title>'
            + '<style>'
            + 'body{margin:0;padding:0;font-family:"Segoe UI",Arial,sans-serif;color:#111;}'
            + '.stack-page{min-height:calc(100vh - 56px);padding:24px 28px;page-break-after:always;break-after:page;}'
            + '.stack-page:last-child{page-break-after:auto;break-after:auto;}'
            + 'h1{font-size:18px;font-weight:800;margin:0 0 4px;letter-spacing:.5px;}'
            + '.meta{font-size:11px;color:#6b7280;margin-bottom:18px;}'
            + 'table{width:100%;border-collapse:collapse;font-size:13px;}'
            + 'thead tr{background:#f3f4f6;}'
            + 'th{text-align:left;padding:8px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#6b7280;border-bottom:2px solid #e5e7eb;}'
            + 'td{padding:9px 10px;border-bottom:1px solid #f3f4f6;vertical-align:middle;}'
            + 'td.sn{width:36px;font-weight:700;color:#9ca3af;font-size:12px;}'
            + 'td.uld{font-family:monospace;font-size:14px;font-weight:800;letter-spacing:.5px;}'
            + '.inactive{font-family:"Segoe UI",Arial,sans-serif;font-size:10px;font-weight:700;color:#9ca3af;letter-spacing:0;}'
            + 'td.gha{font-size:12px;color:#4f46e5;}td.awb{font-family:monospace;font-size:12px;}'
            + 'tr:last-child td{border-bottom:none;}.footer{margin-top:20px;font-size:10px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:8px;}'
            + '@media print{.stack-page{min-height:auto;padding:12px 16px;}}'
            + '</style></head><body>' + pages + '</body></html>';
        var win = window.open('', '_blank', 'width=760,height=720');
        if (!win) { _uldToast('Engedélyezd a felugró ablakokat.', 'error'); return; }
        win.document.write(html);
        win.document.close();
        win.focus();
        setTimeout(function () { win.print(); }, 300);
        _uldToast(payloads.length + ' stack - nyomtatás', 'info');
    }

    function copyUldStackek(stackEls) {
        var payloads = (stackEls || []).map(stackPayload).filter(function (p) { return p.items.length; });
        if (!payloads.length) {
            _uldToast('Nincs másolható stack a kijelölésben', 'error');
            return;
        }
        function cleanCell(value) {
            return String(value == null ? '' : value).replace(/[\t\r\n]+/g, ' ').trim();
        }
        function fmtExpiry(iso) {
            if (!iso) return '';
            var d = new Date(iso);
            if (isNaN(d.getTime())) return '';
            var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
            return pad(d.getMonth() + 1) + '.' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
        }
        function itemExpiry(it) {
            if (it.expiry) {
                var s = fmtExpiry(it.expiry);
                if (s) return s;
            }
            if (it.am) {
                var amDate = new Date(it.am);
                if (!isNaN(amDate.getTime())) return fmtExpiry(new Date(amDate.getTime() + 48 * 3600 * 1000).toISOString());
            }
            return '';
        }
        var header = ['#', 'ULD szám', 'GHA', 'AWB', 'Lejárat'];
        var textBlocks = [];
        var tablesHtml = payloads.map(function (p) {
            var bodyRows = p.items.map(function (it) {
                var awb = Array.isArray(it.awbs) ? it.awbs.join(', ') : (it.awbs || '');
                var uld = it.active === false ? it.uld + ' (inaktív)' : it.uld;
                return [it.pos, uld, it.gha || '', awb, itemExpiry(it)];
            });
            textBlocks.push([[p.name, '', '', '', ''], header].concat(bodyRows).map(function (row) {
                return row.map(cleanCell).join('\t');
            }).join('\r\n'));
            // GHA-bound header color; explicit fg/bg keeps it readable in light & dark Outlook.
            var ghaColor = ghaColorFor(p.gha);
            var titleBg = ghaColor || '#111827';
            var titleFg = ghaColor ? contrastText(ghaColor) : '#fff';
            var headBg = ghaColor || '#1f2937';
            var headFg = ghaColor ? contrastText(ghaColor) : '#fff';
            var bc = '#334155';
            var title = '<tr><th colspan="5" style="border:1.5px solid ' + bc + ';background:' + titleBg + ';color:' + titleFg + ';font-weight:900;padding:9px 12px;text-align:center;">' + _esc(cleanCell(p.name)) + '</th></tr>';
            var head = '<tr>' + header.map(function (cell) {
                return '<th style="border:1.5px solid ' + bc + ';background:' + headBg + ';color:' + headFg + ';font-weight:800;padding:8px 12px;text-align:center;">' + _esc(cell) + '</th>';
            }).join('') + '</tr>';
            var body = bodyRows.map(function (row) {
                return '<tr>' + row.map(function (cell) {
                    return '<td style="border:1.5px solid ' + bc + ';padding:7px 12px;text-align:center;color:#111827;background:#ffffff;">' + _esc(cleanCell(cell)) + '</td>';
                }).join('') + '</tr>';
            }).join('');
            return '<table style="border-collapse:collapse;border:2px solid ' + bc + ';font-family:Segoe UI,Arial,sans-serif;page-break-after:always;margin:0 0 20px;">'
                + '<thead>' + title + head + '</thead><tbody>' + body + '</tbody></table>';
        }).join('');
        var text = textBlocks.join('\r\n\r\n');
        var html = '<html><body>' + tablesHtml + '</body></html>';
        function fallbackMásolás() {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () {
                    _uldToast(payloads.length + ' stack táblázata másolva', 'ok');
                }).catch(function () { _uldToast('A másolás sikertelen', 'error'); });
            } else {
                _uldToast('A másolás sikertelen', 'error');
            }
        }
        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], { type: 'text/html' }),
                'text/plain': new Blob([text], { type: 'text/plain' })
            })]).then(function () {
                _uldToast(payloads.length + ' stack a vágólapra másolva', 'ok');
            }).catch(fallbackMásolás);
        } else {
            fallbackMásolás();
        }
    }

    var pendingTörlésStack = null;
    var pendingKiadásStack = null;

    function stackSnapshotItems(stackEl) {
        var items = [];
        try { items = JSON.parse(stackEl && stackEl.dataset.stackJson || '[]'); } catch (e) { items = []; }
        if (!items.length && stackEl) {
            items = Array.from(stackEl.querySelectorAll('.uld-si[data-uld]')).map(function (el, idx) {
                return {
                    pos: idx + 1,
                    uld: el.dataset.uld,
                    gha: el.dataset.gha || '',
                    awbs: (el.dataset.awbs || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean),
                    active: !el.classList.contains('uld-si-inactive'),
                    is_szerkesztve: el.dataset.szerkesztve === '1',
                    szerkesztve_at: el.dataset.szerkesztveAt || '',
                    szerkesztve_by: el.dataset.szerkesztveBy || ''
                };
            });
        }
        return items;
    }

    function openTörlésModal(stackId, stackName, items) {
        var stackEl = stackElById(stackId);
        items = Array.isArray(items) ? items : [];
        var removed = items.map(function (it) { return it.uld; }).filter(Boolean);
        pendingTörlésStack = {
            stackId: stackId,
            stackName: stackName,
            removed: removed,
            stackRevision: stackEl ? stackRevision(stackEl) : undefined
        };
        var modal = document.getElementById('uld-delete-modal');
        if (!modal) return;
        var title = document.getElementById('uld-delete-title');
        var text = document.getElementById('uld-delete-text');
        if (title) title.textContent = stackName;

        // Derive a single GHA (if uniform) + active/inactive split for the summary.
        var ghas = {};
        var activeCount = 0;
        items.forEach(function (it) {
            var g = (it.gha || '').trim();
            if (g) ghas[g] = true;
            if (it.active !== false) activeCount += 1;
        });
        var ghaKeys = Object.keys(ghas);
        var inactiveCount = items.length - activeCount;

        if (text) {
            if (!items.length) {
                text.innerHTML =
                    '<div class="uld-delete-empty">' +
                        '<span class="uld-delete-empty-icon">∅</span>' +
                        '<span>Ez egy <b>üres stack</b>. Törlöd?</span>' +
                    '</div>';
            } else {
                var summary =
                    '<div class="uld-delete-summary">' +
                        '<span class="uld-delete-pill uld-delete-pill-count">' + items.length + ' ULD</span>' +
                        (ghaKeys.length === 1 ? '<span class="uld-delete-pill uld-delete-pill-gha">' + _esc(ghaKeys[0]) + '</span>' : '') +
                        (ghaKeys.length > 1 ? '<span class="uld-delete-pill uld-delete-pill-gha">' + ghaKeys.length + ' GHA</span>' : '') +
                        (inactiveCount ? '<span class="uld-delete-pill uld-delete-pill-inactive">' + inactiveCount + ' inaktív</span>' : '') +
                    '</div>' +
                    '<div class="uld-delete-hint">A stack törlése ezeket az ULD-ket visszateszi a listába:</div>';

                var rows = items.map(function (it, idx) {
                    var awb = Array.isArray(it.awbs) ? it.awbs.filter(Boolean).join(', ') : (it.awbs || '');
                    var prefix = String(it.uld || '').slice(0, 3).toUpperCase();
                    var col = prefixColorFor(prefix);
                    var inactive = it.active === false;
                    return '<li class="uld-delete-item' + (inactive ? ' uld-delete-item-inactive' : '') + '"' +
                                ' style="--i:' + idx + ';--dc:' + col + '">' +
                            '<span class="uld-delete-pos" style="color:' + col + ';border-color:' + col + '88;background:' + col + '1f">' + (idx + 1) + '</span>' +
                            '<span class="uld-delete-uld">' + _esc(it.uld || '') + '</span>' +
                            (it.gha ? '<span class="uld-delete-gha">' + _esc(it.gha) + '</span>' : '<span class="uld-delete-gha uld-delete-gha-none">—</span>') +
                            '<span class="uld-delete-awb">' + _esc(awb || '—') + '</span>' +
                            (inactive ? '<span class="uld-delete-tag">inaktív</span>' : '') +
                        '</li>';
                }).join('');
                text.innerHTML = summary + '<ul class="uld-delete-list">' + rows + '</ul>';
            }
        }
        modal.classList.remove('uld-delete-modal-closing');
        modal.classList.add('uld-delete-modal-show');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeTörlésModal() {
        var modal = document.getElementById('uld-delete-modal');
        if (modal) {
            // Play the exit animation, then fully hide.
            modal.classList.add('uld-delete-modal-closing');
            modal.setAttribute('aria-hidden', 'true');
            setTimeout(function () {
                modal.classList.remove('uld-delete-modal-show', 'uld-delete-modal-closing');
            }, 200);
        }
        pendingTörlésStack = null;
    }

    function confirmTörlésModal() {
        if (!pendingTörlésStack || !pendingTörlésStack.stackId) return;
        var info = pendingTörlésStack;
        var confirmBtn = document.getElementById('uld-delete-confirm');
        if (confirmBtn && !guardActionButton(confirmBtn, 'Törlés...')) return;
        var stackEl = stackElById(info.stackId);
        var deleteRevision = info.stackRevision == null ? (stackEl ? stackRevision(stackEl) : undefined) : info.stackRevision;
        closeTörlésModal();

        // IMPORTANT: do NOT remove the stack card from the DOM here. The card is a
        // Dash/React-owned node; tearing it out kézily desyncs React's child list,
        // and the next server re-render then unmounts the WRONG sibling — that's the
        // "delete one stack, a neighbour also vanishes" bug. Instead we only play the
        // exit animation on the card, keep applyState off (so applyServerStackState
        // doesn't reconcile the grid either), and let the Dash re-render — which
        // returns the stack list WITHOUT this stack — unmount this exact node cleanly
        // via React. refreshDelay is long enough for the animation to finish first.
        if (stackEl) {
            stackEl.classList.add('uld-stack-deleting');
            uldUiState.lockedStackek.delete(info.stackId);
            // A client-built (optimistic) card is NOT React-owned, so the Dash
            // re-render won't unmount it — drop it ourselves after the animation.
            // React-owned cards are left untouched (Dash removes them cleanly).
            if (stackEl.classList.contains('uld-stack-clientmade')) {
                setTimeout(function () {
                    if (stackEl.parentNode) stackEl.parentNode.removeChild(stackEl);
                }, 340);
            }
        }
        info.removed.forEach(function (uld) {
            selectedUlds.delete(uld);
            clearRowStackBadge(uld);
        });
        reconcileSelection();
        uldApiCall(
            deleteStackPayload(info.stackId, deleteRevision),
            function () {
                _uldToast(info.stackName + ' törölve', 'info');
            },
            {
                applyState: false,   // never hand-reconcile the grid for a delete
                listDirty: true,
                refreshDelay: 340,   // play the exit animation, then Dash removes the node
                rollback: function () {
                    // API failed → restore the card in place (no DOM surgery).
                    if (stackEl) stackEl.classList.remove('uld-stack-deleting');
                },
                done: function () { releaseActionButton(confirmBtn); }
            }
        );
    }

    function openKiadásModal(stackId, stackName, items, options) {
        options = options || {};
        var stackEl = stackElById(stackId);
        var bulkStackEls = Array.isArray(options.stackEls) ? options.stackEls : null;
        items = Array.isArray(items) ? items : [];
        var groups = Array.isArray(options.groups) ? options.groups : null;
        pendingKiadásStack = {
            stackId: stackId,
            stackName: stackName,
            removed: items.map(function (it) { return it.uld; }).filter(Boolean),
            stackRevision: stackEl ? stackRevision(stackEl) : undefined,
            stackEls: bulkStackEls
        };
        var modal = document.getElementById('uld-dispatch-modal');
        if (!modal) return;
        var title = document.getElementById('uld-dispatch-title');
        var text = document.getElementById('uld-dispatch-text');
        var plateInput = document.getElementById('uld-dispatch-plate');
        if (title) title.textContent = stackName;
        if (plateInput) {
            plateInput.value = '';
            setTimeout(function () { try { plateInput.focus(); } catch (e) {} }, 80);
        }

        var ghas = {};
        items.forEach(function (it) {
            var g = (it.gha || '').trim();
            if (g) ghas[g] = true;
        });
        var ghaKeys = Object.keys(ghas);
        if (text) {
            if (!items.length) {
                text.innerHTML =
                    '<div class="uld-delete-empty">' +
                        '<span class="uld-delete-empty-icon">⇥</span>' +
                        '<span>Ez egy <b>üres stack</b>, ezért nem adható ki.</span>' +
                    '</div>';
            } else {
                var groupCount = groups ? groups.length : (bulkStackEls ? bulkStackEls.length : 1);
                var summary =
                    '<div class="uld-delete-summary uld-dispatch-summary">' +
                        '<span class="uld-delete-pill uld-dispatch-pill-count">' + items.length + ' ULD</span>' +
                        (groupCount > 1 ? '<span class="uld-delete-pill uld-dispatch-pill-count">' + groupCount + ' stack</span>' : '') +
                        (ghaKeys.length === 1 ? '<span class="uld-delete-pill uld-delete-pill-gha"' + ghaBadgeStyle(ghaKeys[0]) + '>' + _esc(ghaKeys[0]) + '</span>' : '') +
                        (ghaKeys.length > 1 ? '<span class="uld-delete-pill uld-delete-pill-gha">' + ghaKeys.length + ' GHA</span>' : '') +
                    '</div>' +
                    '<div class="uld-delete-hint">Kiadás után ezek az ULD-k kikerülnek az aktív listából, a stack pedig a Kiadott stackek nézetben marad.</div>';

                // Renders one ULD row; pos is the 1-based index WITHIN its stack.
                var renderItem = function (it, posInStack) {
                    var awb = Array.isArray(it.awbs) ? it.awbs.filter(Boolean).join(', ') : (it.awbs || '');
                    var prefix = String(it.uld || '').slice(0, 3).toUpperCase();
                    var col = prefixColorFor(prefix);
                    return '<li class="uld-delete-item uld-dispatch-item" style="--i:' + (posInStack - 1) + ';--dc:' + col + '">' +
                            '<span class="uld-delete-pos" style="color:' + col + ';border-color:' + col + '88;background:' + col + '1f">' + posInStack + '</span>' +
                            '<span class="uld-delete-uld">' + _esc(it.uld || '') + '</span>' +
                            (it.gha ? '<span class="uld-delete-gha">' + _esc(it.gha) + '</span>' : '<span class="uld-delete-gha uld-delete-gha-none">—</span>') +
                            '<span class="uld-delete-awb">' + _esc(awb || '—') + '</span>' +
                        '</li>';
                };

                var body;
                if (groups && groups.length > 1) {
                    // Multi-stack: one section per stack, each numbered 1..n on its own.
                    body = groups.map(function (g) {
                        var gItems = Array.isArray(g.items) ? g.items : [];
                        var rows = gItems.map(function (it, idx) { return renderItem(it, idx + 1); }).join('');
                        return '<div class="uld-dispatch-group">' +
                                '<div class="uld-dispatch-group-head">' +
                                    '<span class="uld-dispatch-group-name">' + _esc(g.name || 'Stack') + '</span>' +
                                    (g.gha ? '<span class="uld-dispatch-group-gha"' + ghaBadgeStyle(g.gha) + '>' + _esc(g.gha) + '</span>' : '') +
                                    '<span class="uld-dispatch-group-count">' + gItems.length + ' ULD</span>' +
                                '</div>' +
                                '<ul class="uld-delete-list uld-dispatch-group-list">' + rows + '</ul>' +
                            '</div>';
                    }).join('');
                } else {
                    var single = (groups && groups.length === 1) ? groups[0].items : items;
                    var rows = single.map(function (it, idx) { return renderItem(it, idx + 1); }).join('');
                    body = '<ul class="uld-delete-list">' + rows + '</ul>';
                }
                text.innerHTML = summary + body;
            }
        }
        modal.classList.remove('uld-delete-modal-closing');
        modal.classList.add('uld-delete-modal-show');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeKiadásModal() {
        var modal = document.getElementById('uld-dispatch-modal');
        if (modal) {
            modal.classList.add('uld-delete-modal-closing');
            modal.setAttribute('aria-hidden', 'true');
            setTimeout(function () {
                modal.classList.remove('uld-delete-modal-show', 'uld-delete-modal-closing');
            }, 200);
        }
        pendingKiadásStack = null;
    }

    function fmtDetailDate(iso) {
        if (!iso) return '—';
        var d = new Date(iso);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleString('hu-HU', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    }

    function openUldInfoModal(itemEl) {
        if (!itemEl) return;
        var modal = document.getElementById('uld-info-modal');
        var title = document.getElementById('uld-info-title');
        var body = document.getElementById('uld-info-body');
        if (!modal || !body) return;

        var uld = itemEl.dataset.uld || 'ULD';
        var awbs = String(itemEl.dataset.awbs || '').split(',').map(function (s) { return s.trim(); }).filter(Boolean);
        var awbHtml = awbs.length
            ? awbs.map(function (awb) { return '<span class="uld-info-awb-chip">' + _esc(awb) + '</span>'; }).join('')
            : '<span class="uld-info-empty">—</span>';
        if (title) title.textContent = uld;
        body.innerHTML =
            '<div class="uld-info-grid">' +
                '<div class="uld-info-field uld-info-field-wide"><span>AWB</span><strong class="uld-info-awb-list">' + awbHtml + '</strong></div>' +
                '<div class="uld-info-field"><span>Átadás ideje</span><strong>' + _esc(fmtDetailDate(itemEl.dataset.am || '')) + '</strong></div>' +
                '<div class="uld-info-field"><span>Kész ideje</span><strong>' + _esc(fmtDetailDate(itemEl.dataset.preparedAt || '')) + '</strong></div>' +
                '<div class="uld-info-field"><span>Stack</span><strong>' + _esc(itemEl.dataset.stackName || '—') + '</strong></div>' +
                '<div class="uld-info-field"><span>GHA</span><strong>' + _esc(itemEl.dataset.stackGha || '—') + '</strong></div>' +
                '<div class="uld-info-field"><span>Kiadás ideje</span><strong>' + _esc(fmtDetailDate(itemEl.dataset.dispatchedAt || '')) + '</strong></div>' +
                '<div class="uld-info-field"><span>Rendszám</span><strong>' + _esc(itemEl.dataset.dispatchRendszám || '—') + '</strong></div>' +
            '</div>';
        modal.classList.remove('uld-delete-modal-closing');
        modal.classList.add('uld-delete-modal-show');
        modal.setAttribute('aria-hidden', 'false');
    }

    function closeUldInfoModal() {
        var modal = document.getElementById('uld-info-modal');
        if (!modal) return;
        modal.classList.add('uld-delete-modal-closing');
        modal.setAttribute('aria-hidden', 'true');
        setTimeout(function () {
            modal.classList.remove('uld-delete-modal-show', 'uld-delete-modal-closing');
        }, 200);
    }

    function updateTouchbarFade(bar) {
        if (!bar) return;
        var max = Math.max(0, bar.scrollWidth - bar.clientWidth - 1);
        var left = bar.scrollHátra > 1;
        var right = bar.scrollHátra < max;
        bar.classList.toggle('uld-touch-has-left', left);
        bar.classList.toggle('uld-touch-has-right', right);
    }

    function refreshTouchbarFades() {
        document.querySelectorAll('.uld-dlist-touchbar').forEach(updateTouchbarFade);
    }

    // After a dispatched-search render, scroll each touchbar so its first matched
    // (highlighted) ULD chip is visible/centered — the operator instantly sees the
    // searched ULD even in a long stack.
    function scrollMatchedChipsIntoView() {
        document.querySelectorAll('.uld-dlist-touchbar').forEach(function (bar) {
            var match = bar.querySelector('.uld-dlist-touch-item-match');
            if (!match) return;
            try {
                bar.scrollHátra = Math.max(0, match.offsetHátra - (bar.clientWidth - match.offsetWidth) / 2);
            } catch (e) {}
        });
    }

    // ── Kiküldött nézet: row selection + bulk revert ───────────────────────────
    function dispatchedRowEls() {
        return Array.from(document.querySelectorAll('.uld-dlist-row[data-stack-id]'));
    }
    function applyDispatchedSelection() {
        var rows = dispatchedRowEls();
        // Drop ids that no longer exist (e.g. reverted) so the count stays honest.
        var present = new Set(rows.map(function (r) { return r.dataset.stackId; }));
        Array.from(dispatchedSelected).forEach(function (id) {
            if (!present.has(id)) dispatchedSelected.delete(id);
        });
        rows.forEach(function (r) {
            r.classList.toggle('uld-dlist-row-selected', dispatchedSelected.has(r.dataset.stackId));
        });
        var count = dispatchedSelected.size;
        var label = document.querySelector('.uld-dlist-selcount');
        if (label) {
            label.textContent = count + ' selected';
            label.dataset.count = String(count);
            label.classList.toggle('uld-dlist-selcount-active', count > 0);
        }
        var revSel = document.querySelector('.uld-dlist-revert-selected');
        if (revSel) revSel.disabled = count === 0;
        var selMind = document.querySelector('.uld-dlist-select-all');
        if (selMind) {
            var allSelected = rows.length > 0 && count === rows.length;
            selMind.textContent = allSelected ? 'Kijelölés törlése' : 'Mind kijelölése';
        }
        // Reset any armed bulk buttons when the selection changes.
        if (!count) disarmDispatchedBulk('.uld-dlist-revert-selected', 'Kijelöltek vissza');
    }
    function toggleDispatchedRow(stackId) {
        if (!stackId) return;
        if (dispatchedSelected.has(stackId)) dispatchedSelected.delete(stackId);
        else dispatchedSelected.add(stackId);
        applyDispatchedSelection();
    }
    function disarmDispatchedBulk(selector, label) {
        var btn = document.querySelector(selector);
        if (!btn) return;
        btn.classList.remove('uld-dlist-revert-armed');
        if (label) btn.textContent = label;
        clearTimeout(btn._disarm);
    }
    // Revert a list of dispatched stack ids (undispatch) — staggered, with the row
    // exit animation, reusing the same API the per-row Visszavonás uses.
    function revertDispatchedStacks(ids) {
        if (!ids || !ids.length) return;
        ids.forEach(function (id, idx) {
            var row = document.querySelector('.uld-dlist-row[data-stack-id="' + cssEsc(id) + '"]');
            setTimeout(function () {
                if (row) row.classList.add('uld-dlist-row-reverting');
                uldApiCall(
                    { action: 'undispatch', stack_id: id },
                    null,
                    {
                        applyState: false,
                        refreshDelay: 360,
                        rollback: function () { if (row) row.classList.remove('uld-dlist-row-reverting'); }
                    }
                );
            }, idx * 110);
        });
        var n = ids.length;
        dispatchedSelected.clear();
        applyDispatchedSelection();
        setTimeout(function () {
            _uldToast(n + ' stack visszaállítva az aktív nézetbe', 'ok');
            setStore('uld-list-dirty-store', Date.now());
        }, n * 110 + 120);
    }

    function confirmKiadásModal() {
        if (!pendingKiadásStack || !pendingKiadásStack.stackId) return;
        var info = pendingKiadásStack;
        var confirmBtn = document.getElementById('uld-dispatch-confirm');
        if (confirmBtn && !guardActionButton(confirmBtn, 'Kiadás...')) return;
        var plateInput = document.getElementById('uld-dispatch-plate');
        var dispatchRendszám = plateInput ? String(plateInput.value || '').trim().toUpperCase() : '';
        if (info.stackEls && info.stackEls.length) {
            var bulkTargets = info.stackEls.slice();
            closeKiadásModal();
            var pendingKiadáses = bulkTargets.length;
            var finishKiadás = function () {
                pendingKiadáses -= 1;
                if (pendingKiadáses <= 0) releaseActionButton(confirmBtn);
            };
            bulkTargets.forEach(function (stackEl) {
                if (!dispatchStackElNow(stackEl, dispatchRendszám, finishKiadás)) finishKiadás();
            });
            selectedStackek.clear();
            reconcileSelection();
            return;
        }
        var stackEl = stackElById(info.stackId);
        var dispatchRevision = info.stackRevision == null ? (stackEl ? stackRevision(stackEl) : undefined) : info.stackRevision;
        closeKiadásModal();
        if (stackEl) {
            stackEl.classList.add('uld-stack-dispatching');
            uldUiState.lockedStackek.delete(info.stackId);
            if (stackEl.classList.contains('uld-stack-clientmade')) {
                setTimeout(function () {
                    if (stackEl.parentNode) stackEl.parentNode.removeChild(stackEl);
                }, 380);
            }
        }
        info.removed.forEach(function (uld) {
            selectedUlds.delete(uld);
            clearRowStackBadge(uld);
            var row = rowForUld(uld);
            if (row) row.classList.add('uld-row-dispatching');
        });
        reconcileSelection();
        uldApiCall(
            { action: 'dispatched', stack_id: info.stackId, stack_revision: dispatchRevision, dispatch_plate: dispatchRendszám },
            function () {
                _uldToast(info.stackName + ' kiküldve', 'ok');
            },
            {
                applyState: false,
                listDirty: true,
                refreshDelay: 380,
                rollback: function () {
                    if (stackEl) stackEl.classList.remove('uld-stack-dispatching');
                    info.removed.forEach(function (uld) {
                        var row = rowForUld(uld);
                        if (row) row.classList.remove('uld-row-dispatching');
                    });
                },
                done: function () { releaseActionButton(confirmBtn); }
            }
        );
    }

    // ── delegated click handler ─────────────────────────────────────────────

    document.addEventListener('click', function (e) {
        if (e.target.closest('#settings-btn')) {
            e.preventDefault();
            openSettingsModal();
            return;
        }
        if (e.target.closest('#settings-close') || e.target.closest('#settings-cancel') || e.target.id === 'settings-overlay') {
            e.preventDefault();
            closeSettingsModal();
            return;
        }
        if (e.target.closest('#settings-save')) {
            e.preventDefault();
            saveSettingsModal();
            return;
        }
    var settingsBrowse = e.target.closest('#settings-ecomm-browse, #settings-pallets-browse, #settings-shared-state-browse');
        if (settingsBrowse) {
            e.preventDefault();
            browseSettingsFile(settingsBrowse.dataset.kind || 'ecomm', settingsBrowse);
            return;
        }
        // View switch (Aktív ULD-k ↔ Kiküldött stack-ek): keep the current search.
        // Operators often check the same pasted ULD list in both views, so clearing
        // here would hide the dispatched/missing accounting they need for release.
        var viewSwitchBtn = e.target.closest('#uld-view-active-btn, #uld-view-dispatched-btn');
        if (viewSwitchBtn) {
            dispatchedSelected.clear();
            setTimeout(updateDispatchedSelectionUi, 0);
        }
        // Active-view search → "found in dispatched stacks" callout: jump to the
        // dispatched view. The search store is shared, so the term is kept and the
        // matching dispatched stack is already highlighted there.
        var dispJump = e.target.closest('.uld-ss-dispatched-jump');
        if (dispJump) {
            e.preventDefault();
            var dispBtn = document.getElementById('uld-view-dispatched-btn');
            if (dispBtn) dispBtn.click();
            return;
        }
        // + Új stack — handled by Dash button callback; nothing to do here
        // Pagination
        var pgBtn = e.target.closest('.uld-pg-btn[data-page]:not(:disabled)');
        if (pgBtn) {
            var pg = parseInt(pgBtn.dataset.page, 10);
            if (!isNaN(pg) && pg > 0) {
                setStore('uld-page-store', pg);
                // Animate the list when new content arrives
                var listEl = document.getElementById('uld-list');
                if (listEl) listEl.classList.add('uld-page-turning');
            }
            return;
        }

        var editBtn = e.target.closest('.uld-row-edit');
        if (editBtn) {
            e.preventDefault();
            e.stopPropagation();
            var editRow = editBtn.closest('.uld-row[data-uld]');
            if (!editRow) {
                _uldToast('Az ULD sora nem található a szerkesztéshez', 'error');
                return;
            }
            openUldEditModal(editRow);
            return;
        }

        if (e.target.closest('#uld-edit-cancel') || e.target.closest('#uld-edit-close') || e.target.id === 'uld-edit-modal') {
            e.preventDefault();
            closeUldEditModal();
            return;
        }
        if (e.target.closest('#uld-edit-save')) {
            e.preventDefault();
            submitUldEditModal();
            return;
        }

        // Bulk stack tools (header toolbar)
        if (e.target.closest('#uld-expand-all-btn')) {
            e.preventDefault();
            e.stopPropagation();
            toggleExpandMindStackek();
            return;
        }
        if (e.target.closest('#uld-select-all-stacks-btn')) {
            e.preventDefault();
            e.stopPropagation();
            toggleSelectMindStackek();
            return;
        }

        // Accordion: the whole header toggles the stack, except the action
        // buttons (handled below) and the rename input.
        var accordionHdr = e.target.closest('.uld-stack-accordion .uld-stack-hdr');
        if (accordionHdr &&
            !e.target.closest('.uld-stack-actions') &&
            !e.target.closest('.uld-stack-name-input')) {
            var accStack = accordionHdr.closest('.uld-stack[data-stack-id]');
            if (accStack && accStack.classList.contains('uld-stack-collapsible')) {
                e.stopPropagation();
                if (e.target.closest('.uld-stack-name')) {
                    // Name supports double-click rename — disambiguate: defer the
                    // toggle briefly; a second click (dblclick) cancels it.
                    if (accStack._accToggleT) {
                        clearTimeout(accStack._accToggleT);
                        accStack._accToggleT = null;
                    } else {
                        accStack._accToggleT = setTimeout(function () {
                            accStack._accToggleT = null;
                            toggleStackAccordion(accStack);
                        }, 230);
                    }
                } else {
                    toggleStackAccordion(accStack);
                }
                return;
            }
        }

        if (e.target.closest('.uld-clear-selection-btn')) {
            clearSelection();
            return;
        }
        if (e.target.closest('.uld-select-all-btn')) {
            selectAllVisibleRows();
            return;
        }
        if (e.target.closest('.uld-selection-stack-print-btn')) {
            e.stopPropagation();
            printUldStackek(selectedStackElements());
            return;
        }
        if (e.target.closest('.uld-selection-stack-copy-btn')) {
            e.stopPropagation();
            copyUldStackek(selectedStackElements());
            return;
        }
        var selectionPrepBtn = e.target.closest('.uld-selection-stack-prep-btn');
        if (selectionPrepBtn) {
            e.stopPropagation();
            if (!guardActionButton(selectionPrepBtn, 'Mentés...')) return;
            // Mode is driven by the button's current status (set in
            // updateSelectionPrepButton): unprepare if all selected are prepared,
            // otherwise prepare the unprepared ones. Clicking the per-stack prep
            // button toggles, so we only target stacks in the opposite state.
            var unprepare = selectionPrepBtn.dataset.prepMode === 'unprepare';
            var selStackekP = selectedStackElements().filter(function (s) {
                return s.querySelectorAll('.uld-si[data-uld]').length > 0;
            });
            var prepTargets = selStackekP.filter(function (s) {
                return unprepare ? isPreparedStack(s) : !isPreparedStack(s);
            });
            if (!prepTargets.length) {
                releaseActionButton(selectionPrepBtn);
                _uldToast(unprepare ? 'Nincs visszaállítható stack a kijelölésben' : 'Nincs készre jelölhető stack a kijelölésben', 'info');
                return;
            }
            prepTargets.forEach(function (stackEl, idx) {
                setTimeout(function () {
                    var btn = stackEl.querySelector('.uld-stack-prep-btn');
                    if (btn && !btn.disabled) btn.click();
                }, idx * 90);
            });
            clearSelection();
            setTimeout(function () { releaseActionButton(selectionPrepBtn); }, prepTargets.length * 90 + 500);
            return;
        }
        var selectionKiadásBtn = e.target.closest('.uld-selection-stack-dispatch-btn');
        if (selectionKiadásBtn) {
            e.stopPropagation();
            if (!guardActionButton(selectionKiadásBtn, 'Kiadás...')) return;
            var dispatchTargets = selectedStackElements().filter(function (s) {
                return s.querySelectorAll('.uld-si[data-uld]').length > 0;
            });
            if (!dispatchTargets.length) {
                releaseActionButton(selectionKiadásBtn);
                _uldToast('Nincs kiadható stack a kijelölésben', 'info');
                return;
            }
            releaseActionButton(selectionKiadásBtn);
            // Build a per-stack breakdown so the preview shows each stack as its own
            // section with its own 1..n numbering — never one flat 1..hundreds list.
            var allItems = [];
            var groups = dispatchTargets.map(function (stackEl) {
                var gItems = stackSnapshotItems(stackEl);
                allItems = allItems.concat(gItems);
                return {
                    id: stackEl.dataset.stackId || '',
                    name: stackEl.dataset.stackName || 'Stack',
                    gha: (stackGha(stackEl) || ''),
                    items: gItems
                };
            });
            var modalTitle = dispatchTargets.length === 1
                ? (dispatchTargets[0].dataset.stackName || 'stack')
                : ('Kiadás ' + dispatchTargets.length + ' selected stacks');
            openKiadásModal(
                dispatchTargets.length === 1 ? dispatchTargets[0].dataset.stackId : '__bulk__',
                modalTitle,
                allItems,
                {
                    stackEls: dispatchTargets.length > 1 ? dispatchTargets : null,
                    groups: groups
                }
            );
            return;
        }
        // Target chip — add selected ULDs to existing stack
        var targetChip = e.target.closest('.uld-target-chip');
        if (targetChip && !selectedStackek.size) {
            e.stopPropagation();
            if (!guardActionButton(targetChip, null)) return;
            if (targetChip.classList.contains('uld-target-chip-disabled')) {
                releaseActionButton(targetChip);
                _uldToast('GHA eltérés - nem kompatibilis', 'error');
                return;
            }
            var stackId = targetChip.dataset.stackId;
            var stackName = targetChip.dataset.stackName || 'stack';
            var stackEl = document.querySelector('.uld-stack[data-stack-id="' + cssEsc(stackId) + '"]');
            if (!stackEl) { releaseActionButton(targetChip); return; }
            // Prepared target — locked. (Chip rendering also marks them disabled,
            // but defend in depth so a stale chip can't slip through.)
            if (isPreparedStack(stackEl)) {
                releaseActionButton(targetChip);
                _uldToast('Kész stackhez nem adható új ULD', 'info');
                return;
            }
            var ulds = Array.from(selectedUlds);
            if (!ulds.length) { releaseActionButton(targetChip); return; }
            // Selected ULDs cannot include any prepared-stack ULD (selection is
            // already filtered by `applySelectionClasses`, but defend again).
            if (ulds.some(uldIsInPreparedStack)) {
                releaseActionButton(targetChip);
                _uldToast('Készre jelölt ULD van a kijelölésben - nem mozgatható', 'info');
                return;
            }
            if (!moveAllowed(stackEl, ulds)) {
                releaseActionButton(targetChip);
                _uldToast(moveBlockMessage(stackEl, ulds), 'error');
                return;
            }
            var targetRevision = stackRevision(stackEl);
            animateUldTransferToStack(ulds, stackEl, targetChip);
            markRowsStackPending(ulds);
            uldApiCall(
                { action: 'add', uld_numbers: ulds, stack_id: stackId, stack_revision: targetRevision },
                function () {
                    confirmRowsStackMembership(ulds, stackId);
                    _uldToast(ulds.length + ' ULD hozzáadva ehhez: ' + stackName, 'ok');
                },
                {
                    listDirty: true,
                    refreshDelay: 0,
                    retryOnConflict: 2,
                    retryTransient: 2,
                    rollback: function () { clearRowsStackPending(ulds); },
                    done: function () { releaseActionButton(targetChip); }
                }
            );
            clearSelection();
            return;
        }
        // + Új stack inside selection bar — create then add
        var newStackBtn = e.target.closest('.uld-selection-newstack-btn');
        if (newStackBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
            if (newStackBtn.disabled) return;
            var pulds = Array.from(selectedUlds);
            if (!pulds.length) return;
            if (pulds.some(uldIsInPreparedStack)) {
                _uldToast('Készre jelölt ULD van a kijelölésben - előbb vedd le a jelölést', 'info');
                return;
            }
            createStackThenHozzáadás(pulds, newStackBtn);
            return;
        }
        var stackSelectBtn = e.target.closest('.uld-stack-select');
        if (stackSelectBtn) {
            e.stopPropagation();
            var stackSel = stackSelectBtn.closest('.uld-stack[data-stack-id]');
            if (stackSel) toggleStackSelection(stackSel);
            return;
        }
        var kéziBtn = e.target.closest('#uld-manual-open-btn');
        if (kéziBtn) {
            e.stopPropagation();
            openManualModal();
            return;
        }
        if (e.target.closest('#uld-manual-cancel') || e.target.closest('#uld-manual-close') || e.target.id === 'uld-manual-modal') {
            closeManualModal();
            return;
        }
        if (e.target.closest('#uld-manual-datetime-clear')) {
            e.preventDefault();
            var dtField = kéziField('uld-manual-datetime');
            if (dtField) {
                dtField.value = '';
                clearManualDuplicate();
                dtField.focus();
            }
            return;
        }
        if (e.target.closest('#uld-manual-save')) {
            submitManualModal();
            return;
        }
        // Stack dispatch
        var dispatchBtn = e.target.closest('.uld-stack-dispatch-btn');
        if (dispatchBtn) {
            e.stopPropagation();
            if (dispatchBtn.disabled) return;
            if (!guardActionButton(dispatchBtn, null)) return;
            var stackElD = dispatchBtn.closest('.uld-stack');
            var stackIdD = stackIdOf(stackElD) || dispatchBtn.dataset.stackId;
            var stackNameD = stackElD ? (stackElD.dataset.stackName || 'stack') : 'stack';
            var itemsD = stackSnapshotItems(stackElD);
            if (!stackIdD || !itemsD.length) {
                releaseActionButton(dispatchBtn);
                _uldToast('Üres stack - előbb adj hozzá ULD-t', 'info');
                return;
            }
            releaseActionButton(dispatchBtn);
            openKiadásModal(stackIdD, stackNameD, itemsD);
            return;
        }

        // Kiküldött nézet — "Mind kijelöl" / kijelölés törlése.
        if (e.target.closest('.uld-dlist-select-all')) {
            e.stopPropagation();
            var allRows = dispatchedRowEls();
            var allSelected = allRows.length > 0 && dispatchedSelected.size === allRows.length;
            dispatchedSelected.clear();
            if (!allSelected) allRows.forEach(function (r) { dispatchedSelected.add(r.dataset.stackId); });
            applyDispatchedSelection();
            return;
        }
        // Kiküldött nézet — "Kijelöltek visszavonása" (two-step confirm).
        var revSelBtn = e.target.closest('.uld-dlist-revert-selected');
        if (revSelBtn) {
            e.stopPropagation();
            if (revSelBtn.disabled || !dispatchedSelected.size) return;
            if (!revSelBtn.classList.contains('uld-dlist-revert-armed')) {
                revSelBtn.classList.add('uld-dlist-revert-armed');
                revSelBtn.textContent = 'Megerősíted? (' + dispatchedSelected.size + ')';
                clearTimeout(revSelBtn._disarm);
                revSelBtn._disarm = setTimeout(function () {
                    disarmDispatchedBulk('.uld-dlist-revert-selected', 'Kijelöltek vissza');
                }, 3000);
                return;
            }
            disarmDispatchedBulk('.uld-dlist-revert-selected', 'Kijelöltek vissza');
            revertDispatchedStacks(Array.from(dispatchedSelected));
            return;
        }
        // Kiküldött nézet — "Mind visszavonása" (two-step confirm).
        var revMindBtn = e.target.closest('.uld-dlist-revert-all');
        if (revMindBtn) {
            e.stopPropagation();
            var everyId = dispatchedRowEls().map(function (r) { return r.dataset.stackId; }).filter(Boolean);
            if (!everyId.length) { _uldToast('Nincs kiküldött stack', 'info'); return; }
            if (!revMindBtn.classList.contains('uld-dlist-revert-armed')) {
                revMindBtn.classList.add('uld-dlist-revert-armed');
                revMindBtn.textContent = 'Megerősíted? (' + everyId.length + ')';
                clearTimeout(revMindBtn._disarm);
                revMindBtn._disarm = setTimeout(function () {
                    disarmDispatchedBulk('.uld-dlist-revert-all', 'Mind vissza');
                }, 3000);
                return;
            }
            disarmDispatchedBulk('.uld-dlist-revert-all', 'Mind vissza');
            revertDispatchedStacks(everyId);
            return;
        }

        // Kiadott lista revert (Visszavonás) — two-step inline confirm, then
        // animate the row out and un-dispatch the stack back to the active view.
        var revertBtn = e.target.closest('.uld-dlist-revert');
        if (revertBtn) {
            e.stopPropagation();
            var rStackId = revertBtn.dataset.stackId;
            var rName = revertBtn.dataset.stackName || 'stack';
            if (!rStackId) return;
            if (!revertBtn.classList.contains('uld-dlist-revert-armed')) {
                // First click → arm; auto-disarm after 3s.
                revertBtn.classList.add('uld-dlist-revert-armed');
                revertBtn.textContent = 'Megerősíted?';
                clearTimeout(revertBtn._disarm);
                revertBtn._disarm = setTimeout(function () {
                    revertBtn.classList.remove('uld-dlist-revert-armed');
                    revertBtn.textContent = 'Vissza';
                }, 3000);
                return;
            }
            // Second click → execute.
            clearTimeout(revertBtn._disarm);
            if (!guardActionButton(revertBtn, '…')) return;
            var rRow = revertBtn.closest('.uld-dlist-row');
            if (rRow) rRow.classList.add('uld-dlist-row-reverting');
            uldApiCall(
                { action: 'undispatch', stack_id: rStackId },
                function () {
                    _uldToast(rName + ' visszaállítva az aktív nézetbe', 'ok');
                },
                {
                    applyState: false,
                    listDirty: true,
                    refreshDelay: 360,
                    rollback: function () {
                        if (rRow) rRow.classList.remove('uld-dlist-row-reverting');
                        releaseActionButton(revertBtn);
                        revertBtn.classList.remove('uld-dlist-revert-armed');
                        revertBtn.textContent = 'Vissza';
                    },
                    done: function () { releaseActionButton(revertBtn); }
                }
            );
            return;
        }

        // Stack delete (×)
        var delBtn = e.target.closest('.uld-stack-del');
        if (delBtn) {
            e.stopPropagation();
            if (!guardActionButton(delBtn, null)) return;
            var stackEl = delBtn.closest('.uld-stack');
            var stackId = stackIdOf(stackEl) || delBtn.dataset.stackId;
            var stackName = stackEl ? (stackEl.dataset.stackName || 'stack') : 'stack';
            if (stackId) {
                // Prefer the rich server snapshot (pos/gha/awbs/active); fall back
                // to scraping the DOM items if it isn't present.
                var items = stackSnapshotItems(stackEl);
                releaseActionButton(delBtn);
                openTörlésModal(stackId, stackName, items);
            } else {
                releaseActionButton(delBtn);
            }
            return;
        }
        if (e.target.closest('#uld-delete-cancel') || e.target.id === 'uld-delete-modal') {
            closeTörlésModal();
            return;
        }
        if (e.target.closest('#uld-delete-confirm')) {
            confirmTörlésModal();
            return;
        }
        if (e.target.closest('#uld-dispatch-cancel') || e.target.id === 'uld-dispatch-modal') {
            closeKiadásModal();
            return;
        }
        if (e.target.closest('#uld-dispatch-confirm')) {
            confirmKiadásModal();
            return;
        }
        if (e.target.closest('#uld-info-close') || e.target.id === 'uld-info-modal') {
            closeUldInfoModal();
            return;
        }
        var touchItem = e.target.closest('.uld-dlist-touch-item[data-uld]');
        if (touchItem) {
            e.stopPropagation();
            openUldInfoModal(touchItem);
            return;
        }
        // Kiküldött nézet — click a row (anywhere except the revert button / a ULD
        // chip handled above) toggles its selection for the bulk actions.
        var dlistRow = e.target.closest('.uld-dlist-row[data-stack-id]');
        if (dlistRow) {
            e.stopPropagation();
            toggleDispatchedRow(dlistRow.dataset.stackId);
            return;
        }
        // Stack "Összekészítve" toggle — concurrency-friendly (no revision check):
        // it's a metadata flag, multiple users may toggle independently without
        // conflicting on the underlying ULD content.
        var prepBtn = e.target.closest('.uld-stack-prep-btn');
        if (prepBtn) {
            e.stopPropagation();
            if (prepBtn.disabled) return;
            if (!guardActionButton(prepBtn, 'Mentés...')) return;
            var stackElP = prepBtn.closest('.uld-stack[data-stack-id]');
            if (!stackElP) { releaseActionButton(prepBtn); return; }
            var stackIdP = stackElP.dataset.stackId;
            // Empty stack → don't allow prepared (nothing to prepare).
            var hasItems = stackElP.querySelectorAll('.uld-si[data-uld]').length > 0;
            var nowPrepared = stackElP.dataset.prepared === '1' || stackElP.classList.contains('uld-stack-prepared');
            if (!hasItems && !nowPrepared) {
                releaseActionButton(prepBtn);
                _uldToast('Üres stack - előbb adj hozzá ULD-t', 'info');
                return;
            }
            var willPrepare = !nowPrepared;
            var domSnapshot = captureUldDom();
            // Optimistic visual toggle — class + label + dataset
            stackElP.classList.toggle('uld-stack-prepared', willPrepare);
            stackElP.dataset.prepared = willPrepare ? '1' : '0';
            // Marking prepared must NOT change the stack's open/closed state. The API
            // refresh re-renders the grid and setupStackCollapse() reapplies accordion
            // state from expandedStackek; pin the *current* state here so a prepared
            // stack never silently re-opens (or collapses) on the refresh.
            if (stackIdP) {
                if (stackElP.classList.contains('uld-stack-expanded')) expandedStackek.add(stackIdP);
                else expandedStackek.delete(stackIdP);
            }
            // Toggle ON → "✓ Összekészítve" (checkmark persists); OFF → "Összekészít".
            // The previous OFF label wrongly read "Összekészítve", so an un-prepared
            // stack looked prepared and the ✓ vanished on the next render.
            prepBtn.textContent = willPrepare ? 'Kész' : 'Készre jelöl';
            prepBtn.className = 'uld-stack-act uld-stack-prep-btn ' + (willPrepare ? 'uld-stack-prep-active' : 'uld-stack-prep-empty');
            prepBtn.title = willPrepare ? 'Kész jelölés törlése' : 'Stack készre jelölése';
            prepBtn.dataset.uldIdleLabel = prepBtn.textContent;
            stackElP.querySelectorAll('.uld-si[data-uld]').forEach(function (si) {
                var row = rowForUld(si.dataset.uld);
                if (row) {
                    row.classList.toggle('uld-row-prepared', willPrepare);
                    row.dataset.prepared = willPrepare ? '1' : '0';
                }
            });
            uldApiCall(
                { action: 'prepared', stack_id: stackIdP, prepared: willPrepare },
                function () { _uldToast(willPrepare ? 'Stack készre jelölve' : 'Kész jelölés törölve', 'ok'); },
                {
                    refreshDelay: 120,
                    rollback: function () { restoreUldDom(domSnapshot); },
                    done: function () { releaseActionButton(prepBtn); }
                }
            );
            return;
        }
        // Stack print
        if (e.target.closest('.uld-stack-print')) {
            var stackEl2 = e.target.closest('.uld-stack');
            if (stackEl2) printUldStack(stackEl2);
            return;
        }
        // Stack copy
        var copyHit = e.target.closest('.uld-stack-copy');
        if (copyHit) {
            var stackEl3 = e.target.closest('.uld-stack');
            if (stackEl3) copyUldStack(stackEl3);
            copyHit.classList.remove('uld-copy-done');
            void copyHit.offsetWidth;
            copyHit.classList.add('uld-copy-done');
            clearTimeout(copyHit._copyTimer);
            copyHit._copyTimer = setTimeout(function () { copyHit.classList.remove('uld-copy-done'); }, 700);
            return;
        }
        // Remove ULD from stack item
        var removeBtn = e.target.closest('.uld-si-remove');
        if (removeBtn) {
            e.stopPropagation();
            if (!guardActionButton(removeBtn, null)) return;
            var uldNum = removeBtn.dataset.uld;
            if (uldNum) {
                var itemEl = removeBtn.closest('.uld-si[data-uld]');
                var parentStack = itemEl ? itemEl.closest('.uld-stack[data-stack-id]') : null;
                if (parentStack && stackLocked(parentStack)) {
                    releaseActionButton(removeBtn);
                    _uldToast('Stack mentése folyamatban', 'info');
                    return;
                }
                if (parentStack && isPreparedStack(parentStack)) {
                    releaseActionButton(removeBtn);
                    _uldToast('Kész stack – előbb töröld a kész jelölést', 'info');
                    return;
                }
                markSyncing(parentStack ? [parentStack] : [], [uldNum]);
                selectedUlds.delete(uldNum);
                reconcileSelection();
                var parentStackId = stackIdOf(parentStack);
                uldApiCall(function () { return removeOneFromStackPayload(parentStackId, uldNum); }, function () {
                    _uldToast(uldNum + ' eltávolítva a stackből', 'ok');
                }, {
                    listDirty: true,
                    refreshDelay: 0,
                    retryOnConflict: 2,
                    retryTransient: 2,
                    done: function () {
                        releaseActionButton(removeBtn);
                        clearSyncingSoon(parentStack ? [parentStack] : [], [uldNum]);
                    }
                });
            } else {
                releaseActionButton(removeBtn);
            }
            return;
        }
        // Filter chip
        var chip = e.target.closest('.uld-chip[data-filter]');
        if (chip) {
            var dim   = chip.dataset.filter;
            var value = chip.dataset.value || '';
            if ((e.ctrlKey || e.metaKey) && chip.dataset.quickSelect && value) {
                e.preventDefault();
                selectByAttr(chip.dataset.quickSelect, value, true);
                _uldToast(selectedUlds.size + ' ULD kijelölve', 'info');
                return;
            }
            var storeMap = {
                'status': 'uld-filter-store',
                'prefix': 'uld-prefix-filter-store',
                'gha':    'uld-gha-filter-store',
            };
            var storeId = storeMap[dim];
            if (storeId) {
                // Immediate visual feedback before render: toggle active state in this row
                var row = chip.closest('.uld-chip-row');
                if (row) {
                    row.querySelectorAll('.uld-chip-active').forEach(function (c) {
                        c.classList.remove('uld-chip-active');
                    });
                    chip.classList.add('uld-chip-active');
                }
                markUldListTransition();
                setStore(storeId, value || (dim === 'status' ? 'all' : ''));
                setStore('uld-page-store', 1); // reset to first page on filter change
            }
            return;
        }
        // Szűrők törlése
        if (e.target.closest('.uld-reset-btn')) {
            clearSelection();
            setStore('uld-filter-store', 'all');
            setStore('uld-prefix-filter-store', '');
            setStore('uld-gha-filter-store', '');
            setStore('uld-page-store', 1);
            var input = document.getElementById('uld-search-input');
            if (input) input.value = '';
            pushSearchValue('', 0);
            _uldToast('Szűrők visszaállítva', 'info');
            return;
        }
    });

    // ── search input — debounced 220ms via JS (avoids Dash thrash) ─────────

    var _searchTimer = null;
    var _lastSearchValue = '';
    function pushSearchValue(value, delay) {
        var val = value || '';
        if (_searchTimer) clearTimeout(_searchTimer);
        _searchTimer = setTimeout(function () {
            if (val === _lastSearchValue) return;
            _lastSearchValue = val;
            markUldListTransition();
            setStore('uld-search-store', val);
            setStore('uld-page-store', 1); // reset to first page on search
        }, delay == null ? 120 : delay);
    }
    document.addEventListener('input', function (e) {
        if (!e.target) return;
        if (e.target.id === 'uld-manual-awb') {
            e.target.value = String(e.target.value || '').replace(/\D/g, '');
            clearManualDuplicate();
            return;
        }
        if (e.target.id === 'uld-manual-gha') {
            clearManualDuplicate();
            var val = String(e.target.value || '').trim().toLowerCase();
            var hit = val ? kéziGhaOptions.find(function (name) { return name.toLowerCase().indexOf(val) === 0; }) : '';
            e.target.dataset.suggestion = hit || '';
            return;
        }
        if (e.target.id === 'uld-manual-uld') {
            clearManualDuplicate();
            return;
        }
        if (e.target.id === 'uld-manual-datetime') {
            clearManualDuplicate();
            return;
        }
        if (!e.target || e.target.id !== 'uld-search-input') return;
        pushSearchValue(e.target.value || '', 120);
    });
    document.addEventListener('change', function (e) {
        if (!e.target) return;
        if (e.target.id === 'uld-manual-stack') {
            clearManualDuplicate();
            updateManualGhaMode();
        }
        if (e.target.id === 'uld-manual-datetime') {
            if (!e.target.value) {
                e.target.max = localDateTimeValue(new Date());
                return;
            }
            setManualTimeFromDate(new Date(e.target.value));
        }
    });
    document.addEventListener('blur', function (e) {
        if (!e.target) return;
        if (e.target.id === 'uld-manual-gha') {
            var completed = normalizeManualGha(e.target.value || '');
            if (kéziGhaOptions.indexOf(completed) !== -1) e.target.value = completed;
        }
    }, true);
    document.addEventListener('keydown', function (e) {
        // Unified modal keyboard contract: Escape closes the topmost open modal,
        // Enter triggers its primary action (add / confirm / dispatch). Works no
        // matter where focus is, so it applies to the kézi ULD add, dispatch
        // confirmation, delete confirmation, etc.
        var openModal = uldTopOpenModal();
        if (openModal) {
            if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                openModal.close();
                return;
            }
            if (e.key === 'Enter') {
                // Don't hijack Enter inside a real multiline textarea (newline).
                if (e.target && e.target.tagName === 'TEXTAREA') return;
                if (openModal.confirm) {
                    e.preventDefault();
                    e.stopPropagation();
                    openModal.confirm();
                }
                return;
            }
            return;
        }
        if (!e.target || e.target.id !== 'uld-search-input') return;
        if (e.key === 'Escape') {
            e.target.value = '';
            pushSearchValue('', 0);
            e.preventDefault();
        }
        if (e.target.tagName === 'TEXTAREA' && e.key === 'Enter') return;
        if (e.key === 'Enter') {
            pushSearchValue(e.target.value || '', 0);
        }
    });

// ── init on load ────────────────────────────────────────────────────────

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            var search = document.getElementById('uld-search-input');
            if (search) search.placeholder = 'Keres\u00e9s: ULD sz\u00e1m vagy AWB... Bulk: ULD-k soronk\u00e9nt';
            updateCountdowns();
        });
    } else {
        var search = document.getElementById('uld-search-input');
        if (search) search.placeholder = 'Keres\u00e9s: ULD sz\u00e1m vagy AWB... Bulk: ULD-k soronk\u00e9nt';
        updateCountdowns();
    }

})();
