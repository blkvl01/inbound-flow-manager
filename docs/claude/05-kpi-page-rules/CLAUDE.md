# KPI Page Rules

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

Source: root CLAUDE.md lines 804-951 before split.

## KPI oldal — kódtérkép és kemény szabályok

Ez a riport KPI oldalra vonatkozik (felső páros INBOUND/OUTBOUND vonaldiagram,
Warehouse tracking, Volume trend, Operational time bands). Ezt olvasd el, mielőtt
ezekhez nyúlsz — ne szkenneld újra az egész kódot. Sorszám helyett **grep-horgony**
(függvénynév / CSS-szelektor), mert a sorok csúsznak.

### Melyik fájl mit renderel (`assets/`)

```text
kpi_page.js   Felső páros shift vonaldiagram (renderFlow/drawFlow/animate/playShiftDraw)
              + KPI flow Day based kombinált inbound/outbound chart (renderDayFlow/
              animateDayFlow/wireDayHover/updateDaySummary)
              + Warehouse tracking (renderTracking/updateTrackingValues/trackingValueList/
              openTrackingGroup/closeTrackingDetail). Belépő: window.__renderKpiPageCharts
kpi_bands.js  "Operational time bands": render/chartHtml/gridHtml/playFill/selectHour/
              selectBand. Belépő: window.__renderKpiBands
kpi_trend.js  "Volume trend" diagram + táblázat + XLSX (renderChart/drawOnLines/renderTable).
              Belépő: window.__renderKpiTrend
kpi_temu.js   "TEMU KPI": milestone stage-duration stacked oszlopok + summary kártyák
              + Flagged records lista (renderSuspects) + data table. Belépő:
              window.__renderKpiTemu. Backend: data_reader._compute_temu_stage_kpi.
kpi_chart.js  A kis Műszak óradiagram a HEADER KPI kártyában (NEM a KPI oldal).
```

### KPI Tracking — Flow chart Shift/Day mode (2026-06-24)

Középső flow szekció: `app.py::_kpi_flow_section`, `assets/kpi_page.js`, `assets/style.css`.

- Segment control: `Shift based` (default) / `Day based`. `Shift based`: két shift vonaldiagram.
  `Day based`: közös inbound vs outbound chart; range `7/14 day`; unit `kg / parcel / colli`.
  Inbound szín: narancs `#ff8a3d`; outbound szín: lila `#a78bfa`.
- Day based: 4 summary kártya (inbound total/avg, outbound total/avg). Pont kijelölése CSAK
  selection state-et frissíthet — nem rajzolhatja újra a teljes SVG-t.
- **NE `stroke-dashoffset`** alapú line draw — félúton megállhat/beugorhat. Stabil minta:
  SVG `clipPath` staggerelt width reveal + `solidifyDayFlow()`.
- **Segment control radius**: thumb szélessége `--flow-control-pad` + oszlopszámból számolódjon;
  `.is-morphing` NEM növelheti. Ez a `kg/parcel/colli` controlra is érvényes.

### KPI oldal — 2 háttér + üveges szegmensek (whole-page, mozgó)

`assets/style.css` vége (KPI PAGE REDESIGN blokk) + body-level `.kpi-bg` réteg (app.py,
`.bg-orbs` mellett, app-root-on KÍVÜL, z-index 1):

- Tracking: kék/cián/türkíz alap + 5 mozgó blob. Riport: lila-kék. FONTOS: `.kpi-sub-report`
  base újra-deklarálja a `background-attachment: fixed`-et — a `background` shorthand
  `scroll`-ra reseteli, ezért a háttér "ismétlődött lefele" a magas snap-oldalon.
- Mozgás CSAK transform (GPU `kpiBgDrift1..5`). SZÜNETEL: `body.kpi-bg-paused` (scroll/snap),
  `body.kpi-bg-hidden`, `:not(.view-kpi-mode)`, `prefers-reduced-motion`.
- Tracking-cardokon a `backdrop-filter` SZÁNDÉKOSAN ki van kapcsolva perf miatt (style.css
  ~22394). NE tegyél generikus `::before/::after`-t a szekciókra — a trend/temu saját
  `::before/::after`-ját elnyomja.
- TILTÁS: `color-mix()` (renderer ledobja) — mindenhol rgba/hex triplet.

### TEMU KPI — csak kiadott tételek (KEMÉNY szabály, 2026-06-23)

A `data_reader.py::_compute_temu_stage_kpi` a TEMU subset (`sub`) felépítése után
**csak a már kiadott tételeket** tartja meg: amelyeknek az E_COMM **AS oszlopában**
(`departure_raw` / Tétel indulás) van valós dátum (`pd.to_numeric(...).notna() & > 0`).
A még úton / be nem fejezett tételek torzítanák a lead-time átlagokat, ezért teljesen
kimaradnak — a Flagged records lista is csak kiadott tételekből épül. A "kiadott" jelet
a NYERS cella adja, nem a date-guard; a departure szakasz megbízhatóságát továbbra is a
downstream guard kezeli külön. (A guard tesztek fixture-jei mind beállítanak
`departure_raw`-t, ezért átmennek.)

### TEMU KPI — date-guard filozófia (KEMÉNY szabály)

A TEMU tételeknél emberek rögzítik a milestone időpontokat (ATA→NOA→Áttár→Vámkez.k→
Vámkez.v→Indulás), ezért sok az elgépelés (előző/következő nap, nap/hónap csere, jövőbeli
dátum). A `data_reader.py::_normalize_temu_stage_dates` egy **konzervatív** date-guard:

- CSAK ember-jellegű elgépelést javít: ±1–2 napos csúszás és nap/hónap csere, ami a
  szomszéd mérföldkövek közelébe esik (`_temu_trusted_candidates`, `_TEMU_TRUST_SHIFT_DAYS`,
  `_TEMU_SWAP_NEAR_DAYS`). SOHA nem relokál nagy távra → nem talál ki hihető, de hamis
  időtartamot. (Régen: a megoldó a valós indulást is elmozdította, hogy koherens legyen.)
- Ha egy mérföldkő nem javítható megbízhatóan, a drop-DP **eldobja és megjelöli**
  (`meta["suspect_fields"]`), az érintett szakasz BLOKKOLT (nem kerül az átlagba). A tétel
  a többi (jó) szakaszával benne MARAD a KPI-ban — semmi TEMU nem vész el.
- A megjelölt tételek `payload["suspects"]`-be gyűlnek (awb, lmp, ország, nap, mezőnként
  raw érték + ok), és a UI-n a "Flagged records" drawer listázza őket (fix at source).
- Aggregálás CSAK lezárt napokra: a kliens `periods()`-e kiszűri a `is_today`/élő oszlopot
  (`isClosedPeriod`), így a mai részleges nap nem torzítja az átlagot.
- Teszt: `tests/test_kpi_page.py::test_temu_stage_guard_*`. NE állítsd vissza a
  "fabricate coherence" viselkedést — user döntés, hogy a hibás adat inkább listázódjon.

### KPI Tracking workbook-paritás

Referencia: `KPI tracking.xlsx` → `Warehouse analysis` képletei (`_compute_tracking_kpis()`).

**Hard rule**: KPI tracking számítást az AWB-szűrés ELŐTT kell futtatni. A workbook
`Kg per parcel` képlete a teljes raw tartományt számolja, AWB nélküli helper/összesítő
sorokkal együtt. Az aktív inbound lista és AWB lookup viszont utána szűrje ki az üres AWB-ket.

### A style.css DUPLIKÁLT — így találod meg az érvényes szabályt

A style.css ~19k sor; a codex redesign-blokkokat fűzött a végére, így a legtöbb KPI
szelektor 3-5-ször van definiálva. **Grepeld a szelektor összes előfordulását; azonos
specificitásnál a LEGUTOLSÓ (legnagyobb sorszámú) nyer.** Ha egy szabály „nem hat",
akkor egy későbbi duplikátum vagy egy magasabb specificitású szelektor (pl.
`.kpi-page-chart-outbound .kpi-page-metric.is-active-view`, 3 osztály) felülírja.

### Kemény szabályok (codex ezeket szokta elrontani)

- **NINCS `color-mix()`** (a portable .exe renderere ledobja). Rgb-triplet változókkal
  helyettesítsd: `rgba(var(--metric-accent-rgb|--group-active-rgb|--detail-accent-rgb|--band-in-rgb|--band-out-rgb), a)`.
- **NINCS folyamatos idle animáció** — kivétel a lassú keret-gyűrű spin (user OK): tracking 7s,
  bands 9s, metric gyűrű 12s. A border gradient az elem KÖRÜL van (maszkolt conic), nem belül forog.
  Szögprop: `@property --tracking-border-angle` / `--kpi-band-border-angle`.
- **NE építsd újra a DOM-ot a 2s poll-nál** — backdrop-filter alatt villog. Minta: dedupe
  signature-rel, fix struktúrájú szekciónál értékeket helyben patch-eld (`updateTrackingValues`).
  A reveal/draw-on dedupe STRUKTURÁLIS signature-re menjen, soha élő kg-ra (`revealSig` a drawFlow-ban).
- **Volume trend vonal toggle**: ha egy sorozatot ki/be kapcsolsz, a vonal opacity/exit-enter
  animációja fusson le előbb; az axis/domain/layout eloszlás csak utána frissüljön, és az is
  tweenelve menjen. Ne ugorjon be az új skála.
- **Volume trend opening/closing**: a delayed draw minden szegmensre külön sorrendet kapjon,
  alacsony volumenű pontoknál se egyszerre jelenjenek meg. Az opening ne álljon meg az
  utolsó előtti szakasznál, és idősáv/óra selection ne indítsa újra az openinget.
- **Operational time bands**: bármelyik történeti nap választható, nem csak előző nap.
  Óra és band selection csak minimális selection transitiont indítson. Band selection
  egyetlen összefüggő szegmensként jelenjen meg, ne külön-külön oszloponként.
- **Peak band kártyák fixek**: mindig a legerősebb időszakot mutassák; óra fókusznál ne
  változzanak át fókuszértékre. Inbound és outbound peak külön kártya.
- **Warehouse tracking**: a summary DOM fix struktúra. Poll alatt csak érték patch,
  ne `innerHTML` rebuild. A total/summary sor háttere fix legyen, hogy scroll/fade alatt
  ne csússzon mögé tartalom.
- **Scroll reveal csak ≥50% láthatóságnál** indul: observerek `threshold:[0,0.25,0.5,0.75,1]` +
  `halfVisible(entry)` helper (mindhárom KPI scriptben).
- Inbound = narancs `#ff8a3d` (255,138,61); Outbound = lila `#a78bfa` (167,139,250). Új
  diagram-markernél a `.kpi-page-chart-outbound …` lila override-ot is tedd be.

### Ellenőrzés szerkesztés után

CSS kapocs (zárójelek egyezzenek) + `node --check assets/<fájl>.js`. App: 127.0.0.1:8501
(általában már fut egy FlowManager példány a porton). Részletes változásnapló a Claude
memóriában: `kpi_page_animation_gating.md`, `kpi_code_map.md`.

---

## Ismert megjegyzések

- Python 3.14 alatt előfordulhat `ZipFile.__del__` zaj pyxlsb/openpyxl környékén; általában nem funkcionális hiba.
- Az Excel olvasás időigényes, ezért van dashboard cache és háttérszál.
- A BUD-Pallets openpyxl warning (`Data Validation extension is not supported`) elnyomásra kerül.
- A forrás Excel fájlokat temp copyból olvassa, hogy ne tartson file lockot.
- A projekt privát Git-repó; destruktív Git-művelet vagy erőltetett feltöltés
  felhasználói jóváhagyás nélkül továbbra sem megengedett. Lásd: `CLOUD.md`.

---

