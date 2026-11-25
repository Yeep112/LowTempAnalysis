"""各分析共用的小工具：擬合、資料整理、依溫度內插的參數、存圖。"""
from __future__ import annotations

import os

import numpy as np
from scipy.optimize import least_squares


# ---------------------------------------------------------------------------
# 資料整理
# ---------------------------------------------------------------------------
def clean_sort(I, V, merge_duplicates=True):
    """去掉 NaN、依電流排序；merge_duplicates=True 時同一電流的電壓取平均。"""
    I = np.asarray(I, dtype=float).ravel()
    V = np.asarray(V, dtype=float).ravel()
    n = min(len(I), len(V))
    I, V = I[:n], V[:n]
    ok = np.isfinite(I) & np.isfinite(V)
    I, V = I[ok], V[ok]
    order = np.argsort(I, kind="stable")
    I, V = I[order], V[order]
    if merge_duplicates and len(I):
        I_u, inverse = np.unique(I, return_inverse=True)
        V = np.bincount(inverse, weights=V) / np.bincount(inverse)
        I = I_u
    return I, V


def derivative(I, V):
    """dV/dI（相鄰點差分），回傳 (I 中點, dV/dI)，已去掉無限大。"""
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.diff(V) / np.diff(I)
    mid = (I[:-1] + I[1:]) / 2
    ok = np.isfinite(d)
    return mid[ok], d[ok]


def robust_linear_fit(x, y, f_scale=0.005):
    """Huber loss 直線擬合 y = intercept + slope * x，回傳 (intercept, slope)。"""
    x = np.asarray(x, float)
    y = np.asarray(y, float)

    def residuals(p):
        return p[0] + p[1] * x - y

    slope0 = np.polyfit(x, y, 1)[0]
    res = least_squares(residuals, x0=[np.median(y), slope0], loss="huber",
                        f_scale=f_scale)
    return float(res.x[0]), float(res.x[1])


def linearize_edges(I_uA, V_mV, neg_edge_uA, pos_edge_uA, edge_width_uA=500,
                    run_label=""):
    """兩側（|I| 超過 edge）用 Huber 直線取代雜訊。回傳 (V_clean_mV, fits dict)。"""
    neg_fit = (I_uA < neg_edge_uA) & (I_uA >= neg_edge_uA - edge_width_uA)
    pos_fit = (I_uA > pos_edge_uA) & (I_uA <= pos_edge_uA + edge_width_uA)
    if np.count_nonzero(neg_fit) < 3:
        raise ValueError(f"{run_label}: 負側擬合範圍點數不足（edge={neg_edge_uA} uA）")
    if np.count_nonzero(pos_fit) < 3:
        raise ValueError(f"{run_label}: 正側擬合範圍點數不足（edge={pos_edge_uA} uA）")
    n_int, n_slope = robust_linear_fit(I_uA[neg_fit], V_mV[neg_fit])
    p_int, p_slope = robust_linear_fit(I_uA[pos_fit], V_mV[pos_fit])
    V_clean = V_mV.copy()
    neg = I_uA < neg_edge_uA
    pos = I_uA > pos_edge_uA
    V_clean[neg] = n_int + n_slope * I_uA[neg]
    V_clean[pos] = p_int + p_slope * I_uA[pos]
    fits = dict(neg_intercept=n_int, neg_slope=n_slope,
                pos_intercept=p_int, pos_slope=p_slope)
    return V_clean, fits


# ---------------------------------------------------------------------------
# 依溫度（或 run）決定的參數
#   數字                → 固定值
#   [[T, v], [T, v]]    → 依溫度線性內插（新的溫度點自動有值）
#   [[T, a, b], ...]    → 多欄位一起內插，回傳 list
# ---------------------------------------------------------------------------
def by_T(spec, T, name="參數"):
    if spec is None:
        return None
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, str):
        return spec
    rows = sorted((list(map(float, r)) for r in spec), key=lambda r: r[0])
    if T is None:
        raise ValueError(f"{name} 用溫度表設定，但這個 run 的溫度未知（請在 labels 或 temperatures 指定）")
    Ts = [r[0] for r in rows]
    cols = list(zip(*[r[1:] for r in rows]))
    vals = [float(np.interp(T, Ts, c)) for c in cols]
    return vals[0] if len(vals) == 1 else vals


def fmt_T(T):
    if T is None:
        return "?"
    if T < 1:
        return f"{T * 1000:g} mK"
    return f"{T:g} K"


# ---------------------------------------------------------------------------
# 繪圖小工具
# ---------------------------------------------------------------------------
def coord(fmt):
    """產生 ax.format_coord，fmt 例如 'I = {x:+.4f} uA, V = {y:+.4f} mV'"""
    return lambda x, y: fmt.format(x=x, y=y)


def enable_hover(fig, ax, x, y, fmt):
    """互動視窗中顯示離滑鼠最近的資料點（只在 --show 模式有作用）。"""
    x = np.asarray(x)
    y = np.asarray(y)
    if len(x) == 0:
        return
    text = ax.text(0.02, 0.98, "", transform=ax.transAxes, va="top", ha="left",
                   fontsize=10, bbox=dict(boxstyle="round", facecolor="white",
                                          edgecolor="gray", alpha=0.9), zorder=50)

    def on_move(event):
        if event.inaxes is not ax or event.xdata is None:
            if text.get_text():
                text.set_text("")
                fig.canvas.draw_idle()
            return
        k = int(np.argmin(np.abs(x - event.xdata)))
        text.set_text(fmt.format(x=x[k], y=y[k]))
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", on_move)


def safe_name(text):
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in str(text)).strip()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
