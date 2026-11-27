(function () {
    'use strict';

    function cleanCell(value) {
        return String(value == null ? '' : value).replace(/[\t\r\n]+/g, ' ').trim();
    }

    function escapeHtml(value) {
        return cleanCell(value).replace(/[&<>"']/g, function (c) {
            return { '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c];
        });
    }

    function readSummaryTable() {
        var table = document.getElementById('summary-prio-table');
        if (!table) return null;
        var rows = Array.from(table.querySelectorAll('tr')).map(function (tr) {
            return Array.from(tr.children).map(function (cell) {
                return cleanCell(cell.textContent || '');
            });
        }).filter(function (row) {
            return row.some(Boolean);
        });
        return rows.length ? rows : null;
    }

    // The full active-AWB list rendered (hidden) by app.py _summary_awb_data.
    function readActiveAwbs() {
        var body = document.getElementById('summary-body');
        // While fill_summary runs, the body still shows the previous opening:
        // never copy that stale list.
        if (!body || body.getAttribute('data-dash-is-loading') === 'true') return null;
        var holder = body.querySelector('.summary-awb-data');
        if (!holder) return null;
        var raw = holder.getAttribute('data-awbs') || '';
        var seen = Object.create(null);
        return raw.split(/\r?\n/).map(function (v) { return v.trim(); }).filter(function (v) {
            if (!v || seen[v]) return false;
            seen[v] = true;
            return true;
        });
    }

    function originalLabel(button) {
        if (!button.dataset.originalText) button.dataset.originalText = button.textContent || '';
        return button.dataset.originalText;
    }

    function setFeedback(button, ok, okText) {
        if (!button) return;
        var original = originalLabel(button);
        clearTimeout(button._copyReset);
        button.textContent = ok ? (okText || 'Másolva') : 'Sikertelen';
        button.classList.toggle('summary-copy-ok', ok);
        button.classList.toggle('summary-copy-fail', !ok);
        button._copyReset = setTimeout(function () {
            button.textContent = original;
            button.classList.remove('summary-copy-ok', 'summary-copy-fail');
        }, 1400);
    }

    function writePlain(text, done) {
        function legacy() {
            var holder = document.createElement('textarea');
            holder.value = text;
            holder.setAttribute('readonly', '');
            holder.style.position = 'fixed';
            holder.style.left = '-9999px';
            document.body.appendChild(holder);
            holder.select();
            var ok = false;
            try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
            document.body.removeChild(holder);
            done(ok);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () { done(true); }).catch(legacy);
        } else {
            legacy();
        }
    }

    function copyRows(rows, button) {
        var text = rows.map(function (row) { return row.map(cleanCell).join('\t'); }).join('\r\n');
        var htmlRows = rows.map(function (row, idx) {
            var tag = idx === 0 ? 'th' : 'td';
            var style = idx === 0
                ? 'border:1px solid #1f2937;background:#111827;color:#fff;font-weight:800;padding:8px 10px;text-align:left;'
                : 'border:1px solid #1f2937;padding:7px 10px;text-align:left;color:#111827;';
            return '<tr>' + row.map(function (cell) {
                return '<' + tag + ' style="' + style + '">' + escapeHtml(cell) + '</' + tag + '>';
            }).join('') + '</tr>';
        }).join('');
        var html = '<html><body><table style="border-collapse:collapse;font-family:Segoe UI,Arial,sans-serif;font-size:13px;">'
            + htmlRows + '</table></body></html>';

        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], { type: 'text/html' }),
                'text/plain': new Blob([text], { type: 'text/plain' })
            })]).then(function () { setFeedback(button, true); }).catch(function () {
                writePlain(text, function (ok) { setFeedback(button, ok); });
            });
        } else {
            writePlain(text, function (ok) { setFeedback(button, ok); });
        }
    }

    // Button label carries the count, so the operator sees what will be copied.
    function syncAwbButton() {
        var button = document.getElementById('summary-copy-awb-btn');
        if (!button || button.classList.contains('summary-copy-ok') || button.classList.contains('summary-copy-fail')) return;
        var awbs = readActiveAwbs();
        var label = awbs && awbs.length ? 'AWB-k másolása (' + awbs.length + ')' : 'AWB-k másolása';
        button.dataset.originalText = label;
        if (button.textContent !== label) button.textContent = label;
        var empty = !!awbs && awbs.length === 0;
        if (button.disabled !== empty) button.disabled = empty;
    }

    function watchSummaryBody(attempt) {
        var body = document.getElementById('summary-body');
        if (!body) {
            if ((attempt || 0) < 200) setTimeout(function () { watchSummaryBody((attempt || 0) + 1); }, 150);
            return;
        }
        new MutationObserver(syncAwbButton).observe(body, {
            childList: true, subtree: true,
            attributes: true, attributeFilter: ['data-dash-is-loading']
        });
        syncAwbButton();
    }

    document.addEventListener('click', function (event) {
        var awbButton = event.target.closest('#summary-copy-awb-btn');
        if (awbButton) {
            event.preventDefault();
            var awbs = readActiveAwbs();
            if (!awbs || !awbs.length) {
                setFeedback(awbButton, false);
                awbButton.textContent = awbs ? 'Nincs aktív AWB' : 'Még töltődik';
                return;
            }
            writePlain(awbs.join('\r\n'), function (ok) {
                setFeedback(awbButton, ok, 'Másolva: ' + awbs.length + ' AWB');
            });
            return;
        }
        var button = event.target.closest('#summary-copy-btn');
        if (!button) return;
        event.preventDefault();
        var rows = readSummaryTable();
        if (!rows) {
            setFeedback(button, false);
            button.textContent = 'Nincs adat';
            return;
        }
        copyRows(rows, button);
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { watchSummaryBody(0); });
    } else {
        watchSummaryBody(0);
    }
})();
