"""2D 分析功能（慢軸 × 快軸 → 量測值，例如 磁場 × 電流 → 電壓）

目前的功能（介面上依 group 分組，依 order 排序）：

  ▦ 色圖（磁場、電場都適用）
    color_map.py   色圖（量測值）+ 點擊切線          任何 2D 資料
                   色圖（微分 dZ/dF）+ 點擊切線      任何 2D 資料
  ⊗ 磁場 B（conditions=("field",)）
    switching.py   切換電流 Ic vs B（磁場）          快軸 I → 量 V
    cuts.py        指定位置切線 + 轉折點              快軸 I → 量 V
  ⚡ 電場（閘極 Vg，conditions=("gate",)）
    gate.py        Ic vs Vg（切換電流）、R vs Vg（電阻）、指定 Vg 的 IV 疊圖

資料一律透過 it.map2d() 取得 Map2D（slow / fast / z 三個 Axis），所以慢軸換成閘極電壓、
角度或溫度時，色圖類功能不用改就能用。

新增功能：複製 _template.py 改名（不要以 _ 開頭）。
共用工具在 _common.py（load_view、map_with_cut、add_map、find_B0、switching_current）。
"""
