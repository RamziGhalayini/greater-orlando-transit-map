#!/usr/bin/env bash
# Start the local street matcher and wait until it answers.
set -euo pipefail
TOOLS="${TOOLS:-osrm-tools}"
BIN=$(ls -d "$TOOLS"/node_modules/@project-osrm/osrm/lib/binding* | head -1)
nohup "$BIN/osrm-routed" --algorithm mld --max-matching-size 1000 -p 5000 osrm/region.osrm > osrm-routed.log 2>&1 &
for i in $(seq 1 60); do
  if curl -fs "http://127.0.0.1:5000/nearest/v1/driving/-81.379,28.542" > /dev/null; then echo "street matcher ready"; exit 0; fi
  sleep 2
done
echo "street matcher did not start"; cat osrm-routed.log; exit 1
