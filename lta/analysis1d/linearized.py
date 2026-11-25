"""線性化 dV/dI（notebook 第 13–16 格）：
|I| 超過 edge 的部分用 Huber 直線取代雜訊，再微分。

每筆 run 可以有自己的設定（介面上的「逐筆設定」表，或直接在圖上拖曳 ▼ 標記）：
  負側 edge、正側 edge、疊圖平移、內插修補區間（把指定電流範圍的尖峰換成直線內插）
沒有個別設定的 run 用上面的 edges（可依溫度內插）。
"""
from collections import OrderedDict

import numpy as np
from scipy.ndimage import median_filter

from ..core.registry import P, Result, analysis, item_value
from ..core.common import clean_sort, derivative, linearize_edges
from ._common import parse_T_table, parse_ranges, patch_regions

NEG_COLOR, POS_COLOR = "#2563eb", "#16a34a"
NAME = "線性化 dV/dI（兩側直線取代雜訊）"
STORE = NAME        # 轉折點追蹤、Ic 擬合用 store=STORE 共用這裡的 edge 與逐筆設定

# 線性化的共用參數（轉折點分析也會列出）
EDGE_PARAMS = [
    P("edges", "預設 edges (μA)", "-870, 950",
      tip="沒有個別設定的 run 用這組。全部同一組：-870, 950　｜　依溫度內插：1.6: -870, 950; 2.8: -666, 718"),
    P("width", "擬合寬度 (μA)", 500.0),
    P("despike", "去除往下尖峰（log）", True),
    P("offset", "預設疊圖平移（Ω 或 decade/條）", 2.0),
]
ITEM_PARAMS = [
    P("neg_edge", "負側 edge (μA)", 0.0, per_item=True),
    P("pos_edge", "正側 edge (μA)", 0.0, per_item=True),
    P("item_offset", "平移", 0.0, per_item=True),
    P("patch", "內插修補區間 (μA)", "", per_item=True,
      tip="例：-505:-495, 300:310　這些電流範圍內的點改用兩端直線內插"),
]


def defaults(items, p):
    """每筆的預設值（逐筆設定表顯示用）。"""
    edge_of = parse_T_table(p["edges"], 2, "edges")
    out = {}
    for k, it in enumerate(items):
        neg, pos = edge_of(it.T)
        out[it.run_id] = {"neg_edge": round(neg, 3), "pos_edge": round(pos, 3),
                          "item_offset": round(k * p["offset"], 6), "patch": ""}
    return out


def item_settings(items, p):
    """每筆實際使用的 (neg, pos, offset, patch 區間)。"""
    d = defaults(items, p)
    out = {}
    for it in items:
        rid = it.run_id
        out[rid] = (item_value(p, "neg_edge", it, d[rid]["neg_edge"]),
                    item_value(p, "pos_edge", it, d[rid]["pos_edge"]),
                    item_value(p, "item_offset", it, d[rid]["item_offset"]),
                    parse_ranges(item_value(p, "patch", it, "")))
    return out


_CACHE = OrderedDict()      # 同樣的資料與設定不重算（拖曳、改其他參數時很快）
_CACHE_MAX = 600


def process(it, neg, pos, width, ylog=True, despike=True, ranges=()):
    """一筆 run → 線性化 dV/dI。回傳 dict(x_uA, y, I_uA, V_raw, V_clean, fit, n_patch)。
    結果會快取；回傳的陣列請不要直接修改。"""
    key = (id(it.ds), it.run_id, float(neg), float(pos), float(width), bool(ylog), bool(despike),
           tuple(tuple(r) for r in ranges))
    hit = _CACHE.get(key)
    if hit is not None and hit[0] is it.ds:
        _CACHE.move_to_end(key)
        return hit[1]
    out = _process(it, neg, pos, width, ylog, despike, ranges)
    _CACHE[key] = (it.ds, out)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return out


def _process(it, neg, pos, width, ylog, despike, ranges):
    I, V = clean_sort(*it.iv())
    I_uA, V_mV = I / 1e-6, V / 1e-3
    V_patched, n_patch = patch_regions(I_uA, V_mV, ranges)
    V_clean, fit = linearize_edges(I_uA, V_patched, neg, pos, width, f"run {it.run_id}")
    x, dvdi = derivative(I, V_clean * 1e-3)
    y = np.maximum(dvdi, 0.0)
    if ylog:
        y = np.log10(np.maximum(y, 1e-6))
        if despike and len(y) >= 5:
            local = median_filter(y, size=5, mode="nearest")
            dip = local - y > 0.25
            y[dip] = local[dip]
    return dict(x_uA=x / 1e-6, y=y, I_uA=I_uA, V_raw=V_mV, V_clean=V_clean, fit=fit, n_patch=n_patch)


@analysis("1D", NAME, group="進階", icon="╱", order=110,
          modes=("current_bias",), item_defaults=defaults, params=EDGE_PARAMS[:2] + [
    P("ylog", "Y 軸用 log10", True),
] + EDGE_PARAMS[2:] + [
    P("show_check", "單筆時顯示 IV 擬合檢查", True),
] + ITEM_PARAMS, help="每筆可以個別設定 edge、平移與要修補的雜訊區間；在圖上拖曳 ▼ 標記也可以調 edge。")
def linearized(fig, items, p):
    settings = item_settings(items, p)
    single = len(items) == 1 and p["show_check"]
    if single:
        ax_iv, ax = fig.subplots(2, 1, sharex=True)
    else:
        ax = fig.add_subplot(111)
    lines, rows = [], []
    for k, it in enumerate(items):
        rid = it.run_id
        neg, pos, off, ranges = settings[rid]
        r = process(it, neg, pos, p["width"], p["ylog"], p["despike"], ranges)
        x_uA, y, I_uA, V_mV_raw, V_clean = r["x_uA"], r["y"], r["I_uA"], r["V_raw"], r["V_clean"]
        fit, n_patch = r["fit"], r["n_patch"]
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
    lines.append("（＊ = 有個別設定的 run）")
    hint = "拖曳藍 / 綠虛線調整這筆的 edge" if single else "拖曳 ▼ 標記可調整該筆的 edge"
    return Result(text="\n".join(lines), hint=hint, export={"edge_settings": (
        ["run_id", "T_K", "neg_edge_uA", "pos_edge_uA", "offset", "patch_ranges_uA",
         "neg_intercept_mV", "neg_slope", "pos_intercept_mV", "pos_slope"], rows)})
