"""Find where public roads cross creeks (and where roads run beside creeks)
from Census TIGER/Line roads + linear water files. Writes data/crossings.geojson
and data/beside.geojson.

Get the county files (Kershaw County SC = 45055) from the Census Bureau:
  https://www2.census.gov/geo/tiger/TIGER2024/ROADS/tl_2024_45055_roads.zip
  https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_45055_linearwater.zip
Unzip both, then from the repo root:
  pip install pyshp shapely pyproj
  python3 tools/build_crossings.py <path>/tl_2024_45055_roads <path>/tl_2024_45055_linearwater data
(The paths are the shapefile names without the .shp ending.)

UTM zone 17N is used for distances; it fits South Carolina. Change EPSG:26917
for counties in other UTM zones.
"""
import json
import sys

import shapefile
from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, Point, MultiPoint, GeometryCollection
from shapely.ops import transform
import re
import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)
from shapely.strtree import STRtree

roads_base, water_base, outdir = sys.argv[1:4]

KEEP_ROADS = {"S1100", "S1200", "S1400"}   # primary, secondary, local streets
MERGE_M = 30          # crossings closer than ~100 ft become one
BESIDE_M = float(sys.argv[4]) if len(sys.argv) > 4 else 25  # road within ~80 ft of a creek
BESIDE_CLEAR_M = 60   # skip beside-points this close to a crossing
SAMPLE_M = 75         # spacing of beside-points along a road

to_m = Transformer.from_crs("EPSG:4269", "EPSG:26917", always_xy=True).transform  # NAD83 -> UTM 17N
to_ll = Transformer.from_crs("EPSG:26917", "EPSG:4326", always_xy=True).transform


def is_big_highway(rec):
    """Interstates and US highways: nearly always bridged over creeks."""
    name = rec["FULLNAME"] or ""
    return rec["MTFCC"] == "S1100" or rec["RTTYP"] in ("I", "U") or name.startswith(("I- ", "US Hwy"))


def lines(shape):
    parts = list(shape.parts) + [len(shape.points)]
    return [LineString(shape.points[a:b]) for a, b in zip(parts[:-1], parts[1:]) if b - a >= 2]


roads = []
for sr in shapefile.Reader(roads_base).iterShapeRecords():
    rec = sr.record.as_dict()
    if rec["MTFCC"] not in KEEP_ROADS:
        continue
    for ln in lines(sr.shape):
        roads.append((transform(to_m, ln), rec))

water = []
for sr in shapefile.Reader(water_base).iterShapeRecords():
    rec = sr.record.as_dict()
    for ln in lines(sr.shape):
        water.append((transform(to_m, ln), rec))

water_tree = STRtree([g for g, _ in water])

# 1. Crossing points
raw = []          # (Point, road_name, creek_name)
skipped_big = 0
for rg, rrec in roads:
    for wi in water_tree.query(rg):
        wg, wrec = water[wi]
        inter = rg.intersection(wg)
        if inter.is_empty:
            continue
        pts = [inter] if isinstance(inter, Point) else [g for g in getattr(inter, "geoms", []) if isinstance(g, Point)]
        if not pts and not inter.is_empty:
            pts = [inter.representative_point()]
        for p in pts:
            raw.append((p, rrec["FULLNAME"] or "", wrec["FULLNAME"] or "", is_big_highway(rrec)))

# merge crossings within MERGE_M of each other (greedy)
merged = []
for p, road, creek, big in raw:
    for m in merged:
        if m["pt"].distance(p) <= MERGE_M:
            m["big"] = m["big"] or big
            if road and road not in m["roads"]:
                m["roads"].append(road)
            if creek and creek not in m["creeks"]:
                m["creeks"].append(creek)
            break
    else:
        merged.append({"pt": p, "roads": [road] if road else [], "creeks": [creek] if creek else [], "big": big})
skipped_big = sum(1 for m in merged if m["big"])
merged = [m for m in merged if not m["big"]]


ABBR = [(r"\bCrk\b", "Creek"), (r"\bBr\b", "Branch"), (r"\bExd\b", "Ext"), (r"\bExn\b", "Ext"),
        (r"\bRiv\b", "River")]


def tidy(name):
    for pat, rep in ABBR:
        name = re.sub(pat, rep, name)
    return name


def pick(names):
    """Prefer an everyday name over a state route number, but keep the route as a second name."""
    everyday = [n for n in names if not n.startswith(("State Rd S-", "State Hwy"))]
    routes = [n for n in names if n not in everyday]
    ordered = everyday[:1] + routes[:1] if everyday else routes[:1]
    return " (".join(tidy(n) for n in ordered) + (")" if len(ordered) == 2 else "")


def label(creeks, roads):
    creek = tidy(creeks[0]) if creeks else "Unnamed creek"
    road = pick(roads) if roads else "an unnamed road"
    return creek, road


crossing_features = []
for m in merged:
    creek, road = label(m["creeks"], m["roads"])
    lon, lat = to_ll(m["pt"].x, m["pt"].y)
    crossing_features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": {"name": f"{creek} crosses {road}", "creek": creek, "road": road},
    })

# 2. Road beside a creek: sample points along roads within BESIDE_M of a creek,
#    away from crossings, one point per SAMPLE_M of road.
cross_pts = MultiPoint([m["pt"] for m in merged]) if merged else None
beside = []
for rg, rrec in roads:
    if is_big_highway(rrec):
        continue
    near = [water[i] for i in water_tree.query(rg.buffer(BESIDE_M))]
    near = [(wg, wrec) for wg, wrec in near if rg.distance(wg) <= BESIDE_M]
    if not near:
        continue
    d = 0.0
    while d <= rg.length:
        p = rg.interpolate(d)
        hit = [(wg, wrec) for wg, wrec in near if p.distance(wg) <= BESIDE_M]
        if hit and (cross_pts is None or p.distance(cross_pts) > BESIDE_CLEAR_M):
            if all(p.distance(b["pt"]) > SAMPLE_M * 0.9 for b in beside[-50:]):
                creek = hit[0][1]["FULLNAME"] or "a creek"
                beside.append({"pt": p, "road": tidy(rrec["FULLNAME"] or "Unnamed road"), "creek": tidy(creek)})
        d += SAMPLE_M / 3

beside_features = []
for b in beside:
    lon, lat = to_ll(b["pt"].x, b["pt"].y)
    beside_features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]},
        "properties": {"name": f"{b['road']} runs beside {b['creek']}", "road": b["road"], "creek": b["creek"]},
    })

for fname, feats in (("crossings.geojson", crossing_features), ("beside.geojson", beside_features)):
    with open(f"{outdir}/{fname}", "w") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f)

named = sum(1 for f in crossing_features if not f["properties"]["creek"].startswith("Unnamed"))
print(f"roads kept: {len(roads)} segments; creek lines: {len(water)}")
print(f"raw crossings: {len(raw)}; merged crossings dropped on interstate/US highways: {skipped_big}")
print(f"crossings after merge: {len(crossing_features)} ({named} with creek names)")
print(f"road-beside-creek points: {len(beside_features)}")
