# Claude–Codex Workflow (teljes eljárás)

> Ezt a fájlt akkor olvasd be, amikor ténylegesen delegálni készülsz Codexnek
> (kódírás, fájlmódosítás, refaktor, teszt). A gyökér `CLAUDE.md` tartalmazza a
> rövid összefoglalót; itt van a részletes, kötelező eljárás.

# Claude–Codex együttműködési szabályok

Ebben a projektben te vagy a vezető fejlesztő, rendszertervező és orchestrator.

A Codex MCP-szerver alügynökként áll rendelkezésedre. A Codex feladata elsősorban a tényleges implementáció, fájlmódosítás, tesztelés és hibajavítás.

## Kötelező munkafolyamat

Minden összetettebb fejlesztési feladatnál kövesd ezt a folyamatot:

### 1. Projektfeltérképezés

Először saját magad vizsgáld meg:

* a projekt szerkezetét;
* a kapcsolódó fájlokat;
* a jelenlegi implementációt;
* az adatfolyamokat;
* a függőségeket;
* a meglévő teszteket;
* a lehetséges regressziós kockázatokat.

Ne delegálj homályos vagy hiányos feladatot Codexnek.

### 2. Implementációs terv

Készíts konkrét tervet, amely tartalmazza:

* az elérendő működést;
* az érintett fájlokat;
* a fájlonkénti módosításokat;
* az elfogadási feltételeket;
* a futtatandó teszteket;
* a build- és lintparancsokat;
* a tiltott vagy érintetlenül hagyandó részeket.

Szükség esetén írd a specifikációt a következő fájlba:

.ai/CODEX_HANDOFF.md

### 3. Delegálás Codexnek

A tényleges implementációhoz használd a Codex MCP `codex` eszközét.

Alapértelmezett beállítások:

* cwd: az aktuális projekt gyökérkönyvtára;
* sandbox: workspace-write;
* approval-policy: never;
* include-plan-tool: true.

A Codexnek adott prompt legyen önmagában is teljesen értelmezhető. Tartalmazza:

* a feladat pontos leírását;
* az érintett fájlokat;
* az elvárt működést;
* a korlátozásokat;
* az elfogadási feltételeket;
* a tesztelési követelményeket.

A Codexet utasítsd arra, hogy:

* olvassa el a releváns projektfájlokat;
* hajtsa végre a teljes implementációt;
* tartsa be a projekt meglévő architektúráját;
* ne hagyjon placeholder kódot vagy félkész TODO-t;
* futtassa a releváns teszteket, lintet és buildet;
* javítsa ki az általa okozott hibákat;
* számoljon be minden sikertelen ellenőrzésről.

### 4. Ellenőrzés

A Codex válasza után saját magad ellenőrizd:

* a git diffet;
* a módosított fájlokat;
* az implementáció teljességét;
* az eredeti követelmények teljesülését;
* a teszteredményeket;
* az esetleges regressziókat;
* a szükségtelen vagy oda nem illő módosításokat.

Ne fogadd el automatikusan a Codex állítását arról, hogy a feladat elkészült.

### 5. Javítási kör

Ha problémát találsz, ugyanazt a Codex-threadet folytasd a `codex-reply` eszközzel.

A javítási prompt tartalmazza:

* a pontos hibát;
* a hibás fájlt vagy kódrészletet;
* az elvárt javítást;
* a megismétlendő teszteket.

Legfeljebb három automatikus Claude–Codex javítási kört végezz. Ha ezután is fennáll probléma, állj meg, és adj világos összefoglalót.

### 6. Végső jelentés

A feladat végén foglald össze:

* mely fájlok változtak;
* milyen működés készült el;
* milyen tesztek futottak le;
* mely ellenőrzések voltak sikeresek;
* maradt-e ismert probléma vagy kockázat.

## Szerepek

Claude feladata:

* követelmények értelmezése;
* architektúra;
* tervezés;
* koordináció;
* diffellenőrzés;
* code review;
* Codex munkájának visszaellenőrzése.

Codex feladata:

* tényleges implementáció;
* fájlmódosítás;
* refaktorálás;
* tesztek létrehozása;
* tesztek futtatása;
* build és lint;
* technikai hibajavítás.

Kisebb, egyértelmű módosításokat Claude saját maga is elvégezhet, de nagyobb implementációknál részesítse előnyben a Codex MCP használatát.

# Claude–Codex munkafolyamat

Minden olyan feladatnál, amely kódírást, fájlmódosítást,
hibajavítást, refaktorálást vagy tesztelést igényel:

1. Claude elemezze a feladatot és készítsen implementációs tervet.
2. Claude delegálja a tényleges implementációt a Codex MCP-agentnek.
3. A Codex eredménye után Claude ellenőrizze a git diffet és a teszteket.
4. Hiba esetén Claude ugyanabban a Codex-threadben kérjen javítást.
5. Claude csak az ellenőrzés után tekintse késznek a feladatot.

Egyszerű magyarázó kérdéseknél, dokumentációs kérdéseknél és
kódmódosítást nem igénylő feladatoknál ne hívd meg a Codexet.
