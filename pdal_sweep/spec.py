"""Pipeline spec model: expand a sweep spec into grid combinations and build
the concrete PDAL pipeline JSON for each combination.

Spec format (JSON):

{
  "name": "my sweep",
  "input": "/path/to.las",
  "workers": 2,
  "raster_resolution": 0.25,
  "render_budget_points": 600000,
  "outputs": {"point_cloud": true, "dtm": true, "chm": true,
              "topdown": true, "crosssection": true},
  "steps": [
    {"id": "noise", "alternatives": [
       {"type": "filters.outlier", "params": {
           "method": {"vary": true, "values": ["statistical", "radius"]},
           "mean_k": {"vary": false, "value": 8}}}
    ]},
    {"id": "ground", "alternatives": [
       {"type": "filters.smrf", "params": {"cell": {"vary": true, "values": [1.0, 2.0]}}},
       {"type": "filters.csf", "params": {}}
    ]}
  ]
}

A step with multiple alternatives is itself a grid axis: each alternative is a
distinct option, and each alternative's varying params expand further.
The full grid is the cartesian product over all steps.
"""

import hashlib
import itertools

from .registry import GROUND_FILTERS, HAG_FILTERS, coerce_value, short_name


def _expand_alternative(alt):
    """Yield (filter_type, concrete_params, label) for one alternative."""
    ftype = alt["type"]
    raw = alt.get("params", {}) or {}
    fixed = {}
    varying = {}
    for name, pv in raw.items():
        if isinstance(pv, dict) and pv.get("vary"):
            vals = [coerce_value(ftype, name, v)
                    for v in pv.get("values", []) if v is not None and v != ""]
            if vals:
                varying[name] = vals
        else:
            if isinstance(pv, dict):
                value = pv.get("value")
            else:
                value = pv
            if value is None or value == "":
                continue
            fixed[name] = coerce_value(ftype, name, value)
    if not varying:
        label = short_name(ftype)
        yield ftype, fixed, label
        return
    names = sorted(varying)
    for values in itertools.product(*[varying[n] for n in names]):
        params = dict(fixed)
        params.update(zip(names, values))
        parts = []
        for n in names:
            v = params[n]
            parts.append(f"{n}={v}")
        yield ftype, params, f"{short_name(ftype)}({','.join(parts)})"


def expand_spec(spec):
    """Expand a spec into a list of combinations.

    Each combo: {"hash", "label", "steps": [(step_id, filter_type, params, label), ...]}
    """
    step_options = []  # list of (step_id, [(ftype, params, label), ...])
    for step in spec.get("steps", []):
        opts = []
        for alt in step.get("alternatives", []):
            opts.extend(_expand_alternative(alt))
        if not opts:
            continue
        step_options.append((step.get("id") or short_name(opts[0][0]), opts))
    if not step_options:
        return []
    combos = []
    for choice in itertools.product(*[opts for _, opts in step_options]):
        steps = []
        labels = []
        for (sid, _), (ftype, params, olabel) in zip(step_options, choice):
            steps.append((sid, ftype, params, olabel))
            labels.append(f"{sid}={olabel}")
        h = hashlib.sha256(repr(steps).encode("utf-8")).hexdigest()[:12]
        combos.append({
            "hash": h,
            "label": " | ".join(labels),
            "steps": steps,
        })
    return combos


def combo_summary(combo):
    """Flat dict of step -> filter type and params, for display."""
    out = {}
    for sid, ftype, params, _ in combo["steps"]:
        out[sid] = {"type": ftype, "params": params}
    return out


def build_pipeline(spec, combo):
    """Build the PDAL JSON pipeline (list of stages) for one combination."""
    stages = [{"type": "readers.las", "filename": spec["input"]}]
    for _sid, ftype, params, _label in combo["steps"]:
        stage = {"type": ftype}
        for name, value in params.items():
            # PDAL rejects where_merge without where; drop the invalid pair
            if name == "where_merge" and "where" not in params:
                continue
            stage[name] = value
        stages.append(stage)
    # stats filter gives us an exact classification histogram in metadata
    stages.append({"type": "filters.stats", "count": "Classification"})
    return stages


def pipeline_flags(combo):
    """Return pipeline feature flags for a combo."""
    types = {ftype for _sid, ftype, _p, _l in combo["steps"]}
    return {
        "has_ground": bool(types & GROUND_FILTERS),
        "has_hag": bool(types & HAG_FILTERS),
    }


def estimate_total(spec):
    """Number of combinations the spec expands to."""
    return len(expand_spec(spec))
