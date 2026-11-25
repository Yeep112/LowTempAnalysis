"""自訂元件：卡片、可捲動區塊、1D/2D 切換鈕、功能卡片清單。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme as T


def card(parent, pad=12):
    """白色卡片（細邊框）。回傳外框與內容框。"""
    outer = tk.Frame(parent, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER,
                     highlightcolor=T.BORDER, bd=0)
    inner = tk.Frame(outer, bg=T.CARD)
    inner.pack(fill="both", expand=True, padx=pad, pady=pad)
    return outer, inner


def section_title(parent, text, icon=None, color=None):
    row = tk.Frame(parent, bg=T.CARD)
    if icon:
        tk.Label(row, text=icon, bg=T.CARD, fg=color or T.MUTED, font=T.font(12, "bold")).pack(side="left", padx=(0, 6))
    lab = tk.Label(row, text=text, bg=T.CARD, fg=T.TEXT, font=T.font(11, "bold"))
    lab.pack(side="left")
    return row, lab


class Chip(tk.Label):
    """小標籤，例如「13 筆」「I→V」。"""

    def __init__(self, parent, text="", fg=None, bg=None, **kw):
        super().__init__(parent, text=text, fg=fg or T.MUTED, bg=bg or T.CHIP, font=T.font(9, "bold"), padx=8, pady=2, **kw)

    def set(self, text, fg=None, bg=None):
        self.configure(text=text)
        if fg:
            self.configure(fg=fg)
        if bg:
            self.configure(bg=bg)


class ScrollFrame(tk.Frame):
    """可上下捲動的區塊；內容放在 self.body。"""

    def __init__(self, parent, bg=None, **kw):
        bg = bg or T.CARD
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for w in (self.canvas, self.body):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))

    def _unbind_wheel(self, _=None):
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(ev)

    def _on_wheel(self, e):
        top, bottom = self.canvas.yview()
        if top <= 0 and bottom >= 1:
            return
        self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")

    def to_top(self):
        self.canvas.yview_moveto(0)


class Segmented(tk.Frame):
    """大型切換鈕（1D / 2D）。"""

    def __init__(self, parent, options, command):
        super().__init__(parent, bg=T.CHIP, padx=3, pady=3)
        self.command = command
        self.buttons = []
        self.active = 0
        for i, (key, title, sub) in enumerate(options):
            b = tk.Frame(self, bg=T.CHIP, cursor="hand2", padx=10, pady=7)
            t = tk.Label(b, text=title, bg=T.CHIP, fg=T.MUTED, font=T.font(11, "bold"), cursor="hand2")
            s = tk.Label(b, text=sub, bg=T.CHIP, fg=T.FAINT, font=T.font(8), cursor="hand2")
            t.pack(anchor="w")
            s.pack(anchor="w")
            b.grid(row=0, column=i, sticky="nsew", padx=1)
            self.columnconfigure(i, weight=1)
            for w in (b, t, s):
                w.bind("<Button-1>", lambda e, i=i: self.select(i))
            self.buttons.append((key, b, t, s))
        self.paint()

    def select(self, i, notify=True):
        if i == self.active and notify:
            return
        self.active = i
        self.paint()
        if notify:
            self.command(self.buttons[i][0])

    def paint(self):
        for i, (key, b, t, s) in enumerate(self.buttons):
            if i == self.active:
                a = T.ACCENTS[key]
                for w in (b, t, s):
                    w.configure(bg=a["main"])
                on = a.get("on", "#ffffff")
                t.configure(fg=on)
                s.configure(fg=T.mix_color(on, a["main"], 0.25))
            else:
                for w in (b, t, s):
                    w.configure(bg=T.CHIP)
                t.configure(fg=T.MUTED)
                s.configure(fg=T.FAINT)


class FeatureList(tk.Frame):
    """依分組列出分析功能的卡片清單。"""

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=T.CARD)
        self.on_select = on_select
        self.cards = {}
        self.selected = None
        self.accent = "1D"

    def set_items(self, analyses, accent, selected_name):
        for w in self.winfo_children():
            w.destroy()
        self.cards = {}
        self.accent = accent
        group = None
        for a in analyses:
            if a.group != group:
                group = a.group
                tk.Label(self, text=group, bg=T.CARD, fg=T.FAINT, font=T.font(9, "bold"),
                         anchor="w").pack(fill="x", pady=(10, 3), padx=2)
            self.cards[a.name] = self._make_card(a)
        self.select(selected_name, notify=False)

    def _make_card(self, a):
        acc = T.ACCENTS[self.accent]
        fr = tk.Frame(self, bg=T.CARD, cursor="hand2")
        fr.pack(fill="x", pady=2)
        bar = tk.Frame(fr, bg=T.CARD, width=4)
        bar.pack(side="left", fill="y")
        icon = tk.Label(fr, text=a.icon, bg=T.CARD, fg=acc["main"], font=T.font(15, "bold"),
                        width=2, cursor="hand2")
        icon.pack(side="left", padx=(6, 4), pady=6)
        txt = tk.Frame(fr, bg=T.CARD, cursor="hand2")
        txt.pack(side="left", fill="x", expand=True, pady=6, padx=(0, 6))
        title = tk.Label(txt, text=a.name, bg=T.CARD, fg=T.TEXT, font=T.font(10, "bold"),
                         anchor="w", justify="left", wraplength=210, cursor="hand2")
        title.pack(fill="x")
        desc_text = (a.help.split("。")[0] if a.help else "")
        if a.modes is not None:
            desc_text = f"[{a.modes_text}] " + desc_text
        desc = tk.Label(txt, text=desc_text, bg=T.CARD, fg=T.MUTED, font=T.font(8),
                        anchor="w", justify="left", wraplength=210, cursor="hand2")
        desc.pack(fill="x")
        widgets = (fr, txt, icon, title, desc)
        for w in widgets:
            w.bind("<Button-1>", lambda e, n=a.name: self.select(n))
            w.bind("<Enter>", lambda e, n=a.name: self._hover(n, True))
            w.bind("<Leave>", lambda e, n=a.name: self._hover(n, False))
        return dict(frame=fr, bar=bar, icon=icon, title=title, desc=desc, txt=txt, enabled=True)

    def _paint(self, name, bg):
        c = self.cards[name]
        for k in ("frame", "icon", "title", "desc", "txt"):
            c[k].configure(bg=bg)

    def _hover(self, name, inside):
        if name == self.selected or name not in self.cards:
            return
        self._paint(name, T.HOVER if inside else T.CARD)

    def select(self, name, notify=True):
        acc = T.ACCENTS[self.accent]
        for n, c in self.cards.items():
            on = n == name
            self._paint(n, acc["soft"] if on else T.CARD)
            c["bar"].configure(bg=acc["main"] if on else T.CARD)
            c["title"].configure(fg=acc["text"] if on else (T.TEXT if c["enabled"] else T.FAINT))
        self.selected = name
        if notify:
            self.on_select(name)

    def set_enabled(self, enabled: dict):
        """依目前選取的資料，把不支援的功能變淡。enabled: 名稱 -> bool"""
        acc = T.ACCENTS[self.accent]
        for n, c in self.cards.items():
            ok = enabled.get(n, True)
            c["enabled"] = ok
            c["icon"].configure(fg=acc["main"] if ok else T.BORDER)
            if n != self.selected:
                c["title"].configure(fg=T.TEXT if ok else T.FAINT)
            c["desc"].configure(fg=T.MUTED if ok else T.BORDER)
