"""溫度序列分析：
  dvdi_vs_T       線性化 dV/dI（線性或 log10）疊圖 + 台階轉折線追蹤 + I-T 相圖
  iv_transitions  IV 二次微分找四個轉折點 + Ic(outer/inner) vs T
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colormaps
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter

from . import register
from .iv import get_edges
from ...core.common import clean_sort, linearize_edges, coord, enable_hover
from ...core import steps
from ...analysis1d.transitions import track_transitions


# ===========================================================================
# dvdi_vs_T
# ===========================================================================
def _prepare_dvdi(ctx, job, r):
    neg_edge, pos_edge = get_edges(job, r)
    I_A, V_V = clean_sort(*ctx.db.load(r["run_id"]).xy(y=job.get("voltage")))
    I_uA, V_mV = I_A / 1e-6, V_V / 1e-3
    V_clean, fit = linearize_edges(I_uA, V_mV, neg_edge, pos_edge,
                                   float(job.get("edge_width_uA", 500)),
                                   f"run {r['run_id']}")
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.diff(V_clean * 1e-3) / np.diff(I_A)
    I_mid = (I_uA[:-1] + I_uA[1:]) / 2
    ok = np.isfinite(d)
    I_mid, d = I_mid[ok], d[ok]
    d = np.maximum(d, 0.0)                         # 移除小於零的峰

    sw = int(job.get("smooth_window", 1))
    if sw > 1:
        if sw % 2 == 0:
            raise ValueError("smooth_window 必須是奇數")
        if len(d) >= sw:
            d = savgol_filter(d, sw, 3)

    log_d = np.log10(np.maximum(d, float(job.get("log_floor_ohm", 1e-6))))
    mw = int(job.get("median_window", 5))
    if len(log_d) >= mw:
        local = median_filter(log_d, size=mw, mode="nearest")
        dip = local - log_d > float(job.get("downward_jump_decade", 0.25))
        log_d[dip] = local[dip]                    # 往下的窄尖峰補回局部中位數

    ctx.log(f"run {r['run_id']} ({r['label']}): edges {neg_edge:+.1f} / {pos_edge:+.1f} uA | "
            f"neg V = {fit['neg_intercept']:+.6f} + {fit['neg_slope']:+.8f} I, "
            f"pos V = {fit['pos_intercept']:+.6f} + {fit['pos_slope']:+.8f} I")
    return dict(run_id=r["run_id"], label=r["label"], T=r["T"], I=I_mid, lin=d,
                log=log_d, neg_edge=neg_edge, pos_edge=pos_edge)


# ---- 台階轉折點偵測：共用 lta/core/steps.py ----
def detect_transitions(processed, lines, cfg):
    manual = {}
    for item in cfg.get("manual_override", []) or []:
        # [線名稱, run_id, 位置uA 或 "none"]
        x = item[2]
        manual[(item[0], int(item[1]))] = None if (x is None or str(x).lower() == "none") else float(x)
    results, _ = steps.detect(processed, lines,
                              median_uA=float(cfg.get("transition_median_uA", 9)),
                              step_window=float(cfg.get("step_window_uA", 15)),
                              max_missing=int(cfg.get("max_missing", 1)),
                              min_gap=float(cfg.get("min_gap_uA", 20)),
                              manual=manual)
    return results


def _ic_fit(ctx, cfg, processed, lines, transitions):
    """[job.ic_fit]：用偵測到的轉折點算 Ic(T) 並做四個階段的擬合（同介面的「Ic vs T 擬合」）。"""
    from ...core import icfit
    from ...analysis1d import ic_vs_T as IC
    runs = [p["run_id"] for p in processed]
    tab = steps.table(transitions, steps.normalize_lines(lines), runs)
    Ts = np.array([p["T"] for p in processed], float)
    pos = {l["name"]: np.array([tab[r][l["name"]] for r in runs]) * 1e-3 for l in lines}
    defs = IC.parse_curves(cfg.get("curves", "outer: outer +, outer -; inner: inner +, inner -"))
    zero_from = cfg.get("zero_from", {})
    if isinstance(zero_from, str):
        zero_from = IC.parse_zero_from(zero_from)
    Ts, pos, Ic = icfit.ic_table(Ts, pos, defs, bool(cfg.get("missing_zero", True)), zero_from,
                                 cfg.get("exclude_T", []), cfg.get("extra_zero_T", []))
    d = dict(Ts=Ts, pos=pos, Ic=Ic, defs=defs, source="batch")
    p = dict(tc_bracket=bool(cfg.get("tc_bracket", True)), T_bulk=float(cfg.get("T_bulk", 7.2)),
             t0_max=float(cfg.get("t0_max", 100.0)), shared_tc=str(cfg.get("shared_tc", "Stage 3 (p=2)")),
             sns_curves=",".join(cfg.get("sns_curves", ["inner"])) if isinstance(cfg.get("sns_curves"), list)
             else str(cfg.get("sns_curves", "inner")))
    fitter = IC.fit_all(d, p)
    final = IC.parse_final(cfg.get("final", "outer: AB (free Tc); inner: Diffusive SNS (free Tc), "
                                             "Diffusive SNS (shared Tc)"))
    text, export = IC.report(d, fitter, [f"{c}: {m}" for c, m in final if fitter.find(c, m) is None])
    ctx.log("")
    for line in text.splitlines():
        ctx.log(line)
    for name, (head, rows) in export.items():
        ctx.save_csv(name, head, rows)
    fig, ax = plt.subplots(figsize=(10, 5))
    IC.plot_final(ax, fitter, Ts, final)
    ax.set_title(cfg.get("title", "Ic(T) physical models"), fontsize=12)
    fig.tight_layout()
    ctx.save(fig, "Ic_fit_physical_models")
    fig, axs = plt.subplots(2, 3, figsize=(16, 9))
    IC._grid(axs.ravel(), d, fitter, Ts, final)
    fig.tight_layout()
    ctx.save(fig, "Ic_fit_stages")


@register("dvdi_vs_T", "線性化 dV/dI 溫度疊圖（linear 或 log），可加轉折線追蹤")
def dvdi_vs_T(ctx, job):
    runs = ctx.resolve_runs(job, sort_by_T=True)
    scale = job.get("scale", "log")
    is_log = scale == "log"
    key = "log" if is_log else "lin"
    ylabel = r"$\log_{10}(dV/dI)$" if is_log else r"dV/dI ($\Omega$)"
    step = float(job.get("offset_step", 2.0 if is_log else 500.0))
    offs = {int(k): float(v) for k, v in (job.get("offsets") or {}).items()}

    processed = []
    for k, r in enumerate(runs):
        p = _prepare_dvdi(ctx, job, r)
        p["shift"] = offs.get(r["run_id"], k * step)
        processed.append(p)

    lines = job.get("transition_lines") or []
    transitions = {}
    if lines:
        if any(p["T"] is None for p in processed):
            raise ValueError("轉折線追蹤需要每個 run 的溫度")
        transitions = detect_transitions(processed, lines, job)
        names = [l["name"] for l in lines]
        ctx.log("")
        ctx.log("Transition points (uA)")
        ctx.log("Run   T(K)  " + "".join(f"{n:>11s}" for n in names))
        rows = []
        for p in processed:
            row = f"{p['run_id']:<5d} {p['T']:4.2f}  "
            for n in names:
                pts = [q for q in transitions[n] if q["run_id"] == p["run_id"]]
                row += f"{pts[0]['I_uA']:+11.1f}" if pts else f"{'--':>11s}"
            ctx.log(row)
        for n in names:
            for q in transitions[n]:
                rows.append([n, q["run_id"], q["T"], f"{q['I_uA']:.3f}",
                             f"{q['y']:.5f}", f"{q['step']:.4f}"])
        ctx.save_csv("transitions", ["line", "run_id", "T_K", "I_uA",
                                     "log10_dVdI", "step_decade"], rows)
        if job.get("ic_fit"):
            _ic_fit(ctx, job["ic_fit"] if isinstance(job["ic_fit"], dict) else {}, processed, lines, transitions)

    def y_at(p, q):
        if is_log:
            return q["y"]
        return float(np.interp(q["I_uA"], p["I"], p["lin"]))

    fmt = "I = {x:+.4f} uA, " + ("log10(dV/dI) = {y:+.6f}" if is_log else "dV/dI = {y:+.6f} Ohm")

    # 個別圖
    if job.get("save_individual", True):
        for p in processed:
            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(p["I"], p[key], lw=1.5, color="tab:blue" if lines else "red")
            ax.axvline(p["neg_edge"], color="gray", ls=":", lw=1)
            ax.axvline(p["pos_edge"], color="gray", ls=":", lw=1)
            for n, pts in transitions.items():
                for q in pts:
                    if q["run_id"] == p["run_id"]:
                        ax.axvline(q["I_uA"], color="red", ls="--", lw=1)
                        ax.plot(q["I_uA"], y_at(p, q), "o", color="red", ms=5)
            ax.set_xlabel(r"I ($\mu$A)", fontsize=18)
            ax.set_ylabel(ylabel, fontsize=18)
            ax.set_title(f"{p['label']}  (run {p['run_id']})", fontsize=15)
            ax.tick_params(labelsize=16)
            ax.format_coord = coord(fmt)
            if ctx.show:
                enable_hover(fig, ax, p["I"], p[key], fmt.replace(", ", "\n"))
            fig.tight_layout()
            ctx.save(fig, f"{scale}_dVdI_run{p['run_id']}", subdir="individual")

    # 疊圖
    def overlay(with_lines):
        fig, ax = plt.subplots(figsize=(9, 5))
        for p in processed:
            ax.plot(p["I"], p[key] + p["shift"], lw=1.5, label=p["label"])
        if with_lines:
            by_run = {p["run_id"]: p for p in processed}
            for n, pts in transitions.items():
                pts = sorted(pts, key=lambda q: q["T"])
                if pts:
                    ax.plot([q["I_uA"] for q in pts],
                            [y_at(by_run[q["run_id"]], q) + by_run[q["run_id"]]["shift"] for q in pts],
                            "-o", color="red", lw=2.2, ms=4, zorder=10)
        ax.set_xlabel(r"I ($\mu$A)", fontsize=18)
        ax.set_ylabel(ylabel, fontsize=18)
        ax.tick_params(labelsize=16)
        if job.get("xlim"):
            ax.set_xlim(*job["xlim"])
        ax.legend(title="Temperature", fontsize=9, title_fontsize=10, loc="upper left",
                  bbox_to_anchor=(1.02, 1), borderaxespad=0)
        ax.format_coord = coord(fmt)
        fig.tight_layout()
        fig.subplots_adjust(right=0.78)
        return fig

    ctx.save(overlay(False), f"{scale}_dVdI_temperature_overlay")
    if transitions:
        ctx.save(overlay(True), f"{scale}_dVdI_temperature_overlay_lines")
        from ...analysis1d import step_transitions as STR
        fig, ax = plt.subplots(figsize=(6.5, 5))
        STR._i_vs_T(ax, dict(lines=steps.normalize_lines(lines), results=transitions),
                    {"unit": job.get("I_vs_T_unit", "mA")})
        ax.set_xlabel("Temperature (K)", fontsize=16)
        ax.set_ylabel(f"I ({job.get('I_vs_T_unit', 'mA')})", fontsize=16)
        ax.tick_params(labelsize=14)
        fig.tight_layout()
        ctx.save(fig, "transition_I_vs_T")


# ===========================================================================
# iv_transitions：IV 的二次微分峰值 → 四個轉折點、Ic outer / inner
# ===========================================================================
@register("iv_transitions", "IV 二次微分找四個轉折點，畫轉折電流與 Ic outer/inner vs T")
def iv_transitions(ctx, job):
    runs = ctx.resolve_runs(job, sort_by_T=True)
    names = job.get("names", ["neg outer", "neg inner", "pos inner", "pos outer"])
    offset_step = float(job.get("offset_step_mV", 0.5))
    curves = []
    for r in runs:
        I, V = ctx.db.load(r["run_id"]).xy(y=job.get("voltage"))
        curves.append((I / 1e-3, V / 1e-3))                     # mA, mV
    all_I, all_V = track_transitions(
        curves, job.get("initial_guess_mA", [-0.85, -0.50, 0.53, 0.92]),
        int(job.get("window_length", 15)), int(job.get("poly_order", 3)),
        float(job.get("first_half_width_mA", 0.12)), float(job.get("step_in_mA", 0.20)),
        float(job.get("step_out_mA", 0.05)), float(job.get("min_gap_mA", 0.08)))

    colors = colormaps[job.get("cmap", "tab20")].resampled(max(len(runs), 2))
    fig_iv, ax_iv = plt.subplots(figsize=(7, 5))
    temps = []
    for index, r in enumerate(runs):
        I, V = curves[index]
        tI, tV = all_I[index], all_V[index]
        off = index * offset_step
        ax_iv.plot(I, V + off, color=colors(index), lw=1.5, label=r["label"])
        ax_iv.scatter(tI, tV + off, s=40, facecolors="none", edgecolors=colors(index),
                      linewidths=1.5, zorder=5)
        ctx.log(f"{r['label']} (run {r['run_id']})")
        for n, c, v in zip(names, tI, tV):
            ctx.log(f"  {n:9s}: I = {c:+.4f} mA, V = {v + off:.4f} mV")
        temps.append(r["T"])

    ax_iv.set_xlabel("I (mA)", fontsize=14)
    ax_iv.set_ylabel("V (mV)", fontsize=14)
    if job.get("ylim"):
        ax_iv.set_ylim(*job["ylim"])
    if job.get("xlim"):
        ax_iv.set_xlim(*job["xlim"])
    ax_iv.legend(title="Temperature", fontsize=10, title_fontsize=11,
                 bbox_to_anchor=(1.02, 1), loc="upper left")
    fig_iv.tight_layout()
    ctx.save(fig_iv, "IV_temperature_with_transitions")

    temps = np.array(temps, float)
    all_I = np.array(all_I)
    raw_I = all_I.copy()

    # ---- 後處理（notebook 第二格）----
    if job.get("unresolved_to_zero", False):
        all_I[~np.isfinite(all_I)] = 0.0
    zero_above = job.get("inner_zero_above_K")
    if zero_above is not None:
        m = temps >= float(zero_above)
        all_I[m, 1] = 0.0
        all_I[m, 2] = 0.0
    no_tr = job.get("no_transition_T") or []
    if no_tr:
        all_I[np.isin(np.round(temps, 6), np.round(no_tr, 6)), :] = np.nan

    fig, ax = plt.subplots(figsize=(5, 4))
    for k, n in enumerate(names):
        ax.plot(temps, all_I[:, k], "o-", label=n)
    ax.axhline(0, color="gray", lw=0.8)
    ax.set_xlabel("Temperature (K)", fontsize=14)
    ax.set_ylabel("Transition current (mA)", fontsize=14)
    ax.legend(fontsize=9)
    fig.tight_layout()
    ctx.save(fig, "transition_current_vs_temperature")

    Ic_out = all_I[:, 3] - all_I[:, 0]
    Ic_in = all_I[:, 2] - all_I[:, 1]
    vo, vi = np.isfinite(Ic_out), np.isfinite(Ic_in)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(temps[vo], Ic_out[vo], "o-", lw=1.5, ms=6, label=r"$I_c$ outer")
    ax.plot(temps[vi], Ic_in[vi], "o-", lw=1.5, ms=6, label=r"$I_c$ inner")
    ax.set_xlabel("Temperature (K)", fontsize=14)
    ax.set_ylabel(r"$I_c$ (mA)", fontsize=14)
    ax.legend(fontsize=10)
    fig.tight_layout()
    ctx.save(fig, "critical_current_vs_temperature")

    ctx.log("")
    ctx.log("Ic outer:")
    for t, ic in zip(temps[vo], Ic_out[vo]):
        ctx.log(f"  {t:.2f} K: {ic:+.4f} mA")
    ctx.log("Ic inner:")
    for t, ic in zip(temps[vi], Ic_in[vi]):
        ctx.log(f"  {t:.2f} K: {ic:+.4f} mA")

    rows = []
    for k, r in enumerate(runs):
        rows.append([r["run_id"], temps[k], *[f"{x:.5f}" for x in raw_I[k]],
                     *[f"{x:.5f}" for x in all_I[k]], f"{Ic_out[k]:.5f}", f"{Ic_in[k]:.5f}"])
    ctx.save_csv("transitions_vs_T",
                 ["run_id", "T_K", *[f"{n} raw (mA)" for n in names],
                  *[f"{n} (mA)" for n in names], "Ic outer (mA)", "Ic inner (mA)"], rows)
