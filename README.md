# PDAL Sweep

Grid search PDAL pipeline parameters and visually examine their impact on
point cloud quality. Desktop application (Tkinter) on top of a PDAL
installation.

## Requirements

- Python 3.9+ with Tkinter (Debian/Ubuntu: `python3-tk`)
- PDAL 2.x (the CLI `pdal` on PATH alongside the python interpreter,
  or pointed to by the launcher)
- numpy, matplotlib, pillow (`requirements.txt`)

The easiest setup is a conda environment that provides everything:

```bash
conda create -n pdal -c conda-forge python pdal numpy matplotlib pillow
conda activate pdal
```

## Run

```bash
./run_app.sh          # launches the Tkinter GUI
```

The launcher uses the `python` on PATH; with multiple environments,
point it explicitly:

```bash
PDAL_SWEEP_PYTHON=~/miniconda3/envs/pdal/bin/python ./run_app.sh
```

The builder defaults its input to `sample.las` in the repo root if
present; use the browse button to pick any LAS/LAZ file.

## How it works

1. **Build** ("new sweep"): define a pipeline as a sequence of steps.
   Filter stages are free-form (any installed PDAL `filters.*`; suggestions
   come from the combobox) and each one's parameters pre-fill with the
   installed PDAL's documented defaults (`pdal --options <stage>`). Every
   parameter has a *set* toggle: on sends the value to PDAL, off omits it
   entirely so PDAL applies its own default (common stage options default
   to off, which also avoids PDAL's `where_merge`-without-`where`
   rejection; the runner drops that invalid pair defensively too). Check
   *vary* and enter a comma-separated list (at least one value) to turn it
   into a grid axis. A step may also have several *alternative filters*
   (e.g. SMRF *or* CSF). The cartesian product over all alternatives and
   vary-lists is the grid; the live counter shows how many combinations it
   expands to.
2. **Outputs** per combination are selectable (each file is prefixed with
   the run's short hash, e.g. `5524b62cadcc_points.laz`, so files copied
   out of different run folders stay identifiable):
   - saved point cloud (`points.laz` by default, LAZ-compressed via PDAL's
     `writers.las`; set *cloud format* to `las` for uncompressed — with
     `HeightAboveGround` as extra dim when a HAG step is present)
   - top-down render (max Z per cell)
   - cross section (X-Z profile through a movable Y strip, colored by
     classification)
   - DTM (`dtm.tif`, ground points, IDW)
   - canopy height map (`chm.tif`, max height above ground per cell)
   Every run also stores the exact `pipeline.json` used, and point/class
     statistics in `meta.json`.
3. **Results**: the sweep list on the left shows state and progress;
   selecting a sweep shows live status with stop/resume. The results view
   lists all runs with point counts and ground fraction; select one (or
   ctrl-click two) to open the detail view or the side-by-side comparison.
   Runs can be **marked good/bad** (buttons or g/b/c keys) — marks persist
   per sweep in `ratings.json`. **export results** zips chosen outputs
   (point cloud, DTM, CHM, renders) for all/good/bad/selected runs into an
   archive named after the sweep, one subdirectory per output kind
   (`points/`, `dtm/`, ...); LAZ point clouds are stored uncompressed
   inside the zip (they are already compressed).
4. **Detail**: tabs for top-down, cross-section (strip position/width
   controls, rendered off the UI thread), a 3D scatter view
   (drag-to-rotate, one show/hide toggle per classification class, a
   point-count budget for interactive speed), DTM and canopy height, a
   class table with per-class output vs input counts, warnings, and an
   "open folder" button for the run's files.
5. **Compare**: any two runs side by side across the same output tabs,
   with a synced cross-section slider.

Builder conveniences: steps can be reordered with **up**/**down**, each
alternative filter can be **collapsed** for readability, **reset
defaults** restores the installed PDAL's documented defaults for a filter
(discarding edits and custom params), and **+ skip** adds a "no filter at
this step" alternative so the grid can test a step's inclusion (e.g.
with vs without an outlier filter). Skip alternatives round-trip through
spec import/export like any other.

The grid search definition can be shared as JSON: "export JSON" in the
builder writes the current spec (steps, alternatives, vary lists, set
toggles, outputs, and run settings); "import JSON" loads one back into
the builder. The format is the same `spec.json` stored inside every sweep
directory, so a sweep's exact definition can be reused by importing its
`spec.json` directly.

All images support wheel zoom, drag pan, and double-click reset.

## Stopping and resuming

- The progress view has **stop** while a sweep is executing and **resume**
  when it is stopped, errored, finished, or when its worker has died
  (shown as "worker not running").
- Stop signals the worker's whole process group (worker + pool children +
  pdal processes), so nothing keeps burning CPU; status.json is marked
  `stopped` by the worker, or by the manager as a backstop.
- Completed combinations are cached on disk (`runs/<hash>/DONE`), so a
  resumed sweep re-runs only the combinations that never finished. Even
  combos finished by orphaned worker children after a hard crash are
  picked up on the next start. Resume also works to retry combos that
  previously errored.

## Layout

```
pdal_sweep/            core library
  registry.py          filter catalog (option names/defaults match the
                       installed PDAL; verify with `pdal --options <stage>`)
  spec.py               spec -> grid expansion, PDAL pipeline builder
  runner.py            parallel grid execution (multiprocessing), caching,
                       DTM/CHM generation, input classification scan
  outputs.py            decimated render cache (npz), matplotlib renders
  manager.py            UI-agnostic sweep control: worker processes,
                       status reading, stop/resume, sweep creation
  worker.py             `python -m pdal_sweep.worker` entry point
gui.py                 Tkinter desktop app
sweeps/                sweep results (one dir per sweep, one per
                       combination under runs/<hash>/; DONE marker enables
                       result caching)
```

## Notes

- Filters that *mark* noise (outlier, ELM) assign Classification 7; add a
  `filters.range` step (`Classification![7:7]`) afterwards to drop those
  points, or they flow into later stages.
- `filters.hag_dem` needs a DEM raster path (e.g. a `dtm.tif` from a
  previous run); the other HAG filters classify from the point cloud
  directly.
- The input file's classification labels are scanned once per sweep (cached
  across resumes). If the input already carries classes (e.g. this
  project's test LAS ships pre-classified with ground and vegetation
  codes), a warning explains that ground/noise filters only relabel ground
  and noise, so pre-existing labels flow through - the default pipeline
  starts with a `filters.assign` step (`value='Classification = 0'`) that
  resets them. Run detail
  views show per-class output counts alongside the input counts.
- Requesting a canopy height map without a height-above-ground step warns
  before starting; no CHM is generated in that case.
- A full-scale run over the 27.5M-point test LAS (~1GB) takes roughly
  10-14 min per combination with 2 workers on 4 cores (~2.2 GB RAM per
  worker). The default input is `sample.las` (~12M points, 440MB) for
  faster iteration. Completed combinations are cached: re-running the
  same sweep directory reuses them.
- The grid limit, worker count, raster resolution, and render point budget
  are all configurable in the build form.
