"""2D（磁場 x 電流）分析：
  field_map        V(B, I) 色圖，可自動找 B0（正向切換電流最大處）並平移
  field_dvdi_map   dV/dI(B, I) 色圖
  field_cuts       指定磁場切 IV，找 transition point 與 bulk transition，畫 I vs B
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colormaps

from . import register
from ...core.common import coord
from ...analysis2d._common import switching_current, find_B0  # noqa: F401
from ...analysis2d.cuts import (find_transition_point, bulk_transition, bulk_in_window,  # noqa: F401
                               detect_cut_transitions, _CUT_DEFAULTS, select_nearest)

_AXIS_LABEL = {"x_field": r"$B_x$", "y_field": r"$B_\perp$", "z_field": r"$B_z$"}


def _field_label(ds, job):
    return job.get("field_label") or _AXIS_LABEL.get(ds.field_name(), "B")


# ---- B0：正向切換電流最大的磁場 ----
def _search_mT(job):
    s = job.get("B0_search_mT")
    if not s:
        return None
    return [None if (x is None or str(x).lower() == "none") else float(x) for x in s]


@register("field_map", "V(B, I) 色圖；shift_B0 = true 時以 B0 為原點")
def field_map(ctx, job):
    for r in ctx.resolve_runs(job):
        rid = r["run_id"]
        ds = ctx.db.load(rid)
        B, I, V = ds.grid(y=job.get("voltage"))
        ctx.log(f"run {rid}: {B.shape[0]} fields x {B.shape[1]} currents, "
                f"B = {B.min() / 1e-3:.2f} ~ {B.max() / 1e-3:.2f} mT")
        label = _field_label(ds, job)
        B0 = 0.0
        if job.get("shift_B0", False):
            B0, Ic_peak, _ = find_B0(B, I, V, float(job.get("B0_threshold_mV", 0.5)), _search_mT(job))
            ctx.log(f"  Peak: Ic = {Ic_peak / 1e-6:.1f} uA at B0 = {B0 / 1e-3:.4f} mT")
        a, b = job.get("field_index_range", [0, B.shape[0]])
        Bp, Ip, Vp = (B[a:b] - B0) / 1e-3, I[a:b] / 1e-6, V[a:b] / 1e-3
        fig, ax = plt.subplots(figsize=(9, 7), constrained_layout=True)
        mesh = ax.pcolormesh(Bp, Ip, Vp, shading="auto", cmap=job.get("cmap", "magma"),
                             vmin=job.get("vmin"), vmax=job.get("vmax"))
        cbar = fig.colorbar(mesh, ax=ax)
        cbar.set_label("V (mV)", fontsize=18)
        cbar.ax.tick_params(labelsize=16)
        if job.get("shift_B0", False):
            if job.get("mark_B0", True):
                ax.axvline(0, color="cyan", ls="--", lw=1, alpha=0.6)
            ax.set_xlabel(label + r"$-B_0$ (mT)", fontsize=18)
        else:
            ax.set_xlabel(label + " (mT)", fontsize=18)
        ax.set_ylabel(r"I ($\mu$A)", fontsize=18)
        ax.tick_params(labelsize=16)
        if job.get("ylim"):
            ax.set_ylim(*job["ylim"])
        if job.get("xlim"):
            ax.set_xlim(*job["xlim"])
        ax.set_title(f"run {rid}  {r['info'].exp_name}", fontsize=10)
        Baxis, Iaxis = Bp[:, 0], Ip[0, :]

        def fmt(x, y, Baxis=Baxis, Iaxis=Iaxis, Vp=Vp):
            i = int(np.argmin(np.abs(Baxis - x)))
            j = int(np.argmin(np.abs(Iaxis - y)))
            return f"B = {x:+.4f} mT, I = {y:+.3f} uA, V = {Vp[i, j]:+.4f} mV"
        ax.format_coord = fmt
        suffix = "_shifted" if job.get("shift_B0", False) else ""
        ctx.save(fig, f"field_map_run{rid}{suffix}")


@register("field_dvdi_map", "dV/dI(B, I) 色圖")
def field_dvdi_map(ctx, job):
    for r in ctx.resolve_runs(job):
        rid = r["run_id"]
        ds = ctx.db.load(rid)
        B, I, V = ds.grid(y=job.get("voltage"))
        with np.errstate(divide="ignore", invalid="ignore"):
            d = np.diff(V, axis=1) / np.diff(I, axis=1)
        B0 = 0.0
        if job.get("shift_B0", False):
            B0, _, _ = find_B0(B, I, V, float(job.get("B0_threshold_mV", 0.5)), _search_mT(job))
            ctx.log(f"run {rid}: B0 = {B0 / 1e-3:.4f} mT")
        a, b = job.get("field_index_range", [0, B.shape[0]])
        X = (B[a:b, :-1] - B0) / 1e-3
        Y = I[a:b, :-1] / 1e-6
        Z = d[a:b]
        fig, ax = plt.subplots(figsize=(10, 8))
        im = ax.pcolormesh(X, Y, Z, cmap=job.get("cmap", "hot"), shading="auto",
                           vmin=job.get("vmin", 0), vmax=job.get("vmax", 1))
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label(r"dV/dI ($\Omega$)", fontsize=22)
        cbar.ax.tick_params(labelsize=20)
        label = _field_label(ds, job) + (r"$-B_0$" if job.get("shift_B0", False) else "")
        ax.set_xlabel(label + " (mT)", fontsize=22)
        ax.set_ylabel(r"I ($\mu$A)", fontsize=22)
        ax.tick_params(labelsize=20)
        if job.get("ylim"):
            ax.set_ylim(*job["ylim"])
        ax.format_coord = coord("B = {x:+.4f} mT, I = {y:+.3f} uA")
        fig.tight_layout()
        ctx.save(fig, f"field_dVdI_map_run{rid}")


# ---- field_cuts ----
@register("field_cuts", "指定磁場切 IV：transition point + bulk transition，畫 I vs B")
def field_cuts(ctx, job):
    p = {k: type(v)(job.get(k, v)) for k, v in _CUT_DEFAULTS.items()}
    t_label = job.get("transition_label", "transition point")
    b_label = job.get("bulk_label", "bulk transition")
    use_bulk = job.get("bulk", True)

    for r in ctx.resolve_runs(job):
        rid = r["run_id"]
        ds = ctx.db.load(rid)
        B, I, V = ds.grid(y=job.get("voltage"))
        B_list = B[:, 0]
        ctx.log(f"run {rid}: {B.shape[0]} fields x {B.shape[1]} currents, "
                f"B = {B_list.min() / 1e-3:.2f} ~ {B_list.max() / 1e-3:.2f} mT")
        if job.get("shift_B0", True):
            B0, Ic_peak, _ = find_B0(B, I, V, float(job.get("B0_threshold_mV", 0.5)), _search_mT(job))
            ctx.log(f"  B0 = {B0 / 1e-3:.4f} mT  (switching current {Ic_peak / 1e-6:.1f} uA)")
        else:
            B0 = 0.0
        B_shift_mT = np.round((B_list - B0) / 1e-3, 6) + 0.0

        targets = job.get("fields_mT")
        if not targets:
            raise ValueError("field_cuts 需要 fields_mT = [...]")
        if isinstance(targets, dict):   # {start, stop, step}
            targets = list(np.arange(targets["start"], targets["stop"] + targets["step"] / 2,
                                     targets["step"]))
        tol = job.get("field_tolerance_mT", "auto")
        tol = None if str(tol).lower() in ("auto", "none", "") else float(tol)
        sel, notes, skipped, spacing = select_nearest(B_shift_mT, targets, tol)
        for n in notes:
            ctx.log("  " + n)
        for t in skipped:
            ctx.log(f"  target {t:+.2f} mT: {tol:.2f} mT 內沒有資料點，略過")
        if not sel:
            raise ValueError("沒有找到任何指定磁場")
        ctx.log(f"  選到 {len(sel)} 個磁場（資料間距 {spacing:.4g} mT）")
        flabel = _field_label(ds, job)

        trans, same = detect_cut_transitions(I, V, sel, B_shift_mT, p, use_bulk)

        ctx.log(f"  Transitions (I > 0): [{t_label} | {b_label}]")
        for row, k in enumerate(sel):
            _, It, _, Ib, _ = trans[row]
            tt = f"{It / 1e-6:8.2f} uA" if np.isfinite(It) else "   not found"
            bt = f"{Ib / 1e-6:8.2f} uA" if np.isfinite(Ib) else "   not found"
            note = "  (bulk searched near peak)" if same[row] else ""
            ctx.log(f"    B - B0 = {B_shift_mT[k]:+.4f} mT : {tt} | {bt}{note}")

        tag = f"_run{rid}"
        ctx.save_csv(f"transition_current_vs_field{tag}",
                     ["B-B0 (mT)", f"I {t_label} (uA)", f"V {t_label} (mV)",
                      f"I {b_label} (uA)", f"V {b_label} (mV)"],
                     [[f"{a:.6g}", f"{b_ / 1e-6:.4f}", f"{c / 1e-3:.5f}",
                       f"{d / 1e-6:.4f}", f"{e / 1e-3:.5f}"] for a, b_, c, d, e in trans])

        colors = colormaps["turbo"].resampled(max(len(sel), 2))
        fcol = {k: colors(i) for i, k in enumerate(sel)}

        # 檢查圖
        fig, ax = plt.subplots(figsize=(7, 5))
        mesh = ax.pcolormesh(B / 1e-3, I / 1e-6, V / 1e-3, cmap="magma", shading="auto")
        for k in sel:
            ax.axvline(B_list[k] / 1e-3, color="cyan", lw=0.6)
        ax.axvline(B0 / 1e-3, color="red", lw=1.5, label=f"B0 = {B0 / 1e-3:.3f} mT")
        B_raw = trans[:, 0] + B0 / 1e-3
        ax.plot(B_raw, trans[:, 1] / 1e-6, "o", ms=4, mfc="none", mec="white", mew=1, label=t_label)
        if use_bulk:
            ax.plot(B_raw, trans[:, 3] / 1e-6, "s", ms=4, mfc="none", mec="cyan", mew=1, label=b_label)
        ax.set_xlabel("B (mT)", fontsize=12)
        ax.set_ylabel(r"I ($\mu$A)", fontsize=12)
        ax.set_title(f"run {rid}", fontsize=12)
        ax.legend(loc="lower right", fontsize=9)
        fig.colorbar(mesh, ax=ax, label="V (mV)")
        fig.tight_layout()
        ctx.save(fig, f"check_map{tag}")

        # 個別 IV
        n = len(sel)
        nc = min(3, n)
        nr = int(np.ceil(n / nc))
        fig, axes = plt.subplots(nr, nc, figsize=(5 * nc, 3.8 * nr), squeeze=False)
        for i, k in enumerate(sel):
            ax = axes[i // nc, i % nc]
            _, It, Vt, Ib, Vb = trans[i]
            ax.plot(I[k] / 1e-6, V[k] / 1e-3, lw=1.5, color=fcol[k])
            if np.isfinite(It):
                ax.plot(It / 1e-6, Vt / 1e-3, "o", ms=8, mfc="none", mec="red", mew=1.8, zorder=5)
                ax.annotate(f"{It / 1e-6:.0f} μA", (It / 1e-6, Vt / 1e-3), xytext=(-8, 8),
                            textcoords="offset points", ha="right", fontsize=9, color="red")
            if np.isfinite(Ib):
                ax.plot(Ib / 1e-6, Vb / 1e-3, "s", ms=8, mfc="none", mec="blue", mew=1.8, zorder=5)
                ax.annotate(f"{Ib / 1e-6:.0f} μA", (Ib / 1e-6, Vb / 1e-3), xytext=(8, -14),
                            textcoords="offset points", ha="left", fontsize=9, color="blue")
            ax.set_title(f"B - B0 = {B_shift_mT[k]:+.4f} mT", fontsize=12)
            ax.set_xlabel(r"I ($\mu$A)", fontsize=12)
            ax.set_ylabel("V (mV)", fontsize=12)
            ax.tick_params(labelsize=10)
        for i in range(n, nr * nc):
            axes[i // nc, i % nc].axis("off")
        fig.tight_layout()
        ctx.save(fig, f"IV_selected_fields{tag}")

        # 疊圖
        fig, ax = plt.subplots(figsize=(7, 5))
        for i, k in enumerate(sel):
            _, It, Vt, Ib, Vb = trans[i]
            ax.plot(I[k] / 1e-6, V[k] / 1e-3, lw=1.2, color=fcol[k], label=f"{B_shift_mT[k]:+.2f} mT")
            if np.isfinite(It):
                ax.plot(It / 1e-6, Vt / 1e-3, "o", ms=6, mfc="none", mec=fcol[k], mew=1.5, zorder=5)
            if np.isfinite(Ib):
                ax.plot(Ib / 1e-6, Vb / 1e-3, "s", ms=6, mfc="none", mec=fcol[k], mew=1.5, zorder=5)
        ax.set_xlabel(r"I ($\mu$A)", fontsize=14)
        ax.set_ylabel("V (mV)", fontsize=14)
        ax.tick_params(labelsize=12)
        ax.legend(title=flabel + r"$-B_0$", fontsize=7, title_fontsize=9, ncol=3)
        fig.tight_layout()
        ctx.save(fig, f"IV_field_dependence_overlay{tag}")

        # I vs B
        fig, ax = plt.subplots(figsize=(7.5, 5))
        cols = [(1, "-")] + ([(3, "--")] if use_bulk else [])
        for c, ls in cols:
            ok = np.isfinite(trans[:, c])
            ax.plot(trans[ok, 0], trans[ok, c] / 1e-6, ls, color="0.7", lw=1, zorder=1)
        handles = []
        for i, k in enumerate(sel):
            Bm, It, _, Ib, _ = trans[i]
            if np.isfinite(It):
                ax.scatter(Bm, It / 1e-6, s=60, marker="o", color=fcol[k], edgecolors="black",
                           linewidths=0.6, zorder=4)
            if np.isfinite(Ib):
                ax.scatter(Bm, Ib / 1e-6, s=55, marker="s", color=fcol[k], edgecolors="black",
                           linewidths=0.6, zorder=3)
            if np.isfinite(It) or np.isfinite(Ib):
                handles.append(plt.Line2D([], [], marker="o", ls="", ms=7, markerfacecolor=fcol[k],
                                          markeredgecolor="black", markeredgewidth=0.6,
                                          label=f"{Bm:+.2f} mT"))
        types = [plt.Line2D([], [], marker="o", ls="-", color="0.6", ms=7, markerfacecolor="white",
                            markeredgecolor="black", label=t_label)]
        if use_bulk:
            types.append(plt.Line2D([], [], marker="s", ls="--", color="0.6", ms=7,
                                    markerfacecolor="white", markeredgecolor="black", label=b_label))
        leg = ax.legend(handles=types, fontsize=9, loc="best")
        ax.add_artist(leg)
        ax.legend(handles=handles, title=flabel, fontsize=8, title_fontsize=9,
                  ncol=max(1, int(np.ceil(len(handles) / 15))), bbox_to_anchor=(1.02, 1),
                  loc="upper left")
        ax.set_xlabel(flabel + r"$-B_0$ (mT)", fontsize=14)
        ax.set_ylabel(r"Transition current $I$ (μA)", fontsize=14)
        ax.tick_params(labelsize=12)
        fig.tight_layout()
        ctx.save(fig, f"transition_current_vs_field{tag}")
