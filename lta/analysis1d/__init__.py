"""1D 分析功能（一個掃描軸 → 一個量測值）

目前的功能（介面上依 group 分組，依 order 排序）：

  基本
    curve.py                 曲線（IV / VI 原始資料）           所有量測模式
    derivative.py            微分 dV/dI、dI/dV                  所有量測模式
    curve_and_derivative.py  曲線 + 微分 上下對照               所有量測模式
  進階
    resistance.py            電阻 R（直線擬合）vs T             I→V、V→I
    linearized.py            線性化 dV/dI（兩側直線取代雜訊）     I→V
    transitions.py           IV 轉折點 + Ic vs T（二次微分）     I→V

新增功能：複製 _template.py 改名（不要以 _ 開頭），改好函式，重新開介面就會出現。
共用工具在 _common.py（排版、微分、參數解析）。
"""
