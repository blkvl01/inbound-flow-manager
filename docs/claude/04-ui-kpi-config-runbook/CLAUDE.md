# UI, KPI, Config and Runbook

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

Source: root CLAUDE.md lines 540-803 before split.

## UI és performance döntések

Fájl: `assets/style.css`

Fő elvek:

- Flow Manager saját, erős vizuális nyelv.
- Inbound/outbound ugyanazt a kártyarendszert használja.
- Liquid/glass hatás csak fő felületeken; nested outbound/inbound listákon nincs drága `backdrop-filter`.
- A háttér és orbs nem animálnak folyamatosan; gradient csak flow váltáskor mozdul.
- Kártyák `content-visibility: auto` és `contain` optimalizálást kapnak.
- Switch/filter store clientside, kattintásra azonnali UI állapot.
- `prefers-reduced-motion` támogatott.

Ikon-rendszer (NINCS emoji sehol — user szabály):

- Minden új ikon inline SVG, `:root`-ban `--icon-*` változóként (`clock`, `comment`),
  `mask-image`-ként használva, `background-color: currentColor`-ral → a szöveg színét
  örökli. A renderer támogatja a `-webkit-mask`-ot.
- IKON CSAK A NAGYON SZÜKSÉGES HELYEN (user kérés: ne legyen túl sok). Jelenleg:
  `badge-note` (chat-buborék + szám) és `badge-aged` (óra). Kártya-meta sor (kg/colli/
  GLABS/sofőr) ikonjai KISZEDVE — túl zsúfolt volt.
- `badge-aged`: csak óra-ikon (nincs szöveg!), hogy SOHA ne növelje a header magasságát;
  `.priority-card .card-strip { min-height: 38px }` garantálja az egységes fejlécmagasságot.
- A meglévő dingbat/glyph gombok (`⚙ 🖨 ✓ ✕ ✎ ⚠`) szándékosan maradtak (user döntés).

Extrás glass (clean, de prémium — CSS-only, renderer-biztos, STATIKUS):

- Erősebb él-fénytörés: mély bevel (inset box-shadow), szélesebb/fényesebb rim
  (`::before` mask-ráma), top frost film + halvány szín-illesztett ambient halo
  (`--glow-ultra`). Glassier hatás a frost + élek + `backdrop-filter: blur(18px)`-ből.
- NINCS folyamatos idle animáció (user: scrollra/morph-ra leesett 10-15 FPS-re).
  Csak interakció/render: hover glow, GLABS `glabsFillIn` egyszer renderkor,
  `badge-aged` halvány opacity-pulse. A numerátor `data-countup` →
  `assets/number_flux.js` rAF tween (csak adatváltáskor fut).
- A morph kill-switch (`html.hdr-morphing`) a `badge-aged`/`glabs-progress-fill`
  animációt is felfüggeszti, a `::before/::after`-t elrejti — semmi se repaintel
  a header morph alatt.
- TILOS: `color-mix()` (renderer ledobja) és per-kártya SVG `backdrop-filter`
  displacement (`.lg-l1`/`#flow-glass-distort` dormant — korábban fekete téglalapként
  renderelt, lásd memória; csak a célgépen, screenshottal igazolva engedélyezhető).

Úton (en-route) tétel: NINCS Betárolva gomb (`store_el = html.Span()`), mert még
meg sem érkezett — nem tárolható be.

Összegzés modal (`_build_summary_table`): TOP-10 tábla, igényesebb glass dialog
(`backdrop-filter`), oszlopok: #, Prioritás, AWB, LMP, Típus, kg, Felvéve.
Soronként bal oldali szín-akcentus (`--row-accent`), rank-badge, prioritás-pill,
aged órajel. A `summary_copy.js` a `textContent`-et másolja → az ikon-spanok
(szöveg nélkül) NEM rondítják a másolt táblát; üres filler `td`-t ne tegyél bele.

- CSAPDA: SOHA ne tegyél `display:flex`-et közvetlenül egy `<td>`-re — szétveri a
  table cellamodellt ("szétcsúszott" bug). A flex tartalom menjen belső `div`-be
  (`.summary-prio-inner`).
- A megnyitás KLIENSOLDALI (`app.clientside_callback` → overlay style), a body-t a
  külön `fill_summary` szerver-callback tölti → a gomb azonnal reagál (a régi
  összevont callback miatt tűnhetett lassúnak; üres tábla = még folyó induló Excel read).
- A `.summary-content` görgethető (`overflow-y:auto` + `max-height`), különben magas
  tábla levágódott ("csak egy tételt látok").

Kártya finomítások (2. kör):

- Kategória-akcentus: a `::before` rim a TELJES keret körül megy, halvány
  prioritás-színű gradiens (`--glow`/`--glow-far`), hoveren erősebb/világosabb.
- Szűrőváltás animáció (`card_fx.js applyFlowFilter`): a megmaradó kártyák FLIP-pel
  csúsznak, az újonnan megjelenők `card-filter-in` pop-pal jönnek be.
- Betárolva: zöld pipa-"stamp" flash (`card-store-flash`) + gomb press-feedback.
- KPI count-up: a `#kpi-muszak-value` és `#kpi-bec-total` (`.kpi-countup`) érték
  0-ról felszámol értékváltáskor (IN/OUT váltás vagy refresh), max ~1.2s —
  `assets/kpi_countup.js`, tisztán kliensoldali (a 2s poll nem indítja újra, ha
  az érték nem változott).

Fontos komponensek:

```text
.priority-card
.outbound-card
.outbound-glabs-block
.outbound-awb-row
.inbound-ready-block
.driver-row
.no-driver
.rest-warning
.flow-switch
```

Driver státusz:

- `Bejelentkezve`, `Sofőr nincs bejelentkezve`, `Nem szerepel rakodásban`, `Pihenőn` mini-kártya jellegű blokk.
- Nem sima szöveg: bal oldali jelző, visszafogott háttér, hover, light mode.

---

## Stat gombok

Közös stat gomb ID-k:

```text
stat-btn-all
stat-btn-athu
stat-btn-drv
stat-btn-sched
stat-btn-ship
stat-btn-betarolt
```

Megjegyzés: a régi TEMU gomb definíció technikailag még `_STAT_DEFS` elején szerepel, de azonnal kiszűrésre kerül:

```python
_STAT_DEFS = [d for d in _STAT_DEFS if d[1] != "temu"]
```

Inbound címkék:

```text
Aktív tétel
AT/HU
Sofőr helyszínen
Nincs itt / pihenőn
Kiadható
Betárolt tételek
```

Outbound címkék:

```text
Összes
Sofőr itt
Kiadásra vár
Pihenőn
Várakozik
Lezárt
```

---

## KPI logika

`data_reader.py::_compute_kpi()`

Header KPI-k:

1. **Műszak kg**: aktuális nappali/éjszakai műszakban felvett súly.
2. **Beérkezhető kg**: `Felvéve`, `Értesítő`, `Megérkezett` státuszú tételek, ahol AL legfeljebb `now + 4h`.

Kivétel:

```text
Megérkezett + carrier_k == "AS Cargo" + AL > mai 14:00 -> kizárva
```

A beérkezhető KPI kattintható/pinnelhető panelt nyit Felvéve/Értesítő/Megérkezett bontással.

A headerben a Beérkezhető KPI mellett kompakt composition diagram is van ugyanebből a bontásból:

```text
Felvéve / Értesítő / Megérkezett kg
```

A sáv 100%-os összetételként mutatja, hogyan adódik össze a teljes beérkező kg a három státuszból. A kis értékek kapnak minimális látható szélességet, a 0 kg-os státuszok nem kapnak látható sávfeliratot, a tooltipben pontos kg és százalék jelenik meg.

---

## Config

Per-user config:

```text
%LOCALAPPDATA%/InboundFlowManager/<USERNAME>/config.json
```

Struktúra:

```json
{
  "ecomm_file": "...E_COMM nyomonkövetés_24.xlsb",
  "pallets_file": "...BUD-Pallets.xlsm",
  "refresh_interval_minutes": 10,
  "port": 8501
}
```

Ha hiányzik valamelyik fájl, `tkinter` file picker indul.

Default OneDrive folder keresés:

```text
Ecommerce - Dokumentumok
Ecommerce - Documents
```

---

## Futtatás

Dev:

```powershell
cd "C:\Inbound Flow Manager"
python app.py
```

URL:

```text
http://127.0.0.1:8501
```

Build:

```powershell
cd "C:\Inbound Flow Manager"
build.bat
```

Kimenet:

```text
dist/FlowManager/FlowManager.exe
```

Deploy közös mappába:

- Ez `--onedir` PyInstaller build, nem egyetlen önálló exe.
- A buildben az assets mappa helye: `dist/FlowManager/_internal/assets/`.
- Ha Python-kód változott (`app.py`, `data_reader.py`, `priority_engine.py`, stb.),
  a teljes `dist/FlowManager/` mappa tartalmát kell kicserélni a közös mappában
  (`FlowManager.exe` + `_internal/` + minden mellékelt adat). Csak az exe cseréje
  nem elég, mert a dependency/runtime fájlok a mappa részei.
- Ha kizárólag `assets/` változott és biztosan ugyanaz a build marad, elég lehet
  a közös mappában a `_internal/assets/` cseréje. Biztonságos release-hez viszont
  mindig a teljes `dist/FlowManager/` tartalmat cseréld.
- A közös `_shared_state/` állapotfájlokat nem a build outputból kell felülírni.

---

## Ellenőrzési parancsok

Syntax:

```powershell
python -c "import ast, pathlib; files=['app.py','data_reader.py','data_cache.py','priority_engine.py','storage_manager.py','config.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8-sig'), filename=f) for f in files]; print('ast_ok')"
```

App import:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "import app; print('app_import_ok')"
```

Adatbetöltés:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; python -c "from data_reader import load_all_flow_data; df, errors, kpi, outbound_cards, outbound_errors, issued_awbs, uld_data = load_all_flow_data(); print(len(df), errors, len(outbound_cards), outbound_errors, len(issued_awbs), len(uld_data))"
```

CSS kapcsok:

```powershell
python -c "from pathlib import Path; s=Path('assets/style.css').read_text(encoding='utf-8-sig'); print(s.count('{'), s.count('}'))"
```

---

