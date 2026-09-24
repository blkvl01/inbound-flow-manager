# Inbound Flow Manager – v0.1.15

Az indításkori frissítéskeresés a legújabb publikus kiadás közvetlen
`manifest.json` letöltési címét használja. Így a közös GitHub API-címen
jelentkező névtelen óránkénti kéréskorlát nem akadályozza a verzióellenőrzést.
Az EXE továbbra is csak a manifestben megadott méret és SHA-256 ellenőrzése
után cserélhető.

## Előző javító kiadás: v0.1.14

A Windows önfrissítő külön helyi segédmásolatból végzi az EXE cseréjét, így a
segédfolyamat nem tartja zárolva a cserélendő programfájlt. A segéd a letöltött
fájlt csere előtt újra ellenőrzi, sikertelen csere esetén visszaindítja a korábbi
verziót, és helyi frissítési naplót ír. Sikeres csere után az új verzió indul,
majd eltávolítja a segédmásolatot.

A v0.1.11 és korábbi telepített EXE-k önfrissítőjében még a zárolási hiba van;
ezeknél egyszeri kézi vagy Program HUB-os csere szükséges. A v0.1.13-as Git tag
forrásmentésként létezik, de önálló publikus EXE-kiadás nem készült belőle.

## Korábbi fejlesztések: v0.1.12–v0.1.13

A Program HUB számára a program verziót, futási állapotot és szűkített technikai
eseményeket jelent a meglévő közös munkatérbe. Üzleti rekord és részletes
hibaüzenet nem kerül a jelentésbe.

Az E_COMM olvasó a felismert fejléc alapján továbbra is csak a szükséges 21
mezőt veszi át, de a széles XLSB munkalap felesleges cellaobjektumait már nem
hozza létre. A napi KPI idősávok óránkénti értékeit egyszer összesíti, majd
újrahasználja. Hideg induláskor a bővített ULD-olvasás a teljes betöltés után
indul, így a két XLSB-bejárás nem versenyez egymással az első képernyő előtt.

A közepes szélességű ablakok operatív és KPI fejlécében a frissességi jelzések,
vezérlők és mutatók külön sorba rendeződnek. A kártyák súgószövege biztonságosan jeleníti
meg a forrásból származó értékeket, és a csökkentett mozgás beállításnál a
diagram nem vár késleltetett áttűnésre.

Helyi, azonos forrás-pillanatképen végzett mérés: 11 646 sor / 21 E_COMM mező,
nyers beolvasás 13,87 → 10,85 mp; a teljes összeállítás két-két futásának
átlaga 28,81 → 23,03 mp. Ezek a helyi gép eredményei, más gépen és szinkronállapotban
eltérhetnek. A nyers adattáblák teljesen egyeztek.

## Előző kiadás: v0.1.11

A hideg indulás kezelése javítva lett: a korábbi dashboard-cache többé nem
rejti el túl korán a loading screent. A képernyő addig marad látható, amíg a
friss Excel-adatok és az első ULD-beolvasás is be nem fejeződik.

Az automatikus GitHub-frissítés stabilabb lett lassú vagy időszakosan akadozó
kapcsolat esetén. A letöltési időkorlát 30 percre nőtt, hálózati hiba esetén a
program legfeljebb háromszor újrapróbálja a letöltést, és a legalább kétórás
félbehagyott ideiglenes frissítési fájlokat induláskor kitakarítja.

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
