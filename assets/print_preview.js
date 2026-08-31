(function () {
    'use strict';

    // ── helpers ──────────────────────────────────────────────────────────────

    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function fmt(v) {
        if (v === null || v === undefined || v === '') return '';
        var n = parseFloat(v);
        if (isNaN(n) || n === 0) return '';
        return String(Math.round(n));
    }

    function nowStr() {
        var d = new Date();
        return d.getFullYear() + '.' +
            String(d.getMonth() + 1).padStart(2, '0') + '.' +
            String(d.getDate()).padStart(2, '0') + ' ' +
            String(d.getHours()).padStart(2, '0') + ':' +
            String(d.getMinutes()).padStart(2, '0');
    }

    // ── row builder ──────────────────────────────────────────────────────────
    // Cols: # | LMP | AWB | Rendszám | Colli | PLT | Korr. | Tárhely | Megjegyzés | del

    function buildRowHTML(item, idx, plate) {
        var rc = item.is_issued ? 'pr-issued' : item.is_ready ? 'pr-ready' : '';
        return (
            '<tr class="' + rc + '" data-idx="' + idx + '">' +
            '<td class="ppt-num">' + idx + '</td>' +
            '<td class="ppt-lmp" contenteditable="true">' + esc(item.lmp) + '</td>' +
            '<td class="ppt-awb" contenteditable="true">' + esc(item.awb) + '</td>' +
            '<td class="ppt-plate" contenteditable="true">' + esc(plate) + '</td>' +
            '<td class="ppt-num ppt-ce" contenteditable="true">' + fmt(item.boxes) + '</td>' +
            '<td class="ppt-num ppt-ce" contenteditable="true">' + fmt(item.pallets_issued) + '</td>' +
            '<td class="ppt-num ppt-korr" contenteditable="true"></td>' +
            '<td class="ppt-loc" contenteditable="true">' + esc(item.location) + '</td>' +
            '<td class="ppt-note" contenteditable="true">' + esc(item.load_note) + '</td>' +
            '<td class="ppt-del no-print"><button class="ppt-del-btn" title="Törlés">&times;</button></td>' +
            '</tr>'
        );
    }

    // ── summary ──────────────────────────────────────────────────────────────

    function updateSummary(paper) {
        var tbody    = paper.querySelector('#ppt-tbody');
        var sumColli = paper.querySelector('.sum-colli');
        var sumPlt   = paper.querySelector('.sum-plt');
        var sumLabel = paper.querySelector('.sum-label');
        if (!tbody) return;
        var rows = tbody.querySelectorAll('tr');
        var tc = 0, tp = 0;
        rows.forEach(function (row) {
            var cells = row.querySelectorAll('td');
            if (cells.length >= 6) {
                tc += parseFloat(cells[4].textContent.trim()) || 0;
                tp += parseFloat(cells[5].textContent.trim()) || 0;
            }
        });
        if (sumColli) sumColli.textContent = tc > 0 ? Math.round(tc) : '—';
        if (sumPlt)   sumPlt.textContent   = tp > 0 ? Math.round(tp) : '—';
        if (sumLabel) sumLabel.textContent = 'ÖSSZESEN (' + rows.length + ' tétel)';
    }

    function renumber(tbody) {
        tbody.querySelectorAll('tr').forEach(function (row, i) {
            var cells = row.querySelectorAll('td');
            if (cells.length > 0) cells[0].textContent = i + 1;
            row.dataset.idx = i + 1;
        });
    }

    // ── table handlers ───────────────────────────────────────────────────────

    function attachTableHandlers(paper) {
        var tbody = paper.querySelector('#ppt-tbody');
        if (!tbody) return;

        tbody.addEventListener('input', function (e) {
            updateSummary(paper);
            // Sync Rendszám across all rows AND the meta header box
            if (e.target.classList.contains('ppt-plate')) {
                var val = e.target.textContent;
                tbody.querySelectorAll('.ppt-plate').forEach(function (cell) {
                    if (cell !== e.target) cell.textContent = val;
                });
                var metaPlate = paper.querySelector('.pmv-plate');
                if (metaPlate) metaPlate.textContent = val;
            }
        });

        tbody.addEventListener('click', function (e) {
            if (e.target.classList.contains('ppt-del-btn')) {
                var row = e.target.closest('tr');
                if (row) { row.remove(); renumber(tbody); updateSummary(paper); }
            }
        });
    }

    // ── barcode SVG ──────────────────────────────────────────────────────────

    function makeBarcodeSVG(text) {
        if (!text) return '';
        if (typeof JsBarcode === 'undefined') {
            return '<span style="font-size:8px;color:#999">' + esc(text) + '</span>';
        }
        var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        try {
            JsBarcode(svg, text, {
                format:       'CODE128',
                displayValue: true,
                fontSize:     12,
                width:        1.8,
                height:       48,
                margin:       2,
                textAlign:    'center',
                font:         '"Segoe UI", Arial, sans-serif',
                lineColor:    '#000000',
                background:   'transparent',
            });
            return svg.outerHTML;
        } catch (e) {
            return '<span style="font-size:8px;color:#999">' + esc(text) + '</span>';
        }
    }

    // ── paper builder ─────────────────────────────────────────────────────────

    function buildPaper(data, paper) {
        var plate      = data.plate    || '';
        var gid        = data.glabs_id || '';
        var items      = data.awb_items || [];
        var date       = nowStr();
        var barcodeSVG = makeBarcodeSVG(gid);

        var rowsHTML = items.map(function (item, i) {
            return buildRowHTML(item, i + 1, plate);
        }).join('');

        paper.innerHTML =
            '<div class="ppt-inner">' +
            '<div class="ppt-doc-header">' +
            '<div class="ppt-barcode">' + barcodeSVG + '</div>' +
            '<div class="ppt-header-right">' +
            '<div class="ppt-company">HGL Group Hungary &middot; Ecommerce Operations</div>' +
            '<div class="ppt-title">RAKODÁSI TERV</div>' +
            '</div>' +
            '</div>' +
            '<div class="ppt-meta">' +
            '<div class="ppt-meta-box"><span class="pml">GLABS ID</span><span class="pmv">' + esc(gid) + '</span></div>' +
            '<div class="ppt-meta-box"><span class="pml">Rendszám</span><span class="pmv pmv-plate">' + esc(plate) + '</span></div>' +
            '<div class="ppt-meta-box ppt-ramp-box"><span class="pml">RAMP</span>' +
            '<span class="pmv pmv-ramp" contenteditable="true" data-empty-hint="pl. G12"></span></div>' +
            '<div class="ppt-meta-box"><span class="pml">Dátum</span><span class="pmv">' + date + '</span></div>' +
            '</div>' +
            '<table class="ppt-table">' +
            '<thead><tr>' +
            '<th class="ppt-num">#</th>' +
            '<th>LMP</th><th>AWB</th><th>Rendszám</th>' +
            '<th class="ppt-num">Colli</th><th class="ppt-num">PLT</th>' +
            '<th class="ppt-num ppt-korr-th">Korr.</th>' +
            '<th>Tárhely</th><th>Megjegyzés</th>' +
            '<th class="no-print ppt-del-th"></th>' +
            '</tr></thead>' +
            '<tbody id="ppt-tbody">' + rowsHTML + '</tbody>' +
            '<tfoot><tr>' +
            '<td colspan="4" class="sum-label">ÖSSZESEN (' + items.length + ' tétel)</td>' +
            '<td class="ppt-num sum-colli">—</td>' +
            '<td class="ppt-num sum-plt">—</td>' +
            '<td class="ppt-korr"></td>' +
            '<td></td><td></td>' +
            '<td class="no-print"></td>' +
            '</tr></tfoot>' +
            '</table>' +
            '</div>';

        updateSummary(paper);
        attachTableHandlers(paper);
    }

    // ── iframe print ─────────────────────────────────────────────────────────
    // Writes a self-contained HTML doc into a hidden iframe and prints it.
    // Avoids all beforeprint/afterprint DOM manipulation issues.

    var IFRAME_CSS = [
        '* { box-sizing: border-box; margin: 0; padding: 0; }',
        'body { font-family: "Segoe UI", Arial, sans-serif; font-size: 9px; color: #000; background: #fff; }',
        '@page { size: A4 landscape; margin: 10mm 12mm; }',
        '.no-print { display: none !important; }',

        /* ── doc header ── */
        '.ppt-inner { padding: 3mm 5mm; }',
        '.ppt-doc-header { display: flex; align-items: flex-end; gap: 12px;',
        '  border-bottom: 3px solid #000; padding-bottom: 5px; margin-bottom: 7px; }',
        '.ppt-barcode { flex-shrink: 0; }',
        '.ppt-barcode svg { display: block; }',
        '.ppt-header-right { flex: 1; display: flex; flex-direction: column; align-items: flex-end; }',
        '.ppt-company { font-size: 8px; color: #333; letter-spacing: .5px; text-transform: uppercase; }',
        '.ppt-title { font-size: 20px; font-weight: 900; letter-spacing: 2px; color: #000; }',

        /* ── meta boxes ── */
        '.ppt-meta { display: grid; grid-template-columns: repeat(4,1fr); gap: 6px; margin-bottom: 7px; }',
        '.ppt-meta-box { border: 2px solid #000; border-radius: 3px; padding: 4px 8px; }',
        '.pml { display: block; font-size: 7px; color: #444; text-transform: uppercase;',
        '  letter-spacing: .6px; margin-bottom: 1px; font-weight: 600; }',
        '.pmv { display: block; font-size: 14px; font-weight: 900; color: #000; min-height: 16px; }',
        '.pmv-ramp { font-size: 18px; text-decoration: underline; }',

        /* ── table ── */
        '.ppt-table { width: 100%; border-collapse: collapse; }',
        '.ppt-table th { background: #000; color: #fff; padding: 4px 5px; text-align: left;',
        '  font-size: 8.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .4px;',
        '  border: 1px solid #000; }',
        '.ppt-table td { padding: 4px 5px; border: 1px solid #888; font-size: 9px; vertical-align: middle; }',
        '.ppt-table tr:nth-child(even) td { background: #f0f0f0; }',
        /* pr-ready: thick left border so it's visible in B&W */
        '.ppt-table tr.pr-ready td { border-top: 1.5px solid #000; border-bottom: 1.5px solid #000; }',
        '.ppt-table tr.pr-ready td:first-child { border-left: 4px solid #000; }',
        '.ppt-table tfoot td { font-weight: 800; background: #e0e0e0; border-top: 2.5px solid #000;',
        '  font-size: 10px; }',

        /* ── col widths ── */
        '.ppt-num  { text-align: center; width: 28px; }',
        '.ppt-lmp  { font-weight: 700; min-width: 68px; }',
        '.ppt-awb  { font-family: monospace; font-size: 8.5px; min-width: 88px; }',
        '.ppt-plate{ font-weight: 700; min-width: 58px; }',
        '.ppt-korr { background: #e8e8e8 !important; min-width: 48px;',
        '  border: 2px dashed #555 !important; }',
        '.ppt-korr-th { width: 52px; }',
        '.ppt-loc  { min-width: 55px; }',
        '.sum-colli,.sum-plt { text-align: center; font-size: 11px; font-weight: 900; }',
        '.sum-label { text-align: right; font-size: 9px; padding-right: 10px; font-weight: 700; }',
    ].join('\n');

    function doPrint() {
        var inner = document.querySelector('#print-preview-paper .ppt-inner');
        if (!inner) return;

        // Collect current text content from editable cells (innerHTML has contenteditable attr,
        // but the text is already rendered in the DOM so outerHTML captures it correctly)
        var html = inner.outerHTML;

        var iframe = document.createElement('iframe');
        iframe.setAttribute('aria-hidden', 'true');
        iframe.style.cssText = [
            'position:fixed',
            'top:-10000px',
            'left:-10000px',
            'width:297mm',
            'height:210mm',
            'border:none',
            'opacity:0',
            'pointer-events:none',
        ].join(';');
        document.body.appendChild(iframe);

        var doc = iframe.contentWindow.document;
        doc.open();
        doc.write('<!DOCTYPE html><html><head><meta charset="utf-8">');
        doc.write('<style>' + IFRAME_CSS + '</style>');
        doc.write('</head><body>');
        doc.write(html);
        doc.write('</body></html>');
        doc.close();

        // Allow layout to settle before printing
        setTimeout(function () {
            try {
                iframe.contentWindow.focus();
                iframe.contentWindow.print();
            } catch (err) {
                console.warn('iframe print failed, falling back to window.print()', err);
                window.print();
            }
            setTimeout(function () {
                if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
            }, 2000);
        }, 250);
    }

    // ── main entry point ──────────────────────────────────────────────────────

    window.flowPrintPreview = function (data) {
        var overlay = document.getElementById('print-preview-overlay');
        if (!overlay) return;

        if (!data || !data.plate) {
            overlay.style.display = 'none';
            return;
        }

        var infoEl = document.getElementById('ppt-info-text');
        if (infoEl) infoEl.textContent = (data.glabs_id || '') + '  ·  ' + (data.plate || '');

        var paper = document.getElementById('print-preview-paper');
        if (paper) buildPaper(data, paper);

        overlay.style.display = 'flex';

        setTimeout(function () {
            var ramp = overlay.querySelector('.pmv-ramp');
            if (ramp) ramp.focus();
        }, 60);
    };

    // ── toolbar clicks ────────────────────────────────────────────────────────

    document.addEventListener('click', function (e) {
        if (e.target.closest('#ppt-print')) {
            doPrint();
            return;
        }

        if (e.target.closest('#ppt-add-row')) {
            var tbody = document.querySelector('#ppt-tbody');
            if (!tbody) return;
            var nextIdx = tbody.querySelectorAll('tr').length + 1;
            var plateCell = tbody.querySelector('.ppt-plate');
            var plate = plateCell ? plateCell.textContent.trim() : '';
            var newRow = document.createElement('tr');
            newRow.dataset.idx = nextIdx;
            newRow.innerHTML =
                '<td class="ppt-num">' + nextIdx + '</td>' +
                '<td class="ppt-lmp" contenteditable="true"></td>' +
                '<td class="ppt-awb" contenteditable="true"></td>' +
                '<td class="ppt-plate" contenteditable="true">' + esc(plate) + '</td>' +
                '<td class="ppt-num ppt-ce" contenteditable="true"></td>' +
                '<td class="ppt-num ppt-ce" contenteditable="true"></td>' +
                '<td class="ppt-num ppt-korr" contenteditable="true"></td>' +
                '<td class="ppt-loc" contenteditable="true"></td>' +
                '<td class="ppt-note" contenteditable="true"></td>' +
                '<td class="ppt-del no-print"><button class="ppt-del-btn" title="Törlés">&times;</button></td>';
            tbody.appendChild(newRow);
            var paper = document.getElementById('print-preview-paper');
            if (paper) updateSummary(paper);
            var first = newRow.querySelector('[contenteditable]');
            if (first) first.focus();
        }
    });

    // ── keyboard ──────────────────────────────────────────────────────────────

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            var closeBtn = document.getElementById('ppt-close');
            if (closeBtn) closeBtn.click();
        }
        if (e.key === 'Enter' && document.activeElement &&
                document.activeElement.hasAttribute('contenteditable')) {
            var row = document.activeElement.closest('tr');
            if (!row) return;
            e.preventDefault();
            var editables = Array.from(row.querySelectorAll('[contenteditable]'));
            var i = editables.indexOf(document.activeElement);
            if (i >= 0 && i < editables.length - 1) editables[i + 1].focus();
        }
    }, true);

    // ── RAMP placeholder ──────────────────────────────────────────────────────

    document.addEventListener('focusin', function (e) {
        if (e.target.classList && e.target.classList.contains('pmv-ramp') &&
                e.target.textContent.trim() === '') {
            e.target.dataset.showHint = 'true';
        }
    });
    document.addEventListener('focusout', function (e) {
        if (e.target.classList && e.target.classList.contains('pmv-ramp')) {
            delete e.target.dataset.showHint;
        }
    });

})();
