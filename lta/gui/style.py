"""圖表樣式：格線、各種字體大小。畫完圖之後套用，改設定時不必重新計算。"""
from __future__ import annotations

import json
import os

DEFAULT_STYLE = dict(grid=True, xlabel=12, ylabel=12, cbar=12, ticks=10, legend=8, title=11,
                     layout="左右並排（可捲動）", panel_w=520, panel_h=380)
LAYOUTS = ["左右並排（可捲動）", "上下排列（可捲動）", "縮放填滿視窗"]

# 介面上的欄位：(key, 標籤)
FIELDS = [("xlabel", "X 標籤"), ("ylabel", "Y 標籤"), ("cbar", "色條標籤"),
          ("ticks", "刻度"), ("legend", "圖例"), ("title", "標題")]


def colorbar_axes(fig):
    out = set()
    for ax in fig.axes:
        for coll in list(ax.collections) + list(ax.images):
            cb = getattr(coll, "colorbar", None)
            if cb is not None:
                out.add(cb.ax)
    return out


def apply_style(fig, st):
    """把樣式套到圖上所有座標軸。"""
    st = {**DEFAULT_STYLE, **(st or {})}
    cbars = colorbar_axes(fig)
    for ax in fig.axes:
        if ax in cbars:
            ax.yaxis.label.set_size(st["cbar"])
            ax.xaxis.label.set_size(st["cbar"])
            ax.tick_params(labelsize=st["ticks"])
            continue
        if not ax.axison:
            continue
        is_map = any(getattr(c, "lta_grid", None) is not None for c in ax.collections)
        ax.grid(bool(st["grid"]) and not is_map)
        ax.xaxis.label.set_size(st["xlabel"])
        ax.yaxis.label.set_size(st["ylabel"])
        ax.tick_params(labelsize=st["ticks"])
        if ax.get_title():
            ax.title.set_size(st["title"])
        leg = ax.get_legend()
        if leg is not None:
            for t in leg.get_texts():
                t.set_fontsize(st["legend"])
            if leg.get_title() is not None:
                leg.get_title().set_fontsize(st["legend"] + 1)
    for leg in getattr(fig, "legends", []):
        for t in leg.get_texts():
            t.set_fontsize(st["legend"])


def settings_path():
    from .launcher import log_dir
    return os.path.join(log_dir(), "gui_settings.json")


def load_settings():
    try:
        with open(settings_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass
