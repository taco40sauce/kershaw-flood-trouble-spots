"""
flood_now.py — builds flood_now.geojson, the "Now: Road closed or hazard
reported" layer of the Kershaw County Flood Trouble Spots map.

It reads up to four sources, keeps only road hazards from a storm, turns
the status codes into plain language, and writes one GeoJSON file that the
uMap layer reads as Remote data.

Sources (a missing or failing source is skipped):
  1. SCDOT road conditions (official closures, statewide KML), filtered to
     storm damage in SCDOT_COUNTIES; planned work zones are skipped   [ON]
  2. This project's own Grist doc ("Kershaw Flood Road Reports"), read
     with the Grist API. The only way people change the flood map.     [ON]
  3. KCGR-RS hand-entered reports, public GeoJSON from the Grist worker [OFF]
  4. KCGR-RS automated feed (APRS + Winlink), public GeoJSON on GitHub  [OFF]
  On/off is set in SOURCE_ENABLED. Sources 3 and 4 were switched off on
  Oct 17, 2026; their code is kept so they can be switched back on.

Nothing is copied between databases. Each run mirrors what the sources say
right now, so a road marked open (OP) drops off the map on the next run.

If a source fails to load, its last good pins (saved in state/) are used
instead, so a brief outage doesn't wipe pins off the map. Only public pins are
saved there, never raw records, because this repo is public.

Run locally:   python3 flood_now.py
Environment variables (all optional):
  GRIST_API_KEY   key for reading the flood Grist doc (GitHub secret)
  GRIST_SERVER    default https://kc4rc.getgrist.com
  GRIST_DOC_ID    default 6gttLV3qkhtsMSpvyDJQnW
  GRIST_TABLE     default Table1
"""
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

# --- Config ----------------------------------------------------------------
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "flood_now.geojson"
STATE_DIR = HERE / "state"

HUB_FEED_URL = ("https://raw.githubusercontent.com/taco40sauce/kcgr-resource-feed/"
                "main/kcgr_resource_feed.geojson")
HUB_MANUAL_URL = "https://kcgr-grist-worker.kc4rc.workers.dev"

# SCDOT road conditions (statewide KML, refreshed by SCDOT every 10 minutes).
# https://www.scdot.org/travel/travel-roadconditions.aspx
SCDOT_URL = "https://ris.scdot.org/roadconditions/"
SCDOT_COUNTIES = {"KERSHAW"}   # add neighbors here, in capitals, e.g. "LEE"

# National Weather Service alerts (used for watches and for crossings inside a warning).
NWS_AREA = "SC"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"
NWS_HEADERS = {"User-Agent": "kershaw-flood-trouble-spots (github.com/taco40sauce/kershaw-flood-trouble-spots)",
               "Accept": "application/geo+json"}
WARNING_EVENTS = ("Flash Flood Warning", "Flood Warning")
WATCH_EVENTS = ("Flood Watch", "Flash Flood Watch")
WATCHES_OUT = HERE / "flood_watches.geojson"
WARNING_CROSSINGS_OUT = HERE / "warning_crossings.geojson"
CROSSINGS_FILE = HERE / "data" / "crossings.geojson"
NAMED_CREEKS_ONLY = True   # crossings inside a warning: named creeks only (set False for all)

GRIST_SERVER = os.environ.get("GRIST_SERVER", "https://kc4rc.getgrist.com").rstrip("/")
GRIST_DOC_ID = os.environ.get("GRIST_DOC_ID", "6gttLV3qkhtsMSpvyDJQnW")
GRIST_TABLE = os.environ.get("GRIST_TABLE", "Table1")
GRIST_API_KEY = os.environ.get("GRIST_API_KEY", "")

# Road hazards shown on the flood map (Specifier codes from the KCGR vocabulary).
ROAD_CAUSES = {
    "FL": "flooding",
    "BR": "bridge or washout damage",
    "TR": "trees or debris down",
    "PL": "power lines down",
}
# KCGR road reports whose Specifier gives no storm cause (CL-closed, CA-caution,
# OP-passable with a limited status): the notes are checked for a cause first;
# if none is found they are shown as "cause not reported". Set False to hide them.
INCLUDE_ROAD_WITHOUT_CAUSE = True

# Words that identify a storm cause in free text (KCGR notes and SCDOT descriptions).
CAUSE_PATTERNS = [
    ("FL", re.compile(r"flood|high water|water over|water on (the )?road|under ?water|inundat|"
                      r"standing water|ponding", re.I)),
    ("BR", re.compile(r"wash ?out|washed out|sinkhole|undermin|collaps|"
                      r"bridge (is )?(out|damaged|failure)|culvert (failure|failed|collaps)", re.I)),
    ("PL", re.compile(r"power ?lines?|lines? down|wires? down|downed (power )?lines?", re.I)),
    ("TR", re.compile(r"\btrees?\b|debris|\blimbs?\b|downed", re.I)),
]
# SCDOT entries mentioning planned work are skipped, so work zones stay off the map.
SCDOT_PLANNED_WORK = re.compile(
    r"resurfac|replac|repair|install|construct|paving|patching|inspection|storm drain|"
    r"water ?line|sewer|maintenance|widening|utility|striping|roundabout|rest area|shoulder work|"
    r"shoulder closure|contractor|asphalt|operating under|stations? \d+\+\d+",
    re.I)
SCDOT_STATUS = {"Closed": "CL-closed", "Lane(s) blocked": "LM-limited", "Caution": "CA-caution"}

# Practice reports never appear on the public map.
# 1) an exercise flag, if the source carries one (Winlink "Exercise:" line), or
# 2) a marker word in the location or notes (e.g. Grist "TEST ITEM" rows).
EXERCISE_FIELDS = ("Exercise", "IsExercise", "is_exercise", "exercise")
EXERCISE_VALUES = {"EXERCISE", "YES", "TRUE", "DRILL", "1"}
EXERCISE_WORDS = re.compile(r"\b(exercise|drill|test|testing|practice|simulated|simulation)\b", re.I)

STATUS_WORDS = {
    "CL": "Road closed",
    "CA": "Use caution",
    "LM": "Partly blocked",
    "UK": "Reported, not yet confirmed",
}
# OP-open is never shown: the road has reopened.

SOURCE_LABELS = {
    "scdot": "SCDOT road conditions",
    "flood_doc": "Flood map team report",
    "hub_manual": "KCGR team report",
    "hub_feed": "KCGR ham radio report",
}
SOURCE_PRIORITY = ["scdot", "flood_doc", "hub_manual", "hub_feed"]  # tie-breaker for duplicates

# Which road sources feed the flood map.
# Automatic official sources (SCDOT) and the flood Grist doc are on.
# The KCGR hub sources (APRS/Winlink radio feed and KCGR hub Grist entries) are
# OFF by decision on Oct 17, 2026: the flood Grist doc is the only way people
# change the flood map. Their code is kept here; set True to switch one back on.
SOURCE_ENABLED = {
    "scdot": True,        # SC Dept. of Transportation road conditions (official, automatic)
    "flood_doc": True,    # Flood Grist doc "Kershaw Flood Road Reports" (manual)
    "hub_manual": False,  # KCGR hub Grist entries (off)
    "hub_feed": False,    # KCGR hub APRS/Winlink feed (off)
}
SEVERITY = ["CL", "LM", "CA", "UK"]  # duplicates keep the most serious status
MERGE_RADIUS_M = 100   # same cause within ~330 ft = one pin (matches the hub)
TIMEOUT = 60


# --- Loading -----------------------------------------------------------------
def _features_from_geojson(data):
    return [f for f in data.get("features", []) if f.get("geometry")]


def load_hub_feed():
    r = requests.get(HUB_FEED_URL, timeout=TIMEOUT)
    r.raise_for_status()
    return _features_from_geojson(r.json())


def load_hub_manual():
    r = requests.get(HUB_MANUAL_URL, timeout=TIMEOUT)
    r.raise_for_status()
    return _features_from_geojson(r.json())


def load_flood_doc():
    url = f"{GRIST_SERVER}/api/docs/{GRIST_DOC_ID}/tables/{GRIST_TABLE}/records"
    headers = {"Authorization": f"Bearer {GRIST_API_KEY}"} if GRIST_API_KEY else {}
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    feats = []
    for rec in r.json().get("records", []):
        f = rec.get("fields", {})
        lat, lon = f.get("Latitude"), f.get("Longitude")
        if lat in (None, "") or lon in (None, ""):
            continue
        props = dict(f)
        props.pop("Latitude", None)
        props.pop("Longitude", None)
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                      "properties": props})
    return feats


def cause_from_text(text):
    """Return the KCGR cause code (FL/BR/TR) a piece of free text points to, or None."""
    for cause_code, pattern in CAUSE_PATTERNS:
        if pattern.search(text or ""):
            return cause_code
    return None


def _scdot_field(placemark, label):
    m = re.search(r"<b>" + re.escape(label) + r":</b>(.*?)<br", placemark, re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""


def parse_scdot_kml(text):
    """Turn SCDOT's road-conditions KML into KCGR-style road reports.

    The KML is read with patterns rather than an XML parser because the feed is
    not always well-formed. One condition can span several Placemarks (road
    segments); they are grouped and pinned at the middle of the whole stretch.
    Only storm damage in SCDOT_COUNTIES is kept.
    """
    groups = {}
    for pm in re.findall(r"<Placemark\b.*?</Placemark>", text, re.S):
        if _scdot_field(pm, "County").upper() not in SCDOT_COUNTIES:
            continue
        style = (re.search(r"<styleUrl>#(.*?)</styleUrl>", pm) or [None, ""])[1]
        if style not in SCDOT_STATUS:
            continue
        m = re.search(r"<b>Description:</b>(.*?)(?:<br/>GPS:|</span></p>|$)", pm, re.S)
        desc = html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).strip() if m else ""
        desc = re.sub(r"\s+", " ", desc)
        if SCDOT_PLANNED_WORK.search(desc):
            continue
        cause_code = cause_from_text(desc)
        if not cause_code:
            continue
        coords = []
        for block in re.findall(r"<coordinates>(.*?)</coordinates>", pm, re.S):
            for triple in block.split():
                parts = triple.split(",")
                if len(parts) >= 2:
                    try:
                        coords.append((float(parts[0]), float(parts[1])))
                    except ValueError:
                        pass
        if not coords:
            continue
        key = re.search(r'<Placemark id="([^"]*)"', pm)
        key = key.group(1) if key else (_scdot_field(pm, "Road Name") + desc)
        g = groups.setdefault(key, {"pm": pm, "desc": desc, "style": style,
                                    "cause": cause_code, "coords": []})
        g["coords"].extend(coords)

    feats = []
    for g in groups.values():
        pm = g["pm"]
        lon, lat = g["coords"][len(g["coords"]) // 2]
        road = _scdot_field(pm, "Road Name") or _scdot_field(pm, "Route")
        span = re.search(r"<br/><br/>(From .*?)<br/>", pm, re.S)
        location = road + (f": {html.unescape(span.group(1)).strip()}" if span else "")
        dates = re.search(r"(\d{2}/\d{2}/\d{4})\s*-", pm)
        detail = g["desc"].split(" . ", 1)[-1].strip()
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "ObjectName": "KCGR-ROAD-road/bridge",
                "Status": SCDOT_STATUS[g["style"]],
                "Specifier": g["cause"],
                "Location": location,
                "Notes": detail,
                "DateTime": f"since {dates.group(1)}" if dates else "",
            },
        })
    return feats


def load_scdot():
    r = requests.get(SCDOT_URL, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_scdot_kml(r.text)


LOADERS = {"scdot": load_scdot, "hub_feed": load_hub_feed, "hub_manual": load_hub_manual,
           "flood_doc": load_flood_doc}


# --- Filtering and wording ---------------------------------------------------
def code(value):
    """'CL-closed' -> 'CL'; handles None and stray spaces."""
    return (str(value or "").strip().split("-")[0] or "").upper()


def is_verified(props):
    v = props.get("Verified")
    return v is True or str(v).strip().lower() in ("yes", "true", "1")


def fmt_time(raw):
    """KCGR DDHHMM -> 'day 14, 21:18'. Anything else is shown as-is."""
    s = str(raw or "").strip()
    if len(s) == 6 and s.isdigit():
        return f"day {s[:2]}, {s[2:4]}:{s[4:]}"
    if len(s) == 4 and s.isdigit():
        return f"day {s[:2]}, {s[2:]}:00"
    return s


def is_exercise(props, source):
    """True for a practice report. SCDOT entries are official and never treated as practice."""
    if source == "scdot":
        return False
    for field in EXERCISE_FIELDS:
        if str(props.get(field) or "").strip().upper() in EXERCISE_VALUES:
            return True
    text = " ".join(str(props.get(k) or "") for k in ("Location", "Notes", "Comment"))
    return bool(EXERCISE_WORDS.search(text))


def to_pin(feature, source):
    """Return a public pin for this report, or None if it doesn't belong on the map."""
    p = feature.get("properties", {})
    if not str(p.get("ObjectName", "")).upper().startswith("KCGR-ROAD"):
        return None
    if is_exercise(p, source):
        return None
    status = code(p.get("Status"))
    cause_code = code(p.get("Specifier"))
    if status == "OP" or status not in STATUS_WORDS:
        return None
    if cause_code not in ROAD_CAUSES:
        # No storm code: look for one in the notes, e.g. "Trees down at..."
        cause_code = cause_from_text(str(p.get("Notes") or p.get("Comment") or "")) or cause_code
    if cause_code in ROAD_CAUSES:
        cause = ROAD_CAUSES[cause_code]
    elif INCLUDE_ROAD_WITHOUT_CAUSE:
        cause = "cause not reported"
        cause_code = "NONE"
    else:
        return None
    # Unconfirmed reports only appear once someone has verified them.
    if status == "UK" and not is_verified(p):
        return None
    # Hand-entered reports only appear once verified (radio reports are
    # corroborated by the hub's own merge step).
    if source in ("hub_manual", "flood_doc") and not is_verified(p):
        return None

    lon, lat = feature["geometry"]["coordinates"][:2]
    location = str(p.get("Location") or "").strip()
    details = str(p.get("Notes") or p.get("Comment") or "").strip()
    lines = []
    if location:
        lines.append(f"**{location}**")
    if details:
        lines.append(details)
    when = fmt_time(p.get("DateTime"))
    if when:
        lines.append(when[0].upper() + when[1:] if when.startswith("since") else f"Reported: {when}")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(float(lon), 6), round(float(lat), 6)]},
        "properties": {
            "name": f"{STATUS_WORDS[status]}: {cause}",
            "status": STATUS_WORDS[status],
            "cause": cause,
            "_cause_code": cause_code,
            "_status_code": status,
            "_sources": [source],
            "_lines": lines,
        },
    }


def meters(a, b):
    (lon1, lat1), (lon2, lat2) = a, b
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def merge(pins):
    """Same cause within MERGE_RADIUS_M = one pin. The most serious status wins
    (closed beats caution), then the source order in SOURCE_PRIORITY."""
    pins.sort(key=lambda f: (SEVERITY.index(f["properties"]["_status_code"]),
                             SOURCE_PRIORITY.index(f["properties"]["_sources"][0])))
    kept = []
    for pin in pins:
        here = pin["geometry"]["coordinates"]
        for k in kept:
            if (k["properties"]["_cause_code"] == pin["properties"]["_cause_code"]
                    and meters(here, k["geometry"]["coordinates"]) <= MERGE_RADIUS_M):
                src = pin["properties"]["_sources"][0]
                if src not in k["properties"]["_sources"]:
                    k["properties"]["_sources"].append(src)
                break
        else:
            kept.append(pin)
    return kept


def finish(pin):
    p = pin["properties"]
    sources = ", ".join(SOURCE_LABELS[s] for s in p["_sources"])
    lines = p["_lines"] + [f"Source: {sources}"]
    return {
        "type": "Feature",
        "geometry": pin["geometry"],
        "properties": {"name": p["name"], "status": p["status"], "cause": p["cause"],
                       "description": "\n".join(lines)},
    }


# --- NWS alerts -----------------------------------------------------------------
def _get_json(url, **params):
    r = requests.get(url, params=params or None, headers=NWS_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_alerts(events):
    """Active NWS alerts of these event types for NWS_AREA, each with a geometry.

    Storm-based alerts (most warnings) carry their own polygon. Zone-based alerts
    (most watches, some warnings) have none, so their forecast/county zones are
    fetched and used as the shape."""
    from shapely.geometry import shape, mapping
    from shapely.ops import unary_union

    data = _get_json(NWS_ALERTS_URL, area=NWS_AREA, event=",".join(events))
    zone_cache = {}
    alerts = []
    for f in data.get("features", []):
        props = f.get("properties", {})
        geom = shape(f["geometry"]) if f.get("geometry") else None
        if geom is None:
            parts = []
            for zone_url in props.get("affectedZones", []):
                if zone_url not in zone_cache:
                    try:
                        z = _get_json(zone_url)
                        zone_cache[zone_url] = shape(z["geometry"]) if z.get("geometry") else None
                    except Exception as exc:
                        print(f"[nws] zone {zone_url} failed: {exc}")
                        zone_cache[zone_url] = None
                if zone_cache[zone_url] is not None:
                    parts.append(zone_cache[zone_url])
            geom = unary_union(parts) if parts else None
        if geom is None or geom.is_empty:
            continue
        alerts.append({
            "geometry": geom,
            "geojson": mapping(geom.simplify(0.002, preserve_topology=True)),
            "event": props.get("event", ""),
            "headline": props.get("headline") or "",
            "expires": props.get("ends") or props.get("expires") or "",
            "area": props.get("areaDesc", ""),
            "instruction": (props.get("instruction") or "").strip(),
        })
    return alerts


def _not_expired(feature):
    exp = feature.get("properties", {}).get("expires", "")
    try:
        return datetime.fromisoformat(exp) > datetime.now(timezone.utc)
    except ValueError:
        return True


def _keep_old_unexpired(path):
    """NWS unreachable: keep the last file but drop anything already expired."""
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    old = json.loads(path.read_text())
    old["features"] = [f for f in old.get("features", []) if _not_expired(f)]
    return old


def build_watches(alerts):
    feats = []
    for a in alerts:
        feats.append({"type": "Feature", "geometry": a["geojson"],
                      "properties": {"name": a["event"], "event": a["event"],
                                     "headline": a["headline"], "expires": a["expires"],
                                     "area": a["area"]}})
    return {"type": "FeatureCollection", "features": feats}


def build_warning_crossings(alerts):
    from shapely.geometry import Point
    from shapely.prepared import prep

    crossings = json.loads(CROSSINGS_FILE.read_text())["features"]
    if NAMED_CREEKS_ONLY:
        crossings = [c for c in crossings if not c["properties"]["creek"].startswith("Unnamed")]
    prepared = [(prep(a["geometry"]), a) for a in alerts]
    feats = []
    for c in crossings:
        pt = Point(c["geometry"]["coordinates"])
        for pg, a in prepared:
            if pg.contains(pt):
                feats.append({"type": "Feature", "geometry": c["geometry"],
                              "properties": {"name": c["properties"]["name"],
                                             "event": a["event"], "expires": a["expires"]}})
                break
    return {"type": "FeatureCollection", "features": feats}


def write_if_changed(path, data, label):
    text = json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text() == text:
        print(f"{label}: no change ({len(data['features'])})")
        return
    path.write_text(text)
    print(f"{label}: wrote {path.name} ({len(data['features'])})")


def update_nws_layers():
    try:
        watches = fetch_alerts(WATCH_EVENTS)
        warnings = fetch_alerts(WARNING_EVENTS)
        print(f"[nws] {len(watches)} watches, {len(warnings)} warnings in {NWS_AREA}")
        write_if_changed(WATCHES_OUT, build_watches(watches), "flood watches")
        write_if_changed(WARNING_CROSSINGS_OUT, build_warning_crossings(warnings),
                         "crossings inside a warning")
    except Exception as exc:
        print(f"[nws] FAILED ({exc}); keeping last files, minus anything expired")
        write_if_changed(WATCHES_OUT, _keep_old_unexpired(WATCHES_OUT), "flood watches")
        write_if_changed(WARNING_CROSSINGS_OUT, _keep_old_unexpired(WARNING_CROSSINGS_OUT),
                         "crossings inside a warning")


# --- Main ----------------------------------------------------------------------
def pins_for(source):
    """Load one source and turn it into public pins. The pins (never the raw
    records, which can include unverified reports) are saved in state/ so a
    later failed load can fall back to them."""
    cache = STATE_DIR / f"{source}.json"
    try:
        feats = LOADERS[source]()
    except Exception as exc:  # network error, bad JSON, auth failure...
        if cache.exists():
            pins = json.loads(cache.read_text())
            print(f"[{source}] FAILED ({exc}); using last good pins ({len(pins)})")
            return pins
        print(f"[{source}] FAILED ({exc}); no saved copy, skipping")
        return []
    pins = []
    for feature in feats:
        try:
            pin = to_pin(feature, source)
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            print(f"[{source}] skipped a malformed record: {exc}")
            continue
        if pin:
            pins.append(pin)
    STATE_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")
    print(f"[{source}] {len(feats)} records, {len(pins)} on the flood map")
    return pins


def build():
    pins = []
    for source in SOURCE_PRIORITY:
        if not SOURCE_ENABLED.get(source, False):
            print(f"[{source}] switched off (SOURCE_ENABLED)")
            continue
        pins.extend(pins_for(source))
    features = [finish(p) for p in merge(pins)]
    features.sort(key=lambda f: (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]))
    return {"type": "FeatureCollection", "features": features}


def main():
    update_nws_layers()
    out = build()
    text = json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    old = OUTPUT.read_text() if OUTPUT.exists() else ""
    if text == old:
        print(f"no change ({len(out['features'])} pins)")
        return 0
    OUTPUT.write_text(text)
    print(f"wrote {OUTPUT.name}: {len(out['features'])} pins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
