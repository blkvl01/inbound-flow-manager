# Outbound, Switch, Storage and Dev Tools

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

Source: root CLAUDE.md lines 313-539 before split.

## OUTBOUND modell

Üzleti forrás a BUD-Pallets adatmodell. `excel` és `shadow` módban ezt a
`BUD-Pallets.xlsm` adja; teljes `oracle` módban ugyanaz a memóriamodell a
`WAREHOUSE_DETAIL_REPORT_VW` mezőiből épül fel. Az Excel-olvasó tartalék és
összehasonlító forrásként megmarad.

Hierarchia:

```text
rendszám > GLABS ID > AWB sorok
```

Outbound ADAT (megmarad): a `data_cache` állapot `outbound_cards` /
`outbound_errors` kulcsai és a KPI `outbound_*` mezői. Fogyasztók: TV mód
(`/api/tv/ops` -> kiadott panel, rakodások, műszak riport) és a KPI oldal
(`kpi-mode-outbound`, Flow/Napszak chartok). Aktív sor: nem `Kiadva`, nincs
`issued_time`; a TV-ben kiadott tétel sehol nem jelenhet meg.

Outbound NÉZET: 2026-11-19-én user döntésre törölve (nem volt elérhető gomb).
Vele ment: `_make_outbound_card`, `_outbound_filter_tokens`, outbound ág a
stat/legend/dashboard callbackekben, `flow-outbound-mode` body class, GLABS
nyomtatási előnézet (`print_preview.js`, `jsbarcode.min.js`, `/print/glabs`,
`print-preview-overlay`) és a csak ezekhez tartozó CSS. Az `outbound-awb-row`,
`outbound-glabs-block` stb. osztályok ÉLŐK: az inbound kiadható-lista
(`_ready_item_rows`, `inbound-ready-block`) használja őket.

---

## INBOUND/ULD switch

`flow-mode` dcc.Store alapérték: `inbound`; értékei `inbound` | `uld`.

A switch és a stat filter store beállítása kliensoldali callbackben történik, hogy a gombállapot azonnal reagáljon, ne várjon szerver roundtripre.

Flow váltáskor:

- body class: `flow-uld-mode`
- rövid `flow-switching` animáció
- aktív filter reset: `all`
- dashboard / ULD nézet újrarenderel

Korábbi hiba: néha beragadt a switch/filter, mert server callback késleltette a store állapotot. A flow és filter store most clientside.

---

## Betárolva funkció

Fájl: `storage_manager.py`

Közös storage a meglévő OneDrive-mappában:

```text
Ecommerce - Dokumentumok\Program HUB\Flow Manager\stored_awbs.shared.json
Ecommerce - Dokumentumok\Program HUB\Flow Manager\stored_awbs.shared.lock
```

Angol OneDrive-mappanév esetén az `Ecommerce - Documents\Program HUB\Flow Manager`
útvonal használható. A program ezt a közös mappát automatikusan keresi, és a
korábban kiválasztott eltérő útvonalat induláskor felülírja.

Formátum:

```json
{
  "<awb>": {
    "stored_at": "<ISO datetime>",
    "stored_by": "<user>",
    "stored_on": "<computer>",
    "snapshot": { "...row fields..." }
  }
}
```

Lock:

- lock fájl: `stored_awbs.shared.lock`
- timeout: 15s
- stale lock: 120s után törölhető
- atomic write temp fájllal + `os.replace`

Működés:

1. User rákattint: `Betárolva`.
2. `storage_manager.toggle_stored()` írja/törli a közös JSON-t.
3. `data_cache.update_stored_flags()` újraolvasás nélkül frissíti a DataFrame-et.
4. A kártya eltűnik aktív nézetből.
5. A kapcsolódó GLABS progress és dropdown azonnal frissül.

UI animáció:

- `assets/card_fx.js` kliensoldali, delegált kattintásfigyelővel indul.
- Kattintáskor a kártyák pozícióiról snapshot készül, a kattintott kártya fixed-position ghostként fade/scale animációval fut ki.
- A valódi kártya React/Dash node: a JS SOHA nem veszi ki (`removeChild`) és nem tesz mellé placeholdert — csak `card-exiting` osztályt kap (`visibility:hidden`, helyén marad). A kártyát a szerver-render veszi ki; ha 8 mp után is a DOM-ban van (mentés nem ment át), visszakapja a láthatóságot.
- Minden kártya stabil Dash id-t kap a `data-card-key`-ből (`_key_cards`: `{"type":"prio-card","index":<AWB | outbound:kulcs>}`). Enélkül a dash-renderer POZÍCIÓ szerint egyeztet, és betároláskor egy MÁSIK kártya tűnt el / a nyitott megjegyzés és a count-up a szomszédra csúszott.
- Az új DOM beérkezése után a megmaradt kártyák FLIP technikával, csak `transform` animációval csúsznak az új helyükre.
- A `Betárolt tételek` renderelés nem ír shared storage-ot, csak olvas. Storage cleanup csak háttérfrissítéskor történhet, így egy kattintás nem tüntethet el mellékhatásként másik kártyát.

Manual stored extra:

- Ha egy tételt manuálisan betárolnak, de BUD szerint még nem ready, akkor az adott GLABS progressben +1-ként jelenhet meg.
- A dropdownban `Betárolva` státusszal, `is-manual` osztállyal szerepelhet.

---

## Megjegyzések (inbound kártyák)

Fájl: `notes_manager.py`
Közös storage: `_shared_state/item_notes.shared.json` + lock (storage_manager minta).

- Több megjegyzés gyűlhet AWB-nként listában; mindegyiknél `id, text, by, on, at`.
- Csak az aktív INBOUND kártyákon van szerkesztő blokk (`_make_notes_block`),
  a Betárolt nézetben és Úton kártyán nincs.
- A kártya fejlécsávjában egy `badge-note` (chat-buborék SVG ikon + `n` szám) jelzi
  zárt kártyán is, hogy van megjegyzés. NINCS emoji — lásd alább az ikon-rendszert.
- Hozzáadás: input + Hozzáad gomb vagy Enter (`handle_note_action`, pattern-matching,
  `triggered_value` guard mint a store-btn-nél). Törlés: ✕ a megjegyzés sorában.
- A `note-action` store Input az `update_dashboard`-nak → azonnali re-render;
  más gépeken a file-watcher (notes mtime → `bump_data_version`) frissít.
- Cleanup csak háttérfrissítéskor: az AWB már nem aktív/betárolt ÉS a legújabb
  megjegyzés 48 óránál régebbi (átmeneti Excel-hiba nem törölhet friss megjegyzést).
- Max hossz: 280 karakter.

---

## Developer menü

Belépés: Beállítások modal → "Developer" gomb → PIN. A PIN sózott SHA-256 hashe
az app.py-ban (`_DEV_PIN_HASH`), session token memóriában (`_DEV_TOKENS`,
oldal-újratöltéskor újra kell PIN-t adni).

Tabok:

1. **Műveletek**: állapot-infó, kényszerített újraolvasás, dashboard cache törlés,
   lock fájlok feloldása.
2. **Állapotok**: Betárolva bejegyzések, megjegyzések, ULD stack-ek, átnevezések
   listája egyenkénti törléssel (`{"type": "dev-del", "kind": ..., "index": ...}`).
3. **Felülbírálás**: per-AWB mező-felülbírálás (`overrides_manager.py`,
   `_shared_state/item_overrides.shared.json`). Engedélyezett mezők:
   `lmp, weight, boxes, cargo_type, uld_number, glabs_id, rendszam, is_shippable`.
   A pipeline-ban a stored-state ELŐTT, a priority engine előtt alkalmazódik
   (`apply_overrides_to_df`), `<mező>__orig` backup oszlopokkal — törléskor az
   Excel-érték visszaáll újraolvasás nélkül. Csak a dashboardot érinti, Excelt nem ír.
4. **Napló**: tevékenységnapló nézet (dátum/user/keresés szűrő, max 500 sor).

---

## Tevékenységnapló

Fájl: `activity_log.py`
Storage: gépenként helyben a `%LOCALAPPDATA%/InboundFlowManager/<COMPUTERNAME>/<USERNAME>/activity_logs`
mappában `activity_YYYY-MM-DD.jsonl`, 30 nap retenció (induláskor takarít).
A logolás soha nem dobhat hibát és nem blokkol (2s lock timeout, hiba esetén az
esemény eldobódik). A közös OneDrive-mappába aktivitásnapló nem kerül.

Logolt események: `app_start/app_stop` (leállási okkal + session perc),
`page_open`, `store_toggle`, `note_add/note_delete`, `uld_*` (API műveletek),
`settings_save`, `dev_*` (minden dev menü művelet + PIN próbálkozások),
`ui_*` (kliensoldali kattintások az `assets/activity_beacon.js`-ből a
`/api/log/ui` beacon végponton: szűrők, flow váltás, téma, frissítés stb.).

---

## Dashboard cache

Fájl: `data_cache.py`

Cache fájlok:

```text
%LOCALAPPDATA%/InboundFlowManager/<user>/dashboard_cache.pkl
```

A dashboard cache gépenkénti gyorsítótár, ezért nem kerül a közös OneDrive
állapotmappába. A közös mappában csak az együttműködéshez szükséges állapotok
maradnak.

Induláskor csak akkor töltődik be cache, ha a forrás Excel fájlok mtime/size signature-je egyezik. Ha bármelyik Excel változott, a cache érvénytelen és a UI loading állapotban marad a friss beolvasás végéig, hogy ne jelenjen meg fals/régi kártya.

Háttérfrissítés:

- `load_all_flow_data()` egyszer olvassa a BUD-Pallets fájlt, majd ebből építi az inbound és outbound nézetet.
- Scheduled refresh: `refresh_interval_minutes`, default 10 perc.
- File watcher: 3 másodpercenként mtime ellenőrzés.
- Excel source változáskor a régi kártyák azonnal eltűnnek és loading látszik az újraolvasás végéig.
- Storage watcher: ha a shared stored JSON változik, csak stored flags újraalkalmazás történik, nem teljes Excel read.

UI poll:

- `dcc.Interval(id="poll", interval=5000, disabled=True)` — NEM ketyeg magától.
  Az `assets/poll_gate.js` 5 mp-enként egy `GET /api/poll-gate`-et küld
  (`_poll_gate_signature`: data_version, status, refreshing, uld_refreshing,
  refresh_count, stall, hiba, updater-fázis; a due-arrival bumpot is ez hívja),
  és csak aláírás-változáskor (+ 60 mp-es biztonsági tick) növeli a
  `poll.n_intervals`-t `set_props`-szal. A poll-callbackek kódja változatlan.
  Előtte 8 callback ment 5 mp-enként a dash-rendereren át, szinte mind
  `no_update`-tel: 50–180 ms-os blokkok, akadó animációk (2026-11-27).
- Új állapot, amire poll-callback reagál → kerüljön be a `_poll_gate_signature`-be.
- Ha `data_version` nem változott, callback `no_update`.
- `update_dashboard` a `last-refresh-ts` és `data-version` store-t csak VÁLTOZÁSKOR
  írja (azonos érték = `no_update`): a Dash minden írásra elsüti az összes függő
  callbacket, így egy szűrő-/lapozó-/fókuszkattintás korábban a legendát, a
  kamion-oldalsávot, a ULD-összegzőt és a visszaszámlálót is újrarenderelte.
- `tick` 30 mp (csak a forrás-kor jelvények percfelbontása kell; a másodperc-
  visszaszámlálás kliensoldali, `refresh_countdown.js`).
- `data_version` csak renderelt változásra emelkedik: adatbeolvasás vége, ULD-olvasás vége/hibája, stored flags, override, megjegyzés, stack-változás. NEM emel: frissítés-indulás (`refreshing`, `uld_refreshing`), ismételt stall-próba (csak az első stall, status- vagy hibaszöveg-váltás). A `refreshing`/stall jelzést a loading overlay és a `tick` saját pollja olvassa.
- ULD aktív nézet: `_uld_active_signature` (rekordok, aktív stackek, stack-időadatok, override, szűrők, oldal) a `uld-dispatched-sig` store-ban `active:` előtaggal; poll + változatlan aláírás = `no_update`. Stack-only render után az aláírás `None`, így a következő verzióváltás teljes renderrel frissíti a listát.
- TV mód (`view-mode == "tv"`): a `data-version` store befagy, a stat/KPI/telítettség/BEC poll-callbackek `no_update`-et adnak (a TV saját `/api/tv/ops` fetch-et használ). Ezek `view-mode` Inputot kaptak, kilépéskor újrarenderelnek.
- `/api/tv/ops`: payload-cache `data_version` kulccsal + 10 s TTL, a `freshness` blokk minden kérésnél friss; a df-ből csak a használt oszlopok kerülnek ki.

---

## CMD / app leállítás

Az app valós böngészős user aktivitást figyel (`mousemove`, `mousedown`, `keydown`, `wheel`, `touchstart`, `scroll`). Ha 2 óráig nincs aktivitás, a watchdog `os._exit(0)`-val leállítja a szervert, hogy a közös gépeken ne maradjon nyitva CMD.

Emellett Flask request szinten minden HTTP `POST` frissíti a `_last_post_time` időbélyeget. Ha 2 óráig nincs POST a Dash app felől, a watchdog szintén leállítja a CMD-t. Ez arra az esetre van, amikor a böngésző/tab már nem küld callbackeket, és a CMD-ben nem pörögnek a POST sorok.

Jelenlegi leállítás:

```text
Ctrl+C
CMD ablak bezárása
taskkill / folyamat leállítása
2 óra böngésző inaktivitás
2 óra POST hiány
```

---

