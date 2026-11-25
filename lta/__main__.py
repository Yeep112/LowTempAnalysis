"""命令列入口。通常從 run_analysis.bat 的選單呼叫，也可以直接打：

  python -m lta gui [db]             開啟分析介面（1D / 2D）
  python -m lta app [db]             以軟體模式開啟（載入畫面 → 分析介面）
  python -m lta install / uninstall  安裝 / 移除 開始功能表與桌面捷徑
  python -m lta catalog              更新資料總表（掃描 data_root 下所有 .db）
  python -m lta list [db]            列出 db 裡所有 run（不給 db = 最新的 db）
  python -m lta quick [db] [run_id]  快速看一個 run（不給 = 最新 db 的最後一個 run）
  python -m lta run [job.toml]       執行分析設定檔（不給 = 從 jobs\\ 選）
  python -m lta run-all              執行 jobs\\ 底下所有設定檔
  python -m lta new [db]             幫一個 db 產生新的分析設定檔
  python -m lta types                列出所有分析功能
"""
from __future__ import annotations

import sys

# 軟體模式（開始功能表 / 桌面捷徑）：先出現載入畫面，再載入其他東西
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] in ("app", "gui"):
    from .gui.launcher import main as _launch
    sys.exit(_launch(sys.argv[2].strip('"') if len(sys.argv) > 2 else None))

import argparse
import glob
import os
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .batch.runner import TOOL_ROOT, load_settings

JOBS_DIR = os.path.join(TOOL_ROOT, "jobs")


def _set_backend(show):
    import matplotlib
    if not show:
        matplotlib.use("Agg")


def _strip(p):
    return p.strip().strip('"').strip("'") if p else p


def _pick_db(arg, settings):
    from .catalog import newest_db
    if arg:
        arg = _strip(arg)
        if os.path.isfile(arg):
            return arg
        from .batch.runner import resolve_db
        return resolve_db(arg, os.path.join(JOBS_DIR, "_"), settings)
    root = settings.get("data_root")
    if not root or not os.path.isdir(root):
        raise SystemExit("settings.toml 的 data_root 不存在，請指定 db 路徑")
    path = newest_db(root)
    print(f"（使用最新的資料庫）{path}")
    return path


def _ask(prompt, default=""):
    try:
        ans = input(prompt).strip()
    except EOFError:
        ans = ""
    return _strip(ans) or default


def _job_files():
    return sorted(f for f in glob.glob(os.path.join(JOBS_DIR, "*.toml"))
                  if not os.path.basename(f).startswith("_"))


def cmd_catalog(a, settings):
    from .catalog import build
    root = _strip(a.root) or settings.get("data_root")
    if not root or not os.path.isdir(root):
        raise SystemExit(f"資料夾不存在：{root}（請改 settings.toml 的 data_root）")
    build(root, TOOL_ROOT)
    xlsx = os.path.join(TOOL_ROOT, "catalog.xlsx")
    if getattr(a, "open", False) and os.path.exists(xlsx) and sys.platform.startswith("win"):
        os.startfile(xlsx)
    if getattr(a, "pause", False):
        _ask("\n完成，按 Enter 關閉視窗…")


def cmd_list(a, settings):
    from .core.db import QDB
    db = QDB(_pick_db(a.db, settings))
    runs = db.runs()
    if a.filter:
        runs = [r for r in runs if a.filter in r.exp_name]
    print(db.table(runs))


def cmd_quick(a, settings):
    _set_backend(not a.no_show)
    from .core.db import QDB
    from .quick import quick_view
    db_arg, run = a.db, a.run_id
    if db_arg and db_arg.isdigit() and run is None and not os.path.exists(db_arg):
        db_arg, run = None, db_arg            # 只給 run_id
    db = QDB(_pick_db(db_arg, settings))
    if run is None and a.ask:
        print(db.table(db.runs()[-15:]))
        run = _ask("run_id（Enter = 最後一個）：") or None
    quick_view(db, int(run) if run else None, show=not a.no_show)


def cmd_run(a, settings):
    _set_backend(a.show)
    from .batch.runner import run_job_file
    job = _strip(a.job)
    if not job:
        files = _job_files()
        if not files:
            raise SystemExit(f"{JOBS_DIR} 裡沒有設定檔，先用「new」建立一個")
        for i, f in enumerate(files, 1):
            print(f"  {i}. {os.path.basename(f)}")
        k = _ask("選擇設定檔編號：")
        if not k.isdigit() or not 1 <= int(k) <= len(files):
            raise SystemExit("取消")
        job = files[int(k) - 1]
    elif not os.path.isfile(job) and os.path.isfile(os.path.join(JOBS_DIR, job)):
        job = os.path.join(JOBS_DIR, job)
    index, _ = run_job_file(os.path.abspath(job), only=a.only, show=a.show, out_override=a.out)
    if a.open and sys.platform.startswith("win"):
        os.startfile(index)
    if getattr(a, "pause", False):
        _ask("\n完成，按 Enter 關閉視窗…")


def cmd_run_all(a, settings):
    _set_backend(False)
    from .batch.runner import run_job_file
    files = [f for f in _job_files() if not os.path.basename(f).startswith("_")]
    if not files:
        raise SystemExit(f"{JOBS_DIR} 裡沒有設定檔")
    for f in files:
        print("\n" + "#" * 70)
        try:
            run_job_file(f)
        except SystemExit as e:
            print(f"✗ {os.path.basename(f)}: {e}")
    if getattr(a, "pause", False):
        _ask("\n完成，按 Enter 關閉視窗…")


def cmd_gui(a, settings):
    from .gui.launcher import main as launch
    launch(_strip(a.db) if a.db else None)


def cmd_install(a, settings):
    from .install import install
    install()


def cmd_uninstall(a, settings):
    from .install import uninstall
    uninstall()


def cmd_diag(a, settings):
    """環境檢查：給別台電腦排除問題用。"""
    import platform
    print(f"lta 版本      {__import__('lta').__version__}")
    print(f"程式資料夾    {TOOL_ROOT}")
    print(f"Python        {sys.version.split()[0]}  ({sys.executable})")
    print(f"作業系統      {platform.platform()}")
    for mod in ("numpy", "scipy", "matplotlib", "openpyxl"):
        try:
            m = __import__(mod)
            print(f"  ✓ {mod:<11} {getattr(m, '__version__', '')}")
        except Exception as e:
            print(f"  ✗ {mod:<11} {e}")
    try:
        import tkinter
        print(f"  ✓ tkinter     Tk {tkinter.TkVersion}")
    except Exception as e:
        print(f"  ✗ tkinter     {e}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        from .gui import theme as T
        from .gui.style import load_settings as gui_settings
        T.PLOT_CJK_FONT = gui_settings().get("plot_cjk_font") or ""
        T.apply_mpl_style()
        cands = T.cjk_font_candidates()
        print(f"圖中中文字型  {T.current_cjk_font() or '（找不到 → 圖上的中文會變方框）'}"
              f"　可用：{', '.join(cands[:6]) or '無'}")
    except Exception as e:
        print(f"圖中中文字型  檢查失敗：{e}")
    from .batch.runner import settings_path
    print(f"設定檔        {settings_path()}")
    root = settings.get("data_root")
    print(f"data_root     {root}  {'（存在）' if root and os.path.isdir(root) else '（不存在 → 在軟體的 ⚙ 設定裡改）'}")
    from .gui.launcher import log_dir
    print(f"記錄資料夾    {log_dir()}")
    try:
        from . import updater
        r = updater.read_release()
        print(f"更新來源      {r.get('repo') or '（未設定）'}  branch {r.get('branch', 'main')}")
    except Exception as e:
        print(f"更新來源      讀取失敗：{e}")


def cmd_update(a, settings):
    from . import updater
    info = updater.check()
    print(f"目前版本 {info['current']}，最新版本 {info['latest']}")
    if not info["newer"]:
        print("已經是最新版本")
        return
    for n in info.get("notes", []):
        print("  ‧ " + n)
    if a.yes or _ask("要更新嗎？(y/N) ").lower().startswith("y"):
        updater.apply_update(info)
        print("更新完成，重新開啟軟體即可使用新版本")


def cmd_release(a, settings):
    from . import updater
    updater.release(version=a.version, notes=a.notes, repo=a.repo, branch=a.branch, push=a.push)


def cmd_types(a, settings):
    from .batch.jobs import load_all
    for name, func in sorted(load_all().items()):
        print(f"  {name:<16} {func.help_text}")


def cmd_new(a, settings):
    from .core.db import QDB
    db = QDB(_pick_db(a.db, settings))
    runs = db.runs()
    sample = os.path.basename(db.folder) or os.path.splitext(db.name)[0]
    name = _strip(a.name) or _ask(f"設定檔名稱（Enter = {sample}）：", sample)
    path = os.path.join(JOBS_DIR, name + ".toml")
    if os.path.exists(path):
        raise SystemExit(f"已存在：{path}")
    with_T = [r for r in runs if r.T_K is not None and not r.is_2d and r.contacts]
    contacts = Counter(r.contacts for r in with_T).most_common(1)
    contacts = contacts[0][0] if contacts else ""
    Ts = sorted({r.T_K for r in with_T if r.contacts == contacts})
    Trange = f"[{Ts[0]:g}, {Ts[-1]:g}]" if Ts else "[1.6, 3.0]"
    two_d = [r for r in runs if r.is_2d]
    last2d = two_d[-1].run_id if two_d else 0
    with open(os.path.join(TOOL_ROOT, "jobs", "_template.toml"), encoding="utf-8") as f:
        text = f.read()
    text = (text.replace("{{DB}}", db.path).replace("{{CONTACTS}}", contacts)
                .replace("{{TRANGE}}", Trange).replace("{{RUN2D}}", str(last2d))
                .replace("{{HAS2D}}", "true" if last2d else "false")
                .replace("{{LASTRUN}}", str(runs[-1].run_id if runs else 1)))
    summary = "\n".join("# " + line for line in db.table().splitlines())
    text += "\n\n# ---- 這個 db 的 run 一覽（建立時） ----\n" + summary + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"已建立 {path}")
    if sys.platform.startswith("win"):
        os.startfile(path)


MENU = """
============================================================
   低溫量測分析工具  Low-Temperature Analysis
============================================================
   1. 開啟分析軟體（獨立視窗，關掉這個選單也沒關係）
   2. 快速查看指定 run
   3. 列出 .db 裡的所有 run
   4. 批次：執行分析設定檔（jobs\\*.toml）
   5. 批次：執行全部分析設定檔
   6. 批次：互動模式執行（開視窗）
   7. 建立新樣品的分析設定檔
   8. 更新資料總表（掃描所有 .db → catalog.xlsx）
   9. 開啟 jobs 資料夾 / settings.toml
   S. 安裝成軟體（開始功能表 + 桌面圖示，之後不用開這個視窗）
   J. 開啟 Jupyter Lab
   0. 離開
------------------------------------------------------------
   小技巧：直接把 .db 或 .toml 拖進這個視窗再按 Enter 也可以
============================================================"""


def spawn_gui(*extra):
    """以獨立程序開啟軟體（pythonw、不綁定這個命令列視窗：關掉選單視窗軟體也不會關）。"""
    import subprocess
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0    # DETACHED_PROCESS | NEW_PROCESS_GROUP
    subprocess.Popen([exe, "-m", "lta", "app", *extra], cwd=TOOL_ROOT, creationflags=flags,
                     close_fds=True, start_new_session=(os.name != "nt"))
    print("已開啟軟體（這個視窗可以關掉，不影響軟體）")


def _sub(*args):
    """每個動作用獨立的 python 執行，matplotlib 視窗與記憶體互不影響。"""
    import subprocess
    return subprocess.call([sys.executable, "-m", "lta", *args])


def _startfile(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    else:
        print(f"（請手動開啟）{path}")


def _open_path(path):
    """依副檔名處理拖進來的檔案。"""
    path = _strip(path)
    ext = os.path.splitext(path)[1].lower()
    if not os.path.exists(path):
        print(f"找不到檔案：{path}")
    elif ext == ".toml":
        _sub("run", path, "--open")
    elif ext == ".db":
        spawn_gui(path)
    else:
        print(f"不支援的檔案類型：{path}（請拖 .db 或 .toml）")


def _quick_ask(db_arg=None):
    """在選單這一層列出最近的 run 並詢問 run_id，再交給 quick 畫圖。"""
    from .core.db import QDB
    try:
        db = QDB(_pick_db(db_arg, load_settings()))
    except (SystemExit, FileNotFoundError) as e:
        print(e)
        return
    print(db.table(db.runs()[-15:]))
    run = _ask("run_id（Enter = 最後一個）：")
    _sub("quick", db.path, *([run] if run else []))


def _ask_db():
    print("\n把 .db 拖進這個視窗後按 Enter；直接按 Enter = 使用最新的 .db")
    return _ask(".db 路徑：")


def cmd_open(a, settings):
    _open_path(a.path)


def cmd_menu(a, settings):
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print(MENU)
        c = _ask("請選擇：")
        if c in ("0", "q", "Q"):
            return
        if not c:
            continue
        if os.path.splitext(c)[1].lower() in (".db", ".toml") or os.path.exists(c):
            _open_path(c)
        elif c == "1":
            spawn_gui()
            continue
        elif c == "2":
            _quick_ask(_ask_db() or None)
        elif c == "3":
            db = _ask_db()
            flt = _ask("只顯示名稱包含（可空白，例如 T_1.6K）：")
            _sub("list", *([db] if db else []), *(["--filter", flt] if flt else []))
        elif c == "4":
            _sub("run", "--open")
        elif c == "5":
            _sub("run-all")
        elif c == "6":
            _sub("run", "--show")
        elif c == "7":
            db = _ask_db()
            _sub("new", *([db] if db else []))
        elif c == "8":
            if _sub("catalog") == 0 and os.path.exists(os.path.join(TOOL_ROOT, "catalog.xlsx")):
                _startfile(os.path.join(TOOL_ROOT, "catalog.xlsx"))
        elif c == "9":
            _startfile(JOBS_DIR)
            from .batch.runner import settings_path
            _startfile(settings_path())
            continue
        elif c in ("s", "S"):
            _sub("install")
        elif c in ("j", "J"):
            import subprocess
            subprocess.Popen([sys.executable, "-m", "jupyter", "lab", f"--notebook-dir={TOOL_ROOT}"])
            continue
        else:
            print(f"沒有這個選項：{c}")
        _ask("\n按 Enter 回到選單...")


def main(argv=None):
    p = argparse.ArgumentParser(prog="lta", description="Low-temperature transport analysis")
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("catalog"); s.add_argument("root", nargs="?")
    s.add_argument("--open", action="store_true"); s.add_argument("--pause", action="store_true")
    s = sub.add_parser("list"); s.add_argument("db", nargs="?"); s.add_argument("--filter")
    s = sub.add_parser("quick"); s.add_argument("db", nargs="?"); s.add_argument("run_id", nargs="?")
    s.add_argument("--no-show", action="store_true"); s.add_argument("--ask", action="store_true")
    s = sub.add_parser("run"); s.add_argument("job", nargs="?")
    s.add_argument("--only", nargs="+", help="只跑指定名稱的 job")
    s.add_argument("--show", action="store_true", help="開互動視窗（可看滑鼠座標）")
    s.add_argument("--open", action="store_true", help="完成後開啟 index.html")
    s.add_argument("--out", help="覆寫輸出資料夾")
    s.add_argument("--pause", action="store_true", help="完成後等待 Enter")
    s = sub.add_parser("run-all"); s.add_argument("--pause", action="store_true")
    s = sub.add_parser("new"); s.add_argument("db", nargs="?"); s.add_argument("--name")
    sub.add_parser("types")
    sub.add_parser("menu")
    sub.add_parser("diag")
    s = sub.add_parser("update"); s.add_argument("-y", "--yes", action="store_true")
    s = sub.add_parser("release", help="發佈新版本（開發者用）")
    s.add_argument("version", nargs="?"); s.add_argument("-n", "--notes", nargs="*", default=None)
    s.add_argument("--repo"); s.add_argument("--branch"); s.add_argument("--push", action="store_true")
    sub.add_parser("install")
    sub.add_parser("uninstall")
    sub.add_parser("shortcut")
    s = sub.add_parser("gui"); s.add_argument("db", nargs="?")
    s = sub.add_parser("app"); s.add_argument("db", nargs="?")
    s = sub.add_parser("open"); s.add_argument("path")
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return
    settings = load_settings()
    if a.cmd not in ("release",):
        try:
            import numpy  # noqa: F401   這個 Python 能用才記下來
            from .gui.launcher import register_paths
            register_paths()
        except Exception:
            pass
    {"catalog": cmd_catalog, "list": cmd_list, "quick": cmd_quick, "run": cmd_run,
     "run-all": cmd_run_all, "new": cmd_new, "types": cmd_types,
     "menu": cmd_menu, "open": cmd_open, "gui": cmd_gui, "app": cmd_gui, "shortcut": cmd_install,
     "install": cmd_install, "uninstall": cmd_uninstall, "diag": cmd_diag,
     "update": cmd_update, "release": cmd_release}[a.cmd](a, settings)


if __name__ == "__main__":
    main()
