# TV Mode

> Loading note: this is one part of the split Flow Manager guidance. Claude,
> Codex, and any assistant working in this repo must also read the root
> `CLAUDE.md` and every sibling split file under `docs/claude/**/CLAUDE.md`.
> No single split file is complete on its own.

## Scope

TV mode changes are limited to the TV surface:

- `app.py` `/api/tv/ops`, `/api/tv/version`, and `_tv_overlay()`
- `assets/tv_mode.js`
- the TV scoped blocks in `assets/style.css`
- TV-specific tests in `tests/test_kpi_page.py`

Do not change normal inbound/outbound dashboard views, KPI views, ULD flows,
settings, or developer views unless the user explicitly asks for those surfaces.

## Current Layout

The TV overlay has two pages inside `.tv-main`:

- `#tv-page-ops`: Rakodasok. This page is now a full-width truck grid only.
- `#tv-page-report`: Muszak riport. This page keeps the outbound-driven report
  gauges, report stats, hourly chart, and the small inbound shift context.

The old external-information surfaces are removed from TV mode. Do not
reintroduce a bottom information strip, news feed, sport strip, weather card, or
Rakodasok right-side expected-arrivals/sidebar. Keep TV mode data on the
existing ops/version endpoints and the two current pages.

The header and page switch remain. Page changes are handled by `tv_mode.js`;
arrow keys page the truck cards, and the page pills switch between Rakodasok and
Muszak riport.

## Rakodasok Cards

Rakodasok truck cards show only operational identity and readiness:

- plate
- GLABS line
- LMP chips
- forwarded marker
- driver state
- readiness `X/Y`
- status pills

Truck cards must not show or receive per-card `kg`, `colli`, or `paletta`
metrics. `/api/tv/ops` truck payloads should not expose `boxes`, `weight`,
`pallets`, or `colli` fields for the cards. Keep the report-page outbound
totals separate; those report metrics are not truck-card metrics.

Only non-issued BUD-Pallets / ECOMM items may appear in TV truck cards. Issued
items must be filtered out even if one source flag is stale but the status says
`Kiadva`.

## Muszak Report

The report page is outbound-driven:

- main `kg / ora` gauge
- `rakodas / ora` gauge
- outbound totals and rates panels
- current-shift hourly outbound chart
- small inbound shift context

Gauge references use stable completed-shift averages from
`data_reader._compute_outbound_shift_kpi`, not the current moving shift. Keep
`ref_kg_kind` and `ref_loadings_kind` as `"big_avg"` when those references are
available.

## Update Watchdog

`/api/tv/version` signs the currently relevant TV runtime files:

- `app.py`
- `data_reader.py`
- `assets/tv_mode.js`
- `assets/style.css`

The frontend polls this endpoint only while TV mode is open and reloads the TV
surface with a cache-busting query when the signature changes. Do not add blind
Python process restarts from the TV client.

## Validation

Use these checks after TV mode changes:

- `node --check assets\tv_mode.js`
- `python -m py_compile app.py data_reader.py data_cache.py priority_engine.py storage_manager.py config.py`
- `python -m unittest tests.test_kpi_page.KpiPageTests.test_tv_ops_report_issued_count_uses_current_shift_kpi tests.test_kpi_page.KpiPageTests.test_tv_ops_truck_cards_do_not_expose_weight_colli_or_pallet_metrics tests.test_kpi_page.KpiPageTests.test_tv_version_endpoint_returns_update_signature`

If the full test suite fails outside TV mode, do not fix unrelated failures
unless the user asks for that scope.
