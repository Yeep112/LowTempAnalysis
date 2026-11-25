"""色圖：量測值與微分的 2D 色圖，左鍵點色圖在右邊加切線。適用任何 2D 資料（磁場、閘極…）。"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import CMAPS, RANGES, load_view, derivative_view, color_limits, map_with_cut

COMMON = [
    P("shift", "以切換電流最大處為慢軸原點（B₀）", True),
    P("b0_thr", "B₀ 判斷門檻 (mV)", 0.5),
    P("range", "色階範圍", RANGES[0], RANGES),
    P("vmin", "手動下限", 0.0),
    P("vmax", "手動上限", 1.0),
    P("cut", "顯示切線面板", True),
]


@analysis("2D", "色圖（量測值）+ 點擊切線", group="色圖", icon="▦", order=10, multi=False,
          params=[P("cmap", "色盤", "magma", CMAPS), *COMMON],
          help="左鍵點色圖 → 右邊加一條該位置的切線；右鍵清除。")
def value_map(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"], p["b0_thr"])
    on_click = map_with_cut(fig, v.S, v.F, v.Z, (v.s_label, v.f_label, v.z_label), p["cmap"],
                            color_limits(v.Z, p["range"], p["vmin"], p["vmax"]), p["cut"],
                            title=f"run {it.run_id}", origin_line=bool(v.origin),
                            cut_title=f"{v.m.z.sym}–{v.m.fast.sym}")
    text = (f"run {it.run_id}  {it.info.mode_short}：{v.S.shape[0]} × {v.S.shape[1]} 點，"
            f"{v.s_label} {v.s_axis.min():.4g} ~ {v.s_axis.max():.4g}\n{v.note}")
    return Result(text=text, on_click=on_click)


@analysis("2D", "色圖（微分 dZ/dF）+ 點擊切線", group="色圖", icon="∂", order=20, multi=False,
          params=[P("cmap", "色盤", "hot", CMAPS), *COMMON,
                  P("log", "色階用 log10", False)],
          help="沿快軸微分（例如 dV/dI）。左鍵點色圖加切線。")
def derivative_map(fig, items, p):
    it = items[0]
    v = load_view(it, p["shift"], p["b0_thr"])
    S, F, D, dlabel, _ = derivative_view(v)
    if p["log"]:
        with np.errstate(divide="ignore", invalid="ignore"):
            D = np.log10(np.where(D > 0, D, np.nan))
        dlabel = f"log10({dlabel})"
    on_click = map_with_cut(fig, S, F, D, (v.s_label, v.f_label, dlabel), p["cmap"],
                            color_limits(D, p["range"], p["vmin"], p["vmax"]), p["cut"],
                            title=f"run {it.run_id}", origin_line=bool(v.origin), cut_title=dlabel.split(" ")[0])
    return Result(text=f"run {it.run_id}  {it.info.mode_short}\n{v.note}", on_click=on_click)
