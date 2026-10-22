"""PDAL Sweep: grid-search PDAL pipelines and evaluate the results."""

import ctypes
import glob
import hashlib
import os
import sys
import tempfile


def _redirect_pdal_driver_path():
    """Point PDAL_DRIVER_PATH at a directory of symlinks to loadable
    plugins only.

    Conda environments can ship PDAL plugin .so files linked against
    library versions that are no longer installed (e.g.
    libpdal_plugin_reader_hdf built for libhdf5_cpp.so.320 while the env
    provides .310). PDAL tries to load every plugin at initialization
    and prints an error for each broken one, polluting worker logs.
    Symlinking only the plugins that actually dlopen keeps the working
    ones available and silences the noise. A user-set
    PDAL_DRIVER_PATH always wins.
    """
    libdir = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)), "lib")
    plugins = sorted(glob.glob(os.path.join(libdir,
                                            "libpdal_plugin_*.so")))
    if not plugins:
        return
    tag = hashlib.sha256(libdir.encode("utf-8")).hexdigest()[:12]
    d = os.path.join(tempfile.gettempdir(), f"pdal_sweep_drivers_{tag}")
    try:
        os.makedirs(d, exist_ok=True)
        for p in plugins:
            link = os.path.join(d, os.path.basename(p))
            if os.path.exists(link):       # already farmed (follows links)
                continue
            try:
                ctypes.CDLL(p)              # canary: does it load?
            except OSError:
                continue                    # broken plugin: leave it out
            try:
                if os.path.islink(link):    # dangling symlink: replace
                    os.unlink(link)
                os.symlink(p, link)
            except OSError:
                pass
        os.environ["PDAL_DRIVER_PATH"] = d
    except OSError:
        pass


if "PDAL_DRIVER_PATH" not in os.environ:
    _redirect_pdal_driver_path()
