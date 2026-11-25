"""曲線：原始量測資料（I→V 畫 V vs I；V→I 畫 I vs V），保留掃描順序可看遲滯。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ..core.measurement import derivative_unit
from ._common import (LAYOUTS, STYLES, PREFIXES, curves_of, axis_scale, make_axes,
                      style_kw, finish)


@analysis("1D", "曲線（IV / VI）", group="基本", icon="∿", order=10, params=[
    P("xpre", "X 軸單位前綴", "自動", PREFIXES),
    P("ypre", "Y 軸單位前綴", "自動", PREFIXES),
    P("layout", "排列", LAYOUTS[0], LAYOUTS),
    P("offset", "疊圖平移（Y 軸單位/條）", 0.0),
    P("style", "線型", STYLES[0], STYLES),
], help="原始資料：X = 施加的量、Y = 量到的值。")
def curve(fig, items, p):
    curves = curves_of(items)
    sx, xl = axis_scale([c.x for c in curves], p["xpre"])
    sy, yl = axis_scale([c.y for c in curves], p["ypre"])
    overlay = p["layout"] == LAYOUTS[0]
    axes, used = make_axes(fig, len(items), p["layout"])
    rows, lines = [], []
    for k, (it, c, ax) in enumerate(zip(items, curves, axes)):
        if ax is None:
            continue
        off = k * p["offset"] if overlay else 0.0
        ax.plot(c.x.values / sx, c.y.values / sy + off, color=it.color, label=it.label, lw=1.3,
                **style_kw(p["style"]))
        if not overlay:
            ax.set_title(it.label, fontsize=9)
        rows += [[it.run_id, it.T, a, b] for a, b in zip(c.x.values, c.y.values)]
        slope = np.polyfit(c.x.values, c.y.values, 1)[0] if len(c.x.values) > 1 else np.nan
        lines.append(f"run {it.run_id:>4}  {it.label:<14} {c.mode_short}  {len(c.x.values):>6} 點  "
                     f"{c.x.sym}: {c.x.values.min() / sx:+.4g} ~ {c.x.values.max() / sx:+.4g}   "
                     f"直線斜率 d{c.y.sym}/d{c.x.sym} = {slope:.5g} {derivative_unit(c.y.unit, c.x.unit)}")
    finish(used, xl, yl, overlay, len(items))
    c0 = curves[0]
    return Result(text="\n".join(lines), export={"data": (
        ["run_id", "T_K", f"{c0.x.name} ({c0.x.unit})", f"{c0.y.name} ({c0.y.unit})"], rows)})
