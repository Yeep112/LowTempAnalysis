"""色圖：量測值與微分的 2D 色圖，左鍵點色圖在右邊加切線。適用任何 2D 資料（磁場、閘極電壓、溫度…）。

B₀ 平移相關的參數只對磁場資料顯示（P(..., conditions=("field",))）。"""
import numpy as np

from ..core.measurement import plain
from ..core.registry import P, Result, analysis
from ._common import CMAPS, RANGES, CUT_HINT, load_view, derivative_view, color_limits, map_with_cut

COMMON = [
    P("shift", "以切換電流最大處為慢軸原點（B₀）", True, conditions=("field",)),
    P("b0_thr", "B₀ 判斷門檻 (mV)", 0.5, conditions=("field",)),
    P("range", "色階範圍", RANGES[0], RANGES),
    P("vmin", "手動下限", 0.0),
    P("vmax", "手動上限", 1.0),
    P("cut", "顯示切線面板", True),
    P("origin_line", "平移後在 0 畫虛線（B₀ 位置）", True, conditions=("field",)),
    P("cut_marks", "色圖上標出切線位置", True),
    P("first_cut", "一開始先畫一條切線（磁場在 B₀，其他在中間）", True),
    P("fast_sym", "Y 軸以 0 為中心對稱", False, tip="電流掃描 ±範圍不對稱時，讓色圖上下對稱"),
]


GROUP = "▦ 色圖（磁場、電場都適用）"


def _sym_ylim(fig, p):
    if p.get("fast_sym") and fig.axes:
        ax = fig.axes[0]
        lo, hi = ax.get_ylim()
        m = max(abs(lo), abs(hi))
        ax.set_ylim(-m, m)


@analysis("2D", "色圖（量測值）+ 點擊切線", group=GROUP, icon="▦", order=10, multi=False,
          params=[P("cmap", "色盤", "magma", CMAPS), *COMMON],
          help="左鍵點色圖 → 右邊加一條該位置的切線；右鍵清除。")
def value_map(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"], p["b0_thr"])
    on_click = map_with_cut(fig, v.S, v.F, v.Z, (v.s_label, v.f_label, v.z_label), p["cmap"],
                            color_limits(v.Z, p["range"], p["vmin"], p["vmax"]), p["cut"],
                            title=f"run {it.run_id}", origin_line=bool(v.origin),
                            cut_title=f"{v.m.z.sym}–{v.m.fast.sym}", show_origin=p["origin_line"],
                            cut_marks=p["cut_marks"], first_cut=p["first_cut"])
    _sym_ylim(fig, p)
    text = (f"run {it.run_id}  {it.info.mode_short}：{v.S.shape[0]} × {v.S.shape[1]} 點，"
            f"{plain(v.s_label)} {v.s_axis.min():.4g} ~ {v.s_axis.max():.4g}\n{v.note}")
    return Result(text=text, on_click=on_click, hint=CUT_HINT if on_click else "")


@analysis("2D", "色圖（微分 dZ/dF）+ 點擊切線", group=GROUP, icon="∂", order=20, multi=False,
          params=[P("cmap", "色盤", "hot", CMAPS), *COMMON,
                  P("log", "色階用 log10", False),
                  P("smooth", "微分前平滑（點數，0 = 不平滑）", 0,
                    tip="雜訊大時填 5、7、9…（Savitzky–Golay，沿電流方向）"),
                  P("edge_skip", "去掉每條掃描頭尾的點數", 0,
                    tip="掃描起點、終點常有很大的假微分（例如 ±150 nA 處的尖峰），填 2–3 可去掉")],
          help="沿快軸微分（例如 dV/dI）。左鍵點色圖加切線。")
def derivative_map(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"], p["b0_thr"])
    S, F, D, dlabel, _ = derivative_view(v, p.get("smooth", 0), p.get("edge_skip", 0))
    if p["log"]:
        with np.errstate(divide="ignore", invalid="ignore"):
            D = np.log10(np.where(D > 0, D, np.nan))
        dlabel = f"log10({dlabel})"
    on_click = map_with_cut(fig, S, F, D, (v.s_label, v.f_label, dlabel), p["cmap"],
                            color_limits(D, p["range"], p["vmin"], p["vmax"]), p["cut"],
                            title=f"run {it.run_id}", origin_line=bool(v.origin), cut_title=dlabel.split(" ")[0],
                            show_origin=p["origin_line"], cut_marks=p["cut_marks"], first_cut=p["first_cut"])
    _sym_ylim(fig, p)
    return Result(text=f"run {it.run_id}  {it.info.mode_short}\n{v.note}", on_click=on_click,
                  hint=CUT_HINT if on_click else "")
