"""讀取分析設定檔（TOML），逐一執行，輸出圖、CSV、index.html。"""
from __future__ import annotations

import html
import os
import time
import traceback
from datetime import datetime

from ..core.db import QDB
from .context import Context
from ..core.common import ensure_dir, safe_name

TOOL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_toml(path):
    try:
        import tomllib
    except ImportError:          # Python 3.10 以前
        try:
            import tomli as tomllib
        except ImportError:
            raise SystemExit("需要 Python 3.11 以上，或先執行：pip install tomli")
    with open(path, "rb") as f:
        try:
            return tomllib.load(f)
        except Exception as e:
            raise SystemExit(f"設定檔格式錯誤 {path}\n  {e}")


def user_dir():
    """每台電腦、每個使用者自己的資料夾（%LOCALAPPDATA%\\LowTempAnalysis），不在工具資料夾裡。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    d = os.path.join(base, "LowTempAnalysis")
    os.makedirs(d, exist_ok=True)
    return d


def settings_path():
    """settings.toml 的位置：放在使用者資料夾，所以換工具資料夾、從 GitHub 更新都不會遺失或被上傳。

    第一次執行時：工具資料夾裡如果有舊的 settings.toml 就搬過來（複製），沒有就用範本建立。"""
    import shutil
    path = os.path.join(user_dir(), "settings.toml")
    if not os.path.exists(path):
        old = os.path.join(TOOL_ROOT, "settings.toml")
        example = os.path.join(TOOL_ROOT, "settings.example.toml")
        for src in (old, example):
            if os.path.exists(src):
                try:
                    shutil.copy2(src, path)
                except OSError:
                    continue
                break
    return path


def load_settings():
    path = settings_path()
    return load_toml(path) if os.path.exists(path) else {}


def resolve_db(spec, job_file, settings):
    """db 可寫：絕對路徑 / 相對於設定檔的路徑 / 只寫檔名（自動在 data_root 底下找）。"""
    if not spec:
        raise SystemExit(f"{job_file} 沒有指定 db = '...'")
    spec = os.path.expanduser(spec)
    cands = [spec, os.path.join(os.path.dirname(job_file), spec)]
    for c in cands:
        if os.path.isfile(c):
            return os.path.abspath(c)
    root = settings.get("data_root")
    if root and os.path.isdir(root):
        from ..catalog import find_dbs
        target = os.path.basename(spec).lower()
        hits = [p for p in find_dbs(root) if os.path.basename(p).lower() == target]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise SystemExit(f"在 data_root 找到多個 {target}，請改寫完整路徑：\n  " + "\n  ".join(hits))
    raise SystemExit(f"找不到資料庫：{spec}")


def output_base(db_path, job_file, settings, override=None):
    tmpl = override or settings.get("output_dir") or "{db_dir}/01 Figure/{job_file}"
    return os.path.normpath(tmpl.format(
        db_dir=os.path.dirname(db_path),
        db_name=os.path.splitext(os.path.basename(db_path))[0],
        job_file=os.path.splitext(os.path.basename(job_file))[0]))


def run_job_file(job_file, only=None, show=False, out_override=None, log=print):
    from .jobs import load_all
    registry = load_all()
    settings = load_settings()
    cfg = load_toml(job_file)
    jobs = cfg.get("job") or []
    if not jobs:
        raise SystemExit(f"{job_file} 裡沒有任何 [[job]]")
    defaults = cfg.get("defaults") or {}
    db_path = resolve_db(cfg.get("db"), job_file, settings)
    base = ensure_dir(output_base(db_path, job_file, settings,
                                  out_override or cfg.get("output_dir")))
    db = QDB(db_path)
    log(f"設定檔：{job_file}")
    log(f"資料庫：{db_path}  （{len(db.runs())} runs）")
    log(f"輸出到：{base}")

    import matplotlib.pyplot as plt
    summary = []
    for raw in jobs:
        job = {**defaults, **raw}
        name = job.get("name") or job.get("type")
        job["name"] = name
        if only and name not in only:
            continue
        if not job.get("enabled", True):
            log(f"\n--- 略過 [{name}]（enabled = false）")
            continue
        func = registry.get(job.get("type"))
        log(f"\n=== [{name}]  type = {job.get('type')}")
        if func is None:
            msg = f"未知的 type：{job.get('type')}；可用：{', '.join(sorted(registry))}"
            log("  ✗ " + msg)
            summary.append(dict(name=name, ok=False, error=msg, files=[], notes=[], out=""))
            continue
        job_db = QDB(resolve_db(job["db"], job_file, settings)) if "db" in raw else db
        out = os.path.join(base, safe_name(name))
        ctx = Context(job_db, out, settings, show=show, logger=lambda m: log("  " + str(m)))
        t0 = time.time()
        try:
            func(ctx, job)
            ok, err = True, ""
            log(f"  ✓ 完成（{time.time() - t0:.1f} s，{len(ctx.files)} 個檔案）")
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
            log("  ✗ 失敗：" + err)
            ctx.notes.append(traceback.format_exc())
        if show:
            plt.show()
        plt.close("all")
        summary.append(dict(name=name, type=job.get("type"), ok=ok, error=err, files=ctx.files,
                            notes=ctx.notes, out=out))

    with open(job_file, encoding="utf-8") as f:
        cfg_text = f.read()
    with open(os.path.join(base, "_config_used.toml"), "w", encoding="utf-8") as f:
        f.write(f"# 執行時間 {datetime.now():%Y-%m-%d %H:%M:%S}\n# 原始設定檔 {job_file}\n\n")
        f.write(cfg_text)
    index = write_index(base, job_file, db_path, summary, cfg_text)
    n_fail = sum(not s["ok"] for s in summary)
    log(f"\n全部完成：{len(summary) - n_fail} 成功，{n_fail} 失敗")
    log(f"結果總覽：{index}")
    return index, summary


def _favicon():
    """assets/icon_64.png 轉成 data URI（index.html 搬到哪裡都看得到圖示）。"""
    import base64
    path = os.path.join(TOOL_ROOT, "assets", "icon_64.png")
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    except OSError:
        return ""


def write_index(base, job_file, db_path, summary, cfg_text):
    """產生 index.html：每個分析的文字結果、圖、CSV 一頁看完。"""
    def rel(p):
        return os.path.relpath(p, base).replace("\\", "/")

    parts = [f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>{html.escape(os.path.basename(job_file))}</title>
<link rel="icon" href="{_favicon()}">
<style>
body{{font-family:"Segoe UI","Microsoft JhengHei",sans-serif;margin:24px auto;max-width:1300px;color:#222;padding:0 16px}}
h1{{font-size:22px;margin-bottom:4px}} .meta{{color:#666;font-size:13px}}
nav a{{margin-right:12px;font-size:14px}}
section{{border-top:1px solid #ddd;margin-top:28px;padding-top:10px}}
h2{{font-size:18px}} .ok{{color:#1a7f37}} .fail{{color:#c62828}}
pre{{background:#f6f8fa;padding:10px;font-size:12px;overflow:auto;max-height:340px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:12px}}
.grid figure{{margin:0}} .grid img{{width:100%;border:1px solid #eee}}
figcaption{{font-size:12px;color:#555;word-break:break-all}}
details summary{{cursor:pointer;color:#0b57d0;margin:8px 0}}
</style></head><body>
<h1>{html.escape(os.path.basename(job_file))}</h1>
<div class="meta">更新時間 {datetime.now():%Y-%m-%d %H:%M:%S}<br>資料庫 {html.escape(db_path)}</div>
<nav>"""]
    for s in summary:
        mark = "✓" if s["ok"] else "✗"
        parts.append(f'<a href="#{html.escape(s["name"])}">{mark} {html.escape(s["name"])}</a>')
    parts.append("</nav>")
    img_ext = (".jpg", ".jpeg", ".png", ".svg")
    for s in summary:
        cls = "ok" if s["ok"] else "fail"
        status = "完成" if s["ok"] else "失敗：" + html.escape(s["error"])
        parts.append(f'<section id="{html.escape(s["name"])}"><h2>{html.escape(s["name"])} '
                     f'<small class="{cls}">{status}</small></h2>')
        if s.get("type"):
            parts.append(f'<div class="meta">type = {html.escape(s["type"])}</div>')
        main = [f for f in s["files"] if f.lower().endswith(img_ext) and
                os.path.dirname(f) == s["out"]]
        sub = [f for f in s["files"] if f.lower().endswith(img_ext) and f not in main]
        csvs = [f for f in s["files"] if f.lower().endswith(".csv")]
        if main:
            parts.append('<div class="grid">')
            for f in main:
                parts.append(f'<figure><a href="{rel(f)}"><img src="{rel(f)}" loading="lazy"></a>'
                             f'<figcaption>{html.escape(os.path.basename(f))}</figcaption></figure>')
            parts.append("</div>")
        if sub:
            parts.append(f"<details><summary>個別圖（{len(sub)} 張）</summary><div class='grid'>")
            for f in sub:
                parts.append(f'<figure><a href="{rel(f)}"><img src="{rel(f)}" loading="lazy"></a>'
                             f'<figcaption>{html.escape(os.path.basename(f))}</figcaption></figure>')
            parts.append("</div></details>")
        if csvs:
            parts.append("<p>CSV：" + "、".join(
                f'<a href="{rel(f)}">{html.escape(os.path.basename(f))}</a>' for f in csvs) + "</p>")
        if s["notes"]:
            parts.append("<details open><summary>文字結果</summary><pre>"
                         + html.escape("\n".join(s["notes"])) + "</pre></details>")
        parts.append("</section>")
    parts.append("<section><details><summary>這次使用的設定檔</summary><pre>"
                 + html.escape(cfg_text) + "</pre></details></section></body></html>")
    path = os.path.join(base, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path
