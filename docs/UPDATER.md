# GitHub Releases frissítés

Az Inbound Flow Manager indítás után a
`https://github.com/blkvl01/inbound-flow-manager/releases/latest/download/manifest.json`
közvetlen publikus címen keresi a legfrissebb GitHub Release manifestjét. Ez az
útvonal nem függ a GitHub névtelen API-jának óránkénti korlátjától. Csak a `manifest.json` által
azonosított, az aktuálisnál újabb `FlowManager.exe` fogadható el.

Ez a kiadási csomag az Excel/OneDrive adatforrást rögzíti. A forráskódban
meglévő Oracle-próbaútvonal ettől a terjesztett EXE-től független; az EXE nem
vált át Oracle-ra környezeti változó hatására.

A manifest kötelező mezői:

```json
{
  "format": 1,
  "app_name": "Inbound Flow Manager",
  "version": "1.0.1",
  "package": {
    "name": "FlowManager.exe",
    "size": 123456789,
    "sha256": "64 kisbetűs hexadecimális karakter"
  }
}
```

A letöltés az EXE mellett ideiglenes fájlba történik. A méret- és SHA-256
ellenőrzés nélkül nincs csere. A lassú kapcsolatokat 30 perces letöltési ablak
és legfeljebb három próbálkozás kezeli; a legalább kétórás, korábbi félbehagyott
ideiglenes fájlok induláskor biztonságosan takaríthatók. Ezután egy rövid életű
segédfolyamat a futó EXE külön, helyi felhasználói mappába másolt példányából
indul. Megvárja a főfolyamat kilépését, ismét ellenőrzi a hash-t,
`os.replace` művelettel cserél, majd elindítja az új EXE-t. Az új példány
eltávolítja a segédmásolatot. Ha a csere mégsem sikerül, a segéd újraindítja a
korábbi EXE-t; a hiba a `%LOCALAPPDATA%\FlowManager\frissites.log` fájlba kerül.

Az EXE-nek írható helyről kell futnia, például a felhasználó Downloads vagy
`%LOCALAPPDATA%` mappájából. Rendszergazdai jogosultság nem kell, de
Program Files vagy más védett könyvtár írhatóságát az updater nem tudja
megkerülni.

A közös munkafájlok továbbra is a OneDrive-on maradnak. Az Excel-források
automatikusan az `Ecommerce - Dokumentumok` vagy `Ecommerce - Documents`
mappából töltődnek. A közös állapot automatikus célja közvetlenül:
`Ecommerce - Dokumentumok\Program HUB\Flow Manager` vagy
`Ecommerce - Documents\Program HUB\Flow Manager`. A program csak már létező
`Flow Manager` mappát fogad el, nem hozza létre sem ezt, sem egy `_shared_state`
almappát. Induláskor röviden újrapróbálja a OneDrive-feloldást. Ha egyik
regisztrált OneDrive-gyökérben sincs Ecommerce mappa, végső tartalékként az
aktuális felhasználói profil közvetlen almappáiban is keresi azt; ez támogatja
például a `HGL Group Hungary Kft\Ecommerce - Dokumentumok` SharePoint-szinkron
elrendezést. A közös `Betárolva`, megjegyzés, override,
ULD-állapotfájlok közvetlenül ebbe a már meglévő `Flow Manager` munkamappába
kerülnek. A dashboard cache és a
fejlesztői aktivitásnapló gépenként helyben, a felhasználó helyi
alkalmazásmappájában marad, így nem terheli az OneDrive-szinkronizálást. Ezt a helyet
a `FLOW_SHARED_STATE_DIR` környezeti változó vagy a Beállításokban megadott
meglévő mappa felülírhatja.
Ha egyik változat sem található, a fagyasztott EXE indításkor felajánlja egy
létező mappa kiválasztását. A választó megszakítása esetén a program helyi,
az EXE mellett írható tartalékot használ.

Ha külön meglévő `shared_state` mappát kell használni, annak útvonala a
Beállítások / Közös állapotmappa mezőben választható ki. A program ezt a mappát
nem hozza létre; csak létező és írható mappát fogad el. A mentés újraindítás
után lép életbe.

A kiadási repository nyilvános, ezért az anonim indítási ellenőrzés a
legújabb release-t token nélkül is eléri. A v0.1.11 és régebbi EXE-kben a
segéd még a zárolt programfájlból indul: ezeket egyszer kézzel vagy a Program
HUB-bal kell az új, javított EXE-re cserélni. Ha később zárt kiadási tárolóra kell
váltani, a biztonságos alternatíva a gépen megadott
`FLOW_MANAGER_GITHUB_TOKEN`; token nem kerülhet az EXE-be, a manifestbe vagy a
naplóba.
