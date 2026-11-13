"""PDAL Sweep desktop GUI (Tkinter).

Replaces the former web app: pipeline builder, grid search control
(start/stop/resume), result viewers with pan/zoom, and side-by-side run
comparison. All sweep execution stays in the pdal_sweep package and the
separately-spawned worker processes.
"""

import io
import json
import os
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from pdal_sweep import manager as M
from pdal_sweep import outputs as O
from pdal_sweep.registry import HAG_FILTERS
from pdal_sweep.spec import expand_spec

BG = "#1d2129"
BG2 = "#232834"
FG = "#d8dee9"
MUTED = "#8a93a5"
ACCENT = "#4da3ff"
GOOD = "#35c28f"
BAD = "#ff6b6b"
WARN = "#ffb347"
CANVAS_BG = "#0d1117"

DEFAULT_INPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "sample.las")


class ZoomableImage(tk.Canvas):
    """Crop-based image viewport: wheel zoom anchored at the cursor,
    drag to pan, double-click (or the fit button) to reset.

    Zoom renders only the visible crop from the source image (LANCZOS),
    so panning and zooming stay fast and sharp at any magnification.
    """

    def __init__(self, master, **kw):
        super().__init__(master, bg=CANVAS_BG, highlightthickness=0, **kw)
        self._pil = None
        self._photo = None
        self._zoom = 1.0    # display scale relative to fit-width
        self._cx = 0.0      # viewport center in source pixels
        self._cy = 0.0
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Double-Button-1>", lambda e: self.fit())
        self.bind("<Button-4>", lambda e: self._on_button45(1.25))
        self.bind("<Button-5>", lambda e: self._on_button45(1 / 1.25))
        # small overlay button to reset the view
        self._fit_btn = tk.Button(self, text="fit", fg=ACCENT, bg=BG,
                                  activebackground=BG, relief="flat",
                                  command=self.fit)
        self._fit_btn.place(relx=1.0, rely=0.0, x=-34, y=2, width=30)

    def set_image_pil(self, pil_img):
        self._pil = pil_img
        self.fit()

    def set_image_bytes(self, png_bytes):
        if png_bytes:
            self.set_image_pil(Image.open(io.BytesIO(png_bytes)))

    # ------------------------------------------------------------- helpers

    def _fit_scale(self):
        if not self._pil:
            return 1.0
        return max(0.01, self.winfo_width() / self._pil.width)

    def fit(self):
        """Whole image fitted to the canvas width."""
        if self._pil:
            self._cx = self._pil.width / 2.0
            self._cy = self._pil.height / 2.0
        self._zoom = 1.0
        self._render()

    def _source_center_at(self, x, y):
        """Convert canvas pixel (x, y) to source-image coordinates."""
        base = self._fit_scale() * self._zoom
        w, h = self.winfo_width(), self.winfo_height()
        return (self._cx + (x - w / 2.0) / base,
                self._cy + (y - h / 2.0) / base)

    def _clamp_center(self):
        if not self._pil:
            return
        base = self._fit_scale() * self._zoom
        half_w = self.winfo_width() / (2.0 * base)
        half_h = self.winfo_height() / (2.0 * base)
        self._cx = min(max(self._cx, half_w), self._pil.width - half_w) \
            if self._pil.width > 2 * half_w else self._pil.width / 2.0
        self._cy = min(max(self._cy, half_h), self._pil.height - half_h) \
            if self._pil.height > 2 * half_h else self._pil.height / 2.0

    def _set_zoom(self, z, anchor=None):
        z = max(1.0, min(16.0, z))
        if anchor is not None:
            ax, ay = self._source_center_at(*anchor)
            self._cx, self._cy = ax, ay
        self._zoom = z
        if anchor is not None:
            # keep the anchored source point under the cursor after zooming
            base = self._fit_scale() * self._zoom
            w, h = self.winfo_width(), self.winfo_height()
            self._cx = ax - (anchor[0] - w / 2.0) / base
            self._cy = ay - (anchor[1] - h / 2.0) / base
        self._clamp_center()
        self._render()

    # ------------------------------------------------------------ events

    def _on_wheel(self, e):
        factor = 1.25 if e.delta > 0 else 1 / 1.25
        self._set_zoom(self._zoom * factor, anchor=(e.x, e.y))

    def _on_button45(self, factor):
        w, h = self.winfo_width() / 2.0, self.winfo_height() / 2.0
        self._set_zoom(self._zoom * factor, anchor=(w, h))

    def _on_press(self, e):
        self._lx, self._ly = e.x, e.y

    def _on_drag(self, e):
        base = self._fit_scale() * self._zoom
        self._cx -= (e.x - self._lx) / base
        self._cy -= (e.y - self._ly) / base
        self._lx, self._ly = e.x, e.y
        self._clamp_center()
        self._render()

    # ------------------------------------------------------------- render

    def _render(self):
        self.delete("all")
        self._fit_btn.place(relx=1.0, rely=0.0, x=-34, y=2, width=30)
        if not self._pil or self.winfo_width() < 4:
            return
        base = self._fit_scale() * self._zoom
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = w / base, h / base          # source rect size
        x0 = max(0.0, self._cx - sw / 2.0)
        y0 = max(0.0, self._cy - sh / 2.0)
        x1 = min(self._pil.width, x0 + sw)
        y1 = min(self._pil.height, y0 + sh)
        if x1 <= x0 or y1 <= y0:
            return
        crop = self._pil.crop((int(x0), int(y0), int(x1), int(y1)))
        img = crop.resize((w, h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.create_image(0, 0, image=self._photo, anchor="nw")


class ScrollFrame(tk.Frame):
    """A frame with a vertical scrollbar; put content in self.body."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical",
                                    command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.body,
                                             anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfig(
                             self.win, width=e.width))
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel_global)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel_global)

    def _unbind_wheel(self, _e=None):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel_global(self, e):
        if self.winfo_ismapped():
            self.canvas.yview_scroll(-1 * int(e.delta / 120), "units")


_DONE_QUEUE = []


def _drain_done_queue(root):
    while _DONE_QUEUE:
        _DONE_QUEUE.pop(0)()
    root.after(50, lambda: _drain_done_queue(root))


# --------------------------------------------------------------- builder UI

class ParamRow:
    """One known stage parameter: set toggle, value, vary toggle."""

    def __init__(self, master, app, pdef, preset=None):
        self.app = app
        self.name = pdef["name"]
        self.pdef = pdef
        self.frame = tk.Frame(master, bg=BG)
        self.set_var = tk.BooleanVar(value=bool(preset))
        self.vary_var = tk.BooleanVar(
            value=bool(preset and preset.get("vary")))
        cb = tk.Checkbutton(self.frame, variable=self.set_var,
                            bg=BG, activebackground=BG, command=self._sync)
        name = tk.Label(self.frame, text=self.name, bg=BG, fg=MUTED,
                        font=("monospace", 9), width=14, anchor="w")
        name.bind("<Enter>", lambda e: app.set_help(pdef.get("help", "")))
        name.bind("<Leave>", lambda e: app.set_help(""))
        self.value_var = tk.StringVar()
        self.bool_var = tk.BooleanVar()
        # typing into a value (e.g. editing a vary list) changes the grid
        self.value_var.trace_add(
            "write", lambda *_: self.app.update_combo_count())
        self._preset_value(preset, pdef)
        self._vary_cb = tk.Checkbutton(self.frame, variable=self.vary_var,
                                       text="vary", bg=BG, activebackground=BG,
                                       fg=MUTED, command=self._vary_toggled)
        cb.pack(side="left")
        name.pack(side="left")
        self._entry = self._make_value_widget()
        self._entry.pack(side="left", fill="x", expand=True, padx=(4, 8))
        self._vary_cb.pack(side="left")

    def _preset_value(self, preset, pdef):
        if preset and preset.get("vary"):
            self.value_var.set(", ".join(str(v) for v in preset["values"]))
        elif preset and "value" in preset:
            if pdef["type"] == "bool":
                self.bool_var.set(bool(preset["value"]))
            else:
                self.value_var.set(str(preset["value"]))
        else:
            if pdef["type"] == "bool":
                self.bool_var.set(pdef["default"] == "true")
            else:
                self.value_var.set(pdef["default"] or "")

    def _make_value_widget(self):
        t = self.pdef["type"]
        if self.vary_var.get() or t != "bool":
            e = tk.Entry(self.frame, textvariable=self.value_var,
                         bg=BG2, fg=FG, insertbackground=FG,
                         relief="flat", highlightthickness=1,
                         highlightbackground="#333a48")
            if self.vary_var.get():
                self.value_var.set(self.value_var.get())
            return e
        return tk.Checkbutton(self.frame, variable=self.bool_var, bg=BG,
                              activebackground=BG)

    def _vary_toggled(self):
        cur = self.bool_var.get() if isinstance(self._entry, tk.Checkbutton) \
            else self.value_var.get()
        self._entry.destroy()
        if self.vary_var.get():
            self.set_var.set(True)
            self.value_var.set(cur if cur else "")
        self._entry = self._make_value_widget()
        self._entry.pack(side="left", fill="x", expand=True, padx=(4, 8),
                         before=self._vary_cb)
        self.app.update_combo_count()

    def _sync(self):
        if self.set_var.get() or self.vary_var.get():
            self.frame.configure(bg=BG)
        self.app.update_combo_count()

    def collect(self):
        """Param spec fragment, or None to omit the parameter entirely."""
        if not (self.set_var.get() or self.vary_var.get()):
            return None
        if self.vary_var.get():
            vals = [v.strip() for v in self.value_var.get().split(",")
                    if v.strip()]
            return {"vary": True, "values": vals} if vals else None
        if isinstance(self._entry, tk.Checkbutton):
            return {"vary": False, "value": self.bool_var.get()}
        v = self.value_var.get().strip()
        return {"vary": False, "value": v} if v else None


class CustomRow:
    """User-defined parameter: free name, value or vary list."""

    def __init__(self, master, app, name="", value="", vary=False):
        self.app = app
        self.frame = tk.Frame(master, bg=BG)
        self.name_var = tk.StringVar(value=name)
        self.value_var = tk.StringVar(value=value)
        self.vary_var = tk.BooleanVar(value=vary)
        # editing name or value changes the collected grid
        self.name_var.trace_add(
            "write", lambda *_: app.update_combo_count())
        self.value_var.trace_add(
            "write", lambda *_: app.update_combo_count())
        name = tk.Entry(self.frame, textvariable=self.name_var, width=14,
                        bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                        highlightthickness=1, highlightbackground="#333a48")
        name.pack(side="left")
        val = tk.Entry(self.frame, textvariable=self.value_var,
                       bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                       highlightthickness=1, highlightbackground="#333a48")
        val.pack(side="left", fill="x", expand=True, padx=(4, 8))
        tk.Checkbutton(self.frame, variable=self.vary_var, text="vary",
                       bg=BG, activebackground=BG, fg=MUTED,
                       command=app.update_combo_count).pack(side="left")

    def collect(self):
        n = self.name_var.get().strip()
        if not n:
            return None
        if self.vary_var.get():
            vals = [v.strip() for v in self.value_var.get().split(",")
                    if v.strip()]
            return (n, {"vary": True, "values": vals}) if vals else None
        v = self.value_var.get().strip()
        return (n, {"vary": False, "value": v}) if v else None


class AltFrame:
    """One alternative filter for a step."""

    def __init__(self, master, app, on_remove, ftype="filters.outlier",
                 preset_params=None):
        self.app = app
        self.preset_params = preset_params or {}
        self.frame = tk.LabelFrame(master, bg=BG, fg=ACCENT,
                                   text="alternative filter",
                                   padx=6, pady=4)
        top = tk.Frame(self.frame, bg=BG)
        top.pack(fill="x")
        self.type_var = tk.StringVar(value=ftype)
        self.type_cb = ttk.Combobox(
            top, textvariable=self.type_var, values=M.all_filter_stages(),
            width=30)
        self.type_cb.pack(side="left")
        self.type_cb.bind("<<ComboboxSelected>>", self._reload)
        self.type_cb.bind("<FocusOut>", self._reload)
        self.collapse_btn = tk.Button(top, text="collapse", fg=ACCENT,
                                      bg=BG, activebackground=BG,
                                      relief="flat",
                                      command=self._toggle_collapse)
        self.collapse_btn.pack(side="right", padx=(6, 0))
        tk.Button(top, text="reset defaults", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self.reset_defaults).pack(side="right", padx=(6, 0))
        tk.Button(top, text="remove", fg=BAD, bg=BG, activebackground=BG,
                  relief="flat", command=on_remove).pack(side="right")
        self._collapsed = False
        self.info = tk.Label(self.frame, text="", bg=BG, fg=MUTED,
                             wraplength=650, justify="left")
        self.info.pack(fill="x", pady=(2, 4))
        self.params_box = tk.Frame(self.frame, bg=BG)
        self.params_box.pack(fill="x")
        self.common_box = tk.Frame(self.frame, bg=BG)
        self.custom_box = tk.Frame(self.frame, bg=BG)
        self._custom_rows = []
        self.add_custom_btn = tk.Button(
            self.frame, text="+ custom param", fg=ACCENT, bg=BG,
            activebackground=BG, relief="flat", command=self._add_custom)
        self.add_custom_btn.pack(anchor="w", pady=(4, 0))
        self._reload()

    def _toggle_collapse(self):
        """Hide or show the parameter area to keep long pipelines readable."""
        self._collapsed = not self._collapsed
        if self._collapsed:
            for w in (self.info, self.params_box, self.common_box,
                      self.custom_box, self.add_custom_btn):
                w.pack_forget()
            self.collapse_btn.configure(text="expand")
        else:
            self.info.pack(fill="x", pady=(2, 4))
            self.params_box.pack(fill="x")
            self.common_box.pack(fill="x")
            self.custom_box.pack(fill="x")
            self.add_custom_btn.pack(anchor="w", pady=(4, 0))
            self.collapse_btn.configure(text="collapse")

    def reset_defaults(self):
        """Discard all values, custom params, and presets for this filter,
        restoring the installed PDAL's documented defaults."""
        for w in self.custom_box.winfo_children():
            w.destroy()
        self._custom_rows = []
        self.preset_params = {}
        self._loaded_type = None  # force a rebuild from defaults
        self._reload()

    def _add_custom(self):
        row = CustomRow(self.custom_box, self.app)
        row.frame.pack(fill="x", pady=1)
        self._custom_rows.append(row)

    def _reload(self, *_a):
        if self.type_cb.get() == getattr(self, "_loaded_type", object()):
            return
        self._loaded_type = self.type_cb.get()
        # presets apply only to the type this alternative was created with;
        # switching filter type starts from that type's defaults
        presets = self.preset_params
        self.preset_params = {}
        stage = self.type_var.get().strip()
        self.frame.configure(text=f"alternative filter: {stage or '(unnamed)'}")
        for w in self.params_box.winfo_children():
            w.destroy()
        for w in self.common_box.winfo_children():
            w.destroy()
        # drop preset-derived custom rows (user-added ones are kept)
        for row in getattr(self, "_preset_custom_rows", []):
            row.frame.destroy()
        self._preset_custom_rows = []
        self.param_rows = []
        opts = M.stage_options(stage)
        if opts is None:
            self.info.configure(
                text=f"no option info for '{stage}' - add custom params "
                     f"below, or check the stage name")
            self.common_box.pack_forget()
        else:
            self.info.configure(
                text="params pre-filled with PDAL defaults. 'set' on = sent "
                     "to PDAL, off = omitted. 'vary' = comma-separated list "
                     "(at least one value) becomes a grid axis.")
            for p in opts:
                preset = presets.get(p["name"])
                if p["type"] == "bool" and isinstance(preset, dict) \
                        and not preset.get("vary") and "value" in preset:
                    preset = {**preset, "value": bool(preset["value"])}
                row = ParamRow(self.params_box if not p["common"]
                               else self.common_box, self.app, p, preset)
                row.frame.pack(fill="x", pady=1)
                self.param_rows.append(row)
            self.params_box.pack(fill="x")
            self.common_box.pack(fill="x")
        # restore preset params this stage doesn't know about as custom rows
        if presets:
            known = set()
            if opts:
                known = {p["name"] for p in opts}
            for pname, pv in presets.items():
                if pname in known or not isinstance(pv, dict):
                    continue
                if pv.get("vary"):
                    row = CustomRow(self.custom_box, self.app, name=pname,
                                    value=", ".join(str(v) for v in
                                                   pv.get("values", [])),
                                    vary=True)
                else:
                    row = CustomRow(self.custom_box, self.app, name=pname,
                                    value=str(pv.get("value", "")))
                row.frame.pack(fill="x", pady=1)
                self._preset_custom_rows.append(row)
        self.custom_box.pack(fill="x")
        self.app.update_combo_count()

    def collect(self):
        params = {}
        for row in self.param_rows:
            frag = row.collect()
            if frag:
                params[row.name] = frag
        for row in list(self._custom_rows) + \
                list(getattr(self, "_preset_custom_rows", [])):
            got = row.collect()
            if got:
                params[got[0]] = got[1]
        return {"type": self.type_var.get().strip(), "params": params}


class StepFrame:
    """One pipeline step with one or more alternatives."""

    def __init__(self, master, app, on_remove, sid, alts=None,
                 on_move=None):
        self.app = app
        self.frame = tk.LabelFrame(master, bg=BG2, fg=FG, text=f"step: {sid}",
                                   padx=8, pady=6)
        top = tk.Frame(self.frame, bg=BG2)
        top.pack(fill="x")
        tk.Label(top, text="id", bg=BG2, fg=MUTED).pack(side="left")
        self.id_var = tk.StringVar(value=sid)
        e = tk.Entry(top, textvariable=self.id_var, width=12, bg=BG2, fg=FG,
                     insertbackground=FG, relief="flat", highlightthickness=1,
                     highlightbackground="#333a48")
        e.pack(side="left", padx=(4, 16))
        tk.Button(top, text="+ alternative", fg=ACCENT, bg=BG2,
                  activebackground=BG2, relief="flat",
                  command=self.add_alt).pack(side="right")
        tk.Button(top, text="+ skip", fg=ACCENT, bg=BG2,
                  activebackground=BG2, relief="flat",
                  command=self.add_skip).pack(side="right", padx=(6, 0))
        if on_move is not None:
            tk.Button(top, text="up", fg=ACCENT, bg=BG2, activebackground=BG2,
                      relief="flat",
                      command=lambda: on_move(-1)).pack(side="right",
                                                         padx=(6, 0))
            tk.Button(top, text="down", fg=ACCENT, bg=BG2,
                      activebackground=BG2, relief="flat",
                      command=lambda: on_move(1)).pack(side="right")
        tk.Button(top, text="remove step", fg=BAD, bg=BG2,
                  activebackground=BG2, relief="flat",
                  command=on_remove).pack(side="right")
        self.alts = []
        self.alts_box = tk.Frame(self.frame, bg=BG2)
        self.alts_box.pack(fill="x", pady=(6, 0))
        for a in (alts or [{"type": "filters.outlier"}]):
            if (a.get("type") or "").strip():
                self.add_alt(a.get("type"), a.get("params"))
            else:
                self.add_skip()

    def add_alt(self, ftype=None, preset=None):
        alt = AltFrame(self.alts_box, self.app,
                       on_remove=lambda: self.remove_alt(alt),
                       ftype=ftype or "filters.outlier",
                       preset_params=preset)
        alt.frame.pack(fill="x", pady=4)
        self.alts.append(alt)
        self.app.update_combo_count()

    def add_skip(self):
        """Add a 'no filter at this step' alternative, so the grid can
        test the step's inclusion (e.g. with vs without an outlier
        filter)."""
        alt = SkipAlt(self.alts_box, self.app,
                      on_remove=lambda: self.remove_alt(alt))
        alt.frame.pack(fill="x", pady=4)
        self.alts.append(alt)
        self.app.update_combo_count()

    def remove_alt(self, alt):
        alt.frame.destroy()
        self.alts.remove(alt)
        self.app.update_combo_count()

    def collect(self):
        alts = [a.collect() for a in self.alts]
        # keep skip alternatives (type "") - they are grid options too
        return {"id": self.id_var.get().strip() or "step",
                "alternatives": alts} if alts else None


class SkipAlt:
    """A 'no filter here' alternative: this step contributes nothing."""

    def __init__(self, master, app, on_remove):
        self.app = app
        self.frame = tk.LabelFrame(master, bg=BG, fg=MUTED,
                                   text="skip", padx=6, pady=4)
        row = tk.Frame(self.frame, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="skip - no filter at this step", bg=BG,
                 fg=MUTED).pack(side="left")
        tk.Button(row, text="remove", fg=BAD, bg=BG, activebackground=BG,
                  relief="flat", command=on_remove).pack(side="right")

    def collect(self):
        return {"type": "", "params": {}}


EXAMPLE_STEPS = [
    {"id": "strip", "alternatives": [
        {"type": "filters.assign",
         "params": {"value": {"vary": False,
                              "value": "Classification = 0"}}}]},
    {"id": "noise", "alternatives": [
        {"type": "filters.outlier",
         "params": {"method": {"vary": False, "value": "statistical"}}}]},
    {"id": "denoise", "alternatives": [
        {"type": "filters.range",
         "params": {"limits": {"vary": False,
                               "value": "Classification![7:7]"}}}]},
    {"id": "ground", "alternatives": [
        {"type": "filters.smrf",
         "params": {"cell": {"vary": True, "values": ["1.0", "2.0"]}}},
        {"type": "filters.csf",
         "params": {"resolution": {"vary": False, "value": "0.5"}}}]},
    {"id": "hag", "alternatives": [{"type": "filters.hag_nn", "params": {}}]},
]


class BuildPanel(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(head, text="input point cloud", bg=BG, fg=MUTED).pack()
        row = tk.Frame(head, bg=BG)
        row.pack(fill="x", pady=2)
        self.input_var = tk.StringVar(value=DEFAULT_INPUT)
        tk.Entry(row, textvariable=self.input_var, bg=BG2, fg=FG,
                 insertbackground=FG, relief="flat", highlightthickness=1,
                 highlightbackground="#333a48").pack(side="left",
                                                     fill="x", expand=True)
        tk.Button(row, text="browse...", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._browse).pack(side="left", padx=(8, 0))
        grid = tk.Frame(head, bg=BG)
        grid.pack(fill="x", pady=8)
        self.name_var = tk.StringVar(value="my sweep")
        self.workers_var = tk.StringVar(value="2")
        self.res_var = tk.StringVar(value="0.25")
        self.budget_var = tk.StringVar(value="2000000")
        self.format_var = tk.StringVar(value="laz")
        for col, (label, var, w) in enumerate((
                ("sweep name", self.name_var, 18),
                ("workers", self.workers_var, 5),
                ("raster res (m)", self.res_var, 8),
                ("render point budget", self.budget_var, 10),
                ("cloud format", self.format_var, 6))):
            f = tk.Frame(grid, bg=BG)
            f.grid(row=0, column=col, padx=(0, 14), sticky="nw")
            tk.Label(f, text=label, bg=BG, fg=MUTED).pack(anchor="w")
            if label == "cloud format":
                cb = ttk.Combobox(f, textvariable=var, state="readonly",
                                  values=("laz", "las"), width=w)
                cb.pack()
            else:
                tk.Entry(f, textvariable=var, width=w, bg=BG2, fg=FG,
                         insertbackground=FG, relief="flat",
                         highlightthickness=1,
                         highlightbackground="#333a48").pack()
        self.out_vars = {}
        of = tk.Frame(head, bg=BG)
        of.pack(anchor="w")
        for key, label in (("point_cloud", "saved cloud"),
                           ("dtm", "DTM"), ("chm", "canopy height map"),
                           ("topdown", "top-down render"),
                           ("crosssection", "cross-section render")):
            v = tk.BooleanVar(value=True)
            self.out_vars[key] = v
            tk.Checkbutton(of, text=label, variable=v, bg=BG,
                           activebackground=BG, fg=FG,
                           command=app.update_combo_count
                           ).pack(side="left", padx=(0, 16))

        sf = ScrollFrame(self)
        sf.pack(fill="both", expand=True, padx=10)
        self.steps_host = sf.body
        self.steps = []
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill="x", padx=10, pady=(8, 0))
        tk.Button(btns, text="add step", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self.add_step()).pack(side="left")
        tk.Button(btns, text="load example: noise -> ground -> HAG",
                  fg=ACCENT, bg=BG, activebackground=BG, relief="flat",
                  command=self.load_example).pack(side="left", padx=10)
        tk.Button(btns, text="preview combinations", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self.preview_combos).pack(side="left")
        btns2 = tk.Frame(self, bg=BG)
        btns2.pack(fill="x", padx=10, pady=(2, 8))
        tk.Button(btns2, text="import JSON", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self.import_spec).pack(side="left")
        tk.Button(btns2, text="export JSON", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self.export_spec).pack(side="left", padx=10)
        self.count_lbl = tk.Label(btns2, text="", bg=BG, fg=MUTED)
        self.count_lbl.pack(side="left", padx=10)
        tk.Button(btns2, text="run grid search", bg=ACCENT, fg="#0d1117",
                  activebackground=ACCENT, relief="flat",
                  command=self.run).pack(side="right")
        self.load_example()
        self.update_count()

    def import_spec(self):
        """Load a grid search spec from JSON into the builder."""
        path = filedialog.askopenfilename(
            title="import grid search JSON",
            filetypes=[("sweep spec JSON", "*.json"), ("all files", "*")])
        if not path:
            return
        try:
            with open(path) as f:
                spec = json.load(f)
            if not isinstance(spec, dict) or "steps" not in spec:
                raise ValueError("not a sweep spec (missing 'steps')")
            self.load_spec(spec)
        except Exception as e:
            messagebox.showerror("import failed", str(e))

    def load_spec(self, spec):
        """Populate the whole builder form from a spec dict."""
        if spec.get("input"):
            self.input_var.set(spec["input"])
        if spec.get("name"):
            self.name_var.set(spec["name"])
        self.workers_var.set(str(spec.get("workers", 2)))
        self.res_var.set(str(spec.get("raster_resolution", 0.25)))
        self.budget_var.set(str(spec.get("render_budget_points",
                                         2000000)))
        self.format_var.set(
            str(spec.get("point_format", "laz")).strip().lower() or "laz")
        for key, var in self.out_vars.items():
            var.set(bool(spec.get("outputs", {}).get(key, True)))
        for s in self.steps:
            s.frame.destroy()
        self.steps = []
        for step in spec.get("steps", []):
            self.add_step(step.get("id") or f"step{len(self.steps) + 1}",
                          step.get("alternatives", []))
        if not self.steps:
            self.add_step()
        self.app.update_combo_count()

    def export_spec(self):
        """Write the current grid search spec to JSON. Never overwrites
        an existing file without an explicit confirmation."""
        spec = self.collect()
        path = filedialog.asksaveasfilename(
            title="export grid search JSON", defaultextension=".json",
            filetypes=[("sweep spec JSON", "*.json"), ("all files", "*")])
        if not path:
            return
        if os.path.exists(path) and not messagebox.askyesno(
                "file exists",
                f"{os.path.basename(path)} already exists.\nOverwrite it?"):
            return
        with open(path, "w") as f:
            json.dump(spec, f, indent=1)

    def preview_combos(self):
        """Show the expanded grid (one row per combination) before running."""
        try:
            combos = expand_spec(self.collect())
        except Exception as e:
            messagebox.showerror("invalid grid", str(e))
            return
        win = tk.Toplevel(self)
        win.title("grid preview")
        win.geometry("700x480")
        win.configure(bg=BG)
        win.transient(self.winfo_toplevel())
        win.lift()
        win.focus_set()
        tk.Label(win, text=f"{len(combos)} combinations in the grid",
                 bg=BG, fg=FG, font=("sans", 11, "bold")).pack(
            anchor="w", padx=10, pady=(10, 4))
        lb = tk.Listbox(win, bg=BG2, fg=FG, relief="flat",
                        highlightthickness=0, selectbackground=ACCENT,
                        selectforeground="#0d1117", activestyle="none")
        lb.pack(fill="both", expand=True, padx=10, pady=10)
        for c in combos:
            lb.insert("end", f"{c['hash']}   {c['label']}")

    def _browse(self):
        p = filedialog.askopenfilename(
            title="input point cloud",
            filetypes=[("point clouds", "*.las *.laz"), ("all files", "*")])
        if p:
            self.input_var.set(p)

    def add_step(self, sid=None, alts=None):
        sid = sid or f"step{len(self.steps) + 1}"
        step = StepFrame(self.steps_host, self.app,
                         on_remove=lambda: self.remove_step(step),
                         on_move=lambda d: self.move_step(step, d),
                         sid=sid, alts=alts)
        step.frame.pack(fill="x", pady=6)
        self.steps.append(step)
        self.app.update_combo_count()

    def move_step(self, step, delta):
        """Move a step up (-1) or down (+1) in the pipeline order."""
        i = self.steps.index(step)
        j = i + delta
        if j < 0 or j >= len(self.steps):
            return
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        for s in self.steps:
            s.frame.pack_forget()
        for s in self.steps:
            s.frame.pack(fill="x", pady=6)

    def remove_step(self, step):
        step.frame.destroy()
        self.steps.remove(step)
        self.app.update_combo_count()

    def load_example(self):
        for s in self.steps:
            s.frame.destroy()
        self.steps = []
        for s in EXAMPLE_STEPS:
            self.add_step(s["id"], s["alternatives"])

    def collect(self):
        steps = [s.collect() for s in self.steps]
        steps = [s for s in steps if s]
        return {
            "name": self.name_var.get().strip() or "sweep",
            "input": self.input_var.get().strip(),
            "workers": int(self.workers_var.get() or 2),
            "raster_resolution": float(self.res_var.get() or 0.25),
            "render_budget_points": int(self.budget_var.get() or 2000000),
            "point_format": self.format_var.get() or "laz",
            "outputs": {k: v.get() for k, v in self.out_vars.items()},
            "steps": steps,
        }

    def update_count(self):
        try:
            n = len(expand_spec(self.collect()))
            note = "  (large grid - each run stores its outputs on disk)" \
                if n > 200 else ""
            self.count_lbl.configure(
                text=f"{n} combinations in the grid{note}", fg=MUTED)
        except Exception:
            self.count_lbl.configure(text="invalid grid", fg=BAD)

    def run(self):
        spec = self.collect()
        types = {alt.get("type") for step in spec["steps"]
                 for alt in step.get("alternatives", [])}
        if spec["outputs"].get("chm", True) and not (types & HAG_FILTERS):
            if not messagebox.askyesno(
                    "CHM not possible",
                    "Canopy height map is checked but there is no "
                    "height-above-ground step (filters.hag_nn / "
                    "hag_delaunay / hag_dem).\nNo CHM will be generated. "
                    "Run anyway?"):
                return
        sid, combos, err = M.create_sweep(spec)
        if sid is None:
            messagebox.showerror("could not start sweep", err)
            return
        self.app.refresh_sweeps(select=sid)
        self.app.show_progress(sid)


class ProgressPanel(tk.Frame):
    def __init__(self, master, app, sweep_id):
        super().__init__(master, bg=BG)
        self.app = app
        self.sweep_id = sweep_id
        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=10, pady=(10, 4))
        self.name_lbl = tk.Label(head, text="", bg=BG, fg=FG,
                                 font=("sans", 12, "bold"))
        self.name_lbl.pack(side="left")
        btns = tk.Frame(head, bg=BG)
        btns.pack(side="right")
        self.stop_btn = tk.Button(btns, text="stop", fg=BAD, bg=BG,
                                  activebackground=BG, relief="flat",
                                  command=self._stop)
        self.stop_btn.pack(side="left", padx=4)
        self.resume_btn = tk.Button(btns, text="resume", fg=ACCENT, bg=BG,
                                    activebackground=BG, relief="flat",
                                    command=self._resume)
        self.resume_btn.pack(side="left", padx=4)
        self.results_btn = tk.Button(btns, text="examine results", bg=ACCENT,
                                     fg="#0d1117", activebackground=ACCENT,
                                     relief="flat",
                                     command=lambda: app.show_results(
                                         self.sweep_id))
        self.results_btn.pack(side="left", padx=4)
        self.state_lbl = tk.Label(self, text="", bg=BG, fg=WARN)
        self.state_lbl.pack(anchor="w", padx=10)
        self.bar = ttk.Progressbar(self, length=400)
        self.bar.pack(anchor="w", padx=10, pady=6)
        self.warn_lbl = tk.Label(self, text="", bg=BG, fg=WARN,
                                 wraplength=520, justify="left")
        self.warn_lbl.pack(anchor="w", padx=10)
        cols = ("label", "points", "ground", "state")
        self.tree = ttk.Treeview(self, columns=cols, show="headings",
                                 height=14)
        for c, w in (("label", 500), ("points", 100), ("ground", 90),
                     ("state", 90)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=10, pady=8)
        self.tree.bind("<Double-1>", self._open_detail)
        tk.Label(self, text="double-click a finished combination to "
                            "examine its outputs (renders, DTM, class stats)",
                 bg=BG, fg=MUTED).pack(anchor="w", padx=10, pady=(0, 8))
        self.refresh()

    def _open_detail(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        h = sel[0]
        if M.read_meta(self.sweep_id, h) is not None:
            self.app.show_detail(self.sweep_id, h)

    def _stop(self):
        M.stop_sweep(self.sweep_id)
        self.refresh()

    def _resume(self):
        ok, err = M.resume_sweep(self.sweep_id)
        if not ok:
            messagebox.showerror("resume failed", err)
        self.refresh()

    def refresh(self):
        status = M.get_status(self.sweep_id)
        if not status:
            return
        total = status.get("total") or 1
        done = status.get("done", 0)
        self.name_lbl.configure(text=status.get("name", self.sweep_id))
        state = status.get("state", "?")
        color = {"finished": GOOD, "running": WARN, "stopped": WARN,
                 "error": BAD}.get(state, MUTED)
        alive = M.worker_alive(M.worker_pid(self.sweep_id))
        extra = "" if alive else "  (worker not running)"
        self.state_lbl.configure(
            text=f"{state}{extra}   {done}/{total} done"
                 f"   {status.get('failed', 0)} failed", fg=color)
        self.bar.configure(maximum=total, value=done)
        self.stop_btn.configure(state="normal" if alive else "disabled")
        self.resume_btn.configure(state="normal" if not alive else "disabled")
        warns = status.get("sweep_warnings") or []
        self.warn_lbl.configure(text="\n".join("- " + w for w in warns))
        self.tree.delete(*self.tree.get_children())
        for h, c in sorted(status.get("combos", {}).items(),
                           key=lambda kv: kv[1].get("label", "")):
            state = c.get("state", "?")
            pts_txt, ground_txt = "", ""
            meta = M.read_meta(self.sweep_id, h) or {}
            pts = meta.get("point_count")
            cc = meta.get("class_counts") or {}
            if pts:
                pts_txt = f"{pts:,}"
                if cc.get(2):
                    ground_txt = f"{100 * cc[2] / pts:.1f}%"
            label = c.get("label", h)
            if state == "error" and c.get("error"):
                label += "   | " + c["error"][:100]
            self.tree.insert("", "end", iid=h,
                             values=(label, pts_txt, ground_txt, state))


class ExportDialog(tk.Toplevel):
    """Batch export: pick a run scope and output kinds, zip to disk.

    The archive is named after the sweep and holds one subdirectory per
    output kind (points/, dtm/, ...); files keep their run-hash prefixes.
    """

    OUT_LABELS = (("point_cloud", "point cloud"),
                  ("dtm", "DTM"),
                  ("chm", "canopy height map"),
                  ("topdown", "top-down render"),
                  ("xsection", "cross-section render"))

    def __init__(self, master, app, sweep_id, runs, selected):
        super().__init__(master)
        self.app = app
        self.sweep_id = sweep_id
        self.runs = runs            # (hash, entry, meta) tuples
        self.selected = list(selected)
        self.title("export results")
        self.configure(bg=BG)
        self.transient(master.winfo_toplevel())
        self.lift()

        tk.Label(self, text="which runs to export", bg=BG, fg=MUTED).pack(
            anchor="w", padx=12, pady=(12, 2))
        self.scope_var = tk.StringVar(value="all")
        for val, label in (("all", "all runs"), ("good", "good runs"),
                           ("bad", "bad runs"),
                           ("selected", "runs selected in the list")):
            tk.Radiobutton(self, text=label, variable=self.scope_var,
                           value=val, bg=BG, fg=FG, activebackground=BG,
                           selectcolor=BG2,
                           command=self._update_count).pack(
                anchor="w", padx=22)

        tk.Label(self, text="which outputs to include", bg=BG,
                 fg=MUTED).pack(anchor="w", padx=12, pady=(10, 2))
        self.out_vars = {}
        row = tk.Frame(self, bg=BG)
        row.pack(anchor="w", padx=22)
        for i, (key, label) in enumerate(self.OUT_LABELS):
            v = tk.BooleanVar(value=key in ("point_cloud", "dtm", "chm"))
            self.out_vars[key] = v
            tk.Checkbutton(row, text=label, variable=v, bg=BG, fg=FG,
                           activebackground=BG).grid(
                row=0, column=i, padx=(0, 14), sticky="w")

        self.count_lbl = tk.Label(self, text="", bg=BG, fg=FG)
        self.count_lbl.pack(anchor="w", padx=12, pady=(10, 2))
        self.export_btn = tk.Button(
            self, text="choose destination & export", bg=ACCENT,
            fg="#0d1117", activebackground=ACCENT, relief="flat",
            command=self._export)
        self.export_btn.pack(anchor="e", padx=12, pady=(6, 12))
        self._update_count()

    def _scope_hashes(self):
        scope = self.scope_var.get()
        if scope == "all":
            return [h for h, _e, _m in self.runs]
        ratings = M.read_ratings(self.sweep_id)
        if scope in ("good", "bad"):
            return [h for h, _e, _m in self.runs
                    if ratings.get(h) == scope]
        return list(self.selected)

    def _update_count(self):
        n = len(self._scope_hashes())
        self.count_lbl.configure(text=f"{n} run(s) in scope")

    def _export(self):
        hashes = self._scope_hashes()
        if not hashes:
            messagebox.showinfo("export", "no runs in this scope",
                               parent=self)
            return
        keys = [k for k, v in self.out_vars.items() if v.get()]
        if not keys:
            messagebox.showinfo("export", "select at least one output",
                               parent=self)
            return
        status = M.get_status(self.sweep_id) or {}
        name = re.sub(r"[^\w.-]+", "_",
                      status.get("name") or self.sweep_id).strip("_")
        name = name or self.sweep_id
        path = filedialog.asksaveasfilename(
            parent=self, title="export zip", defaultextension=".zip",
            initialfile=f"{name}.zip",
            filetypes=[("zip archive", "*.zip"), ("all files", "*")])
        if not path:
            return
        # the save dialog already confirmed any overwrite
        if os.path.exists(path):
            os.remove(path)
        self.export_btn.configure(state="disabled", text="exporting...")

        def job():
            return M.export_runs(self.sweep_id, hashes, keys, path)

        def done(counts, err):
            if err is not None:
                self.export_btn.configure(state="normal", text="export")
                messagebox.showerror("export failed", str(err), parent=self)
                return
            total = sum(counts.values())
            lines = "\n".join(f"{M.EXPORT_SUBDIRS[k]}/: {n} file(s)"
                              for k, n in counts.items() if n)
            messagebox.showinfo(
                "export complete",
                f"{total} file(s) zipped to\n{path}\n\n{lines}",
                parent=self)
            self.destroy()

        self.app.submit(self, job, done)


class ResultsPanel(tk.Frame):
    def __init__(self, master, app, sweep_id):
        super().__init__(master, bg=BG)
        self.app = app
        self.sweep_id = sweep_id
        status = M.get_status(sweep_id) or {}
        tk.Label(self, text=f"results - {status.get('name', sweep_id)}",
                 bg=BG, fg=FG, font=("sans", 12, "bold")).pack(
            anchor="w", padx=10, pady=(10, 4))
        warns = status.get("sweep_warnings") or []
        if warns:
            tk.Label(self, text="\n".join("- " + w for w in warns), bg=BG,
                     fg=WARN, wraplength=520, justify="left").pack(
                anchor="w", padx=10)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=6)
        cols = ("label", "points", "ground", "secs", "state", "mark")
        widths = (240, 60, 55, 45, 60, 45)
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(left, columns=cols, show="headings",
                                 selectmode="extended", height=16)
        for c, w in zip(cols, widths):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("good", foreground=GOOD)
        self.tree.tag_configure("bad", foreground=BAD)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        hb = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=hb.set)
        hb.grid(row=1, column=0, sticky="ew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))
        ptop = tk.Frame(right, bg=BG)
        ptop.pack(fill="x", pady=(0, 4))
        self.preview_lbl = tk.Label(ptop, text="select a run to preview",
                                    bg=BG, fg=MUTED, anchor="w")
        self.preview_lbl.pack(side="left")
        self.kind_var = tk.StringVar(value="top-down")
        self.kind_cb = ttk.Combobox(
            ptop, textvariable=self.kind_var, state="readonly", width=14,
            values=[k for k, _key, _legacy in self.PREVIEW_KINDS])
        self.kind_cb.pack(side="right")
        self.kind_cb.bind("<<ComboboxSelected>>", lambda e: self._preview())
        self.preview = ZoomableImage(right)
        self.preview.pack(fill="both", expand=True, pady=(0, 8))
        btns = tk.Frame(right, bg=BG)
        btns.pack()
        tk.Button(btns, text="open detail", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._open_detail).pack(side="left", padx=4)
        tk.Button(btns, text="refresh", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: app.show_results(sweep_id,
                                                   force=True)).pack(
            side="left", padx=4)
        tk.Button(btns, text="compare selected (2)", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._compare).pack(side="left", padx=4)
        tk.Button(btns, text="export results", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._export).pack(side="left", padx=4)
        marks = tk.Frame(right, bg=BG)
        marks.pack(pady=(4, 0))
        tk.Button(marks, text="mark good", fg=GOOD, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self._mark("good")).pack(
            side="left", padx=4)
        tk.Button(marks, text="mark bad", fg=BAD, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self._mark("bad")).pack(
            side="left", padx=4)
        tk.Button(marks, text="clear mark", fg=MUTED, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self._mark(None)).pack(
            side="left", padx=4)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._preview())
        self.tree.bind("<Double-1>", lambda e: self._open_detail())
        # keyboard marking while the list has focus
        self.tree.bind("g", lambda e: self._mark("good"))
        self.tree.bind("b", lambda e: self._mark("bad"))
        self.tree.bind("c", lambda e: self._mark(None))
        self.runs = []
        self.metas = {}
        ratings = M.read_ratings(sweep_id)
        for h, entry in sorted((status.get("combos") or {}).items(),
                               key=lambda kv: kv[1].get("label", "")):
            meta = M.read_meta(sweep_id, h) or {}
            self.runs.append((h, entry, meta))
            self.metas[h] = meta
            pts = meta.get("point_count")
            cc = meta.get("class_counts") or {}
            ground = ""
            if pts and cc.get(2):
                ground = f"{100 * cc[2] / pts:.1f}%"
            mark = ratings.get(h, "")
            self.tree.insert(
                "", "end", iid=h, tags=(mark,) if mark else (),
                values=(entry.get("label", h), f"{pts:,}" if pts else "",
                        ground, meta.get("seconds", ""),
                        entry.get("state", ""), mark))

    def _selected(self):
        return self.tree.selection()

    # (label, meta outputs key, legacy filename) for the preview selector
    PREVIEW_KINDS = (("top-down", "topdown", "topdown.png"),
                     ("cross-section", "xsection", "xsection.png"),
                     ("DTM", "dtm_preview", "dtm.png"),
                     ("canopy height", "chm_preview", "chm.png"))

    def _preview(self):
        """Update the preview to the selected run and chosen output kind."""
        sel = self._selected()
        if not sel:
            return
        h = sel[0]
        entry = (self.tree.item(h, "values")[0] if self.tree.exists(h)
                 else h)
        outs = (self.metas.get(h) or {}).get("outputs") or {}
        kind = self.kind_var.get()
        key, legacy = next(((k, l) for kk, k, l in self.PREVIEW_KINDS
                            if kk == kind), ("topdown", "topdown.png"))
        path = os.path.join(M.run_dir(self.sweep_id, h),
                            outs.get(key, legacy))
        if not os.path.exists(path) and key != "topdown":
            # chosen output missing for this run: fall back to top-down
            kind, key, legacy = "top-down", "topdown", "topdown.png"
            self.kind_var.set(kind)
            path = os.path.join(M.run_dir(self.sweep_id, h),
                                outs.get(key, legacy))
        self.preview_lbl.configure(text=f"{kind}: {entry}")
        self.preview.set_image_pil(
            Image.open(path) if os.path.exists(path) else None)

    def _open_detail(self):
        sel = self._selected()
        if sel:
            self.app.show_detail(self.sweep_id, sel[0])

    def _compare(self):
        sel = list(self._selected())[:2]
        if len(sel) < 2:
            messagebox.showinfo("compare", "select two runs (ctrl-click)")
            return
        self.app.show_compare(self.sweep_id, sel[0], sel[1])

    def _export(self):
        """Open the batch-export dialog for this sweep."""
        ExportDialog(self, self.app, self.sweep_id, self.runs,
                     self._selected())

    def _mark(self, rating):
        """Persist good/bad/clear for every selected run, update the rows."""
        sel = list(self._selected())
        if not sel:
            messagebox.showinfo("mark", "select at least one run")
            return
        for h in sel:
            if not M.set_rating(self.sweep_id, h, rating):
                messagebox.showerror("mark", "could not save rating")
                return
            mark = rating or ""
            self.tree.set(h, "mark", mark)
            self.tree.item(h, tags=(mark,) if mark else ())


class Viewer3D(tk.Frame):
    """Interactive 3D scatter of a run's decimated points.

    mplot3d embedded in Tkinter: left-drag rotates, right-drag zooms.
    One toggle per classification class. Points are decimated to a
    budget because mplot3d rotation is only interactive below a few
    hundred thousand points.
    """

    DEFAULT_BUDGET = 50000
    DEFAULT_SIZE = 0.7

    def __init__(self, master, app, data=None, compact=False):
        super().__init__(master, bg=BG)
        self.app = app
        self.compact = compact
        self.full = None          # full point arrays, kept for re-budget
        self.artists = {}         # class -> scatter artist
        self.checks = {}          # class -> BooleanVar
        self._pending_render = None
        self._zoom_factor = 1.0   # box zoom; right-drag multiplies this
        self._aspect = (4.0, 4.0, 3.0)
        self._b3_y = None
        self._b3_x = None
        self._b3_z0 = 1.0
        ctrl = tk.Frame(self, bg=BG)
        ctrl.pack(fill="x", pady=(0, 2))
        tk.Label(ctrl, text="max points", bg=BG, fg=MUTED).pack(
            side="left", padx=(2, 4))
        self.budget_var = tk.StringVar(value=f"{self.DEFAULT_BUDGET:,}")
        self.budget_var.trace_add("write", lambda *_: self._render())
        tk.Entry(ctrl, textvariable=self.budget_var, width=9, bg=BG2,
                 fg=FG, insertbackground=FG, relief="flat",
                 highlightthickness=1, highlightbackground="#333a48").pack(
            side="left")
        tk.Label(ctrl, text="point size", bg=BG, fg=MUTED).pack(
            side="left", padx=(10, 4))
        self.size_var = tk.StringVar(value=str(self.DEFAULT_SIZE))
        self.size_var.trace_add("write", lambda *_: self._apply_size())
        tk.Entry(ctrl, textvariable=self.size_var, width=5, bg=BG2,
                 fg=FG, insertbackground=FG, relief="flat",
                 highlightthickness=1, highlightbackground="#333a48").pack(
            side="left")
        if not compact:
            tk.Label(ctrl, bg=BG, fg=MUTED,
                     text="drag to rotate, right-drag to zoom").pack(
                side="left", padx=14)
        tk.Button(ctrl, text="reset view", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self._reset_view()).pack(side="right")
        self.classbar = tk.Frame(self, bg=BG)
        self.classbar.pack(fill="x")
        self.plot_host = tk.Frame(self, bg=CANVAS_BG)
        self.plot_host.pack(fill="both", expand=True)
        if data is not None:
            self.set_data(data)

    # ------------------------------------------------------------ data flow

    def set_data(self, data):
        """Store the full point arrays, decimate to the budget and draw."""
        self.full = data
        data = self._decimate(data)
        for w in self.classbar.winfo_children():
            w.destroy()
        self.artists.clear()
        self.checks.clear()
        if self.plot_host.winfo_children():
            self.plot_host.winfo_children()[0].destroy()

        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        fig = Figure(figsize=(7, 6), dpi=110, facecolor=CANVAS_BG)
        ax = fig.add_subplot(projection="3d", facecolor=CANVAS_BG)
        for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
            axis.set_pane_color((0.07, 0.09, 0.12, 1.0))
            axis.label.set_color(MUTED)
        ax.tick_params(colors=MUTED, labelsize=7)
        ax.set_xlabel("X", fontsize=8)
        ax.set_ylabel("Y", fontsize=8)
        ax.set_zlabel("Z", fontsize=8)
        x, y, z = data["X"], data["Y"], data["Z"]
        c = data["Classification"].astype(int)
        row = None
        for i, cls in enumerate(sorted(set(c.tolist()))):
            m = c == cls
            color = O.CLASS_COLORS.get(cls, "#7f8c8d")
            self.artists[cls] = ax.scatter(
                x[m], y[m], z[m], s=self._size(), c=color, linewidths=0,
                depthshade=True)
            var = tk.BooleanVar(value=True)
            var.trace_add("write", lambda *_a, _cl=cls: self._apply(_cl))
            self.checks[cls] = var
            name = O.CLASS_NAMES.get(cls, f"class {cls}")
            # wrap the toggle bar so many classes stay inside the pane
            if i % 2 == 0:
                row = tk.Frame(self.classbar, bg=BG)
                row.pack(anchor="w")
            tk.Label(row, text="\u25a0", fg=color, bg=BG).pack(
                side="left", padx=(8, 0))
            tk.Checkbutton(
                row, text=f"{name} ({int(m.sum()):,})",
                variable=var, bg=BG, fg=FG, activebackground=BG,
                selectcolor=BG2).pack(side="left")
        xr = float(x.max() - x.min()) or 1.0
        yr = float(y.max() - y.min()) or 1.0
        zr = float(z.max() - z.min()) or 1.0
        self._aspect = (xr, yr, zr * 3)
        ax.set_box_aspect(self._aspect, zoom=self._zoom_factor)
        self._ax = ax
        self._fig = fig
        self.canvas = FigureCanvasTkAgg(fig, master=self.plot_host)
        self.canvas.draw()
        # small size request: the packer stretches the canvas to the
        # available space, and mpl re-renders at the allocated size
        w = self.canvas.get_tk_widget()
        w.configure(width=40, height=40)
        w.pack(fill="both", expand=True)
        # take over right-drag: zoom the rendered cloud (box zoom) instead
        # of mplot3d's default, which only rescales the axis limits
        ax.mouse_init(zoom_btn=[])
        w.bind("<ButtonPress-3>", self._on_b3_press)
        # a rapid second press arrives as Double-Button-3, not ButtonPress-3
        w.bind("<Double-Button-3>", self._on_b3_press)
        w.bind("<B3-Motion>", self._on_b3_motion)
        w.bind("<ButtonRelease-3>", self._on_b3_release)

    def _reset_view(self):
        if getattr(self, "_ax", None) is not None:
            self._zoom_factor = 1.0
            self._ax.set_box_aspect(self._aspect, zoom=1.0)
            self._ax.view_init(elev=30, azim=-60)
            self.canvas.draw_idle()

    def _size(self):
        try:
            return max(0.05, min(100.0, float(self.size_var.get())))
        except ValueError:
            return self.DEFAULT_SIZE

    def _apply_size(self):
        """Point size entry changed: resize every class's scatter."""
        if not self.artists:
            return
        s = self._size()
        for a in self.artists.values():
            a.set_sizes([s])
        if getattr(self, "canvas", None) is not None:
            self.canvas.draw_idle()

    def _on_b3_press(self, e):
        # zoom is a function of the total drag from the press point, so
        # short and fast drags behave predictably
        self._b3_x = e.x
        self._b3_y = e.y
        self._b3_z0 = self._zoom_factor

    def _on_b3_motion(self, e):
        if self._b3_y is None or getattr(self, "_ax", None) is None:
            return
        w = max(1, e.widget.winfo_width())
        h = max(1, e.widget.winfo_height())
        dx = e.x - self._b3_x
        dy = e.y - self._b3_y
        # zoom along whichever axis the drag is mostly on; dragging
        # down or right (positive d) zooms in
        if abs(dy) >= abs(dx):
            ref, d = h, dy
        else:
            ref, d = w, dx
        denom = max(1.0, ref - d)
        self._zoom_factor = max(0.05, min(40.0, self._b3_z0 * ref / denom))
        self._ax.set_box_aspect(self._aspect, zoom=self._zoom_factor)
        self.canvas.draw_idle()

    def _on_b3_release(self, _e):
        self._b3_y = None

    def _apply(self, cls):
        """One class's checkbox changed: toggle its artist."""
        if cls in self.artists:
            self.artists[cls].set_visible(bool(self.checks[cls].get()))
            self.canvas.draw_idle()

    def _budget(self):
        try:
            return max(10000, min(500000,
                                  int(self.budget_var.get().replace(",", ""))))
        except ValueError:
            return self.DEFAULT_BUDGET

    def _decimate(self, data):
        import numpy as np
        budget = self._budget()
        n = len(data["X"])
        if n > budget:
            idx = np.linspace(0, n - 1, budget).astype(int)
            data = {k: v[idx] for k, v in data.items()}
        return data

    def _render(self):
        """Debounced budget rebuild: every keystroke in the entry fires
        this; coalesce into one rebuild after typing settles."""
        if self._pending_render is not None:
            self.after_cancel(self._pending_render)
        self._pending_render = self.after(400, self._render_now)

    def _render_now(self):
        """Re-decimate and redraw, keeping the current view angles."""
        self._pending_render = None
        if self.full is None:
            return
        elev = azim = None
        if getattr(self, "_ax", None) is not None:
            elev, azim = self._ax.elev, self._ax.azim
        self.set_data(self.full)
        if elev is not None:
            self._ax.view_init(elev=elev, azim=azim)
            self.canvas.draw_idle()


class DetailPanel(tk.Frame):
    def __init__(self, master, app, sweep_id, run_hash):
        super().__init__(master, bg=BG)
        self.app = app
        self.sweep_id = sweep_id
        self.run_hash = run_hash
        self.rdir = M.run_dir(sweep_id, run_hash)
        meta = M.read_meta(sweep_id, run_hash) or {}
        self.meta = meta
        status = M.get_status(sweep_id) or {}

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=10, pady=(10, 4))
        tk.Button(head, text="back to results", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: app.show_results(sweep_id, force=True)
                  ).pack(side="left", padx=(0, 10))
        tk.Label(head, text=meta.get("label", run_hash), bg=BG, fg=FG,
                 font=("sans", 12, "bold"), wraplength=520,
                 justify="left").pack(side="left")
        tk.Button(head, text="open folder", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: subprocess.Popen(["xdg-open", self.rdir])
                  ).pack(side="right")
        mark_frame = tk.Frame(head, bg=BG)
        mark_frame.pack(side="right", padx=(0, 12))
        self._rating = (M.read_ratings(sweep_id) or {}).get(run_hash)
        self.rating_lbl = tk.Label(mark_frame, bg=BG, fg=MUTED)
        self.rating_lbl.pack(side="left", padx=(0, 6))
        for text, color, rating in (("good", GOOD, "good"),
                                    ("bad", BAD, "bad"),
                                    ("clear", MUTED, None)):
            tk.Button(mark_frame, text=text, fg=color, bg=BG,
                      activebackground=BG, relief="flat",
                      command=lambda r=rating: self._mark(r)).pack(
                side="left", padx=2)
        self._sync_rating_lbl()
        warns = (meta.get("warnings") or []) + (status.get("sweep_warnings")
                                                or [])
        if warns:
            tk.Label(self, text="\n".join("- " + w for w in warns), bg=BG,
                     fg=WARN, wraplength=520, justify="left").pack(
                anchor="w", padx=10)

        stats = tk.Frame(self, bg=BG)
        stats.pack(anchor="w", padx=10, pady=6)
        lines = [
            f"input points: {meta.get('input_points', 0):,}",
            f"output points: {meta.get('point_count', 0):,}",
        ]
        tk.Label(stats, text="   |   ".join(lines), bg=BG, fg=MUTED).pack()

        cols = ("class", "name", "points", "share", "in input")
        ctree = ttk.Treeview(self, columns=cols, show="headings", height=5)
        for c, w in zip(cols, (60, 110, 100, 80, 100)):
            ctree.heading(c, text=c)
            ctree.column(c, width=w, anchor="w")
        ic = ((status.get("input_classes") or {}).get("class_counts") or {})
        pts = meta.get("point_count") or 0
        for cls, n in sorted((meta.get("class_counts") or {}).items()):
            inp = ic.get(str(cls))
            ctree.insert("", "end", values=(
                cls, O.CLASS_NAMES.get(cls, "unlabeled"), f"{n:,}",
                f"{100 * n / pts:.1f}%" if pts else "",
                f"{inp:,}" if inp else "not in input"))
        ctree.pack(anchor="w", padx=10, pady=4)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.views = {}
        self._add_tab("top-down", self._load_file(
            self._output_file("topdown", "topdown.png")))
        self._xsection_tab()
        self._3d_tab()
        self._add_tab("DTM", self._load_file(
            self._output_file("dtm_preview", "dtm.png")))
        self._add_tab("canopy height", self._load_file(
            self._output_file("chm_preview", "chm.png")))

    def _output_file(self, key, legacy):
        """Output filename from meta.json, falling back to the
        pre-prefix name for runs produced by older versions."""
        return (self.meta.get("outputs") or {}).get(key) or legacy

    def _sync_rating_lbl(self):
        self.rating_lbl.configure(
            text=f"marked: {self._rating}" if self._rating
            else "not marked yet",
            fg={"good": GOOD, "bad": BAD}.get(self._rating, MUTED))

    def _mark(self, rating):
        """Set this run's rating and persist it."""
        if not M.set_rating(self.sweep_id, self.run_hash, rating):
            messagebox.showerror("mark", "could not save rating")
            return
        self._rating = rating
        self._sync_rating_lbl()

    def _tab_changed(self, _e=None):
        tab = self.nb.select()
        if not tab:
            return
        for w in self.nametowidget(tab).winfo_children():
            if isinstance(w, ZoomableImage):
                w._render()

    def _3d_tab(self):
        """3D scatter tab: loads the run's cached points (reusing the
        cross-section's cache if it is already in memory)."""
        f = tk.Frame(self.nb, bg=BG)
        self.nb.add(f, text="3D view")
        self.viewer3d = Viewer3D(f, self.app)
        self.viewer3d.pack(fill="both", expand=True)
        if not os.path.exists(os.path.join(self.rdir,
                                           "render_points.npz")):
            tk.Label(f, text="no cached points for this run", bg=BG,
                     fg=MUTED).pack(padx=20, pady=20)
            return
        cache = self._cache if self._cache is not None else self.rdir

        def job():
            return cache if isinstance(cache, dict) else O.load_cache(cache)

        def done(data, err):
            if err is None and data:
                self._cache = data
                self.viewer3d.set_data(data)
            else:
                tk.Label(f, text=f"could not load points: {err}", bg=BG,
                         fg=BAD).pack(padx=20, pady=20)

        # separate job owner from the cross-section's: a new submit cancels
        # the owner's pending job, and both tabs load the cache at build
        self.app.submit(self.viewer3d, job, done)

    def _load_file(self, name):
        path = os.path.join(self.rdir, name)
        if not os.path.exists(path):
            return None
        return Image.open(path)

    def _add_tab(self, title, pil_img):
        f = tk.Frame(self.nb, bg=BG)
        self.nb.add(f, text=title)
        if pil_img is None:
            tk.Label(f, text=f"no '{title}' output for this run", bg=BG,
                     fg=MUTED).pack(padx=20, pady=20)
            return
        z = ZoomableImage(f)
        z.pack(fill="both", expand=True)
        z.set_image_pil(pil_img)

    def _xsection_tab(self):
        f = tk.Frame(self.nb, bg=BG)
        self.nb.add(f, text="cross-section")
        ctrl = tk.Frame(f, bg=BG)
        ctrl.pack(fill="x", pady=4)
        tk.Label(ctrl, text="strip position", bg=BG, fg=MUTED).pack(
            side="left", padx=(6, 4))
        self.frac_var = tk.DoubleVar(value=0.5)
        self.frac_scale = tk.Scale(ctrl, variable=self.frac_var,
                                   from_=0.0, to=1.0, resolution=0.005,
                                   orient="horizontal", bg=BG, fg=FG,
                                   troughcolor=BG2, length=320,
                                   highlightthickness=0, showvalue=False,
                                   command=lambda _v: self._render_xsection())
        self.frac_scale.pack(side="left")
        tk.Label(ctrl, text="width (m)", bg=BG, fg=MUTED).pack(
            side="left", padx=(16, 4))
        self.width_var = tk.StringVar(value="2")
        self.width_var.trace_add("write", lambda *_: self._render_xsection())
        tk.Entry(ctrl, textvariable=self.width_var, width=5, bg=BG2, fg=FG,
                 insertbackground=FG, relief="flat", highlightthickness=1,
                 highlightbackground="#333a48").pack(side="left")
        self.xview = ZoomableImage(f)
        self.xview.pack(fill="both", expand=True)
        self._cache = None       # run's decimated points, loaded once
        self._pending_render = None
        self._render_xsection()

    def _render_xsection(self):
        """Debounced: a slider drag fires this many times per second, and
        each render is expensive (cache load + figure); coalesce them."""
        if self._pending_render is not None:
            self.after_cancel(self._pending_render)
        self._pending_render = self.after(300, self._render_xsection_now)

    def _render_xsection_now(self):
        self._pending_render = None
        self.app.cancel_pending(self)
        try:
            width = max(0.1, min(30.0, float(self.width_var.get() or 2)))
        except ValueError:
            return
        frac = max(0.0, min(1.0, self.frac_var.get()))
        rdir = self.rdir

        if self._cache is not None:
            def job():
                return O.render_xsection(self._cache, frac=frac, width=width)

            def done(png, err):
                self.xview.set_image_bytes(None if err else png)
        else:
            # first render for this panel: load the run's decimated points
            # once and keep them for subsequent slider moves
            def job():
                data = O.load_cache(rdir)
                return (O.render_xsection(data, frac=frac, width=width),
                        data)

            def done(res, err):
                if err is None and res:
                    png, data = res
                    self._cache = data
                    self.xview.set_image_bytes(png)
                else:
                    self.xview.set_image_bytes(None)

        self.app.submit(self, job, done)


class ComparePanel(tk.Frame):
    def __init__(self, master, app, sweep_id, a, b):
        super().__init__(master, bg=BG)
        self.app = app
        self.sweep_id = sweep_id
        status = M.get_status(sweep_id) or {}
        self.by_hash = {h: e.get("label", h)
                        for h, e in (status.get("combos") or {}).items()}
        hashes = sorted(self.by_hash)
        a = a if a in self.by_hash else (hashes[0] if hashes else None)
        b = b if b in self.by_hash else (hashes[-1] if len(hashes) > 1
                                        else a)
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Button(top, text="back to results", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: app.show_results(sweep_id, force=True)
                  ).pack(side="left", padx=(0, 20))
        self.sel = {}
        for side, cur in (("A", a), ("B", b)):
            f = tk.Frame(top, bg=BG)
            f.pack(side="left", padx=(0, 20))
            tk.Label(f, text=f"run {side}", bg=BG, fg=MUTED).pack(anchor="w")
            var = tk.StringVar(value=cur or "")
            self.sel[side] = var
            cb = ttk.Combobox(f, textvariable=var, values=hashes, width=44)
            cb.pack()
            cb.bind("<<ComboboxSelected>>", lambda e, s=side: self.reload())
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=8)
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.views = {"top-down": [], "DTM": [], "canopy height": []}
        self.tabs = {}
        for title in ("top-down", "cross-section", "3D view", "DTM",
                      "canopy height"):
            f = tk.Frame(self.nb, bg=BG)
            self.nb.add(f, text=title)
            self.tabs[title] = f
            if title == "cross-section":
                ctrl = tk.Frame(f, bg=BG)
                ctrl.pack(fill="x", pady=4)
                tk.Label(ctrl, text="strip position", bg=BG, fg=MUTED).pack(
                    side="left", padx=(6, 4))
                self.frac_var = tk.DoubleVar(value=0.5)
                sc = tk.Scale(ctrl, variable=self.frac_var, from_=0.0,
                              to=1.0, resolution=0.005, orient="horizontal",
                              bg=BG, fg=FG, troughcolor=BG2, length=320,
                              highlightthickness=0, showvalue=False,
                              command=lambda _v: self.reload_xsections())
                sc.pack(side="left")
                tk.Label(ctrl, text="width (m)", bg=BG, fg=MUTED).pack(
                    side="left", padx=(16, 4))
                self.width_var = tk.StringVar(value="2")
                self.width_var.trace_add(
                    "write", lambda *_: self.reload_xsections())
                tk.Entry(ctrl, textvariable=self.width_var, width=5,
                         bg=BG2, fg=FG, insertbackground=FG, relief="flat",
                         highlightthickness=1,
                         highlightbackground="#333a48").pack(side="left")
                self.xviews = []
                for _ in range(2):
                    z = ZoomableImage(f)
                    z.pack(side="left", fill="both", expand=True, padx=2)
                    self.xviews.append(z)
            elif title == "3D view":
                self.views3d = {}
                for side in ("A", "B"):
                    vf = tk.Frame(f, bg=BG)
                    vf.pack(side="left", fill="both", expand=True,
                            padx=(4, 4))
                    tk.Label(vf, text=f"run {side}", bg=BG, fg=MUTED
                             ).pack(anchor="w")
                    v = Viewer3D(vf, self.app, compact=True)
                    v.pack(fill="both", expand=True)
                    self.views3d[side] = v
            else:
                for _ in range(2):
                    z = ZoomableImage(f)
                    z.pack(side="left", fill="both", expand=True, padx=2)
                    self.views[title].append(z)
        self.reload()

    def _tab_changed(self, _e=None):
        tab = self.nb.select()
        if not tab:
            return
        for w in self.nametowidget(tab).winfo_children():
            if isinstance(w, ZoomableImage):
                w._render()

    def reload(self):
        for title in ("top-down", "DTM", "canopy height"):
            key, legacy = {
                "top-down": ("topdown", "topdown.png"),
                "DTM": ("dtm_preview", "dtm.png"),
                "canopy height": ("chm_preview", "chm.png")}[title]
            for side, z in zip(("A", "B"), self.views[title]):
                h = self.sel[side].get()
                meta = M.read_meta(self.sweep_id, h) or {}
                name = (meta.get("outputs") or {}).get(key) or legacy
                path = os.path.join(M.run_dir(self.sweep_id, h), name)
                z.set_image_pil(Image.open(path)
                                if os.path.exists(path) else None)
        self.reload_xsections()
        self._reload_3d()

    def _reload_3d(self):
        """Point each side's 3D viewer at its run's cached points.

        Reuses the cross-section caches when already loaded; loads
        per-viewer with the viewer itself as job owner, so these loads
        never cancel the cross-section render jobs.
        """
        if not hasattr(self, "_caches"):
            self._caches = {}
        for side, viewer in self.views3d.items():
            h = self.sel[side].get()
            viewer._run_hash = h
            if not h:
                continue
            if h in self._caches:
                viewer.set_data(self._caches[h])
                continue
            rdir = M.run_dir(self.sweep_id, h)
            if not os.path.exists(os.path.join(rdir, "render_points.npz")):
                continue

            def job(h=h):
                return O.load_cache(M.run_dir(self.sweep_id, h))

            def done(data, err, h=h, viewer=viewer):
                if err is None and data:
                    self._caches[h] = data
                    if viewer._run_hash == h:
                        viewer.set_data(data)

            self.app.submit(viewer, job, done)

    def reload_xsections(self):
        """Debounced: coalesce rapid slider drags into one render pair."""
        if getattr(self, "_pending_render", None) is not None:
            self.after_cancel(self._pending_render)
        self._pending_render = self.after(300, self._render_xsections_now)

    def _render_xsections_now(self):
        self._pending_render = None
        self.app.cancel_pending(self)
        try:
            width = max(0.1, min(30.0, float(self.width_var.get() or 2)))
        except ValueError:
            return
        frac = max(0.0, min(1.0, self.frac_var.get()))
        rdirs = [M.run_dir(self.sweep_id, self.sel[s].get())
                 for s in ("A", "B")]
        if not hasattr(self, "_caches"):
            self._caches = {}

        def job():
            out, keep = [], {}
            for rd in rdirs:
                if rd in self._caches:
                    data = self._caches[rd]
                else:
                    data = O.load_cache(rd)
                keep[rd] = data
                out.append(O.render_xsection(data, frac=frac, width=width))
            self._caches = keep  # evict runs no longer displayed
            return out

        def done(pngs, err):
            if err or not pngs:
                return
            for z, png in zip(self.xviews, pngs):
                z.set_image_bytes(png)

        self.app.submit(self, job, done)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDAL Sweep")
        self.geometry("1280x820")
        self.configure(bg=BG)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", background=BG2, foreground=FG,
                        fieldbackground=BG2, bordercolor=BG,
                        rowheight=22)
        style.configure("Treeview.Heading", background=BG, foreground=MUTED)
        style.configure("TNotebook", background=BG)
        style.configure("TNotebook.Tab", background=BG2, foreground=MUTED,
                        padding=(10, 4))
        style.map("TNotebook.Tab", background=[("selected", ACCENT)],
                  foreground=[("selected", "#0d1117")])
        style.configure("Vertical.TScrollbar", background=BG2,
                        troughcolor=BG)

        # left: sweep list; right: content panels
        self.paned = tk.PanedWindow(self, orient="horizontal", bg=BG,
                                    sashwidth=5, bd=0)
        self.paned.pack(fill="both", expand=True)
        left = tk.Frame(self.paned, bg=BG)
        self.paned.add(left, minsize=280, width=300)
        tk.Label(left, text="sweeps", bg=BG, fg=MUTED).pack(
            anchor="w", padx=10, pady=(10, 2))
        self.sweep_list = tk.Listbox(left, bg=BG2, fg=FG,
                                     selectbackground=ACCENT,
                                     selectforeground="#0d1117",
                                     relief="flat", highlightthickness=0,
                                     activestyle="none")
        self.sweep_list.pack(fill="both", expand=True, padx=10)
        self.sweep_list.bind("<<ListboxSelect>>", self._on_select)
        btns = tk.Frame(left, bg=BG)
        btns.pack(fill="x", padx=10, pady=8)
        tk.Button(btns, text="new sweep", bg=ACCENT, fg="#0d1117",
                  activebackground=ACCENT, relief="flat",
                  command=lambda: self.show_build()).pack(fill="x")
        tk.Button(btns, text="examine results", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._examine_selected).pack(fill="x", pady=(6, 0))
        tk.Button(btns, text="refresh", fg=ACCENT, bg=BG,
                  activebackground=BG, relief="flat",
                  command=lambda: self.refresh_sweeps()).pack(fill="x")
        tk.Button(btns, text="delete sweep", fg=BAD, bg=BG,
                  activebackground=BG, relief="flat",
                  command=self._delete_sweep).pack(fill="x")
        self.jobs_lbl = tk.Label(left, text="no running jobs", bg=BG,
                                 fg=MUTED, anchor="w", wraplength=270,
                                 justify="left")
        self.jobs_lbl.pack(anchor="w", padx=10, pady=(0, 6))
        # right: content host
        self.content = tk.Frame(self.paned, bg=BG)
        self.paned.add(self.content, minsize=600)
        self.help_lbl = tk.Label(self, text="", bg=BG, fg=MUTED, anchor="w")
        self.help_lbl.pack(fill="x", padx=10, pady=(0, 4))

        self._panel = None
        self._panel_key = None
        self._sweeps = []
        self._sweep_sig = None
        self._jobs = {}      # owner -> (generation, done callback)
        self.refresh_sweeps()
        self.show_build()
        self.after(2000, self._tick)
        _drain_done_queue(self)

    # ------------------------------------------------------------ panels

    def _clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()

    def _switch(self, key, factory, force=False):
        if self._panel_key == key and not force:
            return
        self._clear_content()
        self._panel = None  # old panel destroyed; nothing to update during
        self._panel = factory()  # the new one's construction
        self._panel.pack(fill="both", expand=True)
        self._panel_key = key

    def show_build(self, force=False):
        self._switch(("build",), lambda: BuildPanel(self.content, self),
                     force=force)

    def show_progress(self, sweep_id):
        self._switch(("progress", sweep_id),
                     lambda: ProgressPanel(self.content, self, sweep_id))
        self.select_sweep(sweep_id)

    def show_results(self, sweep_id, force=False):
        self._switch(("results", sweep_id),
                     lambda: ResultsPanel(self.content, self, sweep_id),
                     force=force)

    def show_detail(self, sweep_id, run_hash):
        self._switch(("detail", sweep_id, run_hash),
                     lambda: DetailPanel(self.content, self, sweep_id,
                                        run_hash))

    def show_compare(self, sweep_id, a=None, b=None):
        self._switch(("compare", sweep_id, a, b),
                     lambda: ComparePanel(self.content, self, sweep_id, a, b))

    # -------------------------------------------------------------- sweeps

    def refresh_sweeps(self, select=None):
        """Refresh the sweep list; quiet unless something changed.

        Preserves the current selection and never touches the content
        panel, so it can run from the periodic tick without stealing
        focus or interrupting the user.
        """
        summaries = M.sweep_summaries()
        sig = [(s["id"], s["name"], s["state"], s["done"], s["failed"],
                s["total"]) for s in summaries]
        self._update_jobs_lbl(summaries)
        if sig == getattr(self, "_sweep_sig", None) and not select:
            return
        self._sweep_sig = sig
        sel = self.sweep_list.curselection()
        keep = self._sweeps[sel[0]]["id"] if (self._sweeps and sel) else None
        self._sweeps = summaries
        self.sweep_list.delete(0, "end")
        for s in self._sweeps:
            failed = f" {s['failed']}!" if s.get("failed") else ""
            self.sweep_list.insert(
                "end", f"{s['name']}\n    {s['state']}  {s['done']}/"
                       f"{s['total']}{failed}")
        target = select or keep
        if target:
            self.select_sweep(target)

    def _update_jobs_lbl(self, summaries):
        running = [s for s in summaries if s["state"] in ("running", "starting")]
        if running:
            txt = " | ".join(f"{s['name']}: {s['done']}/{s['total']}"
                             for s in running)
            self.jobs_lbl.configure(text="running: " + txt, fg=WARN)
        else:
            self.jobs_lbl.configure(text="no running jobs", fg=MUTED)

    def select_sweep(self, sweep_id):
        for i, s in enumerate(self._sweeps):
            if s["id"] == sweep_id:
                self.sweep_list.selection_clear(0, "end")
                self.sweep_list.selection_set(i)
                self.sweep_list.see(i)
                return

    def _on_select(self, _e=None):
        sel = self.sweep_list.curselection()
        if not sel:
            return
        self.show_progress(self._sweeps[sel[0]]["id"])

    def _examine_selected(self, _e=None):
        sel = self.sweep_list.curselection()
        if not sel:
            messagebox.showinfo("examine results",
                                "select a sweep in the list first")
            return
        self.show_results(self._sweeps[sel[0]]["id"])

    def _delete_sweep(self):
        sel = self.sweep_list.curselection()
        if not sel:
            return
        s = self._sweeps[sel[0]]
        if not messagebox.askyesno(
                "delete sweep",
                f"Delete sweep '{s['name']}' and all its runs?\n"
                f"This cannot be undone."):
            return
        M.stop_sweep(s["id"])  # make sure nothing is still writing
        import shutil
        shutil.rmtree(M.sweep_path(s["id"]))
        self.refresh_sweeps()
        self.show_build()

    # ------------------------------------------------------------ helpers

    def set_help(self, text):
        self.help_lbl.configure(text=text)

    def update_combo_count(self):
        panel = getattr(self, "_panel", None)
        if isinstance(panel, BuildPanel):
            panel.update_count()

    def submit(self, owner, job, done):
        """Run job in a thread; only the latest per owner updates the UI."""
        self.cancel_pending(owner)
        gen = {}
        self._jobs[owner] = gen
        t = threading.Thread(
            target=lambda: self._run_job(owner, gen, job, done), daemon=True)
        t.start()

    def _run_job(self, owner, gen, job, done):
        try:
            result, err = job(), None
        except Exception as e:
            result, err = None, e
        if self._jobs.get(owner) is not gen:
            return
        def _deliver():
            try:
                if self._jobs.get(owner) is gen:
                    done(result, err)
            except tk.TclError:
                pass  # target widget was destroyed (panel switched)
        _DONE_QUEUE.append(_deliver)

    def cancel_pending(self, owner):
        if owner in self._jobs:
            self._jobs[owner] = {}  # invalidates in-flight generation

    def _tick(self):
        # nonintrusive periodic update: progress panel (if visible) and
        # the sweep list / jobs line. No focus changes, no panel switching.
        if isinstance(self._panel, ProgressPanel):
            self._panel.refresh()
        self.refresh_sweeps()
        self.after(2000, self._tick)


if __name__ == "__main__":
    App().mainloop()
