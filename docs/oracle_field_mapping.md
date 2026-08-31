# E_COMM Excel -> WebbyCom Oracle mezőmegfeleltetés

Állapot: ellenőrzésre kész, az éles adatforrás nincs átállítva. Az Excel-fejlécek
helye és pontos szövege 2026-08-25-én read-only másolaton újraellenőrizve; a
szemantikus fejléc az `E-comm` munkalap 12. sorában van.

Érvényes összehasonlítás: 2026-08-12 12:47 (Europe/Budapest). Az Excel és az Oracle csak 372 `AWB + ügyfél` kulcson fedte egymást, ezért az egyezési arányok kizárólag erre az átfedésre vonatkoznak.

## Mezőszintű megfeleltetés

| Excel oszlop / fejléc | Belső mező | Oracle view.oszlop | Szint | Típus és átalakítás | Flow Manager használat | Bizonytalanság / élő ellenőrzés |
|---|---|---|---|---|---|---|
| B — `Ügyfél` | `lmp` | `WAREHOUSE_DETAIL_REPORT_VW.CLIENT_NAME` + TEMU-besoroláshoz `WAREHOUSE_HEAD_REPORT_VW.HEAD_MAIN_CLIENT_GROUP` | detail név + head besorolás | Excel szöveg -> Oracle `VARCHAR2`; whitespace-trim. TEMU head esetén a detail ügyfélnév elé `TEMU ` kerül, ha még nincs rajta. | inbound lista, LMP-prioritás, ULD-k, outbound csoportosítás, trend és TEMU ország | A detail `CLIENT_NAME` az aktuális view-ban például `SI POST`; az Excel-jellegű `TEMU SI POST` megjelenítéshez szükséges a head TEMU-csoportja. Nem TEMU headnél a detail név változatlan. |
| D — `AWB` | `awb` | `WAREHOUSE_DETAIL_REPORT_VW.AIR_WAYBILL`; kontrollként `WAREHOUSE_HEAD_REPORT_VW.AIR_WAYBILL` | detail + head kulcs | Excel szöveg -> Oracle `VARCHAR2`; trim; vezető nullát meg kell őrizni | minden összevonás, inbound/outbound sorok, ULD, KPI és override kulcs | Alacsony: 372/372 egyezett. Az Oracle belső kapcsolata `REF_E_COMM_ID -> E_COMM_ID`. |
| E — `Colli` | `boxes` | `WAREHOUSE_DETAIL_REPORT_VW.DETAIL_COLLIS` | detail | Excel numerikus -> Oracle `NUMBER`; null kezelése, összegekhez decimal; tolerancia 0,01 | inbound mennyiség, rakodási összesítések, KPI | Alacsony az átfedésben: 369/372. Head kontroll: `SUM_OF_COLLIS`; head értéket tilos detail soronként ismételten összeadni. |
| F — `Súly` | `weight` | `WAREHOUSE_DETAIL_REPORT_VW.DETAIL_WEIGHT_KG` | detail | Excel numerikus -> Oracle `NUMBER(18,2)`; decimal, 0,01 tolerancia | prioritás, inbound/outbound mennyiségek, napi/heti és TEMU KPI | Alacsony: 370/372. Head kontroll: `SUM_OF_WEIGHT_KG`; 33 head közül többnél főleg súlyösszeg-eltérés látszik. |
| G — `Parcel` | `parcel_count` | `WAREHOUSE_DETAIL_REPORT_VW.DETAIL_PARCELS` | detail | Excel numerikus -> Oracle `NUMBER`; egész darabszám | státusz-AWB aggregáció, KPI, inbound összesítés | Közepes: 347/372. Head kontroll: `SUM_OF_PARCELS`. |
| J — `POE` | `poe_raw` | nincs az átadott view-kban | detail jellegű | Excel vegyes/szöveg; Oracle-forrás nincs | jelenleg nincs downstream fogyasztó | Nyitott, de az első Oracle-verziót nem blokkolja, amíg valóban nincs fogyasztója. |
| K — `GHA` | `carrier_k` | `WAREHOUSE_DETAIL_REPORT_VW.GHA_COMPANY_NAME` | detail | Excel szöveg -> Oracle `VARCHAR2`; trim | AS Cargo KPI-kivétel, ULD megjelenítés/csoportosítás | Alacsony: 372/372 egyezett. Stabil belső kulcshoz később `REF_GHA_COMPANY_ID` használható. |
| L — `Várhatóérk.` | `expected_arrival_raw` | `WAREHOUSE_DETAIL_REPORT_VW.EXPECTED_ARRIVAL` | detail | Excel dátumsorszám -> Oracle `DATE`; Python `datetime`, másodperc-pontosság; összevetéskor 60 mp tolerancia | beérkezhető KPI és időablakok | Közepes: 349/372. A munkalapon két hasonló fejléc lehet; a reader a jelenlegi L-hez legközelebbit választja. |
| N — `Státusz` | `status_n` | `WAREHOUSE_DETAIL_REPORT_VW.PARCEL_STATUS_NAME` | detail | Excel/Oracle szöveg -> közös ékezet- és kisbetűfüggetlen státuszkulcs | aktív inbound szűrés, prioritás, kiadhatóság, KPI panelek | A `DUP` Excel-oldali technikai/történeti jelzés: azt mutatja, hogy a rekord WebbyComban szerepel. Nem Oracle életciklus-státusz, ezért az összehasonlítás és a jövőbeli státuszlogika figyelmen kívül hagyja. |
| O — `=` | `partial_boxes_raw` | egyelőre nincs átvezetve | detail | Excel numerikus részérték | jelenlegi Excel-specifikus `Részben` logika | Halasztva: az Oracle-alapú modell első verziójában nem szükséges. Konkrét üzleti igénynél részletesen újravizsgálandó. |
| P — `Részben(kg)` | `partial_weight_raw` | egyelőre nincs átvezetve | detail | Excel numerikus részérték | jelenlegi Excel-specifikus `Részben` logika | Halasztva: az Oracle-alapú modell első verziójában nem szükséges. Konkrét üzleti igénynél részletesen újravizsgálandó. |
| V — `ULD azonosító` | `uld_raw` | `WAREHOUSE_DETAIL_REPORT_VW.ULD_NUMBER` | detail | Excel többértékű szöveg -> Oracle `VARCHAR2`; a meglévő ULD-parser normalizált halmazt képez | ULD/PLT besorolás, ULD monitor, visszaadási ciklus | Alacsony-közepes: 365/372. Ellenőrizni kell, hogy az Oracle mindig egy ULD-t ad-e detailenként. |
| AD — `Vissza` | `ad_raw` | `WAREHOUSE_DETAIL_REPORT_VW.ULD_RETURN_DATE` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | ULD visszaadva állapot; az aktív ULD-listából kizárás | Üzletileg megerősítve: az ULD GHA-nak történt visszavitelének időpontja. A 203/372 történeti egyezés eltérését adatfrissítési/időbeli okként kell tovább vizsgálni, nem mezőtérképezési kérdésként. |
| AH — `PRE-ALERT` | `ai_raw` | nincs az átadott view-kban | detail | Excel dátumsorszám; Oracle-forrás nincs | csak a jelenlegi Excel-specifikus `Részben` 24 órás segédszabály használja | Üzletileg nem szükséges az Oracle-alapú működésben; nem blokkoló és nem kerül az új memóriamodellbe. |
| AJ — `ATA` | `ak_raw` | `WAREHOUSE_DETAIL_REPORT_VW.ATA` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | beérkezési KPI, TEMU `ATA -> NOA` lead time, fallback riportdátum | Alacsony: 371/372. |
| AK — `NOA` | `noa_raw` | `WAREHOUSE_DETAIL_REPORT_VW.NOA` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | reggeli trend, TEMU `ATA -> NOA` és `NOA -> Transfer` | Alacsony-közepes: 366/372. |
| AL — `Áttár` | `am_raw` | `WAREHOUSE_DETAIL_REPORT_VW.GOODS_TRANSFER_DATE` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | aktív inbound 24 órás ablak, prioritás kor, ULD induló idő, TEMU transfer | Alacsony-közepes: 365/372. A projekt néhol „felvétel ideje”, néhol „áttár/transfer” néven kezeli; a definíciót rögzíteni kell. |
| AN — `Rendszám` | `rendszam_raw` | `WAREHOUSE_DETAIL_REPORT_VW.TRANSFER_PLATE_NUMBER` | detail | Excel szöveg -> Oracle `VARCHAR2`; trim | inbound tétel megjelenítés, override és rakodási egyeztetés | Üzletileg megerősítve: az áttárhoz tartozó inbound rendszám. Az Oracle-mezőben előforduló nem rendszám formátumú értékeket adatminőségi hibaként kell kezelni. |
| AP — `Vámkez.k.` | `aq_raw` | `WAREHOUSE_DETAIL_REPORT_VW.CUSTOMS_CLEARANCE_START` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | aktív inbound kizárás; TEMU `Transfer -> Customs` | Közepes: 354/372. A reader a korábbi `Vámkez k.` alakot is explicit kompatibilitási aliasként elfogadja. |
| AQ — `Vámkez.v.` | `ar_raw` | `WAREHOUSE_DETAIL_REPORT_VW.CUSTOMS_CLEARANCE_FINISH` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | kiadhatósági állapot és TEMU `Customs -> Departure` | Alacsony-közepes: 365/372. |
| AS — `Tétel indulás` | `departure_raw` | `WAREHOUSE_DETAIL_REPORT_VW.ITEM_DEPARTURE` | detail | Excel dátumsorszám -> Oracle `DATE`; `datetime`, 60 mp tolerancia | outbound műszakmennyiség, heti átlag, TEMU lezárt tételek és lead time | Közepes: 359/372. |

## Oracle head mezők

| Oracle mező | Szerep a tervezett memóriamodellben |
|---|---|
| `E_COMM_ID NUMBER(18,0)` | Stabil head elsődleges kulcs. |
| `AIR_WAYBILL VARCHAR2` | AWB head szinten; detail AWB-k konzisztencia-ellenőrzése. |
| `SUM_OF_COLLIS NUMBER` | Head kontrollösszeg, nem detail sorérték. |
| `SUM_OF_WEIGHT_KG NUMBER(18,2)` | Head kontrollösszeg, nem detail sorérték. |
| `SUM_OF_PARCELS NUMBER` | Head kontrollösszeg, nem detail sorérték. |
| `HEAD_MAIN_CLIENT_GROUP VARCHAR2` | Head csoportosító metaadat; nem helyettesíti a detail `CLIENT_NAME` mezőt, de a `TEMU` besorolást ez adja az Excel-jellegű LMP-címkéhez. |
| `CREATION_DATE DATE` | Audit/diagnosztikai mező; nincs jelenlegi Excel-megfelelő. |
| `LAST_CHANGE_DATE TIMESTAMP(6)` | Inkrementális head-frissítés vízjele. |

## Oracle detail kulcsok és további mezők

- `E_COMM_DETAIL_ID`: stabil detail elsődleges kulcs; ezt kell upsert-kulcsként használni.
- `REF_E_COMM_ID`: kapcsolat a head `E_COMM_ID` mezőhöz.
- `LAST_CHANGE_DATE TIMESTAMP(6)`: detail inkrementális frissítési vízjel.
- `REF_DETAIL_CLIENT_ID`, `REF_GHA_COMPANY_ID`, `REF_PARCELS_STATUS_ID`, `REF_DEST_COUNTRY_ID`: stabil törzsazonosítók; a jelenlegi Excelben nincs megfelelőjük.
- `LAST_PARCEL_ST_CHANGE`: státuszváltozás auditideje; a jelenlegi Excelben nincs külön megfelelője.
- `DELIVERY_PLATE_NUMBER`: üzletileg a kiszállításhoz tartozó outbound rendszám; külön mező, nem az Excel AN / `rendszam_raw` helyettesítője.
- `DEST_COUNTRY_NAME` → outbound `bud_lmp` / célhely.
- `DELIVERY_PLATE_NUMBER` → outbound `plate` / kiszállítási rendszám.
- `GLABS_ID` → outbound `glabs_id`.
- `DETAIL_COLLIS`, `DETAIL_WEIGHT_KG`, `PARCEL_STATUS_NAME` → outbound colli, súly és státusz.
- `ITEM_DEPARTURE` → outbound `issued_time` / kiadás időpontja.
- `PLT_OUT` → outbound `pallets_issued` / kiadott paletták száma.
- `BUD_PALLET_LOCATION` → outbound `location`; ez a WebbyCom `BudPalletLocation` mezője.
- `BUD_PALLET_CHECK_IN` → outbound `driver_checkin`.
- `BUD_PALLET_REMARK` → pihenő/E-COM értelmezés és szabad megjegyzés.
- `BUD_PALLET_RAMP` → rámpa és műszakvezető; üres értéknél a megjegyzés a tartalék.
- `BUD_PALLET_DUMP_NUMBER` → `dump_number` / lerakó sorszám; nem palettaszám.
- `LOCATION_CODES` továbbra is a tétel raktári tárhelykód-gyűjteménye, ezért nem használható BUD-Pallets lokációként.

## Élő összehasonlítás összegzése

- Excel: 9 821 adatsor, 9 818 egyedi `AWB + ügyfél` kulcs.
- Oracle: 1 086 head és 1 319 detail rekord.
- Átfedés: 372 kulcs; ebből 43 minden összevetett mezőben egyezett, 329-nél legalább egy eltérés volt.
- 9 446 Excel-kulcs és 946 Oracle-kulcs csak az egyik forrásban szerepelt. Ez főként eltérő történeti/időablakos lefedettséget jelez, nem önmagában hibát.
- 580 mezőeltérésből 293 státusz, ebből 282 `DUP` vs `Kiadva`; 169 ULD-visszaadási dátum.
- 33 head rekordnál legalább egy head összeg nem egyezett a hozzá tartozó detail összegekkel. Ezeket az Oracle-oldali jelentéslogikával kell tisztázni; emiatt a head összegek nem használhatók detail KPI-ként.

## Jóváhagyás előtt eldöntendő

1. A head/detail érdemi kontrollösszeg-eltérések a view-k szándékos számítási és hozzárendelési logikájából erednek-e? Részletek: `docs/oracle_head_detail_discrepancies.md`.

Csak e pontok közös jóváhagyása után következhet az Oracle adatforrás implementálása. Az Excel olvasó addig változatlanul az aktív és tartalék adatforrás.
