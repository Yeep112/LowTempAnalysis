"""資料總表：掃描資料夾下所有 .db，把每個 run 整理成一張表（CSV / Excel）。

有快取：只會重新讀取有變動的 .db，資料越來越多也很快。
"""
from __future__ import annotations

import csv
import json
import os

from .core.db import QDB

COLUMNS = ["db", "sample_folder", "run_id", "kind", "T_K", "contacts", "sweep",
           "field_axis", "n_points", "start", "duration_s", "exp_name", "db_path"]


def find_dbs(root):
    out = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith((".", "#", "@"))]   # 略過 Synology 隱藏資料夾
        for f in files:
            if f.lower().endswith(".db"):
                out.append(os.path.join(r, f))
    return sorted(out)


def db_mtime(path):
    times = [os.path.getmtime(p) for p in (path, path + "-wal") if os.path.exists(p)]
    return max(times)


def newest_db(root):
    dbs = find_dbs(root)
    if not dbs:
        raise FileNotFoundError(f"{root} 底下找不到 .db")
    return max(dbs, key=db_mtime)


def build(root, out_dir, cache_path=None, log=print):
    cache_path = cache_path or os.path.join(out_dir, ".catalog_cache.json")
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    new_cache, rows = {}, []
    dbs = find_dbs(root)
    log(f"掃描 {root}：找到 {len(dbs)} 個 .db")
    n_read = 0
    for path in dbs:
        stamp = [db_mtime(path), os.path.getsize(path)]
        hit = cache.get(path)
        if hit and hit["stamp"] == stamp:
            db_rows = hit["rows"]
        else:
            try:
                db = QDB(path)
                db_rows = []
                for r in db.runs():
                    row = r.as_row()
                    db_rows.append({
                        "db": db.name, "sample_folder": os.path.basename(db.folder),
                        "run_id": row["run_id"], "kind": row["kind"], "T_K": row["T_K"],
                        "contacts": row["contacts"], "sweep": row["sweep"],
                        "field_axis": row["field_axis"], "n_points": row["n_points"],
                        "start": row["start"], "duration_s": row["duration_s"],
                        "exp_name": row["exp_name"], "db_path": path,
                    })
                n_read += 1
                log(f"  讀取 {db.name}: {len(db_rows)} runs")
            except Exception as e:                       # 壞檔或不是 qcodes db
                log(f"  略過 {path}: {e}")
                continue
        new_cache[path] = {"stamp": stamp, "rows": db_rows}
        rows.extend(db_rows)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(new_cache, f, ensure_ascii=False)

    csv_path = os.path.join(out_dir, "catalog.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    log(f"更新 {n_read} 個、沿用快取 {len(dbs) - n_read} 個；共 {len(rows)} runs")
    log(f"已寫入 {csv_path}")
    xlsx = _write_xlsx(rows, os.path.join(out_dir, "catalog.xlsx"))
    if xlsx:
        log(f"已寫入 {xlsx}")
    return rows


def _write_xlsx(rows, path):
    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "runs"
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r.get(c) for c in COLUMNS])
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"
    widths = {"db": 30, "sample_folder": 16, "exp_name": 48, "db_path": 60, "contacts": 26,
              "start": 19}
    for i, c in enumerate(COLUMNS, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths.get(c, 10)
    try:
        wb.save(path)
    except PermissionError:          # Excel 正開著這個檔
        return None
    return path
