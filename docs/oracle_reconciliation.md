# WebbyCom Oracle – E_COMM összehasonlítás

Az `oracle_reconciliation.py` kizárólag olvasási műveleteket végez:

- az E_COMM fájlt a Flow Manager meglévő, ideiglenes másolatos olvasójával nyitja meg;
- az Oracle-ben csak `SELECT` lekérdezéseket futtat a két report view-n;
- egyik forrást sem módosítja;
- a jelszót nem fogadja parancssori argumentumként és nem írja riportba.

## Biztonságos futtatás

A jelszó csak a futtató folyamat környezetében legyen elérhető:

```powershell
$env:WEBBYCOM_ORACLE_PASSWORD = Read-Host "Oracle jelszó" -MaskInput
try {
    python oracle_reconciliation.py
} finally {
    Remove-Item Env:WEBBYCOM_ORACLE_PASSWORD -ErrorAction SilentlyContinue
}
```

Ha a `python-oracledb` driver nem a futtató Python környezetében található, külön
ideiglenes könyvtár adható meg a `WEBBYCOM_ORACLE_DRIVER_DIR` változóval vagy a
`--driver-dir` argumentummal. A jelszóhoz nincs CLI argumentum.

Alapértelmezett kimenet:

```text
outputs/webbycom_oracle_reconciliation/
```

A JSON és CSV fájlok auditálható köztes eredmények. A formázott XLSX riport ezekből
készül. A program detail szinten hasonlít, a head mennyiségeket pedig külön
kontrollként kezeli, így a head összeg nem szorzódhat fel a detail sorokon.

## Fontos értelmezési korlátok

- A párosítás üzleti kulcsa az összehasonlításban `AWB + CLIENT_NAME/LMP`.
- Az Oracle-integráció későbbi belső kulcsa ettől függetlenül az
  `E_COMM_ID` / `E_COMM_DETAIL_ID` lesz.
- A `PRE-ALERT` mezőnek jelenleg nincs Oracle-view megfelelője.
- A részleges colli és súly megfeleltetése jelölt, nem jóváhagyott mapping.
- A rendszám első jelöltje a `TRANSFER_PLATE_NUMBER`; a riport eltérései alapján
  külön ellenőrizni kell a `DELIVERY_PLATE_NUMBER` lehetőségét is.
