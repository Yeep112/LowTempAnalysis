"""台階轉折點偵測（log10(dV/dI) 上的「往 |I| 變大方向往上跳」的台階）。

介面（analysis1d/step_transitions.py）、Ic 擬合（analysis1d/ic_vs_T.py）與批次分析
（batch/jobs/tseries.py 的 dvdi_vs_T）共用同一份計算。

轉折線設定：每條線一個 dict
    name        線名稱，例如 "outer +"
    side        "neg" / "pos"
    guess_uA    "edge" | 數字 | [[T, I], ...]（錨點，依溫度內插）
    search_uA   數字 | [[T, 寬度], ...]
    min_step    台階高度門檻（decade）
    window_uA   （可省略）台階偵測窗寬
    max_missing （可省略）連續幾個溫度找不到就停止追蹤

介面上用文字表示，一行一條線：
    名稱 | 猜測 | 搜尋寬 | 門檻 | 其他
    outer - | edge | 40 | 0.3
    inner - | 1.6:-500, 1.9:-460, 2.8:-80 | 60 | 0.05 | window=30 missing=2
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter

from .common import by_T

DEFAULT_LINES_TEXT = """\
outer - | edge | 40 | 0.3
inner - | 1.6:-500, 1.9:-460, 2.1:-380, 2.3:-280, 2.5:-190, 2.8:-80 | 60 | 0.05 | window=30 missing=2
center - | -150 | 60 | 0.15
center + | 1.6:225, 1.7:232, 1.8:240, 1.9:150, 2.0:125, 2.1:95, 2.2:70, 2.3:50, 2.4:40, 2.5:25, 2.6:20 | 1.6:50, 1.8:50, 1.9:30, 2.6:20 | 0.05 | missing=2
inner + | 1.6:530, 1.8:430, 2.0:320, 2.2:230, 2.3:265, 2.4:155, 2.5:125, 2.6:125, 2.8:120 | 1.6:60, 2.3:60, 2.4:30, 2.8:30 | 0.05 | window=30 missing=2
outer + | edge | 40 | 0.3"""

NONE_WORDS = {"none", "無", "x", "-", "刪除", "del", "nan"}


# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
def _pairs(text, what):
    """'1.6:-500, 1.9:-460' → [[1.6, -500], [1.9, -460]]；單一數字 → float"""
    text = str(text).strip().replace("，", ",").replace("：", ":")
    if ":" not in text:
        try:
            return float(text)
        except ValueError:
            raise ValueError(f"{what} 需要數字或「溫度:值, 溫度:值」，收到：{text!r}")
    rows = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            T, v = part.split(":")
            rows.append([float(T), float(v)])
        except ValueError:
            raise ValueError(f"{what} 的「{part}」格式錯誤，要寫成 溫度:值，例如 1.6:-500")
    if not rows:
        raise ValueError(f"{what} 是空的")
    return sorted(rows)


def side_of(name, guess=None):
    n = name.strip()
    if n.endswith("-") or n.endswith("−") or n.startswith("neg"):
        return "neg"
    if n.endswith("+") or n.startswith("pos"):
        return "pos"
    if isinstance(guess, (int, float)):
        return "neg" if guess < 0 else "pos"
    if isinstance(guess, list):
        return "neg" if guess[0][1] < 0 else "pos"
    raise ValueError(f"無法判斷「{name}」在哪一側：名稱請以 + 或 - 結尾，或加上 side=neg / side=pos")


def parse_lines_text(text):
    """介面文字 → 轉折線 dict 的 list。"""
    lines = []
    for n, raw in enumerate(str(text).splitlines(), 1):
        raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        f = [x.strip() for x in raw.replace("｜", "|").split("|")]
        if len(f) < 4:
            raise ValueError(f"轉折線第 {n} 行要有至少 4 欄：名稱 | 猜測 | 搜尋寬 | 門檻，收到：{raw}")
        name = f[0]
        if not name:
            raise ValueError(f"轉折線第 {n} 行沒有名稱")
        guess = "edge" if f[1].lower() in ("edge", "邊緣") else _pairs(f[1], f"「{name}」的猜測")
        line = dict(name=name, guess_uA=guess, search_uA=_pairs(f[2], f"「{name}」的搜尋寬"))
        try:
            line["min_step"] = float(f[3])
        except ValueError:
            raise ValueError(f"「{name}」的門檻需要數字，收到：{f[3]!r}")
        side = None
        for opt in " ".join(f[4:]).replace(",", " ").split():
            if "=" not in opt:
                raise ValueError(f"「{name}」的其他設定要寫成 key=值，例如 window=30，收到：{opt}")
            k, v = (s.strip().lower() for s in opt.split("=", 1))
            if k in ("window", "window_ua"):
                line["window_uA"] = float(v)
            elif k in ("missing", "max_missing"):
                line["max_missing"] = int(float(v))
            elif k == "side":
                if v not in ("neg", "pos"):
                    raise ValueError(f"「{name}」side 只能是 neg 或 pos")
                side = v
            else:
                raise ValueError(f"「{name}」不認得的設定：{k}（可用 window、missing、side）")
        line["side"] = side or side_of(name, guess)
        lines.append(line)
    names = [l["name"] for l in lines]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ValueError(f"轉折線名稱重複：{', '.join(sorted(dup))}")
    if not lines:
        raise ValueError("沒有設定任何轉折線")
    return lines


def normalize_lines(lines):
    """TOML / dict 寫法統一：{T: v} → [[T, v]]，補上 side。"""
    out = []
    for l in lines:
        l = dict(l)
        for k in ("guess_uA", "search_uA"):
            v = l.get(k)
            if isinstance(v, dict):
                l[k] = sorted([float(T), float(x)] for T, x in v.items())
        l.setdefault("search_uA", 40)
        l.setdefault("min_step", 0.1)
        if "side" not in l:
            l["side"] = side_of(l["name"], l.get("guess_uA"))
        out.append(l)
    return out


# ---------------------------------------------------------------------------
# 手動修正（逐筆文字：  outer + = 950; inner - = none ）
# ---------------------------------------------------------------------------
def parse_fix(text):
    """'outer + = 950; inner - = none' → {'outer +': 950.0, 'inner -': None}"""
    out = {}
    for part in str(text or "").replace("；", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        sep = "=" if "=" in part else (":" if ":" in part else None)
        if sep is None:
            raise ValueError(f"手動轉折點格式是「線名稱 = 位置(μA)」，例如 inner + = 430，收到：{part}")
        name, val = (s.strip() for s in part.rsplit(sep, 1))
        if val.lower() in NONE_WORDS:
            out[name] = None
        else:
            try:
                out[name] = float(val)
            except ValueError:
                raise ValueError(f"「{name}」的位置需要數字或 none，收到：{val!r}")
    return out


def format_fix(d):
    return "; ".join(f"{n} = {'none' if v is None else f'{v:.4g}'}" for n, v in d.items())


def set_fix(text, name, value):
    """在逐筆文字裡設定一條線：value = 數字、None（不畫點）或 'auto'（刪掉手動值）。"""
    d = parse_fix(text)
    if isinstance(value, str) and value == "auto":
        d.pop(name, None)
    else:
        d[name] = None if value is None else float(value)
    return format_fix(d)


# ---------------------------------------------------------------------------
# 偵測
# ---------------------------------------------------------------------------
def step_profile(I_uA, y_log, window_uA, median_uA):
    """回傳中值濾波後曲線，以及每一點左、右窗的平均值。"""
    dx = np.median(np.diff(I_uA))
    k = max(1, int(round(median_uA / dx)))
    if k % 2 == 0:
        k += 1
    y = median_filter(y_log, size=k, mode="nearest")
    w = max(2, int(round(window_uA / dx)))
    n = len(y)
    c = np.concatenate([[0.0], np.cumsum(y)])
    left = np.full(n, np.nan)
    right = np.full(n, np.nan)
    i = np.arange(w, n - w - 1)
    left[i] = (c[i] - c[i - w]) / w
    right[i] = (c[i + 1 + w] - c[i + 1]) / w
    return y, left, right


def find_step(I_uA, left, right, side, lo, hi, min_step):
    """在 [lo, hi] 內找往外側上升的最大台階。"""
    win = (I_uA >= lo) & (I_uA <= hi) & np.isfinite(left) & np.isfinite(right)
    score = left - right if side == "neg" else right - left
    cand = np.flatnonzero(win)
    if cand.size == 0:
        return None
    k = cand[np.argmax(score[cand])]
    if score[k] < min_step:
        return None
    return k, float(score[k])


def line_guess(line, p, last):
    g = line["guess_uA"]
    if isinstance(g, str) and g == "edge":
        return p["neg_edge"] if line["side"] == "neg" else p["pos_edge"]
    if isinstance(g, list):
        return by_T(g, p["T"], f"{line['name']} 的猜測")
    return float(g) if last is None else last


def detect(processed, lines, median_uA=9.0, step_window=15.0, max_missing=1, min_gap=20.0,
           manual=None):
    """溫度由低到高逐筆偵測。

    processed: [dict(run_id, T, label, I (μA), log, neg_edge, pos_edge), ...]
    manual:    {(線名稱, run_id): 位置 μA 或 None（不畫點）}
    回傳 (results, empty)
      results[name] = [dict(run_id, T, label, I_uA, y, step, manual), ...]
      empty[name]   = [dict(run_id, T, I_uA, y), ...]   沒找到的位置（介面畫成可拖曳的空心點）
    """
    manual = manual or {}
    lines = normalize_lines(lines)
    results = {l["name"]: [] for l in lines}
    empty = {l["name"]: [] for l in lines}
    state = {l["name"]: dict(last=None, missing=0, active=True) for l in lines}
    cache = {}

    def profile(p, w):
        key = (p["run_id"], w)
        if key not in cache:
            cache[key] = step_profile(p["I"], p["log"], w, median_uA)
        return cache[key]

    for p in sorted(processed, key=lambda q: q["T"]):
        I_uA = p["I"]
        for side in ("neg", "pos"):
            act = [l for l in lines if l["side"] == side
                   and (state[l["name"]]["active"] or (l["name"], p["run_id"]) in manual)]
            guesses = {l["name"]: line_guess(l, p, state[l["name"]]["last"]) for l in act}
            act.sort(key=lambda l: guesses[l["name"]] * (1 if side == "neg" else -1))
            bound = -np.inf if side == "neg" else np.inf
            for line in act:
                name = line["name"]
                st = state[name]
                y, left, right = profile(p, float(line.get("window_uA", step_window)))
                g = guesses[name]
                sw = by_T(line.get("search_uA", 40), p["T"], f"{name} 的搜尋寬")
                if side == "neg":
                    lo, hi = max(g - sw, bound + min_gap), min(g + sw, 0.0)
                else:
                    lo, hi = max(g - sw, 0.0), min(g + sw, bound - min_gap)
                key = (name, p["run_id"])
                is_manual = key in manual
                if is_manual:
                    if manual[key] is None:
                        k0 = int(np.argmin(np.abs(I_uA - g)))
                        empty[name].append(dict(run_id=p["run_id"], T=p["T"], I_uA=float(I_uA[k0]),
                                                y=float(y[k0]), removed=True))
                        continue
                    k = int(np.argmin(np.abs(I_uA - manual[key])))
                    step = np.nan
                else:
                    found = find_step(I_uA, left, right, side, lo, hi, float(line.get("min_step", 0.1)))
                    if found is None:
                        k0 = int(np.argmin(np.abs(I_uA - np.clip(g, min(lo, hi), max(lo, hi)))))
                        empty[name].append(dict(run_id=p["run_id"], T=p["T"], I_uA=float(I_uA[k0]),
                                                y=float(y[k0]), removed=False))
                        if results[name]:
                            st["missing"] += 1
                            if st["missing"] > int(line.get("max_missing", max_missing)):
                                st["active"] = False
                        continue
                    k, step = found
                st["missing"] = 0
                st["last"] = float(I_uA[k])
                bound = float(I_uA[k])
                y_mark = (0.5 * (left[k] + right[k])
                          if np.isfinite(left[k]) and np.isfinite(right[k]) else y[k])
                I_val = float(manual[key]) if is_manual else float(I_uA[k])   # 手動值照原樣保留
                if is_manual:
                    st["last"] = bound = I_val
                results[name].append(dict(run_id=p["run_id"], T=p["T"], label=p.get("label", ""),
                                          I_uA=I_val, y=float(y_mark), step=step,
                                          manual=is_manual))
        # 停止追蹤的線：在這個溫度也標出空位，方便手動補點
        for l in lines:
            name = l["name"]
            if state[name]["active"] or any(q["run_id"] == p["run_id"] for q in results[name] + empty[name]):
                continue
            g = line_guess(l, p, state[name]["last"])
            y, _, _ = profile(p, float(l.get("window_uA", step_window)))
            k0 = int(np.argmin(np.abs(I_uA - g)))
            empty[name].append(dict(run_id=p["run_id"], T=p["T"], I_uA=float(I_uA[k0]),
                                    y=float(y[k0]), removed=False))
    return results, empty


def table(results, lines, runs):
    """→ {run_id: {name: I_uA 或 nan}}"""
    names = [l["name"] for l in lines]
    out = {r: {n: np.nan for n in names} for r in runs}
    for n in names:
        for q in results.get(n, []):
            out[q["run_id"]][n] = q["I_uA"]
    return out
