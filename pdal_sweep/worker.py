"""Worker entry point: python -m pdal_sweep.worker <spec.json> <sweep_dir>"""

import json
import os
import signal
import sys

from pdal_sweep.runner import _recount, _stop, _write_status


def _sigterm_handler(sweep_dir):
    def handler(signum, frame):
        # stopping: record it in status.json, then die without waiting
        # for the process pool (its children were signalled as a group)
        try:
            path = os.path.join(sweep_dir, "status.json")
            with open(path) as f:
                status = json.load(f)
            if status.get("state") in ("running", "starting"):
                _recount(status)
                _stop(status)
                _write_status(sweep_dir, status)
        except Exception:
            pass
        os._exit(0)
    return handler


def main():
    spec_path, sweep_dir = sys.argv[1], sys.argv[2]
    with open(spec_path) as f:
        spec = json.load(f)
    signal.signal(signal.SIGTERM, _sigterm_handler(sweep_dir))
    from pdal_sweep.runner import run_sweep
    try:
        status = run_sweep(spec, sweep_dir)
    except Exception as e:
        # record the crash in status.json so the UI shows it
        import traceback
        status_path = os.path.join(sweep_dir, "status.json")
        try:
            with open(status_path) as f:
                status = json.load(f)
        except Exception:
            status = {"combos": {}}
        status["state"] = "error"
        status["error"] = str(e)
        status["traceback"] = traceback.format_exc()[-2000:]
        with open(status_path, "w") as f:
            json.dump(status, f, indent=1)
        raise
    if status.get("state") == "stopped":
        # in-flight pool children may still be running; don't block on them
        os._exit(0)


if __name__ == "__main__":
    main()
