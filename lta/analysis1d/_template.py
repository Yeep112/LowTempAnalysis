"""【範本】新增 1D 分析功能

步驟：
  1. 複製這個檔案，改成不以 _ 開頭的名字，例如 my_peak.py
  2. 改下面 @analysis(...) 的名稱、分組、圖示、支援模式、參數
  3. 改函式內容（畫在 fig 上，回傳 Result）
  4. 重新開介面 → 左邊功能清單就會出現

可用的資料（items 裡每一筆 it）：
  it.curve()   → Curve：c.x（施加的量）、c.y（量到的值），都是 Axis：
                 .values（SI 單位陣列）、.unit（'A'、'V'…）、.sym（'I'、'V'、'B⊥'…）
                 .scaled() → (自動加單位前綴的數值, 軸標籤 'I (μA)')
                 c.mode → 'current_bias'（I→V）/ 'voltage_bias'（V→I）/ 'generic'
  it.iv()      → (I [A], V [V])，不論施加電流或電壓
  it.T         → 溫度（K，從實驗描述解析；可能是 None）
  it.label / it.color → 介面依「圖例」「顏色」設定好的文字與顏色
  it.info      → run 資訊（run_id、exp_name、n_points…）

參數 P(key, 標籤, 預設值, choices=[...], tip="說明")：
  預設值是 float/int → 輸入框；bool → 勾選框；有 choices → 下拉選單；str → 文字框
  函式裡用 p["key"] 取值（已轉成正確型別）。

共用工具（from ._common import ...）：
  curves_of(items)              取所有 Curve 並確認量測方式一致
  axis_scale(axes, choice)      多條曲線共用的單位前綴與軸標籤
  compute_derivative(x, y, ...) 微分（相鄰點差分或 Savitzky-Golay）
  make_axes(fig, n, layout)     疊圖 / 子圖排版
  parse_floats("1, 2, 3")       文字 → 數字 list
  parse_T_table("1.6: a, b; 2.8: c, d", 2)   依溫度內插的參數
"""
import numpy as np

from ..core.registry import P, Result, analysis
from ._common import curves_of, axis_scale


# 把下一行的 # 拿掉就會啟用（檔名也要改成不以 _ 開頭）
# @analysis("1D", "範例：曲線最大值", group="進階", icon="★", order=200,
#           modes=None,                       # None = 所有量測模式；只支援 I→V 寫 ("current_bias",)
#           params=[P("smooth", "平滑點數", 1)],
#           help="找每條曲線的最大值並標出來。")
def example_max(fig, items, p):
    curves = curves_of(items)
    sx, xl = axis_scale([c.x for c in curves])
    sy, yl = axis_scale([c.y for c in curves])
    ax = fig.add_subplot(111)
    lines, rows = [], []
    for it, c in zip(items, curves):
        x, y = c.x.values, c.y.values
        if p["smooth"] > 1:
            y = np.convolve(y, np.ones(p["smooth"]) / p["smooth"], mode="same")
        k = int(np.nanargmax(y))
        ax.plot(x / sx, y / sy, color=it.color, label=it.label)
        ax.plot(x[k] / sx, y[k] / sy, "o", color=it.color, ms=8, mfc="none")
        lines.append(f"{it.label}: 最大值 {y[k] / sy:.4g} @ {x[k] / sx:.4g}")
        rows.append([it.run_id, x[k], y[k]])
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    ax.legend(fontsize=8)
    return Result(text="\n".join(lines),
                  export={"maxima": (["run_id", "x (SI)", "y (SI)"], rows)})
