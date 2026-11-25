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
    lines: int = 1              # 文字參數 > 1 時顯示成多行文字框

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
    hint: str = ""                                   # 操作提示（顯示在圖的上方，不會畫進圖裡）


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
    store: str | None = None    # 設定群組：同群組的分析共用參數值與逐筆設定（預設 = 自己的名稱）
    compute: Callable | None = None   # (items, p, progress) -> data：在背景執行的計算，結果放在 p["_data"]
    progressive: bool = False   # True = 計算中可以先畫已完成的那幾筆

    @property
    def store_key(self):
        return self.store or self.name

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
             group="基本", icon="•", order=100, item_defaults=None, store=None, compute=None,
             progressive=False):
    """登錄一個分析功能（裝飾器）。

    逐筆參數：P(..., per_item=True)。函式收到的 p[key] 是 {run_id: 使用者設定的值}
    （沒設定的 run 不在字典裡，用 item_value(p, key, it, 預設) 取值）。
    item_defaults(items, p) 回傳每筆的預設值，介面的逐筆設定表會顯示它。
    圖上可拖曳的線或點：artist.lta_drag = (key, run_id)，拖完介面會自動更新該筆的值。
    進階拖曳（上下拖、右鍵選單）：artist.lta_drag = Drag(...)，見下面的 Drag。
    store="另一個分析的名稱"：和那個分析共用參數值與逐筆設定（例如轉折點、Ic 擬合共用線性化的 edge）。
    compute=fn：慢的計算 fn(items, p, progress) 會在背景執行（介面不會卡住、顯示進度），
                結果放在 p["_data"] 給繪圖函式用；progress(k, n, 說明) 回報進度。
    progressive=True：計算中先用已完成的那幾筆畫圖（每筆獨立的分析才適合）。"""
    def deco(func):
        lst = ANALYSES[category]
        lst[:] = [a for a in lst if a.name != name]        # 重新載入時不重複
        lst.append(Analysis(category, name, func, params or [], help, multi,
                            tuple(modes) if modes else None, group, icon, order, item_defaults, store,
                            compute, progressive))
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


def group_params(analysis_obj):
    """同一設定群組所有分析的參數（自己的優先）：回傳 (一般參數 list, 逐筆參數 list)。"""
    seen, glob, per = set(), [], []
    members = [analysis_obj] + [a for lst in ANALYSES.values() for a in lst
                                if a is not analysis_obj and a.store_key == analysis_obj.store_key]
    for a in members:
        for q in a.params:
            if q.key in seen:
                continue
            seen.add(q.key)
            (per if q.per_item else glob).append(q)
    return glob, per


def defaults_for(analysis_obj, items=None):
    """沒有介面時（例如 Jupyter）產生完整的參數：一般參數用預設值、逐筆參數為空字典。"""
    glob, per = group_params(analysis_obj)
    p = {q.key: q.default for q in glob}
    p.update({q.key: {} for q in per})
    return p


@dataclass
class Drag:
    """圖上可拖曳的點或線（artist.lta_drag = Drag(...)）。

    key / run_id : 拖完後寫入的逐筆參數
    axis         : "x" 左右拖、"y" 上下拖
    value        : value(座標, 目前文字) -> 新文字；省略 = 直接寫入座標
    group        : 同一個 group 的 artist 一起移動（省略 = (key, run_id)）
    menu         : 右鍵選單 [(標籤, fn(目前文字) -> 新文字), ...]
    tip          : 滑鼠移上去時狀態列顯示的說明
    """
    key: str
    run_id: int
    axis: str = "x"
    value: Callable | None = None
    group: Any = None
    menu: list = field(default_factory=list)
    tip: str = ""

    @property
    def gid(self):
        return self.group if self.group is not None else (self.key, self.run_id)


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
    check_groups()
    return ANALYSES


def check_groups():
    """同一設定群組裡，同名參數必須是同一種設定（否則會互相覆蓋）。"""
    seen = {}
    for lst in ANALYSES.values():
        for a in lst:
            for q in a.params:
                sig = (q.per_item, q.kind, repr(q.default), tuple(q.choices or ()))
                key = (a.store_key, q.key)
                if key in seen and seen[key][0] != sig:
                    raise ValueError(f"「{a.name}」的參數 {q.key} 和同群組的「{seen[key][1]}」同名但設定不同，"
                                     "請換一個名稱")
                seen.setdefault(key, (sig, a.name))


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
