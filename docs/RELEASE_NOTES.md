# Inbound Flow Manager – v0.1.8

Az E_COMM beolvasás legfeljebb a 25 000. Excel-sorig dolgozik. Az ULD-lista
gyors útvonala először a helyi OneDrive-fájlt olvassa, és csak szükség esetén
készít biztonságos munkamásolatot, ezért induláskor nem készül felesleges
második teljes XLSB-másolat.

A betöltési képernyő a teljes adatbetöltés végéig látható marad, és a százalékos
progress bar mellett az aktuális műveletet és a feldolgozott sorokat is mutatja.

Az automatikus leállítás POST- és böngésző-inaktivitási határa 2 órára nőtt.

A kiadás egyetlen futtatható fájlja a `FlowManager.exe`. A program a közös
Excel/OneDrive munkateret nem módosítja és nem igényel `_internal` mappát.

A loading képernyő nem tartalmaz tesztnézet-indítót. A közös állapotmappa
automatikusan a felhasználó meglévő OneDrive-mappájában, az
`Ecommerce - Dokumentumok\Program HUB\Flow Manager\_shared_state` vagy
`Ecommerce - Documents\Program HUB\Flow Manager\_shared_state` útvonalon kereshető.
Ha a `Program HUB\Flow Manager` már létezik, a hiányzó `_shared_state` almappát
a program automatikusan létrehozza. Indításkor rövid ideig újrapróbálja a
OneDrive-feloldást, ezért a felhasználónak normál esetben nem kell mappát
választania. A dashboard cache és az aktivitásnapló helyi felhasználói mappába
kerül; a OneDrive-on csak a közös műveleti állapot marad.

Kiadási assetek:

- `FlowManager.exe`
- `manifest.json`

A dokumentáció tájékoztató jellegű; a frissítő működéséhez csak az EXE és a
manifest szükséges.
