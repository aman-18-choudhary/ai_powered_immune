# Phase 10 report: complaint hotspots

Date: 2026-10-19. Branch `phase10-geo`, on top of `06ce8d2` (Phase 8).

> Counts are of **complaints**, not of offences or offenders. Coordinates are optional: only the
> complaints whose complainants shared a location appear on the map. The rest are counted by
> the state and city they typed.

## 1. What was built

* **API: `GET /geo/complaints/hotspots`** (INVESTIGATOR, ADMIN; citizens 403, anonymous 401).
  * Counts per latitude/longitude grid cell; cell sizes 0.1, 0.25 (default), 0.5 and 1.0 degrees.
  * Each cell returns its centre and bounds, never a complaint's own coordinates.
  * Each cell carries the count, categories, risk from the latest analysis (or `not_scored`), and
    first and last report time.
  * Filters: dates, category, risk level, bounding box, minimum count.
  * `compare=true` (needs `from`) adds the count and change for the equal period before `from`.
  * Complaints without coordinates are counted by state and city.
  * Totals and `truncated` flags are included. Bounds: 50,000 complaints read, 500 cells,
    100 places.
* **Privacy:**
  * Deleted complaints and complaints whose owner turned off case matching are not counted, the
    same rule as the network and Fraud DNA. Hotspots therefore undercount; the shortfall is not
    shown, so no excluded complaint can be inferred.
  * Nothing is geocoded.
  * No map, tile or geocoding service is contacted by the server or the console.
* **Console: `/investigator/map`** (staff nav entry "Map").
  * Uses the bundled India map (d3-geo, no network).
  * Circles are sized by count and coloured by change when comparing (red more, green fewer,
    blue unchanged).
  * Cells can be selected by keyboard, with a breakdown panel.
  * A cell list, a by-place list for complaints without coordinates, filters and a privacy note.
  * Reading the complaints themselves stays in the queue (filter by city or state).
* **Deviation from the earlier plan:** Leaflet with OpenStreetMap tiles was not used. Tiles would
  reveal the viewed area to a third party and would not work offline. The bundled map needs
  neither.
* **Found during validation and fixed:**
  * An inline link was distinguished by colour only (axe `link-in-text-block`).
  * `GET /complaints/{id}/similar` (Phase 8) was in the 10-per-minute analysis rate bucket, and the
    panel's polling while the model loads hit it (429). It is now in the read bucket, with a
    regression test.

## 2. Tests (2026-10-19)

| Suite | Result |
|---|---|
| `tests/test_geo.py` | 7 passed: access, grid maths and centres, filters, places without coordinates, opt-out and deletion not counted, period comparison, validation (8 invalid inputs, all 422) |
| Console `map.test.tsx` | 5 passed: drawing, keyboard selection, filters and comparison rule, query string, empty state |
| `scripts/test_all.sh`, Python 3.13 | 17/17 packages (complaints 624 passed, 8 skipped; console 297) |
| CI equivalent, Python 3.11, fresh clone, `TZ=UTC` | 16/16 packages, `pip check` clean (complaints 619 passed, 13 skipped) |
| Playwright, all specs, fresh local server with the real model | 26/26 |

The browser test gives each run a map cell of its own. It checks that exactly A, B and C are
counted there: not D (opted out) and not the deleted complaint. It also checks that the returned
centre is the cell centre, and drives the form's location button with an emulated geolocation.

## 3. Limitations

* **Coverage:** most complaints have no coordinates (they are optional), so the map shows only part
  of the reports. Free-text places are counted, not geocoded, so spelling variants of one city
  count separately.
* **Grid:** a fixed grid splits a hotspot that straddles a cell edge.
* **Scale:** aggregation runs in Python over up to 50,000 rows per request. A larger deployment
  would need SQL-side grouping or PostGIS.
* **Undercount:** opted-out complaints are not counted (privacy over completeness, by design).
