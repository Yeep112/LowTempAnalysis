"""【範本】新增 2D 分析功能

步驟：
  1. 複製這個檔案，改成不以 _ 開頭的名字，例如 gate_map.py
  2. 改 @analysis(...) 的名稱、分組、圖示、支援模式、參數
  3. 改函式內容（畫在 fig 上，回傳 Result）
  4. 重新開介面 → 2D 功能清單就會出現

可用的資料（items[0]，2D 功能通常一次分析一筆：multi=False）：
  it.map2d()   → Map2D：m.slow（慢軸）、m.fast（快軸）、m.z（量測值），都是 Axis，
                 .values 為 (N_slow, N_fast) 的 SI 陣列，.unit / .sym / .scaled()
                 m.mode → 快軸 / 量測值的模式（'current_bias' = 快軸施加電流、量電壓）
  load_view(it, shift=False) → View：已縮放成顯示單位
                 v.S / v.F / v.Z、v.s_label / v.f_label / v.z_label、v.s_axis（慢軸 1D）

共用工具（from ._common import ...）：
  map_with_cut(fig, X, Y, Z, (xl, yl, zl), cmap, vlim)  色圖 + 點擊切線，回傳 on_click
  add_map(fig, ax, X, Y, Z, cmap, vlim, zlabel)         單純色圖（含滑鼠讀值）
  color_limits(Z, "自動 (1–99%)")                       色階範圍
  derivative_view(v)                                    沿快軸微分
  find_B0 / switching_current                           切換電流相關

如果之後有 3 維以上的資料（例如 磁場 × 閘極 × 電流），可以在這裡新增一個功能，
用 it.ds.data（原始 1D 陣列字典）自己整理，或先在 core/measurement.py 加一個 MapND。
"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import CMAPS, RANGES, load_view, map_with_cut, color_limits


# 把下一行的 # 拿掉就會啟用（檔名也要改成不以 _ 開頭）
# @analysis("2D", "範例：扣掉每列平均的色圖", group="色圖", icon="★", order=200, multi=False,
#           modes=None, params=[P("cmap", "色盤", "RdBu_r", CMAPS)],
#           help="每個慢軸位置扣掉該列的平均值，凸顯變化。")
def example_subtract_mean(fig, items, p):
    it = items[0]
    v = load_view(it)
    Z = v.Z - np.nanmean(v.Z, axis=1, keepdims=True)
    on_click = map_with_cut(fig, v.S, v.F, Z, (v.s_label, v.f_label, "ΔZ"), p["cmap"],
                            color_limits(Z, RANGES[0]))
    return Result(text=f"run {it.run_id}", on_click=on_click)
