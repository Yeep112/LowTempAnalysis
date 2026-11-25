"""微分：I→V 為 dV/dI（Ω），V→I 為 dI/dV（S）；也可以畫倒數（例如 V→I 的 dV/dI）。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ..core.measurement import si_scale, derivative_unit
from ._common import (LAYOUTS, METHODS, PREFIXES, curves_of, axis_scale, make_axes, finish,
                      compute_derivative, derivative_label)

DIRECTIONS = ["dY/dX（I→V 為 dV/dI、V→I 為 dI/dV）", "dX/dY（倒數）"]

# 給其他分析共用的參數
DERIV_PARAMS = [
    P("method", "微分方法", METHODS[0], METHODS),
    P("window", "平滑點數（SG）", 11),
    P("order", "多項式階數（SG）", 3),
    P("merge", "同一掃描值取平均", True),
    P("direction", "微分方向", DIRECTIONS[0], DIRECTIONS),
    P("ylog", "Y 軸用 log10", False),
]


def derivatives(curves, p):
    """回傳 [(x SI, d SI)]、軸標籤、縮放用的除數"""
    inverse = p["direction"] == DIRECTIONS[1]
    out = []
    for c in curves:
        x, d = compute_derivative(c.x.values, c.y.values, p["method"], p["window"], p["order"],
                                  p["merge"])
        if inverse:
            with np.errstate(divide="ignore", invalid="ignore"):
                d = 1.0 / d
            d[~np.isfinite(d)] = np.nan
        out.append((x, d))
    c0 = curves[0]
    num, den = (c0.x, c0.y) if inverse else (c0.y, c0.x)
    unit = derivative_unit(num.unit, den.unit)
    if p["ylog"]:
        return out, derivative_label(c0, inverse, log=True), None
    allv = np.concatenate([d for _, d in out if len(d)]) if out else np.array([])
    sd, u = si_scale(allv, unit)
    return out, derivative_label(c0, inverse, unit_text=u), sd


def transform(d, sd):
    """log 模式 sd=None：取 log10（負值與 0 去掉）"""
    if sd is None:
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log10(np.where(d > 0, d, np.nan))
    return d / sd


@analysis("1D", "微分 dV/dI · dI/dV", group="基本", icon="∂", order=20, params=[
    P("xpre", "X 軸單位前綴", "自動", PREFIXES),
    P("layout", "排列", LAYOUTS[0], LAYOUTS),
    *DERIV_PARAMS,
    P("offset", "疊圖平移（Y 軸單位/條）", 0.0),
], help="相鄰點差分 = notebook 做法；Savitzky-Golay = 先平滑再微分，雜訊較小。")
def derivative(fig, items, p):
    curves = curves_of(items)
    sx, xl = axis_scale([c.x for c in curves], p["xpre"])
    ders, yl, sd = derivatives(curves, p)
    overlay = p["layout"] == LAYOUTS[0]
    axes, used = make_axes(fig, len(items), p["layout"])
    rows, lines = [], []
    for k, (it, (x, d), ax) in enumerate(zip(items, ders, axes)):
        if ax is None or not len(x):
            continue
        off = k * p["offset"] if overlay else 0.0
        ax.plot(x / sx, transform(d, sd) + off, color=it.color, label=it.label, lw=1.2)
        if not overlay:
            ax.set_title(it.label, fontsize=9)
        rows += [[it.run_id, it.T, a, b] for a, b in zip(x, d)]
        center = np.abs(x) <= 0.05 * np.nanmax(np.abs(x))
        zero = np.nanmedian(d[center]) if center.any() else np.nan
        k_max = int(np.nanargmax(d)) if np.isfinite(d).any() else 0
        lines.append(f"run {it.run_id:>4}  {it.label:<14} 零偏壓附近 {zero:.5g}   "
                     f"最大 {d[k_max]:.5g} @ {x[k_max] / sx:+.4g}   （SI 單位）")
    finish(used, xl, yl, overlay, len(items))
    return Result(text="\n".join(lines), export={"derivative": (
        ["run_id", "T_K", "x (SI)", "derivative (SI)"], rows)})
