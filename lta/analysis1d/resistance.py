"""電阻：在指定範圍內直線擬合。I→V 擬合 V = R·I + V0；V→I 擬合 I = G·V + I0，R = 1/G。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import curves_of, axis_scale


@analysis("1D", "電阻 R（直線擬合）vs T", group="進階", icon="Ω", order=100,
          modes=("current_bias", "voltage_bias"), params=[
    P("lo", "擬合範圍下限（X 軸顯示單位，空白=全部）", ""),
    P("hi", "擬合範圍上限（X 軸顯示單位，空白=全部）", ""),
], help="範圍用 X 軸顯示的單位（例如 μA 或 mV）。")
def resistance(fig, items, p):
    curves = curves_of(items)
    sx, xl = axis_scale([c.x for c in curves])
    sy, yl = axis_scale([c.y for c in curves])
    lo = float(p["lo"]) * sx if str(p["lo"]).strip() else -np.inf
    hi = float(p["hi"]) * sx if str(p["hi"]).strip() else np.inf
    gs = fig.add_gridspec(1, 2, width_ratios=[1.3, 1])
    ax_c = fig.add_subplot(gs[0])
    ax_r = fig.add_subplot(gs[1])
    pts, lines, rows = [], [], []
    for it, c in zip(items, curves):
        x, y = c.x.values, c.y.values
        m = np.isfinite(x) & np.isfinite(y) & (x >= lo) & (x <= hi)
        if m.sum() < 2:
            lines.append(f"run {it.run_id}: 範圍內點數不足")
            continue
        slope, icpt = np.polyfit(x[m], y[m], 1)
        R = 1.0 / slope if c.mode == "voltage_bias" else slope
        ax_c.plot(x[m] / sx, y[m] / sy, ".", ms=3, color=it.color, label=it.label)
        xx = np.array([x[m].min(), x[m].max()])
        ax_c.plot(xx / sx, (slope * xx + icpt) / sy, "-", color=it.color, lw=1, gid="nohover")
        pts.append((it.T if it.T is not None else it.run_id, R, it.color))
        rows.append([it.run_id, it.T, R])
        lines.append(f"run {it.run_id:>4}  {it.label:<14} R = {R:.6g} Ω")
    if pts:
        pts.sort(key=lambda t: t[0])
        ax_r.plot([a for a, _, _ in pts], [b for _, b, _ in pts], "-", color="0.7", gid="nohover")
        for a, b, col in pts:
            ax_r.plot(a, b, "o", color=col, ms=6)
    ax_c.set_xlabel(xl)
    ax_c.set_ylabel(yl)
    ax_r.set_xlabel("T (K)" if all(it.T is not None for it in items) else "run")
    ax_r.set_ylabel("R (Ω)")
    return Result(text="\n".join(lines), export={"resistance": (["run_id", "T_K", "R_ohm"], rows)})
