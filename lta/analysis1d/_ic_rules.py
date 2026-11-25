"""轉折位置 → Ic 的後處理規則（Ic_T_analysis.ipynb Cell 3）。

「IV 轉折點（二次微分）」與「Ic vs T 擬合」共用同一組參數：在任一頁改，另一頁跟著變。
  1. 找不到的轉折位置視為 0
  2. 指定曲線從某溫度起強制為 0（例：inner: 2.6 → 2.6 K 以上 neg/pos inner = 0）
  3. 沒有轉折的溫度整列不使用（例：3.8）
"""
from ..core.registry import P
from ..core import icfit

MISSING_ZERO = P("missing_zero", "找不到的轉折位置視為 0", True)
ZERO_FROM = P("zero_from", "強制 Ic = 0（曲線: 從幾 K 起）", "",
              tip="例：inner: 2.6　→ 2.6 K 以上 neg inner、pos inner 都當作 0（notebook 的 INNER_ZERO_ABOVE_K）")
EXCLUDE_T = P("exclude_T", "沒有轉折的溫度（不畫、不算）", "",
              tip="例：3.8　或 3.8, 4.2（notebook 的 NO_TRANSITION_TEMPERATURES）")
RULES = [MISSING_ZERO, ZERO_FROM, EXCLUDE_T]


def _split(text):
    return [s.strip() for s in str(text or "").replace("；", ";").replace("\n", ";").split(";") if s.strip()]


def parse_floats(text, name):
    try:
        return [float(x) for x in str(text or "").replace("，", ",").replace(" ", ",").split(",") if x.strip()]
    except ValueError:
        raise ValueError(f"「{name}」需要用逗號分開的數字")


def parse_zero_from(text):
    out = {}
    for part in _split(text):
        part = part.replace("：", ":").replace(">=", ":").replace("≥", ":")
        if ":" not in part:
            raise ValueError(f"強制 Ic = 0 的格式是「曲線: 溫度」，例如 inner: 2.6，收到：{part}")
        n, T = (s.strip() for s in part.split(":", 1))
        try:
            out[n] = float(T)
        except ValueError:
            raise ValueError(f"強制 Ic = 0：「{T}」不是溫度")
    return out


def apply(Ts, positions, curve_defs, p, extra_zero_T=()):
    """套用規則 → (Ts, positions, Ic)。positions 單位 mA。"""
    return icfit.ic_table(Ts, positions, curve_defs, bool(p.get("missing_zero", True)),
                          parse_zero_from(p.get("zero_from", "")),
                          parse_floats(p.get("exclude_T", ""), "沒有轉折的溫度"), extra_zero_T)


def duplicate_note(items):
    """同一個溫度選了好幾筆時提醒（例如 2.8 K 的 run 103 與 104）。"""
    by_T = {}
    for it in items:
        if it.T is not None:
            by_T.setdefault(round(it.T, 6), []).append(it.run_id)
    dup = {T: r for T, r in by_T.items() if len(r) > 1}
    if not dup:
        return ""
    return "⚠ 同一溫度選了多筆：" + "；".join(f"{T:g} K → run {', '.join(map(str, r))}" for T, r in sorted(dup.items())) + \
        "（圖上會有重複的點；只想留一筆就在清單取消其他筆）"
