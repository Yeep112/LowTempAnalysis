"""📦 打包錯誤記錄：把 error.log、crash.log、app.log 與環境資訊壓成一個 zip，方便傳給維護者。

    python -m lta report            產生在桌面（找不到桌面就放在記錄資料夾）

不會包含任何 .db 量測資料；只有記錄檔、設定檔（資料夾路徑、介面設定）與軟體 / Python 版本。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import zipfile

LOGS = ("error.log", "crash.log", "app.log")
TAIL_BYTES = 1_000_000           # 每個記錄檔只取最後 1 MB


def default_dir():
    """桌面（含 OneDrive 桌面）；都找不到就用記錄資料夾。"""
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    cands = [os.path.join(home, "Desktop"), os.path.join(home, "OneDrive", "Desktop"),
             os.path.join(home, "OneDrive", "桌面"), os.path.join(home, "桌面")]
    if os.environ.get("OneDrive"):
        cands.insert(1, os.path.join(os.environ["OneDrive"], "Desktop"))
    for c in cands:
        if os.path.isdir(c):
            return c
    from .gui.launcher import log_dir
    return log_dir()


def default_name():
    return time.strftime("LowTempAnalysis_錯誤記錄_%Y%m%d_%H%M.zip")


def _tail(path, n=TAIL_BYTES):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > n:
            f.seek(size - n)
            data = f.read()
            data = b"(... \xe5\x89\x8d\xe9\x9d\xa2\xe7\x9c\x81\xe7\x95\xa5 ...)\n" + data.split(b"\n", 1)[-1]
        else:
            data = f.read()
    return data


def environment(extra=None):
    """環境資訊（純文字）。不 import tkinter / 不切換 matplotlib backend，介面裡也能安全呼叫。"""
    from . import __version__
    from .gui.launcher import TOOL_ROOT, log_dir
    lines = [f"產生時間      {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"lta 版本      {__version__}"]
    try:
        from . import updater
        c = updater.installed_commit()
        lines.append(f"安裝的 commit {c or '（未記錄）'}{'　（開發資料夾）' if updater.is_dev_folder() else ''}")
    except Exception as e:
        lines.append(f"安裝的 commit 讀取失敗：{e}")
    lines += [f"程式資料夾    {TOOL_ROOT}",
              f"記錄資料夾    {log_dir()}",
              f"Python        {sys.version.split()[0]}  ({sys.executable})",
              f"作業系統      {platform.platform()}"]
    for mod in ("numpy", "scipy", "matplotlib", "openpyxl"):
        m = sys.modules.get(mod)
        if m is None:
            try:
                m = __import__(mod)
            except Exception as e:
                lines.append(f"  ✗ {mod:<11} {e}")
                continue
        lines.append(f"  ✓ {mod:<11} {getattr(m, '__version__', '')}")
    tkm = sys.modules.get("tkinter")
    if tkm is not None:
        lines.append(f"  ✓ tkinter     Tk {getattr(tkm, 'TkVersion', '?')}")
    th = sys.modules.get("lta.gui.theme")
    if th is not None:
        lines.append(f"介面大小      {getattr(th, 'UI_FACTOR', '?')}　螢幕 {getattr(th, 'SCREEN', {})}")
        lines.append(f"圖中中文字型  {getattr(th, 'PLOT_CJK_FONT', '') or '自動'}")
    if extra:
        lines.append("")
        lines.append("== 回報時的軟體狀態 ==")
        for k, v in extra.items():
            lines.append(f"{k:<12}  {v}")
    return "\n".join(lines) + "\n"


def make_report(dest=None, extra=None, note=""):
    """建立 zip，回傳 (路徑, 放進去的檔名清單)。"""
    from .gui.launcher import log_dir
    dest = dest or os.path.join(default_dir(), default_name())
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    added = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        info = environment(extra)
        if note:
            info = f"== 使用者說明 ==\n{note.strip()}\n\n" + info
        z.writestr("環境資訊.txt", info.encode("utf-8"))
        added.append("環境資訊.txt")
        ld = log_dir()
        for name in LOGS:
            p = os.path.join(ld, name)
            if os.path.isfile(p):
                try:
                    z.writestr(name, _tail(p))
                    added.append(name)
                except OSError:
                    pass
        try:
            from .gui.style import settings_path as gui_path
            if os.path.isfile(gui_path()):
                z.write(gui_path(), "gui_settings.json")
                added.append("gui_settings.json")
        except Exception:
            pass
        try:
            from .batch.runner import settings_path
            if os.path.isfile(settings_path()):
                z.write(settings_path(), "settings.toml")
                added.append("settings.toml")
        except Exception:
            pass
        z.writestr("檔案清單.json", json.dumps(added, ensure_ascii=False).encode("utf-8"))
    return dest, added


def reveal(path):
    """在檔案總管中選取這個檔案（Windows）；其他系統開啟資料夾。"""
    try:
        if os.name == "nt":
            import subprocess
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            return False
        return True
    except Exception:
        return False
