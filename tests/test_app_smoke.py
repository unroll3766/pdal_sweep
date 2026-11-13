"""App-level smoke check: real navigation flow through build -> results
-> detail -> back -> compare -> back, plus the preview output selector.
Run with the pdal env python from the repo root (uses the real DISPLAY)."""
import os
import sys

sys.path.insert(0, "/home/theo/Documents/pdal_sweep")
os.chdir("/home/theo/Documents/pdal_sweep")

import tkinter as tk
import gui

FAIL = []


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        FAIL.append(name)


def find_button(widget, text):
    for w in widget.winfo_children():
        if isinstance(w, tk.Button) and w.cget("text") == text:
            return w
        r = find_button(w, text)
        if r is not None:
            return r
    return None


app = gui.App()
app.geometry("1280x820")
app.update()
check("app starts on build panel", isinstance(app._panel, gui.BuildPanel))

sweep_id = "20261019_091722_my_sweep"
h, h2 = "c60c3a9fdebc", "a9b8a9d291a0"

app.show_results(sweep_id, force=True)
app.update()
check("results panel shown", isinstance(app._panel, gui.ResultsPanel))
rp = app._panel
assert rp.tree.exists(h)
rp.tree.selection_set(h)
app.update()
rp._preview()
check("preview defaults to top-down",
      rp.preview_lbl.cget("text").startswith("top-down"))
rp.kind_var.set("DTM")
rp._preview()
app.update()
check("preview swaps to DTM",
      rp.preview_lbl.cget("text").startswith("DTM")
      and rp.preview._pil is not None)

app.show_detail(sweep_id, h)
app.update()
check("detail panel shown", isinstance(app._panel, gui.DetailPanel))
bb = find_button(app._panel, "back to results")
check("detail has back button", bb is not None)
if bb is not None:
    bb.invoke()
    app.update()
    check("back from detail returns to results",
          isinstance(app._panel, gui.ResultsPanel))

app.show_compare(sweep_id, h, h2)
app.update()
check("compare panel shown", isinstance(app._panel, gui.ComparePanel))
bb2 = find_button(app._panel, "back to results")
check("compare has back button", bb2 is not None)
if bb2 is not None:
    bb2.invoke()
    app.update()
    check("back from compare returns to results",
          isinstance(app._panel, gui.ResultsPanel))

app.show_build(force=True)
app.update()
check("build panel shown again", isinstance(app._panel, gui.BuildPanel))

app.destroy()
print("FAILURES:", FAIL if FAIL else "none")
sys.exit(1 if FAIL else 0)
