# Inbound Flow Manager

Portable Python/Dash alkalmazás inbound és outbound raktári folyamatok,
ULD-életciklusok és operatív KPI-k áttekintéséhez.

Ez a privát tároló a forráskódot, felületi erőforrásokat, fejlesztési
útmutatókat és automatizált teszteket tartalmazza. Üzemi adatok és hozzáférési
kulcsok nem részei a tárolónak.

Felhős fejlesztéshez és a laptop–telefon munkafolyamathoz lásd: [CLOUD.md](CLOUD.md).

Telepítés: `python -m pip install -r requirements.txt`

Biztonságos teljes teszt: `python scripts/codex-test.py`

Az alkalmazás helyi indítása és Windows-csomagolása a projekt
`docs/claude/04-ui-kpi-config-runbook/CLAUDE.md` útmutatója szerint történik.
Az éles indítás helyi adatforrás-beállítást igényel; felhőben ne indíts élő
adatfrissítést és ne tölts fel üzemi adatállományt.
