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
from ...core.common import clean_sort, linearize_edges, by_T, coord, enable_hover
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


# ---- 台階轉折點偵測（移植自 notebook 的 TRANSITION_LINES 版本）----
def _step_profile(I_uA, y_log, window_uA, median_uA):
    dx = np.median(np.diff(I_uA))
    k = max(1, int(round(median_uA / dx)))
    if k % 2 == 0:
        k += 1
    y = median_filter(y_log, size=k, mode="nearest")
    w = max(2, int(round(window_uA / dx)))
    n = len(y)
    c = np.concatenate([[0.0], np.cumsum(y)])
    left = np.full(n, np.nan)
    right = np.full(n, np.nan)
    i = np.arange(w, n - w - 1)
    left[i] = (c[i] - c[i - w]) / w
    right[i] = (c[i + 1 + w] - c[i + 1]) / w
    return y, left, right


def _find_step(I_uA, left, right, side, lo, hi, min_step):
    win = (I_uA >= lo) & (I_uA <= hi) & np.isfinite(left) & np.isfinite(right)
    score = left - right if side == "neg" else right - left
    cand = np.flatnonzero(win)
    if cand.size == 0:
        return None
    k = cand[np.argmax(score[cand])]
    if score[k] < min_step:
        return None
    return k, float(score[k])


def _line_guess(line, p, last):
    g = line["guess_uA"]
    if isinstance(g, str) and g == "edge":
        return p["neg_edge"] if line["side"] == "neg" else p["pos_edge"]
    if isinstance(g, list):
        return by_T(g, p["T"], f"{line['name']}.guess_uA")
    return float(g) if last is None else last


def detect_transitions(processed, lines, cfg):
    median_uA = float(cfg.get("transition_median_uA", 9))
    step_window = float(cfg.get("step_window_uA", 15))
    max_missing_default = int(cfg.get("max_missing", 1))
    min_gap = float(cfg.get("min_gap_uA", 20))
    manual = {}
    for item in cfg.get("manual_override", []) or []:
        # [線名稱, run_id, 位置uA 或 "none"]
        x = item[2]
        manual[(item[0], int(item[1]))] = None if (x is None or str(x).lower() == "none") else float(x)

    results = {l["name"]: [] for l in lines}
    state = {l["name"]: dict(last=None, missing=0, active=True) for l in lines}
    cache = {}

    def profile(p, w):
        key = (p["run_id"], w)
        if key not in cache:
            cache[key] = _step_profile(p["I"], p["log"], w, median_uA)
        return cache[key]

    for p in sorted(processed, key=lambda q: q["T"]):
        I_uA = p["I"]
        for side in ("neg", "pos"):
            act = [l for l in lines if l["side"] == side and state[l["name"]]["active"]]
            guesses = {l["name"]: _line_guess(l, p, state[l["name"]]["last"]) for l in act}
            act.sort(key=lambda l: guesses[l["name"]] * (1 if side == "neg" else -1))
            bound = -np.inf if side == "neg" else np.inf
            for line in act:
                name = line["name"]
                st = state[name]
                y, left, right = profile(p, float(line.get("window_uA", step_window)))
                g = guesses[name]
                sw = by_T(line.get("search_uA", 40), p["T"], f"{name}.search_uA")
                if side == "neg":
                    lo, hi = max(g - sw, bound + min_gap), min(g + sw, 0.0)
                else:
                    lo, hi = max(g - sw, 0.0), min(g + sw, bound - min_gap)
                key = (name, p["run_id"])
                if key in manual:
                    if manual[key] is None:
                        continue
                    k = int(np.argmin(np.abs(I_uA - manual[key])))
                    step = np.nan
                else:
                    found = _find_step(I_uA, left, right, side, lo, hi,
                                       float(line.get("min_step", 0.1)))
                    if found is None:
                        if results[name]:
                            st["missing"] += 1
                            if st["missing"] > int(line.get("max_missing", max_missing_default)):
                                st["active"] = False
                        continue
                    k, step = found
                st["missing"] = 0
                st["last"] = float(I_uA[k])
                bound = float(I_uA[k])
                y_mark = (0.5 * (left[k] + right[k])
                          if np.isfinite(left[k]) and np.isfinite(right[k]) else y[k])
                results[name].append(dict(run_id=p["run_id"], T=p["T"], label=p["label"],
                                          I_uA=float(I_uA[k]), y=float(y_mark), step=step))
    return results


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
        fig, ax = plt.subplots(figsize=(6, 5))
        for n, pts in transitions.items():
            pts = sorted(pts, key=lambda q: q["T"])
            if pts:
                ax.plot([q["T"] for q in pts], [q["I_uA"] for q in pts], "-o", ms=4, label=n)
        ax.axhline(0, color="gray", lw=0.8)
        ax.set_xlabel("T (K)", fontsize=16)
        ax.set_ylabel(r"I ($\mu$A)", fontsize=16)
        ax.tick_params(labelsize=14)
        ax.legend(fontsize=9)
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
