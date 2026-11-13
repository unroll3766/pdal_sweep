# Check log

## 2026-11-13 — notes.md feedback fixes (items 1-6)
- Items 1-5 (gui.py): `~/miniconda3/envs/pdal/bin/python tests/test_gui_items.py` — 20/20 PASS (vary checkbox pack order incl. bool params; back-to-results in DetailPanel and ComparePanel; 3D right-drag box zoom with limits unchanged + point-size control; results preview output selector top-down/xsection/DTM/CHM).
- Item 6 unit (runner.py): `~/miniconda3/envs/pdal/bin/python tests/test_runner_bounds.py` — 6/6 PASS (_parse_meta extracts X/Y bounds; _gdal_bounds string; _input_bounds cache/old-cache/missing).
- Item 6 end-to-end: `pdal pipeline` on readers.faux bounds ([0,100],[0,100]) + filters.range X[20:80] + writers.gdal, with vs without explicit `bounds`; gdalinfo shows no-bounds raster 61x101 at origin X~19.2 (shrunk to surviving points) vs with-bounds 101x101 at origin (0,101) (full extent, nodata elsewhere). Also confirmed PDAL bounds order is ([minx,maxx],[miny,maxy]).
- `python -m py_compile gui.py pdal_sweep/runner.py` — OK.

## 2026-11-13 — review pass (re-checked work against notes.md)
- Reviewed full diff of cdfcf23 + b6484c4 against all six notes.md items; one edge found and fixed: Viewer3D right-drag zoom divided by zero when a motion event jumped >= widget height (now guarded in `_on_b3_motion`).
- `tests/test_app_smoke.py` (real App navigation: build -> results -> preview swap to DTM -> detail -> back -> compare -> back -> build): 11/11 PASS.
- `tests/test_gui_items.py` re-run with 2 new zoom-guard checks: 22/22 PASS; `tests/test_runner_bounds.py` re-run: 6/6 PASS.

## 2026-11-13 — item 3 "right-drag zoom does not work" report, investigation
- mpl 3.11.2 source checked: default button-3 zoom only rescales axis limits; our takeover (`mouse_init(zoom_btn=[])`, Tk Button-3 bindings, `set_box_aspect(zoom=...)`) is present in the committed code and bindings persist (mpl binds once at canvas creation).
- `tests/test_b3_app_drag.py`: real App -> run detail -> 3D tab -> synthetic button-3 press/motion/release on the actual canvas: handlers fired, zoom 1.0 -> 1.355, axis limits unchanged, 33566 render pixels changed. 9/9 PASS. All other suites re-run green (22+11+6).
- Failure could not be reproduced headless. Real-X input injection (XTest) was not run: it moves the real pointer and needed approval.

## 2026-11-13 — item 3 fixed after user report (app restarted, right-drag still "did nothing")
- User ran the instrumented launcher (tests/debug_b3.py) and right-dragged twice: log showed our handlers DO fire on real input on the canvas, but zoom only responded to vertical dy with per-event multiplication, so a mostly-horizontal reflex drag (dy=-12 over 182 px) moved zoom 1.0 -> 0.94 — imperceptible.
- Fix: zoom is now press-relative and follows the dominant drag axis (down/right = zoom in), clamped [0.05, 40]; also bound <Double-Button-3> because a rapid second press arrives as Double, not ButtonPress-3.
- tests/test_b3_app_drag.py: 13/13 PASS (vertical 120 px -> zoom 1.429 = H/(H-120); horizontal 150 px -> 1.696; drag up zooms out; huge jump clamped; limits unchanged; 24430 render px changed). test_gui_items.py / test_app_smoke.py / test_runner_bounds.py re-run green.
