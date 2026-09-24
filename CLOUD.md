# Flow Manager – GitHub és Codex Cloud

Nyilvános forrás- és kiadási repó: https://github.com/blkvl01/inbound-flow-manager

Az aktuális hordozható kiadás: `v0.1.14`. A `FlowManager.exe` és a hozzá tartozó
`manifest.json` publikus GitHub Release assetként érhető el; a HUB és az updater
token nélkül is ezt a kiadást tudja ellenőrizni.

## Fejlesztés és ellenőrzés

A felhős környezet a program forrását és izolált tesztjeit tartalmazza.
Nem szolgáltat élő raktári dashboardot, nem fér hozzá a laptop fájljaihoz,
és nem kap üzemi Oracle-jelszót vagy Excel-adatokat.

Telepítés: `bash scripts/codex-setup.sh`

Teljes teszt: `bash scripts/codex-test.sh`

Windows alatt ugyanaz a teszt: `python scripts/codex-test.py`

A tesztindító külön átmeneti beállítási és állapotmappát használ, eltávolítja
az örökölt Oracle-beállításokat, tiltja a tesztfolyamat hálózati kapcsolatait,
és nem indít Windows-fájlválasztót. A tesztek saját mintákkal és helyettesített
adatforrásokkal dolgoznak. Hiányzó éles fájlt ne tölts fel a tesztek kedvéért.

Az AGENTS.md és a projekt CLAUDE.md útmutatói továbbra is érvényesek.
Kész Windows-kiadást helyben, a meglévő buildeljárással kell készíteni;
a felhős teszt nem helyettesíti a kész EXE és az üzemi környezet ellenőrzését.

## Laptop és telefon közötti folytatás

- Helyi munkamappa: `C:\Inbound Flow Manager`.
- A Codex GitHub mentés az engedélyezett forrásfájlokat a `main` ágra menti.
- Az állapotablakban a projekt neve: **Inbound Flow Manager**.
- Kikapcsolás előtt várd meg a friss „Minden feltöltve” állapotot.
- A telefonos/felhős munka eredményét ellenőrzés után olvaszd be a `main` ágba.
- A laptopon mentsd és zárd be a szerkesztőt, majd az állapotablakban válaszd
  a Flow Managert és a „Felhős változások átvétele” gombot.
- Ha mindkét oldalon változtattál, az automatika megáll; mindkét munka megmarad.

Átvétel után frissen nyisd meg a helyi forrásfájlt. Egy korábbról nyitva maradt
szerkesztőlap régi tartalmát ne mentsd rá a GitHubról érkezett változatra.

Az üzemi config.json, Excel-, adatbázis-, állapot-, napló- és kiadási fájlok,
a valódi Oracle-eltéréslista és az operatív képernyőképek helyben maradnak.
Új adat-, JSON- vagy médiafájl külön ellenőrzést igényel; nem automatikus feltöltés.
