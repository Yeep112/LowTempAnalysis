"""量測模式：把「施加什麼、量什麼」抽象化，讓分析不必寫死 appl_current / meas_voltage。

‧ 1D：Curve  = 一個掃描軸 (source) → 一個量測值 (measured)
‧ 2D：Map2D  = 慢軸 (slow，例如磁場、閘極) × 快軸 (fast，例如電流) → 量測值 (z)

新增量測方式時通常只要改這個檔案：
  1. 參數名稱 → 符號：在 SYMBOLS 加一行（例如 "appl_gate": "Vg"）
  2. 新的模式：在 MODES 加一筆，並在 detect_mode() 加判斷規則
分析功能用 modes=("current_bias",) 之類宣告自己支援哪些模式，介面會自動擋掉不適用的資料。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# 量測模式
# ---------------------------------------------------------------------------
MODES = {
    "current_bias": dict(short="I→V", label="施加電流、量電壓（current bias）"),
    "voltage_bias": dict(short="V→I", label="施加電壓、量電流（voltage bias）"),
    "generic":      dict(short="X→Y", label="其他（一般曲線）"),
}


def detect_mode(source_unit: str, measured_unit: str) -> str:
    """依掃描軸與量測值的單位判斷模式。新的模式在這裡加規則。"""
    if source_unit == "A" and measured_unit == "V":
        return "current_bias"
    if source_unit == "V" and measured_unit == "A":
        return "voltage_bias"
    return "generic"


# 參數名稱 → 圖上的符號（找不到時依單位猜：A→I、V→V、T→B、K→T）
SYMBOLS = {
    "appl_current": "I", "meas_current_K1": "I", "meas_current_K2": "I",
    "appl_voltage": "V", "meas_voltage_K1": "V", "meas_voltage_K2": "V",
    "x_field": "Bx", "y_field": "B⊥", "z_field": "Bz", "field": "B",
    "gate": "Vg", "appl_gate": "Vg", "gate_voltage": "Vg",
    "temperature": "T", "angle": "θ",
}
_UNIT_SYMBOL = {"A": "I", "V": "V", "T": "B", "K": "T", "deg": "θ", "Hz": "f", "Ohm": "R"}


# ---------------------------------------------------------------------------
# 外加條件：磁場 / 電場（閘極）/ 無外加 —— 介面用它把資料與功能分成三大類
# ---------------------------------------------------------------------------
CONDITIONS = {
    "field": dict(icon="⊗", short="磁場", label="磁場 B"),
    "gate":  dict(icon="⚡", short="電場", label="電場（閘極電壓 Vg）"),
    "none":  dict(icon="○", short="無外加", label="無外加（只有溫度等）"),
}

# 閘極電壓的來源（電場從樣品哪一側來）。介面「⚡ 閘極設定」可以逐個參數修改。
GATE_ROLES_INFO = {
    "bottom": dict(sym="V$_\\mathrm{bottom}$", plain="V_bottom", label="底部閘極 V_bottom（電場來自樣品底部）"),
    "top":    dict(sym="V$_\\mathrm{top}$", plain="V_top", label="頂部閘極 V_top（電場來自樣品上方）"),
    "gate":   dict(sym="V$_\\mathrm{g}$", plain="Vg", label="一般閘極 Vg（不區分上下）"),
}
DEFAULT_GATE_ROLE = "bottom"
GATE_ROLES: dict = {}            # 參數名稱 → "bottom" / "top" / "gate"；沒列出的用 DEFAULT_GATE_ROLE

_GATE_RE = re.compile(r"(^|_)(v?tg|v?bg|vg|gate\w*|v_?gate\w*|top_?gate|bottom_?gate|back_?gate)($|_)", re.I)


def is_field(name: str, unit: str) -> bool:
    return name.endswith("_field") or name == "field" or unit == "T"


def is_gate(name: str, unit: str = "V") -> bool:
    """閘極電壓參數：名稱像 appl_TG、appl_BG、gate、Vg…，單位是 V。"""
    return unit in ("V", "") and bool(_GATE_RE.search(name or ""))


def gate_role(name: str) -> str:
    role = GATE_ROLES.get(name, DEFAULT_GATE_ROLE)
    return role if role in GATE_ROLES_INFO else DEFAULT_GATE_ROLE


def axis_condition(name: str, unit: str):
    if is_field(name, unit):
        return "field"
    if is_gate(name, unit):
        return "gate"
    return None


def run_condition(setpoints, units) -> str:
    """一筆量測的外加條件：有磁場掃描軸 → field；有閘極掃描軸 → gate；都沒有 → none。"""
    conds = {axis_condition(n, (units or {}).get(n, "")) for n in setpoints}
    if "field" in conds:
        return "field"
    if "gate" in conds:
        return "gate"
    return "none"


def plain(text: str) -> str:
    """把圖上用的符號（例如 V$_\\mathrm{bottom}$）換成一般文字 V_bottom，給清單、狀態列用。"""
    if not text or "$" not in text:
        return text
    t = re.sub(r"\$_\\mathrm\{([^}]*)\}\$", r"_\1", text)
    t = re.sub(r"\$_\{?([^}$]*)\}?\$", r"_\1", t)
    return t.replace("$", "")


def symbol(name: str, unit: str) -> str:
    if is_gate(name, unit) and not is_field(name, unit):
        return GATE_ROLES_INFO[gate_role(name)]["sym"]
    if name in SYMBOLS:
        return SYMBOLS[name]
    for key, sym in SYMBOLS.items():
        if name.endswith(key):
            return sym
    return _UNIT_SYMBOL.get(unit, name)


# ---------------------------------------------------------------------------
# 單位前綴：自動挑 n / μ / m / （無）/ k，讓數字落在好讀的範圍
# ---------------------------------------------------------------------------
_PREFIXES = [(1e-9, "n"), (1e-6, "μ"), (1e-3, "m"), (1.0, ""), (1e3, "k")]
PREFIX_CHOICES = ["自動", "n", "μ", "m", "無"]


def si_scale(values, unit: str, choice: str = "自動"):
    """回傳 (除數, 顯示單位)。例：1.2e-3 A → (1e-6, 'μA')"""
    if unit in ("", None):
        return 1.0, ""
    if choice and choice != "自動":
        p = {"n": 1e-9, "μ": 1e-6, "u": 1e-6, "m": 1e-3, "無": 1.0, "": 1.0}[choice]
        return p, ("" if p == 1.0 else choice.replace("u", "μ")) + unit
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    mx = float(np.max(np.abs(v))) if v.size else 0.0
    if mx == 0:
        return 1.0, unit
    for p, s in _PREFIXES:
        if mx / p < 2000:
            return p, s + unit
    return 1.0, unit


def derivative_unit(y_unit: str, x_unit: str) -> str:
    if (y_unit, x_unit) == ("V", "A"):
        return "Ω"
    if (y_unit, x_unit) == ("A", "V"):
        return "S"
    return f"{y_unit}/{x_unit}" if x_unit else y_unit


# ---------------------------------------------------------------------------
# 資料物件
# ---------------------------------------------------------------------------
@dataclass
class Axis:
    name: str
    unit: str
    values: np.ndarray            # SI 單位

    @property
    def sym(self):
        return symbol(self.name, self.unit)

    def scaled(self, choice="自動"):
        """回傳 (縮放後的數值, 軸標籤)，例如 (array, 'I (μA)')"""
        p, u = si_scale(self.values, self.unit, choice)
        return self.values / p, f"{self.sym} ({u})" if u else self.sym


@dataclass
class Curve:
    """1D：source → measured。"""
    x: Axis
    y: Axis
    mode: str

    @property
    def mode_short(self):
        return MODES.get(self.mode, MODES["generic"])["short"]


@dataclass
class Map2D:
    """2D：slow × fast → z，三者都是 (N_slow, N_fast) 陣列。"""
    slow: Axis
    fast: Axis
    z: Axis
    mode: str                     # 由 fast / z 判斷（例如快軸電流、量電壓 = current_bias）

    @property
    def slow_1d(self):
        return self.slow.values[:, 0]


def reshape_grid(slow, fast, z):
    """依「慢軸第一次改變的位置」把 1D 資料排成 (N_slow, N_fast)。未完成的量測會截掉最後不完整的一列。"""
    total = min(len(slow), len(fast), len(z))
    slow, fast, z = slow[:total], fast[:total], z[:total]
    changes = np.flatnonzero(slow != slow[0])
    nf = int(changes[0]) if len(changes) else total
    if nf <= 0:
        raise ValueError("無法判斷快軸的點數")
    ns = total // nf
    used = ns * nf
    return slow[:used].reshape(ns, nf), fast[:used].reshape(ns, nf), z[:used].reshape(ns, nf)


def order_setpoints(data: dict, setpoints: list) -> list:
    """把掃描軸排成 [最慢, ..., 最快]（依數值改變的次數）。"""
    def n_changes(name):
        v = data[name]
        return int(np.count_nonzero(np.diff(v) != 0)) if len(v) > 1 else 0
    return sorted(setpoints, key=n_changes)
