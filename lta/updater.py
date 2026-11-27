"""透過 GitHub 自動更新。

運作方式
  ‧ 工具資料夾裡的 release.json 記錄：目前版本、GitHub 儲存庫（owner/repo）、分支、更新內容
  ‧ 檢查：讀 https://raw.githubusercontent.com/<repo>/<branch>/release.json，版本比較新就提示
  ‧ 更新：下載 https://github.com/<repo>/archive/refs/heads/<branch>.zip，
          先把目前的程式備份到 _backup\\，再換成新版；使用者自己的檔案不會動：
          settings.toml、jobs\\*.toml（_template.toml 除外）、catalog.*、_backup\\、logs\\
  ‧ 開發用資料夾（有 .git）不會被自動更新，請用 git pull

簽章
  ‧ 每次發佈都附上 release_manifest.json（每個檔案的 SHA-256）與 release_manifest.sig（Ed25519，私鑰只在開發者電腦）
  ‧ 同事的軟體用自己已經有的公鑰（lta/release_key.pub）驗證，對不上就不安裝（見 signing.py）

發佈新版（開發者）
  publish.bat     你自己改的程式 → 測試 → 簽章 → 上傳
  收更新.bat       Claude 推到 pending 分支的更新 → 測試 → 簽章 → 上傳
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

from .gui.launcher import TOOL_ROOT

RELEASE_FILE = os.path.join(TOOL_ROOT, "release.json")
INSTALLED_FILE = ".installed_commit"     # 目前安裝的是 GitHub 上哪一個 commit（沒改版本號的修改也能更新）
# 不會被更新覆蓋的使用者檔案 / 資料夾（工具資料夾第一層）
PRESERVE = {INSTALLED_FILE, "settings.toml", "jobs", "_backup", "logs", ".git", "catalog.csv", "catalog.xlsx",
            ".catalog_cache.json", "__pycache__"}
KEEP_BACKUPS = 3


# ---------------------------------------------------------------------------
# 版本資訊
# ---------------------------------------------------------------------------
def read_release(path=RELEASE_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"version": "0", "repo": "", "branch": "main", "notes": []}


def write_release(data, path=RELEASE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_version(v):
    out = []
    for part in str(v).lstrip("vV").split("."):
        num = "".join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def push_history(rel):
    """換版本號之前，把目前這一版的說明移到 history（最新的在前面）。"""
    cur = {k: rel.get(k) for k in ("version", "date", "notes") if rel.get(k) is not None}
    if not cur.get("version"):
        return rel
    hist = [h for h in rel.get("history", []) if h.get("version") != cur["version"]]
    rel["history"] = ([cur] + hist)[:60]
    return rel


def all_notes(rel):
    """[(版本, 日期, [說明…])]，最新的在前面。"""
    out = [(rel.get("version", "?"), rel.get("date", ""), rel.get("notes", []))]
    out += [(h.get("version", "?"), h.get("date", ""), h.get("notes", [])) for h in rel.get("history", [])
            if h.get("version") != rel.get("version")]
    return out


def notes_since(remote, current):
    """遠端 release.json 裡，比 current 新的每一版說明（同事跳過好幾版時一次列出）。"""
    return [(v, d, n) for v, d, n in all_notes(remote or {}) if parse_version(v) > parse_version(current)]


def disk_version(root=None):
    """資料夾裡 release.json 現在寫的版本（覆蓋新檔案後可能和執行中的程式不同）。"""
    return read_release(os.path.join(root or TOOL_ROOT, "release.json")).get("version", "0")


def manifest_url(repo, branch="main"):
    return f"https://raw.githubusercontent.com/{repo}/{branch}/release.json"


def zip_url(repo, branch="main"):
    return f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"


def is_dev_folder(root=None):
    return os.path.isdir(os.path.join(root or TOOL_ROOT, ".git"))


def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": "LowTempAnalysis-updater",
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------------------------------------------------------------------
# 檢查
# ---------------------------------------------------------------------------
def remote_commit(repo, branch="main", timeout=8):
    """GitHub 上最新的 commit → (sha, 第一行說明)；讀不到回傳 (None, "")。"""
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{branch}",
                                     headers={"User-Agent": "LowTempAnalysis-updater",
                                              "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("sha"), (data.get("commit", {}).get("message", "") or "").strip()
    except Exception:
        return None, ""


def installed_commit(root=None):
    root = root or TOOL_ROOT
    try:
        with open(os.path.join(root, INSTALLED_FILE), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write_installed(sha, root=None):
    root = root or TOOL_ROOT
    if sha and not is_dev_folder(root):
        try:
            with open(os.path.join(root, INSTALLED_FILE), "w", encoding="utf-8") as f:
                f.write(sha)
        except OSError:
            pass


def check(timeout=8, url=None):
    """回傳 {current, latest, newer, notes, repo, branch, remote}；沒設定儲存庫時 newer=False。"""
    local = read_release(os.path.join(TOOL_ROOT, "release.json"))
    repo, branch = local.get("repo", ""), local.get("branch", "main")
    info = {"current": local.get("version", "0"), "latest": None, "newer": False, "notes": [],
            "repo": repo, "branch": branch, "remote": None}
    if not repo and not url:
        info["error"] = "尚未設定 GitHub 儲存庫（release.json 的 repo）"
        return info
    use_github = url is None
    url = url or manifest_url(repo, branch)
    remote = json.loads(_get(url + ("&" if "?" in url else "?") + f"t={int(time.time())}"
                             if url.startswith("http") else url, timeout).decode("utf-8"))
    info.update(latest=remote.get("version", "0"), notes=remote.get("notes", []), remote=remote,
                newer=parse_version(remote.get("version", "0")) > parse_version(info["current"]))
    info["since"] = notes_since(remote, info["current"])
    # 版本號沒變、但 GitHub 上有新的修改（例如直接用 GitHub Desktop 推上去）→ 也算有更新
    if not info["newer"] and use_github and not is_dev_folder():
        sha, msg = remote_commit(repo, branch, timeout)
        info["commit"], info["commit_msg"] = sha, msg
        mine = installed_commit()
        same_version = parse_version(info["latest"]) == parse_version(info["current"])
        if sha and mine is None and same_version:
            _write_installed(sha)                       # 第一次：當作目前就是 GitHub 上這一版
        elif sha and mine and sha != mine and same_version:
            info.update(newer=True, by_commit=True, latest=info["current"],
                        notes=[l for l in msg.splitlines() if l.strip()][:8] or ["GitHub 上有新的修改"])
    return info


# ---------------------------------------------------------------------------
# 更新
# ---------------------------------------------------------------------------
def _find_root(folder):
    """在解壓縮的資料夾裡找含有 lta/__main__.py 的那一層（GitHub 的 zip 會多包一層 repo-branch/）。"""
    for dirpath, dirs, files in os.walk(folder):
        if os.path.isfile(os.path.join(dirpath, "lta", "__main__.py")):
            return dirpath
        if dirpath.count(os.sep) - folder.count(os.sep) > 2:
            dirs[:] = []
    return None


def _copy_item(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"), dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _remove(path):
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        os.remove(path)


def apply_update(info=None, log=print, zip_source=None, root=TOOL_ROOT):
    """下載並安裝新版。回傳新版本號。zip_source 可以是網址或本機 zip 路徑（測試用）。"""
    if is_dev_folder(root):
        raise RuntimeError("這是開發用資料夾（有 .git），請用 git pull 更新，不會自動覆蓋")
    local = read_release(os.path.join(root, "release.json"))
    if zip_source is None:
        if not local.get("repo"):
            raise RuntimeError("尚未設定 GitHub 儲存庫")
        zip_source = zip_url(local["repo"], local.get("branch", "main"))
    tmp = tempfile.mkdtemp(prefix="lta_update_")
    try:
        log("下載新版本…")
        zpath = os.path.join(tmp, "update.zip")
        if os.path.isfile(zip_source):
            shutil.copy2(zip_source, zpath)
        else:
            with open(zpath, "wb") as f:
                f.write(_get(zip_source, timeout=60))
        verify_download(zpath, root, local.get("version", "0"), log)
        log("解壓縮…")
        with zipfile.ZipFile(zpath) as z:
            z.extractall(os.path.join(tmp, "x"))
        new_root = _find_root(os.path.join(tmp, "x"))
        if not new_root:
            raise RuntimeError("下載的檔案裡找不到程式（lta\\__main__.py）")
        new_rel = read_release(os.path.join(new_root, "release.json"))
        new_ver = new_rel.get("version", "0")
        if not new_rel.get("repo") and local.get("repo"):
            new_rel["repo"] = local["repo"]                    # 保留更新來源

        # ---- 備份目前版本 ----
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = os.path.join(root, "_backup", f"v{local.get('version', '0')}_{stamp}")
        os.makedirs(backup, exist_ok=True)
        entries = [e for e in os.listdir(new_root) if e not in PRESERVE]
        log(f"備份目前版本到 {backup}")
        for e in entries:
            cur = os.path.join(root, e)
            if os.path.exists(cur):
                if e == "lta":
                    try:
                        shutil.move(cur, os.path.join(backup, e))  # 整個搬走，避免留下已刪除的舊模組
                    except OSError:                                # 搬不動（被占用）就複製一份再盡量刪
                        _copy_item(cur, os.path.join(backup, e))
                        shutil.rmtree(cur, ignore_errors=True)
                else:
                    _copy_item(cur, os.path.join(backup, e))

        # ---- 安裝新版 ----
        log(f"安裝 v{new_ver}…")
        for e in entries:
            dst = os.path.join(root, e)
            _remove(dst)
            _copy_item(os.path.join(new_root, e), dst)
        # jobs\ 只更新範本，不碰使用者的設定檔
        new_jobs = os.path.join(new_root, "jobs")
        if os.path.isdir(new_jobs):
            os.makedirs(os.path.join(root, "jobs"), exist_ok=True)
            for fn in os.listdir(new_jobs):
                dst = os.path.join(root, "jobs", fn)
                if fn.startswith("_") or not os.path.exists(dst):
                    shutil.copy2(os.path.join(new_jobs, fn), dst)
        write_release(new_rel, os.path.join(root, "release.json"))
        sha = (info or {}).get("commit") or remote_commit(local.get("repo", ""), local.get("branch", "main"))[0] \
            if local.get("repo") else None
        _write_installed(sha, root)
        _prune_backups(os.path.join(root, "_backup"))
        log(f"完成：已更新到 v{new_ver}")
        return new_ver
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify_download(zpath, root, current_version, log=print):
    """用這台電腦上已經有的公鑰驗證下載的更新；還沒有公鑰（舊版）就跳過。失敗丟 RuntimeError，不會安裝。"""
    from . import signing
    public = signing.read_public_key(root)
    if public is None:
        log("（這台電腦的版本還沒有簽章驗證，這次直接安裝；之後的更新都會驗證）")
        return None
    try:
        body = signing.verify_files(signing.zip_files(zpath), public)
    except ValueError as e:
        raise RuntimeError(f"更新的簽章驗證失敗，為了安全沒有安裝。\n原因：{e}\n"
                           "可能是發佈的人還沒簽章就上傳（例如直接用 GitHub Desktop 推送）。請通知維護者。")
    if parse_version(body.get("version", "0")) < parse_version(current_version):
        raise RuntimeError(f"下載到的是比較舊的版本（v{body.get('version')}），沒有安裝")
    log(f"✓ 簽章驗證通過（v{body.get('version')}，{body.get('signed', '')} 簽章）")
    return body


def _prune_backups(folder, keep=KEEP_BACKUPS):
    try:
        items = sorted((os.path.join(folder, d) for d in os.listdir(folder)), key=os.path.getmtime)
    except OSError:
        return
    for old in items[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def list_backups(root=TOOL_ROOT):
    folder = os.path.join(root, "_backup")
    try:
        return sorted((os.path.join(folder, d) for d in os.listdir(folder)), key=os.path.getmtime,
                      reverse=True)
    except OSError:
        return []


def rollback(log=print, root=TOOL_ROOT):
    """還原到最近一次的備份。"""
    backups = list_backups(root)
    if not backups:
        raise RuntimeError("沒有可以還原的備份")
    b = backups[0]
    for e in os.listdir(b):
        dst = os.path.join(root, e)
        _remove(dst)
        _copy_item(os.path.join(b, e), dst)
    shutil.rmtree(b, ignore_errors=True)
    log(f"已還原：{os.path.basename(b)}")
    return os.path.basename(b)


# ---------------------------------------------------------------------------
# 發佈（開發者）
# ---------------------------------------------------------------------------
def release(version=None, notes=None, repo=None, branch=None, push=False, log=print):
    """更新 release.json（版本、更新內容、儲存庫），--push 時用 git commit + push。"""
    rel = read_release()
    if repo:
        rel["repo"] = repo.strip().removeprefix("https://github.com/").strip("/")
    if branch:
        rel["branch"] = branch
    rel.setdefault("branch", "main")
    if version:
        push_history(rel)
        if parse_version(version) <= parse_version(rel.get("version", "0")):
            log(f"注意：新版本 {version} 沒有比目前的 {rel.get('version')} 大，使用者不會收到更新")
        rel["version"] = version.lstrip("vV")
        rel["date"] = time.strftime("%Y-%m-%d")
    if notes is not None:
        rel["notes"] = notes
    write_release(rel)
    log(f"release.json：版本 {rel.get('version')}，儲存庫 {rel.get('repo') or '（未設定）'}，"
        f"分支 {rel.get('branch')}")
    for n in rel.get("notes", []):
        log(f"  ‧ {n}")
    if not push:
        log("\n要發佈給其他人，請把整個資料夾 commit 並 push 到 GitHub（或加上 --push 自動執行）。")
        return rel
    if not is_dev_folder():
        log("\n這個資料夾還不是 git 儲存庫，請先照「發佈與更新.md」第 1 步設定。")
        return rel

    def git(*args):
        log("$ git " + " ".join(args))
        out = subprocess.run([find_git() or "git", *args], cwd=TOOL_ROOT, capture_output=True, text=True)
        if out.stdout.strip():
            log(out.stdout.strip())
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or f"git {args[0]} 失敗")
    git("add", "-A")
    msg = f"v{rel.get('version')}" + (": " + "; ".join(rel.get("notes", [])) if rel.get("notes") else "")
    try:
        git("commit", "-m", msg)
    except RuntimeError as e:
        if "nothing to commit" not in str(e):
            raise
    git("push", "origin", rel.get("branch", "main"))
    log(f"\n已發佈 v{rel.get('version')}。其他人下次開啟軟體就會收到更新通知。")
    return rel


# ---------------------------------------------------------------------------
# 一鍵發佈（publish.bat / 設定 → 更新 → 發佈到 GitHub）
# ---------------------------------------------------------------------------
def find_git():
    """找 git：PATH → Git for Windows → GitHub Desktop 內建的 git。"""
    import glob
    import shutil
    path = shutil.which("git")
    if path:
        return path
    cands = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, "Git", "cmd", "git.exe"))
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        cands.append(os.path.join(local, "Programs", "Git", "cmd", "git.exe"))
        cands += sorted(glob.glob(os.path.join(local, "GitHubDesktop", "app-*", "resources", "app", "git",
                                               "cmd", "git.exe")), reverse=True)
    return next((c for c in cands if os.path.isfile(c)), None)


def bump_patch(version):
    parts = list(parse_version(version)) + [0, 0, 0]
    major, minor, patch = parts[:3]
    return f"{major}.{minor}.{patch + 1}"


PERSONAL_FILES = ("settings.toml", "catalog.csv", "catalog.xlsx", ".catalog_cache.json", "release_signing_key.txt")


def run_selftest(root, log=print, python=None):
    """在工具資料夾跑 python -m lta selftest（獨立的行程，用的是資料夾裡的新程式）。回傳 True/False。"""
    import sys as _sys
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MPLBACKEND="Agg")
    proc = subprocess.Popen([python or _sys.executable, "-m", "lta", "selftest"], cwd=root, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    for line in proc.stdout:
        log(line.rstrip())
    return proc.wait() == 0


def _git_runner(git, root, log):
    def run(*args, check=True, quiet=False, extra=()):
        out = subprocess.run([git, *extra, *args], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if not quiet and out.stdout.strip():
            log(out.stdout.strip())
        if check and out.returncode != 0:
            raise RuntimeError((out.stderr or out.stdout).strip() or f"git {args[0]} 失敗")
        return out
    return run


def _identity(run, rel):
    if run("config", "user.email", check=False, quiet=True).returncode != 0:
        owner = (rel.get("repo") or "user/x").split("/")[0]
        return ("-c", f"user.name={owner}", "-c", f"user.email={owner}@users.noreply.github.com")
    return ()


def ensure_signing_key(root, log=print):
    """拿到私鑰；第一次發佈時自動產生（並把公鑰寫進 lta/release_key.pub）。回傳私鑰。"""
    from . import signing
    secret = signing.load_private_key()
    public = signing.read_public_key(root)
    if secret is None:
        if public is not None:
            raise RuntimeError(
                f"找不到發佈用的私鑰：{signing.key_path()}\n"
                "這個私鑰之前已經用來簽章，同事的軟體只接受它簽的更新。請從備份放回這個位置再發佈。\n"
                "（真的遺失了：刪掉 lta\\release_key.pub 會產生新的一組，但同事必須手動重新安裝一次）")
        secret, public = signing.new_private_key()
        signing.write_public_key(root, public)
        log("\n★ 第一次發佈：已產生發佈用的簽章金鑰")
        log(f"  私鑰（只在這台電腦，不會上傳）：{signing.key_path()}")
        log("  請把這個檔案另外備份（隨身碟、密碼管理器）；換電腦發佈時要把它放回同一個位置。")
        log(f"  公鑰已寫入 {signing.PUBKEY_REL}，會跟著程式發給同事。\n")
        return secret
    if public is not None and signing.public_key(secret) != public:
        raise RuntimeError(f"私鑰和程式裡的公鑰（{signing.PUBKEY_REL}）不是同一組：{signing.key_path()}")
    if public is None:
        signing.write_public_key(root, signing.public_key(secret))
    return secret


def head_files(git, root, ref="HEAD"):
    """用 git archive 取得某個 commit 的所有檔案（和 GitHub 下載的 zip 內容相同）。"""
    from . import signing
    tmp = tempfile.mkdtemp(prefix="lta_sign_")
    try:
        zpath = os.path.join(tmp, "head.zip")
        out = subprocess.run([git, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=zip",
                              "-o", zpath, ref], cwd=root, capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        if out.returncode != 0:
            raise RuntimeError(out.stderr.strip() or "git archive 失敗")
        return signing.zip_files(zpath)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def is_signed(git, root, ref="HEAD"):
    from . import signing
    public = signing.read_public_key(root)
    if public is None:
        return False
    try:
        signing.verify_files(head_files(git, root, ref), public)
        return True
    except (ValueError, RuntimeError):
        return False


def sign_head(run, git, root, rel, secret, log=print):
    """對目前的 commit 簽章：寫入 release_manifest.json / .sig 並另外 commit 一次。"""
    from . import signing
    files = head_files(git, root)
    manifest, sig = signing.sign_files(files, rel.get("version", "0"), secret)
    with open(os.path.join(root, signing.MANIFEST), "wb") as f:
        f.write(manifest)
    with open(os.path.join(root, signing.SIGNATURE), "w", encoding="ascii", newline="\n") as f:
        f.write(sig)
    run("add", signing.MANIFEST, signing.SIGNATURE)
    body = "\n".join("- " + n for n in rel.get("notes", []))
    run("commit", "-m", f"v{rel.get('version', '0')} 簽章", *(("-m", body) if body else ()),
        extra=_identity(run, rel), quiet=True)
    if not is_signed(git, root):
        raise RuntimeError("簽章後自我驗證失敗（不應該發生），沒有上傳")
    log(f"✓ 已簽章：{len(files) - 2 if signing.MANIFEST in files else len(files)} 個檔案")


def pending_info(timeout=8, root=None):
    """GitHub 上 pending 分支比 main 多了什麼：{ahead, version, notes, sha, checks}；沒有 pending 回傳 None。"""
    root = root or TOOL_ROOT
    rel = read_release(os.path.join(root, "release.json"))
    repo, branch = rel.get("repo", ""), rel.get("branch", "main")
    pend = rel.get("pending_branch", "pending")
    if not repo:
        return None
    try:
        cmp = json.loads(_get(f"https://api.github.com/repos/{repo}/compare/{branch}...{pend}", timeout))
    except Exception:
        return None
    ahead = int(cmp.get("ahead_by", 0))
    if ahead <= 0:
        return {"ahead": 0}
    info = {"ahead": ahead, "sha": (cmp.get("commits") or [{}])[-1].get("sha"),
            "messages": [c.get("commit", {}).get("message", "").splitlines()[0] for c in cmp.get("commits", [])]}
    try:
        prel = json.loads(_get(manifest_url(repo, pend) + f"?t={int(time.time())}", timeout).decode("utf-8"))
        info.update(version=prel.get("version"), notes=prel.get("notes", []))
    except Exception:
        pass
    info["checks"] = ci_status(repo, info.get("sha"), timeout)
    return info


def ci_status(repo, sha, timeout=8):
    """GitHub Actions 測試結果：'success' / 'failure' / 'pending' / None（沒有測試或讀不到）。"""
    if not repo or not sha:
        return None
    try:
        data = json.loads(_get(f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs", timeout))
    except Exception:
        return None
    runs = data.get("check_runs", [])
    if not runs:
        return None
    if any(r.get("status") != "completed" for r in runs):
        return "pending"
    if all(r.get("conclusion") in ("success", "skipped", "neutral") for r in runs):
        return "success"
    return "failure"


def receive(log=print, ask=input, root=None, test=True):
    """收更新：把 Claude 推到 pending 分支的更新收下來 → 自我測試 → 簽章 → 發佈到 main。

    · 不會動到你資料夾裡還沒發佈的修改（有的話會先停下來請你處理）
    · 測試沒過就還原，main 不會變"""
    root = root or TOOL_ROOT
    if not os.path.isdir(os.path.join(root, ".git")):
        log("這個資料夾不是用 GitHub Desktop 複製（Clone）下來的，沒辦法收更新。請照「發佈與更新.md」設定。")
        return False
    git = find_git()
    if not git:
        log("找不到 git。請安裝 GitHub Desktop（https://desktop.github.com）或 Git for Windows（https://git-scm.com）。")
        return False
    run = _git_runner(git, root, log)
    rel = read_release(os.path.join(root, "release.json"))
    branch, pend = rel.get("branch", "main"), rel.get("pending_branch", "pending")
    log(f"工具資料夾：{root}")
    log("讀取 GitHub 上的待發佈更新…")
    if run("fetch", "origin", branch, check=False, quiet=True).returncode != 0:
        log("連不上 GitHub（git fetch 失敗）。請確認網路，或先打開 GitHub Desktop 登入一次。")
        return False
    if run("fetch", "origin", pend, check=False, quiet=True).returncode != 0:
        log(f"\nGitHub 上沒有 {pend} 分支，目前沒有待發佈的更新。")
        return True
    changes = [l for l in run("status", "--porcelain", "--untracked-files=no", quiet=True).stdout.splitlines()
               if l.strip()]
    cache = [l[3:].strip().strip('"') for l in changes if "__pycache__" in l or l.rstrip().endswith(".pyc")]
    if cache:                                       # Python 自動產生的編譯檔：直接還原，不算你的修改
        run("checkout", "--", *cache, check=False, quiet=True)
        changes = [l for l in changes if "__pycache__" not in l and not l.rstrip().endswith(".pyc")]
    if changes:
        log("\n你的資料夾有還沒發佈的修改，為了不蓋掉它們，先停在這裡：")
        for l in changes[:15]:
            log("  " + l)
        log("→ 要保留：先執行 publish.bat 發佈；不要了：在 GitHub Desktop 對那些檔案按右鍵「Discard changes」。")
        return False
    cur = run("rev-parse", "--abbrev-ref", "HEAD", quiet=True).stdout.strip()
    if cur != branch:
        run("checkout", branch, quiet=True)
    if run("merge", "--ff-only", f"origin/{branch}", check=False, quiet=True).returncode != 0:
        log(f"本機的 {branch} 和 GitHub 上的不一致（兩邊各有不同的修改）。請在 GitHub Desktop 按 Pull 處理後再試。")
        return False
    n = run("rev-list", "--count", f"HEAD..origin/{pend}", quiet=True).stdout.strip()
    if n in ("", "0"):
        log("\n✓ 沒有待發佈的更新（pending 已經都發佈了）。")
        return True
    try:
        prel = json.loads(run("show", f"origin/{pend}:release.json", quiet=True).stdout)
    except (ValueError, RuntimeError):
        prel = {}
    log(f"\n待發佈：v{prel.get('version', '?')}（目前 v{rel.get('version', '?')}），{n} 個修改")
    for note in prel.get("notes", []):
        log(f"  · {note}")
    sha = run("rev-parse", f"origin/{pend}", quiet=True).stdout.strip()
    ci = ci_status(rel.get("repo", ""), sha)
    log({"success": "GitHub 測試（Windows、Mac）：✓ 通過", "failure": "GitHub 測試：✗ 沒有通過",
         "pending": "GitHub 測試：還在跑"}.get(ci, "GitHub 測試：讀不到結果"))
    if ci == "failure":
        if (ask("\nGitHub 上的測試沒有通過，建議先不要發佈。仍要繼續請輸入 yes，直接 Enter = 取消：") or "").strip().lower() != "yes":
            log("已取消。")
            return False
    elif (ask("\n收下並發佈？（直接 Enter = 是，輸入 n = 取消）：") or "").strip().lower() in ("n", "no"):
        log("已取消。")
        return False
    old = run("rev-parse", "HEAD", quiet=True).stdout.strip()
    if run("merge", "--ff-only", f"origin/{pend}", check=False, quiet=True).returncode != 0:
        log(f"{pend} 不是接在目前的 {branch} 後面（可能 main 之後又有新的發佈）。請告訴 Claude「pending 需要重新整理」。")
        return False
    rel = read_release(os.path.join(root, "release.json"))

    def undo(reason):
        run("reset", "--hard", old, check=False, quiet=True)
        log(f"\n{reason}，已還原，main 沒有改變、也沒有上傳。")

    if test:
        log("\n--- 自我測試（約 15–40 秒）---")
        if not run_selftest(root, log):
            undo("自我測試沒有通過")
            log("把上面 ✗ 的錯誤訊息貼給 Claude 就可以修正。")
            return False
    try:
        secret = ensure_signing_key(root, log)
        from . import signing
        if run("status", "--porcelain", "--", signing.PUBKEY_REL, quiet=True).stdout.strip():
            run("add", signing.PUBKEY_REL)
            run("commit", "-m", "加入發佈公鑰", extra=_identity(run, rel), quiet=True)
        sign_head(run, git, root, rel, secret, log)
    except RuntimeError as e:
        undo(f"簽章失敗：{e}")
        return False
    try:
        run("push", "origin", branch)
    except RuntimeError as e:
        log(f"\n上傳到 GitHub 失敗：{e}")
        log("→ 已經收好、測試、簽章完成。打開 GitHub Desktop 按「Push origin」就完成。")
        return False
    log(f"\n✓ 已發佈 v{rel.get('version')} 到 GitHub（{rel.get('repo')}），同事幾分鐘內會收到更新提示。")
    return True


def publish(log=print, ask=input, root=None, test=True):
    """把工具資料夾發佈到 GitHub：不用開 GitHub Desktop、不用打版本號。

    · 版本號用 release.json 的（我給你的更新檔已經改好）
    · 你自己改了程式、版本號沒變 → 自動 +0.0.1，並問一句更新說明（直接 Enter 也可以）
    · 不小心被追蹤的個人檔案（settings.toml、資料總表）會停止上傳，但留在你的電腦上
    推送失敗時（例如還沒登入）：變更已經存好，打開 GitHub Desktop 按「Push origin」即可。"""
    root = root or TOOL_ROOT
    if not os.path.isdir(os.path.join(root, ".git")):
        log("這個資料夾不是用 GitHub Desktop 複製（Clone）下來的，沒辦法直接發佈。")
        log("請照「發佈與更新.md」第 1 步，用 GitHub Desktop Clone 一份，以後都在那個資料夾裡操作。")
        return False
    git = find_git()
    if not git:
        log("找不到 git。請安裝 GitHub Desktop（https://desktop.github.com）或 Git for Windows（https://git-scm.com）。")
        return False

    run = _git_runner(git, root, log)
    rel = read_release(os.path.join(root, "release.json"))
    branch = rel.get("branch", "main")
    log(f"工具資料夾：{root}")
    log(f"使用的 git：{git}")
    run("fetch", "origin", branch, check=False, quiet=True)
    changes = [l for l in run("status", "--porcelain", quiet=True).stdout.splitlines() if l.strip()]
    remote = None
    try:
        remote = check(timeout=8).get("latest")
    except Exception as e:
        log(f"（讀不到 GitHub 上的版本：{e}，照本機 release.json 發佈）")
    local = rel.get("version", "0")
    log(f"本機版本 v{local}　GitHub 上 v{remote or '?'}　有變更的檔案：{len(changes)} 個")

    if remote and parse_version(local) <= parse_version(remote):
        if not changes:
            ahead = run("rev-list", "--count", f"origin/{branch}..HEAD", check=False, quiet=True).stdout.strip()
            if ahead and ahead != "0":
                log("有已經存好但還沒上傳的版本，直接上傳。")
            elif not is_signed(git, root):
                log("GitHub 上的版本還沒有簽章，補簽章後上傳。")
            else:
                log(f"\nGitHub 上已經是 v{remote}，而且沒有新的變更，不需要發佈。")
                return True
        else:
            new = bump_patch(remote)
            note = (ask(f"\n版本號會改成 v{new}。這一版改了什麼？（直接 Enter = 小幅修正）：") or "").strip()
            push_history(rel)
            rel["version"] = new
            rel["date"] = time.strftime("%Y-%m-%d")
            rel["notes"] = [note or "小幅修正"]
            write_release(rel, os.path.join(root, "release.json"))
            log(f"release.json → v{new}")

    if test:
        log("\n--- 上傳前自我測試（約 15–40 秒）---")
        if not run_selftest(root, log):
            ans = (ask("\n自我測試沒有通過，所以還沒上傳（避免把壞掉的版本推給同事）。\n"
                       "確定要強制上傳請輸入 yes，直接 Enter = 取消：") or "").strip().lower()
            if ans != "yes":
                log("已取消，沒有上傳。把上面 ✗ 的錯誤訊息貼給 Claude 就可以修正。")
                return False
            log("強制上傳。")
        log("")

    for f in PERSONAL_FILES:
        if run("ls-files", "--error-unmatch", f, check=False, quiet=True).returncode == 0:
            run("rm", "--cached", "--quiet", f, check=False)
            log(f"停止上傳 {f}（檔案仍留在你的電腦）")

    try:
        secret = ensure_signing_key(root, log)
    except RuntimeError as e:
        log(f"\n{e}")
        log("沒有上傳。")
        return False
    ident = _identity(run, rel)
    run("add", "-A")
    msg = f"v{rel['version']}"
    body = "\n".join("- " + n for n in rel.get("notes", []))
    out = run("commit", "-m", msg, *(("-m", body) if body else ()), check=False, extra=ident)
    if out.returncode != 0 and "nothing to commit" not in (out.stdout + out.stderr):
        raise RuntimeError(out.stderr.strip() or "commit 失敗")
    log(f"\n已存好：{msg}")
    if not is_signed(git, root):
        sign_head(run, git, root, rel, secret, log)
    try:
        run("push", "origin", branch)
    except RuntimeError as e:
        log(f"\n上傳到 GitHub 失敗：{e}")
        log("→ 變更已經存好了。打開 GitHub Desktop，按上方的「Push origin」就完成（不用再填 Summary）。")
        log("  以後想完全一鍵：安裝 Git for Windows（https://git-scm.com），第一次上傳會跳出瀏覽器登入 GitHub。")
        return False
    log(f"\n✓ 已發佈 v{rel['version']} 到 GitHub（{rel.get('repo')}）。")
    log("  同事開啟軟體時、或執行中每 30 分鐘會檢查到；GitHub 快取約 5 分鐘，最慢幾分鐘內就會跳出更新提示。")
    return True
