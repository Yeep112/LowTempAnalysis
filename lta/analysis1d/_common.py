"""1D 分析共用工具：排版、微分、文字參數解析。"""
from __future__ import annotations

import numpy as np
from scipy.signal import savgol_filter

from ..core.common import clean_sort, derivative
from ..core.measurement import si_scale, derivative_unit, PREFIX_CHOICES

LAYOUTS = ["疊圖", "分開（子圖）"]
STYLES = ["線", "點", "線+點"]
METHODS = ["相鄰點差分", "Savitzky-Golay 平滑微分"]
PREFIXES = PREFIX_CHOICES


# ---------------------------------------------------------------------------
# 取資料
# ---------------------------------------------------------------------------
def curves_of(items):
    """取所有 item 的 Curve，並確認量測方式一致（疊圖時單位才有意義）。"""
    curves = [it.curve() for it in items]
    kinds = {(c.x.unit, c.y.unit) for c in curves}
    if len(kinds) > 1:
        raise ValueError("選取的資料量測方式不同（例如同時有 I→V 與 V→I），請分開分析")
    return curves


def axis_scale(axes, choice="自動"):
    """多條曲線共用的縮放：回傳 (除數, 軸標籤)。axes 是 Axis 的 list。"""
    vals = np.concatenate([a.values for a in axes]) if axes else np.array([])
    p, u = si_scale(vals, axes[0].unit, choice)
    sym = axes[0].sym
    return p, (f"{sym} ({u})" if u else sym)


# ---------------------------------------------------------------------------
# 微分
# ---------------------------------------------------------------------------
def compute_derivative(x, y, method=METHODS[0], window=11, order=3, merge=True):
    """dy/dx。回傳 (x 中點或格點, dy/dx)，單位與輸入相同（SI）。"""
    x, y = clean_sort(x, y, merge)
    if len(x) < 3:
        return np.array([]), np.array([])
    if method == METHODS[0]:
        return derivative(x, y)
    step = np.median(np.diff(x))
    grid = np.arange(x[0], x[-1] + step / 2, step)
    yg = np.interp(grid, x, y)
    w = int(window) + (int(window) % 2 == 0)
    w = max(w, order + 2 + (order % 2 == 0))
    w = min(w, len(grid) - (1 - len(grid) % 2))
    if w <= order:
        return derivative(x, y)
    return grid, savgol_filter(yg, w, int(order), deriv=1, delta=step)


def derivative_label(c, inverse=False, log=False, unit_text=None):
    """例如 'dV/dI (Ω)'、'dI/dV (μS)'、'log10(dV/dI / Ω)'"""
    num, den = (c.x, c.y) if inverse else (c.y, c.x)
    name = f"d{num.sym}/d{den.sym}"
    unit = unit_text or derivative_unit(num.unit, den.unit)
    if log:
        return f"log10({name} / {derivative_unit(num.unit, den.unit)})"
    return f"{name} ({unit})" if unit else name


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def make_axes(fig, n, layout, sharex=True):
    """疊圖 → 同一個 ax 重複 n 次；分開 → 子圖格子。回傳 (每筆對應的 ax, 用到的 ax)"""
    if layout == LAYOUTS[0] or n == 1:
        ax = fig.add_subplot(111)
        return [ax] * n, [ax]
    n_show = min(n, 25)
    nc = int(np.ceil(np.sqrt(n_show)))
    nr = int(np.ceil(n_show / nc))
    axes = fig.subplots(nr, nc, squeeze=False, sharex=sharex)
    flat = list(axes.ravel())
    for ax in flat[n_show:]:
        ax.axis("off")
    return flat[:n_show] + [None] * (n - n_show), flat[:n_show]


def style_kw(style):
    return {"線": dict(ls="-", marker=None), "點": dict(ls="none", marker=".", ms=3),
            "線+點": dict(ls="-", marker=".", ms=3)}[style]


def finish(axs, xlabel, ylabel, overlay, n):
    for ax in axs:
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
    if overlay and n > 1:
        axs[0].legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1),
                      borderaxespad=0, ncol=1 if n <= 20 else 2)


# ---------------------------------------------------------------------------
# 文字參數
# ---------------------------------------------------------------------------
def parse_floats(text, n=None, name="數值"):
    vals = [float(x) for x in str(text).replace("，", ",").replace(" ", ",").split(",") if x.strip()]
    if n is not None and len(vals) != n:
        raise ValueError(f"「{name}」需要 {n} 個數字，用逗號分開")
    return vals


def parse_T_table(text, n_values, name="參數"):
    """'a, b' 或 '1.6: a, b; 2.8: c, d'（依溫度內插）→ 函式 f(T) -> list"""
    text = str(text).strip()
    if ":" not in text:
        vals = parse_floats(text, n_values, name)
        return lambda T: vals
    rows = []
    for part in text.replace("；", ";").split(";"):
        if part.strip():
            T, rest = part.split(":", 1)
            rows.append([float(T)] + parse_floats(rest, n_values, name))
    rows.sort()
    Ts = [r[0] for r in rows]

    def f(T):
        if T is None:
            return rows[0][1:]
        return [float(np.interp(T, Ts, [r[i] for r in rows])) for i in range(1, n_values + 1)]
    return f


# ---------------------------------------------------------------------------
# 雜訊修補
# ---------------------------------------------------------------------------
def parse_ranges(text):
    """'-505:-495, 300:310' → [(-505, -495), (300, 310)]（也接受 ~ 當分隔）"""
    out = []
    for part in str(text or "").replace("，", ",").replace("；", ",").replace(";", ",").split(","):
        part = part.strip().replace("~", ":")
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"修補區間格式是「起:終」，例如 -505:-495，收到：{part}")
        a, b = (float(x) for x in part.split(":", 1))
        out.append((min(a, b), max(a, b)))
    return out


def patch_regions(x, y, ranges):
    """把 x 在每個區間內的 y 換成區間兩端的直線內插（x 需已排序）。回傳新的 y 與被修補的點數。"""
    y = np.array(y, float)
    n = 0
    for a, b in ranges:
        inside = (x >= a) & (x <= b)
        if not inside.any():
            continue
        idx = np.flatnonzero(inside)
        lo, hi = idx[0] - 1, idx[-1] + 1
        if lo < 0 and hi >= len(x):
            continue
        if lo < 0:
            y[inside] = y[hi]
        elif hi >= len(x):
            y[inside] = y[lo]
        else:
            y[inside] = np.interp(x[inside], [x[lo], x[hi]], [y[lo], y[hi]])
        n += int(inside.sum())
    return y, n
