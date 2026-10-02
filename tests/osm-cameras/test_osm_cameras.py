"""osm-cameras: OpenStreetMap and the city lists become the right
records, the two are merged without counting a camera twice, and the
service is a conforming, read-only Flare plugin with a snapshot."""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest
from ca_roads import flare

HERE = Path(__file__).resolve().parents[2] / "osm-cameras"

OVERPASS = {
    "osm3s": {"timestamp_osm_base": "2026-10-01T00:00:00Z"},
    "elements": [
        {"type": "node", "id": 1, "lat": 38.9001, "lon": -77.0301,
         "tags": {"highway": "speed_camera", "maxspeed": "25 mph"}},
        {"type": "node", "id": 2, "lat": 38.9002, "lon": -77.0302,
         "tags": {"highway": "speed_camera", "enforcement": "traffic_signals"}},
        {"type": "node", "id": 3, "lat": 38.9003, "lon": -77.0303,
         "tags": {"highway": "speed_camera", "maxspeed": "signals"}},
        # A relation's device: first with tags that say nothing about what
        # it enforces, then again as a bare skeleton.
        {"type": "node", "id": 4, "lat": 38.9004, "lon": -77.0304,
         "tags": {"highway": "speed_camera"}},
        {"type": "relation", "id": 9,
         "tags": {"type": "enforcement", "enforcement": "traffic_signals"},
         "members": [{"type": "node", "ref": 4, "role": "device"},
                     {"type": "way", "ref": 77, "role": "from"}]},
        {"type": "node", "id": 4, "lat": 38.9004, "lon": -77.0304},
        # A device that is not tagged as a camera on its own.
        {"type": "relation", "id": 10, "tags": {"type": "enforcement", "enforcement": "maxspeed",
                                                "maxspeed": "45"},
         "members": [{"type": "node", "ref": 5, "role": "device"}]},
        {"type": "node", "id": 5, "lat": 40.0, "lon": -105.0},
        # Not a camera at all.
        {"type": "node", "id": 6, "lat": 40.1, "lon": -105.1, "tags": {"highway": "crossing"}},
    ],
}


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"osm_cameras_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cameras():
    refresh = load("refresh")
    city = refresh.from_sf([{"site_id": "MTAF024", "location": "WB 1333 BAY ST",
                             "posted_speed": "25", "latitude": "37.8036",
                             "longitude": "-122.4290"}])
    return refresh.merge(city, refresh.from_overpass(OVERPASS))


@pytest.fixture
def server(tmp_path, monkeypatch, cameras):
    data = tmp_path / "cameras.json"
    data.write_text(json.dumps({"as_of": "2026-10-01T00:00:00Z", "cameras": cameras}),
                    encoding="utf-8")
    monkeypatch.setenv("CAMERAS_FILE", str(data))
    return load("server")


def test_overpass_elements_become_camera_records():
    found = {c["ref"]: c for c in load("refresh").from_overpass(OVERPASS)}
    assert sorted(found) == ["n1", "n2", "n3", "n4", "n5"]
    assert found["n1"] == {"ref": "n1", "kind": "CAMERA_SPEED", "lat": 38.9001, "lon": -77.0301,
                           "by": "OpenStreetMap", "mph": 25,
                           "url": "https://www.openstreetmap.org/node/1"}
    assert found["n2"]["kind"] == "CAMERA_RED_LIGHT" and "mph" not in found["n2"]
    assert found["n3"]["kind"] == "CAMERA_SPEED" and "mph" not in found["n3"]   # "signals"
    assert found["n4"]["kind"] == "CAMERA_RED_LIGHT"   # the relation decides
    assert found["n5"]["mph"] == 45


def test_limits_are_read_as_miles_per_hour_or_not_at_all():
    limit = load("refresh").limit_mph
    assert limit("35 mph") == 35 and limit("35") == 35 and limit(" 55MPH ") == 55
    assert limit(25.0) == 25
    assert limit("signals") is None and limit("50 km/h") is None and limit(None) is None
    assert limit("300") is None


def test_city_lists_become_camera_records():
    refresh = load("refresh")
    chi = refresh.from_chicago([{"intersection": "744 W Fullerton Ave", "first_approach": "WB",
                                 "latitude": "41.9255", "longitude": "-87.6483"}],
                               "CAMERA_RED_LIGHT")
    assert chi[0]["kind"] == "CAMERA_RED_LIGHT" and chi[0]["by"] == "City of Chicago"
    assert chi[0]["where"] == "744 W Fullerton Ave (WB)" and chi[0]["ref"].startswith("chi:rl:")
    dc = refresh.from_dc([
        {"attributes": {"ENFORCEMENT_SPACE_CODE": "ATE 0846", "ENFORCEMENT_TYPE": "Speed",
                        "LOCATION_DESCRIPTION": "700 BLK ALLEN Y LEW PL NW E/B",
                        "SPEED_LIMIT": 25.0, "ACTIVE_STATUS": "Active",
                        "CAMERA_LATITUDE": 38.9029, "CAMERA_LONGITUDE": -77.02341}},
        # A stop sign camera is another kind, and a retired camera is gone.
        {"attributes": {"ENFORCEMENT_SPACE_CODE": "ATE 1", "ENFORCEMENT_TYPE": "Stop Sign",
                        "ACTIVE_STATUS": "Active", "CAMERA_LATITUDE": 38.9,
                        "CAMERA_LONGITUDE": -77.0}},
        {"attributes": {"ENFORCEMENT_SPACE_CODE": "ATE 2", "ENFORCEMENT_TYPE": "Speed",
                        "ACTIVE_STATUS": "Inactive", "CAMERA_LATITUDE": 38.9,
                        "CAMERA_LONGITUDE": -77.0}},
    ])
    # A source's own key can hold characters an alert id cannot.
    assert [c["ref"] for c in dc] == ["dc:ATE-0846"] and dc[0]["mph"] == 25
    # San Francisco's dataset is one row per camera per day; a camera is kept once.
    sf = refresh.from_sf([{"site_id": "MTAF024", "location": "WB 1333 BAY ST", "posted_speed": "25",
                           "latitude": "37.8036", "longitude": "-122.4290"}] * 3)
    assert len(sf) == 1 and sf[0]["mph"] == 25
    # A row with no usable position, or one outside the country, is dropped.
    assert refresh.record("x", "CAMERA_SPEED", None, "-87.6", by="x") is None
    assert refresh.record("x", "CAMERA_SPEED", "51.5", "-0.1", by="x") is None


def test_a_camera_in_both_a_city_list_and_openstreetmap_is_counted_once():
    refresh = load("refresh")
    city = [refresh.record("dc:1", "CAMERA_SPEED", 38.90012, -77.03012, mph=25, by="DC")]
    merged = refresh.merge(city, refresh.from_overpass(OVERPASS))
    refs = [c["ref"] for c in merged]
    assert "dc:1" in refs and "n1" not in refs       # the same camera: the city's record wins
    assert "n2" in refs                              # a red light camera a few meters away is not
    assert "n5" in refs


def test_a_refresh_that_lost_most_cameras_is_refused(tmp_path, monkeypatch):
    refresh = load("refresh")
    out = tmp_path / "cameras.json"
    out.write_text(json.dumps({"as_of": "x", "cameras": [{"ref": str(i)} for i in range(100)]}),
                   encoding="utf-8")
    monkeypatch.setattr(refresh, "OUT", out)
    monkeypatch.setattr(refresh, "fetch_all", lambda saved=None: ([{"ref": "a"}], "now", {"x": 1}))
    assert refresh.main([]) == 1
    assert len(json.loads(out.read_text(encoding="utf-8"))["cameras"]) == 100


@pytest.mark.asyncio
async def test_the_plugin_passes_the_conformance_check(server):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://plugin.example") as c:
        rep = await flare.check_plugin("https://plugin.example", client=c)
    assert rep.success, rep.text()
    assert any(p.startswith("snapshot:") for p in rep.passed)


@pytest.mark.asyncio
async def test_alerts_are_valid_nearest_first_and_say_where_they_come_from(server):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://plugin.example") as c:
        hs = (await c.get("/flare/v1/handshake")).json()
        assert flare.validate_handshake(hs) == []
        assert hs["capabilities"] == {"alerts": True, "report": False, "confirm": False,
                                      "notify": False, "snapshot": True}
        body = (await c.get("/flare/v1/alerts",
                            params={"lat": 38.9, "lon": -77.03, "r": 2000})).json()
        got = body["alerts"]
        assert [a["id"] for a in got] == [f"osm-cameras:n{i}" for i in (1, 2, 3, 4)]
        for a in got:
            assert flare.validate_alert(a) == [], a
            assert a["notify"] is False
        assert got[0]["description"] == "Speed camera, limit 25 mph."
        assert got[0]["extra"] == {"data": "OpenStreetMap", "limit_mph": 25}
        assert got[0]["source_url"] == "https://www.openstreetmap.org/node/1"
        assert got[1]["description"] == "Red light camera."
        assert body["as_of"] == "2026-10-01T00:00:00Z"
        sf = (await c.get("/flare/v1/alerts",
                          params={"lat": 37.80, "lon": -122.43, "r": 2000})).json()["alerts"]
        assert sf[0]["id"] == "osm-cameras:sf:MTAF024"
        assert sf[0]["description"] == "Speed camera, limit 25 mph. WB 1333 Bay St."
        assert sf[0]["extra"]["data"] == "City and County of San Francisco"
        assert sf[0]["reliability"] > got[0]["reliability"]   # a city's own list is surer


@pytest.mark.asyncio
async def test_the_snapshot_is_every_camera_in_the_same_shape(server, cameras):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://plugin.example") as c:
        body = (await c.get("/flare/v1/snapshot")).json()
    assert len(body["alerts"]) == len(cameras) == 6
    kept, problems = flare.accept_alerts(body, limit=flare.SNAPSHOT_MAX_ALERTS)
    assert problems == [] and len(kept) == 6


@pytest.mark.asyncio
async def test_bad_and_out_of_range_requests_are_refused(server):
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://plugin.example") as c:
        r = await c.get("/flare/v1/alerts", params={"lat": 51.5, "lon": -0.1, "r": 1000})
        assert r.status_code == 422 and r.json()["error"]["code"] == "outside_coverage"
        r = await c.get("/flare/v1/alerts", params={"lat": "nan", "lon": -77, "r": 1000})
        assert r.status_code == 400
        r = await c.get("/flare/v1/alerts", params={"lon": -77})
        assert r.status_code == 400
        assert (await c.post("/flare/v1/report", json={})).status_code in (404, 405)


def test_the_shipped_file_is_what_the_server_expects():
    data = json.loads((HERE / "cameras.json").read_text(encoding="utf-8"))
    assert data["as_of"] and len(data["cameras"]) > 2000
    refs = [c["ref"] for c in data["cameras"]]
    assert len(refs) == len(set(refs))
    sources = {c["by"] for c in data["cameras"]}
    assert {"OpenStreetMap", "City of Chicago", "District of Columbia",
            "City and County of San Francisco"} <= sources
    server = load("server")
    for cam in data["cameras"]:
        # Every shipped record makes a valid alert: checked on all of them,
        # because one city's keys can break a rule the others never touch.
        errs = flare.validate_alert(server.alert(cam, server._hour()))
        assert errs == [], (cam, errs)
        assert cam["kind"] in ("CAMERA_SPEED", "CAMERA_RED_LIGHT")
        assert 18 <= cam["lat"] <= 71.5 and -168 <= cam["lon"] <= -66.5, cam
