"""Ic vs T 擬合（Ic_T_analysis.ipynb 的四個階段）。

資料來源：
  · 預設：選取的 run → 「轉折點追蹤 + I vs T」的轉折位置（同一組設定，包含你拖曳修改過的紅點）
  · 或從 CSV 讀（轉折點分析匯出的 transitions.csv / transitions_long.csv）
Ic = 正側轉折 − 負側轉折（mA），例如 Ic outer = (outer +) − (outer −)。
計算核心在 lta/core/icfit.py。
"""
import csv
import os

import numpy as np

from ..core.registry import P, Result, analysis
from ..core import icfit
from . import linearized as LIN
from . import step_transitions as ST
from . import transitions as D2
from ._ic_rules import RULES, apply as apply_rules, parse_floats, parse_zero_from, duplicate_note

VIEWS = ["最終圖（總覽）", "轉折位置 + Ic vs T", "Stage 1：power law（自由 Tc, p, q）",
         "Stage 2：power law（固定 bulk Tc）", "Stage 3：共用 Tc", "Stage 4：接面 / 薄膜模型",
         "Stage 4：SNS 鄰近效應", "全部階段（格狀）"]
CURVE_COLORS = ["tab:blue", "tab:orange", "tab:green", "tab:red", "tab:purple", "tab:brown"]
EXP_COLORS = ["#1f4e79", "#d35400", "#1e7b34", "#9b1c1c", "#5b2c83", "#6d4c2f"]
FIT_STYLES = ["-", "--", "-.", ":"]

SOURCES = ["log dV/dI 台階（轉折點追蹤）", "IV 二次微分"]

PARAMS = [
    P("ic_view", "顯示", VIEWS[0], choices=VIEWS),
    P("ic_source", "轉折點來源", SOURCES[0], choices=SOURCES,
      tip="和「轉折點追蹤 + I vs T」或「IV 轉折點（二次微分）」用同一組設定（含手動修改的紅點）"),
    P("ic_curves", "Ic 的定義（台階來源）　名稱: 正側線, 負側線", "outer: outer +, outer -; inner: inner +, inner -",
      tip="線名稱和「轉折點追蹤」的轉折線設定相同。要多一條：…; center: center +, center -。二次微分來源固定為 outer、inner"),
] + RULES + [
    P("extra_zero_T", "額外 Ic = 0 的溫度 (K)", "",
      tip="沒有選到、但已知 Ic = 0 的溫度，例如 3.1；用來決定 Tc 的上限"),
    P("T_bulk", "bulk Tc (K)", 7.2),
    P("tc_bracket", "Tc 限制在「最後一個非零點 ~ 第一個 Ic=0」之間", True),
    P("shared_tc", "Stage 4 的共用 Tc", "Stage 3 (p=2)", tip="Stage 3 (p=1)、Stage 3 (p=2)，或直接填數字（K）"),
    P("sns_curves", "Stage 4 用 SNS 模型的曲線", "inner", tip="其他曲線用 AB / KO / GL / KL；多條用逗號分開"),
    P("t0_max", "T0 擬合上限 (K)", 100.0),
    P("final", "最終圖要畫的擬合", "outer: AB (free Tc); inner: Diffusive SNS (free Tc), Diffusive SNS (shared Tc)",
      lines=2, tip="曲線: 模型, 模型; 曲線: 模型。可用的模型名稱列在結果文字的總表裡"),
    P("csv", "改從 CSV 讀轉折點（空白 = 用選取的 run）", "",
      tip="轉折點分析匯出的 transitions.csv 或 transitions_long.csv 的完整路徑"),
]


# ---------------------------------------------------------------------------
# 參數解析
# ---------------------------------------------------------------------------
def _split(text):
    return [s.strip() for s in str(text or "").replace("；", ";").replace("\n", ";").split(";") if s.strip()]


def parse_curves(text):
    out = []
    for part in _split(text):
        if ":" not in part:
            raise ValueError(f"Ic 定義格式是「名稱: 正側線, 負側線」，收到：{part}")
        name, rest = (s.strip() for s in part.replace("：", ":").split(":", 1))
        lines = [s.strip() for s in rest.replace("，", ",").split(",") if s.strip()]
        if len(lines) != 2:
            raise ValueError(f"Ic「{name}」需要兩條線（正側, 負側），收到：{rest}")
        out.append((name, lines[0], lines[1]))
    if not out:
        raise ValueError("Ic 的定義是空的")
    return out


def parse_final(text):
    out = []
    for part in _split(text):
        if ":" not in part:
            raise ValueError(f"最終圖的格式是「曲線: 模型, 模型」，收到：{part}")
        c, rest = (s.strip() for s in part.replace("：", ":").split(":", 1))
        # 模型名稱裡有逗號（free Tc, p, q），所以只在「), 」處分開
        models, depth, cur = [], 0, ""
        for ch in rest:
            depth += ch == "("
            depth -= ch == ")"
            if ch in ",，" and depth == 0:
                models.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            models.append(cur.strip())
        out += [(c, m) for m in models if m]
    return out


# ---------------------------------------------------------------------------
# 資料
# ---------------------------------------------------------------------------
def read_csv_positions(path):
    """→ (Ts, {線名稱: 位置 mA})"""
    path = os.path.expanduser(str(path).strip().strip('"'))
    if not os.path.isfile(path):
        raise ValueError(f"找不到 CSV：{path}")
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    head = [h.strip() for h in rows[0]]
    body = [r for r in rows[1:] if any(c.strip() for c in r)]
    low = [h.lower() for h in head]
    if "line" in low and "i_ua" in low:                       # 長格式
        il, iT, iI = low.index("line"), low.index("t_k"), low.index("i_ua")
        Ts = sorted({float(r[iT]) for r in body})
        names = list(dict.fromkeys(r[il] for r in body))
        pos = {n: np.full(len(Ts), np.nan) for n in names}
        for r in body:
            pos[r[il]][Ts.index(float(r[iT]))] = float(r[iI]) * 1e-3
        return np.array(Ts), pos
    if "t_k" in low:                                          # 寬格式
        iT = low.index("t_k")
        cols = [(k, h) for k, h in enumerate(head) if k != iT and h.lower() != "run_id"]
        Ts = np.array([float(r[iT]) for r in body])
        pos = {}
        for k, h in cols:
            scale = 1.0 if "(ma)" in h.lower() else 1e-3
            name = h.split("(")[0].strip()
            pos[name] = np.array([float(r[k]) * scale if r[k].strip() else np.nan for r in body])
        return Ts, pos
    raise ValueError("CSV 需要有 T_K 欄（寬格式）或 line / T_K / I_uA 欄（長格式）")


def gather(items, p, progress=None):
    """→ dict(Ts, pos, Ic, curve_defs, source)"""
    defs = parse_curves(p["ic_curves"])
    note = ""
    if str(p.get("csv", "")).strip():
        Ts, pos = read_csv_positions(p["csv"])
        source = f"CSV：{os.path.basename(str(p['csv']).strip().strip(chr(34)))}"
    elif p.get("ic_source") == SOURCES[1]:
        data = D2.compute(items, p, progress)
        Ts = data["xs"]
        pos = {n: data["raw_I"][:, k] for k, n in enumerate(D2.NAMES)}
        defs = D2.CURVE_DEFS
        source = f"選取的 {len(data['items'])} 筆 run（IV 二次微分）"
        note = duplicate_note(data["items"])
        if not data["has_T"]:
            raise ValueError("Ic 擬合需要每筆 run 的溫度")
    else:
        items = sorted(items, key=lambda it: (it.T is None, it.T or 0, it.run_id))
        data = ST.compute(items, p)
        names = [l["name"] for l in data["lines"]]
        runs = [q["run_id"] for q in data["processed"]]
        tab = ST.steps.table(data["results"], data["lines"], runs)
        Ts = np.array([q["T"] for q in data["processed"]], float)
        pos = {n: np.array([tab[r][n] for r in runs]) * 1e-3 for n in names}
        source = f"選取的 {len(runs)} 筆 run（轉折點追蹤的設定與手動修改）"
        note = duplicate_note(items)
    Ts, pos, Ic = apply_rules(Ts, pos, defs, p, parse_floats(p["extra_zero_T"], "額外 Ic = 0 的溫度"))
    return dict(Ts=Ts, pos=pos, Ic=Ic, defs=defs, source=source, note=note)


# ---------------------------------------------------------------------------
# 繪圖
# ---------------------------------------------------------------------------
def _colors(fitter):
    return {n: (CURVE_COLORS[k % len(CURVE_COLORS)], EXP_COLORS[k % len(EXP_COLORS)])
            for k, n in enumerate(fitter.curves)}


def _T_fine(fitter, Ts):
    lo = np.nanmin(Ts) - 0.05
    hi = max(c["T"].max() for c in fitter.curves.values()) + 0.05
    return np.linspace(lo, hi, 400)


def plot_stage(ax, fitter, Ts, results, title, small=False):
    col = _colors(fitter)
    for n, c in fitter.curves.items():
        ax.plot(c["T"], c["Ic"], "o", color=col[n][0], ms=4 if small else 6, label=f"Ic {n} (exp.)")
    T_fine = _T_fine(fitter, Ts)
    count = {}
    for res in results:
        k = count.get(res["curve"], 0)
        count[res["curve"]] = k + 1
        ax.plot(T_fine, res["func"](T_fine, **res["params"]), FIT_STYLES[k % len(FIT_STYLES)],
                color=col[res["curve"]][0], lw=1.6, gid="nohover",
                label=f"{res['curve']}: {res['label']} (R²={res['R2']:.3f})" + (" ⚠" if res["at_bound"] else ""))
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Ic (mA)")
    ax.set_title(title, fontsize=9 if small else 11)
    ax.grid(True, ls=":", alpha=0.5)
    if small:
        ax.legend(fontsize=6, loc="best")
    else:
        ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)


def plot_data(ax_pos, ax_ic, d, fitter):
    for k, (n, v) in enumerate(d["pos"].items()):
        side_neg = n.strip()[-1:] in "-−"
        base = ST.base_name(n)
        bases = list(dict.fromkeys(ST.base_name(x) for x in d["pos"]))
        c = ST.PAIR_COLORS[bases.index(base) % len(ST.PAIR_COLORS)]
        ax_pos.plot(d["Ts"], v, "o-", color=c, mfc="white" if side_neg else c, ms=5, lw=1.3, label=n)
    ax_pos.axhline(0, color="gray", lw=0.8, gid="nohover")
    ax_pos.set_xlabel("Temperature (K)")
    ax_pos.set_ylabel("Transition current (mA)")
    ax_pos.legend(fontsize=7)
    col = _colors(fitter)
    for n, ic in d["Ic"].items():
        ok = np.isfinite(ic)
        ax_ic.plot(d["Ts"][ok], ic[ok], "o-", lw=1.5, ms=6, label=f"Ic {n}",
                   color=col.get(n, ("gray",))[0])
    ax_ic.set_xlabel("Temperature (K)")
    ax_ic.set_ylabel("Ic (mA)")
    ax_ic.legend(fontsize=8)


def plot_final(ax, fitter, Ts, final, small=False):
    col = _colors(fitter)
    for n, c in fitter.curves.items():
        ax.plot(c["T"], c["Ic"], "o", color=col[n][1], ms=5 if small else 7, label=f"Ic {n} (exp.)")
    T_fine = _T_fine(fitter, Ts)
    styles = {"0": [("-", "tab:blue")], "1": [("-", "tab:orange"), ("--", "tab:red"), (":", "tab:brown")]}
    used, missing = {}, []
    for curve, label in final:
        res = fitter.find(curve, label)
        if res is None:
            missing.append(f"{curve}: {label}")
            continue
        k = used.get(curve, 0)
        used[curve] = k + 1
        idx = list(fitter.curves).index(curve)
        pal = styles.get(str(idx))
        ls, c = pal[k % len(pal)] if pal else (FIT_STYLES[k % 4], col[curve][0])
        prm = res["params"]
        extra = f", T0 = {prm['T0']:.3g} K" if "T0" in prm else ""
        warn = " (bound)" if res["at_bound"] else ""
        ax.plot(T_fine, res["func"](T_fine, **prm), ls, color=c, lw=2, gid="nohover",
                label=f"{res['label'].split(' (')[0]} (Ic {curve}): Tc = {prm['Tc']:.2f} K{extra}{warn}, "
                      f"R² = {res['R2']:.3f}")
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Ic (mA)")
    ax.grid(True, ls=":", alpha=0.5)
    if small:
        ax.set_title("Final fits", fontsize=9)
        ax.legend(fontsize=6, loc="best")
    else:
        ax.legend(fontsize=8, frameon=False, loc="upper left", bbox_to_anchor=(1.02, 1), borderaxespad=0)
    return missing


# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------
def report(d, fitter, missing):
    out = [f"資料來源：{d['source']}"] + ([d["note"]] if d.get("note") else []) + \
        ["", "Ic (mA)", "  T(K) " + "".join(f"{n:>11s}" for n in d["Ic"])]
    for k, T in enumerate(d["Ts"]):
        out.append(f"  {T:5.2f}" + "".join(f"{v[k]:+11.4f}" if np.isfinite(v[k]) else f"{'--':>11s}"
                                         for v in d["Ic"].values()))
    out.append("")
    for n, c in fitter.curves.items():
        lo, hi = fitter.tc_bounds(n)
        out.append(f"Ic {n}：{c['T_fit'].size} 個非零點（{c['T_fit'].min():.2f}–{c['T_fit'].max():.2f} K），"
                   f"Tc 擬合範圍 [{lo:.2f}, {hi:.2f}] K")
    if getattr(fitter, "shared", (None,))[0] is not None:
        out.append(f"Stage 4 共用 Tc = {fitter.shared[0]:.3f} K（{fitter.shared[1]}）")
    out += ["", "擬合總表（各曲線依 R² 排序）",
            f"  {'stage':<8} {'curve':<7} {'model':<32} {'R²':>8} {'RMSE':>8}  參數"]
    rows = fitter.summary()
    for r in rows:
        out.append(f"  {r['stage']:<8} {r['curve']:<7} {r['model']:<32} {r['R2']:>8.4f} {r['RMSE']:>8.4f}  "
                   f"{r['params']}")
    out.append("")
    for n in fitter.curves:
        best = next((r for r in rows if r["curve"] == n), None)
        if best:
            flag = f"（注意：{best['at_bound']} 貼邊界）" if best["at_bound"] else ""
            out.append(f"Ic {n}：R² 最高 = {best['model']} [{best['stage']}]，R² = {best['R2']:.4f}{flag}")
    if missing:
        out.append(f"\n⚠ 最終圖找不到這些擬合：{'; '.join(missing)}（名稱請照上面總表的 model 欄）")
    if fitter.notes:
        out += [""] + fitter.notes
    export = {
        "Ic_vs_T": (["T_K"] + [f"Ic_{n}_mA" for n in d["Ic"]],
                    [[T] + [round(v[k], 6) if np.isfinite(v[k]) else "" for v in d["Ic"].values()]
                     for k, T in enumerate(d["Ts"])]),
        "Ic_fit_summary": (["stage", "curve", "model", "R2", "RMSE_mA", "params", "at_bound"],
                           [[r["stage"], r["curve"], r["model"], round(r["R2"], 5), round(r["RMSE"], 5),
                             r["params"], r["at_bound"]] for r in rows]),
    }
    return "\n".join(out), export


_FIT_CACHE = {}


def fit_all(d, p, progress=None):
    """所有擬合（同樣的資料與設定直接用上次的結果）。"""
    key = (d["Ts"].tobytes(), tuple((n, v.tobytes()) for n, v in d["Ic"].items()),
           bool(p["tc_bracket"]), float(p["T_bulk"]), float(p["t0_max"]), str(p["sns_curves"]), str(p["shared_tc"]))
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]
    fitter = _fit_all(d, p, progress)
    if len(_FIT_CACHE) > 30:
        _FIT_CACHE.clear()
    _FIT_CACHE[key] = fitter
    return fitter


def _fit_all(d, p, progress=None):
    curves = {n: icfit.prepare_curve(d["Ts"], v) for n, v in d["Ic"].items()}
    empty = [n for n, c in curves.items() if c is None]
    fitter = icfit.IcFitter(curves, p["tc_bracket"], p["T_bulk"], p["t0_max"])
    if not fitter.curves:
        raise ValueError("所有 Ic 都是 0 或沒有資料，無法擬合")
    sns = [s.strip() for s in str(p["sns_curves"]).replace("，", ",").split(",") if s.strip()]
    fitter.run_all(sns, p["shared_tc"], progress=progress)
    if empty:
        fitter.notes.insert(0, f"Ic {', '.join(empty)} 沒有非零點，未擬合")
    return fitter


# ---------------------------------------------------------------------------
# 介面
# ---------------------------------------------------------------------------
def _compute_hook(items, p, progress):
    if p.get("ic_source") != SOURCES[1] and not str(p.get("csv", "")).strip():
        ST.warm(items, p, progress)
    d = gather(items, p, progress)
    return dict(d=d, fitter=fit_all(d, p, progress))


@analysis("1D", "Ic vs T 擬合（power law / AB / SNS）", group="進階", icon="↘", order=114,
          modes=("current_bias",), store=LIN.STORE, params=PARAMS, compute=_compute_hook,
          help="轉折位置來自「轉折點追蹤」或「IV 二次微分」（同一組設定，含手動修改的紅點）。Ic = 正側 − 負側。")
def ic_vs_T(fig, items, p):
    if p.get("_data"):
        d, fitter = p["_data"]["d"], p["_data"]["fitter"]
    else:
        d = gather(items, p)
        fitter = fit_all(d, p)
    final = parse_final(p["final"])
    view = p["ic_view"]
    missing = []
    Ts = d["Ts"]
    if view == VIEWS[0]:
        missing = plot_final(fig.add_subplot(111), fitter, Ts, final)
    elif view == VIEWS[1]:
        a1, a2 = fig.subplots(1, 2)
        plot_data(a1, a2, d, fitter)
    elif view == VIEWS[7]:
        _grid(fig.subplots(2, 3, gridspec_kw=dict(hspace=0.5, wspace=0.3)).ravel(), d, fitter, Ts, final)
    else:
        stage = {VIEWS[2]: ("Stage 1", None), VIEWS[3]: ("Stage 2", None), VIEWS[4]: ("Stage 3", None),
                 VIEWS[5]: ("Stage 4", icfit.FILM_MODELS), VIEWS[6]: ("Stage 4", icfit.SNS_MODELS)}[view]
        res = fitter.stage_results(*stage)
        title = view.replace("：", ": ")
        plot_stage(fig.add_subplot(111), fitter, Ts, res, title)
    text, export = report(d, fitter, missing or [f"{c}: {m}" for c, m in final if fitter.find(c, m) is None])
    return Result(text=text, export=export, hint="點圖例可隱藏 / 顯示該曲線；拖曳圖例可移動位置")


def _grid(axs, d, fitter, Ts, final):
    col = _colors(fitter)
    ax = axs[0]
    for n, ic in d["Ic"].items():
        ok = np.isfinite(ic)
        ax.plot(Ts[ok], ic[ok], "o-", ms=4, lw=1.2, label=f"Ic {n}", color=col.get(n, ("gray",))[0])
    ax.set_title("Ic vs T", fontsize=9)
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel("Ic (mA)")
    ax.legend(fontsize=6)
    for a, (title, stage, labels) in zip(axs[1:5], [
            ("Stage 1", "Stage 1", None), ("Stage 2", "Stage 2", None), ("Stage 3", "Stage 3", None),
            ("Stage 4", "Stage 4", None)]):
        plot_stage(a, fitter, Ts, fitter.stage_results(stage, labels), title, small=True)
    plot_final(axs[5], fitter, Ts, final, small=True)
