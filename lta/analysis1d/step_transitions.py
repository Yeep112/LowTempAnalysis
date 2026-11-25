"""轉折點追蹤（log10 dV/dI 台階）+ 轉折電流 I vs T。

和「線性化 dV/dI」共用 edge、平移、修補區間（逐筆設定表的同一組值）。
紅點可以直接在圖上修改：
  · 左邊疊圖：左右拖曳紅點 → 這筆 run 這條線的位置
  · 右邊 I vs T：上下拖曳 → 同上
  · 空心灰點 = 這個溫度沒找到；拖曳它就補上一個點
  · 右鍵紅點：刪除（這筆不畫）或恢復自動偵測
修改會寫進逐筆設定表的「手動轉折點」欄，例如  inner + = 430; center - = none
"""
import numpy as np

from ..core.registry import P, Result, Drag, analysis, item_value
from ..core import steps
from . import linearized as LIN

VIEWS = ["疊圖 + I vs T", "只有疊圖", "只有 I vs T"]
UNITS = ["mA", "μA"]
PAIR_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf"]
RED = "#e11d1d"

PARAMS = [
    P("lines", "轉折線設定　名稱 | 猜測 (μA) | 搜尋寬 (μA) | 門檻 (decade) | 其他", steps.DEFAULT_LINES_TEXT,
      lines=7, tip="猜測：edge（用 edge 位置）、數字（最低溫起點，之後追蹤）、或 溫度:位置, 溫度:位置（內插）。"
                   "搜尋寬可寫數字或 溫度:寬度。其他：window=台階窗寬 missing=可連續漏失次數。"
                   "名稱以 + / - 結尾決定正負側。"),
    P("median_uA", "偵測前中值濾波寬度 (μA)", 9.0),
    P("step_window", "台階左右平均窗寬 (μA)", 15.0),
    P("max_missing", "連續找不到幾個溫度就停止", 1),
    P("min_gap", "同側相鄰兩線最小間距 (μA)", 20.0),
    P("view", "顯示", VIEWS[0], choices=VIEWS),
    P("unit", "I vs T 的電流單位", UNITS[0], choices=UNITS),
    P("show_empty", "顯示沒找到的空位（拖曳可補點）", True),
    P("connect", "疊圖上用紅線連接", True),
]
FIX = P("fix", "手動轉折點", "", per_item=True,
        tip="例：inner + = 430; center - = none（none = 這筆不畫點）。在圖上拖曳紅點會自動填入。")


def defaults(items, p):
    d = LIN.defaults(items, p)
    for v in d.values():
        v["fix"] = ""
    return d


def base_name(name):
    n = name.strip()
    while n and n[-1] in "+-−":
        n = n[:-1]
    return n.strip() or name


def compute(items, p):
    """選取的 run → 轉折點。Ic 擬合也用這個函式。"""
    if any(it.T is None for it in items):
        miss = ", ".join(str(it.run_id) for it in items if it.T is None)
        raise ValueError(f"轉折線追蹤需要溫度，這些 run 沒有溫度：{miss}")
    lines = steps.parse_lines_text(p["lines"])
    names = {l["name"] for l in lines}
    settings = LIN.item_settings(items, p)
    processed, manual, unknown = [], {}, set()
    for it in items:
        neg, pos, off, ranges = settings[it.run_id]
        r = LIN.process(it, neg, pos, p["width"], True, p["despike"], ranges)
        processed.append(dict(run_id=it.run_id, T=it.T, label=it.label, color=it.color,
                              I=r["x_uA"], log=r["y"], neg_edge=neg, pos_edge=pos, shift=off))
        for n, v in steps.parse_fix(item_value(p, "fix", it, "")).items():
            if n in names:
                manual[(n, it.run_id)] = v
            else:
                unknown.add(n)
    results, empty = steps.detect(processed, lines, p["median_uA"], p["step_window"],
                                  p["max_missing"], p["min_gap"], manual)
    return dict(lines=lines, processed=processed, results=results, empty=empty,
                unknown=sorted(unknown), manual=manual)


def _drag(name, rid, axis, to_uA=lambda v: v):
    return Drag("fix", rid, axis=axis,
                value=lambda v, old: steps.set_fix(old, name, round(to_uA(v), 2)),
                group=("tr", name, rid, axis),
                menu=[("刪除這個點（這筆不畫）", lambda old: steps.set_fix(old, name, None)),
                      ("恢復自動偵測", lambda old: steps.set_fix(old, name, "auto"))],
                tip=f"{name} · run {rid}：{'左右' if axis == 'x' else '上下'}拖曳修改位置，右鍵可刪除或恢復自動")


def _overlay(ax, data, p):
    by_run = {q["run_id"]: q for q in data["processed"]}
    for q in data["processed"]:
        ax.plot(q["I"], q["log"] + q["shift"], color=q["color"], lw=1.2, label=q["label"])
    for name, pts in data["results"].items():
        pts = sorted(pts, key=lambda q: q["T"])
        if p["connect"] and len(pts) > 1:
            ax.plot([q["I_uA"] for q in pts], [q["y"] + by_run[q["run_id"]]["shift"] for q in pts],
                    "-", color=RED, lw=2.0, zorder=9, gid="nohover")
        for q in pts:
            y = q["y"] + by_run[q["run_id"]]["shift"]
            if q["manual"]:
                mk, = ax.plot([q["I_uA"]], [y], "D", ms=6.5, color=RED, mec="black", mew=1.0,
                              zorder=11, gid="nohover")
            else:
                mk, = ax.plot([q["I_uA"]], [y], "o", ms=5.5, color=RED, zorder=11, gid="nohover")
            mk.lta_drag = _drag(name, q["run_id"], "x")
    if p["show_empty"]:
        for name, pts in data["empty"].items():
            for q in pts:
                y = q["y"] + by_run[q["run_id"]]["shift"]
                mk, = ax.plot([q["I_uA"]], [y], "o", ms=6, mfc="none",
                              mec=RED if q["removed"] else "0.55", mew=1.2,
                              alpha=0.55 if q["removed"] else 0.8, zorder=10, gid="nohover")
                spec = _drag(name, q["run_id"], "x")
                spec.tip = (f"{name} · run {q['run_id']}：已刪除，右鍵可恢復自動；拖曳可放回"
                            if q["removed"] else f"{name} · run {q['run_id']}：沒找到，拖曳到正確位置可補點")
                mk.lta_drag = spec
    ax.set_xlabel("I (μA)")
    ax.set_ylabel("log10(dV/dI / Ω)")
    ax.legend(title="Temperature", fontsize=8, title_fontsize=8, loc="upper left",
              bbox_to_anchor=(1.01, 1), borderaxespad=0)


def _i_vs_T(ax, data, p):
    scale = 1e-3 if p["unit"] == "mA" else 1.0
    bases = []
    for l in data["lines"]:
        b = base_name(l["name"])
        if b not in bases:
            bases.append(b)
    color_of = {b: PAIR_COLORS[k % len(PAIR_COLORS)] for k, b in enumerate(bases)}
    side_of = {l["name"]: l["side"] for l in data["lines"]}

    def mean_I(n):
        v = [q["I_uA"] for q in data["results"][n]]
        return np.mean(v) if v else (-np.inf if side_of[n] == "neg" else np.inf)
    order = sorted(data["results"], key=mean_I, reverse=True)
    for name in order:
        pts = sorted(data["results"][name], key=lambda q: q["T"])
        if not pts:
            continue
        col = color_of[base_name(name)]
        hollow = side_of[name] == "neg"
        ax.plot([q["T"] for q in pts], [q["I_uA"] * scale for q in pts], "-", color=col, lw=1.5,
                marker="o", ms=6, mfc="white" if hollow else col, mec=col, label=name)
        for q in pts:
            mk, = ax.plot([q["T"]], [q["I_uA"] * scale], "D" if q["manual"] else "o",
                          ms=7 if q["manual"] else 6, color=col, mfc="white" if hollow else col,
                          mec="black" if q["manual"] else col, mew=1.2 if q["manual"] else 1.0,
                          zorder=11, gid="nohover")
            mk.lta_drag = _drag(name, q["run_id"], "y", to_uA=lambda v, s=scale: v / s)
    ax.axhline(0, color="gray", lw=0.8, gid="nohover")
    ax.set_xlabel("Temperature (K)")
    ax.set_ylabel(f"I ({p['unit']})")
    ax.legend(fontsize=8, loc="upper left", bbox_to_anchor=(1.01, 1), borderaxespad=0)


def report(data):
    """文字表格與匯出資料。"""
    names = [l["name"] for l in data["lines"]]
    tab = steps.table(data["results"], data["lines"], [q["run_id"] for q in data["processed"]])
    manual = data["manual"]
    out = ["轉折位置 (μA)　＊ = 手動修改",
           "run    T(K) " + "".join(f"{n:>12s}" for n in names)]
    wide, long_rows = [], []
    for q in sorted(data["processed"], key=lambda q: q["T"]):
        rid = q["run_id"]
        row = f"{rid:<5d} {q['T']:5.2f} "
        for n in names:
            v = tab[rid][n]
            mark = "＊" if (n, rid) in manual else " "
            row += (f"{v:+10.1f}{mark}" if np.isfinite(v) else f"{'--':>10s}{mark}")
        out.append(row)
        wide.append([rid, q["T"]] + [round(tab[rid][n], 3) if np.isfinite(tab[rid][n]) else "" for n in names])
    for n in names:
        for q in sorted(data["results"][n], key=lambda q: q["T"]):
            long_rows.append([n, q["run_id"], q["T"], round(q["I_uA"], 3), round(q["y"], 5),
                              "" if not np.isfinite(q["step"]) else round(q["step"], 4),
                              "manual" if q["manual"] else "auto"])
    if data["unknown"]:
        out.append(f"\n⚠ 手動轉折點裡有不存在的線名稱：{', '.join(data['unknown'])}")
    export = {"transitions": (["run_id", "T_K"] + [f"{n} (uA)" for n in names], wide),
              "transitions_long": (["line", "run_id", "T_K", "I_uA", "log10_dVdI", "step_decade", "source"],
                                   long_rows)}
    return "\n".join(out), export


@analysis("1D", "轉折點追蹤 + I vs T（log dV/dI 台階）", group="進階", icon="◈", order=112,
          modes=("current_bias",), item_defaults=defaults, store=LIN.STORE,
          params=LIN.EDGE_PARAMS + PARAMS + LIN.ITEM_PARAMS[:3] + [FIX] + LIN.ITEM_PARAMS[3:],
          help="和「線性化 dV/dI」共用 edge 與逐筆設定。紅點可拖曳修改，結果也會用在「Ic vs T 擬合」。")
def step_transitions(fig, items, p):
    items = sorted(items, key=lambda it: (it.T is None, it.T or 0, it.run_id))
    data = compute(items, p)
    view = p["view"]
    if view == VIEWS[0]:
        gs = fig.add_gridspec(1, 2, width_ratios=[1.55, 1], wspace=0.55)
        ax_ov, ax_it = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    elif view == VIEWS[1]:
        ax_ov, ax_it = fig.add_subplot(111), None
    else:
        ax_ov, ax_it = None, fig.add_subplot(111)
    if ax_ov is not None:
        _overlay(ax_ov, data, p)
    if ax_it is not None:
        _i_vs_T(ax_it, data, p)
    text, export = report(data)
    return Result(text=text, export=export,
                  hint="疊圖左右拖曳紅點、I vs T 上下拖曳都可修改 · 空心點拖曳可補點 · 右鍵：刪除 / 恢復自動")
