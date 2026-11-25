"""切換電流 vs 慢軸：每個慢軸位置（磁場、閘極…）從 I=0 往外走，|V − V(0)| 超過門檻前的電流。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import CMAPS, RANGES, load_view, add_map, color_limits, switching_current


@analysis("2D", "切換電流 Ic vs 慢軸", group="萃取", icon="↯", order=100, multi=False,
          modes=("current_bias",), params=[
    P("thr", "切換門檻 |V − V(0)| (mV)", 0.5),
    P("shift", "以切換電流最大處為慢軸原點（B₀）", True),
    P("negative", "也算負向 Ic−", True),
    P("cmap", "色盤", "magma", CMAPS),
], help="左：色圖與切換點；右：Ic+、|Ic−| 對慢軸。")
def switching(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"], p["thr"])
    m = v.m
    I, V = m.fast.values, m.z.values                       # SI
    thr = p["thr"] * 1e-3
    Icp = np.array([switching_current(I[k], V[k], thr) for k in range(len(I))])
    Icn = (np.array([switching_current(-I[k], -V[k], thr) for k in range(len(I))])
           if p["negative"] else np.full(len(I), np.nan))
    S = v.s_axis
    gs = fig.add_gridspec(1, 2, width_ratios=[1.3, 1])
    ax = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    add_map(fig, ax, v.S, v.F, v.Z, p["cmap"], color_limits(v.Z, RANGES[0]), v.z_label)
    ax.plot(S, Icp / v.f_scale, ".", color="cyan", ms=3, label="Ic+")
    if p["negative"]:
        ax.plot(S, -Icn / v.f_scale, ".", color="lime", ms=3, label="Ic−")
    ax.legend(fontsize=8, loc="lower right")
    ax.set_xlabel(v.s_label)
    ax.set_ylabel(v.f_label)
    o = np.argsort(S)
    ax2.plot(S[o], Icp[o] / 1e-6, "o-", ms=3, label="Ic+")
    if p["negative"]:
        ax2.plot(S[o], Icn[o] / 1e-6, "s-", ms=3, label="|Ic−|")
    ax2.set_xlabel(v.s_label)
    ax2.set_ylabel("Ic (μA)")
    ax2.legend(fontsize=8)
    lines = [v.note] if v.note else []
    if np.isfinite(Icp).any():
        k = int(np.nanargmax(Icp))
        lines.append(f"Ic+ 最大 {Icp[k] / 1e-6:.1f} μA @ {v.s_label} = {S[k]:+.4g}")
    if p["negative"] and np.isfinite(Icn).any():
        k = int(np.nanargmax(Icn))
        lines.append(f"|Ic−| 最大 {Icn[k] / 1e-6:.1f} μA @ {v.s_label} = {S[k]:+.4g}")
    rows = [[s, a / 1e-6, b / 1e-6] for s, a, b in zip(S, Icp, Icn)]
    return Result(text="\n".join(lines),
                  export={"Ic": ([v.s_label, "Ic+ (uA)", "|Ic-| (uA)"], rows)})
