# GitHub Releases frissítés

Az Inbound Flow Manager indítás után a
`https://api.github.com/repos/blkvl01/inbound-flow-manager/releases/latest`
címen keresi a legfrissebb GitHub Release-t. Csak a `manifest.json` által
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
ellenőrzés nélkül nincs csere. Ezután egy rövid életű segédfolyamat megvárja a
főfolyamat kilépését, ismét ellenőrzi a hash-t, `os.replace` művelettel cserél,
majd elindítja az új EXE-t.

Az EXE-nek írható helyről kell futnia, például a felhasználó Downloads vagy
`%LOCALAPPDATA%` mappájából. Rendszergazdai jogosultság nem kell, de
Program Files vagy más védett könyvtár írhatóságát az updater nem tudja
megkerülni.

A közös munkafájlok továbbra is a OneDrive-on maradnak. Az Excel-források
automatikusan az `Ecommerce - Dokumentumok` vagy `Ecommerce - Documents`
mappából töltődnek. A közös állapot automatikus célja közvetlenül:
`Ecommerce - Dokumentumok\Program HUB\Flow Manager\_shared_state` vagy
`Ecommerce - Documents\Program HUB\Flow Manager\_shared_state`. A hiányzó végső
`_shared_state` mappát a program meglévő Ecommerce-munkaterületen automatikusan
létrehozza, és induláskor röviden újrapróbálja a OneDrive-feloldást. A közös `Betárolva`, megjegyzés, override,
ULD-állapotfájlok közvetlenül ebbe a már meglévő munkamappába kerülnek; a
program nem hoz létre hozzá saját OneDrive-almappát. A dashboard cache és a
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
`v0.1.6` release-t token nélkül is eléri. Ha később zárt kiadási tárolóra kell
váltani, a biztonságos alternatíva a gépen megadott
`FLOW_MANAGER_GITHUB_TOKEN`; token nem kerülhet az EXE-be, a manifestbe vagy a
naplóba.
