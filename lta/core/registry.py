"""分析功能登錄（介面與 Jupyter 共用）。

每個功能 = 一個函式 + 參數清單 + 支援的量測模式。
把檔案放進 lta/analysis1d/ 或 lta/analysis2d/ 就會自動被載入（檔名以 _ 開頭的不會）。

    from ..core.registry import P, Result, analysis

    @analysis("1D", "我的分析", group="進階", icon="★",
              modes=("current_bias",),            # None = 所有模式都可以
              params=[P("window", "平滑點數", 11),
                      P("scale", "Y 軸", "linear", choices=["linear", "log"])])
    def my_analysis(fig, items, p):
        ax = fig.add_subplot()
        for it in items:
            c = it.curve()                         # c.x / c.y：Axis（SI 單位）
            x, xl = c.x.scaled(); y, yl = c.y.scaled()
            ax.plot(x, y, color=it.color, label=it.label)
        ax.set_xlabel(xl); ax.set_ylabel(yl)
        return Result(text="...")
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable

from .measurement import MODES

ANALYSES: dict[str, list] = {"1D": [], "2D": []}
CATEGORY_PACKAGES = {"1D": "lta.analysis1d", "2D": "lta.analysis2d"}


@dataclass
class P:
    """一個參數。依 default 的型別自動產生輸入欄位：
    float/int → 輸入框、bool → 勾選、choices → 下拉選單、str → 文字框"""
    key: str
    label: str
    default: Any
    choices: list | None = None
    tip: str = ""
    per_item: bool = False      # True = 每筆 run 可以有自己的值（介面會出現逐筆設定表）

    @property
    def kind(self):
        if self.choices:
            return "choice"
        if isinstance(self.default, bool):
            return "bool"
        if isinstance(self.default, int):
            return "int"
        if isinstance(self.default, float):
            return "float"
        return "text"

    def parse(self, raw):
        k = self.kind
        if k == "bool":
            return bool(raw)
        if k in ("int", "float"):
            if isinstance(raw, str):
                raw = raw.strip()
                if raw == "":
                    return self.default
            try:
                return int(float(raw)) if k == "int" else float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"「{self.label}」需要數字，收到：{raw!r}")
        return raw if raw is not None else self.default


@dataclass
class Result:
    text: str = ""
    export: dict = field(default_factory=dict)      # 名稱 -> (header list, rows)
    on_click: Callable | None = None                 # 色圖點擊切線等互動
    title: str = ""


@dataclass
class Analysis:
    category: str
    name: str
    func: Callable
    params: list
    help: str = ""
    multi: bool = True                  # 可同時分析多筆
    modes: tuple | None = None          # 支援的量測模式；None = 全部
    group: str = "基本"
    icon: str = "•"
    order: int = 100
    item_defaults: Callable | None = None   # (items, p) -> {run_id: {key: 預設值}}，給逐筆設定表顯示

    @property
    def per_item_params(self):
        return [q for q in self.params if q.per_item]

    @property
    def global_params(self):
        return [q for q in self.params if not q.per_item]

    def supports(self, mode):
        return self.modes is None or mode in self.modes

    @property
    def modes_text(self):
        if self.modes is None:
            return "所有量測模式"
        return "、".join(MODES.get(m, {"short": m})["short"] for m in self.modes)


def analysis(category, name, params=None, help="", multi=True, modes=None,
             group="基本", icon="•", order=100, item_defaults=None):
    """登錄一個分析功能（裝飾器）。

    逐筆參數：P(..., per_item=True)。函式收到的 p[key] 是 {run_id: 使用者設定的值}
    （沒設定的 run 不在字典裡，用 item_value(p, key, it, 預設) 取值）。
    item_defaults(items, p) 回傳每筆的預設值，介面的逐筆設定表會顯示它。
    圖上可拖曳的線或點：artist.lta_drag = (key, run_id)，拖完介面會自動更新該筆的值。"""
    def deco(func):
        lst = ANALYSES[category]
        lst[:] = [a for a in lst if a.name != name]        # 重新載入時不重複
        lst.append(Analysis(category, name, func, params or [], help, multi,
                            tuple(modes) if modes else None, group, icon, order, item_defaults))
        lst.sort(key=lambda a: (a.order, a.name))
        return func
    return deco


gui_analysis = analysis      # 舊名稱


def item_value(p, key, it, default=None):
    """逐筆參數：這筆 run 有個別設定就用它，否則用 default。"""
    values = p.get(key)
    if not isinstance(values, dict):
        return default
    return values.get(it.run_id, default)


def defaults_for(analysis_obj, items):
    """沒有介面時（例如 Jupyter）產生完整的參數：一般參數用預設值、逐筆參數為空字典。"""
    p = {q.key: q.default for q in analysis_obj.global_params}
    p.update({q.key: {} for q in analysis_obj.per_item_params})
    return p


def load_analyses(reload=False):
    """自動匯入 analysis1d/、analysis2d/ 底下所有不以 _ 開頭的模組。"""
    for cat, pkg_name in CATEGORY_PACKAGES.items():
        pkg = importlib.import_module(pkg_name)
        for m in pkgutil.iter_modules(pkg.__path__):
            if m.name.startswith("_"):
                continue
            mod = importlib.import_module(f"{pkg_name}.{m.name}")
            if reload:
                importlib.reload(mod)
    return ANALYSES


load_gui_analyses = load_analyses


class Item:
    """一筆被選取的資料：給分析函式用。"""

    def __init__(self, ds, label, color, T=None):
        self.ds = ds
        self.info = ds.info
        self.run_id = ds.run_id
        self.label = label
        self.color = color
        self.T = T if T is not None else ds.info.T_K

    @property
    def mode(self):
        return self.info.mode

    def curve(self):
        """1D：Curve（x = 掃描軸、y = 量測值，SI 單位）"""
        return self.ds.curve()

    def map2d(self):
        """2D：Map2D（slow × fast → z）"""
        return self.ds.map2d()

    def iv(self):
        """(I [A], V [V])；不論施加的是電流或電壓都回傳電流、電壓。"""
        c = self.ds.curve()
        if c.mode == "voltage_bias":
            return c.y.values, c.x.values
        return c.x.values, c.y.values
