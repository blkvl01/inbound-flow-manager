# Project and Data Sources

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

Source: root CLAUDE.md lines 1-119 before split.

# Flow Manager - CLAUDE.md

## Projekt célja

A Flow Manager egy portable Python/Dash dashboard a HGL Group Hungary Ecommerce operációhoz.
Két nézetet kezel ugyanazon felületen:

- **INBOUND**: aktív E_COMM tételek priorizálása érkeztetéshez/raktári kezeléshez.
- **OUTBOUND**: BUD-Pallets rakodások áttekintése rendszám > GLABS ID > LMP/AWB bontásban.

Az app portable `.exe`-ként is futtatható. A tervezett céges használat: közös mappában van a portable verzió, több felhasználó egyszerre használja, a manuális "Betárolva" állapotot közös fájlban osztják meg.

---

## Fő fájlok

```text
app.py               Dash layout, kártya-renderelés, callbackek, kliensoldali UI state
data_reader.py       E_COMM és BUD-Pallets olvasás, inbound/outbound adatmodellek
data_cache.py        Háttérfrissítés, dashboard cache, shared stored-state újraalkalmazás
priority_engine.py   Inbound prioritási score és rank
storage_manager.py   Közös "Betárolva" JSON + lock kezelés
notes_manager.py     Közös per-AWB megjegyzések (inbound kártyák)
overrides_manager.py Dev menü per-AWB mező-felülbírálások
activity_log.py      Megosztott tevékenységnapló (napi JSONL, 30 nap retenció)
config.py            Per-user config, Excel útvonalak, port
assets/style.css     Teljes custom UI, liquid/glass, inbound/outbound stílusok
build.bat            PyInstaller build
FlowManager.spec     PyInstaller spec
```

GitHub-forrásmentés: a projekt helyi Git-repóként a privát
`blkvl01/inbound-flow-manager` tárolóhoz kapcsolódik. Az üzemi adatokat,
helyi beállításokat és Oracle-eltéréslistát ne add Githez. Lásd: `CLOUD.md`.

---

## Adatforrások

Backend típusbiztonság: Excelből érkező rendezési és számítási mezők nem használhatók nyersen. A prioritási score, outbound sort kulcsok, GLABS számítások és UI sort műveletek mindenhol típusbiztos numeric/text/datetime helperen mennek át, hogy hibás cellaérték (`float`, `str`, üres, `NaN`, rossz dátum) ne okozzon runtime összehasonlítási hibát.

### E_COMM

Fájl: `E_COMM nyomonkövetés_24.xlsb`
Sheet: `E-comm`
Az olvasó az első 100 sorban az `Ügyfél` + `AWB` címkékből dinamikusan keresi meg
a szemantikus fejlécsort, a címkék oszlophelyét sem rögzíti fix B/D pozícióra.
A teljes pandas-import és a gyors ULD-olvasó is a ténylegesen felismert
`header_row`-ból és ugyanabból a 21 mezős szemantikus mappingból dolgozik.
2026-08-25-én a fejléc a 12. Excel sorban volt.
Olvasó: pandas `pyxlsb`
Limit: a felismert fejléc utáni adatsoroktól a 25000. Excel sorig olvas; a `nrows`
értékét a tényleges fejlécsorból számolja. A korábbi 10000 soros KPI-export határt
2026-08-24-vel kinőtte az élő E_COMM: az aktív ULD-k és beérkező tételek már a
10000. sor után voltak.

Használt oszlopok:

```text
B  lmp        LMP/carrier
D  awb        AWB
E  boxes      Colli
F  weight     kg
G  parcel_count  Parcel
J  poe_raw        POE
K  carrier_k  KPI kivételhez
L  expected_arrival_raw  Várhatóérk.
N  status_n   E_COMM státusz
O  partial_boxes_raw     részleges Colli (=)
P  partial_weight_raw    Részben(kg)
V  uld_raw        ULD azonosító; ha van érték: ULD, különben PLT
AD ad_raw         ULD visszaadás a GHA-nak (Vissza)
AH ai_raw         részleges tétel PRE-ALERT időpontja
AJ ak_raw         várható érkezés / ATA, KPI
AK noa_raw        NOA
AL am_raw         felvétel ideje / áttárolás (Áttár)
AN rendszam_raw   rendszám
AP aq_raw         vámkezelés kezdete (Vámkez.k.; legacy alias: Vámkez k.)
AQ ar_raw         vámkezelés vége (Vámkez.v.)
AS departure_raw  tétel indulás / outbound
```

Fontos (2026-07-22 ellenőrzés): a 2026-07-14-én látott üres `T` oszlop a jelenlegi
élő fájlban már nincs jelen, ezért a fenti mezők visszább kerültek. A pandas import
és a gyors ULD-olvasó közös mappingot használ, és a tényleges szemantikus
fejlécekből oldja fel az indexeket; a 2026-07-14-i beszúrt-T layoutot is támogatja.

Fontos (2026-08-25 ellenőrzés): a read-only auditmásolaton a resolver mind a 21
oszlopot a fenti helyre oldotta fel, a teljes nyers import 11 767 adatsort adott,
és a teljes `_read_ecomm` kivétel nélkül lefutott (2 aktív sor, 10 053 kiadható
AWB-kulcs, 30 nyitott ULD). A fejlécsor már nem kerül be ál-adatsorként, mert a
pandas-import a dinamikusan felismert 12. sort használja tényleges fejlécként.

Fontos (2026-08-25 újraellenőrzés, a mostani betűkkel): a customs mezők szemantikája
`aq_raw`=AP (Vámkez.k., kezdete), `ar_raw`=AQ (Vámkez.v., vége). Ne térjen vissza
az a korábbi hiba, amely a customs végét vagy a `CT/kell?` oszlopot olvasta.

Aktív INBOUND tétel:

1. AWB nem üres.
2. `am_raw` nem üres.
3. `status_n == "Felvéve"`.
4. `aq_raw` (AP / Vámkez.k.) üres.
5. `am_time >= most - 24 óra`.

Fontos döntés: az E_COMM `Kiadható`, `Mitörténik`, `Vámkez alatt` státuszú sorai **nem** kerülnek be aktív inbound kártyaként. Ezek korábban 61 tételes túlmutatást okoztak. Az aktív inbound lista a valós `Felvéve + AP (Vámkez.k.) üres` állapotot mutatja.

ULD megjelenítés: ha az E_COMM `ULD azonosító` mezője (jelenleg V) nem üres,
a tétel `cargo_type == ULD`, és az érték `uld_number` mezőként bekerül az inbound
kártyára. A kártyán az AWB sor jobb oldalán csak ULD esetén látszik sárga komment
jellegű ULD badge; PLT-nél nincs ilyen elem.

ULD / GHA edit flow fontos:

- A kanonikus GHA dropdown értékek: `AS Cargo`, `Menzies`, `Celebi`.
- ULD szerkesztésnél az állapotnak végig kell mennie backend override-on,
  shared persistence-en, row dataseten és stack payloadon. Ne csak a frontend labelt
  írd át.
- Audit mezők: `edited_at`, `edited_by`; a UI badge és dev menü ezekből dolgozik.
- Stack numbering stabil: a stack sorszáma a mentett stack suffixből/display name-ből
  jön, nem az aktuális látható sorrendből. Ha egy stack eltűnik, a többi nem
  számozódhat át.
- Egy fizikai ULD azonosítója újra felhasználható. Ha a régi E_COMM AWB-sorban a
  `Vissza` mező kitöltött, majd ugyanaz az ULD egy későbbi, nyitott AWB-sorban új
  `Áttár` idővel jelenik meg, a korábbi kiküldött stack nem blokkolhatja az új
  ciklust. A ciklushatárt a stack `dispatched_at` és az aktuális nyitott sor
  `am_time` értéke határozza meg; hiányzó időbélyegnél a rendszer konzervatívan
  megtartja a zárolást.

### BUD-Pallets

Teljes `oracle` módban az outbound nem igényli ezt a fájlt: az Oracle detail
snapshotból épül ugyanaz a memóriamodell. A WebbyCom BUD-Pallets lokációja
`BUD_PALLET_LOCATION`; a `LOCATION_CODES` ettől különálló raktári tárhelykód,
ezért nem használható lokációként. Excel és shadow módban az alábbi eredeti
Excel-olvasás változatlanul aktív.

Fájl: `BUD-Pallets.xlsm`
Sheet: `Rakodások`
Olvasó: `openpyxl`, `read_only=True`, `keep_vba=True`, `data_only=True`

Használt oszlopok:

```text
A  bud_lmp          LMP / lerakó / carrier
B  awb              AWB
C  plate            rendszám
D  eta_note         megjegyzés
E  glabs_id         GLABS ID
F  boxes            colli
G  weight           kg
H  kiad_status      státusz
I  pallets_issued   kiadott paletták száma
J  location         lokáció
K  driver_checkin   sofőr bejelentkezés
L  issued_time      kiadás ideje
M  rest_until       pihenő megjegyzés/idő
N  load_note        megjegyzés
```

---

