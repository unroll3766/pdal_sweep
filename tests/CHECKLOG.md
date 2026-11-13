# Check log

## 2026-11-13 — notes.md feedback fixes (items 1-6)
- Items 1-5 (gui.py): `~/miniconda3/envs/pdal/bin/python tests/test_gui_items.py` — 20/20 PASS (vary checkbox pack order incl. bool params; back-to-results in DetailPanel and ComparePanel; 3D right-drag box zoom with limits unchanged + point-size control; results preview output selector top-down/xsection/DTM/CHM).
- Item 6 unit (runner.py): `~/miniconda3/envs/pdal/bin/python tests/test_runner_bounds.py` — 6/6 PASS (_parse_meta extracts X/Y bounds; _gdal_bounds string; _input_bounds cache/old-cache/missing).
- Item 6 end-to-end: `pdal pipeline` on readers.faux bounds ([0,100],[0,100]) + filters.range X[20:80] + writers.gdal, with vs without explicit `bounds`; gdalinfo shows no-bounds raster 61x101 at origin X~19.2 (shrunk to surviving points) vs with-bounds 101x101 at origin (0,101) (full extent, nodata elsewhere). Also confirmed PDAL bounds order is ([minx,maxx],[miny,maxy]).
- `python -m py_compile gui.py pdal_sweep/runner.py` — OK.
