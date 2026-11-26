"""2D 分析共用工具：讀色圖資料、平移原點（B0）、色階、可點擊切線的色圖。

2D 資料一律是 Map2D：slow（慢軸，例如磁場、閘極）× fast（快軸，例如電流）→ z（量測值）。
"""
from __future__ import annotations

from dataclasses import dataclass

import time

import numpy as np
from matplotlib import colormaps

from ..core.measurement import si_scale

CMAPS = ["magma", "inferno", "viridis", "plasma", "cividis", "hot", "RdBu_r", "coolwarm", "gray"]
RANGES = ["自動 (1–99%)", "全範圍", "手動"]


# ---------------------------------------------------------------------------
# 計算核心（批次分析 batch/jobs/field.py 也用這些）
# ---------------------------------------------------------------------------
def switching_current(I_row, V_row, threshold_V):
    """從 I=0 往正方向走，|V − V(I≈0)| 第一次超過門檻前的電流。單位隨輸入。"""
    ok = np.isfinite(I_row) & np.isfinite(V_row)
    I_row, V_row = I_row[ok], V_row[ok]
    order = np.argsort(I_row, kind="stable")
    I_s, V_s = I_row[order], V_row[order]
    if not len(I_s):
        return np.nan
    V0 = V_s[np.argmin(np.abs(I_s))]
    pos = I_s >= 0
    I_p, V_p = I_s[pos], V_s[pos]
    sw = np.flatnonzero(np.abs(V_p - V0) > threshold_V)
    if len(sw) == 0:
        return np.nan
    return I_p[max(sw[0] - 1, 0)]


def find_B0(B, I, V, threshold_mV=0.5, search_mT=None):
    """正向切換電流最大的慢軸位置。B, I, V 為 SI 的 (N_slow, N_fast) 陣列。
    回傳 (B0 [SI], Ic_peak [A], 每列的 Ic)"""
    B_list = B[:, 0]
    Ic = np.array([switching_current(I[k], V[k], threshold_mV * 1e-3) for k in range(len(B_list))])
    search = np.isfinite(Ic)
    if search_mT:
        lo, hi = search_mT
        if lo is not None:
            search &= B_list >= lo * 1e-3
        if hi is not None:
            search &= B_list <= hi * 1e-3
    if not search.any():
        raise ValueError("搜尋範圍內找不到切換電流")
    Ic_peak = np.nanmax(Ic[search])
    steps = np.abs(np.diff(np.sort(np.unique(I[0]))))
    dI = np.median(steps) if len(steps) else np.nan
    top = search & (Ic >= Ic_peak - 0.5 * dI) if np.isfinite(dI) else search & (Ic >= Ic_peak)
    return float(B_list[top].mean()), float(Ic_peak), Ic


# ---------------------------------------------------------------------------
# 顯示用的資料
# ---------------------------------------------------------------------------
@dataclass
class View:
    """縮放成顯示單位的 2D 資料。"""
    S: np.ndarray          # 慢軸（顯示單位，可能已平移）
    F: np.ndarray          # 快軸
    Z: np.ndarray          # 量測值
    s_label: str
    f_label: str
    z_label: str
    s_scale: float
    f_scale: float
    z_scale: float
    origin: float          # 平移量（SI）
    note: str
    m: object              # 原始 Map2D

    @property
    def s_axis(self):
        return self.S[:, 0]


def load_view(it, shift=False, threshold_mV=0.5):
    """讀一筆 2D 資料並縮放；shift=True 且為 I→V 時以切換電流最大處為慢軸原點。"""
    m = it.map2d()
    origin, note = 0.0, ""
    if shift:
        if m.mode != "current_bias":
            note = "（平移原點只適用於快軸施加電流、量電壓的資料，已略過）"
        else:
            origin, Ic_peak, _ = find_B0(m.slow.values, m.fast.values, m.z.values, threshold_mV)
            ps, us = si_scale(m.slow.values, m.slow.unit)
            note = f"{m.slow.sym[0]}₀ = {origin / ps:.4f} {us}（Ic+ max {Ic_peak / 1e-6:.1f} μA）"
    ps, us = si_scale(m.slow.values, m.slow.unit)
    pf, uf = si_scale(m.fast.values, m.fast.unit)
    pz, uz = si_scale(m.z.values, m.z.unit)
    s_sym = m.slow.sym + (f" − {m.slow.sym[0]}₀" if origin else "")
    return View((m.slow.values - origin) / ps, m.fast.values / pf, m.z.values / pz,
                f"{s_sym} ({us})", f"{m.fast.sym} ({uf})", f"{m.z.sym} ({uz})",
                ps, pf, pz, origin, note, m)


def derivative_view(v: View):
    """dZ/dF（相鄰點差分）。回傳 (S, F_mid, D, 標籤, 縮放)，D 為 SI 單位再縮放。"""
    from ..core.measurement import derivative_unit
    m = v.m
    with np.errstate(divide="ignore", invalid="ignore"):
        D = np.diff(m.z.values, axis=1) / np.diff(m.fast.values, axis=1)
    D[~np.isfinite(D)] = np.nan
    unit = derivative_unit(m.z.unit, m.fast.unit)
    pd, ud = si_scale(D, unit)
    return (v.S[:, :-1], (v.F[:, :-1] + v.F[:, 1:]) / 2, D / pd,
            f"d{m.z.sym}/d{m.fast.sym} ({ud})", pd)


def color_limits(Z, mode, vmin=0.0, vmax=1.0):
    Zf = Z[np.isfinite(Z)]
    if not len(Zf):
        return None, None
    if mode == RANGES[0]:
        return tuple(np.percentile(Zf, [1, 99]))
    if mode == RANGES[1]:
        return float(Zf.min()), float(Zf.max())
    return float(vmin), float(vmax)


def add_map(fig, ax, X, Y, Z, cmap, vlim, zlabel):
    """畫色圖並掛上給滑鼠讀值用的資料。"""
    mesh = ax.pcolormesh(X, Y, Z, cmap=cmap, shading="auto", vmin=vlim[0], vmax=vlim[1],
                         rasterized=True)
    mesh.lta_grid = (X[:, 0], Y[0, :], Z, zlabel)
    ax.grid(False)
    cb = fig.colorbar(mesh, ax=ax, pad=0.02)
    cb.set_label(zlabel)
    return mesh


CUT_HINT = "左鍵點色圖加切線（右邊畫 IV）· 在切線上按住可拖曳到別的位置 · 右鍵清除"


def map_with_cut(fig, X, Y, Z, labels, cmap, vlim, cut=True, title="", origin_line=False,
                 cut_title="cut", show_origin=True, cut_marks=True, first_cut=True):
    """色圖（左）＋ 切線面板（右）。回傳點擊處理函式（左鍵加切線、右鍵清除）或 None。
    labels = (x 軸, y 軸, z 軸)"""
    xl, yl, zl = labels
    if cut:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.45, 1], wspace=0.04)   # 色條與右圖之間留空間
        ax = fig.add_subplot(gs[0])
        ax_cut = fig.add_subplot(gs[1])
    else:
        ax = fig.add_subplot(111)
        ax_cut = None
    add_map(fig, ax, X, Y, Z, cmap, vlim, zl)
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    if title:
        ax.set_title(title, fontsize=9)
    if origin_line and show_origin:
        ax.axvline(0, color="cyan", ls="--", lw=0.8, alpha=0.7, gid="nohover")
    if ax_cut is None:
        return None

    X_axis = X[:, 0]
    state = {"lines": [], "marks": [], "xs": [], "n": 0}
    palette = colormaps["tab10"]
    ax_cut.set_xlabel(yl)
    ax_cut.set_ylabel(zl)

    def add_cut(x):
        i = int(np.argmin(np.abs(X_axis - x)))
        c = palette(state["n"] % 10)
        state["n"] += 1
        ln, = ax_cut.plot(Y[i], Z[i], color=c, lw=1.3, label=f"{X_axis[i]:+.4g}")
        state["lines"].append(ln)
        state["xs"].append(float(X_axis[i]))
        if cut_marks:
            state["marks"].append(ax.axvline(X_axis[i], color=c, lw=1.4, gid="nohover"))
        ax_cut.relim()
        ax_cut.autoscale_view()
        ax_cut.legend(fontsize=7, title=f"{cut_title} @ {xl}", title_fontsize=7)

    def clear():
        for a in state["lines"] + state["marks"]:
            a.remove()
        state.update(lines=[], marks=[], xs=[], n=0)
        leg = ax_cut.get_legend()
        if leg:
            leg.remove()

    if first_cut:
        add_cut(0.0 if origin_line else X_axis[len(X_axis) // 2])

    def refresh_legend():
        ax_cut.relim()
        ax_cut.autoscale_view()
        ax_cut.legend(fontsize=7, title=f"{cut_title} @ {xl}", title_fontsize=7)

    def mark_near(event, tol=7):
        """滑鼠附近（像素）的切線標記序號。"""
        best, k_best = tol, None
        for k, mk in enumerate(state["marks"]):
            xd = mk.get_xdata()[0]
            px_, _ = ax.transData.transform((xd, 0))
            if abs(px_ - event.x) <= best:
                best, k_best = abs(px_ - event.x), k
        return k_best

    def move_cut(k, x):
        i = int(np.argmin(np.abs(X_axis - x)))
        state["marks"][k].set_xdata([X_axis[i], X_axis[i]])
        state["xs"][k] = float(X_axis[i])
        ln = state["lines"][k]
        ln.set_data(Y[i], Z[i])
        ln.set_label(f"{X_axis[i]:+.4g}")

    def blit(canvas, k):
        d = state.get("drag")
        if d is None or d.get("bg") is None:
            canvas.draw_idle()
            return
        try:
            canvas.restore_region(d["bg"])
            ax.draw_artist(state["marks"][k])
            ax_cut.draw_artist(state["lines"][k])
            canvas.blit(canvas.figure.bbox)
        except Exception:
            d["bg"] = None
            canvas.draw_idle()

    def on_mouse(event):
        """左鍵點一下：在那個位置加切線（右邊畫 IV）；在切線上按住（或按住拖曳）：把那條切線移到別的位置；
        右鍵：清除。"""
        canvas = event.canvas
        name = event.name
        if name == "button_press_event":
            if event.inaxes is not ax or event.xdata is None:
                return
            if event.button == 3:
                clear()
                canvas.draw_idle()
                notify()
                return
            if event.button != 1:
                return
            k = mark_near(event)
            state["press"] = dict(x=event.x, y=event.y, t=time.time(), xdata=event.xdata, k=k)
            state["drag"] = None
            return
        press = state.get("press")
        if press is None:
            return
        if name == "motion_notify_event":
            if press["k"] is None:
                return
            d = state.get("drag")
            if d is None:
                held = time.time() - press["t"] >= 0.25
                moved = abs(event.x - press["x"]) > 6
                if not (held or moved):
                    return
                k = press["k"]
                d = state["drag"] = dict(k=k, bg=None)
                for art in (state["marks"][k], state["lines"][k]):
                    art.set_animated(True)
                try:
                    canvas.draw()
                    d["bg"] = canvas.copy_from_bbox(canvas.figure.bbox)
                except Exception:
                    d["bg"] = None
            x = event.xdata if event.inaxes is ax else None
            if x is None:
                inv = ax.transData.inverted()
                x = inv.transform((event.x, event.y))[0]
                x = min(max(x, X_axis.min()), X_axis.max())
            move_cut(d["k"], x)
            blit(canvas, d["k"])
            return
        if name == "button_release_event":
            state["press"] = None
            d, state["drag"] = state.get("drag"), None
            if d is not None:                      # 拖曳結束
                for art in (state["marks"][d["k"]], state["lines"][d["k"]]):
                    art.set_animated(False)
                refresh_legend()
                canvas.draw_idle()
                notify()
                return
            if abs(event.x - press["x"]) <= 6 and abs(event.y - press["y"]) <= 6:   # 點一下 → 加切線
                add_cut(press["xdata"])
                canvas.draw_idle()
                notify()

    def notify():
        cb = getattr(on_mouse, "changed", None)
        if cb is not None:
            try:
                cb(list(state["xs"]))
            except Exception:
                pass

    def set_cuts(xs):
        """換成指定的切線位置（介面用來還原上次的切線；空清單 = 沒有切線）。"""
        clear()
        for x in xs:
            try:
                add_cut(float(x))
            except (TypeError, ValueError):
                pass

    on_mouse.full_events = True        # 介面會把按下、移動、放開都交給它
    on_mouse.drag_ax = ax
    on_mouse.get_cuts = lambda: list(state["xs"])
    on_mouse.set_cuts = set_cuts
    on_mouse.changed = None            # 介面設定：切線改變時呼叫 changed([x, …])，用來記住位置
    return on_mouse


