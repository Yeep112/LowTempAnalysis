"""顏色工具：解析 HEX / RGB、產生主色的深淺變化、介面配色預設組。"""
from __future__ import annotations

import re


def parse_color(text):
    """接受 '#e4572e'、'e4572e'、'#e52'、'rgb(228, 87, 46)'、'228,87,46'、'228 87 46'、
    '0.89, 0.34, 0.18'（0–1 小數）→ 回傳 '#rrggbb'。看不懂就丟出 ValueError。"""
    s = str(text).strip().lower()
    if not s:
        raise ValueError("沒有輸入顏色")
    m = re.fullmatch(r"#?([0-9a-f]{6})", s)
    if m:
        return "#" + m.group(1)
    m = re.fullmatch(r"#?([0-9a-f]{3})", s)
    if m:
        return "#" + "".join(c * 2 for c in m.group(1))
    nums = re.findall(r"[-+]?\d*\.?\d+", s)
    if len(nums) == 3:
        vals = [float(n) for n in nums]
        if all(0 <= v <= 1 for v in vals) and any("." in n for n in nums):
            vals = [v * 255 for v in vals]
        if all(0 <= v <= 255 for v in vals):
            return "#" + "".join(f"{int(round(v)):02x}" for v in vals)
    raise ValueError(f"看不懂的顏色：{text}（請用 #RRGGBB 或 R, G, B）")


def to_rgb(hex_color):
    h = parse_color(hex_color)[1:]
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, int(round(v)))):02x}" for v in rgb)


def mix(c1, c2, t):
    """c1 與 c2 混合，t=0 → c1、t=1 → c2"""
    a, b = to_rgb(c1), to_rgb(c2)
    return to_hex([x + (y - x) * t for x, y in zip(a, b)])


def shades(main):
    """由一個主色產生 main / dark / soft / text 四種變化（介面用）。"""
    main = parse_color(main)
    return dict(main=main, dark=mix(main, "#000000", 0.18), soft=mix(main, "#ffffff", 0.88),
                text=mix(main, "#000000", 0.35))


def rgb_text(hex_color):
    r, g, b = to_rgb(hex_color)
    return f"{r}, {g}, {b}"


# 介面配色預設組：標題列、背景、1D 主色、2D 主色
THEME_PRESETS = {
    "藍紫（預設）": dict(header="#18223d", bg="#eef2f8", accent_1d="#2563eb", accent_2d="#7c3aed"),
    "青綠": dict(header="#0f2f2e", bg="#edf5f4", accent_1d="#0d9488", accent_2d="#0284c7"),
    "暖橘": dict(header="#2c1c14", bg="#f7f2ec", accent_1d="#ea580c", accent_2d="#db2777"),
    "石墨": dict(header="#111827", bg="#f1f2f4", accent_1d="#475569", accent_2d="#6366f1"),
    "森林": dict(header="#14261b", bg="#eff4ef", accent_1d="#15803d", accent_2d="#a16207"),
}

DEFAULT_CYCLE = ["#2563eb", "#e4572e", "#17a398", "#f2a541", "#7c3aed", "#ef476f",
                 "#118ab2", "#8d6a9f", "#06d6a0", "#5c6b73"]
