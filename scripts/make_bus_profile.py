"""Make an OSRM 'bus' profile from the stock car profile.

Changes from car.lua:
  * bus=yes / psv=yes open roads that are closed to cars (bus lanes, terminals)
  * highway=busway is drivable
  * one-way streets are ignored, because buses use contraflow lanes and we only
    need the line to follow the right street, not to obey turn rules
Fails loudly if car.lua changed shape, so a silent mismatch can't slip through.
"""
import sys
from pathlib import Path

src, dst = Path(sys.argv[1]), Path(sys.argv[2])
s = src.read_text()
changes = [
    ("oneway_handling           = true,", "oneway_handling           = false,"),
    ("access_tags_hierarchy = Sequence {\n      'motorcar',",
     "access_tags_hierarchy = Sequence {\n      'bus',\n      'psv',\n      'motorcar',"),
    ("        service         = 15,", "        service         = 15,\n        busway          = 40,"),
]
for old, new in changes:
    if s.count(old) != 1:
        sys.exit(f"car.lua changed: could not find {old!r}")
    s = s.replace(old, new)
dst.write_text(s)
print(f"wrote {dst}")
