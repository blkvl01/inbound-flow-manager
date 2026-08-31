# ULD kezelő felület - gyors kontextus

Ez a jegyzet arra van, hogy uj Codex/Claude beszélgetésben ne kelljen a teljes korábbi beszélgetést és nagy fájlokat újra beolvasni.

## Fő fájlok

- `app.py`
  - Dash layout, ULD render callbackok, Flask API: `/api/uld/stack`
  - ULD lista, stack grid, szűrők, pagination, badge-ek, stack display name
- `assets/uld_manager.js`
  - drag/drop, multi-select, stack műveletek, kliensoldali feedback, toastok
- `assets/style.css`
  - ULD oldal design, light/dark kontraszt, animációk, stack/lista állapotok
- `uld_stack_manager.py`
  - megosztott stack állapot kezelése, lock, atomic write, create/delete/add/remove/reorder/rename
- `data_cache.py`
  - ULD adatok gyors frissítése, cache, workbook source freshness

## Adatmodell

- A stack állapot megosztott fájlban él az `_shared_state` alatt.
- Egy stack tartalmaz:
  - `id`
  - `name`
  - `ulds`
  - `revision`
  - módosítási metaadatokat
- Fontos: `uld_stack_manager.py` belül a stack `ulds` sorrendje bottom-to-top.
- UI-ban az első sor a stack teteje, az utolsó az alja.
- A UI/API top-to-bottom pozíciót használ, a backend fordítja át bottom-to-top sorrendre.

## Állapotforrás

- A stack tartalom végleges forrása mindig a backend.
- A frontend nem írhat végleges stack tartalmat kézzel a DOM-ba drag/drop után.
- Drag/drop közben csak átmeneti vizuális állapot megengedett:
  - `uld-moving-out`
  - `uld-returning-list`
  - `uld-stack-syncing`
  - `uld-si-syncing`
- Sikeres API művelet után a Dash render frissíti a stackeket.
- Ha egy hiba oldalfrissítés után helyrejön, az jellemzően frontend/backend állapoteltérés vagy optimista DOM-manipuláció.

## Üzleti szabályok

- Egy stackben nem keveredhet GHA.
- Több kijelöléssel sem lehet vegyes GHA-t egy stackbe húzni.
- Ezt kliensoldalon és backend oldalon is ellenőrizni kell.
- Stacken belül inaktív ULD maradhat, ha a stack nem teljesen inaktív.
- Inaktív ULD színe elhalványított és badge jelöli: `nem aktív`.
- Ha egy ULD már stackben van, a listában badge jelzi, melyik stackben szerepel.
- Ez a badge nem duplikálódhat.
- Ha egy stack törlődik, a benne lévő ULD-k kijelölése automatikusan megszűnik.
- Az ULD-kód újra felhasználható: egy régi, `Vissza` dátummal lezárt AWB után
  ugyanaz a kód új, későbbi `Áttár` idővel ismét aktív lehet. Ilyenkor a
  jelenlegi ciklus előtti `dispatched_at` idejű kiküldött stackből az ULD eltűnik,
  nem blokkolja az aktív listát, és az aktuális stackbe ismét betehető. Az aktuális
  ciklusban kiküldött stack továbbra is zárolt.

## Stack nevek

- A stack kártyán a státusz felirat helyett név van.
- A név szerkeszthető.
- Ha csak egy adott GHA-tól van benne tétel, és a stack neve alapértelmezett, akkor a GHA nevét kell mutatni.
- Több azonos GHA stack esetén sorszám jelenhet meg.
- Az utolsó módosítást nem kell kiírni a stack kártyán.

## Drag/drop szabályok

- Listából stackbe húzás:
  - GHA validáció
  - API `add`
  - az UI csak syncing állapotot mutat
  - a renderelt backend állapot dönt
- Stackből listába húzás:
  - API `remove`
  - ne törölje ki kézzel a stack itemet a DOM-ból
- Stacken belüli sorrendezés:
  - API `add` pozícióval vagy `reorder`
  - nem szabad kliensoldalon új itemeket beszúrni vagy meglévőket eltávolítani
  - cél: ne legyen duplikáció, eltűnés, rossz sorrend
- Stackek között mozgatás:
  - ugyanaz a GHA szabály érvényes
  - backend eltávolítja az ULD-t minden más stackből, majd beteszi a cél stackbe

## Kijelölés

- Ctrl + bal klikk: hozzáad/töröl a kijelölésből.
- Sima bal klikk: csak az az egy ULD legyen kijelölve.
- Stacken belül ne legyen erős vizuális kijelölés, csak a lista oldali kijelölés legyen hangsúlyos.
- Szűrő/lapozás/stacktörlés után a kijelölésnek konzisztensnek kell maradnia.
- Nem látható vagy már nem létező ULD ne maradjon kijelölve.

## Teljesítmény

- ULD lista lapozott: egyszerre csak az aktuális oldal elemei jelenjenek meg.
- A backend cache-ből adja az ULD adatokat, ne olvassa újra feleslegesen az Excel/OneDrive forrást.
- Flow nézetváltásnál az UI gyorsan jelenjen meg, de üres tartalom helyett legyen betöltési feedback.
- Gombnyomásra legyen azonnali feedback, de ne legyen hamis végleges állapot.

## API stabilitás

- `/api/uld/stack` műveletek:
  - `add`
  - `remove`
  - `create`
  - `delete`
  - `rename`
  - `reorder`
- Minden állapotmódosító műveletnél revision alapú konfliktuskezelés szükséges.
- Lock timeout esetén ne sérüljön az állapot, a UI frissítsen vissza backend állapotra.
- A create/delete/add/remove műveleteknek első gombnyomásra látszaniuk kell, de a látvány a backend-renderből jöjjön.

## GHA feloldás és hibabiztonság

- A GHA ismert írásváltozatai egy közös kulcsra normalizálódnak: `AS Cargo`, `Menzies`, `Celebi`.
- Ha az ULD sorának GHA mezője üres, a parser csak ugyanazon, teljesen azonos AWB másik sorából töltheti vissza.
- Több eltérő AWB-alapú jelölt esetén nem szabad találgatni: a GHA üres marad és `gha_conflict` jelölést kap.
- A hiányzó GHA kijelzési szövege nem kerülhet a `data-gha` attribútumba, mert az üzleti ellenőrzés csak valódi GHA-t használhat.
- A stack első hiteles GHA-ja `stack_gha` mezőben tartósan mentődik; ez később visszatöltheti az időközben hiányossá vált forrásadatot.
- Egy lassú teljes adatfrissítés nem írhatja felül a közben befejeződött frissebb ULD-részfrissítést.
- Az `add` művelet a backendben minden más aktív stackből atomikusan eltávolítja az ULD-t, ezért a kliensnek nem kell külön, régi source-revisionre épülő mozgatást küldenie.
- Konfliktusnál az API visszaadja az aktuális stack-revisionöket; a kliens ugyanazzal a `client_op_id` értékkel, korlátozottan újrapróbálhatja a biztonságos műveletet.
- A `reorder` csak a stack teljes, változatlan ULD-halmazának új sorrendjét fogadhatja el; hiányzó vagy idegen elem esetén nincs mentés.

## Tipikus hibák és első vizsgálati helyek

- Frissítés után helyrejövő rossz stack:
  - `assets/uld_manager.js` optimista DOM módosítás
  - `bumpTriggerSoon`
  - Dash render késése
- Rossz stack törlődik vizuálisan:
  - frontend kézzel eltávolított DOM elem
  - delete API siker előtt manipulált stack grid
- Dupla stack badge az ULD listában:
  - `.uld-in-stack-badge` több példánya egy sorban
  - `cleanupRowStackBadges`
- Stacken belül duplikált/eltűnő ULD:
  - `optimisticAddToStack`
  - `optimisticRemoveFromStacks`
  - drag/drop drop handler
- Vegyes GHA:
  - frontend `ghaAllowed`
  - backend `_validate_stack_gha_move`

## Ajánlott új chat prompt

```text
Projekt: C:\Inbound Flow Manager.
Olvasd el először az ULD_ARCHITECTURE.md fájlt.
Csak ezután vizsgáld célzottan ezeket: app.py, assets/uld_manager.js, assets/style.css, uld_stack_manager.py.
Aktuális cél: az ULD stack működés legyen stabil, ne legyen frontend/backend állapoteltérés, ne keveredjen GHA, ne duplikálódjanak ULD-k vagy stack badge-ek.
Ne olvasd be az egész projektet, csak a releváns részeket rg-vel és kis kódrészletekkel.
```
