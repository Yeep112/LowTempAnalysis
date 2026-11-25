"""介面配色與字型。想換顏色改這裡就好。"""
from __future__ import annotations

import matplotlib
from cycler import cycler

# ---- 配色 ----
BG = "#eef2f8"           # 視窗底色
CARD = "#ffffff"         # 卡片
BORDER = "#dfe5ef"
HEADER = "#18223d"       # 頂部深藍
HEADER_2 = "#253257"     # 頂部按鈕
HEADER_TEXT = "#ffffff"
HEADER_MUTED = "#9fb0d6"
TEXT = "#1f2937"
MUTED = "#6b7280"
FAINT = "#9ca3af"
HOVER = "#f3f6fc"
STRIPE = "#f8fafd"
SUBTLE = "#f6f8fc"       # 樣式列等淡底
FIELD = "white"          # 輸入框
CHIP = "#f1f4f9"         # 小標籤底色
TEXT_BG = "#f8fafc"      # 文字結果底色
DARK = False             # 深色版面
SUCCESS = "#16a34a"
WARNING = "#d97706"
ERROR = "#dc2626"

# 1D / 2D 各一個主色，切換分頁時整個介面跟著換色
ACCENTS = {
    "1D": dict(main="#2563eb", dark="#1d4ed8", soft="#e6eeff", text="#1e40af", on="#ffffff"),
    "2D": dict(main="#7c3aed", dark="#6d28d9", soft="#efe7ff", text="#5b21b6", on="#ffffff"),
}

# 清單上量測模式的顏色
MODE_COLORS = {"current_bias": "#1d4ed8", "voltage_bias": "#047857", "generic": "#6b7280",
               "2D": "#6d28d9"}

# 圖的顏色循環
PLOT_CYCLE = ["#2563eb", "#e4572e", "#17a398", "#f2a541", "#7c3aed", "#ef476f",
              "#118ab2", "#8d6a9f", "#06d6a0", "#5c6b73"]

FONT_FAMILY = "TkDefaultFont"
MONO_FAMILY = "Consolas"


def pick_fonts(tkfont):
    """依系統可用字型挑中文介面字型與等寬字型。"""
    global FONT_FAMILY, MONO_FAMILY
    fams = set(tkfont.families())
    for f in ("Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC",
              "Noto Sans TC", "Segoe UI"):
        if f in fams:
            FONT_FAMILY = f
            break
    for f in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono"):
        if f in fams:
            MONO_FAMILY = f
            break


def mix_color(c1, c2, t):
    from .colors import mix
    return mix(c1, c2, t)


def font(size=10, weight="normal"):
    return (FONT_FAMILY, size, weight)


def setup_ttk(style, accent):
    """ttk 元件樣式（clam 主題可以自由上色）。切換 1D/2D 時重新呼叫以更新主色。"""
    a = ACCENTS[accent]
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", background=CARD, foreground=TEXT, font=font(10), bordercolor=BORDER,
                    focuscolor=a["main"])
    style.configure("TFrame", background=CARD)
    style.configure("TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=font(9))
    style.configure("TCheckbutton", background=CARD, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", CARD)],
              indicatorcolor=[("selected", a["main"]), ("!selected", FIELD)])
    style.configure("TRadiobutton", background=CARD, foreground=TEXT)
    style.map("TRadiobutton", background=[("active", CARD)],
              indicatorcolor=[("selected", a["main"]), ("!selected", FIELD)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, padding=(6, 4))
    style.map("TEntry", bordercolor=[("focus", a["main"])], lightcolor=[("focus", a["main"])])
    style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED, padding=(6, 3))
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
              bordercolor=[("focus", a["main"])], selectbackground=[("readonly", FIELD)],
              selectforeground=[("readonly", TEXT)])

    # 按鈕
    style.configure("Accent.TButton", background=a["main"], foreground=a.get("on", "white"), borderwidth=0,
                    focusthickness=0, padding=(16, 8), font=font(10, "bold"))
    style.map("Accent.TButton", background=[("pressed", a["dark"]), ("active", a["dark"])])
    style.configure("Ghost.TButton", background=CARD, foreground=a["text"], borderwidth=1,
                    bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD, padding=(10, 7))
    style.map("Ghost.TButton", background=[("active", a["soft"])],
              bordercolor=[("active", a["main"])])
    style.configure("Small.TButton", background=CARD, foreground=MUTED, borderwidth=1,
                    bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD, padding=(8, 3), font=font(9))
    style.map("Small.TButton", background=[("active", a["soft"])], foreground=[("active", a["text"])])
    style.configure("Header.TButton", background=HEADER_2, foreground=HEADER_TEXT, borderwidth=0,
                    padding=(12, 6))
    style.map("Header.TButton", background=[("active", a["main"]), ("pressed", a["dark"])])

    # 清單
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    rowheight=26, borderwidth=0, font=font(10))
    style.map("Treeview", background=[("selected", a["soft"])], foreground=[("selected", a["text"])])
    style.configure("Treeview.Heading", background=SUBTLE, foreground=MUTED, relief="flat",
                    borderwidth=0, font=font(9, "bold"), padding=(4, 5))
    style.map("Treeview.Heading", background=[("active", a["soft"])])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=BORDER, troughcolor=CARD, borderwidth=0,
                        arrowcolor=MUTED, gripcount=0)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=SUBTLE, foreground=MUTED, padding=(12, 5))
    style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", a["text"])])
    style.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT, arrowcolor=MUTED, bordercolor=BORDER)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, background=BG)
    return a


def _windows_cjk_fonts():
    """直接把 Windows 的中文字型檔加進 matplotlib（舊版 matplotlib 不會自動讀 .ttc）。"""
    import os
    from matplotlib import font_manager
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = []
    for fn in ("msjh.ttc", "msjhbd.ttc", "msjh.ttf", "msyh.ttc", "msyhbd.ttc", "mingliu.ttc",
               "kaiu.ttf", "simhei.ttf"):
        path = os.path.join(fonts_dir, fn)
        if os.path.exists(path):
            try:
                font_manager.fontManager.addfont(path)
                name = font_manager.FontProperties(fname=path).get_name()
                if name not in names:
                    names.append(name)
            except Exception:
                pass
    return names


PLOT_CJK_FONT = ""        # 使用者在設定裡選的圖中中文字型（空白 = 自動）
_CJK_HINTS = ("JhengHei", "YaHei", "MingLiU", "PMingLiU", "DFKai", "KaiTi", "SimHei", "SimSun", "Ming",
              "CJK", "PingFang", "Heiti", "Hiragino", "Noto Sans TC", "Noto Sans SC", "Source Han",
              "Arial Unicode", "Microsoft JhengHei", "Yu Gothic", "Meiryo", "Malgun")


def cjk_font_candidates():
    """matplotlib 找得到、看起來有中文字的字型名稱。"""
    from matplotlib import font_manager
    _windows_cjk_fonts()
    names = sorted({f.name for f in font_manager.fontManager.ttflist
                    if any(h.lower() in f.name.lower() for h in _CJK_HINTS)})
    pref = ["Microsoft JhengHei", "Microsoft JhengHei UI", "Microsoft YaHei", "PMingLiU", "MingLiU",
            "Noto Sans CJK TC", "PingFang TC"]
    return [n for n in pref if n in names] + [n for n in names if n not in pref]


def current_cjk_font():
    """目前畫中文實際會用到的字型。"""
    cands = set(cjk_font_candidates())
    for name in matplotlib.rcParams.get("font.sans-serif", []):
        if name in cands:
            return name
    return ""


def apply_mpl_style():
    rc = matplotlib.rcParams
    from matplotlib import font_manager
    cjk = _windows_cjk_fonts()
    have = {f.name for f in font_manager.fontManager.ttflist}
    cjk += [f for f in ("Microsoft JhengHei", "Microsoft YaHei", "PMingLiU", "PingFang TC", "Noto Sans CJK TC",
                        "Noto Sans CJK JP", "Arial Unicode MS") if f in have and f not in cjk]
    if PLOT_CJK_FONT and PLOT_CJK_FONT in have and PLOT_CJK_FONT not in cjk:
        cjk.append(PLOT_CJK_FONT)
    try:
        major, minor = (int(x) for x in matplotlib.__version__.split(".")[:2])
    except ValueError:
        major, minor = 3, 6
    if (major, minor) >= (3, 6):
        # 字型要「逐一列出名稱」matplotlib 才會逐字備援（寫 "sans-serif" 只會用到一個字型 → 中文變方框）。
        # 英文、符號（⊥ ₀ μ Ω）用 DejaVu Sans，中文自動改用中文字型；使用者指定的字型放最前面。
        order = ([PLOT_CJK_FONT] if PLOT_CJK_FONT in cjk else []) + ["DejaVu Sans"] + \
                [f for f in cjk if f != PLOT_CJK_FONT]
    else:
        # 舊版只會用第一個找得到的字型 → 中文字型放第一個
        order = cjk + ["DejaVu Sans"]
    rc["font.sans-serif"] = order
    rc["font.family"] = order
    rc["axes.unicode_minus"] = False
    rc.update({
        "figure.facecolor": "white", "axes.facecolor": "white",
        "axes.edgecolor": "#a3acb9", "axes.linewidth": 0.9,
        "axes.labelcolor": "#374151", "axes.labelsize": 11, "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.titlecolor": "#1f2937",
        "axes.grid": True, "grid.color": "#e8ecf2", "grid.linewidth": 0.8,
        "axes.prop_cycle": cycler(color=PLOT_CYCLE),
        "xtick.color": "#4b5563", "ytick.color": "#4b5563",
        "xtick.direction": "in", "ytick.direction": "in",
        "lines.linewidth": 1.4, "legend.frameon": False, "legend.fontsize": 8,
        "font.size": 10, "figure.dpi": 100, "savefig.facecolor": "white",
    })


def theme_palette(cfg):
    """設定 → 一整套顏色（dict）。新版：theme_base（一個顏色）；舊版：theme_preset / theme_custom。"""
    from .colors import THEME_PRESETS, SWATCHES, derive_theme, legacy_theme, parse_color
    cfg = cfg or {}
    a1, a2 = cfg.get("theme_accent_1d") or None, cfg.get("theme_accent_2d") or None
    try:
        a1 = parse_color(a1) if a1 else None
        a2 = parse_color(a2) if a2 else None
    except ValueError:
        a1 = a2 = None
    base = cfg.get("theme_base")
    if base:
        try:
            base = parse_color(SWATCHES.get(base, base))
            if base == SWATCHES["預設藍"]:
                th = THEME_PRESETS["藍紫（預設）"]
                return legacy_theme(th["header"], th["bg"], a1 or th["accent_1d"], a2 or th["accent_2d"])
            return derive_theme(base, a1, a2)
        except ValueError:
            pass
    th = dict(THEME_PRESETS["藍紫（預設）"])
    preset = cfg.get("theme_preset")
    if preset in THEME_PRESETS:
        th.update(THEME_PRESETS[preset])
    if preset == "自訂":
        th.update({k: v for k, v in (cfg.get("theme_custom") or {}).items() if v})
    try:
        return legacy_theme(th["header"], th["bg"], a1 or th["accent_1d"], a2 or th["accent_2d"])
    except ValueError:
        th = THEME_PRESETS["藍紫（預設）"]
        return legacy_theme(th["header"], th["bg"], th["accent_1d"], th["accent_2d"])


def apply_theme(cfg):
    """依設定（gui_settings.json）改變上面的配色。要在建立介面之前呼叫（換配色時介面會重建）。"""
    global PLOT_CYCLE, DARK, PLOT_CJK_FONT
    from .colors import parse_color
    cfg = cfg or {}
    PLOT_CJK_FONT = cfg.get("plot_cjk_font") or ""
    g = globals()
    t = theme_palette(cfg)
    for k in ("BG", "CARD", "BORDER", "HEADER", "HEADER_2", "HEADER_TEXT", "HEADER_MUTED", "TEXT", "MUTED",
              "FAINT", "HOVER", "STRIPE", "SUBTLE", "FIELD", "CHIP", "TEXT_BG"):
        g[k] = t[k]
    DARK = t["dark"]
    ACCENTS["1D"] = t["ACCENTS"]["1D"]
    ACCENTS["2D"] = t["ACCENTS"]["2D"]
    MODE_COLORS["2D"] = ACCENTS["2D"]["dark"]
    MODE_COLORS["current_bias"] = ACCENTS["1D"]["dark"]
    cyc = cfg.get("cycle")
    if cyc:
        try:
            PLOT_CYCLE = [parse_color(c) for c in cyc]
        except ValueError:
            pass
