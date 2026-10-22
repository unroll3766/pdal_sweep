"""Sweep runner: executes every grid combination, writes outputs and status.

Each combination runs as an independent unit in a worker process:
  1. build PDAL pipeline JSON, run it via the pdal CLI (writers.las + stats)
  2. decimate the result and cache points for rendering
  3. generate selected outputs: renders, DTM, CHM

A status.json in the sweep directory tracks progress for the GUI.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

from . import outputs as O
from .registry import HAG_FILTERS
from .spec import build_pipeline, expand_spec, pipeline_flags

def _default_pdal_bin():
    if os.environ.get("PDAL_BIN"):
        return os.environ["PDAL_BIN"]
    cand = os.path.join(os.path.dirname(sys.executable), "pdal")
    if os.path.exists(cand):
        return cand
    return shutil.which("pdal") or "pdal"


PDAL_BIN = _default_pdal_bin()


def _write_status(sweep_dir, status):
    tmp = os.path.join(sweep_dir, "status.json.tmp")
    with open(tmp, "w") as f:
        json.dump(status, f, indent=1)
    os.replace(tmp, os.path.join(sweep_dir, "status.json"))


def _run_pdal_pipeline(stages, metadata_path=None, timeout=3600):
    """Run a PDAL JSON pipeline via the CLI; returns (ok, stderr_tail)."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        json.dump(stages, tf)
        path = tf.name
    cmd = [PDAL_BIN, "pipeline", path]
    if metadata_path:
        cmd += ["--metadata", metadata_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        err = (proc.stderr or "")[-2000:]
        return proc.returncode == 0, err
    finally:
        os.unlink(path)


def _parse_meta(metadata_path):
    """Extract point counts and classification histogram from pipeline metadata."""
    try:
        with open(metadata_path) as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    out = {}
    stages = meta.get("stages", {})
    for key, val in stages.items():
        if key.startswith("readers.las") and isinstance(val, dict):
            if isinstance(val.get("count"), int):
                out["input_points"] = val["count"]
            break
    for key, val in stages.items():
        if key.startswith("filters.stats") and isinstance(val, dict):
            stats = val.get("statistic", [])
            if isinstance(stats, dict):
                stats = stats.get("statistic", [])
            for s in stats if isinstance(stats, list) else []:
                if isinstance(s, dict) and s.get("name") == "Classification":
                    if isinstance(s.get("count"), int):
                        out["point_count"] = s["count"]
                    if isinstance(s.get("bins"), dict):
                        out["class_counts"] = {
                            int(float(k)): int(v) for k, v in s["bins"].items()}
                    break
            break
    return out


def run_combo(spec, combo, sweep_dir):
    """Execute one grid combination. Runs inside a worker process."""
    run_dir = os.path.join(sweep_dir, "runs", combo["hash"])
    os.makedirs(run_dir, exist_ok=True)
    t0 = time.time()
    flags = pipeline_flags(combo)
    out = spec.get("outputs", {})

    meta_path = os.path.join(run_dir, "meta.json")
    done_path = os.path.join(run_dir, "DONE")
    if os.path.exists(done_path):
        return {"hash": combo["hash"], "status": "cached"}

    # point cloud export: LAZ by default, LAS via spec point_format
    fmt = str(spec.get("point_format", "laz")).strip().lower()
    ext = "laz" if fmt == "laz" else "las"
    points_las = os.path.join(run_dir, f"{combo['hash']}_points.{ext}")
    stages = build_pipeline(spec, combo)
    writer = {"type": "writers.las", "filename": points_las}
    if ext == "laz":
        writer["compression"] = True
    if flags["has_hag"]:
        writer["extra_dims"] = "HeightAboveGround=double"
    stages.append(writer)
    with open(os.path.join(run_dir, "pipeline.json"), "w") as f:
        json.dump(stages, f, indent=1)

    meta_raw = os.path.join(run_dir, "pdal_metadata.json")
    ok, err = _run_pdal_pipeline(stages, metadata_path=meta_raw)
    if not ok:
        return {"hash": combo["hash"], "status": "error", "error": err}

    pdal_meta = _parse_meta(meta_raw)
    os.unlink(meta_raw)

    # decimated point cache for renders
    budget = int(spec.get("render_budget_points", 2000000))
    warnings = []
    try:
        data = O.load_decimated(points_las, budget)
        O.save_cache(run_dir, data)
        approx = O.compute_stats(data)
    except Exception as e:
        approx = {}
        data = None
        warnings.append(f"render cache failed: {e}")

    result = {
        "hash": combo["hash"],
        "label": combo["label"],
        "steps": [{"id": s[0], "type": s[1], "params": s[2]} for s in combo["steps"]],
        "input_points": pdal_meta.get("input_points"),
        "point_count": pdal_meta.get("point_count") or approx.get("render_points"),
        "class_counts": pdal_meta.get("class_counts"),
        "approx": approx,
        "outputs": {},
        "warnings": warnings,
        "seconds": round(time.time() - t0, 1),
    }

    # renders
    rh = combo["hash"]
    if data is not None:
        if out.get("topdown", True):
            name = f"{rh}_topdown.png"
            with open(os.path.join(run_dir, name), "wb") as f:
                f.write(O.render_topdown(data))
            result["outputs"]["topdown"] = name
        if out.get("crosssection", True):
            name = f"{rh}_xsection.png"
            with open(os.path.join(run_dir, name), "wb") as f:
                f.write(O.render_xsection(data))
            result["outputs"]["xsection"] = name

    # DTM: ground points (or all points if no ground classifier), idw
    res = float(spec.get("raster_resolution", 0.25))
    if out.get("dtm", True):
        dtm_tif = os.path.join(run_dir, f"{rh}_dtm.tif")
        dtm_stages = [{"type": "readers.las", "filename": points_las}]
        if flags["has_ground"]:
            dtm_stages.append({"type": "filters.range",
                               "limits": "Classification[2:2]"})
        dtm_stages.append({"type": "writers.gdal",
                           "filename": dtm_tif,
                           "resolution": res, "output_type": "idw",
                           "gdaldriver": "GTiff", "data_type": "float"})
        ok_dtm, err_dtm = _run_pdal_pipeline(dtm_stages)
        if ok_dtm and os.path.exists(dtm_tif):
            result["outputs"]["dtm"] = os.path.basename(dtm_tif)
            try:
                dtm_png = os.path.join(run_dir, f"{rh}_dtm.png")
                with open(dtm_png, "wb") as f:
                    f.write(O.render_tif_png(dtm_tif,
                                             "terrain", "Z (DTM)"))
                result["outputs"]["dtm_preview"] = os.path.basename(dtm_png)
            except Exception as e:
                result["warnings"] = result.get("warnings", []) + [f"dtm preview failed: {e}"]
        else:
            result["warnings"] = result.get("warnings", []) + [f"dtm failed: {err_dtm}"]
    # CHM: max HeightAboveGround per cell (requires a HAG step)
    if out.get("chm", True):
        chm_tif = os.path.join(run_dir, f"{rh}_chm.tif")
        if flags["has_hag"]:
            chm_stages = [
                {"type": "readers.las", "filename": points_las},
                {"type": "writers.gdal", "filename": chm_tif,
                 "dimension": "HeightAboveGround", "resolution": res,
                 "output_type": "max", "gdaldriver": "GTiff",
                 "data_type": "float"},
            ]
            ok_chm, err_chm = _run_pdal_pipeline(chm_stages)
            if ok_chm and os.path.exists(chm_tif):
                result["outputs"]["chm"] = os.path.basename(chm_tif)
                try:
                    chm_png = os.path.join(run_dir, f"{rh}_chm.png")
                    with open(chm_png, "wb") as f:
                        f.write(O.render_tif_png(chm_tif, "viridis", "height above ground"))
                    result["outputs"]["chm_preview"] = os.path.basename(chm_png)
                except Exception as e:
                    result["warnings"] = result.get("warnings", []) + [
                        f"chm preview failed: {e}"]
            else:
                result["warnings"] = result.get("warnings", []) + [f"chm failed: {err_chm}"]
        else:
            result["warnings"] = result.get("warnings", []) + [
                "chm skipped: no height-above-ground step in the pipeline"]

    if out.get("point_cloud", True):
        result["outputs"]["point_cloud"] = os.path.basename(points_las)

    with open(meta_path, "w") as f:
        json.dump(result, f, indent=1)
    with open(done_path, "w") as f:
        f.write("ok")
    return {"hash": combo["hash"], "status": "done"}


def _input_class_histogram(spec, sweep_dir):
    """One-time scan of the input cloud's Classification values.

    Cached in <sweep_dir>/input_meta.json so resumes skip the extra read
    pass. Returns dict with class_counts and point_count, or None.
    """
    cache = os.path.join(sweep_dir, "input_meta.json")
    if os.path.exists(cache):
        try:
            with open(cache) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    meta_raw = os.path.join(sweep_dir, "input_pdal_metadata.json")
    stages = [
        {"type": "readers.las", "filename": spec["input"]},
        {"type": "filters.stats", "count": "Classification"},
        {"type": "writers.null"},
    ]
    ok, _err = _run_pdal_pipeline(stages, metadata_path=meta_raw, timeout=600)
    result = None
    if ok:
        parsed = _parse_meta(meta_raw)
        if parsed.get("class_counts"):
            result = {
                "class_counts": {str(k): v for k, v in
                                 parsed["class_counts"].items()},
                "point_count": parsed.get("point_count"),
            }
    try:
        if os.path.exists(meta_raw):
            os.unlink(meta_raw)
    except OSError:
        pass
    if result is None:
        return None
    try:
        with open(cache, "w") as f:
            json.dump(result, f, indent=1)
    except OSError:
        pass
    return result


def _sweep_warnings(spec, combos, input_classes):
    """Sweep-level warnings shown as a banner on every page."""
    warnings = []
    out = spec.get("outputs", {})
    step_types = {ftype for c in combos for _sid, ftype, _p, _l in c["steps"]}
    if out.get("chm", True) and not (step_types & HAG_FILTERS):
        warnings.append(
            "canopy height map requested but no height-above-ground step "
            "(filters.hag_nn / hag_delaunay / hag_dem) is in the pipeline: "
            "CHM will not be generated for any run")
    if input_classes:
        nontrivial = {int(k) for k, v in input_classes.get("class_counts", {}).items()
                      if int(v) > 0 and int(k) not in (0, 1)}
        if nontrivial and not (step_types & {"filters.assign"}):
            names = ", ".join(sorted(str(c) for c in nontrivial))
            warnings.append(
                f"the input file already carries classification labels "
                f"(classes {names}); ground/noise filters only relabel ground "
                f"and noise, so pre-existing labels flow through to outputs "
                f"(add a filters.assign step with value='Classification = 0' "
                f"as the first step to reset them)")
    return warnings


def run_sweep(spec, sweep_dir, workers=None):
    """Entry point: run the full grid for a spec, keeping status.json current."""
    combos = expand_spec(spec)
    workers = max(1, int(workers or spec.get("workers", 2)))
    os.makedirs(os.path.join(sweep_dir, "runs"), exist_ok=True)
    # clear any stale stop request from a previous run
    try:
        os.unlink(os.path.join(sweep_dir, "STOP"))
    except FileNotFoundError:
        pass
    # one-time input classification scan (cached across resumes)
    input_classes = _input_class_histogram(spec, sweep_dir)
    sweep_warnings = _sweep_warnings(spec, combos, input_classes)
    # already-completed combinations (DONE marker) resume as cached
    status = {
        "state": "running",
        "name": spec.get("name", "sweep"),
        "input": spec.get("input"),
        "total": len(combos),
        "done": 0,
        "failed": 0,
        "combos": {},
        "input_classes": input_classes,
        "sweep_warnings": sweep_warnings,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    for c in combos:
        done = os.path.exists(os.path.join(sweep_dir, "runs", c["hash"], "DONE"))
        status["combos"][c["hash"]] = {
            "label": c["label"], "state": "cached" if done else "pending"}
    _recount(status)
    _write_status(sweep_dir, status)

    def stop_requested():
        return os.path.exists(os.path.join(sweep_dir, "STOP"))

    if len(combos) == 1 or workers == 1:
        for c in combos:
            if stop_requested():
                _stop(status)
                _write_status(sweep_dir, status)
                return status
            _safe_run(spec, c, sweep_dir, status)
        _finish(status)
        _write_status(sweep_dir, status)
        return status

    ctx = _mp_context()
    ex = ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
    try:
        futures = {ex.submit(_worker_entry, spec, c, sweep_dir): c
                   for c in combos}
        for fut in as_completed(futures):
            c = futures[fut]
            try:
                r = fut.result()
            except Exception as e:  # worker crashed
                r = {"hash": c["hash"], "status": "error",
                     "error": str(e) or type(e).__name__}
            _apply_result(status, c, r)
            _write_status(sweep_dir, status)
            if stop_requested():
                # cancel queued combos; in-flight ones are killed via SIGTERM
                ex.shutdown(wait=False, cancel_futures=True)
                _stop(status)
                _write_status(sweep_dir, status)
                return status
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    _finish(status)
    _write_status(sweep_dir, status)
    return status


def _safe_run(spec, c, sweep_dir, status):
    try:
        r = run_combo(spec, c, sweep_dir)
    except Exception:
        r = {"hash": c["hash"], "status": "error",
             "error": traceback.format_exc()[-800:]}
    _apply_result(status, c, r)
    _write_status(sweep_dir, status)
    return r


def _worker_entry(spec, combo, sweep_dir):
    try:
        return run_combo(spec, combo, sweep_dir)
    except Exception:
        return {"hash": combo["hash"], "status": "error",
                "error": traceback.format_exc()[-800:]}


def _apply_result(status, combo, r):
    entry = status["combos"].get(combo["hash"], {"label": combo["label"]})
    entry["state"] = r.get("status", "error")
    entry["label"] = combo["label"]
    # keep the message even if empty (e.g. str() of CancelledError)
    if r.get("error") is not None:
        entry["error"] = r["error"] or "(no error message)"
    status["combos"][combo["hash"]] = entry
    if r.get("status") in ("done", "cached"):
        status["done"] = sum(1 for e in status["combos"].values()
                             if e.get("state") in ("done", "cached"))
    else:
        status["failed"] = sum(1 for e in status["combos"].values()
                               if e.get("state") == "error")


def _finish(status):
    status["state"] = "finished"
    status["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _stop(status):
    status["state"] = "stopped"
    status["stopped"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _recount(status):
    status["done"] = sum(1 for e in status["combos"].values()
                         if e.get("state") in ("done", "cached"))
    status["failed"] = sum(1 for e in status["combos"].values()
                           if e.get("state") == "error")


def _mp_context():
    import multiprocessing as mp
    try:
        return mp.get_context("spawn")
    except ValueError:
        return mp.get_context()
