# Inbound Flow Manager – kiadás

A kiadás egyetlen futtatható fájlja a `FlowManager.exe`. A program a közös
Excel/OneDrive munkateret nem módosítja és nem igényel `_internal` mappát.

Az automatikus leállítás csak 30 perc POST- és böngésző-inaktivitás után
történik.

A loading képernyő nem tartalmaz tesztnézet-indítót. A közös állapotmappa a
Beállításokban mappaválasztóval megadható; a program csak meglévő mappát használ.

Kiadási assetek:

- `FlowManager.exe`
- `manifest.json`

A dokumentáció tájékoztató jellegű; a frissítő működéséhez csak az EXE és a
manifest szükséges.
