"""發佈簽章：確保同事自動下載的更新，真的是你在自己電腦上發佈的那一版。

  · 私鑰只存在你的電腦：%LOCALAPPDATA%\\LowTempAnalysis\\release_signing_key.txt（不會上傳，請另外備份）
  · 公鑰放在程式裡：lta/release_key.pub（跟著程式發給同事）
  · 每次發佈（publish.bat、收更新.bat）都會產生 release_manifest.json（每個檔案的 SHA-256）
    與 release_manifest.sig（Ed25519 簽章）
  · 同事的軟體下載更新後，先用「自己電腦上已經有的公鑰」驗證，對不上就不安裝

Ed25519 用純 Python 實作（RFC 8032 的參考演算法），不需要安裝任何套件；一次簽章 / 驗證約 0.1 秒。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile

MANIFEST = "release_manifest.json"
SIGNATURE = "release_manifest.sig"
PUBKEY_REL = os.path.join("lta", "release_key.pub")
APP = "LowTempAnalysis"

# ---------------------------------------------------------------------------
# Ed25519（RFC 8032）
# ---------------------------------------------------------------------------
_p = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _H(m):
    return hashlib.sha512(m).digest()


def _recover_x(y, sign):
    if y >= _p:
        return None
    x2 = (y * y - 1) * pow(_d * y * y + 1, _p - 2, _p)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_p + 3) // 8, _p)
    if (x * x - x2) % _p != 0:
        x = x * _I % _p
    if (x * x - x2) % _p != 0:
        return None
    if (x & 1) != sign:
        x = _p - x
    return x


_gy = 4 * pow(5, _p - 2, _p) % _p
_gx = _recover_x(_gy, 0)
_G = (_gx, _gy, 1, _gx * _gy % _p)
_ZERO = (0, 1, 1, 0)


def _add(P, Q):
    A = (P[1] - P[0]) * (Q[1] - Q[0]) % _p
    B = (P[1] + P[0]) * (Q[1] + Q[0]) % _p
    C = 2 * P[3] * Q[3] * _d % _p
    D = 2 * P[2] * Q[2] % _p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _p, G * H % _p, F * G % _p, E * H % _p)


def _mul(s, P):
    Q = _ZERO
    while s > 0:
        if s & 1:
            Q = _add(Q, P)
        P = _add(P, P)
        s >>= 1
    return Q


def _equal(P, Q):
    return (P[0] * Q[2] - Q[0] * P[2]) % _p == 0 and (P[1] * Q[2] - Q[1] * P[2]) % _p == 0


def _compress(P):
    zinv = pow(P[2], _p - 2, _p)
    x, y = P[0] * zinv % _p, P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _p)


def _expand(secret):
    if len(secret) != 32:
        raise ValueError("私鑰長度不對（應該是 32 bytes）")
    h = _H(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, msg: bytes) -> bytes:
    a, prefix = _expand(secret)
    A = _compress(_mul(a, _G))
    r = int.from_bytes(_H(prefix + msg), "little") % _L
    R = _compress(_mul(r, _G))
    h = int.from_bytes(_H(R + A + msg), "little") % _L
    s = (r + h * a) % _L
    return R + int.to_bytes(s, 32, "little")


def verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = _decompress(public)
    R = _decompress(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(_H(signature[:32] + public + msg), "little") % _L
    return _equal(_mul(s, _G), _add(R, _mul(h, A)))


# ---------------------------------------------------------------------------
# 金鑰檔
# ---------------------------------------------------------------------------
def key_path():
    """私鑰位置：環境變數 LTA_SIGNING_KEY，否則 %LOCALAPPDATA%\\LowTempAnalysis\\release_signing_key.txt。"""
    if os.environ.get("LTA_SIGNING_KEY"):
        return os.environ["LTA_SIGNING_KEY"]
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "LowTempAnalysis", "release_signing_key.txt")


def load_private_key(path=None):
    path = path or key_path()
    if not os.path.isfile(path):
        return None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            return bytes.fromhex(line)
    return None


def new_private_key(path=None):
    """產生新的私鑰並存檔；回傳 (私鑰, 公鑰)。"""
    path = path or key_path()
    secret = os.urandom(32)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 低溫量測分析：發佈用私鑰（Ed25519）。不要上傳、不要給別人；請備份到隨身碟或密碼管理器。\n"
                f"# 產生時間 {time.strftime('%Y-%m-%d %H:%M')}；公鑰 {public_key(secret).hex()}\n"
                f"{secret.hex()}\n")
    return secret, public_key(secret)


def read_public_key(root):
    path = os.path.join(root, PUBKEY_REL)
    if not os.path.isfile(path):
        return None
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            return bytes.fromhex(line)
    return None


def write_public_key(root, public):
    with open(os.path.join(root, PUBKEY_REL), "w", encoding="utf-8", newline="\n") as f:
        f.write("# 低溫量測分析的發佈公鑰。同事的軟體用它確認更新是你發佈的；換掉它需要舊的私鑰簽章。\n"
                f"{public.hex()}\n")


# ---------------------------------------------------------------------------
# 清單（每個檔案的 SHA-256）
# ---------------------------------------------------------------------------
def file_hash(data: bytes) -> str:
    """換行一律當成 LF 再算（Windows / GitHub 的換行轉換不影響結果）。"""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def zip_files(zpath):
    """讀 zip（GitHub 下載的 repo-branch.zip 或 git archive），回傳 {相對路徑: bytes}，去掉最上層資料夾。"""
    out = {}
    with zipfile.ZipFile(zpath) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        for n in names:
            rel = n.split("/", 1)[1] if strip else n
            out[rel] = z.read(n)
    return out


def build_manifest(files: dict, version: str) -> bytes:
    body = {"app": APP, "version": version, "signed": time.strftime("%Y-%m-%d %H:%M"),
            "files": {k: file_hash(v) for k, v in sorted(files.items()) if k not in (MANIFEST, SIGNATURE)}}
    return json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True).encode("utf-8") + b"\n"


def sign_files(files: dict, version: str, secret: bytes):
    """回傳 (清單 bytes, 簽章文字)。"""
    manifest = build_manifest(files, version)
    return manifest, sign(secret, manifest).hex() + "\n"


def verify_files(files: dict, public: bytes):
    """驗證一組檔案。成功回傳清單 dict；失敗丟出 ValueError（說明原因）。"""
    if MANIFEST not in files or SIGNATURE not in files:
        raise ValueError("這一版沒有簽章（release_manifest.json / .sig）")
    manifest = files[MANIFEST]
    try:
        sig = bytes.fromhex(files[SIGNATURE].decode("ascii").strip())
    except ValueError:
        raise ValueError("簽章檔格式不對")
    ok = verify(public, manifest, sig) or verify(public, manifest.replace(b"\r\n", b"\n"), sig)
    if not ok:
        raise ValueError("簽章不符：不是用你的私鑰簽的，或清單被改過")
    body = json.loads(manifest.decode("utf-8"))
    listed = body.get("files", {})
    actual = {k: file_hash(v) for k, v in files.items() if k not in (MANIFEST, SIGNATURE)}
    extra = sorted(set(actual) - set(listed))
    missing = sorted(set(listed) - set(actual))
    changed = sorted(k for k in set(actual) & set(listed) if actual[k] != listed[k])
    if extra or missing or changed:
        parts = []
        if changed:
            parts.append("內容和簽章時不同：" + "、".join(changed[:5]))
        if extra:
            parts.append("多出沒簽章的檔案：" + "、".join(extra[:5]))
        if missing:
            parts.append("少了檔案：" + "、".join(missing[:5]))
        raise ValueError("；".join(parts))
    return body
