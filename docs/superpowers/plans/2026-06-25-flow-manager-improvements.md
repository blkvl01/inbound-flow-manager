# Flow Manager – Javítások és Optimalizálás

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 12 célzott javítás – dead code, bugfix, teljesítmény, UX, biztonság – a Flow Manager portfólióban.

**Architecture:** Minden feladat izolált, egymástól független. A Python-oldali változások szintaxis-ellenőrzéssel (ast.parse) validálandók; a JS-változások Node --check-kel. Nincs git repo, ezért commit lépés helyett ellenőrző parancs zárja a taskokat.

**Tech Stack:** Python 3.14, Dash/Flask, pandas, pyxlsb, openpyxl; vanilla JS (no bundler); CSS (no preprocessor)

## Global Constraints

- NINCS emoji sehol (SVG mask-image inline ikonok)
- NINCS color-mix() a CSS-ben (renderer nem támogatja)
- NINCS folyamatos idle animáció
- Nincs git repo – commit lépések helyett ast.parse / node --check ellenőrzés
- style.css ~26k sor duplikált szelektorokkal – szerkesztésnél a LEGUTOLSÓ (legnagyobb sorszámú) definíció nyeri azonos specificitásnál
- A Dash callback-ek csak az app.py-ban lehetnek (suppress_callback_exceptions=True, de az összes ID ott van definiálva)

---

## Task 1: Dead code törlése – _time_band_inbound

**Files:**
- Modify: `data_reader.py` (sorok a `_time_band_inbound` függvényben, az early return után)

**Interfaces:**
- Consumes: semmi (standalone törlés)
- Produces: semmi (csak kód eltávolítás)

- [ ] **Step 1: Azonosítsd a dead code-ot**

`data_reader.py`-ban a `_time_band_inbound` függvény:
```python
def _time_band_inbound(am_f, w, now_dt: datetime, epoch: datetime,
                       boxes=None, parcels=None, day_offset: int = 0) -> list[dict]:
    return _time_band_buckets(am_f, w, now_dt, epoch, boxes, parcels, day_offset)
    """Inbound kg/db per time band..."""   # ← dead, soha nem fut
    day0 = now_dt.replace(...)             # ← dead
    ...                                    # ← 25+ sor dead code
```

- [ ] **Step 2: Töröld az early return utáni összes sort a függvényen belül**

A függvény maradjon:
```python
def _time_band_inbound(am_f, w, now_dt: datetime, epoch: datetime,
                       boxes=None, parcels=None, day_offset: int = 0) -> list[dict]:
    return _time_band_buckets(am_f, w, now_dt, epoch, boxes, parcels, day_offset)
```

- [ ] **Step 3: Szintaxis-ellenőrzés**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('data_reader.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
```
Elvárt: `ast_ok`

---

## Task 2: ensurePlateFocusPill orphaned pill bug

**Files:**
- Modify: `assets/card_fx.js`

**Interfaces:**
- Consumes: `plateFocusPill` modul-szintű változó
- Produces: `ensurePlateFocusPill()` – mindig DOM-ban lévő, valid elemet ad vissza

- [ ] **Step 1: Keresd meg a bugos sort**

`card_fx.js`-ben:
```javascript
function ensurePlateFocusPill() {
    if (plateFocusPill) return plateFocusPill;  // ← BUG: orphaned lehet
```

- [ ] **Step 2: Javítsd a contains-check-kel**

```javascript
function ensurePlateFocusPill() {
    if (plateFocusPill && document.body.contains(plateFocusPill)) return plateFocusPill;
```

- [ ] **Step 3: JS szintaxis-ellenőrzés**

```powershell
node --check assets/card_fx.js && Write-Host "js_ok"
```
Elvárt: `js_ok`

---

## Task 3: FLIP ablak pendingUntil race condition fix

**Files:**
- Modify: `assets/card_fx.js`

**Interfaces:**
- Consumes: `FLIP_MS` konstans (340), `pendingUntil` változó
- Produces: helyes időablak a FLIP animáció végéhez igazítva

- [ ] **Step 1: Keresd meg a bugos sort a `runFlip` végén**

```javascript
if (animated) pendingUntil = performance.now() + 120;
```

- [ ] **Step 2: Cseréld FLIP_MS + 80-ra**

```javascript
if (animated) pendingUntil = performance.now() + FLIP_MS + 80;
```

- [ ] **Step 3: JS szintaxis-ellenőrzés**

```powershell
node --check assets/card_fx.js && Write-Host "js_ok"
```

---

## Task 4: Print gomb emoji → SVG ikon

**Files:**
- Modify: `app.py` (`_generate_print_html` függvény)

**Interfaces:**
- Consumes: `_generate_print_html` return string
- Produces: printer SVG ikon a `&#128438;` helyett

- [ ] **Step 1: Keresd meg az emoji-t**

`app.py`-ban:
```python
'    <button class="print-btn" onclick="window.print()">&#128438; Nyomtatás</button>\n'
```

- [ ] **Step 2: Cseréld inline SVG-re**

```python
'    <button class="print-btn" onclick="window.print()">'
'<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
'style="vertical-align:-2px;margin-right:5px" aria-hidden="true">'
'<polyline points="6 9 6 2 18 2 18 9"/>'
'<path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>'
'<rect x="6" y="14" width="12" height="8"/>'
'</svg>Nyomtatás</button>\n'
```

- [ ] **Step 3: Szintaxis-ellenőrzés**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
```

---

## Task 5: _generate_print_html HTML escape

**Files:**
- Modify: `app.py` (`_generate_print_html` és `_make_rows_html` belső logika)

**Interfaces:**
- Consumes: `awb_items` lista dict-ek: `awb`, `lmp`, `location`, `load_note`, `is_issued`, `is_ready`, `boxes`, `pallets_issued`
- Produces: HTML-safe string minden dinamikus értékre

- [ ] **Step 1: Importold az html modult (ha még nincs)**

`app.py` tetején importok között:
```python
import html as _html
```

- [ ] **Step 2: Wrap minden dinamikus értéket `_html.escape()`-pel a rows_html generálásban**

Az f-string soroknál:
```python
awb_e      = _html.escape(str(awb))
lmp_e      = _html.escape(str(lmp))
location_e = _html.escape(str(location))
note_e     = _html.escape(str(note))
plate_e    = _html.escape(str(plate))
rows_html += (
    f'<tr class="{row_cls}">'
    f'<td class="td-num">{i}</td>'
    f'<td class="td-lmp">{lmp_e}</td>'
    f'<td class="td-awb">{awb_e}</td>'
    f'<td class="td-plate">{plate_e}</td>'
    f'<td class="td-num">{colli_disp}</td>'
    f'<td class="td-num">{plt_disp}</td>'
    f'<td class="td-korr"></td>'
    f'<td class="td-loc">{location_e}</td>'
    f'<td class="td-note">{note_e}</td>'
    f'</tr>\n'
)
```

- [ ] **Step 3: A fejléc meta értékeit is escape-eld**

```python
glabs_id_e = _html.escape(str(glabs_id))
plate_e    = _html.escape(str(plate))
ramp_e     = _html.escape(str(ramp or "—"))
```
És a meta-grid blokkban ezeket használd.

- [ ] **Step 4: Szintaxis-ellenőrzés**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
```

---

## Task 6: _shared_state .tmp fájlok cleanup induláskor

**Files:**
- Modify: `data_cache.py` (`start()` függvény elején)

**Interfaces:**
- Consumes: `storage_manager.get_storage_path()` → `_shared_state/` mappa elérési útja
- Produces: 1+ óránál régebbi `*.tmp` fájlok törlése induláskor

- [ ] **Step 1: Írd meg a cleanup függvényt `data_cache.py`-ban**

A `start()` függvény ELÉ:
```python
def _cleanup_stale_tmp_files():
    """Remove leftover *.tmp files from _shared_state/ older than 1 hour.
    These accumulate when atomic writes are interrupted (crash, race condition).
    """
    try:
        shared_dir = storage_manager.get_storage_path().parent
        cutoff = time.time() - 3600
        for p in shared_dir.glob("*.tmp"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    log.info("Cleaned up stale tmp: %s", p.name)
            except OSError:
                pass
    except Exception as exc:
        log.debug("Tmp cleanup skipped: %s", exc)
```

- [ ] **Step 2: Hívd meg a `start()` legelején**

```python
def start():
    _cleanup_stale_tmp_files()
    cache_loaded = _load_dashboard_cache()
    ...
```

- [ ] **Step 3: Szintaxis-ellenőrzés**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('data_cache.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
```

---

## Task 7: plate-focus-pill keyboard focus ring CSS

**Files:**
- Modify: `assets/style.css` (a `.plate-focus-pill` blokkhoz hozzáfűzve)

**Interfaces:**
- Consumes: `.plate-focus-pill` CSS osztály (létező)
- Produces: látható `:focus-visible` ring Tab-navigációnál

- [ ] **Step 1: Keresd meg a `.plate-focus-pill` utolsó előfordulását a style.css-ben**

```powershell
Select-String -Path "assets\style.css" -Pattern "\.plate-focus-pill" | Select-Object -Last 5
```

- [ ] **Step 2: A meglévő `.plate-focus-pill` blokk UTÁN add hozzá**

```css
.plate-focus-pill:focus-visible {
  outline: 2px solid var(--accent, #ff8a3d);
  outline-offset: 2px;
}
```

- [ ] **Step 3: CSS kapocs-ellenőrzés**

```powershell
python -c "from pathlib import Path; s=Path('assets/style.css').read_text(encoding='utf-8-sig'); print(s.count('{'), s.count('}'))"
```
Elvárt: mindkét szám egyforma.

---

## Task 8: MutationObserver debounce – applyPlateVisualState

**Files:**
- Modify: `assets/card_fx.js`

**Interfaces:**
- Consumes: `applyPlateVisualState()` (meglévő)
- Produces: RAF-alapú debounce, így Dash re-render közben legfeljebb 1× fut le per frame

- [ ] **Step 1: Adj hozzá egy debounce wrapper változót a modul tetejére (a többi `var` mellé)**

```javascript
var _plateStateRafPending = false;
function _debouncedPlateState() {
  if (_plateStateRafPending) return;
  _plateStateRafPending = true;
  requestAnimationFrame(function () {
    _plateStateRafPending = false;
    applyPlateVisualState();
  });
}
```

- [ ] **Step 2: Cseréld le a 3 MutationObserver callback-et**

```javascript
// ELŐTTE:
var observer = new MutationObserver(applyPlateVisualState);
// ...
var legendObserver = new MutationObserver(applyPlateVisualState);
// ...
var truckObserver = new MutationObserver(applyPlateVisualState);

// UTÁNA (mindhárom helyen):
var observer = new MutationObserver(_debouncedPlateState);
// ...
var legendObserver = new MutationObserver(_debouncedPlateState);
// ...
var truckObserver = new MutationObserver(_debouncedPlateState);
```

- [ ] **Step 3: JS szintaxis-ellenőrzés**

```powershell
node --check assets/card_fx.js && Write-Host "js_ok"
```

---

## Task 9: Tooltip hideTip scroll közben

**Files:**
- Modify: `assets/card_fx.js`

**Interfaces:**
- Consumes: `hideTip()` (meglévő), `initCardTooltips()` (meglévő)
- Produces: scroll event elrejti a tooltipet, hogy ne "lógjon" régi pozícióban

- [ ] **Step 1: Az `initCardTooltips` végén add hozzá a scroll listenert**

```javascript
function initCardTooltips() {
    var area = document.getElementById("cards-area");
    if (!area) { setTimeout(initCardTooltips, 150); return; }

    area.addEventListener("mouseenter", function (e) { ... }, true);
    area.addEventListener("mouseleave", function (e) { ... }, true);
    area.addEventListener("mousemove", function (e) { ... });

    // ÚJ: scroll közben rejtsük el a tooltipet
    window.addEventListener("scroll", function () { hideTip(); }, { passive: true });
}
```

- [ ] **Step 2: JS szintaxis-ellenőrzés**

```powershell
node --check assets/card_fx.js && Write-Host "js_ok"
```

---

## Task 10: iterrows() → vectorized – _apply_stored_state extras

**Files:**
- Modify: `data_cache.py` (`_apply_stored_state` függvény)

**Interfaces:**
- Consumes: `stored_extra` DataFrame (glabs_id, awb, lmp, bud_lmp, bud_is_shippable oszlopok)
- Produces: ugyanaz a `extras` lista dict-ekből, de `iterrows()` nélkül

- [ ] **Step 1: Keresd meg az iterrows-t**

```python
for _, row in stored_extra[stored_extra["glabs_id"] == glabs_id].iterrows():
    extras.append({
        "lmp": str(row.get("lmp") or row.get("bud_lmp") or ""),
        "awb": str(row.get("awb") or ""),
        "status": "Betárolva",
        "is_ready": True,
        "is_manual": True,
    })
```

- [ ] **Step 2: Cseréld vectorized dict-list építésre**

```python
subset = stored_extra[stored_extra["glabs_id"] == glabs_id]
lmp_col = subset["lmp"].fillna("").astype(str)
bud_lmp_col = subset.get("bud_lmp", pd.Series("", index=subset.index)).fillna("").astype(str)
awb_col = subset["awb"].fillna("").astype(str)
lmp_resolved = lmp_col.where(lmp_col != "", bud_lmp_col)
extras = [
    {"lmp": lmp, "awb": awb, "status": "Betárolva", "is_ready": True, "is_manual": True}
    for lmp, awb in zip(lmp_resolved.tolist(), awb_col.tolist())
]
```

- [ ] **Step 3: Szintaxis-ellenőrzés**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('data_cache.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
```

---

## Task 11: apply_priorities flag-ek vektorizálása

**Files:**
- Modify: `priority_engine.py` (`apply_priorities` függvény)

**Interfaces:**
- Consumes: `df` DataFrame, `priority_lane_text` (str oszlop), `_AT_HU/_TEMU/_TEMU_MD/_MEEST_MD/_FOUR_PX` regex-ek
- Produces: `is_at_hu`, `is_temu`, `is_md_lane` – vektorizált bool Series-ek

- [ ] **Step 1: Cseréld az apply-alapú flag számítást vektorizált pandas str.contains-re**

Az `apply_priorities`-ban:
```python
# ELŐTTE:
df["priority_lane_text"] = df.apply(_lane_blob, axis=1)
df["is_at_hu"]   = df["priority_lane_text"].apply(_is_at_hu_lane)
df["is_temu"]    = df["priority_lane_text"].apply(_is_temu_general)
df["is_md_lane"] = df["priority_lane_text"].apply(_is_md_lane)

# UTÁNA:
df["priority_lane_text"] = df.apply(_lane_blob, axis=1)
_lane = df["priority_lane_text"].str.upper().str.replace(r"[^A-Z0-9]+", " ", regex=True)
df["is_at_hu"]   = _lane.str.contains(r"(?:^| )(?:AT|HU)(?: |$)", regex=True, na=False)
df["is_temu"]    = (
    _lane.str.contains(r"(?:^| )TEMU(?: |$)", regex=True, na=False)
    & ~_lane.str.contains(r"(?:^| )TEMU MD(?: |$)", regex=True, na=False)
)
df["is_md_lane"] = (
    _lane.str.contains(r"(?:^| )MEEST MD(?: |$)", regex=True, na=False)
    | _lane.str.contains(r"(?:^| )4PX(?: |$)", regex=True, na=False)
    | _lane.str.contains(r"(?:^| )TEMU MD(?: |$)", regex=True, na=False)
)
```

- [ ] **Step 2: is_at_hu_priority és lmp_priority_tier marad apply(axis=1)**

Ezek a vektorizált `is_at_hu`-t olvassák, tehát sorrend számít:
```python
df["is_at_hu_priority"] = df["is_at_hu"]  # egyszerű alias
df["lmp_priority_tier"] = df.apply(_lmp_tiebreak, axis=1)
```

- [ ] **Step 3: hours_since_am és is_en_route részleges vektorizálása**

```python
_now = datetime.now()
am_times = pd.to_datetime(df["am_time"], errors="coerce")
df["hours_since_am"] = (_now - am_times).dt.total_seconds().div(3600).fillna(0.0).clip(lower=0)
# is_aged: hours >= 4 és nem en-route (en-route az apply-ban marad, mert _arrival_time komplex)
df["truck_arrival_time"] = df.apply(_arrival_time, axis=1)
df["is_en_route"] = df.apply(_is_en_route, axis=1)
df["is_aged"] = (df["hours_since_am"] >= _AGING_HOURS) & ~df["is_en_route"]
```

- [ ] **Step 4: Szintaxis-ellenőrzés + gyors import teszt**

```powershell
python -c "import ast, pathlib; ast.parse(pathlib.Path('priority_engine.py').read_text(encoding='utf-8-sig')); print('ast_ok')"
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "from priority_engine import apply_priorities; import pandas as pd; print('import_ok')"
```

---

## Task 12: header_condense.js init – MutationObserver alapú

**Files:**
- Modify: `assets/header_condense.js`

**Interfaces:**
- Consumes: `.app-header` DOM elem, `ensureTopBtn()`, `remeasure()`, `requestUpdate()`
- Produces: MutationObserver alapú init ami azonnal triggel, és nem pollingol 20mp-ig

- [ ] **Step 1: Cseréld le a poll loopot**

```javascript
// ELŐTTE (eltávolítandó):
var tries = 0;
(function init() {
    var el = headerEl();
    if (!el) {
        if (++tries < 80) setTimeout(init, 250);
        return;
    }
    ensureTopBtn();
    remeasure();
    if (window.ResizeObserver) {
        new ResizeObserver(function () { if (!mini) remeasure(); }).observe(el);
    }
    requestUpdate();
})();

// UTÁNA:
(function init() {
    var el = headerEl();
    if (el) {
        ensureTopBtn();
        remeasure();
        if (window.ResizeObserver) {
            new ResizeObserver(function () { if (!mini) remeasure(); }).observe(el);
        }
        requestUpdate();
        return;
    }
    // Dash rendereli a layout-ot async — MutationObserver-rel várjuk
    var root = document.getElementById('app-root') || document.body;
    var initObs = new MutationObserver(function (_, obs) {
        var found = headerEl();
        if (!found) return;
        obs.disconnect();
        ensureTopBtn();
        remeasure();
        if (window.ResizeObserver) {
            new ResizeObserver(function () { if (!mini) remeasure(); }).observe(found);
        }
        requestUpdate();
    });
    initObs.observe(root, { childList: true, subtree: true });
})();
```

- [ ] **Step 2: JS szintaxis-ellenőrzés**

```powershell
node --check assets/header_condense.js && Write-Host "js_ok"
```

---

## Ellenőrző parancsok (minden task után)

**Python szintaxis (összes fájl egyszerre):**
```powershell
python -c "import ast, pathlib; files=['app.py','data_reader.py','data_cache.py','priority_engine.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast_ok')"
```

**App import:**
```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "import app; print('app_import_ok')"
```

**CSS kapocs:**
```powershell
python -c "from pathlib import Path; s=Path('assets/style.css').read_text(encoding='utf-8-sig'); o=s.count('{'); c=s.count('}'); print(f'open={o} close={c} match={o==c}')"
```
