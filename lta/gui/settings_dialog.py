"""⚙ 設定視窗：介面配色、預設數據顏色、資料與輸出、啟動與安裝、關於。

設定存在 %LOCALAPPDATA%\\LowTempAnalysis\\gui_settings.json；
資料夾與輸出格式寫到 %LOCALAPPDATA%\\LowTempAnalysis\\settings.toml（批次分析也會用）。
"""
from __future__ import annotations

import os
import re
import shutil
import sys

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

from . import theme as T
from . import style as ST
from .colors import THEME_PRESETS, DEFAULT_CYCLE, SWATCHES, parse_color, describe_base
from .launcher import TOOL_ROOT, log_dir


def update_toml_value(path, key, literal):
    """把 settings.toml 裡 key = ... 這一行換掉（保留其他內容與註解）；沒有就加在最後。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    pat = re.compile(rf"^{re.escape(key)}\s*=.*$", re.M)
    line = f"{key} = {literal}"
    text = pat.sub(lambda m: line, text, count=1) if pat.search(text) else text.rstrip() + "\n" + line + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class SettingsDialog:
    def __init__(self, app, tab=None, geometry=None):
        self.app = app
        self.cfg = dict(app.gui_settings)
        self.win = win = tk.Toplevel(app.root)
        win.title("設定")
        win.geometry(geometry or "820x660")
        win.configure(bg=T.BG)
        win.transient(app.root)
        self.base_var = tk.StringVar(value=self._current_base())
        self.acc1_var = tk.StringVar(value=self.cfg.get("theme_accent_1d", ""))
        self.acc2_var = tk.StringVar(value=self.cfg.get("theme_accent_2d", ""))
        self.cycle = list(self.cfg.get("cycle") or T.PLOT_CYCLE)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tabs = {}
        for title, builder in (("外觀", self._tab_theme), ("數據顏色", self._tab_colors),
                               ("資料與輸出", self._tab_data), ("啟動與安裝", self._tab_launch),
                               ("更新", self._tab_update), ("關於", self._tab_about)):
            fr = tk.Frame(nb, bg=T.CARD, padx=16, pady=12)
            nb.add(fr, text=f"  {title}  ")
            self.tabs[title] = fr
            builder(fr)
        if tab in self.tabs:
            nb.select(self.tabs[tab])

        bf = tk.Frame(win, bg=T.BG)
        bf.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bf, text="取消", style="Ghost.TButton", command=win.destroy).pack(side="right")
        ttk.Button(bf, text="儲存", style="Accent.TButton", command=self.save).pack(side="right", padx=6)
        tk.Label(bf, text="介面顏色點一下就套用；其他分頁的設定按「儲存」生效。", bg=T.BG,
                 fg=T.MUTED, font=T.font(9)).pack(side="left")

    def _current_base(self):
        base = self.cfg.get("theme_base")
        if base:
            return SWATCHES.get(base, base)
        if self.cfg.get("theme_preset") == "自訂":
            return (self.cfg.get("theme_custom") or {}).get("bg", SWATCHES["預設藍"])
        if self.cfg.get("theme_preset") in THEME_PRESETS:
            return THEME_PRESETS[self.cfg["theme_preset"]]["bg"]
        return SWATCHES["預設藍"]

    # ------------------------------------------------------------------ 小工具
    def _h(self, parent, text, sub=None):
        tk.Label(parent, text=text, bg=T.CARD, fg=T.TEXT, font=T.font(11, "bold"), anchor="w").pack(fill="x", pady=(6, 0))
        if sub:
            tk.Label(parent, text=sub, bg=T.CARD, fg=T.FAINT, font=T.font(9), anchor="w", justify="left",
                     wraplength=700).pack(fill="x", pady=(0, 6))

    def _swatch(self, parent, color, w=3, cmd=None):
        lab = tk.Label(parent, text=" " * w, bg=color, relief="flat", bd=0, cursor="hand2" if cmd else "",
                       highlightthickness=1, highlightbackground=T.BORDER)
        if cmd:
            lab.bind("<Button-1>", lambda e: cmd())
        return lab

    def _choose(self, initial, title):
        try:
            res = colorchooser.askcolor(color=initial, title=title, parent=self.win)
        except Exception:
            res = None
        return res[1] if res and res[1] else None

    # ------------------------------------------------------------------ 外觀
    def _tab_theme(self, f):
        self._h(f, "介面顏色", None)
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x", pady=(4, 8))
        self._circles = {}
        cur = self._safe(self.base_var.get(), "")
        for name, c in SWATCHES.items():
            cell = tk.Frame(row, bg=T.CARD)
            cell.pack(side="left", padx=(0, 10))
            cv = tk.Canvas(cell, width=52, height=52, bg=T.CARD, highlightthickness=0, cursor="hand2")
            cv.pack()
            cv.create_oval(6, 6, 46, 46, fill=c, outline=T.BORDER, width=1)
            ring = cv.create_oval(2, 2, 50, 50, outline="", width=2.5)
            tk.Label(cell, text=name, bg=T.CARD, fg=T.TEXT, font=T.font(9)).pack()
            cv.bind("<Button-1>", lambda e, col=c: self._choose_base(col))
            self._circles[c] = (cv, ring)
        self._mark_circle(cur)

        self._h(f, "自訂 Hex 色碼", None)
        hrow = tk.Frame(f, bg=T.CARD)
        hrow.pack(fill="x", pady=(2, 4))
        self._base_sw = tk.Label(hrow, text="    ", width=5, bg=cur or "#ffffff", relief="flat",
                                 highlightthickness=1, highlightbackground=T.BORDER, cursor="hand2")
        self._base_sw.pack(side="left", ipady=6)
        self._base_sw.bind("<Button-1>", lambda e: self._pick_base())
        ent = ttk.Entry(hrow, textvariable=self.base_var, width=14, font=T.font(11))
        ent.pack(side="left", padx=8)
        ent.bind("<Return>", lambda e: self._choose_base(self.base_var.get()))
        ttk.Button(hrow, text="🎨 色盤", style="Ghost.TButton", command=self._pick_base).pack(side="left")
        ttk.Button(hrow, text="套用", style="Accent.TButton",
                   command=lambda: self._choose_base(self.base_var.get())).pack(side="left", padx=6)
        self._kind_lab = tk.Label(hrow, text="", bg=T.CARD, fg=T.MUTED, font=T.font(9))
        self._kind_lab.pack(side="left", padx=8)
        tk.Label(f, text="淺色（例如薰衣草紫）會直接當作背景；較深或較飽和的顏色會當作重點色；很深的顏色會自動切成"
                 "深色版面。文字對比會自動調整，換成什麼顏色都看得清楚。可輸入 #RRGGBB 或 R, G, B。",
                 bg=T.CARD, fg=T.MUTED, font=T.font(9), wraplength=740, justify="left").pack(anchor="w", pady=(2, 8))
        self.base_var.trace_add("write", lambda *a: self._update_preview())

        self._h(f, "預覽", None)
        self._preview = tk.Frame(f, bg=T.CARD)
        self._preview.pack(fill="x", pady=(2, 8))

        self._h(f, "進階：1D / 2D 主色（空白 = 依介面顏色自動）", None)
        grid = tk.Frame(f, bg=T.CARD)
        grid.pack(fill="x")
        self._acc_sw = {}
        for c, (lab, var) in enumerate((("1D 主色", self.acc1_var), ("2D 主色", self.acc2_var))):
            tk.Label(grid, text=lab, bg=T.CARD, fg=T.MUTED, font=T.font(9)).grid(row=0, column=c * 3, sticky="w")
            e = ttk.Entry(grid, textvariable=var, width=12)
            e.grid(row=0, column=c * 3 + 1, padx=6)
            e.bind("<Return>", lambda ev: self._choose_base(self.base_var.get()))
            sw = self._swatch(grid, self._safe(var.get(), T.CARD), 4,
                              cmd=lambda v=var: self._pick_into(v))
            sw.grid(row=0, column=c * 3 + 2, padx=(0, 18))
            self._acc_sw[str(var)] = sw
            var.trace_add("write", lambda *a: self._update_preview())

        self._h(f, "圖中的中文字型", "圖上的中文（標題、標籤）變成方框時，在這裡換一個字型。")
        frow = tk.Frame(f, bg=T.CARD)
        frow.pack(fill="x")
        fonts = ["自動"] + T.cjk_font_candidates()
        self.font_var = tk.StringVar(value=self.cfg.get("plot_cjk_font") or "自動")
        ttk.Combobox(frow, textvariable=self.font_var, values=fonts, state="readonly", width=28).pack(side="left")
        tk.Label(frow, text=f"目前使用：{T.current_cjk_font() or '（找不到中文字型）'}", bg=T.CARD, fg=T.MUTED,
                 font=T.font(9)).pack(side="left", padx=10)
        self._update_preview()

    def _mark_circle(self, color):
        for c, (cv, ring) in self._circles.items():
            cv.itemconfigure(ring, outline=T.ACCENTS["1D"]["main"] if c == color else "")

    def _palette_for(self, base):
        cfg = {"theme_base": base, "theme_accent_1d": self.acc1_var.get().strip(),
               "theme_accent_2d": self.acc2_var.get().strip()}
        return T.theme_palette(cfg)

    def _update_preview(self):
        try:
            base = parse_color(self.base_var.get())
        except ValueError:
            self._kind_lab.configure(text="看不懂這個顏色", fg=T.ERROR)
            return
        self._base_sw.configure(bg=base)
        self._kind_lab.configure(text=f"→ {describe_base(base)}", fg=T.MUTED)
        for var in (self.acc1_var, self.acc2_var):
            sw = self._acc_sw.get(str(var))
            if sw is not None:
                sw.configure(bg=self._safe(var.get(), T.CARD))
        t = self._palette_for(base)
        for w in self._preview.winfo_children():
            w.destroy()
        box = tk.Frame(self._preview, bg=t["BG"], highlightthickness=1, highlightbackground=t["BORDER"])
        box.pack(anchor="w")
        hd = tk.Frame(box, bg=t["HEADER"], width=520, height=34)
        hd.pack(fill="x")
        tk.Label(hd, text="  ◢ 低溫量測分析", bg=t["HEADER"], fg=t["HEADER_TEXT"], font=T.font(10, "bold")).pack(side="left", pady=5)
        tk.Label(hd, text=" ⚙ 設定 ", bg=t["HEADER_2"], fg=t["HEADER_TEXT"], font=T.font(9)).pack(side="right", padx=8)
        body = tk.Frame(box, bg=t["BG"], padx=10, pady=10)
        body.pack(fill="x")
        for key in ("1D", "2D"):
            a = t["ACCENTS"][key]
            card = tk.Frame(body, bg=t["CARD"], highlightthickness=1, highlightbackground=t["BORDER"], padx=10, pady=8)
            card.pack(side="left", padx=(0, 10))
            tk.Label(card, text=f"{key} 資料分析", bg=a["main"], fg=a["on"], font=T.font(9, "bold"),
                     padx=10, pady=3).pack(anchor="w")
            tk.Label(card, text="已選取的項目", bg=a["soft"], fg=a["text"], font=T.font(9), padx=8).pack(anchor="w", pady=(6, 0))
            tk.Label(card, text="一般文字　次要說明", bg=t["CARD"], fg=t["TEXT"], font=T.font(9)).pack(anchor="w", pady=(4, 0))
            tk.Label(card, text="提示文字", bg=t["CARD"], fg=t["MUTED"], font=T.font(8)).pack(anchor="w")

    def _choose_base(self, color):
        """點顏色 → 立刻套用到整個介面（畫面在原視窗重建，資料與選取都保留）。"""
        try:
            base = parse_color(color)
            for v in (self.acc1_var, self.acc2_var):
                if v.get().strip():
                    parse_color(v.get())
        except ValueError as e:
            messagebox.showerror("介面顏色", str(e), parent=self.win)
            return
        cfg = self.app.gui_settings
        cfg["theme_base"] = base
        cfg["theme_accent_1d"] = parse_color(self.acc1_var.get()) if self.acc1_var.get().strip() else ""
        cfg["theme_accent_2d"] = parse_color(self.acc2_var.get()) if self.acc2_var.get().strip() else ""
        cfg.pop("theme_preset", None)
        cfg.pop("theme_custom", None)
        if self.font_var.get() != (cfg.get("plot_cjk_font") or "自動"):
            cfg["plot_cjk_font"] = "" if self.font_var.get() == "自動" else self.font_var.get()
        ST.save_settings(cfg)
        geo = self.win.geometry()
        self.win.destroy()
        new_app = self.app.rebuild()
        new_app.status(f"已套用介面顏色 {base}", "ok")
        SettingsDialog(new_app, tab="外觀", geometry=geo)

    def _pick_base(self):
        c = self._choose(self._safe(self.base_var.get()), "選擇介面顏色")
        if c:
            self.base_var.set(c)
            self._choose_base(c)

    def _pick_into(self, var):
        c = self._choose(self._safe(var.get(), "#2563eb"), "選擇主色")
        if c:
            var.set(c)

    def _safe(self, c, fallback="#ffffff"):
        try:
            return parse_color(c)
        except ValueError:
            return fallback

    # ------------------------------------------------------------------ 數據顏色
    def _tab_colors(self, f):
        self._h(f, "預設顏色循環", "沒有個別指定顏色的數據，依序使用這些顏色（選取 10 筆以內、顏色設定為「自動」時）。"
                "點色塊用色盤改，或在下面直接編輯（HEX 或 R, G, B，用 ; 分開）。")
        self.cycle_frame = tk.Frame(f, bg=T.CARD)
        self.cycle_frame.pack(fill="x", pady=4)
        self.cycle_text = tk.StringVar()
        ent = ttk.Entry(f, textvariable=self.cycle_text)
        ent.pack(fill="x", pady=(6, 2))
        ent.bind("<Return>", lambda e: self._cycle_from_text())
        ent.bind("<FocusOut>", lambda e: self._cycle_from_text())
        br = tk.Frame(f, bg=T.CARD)
        br.pack(fill="x", pady=4)
        ttk.Button(br, text="恢復預設", style="Small.TButton", command=self._cycle_reset).pack(side="left")
        ttk.Button(br, text="套用文字", style="Small.TButton", command=self._cycle_from_text).pack(side="left", padx=6)
        self._draw_cycle()

        self._h(f, "個別數據的顏色", "在資料清單上選取 run → 按右鍵（或「● 顏色」按鈕）→ 用色盤選、輸入 HEX / RGB，"
                "或依溫度套用兩色漸層。設定會存在樣品資料夾，下次開啟還在。")
        n = len(self.app.run_colors)
        ttk.Button(f, text=f"清除這個資料庫所有自訂顏色（目前 {n} 筆）", style="Small.TButton",
                   command=self._clear_run_colors).pack(anchor="w", pady=4)

    def _draw_cycle(self):
        for w in self.cycle_frame.winfo_children():
            w.destroy()
        for i, c in enumerate(self.cycle):
            cell = tk.Frame(self.cycle_frame, bg=T.CARD)
            cell.pack(side="left", padx=3)
            self._swatch(cell, c, 5, cmd=lambda i=i: self._pick_cycle(i)).pack()
            tk.Label(cell, text=c, bg=T.CARD, fg=T.MUTED, font=T.font(8)).pack()
        self.cycle_text.set(" ; ".join(self.cycle))

    def _pick_cycle(self, i):
        c = self._choose(self.cycle[i], f"第 {i + 1} 個顏色")
        if c:
            self.cycle[i] = c
            self._draw_cycle()

    def _cycle_from_text(self):
        try:
            cyc = [parse_color(x) for x in re.split(r"[;\n]", self.cycle_text.get()) if x.strip()]
        except ValueError as e:
            messagebox.showerror("顏色", str(e), parent=self.win)
            return
        if cyc:
            self.cycle = cyc
            self._draw_cycle()

    def _cycle_reset(self):
        self.cycle = list(DEFAULT_CYCLE)
        self._draw_cycle()

    def _clear_run_colors(self):
        if self.app.run_colors and messagebox.askyesno("清除顏色", "確定清除這個資料庫所有自訂的數據顏色？",
                                                       parent=self.win):
            self.app.set_run_colors({r: None for r in list(self.app.run_colors)})

    # ------------------------------------------------------------------ 資料與輸出
    def _tab_data(self, f):
        s = self.app.settings
        self._h(f, "資料夾", "所有樣品資料夾的最上層。上方的資料庫下拉選單與資料總表會從這裡往下找 .db。")
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x")
        self.root_var = tk.StringVar(value=s.get("data_root", ""))
        ttk.Entry(row, textvariable=self.root_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="瀏覽…", style="Small.TButton", command=self._browse_root).pack(side="left", padx=6)

        self._h(f, "輸出", "「存圖」的解析度，以及批次分析輸出的圖檔格式。")
        grid = tk.Frame(f, bg=T.CARD)
        grid.pack(fill="x")
        tk.Label(grid, text="解析度 (dpi)", bg=T.CARD, fg=T.MUTED).grid(row=0, column=0, sticky="w", pady=3)
        self.dpi_var = tk.StringVar(value=str(s.get("dpi", 300)))
        ttk.Spinbox(grid, from_=72, to=1200, increment=50, width=8, textvariable=self.dpi_var).grid(row=0, column=1, padx=8)
        tk.Label(grid, text="批次圖檔格式", bg=T.CARD, fg=T.MUTED).grid(row=1, column=0, sticky="w", pady=3)
        self.fmt_var = tk.StringVar(value=s.get("fig_format", "jpg"))
        ttk.Combobox(grid, textvariable=self.fmt_var, values=["jpg", "png", "pdf", "svg"], state="readonly",
                     width=8).grid(row=1, column=1, padx=8, sticky="w")
        from ..batch.runner import settings_path
        sp = settings_path()
        self._h(f, "設定檔位置", "這些設定存在你電腦的使用者資料夾，不在工具資料夾裡：換工具資料夾、從 GitHub 更新或上傳，"
                "都不會遺失也不會被上傳。")
        row2 = tk.Frame(f, bg=T.CARD)
        row2.pack(fill="x")
        tk.Label(row2, text=sp, bg=T.CARD, fg=T.TEXT, font=T.font(9)).pack(side="left")
        ttk.Button(row2, text="開啟資料夾", style="Small.TButton",
                   command=lambda: self._open(os.path.dirname(sp))).pack(side="left", padx=8)

    def _browse_root(self):
        d = filedialog.askdirectory(title="選擇資料夾", initialdir=self.root_var.get() or os.path.expanduser("~"),
                                    parent=self.win)
        if d:
            self.root_var.set(os.path.normpath(d))

    # ------------------------------------------------------------------ 啟動與安裝
    def _tab_launch(self, f):
        self._h(f, "位置", "啟動檔（run_analysis.bat）複製到任何地方都能用：每次啟動都會記下下面的程式與 Python 位置。")
        info = tk.Frame(f, bg=T.CARD)
        info.pack(fill="x")
        for r, (lab, val) in enumerate((("程式資料夾", TOOL_ROOT), ("Python", sys.executable),
                                        ("記錄與設定", log_dir()))):
            tk.Label(info, text=lab, bg=T.CARD, fg=T.MUTED, font=T.font(9)).grid(row=r, column=0, sticky="w", pady=2)
            e = ttk.Entry(info, width=70)
            e.insert(0, val)
            e.configure(state="readonly")
            e.grid(row=r, column=1, sticky="ew", padx=8, pady=2)
        info.columnconfigure(1, weight=1)

        self._h(f, "捷徑", "在開始功能表與桌面建立「低溫量測分析」（沒有黑色視窗、有圖示、可釘選到工作列）。"
                "程式資料夾搬家後，按一次「建立 / 修復」即可。")
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="建立 / 修復捷徑", style="Ghost.TButton", command=self._install).pack(side="left")
        ttk.Button(row, text="移除捷徑", style="Ghost.TButton", command=self._uninstall).pack(side="left", padx=6)
        ttk.Button(row, text="複製啟動檔到桌面", style="Ghost.TButton", command=self._copy_bat).pack(side="left")
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text="開啟程式資料夾", style="Small.TButton",
                   command=lambda: self._open(TOOL_ROOT)).pack(side="left")
        ttk.Button(row, text="開啟記錄資料夾", style="Small.TButton",
                   command=lambda: self._open(log_dir())).pack(side="left", padx=6)

    def _capture(self, func):
        lines = []
        try:
            func(log=lines.append)
        except SystemExit as e:
            lines.append(str(e))
        except Exception as e:
            lines.append(f"失敗：{e}")
        messagebox.showinfo("捷徑", "\n".join(lines) or "完成", parent=self.win)

    def _install(self):
        from ..install import install
        self._capture(install)

    def _uninstall(self):
        from ..install import uninstall
        self._capture(uninstall)

    def _copy_bat(self):
        try:
            from ..install import _desktop_dir
            dst = os.path.join(_desktop_dir(), "run_analysis.bat")
            shutil.copy2(os.path.join(TOOL_ROOT, "run_analysis.bat"), dst)
            messagebox.showinfo("啟動檔", f"已複製到：\n{dst}\n\n這個檔案放在任何地方都能開啟選單。", parent=self.win)
        except Exception as e:
            messagebox.showerror("啟動檔", f"複製失敗：{e}", parent=self.win)

    def _open(self, path):
        try:
            os.startfile(path)
        except Exception:
            messagebox.showinfo("位置", path, parent=self.win)

    # ------------------------------------------------------------------ 更新
    def _tab_update(self, f):
        from .. import updater, __version__
        rel = updater.read_release()
        self._h(f, "版本", None)
        tk.Label(f, text=f"目前版本　v{__version__}" + (f"（{rel.get('date')}）" if rel.get("date") else ""),
                 bg=T.CARD, fg=T.TEXT, font=T.font(11)).pack(anchor="w")
        repo = rel.get("repo") or "（尚未設定）"
        tk.Label(f, text=f"更新來源　github.com/{repo}　分支 {rel.get('branch', 'main')}", bg=T.CARD,
                 fg=T.MUTED).pack(anchor="w", pady=(2, 8))
        self.auto_upd = tk.BooleanVar(value=self.app.gui_settings.get("auto_update_check", True))
        ttk.Checkbutton(f, text="開啟軟體時自動檢查更新", variable=self.auto_upd).pack(anchor="w")
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x", pady=8)
        ttk.Button(row, text="立即檢查更新", style="Ghost.TButton",
                   command=lambda: self.app.check_update(manual=True)).pack(side="left")
        ttk.Button(row, text="還原上一版", style="Ghost.TButton", command=self._rollback).pack(side="left", padx=6)
        if updater.is_dev_folder():
            tk.Label(f, text="這是開發用資料夾（git），不會自動更新；發佈新版請用：\n"
                     "python -m lta release 2.4.0 -n \"更新說明\" --push", bg=T.CARD, fg=T.WARNING,
                     justify="left").pack(anchor="w", pady=(6, 0))
        self._h(f, "這一版的更新內容", None)
        for n in rel.get("notes", []):
            tk.Label(f, text="‧ " + n, bg=T.CARD, fg=T.TEXT, anchor="w", justify="left",
                     wraplength=700).pack(fill="x")
        tk.Label(f, text="\n更新時會先把目前的程式備份到工具資料夾的 _backup\\（保留最近 3 份）；"
                 "settings.toml、jobs 裡的設定檔、資料總表都不會被覆蓋。", bg=T.CARD, fg=T.FAINT,
                 font=T.font(9), wraplength=700, justify="left").pack(anchor="w")

    def _rollback(self):
        from .. import updater
        backups = updater.list_backups()
        if not backups:
            messagebox.showinfo("還原", "沒有可以還原的備份", parent=self.win)
            return
        name = os.path.basename(backups[0])
        if not messagebox.askyesno("還原", f"還原到 {name}？還原後會重新開啟軟體。", parent=self.win):
            return
        try:
            updater.rollback()
        except Exception as e:
            messagebox.showerror("還原", str(e), parent=self.win)
            return
        self.win.destroy()
        self.app.restart()

    # ------------------------------------------------------------------ 關於
    def _tab_about(self, f):
        from .. import __version__
        tk.Label(f, text="低溫量測分析", bg=T.CARD, fg=T.TEXT, font=T.font(18, "bold")).pack(anchor="w")
        tk.Label(f, text=f"Low-Temperature Analysis　版本 {__version__}", bg=T.CARD, fg=T.MUTED).pack(anchor="w")
        tk.Label(f, text="\n程式是工具資料夾裡的 Python 原始檔（lta\\），修改後重新開啟即生效。\n"
                 "新增分析功能、新的量測方式請看工具資料夾裡的「擴充指南.md」。", bg=T.CARD, fg=T.TEXT,
                 justify="left").pack(anchor="w")

    # ------------------------------------------------------------------ 儲存
    def save(self, restart=False):
        cfg = self.app.gui_settings
        old_font = cfg.get("plot_cjk_font") or ""
        if hasattr(self, "font_var"):
            cfg["plot_cjk_font"] = "" if self.font_var.get() == "自動" else self.font_var.get()
        self._cycle_from_text()
        cfg["cycle"] = list(self.cycle)
        if hasattr(self, "auto_upd"):
            cfg["auto_update_check"] = bool(self.auto_upd.get())
        ST.save_settings(cfg)

        # settings.toml
        from ..batch.runner import settings_path
        path = settings_path()
        try:
            root_dir = self.root_var.get().strip()
            if "'" in root_dir:
                raise ValueError("資料夾路徑不能包含單引號 '")
            dpi = int(float(self.dpi_var.get()))
            update_toml_value(path, "data_root", f"'{root_dir}'")
            update_toml_value(path, "dpi", str(dpi))
            update_toml_value(path, "fig_format", f'"{self.fmt_var.get()}"')
            changed_root = root_dir != self.app.settings.get("data_root")
            self.app.settings.update(data_root=root_dir, dpi=dpi, fig_format=self.fmt_var.get())
        except (ValueError, OSError) as e:
            messagebox.showerror("設定", f"無法寫入 settings.toml：{e}", parent=self.win)
            return

        # 立即生效：數據顏色循環、圖中字型
        T.PLOT_CYCLE = list(cfg["cycle"])
        T.PLOT_CJK_FONT = cfg.get("plot_cjk_font") or ""
        T.apply_mpl_style()
        if changed_root:
            self.app._scan_dbs()
        self.app._schedule_plot(delay=50)
        self.win.destroy()
        if restart:
            self.app.restart()
        else:
            self.app.status("設定已儲存" + ("（字型已更換）" if old_font != (cfg.get("plot_cjk_font") or "") else ""), "ok")
