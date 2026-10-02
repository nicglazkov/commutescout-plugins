"""Rebuild cameras.json: fixed speed and red light cameras in the
United States, from open data.

Sources, each fetched once per run:

- OpenStreetMap, nationwide (one Overpass query): every
  ``highway=speed_camera`` node and the device of every
  ``type=enforcement`` relation that enforces a speed limit or a traffic
  signal. Coverage is whatever volunteers have mapped.
- City open data, where a city publishes its own camera list. These are
  complete for their city, so they are what makes the map useful there:
  Chicago (red light and speed), Washington DC (red light and speed) and
  San Francisco (speed).

A city camera and an OpenStreetMap camera of the same kind within
``SAME_M`` of each other are one camera; the city's record wins, because
it carries the speed limit and the approach.

The result is the small file the plugin serves from, so the running
service never depends on any of these.

    python refresh.py            # writes cameras.json beside this file
    python refresh.py saved.json # OpenStreetMap from a saved Overpass response

Run it when the map files are refreshed (monthly). OpenStreetMap data is
(c) OpenStreetMap contributors, under the ODbL; the city datasets are
public records published by each city.
"""

from __future__ import annotations

import json
import math
import sys
import urllib.parse
import urllib.request
from pathlib import Path

OVERPASS = "https://overpass-api.de/api/interpreter"
QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="US"][admin_level=2]->.us;
(
  node["highway"="speed_camera"](area.us);
  relation["type"="enforcement"]["enforcement"~"^(maxspeed|traffic_signals)$"](area.us);
);
out body;
node(r:"device");
out skel;
"""
CHICAGO_RED = "https://data.cityofchicago.org/resource/thvf-6diy.json?$limit=5000"
CHICAGO_SPEED = "https://data.cityofchicago.org/resource/4i42-qv3h.json?$limit=5000"
DC = ("https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Public_Safety_WebMercator/"
      "MapServer/43/query?where=1%3D1&outFields=*&outSR=4326&f=json"
      "&resultOffset={offset}&resultRecordCount=1000")
SF_SPEED = ("https://data.sf.gov/resource/d5uh-bk84.json?$select=site_id,location,posted_speed,"
            "latitude,longitude&$group=site_id,location,posted_speed,latitude,longitude&$limit=5000")
USER_AGENT = "CommuteScout cameras plugin (https://commutescout.com/contact)"
OUT = Path(__file__).with_name("cameras.json")
# A refresh that comes back this much smaller than the file it replaces
# is a source having a bad day, not a real change.
MIN_KEEP = 0.8
# Two records of one kind this close together are one camera.
SAME_M = 60.0
SPEED, RED = "CAMERA_SPEED", "CAMERA_RED_LIGHT"


def limit_mph(value) -> int | None:
    """``35 mph``, ``35`` or ``35.0`` (US data means mph either way);
    None for anything else, such as ``signals`` or a km/h value."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text.endswith("mph"):
        text = text[:-3].strip()
    try:
        mph = float(text)
    except ValueError:
        return None
    return int(mph) if mph == int(mph) and 5 <= mph <= 90 else None


def meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    kx = 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lat2 - lat1) * 111_320, (lon2 - lon1) * kx)


def record(ref: str, kind: str, lat, lon, *, mph=None, where: str | None = None,
           url: str | None = None, by: str) -> dict | None:
    """One camera, or None when the source row has no usable position."""
    try:
        lat, lon = round(float(lat), 6), round(float(lon), 6)
    except (TypeError, ValueError):
        return None
    if not (18.0 <= lat <= 71.5 and -168.0 <= lon <= -66.5):
        return None
    rec = {"ref": ref, "kind": kind, "lat": lat, "lon": lon, "by": by}
    limit = limit_mph(mph)
    if limit and kind == SPEED:
        rec["mph"] = limit
    if where and where.strip():
        rec["where"] = " ".join(where.split())[:120]
    if url:
        rec["url"] = url
    return rec


def from_overpass(overpass: dict) -> list[dict]:
    nodes: dict[int, dict] = {}
    relations = []
    for el in overpass.get("elements", []):
        if el.get("type") == "node" and "lat" in el:
            # A device comes back twice, with and without its tags.
            nodes.setdefault(el["id"], {}).update(el)
        elif el.get("type") == "relation":
            relations.append(el)

    def rec(node: dict, kind: str, maxspeed) -> dict | None:
        return record(f"n{node['id']}", kind, node["lat"], node["lon"], mph=maxspeed,
                      url=f"https://www.openstreetmap.org/node/{node['id']}", by="OpenStreetMap")

    out: dict[int, dict] = {}
    for node in nodes.values():
        tags = node.get("tags") or {}
        if tags.get("highway") != "speed_camera":
            continue
        red = (tags.get("enforcement") == "traffic_signals"
               or tags.get("speed_camera") == "red_light")
        if (r := rec(node, RED if red else SPEED, tags.get("maxspeed"))):
            out[node["id"]] = r
    # A relation says what its device enforces more exactly than the
    # node's own tags do, so it wins.
    for rel in relations:
        tags = rel.get("tags") or {}
        kind = RED if tags.get("enforcement") == "traffic_signals" else SPEED
        for member in rel.get("members", []):
            node = nodes.get(member.get("ref")) if member.get("type") == "node" else None
            if member.get("role") != "device" or not node:
                continue
            if (r := rec(node, kind, tags.get("maxspeed"))):
                out[node["id"]] = r
    return list(out.values())


def from_chicago(rows: list[dict], kind: str) -> list[dict]:
    page = ("https://data.cityofchicago.org/Transportation/Red-Light-Camera-Locations/thvf-6diy"
            if kind == RED else
            "https://data.cityofchicago.org/Transportation/Speed-Camera-Locations/4i42-qv3h")
    out = []
    for i, row in enumerate(rows):
        where = row.get("intersection") or row.get("address")
        approach = " ".join(x for x in (row.get("first_approach"), row.get("second_approach"),
                                         row.get("third_approach")) if x)
        key = row.get("location_id") or row.get("id") or (where or str(i)).lower().replace(" ", "-")
        r = record(f"chi:{'rl' if kind == RED else 'sp'}:{key}", kind,
                   row.get("latitude"), row.get("longitude"),
                   where=f"{where} ({approach})" if where and approach else where,
                   url=page, by="City of Chicago")
        if r:
            out.append(r)
    return out


def from_dc(features: list[dict]) -> list[dict]:
    kinds = {"speed": SPEED, "red light": RED}
    out = []
    for f in features:
        a = f.get("attributes") or {}
        kind = kinds.get(str(a.get("ENFORCEMENT_TYPE") or "").strip().lower())
        if not kind or str(a.get("ACTIVE_STATUS") or "").lower() != "active":
            continue   # stop sign, bus lane and truck cameras are other kinds
        r = record(f"dc:{a.get('ENFORCEMENT_SPACE_CODE') or a.get('OBJECTID')}", kind,
                   a.get("CAMERA_LATITUDE"), a.get("CAMERA_LONGITUDE"), mph=a.get("SPEED_LIMIT"),
                   where=a.get("LOCATION_DESCRIPTION"),
                   url="https://opendata.dc.gov/datasets/automated-safety-cameras",
                   by="District of Columbia")
        if r:
            out.append(r)
    return out


def _street(text: str) -> str:
    """``WB 1333 BAY ST`` as ``WB 1333 Bay St``: the approach stays in capitals."""
    words = text.split()
    return " ".join(w if i == 0 and w in ("NB", "SB", "EB", "WB") else w.title()
                    for i, w in enumerate(words))


def from_sf(rows: list[dict]) -> list[dict]:
    out = {}
    for row in rows:
        if not row.get("site_id"):
            continue
        r = record(f"sf:{row['site_id']}", SPEED, row.get("latitude"), row.get("longitude"),
                   mph=row.get("posted_speed"), where=_street(row.get("location") or ""),
                   url="https://www.sfmta.com/projects/speed-safety-cameras",
                   by="City and County of San Francisco")
        if r:
            out[r["ref"]] = r
    return list(out.values())


def merge(city: list[dict], osm: list[dict]) -> list[dict]:
    """City records, plus the OpenStreetMap ones that are not the same
    camera as a city record."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for c in city:
        buckets.setdefault((round(c["lat"] * 100), round(c["lon"] * 100)), []).append(c)

    def taken(o: dict) -> bool:
        by, bx = round(o["lat"] * 100), round(o["lon"] * 100)
        return any(c["kind"] == o["kind"]
                   and meters(o["lat"], o["lon"], c["lat"], c["lon"]) <= SAME_M
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                   for c in buckets.get((by + dy, bx + dx), ()))

    return sorted(city + [o for o in osm if not taken(o)], key=lambda r: r["ref"])


def get_json(url: str, data: bytes | None = None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=240) as r:  # noqa: S310 (fixed https URLs)
        return json.load(r)


def fetch_all(saved_overpass: str | None = None) -> tuple[list[dict], str | None, dict[str, int]]:
    """Every source. One city failing loses that city, not the run; the
    size check in main() catches a run that lost too much."""
    counts: dict[str, int] = {}
    if saved_overpass:
        overpass = json.loads(Path(saved_overpass).read_text(encoding="utf-8"))
    else:
        overpass = get_json(OVERPASS, urllib.parse.urlencode({"data": QUERY}).encode())
    osm = from_overpass(overpass)
    counts["OpenStreetMap"] = len(osm)
    city: list[dict] = []

    def add(name: str, load) -> None:
        try:
            got = load()
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: skipped ({exc})", file=sys.stderr)
            got = []
        counts[name] = len(got)
        city.extend(got)

    add("Chicago red light", lambda: from_chicago(get_json(CHICAGO_RED), RED))
    add("Chicago speed", lambda: from_chicago(get_json(CHICAGO_SPEED), SPEED))

    def dc() -> list[dict]:
        rows, offset = [], 0
        while True:
            page = get_json(DC.format(offset=offset)).get("features") or []
            rows += page
            offset += len(page)
            if len(page) < 1000:
                return from_dc(rows)

    add("Washington DC", dc)
    add("San Francisco speed", lambda: from_sf(get_json(SF_SPEED)))
    as_of = (overpass.get("osm3s") or {}).get("timestamp_osm_base")
    return merge(city, osm), as_of, counts


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    found, as_of, counts = fetch_all(args[0] if args else None)
    for name, n in counts.items():
        print(f"{name}: {n}")
    if OUT.exists():
        had = len(json.loads(OUT.read_text(encoding="utf-8"))["cameras"])
        if len(found) < had * MIN_KEEP:
            print(f"refusing: {len(found)} cameras, the file has {had}", file=sys.stderr)
            return 1
    OUT.write_text(json.dumps({"as_of": as_of, "cameras": found}, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    print(f"{len(found)} cameras as of {as_of}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
