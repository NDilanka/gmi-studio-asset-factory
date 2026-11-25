"""Render factory-card.png (1600x900): one brief, two different worlds."""

from __future__ import annotations

import time

from PIL import Image

import kit as k

OUT = k.ROOT / "factory-card.png"
W, H = 1600, 900
M = 72  # outer margin

# Grid geometry. Both columns share the same top and bottom edges.
LABEL_CAP_TOP = 212
IMG_TOP = 246
CELL_H = 255
GAP_Y = 16
STACK_H = 2 * CELL_H + GAP_Y          # 526
LEFT_W = 415                          # fixed by the stacked-height budget (16:9 over 3:2)
DASH_H = round(LEFT_W * 9 / 16)       # 233
PACK_H = round(LEFT_W * 2 / 3)        # 277
DIV_X = M + LEFT_W + 56               # 543
RIGHT_X = DIV_X + 56                  # 599
CELL_W = 456
GAP_X = 17
METRIC_BASE = 818
ICON_MODE = "contain"                 # preserve full 1:1 icon geometry on STONE ground
assert DASH_H + GAP_Y + PACK_H == STACK_H
assert RIGHT_X + 2 * CELL_W + GAP_X == W - M


def main() -> None:
    t0 = time.time()
    m = k.load_metrics()
    k.verify_metrics(m)
    s = k.metric_strings(m)

    cv = k.alabaster(W, H)
    k.draw_text(cv, "GMI STUDIO PATTERN  \u00b7  SEEDREAM API RUN", "semibold", 18, k.TERRA,
                M, k.cap_baseline(M, 18), tracking=2.0)
    k.draw_text(cv, "Same brief. Two different worlds.", "bold", 56, k.INK,
                M, k.cap_baseline(108, 56), tracking=-1.0)

    lab = k.cap_baseline(LABEL_CAP_TOP, 19)
    k.draw_text(cv, "UNBATCHED PROMPTS", "semibold", 19, k.SLATE, M, lab, tracking=1.2)
    k.draw_text(cv, "2-ANCHOR BATCH REMIX", "semibold", 19, k.TERRA, RIGHT_X, lab, tracking=1.2)
    k.hairline_v(cv, DIV_X, LABEL_CAP_TOP, IMG_TOP + STACK_H)

    # Left: the two real baseline images, 16:9 over 3:2.
    k.blit_photo(cv, k.photo(k.BASELINE_DIR / "baseline_dashboard.png", LEFT_W, DASH_H, r=10), M, IMG_TOP)
    k.blit_photo(cv, k.photo(k.BASELINE_DIR / "baseline_packaging.png", LEFT_W, PACK_H, r=10),
                 M, IMG_TOP + DASH_H + GAP_Y)

    # Right: 2x2 grid of real factory outputs.
    cells = [
        ("anchor_hardware.png", "ANCHOR", "cover"),
        ("asset_app_icon.png", "1:1 ICON", ICON_MODE),
        ("asset_packaging.png", "3:2 BOX", "cover"),
        ("asset_social_banner.png", "16:9 BANNER", "cover"),
    ]
    for idx, (fname, tag, mode) in enumerate(cells):
        r, c = divmod(idx, 2)
        x = RIGHT_X + c * (CELL_W + GAP_X)
        y = IMG_TOP + r * (CELL_H + GAP_Y)
        ph = k.photo(k.FACTORY_DIR / fname, CELL_W, CELL_H, r=10, mode=mode)
        k.blit_photo(cv, ph, x, y)
        pill = k.pill_sprite(tag, "semibold", 17, k.INK, k.SURFACE, h=36, padx=14, tracking=0.8)
        k.blit(cv, pill, x + 14, y + 14)

    # Metric lines.
    left_txt = f"{s['bl_terra']} terra accent  \u00b7  {s['bl_std']} L* std dev"
    w_left = k.draw_text(cv, left_txt, "semibold", 22, k.SLATE, M, METRIC_BASE)
    assert w_left <= LEFT_W, f"left metric overflows: {w_left}"
    x = RIGHT_X
    for txt, col in [
        (f"{s['fa_terra']} terra accent  \u00b7  {s['fa_std']} L* std dev (", k.INK),
        (f"-{s['red_int']}%", k.TERRA),
        (")", k.INK),
    ]:
        x += k.draw_text(cv, txt, "bold", 22, col, x, METRIC_BASE)
    assert x - RIGHT_X <= 2 * CELL_W + GAP_X, "right metric overflows"

    cv.convert("RGB").save(OUT, optimize=True)
    chk = Image.open(OUT)
    print(f"wrote {OUT} size={chk.size} mode={chk.mode} in {time.time() - t0:.1f}s")
    print(f"left metric: {left_txt!r}")
    print(f"right metric: {x - RIGHT_X:.0f}px wide")
    assert chk.size == (W, H)


if __name__ == "__main__":
    main()
