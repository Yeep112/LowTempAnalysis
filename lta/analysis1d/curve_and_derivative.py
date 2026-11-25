"""曲線 + 微分 上下對照，X 軸連動縮放。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import PREFIXES, curves_of, axis_scale
from .derivative import DERIV_PARAMS, derivatives, transform


@analysis("1D", "曲線 + 微分 上下對照", group="基本", icon="⇅", order=30, params=[
    P("xpre", "X 軸單位前綴", "自動", PREFIXES),
    *DERIV_PARAMS,
    P("offset_c", "曲線平移（Y 單位/條）", 0.0),
    P("offset_d", "微分平移（Y 單位/條）", 0.0),
], help="上：原始曲線；下：微分。縮放上面那張，下面會跟著動。")
def curve_and_derivative(fig, items, p):
    curves = curves_of(items)
    sx, xl = axis_scale([c.x for c in curves], p["xpre"])
    sy, yl = axis_scale([c.y for c in curves])
    ders, dl, sd = derivatives(curves, p)
    ax1, ax2 = fig.subplots(2, 1, sharex=True)
    lines = []
    for k, (it, c, (x, d)) in enumerate(zip(items, curves, ders)):
        ax1.plot(c.x.values / sx, c.y.values / sy + k * p["offset_c"], color=it.color,
                 label=it.label, lw=1.2)
        if len(x):
            ax2.plot(x / sx, transform(d, sd) + k * p["offset_d"], color=it.color, label=it.label, lw=1.1)
            lines.append(f"run {it.run_id:>4}  {it.label:<14} 微分最大值 {np.nanmax(d):.5g}（SI）")
    ax1.set_ylabel(yl)
    ax2.set_ylabel(dl)
    ax2.set_xlabel(xl)
    if len(items) > 1:
        ax1.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1), borderaxespad=0)
    return Result(text="\n".join(lines))
