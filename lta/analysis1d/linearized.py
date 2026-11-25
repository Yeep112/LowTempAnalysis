"""線性化 dV/dI（notebook 第 13–16 格）：
|I| 超過 edge 的部分用 Huber 直線取代雜訊，再微分。

每筆 run 可以有自己的設定（介面上的「逐筆設定」表，或直接在圖上拖曳 ▼ 標記）：
  負側 edge、正側 edge、疊圖平移、內插修補區間（把指定電流範圍的尖峰換成直線內插）
沒有個別設定的 run 用上面的 edges（可依溫度內插）。
"""
import numpy as np
from scipy.ndimage import median_filter

from ..core.registry import P, Result, analysis, item_value
from ..core.common import clean_sort, derivative, linearize_edges
from ._common import parse_T_table, parse_ranges, patch_regions

NEG_COLOR, POS_COLOR = "#2563eb", "#16a34a"


def defaults(items, p):
    """每筆的預設值（逐筆設定表顯示用）。"""
    edge_of = parse_T_table(p["edges"], 2, "edges")
    out = {}
    for k, it in enumerate(items):
        neg, pos = edge_of(it.T)
        out[it.run_id] = {"neg_edge": round(neg, 3), "pos_edge": round(pos, 3),
                          "item_offset": round(k * p["offset"], 6), "patch": ""}
    return out


@analysis("1D", "線性化 dV/dI（兩側直線取代雜訊）", group="進階", icon="╱", order=110,
          modes=("current_bias",), item_defaults=defaults, params=[
    P("edges", "預設 edges (μA)", "-870, 950",
      tip="沒有個別設定的 run 用這組。全部同一組：-870, 950　｜　依溫度內插：1.6: -870, 950; 2.8: -666, 718"),
    P("width", "擬合寬度 (μA)", 500.0),
    P("ylog", "Y 軸用 log10", True),
    P("despike", "去除往下尖峰（log）", True),
    P("offset", "預設疊圖平移（Ω 或 decade/條）", 2.0),
    P("show_check", "單筆時顯示 IV 擬合檢查", True),
    # ---- 逐筆 ----
    P("neg_edge", "負側 edge (μA)", 0.0, per_item=True),
    P("pos_edge", "正側 edge (μA)", 0.0, per_item=True),
    P("item_offset", "平移", 0.0, per_item=True),
    P("patch", "內插修補區間 (μA)", "", per_item=True,
      tip="例：-505:-495, 300:310　這些電流範圍內的點改用兩端直線內插"),
], help="每筆可以個別設定 edge、平移與要修補的雜訊區間；在圖上拖曳 ▼ 標記也可以調 edge。")
def linearized(fig, items, p):
    d = defaults(items, p)
    single = len(items) == 1 and p["show_check"]
    if single:
        ax_iv, ax = fig.subplots(2, 1, sharex=True)
    else:
        ax = fig.add_subplot(111)
    lines, rows = [], []
    for k, it in enumerate(items):
        rid = it.run_id
        neg = item_value(p, "neg_edge", it, d[rid]["neg_edge"])
        pos = item_value(p, "pos_edge", it, d[rid]["pos_edge"])
        off = item_value(p, "item_offset", it, d[rid]["item_offset"])
        ranges = parse_ranges(item_value(p, "patch", it, ""))
        I, V = clean_sort(*it.iv())
        I_uA, V_mV = I / 1e-6, V / 1e-3
        V_mV_raw = V_mV
        V_mV, n_patch = patch_regions(I_uA, V_mV, ranges)
        V_clean, fit = linearize_edges(I_uA, V_mV, neg, pos, p["width"], f"run {rid}")
        x, dvdi = derivative(I, V_clean * 1e-3)
        y = np.maximum(dvdi, 0.0)
        if p["ylog"]:
            y = np.log10(np.maximum(y, 1e-6))
            if p["despike"] and len(y) >= 5:
                local = median_filter(y, size=5, mode="nearest")
                dip = local - y > 0.25
                y[dip] = local[dip]
        x_uA = x / 1e-6
        ax.plot(x_uA, y + off, color=it.color, label=it.label, lw=1.2)
        for a, b in ranges:
            ax.axvspan(a, b, color=it.color, alpha=0.12, lw=0)
        if single:
            ax_iv.plot(I_uA, V_mV_raw, ".", color="0.6", ms=2, label="data")
            ax_iv.plot(I_uA, V_clean, "-", color="#e4572e", lw=1.4, label="linearized")
            for a, b in ranges:
                ax_iv.axvspan(a, b, color="#f2a541", alpha=0.25, lw=0)
            for axx in (ax_iv, ax):
                for key, val, col in (("neg_edge", neg, NEG_COLOR), ("pos_edge", pos, POS_COLOR)):
                    ln = axx.axvline(val, color=col, ls="--", lw=1.6, gid="nohover")
                    ln.lta_drag = (key, rid)
            ax_iv.set_ylabel("V (mV)")
            ax_iv.legend(fontsize=8)
            ax_iv.set_title("拖曳藍 / 綠虛線調整這筆的 edge", fontsize=9, color="0.45")
        else:
            for key, val, col in (("neg_edge", neg, NEG_COLOR), ("pos_edge", pos, POS_COLOR)):
                yv = float(np.interp(val, x_uA, y)) + off
                mk, = ax.plot([val], [yv], marker="v", ms=9, color=it.color, mec="black", mew=0.8,
                              ls="none", gid="nohover", zorder=20)
                mk.lta_drag = (key, rid)
        rows.append([rid, it.T, neg, pos, off, item_value(p, "patch", it, ""), fit["neg_intercept"],
                     fit["neg_slope"], fit["pos_intercept"], fit["pos_slope"]])
        mark = "＊" if any(rid in (p.get(kk) or {}) for kk in ("neg_edge", "pos_edge", "patch")) else "  "
        lines.append(f"{mark}run {rid:>4} {it.label:<14} edges {neg:+.1f}/{pos:+.1f} μA"
                     + (f"  修補 {n_patch} 點" if n_patch else "")
                     + f" | neg V = {fit['neg_intercept']:+.5f} + {fit['neg_slope']:+.7f} I"
                     f" | pos V = {fit['pos_intercept']:+.5f} + {fit['pos_slope']:+.7f} I")
    ax.set_xlabel("I (μA)")
    ax.set_ylabel("log10(dV/dI / Ω)" if p["ylog"] else "dV/dI (Ω)")
    if len(items) > 1:
        ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1), borderaxespad=0)
        ax.set_title("拖曳 ▼ 標記可調整該筆的 edge", fontsize=9, color="0.45")
    lines.append("（＊ = 有個別設定的 run）")
    return Result(text="\n".join(lines), export={"edge_settings": (
        ["run_id", "T_K", "neg_edge_uA", "pos_edge_uA", "offset", "patch_ranges_uA",
         "neg_intercept_mV", "neg_slope", "pos_intercept_mV", "pos_slope"], rows)})
