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
import hashlib
import json
import math
import urllib.request

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


def positions_along(points, line):
    """For each point, its distance from `line`, and its position (metres from the
    start) along `line`. Searches forward so loops and out-and-back routes work."""
    cum = [0.0]
    for a, b in zip(line, line[1:]):
        cum.append(cum[-1] + _m(a, b))
    out = []
    start = 0
    for p in points:
        best = (float("inf"), start, 0.0)
        # look ahead a limited window first, then the whole rest of the line
        for lo, hi in ((start, min(len(line) - 1, start + 400)), (start, len(line) - 1)):
            for i in range(lo, hi):
                d, t = _project(p, line[i], line[i + 1])
                if d < best[0]:
                    best = (d, i, t)
            if best[0] <= RADIUS_M * 2:
                break
        d, i, t = best
        start = i
        out.append((d, cum[i] + t * (cum[i + 1] - cum[i]) if i + 1 < len(cum) else cum[i]))
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
    pos = positions_along(line, snapped)
    worst = max(d for d, _ in pos)
    strays = sum(1 for d, _ in pos if d > MAX_POINT_DEV_M)
    # A few points inside bus terminals sit off the street grid; allow those.
    if worst > 120 or strays > max(2, 0.05 * len(pos)):
        return None, f"match strays {round(worst)} m from the agency line ({strays} points)"
    ratio = length(snapped) / max(length(line), 1)
    if ratio > MAX_LENGTH_RATIO:
        return None, f"match is {ratio:.2f}x longer than the agency line"
    # Count straight jumps in the agency line that the street path had to go around.
    fixed = 0
    for (a, b), (_, pa), (_, pb) in zip(zip(line, line[1:]), pos, pos[1:]):
        straight = _m(a, b)
        along = pb - pa
        if straight > 40 and along > straight * 1.12 + 15:
            fixed += 1
    return snapped, fixed


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
        if k in self.data:
            self.used.add(k)
            return self.data[k]
        return None

    def put(self, line, value):
        k = self.key(line)
        self.data[k] = value
        self.used.add(k)

    def save(self):
        # Drop entries for lines that no longer exist.
        keep = {k: v for k, v in self.data.items() if k in self.used}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(keep, separators=(",", ":")))
