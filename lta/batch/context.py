"""每個分析拿到的 ctx：資料庫、輸出資料夾、存圖存表、挑 run。"""
from __future__ import annotations

import csv
import os

import matplotlib.pyplot as plt

from ..core.common import ensure_dir, fmt_T, safe_name


class Context:
    def __init__(self, db, out_dir, settings=None, show=False, logger=print):
        self.db = db
        self.out = ensure_dir(out_dir)
        self.settings = settings or {}
        self.show = show
        self.files = []          # 這次產生的檔案（給 index.html 用）
        self.notes = []          # 文字結果（印在 index.html）
        self._log = logger

    # ---- 輸出 ----
    def log(self, msg=""):
        self._log(msg)
        self.notes.append(str(msg))

    def save(self, fig, name, subdir=None, keep_open=False):
        fmt = self.settings.get("fig_format", "jpg")
        dpi = self.settings.get("dpi", 300)
        folder = ensure_dir(os.path.join(self.out, subdir)) if subdir else self.out
        path = os.path.join(folder, safe_name(name) + "." + fmt)
        fig.savefig(path, dpi=dpi, bbox_inches="tight")
        self.files.append(path)
        if not (self.show or keep_open):
            plt.close(fig)
        return path

    def save_csv(self, name, header, rows):
        path = os.path.join(self.out, safe_name(name) + ".csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in rows:
                w.writerow(r)
        self.files.append(path)
        return path

    # ---- 挑 run ----
    def resolve_runs(self, job, sort_by_T=None):
        """依 job 設定回傳 [{run_id, T, label, info}, ...]

        job 可用：
          runs   = [116, 115, ...]               明確指定
          select = {kind="IV", contacts="...", T_range=[1.6, 2.8], duplicates="latest", exclude=[103]}
          labels = {"104" = "2.8 K"}             覆寫圖例文字
          temperatures = {"104" = 2.8}           覆寫溫度（名稱沒有溫度時用）
        """
        db = self.db
        if "runs" in job and job["runs"] not in (None, "", []):
            ids = job["runs"]
            if isinstance(ids, int):
                ids = [ids]
            infos = [db.info(i) for i in ids]
            default_sort = False
        elif "select" in job:
            allowed = {"kind", "name_contains", "contacts", "T_range", "sweep", "field_axis",
                       "run_range", "exclude", "duplicates", "min_points"}
            unknown = set(job["select"]) - allowed
            if unknown:
                raise ValueError(f"[job.select] 不認得 {sorted(unknown)}；"
                                 "提醒：[job.select] 之後的設定都會被算進 select，"
                                 "請把它放在該 job 的最後面")
            infos = db.select(**job["select"])
            default_sort = True
        else:
            raise ValueError(f"[{job.get('name')}] 需要 runs = [...] 或 [job.select]")
        if not infos:
            raise ValueError(f"[{job.get('name')}] 沒有符合條件的 run")

        labels = {int(k): v for k, v in (job.get("labels") or {}).items()}
        temps = {int(k): float(v) for k, v in (job.get("temperatures") or {}).items()}
        out = []
        for info in infos:
            T = temps.get(info.run_id, info.T_K)
            label = labels.get(info.run_id, fmt_T(T) if T is not None else f"run {info.run_id}")
            out.append(dict(run_id=info.run_id, T=T, label=label, info=info))
        if sort_by_T if sort_by_T is not None else default_sort:
            out.sort(key=lambda r: (r["T"] is None, r["T"] if r["T"] is not None else 0, r["run_id"]))
        return out
