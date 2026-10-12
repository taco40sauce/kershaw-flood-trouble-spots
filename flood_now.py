"""
flood_now.py — builds flood_now.geojson, the "Now: Road closed or hazard
reported" layer of the Kershaw County Flood Trouble Spots map.

It reads up to three sources, keeps only road hazards from a storm, turns
the status codes into plain language, and writes one GeoJSON file that the
uMap layer reads as Remote data.

Sources (all optional; a missing or failing source is skipped):
  1. KCGR-RS automated feed (APRS + Winlink), public GeoJSON on GitHub
  2. KCGR-RS hand-entered reports, public GeoJSON from the Grist worker
  3. This project's own Grist doc ("Kershaw Flood Road Reports"), read
     with the Grist API

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
import json
import math
import os
import sys
from pathlib import Path

import requests

# --- Config ----------------------------------------------------------------
HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "flood_now.geojson"
STATE_DIR = HERE / "state"

HUB_FEED_URL = ("https://raw.githubusercontent.com/taco40sauce/kcgr-resource-feed/"
                "main/kcgr_resource_feed.geojson")
HUB_MANUAL_URL = "https://kcgr-grist-worker.kc4rc.workers.dev"

GRIST_SERVER = os.environ.get("GRIST_SERVER", "https://kc4rc.getgrist.com").rstrip("/")
GRIST_DOC_ID = os.environ.get("GRIST_DOC_ID", "6gttLV3qkhtsMSpvyDJQnW")
GRIST_TABLE = os.environ.get("GRIST_TABLE", "Table1")
GRIST_API_KEY = os.environ.get("GRIST_API_KEY", "")

# Road hazards shown on the flood map (Specifier codes from the KCGR vocabulary).
ROAD_CAUSES = {
    "FL": "flooding",
    "BR": "bridge or washout damage",
    "TR": "trees or debris down",
}
# Road reports whose Specifier gives no cause (CL-closed, CA-caution) are left
# out unless this is True. Pending decision; see README.
INCLUDE_ROAD_WITHOUT_CAUSE = False

STATUS_WORDS = {
    "CL": "Road closed",
    "CA": "Use caution",
    "LM": "Partly blocked",
    "UK": "Reported, not yet confirmed",
}
# OP-open is never shown: the road has reopened.

SOURCE_LABELS = {
    "flood_doc": "Flood map team report",
    "hub_manual": "KCGR team report",
    "hub_feed": "KCGR ham radio report",
}
SOURCE_PRIORITY = ["flood_doc", "hub_manual", "hub_feed"]  # tie-breaker for duplicates
SEVERITY = ["CL", "LM", "CA", "UK"]  # duplicates keep the most serious status
MERGE_RADIUS_M = 100   # same cause within ~330 ft = one pin (matches the hub)
TIMEOUT = 30


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


LOADERS = {"hub_feed": load_hub_feed, "hub_manual": load_hub_manual, "flood_doc": load_flood_doc}


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


def to_pin(feature, source):
    """Return a public pin for this report, or None if it doesn't belong on the map."""
    p = feature.get("properties", {})
    if not str(p.get("ObjectName", "")).upper().startswith("KCGR-ROAD"):
        return None
    status = code(p.get("Status"))
    cause_code = code(p.get("Specifier"))
    if status == "OP" or status not in STATUS_WORDS:
        return None
    if cause_code in ROAD_CAUSES:
        cause = ROAD_CAUSES[cause_code]
    elif INCLUDE_ROAD_WITHOUT_CAUSE:
        cause = "cause not reported"
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
        lines.append(f"Reported: {when}")
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
        pins.extend(pins_for(source))
    features = [finish(p) for p in merge(pins)]
    features.sort(key=lambda f: (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]))
    return {"type": "FeatureCollection", "features": features}


def main():
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
