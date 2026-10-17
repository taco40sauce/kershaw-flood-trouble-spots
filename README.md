# Kershaw County Flood Trouble Spots

A plain-language public map of **roads to avoid when a flood warning is in
effect** in Kershaw County, South Carolina.

**Live map:** https://umap.openstreetmap.fr/en/map/kershaw-county-flood-trouble-spots_1471681

Built by KC4RC / Kershaw County ARES. Open source (MIT) so any county or
emergency group can set up its own copy.

## Why this exists

Flood warnings tell people *that* flooding is likely, not *where* to avoid.
The information that answers "where" (creek crossings, low roads, places
that flooded before, roads reported closed right now) is scattered across
federal datasets and agency channels. This map puts it in one place, in
plain words, for someone deciding whether to drive. Most flood deaths happen
in vehicles, so the map focuses on roads.

## What the map shows

| Group | Layer | Symbol | Source |
|---|---|---|---|
| Now | Flood warnings | Red shaded area | National Weather Service, live |
| Now | Flood watch (flooding possible) | Pale red, dashed edge | `flood_watches.geojson`, built from NWS by `flood_now.py` |
| Now | Road closed or hazard reported | Red pin | `flood_now.geojson` in this repo, updated every few minutes |
| Now | Creek crossing inside a flood warning | Orange pin | `warning_crossings.geojson`: named-creek crossings inside an active NWS warning |
| Has flooded before | Flooded in the past (verified) | Blue drop | NWS Storm Events reports (`data/history.geojson`) |
| Could flood | Creek crosses the road | Small orange dot | Census TIGER/Line roads and creeks (`data/crossings.geojson`) |
| Could flood | Road beside a creek (zoom in) | Small yellow dot | Census TIGER/Line (`data/beside.geojson`) |

Named-creek crossings show at every zoom level. Small-creek crossings and
road-beside-creek dots appear when you zoom in to neighborhood level.
Interstates and US highways are left out (nearly always bridged, and
officials close them when threatened).

## What's in this repo

| Path | What it is |
|---|---|
| `flood_now.py` | Builds `flood_now.geojson`, the red-pin layer, from SCDOT, Grist and KCGR-RS |
| `flood_now.geojson` | The live red-pin data the map reads |
| `flood_watches.geojson` | Active NWS flood watches for South Carolina (zone shapes filled in) |
| `warning_crossings.geojson` | Named-creek crossings inside active NWS flood warnings |
| `.github/workflows/flood-now.yml` | Runs `flood_now.py` and commits changes |
| `state/` | Last good pins from each source, used if a source is briefly down |
| `data/` | Fixed map data: crossings, roads beside creeks, flood history |
| `tools/build_crossings.py` | Computes crossings from Census TIGER files |
| `tools/build_map.py` | Builds `kershaw_flood_roads.umap` for import into uMap |
| `kershaw_flood_roads.umap` | The uMap import file |

## How the red-pin layer works

`flood_now.py` reads three sources and keeps only **road** reports with one
of three storm causes, using the KCGR code vocabulary:

| Specifier | Public wording |
|---|---|
| FL-flooding | flooding |
| BR-bridge damage | bridge or washout damage |
| TR-trees/debris | trees or debris down |

| Status | Public wording | Shown? |
|---|---|---|
| CL-closed | Road closed | Yes |
| LM-limited | Partly blocked | Yes |
| CA-caution | Use caution | Yes |
| UK-unknown | Reported, not yet confirmed | Only when verified |
| OP-open | (reopened) | No, the pin drops off |

Sources:
1. **SCDOT road conditions** (official, statewide feed refreshed every 10
   minutes), limited to `SCDOT_COUNTIES`. Only storm damage is kept:
   descriptions mentioning flooding, washouts, sinkholes, collapses, or trees
   and debris. Planned work (resurfacing, replacements, repairs, inspections,
   contractor work) is skipped so work zones stay off the map.
2. **This project's Grist doc**, "Kershaw Flood Road Reports," for
   flood-only reports. Shown only when verified.
3. **KCGR-RS hand-entered reports** (Grist), through the KCGR Grist worker.
   Shown only when verified.
4. **KCGR-RS automated feed** (APRS and Winlink):
   `taco40sauce/kcgr-resource-feed`, `kcgr_resource_feed.geojson`

KCGR road reports sent without a storm code are checked for one in their
notes (for example "Trees down at..." becomes *trees or debris down*). If
none is found, they appear as "cause not reported," so a missed code never
hides a blocked road. Set `INCLUDE_ROAD_WITHOUT_CAUSE = False` in
`flood_now.py` to hide them instead.

**Practice reports are skipped.** A report is left off the public map when it
carries an exercise flag (Winlink `Exercise: EXERCISE`) or when its location
or notes contain EXERCISE, DRILL, TEST, PRACTICE or SIMULATED (for example
Grist "TEST ITEM" rows). SCDOT entries are never treated as practice.

**NWS layers.** Each run also reads active NWS alerts for South Carolina.
Flood watches are written to `flood_watches.geojson`; watches usually cover
whole forecast zones, so the zone shapes are fetched and filled in. Named-creek
crossings that fall inside an active Flood Warning or Flash Flood Warning are
written to `warning_crossings.geojson` (set `NAMED_CREEKS_ONLY = False` to
include small creeks). If NWS can't be reached, the last files are kept minus
anything already expired.

Nothing is copied between databases. Each run mirrors what the sources say
at that moment. Reports of the same cause within about 330 feet are merged
into one pin, keeping the most serious status. Reporter callsigns are not
shown on this map.

## Setup

### 1. Grist key (to read the flood Grist doc)
1. In Grist, open your profile settings and create an **API key**.
2. In this repo on GitHub: **Settings → Secrets and variables → Actions →
   New repository secret.** Name: `GRIST_API_KEY`. Value: the key.

Without the key, the job still runs using the two KCGR-RS sources.

### 2. Every-5-minutes trigger (cron-job.org)
GitHub's own schedule (every 15 minutes in the workflow) is only a backup;
it often runs late. Set up a cron-job.org job like the KCGR-RS pollers use:
- **URL:** `https://api.github.com/repos/taco40sauce/kershaw-flood-trouble-spots/actions/workflows/flood-now.yml/dispatches`
- **Method:** POST, every 5 minutes
- **Headers:**
  - `Authorization: Bearer <token>` (note the word `Bearer ` and the space)
  - `Accept: application/vnd.github+json`
- **Body:** `{"ref":"main"}`
- **Token:** a GitHub fine-grained token with **Actions: read and write**
  on this repo. An existing trigger token can be edited to include this repo.

### 3. Load the map into uMap
1. `python3 tools/build_map.py` (only needed after changing data or settings).
2. In uMap, open the map and click **Edit**. **Zoom in to a neighborhood
   first**, because layers limited to closer zoom levels are greyed out and
   can't be deleted at county view.
3. Delete the existing layers (**Manage layers**, trash can on each), then **Save**.
4. Press **Ctrl+I**, choose `kershaw_flood_roads.umap`, **Import data**, then **Save**.

## Refreshing the fixed data
- **Crossings (yearly):** download the new Census TIGER roads and linear
  water files for the county and run `tools/build_crossings.py` (instructions
  at the top of the file), then rebuild and re-import the map.
- **Flood history:** add new NWS Storm Events reports to `data/history.geojson`.

## Using this for another county
1. Fork or copy this repo.
2. Download that county's Census TIGER files and run `tools/build_crossings.py`.
3. Replace `data/history.geojson` with that county's verified flood reports.
4. Edit the settings at the top of `tools/build_map.py` (map name, repo,
   center, state) and the sources at the top of `flood_now.py`.
5. Build the `.umap` file and import it into a new uMap map.

## Checks after the next real storm
- The SCDOT filter matches storm entries by their wording (SCDOT usually adds
  the event name, for example "Hurricane Isaias (October 9, 2026)"). Compare
  the map with SCDOT's own list during the next event and adjust
  `CAUSE_PATTERNS` or `SCDOT_PLANNED_WORK` in `flood_now.py` if needed.

## Limits
- A road with no mark is not guaranteed safe.
- Crossing locations come from Census line data and can be off by tens of feet.
- Some small bridges are not identified, so a few crossings may be bridged.
- The red-pin layer updates when the page is reloaded, not instantly.

## License
MIT. See `LICENSE`.
