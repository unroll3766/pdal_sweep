"""Per-run output generation: decimated point cache, top-down and
cross-section renders, DTM/CHM rasters and their PNG previews.

All heavy work happens here, inside worker processes.
"""

import io
import json
import os

import numpy as np

import matplotlib
matplotlib.use("Agg")
# Object-oriented API only: pyplot keeps a global figure registry that
# leaks figures (and memory) when renders run on worker threads.
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def _new_fig(w, h):
    """A fresh Agg figure with no global pyplot state."""
    fig = Figure(figsize=(w, h), dpi=150)
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    return fig, ax

CLASS_COLORS = {
    0: "#4a7ebb",   # never classified
    1: "#7f8c8d",   # unclassified
    2: "#d35400",   # ground
    3: "#27ae60",   # low vegetation
    4: "#2ecc71",   # med vegetation
    5: "#16a085",   # high vegetation
    6: "#e67e22",   # building
    7: "#c0392b",   # noise (low point / noise)
    18: "#c0392b",   # high noise
}
CLASS_NAMES = {
    0: "never classified", 1: "unclassified", 2: "ground", 3: "low veg",
    4: "med veg", 5: "high veg", 6: "building", 7: "noise", 18: "noise",
}


def load_decimated(points_las, budget):
    """Read the run output cloud, decimated to <= budget points.

    Returns dict of numpy arrays: X, Y, Z, Classification, and
    HeightAboveGround when present.
    """
    import pdal
    count = _point_count(points_las)
    step = max(1, int(np.ceil(count / max(1, budget))))
    p = pdal.Reader.las(points_las) | pdal.Filter.decimation(step=step)
    p.execute()
    arr = p.arrays[0]
    dims = {name: arr[name] for name in arr.dtype.names}
    out = {
        "X": dims["X"], "Y": dims["Y"], "Z": dims["Z"],
        "Classification": dims["Classification"].astype(np.int32),
    }
    if "HeightAboveGround" in dims:
        out["HeightAboveGround"] = dims["HeightAboveGround"]
    return out


def _point_count(points_las):
    """Point count from the saved run meta, else a cheap header read."""
    meta_path = os.path.join(os.path.dirname(points_las), "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                m = json.load(f)
            if m.get("point_count"):
                return int(m["point_count"])
        except (json.JSONDecodeError, OSError):
            pass
    import pdal
    info = pdal.Reader.las(points_las).pipeline().quickinfo["readers.las"]
    return info.get("num_points", 0)


def save_cache(run_dir, data):
    np.savez_compressed(os.path.join(run_dir, "render_points.npz"), **data)


def load_cache(run_dir):
    with np.load(os.path.join(run_dir, "render_points.npz")) as z:
        return {k: z[k] for k in z.files}


def render_topdown(data, width=900):
    """Top-down view: X vs Y, colored by max Z per pixel cell."""
    x, y, z = data["X"], data["Y"], data["Z"]
    fig, ax = _new_fig(9, 9)
    x0, x1 = np.floor(x.min()), np.ceil(x.max())
    y0, y1 = np.floor(y.min()), np.ceil(y.max())
    nx = min(width, max(64, int((x1 - x0) * 20)))
    ny = min(width, max(64, int((y1 - y0) * 20)))
    # max Z per cell for a surface impression
    img = np.full((ny, nx), -np.inf)
    ix = np.clip(((x - x0) / (x1 - x0) * (nx - 1)).astype(int), 0, nx - 1)
    iy = np.clip(((y - y0) / (y1 - y0) * (ny - 1)).astype(int), 0, ny - 1)
    np.maximum.at(img, (iy, ix), z)
    img[~np.isfinite(img)] = np.nan
    im = ax.imshow(img, extent=[x0, x1, y0, y1], origin="lower",
                   cmap="terrain", aspect="equal")
    fig.colorbar(im, ax=ax, label="Z (max per cell)", shrink=0.8)
    ax.set_title("Top-down (max Z per cell)")
    ax.set_xlabel("X"); ax.set_ylabel("Y")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def render_xsection(data, frac=0.5, width=2.0):
    """Cross section through a Y strip: X vs Z, colored by classification."""
    x, y, z = data["X"], data["Y"], data["Z"]
    c = data["Classification"]
    y0, y1 = y.min(), y.max()
    yc = y0 + frac * (y1 - y0)
    half = min(width / 2.0, (y1 - y0) / 2.0)
    m = (y >= yc - half) & (y <= yc + half)
    xs, zs, cs = x[m], z[m], c[m]
    fig, ax = _new_fig(12, 6)
    if len(xs) == 0:
        ax.text(0.5, 0.5, "empty strip", ha="center", va="center",
                transform=ax.transAxes)
    else:
        # jitter the strip position slightly so the profile is visible
        order = np.argsort(xs)
        xs, zs, cs = xs[order], zs[order], cs[order]
        seen = np.unique(cs)
        for cls in seen:
            mm = cs == cls
            ax.scatter(xs[mm], zs[mm], s=0.5, c=CLASS_COLORS.get(int(cls), "#7f8c8d"),
                       label=f"{CLASS_NAMES.get(int(cls), 'class ' + str(cls))} ({mm.sum()})",
                       linewidths=0)
        ax.legend(markerscale=8, fontsize=8, loc="upper right")
    ax.set_title(f"Cross section, Y strip {yc - half:.1f} to {yc + half:.1f}")
    ax.set_xlabel("X"); ax.set_ylabel("Z")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def render_tif_png(tif_path, cmap_name="terrain", label="elevation"):
    """Read a single-band float GeoTIFF and return PNG bytes.

    Percentile-stretched for display, but the colorbar shows the true
    cell values, not normalized 0-1.
    """
    from PIL import Image
    img = Image.open(tif_path)
    a = np.asarray(img, dtype=np.float64)
    # PDAL's writers.gdal nodata is -9999; GDAL defaults can be ~-1e30.
    a[a <= -9998.0] = np.nan
    lo, hi = np.nanpercentile(a, 2), np.nanpercentile(a, 98)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(a)) if np.isfinite(np.nanmin(a)) else 0.0
        hi = lo + 1.0
    fig, ax = _new_fig(8, 8)
    im = ax.imshow(a, cmap=cmap_name, origin="upper", vmin=lo, vmax=hi)
    fig.colorbar(im, ax=ax, label=label, shrink=0.8)
    ax.set_title(f"{os.path.basename(tif_path)}  "
                 f"({lo:.2f} to {hi:.2f}, percentile stretch)")
    ax.set_xticks([]); ax.set_yticks([])
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def compute_stats(data):
    """Summary stats from the decimated cache (approximate percentages)."""
    c = data["Classification"]
    stats = {
        "render_points": int(len(c)),
        "ground_points": int((c == 2).sum()),
        "noise_points": int(((c == 7) | (c == 18)).sum()),
    }
    stats["ground_frac"] = round(stats["ground_points"] / max(1, len(c)), 4)
    if "HeightAboveGround" in data:
        h = data["HeightAboveGround"]
        stats["hag_mean"] = round(float(np.mean(h)), 3)
        stats["hag_p95"] = round(float(np.percentile(h, 95)), 3)
    return stats
