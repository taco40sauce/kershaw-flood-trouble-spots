"""
build_map.py — builds kershaw_flood_roads.umap, the uMap import file for the
Kershaw County Flood Trouble Spots map.

Inputs (in data/):
  crossings.geojson  road-creek crossings  (made by tools/build_crossings.py)
  beside.geojson     roads beside a creek  (made by tools/build_crossings.py)
  history.geojson    verified past flooding (hand-checked from NWS Storm Events)

Live layers (loaded by uMap each time the map opens):
  NWS flood warnings                      api.weather.gov
  Now: Road closed or hazard reported     flood_now.geojson in this repo,
                                          refreshed by flood_now.py

Run from the repo root:  python3 tools/build_map.py
Then import kershaw_flood_roads.umap into uMap (see README).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "kershaw_flood_roads.umap"

# Change these if the repo or map is copied for another county.
MAP_NAME = "Kershaw County Flood Trouble Spots"
GITHUB_REPO = "taco40sauce/kershaw-flood-trouble-spots"
CENTER = [-80.650, 34.240]   # lon, lat: Wateree River between Camden and Lugoff
START_ZOOM = 13
NWS_STATE = "SC"
CREDIT = "Prototype by KC4RC / Kershaw County ARES."

RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
NOW_URL = f"{RAW}/flood_now.geojson"
WATCHES_URL = f"{RAW}/flood_watches.geojson"
WARNING_CROSSINGS_URL = f"{RAW}/warning_crossings.geojson"
NWS_ALERTS = (f"https://api.weather.gov/alerts/active?area={NWS_STATE}"
              "&event=Flash%20Flood%20Warning,Flood%20Warning")

# Symbol colors. Red is reserved for "happening now".
RED = "#d7191c"      # Now
BLUE = "#2c7bb6"     # Has flooded before
ORANGE = "#e66101"   # Could flood: creek crosses road
YELLOW = "#f2b701"   # Could flood: road beside creek

NEVER_DRIVE = ("Never drive into water over a road. Twelve inches of moving water "
               "can carry away most cars. Turn around, don't drown.")
COMMON = {"displayOnLoad": True, "browsable": True, "inCaption": True}


def load(name):
    return json.loads((DATA / name).read_text())["features"]


crossings = load("crossings.geojson")
named = [f for f in crossings if not f["properties"]["creek"].startswith("Unnamed")]
unnamed = [f for f in crossings if f["properties"]["creek"].startswith("Unnamed")]
crossing_popup = ("# Creek crosses the road\n**{name}**\n\nIn heavy rain, water can cover "
                  "the road here.\n\n" + NEVER_DRIVE)

layers = [
    {
        "type": "FeatureCollection", "features": [],
        "properties": {
            **COMMON,
            "name": "Now: Flood watch (flooding possible)",
            "description": "Areas under a National Weather Service Flood Watch or Flash Flood Watch: "
                           "flooding is possible, so be ready. Blank when no watch is in effect. "
                           "Updated every few minutes from NWS.",
            "color": RED, "fillOpacity": 0.07, "weight": 1, "dashArray": "6,6", "opacity": 0.6,
            "popupShape": "Panel",
            "popupContentTemplate": "# {event}\n{headline}\n\nUntil: {expires}\n\nFlooding is "
                                    "possible. Check this map again before you drive.",
            "remoteData": {"url": WATCHES_URL, "format": "geojson", "dynamic": False,
                           "licence": "NOAA / National Weather Service (public domain)"},
        },
    },
    {
        "type": "FeatureCollection", "features": [],
        "properties": {
            **COMMON,
            "name": "Now: Flood warnings (live from NWS)",
            "description": "Flash Flood and Flood Warning areas from the National Weather Service. "
                           "Blank when no warning is in effect. Flood watches cover whole "
                           "counties and are not drawn.",
            "color": RED, "fillOpacity": 0.25, "weight": 2,
            "popupShape": "Panel",
            "popupContentTemplate": "# {event}\n{headline}\n\nUntil: {expires}\n\n{instruction}",
            "remoteData": {"url": NWS_ALERTS, "format": "geojson", "dynamic": False,
                           "licence": "NOAA / National Weather Service (public domain)"},
        },
    },
    {
        "type": "FeatureCollection", "features": [],
        "properties": {
            **COMMON,
            "name": "Now: Road closed or hazard reported",
            "description": "Roads reported closed or unsafe during an event because of flooding, "
                           "bridge or washout damage, or trees and debris. Reports come from KCGR "
                           "ham radio operators and verified team entries. Empty when no event is "
                           "under way. Reload the page for the latest reports.",
            "color": RED, "iconClass": "Default",
            "popupContentTemplate": "# {name}\n{description}\n\n" + NEVER_DRIVE,
            "remoteData": {"url": NOW_URL, "format": "geojson", "dynamic": False},
        },
    },
    {
        "type": "FeatureCollection", "features": [],
        "properties": {
            **COMMON,
            "name": "Now: Creek crossing inside a flood warning",
            "description": "Named-creek crossings inside an active National Weather Service flood "
                           "warning. Avoid these roads. Blank when no warning is in effect.",
            "color": ORANGE, "iconClass": "Default",
            "popupContentTemplate": "# Creek crossing inside a flood warning\n**{name}**\n\n"
                                    "This crossing is inside an active {event}. Water can cover "
                                    "the road here within minutes.\n\n" + NEVER_DRIVE,
            "remoteData": {"url": WARNING_CROSSINGS_URL, "format": "geojson", "dynamic": False},
        },
    },
    {
        "type": "FeatureCollection", "features": load("history.geojson"),
        "properties": {
            **COMMON,
            "name": "Before: Flooded in the past (verified)",
            "description": "Places that have actually flooded, from NWS Storm Events reports "
                           "for Kershaw County, 1996 to mid-2026. Six more reports covered broad areas "
                           "(eastern county, Elgin and Lugoff low areas, southwest county roads, "
                           "east of Camden) and are not pinned. Add new pins with the date and source.",
            "color": BLUE, "iconClass": "Drop",
            "popupContentTemplate": "# {name}\n{description}",
        },
    },
    {
        "type": "FeatureCollection", "features": named,
        "properties": {
            **COMMON,
            "name": "Could flood: Named creek crosses the road",
            "description": "A public road crosses a named creek. Named creeks are usually larger "
                           "and rise faster in heavy rain. Interstates and US highways are left out. "
                           "Source: U.S. Census Bureau TIGER/Line 2024 roads and water (creeks from USGS).",
            "color": ORANGE, "iconClass": "Circle",
            "popupContentTemplate": crossing_popup,
        },
    },
    {
        "type": "FeatureCollection", "features": unnamed,
        "properties": {
            **COMMON,
            "name": "Could flood: Small creek crosses the road (zoom in)",
            "description": "A public road crosses a small unnamed creek. Small creeks can flood "
                           "quickly. Shows when you zoom in to neighborhood level.",
            "color": ORANGE, "iconClass": "Circle", "fromZoom": 13,
            "popupContentTemplate": crossing_popup,
        },
    },
    {
        "type": "FeatureCollection", "features": load("beside.geojson"),
        "properties": {
            **COMMON,
            "name": "Could flood: Road beside a creek (zoom in)",
            "description": "The road runs within about 80 feet of a creek. Shows when you zoom in "
                           "to neighborhood level. Source: U.S. Census Bureau TIGER/Line 2024.",
            "color": YELLOW, "iconClass": "Circle", "fromZoom": 13,
            "popupContentTemplate": "# Road beside a creek\n**{name}**\n\nThis road is low and "
                                    "close to a creek. It can flood in heavy rain.\n\n" + NEVER_DRIVE,
        },
    },
]

description = (
    "**Roads to avoid when a flood warning is in effect.**\n\n"
    "## Now\n"
    "**Red shaded area:** a flood warning is in effect.\n"
    "**Orange pin:** a creek crossing inside the warning area. Avoid it.\n"
    "**Pale red area with a dashed edge:** a flood watch. Flooding is possible; be ready.\n"
    "**Red pin:** road closed or unsafe right now: flooding, washout, or trees down. "
    "Empty when no event is under way.\n\n"
    "## Has flooded before\n"
    "**Blue drop:** a place that has flooded in the past (verified).\n\n"
    "## Could flood\n"
    "**Small orange dot:** a creek crosses the road. Zoom in to see crossings of small creeks too.\n"
    "**Small yellow dot:** the road runs beside a creek. Zoom in to see these.\n\n"
    "**Greyed-out layers:** layers marked \"(zoom in)\" only show when you are zoomed in close. "
    "Zoom in (tap + or spread two fingers) until they turn dark. A layer turned off with its eye "
    "icon also looks grey; tap the eye to turn it back on.\n\n"
    "**Orange dots inside a red warning area are the most dangerous.** Creek crossings "
    "can go underwater within minutes of heavy rain.\n\n"
    "Tap any symbol for details. Reload the page for the latest reports.\n\n"
    "A road with no mark is not guaranteed safe. If water covers the road, turn around. "
    "Follow NWS Columbia and Kershaw County Emergency Management.\n\n"
    "Interstate and US highway bridges are not marked. Officials close those roads when "
    "they are threatened.\n\n"
    "Use the locate button to jump to where you are.\n\n"
    + CREDIT
)

umap = {
    "type": "umap",
    "geometry": {"type": "Point", "coordinates": CENTER},
    "properties": {
        "name": MAP_NAME,
        "description": description,
        "zoom": START_ZOOM,
        "onLoadPanel": "caption",
        "captionBar": True,
        "datalayersControl": True,
        "tilelayersControl": True,
        "scaleControl": True,
        "searchControl": True,
        "locateControl": True,
        "fullscreenControl": True,
        "scrollWheelZoom": True,
        "displayPopupFooter": False,
        "tilelayer": {
            "name": "USGS Topo (elevation contours)",
            "url_template": "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
            "attribution": "USGS The National Map",
            "minZoom": 0, "maxZoom": 16,
        },
    },
    "layers": layers,
}

OUT.write_text(json.dumps(umap, indent=1))
print(f"wrote {OUT.name}: " + ", ".join(f"{l['properties']['name']} ({len(l['features'])})" for l in layers))
