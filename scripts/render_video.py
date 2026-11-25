"""Render video/studio-asset-factory-60s.mp4: 1920x1080, 30 fps, 60.0 s.

Frames are composed in memory and streamed into ffmpeg. Veo clips are decoded
through ffmpeg pipes one frame at a time. Nothing is written as a raw frame
sequence.

Usage:
  py -3 -B scripts/render_video.py                 full render, verify, QA frames
  py -3 -B scripts/render_video.py --still 14.5    single-frame PNG stills to %TEMP%
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

import kit as k

VIDEO_DIR = k.ROOT / "video"
BUILD_DIR = VIDEO_DIR / "_build"
OUT_MP4 = VIDEO_DIR / "studio-asset-factory-60s.mp4"
QA_DIR = VIDEO_DIR / "qa_frames"
AUDIO_SRC = k.RAW_DIR / "progressive_house.mp3"

FPS = 30
W, H = 1920, 1080
TOTAL = 1800                 # 60.0 s
AUDIO_SS = 36.0              # source drop at 76.0 s lands at video t = 40.0 s
AUDIO_LEN = 60.0

CLIPS = {
    "clip1": (k.RAW_DIR / "clip1_anchor_object.mp4", 126),    # video 0.0-4.2 s
    "clip2": (k.RAW_DIR / "clip2_style_drift.mp4", 108),      # video 8.0-11.6 s
    "clip3": (k.RAW_DIR / "clip3_precision_grid.mp4", 120),   # video 28.0-32.0 s
    "clip4": (k.RAW_DIR / "clip4_calm_showcase.mp4", 138),    # video 51.0-55.6 s
}
FACTORY = k.FACTORY_DIR
BASE = k.BASELINE_DIR

QA_STAMPS = [2.0, 6.5, 10.0, 14.5, 19.0, 26.5, 30.0, 35.5, 39.4, 42.0, 46.5, 49.5, 53.5, 58.0]
QA_SINGLES = [14.5, 26.5, 35.5, 42.0, 46.5, 49.5, 58.0]

# Scene 3 node-graph geometry.
NW = 392
COL_X = [80, 536, 992, 1448]
REMIX_H, REMIX_GAP = 112, 18
REMIX_TOPS = [289 + i * (REMIX_H + REMIX_GAP) for i in range(4)]   # 289, 419, 549, 679

WORDS = [(37.80, "1 brief.", k.INK), (38.53, "2 anchors.", k.INK), (39.26, "4 formats.", k.TERRA)]
GRID_CELLS = [  # (file, aspect label, fit mode, beat start, x, y)
    ("asset_app_icon.png", "1:1 ICON", "contain", 40.00, 64, 120),
    ("asset_packaging.png", "3:2 PACKAGING", "cover", 40.06, 968, 120),
    ("asset_social_banner.png", "16:9 BANNER", "cover", 40.12, 64, 576),
    ("asset_dashboard_shell.png", "16:9 UI", "cover", 40.18, 968, 576),
]
GCW, GCH = 888, 440


class Ctx:
    def __init__(self, metrics: dict):
        self.s = k.metric_strings(metrics)
        self.readers: dict = {}
        self.scrim = k.bottom_left_scrim()
        self.grid = k.dot_grid(W, H)

    def clip(self, key: str) -> Image.Image:
        if key not in self.readers:
            path, n = CLIPS[key]
            self.readers[key] = k.ClipReader(path, n)
        return self.readers[key].next()

    def clip_final(self, key: str) -> Image.Image:
        path, n = CLIPS[key]
        if key not in self.readers:
            self.readers[key] = k.ClipReader(path, n)
        r = self.readers[key]
        while r.count < r.n:
            r.next()
        return r.last

    def close(self) -> None:
        for r in self.readers.values():
            r.close()


def pop(t: float, start: float, dur: float = 0.05) -> float:
    """Hard pop: fully opaque on its first frame, zero before start."""
    if t < start - 1e-6:
        return 0.0
    return min(1.0, (t - start) / dur + 1.0)


def out(cv: Image.Image) -> Image.Image:
    return cv.convert("RGB")


def two_tone(cv, a_txt, b_txt, role, size, baseline, cx, col_a, col_b, alpha, dy, tracking=0.0):
    adv_a = k.advance(a_txt, role, size, tracking)
    adv_b = k.advance(b_txt, role, size, tracking)
    x0 = cx - (adv_a + adv_b) / 2.0
    k.draw_text(cv, a_txt, role, size, col_a, x0, baseline, tracking=tracking, alpha=alpha, dy=dy)
    k.draw_text(cv, b_txt, role, size, col_b, x0 + adv_a, baseline, tracking=tracking, alpha=alpha, dy=dy)


# ------------------------------------------------------------- scene 1 ---
def s1_hook_clip(t, c):
    cv = c.clip("clip1").convert("RGBA")
    spr, sx, sy = c.scrim
    k.blit(cv, spr, sx, sy)
    a = k.reveal(t, 0.8, 0.5)
    pill = k.pill_sprite("GMI STUDIO", "semibold", 20, k.WHITE, k.TERRA, h=44, padx=18, tracking=2.0)
    k.blit(cv, pill, 120, 724 + (1 - a) * 14, a)
    b = k.reveal(t, 1.0, 0.5)
    k.draw_text(cv, "Every brand starts with one object.", "bold", 64, k.WHITE, 120, 858,
                alpha=b, dy=(1 - b) * 16)
    return out(cv)


def s1_hook_type(t, c):
    cv = k.alabaster(W, H)
    a = k.reveal(t, 4.3, 0.5)
    k.draw_text(cv, "Then you ask AI for six assets.", "bold", 84, k.INK, W / 2, 520,
                align="center", tracking=-1.0, alpha=a, dy=(1 - a) * 24)
    b = k.reveal(t, 6.0, 0.5)
    two_tone(cv, "And the brand ", "falls apart.", "bold", 84, 630, W / 2, k.INK, k.TERRA,
             alpha=b, dy=(1 - b) * 24, tracking=-1.0)
    return out(cv)


# ------------------------------------------------------------- scene 2 ---
def s2_drift_clip(t, c):
    cv = c.clip("clip2").convert("RGBA")
    a = k.reveal(t, 8.0, 0.3)
    tag = k.pill_sprite("WITHOUT A WORKFLOW", "semibold", 26, k.INK, k.SURFACE, h=54, padx=26, tracking=1.2)
    k.blit(cv, tag, 96, 96 + (1 - a) * 10, a)
    return out(cv)


def s2_compare(t, c):
    s = c.s
    IMG_Y, IMG_H = 200, 500
    DASH_W, PACK_W = 889, 750          # 16:9 and 3:2 at IMG_H
    XL, XR = 100, 100 + DASH_W + 80    # 1069
    cap_dash = f"Dashboard  \u00b7  L* {s['dash_L']}  \u00b7  no brand accent"
    cap_pack = f"Packaging  \u00b7  L* {s['pack_L']}  \u00b7  wrong shape  \u00b7  invented logo"
    assert k.advance(cap_dash, "semibold", 26) <= DASH_W, cap_dash
    assert k.advance(cap_pack, "semibold", 26) <= PACK_W, cap_pack

    cv = k.alabaster(W, H)
    a = k.reveal(t, 11.6, 0.4)
    k.draw_text(cv, "RAW PROMPTS  \u00b7  NO SHARED REFERENCES", "semibold", 24, k.SLATE, 96,
                k.cap_baseline(104, 24), tracking=2.0, alpha=a, dy=(1 - a) * 10)
    e1 = k.reveal(t, 11.6, 0.7)
    e2 = k.reveal(t, 11.75, 0.7)
    dash = k.photo(BASE / "baseline_dashboard.png", DASH_W, IMG_H, r=10)
    pack = k.photo(BASE / "baseline_packaging.png", PACK_W, IMG_H, r=10)
    k.blit_photo(cv, dash, XL - 120 * (1 - e1), IMG_Y, alpha=e1)
    k.blit_photo(cv, pack, XR + 120 * (1 - e2), IMG_Y, alpha=e2)
    cap_y = IMG_Y + IMG_H + 56
    c1 = k.reveal(t, 12.6, 0.4)
    k.draw_text(cv, cap_dash, "semibold", 26, k.INK, XL, cap_y, alpha=c1, dy=(1 - c1) * 10)
    c2 = k.reveal(t, 12.8, 0.4)
    k.draw_text(cv, cap_pack, "semibold", 26, k.INK, XR, cap_y, alpha=c2, dy=(1 - c2) * 10)
    h = k.reveal(t, 13.0, 0.5)
    k.hairline_h(cv, 100, 1820, 872, alpha=h)
    k.draw_text(cv, f"{s['bl_std']} L* tonal drift (std dev)  \u00b7  {s['bl_terra']} terracotta accent",
                "semibold", 30, k.INK, 100, 928, alpha=h, dy=(1 - h) * 10)
    return out(cv)


# ------------------------------------------------------------- scene 3 ---
def s3_statement(t, c):
    cv = k.alabaster(W, H)
    a = k.reveal(t, 17.0, 0.5)
    k.draw_text(cv, "Lock the rules once.", "bold", 96, k.INK, W / 2, 492, align="center",
                tracking=-1.5, alpha=a, dy=(1 - a) * 24)
    b = k.reveal(t, 18.4, 0.45)
    k.draw_text(cv, "Run the factory forever.", "bold", 96, k.TERRA, W / 2, 628, align="center",
                tracking=-1.5, alpha=b, dy=(1 - b) * 24)
    return out(cv)


def s3_graph(t, c):
    cv = c.grid.copy()
    a = k.reveal(t, 20.2, 0.4)
    k.draw_text(cv, "GMI STUDIO PATTERN  \u00b7  TWO-PHASE WORKFLOW", "semibold", 22, k.TERRA, 80,
                k.cap_baseline(96, 22), tracking=2.0, alpha=a, dy=(1 - a) * 10)
    for x, txt, st in [(COL_X[0], "Brief", 20.2), (COL_X[1], "Stage 1 \u00b7 Anchors", 22.2),
                       (COL_X[2], "Approval", 24.0), (COL_X[3], "Stage 2 \u00b7 4x Remix", 25.5)]:
        ha = k.reveal(t, st, 0.4)
        k.draw_text(cv, txt, "semibold", 24, k.SLATE, x, 240, alpha=ha, dy=(1 - ha) * 10)

    def wire_anim(p0, p3, start, phase):
        prog = k.ease_in_out_cubic(k.window(t, start, 0.8))
        if prog <= 0:
            return
        pulse = None
        if prog >= 1.0:
            pulse = ((t - (start + 0.8)) * 0.45 + phase) % 1.0
        k.wire(cv, p0, p3, prog, pulse=pulse)

    # Studio graph wires only exist from uploaded anchors -> Batch -> 4x Remix.
    # Stage 0 -> Stage 1 and Stage 1 -> Approval are manual human handoffs.
    m1 = k.reveal(t, 22.2, 0.4)
    k.draw_text(cv, "\u2192", "semibold", 30, k.MUTED, (COL_X[0] + NW + COL_X[1]) / 2, 548,
                align="center", alpha=m1)
    m2 = k.reveal(t, 24.0, 0.4)
    k.draw_text(cv, "\u2192", "semibold", 30, k.MUTED, (COL_X[1] + NW + COL_X[2]) / 2, 548,
                align="center", alpha=m2)
    mn = k.reveal(t, 24.4, 0.4)
    k.draw_text(cv, "Two-phase handoff  \u00b7  copy expanded prompts into Stage 1, then upload approved anchors into Batch",
                "regular", 22, k.MUTED, 80, 916, alpha=mn)
    for i, y in enumerate(REMIX_TOPS):
        wire_anim((COL_X[2] + NW, 540), (COL_X[3], y + REMIX_H // 2), 25.5, 0.1 + 0.2 * i)

    def node(nd, x, y, st):
        ad = k.reveal(t, st, 0.35)
        if ad > 0:
            k.blit_node(cv, nd, x, y, alpha=ad, dy=(1 - ad) * 16)

    llm = k.node_card("LLM Node", ["GLM-5.3-Flash \u00b7 temp 0.0", "1 JSON brief \u2192 6 slot prompts"], NW, 150)
    anc1 = k.node_card("Anchor 01 Hardware", ["16:9 \u00b7 material and light"], NW, 120)
    anc2 = k.node_card("Anchor 02 UI Grid", ["16:9 \u00b7 grid and layout"], NW, 120)
    batch = k.node_card("GMI Batch Images", ["2 approved anchors \u2192 1 batch"], NW, 150, active=True)
    remix = [
        k.node_card("1:1 Icon", ["Square app icon"], NW, REMIX_H),
        k.node_card("16:9 UI", ["Dashboard shell"], NW, REMIX_H),
        k.node_card("3:2 Box", ["Packaging"], NW, REMIX_H),
        k.node_card("16:9 Banner", ["Social banner"], NW, REMIX_H),
    ]
    node(llm, COL_X[0], 465, 20.5)
    node(anc1, COL_X[1], 408, 22.2)
    node(anc2, COL_X[1], 552, 22.35)
    node(batch, COL_X[2], 465, 24.0)
    for i, nd in enumerate(remix):
        node(nd, COL_X[3], REMIX_TOPS[i], 25.5)
    return out(cv)


# ------------------------------------------------------------- scene 4 ---
def s4_clip(t, c):
    cv = c.clip("clip3").convert("RGBA")
    spr, sx, sy = c.scrim
    k.blit(cv, spr, sx, sy)
    a = k.reveal(t, 28.3, 0.5)
    k.draw_text(cv, "Two anchors lock material, light, and grid.", "semibold", 44, k.WHITE,
                120, 956, alpha=a, dy=(1 - a) * 14)
    return out(cv)


def s4_showcase(t, c):
    IW, IH, Y0 = 840, 473, 176
    cv = k.alabaster(W, H)
    p1 = k.photo(FACTORY / "anchor_hardware.png", IW, IH, r=10)
    p2 = k.photo(FACTORY / "anchor_interface.png", IW, IH, r=10)
    a1 = k.reveal(t, 32.0, 0.6)
    k.blit_photo(cv, p1, 96, Y0, alpha=a1, dy=(1 - a1) * 28)
    a2 = k.reveal(t, 32.12, 0.6)
    k.blit_photo(cv, p2, 984, Y0, alpha=a2, dy=(1 - a2) * 28)
    cap = k.reveal(t, 32.4, 0.4)
    k.draw_text(cv, "ANCHOR 01  \u00b7  HARDWARE", "semibold", 20, k.SLATE, 96, Y0 + IH + 44,
                tracking=1.5, alpha=cap)
    k.draw_text(cv, "ANCHOR 02  \u00b7  UI GRID", "semibold", 20, k.SLATE, 984, Y0 + IH + 44,
                tracking=1.5, alpha=cap)
    SW, SH, GAP = 240, 96, 24
    x0 = (W - (5 * SW + 4 * GAP)) // 2
    swatches = [("#F5F3EE", k.BG), ("#E8E3D9", k.STONE), ("#D8D4CC", k.ALUMINUM),
                ("#18181B", k.INK), ("#C84B31", k.TERRA)]
    for i, (hx, col) in enumerate(swatches):
        a = k.reveal(t, 33.0 + 0.06 * i, 0.4)
        if a <= 0:
            continue
        x = x0 + i * (SW + GAP)
        k.blit(cv, k.card_sprite(SW, SH, 10, col, k.BORDER, 1), x, 752 + (1 - a) * 12, a)
        k.draw_text(cv, hx, "mono", 22, k.MUTED, x + SW / 2, 752 + SH + 34, align="center", alpha=a)
    if t >= 36.4 - 1e-6:
        b = k.reveal(t, 36.4, 0.3)
        badge = k.pill_sprite("2-Anchor Reference Batch", "semibold", 26, k.WHITE, k.TERRA, h=56, padx=28,
                              tracking=0.3)
        bw, bh = badge.size
        ph = ((t - 36.4) / 0.9) % 1.0
        pad = int(round(4 + ph * 16))
        ring = k.ring_sprite(bw + 2 * pad, bh + 2 * pad, bh // 2 + pad, k.TERRA, bw=2, alpha=(1 - ph) * 0.55)
        k.blit(cv, ring, 96 - pad, 96 - pad, b)
        k.blit(cv, badge, 96, 96 + (1 - b) * 12, b)
    return out(cv)


def s4_words(t, c):
    if t >= 39.90 - 1e-6:
        return Image.new("RGB", (W, H), (253, 252, 250))   # bright alabaster beat flash
    cur = WORDS[0]
    for w in WORDS:
        if t >= w[0] - 1e-6:
            cur = w
    st, txt, col = cur
    cv = k.alabaster(W, H)
    dy = (1 - k.ease_out_cubic(k.window(t, st, 0.12))) * 18
    k.draw_text(cv, txt, "bold", 112, col, W / 2, 585, align="center", tracking=-2.0, dy=dy)
    return out(cv)


# ------------------------------------------------------------- scene 5 ---
def s5_grid(t, c):
    cv = k.alabaster(W, H)
    k.draw_text(cv, "4 MULTI-REFERENCE REMIXES  \u00b7  ONE SHARED BATCH", "semibold", 22, k.SLATE,
                64, k.cap_baseline(56, 22), tracking=2.0)
    for fname, label, mode, st, x, y in GRID_CELLS:
        if t < st - 1e-6:
            continue
        a = pop(t, st)
        sc = 1.03 - 0.03 * k.ease_out_cubic(k.window(t, st, 0.45))
        ph = k.photo(FACTORY / fname, GCW, GCH, r=12, mode=mode)
        k.blit_photo(cv, ph, x, y, alpha=a, scale=sc)
        lab = k.pill_sprite(label, "semibold", 20, k.INK, k.SURFACE, h=40, padx=16, tracking=1.0)
        k.blit(cv, lab, x + 20, y + GCH - 20 - 40, a)
    return out(cv)


def s5_packaging(t, c):
    IW, IH, Y = 810, 540, 280
    LX, RX = 102, 1008
    cv = k.alabaster(W, H)
    e1 = k.reveal(t, 44.5, 0.55)
    e2 = k.reveal(t, 44.6, 0.55)
    lp = k.photo(BASE / "baseline_packaging.png", IW, IH, r=10)
    rp = k.photo(FACTORY / "asset_packaging.png", IW, IH, r=10)
    k.blit_photo(cv, lp, LX - 60 * (1 - e1), Y, alpha=e1)
    k.blit_photo(cv, rp, RX + 60 * (1 - e2), Y, alpha=e2)
    la = k.reveal(t, 44.5, 0.3)
    k.draw_text(cv, "BEFORE  \u00b7  UNBATCHED", "semibold", 24, k.SLATE, LX, 248, tracking=2.0,
                alpha=la, dy=(1 - la) * 8)
    k.draw_text(cv, "AFTER  \u00b7  2-ANCHOR BATCH", "semibold", 24, k.TERRA, RX, 248, tracking=2.0,
                alpha=la, dy=(1 - la) * 8)
    return out(cv)


def s5_metrics(t, c):
    s = c.s
    cv = k.alabaster(W, H)
    cols = [
        (416, s["red_text"], k.TERRA, "Lightness std dev", f"{s['bl_std']} \u2192 {s['fa_std']} L*"),
        (960, s["fa_terra"].replace("/", " / "), k.INK, "Terracotta brand accent",
         f"{s['bl_terra']} on unbatched baseline"),
        (1504, s["cost"], k.INK, "All 6 factory assets", "seedream-5.0-pro list"),
    ]
    for i, (cx, big, col, cap, sub) in enumerate(cols):
        a = k.reveal(t, 48.0 + 0.18 * i, 0.45)
        dy = (1 - a) * 18
        k.draw_text(cv, big, "bold", 96, col, cx, 500, align="center", tracking=-2.0, alpha=a, dy=dy)
        k.draw_text(cv, cap, "semibold", 32, k.INK, cx, 584, align="center", alpha=a, dy=dy)
        k.draw_text(cv, sub, "regular", 26, k.SLATE, cx, 628, align="center", alpha=a, dy=dy)
    hl = k.reveal(t, 48.2, 0.5)
    k.hairline_v(cv, 688, 360, 680, alpha=hl)
    k.hairline_v(cv, 1232, 360, 680, alpha=hl)
    return out(cv)


# ------------------------------------------------------------- scene 6 ---
def s6_clip(t, c):
    cv = c.clip("clip4").convert("RGBA")
    spr, sx, sy = c.scrim
    k.blit(cv, spr, sx, sy)
    a = k.reveal(t, 51.3, 0.4)
    k.draw_text(cv, "Adapt the pattern for any brand.", "semibold", 44, k.WHITE,
                120, 956, alpha=a, dy=(1 - a) * 14)
    return out(cv)


def s6_close(t, c):
    base = c.clip_final("clip4")
    cv = k.alabaster(W, H)
    a0 = k.reveal(t, 55.8, 0.4)
    k.blit(cv, k.circle_sprite(28, k.TERRA), W / 2 - 14, 404 - 14 + (1 - a0) * 10, a0)
    a1 = k.reveal(t, 56.2, 0.5)
    k.draw_text(cv, "Reusable Brand & UI Asset Factory", "bold", 64, k.INK, W / 2, 540, align="center",
                tracking=-0.8, alpha=a1, dy=(1 - a1) * 18)
    a2 = k.reveal(t, 56.6, 0.5)
    k.draw_text(cv, "GMI Studio templates  \u00b7  Seedream API run  \u00b7  CIELAB verifier", "regular", 28,
                k.SLATE, W / 2, 608, align="center", alpha=a2, dy=(1 - a2) * 18)
    card = out(cv)
    fade = k.ease_in_out_cubic(k.window(t, 55.6, 0.6))
    return card if fade >= 1.0 else Image.blend(base, card, fade)


# (start frame, end frame exclusive, scene fn, clip key used by the scene or None)
SCENES = [
    (0, 126, s1_hook_clip, "clip1"),
    (126, 240, s1_hook_type, None),
    (240, 348, s2_drift_clip, "clip2"),
    (348, 510, s2_compare, None),
    (510, 606, s3_statement, None),
    (606, 840, s3_graph, None),
    (840, 960, s4_clip, "clip3"),
    (960, 1134, s4_showcase, None),
    (1134, 1200, s4_words, None),
    (1200, 1335, s5_grid, None),
    (1335, 1440, s5_packaging, None),
    (1440, 1530, s5_metrics, None),
    (1530, 1668, s6_clip, "clip4"),
    (1668, 1800, s6_close, None),
]


def render_frame(i: int, c: Ctx, still: bool = False) -> Image.Image:
    t = i / FPS
    for lo, hi, fn, key in SCENES:
        if lo <= i < hi:
            if still and key:
                for _ in range(i - lo):   # fast-forward the clip reader to frame i
                    c.clip(key)
            return fn(t, c)
    raise IndexError(i)


# ------------------------------------------------------------ audio/mux ---
def build_audio(out_m4a: Path) -> dict:
    chain = (f"atrim=start={AUDIO_SS:g}:duration={AUDIO_LEN:g},asetpts=PTS-STARTPTS,"
             "aresample=48000,aformat=channel_layouts=stereo")
    measure = [k.FFMPEG, "-hide_banner", "-nostats", "-i", str(AUDIO_SRC), "-vn",
               "-af", chain + ",loudnorm=I=-15:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"]
    r = subprocess.run(measure, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stats = json.loads(re.findall(r"\{[^{}]*\}", r.stderr)[-1])
    af = (chain
          + f",loudnorm=I=-15:TP=-1.5:LRA=11:measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}:linear=false"
          + ",alimiter=limit=0.75:attack=0.5:release=50:level=disabled,afade=t=in:st=0:d=0.6,afade=t=out:st=57.2:d=2.8")
    encode = [k.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(AUDIO_SRC), "-vn",
              "-af", af, "-t", f"{AUDIO_LEN:g}", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
              "-ac", "2", str(out_m4a)]
    subprocess.run(encode, check=True)
    return stats


def mux(video: Path, audio: Path, out_path: Path) -> None:
    cmd = [k.FFMPEG, "-hide_banner", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
           "-t", f"{TOTAL / FPS:g}", "-movflags", "+faststart", str(out_path)]
    subprocess.run(cmd, check=True)


def probe(path: Path) -> dict:
    r = subprocess.run([k.FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def loudness(path: Path) -> tuple[float, float]:
    r = subprocess.run([k.FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True)
    i_vals = re.findall(r"I:\s+(-?\d+\.\d+) LUFS", r.stderr)
    tp = re.search(r"True peak:\s*Peak:\s+(-?\d+\.\d+) dBFS", r.stderr)
    return float(i_vals[-1]), float(tp.group(1))


def verify(path: Path) -> None:
    info = probe(path)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    fmt = info["format"]
    fnum, fden = (int(x) for x in v["r_frame_rate"].split("/"))
    dur = float(fmt["duration"])
    size_mb = int(fmt["size"]) / 1e6
    print("VIDEO", {k_: v.get(k_) for k_ in ["codec_name", "profile", "level", "width", "height", "pix_fmt",
                                             "r_frame_rate", "avg_frame_rate", "nb_frames", "color_space",
                                             "color_transfer", "color_primaries", "color_range"]})
    print("AUDIO", {k_: a.get(k_) for k_ in ["codec_name", "sample_rate", "channels", "bit_rate", "channel_layout"]})
    print(f"FORMAT duration={dur:.3f}s size={size_mb:.2f} MB bit_rate={fmt.get('bit_rate')}")
    assert v["width"] == 1920 and v["height"] == 1080
    assert v["codec_name"] == "h264" and v.get("profile") == "High" and v.get("level") == 41
    assert v["pix_fmt"] == "yuv420p"
    assert fnum == 30 * fden, v["r_frame_rate"]
    assert v.get("color_space") == "bt709" and v.get("color_primaries") == "bt709"
    assert v.get("color_transfer") == "bt709"
    if v.get("nb_frames"):
        assert int(v["nb_frames"]) == TOTAL, v["nb_frames"]
    assert a["codec_name"] == "aac" and int(a["sample_rate"]) == 48000 and int(a["channels"]) == 2
    assert abs(dur - 60.0) < 0.05, dur
    assert size_mb < 50, size_mb
    lufs, tp = loudness(path)
    print(f"LOUDNESS integrated={lufs:.2f} LUFS  true_peak={tp:.2f} dBFS")
    assert abs(lufs + 15.0) <= 0.5, lufs
    assert tp <= -1.4, tp


def drop_check() -> None:
    """Print the source-track RMS envelope around the 76.0 s drop (source time)."""
    cmd = [k.FFMPEG, "-hide_banner", "-v", "error", "-ss", "66", "-t", "16", "-i", str(AUDIO_SRC),
           "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    buf = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(buf, dtype="<i2").astype(np.float32) / 32768.0
    win = 4000  # 0.5 s at 8 kHz
    n = len(x) // win
    rms = np.sqrt((x[: n * win].reshape(n, win) ** 2).mean(axis=1))
    row = " ".join(f"{66 + 0.5 * i:.1f}s:{20 * np.log10(v + 1e-9):.1f}" for i, v in enumerate(rms))
    print("SOURCE RMS dB per 0.5s (source time):", row)


# ------------------------------------------------------------------- QA ---
def grab(mp4: Path, ts: float) -> Image.Image:
    cmd = [k.FFMPEG, "-hide_banner", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(mp4),
           "-frames:v", "1", "-vf", "scale=1280:720:flags=lanczos", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    buf = subprocess.run(cmd, capture_output=True, check=True).stdout
    assert len(buf) == 1280 * 720 * 3, len(buf)
    return Image.frombytes("RGB", (1280, 720), buf)


def write_qa(mp4: Path) -> None:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    frames = {}
    for ts in QA_STAMPS:
        img = grab(mp4, ts)
        frames[ts] = img
        if ts in QA_SINGLES:
            img.save(QA_DIR / f"qa_{ts:.1f}s.jpg", quality=93, subsampling=0)
    TW, TH, M, G = 448, 252, 24, 16
    sheet = Image.new("RGBA", (2 * M + 4 * TW + 3 * G, 2 * M + 4 * TH + 3 * G), k.BG + (255,))
    for idx, ts in enumerate(QA_STAMPS):
        r_, c_ = divmod(idx, 4)
        x = M + c_ * (TW + G)
        y = M + r_ * (TH + G)
        sheet.paste(frames[ts].resize((TW, TH), k.LANCZOS).convert("RGBA"), (x, y))
        lab = k.pill_sprite(f"{ts:.1f}s", "semibold", 18, k.WHITE, k.INK, h=30, padx=10)
        k.blit(sheet, lab, x + 12, y + TH - 12 - 30)
    sheet.convert("RGB").save(QA_DIR / "qa_contact_sheet.jpg", quality=92, subsampling=0)


def stills(times) -> None:
    m = k.load_metrics()
    tmp = Path(tempfile.gettempdir())
    for t in times:
        c = Ctx(m)
        try:
            i = int(round(t * FPS))
            img = render_frame(i, c, still=True)
            p = tmp / f"day6_still_{t:.2f}s.png"
            img.save(p)
            print("still", p)
        finally:
            c.close()


def main(argv) -> int:
    if "--still" in argv:
        stills([float(x) for x in argv[argv.index("--still") + 1:]])
        return 0
    t0 = time.time()
    m = k.load_metrics()
    k.verify_metrics(m)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    silent = BUILD_DIR / "silent_video.mp4"
    audio = BUILD_DIR / "audio_aac.m4a"
    c = Ctx(m)
    pipe = k.RgbPipe(silent)
    try:
        for i in range(TOTAL):
            pipe.write(render_frame(i, c))
            if i % 150 == 0:
                print(f"frame {i}/{TOTAL} t={i / FPS:.1f}s elapsed={time.time() - t0:.0f}s", flush=True)
    finally:
        c.close()
        pipe.close()
    t_video = time.time() - t0
    print(f"silent video encoded in {t_video:.1f}s", flush=True)
    stats = build_audio(audio)
    print("loudnorm measured:", {k_: stats[k_] for k_ in ["input_i", "input_tp", "input_lra", "target_offset"]})
    drop_check()
    mux(silent, audio, OUT_MP4)
    verify(OUT_MP4)
    write_qa(OUT_MP4)
    shutil.rmtree(BUILD_DIR, ignore_errors=True)
    print(f"DONE {OUT_MP4} total {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
