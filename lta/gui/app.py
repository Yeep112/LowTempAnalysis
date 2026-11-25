"""低溫量測分析介面（Tkinter）。

    python -m lta gui [db 路徑]

版面：
  頂部       資料庫選擇、重新整理、自動更新
  左欄       1D / 2D 切換 → 功能卡片清單（依分組）
  中欄       run 清單（篩選、多選、排序）→ 參數 → 繪圖 / 存圖 / 匯出
  右欄       圖（工具列、滑鼠讀值、點擊切線）→ 文字結果
"""
from __future__ import annotations

import csv
import os
import queue
import re
import threading
import time
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.figure import Figure
from matplotlib import colormaps
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from ..core.db import QDB
from ..core.common import fmt_T, safe_name, ensure_dir
from ..core.measurement import MODES
from ..core.registry import load_analyses, Item, Drag, group_params
from ..batch.runner import load_settings, TOOL_ROOT
from ..catalog import find_dbs, db_mtime
from .hover import Hover
from . import theme as T
from . import style as ST
from .colors import parse_color, mix, rgb_text
import json
import subprocess
import sys


class FitToolbar(NavigationToolbar2Tk):
    """Home 鍵：回到剛畫好時的範圍，並讓圖剛好填滿視窗。"""

    def __init__(self, canvas, window, on_home, **kw):
        self._on_home = on_home
        super().__init__(canvas, window, **kw)

    def home(self, *args):
        self._on_home()
from .widgets import card, section_title, Chip, ScrollFrame, Segmented, FeatureList, Paned


class SmoothCanvas(FigureCanvasTkAgg):
    """拉動視窗、分隔線時，等停下來 0.15 秒才重畫圖（不會每移動一點就整張重畫而卡頓）。"""
    _resize_job = None

    def resize(self, event):
        w = self.get_tk_widget()
        if self._resize_job is not None:
            try:
                w.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = w.after(150, lambda e=event: self._do_resize(e))

    def _do_resize(self, event):
        self._resize_job = None
        try:
            FigureCanvasTkAgg.resize(self, event)
        except Exception:
            pass

LEGENDS = ["溫度 (run)", "溫度", "run", "實驗描述"]
CMAP_CHOICES = ["自動", "turbo", "viridis", "plasma", "coolwarm", "tab10", "tab20", "jet"]
SORTS = ["溫度", "run"]
CATS = ["1D", "2D"]
ASSETS = os.path.join(TOOL_ROOT, "assets")


class _Cancelled(Exception):
    """背景計算已經過時（參數又改了），停止。"""


class App:
    UPDATE_INTERVAL_MS = 30 * 60 * 1000      # 執行中每 30 分鐘檢查一次新版本

    def __init__(self, root, db_path=None, restore=None):
        self.root = root
        self._dead = False
        self.settings = load_settings()
        self.analyses = load_analyses()
        self.db = None
        self.cache = {}
        self.all_runs = []
        self.category = "1D"
        self.current = {c: self.analyses[c][0] for c in CATS if self.analyses[c]}
        self.param_vars = {}            # 設定群組 -> {參數 -> tk 變數}
        self.saved_params = {}          # 設定群組 -> {參數 -> 文字}（和預設不同的值，存在樣品資料夾）
        self.labels = {}                # 分析名稱 -> {"suptitle": 文字, "axes": {序號: {title/xlabel/ylabel}}}
        self._param_defs = {}           # (群組, 參數) -> P
        self._text_boxes = {}           # (群組, 參數) -> 多行文字框
        self._save_job = None
        self._loading_params = False
        self.result = None
        self.last_items = []
        self._click_cid = None
        self._plot_job = None
        self._compute_gen = 0
        self._compute_job = None
        self._history, self._redo = [], []
        self._undoing = False
        self._legdrag = None
        self._suspend = False
        self._gen = 0
        self._queue = queue.Queue()
        self._db_paths = {}
        self._mtime = None
        self._sort = ("run", False)
        self._filters = {c: "" for c in CATS}
        self.gui_settings = ST.load_settings()
        self.plot_style = {**ST.DEFAULT_STYLE, **self.gui_settings.get("style", {})}
        self.per_run = {}               # 分析名稱 -> {參數 -> {run_id: 文字}}（存在樣品資料夾）
        self._drag = None
        self._draggables = []
        self._restyle_job = None
        self._home_limits = {}
        self.run_colors = {}            # run_id -> '#rrggbb'（使用者自訂，存在樣品資料夾）
        self.style = ttk.Style(root)
        T.setup_ttk(self.style, "1D")

        root.title("低溫量測分析 Low-Temperature Analysis")
        try:
            sw, sh = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080
        root.geometry(f"{min(T.px(1600), sw - 40)}x{min(T.px(940), sh - 80)}")
        root.minsize(1200, 720)
        root.configure(bg=T.BG)
        self._build()
        self._load_feature_list()
        self._welcome()
        root.after(200, self._poll_queue)
        root.after(10000, self._auto_update)
        root.protocol("WM_DELETE_WINDOW", self._close)
        if restore:
            self._restore(restore)
        else:
            if self.gui_settings.get("auto_update_check", True):
                root.after(3000, self.check_update)
            if db_path:
                self.open_db(db_path)
            self._scan_dbs(open_newest=not db_path)
        root.after(self.UPDATE_INTERVAL_MS, self._periodic_update_check)
        if not restore:
            root.after(1500, self._maybe_whats_new)

    # ================================================================== 更新後：這版新增了什麼
    def _maybe_whats_new(self):
        """更新（或手動換成新版）後第一次開啟 → 跳出這次新增的功能。"""
        if self._dead:
            return
        from .. import updater
        cfg = self.gui_settings
        running = self._running_version
        since = cfg.pop("updated_from", None) or cfg.get("last_seen_version")
        cfg["last_seen_version"] = running
        ST.save_settings(cfg)
        if not since or updater.parse_version(running) <= updater.parse_version(since):
            return
        if not cfg.get("show_whats_new", True):
            return
        rel = updater.read_release()
        entries = [(v, d, n) for v, d, n in updater.all_notes(rel)
                   if updater.parse_version(since) < updater.parse_version(v) <= updater.parse_version(running)]
        if entries:
            self.show_whats_new(entries, since)

    def show_whats_new(self, entries, since=None):
        a = T.ACCENTS[self.category]
        win = tk.Toplevel(self.root)
        win.title("這版新增了什麼")
        win.configure(bg=T.CARD)
        win.transient(self.root)
        win.resizable(False, True)
        head = tk.Frame(win, bg=a["main"], padx=18, pady=12)
        head.pack(fill="x")
        tk.Label(head, text=f"已更新到 v{entries[0][0]}", bg=a["main"], fg=a.get("on", "white"),
                 font=T.font(14, "bold")).pack(anchor="w")
        if since:
            tk.Label(head, text=f"上次使用的是 v{since}，以下是這段期間新增的功能", bg=a["main"],
                     fg=T.mix_color(a.get("on", "#ffffff"), a["main"], 0.25), font=T.font(9)).pack(anchor="w")
        box = ScrollFrame(win)
        box.pack(fill="both", expand=True, padx=16, pady=(10, 4))
        body = box.body
        body.columnconfigure(1, weight=1)
        r = 0
        for k, (v, d, notes) in enumerate(entries[:8]):
            tk.Label(body, text=f"v{v}" + (f"　{d}" if d else ""), bg=T.CARD, fg=a["text"],
                     font=T.font(10, "bold")).grid(row=r, column=0, columnspan=2, sticky="w", pady=(10 if k else 0, 3))
            r += 1
            for n in notes:
                tk.Label(body, text="✦", bg=T.CARD, fg=a["main"], font=T.font(9)).grid(row=r, column=0, sticky="nw",
                                                                                       padx=(2, 6), pady=1)
                tk.Label(body, text=n, bg=T.CARD, fg=T.TEXT, font=T.font(10), wraplength=T.px(470), justify="left",
                         anchor="w").grid(row=r, column=1, sticky="w", pady=1)
                r += 1
        bf = tk.Frame(win, bg=T.CARD, padx=16, pady=10)
        bf.pack(fill="x")
        dont = tk.BooleanVar(value=False)
        ttk.Checkbutton(bf, text="以後更新時不要自動顯示", variable=dont).pack(side="left")

        def close(open_log=False):
            if dont.get():
                self.gui_settings["show_whats_new"] = False
                ST.save_settings(self.gui_settings)
            win.destroy()
            if open_log:
                self.open_settings(tab="更新")
        ttk.Button(bf, text="知道了", style="Accent.TButton", command=close).pack(side="right")
        ttk.Button(bf, text="完整更新記錄", style="Ghost.TButton",
                   command=lambda: close(True)).pack(side="right", padx=6)
        n_lines = sum(len(n) + 1 for _, _, n in entries[:8])
        win.geometry(f"{T.px(560)}x{T.px(min(160 + 28 * n_lines, 620))}")
        try:
            win.update_idletasks()
            x = self.root.winfo_rootx() + (self.root.winfo_width() - T.px(560)) // 2
            y = self.root.winfo_rooty() + 120
            win.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            pass
        win.bind("<Escape>", lambda e: close())
        return win

    # ================================================================== 換配色：重建畫面
    def rebuild(self):
        """套用新配色：在同一個視窗裡重建整個介面，資料、選取、功能都保留（不用重新開啟）。"""
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_per_run()
        for job in (self._plot_job, self._restyle_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        state = dict(db=self.db.path if self.db else None, cache=self.cache, category=self.category,
                     current={c: a.name for c, a in self.current.items()},
                     selection=list(self.tree.selection()), filters=dict(self._filters),
                     filter=self.filter_var.get(), db_paths=dict(self._db_paths),
                     update_info=self._update_info, prompted=self._prompted_version,
                     history=(self._history, self._redo))
        self._save_sash()
        self._dead = True
        self._gen += 1
        for w in self.root.winfo_children():
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        T.apply_scaling(self.root, self.gui_settings.get("ui_scale", "auto"))
        T.apply_theme(self.gui_settings)
        T.apply_mpl_style()
        self.root.configure(bg=T.BG)
        return App(self.root, restore=state)

    def _restore(self, st):
        self._db_paths = st["db_paths"]
        self.db_combo["values"] = list(self._db_paths)
        if st["db"]:
            self.cache = st["cache"]
            self.open_db(st["db"], cache=st["cache"])
        self._filters = st["filters"]
        if st["category"] != self.category:
            self.segment.select(0 if st["category"] == "1D" else 1, notify=False)
            self._on_category(st["category"])
        for c, name in st["current"].items():
            a = next((x for x in self.analyses.get(c, []) if x.name == name), None)
            if a:
                self.current[c] = a
        self._load_feature_list()
        self.filter_var.set(st["filter"])
        sel = [i for i in st["selection"] if self.tree.exists(i)]
        if sel:
            self.tree.selection_set(sel)
            self._on_select()
        self._prompted_version = st["prompted"]
        self._history, self._redo = st.get("history", ([], []))
        if st["update_info"]:
            self._on_update_info(st["update_info"], False, None)

    # ================================================================== 版面
    def _build(self):
        self._build_header()
        body = tk.Frame(self.root, bg=T.BG)
        body.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        pw = Paned(body, orient="horizontal")
        pw.pack(fill="both", expand=True)
        self._main_paned = pw

        left_o, left = card(pw, pad=10)
        mid_o, mid = card(pw, pad=10)
        right_o, right = card(pw, pad=10)
        pw.add_pane(left_o, stretch=False, size=T.px(300), minsize=T.px(270))
        pw.add_pane(mid_o, stretch=False, size=T.px(470), minsize=T.px(400))
        pw.add_pane(right_o, stretch=True, minsize=T.px(400))
        self._build_left(left)
        self._build_middle(mid)
        self._build_right(right)
        self._build_status()

        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Control-s>", lambda e: self.save_figure())
        self.root.bind("<Control-z>", self.undo)
        self.root.bind("<Control-y>", self.redo)
        self.root.bind("<Control-Z>", self.redo)
        self.root.bind("<Return>", lambda e: None if isinstance(e.widget, (tk.Entry, ttk.Entry)) else self.plot())

    def _build_header(self):
        h = tk.Frame(self.root, bg=T.HEADER, height=T.px(64))
        h.pack(fill="x")
        logo = tk.Frame(h, bg=T.HEADER)
        logo.pack(side="left", padx=(16, 24), pady=8)
        self.logo_mark = tk.Label(logo, text="◢", bg=T.HEADER, fg=T.ACCENTS["1D"]["main"],
                                  font=T.font(22, "bold"))
        try:                                   # 有圖示檔就用圖示
            self._logo_img = tk.PhotoImage(file=os.path.join(ASSETS, "icon_40.png"))
            self.logo_mark.configure(image=self._logo_img, text="")
        except Exception:
            pass
        self.logo_mark.pack(side="left", padx=(0, 10))
        tf = tk.Frame(logo, bg=T.HEADER)
        tf.pack(side="left")
        tk.Label(tf, text="低溫量測分析", bg=T.HEADER, fg=T.HEADER_TEXT, font=T.font(15, "bold")).pack(anchor="w")
        sub = tk.Frame(tf, bg=T.HEADER)
        sub.pack(anchor="w")
        tk.Label(sub, text="Low-Temperature Analysis", bg=T.HEADER, fg=T.HEADER_MUTED,
                 font=T.font(9)).pack(side="left")
        from .. import __version__
        self.version_badge = tk.Label(sub, text=f"v{__version__}", bg=T.HEADER_2, fg=T.HEADER_MUTED,
                                      font=T.font(8, "bold"), padx=6, cursor="hand2")
        self.version_badge.pack(side="left", padx=(8, 0))
        self.version_badge.bind("<Button-1>", lambda e: self._badge_click())
        self._running_version = __version__

        right = tk.Frame(h, bg=T.HEADER)
        right.pack(side="left", fill="x", expand=True, pady=12, padx=(0, 16))
        tk.Label(right, text="資料庫", bg=T.HEADER, fg=T.HEADER_MUTED, font=T.font(10)).pack(side="left", padx=(0, 8))
        self.db_var = tk.StringVar()
        self.db_combo = ttk.Combobox(right, textvariable=self.db_var, state="readonly", font=T.font(10))
        self.db_combo.pack(side="left", fill="x", expand=True, ipady=2)
        self.db_combo.bind("<<ComboboxSelected>>", lambda e: self.open_db(self._db_paths.get(self.db_var.get())))
        ttk.Button(right, text="▤ 開啟檔案", style="Header.TButton", command=self._browse_db).pack(side="left", padx=(8, 4))
        ttk.Button(right, text="⟳ 重新整理", style="Header.TButton", command=self.refresh).pack(side="left", padx=4)
        self.tools_btn = ttk.Button(right, text="▾ 工具", style="Header.TButton",
                                    command=lambda: self._tools_menu(self.tools_btn))
        self.tools_btn.pack(side="left", padx=4)
        ttk.Button(right, text="⚙ 設定", style="Header.TButton", command=self.open_settings).pack(side="left", padx=4)
        self.update_btn = ttk.Button(right, text="⬆ 有新版本", style="Accent.TButton",
                                     command=lambda: self._prompt_update(self._update_info))
        self._update_info = None
        self._prompted_version = None
        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(right, text="量測中自動更新", variable=self.auto_var, bg=T.HEADER, fg=T.HEADER_TEXT,
                       selectcolor=T.HEADER_2, activebackground=T.HEADER, activeforeground=T.HEADER_TEXT,
                       font=T.font(10), bd=0, highlightthickness=0).pack(side="left", padx=(10, 0))

    def _build_left(self, parent):
        self.segment = Segmented(parent, [("1D", "1D 資料分析", "曲線：IV、VI…"),
                                          ("2D", "2D 資料分析", "色圖：磁場 × 電流…")], self._on_category)
        self.segment.pack(fill="x")
        tk.Label(parent, text="分析功能", bg=T.CARD, fg=T.TEXT, font=T.font(11, "bold"),
                 anchor="w").pack(fill="x", pady=(14, 0))
        tk.Label(parent, text="點選功能；變淡的功能不支援目前選取的資料", bg=T.CARD, fg=T.FAINT,
                 font=T.font(8), anchor="w").pack(fill="x")
        sf = ScrollFrame(parent)
        sf.pack(fill="both", expand=True, pady=(2, 0))
        self.features = FeatureList(sf.body, self._on_feature)
        self.features.pack(fill="both", expand=True)
        self._feature_scroll = sf

    def _build_middle(self, parent):
        vp = Paned(parent, orient="vertical")
        vp.configure(bg=T.CARD)
        vp.pack(fill="both", expand=True)
        self._mid_paned = vp
        data = tk.Frame(vp, bg=T.CARD)
        prm = tk.Frame(vp, bg=T.CARD)
        vp.add_pane(data, stretch=True, minsize=T.px(150))
        vp.add_pane(prm, stretch=True, minsize=T.px(150))

        # ---- 資料 ----
        row, _ = section_title(data, "資料", "☰", T.MUTED)
        row.pack(fill="x")
        self.count_chip = Chip(row, "")
        self.count_chip.pack(side="right")
        self._list_head = row
        fr = tk.Frame(data, bg=T.CARD)
        fr.pack(fill="x", pady=(8, 2))
        tk.Label(fr, text="⌕", bg=T.CARD, fg=T.MUTED, font=T.font(10)).pack(side="left")
        self.filter_var = tk.StringVar()
        ttk.Entry(fr, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=(4, 0))
        self.filter_var.trace_add("write", lambda *a: self._fill_tree())
        tk.Label(data, text="例：T_1.6K　I+_38　104-116（run 範圍）　多個條件用空白分開",
                 bg=T.CARD, fg=T.FAINT, font=T.font(8), anchor="w").pack(fill="x")
        tf = tk.Frame(data, bg=T.CARD)
        tf.pack(fill="both", expand=True, pady=(4, 0))
        cols = ("c", "run", "mode", "T", "pts", "time", "name")
        heads = ("色", "run", "量測", "T (K)", "點數", "時間", "實驗描述")
        widths = tuple(T.px(w) for w in (26, 46, 74, 50, 56, 88, 250))
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, hd, w in zip(cols, heads, widths):
            self.tree.heading(c, text=hd, command=lambda c=c: self._sort_by(c))
            self.tree.column(c, width=w, minwidth=T.px(22 if c == "c" else 40), stretch=(c == "name"),
                             anchor="center" if c == "c" else ("w" if c in ("name", "mode", "time") else "e"))
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("odd", background=T.STRIPE)
        for m, col in T.MODE_COLORS.items():
            self.tree.tag_configure(f"m_{m}", foreground=col)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Control-a>", lambda e: (self._select_all(), "break")[1])
        self.tree.bind("<Button-3>", self._tree_menu)
        self.tree.bind("<Button-2>", self._tree_menu)
        self.tree.bind("<Motion>", self._tree_motion, add="+")
        bf = tk.Frame(data, bg=T.CARD)
        bf.pack(fill="x", pady=(6, 4))
        ttk.Button(bf, text="全選", style="Small.TButton", command=self._select_all).pack(side="left")
        ttk.Button(bf, text="清除", style="Small.TButton", command=self._clear_sel).pack(side="left", padx=4)
        self.color_btn = ttk.Button(bf, text="● 顏色", style="Small.TButton",
                                    command=lambda: self._color_menu_at(self.color_btn))
        self.color_btn.pack(side="left")
        self.dedup_btn = ttk.Button(bf, text="⧉ 同溫度留一筆", style="Small.TButton",
                                    command=lambda: self._dedup_menu(self.dedup_btn))
        self.dedup_btn.pack(side="left", padx=4)
        self.load_chip = Chip(self._list_head, "")
        self.load_chip.pack(side="right", padx=(0, 6))

        # ---- 參數 ----
        head = tk.Frame(prm, bg=T.CARD)
        head.pack(fill="x", pady=(8, 0))
        self.prm_icon = tk.Label(head, text="•", bg=T.CARD, fg=T.ACCENTS["1D"]["main"], font=T.font(13, "bold"))
        self.prm_icon.pack(side="left", padx=(0, 6))
        self.prm_title = tk.Label(head, text="參數", bg=T.CARD, fg=T.TEXT, font=T.font(11, "bold"),
                                  anchor="w", justify="left", wraplength=T.px(360))
        self.prm_title.pack(side="left", fill="x", expand=True)
        ttk.Button(head, text="恢復預設", style="Small.TButton", command=self._reset_params).pack(side="right")
        self.help_lab = tk.Label(prm, text="", bg=T.CARD, fg=T.MUTED, font=T.font(9), anchor="w",
                                 justify="left", wraplength=T.px(430))
        self.help_lab.pack(fill="x", pady=(2, 4))
        self.param_scroll = ScrollFrame(prm)
        self.param_scroll.pack(fill="both", expand=True)
        self.param_frame = self.param_scroll.body
        prm.bind("<Configure>", lambda e: (self.help_lab.configure(wraplength=max(e.width - T.px(16), 120)),
                                          self.prm_title.configure(wraplength=max(e.width - T.px(140), 100))),
                 add="+")

        disp = tk.Frame(prm, bg=T.CARD)
        disp.pack(side="bottom", fill="x", pady=(6, 0), before=self.param_scroll)
        self.legend_var = tk.StringVar(value=LEGENDS[0])
        self.cmap_var = tk.StringVar(value="自動")
        self.sort_var = tk.StringVar(value=SORTS[0])
        for i, (lab, var, ch, w) in enumerate((("圖例", self.legend_var, LEGENDS, 10),
                                               ("顏色", self.cmap_var, CMAP_CHOICES, 8),
                                               ("排序", self.sort_var, SORTS, 6))):
            tk.Label(disp, text=lab, bg=T.CARD, fg=T.MUTED, font=T.font(9)).grid(row=0, column=2 * i, sticky="w", padx=(0, 4))
            cb = ttk.Combobox(disp, textvariable=var, values=ch, state="readonly", width=w)
            cb.grid(row=0, column=2 * i + 1, padx=(0, 10))
            cb.bind("<<ComboboxSelected>>", lambda e: self._schedule_plot())
        self.autoplot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(disp, text="選取或改參數時自動重畫", variable=self.autoplot_var).grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        btn = tk.Frame(prm, bg=T.CARD)
        btn.pack(side="bottom", fill="x", pady=(8, 0), before=disp)
        ttk.Button(btn, text="▶  繪圖", style="Accent.TButton", command=self.plot).pack(side="left", fill="x", expand=True)
        ttk.Button(btn, text="⬇ 存圖", style="Ghost.TButton", command=self.save_figure).pack(side="left", padx=(6, 0))
        ttk.Button(btn, text="⇩ CSV", style="Ghost.TButton", command=self.export_csv).pack(side="left", padx=(6, 0))
        ttk.Button(btn, text="⧉ 新視窗", style="Ghost.TButton", command=self.pop_out).pack(side="left", padx=(6, 0))

    def _build_right(self, parent):
        head = tk.Frame(parent, bg=T.CARD)
        head.pack(fill="x")
        self.plot_icon = tk.Label(head, text="◢", bg=T.ACCENTS["1D"]["soft"], fg=T.ACCENTS["1D"]["main"],
                                  font=T.font(16, "bold"), width=2, pady=2)
        self.plot_icon.pack(side="left", padx=(0, 10))
        tt = tk.Frame(head, bg=T.CARD)
        tt.pack(side="left", fill="x", expand=True)
        self.plot_title = tk.Label(tt, text="尚未繪圖", bg=T.CARD, fg=T.TEXT, font=T.font(13, "bold"), anchor="w")
        self.plot_title.pack(fill="x")
        self.plot_sub = tk.Label(tt, text="", bg=T.CARD, fg=T.MUTED, font=T.font(9), anchor="w")
        self.plot_sub.pack(fill="x")
        self.plot_hint = tk.Label(tt, text="", bg=T.CARD, fg=T.ACCENTS["1D"]["text"], font=T.font(9), anchor="w")
        self.mode_chip = Chip(head, "")
        self.mode_chip.pack(side="right", padx=(6, 0))
        self.n_chip = Chip(head, "")
        self.n_chip.pack(side="right")

        self._build_style_bar(parent)
        vp = Paned(parent, orient="vertical")
        vp.configure(bg=T.CARD)
        vp.pack(fill="both", expand=True, pady=(4, 0))
        figf = tk.Frame(vp, bg=T.CARD)
        txtf = tk.Frame(vp, bg=T.CARD)
        vp.add_pane(figf, stretch=True, minsize=T.px(200))
        vp.add_pane(txtf, stretch=False, minsize=T.px(60))
        self._right_paned = vp
        self.root.after(400, self._init_sash)
        self.fig = Figure(figsize=(4, 3), layout="constrained")
        # 圖放在可捲動的區域裡：多張圖時每張保持最小尺寸，不夠放就出現捲軸，不會被壓扁
        host = tk.Frame(figf, bg=T.CARD)
        self.plot_host = tk.Canvas(host, bg="white", highlightthickness=0, bd=0)
        self._vscroll = ttk.Scrollbar(host, orient="vertical", command=self.plot_host.yview)
        self._hscroll = ttk.Scrollbar(host, orient="horizontal", command=self.plot_host.xview)
        self.plot_host.configure(xscrollcommand=self._hscroll.set, yscrollcommand=self._vscroll.set)
        self.plot_host.grid(row=0, column=0, sticky="nsew")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.canvas = SmoothCanvas(self.fig, master=self.plot_host)
        self.canvas.get_tk_widget().configure(width=320, height=240)
        self._plot_win = self.plot_host.create_window(0, 0, window=self.canvas.get_tk_widget(), anchor="nw")
        self._plot_need = (0, 0)
        self.plot_host.bind("<Configure>", lambda e: self._schedule_layout())
        w = self.canvas.get_tk_widget()
        w.bind("<MouseWheel>", self._plot_wheel, add="+")
        w.bind("<Shift-MouseWheel>", lambda e: self._plot_wheel(e, horizontal=True), add="+")
        w.bind("<Button-4>", lambda e: self._plot_wheel(e, delta=120), add="+")
        w.bind("<Button-5>", lambda e: self._plot_wheel(e, delta=-120), add="+")
        w.bind("<Control-MouseWheel>", self._zoom_wheel)
        w.bind("<Control-Button-4>", lambda e: self._zoom_wheel(e, delta=120))
        w.bind("<Control-Button-5>", lambda e: self._zoom_wheel(e, delta=-120))
        self.toolbar = FitToolbar(self.canvas, figf, self._fit_view, pack_toolbar=False)
        tb_bg = T.CARD if not T.DARK else "#e5e7eb"
        try:
            self.toolbar.configure(background=tb_bg)
            for w in self.toolbar.winfo_children():
                w.configure(background=tb_bg)
        except Exception:
            pass
        self.toolbar.update()
        self.toolbar.pack(side="top", fill="x")
        host.pack(side="top", fill="both", expand=True)
        self.hover = Hover(self.canvas)
        self.canvas.mpl_connect("button_press_event", self._drag_press)
        self.canvas.mpl_connect("motion_notify_event", self._drag_motion)
        self.canvas.mpl_connect("button_release_event", self._drag_release)

        th = tk.Frame(txtf, bg=T.CARD)
        th.pack(fill="x", pady=(6, 2))
        tk.Label(th, text="≡ 文字結果", bg=T.CARD, fg=T.MUTED, font=T.font(9, "bold")).pack(side="left")
        self.text = tk.Text(txtf, height=7, font=(T.MONO_FAMILY, 10), wrap="none", bg=T.TEXT_BG,
                            fg=T.TEXT, bd=0, highlightthickness=1, highlightbackground=T.BORDER,
                            padx=10, pady=8)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("head", foreground=T.ACCENTS["1D"]["text"], font=(T.FONT_FAMILY, 10, "bold"))
        self.text.tag_configure("err", foreground=T.ERROR)

    def _init_sash(self):
        """分隔線位置：用上次關閉時的位置，沒有就用預設比例。"""
        saved = self.gui_settings.get("panes") or {}
        try:
            if saved.get("main"):
                a, b = saved["main"]
                a = max(int(a), T.px(280))               # 舊版存的位置可能太窄 → 至少留給文字的寬度
                b = max(int(b), a + T.px(410))
                self._main_paned.set_sash(0, a)
                self._main_paned.set_sash(1, b)
            h = int(self._mid_paned.winfo_height() or 0)
            if saved.get("mid"):
                self._mid_paned.set_sash(0, saved["mid"])
            elif h > 200:
                self._mid_paned.set_sash(0, int(h * 0.58))
            h = int(self._right_paned.winfo_height() or 0)
            if saved.get("right"):
                self._right_paned.set_sash(0, saved["right"])
            elif h > 200:
                self._right_paned.set_sash(0, int(h * 0.80))
        except Exception:
            pass

    def _save_sash(self):
        try:
            panes = {"main": [self._main_paned.get_sash(i) for i in range(2)],
                     "mid": self._mid_paned.get_sash(0), "right": self._right_paned.get_sash(0)}
            if all(v is not None for v in panes["main"]) and panes["mid"] and panes["right"]:
                self.gui_settings["panes"] = panes
                ST.save_settings(self.gui_settings)
        except Exception:
            pass

    # ---------------------------------------------------------------- 標題與軸標籤
    def _label_axes(self, fig=None):
        fig = fig or self.fig
        return [ax for ax in fig.axes if ax.axison and ax.get_visible()]

    @staticmethod
    def _label_value(text):
        return "" if text.strip() == "-" else text

    def _apply_labels(self, a):
        ov = self.labels.get(a.name) or {}
        if ov.get("suptitle"):
            self.fig.suptitle(self._label_value(ov["suptitle"]))
        axes = self._label_axes()
        for idx, pos in (ov.get("legend") or {}).items():
            k = int(idx)
            if k < len(axes) and axes[k].get_legend() is not None:
                self._place_legend(axes[k].get_legend(), axes[k], *pos)
        for idx, hidden in (ov.get("hidden") or {}).items():
            k = int(idx)
            if k < len(axes) and hidden:
                self._apply_hidden(axes[k], hidden, rescale=False)
                axes[k].relim(visible_only=True)
                axes[k].autoscale_view()
        for idx, d in (ov.get("axes") or {}).items():
            k = int(idx)
            if k >= len(axes):
                continue
            ax = axes[k]
            for key, setter in (("title", ax.set_title), ("xlabel", ax.set_xlabel), ("ylabel", ax.set_ylabel)):
                if d.get(key):
                    setter(self._label_value(d[key]))

    def edit_labels(self):
        """✎ 修改圖的標題與軸標籤（依資料庫、分析各自記住；空白 = 自動，「-」= 不顯示）。"""
        if self.result is None:
            self.status("先畫出圖，再修改標題與軸標籤", "warn")
            return
        a = self.current[self.category]
        ov = self.labels.setdefault(a.name, {})
        win = tk.Toplevel(self.root)
        win.title(f"標題與軸標籤 — {a.name}")
        win.configure(bg=T.CARD)
        win.transient(self.root)
        tk.Label(win, text="空白 = 自動（灰字是目前的自動文字）；輸入「-」= 不顯示；可用 $...$ 寫數學式，例如 $I_c$ (mA)",
                 bg=T.CARD, fg=T.MUTED, font=T.font(9), wraplength=680, justify="left").pack(anchor="w", padx=12, pady=(10, 4))
        body = tk.Frame(win, bg=T.CARD)
        body.pack(fill="both", expand=True, padx=12)
        entries = {}
        sup_var = tk.StringVar(value=ov.get("suptitle", ""))
        tk.Label(body, text="整張圖的標題", bg=T.CARD, fg=T.TEXT, font=T.font(10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(body, textvariable=sup_var, width=50).grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
        cbars = ST.colorbar_axes(self.fig)
        row = 1
        saved_axes = ov.get("axes") or {}
        for k, ax in enumerate(self._label_axes()):
            kind = "色條" if ax in cbars else "子圖"
            tk.Label(body, text=f"{kind} {k + 1}", bg=T.CARD, fg=T.ACCENTS[self.category]["text"],
                     font=T.font(10, "bold")).grid(row=row, column=0, sticky="w", pady=(8, 0))
            row += 1
            keys = (("ylabel", "標籤"),) if ax in cbars else (("title", "標題"), ("xlabel", "X 標籤"), ("ylabel", "Y 標籤"))
            for key, lab in keys:
                auto = {"title": ax.get_title(), "xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel()}[key]
                var = tk.StringVar(value=saved_axes.get(str(k), {}).get(key, ""))
                tk.Label(body, text=lab, bg=T.CARD, fg=T.MUTED, font=T.font(9)).grid(row=row, column=0, sticky="w", padx=(12, 0))
                ttk.Entry(body, textvariable=var, width=36).grid(row=row, column=1, sticky="ew", pady=2)
                tk.Label(body, text=auto[:40], bg=T.CARD, fg=T.FAINT, font=T.font(9)).grid(row=row, column=2, sticky="w", padx=8)
                entries[(k, key)] = var
                row += 1
        body.columnconfigure(1, weight=1)

        def apply(close=False):
            ov["suptitle"] = sup_var.get().strip()
            axes = {}
            for (k, key), var in entries.items():
                if var.get().strip():
                    axes.setdefault(str(k), {})[key] = var.get().strip()
            ov["axes"] = axes
            self._save_per_run()
            if close:
                win.destroy()
            self.plot()

        def reset():
            sup_var.set("")
            for var in entries.values():
                var.set("")
            apply()
        bf = tk.Frame(win, bg=T.CARD)
        bf.pack(fill="x", padx=12, pady=10)
        ttk.Button(bf, text="完成", style="Accent.TButton", command=lambda: apply(True)).pack(side="right")
        ttk.Button(bf, text="套用", style="Ghost.TButton", command=apply).pack(side="right", padx=6)
        ttk.Button(bf, text="全部恢復自動", style="Ghost.TButton", command=reset).pack(side="left")

        def restore_legends():
            ov.pop("legend", None)
            ov.pop("hidden", None)
            self._save_per_run()
            self.plot()
        ttk.Button(bf, text="顯示隱藏的曲線、圖例回原位", style="Ghost.TButton",
                   command=restore_legends).pack(side="left", padx=6)
        return win

    # ---------------------------------------------------------------- 圖例：點一下隱藏 / 顯示，拖曳移動
    @staticmethod
    def _legend_handles(leg):
        return list(getattr(leg, "legend_handles", None) or getattr(leg, "legendHandles", []))

    def _legend_at(self, event):
        """滑鼠下面的圖例 → (legend, axes) 或 None。"""
        if event.x is None:
            return None
        for ax in self.fig.axes:
            leg = ax.get_legend()
            if leg is None or not leg.get_visible():
                continue
            try:
                if leg.get_window_extent().contains(event.x, event.y):
                    return leg, ax
            except Exception:
                pass
        return None

    def _legend_press(self, event):
        hit = self._legend_at(event)
        if hit is None:
            return False
        leg, ax = hit
        bb = leg.get_window_extent()
        self._legdrag = dict(leg=leg, ax=ax, x=event.x, y=event.y, x0=bb.x0, y0=bb.y0, moved=False, bg=None)
        return True

    @staticmethod
    def _place_legend(leg, ax, xa, ya):
        leg.set_bbox_to_anchor((xa, ya), transform=ax.transAxes)
        try:
            leg.set_loc("lower left")
        except Exception:
            leg._loc = 3

    def _legend_motion(self, event):
        d = self._legdrag
        if event.x is None:
            return
        dx, dy = event.x - d["x"], event.y - d["y"]
        if not d["moved"]:
            if abs(dx) + abs(dy) < 4:
                return
            d["moved"] = True
            self.hover.paused = True
            self.hover._hide_all()
            try:
                d["leg"].set_animated(True)
                self.canvas.draw()
                d["bg"] = self.canvas.copy_from_bbox(self.fig.bbox)
            except Exception:
                d["bg"] = None
        xa, ya = d["ax"].transAxes.inverted().transform((d["x0"] + dx, d["y0"] + dy))
        self._place_legend(d["leg"], d["ax"], xa, ya)
        d["pos"] = (float(xa), float(ya))
        if d["bg"] is not None:
            try:
                self.canvas.restore_region(d["bg"])
                d["ax"].draw_artist(d["leg"])
                self.canvas.blit(self.fig.bbox)
                return
            except Exception:
                d["bg"] = None
        self.canvas.draw_idle()

    def _legend_release(self, event):
        d, self._legdrag = self._legdrag, None
        self.hover.paused = False
        a = self.current[self.category]
        ov = self.labels.setdefault(a.name, {})
        axes = self._label_axes()
        idx = str(axes.index(d["ax"])) if d["ax"] in axes else None
        if d["moved"]:
            d["leg"].set_animated(False)
            if idx is not None and "pos" in d:
                ov.setdefault("legend", {})[idx] = [round(v, 4) for v in d["pos"]]
                self._save_per_run()
            self.canvas.draw_idle()
            self.status("圖例已移動（會記住位置；✎ 標題與軸標籤 → 圖例回到原位）", "ok")
            return
        # 沒有移動 = 點一下：隱藏 / 顯示那一項
        leg = d["leg"]
        label = None
        for t, h in zip(leg.get_texts(), self._legend_handles(leg)):
            try:
                if t.get_window_extent().contains(d["x"], d["y"]) or h.get_window_extent().contains(d["x"], d["y"]):
                    label = t.get_text()
                    break
            except Exception:
                continue
        if label is None or idx is None:
            return
        hidden = ov.setdefault("hidden", {}).setdefault(idx, [])
        if label in hidden:
            hidden.remove(label)
        else:
            hidden.append(label)
        self._apply_hidden(d["ax"], hidden)
        self._save_per_run()
        self.canvas.draw_idle()
        self.status(f"「{label}」{'已隱藏' if label in hidden else '已顯示'}（再點一下圖例可切換）", "ok")

    def _apply_hidden(self, ax, hidden, rescale=True):
        """把圖例名稱在 hidden 裡的曲線藏起來，圖例那一項變淡；Y 範圍只算看得到的資料。"""
        changed = False
        for art in list(ax.lines) + list(ax.collections) + list(ax.patches):
            lab = art.get_label()
            if not lab or lab.startswith("_"):
                continue
            vis = lab not in hidden
            if art.get_visible() != vis:
                art.set_visible(vis)
                changed = True
        leg = ax.get_legend()
        if leg is not None:
            for t, h in zip(leg.get_texts(), self._legend_handles(leg)):
                off = t.get_text() in hidden
                t.set_alpha(0.35 if off else 1.0)
                try:
                    h.set_alpha(0.25 if off else 1.0)
                except Exception:
                    pass
        if changed and rescale and not any(getattr(c, "lta_grid", None) is not None for c in ax.collections):
            ax.relim(visible_only=True)
            ax.autoscale_view()
            self._home_limits[ax] = (ax.get_xlim(), ax.get_ylim())

    # ---------------------------------------------------------------- Ctrl + 滾輪：以滑鼠位置為中心縮放
    def _zoom_wheel(self, event, delta=None):
        d = delta if delta is not None else getattr(event, "delta", 0)
        if not d:
            return "break"
        try:
            conv = getattr(self.canvas, "_event_mpl_coords", None)
            if conv is not None:
                x, y = conv(event)
            else:
                w = self.canvas.get_tk_widget()
                x, y = w.canvasx(event.x), self.fig.bbox.height - w.canvasy(event.y)
            x, y = float(x), float(y)
        except Exception:
            return "break"
        cbars = ST.colorbar_axes(self.fig)
        target = None
        for ax in self.fig.axes:
            if ax in cbars or not ax.axison or not ax.get_navigate():
                continue
            if ax.bbox.contains(x, y):
                target = ax
        if target is None:
            return "break"
        f = 0.8 if d > 0 else 1.25
        try:
            self.toolbar.push_current()
        except Exception:
            pass
        xd, yd = target.transData.inverted().transform((x, y))
        for axis, c, get, setl in ((target.xaxis, xd, target.get_xlim, target.set_xlim),
                                   (target.yaxis, yd, target.get_ylim, target.set_ylim)):
            tr = axis.get_transform()
            lo, hi = tr.transform(get())
            tc = tr.transform([c])[0]
            nlo, nhi = tc + (lo - tc) * f, tc + (hi - tc) * f
            setl(*tr.inverted().transform([nlo, nhi]))
        self.canvas.draw_idle()
        return "break"

    # ---------------------------------------------------------------- 多張圖的排版與捲動
    def _primary_axes(self, fig=None):
        fig = fig or self.fig
        return [ax for ax in fig.axes if ax.get_subplotspec() is not None and ax.axison]

    def _relayout_panels(self):
        """「上下排列」：把一列並排的子圖改成一欄（不改分析程式）。"""
        if self.plot_style.get("layout") != ST.LAYOUTS[1]:
            return
        prim = self._primary_axes()
        if len(prim) < 2:
            return
        specs = [ax.get_subplotspec() for ax in prim]
        gs = specs[0].get_gridspec()
        if any(sp.get_gridspec() is not gs for sp in specs) or gs.nrows != 1 or gs.ncols < 2:
            return
        new = self.fig.add_gridspec(gs.ncols, 1, height_ratios=gs.get_width_ratios())
        for ax, sp in zip(prim, specs):
            ax.set_subplotspec(new[sp.colspan.start:sp.colspan.stop, 0])

    def _panel_need(self):
        """這張圖需要的最小像素大小 (寬, 高)；0 = 跟視窗一樣大。"""
        if self.plot_style.get("layout") == ST.LAYOUTS[2]:
            return (0, 0)
        prim = self._primary_axes()
        if len(prim) < 2:
            return (0, 0)
        rows = cols = 1
        for ax in prim:
            gs = ax.get_subplotspec().get_topmost_subplotspec().get_gridspec()
            rows, cols = max(rows, gs.nrows), max(cols, gs.ncols)
        pw = int(int(self.plot_style.get("panel_w", 520)) * T.PX)       # 設定值以 100% 大小為準
        ph = int(int(self.plot_style.get("panel_h", 380)) * T.PX)
        return (cols * pw if cols > 1 else 0, rows * ph if rows > 1 else 0)

    def _schedule_layout(self):
        """拉動視窗時合併成一次排版（等停下來 0.08 秒）。"""
        job = getattr(self, "_layout_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        self._layout_job = self.root.after(80, self._layout_plot)

    def _layout_plot(self):
        self._layout_job = None
        try:
            aw, ah = int(self.plot_host.winfo_width() or 0), int(self.plot_host.winfo_height() or 0)
        except (tk.TclError, TypeError, ValueError):
            return
        if aw < 20 or ah < 20:
            return
        nw, nh = self._plot_need
        W, H = max(aw, nw), max(ah, nh)
        self.plot_host.itemconfigure(self._plot_win, width=W, height=H)
        self.plot_host.configure(scrollregion=(0, 0, W, H))
        for sb, need, kw in ((self._hscroll, W > aw, dict(row=1, column=0, sticky="ew")),
                             (self._vscroll, H > ah, dict(row=0, column=1, sticky="ns"))):
            if need:
                sb.grid(**kw)
            else:
                sb.grid_remove()
        if W <= aw:
            self.plot_host.xview_moveto(0)
        if H <= ah:
            self.plot_host.yview_moveto(0)

    def _plot_wheel(self, event, horizontal=False, delta=None):
        d = delta if delta is not None else getattr(event, "delta", 0)
        step = -1 if d > 0 else 1
        nw, nh = self._plot_need
        aw, ah = int(self.plot_host.winfo_width() or 0), int(self.plot_host.winfo_height() or 0)
        if horizontal or (nh <= ah and nw > aw):
            if nw > aw:
                self.plot_host.xview_scroll(step * 3, "units")
        elif nh > ah:
            self.plot_host.yview_scroll(step * 3, "units")

    def _remember_view(self, fig=None):
        fig = fig or self.fig
        return {ax: (ax.get_xlim(), ax.get_ylim()) for ax in fig.axes}

    def _fit_view(self, fig=None, canvas=None, toolbar=None, limits=None):
        """回到剛畫好時的範圍，並把圖調成剛好填滿可見區域。"""
        fig = fig or self.fig
        canvas = canvas or self.canvas
        toolbar = toolbar or self.toolbar
        limits = self._home_limits if limits is None else limits
        try:
            w = canvas.get_tk_widget()
            ww, hh = int(w.winfo_width()), int(w.winfo_height())
            if ww > 20 and hh > 20:
                fig.set_size_inches(ww / fig.dpi, hh / fig.dpi, forward=False)
        except Exception:
            pass
        for ax, (xl, yl) in (limits or {}).items():
            if ax in fig.axes:
                ax.set_xlim(xl)
                ax.set_ylim(yl)
        canvas.draw_idle()
        try:
            toolbar.update()
        except Exception:
            pass

    def _build_style_bar(self, parent):
        """圖表樣式列：格線、字體大小（改了立刻套用，不重新計算）。"""
        bar = tk.Frame(parent, bg=T.SUBTLE, highlightthickness=1, highlightbackground=T.BORDER)
        bar.pack(fill="x", pady=(8, 0))
        tk.Label(bar, text="圖表樣式", bg=T.SUBTLE, fg=T.MUTED, font=T.font(9, "bold")).pack(side="left", padx=(8, 10))
        self.style_vars = {"grid": tk.BooleanVar(value=bool(self.plot_style["grid"]))}
        tk.Checkbutton(bar, text="格線", variable=self.style_vars["grid"], bg=T.SUBTLE, fg=T.TEXT,
                       activebackground=T.SUBTLE, selectcolor=T.FIELD, font=T.font(9), bd=0,
                       highlightthickness=0).pack(side="left", padx=(0, 10))
        for key, label in ST.FIELDS:
            tk.Label(bar, text=label, bg=T.SUBTLE, fg=T.MUTED, font=T.font(9)).pack(side="left", padx=(6, 3))
            var = tk.StringVar(value=str(self.plot_style[key]))
            self.style_vars[key] = var
            ttk.Spinbox(bar, from_=5, to=40, width=3, textvariable=var,
                        command=self._schedule_restyle).pack(side="left", pady=4)
        ttk.Button(bar, text="恢復預設", style="Small.TButton", command=self._reset_style).pack(side="right", padx=6)
        bar2 = tk.Frame(parent, bg=T.SUBTLE, highlightthickness=1, highlightbackground=T.BORDER)
        bar2.pack(fill="x")
        tk.Label(bar2, text="多張圖", bg=T.SUBTLE, fg=T.MUTED, font=T.font(9, "bold")).pack(side="left", padx=(8, 6))
        self.style_vars["layout"] = tk.StringVar(value=self.plot_style.get("layout", ST.LAYOUTS[0]))
        ttk.Combobox(bar2, textvariable=self.style_vars["layout"], values=ST.LAYOUTS, state="readonly",
                     width=18, font=T.font(9)).pack(side="left", pady=3)
        for key, label in (("panel_w", "每張最小寬"), ("panel_h", "最小高")):
            tk.Label(bar2, text=label, bg=T.SUBTLE, fg=T.MUTED, font=T.font(9)).pack(side="left", padx=(10, 3))
            var = tk.StringVar(value=str(int(self.plot_style.get(key, ST.DEFAULT_STYLE[key]))))
            self.style_vars[key] = var
            ttk.Spinbox(bar2, from_=200, to=2000, increment=20, width=5, textvariable=var,
                        command=self._schedule_restyle).pack(side="left")
        tk.Label(bar2, text="px", bg=T.SUBTLE, fg=T.FAINT, font=T.font(8)).pack(side="left", padx=(2, 0))
        ttk.Button(bar2, text="✎ 標題與軸標籤", style="Small.TButton",
                   command=self.edit_labels).pack(side="right", padx=6, pady=2)
        for var in self.style_vars.values():
            var.trace_add("write", lambda *a: self._schedule_restyle())

    def _read_style(self):
        st = {"grid": bool(self.style_vars["grid"].get()), "layout": self.style_vars["layout"].get()}
        for key in ("panel_w", "panel_h"):
            try:
                st[key] = int(max(200, min(3000, float(self.style_vars[key].get()))))
            except (TypeError, ValueError):
                st[key] = self.plot_style.get(key, ST.DEFAULT_STYLE[key])
        for key, _ in ST.FIELDS:
            try:
                st[key] = max(4, min(60, float(self.style_vars[key].get())))
            except (TypeError, ValueError):
                st[key] = self.plot_style.get(key, ST.DEFAULT_STYLE[key])
        return st

    def _schedule_restyle(self):
        if self._restyle_job is not None:
            self.root.after_cancel(self._restyle_job)
        self._restyle_job = self.root.after(150, self._restyle)

    def _restyle(self):
        self._restyle_job = None
        if self._dead:
            return
        old = self.plot_style
        self.plot_style = self._read_style()
        self.gui_settings["style"] = self.plot_style
        ST.save_settings(self.gui_settings)
        if self.result is not None:
            if old.get("layout") != self.plot_style["layout"]:
                self._schedule_plot(delay=10)          # 排列方式改了要重新排版
                return
            ST.apply_style(self.fig, self.plot_style)
            self._plot_need = self._panel_need()
            self._layout_plot()
            self.canvas.draw_idle()

    def _reset_style(self):
        for key, var in self.style_vars.items():
            var.set(ST.DEFAULT_STYLE[key] if key == "grid" else str(ST.DEFAULT_STYLE[key]))

    def _build_status(self):
        bar = tk.Frame(self.root, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER)
        bar.pack(fill="x", side="bottom")
        self.status_dot = tk.Label(bar, text="●", bg=T.CARD, fg=T.FAINT, font=T.font(10))
        self.status_dot.pack(side="left", padx=(10, 4), pady=3)
        self.status_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.status_var, bg=T.CARD, fg=T.TEXT, font=T.font(9),
                 anchor="w").pack(side="left", fill="x", expand=True)
        from .. import __version__
        tk.Label(bar, text=f"v{__version__}", bg=T.CARD, fg=T.FAINT, font=T.font(8, "bold")).pack(side="right", padx=(4, 10))
        self.progress_lab = tk.Label(bar, text="", bg=T.CARD, fg=T.ACCENTS["1D"]["text"], font=T.font(9))
        self.progress_bar = ttk.Progressbar(bar, length=160, mode="determinate")
        tk.Label(bar, text="F5 重新整理 · Ctrl+S 存圖 · Ctrl+Z/Y 復原/重做 · Ctrl+滾輪 縮放", bg=T.CARD,
                 fg=T.FAINT, font=T.font(8)).pack(side="right", padx=6)

    # ================================================================== 主色
    def _apply_accent(self):
        a = T.setup_ttk(self.style, self.category)
        self.logo_mark.configure(fg=a["main"])
        self.prm_icon.configure(fg=a["main"])
        self.plot_icon.configure(bg=a["soft"], fg=a["main"])
        self.text.tag_configure("head", foreground=a["text"])

    # ================================================================== 資料庫
    def _scan_dbs(self, open_newest=False):
        root_dir = self.settings.get("data_root")
        if not root_dir or not os.path.isdir(root_dir):
            self.status(f"settings.toml 的 data_root 不存在（{root_dir}），請用「開啟檔案」", "warn")
            return
        gen = self._gen

        def work():
            try:
                dbs = sorted(find_dbs(root_dir), key=db_mtime, reverse=True)
            except Exception as e:
                dbs = []
                self._queue.put(("status", (f"掃描資料夾失敗：{e}", "error")))
            self._queue.put(("dblist", (dbs, open_newest and gen == self._gen and self.db is None)))
        threading.Thread(target=work, daemon=True).start()
        if self.db is None:
            self.status(f"正在搜尋 {root_dir} 底下的 .db …", "busy")

    def _set_db_list(self, dbs):
        names = {}
        for p in dbs:
            label = f"{os.path.basename(os.path.dirname(p))}  ›  {os.path.basename(p)}"
            names[label if label not in names else p] = p
        if self.db and self.db.path not in names.values():
            names[self.db.path] = self.db.path
        self._db_paths = names
        self.db_combo["values"] = list(names)

    def _browse_db(self):
        init = self.db.folder if self.db else (self.settings.get("data_root") or os.getcwd())
        path = filedialog.askopenfilename(title="選擇 qcodes 資料庫", initialdir=init,
                                          filetypes=[("qcodes database", "*.db"), ("all", "*.*")])
        if path:
            self.open_db(path)

    def open_db(self, path, cache=None):
        if not path:
            return
        try:
            db = QDB(path)
            runs = db.runs()
        except Exception as e:
            messagebox.showerror("無法開啟", f"{path}\n\n{e}")
            return
        self._gen += 1
        self.db, self.cache, self.all_runs = db, (cache if cache is not None else {}), runs
        self._mtime = db_mtime(db.path)
        self._load_per_run()
        self._apply_saved_params()
        label = next((k for k, v in self._db_paths.items() if v == db.path), None)
        if label is None:
            label = db.path
            self._db_paths[label] = db.path
            self.db_combo["values"] = list(self._db_paths)
        self.db_var.set(label)
        self.root.title(f"低溫量測分析 — {db.name}")
        self._fill_tree()
        self._preload()
        n2 = sum(r.is_2d for r in runs)
        self.status(f"已開啟 {db.name}：{len(runs)} 筆（1D {len(runs) - n2}、2D {n2}），背景載入中…", "busy")
        self._welcome()

    def _preload(self):
        """一次把所有 run 讀進記憶體（背景執行）。"""
        gen, db, ids = self._gen, self.db, [r.run_id for r in self.all_runs]

        def work():
            for k, rid in enumerate(ids):
                if gen != self._gen:
                    return
                if rid not in self.cache:
                    try:
                        self.cache[rid] = db.load(rid)
                    except Exception:
                        pass
                if k % 10 == 0 or k == len(ids) - 1:
                    self._queue.put(("loaded", (gen, k + 1, len(ids))))
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        if not self.db:
            self._scan_dbs()
            return
        old = {r.run_id: r.n_points for r in self.all_runs}
        try:
            runs = self.db.runs(refresh=True)
        except Exception as e:
            self.status(f"重新整理失敗：{e}", "error")
            return
        for r in runs:
            if old.get(r.run_id) != r.n_points:
                self.cache.pop(r.run_id, None)
        self.all_runs = runs
        self._mtime = db_mtime(self.db.path)
        new = [r.run_id for r in runs if r.run_id not in old]
        self._fill_tree()
        self._preload()
        self.status(f"已重新整理：{len(runs)} 筆" + (f"，新增 run {', '.join(map(str, new))}" if new else ""),
                    "ok")
        if self.autoplot_var.get() and self.tree.selection():
            self._schedule_plot()

    def _auto_update(self):
        if self._dead:
            return
        try:
            pending = self._restart_pending()
            if pending != getattr(self, "_last_pending", None):
                self._last_pending = pending
                self._set_version_badge(self._update_info["latest"] if self._update_info else None)
                if pending:
                    self.status(f"資料夾裡已經是 v{pending}，重新開啟軟體就會套用（點左上角版本標籤）", "warn")
        except Exception:
            pass
        try:
            if self.auto_var.get() and self.db and os.path.exists(self.db.path):
                m = db_mtime(self.db.path)
                if self._mtime is not None and m != self._mtime:
                    self.refresh()
        finally:
            self.root.after(10000, self._auto_update)

    def _poll_queue(self):
        if self._dead:
            return
        try:
            while True:
                kind, data = self._queue.get_nowait()
                if kind == "dblist":
                    dbs, open_newest = data
                    self._set_db_list(dbs)
                    if open_newest and dbs and self.db is None:
                        self.open_db(dbs[0])
                    elif not dbs:
                        self.status("data_root 底下沒有找到 .db，請用「開啟檔案」", "warn")
                elif kind == "loaded":
                    gen, k, n = data
                    if gen == self._gen:
                        done = k >= n
                        self.load_chip.set(f"{'✓ ' if done else ''}已載入 {k}/{n}",
                                           fg=T.SUCCESS if done else T.WARNING)
                        if done and self.status_var.get().endswith("背景載入中…"):
                            self.status(self.status_var.get().replace("背景載入中…", "全部載入完成"), "ok")
                elif kind == "status":
                    self.status(*data)
                elif kind == "progress":
                    self._on_progress(*data)
                elif kind == "computed":
                    self._on_computed(*data)
                elif kind == "compute_error":
                    self._on_compute_error(*data)
                elif kind == "update":
                    self._on_update_info(*data)
                elif kind == "updated":
                    messagebox.showinfo("更新完成", f"已更新到 v{data}，接下來會重新開啟軟體。", parent=self.root)
                    self.restart()
                elif kind == "update_error":
                    self.status(f"更新失敗：{data}", "error")
                    messagebox.showerror("更新失敗", f"{data}\n\n可以在 ⚙ 設定 → 更新 裡還原上一版。", parent=self.root)
        except queue.Empty:
            pass
        self.root.after(60 if self._compute_job else 200, self._poll_queue)

    # ================================================================== run 清單
    def _visible_runs(self):
        want_2d = self.category == "2D"
        runs = [r for r in self.all_runs if r.is_2d == want_2d]
        for tok in self.filter_var.get().split():
            m = re.fullmatch(r"(\d+)-(\d+)", tok)
            if m:
                a, b = sorted((int(m.group(1)), int(m.group(2))))
                runs = [r for r in runs if a <= r.run_id <= b]
            elif tok.isdigit():
                runs = [r for r in runs if r.run_id == int(tok) or tok in r.exp_name]
            else:
                t = tok.lower()
                runs = [r for r in runs if t in r.exp_name.lower() or t in r.kind.lower()
                        or t in r.mode_short.lower()]
        col, desc = self._sort
        keyf = {"run": lambda r: r.run_id, "T": lambda r: (r.T_K is None, r.T_K or 0, r.run_id),
                "pts": lambda r: r.n_points, "mode": lambda r: r.mode_short, "time": lambda r: r.start,
                "name": lambda r: r.exp_name}[col]
        return sorted(runs, key=keyf, reverse=desc)

    def _fill_tree(self):
        keep = set(self.tree.selection())
        self._suspend = True
        self.tree.delete(*self.tree.get_children())
        for k, r in enumerate(self._visible_runs()):
            T_txt = f"{r.T_K:g}" if r.T_K is not None else ""
            mtag = "m_2D" if r.is_2d else f"m_{r.mode}"
            col = self.run_colors.get(r.run_id)
            tags = (mtag,) + (("odd",) if k % 2 else ())
            if col:
                ctag = "c_" + col[1:]
                self.tree.tag_configure(ctag, foreground=col)
                tags = tags + (ctag,)
            self.tree.insert("", "end", iid=str(r.run_id), tags=tags,
                             values=("●" if col else "", r.run_id, r.mode_short, T_txt, r.n_points,
                                     r.start[5:16], r.exp_name))
        again = [i for i in keep if self.tree.exists(i)]
        if again:
            self.tree.selection_set(again)
        self._suspend = False
        self._update_count()

    def _update_count(self):
        n, s = len(self.tree.get_children()), len(self.tree.selection())
        a = T.ACCENTS[self.category]
        self.count_chip.set(f"{s} / {n} 已選", fg=a["text"] if s else T.MUTED, bg=a["soft"] if s else T.CHIP)

    def _sort_by(self, col):
        c, desc = self._sort
        self._sort = (col, not desc if c == col else False)
        self._fill_tree()

    def _select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def _clear_sel(self):
        self.tree.selection_remove(self.tree.selection())

    def _selected_infos(self):
        if not self.db:
            return []
        return [self.db.info(int(i)) for i in self.tree.selection()]

    def _on_select(self, event=None):
        if self._suspend:
            return
        self._update_count()
        self._update_feature_compat()
        self._refresh_item_table()
        self._schedule_plot()

    def _update_feature_compat(self):
        modes = {i.mode for i in self._selected_infos()}
        self.features.set_enabled({a.name: all(a.supports(m) for m in modes)
                                   for a in self.analyses[self.category]})

    def _on_category(self, cat):
        if cat == self.category:
            return
        self._filters[self.category] = self.filter_var.get()
        self.category = cat
        self._apply_accent()
        self._suspend = True
        self.tree.selection_remove(self.tree.selection())
        self._suspend = False
        if self.filter_var.get() != self._filters[cat]:
            self.filter_var.set(self._filters[cat])
        else:
            self._fill_tree()
        self._load_feature_list()
        self._welcome()

    # ================================================================== 功能與參數
    def _load_feature_list(self):
        lst = self.analyses[self.category]
        self.features.set_items(lst, self.category, self.current[self.category].name)
        self._feature_scroll.to_top()
        self._build_params()
        self._update_feature_compat()

    def _on_feature(self, name):
        self.current[self.category] = next(a for a in self.analyses[self.category] if a.name == name)
        self._build_params()
        self._schedule_plot()

    def _build_params(self):
        for w in self.param_frame.winfo_children():
            w.destroy()
        a = self.current[self.category]
        self.prm_icon.configure(text=a.icon)
        self.prm_title.configure(text=a.name)
        extra = [] if a.multi else ["一次分析一筆（多選時用第一筆）"]
        if a.modes is not None:
            extra.append(f"支援：{a.modes_text}")
        self.help_lab.configure(text=a.help + ("　" + "；".join(extra) if extra else ""))
        store = a.store_key
        row = 0
        self._text_boxes = {}
        for prm in a.global_params:
            var = self._param_var(store, prm)
            if prm.kind == "bool":
                ttk.Checkbutton(self.param_frame, text=prm.label, variable=var).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=3)
            elif prm.kind == "text" and prm.lines > 1:
                tk.Label(self.param_frame, text=prm.label, bg=T.CARD, fg=T.MUTED, font=T.font(9),
                         anchor="w").grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 1))
                row += 1
                box = tk.Text(self.param_frame, height=prm.lines, width=48, wrap="none", undo=True,
                              font=("Consolas", 9), relief="solid", bd=1, highlightthickness=0)
                box.insert("1.0", var.get())
                box.bind("<KeyRelease>", lambda e, b=box, v=var: self._text_to_var(b, v))
                box.bind("<FocusOut>", lambda e, b=box, v=var: self._text_to_var(b, v))
                box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(0, 3))
                self._text_boxes[(store, prm.key)] = box
            else:
                tk.Label(self.param_frame, text=prm.label, bg=T.CARD, fg=T.MUTED, font=T.font(9),
                         anchor="w", justify="left", wraplength=T.px(190)).grid(row=row, column=0, sticky="w", pady=3)
                if prm.kind == "choice":
                    w = ttk.Combobox(self.param_frame, textvariable=var, values=prm.choices,
                                     state="readonly", width=22)
                else:
                    w = ttk.Entry(self.param_frame, textvariable=var, width=24)
                w.grid(row=row, column=1, sticky="ew", padx=(8, 4), pady=3)
            row += 1
            if prm.tip:
                tk.Label(self.param_frame, text="↳ " + prm.tip, bg=T.CARD, fg=T.FAINT, font=T.font(8),
                         wraplength=T.px(400), justify="left", anchor="w").grid(row=row, column=0, columnspan=2,
                                                                          sticky="w", pady=(0, 3))
                row += 1
        if not a.params:
            tk.Label(self.param_frame, text="這個功能沒有參數", bg=T.CARD, fg=T.FAINT,
                     font=T.font(9)).grid(row=0, column=0, sticky="w")
        self.item_tree = None
        self._hl_run = None
        self._hl_orig = {}
        if a.per_item_params:
            self._build_item_table(a, row)
        self.param_frame.columnconfigure(1, weight=1)
        self.param_scroll.to_top()

    # ---------------------------------------------------------------- 參數值（每個資料庫各自記住）
    def _param_var(self, store, prm):
        """取得（或建立）參數的 tk 變數；初始值 = 這個資料庫記住的值，沒有就用預設。"""
        vars_ = self.param_vars.setdefault(store, {})
        self._param_defs[(store, prm.key)] = prm
        var = vars_.get(prm.key)
        if var is None:
            saved = self.saved_params.get(store, {}).get(prm.key)
            if prm.kind == "bool":
                var = tk.BooleanVar(value=prm.default if saved is None else bool(saved))
            else:
                var = tk.StringVar(value=str(prm.default) if saved is None else str(saved))
            var.trace_add("write", lambda *x, st=store, q=prm, v=var: self._on_param_change(st, q, v))
            vars_[prm.key] = var
        return var

    @staticmethod
    def _text_to_var(box, var):
        text = box.get("1.0", "end-1c")
        if text != var.get():
            var.set(text)

    def _on_param_change(self, store, prm, var):
        try:
            val = var.get()
        except tk.TclError:
            return
        box = self._text_boxes.get((store, prm.key))
        if box is not None:
            try:
                if box.get("1.0", "end-1c") != val:
                    box.delete("1.0", "end")
                    box.insert("1.0", val)
            except tk.TclError:
                pass
        if self._loading_params:
            return
        saved = self.saved_params.setdefault(store, {})
        default = prm.default if prm.kind == "bool" else str(prm.default)
        if val == default:
            saved.pop(prm.key, None)
        else:
            saved[prm.key] = val
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(1000, self._save_per_run)
        self._schedule_plot(delay=700)

    def _apply_saved_params(self):
        """換資料庫後，把已建立的參數變數換成新資料庫記住的值。"""
        self._loading_params = True
        try:
            for store, vars_ in self.param_vars.items():
                saved = self.saved_params.get(store, {})
                for key, var in vars_.items():
                    prm = self._param_defs.get((store, key))
                    if prm is None:
                        continue
                    v = saved.get(key, prm.default if prm.kind == "bool" else str(prm.default))
                    var.set(bool(v) if prm.kind == "bool" else str(v))
        finally:
            self._loading_params = False

    # ---------------------------------------------------------------- 逐筆設定
    def _per_run_path(self):
        return os.path.join(self.db.folder, "01 Figure", "gui", "per_run_settings.json") if self.db else None

    def _load_per_run(self):
        self.per_run = {}
        self.run_colors = {}
        self.saved_params = {}
        self.labels = {}
        path = self._per_run_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f).get(self.db.name, {})
            self.run_colors = {int(r): v for r, v in data.pop("__colors__", {}).items()}
            self.saved_params = {st: dict(v) for st, v in data.pop("__params__", {}).items()}
            self.labels = data.pop("__labels__", {})
            self.per_run = {an: {k: {int(r): v for r, v in vals.items()} for k, vals in keys.items()}
                            for an, keys in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_per_run(self):
        self._save_job = None
        path = self._per_run_path()
        if not path:
            return
        try:
            ensure_dir(os.path.dirname(path))
            try:
                with open(path, encoding="utf-8") as f:
                    allc = json.load(f)
            except (OSError, ValueError):
                allc = {}
            clean = {an: {k: {str(r): v for r, v in vals.items()} for k, vals in keys.items() if vals}
                     for an, keys in self.per_run.items()}
            allc[self.db.name] = {an: keys for an, keys in clean.items() if keys}
            if self.run_colors:
                allc[self.db.name]["__colors__"] = {str(r): c for r, c in self.run_colors.items()}
            params = {st: v for st, v in self.saved_params.items() if v}
            if params:
                allc[self.db.name]["__params__"] = params
            labels = {an: v for an, v in self.labels.items()
                      if v.get("suptitle") or v.get("axes") or v.get("legend") or any((v.get("hidden") or {}).values())}
            if labels:
                allc[self.db.name]["__labels__"] = labels
            with open(path, "w", encoding="utf-8") as f:
                json.dump(allc, f, ensure_ascii=False, indent=1)
        except OSError as e:
            self.status(f"無法儲存逐筆設定：{e}", "warn")

    def set_item_value(self, key, run_id, value, record=True):
        """逐筆設定：寫入一個值（空字串 = 恢復預設）。圖上拖曳也會呼叫這裡。可以 Ctrl+Z 復原。"""
        a = self.current[self.category]
        self._set_raw(a.store_key, key, run_id, value, record)
        self._save_per_run()
        self._refresh_item_table()

    def _set_raw(self, store_key, key, run_id, value, record=True):
        store = self.per_run.setdefault(store_key, {}).setdefault(key, {})
        old = store.get(int(run_id), "")
        text = "" if value is None else str(value).strip()
        if text == old:
            return
        if text == "":
            store.pop(int(run_id), None)
        else:
            store[int(run_id)] = text
        if record and not self._undoing:
            change = (store_key, key, int(run_id), old, text)
            if getattr(self, "_history_group", None) is not None:
                self._history_group.append(change)
            else:
                self._push_history([change])

    # ---------------------------------------------------------------- 復原 / 重做（逐筆設定）
    def _push_history(self, changes):
        if not changes:
            return
        self._history.append(changes)
        del self._history[:-200]
        self._redo.clear()
        self._update_undo_buttons()

    def _begin_group(self):
        self._history_group = []

    def _end_group(self):
        group, self._history_group = self._history_group, None
        self._push_history(group)

    @staticmethod
    def _describe(changes):
        st, key, rid, old, new = changes[0]
        more = f"（共 {len(changes)} 項）" if len(changes) > 1 else ""
        return f"run {rid} {key}：{old or '（預設）'} → {new or '（預設）'}{more}"

    def _focus_is_text(self):
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, ttk.Entry, tk.Text, ttk.Combobox, ttk.Spinbox))

    def undo(self, event=None):
        if event is not None and self._focus_is_text():
            return None
        if not self._history:
            self.status("沒有可以復原的動作", "warn")
            return "break"
        changes = self._history.pop()
        self._apply_changes(changes, undo=True)
        self._redo.append(changes)
        self.status("已復原：" + self._describe(changes) + "　（Ctrl+Y 重做）", "ok")
        return "break"

    def redo(self, event=None):
        if event is not None and self._focus_is_text():
            return None
        if not self._redo:
            self.status("沒有可以重做的動作", "warn")
            return "break"
        changes = self._redo.pop()
        self._apply_changes(changes, undo=False)
        self._history.append(changes)
        self.status("已重做：" + self._describe(changes), "ok")
        return "break"

    def _apply_changes(self, changes, undo):
        self._undoing = True
        try:
            for st, key, rid, old, new in (reversed(changes) if undo else changes):
                self._set_raw(st, key, rid, old if undo else new, record=False)
        finally:
            self._undoing = False
        self._save_per_run()
        self._refresh_item_table()
        self._update_undo_buttons()
        self._schedule_plot(delay=30)

    def _update_undo_buttons(self):
        for name, stack in (("undo_btn", self._history), ("redo_btn", self._redo)):
            b = getattr(self, name, None)
            if b is None:
                continue
            try:
                b.configure(state="normal" if stack else "disabled")
            except tk.TclError:
                pass

    def _build_item_table(self, a, row):
        acc = T.ACCENTS[self.category]
        head = tk.Frame(self.param_frame, bg=T.CARD)
        head.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 2))
        tk.Label(head, text="▤ 逐筆設定", bg=T.CARD, fg=acc["text"], font=T.font(10, "bold")).pack(side="left")
        ttk.Button(head, text="清除選取 run 的設定", style="Small.TButton",
                   command=self._clear_item_values).pack(side="right")
        self.redo_btn = ttk.Button(head, text="↷ 重做", style="Small.TButton", command=self.redo)
        self.redo_btn.pack(side="right", padx=(0, 4))
        self.undo_btn = ttk.Button(head, text="↶ 復原", style="Small.TButton", command=self.undo)
        self.undo_btn.pack(side="right", padx=(0, 4))
        self._update_undo_buttons()
        tk.Label(self.param_frame, text="雙擊格子修改，按 Enter 確定；清空 = 用預設（括號內）。"
                 "有個別設定的列會變色，也可以在圖上直接拖曳標記。",
                 bg=T.CARD, fg=T.FAINT, font=T.font(8), wraplength=410, justify="left",
                 anchor="w").grid(row=row + 1, column=0, columnspan=2, sticky="w")
        cols = ["run", "T"] + [q.key for q in a.per_item_params]
        tr = ttk.Treeview(self.param_frame, columns=cols, show="headings", height=6, selectmode="browse")
        tr.heading("run", text="run")
        tr.heading("T", text="T (K)")
        tr.column("run", width=T.px(46), anchor="e", stretch=False)
        tr.column("T", width=T.px(46), anchor="e", stretch=False)
        for q in a.per_item_params:
            tr.heading(q.key, text=q.label)
            tr.column(q.key, width=T.px(96 if q.kind != "text" else 130), anchor="e" if q.kind != "text" else "w")
        tr.tag_configure("manual", foreground=acc["text"], background=acc["soft"])
        self.item_hint = tk.Label(self.param_frame, text="", bg=acc["main"], fg=acc.get("on", "white"),
                                  font=T.font(9, "bold"), anchor="w", padx=8, pady=3)
        tr.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._item_hint_row = row + 4
        tr.bind("<Double-1>", self._edit_item_cell)
        tr.bind("<<TreeviewSelect>>", lambda e: self._on_item_row_select())
        tips = [f"{q.label}：{q.tip}" for q in a.per_item_params if q.tip]
        if tips:
            tk.Label(self.param_frame, text="↳ " + "　".join(tips), bg=T.CARD, fg=T.FAINT, font=T.font(8),
                     wraplength=T.px(410), justify="left", anchor="w").grid(row=row + 3, column=0, columnspan=2, sticky="w")
        self.item_tree = tr
        self._item_cols = cols
        self._refresh_item_table()

    def _refresh_item_table(self):
        tr = getattr(self, "item_tree", None)
        if tr is None:
            return
        a = self.current[self.category]
        tr.delete(*tr.get_children())
        try:
            items = self._items()
        except Exception:
            items = []
        if not a.multi:
            items = items[:1]
        defaults = {}
        if a.item_defaults and items:
            try:
                defaults = a.item_defaults(items, self._params(per_item=False))
            except Exception:
                defaults = {}
        store = self.per_run.get(a.store_key, {})
        for it in items:
            vals, manual = [it.run_id, f"{it.T:g}" if it.T is not None else ""], False
            for q in a.per_item_params:
                v = store.get(q.key, {}).get(it.run_id)
                if v is not None:
                    vals.append(v)
                    manual = True
                else:
                    dv = defaults.get(it.run_id, {}).get(q.key, "")
                    vals.append(f"({dv:g})" if isinstance(dv, (int, float)) else (f"({dv})" if dv else ""))
            tr.insert("", "end", iid=str(it.run_id), values=vals, tags=("manual",) if manual else ())
        tr.configure(height=max(3, min(len(items), 8)))
        hl = getattr(self, "_hl_run", None)
        if hl is not None and tr.exists(str(hl)):
            tr.selection_set(str(hl))
            tr.see(str(hl))

    # ---------------------------------------------------------------- 正在設定哪一筆
    def _on_item_row_select(self):
        tr = getattr(self, "item_tree", None)
        sel = tr.selection() if tr is not None else ()
        if not sel:
            self._hl_run = None
            try:
                self.item_hint.grid_remove()
            except (tk.TclError, AttributeError):
                pass
            self._clear_item_highlight()
            return
        rid = int(sel[0])
        it = next((x for x in self.last_items if x.run_id == rid), None)
        Tt = f" · {fmt_T(it.T)}" if it is not None and it.T is not None else ""
        self._hl_run = rid
        self.item_hint.configure(text=f"✎ 正在設定：run {rid}{Tt}　（圖上用粗線標出，其他資料變淡；Esc 取消）")
        self.item_hint.grid(row=self._item_hint_row, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        tr.bind("<Escape>", lambda e: tr.selection_remove(tr.selection()))
        self._highlight_item(rid)
        self.status(f"正在設定 run {rid}{Tt}：雙擊表格修改，或在圖上拖曳這筆的標記", "info")

    def _clear_item_highlight(self, redraw=True):
        orig = getattr(self, "_hl_orig", None) or {}
        for art, (lw, alpha, z, ms) in orig.items():
            try:
                art.set_linewidth(lw)
                art.set_alpha(alpha)
                art.set_zorder(z)
                if ms is not None:
                    art.set_markersize(ms)
            except Exception:
                pass
        self._hl_orig = {}
        try:
            self.text.tag_remove("hl", "1.0", "end")
        except (tk.TclError, AttributeError):
            pass
        if redraw and orig:
            self.canvas.draw_idle()

    def _highlight_item(self, rid):
        self._clear_item_highlight(redraw=False)
        by_label = {it.label: it.run_id for it in self.last_items}
        orig = {}
        for ax in self.fig.axes:
            for ln in ax.lines:
                spec = getattr(ln, "lta_drag", None)
                owner = by_label.get(ln.get_label())
                if owner is None and spec is not None:
                    owner = self._drag_spec(ln).run_id
                if owner is None:
                    continue
                orig[ln] = (ln.get_linewidth(), ln.get_alpha(), ln.get_zorder(),
                            ln.get_markersize() if spec is not None else None)
                if owner == rid:
                    ln.set_linewidth(ln.get_linewidth() * 2.2)
                    ln.set_zorder(ln.get_zorder() + 20)
                    ln.set_alpha(1.0)
                    if spec is not None:
                        ln.set_markersize(ln.get_markersize() * 1.5)
                else:
                    ln.set_alpha(0.22)
        self._hl_orig = orig
        try:
            acc = T.ACCENTS[self.category]
            self.text.tag_configure("hl", background=acc["soft"], foreground=acc["text"])
            pat = re.compile(rf"(^|\s|run\s+){rid}(\s|$|\))")
            for k, line in enumerate(self.text.get("1.0", "end").splitlines(), 1):
                if pat.search(line[:40]):
                    self.text.tag_add("hl", f"{k}.0", f"{k}.end")
        except (tk.TclError, AttributeError):
            pass
        self.canvas.draw_idle()

    def _edit_item_cell(self, event):
        tr = self.item_tree
        row, col = tr.identify_row(event.y), tr.identify_column(event.x)
        if not row or not col:
            return
        idx = int(col.replace("#", "")) - 1
        if idx < 2:
            return
        key = self._item_cols[idx]
        bbox = tr.bbox(row, col)
        if not bbox:
            return
        x, y, w, h = bbox
        a = self.current[self.category]
        cur = self.per_run.get(a.store_key, {}).get(key, {}).get(int(row), "")
        ent = ttk.Entry(tr)
        ent.insert(0, cur)
        ent.select_range(0, "end")
        ent.place(x=x, y=y, width=max(w, 80), height=h)
        ent.focus_set()
        done = {"v": False}

        def commit(_=None):
            if done["v"]:
                return
            done["v"] = True
            val = ent.get()
            ent.destroy()
            prm = next(q for q in a.per_item_params if q.key == key)
            if val.strip():
                try:
                    prm.parse(val)
                except ValueError as e:
                    self.status(str(e), "error")
                    return
            self.set_item_value(key, int(row), val)
            self._schedule_plot(delay=100)

        def cancel(_=None):
            done["v"] = True
            ent.destroy()
        ent.bind("<Return>", commit)
        ent.bind("<KP_Enter>", commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>", cancel)

    def _clear_item_values(self):
        a = self.current[self.category]
        ids = {int(i) for i in self.tree.selection()}
        own = {q.key for q in a.per_item_params}
        self._begin_group()
        for key, vals in list(self.per_run.get(a.store_key, {}).items()):
            if key not in own:
                continue
            for rid in list(vals):
                if rid in ids:
                    self._set_raw(a.store_key, key, rid, "")
        self._end_group()
        self._save_per_run()
        self._refresh_item_table()
        self._schedule_plot(delay=100)

    # ---------------------------------------------------------------- 圖上拖曳
    def _collect_draggables(self):
        self._draggables = [ln for ax in self.fig.axes for ln in ax.lines if hasattr(ln, "lta_drag")]

    def _near_draggable(self, event):
        if event.inaxes is None or not self._draggables:
            return None
        best = None
        for ln in self._draggables:
            if ln.axes is not event.inaxes:
                continue
            xd, yd = ln.get_xdata(), ln.get_ydata()
            if ln.get_transform() is ln.axes.transData:          # 標記點
                px, py = ln.axes.transData.transform((float(xd[0]), float(yd[0])))
                d = ((px - event.x) ** 2 + (py - event.y) ** 2) ** 0.5
            else:                                                # axvline
                px = ln.axes.transData.transform((float(xd[0]), 0))[0]
                d = abs(px - event.x)
            if d <= 9 and (best is None or d < best[0]):
                best = (d, ln)
        return best[1] if best else None

    @staticmethod
    def _drag_spec(ln):
        """把舊寫法 (key, run_id) 統一成 Drag。"""
        tag = ln.lta_drag
        if isinstance(tag, Drag):
            return tag
        key, rid = tag
        return Drag(key, rid)

    def _drag_press(self, event):
        if str(getattr(self.toolbar, "mode", "")):
            return
        if event.button == 1 and self._legend_press(event):
            return
        if event.button == 3:
            ln = self._near_draggable(event)
            if ln is not None and self._drag_spec(ln).menu:
                self._drag_menu(ln, event)
            return
        if event.button != 1:
            return
        ln = self._near_draggable(event)
        if ln is None:
            return
        spec = self._drag_spec(ln)
        lines = [l for l in self._draggables if l.axes is ln.axes and self._drag_spec(l).gid == spec.gid]
        self._drag = {"spec": spec, "v": None, "lines": lines, "bg": None}
        # blitting：先畫一張沒有被拖曳物件的背景，拖曳時只重畫被拖的點
        self.hover.paused = True
        self.hover._hide_all()
        try:
            for l in lines:
                l.set_animated(True)
            self.canvas.draw()
            self._drag["bg"] = self.canvas.copy_from_bbox(self.fig.bbox)
            self._drag_blit()
        except Exception:
            self._drag["bg"] = None

    def _drag_blit(self):
        d = self._drag
        if d is None or d["bg"] is None:
            self.canvas.draw_idle()
            return
        try:
            self.canvas.restore_region(d["bg"])
            for l in d["lines"]:
                l.axes.draw_artist(l)
            self.canvas.blit(self.fig.bbox)
        except Exception:
            d["bg"] = None
            self.canvas.draw_idle()

    def _end_drag_visuals(self, drag):
        for l in drag["lines"]:
            try:
                l.set_animated(False)
            except Exception:
                pass
        self.hover.paused = False

    def _drag_motion(self, event):
        if self._legdrag is not None:
            self._legend_motion(event)
            return
        if self._drag is None:
            near = self._near_draggable(event) if event.inaxes is not None else None
            cursor = ""
            if near is not None:
                spec = self._drag_spec(near)
                cursor = "sb_v_double_arrow" if spec.axis == "y" else "sb_h_double_arrow"
                if spec.tip:
                    self.status(spec.tip, "info")
            try:
                self.canvas.get_tk_widget().configure(cursor=cursor)
            except Exception:
                pass
            return
        spec = self._drag["spec"]
        v = event.ydata if spec.axis == "y" else event.xdata
        if event.inaxes is None or v is None:
            return
        v = float(v)
        self._drag["v"] = v
        for ln in self._drag["lines"]:
            if spec.axis == "y":
                ln.set_ydata([v] * len(ln.get_ydata()))
            else:
                ln.set_xdata([v] * len(ln.get_xdata()))
        self._drag_blit()

    def _current_item_text(self, key, rid):
        a = self.current[self.category]
        return self.per_run.get(a.store_key, {}).get(key, {}).get(int(rid), "")

    def _drag_release(self, event):
        if self._legdrag is not None:
            self._legend_release(event)
            return
        if self._drag is None:
            return
        drag, self._drag = self._drag, None
        self._end_drag_visuals(drag)
        if drag["v"] is None:
            self.canvas.draw_idle()
            return
        spec = drag["spec"]
        try:
            new = (spec.value(drag["v"], self._current_item_text(spec.key, spec.run_id))
                   if spec.value else f"{drag['v']:.4g}")
        except Exception as e:
            self.status(f"無法設定：{e}", "error")
            self._schedule_plot(delay=50)
            return
        self.set_item_value(spec.key, spec.run_id, new)
        self.status(f"run {spec.run_id}：{new if spec.value else f'{spec.key} = {new}'}", "ok")
        self._schedule_plot(delay=50)

    def _drag_menu(self, ln, event):
        spec = self._drag_spec(ln)
        menu = tk.Menu(self.root, tearoff=0)

        def run(fn):
            try:
                new = fn(self._current_item_text(spec.key, spec.run_id))
            except Exception as e:
                self.status(f"無法設定：{e}", "error")
                return
            self.set_item_value(spec.key, spec.run_id, new)
            self._schedule_plot(delay=50)
        for label, fn in spec.menu:
            menu.add_command(label=label, command=lambda f=fn: run(f))
        ge = getattr(event, "guiEvent", None)
        try:
            x, y = (ge.x_root, ge.y_root) if ge is not None and hasattr(ge, "x_root") else self.root.winfo_pointerxy()
        except Exception:
            x, y = self.root.winfo_rootx() + 200, self.root.winfo_rooty() + 200
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ---------------------------------------------------------------- 同溫度留一筆
    DEDUP_RULES = [("保留 run 號最小的（最早量的）", "first"), ("保留 run 號最大的（最新的）", "last"),
                   ("保留點數最多的", "most")]

    def _dedup_menu(self, widget):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="選取的資料裡，同一個溫度、同一組接點只保留一筆：", state="disabled")
        last = self.gui_settings.get("dedup_rule", "first")
        for label, rule in self.DEDUP_RULES:
            m.add_command(label=("✓ " if rule == last else "　") + label, command=lambda r=rule: self.dedup_selection(r))
        try:
            m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def dedup_selection(self, rule="first"):
        """同一溫度（且同接點、同量測種類、同掃描方向）選到多筆時，只留一筆。"""
        infos = self._selected_infos()
        groups = {}
        for r in infos:
            if r.T_K is None:
                continue
            key = (round(r.T_K, 4), r.contacts, r.kind, r.mode, r.sweep, r.is_2d)
            groups.setdefault(key, []).append(r)
        drop = []
        for key, rs in groups.items():
            if len(rs) < 2:
                continue
            if rule == "last":
                keep = max(rs, key=lambda r: r.run_id)
            elif rule == "most":
                keep = max(rs, key=lambda r: (r.n_points, -r.run_id))
            else:
                keep = min(rs, key=lambda r: r.run_id)
            drop += [r for r in rs if r is not keep]
        self.gui_settings["dedup_rule"] = rule
        ST.save_settings(self.gui_settings)
        if not drop:
            self.status("選取的資料裡沒有同溫度重複的", "info")
            return []
        self.tree.selection_remove([str(r.run_id) for r in drop])
        self._on_select()
        msg = "已取消選取：" + "、".join(f"run {r.run_id}（{fmt_T(r.T_K)}）" for r in sorted(drop, key=lambda r: r.run_id))
        self.status(msg, "ok")
        return [r.run_id for r in drop]

    def _tree_motion(self, event):
        """滑鼠移到「色」欄的圓點：說明這筆有自訂顏色、怎麼清除。"""
        try:
            col, row = self.tree.identify_column(event.x), self.tree.identify_row(event.y)
        except Exception:
            return
        key = (col, row)
        if key == getattr(self, "_tree_tip", None):
            return
        self._tree_tip = key
        if col == "#1" and row and int(row) in self.run_colors:
            self.status(f"run {row} 有自訂顏色 {self.run_colors[int(row)]}（右鍵 → 清除顏色 可恢復自動）", "info")

    # ---------------------------------------------------------------- 數據顏色
    def _tree_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set([row])
            self._on_select()
        self._color_menu(event.x_root, event.y_root)

    def _color_menu_at(self, widget):
        try:
            self._color_menu(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        except Exception:
            self._color_menu(0, 0)

    def _color_menu(self, x, y):
        ids = [int(i) for i in self.tree.selection()]
        m = tk.Menu(self.root, tearoff=0)
        n = len(ids)
        m.add_command(label=f"為選取的 {n} 筆設定顏色", state="disabled")
        m.add_separator()
        st = "normal" if ids else "disabled"
        m.add_command(label="用色盤選擇…", command=lambda: self._pick_color(ids), state=st)
        m.add_command(label="輸入 HEX 或 RGB…", command=lambda: self._type_color(ids), state=st)
        m.add_command(label="依溫度套用漸層（兩個顏色）…", command=lambda: self._gradient_colors(ids),
                      state="normal" if n >= 2 else "disabled")
        m.add_separator()
        m.add_command(label="清除顏色（恢復自動）", command=lambda: self.set_run_colors({r: None for r in ids}),
                      state=st)
        try:
            m.tk_popup(x, y)
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def set_run_colors(self, mapping):
        """mapping: {run_id: '#rrggbb' 或 None（清除）}"""
        for rid, col in mapping.items():
            if col:
                self.run_colors[int(rid)] = parse_color(col)
            else:
                self.run_colors.pop(int(rid), None)
        self._save_per_run()
        self._fill_tree()
        self._schedule_plot(delay=50)
        self.status(f"已更新 {len(mapping)} 筆的顏色", "ok")

    def _current_color(self, ids):
        return self.run_colors.get(ids[0]) if ids else None

    def _pick_color(self, ids):
        from tkinter import colorchooser
        res = colorchooser.askcolor(color=self._current_color(ids) or T.PLOT_CYCLE[0],
                                    title="選擇數據顏色", parent=self.root)
        if res and res[1]:
            self.set_run_colors({r: res[1] for r in ids})

    def _type_color(self, ids):
        from tkinter import simpledialog
        cur = self._current_color(ids)
        txt = simpledialog.askstring("數據顏色", "輸入 HEX（例如 #e4572e）或 RGB（例如 228, 87, 46）：",
                                     initialvalue=f"{cur}   ({rgb_text(cur)})" if cur else "",
                                     parent=self.root)
        if not txt:
            return
        try:
            col = parse_color(txt.split("(")[0])
        except ValueError as e:
            messagebox.showerror("數據顏色", str(e), parent=self.root)
            return
        self.set_run_colors({r: col for r in ids})

    def _gradient_colors(self, ids):
        from tkinter import simpledialog
        txt = simpledialog.askstring("漸層顏色", "輸入兩個顏色（最低溫 → 最高溫），用 ; 分開\n"
                                     "例如：#2563eb ; #e4572e　或　37,99,235 ; 228,87,46",
                                     initialvalue="#2563eb ; #e4572e", parent=self.root)
        if not txt:
            return
        try:
            c1, c2 = (parse_color(x) for x in txt.split(";")[:2])
        except ValueError as e:
            messagebox.showerror("漸層顏色", str(e), parent=self.root)
            return
        infos = sorted((self.db.info(r) for r in ids), key=lambda i: (i.T_K is None, i.T_K or 0, i.run_id))
        n = len(infos)
        self.set_run_colors({inf.run_id: mix(c1, c2, k / max(n - 1, 1)) for k, inf in enumerate(infos)})

    # ---------------------------------------------------------------- 工具選單（批次分析等）
    def _tools_menu(self, widget):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="執行批次分析設定檔…", command=self._tool_run_job)
        m.add_command(label="執行全部批次分析", command=lambda: self.spawn_console("run-all", "--pause"))
        m.add_separator()
        m.add_command(label="更新資料總表（catalog.xlsx）",
                      command=lambda: self.spawn_console("catalog", "--open", "--pause"))
        m.add_command(label="為目前的資料庫建立批次設定檔",
                      command=lambda: self.spawn_console("new", self.db.path) if self.db else None,
                      state="normal" if self.db else "disabled")
        m.add_command(label="開啟 jobs 資料夾", command=lambda: self._open_path(os.path.join(TOOL_ROOT, "jobs")))
        m.add_command(label="從舊的工具資料夾匯入 jobs 設定檔…", command=self.import_jobs)
        m.add_separator()
        m.add_command(label="文字選單（tools.bat）", command=lambda: self.spawn_console("menu"))
        m.add_command(label="環境檢查（diag）", command=lambda: self.spawn_console("diag", pause=True))
        try:
            m.tk_popup(widget.winfo_rootx(), widget.winfo_rooty() + widget.winfo_height())
        finally:
            try:
                m.grab_release()
            except Exception:
                pass

    def import_jobs(self, folder=None):
        """把另一個（舊的）工具資料夾裡自己寫的批次設定檔複製過來；同名的不覆蓋。"""
        import shutil
        if folder is None:
            folder = filedialog.askdirectory(title="選擇舊的工具資料夾（裡面有 jobs 資料夾的那個）",
                                             initialdir=os.path.dirname(TOOL_ROOT))
        if not folder:
            return 0
        src = os.path.join(folder, "jobs") if os.path.isdir(os.path.join(folder, "jobs")) else folder
        dst = os.path.join(TOOL_ROOT, "jobs")
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            messagebox.showinfo("匯入 jobs", "選到的就是目前的工具資料夾，不用匯入。", parent=self.root)
            return 0
        copied, skipped = [], []
        for name in sorted(os.listdir(src)):
            if not name.lower().endswith(".toml") or name.startswith("_"):
                continue
            target = os.path.join(dst, name)
            if os.path.exists(target):
                skipped.append(name)
                continue
            shutil.copy2(os.path.join(src, name), target)
            copied.append(name)
        msg = (f"已匯入 {len(copied)} 個：{', '.join(copied)}" if copied else "沒有新的設定檔可以匯入") + \
            (f"\n已經存在、沒有覆蓋：{', '.join(skipped)}" if skipped else "")
        messagebox.showinfo("匯入 jobs", msg, parent=self.root)
        self.status(msg.splitlines()[0], "ok" if copied else "info")
        return len(copied)

    def _tool_run_job(self):
        path = filedialog.askopenfilename(title="選擇批次分析設定檔", initialdir=os.path.join(TOOL_ROOT, "jobs"),
                                          filetypes=[("設定檔", "*.toml")], parent=self.root)
        if path:
            self.spawn_console("run", path, "--open", "--pause")

    def spawn_console(self, *args, pause=False):
        """在新的命令列視窗執行 python -m lta ...（看得到進度；不影響這個軟體）。"""
        py = os.path.join(os.path.dirname(sys.executable), "python.exe")
        if not os.path.exists(py):
            py = sys.executable
        cmd = [py, "-m", "lta", *args]
        try:
            if os.name == "nt":
                if pause:
                    cmd = ["cmd", "/c"] + [f'"{c}"' if " " in c else c for c in cmd] + ["&", "pause"]
                    subprocess.Popen(" ".join(cmd), cwd=TOOL_ROOT, creationflags=0x00000010)
                else:
                    subprocess.Popen(cmd, cwd=TOOL_ROOT, creationflags=0x00000010)
            else:
                subprocess.Popen(cmd, cwd=TOOL_ROOT)
            self.status("已在新視窗執行：" + " ".join(args[:1]), "ok")
        except Exception as e:
            messagebox.showerror("工具", f"無法執行：{e}", parent=self.root)

    def _open_path(self, path):
        try:
            os.startfile(path)
        except Exception:
            self.status(path)

    # ---------------------------------------------------------------- 自動更新
    def _periodic_update_check(self):
        if self._dead:
            return
        if self.gui_settings.get("auto_update_check", True):
            self.check_update()
        self.root.after(self.UPDATE_INTERVAL_MS, self._periodic_update_check)

    def check_update(self, manual=False):
        """背景檢查 GitHub 上有沒有新版本。"""
        if self._dead:
            return

        def work():
            try:
                from .. import updater
                info = updater.check()
                self._queue.put(("update", (info, manual, None)))
            except Exception as e:
                self._queue.put(("update", (None, manual, str(e))))
        threading.Thread(target=work, daemon=True).start()

    def _on_update_info(self, info, manual, error):
        if self._dead:
            return
        if error or info is None:
            if manual:
                messagebox.showwarning("檢查更新", f"無法連線到 GitHub：{error}", parent=self.root)
            return
        if info.get("error"):
            if manual:
                messagebox.showinfo("檢查更新", info["error"], parent=self.root)
            return
        if not info["newer"]:
            self._set_version_badge(None)
            if manual:
                if self._restart_pending():
                    if messagebox.askyesno("檢查更新", f"資料夾裡已經是最新的 v{info['current']}，"
                                           f"但目前執行的還是 v{self._running_version}。\n\n現在重新開啟套用嗎？",
                                           parent=self.root):
                        self.restart()
                else:
                    messagebox.showinfo("檢查更新", f"目前已是最新版本（v{info['current']}）", parent=self.root)
            return
        self._update_info = info
        self._set_version_badge(info["latest"])
        self.update_btn.configure(text=f"⬆ 更新到 v{info['latest']}")
        self.update_btn.pack(side="left", padx=(10, 0))
        self.status(f"有新版本 v{info['latest']}（目前 v{info['current']}），點右上角按鈕更新", "warn")
        key = info.get("commit") if info.get("by_commit") else info["latest"]
        if manual or (key != self._prompted_version and key != self.gui_settings.get("skip_version")):
            self._prompted_version = key
            self._prompt_update(info)

    def _badge_click(self):
        disk = self._restart_pending()
        if disk and messagebox.askyesno("重新開啟", f"資料夾裡已經是 v{disk}，目前執行的是 v{self._running_version}。\n"
                                        "現在重新開啟以套用新版嗎？", parent=self.root):
            self.restart()
            return
        self.open_settings(tab="更新")

    def _restart_pending(self):
        """資料夾裡的檔案已經是新版（例如手動覆蓋），但執行中的還是舊程式。"""
        try:
            from .. import updater
            disk = updater.disk_version()
            return updater.parse_version(disk) != updater.parse_version(self._running_version) and disk
        except Exception:
            return None

    def _set_version_badge(self, latest):
        from .. import __version__
        try:
            disk = self._restart_pending()
            if disk:
                a = T.ACCENTS[self.category]
                self.version_badge.configure(text=f"v{self._running_version} → 重新開啟以套用 v{disk}",
                                             bg=T.WARNING, fg="white")
                return
            if latest:
                a = T.ACCENTS[self.category]
                label = "有新的修改可更新" if (self._update_info or {}).get("by_commit") else f"v{latest} 可更新"
                self.version_badge.configure(text=f"v{__version__} → {label}", bg=a["main"],
                                             fg=a.get("on", "white"))
            else:
                self.version_badge.configure(text=f"v{__version__}", bg=T.HEADER_2, fg=T.HEADER_MUTED)
        except tk.TclError:
            pass

    def _prompt_update(self, info):
        if not info:
            return
        if info.get("by_commit"):
            head = f"GitHub 上有新的修改（版本號仍是 v{info['current']}）"
            notes = "\n".join("‧ " + n for n in info.get("notes", [])) or "（沒有說明）"
        else:
            head = f"有新版本 v{info['latest']}（目前 v{info['current']}）"
            blocks = []
            for v, d, ns in (info.get("since") or [(info["latest"], "", info.get("notes", []))])[:6]:
                blocks.append(f"v{v}" + (f"（{d}）" if d else "") + "\n" + "\n".join("‧ " + n for n in ns))
            notes = "\n\n".join(blocks) or "（沒有說明）"
        ans = messagebox.askyesnocancel(
            "有新版本", f"{head}\n\n更新內容：\n{notes}\n\n"
            "是 = 立即更新並重新開啟\n否 = 稍後再說\n取消 = 略過這個版本",
            parent=self.root)
        if ans is True:
            self.do_update(info)
        elif ans is None:
            self.gui_settings["skip_version"] = info.get("commit") if info.get("by_commit") else info["latest"]
            ST.save_settings(self.gui_settings)
            self.update_btn.pack_forget()

    def do_update(self, info=None):
        self.status("正在下載更新…", "busy")
        self.gui_settings["updated_from"] = self._running_version     # 重開後顯示「這版新增了什麼」
        ST.save_settings(self.gui_settings)

        def work():
            try:
                from .. import updater
                ver = updater.apply_update(info, log=lambda m: self._queue.put(("status", (m, "busy"))))
                self._queue.put(("updated", ver))
            except Exception as e:
                self._queue.put(("update_error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- 設定、重新開啟
    def open_settings(self, tab=None):
        from .settings_dialog import SettingsDialog
        return SettingsDialog(self, tab)

    def restart(self):
        """重新開啟軟體（套用介面配色等需要重建畫面的設定）。"""
        args = [sys.executable, "-m", "lta", "app"]
        if self.db:
            args.append(self.db.path)
        try:
            subprocess.Popen(args, cwd=TOOL_ROOT, close_fds=True)
        except Exception as e:
            messagebox.showerror("重新開啟", f"無法重新開啟：{e}", parent=self.root)
            return
        self._close()

    def _reset_params(self):
        a = self.current[self.category]
        for prm in a.global_params:
            var = self.param_vars.get(a.store_key, {}).get(prm.key)
            if var is not None:
                var.set(prm.default if prm.kind == "bool" else str(prm.default))

    def _params(self, per_item=True):
        a = self.current[self.category]
        glob, per = group_params(a)
        vars_ = self.param_vars.get(a.store_key, {})
        saved = self.saved_params.get(a.store_key, {})
        p = {}
        for prm in glob:
            if prm.key in vars_:
                p[prm.key] = prm.parse(vars_[prm.key].get())
            elif prm.key in saved:
                p[prm.key] = prm.parse(saved[prm.key])
            else:
                p[prm.key] = prm.default
        store = self.per_run.get(a.store_key, {})
        for prm in per:
            p[prm.key] = ({rid: prm.parse(v) for rid, v in store.get(prm.key, {}).items() if str(v).strip()}
                          if per_item else {})
        return p

    # ================================================================== 繪圖
    def _schedule_plot(self, delay=300):
        if not self.autoplot_var.get():
            return
        if self._plot_job is not None:
            self.root.after_cancel(self._plot_job)
        self._plot_job = self.root.after(delay, self.plot)

    def _get_ds(self, rid):
        ds = self.cache.get(rid)
        if ds is None:
            ds = self.db.load(rid)
            self.cache[rid] = ds
        return ds

    def _items(self):
        infos = self._selected_infos()
        if not infos:
            return []
        if self.sort_var.get() == "溫度":
            infos.sort(key=lambda r: (r.T_K is None, r.T_K or 0, r.run_id))
        else:
            infos.sort(key=lambda r: r.run_id)
        n = len(infos)
        cm = self.cmap_var.get()
        if cm == "自動":
            cm = "tab10" if n <= 10 else "turbo"
        cmap = colormaps[cm]
        qualitative = cm in ("tab10", "tab20")
        items = []
        for k, info in enumerate(infos):
            if cm == "tab10" and self.cmap_var.get() == "自動":
                color = T.PLOT_CYCLE[k % len(T.PLOT_CYCLE)]
            elif qualitative:
                color = cmap(k % cmap.N)
            else:
                color = cmap(0.05 + 0.9 * k / max(n - 1, 1)) if n > 1 else cmap(0.25)
            color = self.run_colors.get(info.run_id, color)
            items.append(Item(self._get_ds(info.run_id), self._label(info), color))
        return items

    def _label(self, info):
        mode = self.legend_var.get()
        Tt = fmt_T(info.T_K) if info.T_K is not None else None
        if mode == "溫度" and Tt:
            return Tt
        if mode == "run":
            return f"run {info.run_id}"
        if mode == "實驗描述":
            return f"{info.run_id}: {info.exp_name}"
        return f"{Tt} ({info.run_id})" if Tt else f"run {info.run_id}"

    def _render(self, fig, analysis, items, params):
        fig.clear()
        res = analysis.func(fig, items, params)
        if res.title:
            fig.suptitle(res.title, fontsize=10)
        return res

    def plot(self):
        self._plot_job = None
        if self._dead or not self.db:
            return
        a = self.current[self.category]
        try:
            items = self._items()
        except Exception as e:
            self.status(f"讀取資料失敗：{e}", "error")
            return
        if not items:
            self._compute_gen += 1
            self._set_busy(None)
            self._welcome()
            return
        if not a.multi:
            items = items[:1]
        bad = sorted({MODES.get(it.mode, {"short": it.mode})["short"] for it in items if not a.supports(it.mode)})
        self._set_plot_header(a, items)
        if bad:
            self._compute_gen += 1
            self._set_busy(None)
            self._message(f"「{a.name}」不支援 {'、'.join(bad)} 的資料",
                          f"這個功能支援：{a.modes_text}\n請換一個功能，或改選其他 run")
            self.status(f"{a.name}：資料模式不符", "warn")
            return
        try:
            params = self._params()
        except Exception as e:
            self._show_error(a, e)
            return
        if a.compute is not None:
            self._start_compute(a, items, params)
        else:
            self._compute_gen += 1
            self._set_busy(None)
            self._show(a, items, params)

    # ---------------------------------------------------------------- 背景計算
    def _start_compute(self, a, items, params):
        """慢的計算放到背景：介面不會卡住，狀態列顯示進度；可以先畫已完成的那幾筆。"""
        self._compute_gen += 1
        gen = self._compute_gen
        job = dict(gen=gen, a=a, items=items, params=params, t0=time.time(), partial_k=0, partial_t=0.0)
        self._compute_job = job
        self._set_busy((0, len(items), "準備中…"))

        def progress(k, n, text=""):
            if gen != self._compute_gen or self._dead:
                raise _Cancelled()
            self._queue.put(("progress", (gen, k, n, text)))

        def work():
            try:
                data = a.compute(items, dict(params), progress)
                self._queue.put(("computed", (gen, data)))
            except _Cancelled:
                pass
            except Exception as e:
                self._queue.put(("compute_error", (gen, e, traceback.format_exc())))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, gen, k, n, text):
        job = self._compute_job
        if job is None or gen != job["gen"]:
            return
        self._set_busy((k, n, text))
        # 每筆獨立的分析：先畫已經算好的那幾筆（至少間隔 0.6 秒，避免一直重畫）
        a = job["a"]
        if a.progressive and n == len(job["items"]) and k >= 2 and k > job["partial_k"] \
                and time.time() - job["partial_t"] > 0.6:
            job["partial_k"] = k
            self._show(a, job["items"][:k], job["params"], partial=(k, n))
            job["partial_t"] = time.time()

    def _on_computed(self, gen, data):
        job = self._compute_job
        if job is None or gen != job["gen"]:
            return
        self._compute_job = None
        params = dict(job["params"])
        params["_data"] = data
        self._set_busy(None)
        self._show(job["a"], job["items"], params, t0=job["t0"])

    def _on_compute_error(self, gen, err, tb):
        job = self._compute_job
        if job is None or gen != job["gen"]:
            return
        self._compute_job = None
        self._set_busy(None)
        print(tb, file=sys.stderr)
        self._show_error(job["a"], err, tb)

    def _set_busy(self, state):
        """state = (k, n, 說明) 顯示進度；None = 結束。"""
        try:
            if state is None:
                self.progress_bar.pack_forget()
                self.progress_lab.pack_forget()
                return
            k, n, text = state
            self.progress_bar.configure(maximum=max(n, 1), value=k)
            self.progress_lab.configure(text=f"{text}　{k}/{n}" if n else text)
            if not self.progress_bar.winfo_ismapped():
                self.progress_lab.pack(side="right", padx=(4, 8))
                self.progress_bar.pack(side="right", pady=3)
        except tk.TclError:
            pass

    def _show_error(self, a, e, tb=None):
        if tb is None:
            traceback.print_exc()
        self.result = None
        self._draggables = []
        self._message("發生錯誤", str(e), error=True)
        self._set_text(f"{a.name} 發生錯誤", tb or traceback.format_exc(), error=True)
        self.status(f"錯誤：{e}", "error")

    def _show(self, a, items, params, partial=None, t0=None):
        """在主執行緒畫圖（背景計算的結果在 params["_data"]）。"""
        t0 = t0 or time.time()
        self.root.config(cursor="watch")
        try:
            self.hover.reset()
            if self._click_cid is not None:
                self.canvas.mpl_disconnect(self._click_cid)
                self._click_cid = None
            res = self._render(self.fig, a, items, params)
            self._relayout_panels()
            self._apply_labels(a)
            ST.apply_style(self.fig, self.plot_style)
            self._collect_draggables()
            self._plot_need = self._panel_need()
            self._layout_plot()
            self.root.update_idletasks()
            self._set_hint(res.hint if not partial else f"計算中，先顯示已完成的 {partial[0]} / {partial[1]} 筆…")
            self.canvas.draw()
            self._home_limits = self._remember_view()
            self._fit_view()
            if partial:
                return
            self.result, self.last_items = res, items
            if res.on_click:
                self._click_cid = self.canvas.mpl_connect("button_press_event",
                                                          self._wrap_click(res.on_click, self.toolbar))
            ids = ", ".join(str(it.run_id) for it in items)
            self._set_text(f"{a.icon} {a.name}  ·  run {ids}", res.text)
            self._hl_orig = {}
            self._refresh_item_table()
            if getattr(self, "_hl_run", None) is not None and getattr(self, "item_tree", None) is not None \
                    and self.item_tree.exists(str(self._hl_run)):
                self._highlight_item(self._hl_run)
            self.status(f"完成：{a.name}，{len(items)} 筆，{time.time() - t0:.2f} s", "ok")
        except Exception as e:
            if partial:
                return                      # 部分結果畫不出來沒關係，等完整結果
            self._show_error(a, e)
        finally:
            self.root.config(cursor="")

    def _set_plot_header(self, a, items):
        a_col = T.ACCENTS[self.category]
        self.plot_icon.configure(text=a.icon)
        self.plot_title.configure(text=a.name)
        ids = [it.run_id for it in items]
        sub = f"run {ids[0]}" if len(ids) == 1 else f"run {min(ids)}–{max(ids)}（{len(ids)} 筆）"
        self.plot_sub.configure(text=f"{sub}  ·  {self.db.name if self.db else ''}")
        self.n_chip.set(f"{len(items)} 筆", fg=a_col["text"], bg=a_col["soft"])
        modes = sorted({it.info.mode_short for it in items})
        mcol = T.MODE_COLORS["2D"] if self.category == "2D" else T.MODE_COLORS.get(items[0].mode, T.MUTED)
        self.mode_chip.set(" · ".join(modes), fg="white", bg=mcol)

    def _reset_plot_area(self):
        self._plot_need = (0, 0)
        self._set_hint("")
        self._clear_item_highlight(redraw=False)
        try:
            self._layout_plot()
        except Exception:
            pass

    def _set_hint(self, text):
        try:
            if text:
                self.plot_hint.configure(text="💡 " + text, fg=T.ACCENTS[self.category]["text"])
                self.plot_hint.pack(fill="x")
            else:
                self.plot_hint.pack_forget()
        except tk.TclError:
            pass

    def _wrap_click(self, handler, toolbar):
        def on_click(event):
            if str(getattr(toolbar, "mode", "")) or self._legend_at(event) is not None:
                return
            handler(event)
        return on_click

    def _welcome(self):
        """還沒選資料時的引導畫面。"""
        self._draggables = []
        self._reset_plot_area()
        self.fig.clear()
        self.hover.reset()
        a = T.ACCENTS[self.category]
        self.fig.text(0.5, 0.70, f"{self.category} 資料分析", ha="center", va="center",
                      fontsize=24, fontweight="bold", color=a["main"])
        steps = ["① 在中間的清單選取 run（Ctrl / Shift 可多選）",
                 "② 在左邊點選分析功能",
                 "③ 調整參數，圖會自動更新"]
        for k, s in enumerate(steps):
            self.fig.text(0.5, 0.52 - k * 0.08, s, ha="center", va="center", fontsize=12, color="#374151",
                          bbox=dict(boxstyle="round,pad=0.6", fc=a["soft"], ec="none"))
        self.fig.text(0.5, 0.18, "滑鼠移到曲線上可讀數值　·　2D 色圖左鍵加切線、右鍵清除",
                      ha="center", fontsize=9, color="#9ca3af")
        self.canvas.draw_idle()
        self.result = None
        self.plot_title.configure(text="尚未繪圖")
        self.plot_sub.configure(text=self.db.name if self.db else "請先選擇資料庫")
        self.n_chip.set("")
        self.mode_chip.set("", bg=T.CARD)

    def _message(self, title, body, error=False):
        self._reset_plot_area()
        self.fig.clear()
        self.hover.reset()
        col = T.ERROR if error else T.WARNING
        self.fig.text(0.5, 0.58, ("✕  " if error else "!  ") + title, ha="center", va="center",
                      fontsize=15, fontweight="bold", color=col, wrap=True)
        self.fig.text(0.5, 0.45, body, ha="center", va="center", fontsize=10, color="#4b5563", wrap=True)
        self.canvas.draw_idle()

    def _set_text(self, head, body, error=False):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", head + "\n", ("head",))
        self.text.insert("end", body, ("err",) if error else ())
        self.text.configure(state="disabled")

    def status(self, msg, kind="info"):
        self.status_var.set(msg)
        self.status_dot.configure(fg={"ok": T.SUCCESS, "error": T.ERROR, "warn": T.WARNING,
                                      "busy": T.WARNING}.get(kind, T.FAINT))

    # ================================================================== 輸出
    def _out_dir(self):
        base = self.db.folder if self.db else os.getcwd()
        return ensure_dir(os.path.join(base, "01 Figure", "gui"))

    def _default_name(self):
        a = self.current[self.category]
        ids = [it.run_id for it in self.last_items]
        tag = (f"run{ids[0]}" if len(ids) == 1 else
               f"run{min(ids)}-{max(ids)}_{len(ids)}runs" if ids else "plot")
        return safe_name(f"{a.name.split('（')[0].split(' ')[0]}_{tag}")

    def save_figure(self):
        if self.result is None:
            messagebox.showinfo("存圖", "目前沒有圖可以存")
            return
        path = filedialog.asksaveasfilename(
            title="存圖", initialdir=self._out_dir(), initialfile=self._default_name() + ".png",
            defaultextension=".png", filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("PDF", "*.pdf"),
                                                ("SVG", "*.svg")])
        if not path:
            return
        self.hover._hide_all()
        self.fig.savefig(path, dpi=int(self.settings.get("dpi", 300)), bbox_inches="tight")
        self.status(f"已存圖：{path}", "ok")

    def export_csv(self):
        if self.result is None or not self.result.export:
            messagebox.showinfo("匯出 CSV", "這個分析沒有可匯出的數據")
            return
        exports = self.result.export
        if len(exports) == 1:
            name, (header, rows) = next(iter(exports.items()))
            path = filedialog.asksaveasfilename(
                title="匯出 CSV", initialdir=self._out_dir(),
                initialfile=f"{self._default_name()}_{name}.csv", defaultextension=".csv",
                filetypes=[("CSV", "*.csv")])
            targets = [(path, header, rows)] if path else []
        else:
            folder = filedialog.askdirectory(title="選擇存放資料夾", initialdir=self._out_dir())
            targets = [(os.path.join(folder, f"{self._default_name()}_{n}.csv"), h, r)
                       for n, (h, r) in exports.items()] if folder else []
        for path, header, rows in targets:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
        if targets:
            self.status("已匯出：" + "、".join(os.path.basename(t[0]) for t in targets), "ok")

    def pop_out(self):
        """目前的圖在新視窗再畫一次，方便並排比較。"""
        if self.result is None or not self.last_items:
            return
        a = self.current[self.category]
        win = tk.Toplevel(self.root)
        ids = ", ".join(str(it.run_id) for it in self.last_items)
        win.title(f"{a.name} — run {ids}")
        win.geometry(f"{T.px(1000)}x{T.px(700)}")
        win.configure(bg=T.CARD)
        fig = Figure(figsize=(4, 3), layout="constrained")
        canvas = SmoothCanvas(fig, master=win)
        canvas.get_tk_widget().configure(width=320, height=240)
        state = {}
        tb = FitToolbar(canvas, win, lambda: self._fit_view(fig, canvas, tb, state.get("lim", {})),
                        pack_toolbar=False)
        tb.update()
        tb.pack(side="top", fill="x")
        canvas.get_tk_widget().pack(fill="both", expand=True)
        win._lta_hover = Hover(canvas)
        try:
            res = self._render(fig, a, self.last_items, self._params())
            ST.apply_style(fig, self.plot_style)
            if res.on_click:
                canvas.mpl_connect("button_press_event", self._wrap_click(res.on_click, tb))
            canvas.draw()
            state["lim"] = self._remember_view(fig)
        except Exception as e:
            fig.text(0.5, 0.5, f"錯誤：{e}", ha="center", color="crimson")
            canvas.draw()

    def _close(self):
        self._save_sash()
        self._gen += 1
        self.root.quit()
        self.root.destroy()


def prepare(root):
    """字型、ttk 與 matplotlib 樣式（建立 App 之前呼叫一次）。"""
    try:
        from tkinter import font as tkfont
        T.pick_fonts(tkfont)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=T.FONT_FAMILY, size=10)
    except Exception:
        pass
    cfg = ST.load_settings()
    T.apply_scaling(root, cfg.get("ui_scale", "auto"))   # 依螢幕解析度 / 設定調整介面大小
    T.apply_theme(cfg)                      # 使用者選的介面配色、數據顏色循環
    T.apply_mpl_style()


def run_gui(db_path=None):
    """舊的進入點：改由軟體啟動器開啟（含載入畫面）。"""
    from .launcher import main
    return main(db_path)
