"""1D 分析：IV、dV/dI、兩側線性化、電阻。"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colormaps

from . import register
from ...core.common import (clean_sort, derivative, linearize_edges, by_T, coord,
                      enable_hover)

_SCALE = {"uA": 1e-6, "mA": 1e-3, "nA": 1e-9, "A": 1.0}
_LABEL = {"uA": r"I ($\mu$A)", "mA": "I (mA)", "nA": "I (nA)", "A": "I (A)"}


def get_edges(job, r):
    """負/正側 edge（uA）：edges_by_run 優先，其次 edges 溫度表。"""
    by_run = {int(k): v for k, v in (job.get("edges_by_run") or {}).items()}
    if r["run_id"] in by_run:
        neg, pos = by_run[r["run_id"]]
        return float(neg), float(pos)
    if job.get("edges") is not None:
        neg, pos = by_T(job["edges"], r["T"], "edges")
        return neg, pos
    raise ValueError(f"[{job['name']}] run {r['run_id']} 沒有設定 edges "
                     "（edges = [[T, neg_uA, pos_uA], ...] 或 edges_by_run）")


def _colors(n, cmap="tab20"):
    return colormaps[cmap].resampled(max(n, 2))


@register("iv", "IV 曲線（個別圖 + 疊圖）")
def iv(ctx, job):
    runs = ctx.resolve_runs(job)
    unit = job.get("x_unit", "uA")
    sx = _SCALE[unit]
    offset = float(job.get("offset_mV", 0.0))
    individual = job.get("individual", True)
    overlay = job.get("overlay", len(runs) > 1)
    colors = _colors(len(runs), job.get("cmap", "tab20"))
    rows = []

    if overlay:
        fig_o, ax_o = plt.subplots(figsize=(8, 5))
    for k, r in enumerate(runs):
        ds = ctx.db.load(r["run_id"])
        I, V = ds.xy(y=job.get("voltage"))
        x, y = I / sx, V / 1e-3
        if job.get("export_csv"):
            rows += [[r["run_id"], r["T"], a, b] for a, b in zip(I, V)]
        if individual:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(x, y)
            ax.set_xlabel(_LABEL[unit], fontsize=18)
            ax.set_ylabel("V (mV)", fontsize=18)
            ax.set_title(f"run {r['run_id']}  {r['label']}", fontsize=13)
            ax.tick_params(labelsize=16)
            _limits(ax, job)
            ax.format_coord = coord("I = {x:+.4f} " + unit + ", V = {y:+.4f} mV")
            if ctx.show:
                enable_hover(fig, ax, x, y, "I = {x:+.4f} " + unit + "\nV = {y:+.4f} mV")
            fig.tight_layout()
            ctx.save(fig, f"IV_run{r['run_id']}", subdir="individual" if overlay else None)
        if overlay:
            ax_o.plot(x, y + k * offset, color=colors(k), lw=1.5, label=r["label"])
    if overlay:
        ax_o.set_xlabel(_LABEL[unit], fontsize=16)
        ax_o.set_ylabel("V (mV)", fontsize=16)
        ax_o.tick_params(labelsize=14)
        _limits(ax_o, job)
        ax_o.legend(fontsize=9, bbox_to_anchor=(1.02, 1), loc="upper left")
        ax_o.format_coord = coord("I = {x:+.4f} " + unit + ", V = {y:+.4f} mV")
        fig_o.tight_layout()
        ctx.save(fig_o, "IV_overlay")
    if rows:
        ctx.save_csv("IV_data", ["run_id", "T_K", "I_A", "V_V"], rows)


@register("dvdi", "原始 dV/dI（相鄰點差分）")
def dvdi(ctx, job):
    runs = ctx.resolve_runs(job)
    merge = job.get("merge_duplicates", True)
    overlay = job.get("overlay", len(runs) > 1)
    offset = float(job.get("offset_ohm", 0.0))
    colors = _colors(len(runs), job.get("cmap", "tab20"))
    if overlay:
        fig_o, ax_o = plt.subplots(figsize=(8, 5))
    for k, r in enumerate(runs):
        I, V = clean_sort(*ctx.db.load(r["run_id"]).xy(y=job.get("voltage")), merge)
        I_mid, d = derivative(I, V)
        x = I_mid / 1e-6
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(x, d)
        ax.set_xlabel(r"I ($\mu$A)", fontsize=18)
        ax.set_ylabel(r"dV/dI ($\Omega$)", fontsize=18)
        ax.set_title(f"run {r['run_id']}  {r['label']}", fontsize=13)
        ax.tick_params(labelsize=16)
        _limits(ax, job)
        ax.format_coord = coord("I = {x:+.4f} uA, dV/dI = {y:+.5f} Ohm")
        if ctx.show:
            enable_hover(fig, ax, x, d, "I = {x:+.4f} uA\ndV/dI = {y:+.5f} Ohm")
        fig.tight_layout()
        ctx.save(fig, f"dVdI_run{r['run_id']}", subdir="individual" if overlay else None)
        if overlay:
            ax_o.plot(x, d + k * offset, color=colors(k), lw=1.3, label=r["label"])
    if overlay:
        ax_o.set_xlabel(r"I ($\mu$A)", fontsize=16)
        ax_o.set_ylabel(r"dV/dI ($\Omega$)", fontsize=16)
        ax_o.tick_params(labelsize=14)
        _limits(ax_o, job)
        ax_o.legend(fontsize=9, bbox_to_anchor=(1.02, 1), loc="upper left")
        fig_o.tight_layout()
        ctx.save(fig_o, "dVdI_overlay")


@register("linearize", "兩側 Huber 直線取代雜訊 → IV 比較圖 + 線性化 dV/dI")
def linearize(ctx, job):
    runs = ctx.resolve_runs(job)
    width = float(job.get("edge_width_uA", 500))
    show_raw = job.get("show_raw_dvdi", False)
    rows_fit = []
    for r in runs:
        rid = r["run_id"]
        neg_edge, pos_edge = get_edges(job, r)
        I, V = clean_sort(*ctx.db.load(rid).xy(y=job.get("voltage")),
                          job.get("merge_duplicates", True))
        I_uA, V_mV = I / 1e-6, V / 1e-3
        V_clean, fit = linearize_edges(I_uA, V_mV, neg_edge, pos_edge, width, f"run {rid}")
        rows_fit.append([rid, r["T"], neg_edge, pos_edge, fit["neg_intercept"],
                         fit["neg_slope"], fit["pos_intercept"], fit["pos_slope"]])
        ctx.log(f"run {rid} ({r['label']}): neg edge {neg_edge:+.1f} uA, pos edge {pos_edge:+.1f} uA")
        ctx.log(f"  Negative fit: V = {fit['neg_intercept']:+.6f} + {fit['neg_slope']:+.8f} I")
        ctx.log(f"  Positive fit: V = {fit['pos_intercept']:+.6f} + {fit['pos_slope']:+.8f} I")

        # IV：原始 vs 線性化
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(I_uA, V_mV, ".", color="gray", ms=2, alpha=0.45, label="Experimental data")
        ax.plot(I_uA, V_clean, "-", color="red", lw=1.8, label="Linearized data")
        for side, color, name in (("neg", "blue", "Negative"), ("pos", "green", "Positive")):
            m = I_uA < neg_edge if side == "neg" else I_uA > pos_edge
            if m.any():
                xx = np.linspace(I_uA[m].min(), I_uA[m].max(), 300)
                ax.plot(xx, fit[f"{side}_intercept"] + fit[f"{side}_slope"] * xx, "--",
                        color=color, lw=1.2, label=f"{name} linear fit")
        for e in (neg_edge, pos_edge):
            ax.axvline(e, color="gray", ls=":", lw=1)
        ax.set_xlabel(r"I ($\mu$A)", fontsize=18)
        ax.set_ylabel("V (mV)", fontsize=18)
        ax.set_title(f"run {rid}  {r['label']}", fontsize=13)
        ax.tick_params(labelsize=16)
        ax.legend(fontsize=10)
        fig.tight_layout()
        ctx.save(fig, f"IV_linearized_run{rid}")

        # dV/dI
        x_c, d_c = derivative(I_uA * 1e-6, V_clean * 1e-3)
        fig, ax = plt.subplots(figsize=(8, 5))
        if show_raw:
            x_r, d_r = derivative(I_uA * 1e-6, V_mV * 1e-3)
            ax.plot(x_r / 1e-6, d_r, ".", color="gray", ms=2, alpha=0.35, label="Experimental dV/dI")
        ax.plot(x_c / 1e-6, d_c, "-", color="red", lw=1.8, label="Linearized dV/dI")
        ax.set_xlabel(r"I ($\mu$A)", fontsize=18)
        ax.set_ylabel(r"dV/dI ($\Omega$)", fontsize=18)
        ax.set_title(f"run {rid}  {r['label']}", fontsize=13)
        ax.tick_params(labelsize=16)
        ax.legend(fontsize=10)
        ax.format_coord = coord("I = {x:+.4f} uA, dV/dI = {y:+.6f} Ohm")
        if ctx.show:
            enable_hover(fig, ax, x_c / 1e-6, d_c, "I = {x:+.4f} uA\ndV/dI = {y:+.6f} Ohm")
        fig.tight_layout()
        ctx.save(fig, f"dVdI_linearized_run{rid}")

        if job.get("export_csv", True):
            ctx.save_csv(f"IV_cleaned_run{rid}", ["I_uA", "V_mV", "V_clean_mV"],
                         zip(I_uA, V_mV, V_clean))
    ctx.save_csv("edge_fits", ["run_id", "T_K", "neg_edge_uA", "pos_edge_uA",
                               "neg_intercept_mV", "neg_slope_mV_per_uA",
                               "pos_intercept_mV", "pos_slope_mV_per_uA"], rows_fit)


@register("resistance", "IV 直線擬合求電阻 R（可限定電流範圍），並畫 R vs T")
def resistance(ctx, job):
    runs = ctx.resolve_runs(job)
    rng = job.get("I_range_uA")
    rows = []
    for r in runs:
        I, V = ctx.db.load(r["run_id"]).xy(y=job.get("voltage"))
        ok = np.isfinite(I) & np.isfinite(V)
        if rng:
            ok &= (I >= rng[0] * 1e-6) & (I <= rng[1] * 1e-6)
        if ok.sum() < 2:
            ctx.log(f"run {r['run_id']}: 點數不足，略過")
            continue
        R = float(np.polyfit(I[ok], V[ok], 1)[0])
        rows.append([r["run_id"], r["T"], R, r["info"].exp_name])
        ctx.log(f"run {r['run_id']:>4}  {r['label']:>8}  R = {R:.6g} Ohm")
    ctx.save_csv("resistance", ["run_id", "T_K", "R_ohm", "exp_name"], rows)
    pts = [(t, R) for _, t, R, _ in rows if t is not None]
    if len(pts) >= 2:
        pts.sort()
        fig, ax = plt.subplots(figsize=(6, 4.5))
        ax.plot(*zip(*pts), "o-")
        ax.set_xlabel("T (K)", fontsize=14)
        ax.set_ylabel(r"R ($\Omega$)", fontsize=14)
        fig.tight_layout()
        ctx.save(fig, "R_vs_T")


def _limits(ax, job):
    if job.get("xlim"):
        ax.set_xlim(*job["xlim"])
    if job.get("ylim"):
        ax.set_ylim(*job["ylim"])
