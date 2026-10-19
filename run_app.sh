#!/bin/bash
# Launch the PDAL Sweep desktop GUI (Tkinter).
#
# The app needs a Python environment with the PDAL toolchain (see
# README.md -> Requirements). Point PDAL_SWEEP_PYTHON at that interpreter,
# e.g.:
#   PDAL_SWEEP_PYTHON=~/miniconda3/envs/pdal/bin/python ./run_app.sh
# With no variable set, the python from PATH is used.
cd "$(dirname "$0")"
exec "${PDAL_SWEEP_PYTHON:-python}" gui.py
