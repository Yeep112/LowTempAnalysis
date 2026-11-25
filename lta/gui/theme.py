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
SUCCESS = "#16a34a"
WARNING = "#d97706"
ERROR = "#dc2626"

# 1D / 2D 各一個主色，切換分頁時整個介面跟著換色
ACCENTS = {
    "1D": dict(main="#2563eb", dark="#1d4ed8", soft="#e6eeff", text="#1e40af"),
    "2D": dict(main="#7c3aed", dark="#6d28d9", soft="#efe7ff", text="#5b21b6"),
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
              indicatorcolor=[("selected", a["main"]), ("!selected", "white")])
    style.configure("TEntry", fieldbackground="white", bordercolor=BORDER, lightcolor=BORDER,
                    darkcolor=BORDER, padding=(6, 4))
    style.map("TEntry", bordercolor=[("focus", a["main"])], lightcolor=[("focus", a["main"])])
    style.configure("TCombobox", fieldbackground="white", background="white", bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, arrowcolor=MUTED, padding=(6, 3))
    style.map("TCombobox", fieldbackground=[("readonly", "white")],
              bordercolor=[("focus", a["main"])], selectbackground=[("readonly", "white")],
              selectforeground=[("readonly", TEXT)])

    # 按鈕
    style.configure("Accent.TButton", background=a["main"], foreground="white", borderwidth=0,
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
    style.configure("Treeview.Heading", background="#f4f6fb", foreground=MUTED, relief="flat",
                    borderwidth=0, font=font(9, "bold"), padding=(4, 5))
    style.map("Treeview.Heading", background=[("active", a["soft"])])
    style.configure("Vertical.TScrollbar", background="#dde3ee", troughcolor=CARD, borderwidth=0,
                    arrowcolor=MUTED, gripcount=0)
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


def apply_mpl_style():
    rc = matplotlib.rcParams
    from matplotlib import font_manager
    cjk = _windows_cjk_fonts()
    have = {f.name for f in font_manager.fontManager.ttflist}
    cjk += [f for f in ("Microsoft JhengHei", "Microsoft YaHei", "PingFang TC", "Noto Sans CJK TC",
                        "Noto Sans CJK JP", "Arial Unicode MS") if f in have and f not in cjk]
    try:
        major, minor = (int(x) for x in matplotlib.__version__.split(".")[:2])
    except ValueError:
        major, minor = 3, 6
    if (major, minor) >= (3, 6):
        # 新版支援逐字備援：英文用 DejaVu Sans，中文自動改用微軟正黑體
        rc["font.sans-serif"] = ["DejaVu Sans"] + cjk
    else:
        # 舊版只會用第一個找得到的字型 → 中文字型放第一個，才不會變方框
        rc["font.sans-serif"] = cjk + ["DejaVu Sans"]
    rc["font.family"] = "sans-serif"
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


def apply_theme(cfg):
    """依設定（gui_settings.json 的 theme / cycle）改變上面的配色。要在建立介面之前呼叫。"""
    global HEADER, HEADER_2, HEADER_MUTED, BG, PLOT_CYCLE
    from .colors import THEME_PRESETS, shades, mix, parse_color
    cfg = cfg or {}
    th = dict(THEME_PRESETS["藍紫（預設）"])
    preset = cfg.get("theme_preset")
    if preset in THEME_PRESETS:
        th.update(THEME_PRESETS[preset])
    th.update({k: v for k, v in (cfg.get("theme_custom") or {}).items() if v} if preset == "自訂" else {})
    try:
        HEADER = parse_color(th["header"])
        BG = parse_color(th["bg"])
        HEADER_2 = mix(HEADER, "#ffffff", 0.12)
        HEADER_MUTED = mix(HEADER, "#ffffff", 0.62)
        ACCENTS["1D"] = shades(th["accent_1d"])
        ACCENTS["2D"] = shades(th["accent_2d"])
        MODE_COLORS["2D"] = ACCENTS["2D"]["dark"]
    except ValueError:
        pass
    cyc = cfg.get("cycle")
    if cyc:
        try:
            PLOT_CYCLE = [parse_color(c) for c in cyc]
        except ValueError:
            pass
