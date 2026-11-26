"""直接讀取 qcodes 的 .db（SQLite），不需要安裝 qcodes。

用法：
    from lta.db import QDB
    db = QDB(r"...\\quad_002_2026-11-14_01.db")
    db.runs()                 # 所有 run 的資訊（list of RunInfo）
    ds = db.load(116)         # 讀一個 run
    I, V = ds.xy()            # 自動找電流、電壓
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime

import numpy as np

from .measurement import (is_field, is_gate, plain, run_condition, Axis, Curve, Map2D, detect_mode, order_setpoints,
                          reshape_grid, symbol)


# ---------------------------------------------------------------------------
# 從實驗名稱解析資訊
#   例：'IV_I+_38, V+_39, V-_40, I-_41, T_1.6K_I-_I+'
#       'IV_I+_38, V+_39, V-_40, I-_41, T_1.6K, By'
# ---------------------------------------------------------------------------
_RE_T = re.compile(r"T_([0-9]*\.?[0-9]+)\s*(mK|K)", re.IGNORECASE)
_RE_CONTACTS = re.compile(r"(I\+_\d+.*?I-_\d+)")
_RE_SWEEP = re.compile(r"_(I[+-])_(I[+-])\s*$")
_RE_FIELD_TAG = re.compile(r",\s*(B[xyz])\b")


def parse_exp_name(name: str) -> dict:
    """從 qcodes 實驗名稱抓出溫度、接點、掃描方向、磁場方向。抓不到就是 None。"""
    info = {"T_K": None, "contacts": None, "sweep": None, "field_tag": None}
    if not name:
        return info
    m = _RE_T.search(name)
    if m:
        value = float(m.group(1))
        info["T_K"] = value / 1000.0 if m.group(2).lower() == "mk" else value
    m = _RE_CONTACTS.search(name)
    if m:
        info["contacts"] = m.group(1)
    m = _RE_SWEEP.search(name)
    if m:
        info["sweep"] = f"{m.group(1)}->{m.group(2)}"
    m = _RE_FIELD_TAG.search(name)
    if m:
        info["field_tag"] = m.group(1)
    return info


@dataclass
class RunInfo:
    run_id: int
    exp_id: int
    exp_name: str             # 實驗描述（qcodes sample_name），例如 'IV_I+_38, ..., T_1.6K'
    sample_name: str
    kind: str                 # qcodes 實驗名稱，例如 IV、VI、IV vs By
    parameters: list
    n_points: int
    start: str
    duration_s: float
    T_K: float | None = None
    contacts: str | None = None
    sweep: str | None = None
    field_axis: str | None = None   # x_field / y_field / z_field
    is_2d: bool = False             # 掃描軸 ≥ 2 個
    table: str = ""
    setpoints: list = field(default_factory=list)   # 掃描軸（施加的量）
    measured: list = field(default_factory=list)    # 量測值
    units: dict = field(default_factory=dict)
    mode: str = "generic"           # current_bias / voltage_bias / generic（見 measurement.py）
    ndim: int = 1

    @property
    def mode_short(self):
        """清單上顯示的量測方式，例如 I→V、B⊥×I→V"""
        if not self.measured:
            return "?"
        z = symbol(self.measured[0], self.units.get(self.measured[0], ""))
        axes = [symbol(n, self.units.get(n, "")) for n in self.setpoints]
        return plain("×".join(axes) + "→" + z)

    @property
    def condition(self):
        """外加條件：field（磁場）/ gate（電場、閘極）/ none（無外加）。見 measurement.CONDITIONS。"""
        return run_condition(self.setpoints, self.units)

    @property
    def gate_params(self):
        """這筆量測裡的閘極電壓參數（掃描軸）。"""
        return [n for n in self.setpoints if is_gate(n, self.units.get(n, "")) and not is_field(n, self.units.get(n, ""))]

    def as_row(self) -> dict:
        row = asdict(self)
        row["parameters"] = ",".join(self.parameters)
        for k in ("table", "setpoints", "measured", "units"):
            row.pop(k)
        return row


@dataclass
class Dataset:
    run_id: int
    info: RunInfo
    data: dict                      # 參數名稱 -> np.ndarray
    units: dict = field(default_factory=dict)
    labels: dict = field(default_factory=dict)
    dependents: list = field(default_factory=list)
    setpoints: list = field(default_factory=list)

    # ---- 常用欄位自動判斷 ----
    def current_name(self) -> str:
        for name in ("appl_current",):
            if name in self.data:
                return name
        for name in self.setpoints:
            if self.units.get(name) == "A" and not name.endswith("_field"):
                return name
        raise KeyError(f"Run {self.run_id}: 找不到電流參數，現有參數 {list(self.data)}")

    def voltage_name(self, preferred: str | None = None) -> str:
        if preferred and preferred in self.data:
            return preferred
        for name in self.dependents:
            if self.units.get(name) == "V":
                return name
        if self.dependents:
            return self.dependents[0]
        raise KeyError(f"Run {self.run_id}: 找不到量測電壓參數")

    def field_name(self) -> str | None:
        for name in self.data:
            if name.endswith("_field"):
                return name
        return None

    def xy(self, x: str | None = None, y: str | None = None):
        """回傳 (I [A], V [V]) 的 1D 陣列。"""
        x = x or self.current_name()
        y = self.voltage_name(y)
        return self.data[x].copy(), self.data[y].copy()

    # ---- 一般化的資料（不限 I→V）----
    def curve(self, measured: str | None = None) -> Curve:
        """1D：回傳 Curve（掃描軸 → 量測值），單位 SI。"""
        if len(self.setpoints) != 1:
            raise ValueError(f"Run {self.run_id} 有 {len(self.setpoints)} 個掃描軸，不是 1D 資料")
        x = self.setpoints[0]
        y = measured if measured in self.data else (self.dependents[0] if self.dependents else None)
        if y is None:
            raise KeyError(f"Run {self.run_id}: 找不到量測值")
        return Curve(Axis(x, self.units.get(x, ""), self.data[x].copy()),
                     Axis(y, self.units.get(y, ""), self.data[y].copy()),
                     detect_mode(self.units.get(x, ""), self.units.get(y, "")))

    def map2d(self, measured: str | None = None) -> Map2D:
        """2D：回傳 Map2D（慢軸 × 快軸 → 量測值），陣列形狀 (N_slow, N_fast)。"""
        if len(self.setpoints) < 2:
            raise ValueError(f"Run {self.run_id} 只有一個掃描軸，不是 2D 資料")
        if len(self.setpoints) > 2:
            raise NotImplementedError(f"Run {self.run_id} 有 {len(self.setpoints)} 個掃描軸，"
                                      "3 維以上的資料還沒有分析功能（見 擴充指南.md）")
        slow, fast = order_setpoints(self.data, self.setpoints)
        z = measured if measured in self.data else self.dependents[0]
        S, F, Z = reshape_grid(self.data[slow], self.data[fast], self.data[z])
        u = self.units
        return Map2D(Axis(slow, u.get(slow, ""), S), Axis(fast, u.get(fast, ""), F),
                     Axis(z, u.get(z, ""), Z), detect_mode(u.get(fast, ""), u.get(z, "")))

    def grid(self, y: str | None = None):
        """2D 量測（磁場 x 電流）整理成 (B, I, V) 三個 (NB, NI) 陣列，單位 T, A, V。

        和 notebook 一樣：以「第一次磁場改變的位置」決定每個磁場有幾個電流點。
        """
        b_name = self.field_name()
        if b_name is None:
            raise ValueError(f"Run {self.run_id} 不是磁場掃描（沒有 *_field 參數）")
        B = self.data[b_name]
        I = self.data[self.current_name()]
        V = self.data[self.voltage_name(y)]
        total = min(len(B), len(I), len(V))
        B, I, V = B[:total], I[:total], V[:total]
        changes = np.flatnonzero(B != B[0])
        NI = int(changes[0]) if len(changes) else total
        if NI <= 0:
            raise ValueError("無法判斷每個磁場的電流點數")
        NB = total // NI
        used = NB * NI
        return (B[:used].reshape(NB, NI), I[:used].reshape(NB, NI),
                V[:used].reshape(NB, NI))


def _parse_description(text):
    """run_description（JSON）→ (units, depends_on)"""
    units, deps = {}, {}
    try:
        desc = json.loads(text)
        for spec in desc["interdependencies"]["paramspecs"]:
            units[spec["name"]] = spec.get("unit", "")
            deps[spec["name"]] = spec.get("depends_on", [])
    except Exception:
        pass
    return units, deps


class QDB:
    """一個 qcodes .db 檔。只讀，不會修改檔案。"""

    def __init__(self, path: str):
        path = os.path.abspath(os.path.expanduser(str(path)))
        if not os.path.isfile(path):
            raise FileNotFoundError(path)
        self.path = path
        self._runs: list[RunInfo] | None = None

    # 以唯讀模式開啟；SynologyDrive 等同步資料夾上 -wal/-shm 檔正常存在
    def _connect(self):
        uri = "file:" + self.path.replace("\\", "/").replace("?", "%3f").replace("#", "%23") + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True)
            con.execute("select 1 from runs limit 1")
            return con
        except sqlite3.Error:
            return sqlite3.connect(self.path)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    def runs(self, refresh: bool = False) -> list[RunInfo]:
        if self._runs is not None and not refresh:
            return self._runs
        con = self._connect()
        try:
            rows = con.execute(
                """select r.run_id, r.exp_id, e.name, e.sample_name,
                          r.result_table_name, r.result_counter,
                          r.run_timestamp, r.completed_timestamp, r.parameters,
                          r.run_description
                   from runs r left join experiments e on r.exp_id = e.exp_id
                   order by r.run_id"""
            ).fetchall()
            counts = {}
            for row in rows:          # 每個 run 的資料點數（result_counter 不是點數）
                table = row[4]
                try:
                    counts[table] = con.execute(f'select count(*) from "{table}"').fetchone()[0]
                except sqlite3.Error:
                    counts[table] = 0
        finally:
            con.close()
        out = []
        for (run_id, exp_id, exp_name, sample, table, counter,
             t0, t1, params, desc_text) in rows:
            params_list = [p for p in (params or "").split(",") if p]
            # qcodes: load_or_create_experiment(name, sample_name)
            #   name        → 'IV'、'IV vs By'（量測種類）
            #   sample_name → 'IV_I+_38, V+_39, V-_40, I-_41, T_1.6K'（描述，溫度在這裡）
            kind = (exp_name or "").strip()
            description = (sample or exp_name or "").strip()
            parsed = parse_exp_name(description)
            if parsed["T_K"] is None and exp_name:
                parsed = {**parsed, **{k: v for k, v in parse_exp_name(exp_name).items() if v}}
            field_axis = next((p for p in params_list if p.endswith("_field")), None)
            units, deps = _parse_description(desc_text)
            measured = [n for n in params_list if deps.get(n)] or params_list[-1:]
            setpoints = []
            for m in measured:
                for d in deps.get(m, []):
                    if d not in setpoints:
                        setpoints.append(d)
            if not setpoints:
                setpoints = [n for n in params_list if n not in measured]
            mode = detect_mode(units.get(setpoints[-1], "") if setpoints else "",
                               units.get(measured[0], "") if measured else "")
            start = (datetime.fromtimestamp(t0).strftime("%Y-%m-%d %H:%M:%S")
                     if t0 else "")
            duration = float(t1 - t0) if (t0 and t1) else float("nan")
            out.append(RunInfo(
                run_id=int(run_id), exp_id=int(exp_id or 0),
                exp_name=description, sample_name=sample or "",
                kind=kind, parameters=params_list,
                n_points=int(counts.get(table, 0)), start=start,
                duration_s=round(duration, 1),
                T_K=parsed["T_K"], contacts=parsed["contacts"],
                sweep=parsed["sweep"], field_axis=field_axis,
                is_2d=len(setpoints) >= 2, table=table or "",
                setpoints=setpoints, measured=measured, units=units, mode=mode,
                ndim=max(len(setpoints), 1),
            ))
        self._runs = out
        return out

    def info(self, run_id: int) -> RunInfo:
        for r in self.runs():
            if r.run_id == int(run_id):
                return r
        raise KeyError(f"{self.name} 裡沒有 run {run_id}")

    def load(self, run_id: int) -> Dataset:
        info = self.info(run_id)
        con = self._connect()
        try:
            desc_text = con.execute(
                "select run_description from runs where run_id=?", (int(run_id),)
            ).fetchone()[0]
            cols = [c[1] for c in con.execute(f'pragma table_info("{info.table}")')]
            cols = [c for c in cols if c != "id"]
            rows = con.execute(
                f'select {", ".join(chr(34) + c + chr(34) for c in cols)} '
                f'from "{info.table}" order by id'
            ).fetchall()
        finally:
            con.close()

        units, labels, deps = {}, {}, {}
        try:
            desc = json.loads(desc_text)
            for spec in desc["interdependencies"]["paramspecs"]:
                units[spec["name"]] = spec.get("unit", "")
                labels[spec["name"]] = spec.get("label", spec["name"])
                deps[spec["name"]] = spec.get("depends_on", [])
        except Exception:
            pass
        dependents = [n for n in cols if deps.get(n)]
        setpoints = [n for n in info.setpoints if n in cols] or [n for n in cols if n not in dependents]

        arr = np.array(rows, dtype=float) if rows else np.empty((0, len(cols)))
        # 和 qcodes get_parameter_data 一樣：丟掉量測值是空的列
        if dependents and len(arr):
            keep = np.all(np.isfinite(arr[:, [cols.index(d) for d in dependents]]), axis=1)
            arr = arr[keep]
        data = {c: arr[:, i].copy() for i, c in enumerate(cols)}
        return Dataset(run_id=int(run_id), info=info, data=data, units=units,
                       labels=labels, dependents=dependents, setpoints=setpoints)

    # ---- 依條件挑 run ----
    def select(self, kind=None, name_contains=None, contacts=None, T_range=None,
               sweep=None, field_axis=None, run_range=None, exclude=None,
               duplicates="all", min_points=0) -> list[RunInfo]:
        """依條件挑選 run。duplicates = 'all' | 'latest' | 'first'（同溫度只留一筆）。"""
        exclude = set(int(x) for x in (exclude or []))
        sel = []
        for r in self.runs():
            if r.run_id in exclude:
                continue
            if kind is not None and r.kind != kind:
                continue
            if name_contains:
                needles = [name_contains] if isinstance(name_contains, str) else name_contains
                if not all(n in r.exp_name for n in needles):
                    continue
            if contacts and (r.contacts or "").replace(" ", "") != contacts.replace(" ", ""):
                continue
            if T_range is not None:
                if r.T_K is None or not (T_range[0] - 1e-9 <= r.T_K <= T_range[1] + 1e-9):
                    continue
            if sweep is not None:
                if str(sweep).lower() == "none":
                    if r.sweep is not None:      # 只要名稱沒有標掃描方向的
                        continue
                elif r.sweep != sweep:
                    continue
            if field_axis is not None and r.field_axis != field_axis:
                continue
            if run_range is not None and not (run_range[0] <= r.run_id <= run_range[1]):
                continue
            if r.n_points < min_points:
                continue
            sel.append(r)
        if duplicates in ("latest", "first"):
            by_T = {}
            for r in sel:
                key = r.T_K if r.T_K is not None else ("run", r.run_id)
                if key not in by_T or duplicates == "latest":
                    by_T[key] = r
            sel = sorted(by_T.values(), key=lambda r: r.run_id)
        return sel

    def table(self, runs=None) -> str:
        """類似 qc.experiments() 的文字總表。"""
        runs = runs if runs is not None else self.runs()
        lines = [f"{'run':>4}  {'量測':<10} {'T(K)':>6}  {'pts':>6}  {'start':<19}  name"]
        for r in runs:
            T = f"{r.T_K:6.3g}" if r.T_K is not None else "     -"
            lines.append(f"{r.run_id:>4}  {r.mode_short[:10]:<10} {T}  {r.n_points:>6}  "
                         f"{r.start:<19}  {r.exp_name}")
        return "\n".join(lines)
