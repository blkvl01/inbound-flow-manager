# Inbound Flow Manager – v0.1.5

A kiadás egyetlen futtatható fájlja a `FlowManager.exe`. A program a közös
Excel/OneDrive munkateret nem módosítja és nem igényel `_internal` mappát.

Az automatikus leállítás csak 30 perc POST- és böngésző-inaktivitás után
történik.

A loading képernyő nem tartalmaz tesztnézet-indítót. A közös állapotmappa
automatikusan a felhasználó meglévő OneDrive-mappájában, az
`Ecommerce - Dokumentumok\Program HUB\Flow Manager` vagy
`Ecommerce - Documents\Program HUB\Flow Manager` útvonalon kereshető.
Ha egyik sem létezik, indításkor létező mappa választható ki, illetve ez a
Beállításokban később is módosítható. A program nem hoz létre saját OneDrive-
almappát.

Kiadási assetek:

- `FlowManager.exe`
- `manifest.json`

A dokumentáció tájékoztató jellegű; a frissítő működéséhez csak az EXE és a
manifest szükséges.
