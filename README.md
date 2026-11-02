# Greater Orlando Transit Map

A free, open-source map of buses, trains and on-call rides in **Orange, Seminole, Osceola, Volusia, Lake and Polk counties**. It's built to be easy to read for anyone, including people who don't use maps or apps much: big text, plain words, and one list of things to switch on and off.

It is a community project, not an official transit agency map.

## What's on it

| Layer | Source | Status |
|---|---|---|
| LYNX buses (with LYMMO shown separately) | LYNX GTFS | ✅ |
| SunRail | SunRail GTFS | ✅ |
| Brightline (Orlando airport station) | Brightline GTFS | ✅ |
| Citrus Connection (Polk) | Citrus Connection GTFS | ✅ |
| LakeXpress (Lake) | LakeXpress GTFS | ✅ |
| I-RIDE Trolley (International Drive) | I-RIDE GTFS | ✅ |
| Votran (Volusia) | Votran GTFS (InfoPoint) | ✅ |
| Scout zones (Seminole) | Seminole County GIS | ✅ |
| LYNX NeighborLink zones | LYNX GIS | ✅ |
| Scout hubs and charging spots | Hand-made (`manual/`) | ✅ |
| Waymo and Tesla Robotaxi areas | Hand-traced, approximate | ⏳ |
| Altamonte CraneRIDES shuttle, downtown trolley, FlixBus, Greyhound, RedCoach, Mears | To research | ⏳ |

Every bus line shows when it runs (first bus out to last bus in) and roughly how often, in plain words. Stops show which routes serve them, with street names spelled out (Road, Boulevard, North).

## How it works

```
sources.json ──► scripts/build.py ──► site/data/*.geojson ──► site/ (Leaflet map)
```

* `sources.json` lists every data source. Add a bus system by adding its GTFS link.
* `scripts/build.py` downloads each source and writes GeoJSON. It uses only the Python standard library. If one source fails, the old copy stays on the map and the rest still update.
* `site/` is a static page (Leaflet + OpenStreetMap). No build step and no API keys.
* `.github/workflows/build.yml` rebuilds the data every Monday and publishes to GitHub Pages.

## Run it yourself

```bash
python3 scripts/build.py            # downloads and builds everything
cd site && python3 -m http.server   # open http://localhost:8000
```

Build one source, or use a GTFS zip you already downloaded:

```bash
python3 scripts/build.py --only sunrail
python3 scripts/build.py --gtfs-file votran=~/Downloads/votran_gtfs.zip --only votran
```

## Adding something

* **A bus or train system with GTFS:** add an entry to `feeds` in `sources.json`.
* **A zone from an ArcGIS map:** add an entry to `zones` with its query URL.
* **Anything else (hubs, robotaxi outlines, coach stops):** add a GeoJSON file to `manual/` and list it under `manual`. Give each feature a `name`, plain-language `lines`, and a `source`.

Hand-traced shapes (like robotaxi areas) must say "approximate" in their name and point riders to the company's app.

## License

Code: MIT (see `LICENSE`). Map data comes from the agencies listed above under their own terms, and the base map is © OpenStreetMap contributors (ODbL).
