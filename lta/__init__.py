"""lta — Low-Temperature Analysis

套件結構
  lta/core/         資料層：讀 qcodes .db、量測模式（I→V、V→I…）、共用工具、分析登錄
  lta/analysis1d/   1D 分析功能（一個功能一個檔案；放進資料夾就會自動出現在介面）
  lta/analysis2d/   2D 分析功能（同上）
  lta/gui/          分析介面
  lta/batch/        批次分析（jobs\\*.toml → 圖 + index.html）

在 Jupyter 裡：
    from lta import QDB
    db = QDB(r"...\\quad_002_2026-11-14_01.db")
    print(db.table())
    c = db.load(116).curve()      # 1D：c.x / c.y（Axis），c.mode
    m = db.load(142).map2d()      # 2D：m.slow / m.fast / m.z
"""
def _read_version():
    import json
    import os
    try:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "release.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("version", "0")
    except Exception:
        return "0"


__version__ = _read_version()          # 版本號只寫在 release.json


def __getattr__(name):
    """延後載入（import lta 不會馬上載入 numpy，讓軟體的載入畫面先出現）。"""
    if name in ("QDB", "parse_exp_name"):
        from .core import db
        return getattr(db, name)
    raise AttributeError(name)
