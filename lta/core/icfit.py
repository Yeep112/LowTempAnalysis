"""Ic(T) 擬合（Ic_T_analysis.ipynb / NbSe2_WSe2_NbSe2_Ic_Fitting_Summary 的流程）。

    Stage 1  Generalized power law，自由 Tc, p, q
    Stage 2  固定 bulk Tc（預設 7.2 K），p = 1 或 2
    Stage 3  所有曲線共用 Tc 的 global power-law fit（p = 1 或 2）
    Stage 4  JJ 物理模型：AB / KO / GL / KL（接面、薄膜）與 diffusive / clean SNS（鄰近效應）

介面（analysis1d/ic_vs_T.py）、批次分析與 Jupyter 共用。單位：T (K)、Ic (mA)。
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import curve_fit


# ===========================================================================
# 物理模型（T >= Tc 時回傳 0）
# ===========================================================================
def _below(T, Tc):
    T = np.atleast_1d(np.asarray(T, dtype=float))
    return T, np.zeros_like(T), T < Tc


def gl_depairing(T, Ic0, Tc):
    """(1) Ginzburg–Landau depairing：Ic0 [1-(T/Tc)^2]^(3/2)"""
    T, out, m = _below(T, Tc)
    out[m] = Ic0 * (1 - (T[m] / Tc) ** 2) ** 1.5
    return out


def kl_dirty(T, Ic0, Tc, alpha):
    """(2) Kupriyanov–Lukichev dirty limit：GL × [1+α(T/Tc)^2]^(-1/2)"""
    T, out, m = _below(T, Tc)
    t2 = (T[m] / Tc) ** 2
    out[m] = Ic0 * (1 - t2) ** 1.5 / np.sqrt(1 + alpha * t2)
    return out


def power_law(T, Ic0, Tc, p, q):
    """(3) Generalized power law：Ic0 [1-(T/Tc)^p]^q"""
    T, out, m = _below(T, Tc)
    out[m] = Ic0 * (1 - (T[m] / Tc) ** p) ** q
    return out


def ab_tunneling(T, Ic0, Tc):
    """(4) Ambegaokar–Baratoff：Ic0 [Δ(T)/Δ0] tanh(Δ(T)/2kT)，Δ0 = 1.764 kTc"""
    T, out, m = _below(T, Tc)
    gap = np.tanh(1.74 * np.sqrt(Tc / T[m] - 1.0))
    out[m] = Ic0 * gap * np.tanh(1.764 * Tc / (2.0 * T[m]) * gap)
    return out


def ko_short(T, Ic0, Tc):
    """(5) Kulik–Omelyanchuk（近 Tc）：Ic0 [1-(T/Tc)^2]"""
    T, out, m = _below(T, Tc)
    out[m] = Ic0 * (1 - (T[m] / Tc) ** 2)
    return out


def sns_diffusive(T, A, Tc, T0):
    """(6) Diffusive SNS：A (1-T/Tc) √T exp(-√(T/T0))"""
    T, out, m = _below(T, Tc)
    out[m] = A * (1 - T[m] / Tc) * np.sqrt(T[m]) * np.exp(-np.sqrt(T[m] / T0))
    return out


def sns_clean(T, A, Tc, T0):
    """(7) Clean (ballistic) SNS：A (1-T/Tc) T exp(-T/T0)"""
    T, out, m = _below(T, Tc)
    out[m] = A * (1 - T[m] / Tc) * T[m] * np.exp(-T[m] / T0)
    return out


FILM_MODELS = ["AB (Tc bracketed)", "AB (free Tc)", "KO (Tc bracketed)",
               "GL depairing (Tc bracketed)", "KL dirty limit (Tc bracketed)"]
SNS_MODELS = ["Diffusive SNS (free Tc)", "Diffusive SNS (shared Tc)",
              "Diffusive SNS (bulk Tc)", "Clean SNS (free Tc)"]


# ===========================================================================
# 資料
# ===========================================================================
def prepare_curve(T, Ic):
    """只用 Ic > 0 的點擬合；Ic = 0 的點只用來決定 Tc 上限。
    Tc 範圍：[最後一個 Ic>0 的溫度, 第一個 Ic=0 的溫度]。沒有非零點回傳 None。"""
    T, Ic = np.asarray(T, float), np.asarray(Ic, float)
    ok = np.isfinite(Ic) & np.isfinite(T)
    T, Ic = T[ok], Ic[ok]
    order = np.argsort(T, kind="stable")
    T, Ic = T[order], Ic[order]
    nonzero = Ic > 0
    if not nonzero.any():
        return None
    Tc_lo = T[nonzero].max()
    zeros_above = T[(~nonzero) & (T > Tc_lo)]
    Tc_hi = zeros_above.min() if zeros_above.size else np.inf
    return dict(T=T, Ic=Ic, T_fit=T[nonzero], y_fit=Ic[nonzero], Tc_lo=float(Tc_lo), Tc_hi=float(Tc_hi))


def ic_table(Ts, positions, curve_defs, missing_zero=True, zero_from=None, exclude_T=(), extra_zero_T=()):
    """轉折位置 → Ic（notebook Cell 3 的規則）。

    Ts          溫度 (K) array
    positions   {線名稱: 位置 array（mA，NaN = 沒找到）}
    curve_defs  [(曲線名稱, 正側線, 負側線), ...]   Ic = 正側 − 負側
    missing_zero 找不到的位置視為 0
    zero_from   {曲線名稱: T}：T 以上這條曲線的兩個位置強制為 0
    exclude_T   這些溫度整列不使用（NaN）
    extra_zero_T 額外加入的溫度，所有位置 = 0（已知 Ic = 0，用來限制 Tc 上限）
    回傳 (Ts, positions, Ic)；Ic = {曲線名稱: array}
    """
    Ts = np.asarray(Ts, float)
    pos = {n: np.array(v, float) for n, v in positions.items()}
    extra = [t for t in extra_zero_T if not np.any(np.isclose(Ts, t))]
    if extra:
        Ts = np.r_[Ts, extra]
        pos = {n: np.r_[v, np.zeros(len(extra))] for n, v in pos.items()}
    order = np.argsort(Ts, kind="stable")
    Ts = Ts[order]
    pos = {n: v[order] for n, v in pos.items()}
    for name, plus, minus in curve_defs:
        for n in (plus, minus):
            if n not in pos:
                raise ValueError(f"Ic「{name}」用到的線「{n}」不存在（現有：{', '.join(pos)}）")
    if missing_zero:
        for v in pos.values():
            v[~np.isfinite(v)] = 0.0
    for name, T0 in (zero_from or {}).items():
        d = next((c for c in curve_defs if c[0] == name), None)
        if d is None:
            raise ValueError(f"強制 Ic = 0 的曲線「{name}」不在 Ic 定義裡")
        m = Ts >= T0 - 1e-9
        pos[d[1]][m] = 0.0
        pos[d[2]][m] = 0.0
    ex = np.zeros(len(Ts), bool)
    for t in exclude_T:
        ex |= np.isclose(Ts, t)
    for v in pos.values():
        v[ex] = np.nan
    Ic = {}
    for name, plus, minus in curve_defs:
        a, b = pos[plus], pos[minus]
        out = np.full(len(Ts), np.nan)
        ok = np.isfinite(a) & np.isfinite(b)
        out[ok] = a[ok] - b[ok]
        Ic[name] = out
    return Ts, pos, Ic


# ===========================================================================
# 擬合
# ===========================================================================
def r_squared(y, f):
    ss = np.sum((y - np.mean(y)) ** 2)
    return float(1 - np.sum((y - f) ** 2) / ss) if ss > 0 else np.nan


class IcFitter:
    def __init__(self, curves, tc_bracket=True, T_bulk=7.2, t0_max=100.0):
        self.curves = {k: v for k, v in curves.items() if v is not None}
        self.tc_bracket = tc_bracket
        self.T_bulk = float(T_bulk)
        self.t0_max = float(t0_max)
        self.results = {}           # (stage, curve, label) -> res
        self.notes = []

    # ---- 工具 ----
    def tc_bounds(self, name, free_hi=20.0):
        c = self.curves[name]
        if self.tc_bracket and np.isfinite(c["Tc_hi"]):
            return c["Tc_lo"], c["Tc_hi"]
        return c["Tc_lo"], free_hi

    def fit(self, stage, name, label, func, free, fixed=None):
        """free: {參數: (初始值, 下限, 上限)}；fixed: {參數: 值}"""
        c = self.curves[name]
        fixed = dict(fixed or {})
        names = list(free)
        p0 = [free[n][0] for n in names]
        lo = [free[n][1] for n in names]
        hi = [free[n][2] for n in names]
        p0 = [min(max(v, l + 1e-9 * max(1, abs(l))), h - 1e-9 * max(1, abs(h))) for v, l, h in zip(p0, lo, hi)]

        def f(T, *vals):
            return func(T, **dict(zip(names, vals)), **fixed)

        try:
            popt, pcov = curve_fit(f, c["T_fit"], c["y_fit"], p0=p0, bounds=(lo, hi), maxfev=100000)
        except (RuntimeError, ValueError) as err:
            self.notes.append(f"[{stage}] Ic {name} {label}：擬合失敗（{err}）")
            return None
        perr = np.sqrt(np.clip(np.diag(pcov), 0, None))
        at_bound = []
        for i, n in enumerate(names):
            near_lo = abs(popt[i] - lo[i]) <= 1e-4 * max(abs(lo[i]), 1e-2)
            near_hi = abs(hi[i] - popt[i]) <= 1e-4 * max(abs(hi[i]), 1e-2)
            if near_lo or near_hi:
                at_bound.append(n)
                perr[i] = np.nan
        fitted = f(c["T_fit"], *popt)
        res = dict(stage=stage, curve=name, label=label, func=func,
                   params={**dict(zip(names, popt)), **fixed},
                   errors={**dict(zip(names, perr)), **{n: 0.0 for n in fixed}},
                   fixed=set(fixed), R2=r_squared(c["y_fit"], fitted),
                   RMSE=float(np.sqrt(np.mean((c["y_fit"] - fitted) ** 2))), at_bound=at_bound)
        self.results[(stage, name, label)] = res
        return res

    def _tc0(self, lo, hi):
        return 0.5 * (lo + min(hi, lo + 1))

    # ---- 各階段 ----
    def stage1(self):
        for name, c in self.curves.items():
            lo, hi = self.tc_bounds(name)
            self.fit("Stage 1", name, "Power law (free Tc, p, q)", power_law,
                     free=dict(Ic0=(c["y_fit"].max(), 0, 50), Tc=(self._tc0(lo, hi), lo, hi),
                               p=(2.0, 0.1, 20), q=(1.0, 0.01, 20)))

    def stage2(self):
        for name, c in self.curves.items():
            if c["Tc_lo"] >= self.T_bulk:
                self.notes.append(f"[Stage 2] Ic {name}：最後一個非零點 {c['Tc_lo']:g} K ≥ bulk Tc，略過")
                continue
            for p in (1, 2):
                self.fit("Stage 2", name, f"Power law (bulk Tc, p={p})", power_law,
                         free=dict(Ic0=(c["y_fit"].max() * 2, 0, 1e3), q=(2.0, 0.01, 100)),
                         fixed=dict(Tc=self.T_bulk, p=p))

    def stage3(self, p_fixed):
        names = list(self.curves)
        if len(names) < 2:
            self.notes.append("[Stage 3] 只有一條曲線，略過共用 Tc 擬合")
            return []
        cs = [self.curves[n] for n in names]
        xs = [c["T_fit"] for c in cs]
        ys = [c["y_fit"] for c in cs]

        def f(_, Tc, *vals):
            return np.concatenate([power_law(x, vals[2 * k], Tc, p_fixed, vals[2 * k + 1])
                                   for k, x in enumerate(xs)])

        top = max(cs, key=lambda c: c["Tc_lo"])
        tc_lo = top["Tc_lo"]
        tc_hi = top["Tc_hi"] if (self.tc_bracket and np.isfinite(top["Tc_hi"])) else 20.0
        p0 = [0.5 * (tc_lo + tc_hi)]
        lo, hi = [tc_lo], [tc_hi]
        for k, y in enumerate(ys):
            p0 += [y.max(), 0.5 if k == 0 else 1.0]
            lo += [0, 0.01]
            hi += [50, 20]
        label = f"Shared Tc power law (p={p_fixed})"
        try:
            popt, pcov = curve_fit(f, None, np.concatenate(ys), p0=p0, bounds=(lo, hi), maxfev=100000)
        except (RuntimeError, ValueError) as err:
            self.notes.append(f"[Stage 3] {label}：擬合失敗（{err}）")
            return []
        perr = np.sqrt(np.clip(np.diag(pcov), 0, None))
        Tc = popt[0]
        tc_at_bound = min(Tc - tc_lo, tc_hi - Tc) <= 1e-4 * tc_hi
        out = []
        for k, (name, x, y) in enumerate(zip(names, xs, ys)):
            I0, q = popt[1 + 2 * k], popt[2 + 2 * k]
            fitted = power_law(x, I0, Tc, p_fixed, q)
            res = dict(stage="Stage 3", curve=name, label=label, func=power_law,
                       params=dict(Ic0=I0, Tc=Tc, p=p_fixed, q=q),
                       errors=dict(Ic0=perr[1 + 2 * k], Tc=np.nan if tc_at_bound else perr[0], p=0.0,
                                   q=perr[2 + 2 * k]),
                       fixed={"p"}, R2=r_squared(y, fitted), RMSE=float(np.sqrt(np.mean((y - fitted) ** 2))),
                       at_bound=["Tc"] if tc_at_bound else [])
            self.results[("Stage 3", name, label)] = res
            out.append(res)
        return out

    def shared_tc(self, spec):
        """'Stage 3 (p=2)'、'Stage 3 (p=1)' 或數字。"""
        s = str(spec).strip()
        try:
            return float(s), f"{float(s):g} K（手動）"
        except ValueError:
            pass
        p = "1" if "p=1" in s.replace(" ", "") else "2"
        for (stage, _, label), res in self.results.items():
            if stage == "Stage 3" and label.endswith(f"(p={p})"):
                return res["params"]["Tc"], f"Stage 3 (p={p})"
        return None, None

    def stage4(self, sns_curves=("inner",), shared_spec="Stage 3 (p=2)"):
        shared, src = self.shared_tc(shared_spec)
        if shared is None:
            self.notes.append(f"[Stage 4] 找不到共用 Tc（{shared_spec}），略過「shared Tc」擬合")
        self.shared = (shared, src)
        tmax = self.t0_max
        for name, c in self.curves.items():
            lo, hi = self.tc_bounds(name)
            tc0 = self._tc0(lo, hi)
            I0 = c["y_fit"].max()
            if name in sns_curves:
                self.fit("Stage 4", name, "Diffusive SNS (free Tc)", sns_diffusive,
                         free=dict(A=(20, 0, 1e6), Tc=(tc0, lo, hi), T0=(0.3, 1e-3, tmax)))
                if shared is not None:
                    if shared <= c["Tc_lo"]:
                        self.notes.append(f"[Stage 4] Ic {name}：共用 Tc {shared:.3g} K ≤ 最後一個非零點，略過")
                    else:
                        self.fit("Stage 4", name, "Diffusive SNS (shared Tc)", sns_diffusive,
                                 free=dict(A=(50, 0, 1e7), T0=(0.3, 1e-3, tmax)), fixed=dict(Tc=shared))
                if self.T_bulk > c["Tc_lo"]:
                    self.fit("Stage 4", name, "Diffusive SNS (bulk Tc)", sns_diffusive,
                             free=dict(A=(100, 0, 1e8), T0=(0.1, 1e-4, tmax)), fixed=dict(Tc=self.T_bulk))
                self.fit("Stage 4", name, "Clean SNS (free Tc)", sns_clean,
                         free=dict(A=(5, 0, 1e6), Tc=(tc0, lo, hi), T0=(1.0, 1e-3, tmax)))
            else:
                self.fit("Stage 4", name, "AB (Tc bracketed)", ab_tunneling,
                         free=dict(Ic0=(I0, 0, 50), Tc=(tc0, lo, hi)))
                ab = self.fit("Stage 4", name, "AB (free Tc)", ab_tunneling,
                              free=dict(Ic0=(I0, 0, 50), Tc=(lo + 0.1, lo, 20)))
                self.fit("Stage 4", name, "KO (Tc bracketed)", ko_short,
                         free=dict(Ic0=(I0, 0, 50), Tc=(tc0, lo, hi)))
                self.fit("Stage 4", name, "GL depairing (Tc bracketed)", gl_depairing,
                         free=dict(Ic0=(I0, 0, 50), Tc=(tc0, lo, hi)))
                self.fit("Stage 4", name, "KL dirty limit (Tc bracketed)", kl_dirty,
                         free=dict(Ic0=(I0, 0, 50), Tc=(tc0, lo, hi), alpha=(0.2, 0, 1)))
                if ab and np.isfinite(c["Tc_hi"]) and ab["params"]["Tc"] > c["Tc_hi"]:
                    self.notes.append(f"⚠ Ic {name}：AB 自由擬合的 Tc = {ab['params']['Tc']:.2f} K，"
                                      f"高於實際歸零的 {c['Tc_hi']:.2f} K → AB 形狀與資料不符")

    def run_all(self, sns_curves=("inner",), shared_spec="Stage 3 (p=2)", progress=None):
        steps = [("Stage 1：自由 power law", self.stage1), ("Stage 2：bulk Tc", self.stage2),
                 ("Stage 3：共用 Tc（p=1）", lambda: self.stage3(1)), ("Stage 3：共用 Tc（p=2）", lambda: self.stage3(2)),
                 ("Stage 4：JJ 模型", lambda: self.stage4(sns_curves, shared_spec))]
        for k, (label, fn) in enumerate(steps):
            if progress:
                progress(k, len(steps), f"Ic 擬合 {label}")
            fn()
        for res in self.results.values():
            if res["at_bound"]:
                self.notes.append(f"⚠ [{res['stage']}] Ic {res['curve']} {res['label']}："
                                  f"{', '.join(res['at_bound'])} 貼在擬合邊界，資料無法約束此參數，數值不宜直接解讀")
        return self

    # ---- 查詢 ----
    def stage_results(self, stage, labels=None):
        return [r for (s, _, l), r in self.results.items() if s == stage and (labels is None or l in labels)]

    def find(self, curve, label):
        """標籤比對不分大小寫、忽略空白。"""
        key = "".join(label.lower().split())
        for (_, c, l), r in self.results.items():
            if c == curve and "".join(l.lower().split()) == key:
                return r
        return None

    def labels(self, curve):
        return [l for (_, c, l) in self.results if c == curve]

    def summary(self):
        rows = []
        for r in self.results.values():
            rows.append(dict(stage=r["stage"], curve=r["curve"], model=r["label"], R2=r["R2"],
                             RMSE=r["RMSE"], params=fmt_params(r), at_bound=", ".join(r["at_bound"])))
        order = {n: k for k, n in enumerate(self.curves)}
        rows.sort(key=lambda d: (order.get(d["curve"], 99), -(d["R2"] if np.isfinite(d["R2"]) else -9)))
        return rows


def fmt_params(res):
    parts = []
    for n, v in res["params"].items():
        if n in res["fixed"]:
            parts.append(f"{n}={v:.4g} (fixed)")
        elif n in res["at_bound"]:
            parts.append(f"{n}={v:.4g} (貼邊界!)")
        else:
            parts.append(f"{n}={v:.4g}±{res['errors'][n]:.2g}")
    return ", ".join(parts)
