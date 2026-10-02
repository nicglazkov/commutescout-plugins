# CommuteScout plugins

Plugins add alerts to the [CommuteScout](https://commutescout.com) map and
to the iPhone and Android apps. Each one is a small server that speaks
[Flare](https://github.com/nicglazkov/commutescout/blob/main/docs/flare.md),
CommuteScout's open protocol for road alert sources. A plugin is a server,
not an app, so one plugin works on the web, on iOS and on Android.

This repository holds the plugins the CommuteScout project runs itself.
Browse them, installed or not, in the
[marketplace](https://commutescout.com/marketplace).

| Plugin | What it shows | Data |
|---|---|---|
| [osm-cameras](osm-cameras/) | Fixed speed and red light cameras in the United States | OpenStreetMap |
| [waze-relay](waze-relay/) | Crowd reports: police, crashes, hazards, jams | Unofficial relay of Waze reports |

## Write your own

Start from the one-file
[reference plugin](https://github.com/nicglazkov/commutescout/tree/main/examples/flare-plugin)
and the [specification](https://github.com/nicglazkov/commutescout/blob/main/docs/flare.md).
`osm-cameras` here is the smallest real one: a data file, two endpoints,
about 170 lines.

A plugin can be private (your own devices), unlisted (shared by link) or
public (listed in the marketplace). The
[plugins page](https://commutescout.com/plugins) explains the three and
how to get listed.

## Develop

```
python -m venv .venv
.venv/bin/pip install -r waze-relay/requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

On Windows the tools are under `.venv\Scripts`. CI installs and tests
each plugin on its own, with the pins that plugin deploys with.

The tests run every plugin through the Flare conformance check, which
lives in the CommuteScout repository; `requirements-dev.txt` installs it
from there.

Check a running plugin the same way:

```
python -m ca_roads.flare check https://your-plugin.example
```

## Deploy

Each plugin has its own `Dockerfile` and pinned `requirements.txt`, and
its README has the deploy command. They share nothing at run time.

## License

MIT. Data served by a plugin keeps its own license; see each plugin's
README.
