"""Registry of PDAL filters exposed in the sweep UI.

Each entry describes the filter and its parameters so the web builder can
render a form. Types: int, float, bool, str, enum. A default of None means
the parameter is optional and is omitted from the pipeline unless the user
provides a value. Option names and defaults follow the installed
PDAL 2.10.x stage options.
"""

REGISTRY = {
    "filters.outlier": {
        "label": "Outlier removal",
        "desc": "Statistical or radius-based outlier detection. Marks noise as Classification 7. "
                "Add a filters.range step (Classification![7:7]) to actually drop the noise points.",
        "params": [
            {"name": "method", "type": "enum", "default": "statistical",
             "options": ["statistical", "radius"],
             "help": "statistical: mean-k distance test; radius: neighborhood count test"},
            {"name": "mean_k", "type": "int", "default": 8,
             "help": "Mean number of neighbors in the kdtree (statistical)"},
            {"name": "multiplier", "type": "float", "default": 2.0,
             "help": "Stddev multiplier threshold (statistical)"},
            {"name": "radius", "type": "float", "default": 1.0,
             "help": "Neighborhood radius (radius method)"},
            {"name": "min_k", "type": "int", "default": 2,
             "help": "Minimum neighbors within radius (radius method)"},
            {"name": "class", "type": "int", "default": 7,
             "help": "Classification value assigned to outliers"},
        ],
    },
    "filters.elm": {
        "label": "Extended local minimum (ELM)",
        "desc": "Marks low-lying noise as Classification 7 within gridded cells.",
        "params": [
            {"name": "cell", "type": "float", "default": 10.0, "help": "Cell size"},
            {"name": "threshold", "type": "float", "default": 1.0,
             "help": "Threshold above local minimum to consider noise"},
            {"name": "class", "type": "int", "default": 7,
             "help": "Classification value assigned to noise"},
        ],
    },
    "filters.range": {
        "label": "Range / expression filter",
        "desc": "Keeps only points matching a limit expression, e.g. Classification![7:7] "
                "(drop noise) or Classification[2:2] (ground only).",
        "params": [
            {"name": "limits", "type": "str", "default": "Classification![7:7]",
             "help": "PDAL range expression"},
        ],
    },
    "filters.smrf": {
        "label": "SMRF ground classification",
        "desc": "Simple Morphological Filter. Marks ground points as Classification 2.",
        "params": [
            {"name": "cell", "type": "float", "default": 1.0, "help": "Cell size"},
            {"name": "slope", "type": "float", "default": 0.15, "help": "Percent slope"},
            {"name": "scalar", "type": "float", "default": 1.25,
             "help": "Elevation scalar for snow/low noise"},
            {"name": "threshold", "type": "float", "default": 0.5,
             "help": "Slope threshold for low points"},
            {"name": "cut", "type": "float", "default": 0.0, "help": "Cut net size limit (0 to disable)"},
            {"name": "window", "type": "float", "default": None,
             "help": "Max window size (default: computed)"},
            {"name": "ignore", "type": "str", "default": "Classification[7:7]",
             "help": "Range expression of points to ignore"},
            {"name": "returns", "type": "str", "default": "last, only",
             "help": "Return types to consider"},
            {"name": "ground_class", "type": "int", "default": 2, "help": "Ground class value"},
            {"name": "only_ground", "type": "bool", "default": False,
             "help": "Keep only ground points in the output"},
        ],
    },
    "filters.csf": {
        "label": "CSF ground classification",
        "desc": "Cloth Simulation Filter. Marks ground points as Classification 2.",
        "params": [
            {"name": "resolution", "type": "float", "default": 1.0,
             "help": "Cloth grid step (post spacing)"},
            {"name": "threshold", "type": "float", "default": 0.5,
             "help": "Height threshold for classifying points as ground"},
            {"name": "hdiff", "type": "float", "default": 0.3,
             "help": "Height difference threshold (smooth post-processing)"},
            {"name": "step", "type": "float", "default": 0.65, "help": "Time step"},
            {"name": "rigidness", "type": "int", "default": 3,
             "help": "1: steep slopes, 2: rugged terrain, 3: flat/forested"},
            {"name": "iterations", "type": "int", "default": 500, "help": "Max cloth iterations"},
            {"name": "smooth", "type": "bool", "default": True,
             "help": "Post-process for steep slopes"},
            {"name": "ignore", "type": "str", "default": "Classification[7:7]",
             "help": "Range expression of points to ignore"},
            {"name": "returns", "type": "str", "default": "last, only",
             "help": "Return types to consider"},
            {"name": "ground_class", "type": "int", "default": 2, "help": "Ground class value"},
            {"name": "only_ground", "type": "bool", "default": False,
             "help": "Keep only ground points in the output"},
        ],
    },
    "filters.pmf": {
        "label": "PMF ground classification",
        "desc": "Progressive Morphological Filter. Marks ground points as Classification 2.",
        "params": [
            {"name": "cell_size", "type": "float", "default": 1.0, "help": "Cell size"},
            {"name": "exponential", "type": "bool", "default": True,
             "help": "Use exponential window growth"},
            {"name": "initial_distance", "type": "float", "default": 0.15,
             "help": "Initial height threshold"},
            {"name": "max_distance", "type": "float", "default": 2.5,
             "help": "Max height threshold"},
            {"name": "max_window_size", "type": "int", "default": 33, "help": "Max window size"},
            {"name": "slope", "type": "float", "default": 1.0, "help": "Slope"},
            {"name": "ignore", "type": "str", "default": "Classification[7:7]",
             "help": "Range expression of points to ignore"},
            {"name": "returns", "type": "str", "default": "last, only",
             "help": "Return types to consider"},
            {"name": "ground_class", "type": "int", "default": 2, "help": "Ground class value"},
            {"name": "only_ground", "type": "bool", "default": False,
             "help": "Keep only ground points in the output"},
        ],
    },
    "filters.hag_nn": {
        "label": "Height above ground (nearest neighbor)",
        "desc": "Computes HeightAboveGround for each point from the nearest ground point.",
        "params": [
            {"name": "count", "type": "int", "default": 1,
             "help": "Number of nearest ground points to consider"},
            {"name": "max_distance", "type": "float", "default": None,
             "help": "Max distance to search for ground points"},
            {"name": "allow_extrapolation", "type": "bool", "default": True,
             "help": "Allow extrapolation beyond ground points"},
            {"name": "class", "type": "int", "default": 2,
             "help": "Classification value of ground points"},
        ],
    },
    "filters.hag_delaunay": {
        "label": "Height above ground (Delaunay)",
        "desc": "Computes HeightAboveGround by interpolation over a Delaunay triangulation of ground points.",
        "params": [
            {"name": "count", "type": "int", "default": 10,
             "help": "Max ground points used to build triangles"},
            {"name": "allow_extrapolation", "type": "bool", "default": True,
             "help": "Allow extrapolation beyond ground points"},
            {"name": "class", "type": "int", "default": 2,
             "help": "Classification value of ground points"},
        ],
    },
    "filters.hag_dem": {
        "label": "Height above ground (DEM raster)",
        "desc": "Computes HeightAboveGround against a raster DEM you provide (e.g. a dtm.tif "
                "produced by an earlier sweep run).",
        "params": [
            {"name": "raster", "type": "str", "default": None,
             "help": "Path to the DEM raster (required)"},
            {"name": "band", "type": "int", "default": 1, "help": "Raster band to use"},
            {"name": "zero_ground", "type": "bool", "default": True,
             "help": "Set HAG to 0 for ground points"},
            {"name": "nodata_hag", "type": "float", "default": 0.0,
             "help": "HAG value where DEM is nodata"},
        ],
    },
    "filters.decimation": {
        "label": "Decimation",
        "desc": "Keeps every Nth point.",
        "params": [
            {"name": "step", "type": "int", "default": 10, "help": "Keep every Nth point"},
            {"name": "offset", "type": "int", "default": 0, "help": "Offset to start decimation"},
            {"name": "limit", "type": "int", "default": None,
             "help": "Max number of points to keep"},
        ],
    },
    "filters.voxelcentroidnearestneighbor": {
        "label": "Voxel downsample",
        "desc": "Downsamples to one point per voxel (nearest neighbor to voxel centroid).",
        "params": [
            {"name": "cell", "type": "float", "default": 0.5, "help": "Voxel cell size"},
        ],
    },
    "filters.iqr": {
        "label": "IQR outlier filter",
        "desc": "Marks outliers in a dimension using the interquartile range as Classification 7.",
        "params": [
            {"name": "dimension", "type": "str", "default": "Z", "help": "Dimension to test"},
            {"name": "k", "type": "float", "default": 1.5, "help": "IQR multiplier"},
        ],
    },
    "filters.returns": {
        "label": "Returns filter",
        "desc": "Keeps points by return type, e.g. last-of-many.",
        "params": [
            {"name": "groups", "type": "str", "default": "last",
             "help": "Comma-separated: only, last, first, intermediate, lastofmany"},
        ],
    },
    "filters.sort": {
        "label": "Sort",
        "desc": "Sorts points by one or more dimensions.",
        "params": [
            {"name": "dimensions", "type": "str", "default": "Z",
             "help": "Comma-separated dimensions to sort by"},
            {"name": "order", "type": "enum", "default": "ASC",
             "options": ["ASC", "DESC"], "help": "Sort order"},
        ],
    },
    "filters.reprojection": {
        "label": "Reprojection",
        "desc": "Reprojects the point cloud.",
        "params": [
            {"name": "out_srs", "type": "str", "default": None,
             "help": "Target SRS (required)"},
            {"name": "in_srs", "type": "str", "default": None,
             "help": "Input SRS (usually autodetected)"},
        ],
    },
}

GROUND_FILTERS = {"filters.smrf", "filters.csf", "filters.pmf"}
HAG_FILTERS = {"filters.hag_nn", "filters.hag_delaunay", "filters.hag_dem"}
NOISE_CLASSES = {7, 18}  # PDAL noise classifications


def coerce_value(filter_type, name, value):
    """Coerce a form value to the type declared in the registry (best effort)."""
    pdef = None
    fdef = REGISTRY.get(filter_type, {})
    for p in fdef.get("params", []):
        if p["name"] == name:
            pdef = p
            break
    if pdef is None:
        # unknown/custom param: try int, float, bool, else string
        if isinstance(value, str):
            s = value.strip()
            try:
                return int(s)
            except ValueError:
                try:
                    return float(s)
                except ValueError:
                    if s.lower() in ("true", "false"):
                        return s.lower() == "true"
                    return s
        return value
    t = pdef["type"]
    try:
        if t == "int":
            return int(float(value))
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes", "on")
            return bool(value)
        if t == "enum":
            return str(value)
        return str(value)
    except (TypeError, ValueError):
        return value


def short_name(filter_type):
    return filter_type.split(".", 1)[1] if "." in filter_type else filter_type
