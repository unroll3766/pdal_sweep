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

## 2026-11-13 — item 3 changed to a zoom slider (user on macOS: right-drag takeover not worth it)
- Reverted all custom mouse handling in Viewer3D (mouse_init override, Button-3/Double-Button-3/B3-Motion/Release bindings, drag handlers, _right_button helper): stock mplot3d mouse behavior is back (left-drag rotate, right-drag axis-limits zoom, as before).
- Zoom that actually scales the points is now a "zoom" slider in the 3D control bar (0.2-5.0, default 1.0), applied as a box zoom via set_box_aspect(aspect, zoom=...), debounced 150 ms; reset view restores 1.0. Platform-independent, no button-number assumptions.
- tests/test_3d_zoom_slider.py (app-level: debounced slider applies zoom 3.0, limits unchanged, 72289 render px changed, reset restores 1.0): 7/7 PASS. tests/test_gui_items.py item3 section rewritten for the slider: PASS. test_app_smoke.py / test_runner_bounds.py re-run green.

## 2026-11-13 — repo review + cleanup (user request)
- Audited hosted tree (git ls-files): only project sources + tests/ (4 test files + this log); no debug/instrumentation code anywhere (grep for debug/XTest/event_generate/print in gui.py and pdal_sweep/ is clean; debug launcher and its log were removed before commit). Big LAS files, sweeps/, __pycache__ are gitignored.
- All 4 test suites re-run green after cleanup. README updated to match the UI (zoom slider + point size in 3D view, preview output selector, back-to-results buttons, full-extent DTM/CHM rasters).
