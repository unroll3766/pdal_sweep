"""Sweep management: process control, status reading, and sweep creation.

UI-agnostic. Used by the Tkinter GUI; keeps all interaction with the
worker processes (spawn/stop/resume) and the sweeps/ directory layout.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time

from .registry import HAG_FILTERS
from .spec import expand_spec

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWEEPS_DIR = os.path.join(PROJECT_DIR, "sweeps")
MAX_COMBOS = 48

PDAL_BIN = os.environ.get("PDAL_BIN") or os.path.join(
    os.path.dirname(sys.executable), "pdal")

COMMON_OPTIONS = {"user_data", "log", "option_file", "where", "where_merge"}

# live Popen handles for spawned workers (kept so dead ones get reaped)
_WORKERS = []

_STAGE_CACHE = {}


# ---------------------------------------------------------------- stage info

def _infer_type(default):
    if default is None:
        return "str"
    if default in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", default):
        return "num"
    return "str"


def parse_stage_options(text):
    """Parse `pdal --options <stage>` output into param dicts."""
    params = []
    cur = None
    for line in text.splitlines():
        m = re.match(r"^  ([a-z_][a-z0-9_]*)(?: \[(.*)\])?$", line)
        if m:
            name, default = m.group(1), m.group(2)
            cur = {"name": name, "default": default,
                   "type": _infer_type(default),
                   "common": name in COMMON_OPTIONS, "help": ""}
            params.append(cur)
        elif line.startswith("      ") and cur is not None:
            cur["help"] = (cur["help"] + " " + line.strip()).strip()
    return params


def stage_options(stage):
    stage = (stage or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_.]*", stage):
        return None
    if stage not in _STAGE_CACHE:
        try:
            proc = subprocess.run([PDAL_BIN, "--options", stage],
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0 or not proc.stdout.strip():
            _STAGE_CACHE[stage] = None
        else:
            _STAGE_CACHE[stage] = parse_stage_options(proc.stdout)
    return _STAGE_CACHE[stage]


def all_filter_stages():
    if ".__all__" not in _STAGE_CACHE:
        try:
            proc = subprocess.run([PDAL_BIN, "--options", "all"],
                                  capture_output=True, text=True, timeout=60)
            stages = re.findall(r"^(filters\.[a-z0-9_]+)", proc.stdout,
                                re.MULTILINE)
        except (OSError, subprocess.TimeoutExpired):
            stages = []
        _STAGE_CACHE[".__all__"] = sorted(set(stages))
    return _STAGE_CACHE[".__all__"]


# ------------------------------------------------------------ sweep directory

def sweep_dirs():
    if not os.path.isdir(SWEEPS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SWEEPS_DIR)):
        d = os.path.join(SWEEPS_DIR, name)
        if os.path.isfile(os.path.join(d, "spec.json")):
            out.append((name, d))
    return out


def sweep_path(sweep_id):
    for name, d in sweep_dirs():
        if name == sweep_id:
            return d
    return None


def run_dir(sweep_id, run_hash):
    return os.path.join(SWEEPS_DIR, sweep_id, "runs", run_hash)


def read_spec(sweep_id):
    d = sweep_path(sweep_id)
    if not d:
        return None
    try:
        with open(os.path.join(d, "spec.json")) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def read_status_raw(sweep_id):
    d = sweep_path(sweep_id)
    if not d:
        return None
    p = os.path.join(d, "status.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def read_meta(sweep_id, run_hash):
    p = os.path.join(run_dir(sweep_id, run_hash), "meta.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            meta = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    cc = meta.get("class_counts")
    if isinstance(cc, dict):
        meta["class_counts"] = {int(k): v for k, v in cc.items()}
    return meta


def get_status(sweep_id):
    """Status.json if it exists, else a synthetic 'starting' status built
    from the spec, so the UI works in the gap before the worker writes it."""
    status = read_status_raw(sweep_id)
    if status is not None:
        normalize_combos(status)
        return status
    spec = read_spec(sweep_id)
    if spec is None:
        return None
    try:
        combos = expand_spec(spec)
    except Exception:
        combos = []
    return {
        "state": "starting", "name": spec.get("name", sweep_id),
        "input": spec.get("input", ""), "total": len(combos),
        "done": 0, "failed": 0,
        "combos": {c["hash"]: {"label": c["label"], "state": "pending"}
                   for c in combos},
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def normalize_combos(status):
    """Ensure combo entries are dicts with label/state, whatever wrote them."""
    combos = status.get("combos")
    if not isinstance(combos, dict):
        return
    for h, e in combos.items():
        if not isinstance(e, dict):
            combos[h] = {"label": str(e), "state": "unknown"}
        else:
            e.setdefault("label", h)
            e.setdefault("state", "unknown")


def sweep_summaries():
    """One-line summaries of every known sweep, for the sidebar."""
    out = []
    for name, d in sweep_dirs():
        status = read_status_raw(name) or {}
        spec = {}
        try:
            with open(os.path.join(d, "spec.json")) as f:
                spec = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
        out.append({
            "id": name,
            "name": spec.get("name", name),
            "state": status.get("state", "not started"),
            "total": status.get("total", "?"),
            "done": status.get("done", 0),
            "failed": status.get("failed", 0),
            "input": spec.get("input", ""),
            "warnings": status.get("sweep_warnings", []),
        })
    return out


# ---------------------------------------------------------- worker processes

def worker_pid(sweep_id):
    d = sweep_path(sweep_id)
    if not d:
        return None
    p = os.path.join(d, "worker.pid")
    if os.path.exists(p):
        try:
            with open(p) as f:
                return int(f.read().strip())
        except ValueError:
            return None
    return None


def spawn_worker(sweep_id):
    """Start (or restart) the sweep worker; records its pid."""
    d = sweep_path(sweep_id)
    if d is None:
        return None
    try:
        os.unlink(os.path.join(d, "STOP"))
    except FileNotFoundError:
        pass
    log = open(os.path.join(d, "worker.log"), "a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "pdal_sweep.worker",
         os.path.join(d, "spec.json"), d],
        cwd=PROJECT_DIR, stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True)
    with open(os.path.join(d, "worker.pid"), "w") as f:
        f.write(str(proc.pid))
    _WORKERS.append(proc)
    return proc


def worker_alive(pid):
    """True if a worker process is actually running.

    Popen children must be polled to be reaped; an unreaped zombie
    answers os.kill(pid, 0) as if alive. For pids we don't own (e.g. after
    an app restart), fall back to a /proc state check.
    """
    if not pid:
        return False
    for p in _WORKERS:
        if p.pid == pid:
            return p.poll() is None
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return True


def mark_stopped(sweep_id):
    """Backstop: if the worker died without recording it, mark the sweep
    stopped so it can be resumed."""
    d = sweep_path(sweep_id)
    if d is None:
        return
    p = os.path.join(d, "status.json")
    try:
        with open(p) as f:
            status = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    if status.get("state") in ("running", "starting"):
        done = sum(1 for e in status.get("combos", {}).values()
                   if e.get("state") in ("done", "cached"))
        failed = sum(1 for e in status.get("combos", {}).values()
                     if e.get("state") == "error")
        status["state"] = "stopped"
        status["done"], status["failed"] = done, failed
        status["stopped"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, indent=1)
        os.replace(tmp, p)


def stop_sweep(sweep_id):
    """Stop a running sweep; returns when everything is dead."""
    d = sweep_path(sweep_id)
    if d is None:
        return
    with open(os.path.join(d, "STOP"), "w") as f:
        f.write("stop")
    pid = worker_pid(sweep_id)
    if pid and worker_alive(pid):
        try:
            pgid = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(50):  # up to 5s for a graceful stop
                if not worker_alive(pid):
                    break
                time.sleep(0.1)
            # hard-kill the group: pool children blocked inside C calls
            # (pdal bindings) defer SIGTERM until the call returns
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(30):
                if not worker_alive(pid):
                    break
                time.sleep(0.1)
    mark_stopped(sweep_id)


def resume_sweep(sweep_id):
    """Resume or restart a sweep's worker. Returns (ok, error_message)."""
    d = sweep_path(sweep_id)
    if d is None or not os.path.exists(os.path.join(d, "spec.json")):
        return False, "sweep not found"
    pid = worker_pid(sweep_id)
    if pid and worker_alive(pid):
        return False, "this sweep is already running"
    if pid and not worker_alive(pid):
        # a crashed worker can leave orphaned pool children in its old
        # process group (pgid == old worker pid); kill them before restart
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    status = read_status_raw(sweep_id) or {}
    if status.get("state") in ("running", "starting"):
        mark_stopped(sweep_id)
        status = read_status_raw(sweep_id) or {}
    status["state"] = "starting"
    status.pop("error", None)
    status.pop("traceback", None)
    tmp = os.path.join(d, "status.json.tmp")
    with open(tmp, "w") as f:
        json.dump(status, f, indent=1)
    os.replace(tmp, os.path.join(d, "status.json"))
    spawn_worker(sweep_id)
    return True, None


# -------------------------------------------------------------- create sweep

def create_sweep(spec):
    """Validate a spec, create the sweep directory, start the worker.

    Returns (sweep_id, combos, warnings) on success or
    (None, None, error_message) on failure.
    """
    if not spec.get("input") or not os.path.exists(spec["input"]):
        return None, None, "input file does not exist"
    if not spec.get("steps"):
        return None, None, "add at least one pipeline step"
    try:
        combos = expand_spec(spec)
    except Exception as e:
        return None, None, f"could not build grid: {e}"
    if not combos:
        return None, None, "the grid is empty"
    if len(combos) > MAX_COMBOS:
        return None, None, (f"{len(combos)} combinations is too many "
                            f"(max {MAX_COMBOS})")

    warnings = []
    if spec.get("outputs", {}).get("chm", True):
        types = {alt.get("type") for step in spec.get("steps", [])
                 for alt in step.get("alternatives", [])}
        if not (types & HAG_FILTERS):
            warnings.append(
                "canopy height map needs a height-above-ground step "
                "(filters.hag_nn / hag_delaunay / hag_dem); it will not "
                "be generated for this sweep")

    base = re.sub(r"[^a-zA-Z0-9_-]+", "_",
                  spec.get("name") or "sweep").strip("_")
    sweep_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{base}"
    d = os.path.join(SWEEPS_DIR, sweep_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "spec.json"), "w") as f:
        json.dump(spec, f, indent=1)
    with open(os.path.join(d, "status.json"), "w") as f:
        json.dump({
            "state": "starting", "name": spec.get("name", sweep_id),
            "input": spec.get("input", ""), "total": len(combos),
            "done": 0, "failed": 0,
            "combos": {c["hash"]: {"label": c["label"], "state": "pending"}
                       for c in combos},
            "sweep_warnings": warnings,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, indent=1)
    spawn_worker(sweep_id)
    return sweep_id, combos, warnings
