"""IV 轉折點：二次微分峰值找 4 個轉折（neg/pos × outer/inner），逐溫度追蹤，算 Ic（notebook 第 18、19 格）。

計算核心 track_transitions() 也給批次分析（batch/jobs/tseries.py）使用。
"""
import numpy as np
from scipy.signal import savgol_filter, find_peaks

from ..core.registry import P, Result, analysis
from ..core.common import clean_sort
from ._common import parse_floats

NAMES = ["neg outer", "neg inner", "pos inner", "pos outer"]


# ---------------------------------------------------------------------------
# 計算核心
# ---------------------------------------------------------------------------
def _prep_d2(I, V, window_length, poly_order):
    I, V = clean_sort(I, V)
    step = np.median(np.diff(I))
    I_grid = np.arange(I[0], I[-1] + step / 2, step)
    V_grid = np.interp(I_grid, I, V)
    window = min(window_length, len(I_grid) - (len(I_grid) + 1) % 2)
    V_s = savgol_filter(V_grid, window, poly_order)
    d2 = savgol_filter(V_grid, window, poly_order, deriv=2, delta=step)
    return I_grid, V_s, d2


def _find_one(I, V_s, d2, low, high):
    if not low < high:
        return np.nan, np.nan
    mask = (I >= low) & (I <= high)
    if mask.sum() < 3:
        return np.nan, np.nan
    sign = 1.0 if (low + high) > 0 else -1.0
    feature = sign * d2[mask]
    idx = np.flatnonzero(mask)
    peaks, _ = find_peaks(feature)
    peaks = peaks[feature[peaks] > 0]
    if len(peaks) == 0:
        return np.nan, np.nan
    best = idx[peaks[np.argmax(feature[peaks])]]
    return I[best], V_s[best]


def track_transitions(curves, initial_guess, window_length=15, poly_order=3,
                      first_half_width=0.12, step_in=0.20, step_out=0.05, min_gap=0.08):
    """curves: [(I_mA, V_mV), ...]，依溫度由低到高。每條找 4 個轉折，下一條以上一條為猜測。
    回傳 (all_I, all_V)，形狀 (N, 4)，單位 mA、mV。"""
    guesses = np.array(initial_guess, float)

    def search_range(g, first):
        if first:
            return g - first_half_width, g + first_half_width
        return (g - step_in, g + step_out) if g > 0 else (g - step_out, g + step_in)

    all_I, all_V = [], []
    for index, (I, V) in enumerate(curves):
        Ig, Vs, d2 = _prep_d2(I, V, window_length, poly_order)
        tI, tV = np.full(4, np.nan), np.full(4, np.nan)
        for outer, inner in [(0, 1), (3, 2)]:
            side = np.sign(guesses[outer])
            lo, hi = search_range(guesses[outer], index == 0)
            tI[outer], tV[outer] = _find_one(Ig, Vs, d2, lo, hi)
            lo, hi = search_range(guesses[inner], index == 0)
            limit = tI[outer] if np.isfinite(tI[outer]) else guesses[outer]
            if side > 0:
                lo, hi = max(lo, 0.0), min(hi, limit - min_gap)
            else:
                lo, hi = max(lo, limit + min_gap), min(hi, 0.0)
            tI[inner], tV[inner] = _find_one(Ig, Vs, d2, lo, hi)
        found = np.isfinite(tI)
        guesses[found] = tI[found]
        all_I.append(tI)
        all_V.append(tV)
    return np.array(all_I), np.array(all_V)


# ---------------------------------------------------------------------------
# 介面
# ---------------------------------------------------------------------------
@analysis("1D", "IV 轉折點 + Ic vs T（二次微分）", group="進階", icon="◆", order=120,
          modes=("current_bias",), params=[
    P("guess", "初始猜測 (mA)", "-0.85, -0.50, 0.53, 0.92",
      tip="最低溫那條的四個轉折：neg outer, neg inner, pos inner, pos outer"),
    P("window", "平滑點數", 15),
    P("order", "多項式階數", 3),
    P("first_hw", "第一條搜尋半寬 (mA)", 0.12),
    P("step_in", "往內搜尋 (mA)", 0.20),
    P("step_out", "往外搜尋 (mA)", 0.05),
    P("min_gap", "outer/inner 最小間距 (mA)", 0.08),
    P("offset", "IV 平移 (mV/條)", 0.5),
    P("ylim", "IV 的 Y 範圍 (mV，空白=自動)", "-0.7, 6.5"),
    P("to_zero", "找不到的轉折視為 0", False),
], help="依溫度由低到高排序後逐條追蹤。")
def iv_transitions(fig, items, p):
    items = sorted(items, key=lambda it: (it.T is None, it.T or 0, it.run_id))
    curves = []
    for it in items:
        I, V = it.iv()
        curves.append((I / 1e-3, V / 1e-3))
    all_I, all_V = track_transitions(curves, parse_floats(p["guess"], 4, "初始猜測"),
                                     p["window"], p["order"], p["first_hw"], p["step_in"],
                                     p["step_out"], p["min_gap"])
    if p["to_zero"]:
        all_I[~np.isfinite(all_I)] = 0.0
    gs = fig.add_gridspec(2, 2, width_ratios=[1.6, 1])
    ax_iv = fig.add_subplot(gs[:, 0])
    ax_tr = fig.add_subplot(gs[0, 1])
    ax_ic = fig.add_subplot(gs[1, 1])
    lines, rows = [], []
    for k, it in enumerate(items):
        I, V = curves[k]
        off = k * p["offset"]
        ax_iv.plot(I, V + off, color=it.color, lw=1.2, label=it.label)
        ax_iv.scatter(all_I[k], all_V[k] + off, s=30, facecolors="none",
                      edgecolors=it.color, linewidths=1.3, zorder=5)
        rows.append([it.run_id, it.T, *all_I[k]])
    xs = np.array([it.T if it.T is not None else it.run_id for it in items], float)
    xlab = "T (K)" if all(it.T is not None for it in items) else "run"
    for j, n in enumerate(NAMES):
        ax_tr.plot(xs, all_I[:, j], "o-", ms=4, label=n)
    ax_tr.axhline(0, color="gray", lw=0.8)
    ax_tr.set_ylabel("Transition I (mA)")
    ax_tr.legend(fontsize=7)
    Ic_out = all_I[:, 3] - all_I[:, 0]
    Ic_in = all_I[:, 2] - all_I[:, 1]
    ax_ic.plot(xs, Ic_out, "o-", ms=4, label="Ic outer")
    ax_ic.plot(xs, Ic_in, "o-", ms=4, label="Ic inner")
    ax_ic.set_xlabel(xlab)
    ax_ic.set_ylabel("Ic (mA)")
    ax_ic.legend(fontsize=7)
    ax_iv.set_xlabel("I (mA)")
    ax_iv.set_ylabel("V (mV)")
    if str(p["ylim"]).strip():
        ax_iv.set_ylim(*parse_floats(p["ylim"], 2, "Y 範圍"))
    for k, it in enumerate(items):
        lines.append(f"{it.label:<14} " + "  ".join(f"{n} {c:+.4f}" for n, c in zip(NAMES, all_I[k]))
                     + f" mA   Ic outer {Ic_out[k]:+.4f}  Ic inner {Ic_in[k]:+.4f}")
    return Result(text="\n".join(lines), export={"transitions": (
        ["run_id", "T_K", *[f"{n} (mA)" for n in NAMES]], rows)})
