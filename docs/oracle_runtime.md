# WebbyCom Oracle futtatási modell

## Adatforrás módok

A `FLOW_ECOMM_SOURCE` folyamat-környezeti változó három értéket fogad:

- `excel`: alapértelmezés; a jelenlegi Excel az aktív forrás, Oracle-kapcsolat nem indul.
- `shadow`: az Excel marad aktív, az Oracle ugyanabban a tízperces ciklusban csak ellenőrzési célból frissül.
- `oracle`: az Oracle a tényleges E_COMM és BUD Pallets forrás; az Excel-olvasók megmaradnak tartalék és összehasonlító üzemhez, de nem keverednek automatikusan az Oracle-snapshottal, és induláskor nincs Excel-fájlválasztó.

Az első próbaüzemhez a `shadow` mód javasolt.

## Biztonságos indítás

Az `Inditas_Oracle.cmd` / `Inditas_Oracle.ps1` maszkoltan kéri be a jelszót. A jelszó csak az indított Flow Manager folyamat környezetébe kerül, fájlba nem íródik, majd az indító saját környezetéből törlődik. A publish folyamat mindkét indítót a publikált `FlowManager.exe` mellé másolja.

Shadow mód:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Inditas_Oracle.ps1 -Mode shadow
```

Oracle aktív mód:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Inditas_Oracle.ps1 -Mode oracle
```

Környezeti beállítások:

| Változó | Alapérték | Szerep |
|---|---|---|
| `WEBBYCOM_ORACLE_HOST` | `192.168.45.180` | Oracle host |
| `WEBBYCOM_ORACLE_PORT` | `1521` | Oracle port |
| `WEBBYCOM_ORACLE_SERVICE` | `HGL` | service name |
| `WEBBYCOM_ORACLE_USER` | `ECOMM_READ` | csak olvasási felhasználó |
| `WEBBYCOM_ORACLE_PASSWORD` | nincs | kötelező jelszó, csak folyamatkörnyezetből |
| `WEBBYCOM_ORACLE_CALL_TIMEOUT_MS` | `120000` | kapcsolati/lekérdezési időkorlát |
| `WEBBYCOM_ORACLE_FULL_REFRESH_HOURS` | `6` | periodikus teljes újratöltés a törlések felismeréséhez |

## Frissítési működés

1. Az első Oracle-frissítés teljes head/detail betöltés.
2. Ezután a tízperces Flow Manager ciklusban `LAST_CHANGE_DATE` alapú inkrementális lekérdezés fut, két másodperces átfedéssel.
3. Az Oracle rendszeridő felső vízjelként szolgál, így a két view lekérdezése közben érkező változás nem veszhet el.
4. A head és detail változások azonosító alapján upsertelődnek a jelölt snapshotba.
5. Egyediség, árva detail, AWB-konzisztencia és `LAST_CHANGE_DATE` ellenőrzés után történik az atomikus memóriacsere.
6. Hatóránként teljes újratöltés történik, mert egy törölt view-rekordot az inkrementális változáslekérdezés önmagában nem jelez.
7. Kapcsolati vagy validációs hiba esetén sem az Oracle belső snapshot, sem a dashboard utolsó sikeres adatállománya nem ürül ki.

Az outbound ugyanabból az atomikus detail snapshotból épül fel. A WebbyCom
`BudPalletLocation` mezőjének Oracle megfelelője `BUD_PALLET_LOCATION`; ezt tilos
a raktári tételhelyeket tartalmazó `LOCATION_CODES` mezővel helyettesíteni.
`BUD_PALLET_DUMP_NUMBER` a lerakó sorszám, `PLT_OUT` a kiadott paletták száma,
`ITEM_DEPARTURE` a kiadás/tételindulás időpontja, `BUD_PALLET_RAMP` pedig a rámpa
és műszakvezető mező. A szabad `BUD_PALLET_REMARK` adja a pihenő/E-COM jelzések
forrását, és üres ramp mezőnél a rakodási megjegyzés tartaléka.

## Mennyiségi adatminőség

- A Flow Manager detail mennyiségeket használ.
- A head mennyiségek kontrollértékek.
- A minden mennyiségében nullás detail rekord figyelmeztetésként jelenik meg a forrás-metaadatban.
- Head értékből automatikus detail-feltöltés nincs.
- A részleges Excel-mezők az első Oracle-verzióban nincsenek leképezve.

## Élő adapterellenőrzés — 2026-08-12 14:41

- Eredmény: `ORACLE_ADAPTER_OK`.
- Teljes betöltés: 1 095 head és 1 328 detail rekord.
- A közös nyers DataFrame: 1 328 sor.
- Oracle-watermark: `2026-08-12T14:41:44.043188`.
- Adatminőségi figyelmeztetés: 17 teljesen nullás detail-mennyiség és 26 head/detail kontrollösszeg-eltérés.
- A figyelmeztetések nem blokkolták az atomikus snapshotot, és nem váltottak ki headből történő automatikus detail-feltöltést.

## Csomagolási ellenőrzés

A külön PyInstaller ellenőrző build sikeresen elkészült. A csomag tartalmazza az `oracle_ecomm` modult, valamint a `python-oracledb 4.0.2` moduljait és natív `.pyd` állományait. Éles publikálás ebben a lépésben nem történt.
