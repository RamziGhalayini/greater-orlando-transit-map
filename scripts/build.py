#!/usr/bin/env python3
"""Build the Greater Orlando transit map data.

Reads sources.json, downloads each GTFS feed and zone layer, and writes
plain-language GeoJSON files into site/data/. Uses only the Python standard
library so it runs anywhere (including GitHub Actions) without installs.

If one source fails, the build keeps that source's previous files and
carries on, so one broken link never blanks the whole map.

Usage:
  python scripts/build.py                 # build every source
  python scripts/build.py --only lynx     # build one feed
  python scripts/build.py --gtfs-file lynx=path/to/google_transit.zip
"""
import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "data"
UA = "GreaterOrlandoTransitMap/1.0 (+https://github.com/RamziGhalayini/greater-orlando-transit-map)"

# ---------------------------------------------------------------- helpers

def fetch(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def rnd(x):
    return round(float(x), 5)


ABBREV = {
    "rd": "Road", "blvd": "Boulevard", "bl": "Boulevard", "ave": "Avenue", "av": "Avenue",
    "dr": "Drive", "ln": "Lane", "ct": "Court", "cir": "Circle", "pkwy": "Parkway",
    "pky": "Parkway", "hwy": "Highway", "trl": "Trail", "tr": "Trail", "pl": "Place",
    "ter": "Terrace", "sq": "Square", "expy": "Expressway", "ctr": "Center", "cntr": "Center",
    "sr": "State Road", "cr": "County Road", "intl": "International", "int'l": "International",
    "n": "North", "s": "South", "e": "East", "w": "West",
    "ne": "Northeast", "nw": "Northwest", "se": "Southeast", "sw": "Southwest",
    "hosp": "Hospital", "apts": "Apartments", "apt": "Apartment", "mkt": "Market",
    "sta": "Station", "stn": "Station", "hts": "Heights", "mt": "Mount", "ft": "Fort", "@": "&",
}


def tidy_name(name):
    """Spell out street abbreviations: 'N ORANGE BLOSSOM TRL & HOLLY ST' ->
    'North Orange Blossom Trail & Holly Street'."""
    if not name:
        return name
    s = name.strip()
    if s.isupper() or s.islower():
        s = s.title()
    words = re.split(r"(\s+|&|/|-|,|\(|\))", s)
    out = []
    for i, t in enumerate(words):
        key = t.lower().rstrip(".")
        nxt = next((w for w in words[i + 1:] if w.strip()), "")
        prev = next((w for w in reversed(words[:i]) if w.strip()), "")
        before_name = bool(nxt) and nxt[0].isalpha()
        if key == "st":
            # 'St Cloud' is Saint Cloud; 'Holly St' is Holly Street.
            out.append("St." if before_name else "Street")
        elif key == "dr" and before_name:
            # 'Dr Phillips', 'Dr Love Road' are names, not Drive.
            out.append("Dr.")
        elif len(key) == 1 and prev.lower() in ("bay", "gate", "door", "platform", "building", "suite"):
            out.append(t)  # 'Bay E' is a bay letter, not East
        elif key in ABBREV:
            out.append(ABBREV[key])
        else:
            out.append(t)
    s = "".join(out)
    s = re.sub(r"\b(Us|US)\b(?=\s*\d)", "U.S.", s)
    s = re.sub(r"\bSuperstop\b", "SuperStop", s)
    s = re.sub(r"\bLymmo\b", "LYMMO", s)
    s = re.sub(r"\bSunrail\b", "SunRail", s)
    s = re.sub(r"\bLynx\b", "LYNX", s)
    s = re.sub(r"\bDeland\b", "DeLand", s)
    s = re.sub(r"\bDebary\b", "DeBary", s)
    s = re.sub(r"\bDeltona\b", "Deltona", s)
    s = re.sub(r"\b(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), s)  # 3Rd -> 3rd
    # Drop trailing direction codes some agencies add (OB = outbound, NB = northbound...).
    s = re.sub(r"[\s-]+\(?(Ob|Ib|Nb|Sb|Eb|Wb)\)?$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def secs(t):
    if not t:
        return None
    h, m, *rest = t.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + (int(rest[0]) if rest else 0)


def clock(s):
    s = s % (24 * 3600)
    h, m = divmod(s // 60, 60)
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {ampm}"


def _seg_dist(p, a, b):
    """Distance from p to the segment ab (in degrees). Works when a == b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / seg2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def simplify(points, tol):
    """Douglas-Peucker on [lon, lat] points. Measures distance to the segment, not
    the endless line through it, so loop routes (which end where they start) and
    out-and-back stretches are kept instead of collapsing."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        best, idx = 0.0, None
        for i in range(a + 1, b):
            d = _seg_dist(points[i], points[a], points[b])
            if d > best:
                best, idx = d, i
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def in_bbox(lon, lat, bbox):
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


# ---------------------------------------------------------------- GTFS

def read_table(zf, name):
    names = {n.split("/")[-1]: n for n in zf.namelist()}
    if name not in names:
        return []
    with zf.open(names[name]) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig", newline="")
        return list(csv.DictReader(text))


def iter_table(zf, name):
    names = {n.split("/")[-1]: n for n in zf.namelist()}
    if name not in names:
        return
    with zf.open(names[name]) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig", newline="")
        yield from csv.DictReader(text)


DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def active_services(zf, start, days=14):
    """Map each service_id to the set of day types it runs on
    ('weekday', 'saturday', 'sunday') during the next `days` days."""
    cal = read_table(zf, "calendar.txt")
    exc = defaultdict(dict)
    for r in read_table(zf, "calendar_dates.txt"):
        exc[r["service_id"]][r["date"]] = r["exception_type"].strip()
    result = defaultdict(set)
    window = [start + timedelta(d) for d in range(days)]
    services = {r["service_id"]: r for r in cal}
    for sid in set(services) | set(exc):
        for d in window:
            ds = d.strftime("%Y%m%d")
            on = False
            r = services.get(sid)
            if r and r["start_date"] <= ds <= r["end_date"] and r.get(DAYS[d.weekday()], "0").strip() == "1":
                on = True
            e = exc.get(sid, {}).get(ds)
            if e == "1":
                on = True
            elif e == "2":
                on = False
            if on:
                result[sid].add("weekday" if d.weekday() < 5 else ("saturday" if d.weekday() == 5 else "sunday"))
    return result


def describe_hours(spans, starts):
    """spans: daytype -> (first_start, last_end); starts: weekday trip starts (one direction)."""
    parts = []
    labels = [("weekday", "Weekdays"), ("saturday", "Saturday"), ("sunday", "Sunday")]
    running = [k for k, _ in labels if k in spans]
    if running == ["weekday", "saturday", "sunday"]:
        runs = "Every day"
    elif running == ["weekday", "saturday"]:
        runs = "Monday through Saturday"
    elif running == ["weekday"]:
        runs = "Weekdays only"
    elif not running:
        runs = "No trips scheduled in the next two weeks"
    elif running == ["saturday"]:
        runs = "Saturdays only"
    elif running == ["sunday"]:
        runs = "Sundays only"
    else:
        runs = " and ".join({"weekday": "Weekdays", "saturday": "Saturday", "sunday": "Sunday"}[k] for k in running)
    for key, label in labels:
        if key in spans:
            a, b = spans[key]
            parts.append(f"{label}: {clock(a)} to {clock(b)}")
        elif running:
            parts.append(f"{label}: no service")
    freq = None
    mid = sorted(s for s in starts if 6 * 3600 <= s <= 19 * 3600)
    if len(mid) >= 4:
        gaps = [b - a for a, b in zip(mid, mid[1:]) if b > a]
        if gaps:
            g = statistics.median(gaps) / 60
            freq = int(5 * round(g / 5)) if g >= 10 else int(round(g))
    return runs, parts, freq


def build_gtfs(feed, data, region_bbox, snapper=None):
    zf = zipfile.ZipFile(io.BytesIO(data))
    today = datetime.now(timezone(timedelta(hours=-5))).date()
    svc = active_services(zf, today)

    routes = {r["route_id"]: r for r in read_table(zf, "routes.txt")}
    trips = {}
    route_shapes = defaultdict(set)
    shape_trip = {}  # one example trip per shape, to find its stops
    for t in iter_table(zf, "trips.txt"):
        trips[t["trip_id"]] = t
        if t.get("shape_id"):
            route_shapes[t["route_id"]].add(t["shape_id"])
            shape_trip.setdefault(t["shape_id"], t["trip_id"])
    example_trips = set(shape_trip.values())

    # Shapes.
    shape_pts = defaultdict(list)
    for p in iter_table(zf, "shapes.txt"):
        shape_pts[p["shape_id"]].append((int(p["shape_pt_sequence"]), float(p["shape_pt_lon"]), float(p["shape_pt_lat"])))
    # Routes with no usable shape get drawn stop-to-stop instead.
    shapeless = {t["route_id"] for t in trips.values()} - {
        rid for rid, sids in route_shapes.items() if any(len(shape_pts.get(x, [])) >= 2 for x in sids)}

    # One pass over stop_times: first/last time per trip, stop -> routes.
    first = {}
    last = {}
    stop_routes = defaultdict(set)
    trip_stops = defaultdict(list)
    for st in iter_table(zf, "stop_times.txt"):
        tid = st["trip_id"]
        t = trips.get(tid)
        if not t:
            continue
        seq = int(st["stop_sequence"])
        dep = secs(st.get("departure_time") or st.get("arrival_time"))
        arr = secs(st.get("arrival_time") or st.get("departure_time"))
        if dep is not None and (tid not in first or seq < first[tid][0]):
            first[tid] = (seq, dep)
        if arr is not None and (tid not in last or seq > last[tid][0]):
            last[tid] = (seq, arr)
        stop_routes[st["stop_id"]].add(t["route_id"])
        if t["route_id"] in shapeless or tid in example_trips:
            trip_stops[tid].append((seq, st["stop_id"]))

    # Hours per route.
    spans = defaultdict(dict)
    starts = defaultdict(list)
    for tid, t in trips.items():
        if tid not in first or tid not in last:
            continue
        rid = t["route_id"]
        for daytype in svc.get(t["service_id"], ()):
            a, b = first[tid][1], last[tid][1]
            cur = spans[rid].get(daytype)
            spans[rid][daytype] = (min(a, cur[0]), max(b, cur[1])) if cur else (a, b)
            if daytype == "weekday" and t.get("direction_id", "0") in ("0", ""):
                starts[rid].append(a)
    for rid in starts:
        starts[rid] = sorted(set(starts[rid]))

    # Stops.
    stops = {s["stop_id"]: s for s in read_table(zf, "stops.txt")}

    def route_label(r):
        short = (r.get("route_short_name") or "").strip()
        long_ = tidy_name((r.get("route_long_name") or "").strip())
        if short and long_:
            long_ = re.sub(r"^" + re.escape(short) + r"\b[\s:-]*", "", long_, flags=re.I).strip() or long_
            if long_.lower() == short.lower():
                return short, short
            return short, f"{short}: {long_}"
        return short or long_, short or long_

    groups = feed.get("groups", [])

    def group_of(r):
        for g in groups:
            pat = re.compile(g["match"], re.I)
            if pat.search(r.get("route_short_name") or "") or pat.search(r.get("route_long_name") or ""):
                return g["id"]
        return feed["id"]

    route_features = []
    clip = feed.get("clip_to_region")
    for rid, r in routes.items():
        lines = []
        for sid in route_shapes.get(rid, ()):
            pts = [(lon, lat) for _, lon, lat in sorted(shape_pts.get(sid, []))]
            if clip:
                pts = [p for p in pts if in_bbox(p[0], p[1], region_bbox)]
            raw = [[rnd(x), rnd(y)] for x, y in pts]
            if snapper and len(raw) >= 2:
                guide = []
                for _, stop_id in sorted(trip_stops.get(shape_trip.get(sid), [])):
                    s_ = stops.get(stop_id)
                    if s_:
                        guide.append([rnd(s_["stop_lon"]), rnd(s_["stop_lat"])])
                raw, ok = snapper(raw, guide, route_label(r)[0])
                pts = simplify(raw, 0.00003) if ok else simplify(raw, 0.00008)
            else:
                pts = simplify(raw, 0.00008)
            if len(pts) >= 2:
                lines.append([[rnd(x), rnd(y)] for x, y in pts])
        if not lines and rid in shapeless:
            best = max((tid for tid, t in trips.items() if t["route_id"] == rid), key=lambda tid: len(trip_stops.get(tid, [])), default=None)
            if best:
                pts = []
                for _, sid in sorted(trip_stops[best]):
                    s = stops.get(sid)
                    if s:
                        pts.append([rnd(s["stop_lon"]), rnd(s["stop_lat"])])
                if snapper and len(pts) >= 2:
                    snapped, ok = snapper(pts, None, route_label(r)[0])
                    pts = [[rnd(x), rnd(y)] for x, y in simplify(snapped, 0.00003)] if ok else pts
                if len(pts) >= 2:
                    lines.append(pts)
        # Drop duplicate shapes.
        uniq = []
        seen = set()
        for ln in lines:
            key = (tuple(ln[0]), tuple(ln[-1]), len(ln))
            if key not in seen:
                seen.add(key)
                uniq.append(ln)
        if not uniq:
            continue
        short, label = route_label(r)
        runs, hours, freq = describe_hours(spans.get(rid, {}), starts.get(rid, []))
        color = (r.get("route_color") or "").strip()
        props = {
            "feed": feed["id"],
            "group": group_of(r),
            "short": short,
            "name": label,
            "runs": runs,
            "hours": hours,
            "every": freq,
            "color": f"#{color}" if re.fullmatch(r"[0-9A-Fa-f]{6}", color or "") and color.upper() not in ("FFFFFF",) else feed["color"],
        }
        route_features.append({"type": "Feature", "properties": props,
                               "geometry": {"type": "MultiLineString", "coordinates": uniq}})

    stop_features = []
    for sid, s in stops.items():
        if s.get("location_type", "0") not in ("", "0"):
            continue
        rids = stop_routes.get(sid)
        if not rids:
            continue
        try:
            lon, lat = float(s["stop_lon"]), float(s["stop_lat"])
        except (ValueError, KeyError):
            continue
        if clip and not in_bbox(lon, lat, region_bbox):
            continue
        names = sorted({route_label(routes[x])[0] for x in rids if x in routes}, key=lambda v: (len(v), v))
        stop_features.append({"type": "Feature",
                              "properties": {"feed": feed["id"], "name": tidy_name(s.get("stop_name")), "routes": names},
                              "geometry": {"type": "Point", "coordinates": [rnd(lon), rnd(lat)]}})

    feed_info = read_table(zf, "feed_info.txt")
    valid = None
    if feed_info and feed_info[0].get("feed_end_date"):
        e = feed_info[0]["feed_end_date"]
        valid = f"{e[4:6]}/{e[6:]}/{e[:4]}"
    return route_features, stop_features, valid


# ---------------------------------------------------------------- zones / manual

def build_zone(zone):
    gj = json.loads(fetch(zone["url"]))
    feats = []
    for f in gj.get("features", []):
        p = f.get("properties", {})
        raw = str(p.get(zone["name_field"], "")).strip()
        raw = re.sub(r"^\s*(\d+)\s*-\s*(.+?)\s*(NeighborLink)?\s*$", r"NeighborLink \1: \2", raw)
        name = zone.get("name_prefix", "") + raw
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"layer": zone["id"], "name": name, "lines": zone["description"],
                                     "source": zone.get("source", "")}})
    if not feats:
        raise RuntimeError("no features returned")
    return feats


# ---------------------------------------------------------------- street snapping

def make_snapper(feed):
    """Return (snapper, stats, cache) for bus feeds, or (None, None, None).

    Lines are matched to streets with the OSRM server in $OSRM_URL. Matches are
    cached in cache/snap/<feed>.json, so builds without OSRM still use them."""
    if feed.get("mode") != "bus" or feed.get("snap") is False:
        return None, None, None
    osrm = os.environ.get("OSRM_URL")
    cache = snap.SnapCache(ROOT / "cache" / "snap" / f"{feed['id']}.json")
    stats = {"lines": 0, "on_streets": 0, "with_spots_off_street": 0, "kept_agency_line": 0,
             "not_checked": 0, "shortcuts_fixed": 0}
    notes = []
    spots = []  # straight pieces the street check could not fix, for a look by hand

    def snapper(line, guide=None, label=""):
        stats["lines"] += 1
        key = [line, guide or []]
        hit = cache.get(key)
        if hit is None and osrm:
            try:
                hit = snap.snap_line(osrm, line, guide)
            except Exception as e:  # network or OSRM error: keep the agency line
                hit = {"line": None, "fixed": 0, "still": [], "note": f"OSRM error: {e}"}
            cache.put(key, hit)
        if hit is None:
            stats["not_checked"] += 1
            return line, False
        if hit.get("note") and len(notes) < 10:
            notes.append(f"{label}: {hit['note']}")
        if not hit["line"]:
            stats["kept_agency_line"] += 1
            return line, False
        stats["shortcuts_fixed"] += hit["fixed"]
        if hit.get("still"):
            stats["with_spots_off_street"] += 1
            for p in hit["still"]:
                if len(spots) < 100:
                    spots.append({"route": label, "at": p})
        else:
            stats["on_streets"] += 1
        return hit["line"], True

    stats["notes"] = notes
    stats["spots_off_street"] = spots
    return snapper, stats, cache


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append")
    ap.add_argument("--gtfs-file", action="append", default=[], help="id=path to use a local zip instead of downloading")
    args = ap.parse_args()
    cfg = json.loads((ROOT / "sources.json").read_text())
    bbox = cfg["region"]["bbox"]
    local = dict(x.split("=", 1) for x in args.gtfs_file)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"layers": {}}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    problems = []

    for feed in cfg["feeds"]:
        if args.only and feed["id"] not in args.only:
            continue
        src = local.get(feed["id"]) or feed.get("url")
        if not src:
            print(f"- {feed['id']}: skipped ({feed.get('todo', 'no url')})")
            continue
        try:
            data = Path(src).read_bytes() if feed["id"] in local else fetch(src)
            snapper, snap_stats, cache = make_snapper(feed)
            routes, stops, valid = build_gtfs(feed, data, bbox, snapper)
            if cache:
                cache.save()
            if not routes:
                raise RuntimeError("feed had no routes with shapes or stops")
            write_json(OUT / f"{feed['id']}-routes.geojson", {"type": "FeatureCollection", "features": routes})
            write_json(OUT / f"{feed['id']}-stops.geojson", {"type": "FeatureCollection", "features": stops})
            manifest["layers"][feed["id"]] = {"kind": "gtfs", "routes": len(routes), "stops": len(stops),
                                              "updated": stamp, "schedule_valid_until": valid}
            if snap_stats:
                manifest["layers"][feed["id"]]["street_snapping"] = snap_stats
                print(f"  street snapping: {snap_stats}")
            print(f"+ {feed['id']}: {len(routes)} routes, {len(stops)} stops")
        except Exception as e:  # keep the previous files
            problems.append(f"{feed['id']}: {e}")
            print(f"! {feed['id']}: {e} (kept previous data if any)")

    for zone in cfg.get("zones", []):
        if args.only and zone["id"] not in args.only:
            continue
        try:
            feats = build_zone(zone)
            write_json(OUT / f"{zone['id']}-zones.geojson", {"type": "FeatureCollection", "features": feats})
            manifest["layers"][zone["id"]] = {"kind": "zones", "zones": len(feats), "updated": stamp}
            print(f"+ {zone['id']}: {len(feats)} zones")
        except Exception as e:
            problems.append(f"{zone['id']}: {e}")
            print(f"! {zone['id']}: {e} (kept previous data if any)")

    for m in cfg.get("manual", []):
        p = ROOT / m["file"]
        if p.exists():
            gj = json.loads(p.read_text())
            write_json(OUT / f"{m['id']}.geojson", gj)
            manifest["layers"][m["id"]] = {"kind": "manual", "features": len(gj.get("features", [])), "updated": stamp}

    manifest["built"] = stamp
    manifest["problems"] = problems
    write_json(manifest_path, manifest)
    (OUT / "config.json").write_text(json.dumps({k: cfg[k] for k in ("region", "feeds", "zones", "manual")}, ensure_ascii=False, indent=1))
    if problems:
        print("\nSome sources failed:\n  " + "\n  ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
