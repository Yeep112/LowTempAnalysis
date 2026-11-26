"""⚡ 電場（閘極電壓）的 2D 分析：慢軸 = 閘極電壓（V_bottom / V_top），快軸 = 電流 → 量電壓。

  Ic vs Vg（切換電流）   每個閘極電壓找 dV/dI 峰值（或電壓門檻）的位置 → Ic+、Ic− 對 Vg
  R vs Vg（電阻）        零偏壓電阻 R₀、高偏壓電阻 R_N 對 Vg（場效應）
  指定 Vg 的 IV 疊圖      在幾個閘極電壓切出 IV 與 dV/dI，疊在一起比較

色圖（量測值、微分 dV/dI）用 color_map.py 的共用功能（磁場、電場都適用）。
閘極是底部或頂部（V_bottom / V_top）由介面的「⚡ 閘極設定」決定，這裡的軸標籤會自動跟著變。
"""
import numpy as np
from matplotlib import colormaps

from ..core.measurement import plain, si_scale
from ..core.registry import P, Result, analysis
from ._common import CMAPS, RANGES, add_map, color_limits, derivative_view, load_view, smooth_rows, switching_current
from .cuts import parse_positions, select_nearest

GROUP = "⚡ 電場（閘極 Vg）"
COND = ("gate",)
METHODS = ["dV/dI 峰值", "電壓門檻"]


# ---------------------------------------------------------------------------
# 計算核心（SI 單位）
# ---------------------------------------------------------------------------
def ic_from_peaks(Fm, D, min_I=0.0, prominence=3.0):
    """每一列在 I > min_I 與 I < −min_I 各找 dV/dI 最大的位置。
    峰值要大於該列 |dV/dI| 中位數 × prominence 才算（否則 NaN，例如完全沒有超導的區域）。
    回傳 (Ic+, Ic−（負值）, 峰值+, 峰值−)"""
    n = D.shape[0]
    out = np.full((4, n), np.nan)
    for k in range(n):
        f, d = Fm[k], D[k]
        ok = np.isfinite(f) & np.isfinite(d)
        if ok.sum() < 3:
            continue
        base = np.nanmedian(np.abs(d[ok]))
        for j, side in enumerate((f > min_I, f < -min_I)):
            m = ok & side
            if not m.any():
                continue
            idx = np.flatnonzero(m)[np.argmax(d[m])]
            if base > 0 and d[idx] < prominence * base:
                continue
            out[j, k] = f[idx]
            out[2 + j, k] = d[idx]
    return out[0], out[1], out[2], out[3]


def ic_from_threshold(I, V, thr_V):
    Icp = np.array([switching_current(I[k], V[k], thr_V) for k in range(len(I))])
    Icn = -np.array([switching_current(-I[k], -V[k], thr_V) for k in range(len(I))])
    return Icp, Icn


def linear_slope(I_row, V_row, mask):
    ok = mask & np.isfinite(I_row) & np.isfinite(V_row)
    if ok.sum() < 3 or np.ptp(I_row[ok]) == 0:
        return np.nan
    return float(np.polyfit(I_row[ok], V_row[ok], 1)[0])


def resistance_vs_slow(I, V, r0_pct=5.0, rn_pct=80.0):
    """每一列：R₀ = |I| ≤ r0_pct% × max|I| 的線性斜率；R_N = |I| ≥ rn_pct% × max|I| 兩側的斜率（平均）。"""
    R0, RN = np.full(len(I), np.nan), np.full(len(I), np.nan)
    for k in range(len(I)):
        Ik, Vk = I[k], V[k]
        imax = np.nanmax(np.abs(Ik)) if np.isfinite(Ik).any() else np.nan
        if not np.isfinite(imax) or imax == 0:
            continue
        R0[k] = linear_slope(Ik, Vk, np.abs(Ik) <= r0_pct / 100 * imax)
        hi = [linear_slope(Ik, Vk, Ik >= rn_pct / 100 * imax), linear_slope(Ik, Vk, Ik <= -rn_pct / 100 * imax)]
        hi = [h for h in hi if np.isfinite(h)]
        RN[k] = float(np.mean(hi)) if hi else np.nan
    return R0, RN


def _gate_text(v):
    """(符號, 單位)，例如 ("V_bottom", "V")，給文字說明用。"""
    lab = plain(v.s_label)
    if "(" in lab:
        return lab[:lab.rfind("(")].strip(), lab[lab.rfind("(") + 1:].rstrip(")")
    return lab, ""


def _ohm(x):
    s, u = si_scale([x], "Ω")
    return f"{x / s:.4g} {u}"


# ---------------------------------------------------------------------------
# Ic vs Vg
# ---------------------------------------------------------------------------
@analysis("2D", "Ic vs Vg（切換電流）", group=GROUP, icon="↯", order=200, multi=False,
          modes=("current_bias",), conditions=COND, family="switching", params=[
    P("method", "找 Ic 的方法", METHODS[0], METHODS,
      tip="dV/dI 峰值：每個 Vg 在正、負電流各找 dV/dI 最大處（色圖上那兩條亮線）；電壓門檻：|V − V(0)| 第一次超過門檻"),
    P("min_I", "忽略 |I| 小於（圖上電流的單位，例如 nA）", 0.0, tip="I≈0 附近雜訊大時可以排除"),
    P("prominence", "峰值至少是該條 dV/dI 中位數的幾倍", 3.0, tip="沒有明顯峰值的 Vg（沒有超導）就不畫"),
    P("thr_uV", "電壓門檻 |V − V(0)|（μV，方法 = 電壓門檻）", 20.0),
    P("smooth", "微分前平滑（點數，0 = 不平滑）", 0),
    P("edge_skip", "去掉每條掃描頭尾的點數", 2, tip="掃描端點常有很大的假 dV/dI"),
    P("negative", "也找負向 Ic−", True),
    P("average", "另外畫 (Ic+ + |Ic−|) / 2", False),
    P("left", "左圖", "dV/dI 色圖", ["dV/dI 色圖", "量測值色圖"]),
    P("cmap", "色盤", "magma", CMAPS),
], help="左：色圖與每個 Vg 找到的 Ic（點）；右：Ic+、|Ic−| 對閘極電壓。")
def ic_vs_gate(fig, items, p):
    it = items[0]
    v = load_view(it)
    m = v.m
    I, V = m.fast.values, m.z.values
    S = v.s_axis
    min_I = float(p["min_I"]) * v.f_scale
    S_d, F_d, D_disp, dlabel, d_scale = derivative_view(v, p["smooth"], p["edge_skip"])
    if p["method"] == METHODS[0]:
        Fm = F_d * v.f_scale                                   # SI
        Icp, Icn, _, _ = ic_from_peaks(Fm, D_disp * d_scale, min_I, float(p["prominence"]))
    else:
        Icp, Icn = ic_from_threshold(I, smooth_rows(V, p["smooth"]), float(p["thr_uV"]) * 1e-6)
        if min_I > 0:
            Icp[Icp < min_I] = np.nan
            Icn[Icn > -min_I] = np.nan
    if not p["negative"]:
        Icn = np.full(len(S), np.nan)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.25, 1])
    ax = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    if p["left"] == "dV/dI 色圖":
        add_map(fig, ax, S_d, F_d, D_disp, p["cmap"], color_limits(D_disp, RANGES[0]), dlabel)
    else:
        add_map(fig, ax, v.S, v.F, v.Z, p["cmap"], color_limits(v.Z, RANGES[0]), v.z_label)
    ax.plot(S, Icp / v.f_scale, ".", color="cyan", ms=4, label="Ic+")
    if p["negative"]:
        ax.plot(S, Icn / v.f_scale, ".", color="lime", ms=4, label="Ic−")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_xlabel(v.s_label)
    ax.set_ylabel(v.f_label)
    ax.set_title(f"run {it.run_id}")
    o = np.argsort(S)
    unit = v.f_label[v.f_label.find("(") + 1:-1] if "(" in v.f_label else ""
    ax2.plot(S[o], Icp[o] / v.f_scale, "o-", ms=3, label="Ic+")
    if p["negative"]:
        ax2.plot(S[o], -Icn[o] / v.f_scale, "s-", ms=3, label="|Ic−|")
    if p["average"] and p["negative"]:
        avg = np.nanmean(np.vstack([Icp, -Icn]), axis=0)
        ax2.plot(S[o], avg[o] / v.f_scale, "-", color="0.3", lw=2, label="平均")
    ax2.set_xlabel(v.s_label)
    ax2.set_ylabel(f"Ic ({unit})" if unit else "Ic")
    ax2.legend(fontsize=8)
    ax2.set_ylim(bottom=0)

    g, gu = _gate_text(v)
    lines = [f"run {it.run_id}  {it.info.mode_short}：{len(S)} 個閘極電壓，{g} = {S.min():.4g} ~ {S.max():.4g} {gu}",
             f"方法：{p['method']}"]
    for name, arr in (("Ic+", Icp), ("|Ic−|", -Icn)):
        if np.isfinite(arr).any():
            kmax, kmin = int(np.nanargmax(arr)), int(np.nanargmin(arr))
            lines.append(f"{name}：最大 {arr[kmax] / v.f_scale:.4g} {unit}（{g} = {S[kmax]:+.4g} {gu}）；"
                         f"最小 {arr[kmin] / v.f_scale:.4g} {unit}（{g} = {S[kmin]:+.4g} {gu}）")
    n_nan = int(np.sum(~np.isfinite(Icp)))
    if n_nan:
        lines.append(f"有 {n_nan} 個 Vg 找不到 Ic+（沒有明顯峰值 / 沒有超過門檻）")
    rows = [[s, a / v.f_scale, -b / v.f_scale] for s, a, b in zip(S[o], Icp[o], Icn[o])]
    return Result(text="\n".join(lines),
                  export={"Ic_vs_Vg": ([plain(v.s_label), f"Ic+ ({unit})", f"|Ic-| ({unit})"], rows)})


# ---------------------------------------------------------------------------
# R vs Vg
# ---------------------------------------------------------------------------
@analysis("2D", "R vs Vg（電阻）", group=GROUP, icon="Ω", order=210, multi=False,
          modes=("current_bias",), conditions=COND, family="resistance", params=[
    P("r0_pct", "R₀ 擬合範圍：|I| ≤ 最大電流的 (%)", 5.0, tip="零偏壓附近的線性斜率"),
    P("rn_pct", "R_N 擬合範圍：|I| ≥ 最大電流的 (%)", 80.0, tip="高偏壓（正負兩側平均）的斜率"),
    P("quantity", "顯示", "電阻 R", ["電阻 R", "電導 G = 1/R"]),
    P("yscale", "Y 軸", "linear", ["linear", "log"]),
    P("ratio", "另外畫 R₀ / R_N", True),
], help="每個閘極電壓的零偏壓電阻 R₀ 與高偏壓電阻 R_N（場效應）。")
def resistance_vs_gate(fig, items, p):
    it = items[0]
    v = load_view(it)
    m = v.m
    S = v.s_axis
    R0, RN = resistance_vs_slow(m.fast.values, m.z.values, float(p["r0_pct"]), float(p["rn_pct"]))
    o = np.argsort(S)
    conduct = p["quantity"].startswith("電導")
    with np.errstate(divide="ignore", invalid="ignore"):
        Y0, YN = (1 / R0, 1 / RN) if conduct else (R0, RN)
    unit0 = "S" if conduct else "Ω"
    sc, u = si_scale(np.concatenate([Y0[np.isfinite(Y0)], YN[np.isfinite(YN)]]) if np.isfinite(Y0).any() else [1],
                     unit0)
    sym = "G" if conduct else "R"
    if p["ratio"]:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.3, 1])
        ax, ax2 = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    else:
        ax, ax2 = fig.add_subplot(), None
    ax.plot(S[o], Y0[o] / sc, "o-", ms=3, label=f"{sym}₀（|I| ≤ {p['r0_pct']:g}%）")
    ax.plot(S[o], YN[o] / sc, "s-", ms=3, label=f"{sym}_N（|I| ≥ {p['rn_pct']:g}%）")
    ax.set_xlabel(v.s_label)
    ax.set_ylabel(f"{sym} ({u})")
    ax.set_yscale(p["yscale"])
    ax.legend(fontsize=8)
    ax.set_title(f"run {it.run_id}")
    if ax2 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = R0 / RN
        ax2.plot(S[o], ratio[o], "o-", ms=3, color="C2")
        ax2.axhline(1, color="0.6", lw=1, ls="--")
        ax2.set_xlabel(v.s_label)
        ax2.set_ylabel("R₀ / R_N")
    g, gu = _gate_text(v)
    lines = [f"run {it.run_id}：{len(S)} 個閘極電壓"]
    if np.isfinite(R0).any():
        kmax, kmin = int(np.nanargmax(R0)), int(np.nanargmin(R0))
        lines.append(f"R₀：最大 {_ohm(R0[kmax])}（{g} = {S[kmax]:+.4g} {gu}）；"
                     f"最小 {_ohm(R0[kmin])}（{g} = {S[kmin]:+.4g} {gu}）")
    if np.isfinite(RN).any():
        lines.append(f"R_N：{_ohm(np.nanmin(RN))} ~ {_ohm(np.nanmax(RN))}")
    rows = [[s, a, b] for s, a, b in zip(S[o], R0[o], RN[o])]
    return Result(text="\n".join(lines),
                  export={"R_vs_Vg": ([plain(v.s_label), "R0 (Ohm)", "RN (Ohm)"], rows)})


# ---------------------------------------------------------------------------
# 指定 Vg 的 IV 疊圖
# ---------------------------------------------------------------------------
@analysis("2D", "指定 Vg 的 IV 疊圖", group=GROUP, icon="≋", order=220, multi=False,
          conditions=COND, family="cuts", params=[
    P("vgs", "閘極電壓位置（V）", "-15:15:5", tip="起:終:間隔，或用逗號列出：-10, -5, 0, 5, 10"),
    P("show", "顯示", "IV + dV/dI", ["IV + dV/dI", "只有 IV", "只有 dV/dI"]),
    P("offset", "每條往上平移（Y 軸單位）", 0.0),
    P("smooth", "微分前平滑（點數，0 = 不平滑）", 0),
    P("edge_skip", "去掉每條掃描頭尾的點數", 2),
    P("cmap", "色盤", "coolwarm", CMAPS),
], help="在指定的閘極電壓切出 IV（與 dV/dI），疊在一起比較；沒有剛好的 Vg 會用最近的。")
def gate_cuts(fig, items, p):
    it = items[0]
    v = load_view(it)
    S = v.s_axis
    targets = parse_positions(p["vgs"])
    if not targets:
        raise ValueError("請在「閘極電壓位置」填至少一個數字")
    sel, notes, _, _ = select_nearest(S, targets)
    S_d, F_d, D_disp, dlabel, _ = derivative_view(v, p["smooth"], p["edge_skip"])
    panels = {"IV + dV/dI": ("iv", "d"), "只有 IV": ("iv",), "只有 dV/dI": ("d",)}[p["show"]]
    axs = [fig.add_subplot(1, len(panels), k + 1) for k in range(len(panels))]
    cmap = colormaps[p["cmap"]]
    off = float(p["offset"])
    g, gu = _gate_text(v)
    rows = []
    for n, k in enumerate(sel):
        col = cmap(n / max(len(sel) - 1, 1))
        lab = f"{S[k]:+.4g}"
        for ax, kind in zip(axs, panels):
            if kind == "iv":
                ax.plot(v.F[k], v.Z[k] + n * off, color=col, lw=1.3, label=lab)
            else:
                ax.plot(F_d[k], D_disp[k] + n * off, color=col, lw=1.3, label=lab)
        rows += [[S[k], f, z] for f, z in zip(v.F[k], v.Z[k])]
    for ax, kind in zip(axs, panels):
        ax.set_xlabel(v.f_label)
        ax.set_ylabel(v.z_label if kind == "iv" else dlabel)
    axs[-1].legend(fontsize=8, title=v.s_label, title_fontsize=8)
    axs[0].set_title(f"run {it.run_id}")
    text = [f"run {it.run_id}：畫了 {len(sel)} 條（{g} = " + ", ".join(f"{S[k]:+.4g}" for k in sel) + f" {gu}）"]
    text += notes
    return Result(text="\n".join(text),
                  export={"IV_at_Vg": ([plain(v.s_label), plain(v.f_label), plain(v.z_label)], rows)})
