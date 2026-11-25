"""快速查看單一 run：1D 畫曲線 + 微分；2D 畫量測值色圖 + 微分色圖。支援任何量測模式。"""
from __future__ import annotations

import os

import numpy as np
import matplotlib.pyplot as plt

from .core.common import clean_sort, derivative, coord, enable_hover, ensure_dir
from .core.measurement import si_scale, derivative_unit


def quick_view(db, run_id=None, show=True, save_dir=None, log=print):
    runs = db.runs()
    if run_id is None:
        run_id = runs[-1].run_id
    info = db.info(run_id)
    ds = db.load(run_id)
    log(f"{db.name}  run {run_id}: {info.exp_name}  [{info.mode_short}]  ({info.n_points} 點, {info.start})")
    if info.ndim >= 2:
        m = ds.map2d()
        S, sl = m.slow.scaled()
        F, fl = m.fast.scaled()
        Z, zl = m.z.scaled()
        with np.errstate(divide="ignore", invalid="ignore"):
            D = np.diff(m.z.values, axis=1) / np.diff(m.fast.values, axis=1)
        pd, ud = si_scale(D, derivative_unit(m.z.unit, m.fast.unit))
        fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
        m0 = axes[0].pcolormesh(S, F, Z, cmap="magma", shading="auto")
        fig.colorbar(m0, ax=axes[0], label=zl)
        lo, hi = np.nanpercentile(D / pd, [1, 99])
        m1 = axes[1].pcolormesh(S[:, :-1], (F[:, :-1] + F[:, 1:]) / 2, D / pd, cmap="hot",
                                shading="auto", vmin=lo, vmax=hi)
        fig.colorbar(m1, ax=axes[1], label=f"d{m.z.sym}/d{m.fast.sym} ({ud})")
        for ax in axes:
            ax.set_xlabel(sl)
            ax.set_ylabel(fl)
        log(f"  {S.shape[0]} × {S.shape[1]} 點")
    else:
        c = ds.curve()
        x, xl = c.x.scaled()
        y, yl = c.y.scaled()
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        axes[0].plot(x, y, ".-", ms=2, lw=1)
        axes[0].set_xlabel(xl)
        axes[0].set_ylabel(yl)
        xs, ys = clean_sort(c.x.values, c.y.values)
        xm, d = derivative(xs, ys)
        pd, ud = si_scale(d, derivative_unit(c.y.unit, c.x.unit))
        px = x[0] / c.x.values[0] if c.x.values[0] else 1.0
        axes[1].plot(xm * px, d / pd, lw=1, color="tab:red")
        axes[1].set_xlabel(xl)
        axes[1].set_ylabel(f"d{c.y.sym}/d{c.x.sym} ({ud})")
        slope = np.polyfit(xs, ys, 1)[0]
        log(f"  直線斜率 d{c.y.sym}/d{c.x.sym} = {slope:.6g} {derivative_unit(c.y.unit, c.x.unit)}")
        if show:
            enable_hover(fig, axes[0], x, y, "x = {x:+.4f}\ny = {y:+.4f}")
        for ax in axes:
            ax.format_coord = coord("x = {x:+.5g}, y = {y:+.5g}")
    fig.suptitle(f"run {run_id}   {info.exp_name}", fontsize=11)
    fig.tight_layout()
    save_dir = save_dir or os.path.join(db.folder, "01 Figure", "quick")
    path = os.path.join(ensure_dir(save_dir), f"run{run_id}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    log(f"  已存圖 {path}")
    if show:
        plt.show()
    plt.close(fig)
    return path
