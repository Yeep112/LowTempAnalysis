"""⚙ 設定視窗：介面配色、預設數據顏色、資料與輸出、啟動與安裝、關於。

設定存在 %LOCALAPPDATA%\\LowTempAnalysis\\gui_settings.json；
資料夾與輸出格式寫回工具資料夾的 settings.toml（批次分析也會用）。
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
from .colors import THEME_PRESETS, DEFAULT_CYCLE, parse_color
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
    def __init__(self, app):
        self.app = app
        self.cfg = dict(app.gui_settings)
        self.win = win = tk.Toplevel(app.root)
        win.title("設定")
        win.geometry("780x600")
        win.configure(bg=T.BG)
        win.transient(app.root)
        self.theme_var = tk.StringVar(value=self.cfg.get("theme_preset", "藍紫（預設）"))
        custom = {**THEME_PRESETS["藍紫（預設）"], **(self.cfg.get("theme_custom") or {})}
        self.custom_vars = {k: tk.StringVar(value=v) for k, v in custom.items()}
        self.cycle = list(self.cfg.get("cycle") or T.PLOT_CYCLE)

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        for title, builder in (("外觀", self._tab_theme), ("數據顏色", self._tab_colors),
                               ("資料與輸出", self._tab_data), ("啟動與安裝", self._tab_launch),
                               ("更新", self._tab_update), ("關於", self._tab_about)):
            fr = tk.Frame(nb, bg=T.CARD, padx=16, pady=12)
            nb.add(fr, text=f"  {title}  ")
            builder(fr)

        bf = tk.Frame(win, bg=T.BG)
        bf.pack(fill="x", padx=10, pady=(4, 10))
        ttk.Button(bf, text="取消", style="Ghost.TButton", command=win.destroy).pack(side="right")
        ttk.Button(bf, text="儲存", style="Ghost.TButton", command=self.save).pack(side="right", padx=6)
        ttk.Button(bf, text="儲存並重新開啟", style="Accent.TButton",
                   command=lambda: self.save(restart=True)).pack(side="right")
        tk.Label(bf, text="介面配色需要重新開啟才會套用；其他設定按「儲存」立即生效。", bg=T.BG,
                 fg=T.MUTED, font=T.font(9)).pack(side="left")

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
        self._h(f, "介面配色", "選一組配色，或選「自訂」自己挑顏色（可輸入 HEX 或 R, G, B，也可以點色塊用色盤選）。")
        for name, th in THEME_PRESETS.items():
            row = tk.Frame(f, bg=T.CARD)
            row.pack(fill="x", pady=2)
            ttk.Radiobutton(row, text=name, value=name, variable=self.theme_var).pack(side="left")
            sw = tk.Frame(row, bg=T.CARD)
            sw.pack(side="left", padx=12)
            for key in ("header", "bg", "accent_1d", "accent_2d"):
                self._swatch(sw, th[key], 4).pack(side="left", padx=2)
        row = tk.Frame(f, bg=T.CARD)
        row.pack(fill="x", pady=(10, 2))
        ttk.Radiobutton(row, text="自訂", value="自訂", variable=self.theme_var).pack(side="left")
        grid = tk.Frame(f, bg=T.CARD)
        grid.pack(fill="x", padx=24)
        labels = {"header": "標題列", "bg": "背景", "accent_1d": "1D 主色", "accent_2d": "2D 主色"}
        self._custom_sw = {}
        for r, (key, lab) in enumerate(labels.items()):
            tk.Label(grid, text=lab, bg=T.CARD, fg=T.MUTED, font=T.font(9)).grid(row=r, column=0, sticky="w", pady=2)
            ent = ttk.Entry(grid, textvariable=self.custom_vars[key], width=16)
            ent.grid(row=r, column=1, padx=8, pady=2)
            sw = self._swatch(grid, self._safe(self.custom_vars[key].get()), 5,
                              cmd=lambda k=key: self._pick_custom(k))
            sw.grid(row=r, column=2, pady=2)
            self._custom_sw[key] = sw
            self.custom_vars[key].trace_add("write", lambda *a, k=key: self._refresh_custom(k))
        tk.Label(f, text="提示：1D、2D 主色分別用在兩個分頁的按鈕、選取色與標題圖示。", bg=T.CARD,
                 fg=T.FAINT, font=T.font(9)).pack(anchor="w", pady=(12, 0))

    def _safe(self, c, fallback="#ffffff"):
        try:
            return parse_color(c)
        except ValueError:
            return fallback

    def _pick_custom(self, key):
        c = self._choose(self._safe(self.custom_vars[key].get()), "選擇顏色")
        if c:
            self.custom_vars[key].set(c)
            self.theme_var.set("自訂")

    def _refresh_custom(self, key):
        try:
            self._custom_sw[key].configure(bg=parse_color(self.custom_vars[key].get()))
        except ValueError:
            pass

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
        old_theme = (cfg.get("theme_preset"), dict(cfg.get("theme_custom") or {}))
        preset = self.theme_var.get()
        custom = {}
        if preset == "自訂":
            try:
                custom = {k: parse_color(v.get()) for k, v in self.custom_vars.items()}
            except ValueError as e:
                messagebox.showerror("自訂配色", str(e), parent=self.win)
                return
        cfg["theme_preset"] = preset
        cfg["theme_custom"] = custom
        self._cycle_from_text()
        cfg["cycle"] = list(self.cycle)
        if hasattr(self, "auto_upd"):
            cfg["auto_update_check"] = bool(self.auto_upd.get())
        ST.save_settings(cfg)

        # settings.toml
        path = os.path.join(TOOL_ROOT, "settings.toml")
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

        # 立即生效：數據顏色循環
        T.apply_theme({"cycle": cfg["cycle"], "theme_preset": old_theme[0],
                       "theme_custom": old_theme[1]})
        T.apply_mpl_style()
        if changed_root:
            self.app._scan_dbs()
        self.app._schedule_plot(delay=50)
        new_theme = (preset, custom)
        self.win.destroy()
        if restart:
            self.app.restart()
        elif new_theme != old_theme:
            if messagebox.askyesno("設定", "介面配色要重新開啟才會套用，現在重新開啟嗎？", parent=self.app.root):
                self.app.restart()
        else:
            self.app.status("設定已儲存", "ok")
