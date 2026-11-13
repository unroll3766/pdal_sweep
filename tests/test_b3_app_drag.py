"""App-level right-drag zoom check for notes.md item 3.

Drives the real App to a run detail, selects the 3D tab, then sends
synthetic Tk button-3 press/motion/release events to the actual viewer
canvas and asserts the zoom factor increases and the render changes.
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

w = v.canvas.get_tk_widget()
check("canvas mapped and sized",
      w.winfo_ismapped() and w.winfo_width() > 100 and w.winfo_height() > 100)
check("default mplot3d right-zoom disabled", 3 not in v._ax._zoom_btn)
check("our ButtonPress-3 binding present",
      "_on_b3_press" in (w.bind("<ButtonPress-3>") or ""))
check("our B3-Motion binding present",
      "_on_b3_motion" in (w.bind("<B3-Motion>") or ""))

calls = {"press": 0, "motion": 0, "release": 0}
for name, attr, seq in (("press", "_on_b3_press", "<ButtonPress-3>"),
                        ("motion", "_on_b3_motion", "<B3-Motion>"),
                        ("release", "_on_b3_release", "<ButtonRelease-3>")):
    orig = getattr(v, attr)

    def make(orig, name):
        def wrapped(e):
            calls[name] += 1
            return orig(e)
        return wrapped
    setattr(v, attr, make(orig, name))
    w.bind(seq, getattr(v, attr))

buf0 = np.asarray(v.canvas.buffer_rgba()).copy()
zf0 = v._zoom_factor
lim0 = v._ax.get_xlim()

w.event_generate("<ButtonPress-3>", x=400, y=200, when="now")
app.update()
for yy in range(210, 321, 10):
    w.event_generate("<B3-Motion>", x=400, y=yy, when="now")
    app.update()
w.event_generate("<ButtonRelease-3>", x=400, y=320, when="now")
app.update()
time.sleep(0.4)
app.update()

check("handlers fired", calls["press"] >= 1 and calls["motion"] >= 2
      and calls["release"] >= 1)
check("zoom factor increased", v._zoom_factor > zf0)
check("axis limits unchanged (box zoom)", v._ax.get_xlim() == lim0)
buf1 = np.asarray(v.canvas.buffer_rgba()).copy()
ndiff = int((buf0 != buf1).any(axis=2).sum())
check("render changed after drag", ndiff > 0)
print(f"  zoom {zf0} -> {v._zoom_factor:.3f}, {ndiff} px changed")

app.destroy()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
