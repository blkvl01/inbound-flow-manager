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

Outbound kártya:

- fő az adott rendszám/truck.
- badge: `X lerakó`, ahol a lerakó az A oszlop különböző értékeinek száma.
- GLABS blokkok `details/summary` dropdownnal.
- GLABS progress: `ready / total kiadható`.
- AWB sorok: `AWB + LMP + colli + súly + státusz`.
- Több lerakó vizuálisan LMP színcsoportokkal elkülönül.

Outbound aktív sor:

- Nem `Kiadva`.
- Nincs `issued_time`.
- A közelmúltban kiadott vagy frissen checkinelt sorok még látszódhatnak outbound kontextusban.

Outbound kártyastátusz:

```text
PIHENŐN           ha rest aktív
LEZÁRVA           ha active == 0
SOFŐR HELYSZÍNEN  ha van checkin és nem pihenő
KIADÁSRA VÁR      ha van ready és nincs checkin
VÁRAKOZIK         ha nincs checkin és nincs ready
```

Outbound szűrők:

```text
Összes
Sofőr itt
Kiadásra vár
Pihenőn
Várakozik
Lezárt
```

Az inbound/outbound ugyanazokat a stat gomb ID-kat használja, de a címkék és színek flow váltáskor átállnak.

---

## INBOUND/OUTBOUND switch

`flow-mode` dcc.Store alapérték: `inbound`.

A switch és a stat filter store beállítása kliensoldali callbackben történik, hogy a gombállapot azonnal reagáljon, ne várjon szerver roundtripre.

Flow váltáskor:

- body class: `flow-outbound-mode`
- rövid `flow-switching` animáció
- aktív filter reset: `all`
- legend és stat labels átállnak
- dashboard újrarendereli a megfelelő kártyarácsot

Korábbi hiba: néha beragadt a switch/filter, mert server callback késleltette a store állapotot. A flow és filter store most clientside.

---

## Betárolva funkció

Fájl: `storage_manager.py`

Közös storage a meglévő OneDrive-mappában:

```text
Ecommerce - Dokumentumok\Program HUB\Flow Manager\_shared_state\stored_awbs.shared.json
Ecommerce - Dokumentumok\Program HUB\Flow Manager\_shared_state\stored_awbs.shared.lock
```

Angol OneDrive-mappanév esetén az `Ecommerce - Documents\Program HUB\Flow Manager\_shared_state`
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
- A tényleges kártya azonnal kikerül a gridből; a helyét egy rövid életű, nullára záródó placeholder tartja, ezért nem marad üres card slot.
- Dash közben azonnal frissíthet; az új DOM beérkezése után a megmaradt kártyák FLIP technikával, csak `transform` animációval csúsznak az új helyükre.
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

- `dcc.Interval(id="poll", interval=2000)`
- Ha `data_version` nem változott, callback `no_update`.

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

