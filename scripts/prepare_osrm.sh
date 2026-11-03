#!/usr/bin/env bash
# Build the street network used to snap bus lines onto streets.
# Downloads OpenStreetMap data for Florida, cuts it to the map region and
# prepares it for OSRM. Runs about once a month (the result is cached).
set -euo pipefail
OSRM_VERSION="${OSRM_VERSION:-26.4.1}"
TOOLS="${TOOLS:-osrm-tools}"
BIN=$(ls -d "$TOOLS"/node_modules/@project-osrm/osrm/lib/binding* | head -1)
PROFILES="$TOOLS/node_modules/@project-osrm/osrm/profiles"
BBOX=$(python3 -c "import json;b=json.load(open('sources.json'))['region']['bbox'];print(','.join(map(str,b)))")
mkdir -p osrm
curl -fL --retry 3 -o osrm/florida.osm.pbf https://download.geofabrik.de/north-america/us/florida-latest.osm.pbf
osmium extract -b "$BBOX" osrm/florida.osm.pbf -o osrm/region.osm.pbf --overwrite
rm osrm/florida.osm.pbf
python3 scripts/make_bus_profile.py "$PROFILES/car.lua" "$PROFILES/bus.lua"
"$BIN/osrm-extract" -p "$PROFILES/bus.lua" osrm/region.osm.pbf
"$BIN/osrm-partition" osrm/region.osrm
"$BIN/osrm-customize" osrm/region.osrm
rm osrm/region.osm.pbf
ls -la osrm
