"""Headless checks for notes.md items 1-5 (gui.py changes). Run with the
pdal env python from the repo root. Prints PASS/FAIL per check."""
import os
import sys

sys.path.insert(0, "/home/theo/Documents/pdal_sweep")
os.chdir("/home/theo/Documents/pdal_sweep")

import tkinter as tk
import numpy as np
import gui
import pdal_sweep.manager as M

FAIL = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAIL.append(name)


class FakeApp:
    def __init__(self):
        self.calls = []

    def set_help(self, t):
        pass

    def update_combo_count(self):
        pass

    def submit(self, owner, job, done):
        pass

    def cancel_pending(self, owner):
        pass

    def show_results(self, sweep_id, force=False):
        self.calls.append(("results", sweep_id))

    def show_detail(self, s, h):
        self.calls.append(("detail", s, h))

    def show_compare(self, s, a, b):
        pass


def find_button(widget, text):
    for w in widget.winfo_children():
        if isinstance(w, tk.Button) and w.cget("text") == text:
            return w
        r = find_button(w, text)
        if r is not None:
            return r
    return None


root = tk.Tk()
root.geometry("1200x800")
app = FakeApp()

# --- item 1: vary checkbox stays rightmost through toggles
pdef = {"name": "cell", "type": "float", "default": "0.5", "help": ""}
row = gui.ParamRow(root, app, pdef)
root.update()
check("item1 initial vary rightmost",
      row.frame.pack_slaves()[-1] is row._vary_cb)
row.vary_var.set(True)
row._vary_toggled()
root.update()
check("item1 after vary-on vary rightmost",
      row.frame.pack_slaves()[-1] is row._vary_cb)
row.vary_var.set(False)
row._vary_toggled()
root.update()
check("item1 after vary-off vary rightmost",
      row.frame.pack_slaves()[-1] is row._vary_cb)
pdef_b = {"name": "smooth", "type": "bool", "default": "false", "help": ""}
rowb = gui.ParamRow(root, app, pdef_b)
root.update()
rowb.vary_var.set(True)
rowb._vary_toggled()
root.update()
check("item1 bool-param vary-on vary rightmost",
      rowb.frame.pack_slaves()[-1] is rowb._vary_cb)

# --- item 2: back-to-results buttons
sweep_id = "20261019_091722_my_sweep"
h = "c60c3a9fdebc"
dp = gui.DetailPanel(root, app, sweep_id, h)
root.update()
bb = find_button(dp, "back to results")
check("item2 detail has back button", bb is not None)
if bb is not None:
    app.calls.clear()
    bb.invoke()
    check("item2 detail back calls show_results",
          app.calls == [("results", sweep_id)])
cp = gui.ComparePanel(root, app, sweep_id, h, h)
root.update()
bb2 = find_button(cp, "back to results")
check("item2 compare has back button", bb2 is not None)
if bb2 is not None:
    app.calls.clear()
    bb2.invoke()
    check("item2 compare back calls show_results",
          app.calls == [("results", sweep_id)])

# --- items 3+4: 3D right-drag box zoom and point size
rng = np.random.default_rng(0)
n = 3000
data = {"X": rng.random(n) * 15 - 114540,
        "Y": rng.random(n) * 20 + 183290,
        "Z": rng.random(n) * 10 + 966,
        "Classification": rng.choice([1, 2, 4], n).astype(np.int32)}
v = gui.Viewer3D(root, app, data=data)
v.pack(fill="both", expand=True)
root.update()
check("item4 artists created", len(v.artists) == 3)
v.size_var.set("3.5")
v._apply_size()
root.update()
sizes = {float(a.get_sizes()[0]) for a in v.artists.values()}
check("item4 point size applied", sizes == {3.5})
check("item3 default right-drag zoom disabled", 3 not in v._ax._zoom_btn)
lim0 = v._ax.get_xlim()


class E:
    pass


e = E()
e.widget = v.canvas.get_tk_widget()
e.x = 100
e.y = 100
v._on_b3_press(e)
e.y = 120  # drag down 20 px on the (small) widget: zoom in
v._on_b3_motion(e)
check("item3 zoom factor increased", v._zoom_factor > 1.0)
check("item3 axis limits unchanged (box zoom, not limits zoom)",
      v._ax.get_xlim() == lim0)
z_before = v._zoom_factor
e.y = 100
v._on_b3_press(e)
e.y = 100 + 10 * e.widget.winfo_height()  # jump far past widget height
try:
    v._on_b3_motion(e)
    ok = True
except ZeroDivisionError:
    ok = False
check("item3 huge drag jump clamped, no error",
      ok and 0.05 <= v._zoom_factor <= 40.0)
v._reset_view()
root.update()
check("item3 reset restores zoom 1", abs(v._zoom_factor - 1.0) < 1e-9)

# --- item 5: results preview output selector
rp = gui.ResultsPanel(root, app, sweep_id)
root.update()
assert rp.tree.exists(h)
rp.tree.selection_set(h)
rp._preview()
root.update()
check("item5 default is top-down",
      rp.preview_lbl.cget("text").startswith("top-down"))
check("item5 topdown image loaded",
      rp.preview._pil is not None and rp.preview._pil.size == (900, 900))
rp.kind_var.set("DTM")
rp._preview()
root.update()
check("item5 DTM label", rp.preview_lbl.cget("text").startswith("DTM"))
check("item5 DTM image loaded",
      rp.preview._pil is not None and rp.preview._pil.size == (800, 800))
rp.kind_var.set("cross-section")
rp._preview()
root.update()
check("item5 cross-section image loaded",
      rp.preview._pil is not None and rp.preview._pil.size == (1200, 600))
rp.kind_var.set("canopy height")
rp._preview()
root.update()
check("item5 canopy image loaded",
      rp.preview._pil is not None and rp.preview._pil.size == (800, 800))

root.destroy()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
