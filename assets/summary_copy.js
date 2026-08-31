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

        function setFeedback(ok) {
            if (!button) return;
            var original = button.dataset.originalText || button.textContent || 'Másolás';
            button.dataset.originalText = original;
            button.textContent = ok ? 'Másolva' : 'Sikertelen';
            button.classList.toggle('summary-copy-ok', ok);
            setTimeout(function () {
                button.textContent = original;
                button.classList.remove('summary-copy-ok');
            }, 1300);
        }

        function fallback() {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(text).then(function () { setFeedback(true); }).catch(function () { setFeedback(false); });
                return;
            }
            var holder = document.createElement('textarea');
            holder.value = text;
            holder.style.position = 'fixed';
            holder.style.left = '-9999px';
            document.body.appendChild(holder);
            holder.select();
            try { setFeedback(document.execCommand('copy')); }
            catch (err) { setFeedback(false); }
            document.body.removeChild(holder);
        }

        if (navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
            navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], { type: 'text/html' }),
                'text/plain': new Blob([text], { type: 'text/plain' })
            })]).then(function () { setFeedback(true); }).catch(fallback);
        } else {
            fallback();
        }
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('#summary-copy-btn');
        if (!button) return;
        event.preventDefault();
        var rows = readSummaryTable();
        if (!rows) {
            button.textContent = 'Nincs adat';
            setTimeout(function () { button.textContent = 'Másolás'; }, 1200);
            return;
        }
        copyRows(rows, button);
    });
})();
