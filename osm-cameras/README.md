# Speed and red light cameras

A read-only [Flare](https://github.com/nicglazkov/commutescout/blob/main/docs/flare.md)
plugin that serves fixed speed and red light cameras in the United
States, from open data.

- Kinds: `CAMERA_SPEED`, `CAMERA_RED_LIGHT`.
- Capabilities: `alerts` and `snapshot`. Nothing is reported to it or
  confirmed through it, and it never asks the apps to speak.
- The data is the same for everyone, so CommuteScout lists it as
  **shared**: cameras show across the map at any zoom, not only around
  the person looking.

## Where the cameras come from

| Source | Covers | Cameras |
|---|---|---|
| OpenStreetMap | Nationwide, wherever volunteers have mapped | about 1,470 |
| City of Chicago open data | Chicago, complete | about 510 |
| District of Columbia open data | Washington DC, complete | about 280 |
| San Francisco open data | San Francisco speed cameras, complete | about 55 |

A city's list is complete for that city; elsewhere coverage is whatever
has been mapped, which is good in some places and thin in others. A
camera that appears in both a city list and OpenStreetMap is counted
once. Every alert says which source it came from and links to it.

Adding a city is a function in `refresh.py` that turns its dataset into
records, and a line in `fetch_all`. A wrong or missing OpenStreetMap
camera is fixed on openstreetmap.org.

## How it works

`refresh.py` reads every source once and writes `cameras.json`. The
service loads that file at start and answers from memory, so it has no
upstream to wait on and starts in about a second.

```
python refresh.py          # rebuild cameras.json (monthly, with the map files)
python server.py           # serve on :8310
python -m ca_roads.flare check http://127.0.0.1:8310
```

## Deploy

```
gcloud run deploy osm-cameras --source osm-cameras \
  --project ca-roads-mcp --region us-west1 \
  --memory 256Mi --cpu 1 --min-instances 0 --max-instances 1 \
  --allow-unauthenticated
```

It scales to zero, so it costs nothing while nobody asks.

The per-address limit (120 requests a minute) is keyed on the last
`X-Forwarded-For` entry, the address Cloud Run itself appends, with IPv6
folded to its /64. Set `FLARE_TRUSTED_TOKEN` to a secret and put the same
value in the catalog manifest's `token` field, and the backend's polls
are exempt from that limit: a scraper sharing its address cannot 429 the
snapshot poll everyone's map depends on.

## Data

OpenStreetMap data is (c) OpenStreetMap contributors, under the Open
Database License: https://www.openstreetmap.org/copyright. The city
datasets are public records published by each city.
