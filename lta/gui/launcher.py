"""軟體啟動器：先顯示載入畫面，再載入 numpy / matplotlib / 分析功能，最後打開主視窗。

開始功能表與桌面捷徑執行的是：
    pythonw.exe -m lta app        （pythonw = 沒有黑色命令列視窗）

這個檔案刻意只在最上面匯入標準函式庫，讓載入畫面能在一秒內出現。
"""
from __future__ import annotations

import os
import sys
import time
import traceback

APP_ID = "Yeep.LowTempAnalysis"
APP_NAME = "低溫量測分析"
TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ASSETS = os.path.join(TOOL_ROOT, "assets")


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(TOOL_ROOT, "logs")
    path = os.path.join(base, "LowTempAnalysis") if os.environ.get("LOCALAPPDATA") else base
    os.makedirs(path, exist_ok=True)
    return path


def register_paths():
    """記下程式資料夾與 Python 的位置，讓任何地方的啟動檔（複製出去的 run_analysis.bat、捷徑）都找得到。
    檔案：%LOCALAPPDATA%\\LowTempAnalysis\\tool_path.txt、python_path.txt（系統預設編碼，cmd 讀得懂）"""
    try:
        d = log_dir()
        enc = "mbcs" if os.name == "nt" else "utf-8"
        exe_dir = os.path.dirname(sys.executable)
        py = os.path.join(exe_dir, "python.exe") if os.name == "nt" else sys.executable
        if not os.path.exists(py):
            py = sys.executable
        for name, value in (("tool_path.txt", TOOL_ROOT), ("python_path.txt", py)):
            with open(os.path.join(d, name), "w", encoding=enc, errors="replace") as f:
                f.write(value + "\n")
    except Exception:
        pass


def setup_environment():
    """1) pythonw 沒有 stdout/stderr → 導到 log 檔；
    2) 沒有先「啟用」conda 環境時，補上 DLL 搜尋路徑（numpy、scipy、Tk 需要）；
    3) Windows：高 DPI 不模糊、工作列使用本軟體的圖示。"""
    if sys.stdout is None or sys.stderr is None or os.path.basename(sys.executable).lower() == "pythonw.exe":
        try:
            f = open(os.path.join(log_dir(), "app.log"), "a", encoding="utf-8", buffering=1)
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}  {sys.executable}\n")
            sys.stdout = sys.stderr = f
        except OSError:
            pass
    if os.name == "nt":
        prefix = sys.prefix
        extra = [prefix, os.path.join(prefix, "Library", "mingw-w64", "bin"),
                 os.path.join(prefix, "Library", "usr", "bin"), os.path.join(prefix, "Library", "bin"),
                 os.path.join(prefix, "Scripts"), os.path.join(prefix, "bin")]
        extra = [p for p in extra if os.path.isdir(p)]
        os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])
        for p in extra:
            try:
                os.add_dll_directory(p)
            except (OSError, AttributeError):
                pass
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass
    if TOOL_ROOT not in sys.path:
        sys.path.insert(0, TOOL_ROOT)


def fatal(msg):
    """連 Tk 都起不來時，用 Windows 原生訊息框。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, APP_NAME, 0x10)
    except Exception:
        print(msg, file=sys.stderr)


class Splash:
    """啟動載入畫面。"""
    W, H = 560, 300

    def __init__(self, root):
        import tkinter as tk
        self.tk = tk
        self.win = win = tk.Toplevel(root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{self.W}x{self.H}+{(sw - self.W) // 2}+{(sh - self.H) // 2}")
        c = self.c = tk.Canvas(win, width=self.W, height=self.H, highlightthickness=0, bd=0, bg="#18223d")
        c.pack(fill="both", expand=True)
        # 背景：深藍 → 紫 的斜向色帶
        for i in range(0, self.W, 4):
            t = i / self.W
            r = int(0x18 + (0x2b - 0x18) * t)
            g = int(0x22 + (0x1d - 0x22) * t)
            b = int(0x3d + (0x5c - 0x3d) * t)
            c.create_rectangle(i, 0, i + 4, self.H, fill=f"#{r:02x}{g:02x}{b:02x}", width=0)
        self.img = None
        try:
            self.img = tk.PhotoImage(file=os.path.join(ASSETS, "icon_128.png"))
            c.create_image(110, 128, image=self.img)
        except Exception:
            c.create_text(110, 128, text="◢", fill="#2563eb", font=("Segoe UI", 60, "bold"))
        fam = self._font_family()
        c.create_text(205, 98, text=APP_NAME, anchor="w", fill="white", font=(fam, 24, "bold"))
        c.create_text(207, 136, text="Low-Temperature Analysis", anchor="w", fill="#9fb0d6", font=(fam, 11))
        try:
            from .. import __version__
        except Exception:
            __version__ = ""
        c.create_text(207, 162, text=f"版本 {__version__}", anchor="w", fill="#6f80a8", font=(fam, 9))
        self.msg = c.create_text(207, 214, text="啟動中…", anchor="w", fill="#cdd6ee", font=(fam, 10))
        c.create_rectangle(207, 236, 520, 240, fill="#2a3760", width=0)
        self.bar = c.create_rectangle(207, 236, 207, 240, fill="#5eead4", width=0)
        win.update()

    def _font_family(self):
        try:
            from tkinter import font as tkfont
            fams = set(tkfont.families())
            for f in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC"):
                if f in fams:
                    return f
        except Exception:
            pass
        return "TkDefaultFont"

    def step(self, text, frac):
        self.c.itemconfigure(self.msg, text=text)
        self.c.coords(self.bar, 207, 236, 207 + int(313 * frac), 240)
        self.win.update()

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def main(db_path=None):
    setup_environment()
    try:
        import tkinter as tk
    except Exception as e:
        fatal(f"無法載入 tkinter：{e}\n\n請確認使用的是 Anaconda 的 Python。")
        return 1

    root = tk.Tk()
    root.withdraw()
    root.title(APP_NAME)
    try:
        if os.name == "nt":
            root.iconbitmap(default=os.path.join(ASSETS, "icon.ico"))
        root._lta_icon = tk.PhotoImage(file=os.path.join(ASSETS, "icon_256.png"))
        root.iconphoto(True, root._lta_icon)
    except Exception:
        pass
    splash = Splash(root)
    t0 = time.time()
    try:
        splash.step("載入數值計算（numpy、scipy）…", 0.15)
        import numpy  # noqa: F401
        import scipy.signal  # noqa: F401
        splash.step("載入繪圖（matplotlib）…", 0.40)
        import matplotlib
        matplotlib.use("TkAgg")
        from matplotlib.backends import backend_tkagg  # noqa: F401
        register_paths()                 # 套件都載入成功才記下這個 Python
        splash.step("載入分析功能…", 0.65)
        from .app import App, prepare
        prepare(root)
        splash.step("建立視窗…", 0.85)
        app = App(root, db_path)
        splash.step("完成", 1.0)
        # 載入畫面至少停留一下，避免一閃而過
        remain = 0.6 - (time.time() - t0)
        if remain > 0:
            time.sleep(remain)
    except Exception:
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        splash.close()
        log = os.path.join(log_dir(), "error.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{tb}")
        except OSError:
            pass
        from tkinter import messagebox
        messagebox.showerror(APP_NAME, f"啟動失敗：\n\n{tb.strip().splitlines()[-1]}\n\n詳細記錄：{log}")
        root.destroy()
        return 1
    splash.close()
    root.deiconify()
    root.lift()
    try:
        root.state("zoomed")            # Windows：最大化開啟
    except Exception:
        pass
    root.focus_force()
    root.report_callback_exception = _report_callback(root)
    root.mainloop()
    return 0


def _report_callback(root):
    """介面操作中發生未預期錯誤時：寫 log + 跳出提示，不會整個關掉。"""
    def handler(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        print(text, file=sys.stderr)
        log = os.path.join(log_dir(), "error.log")
        try:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}")
        except OSError:
            pass
        try:
            from tkinter import messagebox
            messagebox.showerror(APP_NAME, f"發生錯誤：{val}\n\n詳細記錄：{log}", parent=root)
        except Exception:
            pass
    return handler


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
