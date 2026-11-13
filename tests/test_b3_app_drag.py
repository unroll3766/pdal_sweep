"""App-level right-drag zoom checks for notes.md item 3.

Drives the real App to a run detail, selects the 3D tab, then sends
synthetic Tk button-3 events to the actual viewer canvas and asserts:
vertical drag down zooms in by the expected factor, horizontal drag
zooms too, drag up zooms out, axis limits stay fixed, the render
changes, and a huge jump is clamped without error.
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

W, H = w.winfo_width(), w.winfo_height()
buf0 = np.asarray(v.canvas.buffer_rgba()).copy()
lim0 = v._ax.get_xlim()


def drag(x0, y0, x1, y1, steps=6):
    # real timestamps > Tk's doubleClickTime apart per press, so a press
    # is not collapsed into Double-Button-3
    time.sleep(0.6)
    w.event_generate("<ButtonPress-3>", x=x0, y=y0, when="now")
    app.update()
    for i in range(1, steps + 1):
        w.event_generate("<B3-Motion>", x=x0 + (x1 - x0) * i // steps,
                         y=y0 + (y1 - y0) * i // steps, when="now")
        app.update()
    w.event_generate("<ButtonRelease-3>", x=x1, y=y1, when="now")
    app.update()


# vertical drag down 120 px: zoom = H / (H - 120)
drag(400, 200, 400, 320)
expected = H / (H - 120)
check("vertical drag down zooms in by expected factor",
      abs(v._zoom_factor - expected) < 0.02)
print(f"  zoom 1.0 -> {v._zoom_factor:.3f} (expected ~{expected:.3f})")

# horizontal drag right 150 px: zoom = W / (W - 150)
z0 = v._zoom_factor
drag(400, 200, 550, 200)
expected = z0 * W / (W - 150)
check("horizontal drag right zooms in", abs(v._zoom_factor - expected) < 0.02
      and v._zoom_factor > z0)
print(f"  zoom {z0:.3f} -> {v._zoom_factor:.3f} (expected ~{expected:.3f})")

# drag up 120 px: zoom out
z0 = v._zoom_factor
drag(400, 300, 400, 180)
check("drag up zooms out", v._zoom_factor < z0)
print(f"  zoom {z0:.3f} -> {v._zoom_factor:.3f}")

# huge jump (delivered directly: out-of-widget synthetic events get
# dropped by Tk): clamped, no exception
z0 = v._zoom_factor


class E:
    pass


e = E()
e.widget = w
e.x, e.y = 400, 200
v._on_b3_press(e)
e.y = 200 + 10 * H
try:
    v._on_b3_motion(e)
    ok = True
except Exception:
    ok = False
check("huge drag clamped without error", ok and 0.05 <= v._zoom_factor <= 40.0)

check("handlers fired", calls["press"] >= 3 and calls["motion"] >= 3
      and calls["release"] >= 3)
print("  handler calls:", calls)
check("axis limits unchanged (box zoom)", v._ax.get_xlim() == lim0)
time.sleep(0.4)
app.update()
buf1 = np.asarray(v.canvas.buffer_rgba()).copy()
ndiff = int((buf0 != buf1).any(axis=2).sum())
check("render changed after drags", ndiff > 0)
print(f"  {ndiff} px changed")

v._reset_view()
app.update()
check("reset restores zoom 1", abs(v._zoom_factor - 1.0) < 1e-9)

app.destroy()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
