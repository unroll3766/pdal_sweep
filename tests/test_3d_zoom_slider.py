"""App-level zoom-slider check for notes.md item 3.

Drives the real App to a run detail's 3D tab, moves the zoom slider,
and asserts the box zoom applies (factor, limits unchanged, render
changes) and reset restores 1.0.
"""
import os
import sys
import time

sys.path.insert(0, "/home/theo/Documents/pdal_sweep")
os.chdir("/home/theo/Documents/pdal_sweep")

import numpy as np
import gui

FAIL = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAIL.append(name)


app = gui.App()
app.geometry("1280x820")
app.update()

sweep_id = "20261019_091722_my_sweep"
h = "c60c3a9fdebc"
app.show_detail(sweep_id, h)
app.update()

v = app._panel.viewer3d
deadline = time.time() + 60
while getattr(v, "_ax", None) is None and time.time() < deadline:
    app.update()
    time.sleep(0.2)
check("3D axes loaded", getattr(v, "_ax", None) is not None)

app._panel.nb.select(v.master)
app.update()
time.sleep(0.4)
app.update()

check("zoom slider present in control bar", v.zoom_var.get() == 1.0)
lim0 = v._ax.get_xlim()
buf0 = np.asarray(v.canvas.buffer_rgba()).copy()

v.zoom_var.set(3.0)
v._zoom_changed()          # debounced path used by the slider
time.sleep(0.4)
app.update()
check("debounced slider applies zoom", abs(v._zoom_factor - 3.0) < 1e-9)
check("axis limits unchanged (box zoom)", v._ax.get_xlim() == lim0)
buf1 = np.asarray(v.canvas.buffer_rgba()).copy()
ndiff = int((buf0 != buf1).any(axis=2).sum())
check("render changed after zoom", ndiff > 0)
print(f"  zoom -> {v._zoom_factor:.2f}, {ndiff} px changed")

v._reset_view()
app.update()
time.sleep(0.3)
app.update()
check("reset restores zoom 1", abs(v._zoom_factor - 1.0) < 1e-9
      and abs(v.zoom_var.get() - 1.0) < 1e-9)

app.destroy()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
