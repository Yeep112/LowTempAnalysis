"""IV 轉折點：二次微分峰值找 4 個轉折（neg/pos × outer/inner），逐溫度追蹤，算 Ic（notebook 第 18、19 格）。

計算核心 track_transitions() 也給批次分析（batch/jobs/tseries.py）使用。
"""
import numpy as np
from scipy.signal import savgol_filter, find_peaks

from ..core.registry import P, Result, analysis
from ..core.common import clean_sort
from ._common import parse_floats
from ._ic_rules import RULES, apply as apply_rules, duplicate_note
from .linearized import STORE as LIN_STORE

NAMES = ["neg outer", "neg inner", "pos inner", "pos outer"]


# ---------------------------------------------------------------------------
# 計算核心
# ---------------------------------------------------------------------------
def _prep_d2(I, V, window_length, poly_order):
    I, V = clean_sort(I, V)
    step = np.median(np.diff(I))
    I_grid = np.arange(I[0], I[-1] + step / 2, step)
    V_grid = np.interp(I_grid, I, V)
    window = min(window_length, len(I_grid) - (len(I_grid) + 1) % 2)
    V_s = savgol_filter(V_grid, window, poly_order)
    d2 = savgol_filter(V_grid, window, poly_order, deriv=2, delta=step)
    return I_grid, V_s, d2


def _find_one(I, V_s, d2, low, high):
    if not low < high:
        return np.nan, np.nan
    mask = (I >= low) & (I <= high)
    if mask.sum() < 3:
        return np.nan, np.nan
    sign = 1.0 if (low + high) > 0 else -1.0
    feature = sign * d2[mask]
    idx = np.flatnonzero(mask)
    peaks, _ = find_peaks(feature)
    peaks = peaks[feature[peaks] > 0]
    if len(peaks) == 0:
        return np.nan, np.nan
    best = idx[peaks[np.argmax(feature[peaks])]]
    return I[best], V_s[best]


def track_transitions(curves, initial_guess, window_length=15, poly_order=3,
                      first_half_width=0.12, step_in=0.20, step_out=0.05, min_gap=0.08, progress=None):
    """curves: [(I_mA, V_mV), ...]，依溫度由低到高。每條找 4 個轉折，下一條以上一條為猜測。
    回傳 (all_I, all_V)，形狀 (N, 4)，單位 mA、mV。"""
    guesses = np.array(initial_guess, float)

    def search_range(g, first):
        if first:
            return g - first_half_width, g + first_half_width
        return (g - step_in, g + step_out) if g > 0 else (g - step_out, g + step_in)

    all_I, all_V = [], []
    for index, (I, V) in enumerate(curves):
        if progress:
            progress(index, len(curves), "二次微分找轉折")
        Ig, Vs, d2 = _prep_d2(I, V, window_length, poly_order)
        tI, tV = np.full(4, np.nan), np.full(4, np.nan)
        for outer, inner in [(0, 1), (3, 2)]:
            side = np.sign(guesses[outer])
            lo, hi = search_range(guesses[outer], index == 0)
            tI[outer], tV[outer] = _find_one(Ig, Vs, d2, lo, hi)
            lo, hi = search_range(guesses[inner], index == 0)
            limit = tI[outer] if np.isfinite(tI[outer]) else guesses[outer]
            if side > 0:
                lo, hi = max(lo, 0.0), min(hi, limit - min_gap)
            else:
                lo, hi = max(lo, limit + min_gap), min(hi, 0.0)
            tI[inner], tV[inner] = _find_one(Ig, Vs, d2, lo, hi)
        found = np.isfinite(tI)
        guesses[found] = tI[found]
        all_I.append(tI)
        all_V.append(tV)
    return np.array(all_I), np.array(all_V)


# ---------------------------------------------------------------------------
# 介面
# ---------------------------------------------------------------------------
VIEWS = ["IV + 轉折位置 + Ic", "轉折位置 + Ic vs T", "只有轉折位置 vs T", "只有 Ic vs T", "只有 IV"]
NAME_COLORS = {"neg outer": "tab:blue", "neg inner": "tab:orange", "pos inner": "tab:green", "pos outer": "tab:red"}
CURVE_DEFS = [("outer", "pos outer", "neg outer"), ("inner", "pos inner", "neg inner")]

PARAMS = [
    P("d2_view", "顯示", VIEWS[0], choices=VIEWS),
    P("d2_guess", "初始猜測 (mA)", "-0.85, -0.50, 0.53, 0.92",
      tip="最低溫那條的四個轉折：neg outer, neg inner, pos inner, pos outer"),
    P("d2_window", "平滑點數", 15),
    P("d2_order", "多項式階數", 3),
    P("d2_first_hw", "第一條搜尋半寬 (mA)", 0.12),
    P("d2_step_in", "往內搜尋 (mA)", 0.20),
    P("d2_step_out", "往外搜尋 (mA)", 0.05),
    P("d2_min_gap", "outer/inner 最小間距 (mA)", 0.08),
    P("d2_offset", "IV 平移 (mV/條)", 0.5),
    P("d2_ylim", "IV 的 Y 範圍 (mV，空白=自動)", "-0.7, 6.5"),
] + RULES


def compute(items, p, progress=None):
    """選取的 run（依溫度排序）→ 四個轉折位置（mA）。Ic 擬合也會用。"""
    items = sorted(items, key=lambda it: (it.T is None, it.T or 0, it.run_id))
    curves = []
    for it in items:
        I, V = it.iv()
        curves.append((I / 1e-3, V / 1e-3))
    all_I, all_V = track_transitions(curves, parse_floats(p["d2_guess"], 4, "初始猜測"),
                                     p["d2_window"], p["d2_order"], p["d2_first_hw"], p["d2_step_in"],
                                     p["d2_step_out"], p["d2_min_gap"], progress=progress)
    xs = np.array([it.T if it.T is not None else it.run_id for it in items], float)
    return dict(items=items, curves=curves, raw_I=all_I, all_V=all_V, xs=xs,
                has_T=all(it.T is not None for it in items))


def processed(data, p):
    """套用規則後 → (xs, positions{name: mA}, Ic{outer, inner})"""
    pos = {n: data["raw_I"][:, k] for k, n in enumerate(NAMES)}
    return apply_rules(data["xs"], pos, CURVE_DEFS, p)


def _draw_iv(ax, data, p):
    for k, it in enumerate(data["items"]):
        I, V = data["curves"][k]
        off = k * p["d2_offset"]
        ax.plot(I, V + off, color=it.color, lw=1.2, label=it.label)
        ax.scatter(data["raw_I"][k], data["all_V"][k] + off, s=30, facecolors="none",
                   edgecolors=it.color, linewidths=1.3, zorder=5)
    ax.set_xlabel("I (mA)")
    ax.set_ylabel("V (mV)")
    if str(p["d2_ylim"]).strip():
        ax.set_ylim(*parse_floats(p["d2_ylim"], 2, "Y 範圍"))
    ax.legend(title="Temperature", fontsize=8, title_fontsize=8, loc="upper left",
              bbox_to_anchor=(1.01, 1), borderaxespad=0)


def _draw_positions(ax, Ts, pos, xlab):
    for n in NAMES:
        ax.plot(Ts, pos[n], "o-", color=NAME_COLORS[n], label=n)
    ax.axhline(0, color="gray", lw=0.8, gid="nohover")
    ax.set_xlabel(xlab)
    ax.set_ylabel("Transition current (mA)")
    ax.legend(fontsize=9)


def _draw_ic(ax, Ts, Ic, xlab):
    for name, col in (("outer", "tab:blue"), ("inner", "tab:orange")):
        v = np.isfinite(Ic[name])
        ax.plot(Ts[v], Ic[name][v], "o-", lw=1.5, ms=6, color=col, label=rf"$I_c$ {name}")
    ax.set_xlabel(xlab)
    ax.set_ylabel(r"$I_c$ (mA)")
    ax.legend(fontsize=10)


def _compute_hook(items, p, progress):
    return compute(items, p, progress)


@analysis("1D", "IV 轉折點 + Ic vs T（二次微分）", group="進階", icon="◆", order=120,
          modes=("current_bias",), store=LIN_STORE, params=PARAMS, compute=_compute_hook,
          help="依溫度由低到高逐條追蹤四個轉折。後處理規則（視為 0、強制為 0、排除溫度）和「Ic vs T 擬合」共用。")
def iv_transitions(fig, items, p):
    data = p.get("_data") or compute(items, p)
    Ts, pos, Ic = processed(data, p)
    xlab = "Temperature (K)" if data["has_T"] else "run"
    view = p["d2_view"]
    if view == VIEWS[0]:
        gs = fig.add_gridspec(2, 2, width_ratios=[1.6, 1])
        _draw_iv(fig.add_subplot(gs[:, 0]), data, p)
        _draw_positions(fig.add_subplot(gs[0, 1]), Ts, pos, xlab)
        _draw_ic(fig.add_subplot(gs[1, 1]), Ts, Ic, xlab)
    elif view == VIEWS[1]:
        a1, a2 = fig.subplots(1, 2)
        _draw_positions(a1, Ts, pos, xlab)
        _draw_ic(a2, Ts, Ic, xlab)
    elif view == VIEWS[2]:
        _draw_positions(fig.add_subplot(111), Ts, pos, xlab)
    elif view == VIEWS[3]:
        _draw_ic(fig.add_subplot(111), Ts, Ic, xlab)
    else:
        _draw_iv(fig.add_subplot(111), data, p)

    lines, rows = [], []
    for k, it in enumerate(data["items"]):
        lines.append(f"{it.label:<14} " + "  ".join(f"{n} {c:+.4f}" for n, c in zip(NAMES, data["raw_I"][k])) + " mA")
        rows.append([it.run_id, it.T, *data["raw_I"][k]])
    out = ["偵測到的轉折位置（套用規則前）"] + lines
    for name in ("outer", "inner"):
        v = np.isfinite(Ic[name])
        out.append(f"\nIc {name}:")
        out += [f"  {T:.2f} K: {ic:+.4f} mA" if data["has_T"] else f"  run {T:g}: {ic:+.4f} mA"
                for T, ic in zip(Ts[v], Ic[name][v])]
    note = duplicate_note(data["items"])
    if note:
        out.append("\n" + note)
    proc_rows = [[T] + [pos[n][k] for n in NAMES] + [Ic["outer"][k], Ic["inner"][k]] for k, T in enumerate(Ts)]
    return Result(text="\n".join(out), export={
        "transitions_raw": (["run_id", "T_K", *[f"{n} (mA)" for n in NAMES]], rows),
        "transitions_Ic": (["T_K", *[f"{n} (mA)" for n in NAMES], "Ic_outer_mA", "Ic_inner_mA"], proc_rows)},
        hint="點圖例可隱藏 / 顯示該曲線；拖曳圖例可移動位置")
