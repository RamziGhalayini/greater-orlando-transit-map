"""Snap transit route lines onto real streets with a local OSRM server.

Some agencies publish route shapes with too few points, so the line cuts
straight across blocks (for example a LYMMO line jumping diagonally from
Westmoreland Drive to Church Street). This module sends each line to OSRM's
map-matching service and replaces it with the street path the bus follows.

Safety checks keep the original line when the match looks wrong, so a bad
match never makes the map worse than the agency's own data.

Results are cached by line, so the slow matching only runs when an agency
changes a route.
"""
import bisect
import hashlib
import json
import math
import urllib.request

SNAP_VERSION = 3       # bump to redo every cached match after changing this file
RADIUS_M = 25          # how far a shape point may be from the street centreline
CHUNK = 400            # points per OSRM request
MAX_POINT_DEV_M = 45   # an original point further than this from the match = bad match
MAX_LENGTH_RATIO = 1.45


def _m(a, b):
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111320 * math.cos(lat), (b[1] - a[1]) * 111320)


def length(line):
    return sum(_m(a, b) for a, b in zip(line, line[1:]))


def _project(p, a, b):
    """Distance (m) from p to segment ab and how far along ab (0..1)."""
    lat = math.radians(p[1])
    kx, ky = 111320 * math.cos(lat), 111320
    ax, ay = (a[0] - p[0]) * kx, (a[1] - p[1]) * ky
    bx, by = (b[0] - p[0]) * kx, (b[1] - p[1]) * ky
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))
    return math.hypot(ax + t * dx, ay + t * dy), t


def positions_along(points, line, back_m=150.0, ahead_m=1500.0):
    """For each point, its distance from `line`, and its position (metres from the
    start) along `line`. Points are taken in order: each one is looked for a short
    way ahead of the previous one, so out-and-back routes on the same street and
    loops don't jump to the wrong pass."""
    cum = [0.0]
    for a, b in zip(line, line[1:]):
        cum.append(cum[-1] + _m(a, b))
    out = []
    start = 0
    prev = None
    nseg = len(line) - 1
    for p in points:
        gap = _m(prev, p) if prev is not None else 0.0
        lo = start
        while lo > 0 and cum[start] - cum[lo] < back_m:
            lo -= 1
        limit = cum[start] + max(ahead_m, 3 * gap)
        best = (float("inf"), start, 0.0)
        i = lo
        while i < nseg and (cum[i] <= limit or i == lo):
            d, t = _project(p, line[i], line[i + 1])
            if d < best[0]:
                best = (d, i, t)
            i += 1
        d, i, t = best
        if d == float("inf"):  # line has a single point
            out.append((_m(p, line[0]), 0.0))
            prev = p
            continue
        start = i
        prev = p
        out.append((d, cum[i] + t * (cum[i + 1] - cum[i])))
    return out


def _match(osrm_url, coords):
    path = ";".join(f"{x:.6f},{y:.6f}" for x, y in coords)
    radii = ";".join([str(RADIUS_M)] * len(coords))
    url = (f"{osrm_url}/match/v1/driving/{path}?geometries=geojson&overview=full"
           f"&gaps=ignore&tidy=true&radiuses={radii}")
    with urllib.request.urlopen(url, timeout=120) as r:
        data = json.load(r)
    if data.get("code") != "Ok" or not data.get("matchings"):
        raise RuntimeError(data.get("code", "no match"))
    out = []
    for m in data["matchings"]:
        g = m["geometry"]["coordinates"]
        if out and g and _m(out[-1], g[0]) < 1:
            g = g[1:]
        out.extend(g)
    return out


def add_stops(line, stops, max_off_m=60):
    """Insert the bus stops into the agency line, in order along it. Stops sit on
    the streets the bus really uses, so they stop the matcher from taking a
    different street where the agency line has too few points."""
    if not stops:
        return line
    pos_line = positions_along(line, line)
    cum = [p for _, p in pos_line]
    placed = []
    for st, (d, at) in zip(stops, positions_along(stops, line)):
        if d <= max_off_m:
            placed.append((at, st))
    merged = [(c, 0, p) for c, p in zip(cum, line)] + [(at, 1, st) for at, st in placed]
    merged.sort(key=lambda x: (x[0], x[1]))
    out = []
    for _, _, p in merged:
        if not out or _m(out[-1], p) > 3:
            out.append(p)
    return out


def snap_line(osrm_url, line, stops=None):
    """Return (snapped_line, shortcuts_fixed) or (None, reason)."""
    if len(line) < 2:
        return None, "too short"
    trace = add_stops(line, stops or [])
    snapped = []
    i = 0
    while i < len(trace) - 1:
        chunk = trace[i:i + CHUNK]
        part = _match(osrm_url, chunk)
        if snapped and part and _m(snapped[-1], part[0]) < 1:
            part = part[1:]
        snapped.extend(part)
        i += CHUNK - 1
    if len(snapped) < 2:
        return None, "empty match"
    snapped = [[round(x, 6), round(y, 6)] for x, y in snapped]
    return splice(line, snapped)


def _point_at(line, cum, pos):
    i = min(max(bisect.bisect_right(cum, pos) - 1, 0), len(line) - 2)
    seg = cum[i + 1] - cum[i]
    t = 0.0 if seg == 0 else min(1.0, max(0.0, (pos - cum[i]) / seg))
    a, b = line[i], line[i + 1]
    return [round(a[0] + (b[0] - a[0]) * t, 6), round(a[1] + (b[1] - a[1]) * t, 6)], i


def splice(line, snapped):
    """Build the final line piece by piece. Each stretch between two agency points
    uses the street path when the match clearly follows it, and keeps the agency's
    straight piece when it doesn't (for example inside a bus terminal). So one bad
    spot no longer throws away the fixes on the rest of the route.

    Returns (new_line, shortcuts_fixed) or (None, reason) if almost nothing matched."""
    cum = [0.0]
    for a, b in zip(snapped, snapped[1:]):
        cum.append(cum[-1] + _m(a, b))
    pos = positions_along(line, snapped)
    out, fixed, on_m, total_m = [], 0, 0.0, 0.0
    for (a, b), (da, pa), (db, pb) in zip(zip(line, line[1:]), pos, pos[1:]):
        straight = _m(a, b)
        total_m += straight
        along = pb - pa
        good = (da <= MAX_POINT_DEV_M and db <= MAX_POINT_DEV_M and along >= 0
                and along <= straight * MAX_LENGTH_RATIO + 150)
        if good:
            pa_pt, ia = _point_at(snapped, cum, pa)
            pb_pt, ib = _point_at(snapped, cum, pb)
            piece = [pa_pt] + snapped[ia + 1:ib + 1] + [pb_pt]
            on_m += straight
            if straight > 40 and along > straight * 1.12 + 15:
                fixed += 1
        else:
            piece = [a, b]
        for p in piece:
            if not out or _m(out[-1], p) > 0.5:
                out.append(p)
    if total_m == 0 or on_m / total_m < 0.3 or len(out) < 2:
        return None, f"street match covered only {round(100 * on_m / max(total_m, 1))}% of the line"
    return out, fixed


class SnapCache:
    """Remembers matched lines (keyed by the agency line) between builds."""

    def __init__(self, path):
        self.path = path
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.used = set()

    @staticmethod
    def key(line):
        return hashlib.sha1(json.dumps(line, separators=(",", ":")).encode()).hexdigest()

    def get(self, line):
        k = self.key(line)
        hit = self.data.get(k)
        if hit is not None and hit.get("v") == SNAP_VERSION:
            self.used.add(k)
            return hit
        return None

    def put(self, line, value):
        k = self.key(line)
        self.data[k] = dict(value, v=SNAP_VERSION)
        self.used.add(k)

    def save(self):
        # Drop entries for lines that no longer exist.
        keep = {k: v for k, v in self.data.items() if k in self.used}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(keep, separators=(",", ":")))
