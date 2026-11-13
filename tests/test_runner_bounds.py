"""Checks for notes.md item 6 (runner.py): bounds parsing, gdal bounds
string, input-bounds cache reading. No LAS files involved."""
import json
import os
import sys

sys.path.insert(0, "/home/theo/Documents/pdal_sweep")
from pdal_sweep import runner as R

SCRATCH = os.path.dirname(os.path.abspath(__file__))
FAIL = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAIL.append(name)


meta = {"stages": {
    "readers.las.input": {"count": 1000},
    "filters.stats.stats": {"statistic": [
        {"name": "Classification", "count": 1000, "bins": {"2": 100}},
        {"name": "X", "minimum": -114539.9, "maximum": -114524.91},
        {"name": "Y", "minimum": 183289.75, "maximum": 183309.73},
        {"name": "Z", "minimum": 966.6, "maximum": 968.4},
    ]},
}}
meta_path = os.path.join(SCRATCH, "synthetic_meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f)
out = R._parse_meta(meta_path)
check("item6 parse_meta extracts bounds",
      out.get("bounds") == {"minx": -114539.9, "miny": 183289.75,
                            "maxx": -114524.91, "maxy": 183309.73})
check("item6 parse_meta still gets counts/classes",
      out.get("point_count") == 1000 and out.get("class_counts") == {2: 100}
      and out.get("input_points") == 1000)

s = R._gdal_bounds(out["bounds"])
check("item6 gdal bounds string (PDAL [minx,maxx],[miny,maxy] order)",
      s == "([-114539.9,-114524.91],[183289.75,183309.73])")

cache_dir = os.path.join(SCRATCH, "synthetic_sweep")
os.makedirs(cache_dir, exist_ok=True)
cache_path = os.path.join(cache_dir, "input_meta.json")
with open(cache_path, "w") as f:
    json.dump({"class_counts": {"2": 100}, "point_count": 1000,
               "bounds": out["bounds"]}, f)
check("item6 _input_bounds reads cache",
      R._input_bounds(cache_dir) == out["bounds"])
with open(cache_path, "w") as f:
    json.dump({"class_counts": {"2": 100}}, f)  # old cache, no bounds
check("item6 _input_bounds old cache -> None",
      R._input_bounds(cache_dir) is None)
check("item6 _input_bounds missing dir -> None",
      R._input_bounds(os.path.join(SCRATCH, "nope")) is None)

print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
