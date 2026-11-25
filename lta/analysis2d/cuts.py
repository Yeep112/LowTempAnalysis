"""指定位置切線 + 轉折點：在指定的慢軸位置（例如磁場）切出 IV，找 transition point 與 bulk transition
（notebook 第 26、27 格）。

計算核心（find_transition_point、bulk_transition、detect_cut_transitions）也給批次分析使用。
"""
import numpy as np
from matplotlib import colormaps

from ..core.registry import P, Result, analysis
from ._common import load_view


# ---------------------------------------------------------------------------
# 計算核心（I、V 為 SI 單位）
# ---------------------------------------------------------------------------
def _baseline(I_pos, V_pos, baseline_max_uA):
    base = I_pos <= baseline_max_uA * 1e-6
    if base.sum() < 3:
        base = np.arange(len(I_pos)) < 3
    slope, offset = np.polyfit(I_pos[base], V_pos[base], 1)
    return base, slope, offset


def _positive_branch(I_row, V_row):
    I_row = np.asarray(I_row, float)
    V_row = np.asarray(V_row, float)
    ok = np.isfinite(I_row) & np.isfinite(V_row)
    order = np.argsort(I_row[ok], kind="stable")
    I_s, V_s = I_row[ok][order], V_row[ok][order]
    pos = I_s >= 0
    return I_s, V_s, I_s[pos], V_s[pos]


def find_transition_point(I_row, V_row, p):
    _, _, I_pos, V_pos = _positive_branch(I_row, V_row)
    if len(I_pos) < p["consecutive"] + 3:
        return np.nan, np.nan
    base, slope, offset = _baseline(I_pos, V_pos, p["baseline_max_uA"])
    residual = V_pos - (slope * I_pos + offset)
    noise = np.std(residual[base])
    threshold = max(p["kink_threshold_mV"] * 1e-3, p["n_sigma"] * noise)
    above = residual > threshold
    run = np.convolve(above.astype(int), np.ones(p["consecutive"], dtype=int), "valid")
    starts = np.flatnonzero(run == p["consecutive"])
    starts = starts[starts > 0]
    if len(starts) == 0:
        return np.nan, np.nan
    k = starts[0]
    I_before = I_pos[k - 1]
    jump = p["jump_mV"] * 1e-3
    if V_pos[k] - V_pos[k - 1] > jump:
        return I_before, V_pos[k - 1]
    seg = [k]
    for j in range(k + 1, min(k + p["kink_fit_points"], len(I_pos))):
        if V_pos[j] - V_pos[j - 1] > jump:
            break
        seg.append(j)
    if len(seg) >= 2:
        b, a = np.polyfit(I_pos[seg], residual[seg], 1)
        if b > 0:
            I_kink = -a / b
            if I_pos[max(k - 10, 0)] <= I_kink <= I_pos[k]:
                return I_kink, slope * I_kink + offset
    return I_before, V_pos[k - 1]


def bulk_transition(I_row, V_row, p):
    _, _, I_pos, V_pos = _positive_branch(I_row, V_row)
    if len(I_pos) < 4:
        return np.nan, np.nan
    _, slope, offset = _baseline(I_pos, V_pos, p["baseline_max_uA"])
    residual = V_pos - (slope * I_pos + offset)
    crossed = np.flatnonzero(residual > p["bulk_threshold_mV"] * 1e-3)
    crossed = crossed[crossed > 0]
    if len(crossed) == 0:
        return np.nan, np.nan
    c = crossed[0]
    start = max(c - p["bulk_lookback"], 1)
    steps = np.diff(V_pos[start - 1:c + 1])
    j = start - 1 + int(np.argmax(steps))
    return I_pos[j], V_pos[j]


def bulk_in_window(I_row, V_row, center, upper_limit, p):
    I_s, V_s, _, _ = _positive_branch(I_row, V_row)
    half = p["bulk_window_uA"] * 1e-6
    win = np.flatnonzero((np.abs(I_s - center) <= half) & (I_s < upper_limit))
    win = win[win > 0]
    if len(win) == 0:
        return np.nan, np.nan
    steps = V_s[win] - V_s[win - 1]
    base = (I_s >= 0) & (I_s <= p["baseline_max_uA"] * 1e-6)
    noise = np.std(np.diff(V_s[base])) if base.sum() > 3 else 0.0
    min_step = max(p["bulk_min_step_mV"] * 1e-3, p["n_sigma"] * noise)
    best = int(np.argmax(np.abs(steps)))
    if abs(steps[best]) < min_step:
        return np.nan, np.nan
    j = win[best] - 1
    return I_s[j], V_s[j]


def detect_cut_transitions(I, V, sel, B_shift_mT, p, use_bulk=True):
    """對選到的磁場列 sel 找 transition point 與 bulk transition。
    回傳 trans (N,5) = [B-B0 mT, I_t A, V_t V, I_b A, V_b V] 與 same（bulk 在 peak 附近另外找）。"""
    trans = []
    for k in sel:
        It, Vt = find_transition_point(I[k], V[k], p)
        Ib, Vb = bulk_transition(I[k], V[k], p) if use_bulk else (np.nan, np.nan)
        trans.append([B_shift_mT[k], It, Vt, Ib, Vb])
    trans = np.array(trans, float).reshape(-1, 5)
    same = np.zeros(len(sel), bool)
    if use_bulk and len(sel):
        step_I = np.median(np.diff(np.unique(I[0])))
        same = (np.isfinite(trans[:, 1]) & np.isfinite(trans[:, 3])
                & (np.abs(trans[:, 3] - trans[:, 1]) <= 1.5 * step_I))
        separate = np.isfinite(trans[:, 3]) & ~same
        if same.any():
            if separate.any():
                ref = np.median(trans[separate, 3])
                for row in np.flatnonzero(same):
                    k = sel[row]
                    trans[row, 3:5] = bulk_in_window(I[k], V[k], ref,
                                                     trans[row, 1] - 0.5 * step_I, p)
            else:
                trans[same, 3:5] = np.nan
    return trans, same


_CUT_DEFAULTS = dict(baseline_max_uA=300, kink_threshold_mV=0.05, n_sigma=5, consecutive=3,
                     jump_mV=1.0, kink_fit_points=8, bulk_threshold_mV=1.2, bulk_lookback=4,
                     bulk_window_uA=60, bulk_min_step_mV=0.05)


def select_nearest(S, targets, tol=None):
    """每個目標位置挑最近的資料點（不會因為沒有剛好的值而跳過）。
    tol=None：一律挑最近的；給數字時，距離超過 tol 的目標才略過。
    回傳 (選到的索引（依位置排序、不重複）, 說明文字 list, 略過的目標)"""
    S = np.asarray(S, float)
    steps = np.abs(np.diff(np.unique(np.round(S, 9))))
    spacing = float(np.median(steps)) if len(steps) else 0.0
    chosen, notes, skipped = {}, [], []
    for t in targets:
        d = np.round(np.abs(S - t), 9)
        ties = np.flatnonzero(d == d.min())
        k = int(ties[np.argmax(S[ties])])        # 剛好在兩筆中間時，取數值較大的（與 notebook 一致）
        if tol is not None and d[k] > tol:
            skipped.append(t)
            continue
        if k in chosen:
            notes.append(f"目標 {t:+.4g} → 最近的 {S[k]:+.4g}（與目標 {chosen[k]:+.4g} 相同，只畫一次）")
            continue
        chosen[k] = t
        if spacing and d[k] > 0.5 * spacing + 1e-12:
            notes.append(f"目標 {t:+.4g} 沒有剛好的資料 → 用最近的 {S[k]:+.4g}")
    sel = sorted(chosen, key=lambda k: S[k])
    return sel, notes, skipped, spacing


# ---------------------------------------------------------------------------
# 介面
# ---------------------------------------------------------------------------
def parse_positions(text):
    """'-1.5:2.1:0.15' 或 '-1, -0.5, 0, 0.5'"""
    text = str(text).strip()
    if ":" in text:
        a, b, s = (float(x) for x in text.split(":"))
        return list(np.arange(a, b + s / 2, s))
    return [float(x) for x in text.replace("，", ",").split(",") if x.strip()]


@analysis("2D", "指定位置切線 + 轉折點", group="萃取", icon="▥", order=110, multi=False,
          modes=("current_bias",), params=[
    P("fields", "切線位置（慢軸顯示單位，例如 mT）", "-1.5:2.1:0.15",
      tip="起:終:間隔，或用逗號列出：-1, 0, 1"),
    P("tol", "最遠距離（空白 = 一律挑最近的資料）", "",
      tip="沒有剛好的位置時會自動用最近的資料；只有填了數字，超過這個距離的目標才會略過"),
    P("shift", "以切換電流最大處為慢軸原點（B₀）", True),
    P("show", "切線顯示", "IV", ["IV", "dV/dI"]),
    P("offset", "疊圖平移（Y 軸單位/條）", 0.0),
    P("detect", "找 transition point / bulk", True),
    P("baseline_max_uA", "基線範圍上限 (μA)", 300.0),
    P("kink_threshold_mV", "離開基線門檻 (mV)", 0.05),
    P("bulk_threshold_mV", "bulk 門檻 (mV)", 1.2),
], help="notebook 第 26、27 格。")
def position_cuts(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"])
    m = v.m
    I, V = m.fast.values, m.z.values                # SI
    S = v.s_axis
    S_round = np.round(S, 6) + 0.0
    tol = float(p["tol"]) if str(p["tol"]).strip() else None
    sel, notes, missing, spacing = select_nearest(S_round, parse_positions(p["fields"]), tol)
    if not sel:
        raise ValueError("沒有找到任何指定位置")
    colors = colormaps["turbo"].resampled(max(len(sel), 2))
    if p["detect"]:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.3, 1])
        ax = fig.add_subplot(gs[0])
        ax2 = fig.add_subplot(gs[1])
    else:
        ax = fig.add_subplot(111)
    unit_s = v.s_label[v.s_label.rfind("(") + 1:-1]
    for n, k in enumerate(sel):
        if p["show"] == "IV":
            x, y = I[k] / 1e-6, V[k] / 1e-3
        else:
            o = np.argsort(I[k])
            Ik, Vk = I[k][o], V[k][o]
            with np.errstate(divide="ignore", invalid="ignore"):
                y = np.diff(Vk) / np.diff(Ik)
            x = (Ik[:-1] + Ik[1:]) / 2 / 1e-6
        ax.plot(x, y + n * p["offset"], color=colors(n), lw=1.1, label=f"{S_round[k]:+.3g} {unit_s}")
    lines = [v.note] if v.note else []
    lines.append(f"選到 {len(sel)} 個位置（資料間距 {spacing:.4g}）")
    lines += notes
    export = {}
    if p["detect"]:
        cp = dict(_CUT_DEFAULTS)
        cp.update(baseline_max_uA=p["baseline_max_uA"], kink_threshold_mV=p["kink_threshold_mV"],
                  bulk_threshold_mV=p["bulk_threshold_mV"])
        trans, same = detect_cut_transitions(I, V, sel, S_round, cp, True)
        if p["show"] == "IV":
            for n in range(len(sel)):
                for col, mk in ((1, "o"), (3, "s")):
                    if np.isfinite(trans[n, col]):
                        ax.plot(trans[n, col] / 1e-6, trans[n, col + 1] / 1e-3 + n * p["offset"],
                                mk, ms=6, mfc="none", mec=colors(n), mew=1.4)
        ok_t, ok_b = np.isfinite(trans[:, 1]), np.isfinite(trans[:, 3])
        ax2.plot(trans[ok_t, 0], trans[ok_t, 1] / 1e-6, "o-", ms=5, label="transition point")
        ax2.plot(trans[ok_b, 0], trans[ok_b, 3] / 1e-6, "s--", ms=5, label="bulk transition")
        ax2.set_xlabel(v.s_label)
        ax2.set_ylabel("Transition I (μA)")
        ax2.legend(fontsize=8)
        for n, k in enumerate(sel):
            t = f"{trans[n, 1] / 1e-6:8.2f}" if ok_t[n] else "      --"
            b = f"{trans[n, 3] / 1e-6:8.2f}" if ok_b[n] else "      --"
            lines.append(f"{v.s_label} = {S_round[k]:+.4g} : transition {t} μA | bulk {b} μA"
                         + ("  (bulk 在 peak 附近另外找)" if same[n] else ""))
        export["cuts"] = ([v.s_label, "I transition (uA)", "V transition (mV)", "I bulk (uA)",
                           "V bulk (mV)"],
                          [[r[0], r[1] / 1e-6, r[2] / 1e-3, r[3] / 1e-6, r[4] / 1e-3] for r in trans])
    if missing:
        lines.append("找不到的位置：" + ", ".join(f"{x:+.3g}" for x in missing))
    ax.set_xlabel("I (μA)")
    ax.set_ylabel("V (mV)" if p["show"] == "IV" else "dV/dI (Ω)")
    ax.legend(fontsize=7, ncol=2 if len(sel) > 12 else 1, loc="upper left")
    return Result(text="\n".join(lines), export=export)


