"""發佈前自我測試：python -m lta selftest（publish.bat 上傳前會自動執行）。

用程式產生的假資料（不需要、也不會碰到你的真實資料）檢查：
  1. 所有程式檔沒有語法錯誤、都能載入
  2. 每個分析功能都能算、能畫（含背景計算的路徑）
  3. 批次分析能跑
  4. 軟體視窗能開啟，並實際畫幾張圖（電腦有 Tk 時）
任何一項失敗 → 回傳失敗，publish 不會上傳。
"""
from __future__ import annotations

import compileall
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback

import numpy as np

from .gui.launcher import TOOL_ROOT

TEMPS = [1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8]
CONTACTS = "I+_38, V+_39, V-_40, I-_41"


# ---------------------------------------------------------------------------
# 假資料
# ---------------------------------------------------------------------------
def _interp(T, table):
    Ts = sorted(table)
    return float(np.interp(T, Ts, [table[t] for t in Ts]))


def transitions_at(T):
    """假樣品在溫度 T 的轉折位置（μA），大致仿照真實樣品，讓預設的轉折線設定找得到。"""
    return {
        "outer -": -860 + 170 * (T - 1.6) ** 1.6,
        "inner -": _interp(T, {1.6: -500, 1.9: -460, 2.1: -380, 2.3: -280, 2.5: -190, 2.8: -80}),
        "center -": -150 + 110 * (T - 1.6),
        "center +": _interp(T, {1.6: 225, 1.8: 240, 1.9: 150, 2.1: 95, 2.3: 50, 2.6: 20, 2.8: 12}),
        "inner +": _interp(T, {1.6: 530, 1.8: 430, 2.0: 320, 2.2: 230, 2.4: 155, 2.6: 125, 2.8: 120}),
        "outer +": 930 - 180 * (T - 1.6) ** 1.6,
    }


def edges_table():
    rows = []
    for T in TEMPS:
        tr = transitions_at(T)
        rows.append(f"{T}: {tr['outer -'] - 20:.0f}, {tr['outer +'] + 20:.0f}")
    return "; ".join(rows)


def iv_curve(T, rng, n=1001):
    """電流偏壓 IV：dV/dI 在各轉折往外一階一階變大（log 上是台階）。"""
    I_uA = np.linspace(-1000, 1000, n)
    tr = transitions_at(T)
    r = np.full(n, 0.01)
    for name, val in (("center", 0.05), ("inner", 0.3), ("outer", 2.0)):
        r[(I_uA < tr[f"{name} -"]) | (I_uA > tr[f"{name} +"])] = val
    dI = np.diff(I_uA) * 1e-6
    V = np.concatenate([[0.0], np.cumsum(0.5 * (r[1:] + r[:-1]) * dI)])
    V -= np.interp(0.0, I_uA, V)
    V += rng.normal(0, 2e-10, n)
    return I_uA * 1e-6, V


def make_db(path, seed=0):
    """產生一個 qcodes 格式的小資料庫（IV × 溫度、VI、IV vs By 色圖）。"""
    rng = np.random.default_rng(seed)
    con = sqlite3.connect(path)
    con.executescript("""
        create table experiments (exp_id INTEGER PRIMARY KEY, name TEXT, sample_name TEXT, start_time INTEGER,
                                  end_time INTEGER, run_counter INTEGER, format_string TEXT);
        create table runs (run_id INTEGER PRIMARY KEY, exp_id INTEGER, name TEXT, result_table_name TEXT,
                           result_counter INTEGER, run_timestamp INTEGER, completed_timestamp INTEGER,
                           is_completed BOOL, parameters TEXT, guid TEXT, run_description TEXT);
    """)
    t0 = time.time() - 86400
    run_id = 0

    def add(kind, sample, specs, columns, data):
        nonlocal run_id
        run_id += 1
        con.execute("insert into experiments (exp_id, name, sample_name, run_counter, format_string) "
                    "values (?, ?, ?, 1, '{}-{}-{}')", (run_id, kind, sample))
        table = f"results-{run_id}-1"
        desc = {"version": 3, "interdependencies": {"paramspecs": [
            {"name": n, "paramtype": "numeric", "label": n, "unit": u, "inferred_from": [], "depends_on": d}
            for n, u, d in specs]}}
        con.execute(f'create table "{table}" (id INTEGER PRIMARY KEY, '
                    + ", ".join(f'"{c}" numeric' for c in columns) + ")")
        con.executemany(f'insert into "{table}" ({", ".join(chr(34) + c + chr(34) for c in columns)}) values '
                        f'({", ".join("?" * len(columns))})', [tuple(None if x is None else float(x) for x in row) for row in data])
        con.execute("insert into runs (run_id, exp_id, name, result_table_name, result_counter, run_timestamp, "
                    "completed_timestamp, is_completed, parameters, run_description) values (?,?,?,?,?,?,?,1,?,?)",
                    (run_id, run_id, "results", table, len(data), t0 + 60 * run_id, t0 + 60 * run_id + 30,
                     ",".join(columns), json.dumps(desc)))
        return run_id

    iv_spec = [("appl_current", "A", []), ("meas_voltage_K2", "V", ["appl_current"])]
    iv_runs = []
    for T in TEMPS:
        I, V = iv_curve(T, rng)
        iv_runs.append(add("IV", f"IV_{CONTACTS}, T_{T}K", iv_spec, ["appl_current", "meas_voltage_K2"],
                           np.column_stack([I, V])))
    # 電壓偏壓 VI（電阻 2 kΩ）
    Vb = np.linspace(-5e-3, 5e-3, 201)
    vi = add("VI", "VI_I_20, V_46", [("appl_voltage", "V", []), ("meas_current", "A", ["appl_voltage"])],
             ["appl_voltage", "meas_current"], np.column_stack([Vb, Vb / 2000 + rng.normal(0, 1e-9, Vb.size)]))
    # 2D：IV vs By（Ic 在 B0 = 2 mT 最大）
    B = np.linspace(-0.008, 0.012, 41)
    Ib = np.linspace(-1.5e-3, 1.5e-3, 151)
    rows = []
    for b in B:
        ic = 1.2e-3 * abs(np.sinc((b - 0.002) / 0.006)) + 0.1e-3
        for i in Ib:
            v = 0.0 if abs(i) < ic else np.sign(i) * (1e-3 + 2.0 * (abs(i) - ic))
            rows.append((b, i, v + rng.normal(0, 1e-8)))
    by = add("IV vs By", f"IV_vs_By_{CONTACTS}, T_1.6K",
             [("y_field", "T", []), ("appl_current", "A", []), ("meas_voltage_K2", "V", ["y_field", "appl_current"])],
             ["y_field", "appl_current", "meas_voltage_K2"], rows)
    # 2D：IV vs 閘極。和真實量測一樣，每個閘極電壓另外記一次溫度（存在不同列、其他欄位是空的），
    # 另外有一個沒接的 K1（整欄空白）→ 讀檔要用 meas_voltage_K2 自己的那幾列
    gate_rows, last = [], None
    for g, i, v in gate_map(rng):
        if g != last:
            gate_rows.append((1.6, g, None, None, None))
            last = g
        gate_rows.append((None, g, i, None, v))
    gate = add("IV vs TG", f"IV_vs_TG_{CONTACTS}, T_1.6K",
               [("meas_temperature", "K", ["appl_TG"]), ("appl_TG", "V", []), ("appl_current", "A", []),
                ("meas_voltage_K1", "V", ["appl_TG", "appl_current"]),
                ("meas_voltage_K2", "V", ["appl_TG", "appl_current"])],
               ["meas_temperature", "appl_TG", "appl_current", "meas_voltage_K1", "meas_voltage_K2"], gate_rows)
    con.commit()
    con.close()
    return dict(iv=iv_runs, vi=[vi], map=[by], gate=[gate])


def gate_ic(vg):
    """假樣品的切換電流 (A)：Vg ≈ −3 V 時最小，往兩邊變大（仿 TaS2 閘極量測）。"""
    return (2 + np.where(vg < -3, 10.5, 6.0) * np.abs(vg + 3)) * 1e-9


def gate_map(rng, vg=None, I=None):
    """2D：閘極電壓 × 電流 → 電壓（超導區 R ≈ 50 Ω，切換後 R_N ≈ 2 kΩ）。"""
    vg = np.linspace(-15, 15, 61) if vg is None else vg
    I = np.linspace(-150e-9, 150e-9, 121) if I is None else I
    rows = []
    for g in vg:
        ic = gate_ic(g)
        for i in I:
            v = 50 * i if abs(i) < ic else np.sign(i) * (50 * ic + 2000 * (abs(i) - ic) + 20e-6)
            rows.append((g, i, v + rng.normal(0, 2e-7)))
    return rows


# ---------------------------------------------------------------------------
# 測試
# ---------------------------------------------------------------------------
class Report:
    def __init__(self, log):
        self.log = log
        self.fails = []

    def check(self, name, fn):
        t = time.time()
        try:
            note = fn()
            self.log(f"  ✓ {name}（{time.time() - t:.1f} s）" + (f"　{note}" if note else ""))
            return True
        except Exception as e:
            self.fails.append((name, traceback.format_exc()))
            self.log(f"  ✗ {name}：{type(e).__name__}: {e}")
            return False


def overrides(a):
    """假資料用的參數（edge 依溫度、二次微分的猜測）。"""
    return {"edges": edges_table(), "d2_guess": "-0.86, -0.50, 0.53, 0.93", "zero_from": "inner: 2.6",
            "extra_zero_T": "3.2"}


def _items(db, ids):
    from matplotlib import colormaps
    from .core.registry import Item
    cm = colormaps["viridis"]
    return [Item(db.load(r), f"run {r}", cm(k / max(len(ids) - 1, 1))) for k, r in enumerate(ids)]


def run_analyses(db, runs, rep):
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from .core.registry import load_analyses, defaults_for
    reg = load_analyses()
    sets = {"1D": [("IV", runs["iv"]), ("VI", runs["vi"])],
            "2D": [("map", runs["map"]), ("gate", runs["gate"])]}
    for cat, lst in reg.items():
        for a in lst:
            for tag, ids in sets[cat]:
                items = _items(db, ids)
                if not all(a.accepts(it.info) for it in items):
                    continue
                if not a.multi:
                    items = items[:1]

                def go(a=a, items=items):
                    p = defaults_for(a)
                    p.update({k: v for k, v in overrides(a).items() if k in p})
                    if a.compute is not None:
                        p["_data"] = a.compute(items, dict(p), lambda k, n, t="": None)
                    fig = Figure(figsize=(11, 6.5), layout="constrained")
                    FigureCanvasAgg(fig)
                    res = a.func(fig, items, p)
                    fig.canvas.draw()
                    if res.on_click is not None:
                        ax = fig.axes[0]
                        from matplotlib.backend_bases import MouseEvent
                        bb = ax.bbox
                        res.on_click(MouseEvent("button_press_event", fig.canvas, bb.x0 + bb.width * .6,
                                                bb.y0 + bb.height * .5, button=1))
                    return f"{len(fig.axes)} 個座標軸"
                rep.check(f"{cat} {a.name}（{tag}）", go)


def run_batch(db_path, runs, rep, out):
    def go():
        from .batch.runner import run_job_file
        job = os.path.join(out, "selftest_job.toml")
        iv = runs["iv"]
        edges = ", ".join(f"[{T}, {transitions_at(T)['outer -'] - 20:.0f}, {transitions_at(T)['outer +'] + 20:.0f}]"
                          for T in TEMPS)
        with open(job, "w", encoding="utf-8") as f:
            f.write(f"""db = '{db_path.replace(os.sep, "/")}'
output_dir = '{out.replace(os.sep, "/")}/batch'

[[job]]
name = "iv"
type = "iv"
runs = {iv}

[[job]]
name = "lin"
type = "linearize"
runs = {iv}
edges = [{edges}]

[[job]]
name = "logdvdi"
type = "dvdi_vs_T"
runs = {iv}
scale = "log"
save_individual = false
edges = [{edges}]

[[job]]
name = "d2"
type = "iv_transitions"
runs = {iv}
initial_guess_mA = [-0.86, -0.50, 0.53, 0.93]
unresolved_to_zero = true

[[job]]
name = "map"
type = "field_map"
runs = {runs['map']}
""")
        _, summary = run_job_file(job, out_override=os.path.join(out, "batch"), log=lambda *a: None)
        bad = [f"{x['name']}（{x['error']}）" for x in summary if not x["ok"]]
        if bad:
            raise RuntimeError("批次分析失敗：" + "；".join(bad))
        n = sum(len(fs) for _, _, fs in os.walk(os.path.join(out, "batch")))
        if n < 5:
            raise RuntimeError(f"批次分析只產生 {n} 個檔案")
        return f"{n} 個輸出檔"
    rep.check("批次分析", go)


def _png_size(path):
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def run_gui(db_path, runs, rep, timeout=60, out=None):
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception as e:
        rep.log(f"  - 略過軟體視窗測試（這台電腦沒有可用的 Tk：{e}）")
        return
    root.withdraw()

    def go():
        from .gui.app import App, prepare
        from .gui import theme as T
        prepare(root)
        try:
            root.attributes("-alpha", 0.0)
        except Exception:
            pass
        root.geometry("1500x950+20+20")
        root.deiconify()
        app = App(root, db_path)
        app.gui_settings["auto_update_check"] = False

        def wait():
            t = time.time()
            while time.time() - t < timeout:
                root.update()
                if app._compute_job is None and app._plot_job is None:
                    return
                time.sleep(0.02)
            raise TimeoutError("等太久")
        try:
            t = time.time()
            while app.db is None or not app.tree.get_children():      # 開檔在背景進行
                if time.time() - t > timeout:
                    raise TimeoutError("開啟資料庫等太久")
                root.update()
                app._poll_queue()
                time.sleep(0.02)
            done = []
            for cat, ids in (("1D", runs["iv"]), ("2D", runs["map"]), ("2D", runs["gate"])):
                if cat != app.category:
                    app.segment.select(0 if cat == "1D" else 1)
                    wait()
                app.tree.selection_set([str(r) for r in ids])
                info = app.db.info(ids[0])
                for a in app.analyses[cat]:
                    if not a.accepts(info):
                        continue
                    app.features.select(a.name)
                    for prm, val in overrides(a).items():
                        var = app.param_vars.get(a.store_key, {}).get(prm)
                        if var is not None:
                            var.set(val)
                    app.plot()
                    wait()
                    for _ in range(200):                       # 資料在背景載入完才會畫
                        if app.status_var.get().startswith(("完成", "錯誤")) or "失敗" in app.status_var.get():
                            break
                        root.update()
                        app._poll_queue()
                        if app._plot_job is not None:
                            app.plot()
                        wait()
                        time.sleep(0.02)
                    status = app.status_var.get()
                    if not status.startswith("完成"):
                        raise RuntimeError(f"{a.name}：{status}")
                    app.status_var.set("")
                    done.append(a.name)
            app.undo(); app.redo()
            if out:                                   # 存圖：指定 8.5 × 6 cm，畫面上的圖大小不能變
                before = tuple(app.fig.get_size_inches())
                path = os.path.join(out, "export.png")
                app._export_figure(app.fig, path, ex=dict(mode="fixed", w_cm=8.5, h_cm=6.0, font="8 pt"))
                w, h = _png_size(path)
                dpi = int(app.settings.get("dpi", 300))
                assert abs(w - 8.5 / 2.54 * dpi) <= 3 and abs(h - 6 / 2.54 * dpi) <= 3, (w, h)
                assert tuple(app.fig.get_size_inches()) == before, "存圖後畫面大小改變了"
            return f"畫了 {len(done)} 個功能"
        finally:
            try:
                app._close()
            except Exception:
                pass
    rep.check("軟體視窗：開啟並畫圖", go)


def main(gui=True, log=print):
    os.environ.setdefault("MPLBACKEND", "Agg")
    tmp = tempfile.mkdtemp(prefix="lta_selftest_")
    old_local = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = os.path.join(tmp, "appdata")        # 不動到你自己的設定
    rep = Report(log)
    t0 = time.time()
    try:
        from . import __version__
        log(f"自我測試 v{__version__}（{TOOL_ROOT}）")
        rep.check("程式檔語法", lambda: None if compileall.compile_dir(
            os.path.join(TOOL_ROOT, "lta"), quiet=1, force=False) else (_ for _ in ()).throw(SyntaxError("有語法錯誤，見上方")))
        pyw = os.path.join(TOOL_ROOT, "LowTempAnalysis.pyw")
        if os.path.exists(pyw):
            rep.check("啟動檔 LowTempAnalysis.pyw", lambda: compile(open(pyw, encoding="utf-8").read(), pyw, "exec") and None)

        def bats():
            bad = []
            for fn in sorted(os.listdir(TOOL_ROOT)):
                if fn.lower().endswith(".bat"):
                    b = open(os.path.join(TOOL_ROOT, fn), "rb").read()
                    if any(c > 127 for c in b):
                        bad.append(f"{fn} 有非英文字元（cmd 會讀錯）")
                    elif b.count(b"\n") != b.count(b"\r\n"):
                        bad.append(f"{fn} 換行不是 CRLF（cmd 可能跳行）")
            assert not bad, "；".join(bad)
            return "全部是 ASCII + CRLF"
        rep.check("bat 檔格式", bats)

        def sig():
            from . import signing
            sk = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
            assert signing.public_key(sk).hex() == "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c"
            assert signing.sign(sk, b"\x72").hex().startswith("92a009a9f0d4cab8720e820b5f642540")
            files = {"a.py": b"print(1)\r\n", "lta/b.py": b"x = 2\n"}
            m, sg = signing.sign_files(files, "1.0", sk)
            pub = signing.public_key(sk)
            ok = dict(files, **{signing.MANIFEST: m, signing.SIGNATURE: sg.encode()})
            signing.verify_files(dict(ok, **{"a.py": b"print(1)\n"}), pub)          # 換行不同不算竄改
            for bad in ({"lta/b.py": b"x = 3\n"}, {"evil.py": b""}):
                try:
                    signing.verify_files(dict(ok, **bad), pub)
                except ValueError:
                    continue
                raise AssertionError(f"竄改沒有被抓到：{list(bad)}")
            try:
                signing.verify_files(ok, signing.public_key(os.urandom(32)))
                raise AssertionError("別的金鑰的簽章被接受了")
            except ValueError:
                pass
            pk = signing.read_public_key(TOOL_ROOT)
            return "Ed25519 正確，竄改會被擋下" + ("；已設定發佈公鑰" if pk else "；還沒有發佈公鑰（第一次發佈時產生）")
        rep.check("更新簽章", sig)

        def imports():
            import importlib
            import pkgutil
            import lta
            n, skipped = 0, []
            for m in pkgutil.walk_packages(lta.__path__, "lta."):
                if m.name.endswith(("__main__",)):
                    continue
                try:
                    importlib.import_module(m.name)
                except ModuleNotFoundError as e:
                    if e.name != "tkinter":
                        raise
                    skipped.append(m.name)              # 這台電腦沒有 Tk（只會發生在測試環境）
                    continue
                n += 1
            from .core.registry import load_analyses, check_groups
            load_analyses()
            check_groups()
            from .batch.jobs import load_all
            return f"{n} 個模組、{len(load_all())} 種批次分析" + (f"（沒有 Tk，略過 {len(skipped)} 個介面模組）" if skipped else "")
        if not rep.check("載入所有模組", imports):
            return False
        from matplotlib import use
        use("Agg")
        from .gui import theme as T
        T.apply_mpl_style()
        db_path = os.path.join(tmp, "selftest.db")
        runs = {}
        rep.check("產生測試資料", lambda: runs.update(make_db(db_path)) or f"{sum(map(len, runs.values()))} 筆 run")
        from .core.db import QDB
        db = QDB(db_path)

        def detect():
            info = {r.run_id: r for r in db.runs()}
            assert info[runs["iv"][0]].mode == "current_bias", info[runs["iv"][0]].mode
            assert info[runs["vi"][0]].mode == "voltage_bias", info[runs["vi"][0]].mode
            assert info[runs["map"][0]].is_2d
            assert info[runs["map"][0]].condition == "field", info[runs["map"][0]].condition
            assert info[runs["gate"][0]].condition == "gate", info[runs["gate"][0]].condition
            assert info[runs["iv"][0]].condition == "none"
            m = db.load(runs["gate"][0]).map2d()
            assert m.z.name == "meas_voltage_K2" and m.z.values.shape == (61, 121), (m.z.name, m.z.values.shape)
            assert abs(info[runs["iv"][0]].T_K - 1.6) < 1e-9
            return "量測模式、溫度、2D 判斷正確"
        rep.check("讀取資料庫", detect)
        run_analyses(db, runs, rep)
        run_batch(db_path, runs, rep, tmp)

        def report():
            from . import bugreport
            import zipfile
            path, files = bugreport.make_report(os.path.join(tmp, "report.zip"), extra={"測試": "selftest"})
            names = zipfile.ZipFile(path).namelist()
            assert "環境資訊.txt" in names, names
            return f"{len(names)} 個檔案"
        rep.check("打包錯誤記錄", report)
        if gui:
            run_gui(db_path, runs, rep, out=tmp)
    finally:
        if old_local is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = old_local
        shutil.rmtree(tmp, ignore_errors=True)
    if rep.fails:
        log(f"\n✗ 自我測試失敗：{len(rep.fails)} 項（{time.time() - t0:.0f} s）")
        for name, tb in rep.fails:
            log(f"\n--- {name} ---\n{tb.strip()[-1500:]}")
        return False
    log(f"\n✓ 自我測試全部通過（{time.time() - t0:.0f} s）")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
