# Inbound Model and Priority

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

Source: root CLAUDE.md lines 120-312 before split.

## Közös státuszlogika

`data_reader.py` helper:

```python
_is_ready_status(status)
```

Ready/kiadható státuszok normalizáltan:

```text
Kiadható
Mitörténik
Vámkez alatt
```

Kiadott státusz:

```python
_is_pallet_issued(status, issued_time)
```

Egy BUD-Pallets sor kiadottnak számít, ha:

- státusz normalizáltan `Kiadva`, vagy
- `issued_time` valós datetime.

Ez inbound és outbound oldalon is ugyanaz a szabály.

---

## Pihenő logika

BUD-Pallets M oszlopa csak akkor jelent pihenőt, ha szövegként tényleges pihenő marker van benne:

```text
pihen
piheno
pihenos
pihin
szunet
```

Sima dátum vagy egyéb megjegyzés nem számít pihenőnek. Ez fontos, mert korábban a dátum jellegű M oszlopértékek hibásan pihenőként viselkedhettek.

Ha pihenő szövegben időpont van, `_parse_rest_text()` megpróbálja kinyerni a végét. Ha nincs értelmezhető végidő, akkor a pihenő "folyamatban" jelleggel kerül jelölésre.

---

## INBOUND modell

Forrás: E_COMM aktív sorok + BUD-Pallets AWB/GLABS adatok.

Összekapcsolás:

1. Pontos AWB egyezés.
2. Ha nincs, BUD AWB prefix match: `p_awb.startswith(ecomm_awb + "-")`.
3. Több BUD sor esetén a `driver_checkin`-es sor preferált.

E_COMM dedup:

- Ha `AWB`, `AWB-1`, `AWB-2` együtt létezik, az alap AWB marad.
- A suffix csak akkor suffix, ha az utolsó `-` utáni rész 1-3 jegyű szám.

### INBOUND GLABS progress

GLABS szinten csak a **nem kiadott** BUD-Pallets sorok számítanak aktív rakodási tételnek.

```text
glabs_total      = adott GLABS nem kiadott sorai
glabs_shippable  = ebből ready státuszú sorok
glabs_ratio      = glabs_shippable / glabs_total
```

Kiadott BUD sor nem kerül progressbe és nem kerül dropdownba.

### INBOUND kártya dropdown

Az inbound kártyán, ha van GLABS/rakodás, megjelenik egy "Rakodás tételei" dropdown.

Tartalma:

```text
LMP + AWB + státusz
```

Ez nem csak a ready tételeket mutatja, hanem az adott GLABS/rakodás összes **még nem kiadott** tételét, hogy látható legyen, a rakodás többi sora hogyan áll.

CSS osztályok:

```text
is-ready   ready státusz
is-open    nem ready, de még aktív/nem kiadott
is-manual  manuálisan betárolt extra
```

Adatmezők:

```text
glabs_loading_items       aktuális dropdown lista
glabs_loading_items_base  Excelből jövő alaplista
glabs_ready_items         kompatibilitási alias, jelenleg ugyanazt kapja
```

---

## INBOUND prioritási logika

Fájl: `priority_engine.py`

B2B abszolút prioritás:

- Ha a tétel saját E_COMM `Ügyfél` / inbound `lmp` mezőjében a `B2B`
  önálló azonosítóként szerepel, a tétel minden más prioritási kapu elé kerül.
- Ez az AT/HU pihenő-hamarosan, a 4+ órás aged és az operatív bucket szabályt is
  megelőzi; a B2B blokkon belül az itt lévő munka megelőzi az úton lévőt.
- A B2B prioritást kizárólag a tétel saját `lmp` mezője adja. Ugyanazon GLABS
  dropdown másik B2B sora nem emelheti előre a nem B2B tételt.
- UI: `B2B - AZONNALI PRIORITÁS` címke és piros `b2b` színkulcs.
- A normál inbound prioritási felületen a `T` billentyű memóriában élő tesztnézetet
  kapcsol. A tesztadatok ugyanazon `apply_priorities()` és `_make_card()` útvonalon
  futnak, mint az üzemi tételek; a tesztmód sem Excelt, sem shared state-et nem ír.

Csoportok:

```text
GRP_AT_HU = 1                  legacy konstans, nem elsődleges csoportképző
GRP_DRIVER_ACTIVE_HIGH = 2     sofőr itt + sok kiadható
GRP_DRIVER_ACTIVE_LOW = 3      sofőr itt + kevés kiadható
GRP_DRIVER_PASSIVE_HIGH = 4    sofőr nincs itt/pihenőn + sok kiadható
GRP_DRIVER_PASSIVE_LOW = 5     sofőr nincs itt/pihenőn + kevés kiadható / nincs rakodásban
```

AT/HU szabály:

- Az operatív állapot az elsődleges: sofőr itt, rakodásban szerepel, sok kiadható tétel előrébb kerülhet egy gyengébb helyzetű AT/HU-nál.
- AT/HU nem külön top csoport, hanem a legerősebb LMP tie-break azonos operatív bucketen belül.
- Példa: egy nem AT/HU tétel, amelynek itt a sofőrje vagy több kiadható tétele van a rakodásában, megelőzhet egy AT/HU tételt, ha az AT/HU nincs rakodásban vagy nincs itt a sofőr.
- Konkrét elv: `3/4 kiadható` rakodásban lévő tétel előrébb van, mint egy `1/3 kiadható` AT/HU tétel, mert az operatív bucket erősebb, mint az LMP tie-break.

LMP tie-break sorrend azonos operatív bucketen belül:

```text
1. AT/HU
2. TEMU, de nem TEMU MD
3. MEEST MD + 4PX + TEMU MD
4. minden más
```

TEMU szabály:

- Nincs külön TEMU prioritás kategória.
- TEMU kategórián belüli tie-break: azonos operatív kategórián belül előrébb kerül.
- `TEMU MD` nem általános TEMU, hanem az MD lane csoportban van.

High/low ready:

```python
high = glabs_ratio >= 0.5 or glabs_shippable >= 3
```

Sürgősségi öregedés (aging):

- `_AGING_HOURS = 4`: ha egy tétel **4+ órája felvéve** (`am_time`) ÉS már nincs úton
  (fizikailag itt van), akkor a normál operatív munka ELÉ kerül — túl régóta vár.
- Az aged tieren belül a **legrégebben felvett** tétel az első (oldest-first).
- Ez egy "hard gate", nem csak tie-break: egy 4+ órás, gyenge operatív helyzetű tétel
  megelőz egy friss, sofőr-itt + sok-kiadható tételt is.
- Sorrend a gate-ek között: `AT/HU pihenő-hamarosan (0) > aged (1) > normál munka (2) > úton (3)`.
- Kártyán `is_aged` flag → `⏳ N ÓRA+` badge + amber keret (`is-aged-card`).

Rendezési kulcs:

```python
(
    hard_gate,            # -1 B2B, 0 AT/HU rest-soon, 1 aged (4+ óra), 2 normál, 3 úton
    arrival,              # csak úton tételnél: korábbi várható érkezés előrébb
    am if aged else 0,    # aged tieren belül: legrégebben felvéve elöl
    priority_group,
    operational_bucket,   # driver itt/high > driver itt/low > rakodásban/high > rakodásban/low > nincs rakodásban
    -glabs_shippable,
    lmp_priority_tier,    # AT/HU > TEMU > MEEST MD/4PX/TEMU MD > egyéb
    -glabs_ratio,
    status_sort,          # E_COMM státusz-mix, csak finom tie-break
    am_time,              # régebbi felvéve idő = előrébb (egyrangú tételeknél a legrégebben felvett kerül előre)
)
```

AT/HU csak akkor kap első LMP tie-breaket, ha szerepel rakodásban. Ha egy másik tétel ugyanabban az operatív szintben több kiadható tétellel rendelkezik a rakodásából, akkor az kerül előrébb az AT/HU előtt.

Debug mezők:

```text
operational_bucket
lmp_priority_tier
priority_score
```

Driver active:

```python
in_bud_pallets and driver_checkin and not driver_in_rest
```

---

