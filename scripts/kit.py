"""Shared visual kit for the Day 6 studio asset factory renders.

Palette, Windows fonts, easing, anti-aliased shapes, cached text sprites,
framed photo sprites, node-graph wires, metric loading, and a streaming
ffmpeg RGB24 writer plus a sequential clip reader. Pillow and numpy only.
Raw frame sequences are never written to disk.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
RESULTS_JSON = ROOT / "outputs" / "factory_results.json"
BASELINE_DIR = ROOT / "assets" / "baseline"
FACTORY_DIR = ROOT / "assets" / "factory"
RAW_DIR = ROOT / "video" / "raw"

BOX = Image.Resampling.BOX
LANCZOS = Image.Resampling.LANCZOS
BILINEAR = Image.Resampling.BILINEAR

# Warm-alabaster editorial palette.
BG = (245, 243, 238)        # #F5F3EE
SURFACE = (255, 255, 255)   # #FFFFFF
STONE = (232, 227, 217)     # #E8E3D9
BORDER = (221, 216, 206)    # #DDD8CE
INK = (24, 24, 27)          # #18181B
SLATE = (87, 83, 78)        # #57534E
MUTED = (138, 133, 124)     # #8A857C
TERRA = (200, 75, 49)       # #C84B31
ALUMINUM = (216, 212, 204)  # #D8D4CC
WIRE = (176, 170, 160)
WHITE = (255, 255, 255)
SHADOW_RGB = (60, 48, 36)

FONT_DIR = Path(r"C:\Windows\Fonts")
FONT_FILES = {
    "regular": "segoeui.ttf",
    "semibold": "seguisb.ttf",
    "bold": "segoeuib.ttf",
    "mono": "consola.ttf",
    "mono_bold": "consolab.ttf",
}
CAP_RATIO = 0.70  # Segoe UI cap height as a fraction of the em size
COST_PER_FACTORY_IMAGE_USD = 0.085  # seedream-5.0-pro list price per image, from the brief
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


# ---------------------------------------------------------------- metrics ---
def load_metrics() -> dict:
    """Read every number the renders use from outputs/factory_results.json."""
    data = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    cmp_ = data["comparison"]
    rows = {r["slot_id"]: r for r in data["baseline"] + data["factory"]}
    m = {
        "baseline_n": int(cmp_["baseline"]["n"]),
        "baseline_terra": int(cmp_["baseline"]["terra_accent_present_count"]),
        "baseline_l_std": float(cmp_["baseline"]["lightness_std_dev"]),
        "factory_n": int(cmp_["factory"]["n"]),
        "factory_terra": int(cmp_["factory"]["terra_accent_present_count"]),
        "factory_aspect_ok": int(cmp_["factory"]["aspect_ok_count"]),
        "factory_l_std": float(cmp_["factory"]["lightness_std_dev"]),
        "reduction_pct": float(cmp_["reduction_pct"]["lightness_std_dev"]),
        "L": {k: float(v["mean_lightness_l"]) for k, v in rows.items()},
    }
    m["factory_cost_usd"] = round(m["factory_n"] * COST_PER_FACTORY_IMAGE_USD, 2)
    return m


def verify_metrics(m: dict) -> None:
    """Fail loudly if the results JSON drifts from the verified numbers in the brief."""
    assert m["baseline_n"] == 2 and m["factory_n"] == 6, "sample sizes changed"
    assert m["baseline_terra"] == 0 and m["factory_terra"] == 6, "accent counts changed"
    assert m["factory_aspect_ok"] == 6, "aspect count changed"
    assert abs(m["baseline_l_std"] - 12.938098) < 1e-4, m["baseline_l_std"]
    assert abs(m["factory_l_std"] - 5.502317) < 1e-4, m["factory_l_std"]
    assert abs(m["reduction_pct"] - 57.471978) < 1e-4, m["reduction_pct"]
    assert round(m["L"]["baseline_dashboard"], 1) == 90.5
    assert round(m["L"]["baseline_packaging"], 1) == 72.2
    assert m["factory_cost_usd"] == 0.51, m["factory_cost_usd"]


def metric_strings(m: dict) -> dict:
    """Display strings derived from the loaded metrics (no hand-typed numbers)."""
    red = abs(m["reduction_pct"])
    return {
        "bl_std": f"{m['baseline_l_std']:.1f}",
        "fa_std": f"{m['factory_l_std']:.1f}",
        "red_text": f"-{red:.1f}%",
        "red_int": f"{round(red)}",
        "bl_terra": f"{m['baseline_terra']}/{m['baseline_n']}",
        "fa_terra": f"{m['factory_terra']}/{m['factory_n']}",
        "dash_L": f"{m['L']['baseline_dashboard']:.1f}",
        "pack_L": f"{m['L']['baseline_packaging']:.1f}",
        "cost": f"${m['factory_cost_usd']:.2f}",
    }


# ----------------------------------------------------------------- easing ---
def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1.0 - (1.0 - x) ** 3


def ease_in_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 4.0 * x * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0


def window(t: float, start: float, dur: float) -> float:
    if dur <= 0:
        return 1.0 if t >= start else 0.0
    return clamp01((t - start) / dur)


def reveal(t: float, start: float, dur: float = 0.45) -> float:
    return ease_out_cubic(window(t, start, dur))


# ------------------------------------------------------------------ text ---
@lru_cache(maxsize=None)
def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONT_FILES[role]), size)


def advance(text: str, role: str, size: int, tracking: float = 0.0) -> float:
    return font(role, size).getlength(text) + tracking * max(len(text) - 1, 0)


_TEXT_CACHE: dict = {}


def text_sprite(text: str, role: str, size: int, fill, tracking: float = 0.0):
    """Cached RGBA sprite for one line. Returns (sprite, ox, oy); (ox, oy) is the
    baseline-left origin inside the sprite."""
    key = (text, role, size, tuple(fill), float(tracking))
    hit = _TEXT_CACHE.get(key)
    if hit is not None:
        return hit
    f = font(role, size)
    adv = advance(text, role, size, tracking)
    pad = max(4, size // 4)
    asc = math.ceil(size * 1.05)
    desc = math.ceil(size * 0.40)
    w = math.ceil(adv) + 2 * pad
    h = asc + desc + 2 * pad
    img = Image.new("RGBA", (w, h), tuple(fill) + (0,))
    d = ImageDraw.Draw(img)
    ox, oy = pad, pad + asc
    col = tuple(fill) + (255,)
    if tracking == 0:
        d.text((ox, oy), text, font=f, fill=col, anchor="ls")
    else:
        for i, ch in enumerate(text):
            if ch != " ":
                x = ox + f.getlength(text[:i]) + i * tracking
                d.text((x, oy), ch, font=f, fill=col, anchor="ls")
    out = (img, ox, oy)
    _TEXT_CACHE[key] = out
    return out


def cap_baseline(cap_top: float, size: float) -> float:
    """Baseline y for a line whose cap height starts at cap_top."""
    return cap_top + CAP_RATIO * size


# ---------------------------------------------------------------- shapes ---
def scale_alpha(img: Image.Image, a: float) -> Image.Image:
    if a >= 0.999:
        return img
    r, g, b, al = img.split()
    al = al.point(lambda v: int(v * a + 0.5))
    return Image.merge("RGBA", (r, g, b, al))


def blit(canvas: Image.Image, sprite: Image.Image, x: float, y: float, alpha: float = 1.0) -> None:
    """Alpha-composite an RGBA sprite at (x, y), clipped to the canvas."""
    if alpha <= 0.001:
        return
    x = int(round(x))
    y = int(round(y))
    cw, ch = canvas.size
    w, h = sprite.size
    x0, y0 = max(0, -x), max(0, -y)
    x1, y1 = min(w, cw - x), min(h, ch - y)
    if x1 <= x0 or y1 <= y0:
        return
    if (x0, y0, x1, y1) != (0, 0, w, h):
        sprite = sprite.crop((x0, y0, x1, y1))
        x += x0
        y += y0
    canvas.alpha_composite(scale_alpha(sprite, alpha), (x, y))


def draw_text(canvas, text, role, size, fill, x, baseline, *, align="left",
              tracking=0.0, alpha=1.0, dy=0.0) -> float:
    sprite, ox, oy = text_sprite(text, role, size, fill, tracking)
    adv = advance(text, role, size, tracking)
    if align == "center":
        x = x - adv / 2.0
    elif align == "right":
        x = x - adv
    blit(canvas, sprite, x - ox, baseline + dy - oy, alpha)
    return adv


_MASK_CACHE: dict = {}


def rr_mask(w: int, h: int, r: float) -> Image.Image:
    """Anti-aliased rounded-rectangle alpha mask (4x supersampled)."""
    w, h = int(w), int(h)
    r = int(max(0, min(r, w // 2, h // 2)))
    key = (w, h, r)
    m = _MASK_CACHE.get(key)
    if m is None:
        ss = 4
        big = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(big).rounded_rectangle(
            (0, 0, w * ss - 1, h * ss - 1), radius=r * ss, fill=255)
        m = big.resize((w, h), BOX)
        _MASK_CACHE[key] = m
    return m


def rr_ring_mask(w: int, h: int, r: float, bw: int = 1) -> Image.Image:
    outer = rr_mask(w, h, r)
    inner = Image.new("L", (w, h), 0)
    iw, ih = w - 2 * bw, h - 2 * bw
    if iw > 0 and ih > 0:
        inner.paste(rr_mask(iw, ih, max(r - bw, 0)), (bw, bw))
    return ImageChops.subtract(outer, inner)


def solid_with_mask(color, mask: Image.Image, alpha: float = 1.0) -> Image.Image:
    spr = Image.new("RGBA", mask.size, tuple(color) + (0,))
    if alpha < 0.999:
        mask = mask.point(lambda v: int(v * alpha + 0.5))
    spr.putalpha(mask)
    return spr


def rr_sprite(w, h, r, fill, alpha: float = 1.0) -> Image.Image:
    return solid_with_mask(fill, rr_mask(w, h, r), alpha)


def card_sprite(w, h, r, fill=SURFACE, border=BORDER, bw=1) -> Image.Image:
    base = rr_sprite(w, h, r, fill)
    if border is not None and bw > 0:
        base.alpha_composite(solid_with_mask(border, rr_ring_mask(w, h, r, bw)))
    return base


def ring_sprite(w, h, r, color, bw: int = 2, alpha: float = 1.0) -> Image.Image:
    return solid_with_mask(color, rr_ring_mask(w, h, r, bw), alpha)


_SHADOW_CACHE: dict = {}


def shadow(w, h, r, blur=12, alpha=24, dy=3):
    """Soft warm shadow sprite. Returns (sprite, pad); blit at (x - pad, y - pad)."""
    key = (w, h, r, blur, alpha, dy)
    hit = _SHADOW_CACHE.get(key)
    if hit is not None:
        return hit
    pad = blur * 3
    m = Image.new("L", (w + 2 * pad, h + 2 * pad), 0)
    m.paste(rr_mask(w, h, r), (pad, pad + dy))
    m = m.filter(ImageFilter.GaussianBlur(blur))
    m = m.point(lambda v: int(v * alpha / 255.0 + 0.5))
    out = (solid_with_mask(SHADOW_RGB, m), pad)
    _SHADOW_CACHE[key] = out
    return out


_CIRCLE_CACHE: dict = {}


def circle_sprite(d: int, color) -> Image.Image:
    key = (d, tuple(color))
    hit = _CIRCLE_CACHE.get(key)
    if hit is not None:
        return hit
    ss = 4
    big = Image.new("L", (d * ss, d * ss), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=255)
    out = solid_with_mask(color, big.resize((d, d), BOX))
    _CIRCLE_CACHE[key] = out
    return out


def hairline_h(cv, x0, x1, y, color=BORDER, alpha=1.0) -> None:
    blit(cv, Image.new("RGBA", (int(x1 - x0), 1), tuple(color) + (255,)), x0, y, alpha)


def hairline_v(cv, x, y0, y1, color=BORDER, alpha=1.0) -> None:
    blit(cv, Image.new("RGBA", (1, int(y1 - y0)), tuple(color) + (255,)), x, y0, alpha)


def pill_sprite(text, role, size, fg, bg, h, padx, tracking=0.0) -> Image.Image:
    """Fully rounded label with centred text (text baseline sits on the optical centre)."""
    adv = advance(text, role, size, tracking)
    w = math.ceil(adv + 2 * padx)
    base = rr_sprite(w, h, h // 2, bg)
    s, ox, oy = text_sprite(text, role, size, fg, tracking)
    baseline = (h + CAP_RATIO * size) / 2.0
    blit(base, s, padx - ox, round(baseline) - oy)
    return base


# ----------------------------------------------------------------- photos ---
@lru_cache(maxsize=None)
def load_rgb(path: str) -> Image.Image:
    with Image.open(path) as im:
        return im.convert("RGB")


class Photo:
    def __init__(self, body, w, h, shadow_spr, pad):
        self.body, self.w, self.h = body, w, h
        self.shadow, self.pad = shadow_spr, pad


_PHOTO_CACHE: dict = {}


def photo(path, w, h, r=10, mode="cover", center=(0.5, 0.5), bg=STONE,
          with_shadow=True, shadow_alpha=22) -> Photo:
    """Rounded, 1px-hairline photo card. mode='cover' crops to fill; 'contain'
    letterboxes on a flat STONE panel (use for square art in wide cells)."""
    key = (str(path), w, h, r, mode, center, tuple(bg), with_shadow, shadow_alpha)
    hit = _PHOTO_CACHE.get(key)
    if hit is not None:
        return hit
    src = load_rgb(str(path))
    if mode == "cover":
        body = ImageOps.fit(src, (w, h), method=LANCZOS, centering=center).convert("RGBA")
    elif mode == "contain":
        fitted = ImageOps.contain(src, (w, h), method=LANCZOS)
        body = Image.new("RGBA", (w, h), tuple(bg) + (255,))
        body.paste(fitted.convert("RGBA"), ((w - fitted.width) // 2, (h - fitted.height) // 2))
    else:
        raise ValueError(mode)
    body.putalpha(rr_mask(w, h, r))
    body.alpha_composite(solid_with_mask(BORDER, rr_ring_mask(w, h, r, 1)))
    if with_shadow:
        sh, pad = shadow(w, h, r, blur=12, alpha=shadow_alpha, dy=4)
    else:
        sh, pad = None, 0
    ph = Photo(body, w, h, sh, pad)
    _PHOTO_CACHE[key] = ph
    return ph


def blit_photo(canvas, ph: Photo, x, y, alpha=1.0, scale=1.0, dy=0.0) -> None:
    """Draw a Photo with its top-left at (x, y). scale is about the photo centre."""
    if alpha <= 0.001:
        return
    cx = x + ph.w / 2.0
    cy = y + ph.h / 2.0 + dy
    if abs(scale - 1.0) < 1e-4:
        body, sh, pad = ph.body, ph.shadow, ph.pad
    else:
        body = ph.body.resize((max(1, round(ph.w * scale)), max(1, round(ph.h * scale))), BILINEAR)
        sh, pad = None, 0
        if ph.shadow is not None:
            sh = ph.shadow.resize((round(ph.shadow.width * scale), round(ph.shadow.height * scale)), BILINEAR)
            pad = ph.pad * scale
    bx = cx - body.width / 2.0
    by = cy - body.height / 2.0
    if sh is not None:
        blit(canvas, sh, bx - pad, by - pad, alpha)
    blit(canvas, body, bx, by, alpha)


# ------------------------------------------------------------------ nodes ---
_NODE_CACHE: dict = {}


def node_card(title, lines, w, h, active=False, tag=None):
    """Workflow node. Returns (sprite, shadow_sprite, shadow_pad)."""
    key = (title, tuple(lines), w, h, active, tag)
    hit = _NODE_CACHE.get(key)
    if hit is not None:
        return hit
    px = 26
    if active:
        base = rr_sprite(w, h, 16, TERRA)
        t_col, s_col, m_col = WHITE, (255, 226, 220), (255, 226, 220)
    else:
        base = card_sprite(w, h, 16, SURFACE, BORDER, 1)
        t_col, s_col, m_col = INK, SLATE, MUTED
    assert advance(title, "semibold", 30) <= w - 2 * px, f"node title overflows: {title}"
    s, ox, oy = text_sprite(title, "semibold", 30, t_col)
    blit(base, s, px - ox, 46 - oy)
    for i, line in enumerate(lines):
        assert advance(line, "regular", 22) <= w - 2 * px, f"node line overflows: {line}"
        s, ox, oy = text_sprite(line, "regular", 22, s_col)
        blit(base, s, px - ox, 80 + i * 30 - oy)
    if tag:
        adv = advance(tag, "mono", 20)
        s, ox, oy = text_sprite(tag, "mono", 20, m_col)
        blit(base, s, w - px - adv - ox, 46 - oy)
    if active:
        sh, pad = None, 0
    else:
        sh, pad = shadow(w, h, 16, blur=10, alpha=26, dy=4)
    out = (base, sh, pad)
    _NODE_CACHE[key] = out
    return out


def blit_node(canvas, node, x, y, alpha=1.0, dy=0.0) -> None:
    spr, sh, pad = node
    if sh is not None:
        blit(canvas, sh, x - pad, y + dy - pad, alpha)
    blit(canvas, spr, x, y + dy, alpha)


def bezier(p0, p1, p2, p3, u: float):
    v = 1.0 - u
    b0, b1, b2, b3 = v * v * v, 3 * v * v * u, 3 * v * u * u, u * u * u
    return (b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0],
            b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1])


def wire(canvas, p0, p3, progress: float, pulse=None, color=WIRE, width=2.5,
         pulse_color=TERRA, alpha=1.0) -> None:
    """Cubic Bezier wire from p0 to p3, drawn to progress in [0, 1]. If pulse is
    given (0..1), a dot travels along the drawn part at that fraction."""
    if progress <= 0.001:
        return
    dx = (p3[0] - p0[0]) * 0.5
    c1 = (p0[0] + dx, p0[1])
    c2 = (p3[0] - dx, p3[1])
    pad = 14
    minx = min(p0[0], p3[0]) - pad
    miny = min(p0[1], p3[1]) - pad
    bw = math.ceil(abs(p3[0] - p0[0]) + 2 * pad)
    bh = math.ceil(abs(p3[1] - p0[1]) + 2 * pad)
    ss = 4
    layer = Image.new("RGBA", (bw * ss, bh * ss), tuple(color) + (0,))
    d = ImageDraw.Draw(layer)
    n = max(12, int(160 * progress))
    pts = []
    for kk in range(n + 1):
        x, y = bezier(p0, c1, c2, p3, progress * kk / n)
        pts.append(((x - minx) * ss, (y - miny) * ss))
    d.line(pts, fill=tuple(color) + (255,), width=int(round(width * ss)), joint="curve")
    if pulse is not None:
        x, y = bezier(p0, c1, c2, p3, pulse * progress)
        r = 5.0 * ss
        cx, cy = (x - minx) * ss, (y - miny) * ss
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=tuple(pulse_color) + (255,))
    blit(canvas, layer.resize((bw, bh), LANCZOS), minx, miny, alpha)


_GRID_CACHE: dict = {}


def dot_grid(w: int, h: int, step: int = 32, radius: float = 1.25,
             color=BORDER, bg=BG) -> Image.Image:
    """Alabaster canvas with a subtle dot grid for the node-graph scene."""
    base = Image.new("RGBA", (w, h), tuple(bg) + (255,))
    ss = 4
    tile = Image.new("L", (step * ss, step * ss), 0)
    r = radius * ss
    c = step * ss / 2.0
    ImageDraw.Draw(tile).ellipse((c - r, c - r, c + r, c + r), fill=255)
    tile = tile.resize((step, step), BOX)
    mask = Image.new("L", (w, h), 0)
    for yy in range(0, h, step):
        for xx in range(0, w, step):
            mask.paste(tile, (xx, yy))
    base.alpha_composite(solid_with_mask(color, mask))
    return base


def alabaster(w: int = 1920, h: int = 1080) -> Image.Image:
    return Image.new("RGBA", (w, h), BG + (255,))


def bottom_left_scrim(canvas_w=1920, canvas_h=1080, reach_x=1500, top_y=520, strength=0.8):
    """Soft dark scrim that exists only in the bottom-left, behind caption text.
    Returns (sprite, x, y) for blit."""
    rh = canvas_h - top_y
    ys = np.arange(rh, dtype=np.float32)
    ay = np.clip(ys / (rh - 1), 0.0, 1.0) ** 0.9
    xs = np.arange(reach_x, dtype=np.float32)
    t = np.clip((xs - 500.0) / (reach_x - 500.0), 0.0, 1.0)
    ax = 1.0 - (t * t * (3.0 - 2.0 * t))
    a = (strength * ay[:, None] * ax[None, :] * 255.0).astype(np.uint8)
    return solid_with_mask(INK, Image.fromarray(a)), 0, top_y


# ------------------------------------------------------------ video I/O ---
class ClipReader:
    """Sequential RGB24 frames decoded from a clip at a given fps, via a pipe."""

    def __init__(self, path, n_frames: int, w: int = 1920, h: int = 1080, fps: int = 30):
        self.w, self.h, self.n = w, h, int(n_frames)
        self.size = w * h * 3
        self.count = 0
        self.last = None
        cmd = [FFMPEG, "-hide_banner", "-v", "error", "-nostdin", "-i", str(path),
               "-an", "-vf", f"fps={fps},scale={w}:{h}:flags=lanczos",
               "-frames:v", str(self.n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)

    def next(self) -> Image.Image:
        if self.count >= self.n:
            raise RuntimeError("clip reader exhausted")
        buf = self.proc.stdout.read(self.size)
        if len(buf) != self.size:
            raise RuntimeError(f"short read at frame {self.count} of {self.n}")
        self.count += 1
        self.last = Image.frombytes("RGB", (self.w, self.h), buf)
        return self.last

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait()
        finally:
            if self.proc.stdout:
                self.proc.stdout.close()


class RgbPipe:
    """Streams RGB24 frames into ffmpeg: H.264 High 4.1, yuv420p, BT.709 tagged."""

    def __init__(self, out_path, w=1920, h=1080, fps=30, crf=19, maxrate="6M", bufsize="12M"):
        self.w, self.h = w, h
        cmd = [FFMPEG, "-hide_banner", "-v", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
               "-vf", "scale=out_color_matrix=bt709:out_range=tv",
               "-an", "-c:v", "libx264", "-preset", "medium", "-profile:v", "high", "-level:v", "4.1",
               "-pix_fmt", "yuv420p", "-crf", str(crf), "-maxrate", maxrate, "-bufsize", bufsize,
               "-g", str(2 * fps), "-r", str(fps),
               "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
               "-color_range", "tv",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv",
               "-movflags", "+faststart", str(out_path)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, img: Image.Image) -> None:
        if img.size != (self.w, self.h) or img.mode != "RGB":
            raise ValueError(f"bad frame {img.size} {img.mode}")
        self.proc.stdin.write(img.tobytes())

    def close(self) -> None:
        self.proc.stdin.close()
        rc = self.proc.wait()
        if rc != 0:
            raise RuntimeError(f"ffmpeg encode failed with exit code {rc}")
