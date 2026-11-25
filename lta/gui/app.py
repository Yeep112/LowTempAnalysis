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
from ..core.registry import load_analyses, Item
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
from .widgets import card, section_title, Chip, ScrollFrame, Segmented, FeatureList

LEGENDS = ["溫度 (run)", "溫度", "run", "實驗描述"]
CMAP_CHOICES = ["自動", "turbo", "viridis", "plasma", "coolwarm", "tab10", "tab20", "jet"]
SORTS = ["溫度", "run"]
CATS = ["1D", "2D"]
ASSETS = os.path.join(TOOL_ROOT, "assets")


class App:
    def __init__(self, root, db_path=None):
        self.root = root
        self.settings = load_settings()
        self.analyses = load_analyses()
        self.db = None
        self.cache = {}
        self.all_runs = []
        self.category = "1D"
        self.current = {c: self.analyses[c][0] for c in CATS if self.analyses[c]}
        self.param_vars = {}
        self.result = None
        self.last_items = []
        self._click_cid = None
        self._plot_job = None
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
        root.geometry("1600x940")
        root.minsize(1200, 720)
        root.configure(bg=T.BG)
        self._build()
        self._load_feature_list()
        self._welcome()
        root.after(200, self._poll_queue)
        root.after(10000, self._auto_update)
        if self.gui_settings.get("auto_update_check", True):
            root.after(3000, self.check_update)
        root.protocol("WM_DELETE_WINDOW", self._close)
        if db_path:
            self.open_db(db_path)
        self._scan_dbs(open_newest=not db_path)

    # ================================================================== 版面
    def _build(self):
        self._build_header()
        body = tk.Frame(self.root, bg=T.BG)
        body.pack(fill="both", expand=True, padx=10, pady=(10, 6))
        pw = ttk.Panedwindow(body, orient="horizontal")
        pw.pack(fill="both", expand=True)

        left_o, left = card(pw, pad=10)
        mid_o, mid = card(pw, pad=10)
        right_o, right = card(pw, pad=10)
        left_o.configure(width=300)
        mid_o.configure(width=470)
        pw.add(left_o, weight=0)
        pw.add(mid_o, weight=0)
        pw.add(right_o, weight=1)
        self._build_left(left)
        self._build_middle(mid)
        self._build_right(right)
        self._build_status()

        self.root.bind("<F5>", lambda e: self.refresh())
        self.root.bind("<Control-s>", lambda e: self.save_figure())
        self.root.bind("<Return>", lambda e: None if isinstance(e.widget, (tk.Entry, ttk.Entry)) else self.plot())

    def _build_header(self):
        h = tk.Frame(self.root, bg=T.HEADER, height=64)
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
        tk.Label(tf, text="Low-Temperature Analysis", bg=T.HEADER, fg=T.HEADER_MUTED,
                 font=T.font(9)).pack(anchor="w")

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
        self._update_prompted = False
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
        vp = ttk.Panedwindow(parent, orient="vertical")
        vp.pack(fill="both", expand=True)
        data = tk.Frame(vp, bg=T.CARD)
        prm = tk.Frame(vp, bg=T.CARD)
        vp.add(data, weight=3)
        vp.add(prm, weight=2)

        # ---- 資料 ----
        row, _ = section_title(data, "資料", "☰", T.MUTED)
        row.pack(fill="x")
        self.count_chip = Chip(row, "")
        self.count_chip.pack(side="right")
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
        widths = (26, 46, 74, 50, 56, 88, 250)
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="extended")
        for c, hd, w in zip(cols, heads, widths):
            self.tree.heading(c, text=hd, command=lambda c=c: self._sort_by(c))
            self.tree.column(c, width=w, minwidth=22 if c == "c" else 40, stretch=(c == "name"),
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
        bf = tk.Frame(data, bg=T.CARD)
        bf.pack(fill="x", pady=(6, 4))
        ttk.Button(bf, text="全選", style="Small.TButton", command=self._select_all).pack(side="left")
        ttk.Button(bf, text="清除選取", style="Small.TButton", command=self._clear_sel).pack(side="left", padx=4)
        self.color_btn = ttk.Button(bf, text="● 顏色", style="Small.TButton",
                                    command=lambda: self._color_menu_at(self.color_btn))
        self.color_btn.pack(side="left")
        self.load_chip = Chip(bf, "")
        self.load_chip.pack(side="right")

        # ---- 參數 ----
        head = tk.Frame(prm, bg=T.CARD)
        head.pack(fill="x", pady=(8, 0))
        self.prm_icon = tk.Label(head, text="•", bg=T.CARD, fg=T.ACCENTS["1D"]["main"], font=T.font(13, "bold"))
        self.prm_icon.pack(side="left", padx=(0, 6))
        self.prm_title = tk.Label(head, text="參數", bg=T.CARD, fg=T.TEXT, font=T.font(11, "bold"),
                                  anchor="w", justify="left", wraplength=360)
        self.prm_title.pack(side="left", fill="x", expand=True)
        ttk.Button(head, text="恢復預設", style="Small.TButton", command=self._reset_params).pack(side="right")
        self.help_lab = tk.Label(prm, text="", bg=T.CARD, fg=T.MUTED, font=T.font(9), anchor="w",
                                 justify="left", wraplength=430)
        self.help_lab.pack(fill="x", pady=(2, 4))
        self.param_scroll = ScrollFrame(prm)
        self.param_scroll.pack(fill="both", expand=True)
        self.param_frame = self.param_scroll.body

        disp = tk.Frame(prm, bg=T.CARD)
        disp.pack(fill="x", pady=(6, 0))
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
        btn.pack(fill="x", pady=(8, 0))
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
        self.mode_chip = Chip(head, "")
        self.mode_chip.pack(side="right", padx=(6, 0))
        self.n_chip = Chip(head, "")
        self.n_chip.pack(side="right")

        self._build_style_bar(parent)
        vp = ttk.Panedwindow(parent, orient="vertical")
        vp.pack(fill="both", expand=True, pady=(4, 0))
        figf = tk.Frame(vp, bg=T.CARD)
        txtf = tk.Frame(vp, bg=T.CARD)
        vp.add(figf, weight=5)
        vp.add(txtf, weight=1)
        self._right_paned = vp
        self.root.after(400, self._init_sash)
        self.fig = Figure(figsize=(4, 3), layout="constrained")
        self.canvas = FigureCanvasTkAgg(self.fig, master=figf)
        # 要求的大小設小一點：圖的大小一律跟著可見區域走，不會超出被切掉
        self.canvas.get_tk_widget().configure(width=320, height=240)
        self.toolbar = FitToolbar(self.canvas, figf, self._fit_view, pack_toolbar=False)
        try:
            self.toolbar.configure(background=T.CARD)
            for w in self.toolbar.winfo_children():
                w.configure(background=T.CARD)
        except Exception:
            pass
        self.toolbar.update()
        self.toolbar.pack(side="top", fill="x")
        self.canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        self.hover = Hover(self.canvas)
        self.canvas.mpl_connect("button_press_event", self._drag_press)
        self.canvas.mpl_connect("motion_notify_event", self._drag_motion)
        self.canvas.mpl_connect("button_release_event", self._drag_release)

        th = tk.Frame(txtf, bg=T.CARD)
        th.pack(fill="x", pady=(6, 2))
        tk.Label(th, text="≡ 文字結果", bg=T.CARD, fg=T.MUTED, font=T.font(9, "bold")).pack(side="left")
        self.text = tk.Text(txtf, height=7, font=(T.MONO_FAMILY, 10), wrap="none", bg="#f8fafc",
                            fg=T.TEXT, bd=0, highlightthickness=1, highlightbackground=T.BORDER,
                            padx=10, pady=8)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("head", foreground=T.ACCENTS["1D"]["text"], font=(T.FONT_FAMILY, 10, "bold"))
        self.text.tag_configure("err", foreground=T.ERROR)

    def _init_sash(self):
        try:
            h = self._right_paned.winfo_height()
            if h > 200:
                self._right_paned.sashpos(0, int(h * 0.80))
        except Exception:
            pass

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
        bar = tk.Frame(parent, bg="#f6f8fc", highlightthickness=1, highlightbackground=T.BORDER)
        bar.pack(fill="x", pady=(8, 0))
        tk.Label(bar, text="圖表樣式", bg="#f6f8fc", fg=T.MUTED, font=T.font(9, "bold")).pack(side="left", padx=(8, 10))
        self.style_vars = {"grid": tk.BooleanVar(value=bool(self.plot_style["grid"]))}
        tk.Checkbutton(bar, text="格線", variable=self.style_vars["grid"], bg="#f6f8fc", fg=T.TEXT,
                       activebackground="#f6f8fc", selectcolor="white", font=T.font(9), bd=0,
                       highlightthickness=0).pack(side="left", padx=(0, 10))
        for key, label in ST.FIELDS:
            tk.Label(bar, text=label, bg="#f6f8fc", fg=T.MUTED, font=T.font(9)).pack(side="left", padx=(6, 3))
            var = tk.StringVar(value=str(self.plot_style[key]))
            self.style_vars[key] = var
            ttk.Spinbox(bar, from_=5, to=40, width=3, textvariable=var,
                        command=self._schedule_restyle).pack(side="left", pady=4)
        for var in self.style_vars.values():
            var.trace_add("write", lambda *a: self._schedule_restyle())
        ttk.Button(bar, text="恢復預設", style="Small.TButton", command=self._reset_style).pack(side="right", padx=6)

    def _read_style(self):
        st = {"grid": bool(self.style_vars["grid"].get())}
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
        self.plot_style = self._read_style()
        self.gui_settings["style"] = self.plot_style
        ST.save_settings(self.gui_settings)
        if self.result is not None:
            ST.apply_style(self.fig, self.plot_style)
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
        tk.Label(bar, text="F5 重新整理 · Ctrl+S 存圖 · Enter 繪圖 · Ctrl+A 全選", bg=T.CARD,
                 fg=T.FAINT, font=T.font(8)).pack(side="right", padx=10)

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

    def open_db(self, path):
        if not path:
            return
        try:
            db = QDB(path)
            runs = db.runs()
        except Exception as e:
            messagebox.showerror("無法開啟", f"{path}\n\n{e}")
            return
        self._gen += 1
        self.db, self.cache, self.all_runs = db, {}, runs
        self._mtime = db_mtime(db.path)
        self._load_per_run()
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
        try:
            if self.auto_var.get() and self.db and os.path.exists(self.db.path):
                m = db_mtime(self.db.path)
                if self._mtime is not None and m != self._mtime:
                    self.refresh()
        finally:
            self.root.after(10000, self._auto_update)

    def _poll_queue(self):
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
        self.root.after(200, self._poll_queue)

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
        self.count_chip.set(f"{s} / {n} 已選", fg=a["text"] if s else T.MUTED, bg=a["soft"] if s else "#f1f4f9")

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
        vars_ = self.param_vars.setdefault(a.name, {})
        row = 0
        for prm in a.global_params:
            var = vars_.get(prm.key)
            if var is None:
                var = tk.BooleanVar(value=prm.default) if prm.kind == "bool" else tk.StringVar(value=str(prm.default))
                var.trace_add("write", lambda *x: self._schedule_plot(delay=700))
                vars_[prm.key] = var
            if prm.kind == "bool":
                ttk.Checkbutton(self.param_frame, text=prm.label, variable=var).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=3)
            else:
                tk.Label(self.param_frame, text=prm.label, bg=T.CARD, fg=T.MUTED, font=T.font(9),
                         anchor="w", justify="left", wraplength=190).grid(row=row, column=0, sticky="w", pady=3)
                if prm.kind == "choice":
                    w = ttk.Combobox(self.param_frame, textvariable=var, values=prm.choices,
                                     state="readonly", width=22)
                else:
                    w = ttk.Entry(self.param_frame, textvariable=var, width=24)
                w.grid(row=row, column=1, sticky="ew", padx=(8, 4), pady=3)
            row += 1
            if prm.tip:
                tk.Label(self.param_frame, text="↳ " + prm.tip, bg=T.CARD, fg=T.FAINT, font=T.font(8),
                         wraplength=400, justify="left", anchor="w").grid(row=row, column=0, columnspan=2,
                                                                          sticky="w", pady=(0, 3))
                row += 1
        if not a.params:
            tk.Label(self.param_frame, text="這個功能沒有參數", bg=T.CARD, fg=T.FAINT,
                     font=T.font(9)).grid(row=0, column=0, sticky="w")
        self.item_tree = None
        if a.per_item_params:
            self._build_item_table(a, row)
        self.param_frame.columnconfigure(1, weight=1)
        self.param_scroll.to_top()

    # ---------------------------------------------------------------- 逐筆設定
    def _per_run_path(self):
        return os.path.join(self.db.folder, "01 Figure", "gui", "per_run_settings.json") if self.db else None

    def _load_per_run(self):
        self.per_run = {}
        self.run_colors = {}
        path = self._per_run_path()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f).get(self.db.name, {})
            self.run_colors = {int(r): v for r, v in data.pop("__colors__", {}).items()}
            self.per_run = {an: {k: {int(r): v for r, v in vals.items()} for k, vals in keys.items()}
                            for an, keys in data.items()}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save_per_run(self):
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
            with open(path, "w", encoding="utf-8") as f:
                json.dump(allc, f, ensure_ascii=False, indent=1)
        except OSError as e:
            self.status(f"無法儲存逐筆設定：{e}", "warn")

    def set_item_value(self, key, run_id, value):
        """逐筆設定：寫入一個值（空字串 = 恢復預設）。圖上拖曳也會呼叫這裡。"""
        a = self.current[self.category]
        store = self.per_run.setdefault(a.name, {}).setdefault(key, {})
        text = "" if value is None else str(value).strip()
        if text == "":
            store.pop(int(run_id), None)
        else:
            store[int(run_id)] = text
        self._save_per_run()
        self._refresh_item_table()

    def _build_item_table(self, a, row):
        acc = T.ACCENTS[self.category]
        head = tk.Frame(self.param_frame, bg=T.CARD)
        head.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 2))
        tk.Label(head, text="▤ 逐筆設定", bg=T.CARD, fg=acc["text"], font=T.font(10, "bold")).pack(side="left")
        ttk.Button(head, text="清除選取 run 的設定", style="Small.TButton",
                   command=self._clear_item_values).pack(side="right")
        tk.Label(self.param_frame, text="雙擊格子修改，按 Enter 確定；清空 = 用預設（括號內）。"
                 "有個別設定的列會變色，也可以在圖上直接拖曳標記。",
                 bg=T.CARD, fg=T.FAINT, font=T.font(8), wraplength=410, justify="left",
                 anchor="w").grid(row=row + 1, column=0, columnspan=2, sticky="w")
        cols = ["run", "T"] + [q.key for q in a.per_item_params]
        tr = ttk.Treeview(self.param_frame, columns=cols, show="headings", height=6, selectmode="browse")
        tr.heading("run", text="run")
        tr.heading("T", text="T (K)")
        tr.column("run", width=46, anchor="e", stretch=False)
        tr.column("T", width=46, anchor="e", stretch=False)
        for q in a.per_item_params:
            tr.heading(q.key, text=q.label)
            tr.column(q.key, width=96 if q.kind != "text" else 130, anchor="e" if q.kind != "text" else "w")
        tr.tag_configure("manual", foreground=acc["text"], background=acc["soft"])
        tr.grid(row=row + 2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        tr.bind("<Double-1>", self._edit_item_cell)
        tips = [f"{q.label}：{q.tip}" for q in a.per_item_params if q.tip]
        if tips:
            tk.Label(self.param_frame, text="↳ " + "　".join(tips), bg=T.CARD, fg=T.FAINT, font=T.font(8),
                     wraplength=410, justify="left", anchor="w").grid(row=row + 3, column=0, columnspan=2, sticky="w")
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
        store = self.per_run.get(a.name, {})
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
        cur = self.per_run.get(a.name, {}).get(key, {}).get(int(row), "")
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
        for vals in self.per_run.get(a.name, {}).values():
            for rid in list(vals):
                if rid in ids:
                    vals.pop(rid)
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

    def _drag_press(self, event):
        if event.button != 1 or str(getattr(self.toolbar, "mode", "")):
            return
        ln = self._near_draggable(event)
        if ln is None:
            return
        tag = ln.lta_drag
        self._drag = {"tag": tag, "lines": [l for l in self._draggables if l.lta_drag == tag], "x": None}

    def _drag_motion(self, event):
        if self._drag is None:
            near = self._near_draggable(event) if event.inaxes is not None else None
            try:
                self.canvas.get_tk_widget().configure(cursor="sb_h_double_arrow" if near else "")
            except Exception:
                pass
            return
        if event.inaxes is None or event.xdata is None:
            return
        x = float(event.xdata)
        self._drag["x"] = x
        for ln in self._drag["lines"]:
            ln.set_xdata([x] * len(ln.get_xdata()))
        self.canvas.draw_idle()

    def _drag_release(self, event):
        if self._drag is None:
            return
        drag, self._drag = self._drag, None
        if drag["x"] is None:
            return
        key, rid = drag["tag"]
        self.set_item_value(key, rid, f"{drag['x']:.4g}")
        self.status(f"run {rid}：{key} = {drag['x']:.4g}", "ok")
        self._schedule_plot(delay=50)

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
    def check_update(self, manual=False):
        """背景檢查 GitHub 上有沒有新版本。"""
        def work():
            try:
                from .. import updater
                info = updater.check()
                self._queue.put(("update", (info, manual, None)))
            except Exception as e:
                self._queue.put(("update", (None, manual, str(e))))
        threading.Thread(target=work, daemon=True).start()

    def _on_update_info(self, info, manual, error):
        if error or info is None:
            if manual:
                messagebox.showwarning("檢查更新", f"無法連線到 GitHub：{error}", parent=self.root)
            return
        if info.get("error"):
            if manual:
                messagebox.showinfo("檢查更新", info["error"], parent=self.root)
            return
        if not info["newer"]:
            if manual:
                messagebox.showinfo("檢查更新", f"目前已是最新版本（v{info['current']}）", parent=self.root)
            return
        self._update_info = info
        self.update_btn.configure(text=f"⬆ 更新到 v{info['latest']}")
        self.update_btn.pack(side="left", padx=(10, 0))
        self.status(f"有新版本 v{info['latest']}（目前 v{info['current']}），點右上角按鈕更新", "warn")
        if manual or (not self._update_prompted and info["latest"] != self.gui_settings.get("skip_version")):
            self._update_prompted = True
            self._prompt_update(info)

    def _prompt_update(self, info):
        if not info:
            return
        notes = "\n".join("‧ " + n for n in info.get("notes", [])) or "（沒有說明）"
        ans = messagebox.askyesnocancel(
            "有新版本",
            f"有新版本 v{info['latest']}（目前 v{info['current']}）\n\n更新內容：\n{notes}\n\n"
            "是 = 立即更新並重新開啟\n否 = 稍後再說\n取消 = 略過這個版本",
            parent=self.root)
        if ans is True:
            self.do_update(info)
        elif ans is None:
            self.gui_settings["skip_version"] = info["latest"]
            ST.save_settings(self.gui_settings)
            self.update_btn.pack_forget()

    def do_update(self, info=None):
        self.status("正在下載更新…", "busy")

        def work():
            try:
                from .. import updater
                ver = updater.apply_update(info, log=lambda m: self._queue.put(("status", (m, "busy"))))
                self._queue.put(("updated", ver))
            except Exception as e:
                self._queue.put(("update_error", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- 設定、重新開啟
    def open_settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self)

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
            var = self.param_vars.get(a.name, {}).get(prm.key)
            if var is not None:
                var.set(prm.default if prm.kind == "bool" else str(prm.default))

    def _params(self, per_item=True):
        a = self.current[self.category]
        vars_ = self.param_vars.get(a.name, {})
        p = {prm.key: prm.parse(vars_[prm.key].get()) if prm.key in vars_ else prm.default
             for prm in a.global_params}
        store = self.per_run.get(a.name, {})
        for prm in a.per_item_params:
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
        if not self.db:
            return
        a = self.current[self.category]
        try:
            items = self._items()
        except Exception as e:
            self.status(f"讀取資料失敗：{e}", "error")
            return
        if not items:
            self._welcome()
            return
        if not a.multi:
            items = items[:1]
        bad = sorted({MODES.get(it.mode, {"short": it.mode})["short"] for it in items if not a.supports(it.mode)})
        self._set_plot_header(a, items)
        if bad:
            self._message(f"「{a.name}」不支援 {'、'.join(bad)} 的資料",
                          f"這個功能支援：{a.modes_text}\n請換一個功能，或改選其他 run")
            self.status(f"{a.name}：資料模式不符", "warn")
            return
        t0 = time.time()
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            params = self._params()
            self.hover.reset()
            if self._click_cid is not None:
                self.canvas.mpl_disconnect(self._click_cid)
                self._click_cid = None
            res = self._render(self.fig, a, items, params)
            ST.apply_style(self.fig, self.plot_style)
            self._collect_draggables()
            self.canvas.draw()
            self._home_limits = self._remember_view()
            self._fit_view()
            self.result, self.last_items = res, items
            if res.on_click:
                self._click_cid = self.canvas.mpl_connect("button_press_event",
                                                          self._wrap_click(res.on_click, self.toolbar))
            ids = ", ".join(str(it.run_id) for it in items)
            self._set_text(f"{a.icon} {a.name}  ·  run {ids}", res.text)
            self._refresh_item_table()
            self.status(f"完成：{a.name}，{len(items)} 筆，{time.time() - t0:.2f} s", "ok")
        except Exception as e:
            traceback.print_exc()
            self.result = None
            self._draggables = []
            self._message("發生錯誤", str(e), error=True)
            self._set_text(f"{a.name} 發生錯誤", traceback.format_exc(), error=True)
            self.status(f"錯誤：{e}", "error")
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

    @staticmethod
    def _wrap_click(handler, toolbar):
        def on_click(event):
            if str(getattr(toolbar, "mode", "")):
                return
            handler(event)
        return on_click

    def _welcome(self):
        """還沒選資料時的引導畫面。"""
        self._draggables = []
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
        win.geometry("1000x700")
        win.configure(bg=T.CARD)
        fig = Figure(figsize=(4, 3), layout="constrained")
        canvas = FigureCanvasTkAgg(fig, master=win)
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
    T.apply_theme(ST.load_settings())       # 使用者選的介面配色、數據顏色循環
    T.apply_mpl_style()


def run_gui(db_path=None):
    """舊的進入點：改由軟體啟動器開啟（含載入畫面）。"""
    from .launcher import main
    return main(db_path)
