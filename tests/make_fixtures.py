#!/usr/bin/env python3
"""Generate deterministic local test fixtures (no network, no external assets)."""
import json
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- palette blocks
# Known ground truth: 5 equal-area colours -> validates real pixel measurement.
BLOCKS = [
    ("#0F2B46", "深海蓝"),
    ("#2E8B93", "浅海青"),
    ("#F4EDE4", "贝壳白"),
    ("#D8C3A5", "沙滩米"),
    ("#9CB89A", "海沫绿"),
]
W, H = 1000, 200
img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
for i, (hexv, _) in enumerate(BLOCKS):
    d.rectangle([i * (W // 5), 0, (i + 1) * (W // 5) - 1, H], fill=hexv)
img.save(os.path.join(OUT, "palette_blocks.png"))

with open(os.path.join(OUT, "palette_blocks.truth.json"), "w", encoding="utf-8") as fh:
    json.dump({"expected_hex": [b[0] for b in BLOCKS], "blocks": len(BLOCKS),
               "equal_area": True}, fh, ensure_ascii=False, indent=2)

# ------------------------------------------------- synthetic product photograph
# A bead strand on a light studio background: one obvious foreground object.
PW, PH = 1200, 900
canvas = Image.new("RGB", (PW, PH), "#EDEAE5")
dr = ImageDraw.Draw(canvas)
dr.ellipse([120, 120, PW - 120, PH - 120], fill="#E4E0DA")  # matte sweep
beads = [
    ("#7A5C43", 96), ("#F2E8DC", 96), ("#7A5C43", 96), ("#C9D6D3", 96),
    ("#F2E8DC", 96), ("#7A5C43", 96), ("#C9D6D3", 96), ("#F2E8DC", 96),
]
import math
cx, cy = PW / 2, PH / 2
ring_r = 250
for i in range(46):
    ang = 2 * math.pi * i / 46
    x = cx + ring_r * math.cos(ang)
    y = cy + ring_r * math.sin(ang) * 0.92
    r = 34
    base, _ = beads[i % len(beads)]
    dr.ellipse([x - r, y - r, x + r, y + r], fill=base, outline="#00000022")
    # specular highlight so beads read as 3D
    dr.ellipse([x - r * 0.45, y - r * 0.55, x + r * 0.05, y - r * 0.1], fill="#FFFFFF55")
canvas = canvas.filter(ImageFilter.GaussianBlur(0.4))
canvas.save(os.path.join(OUT, "product_beads.png"))

# ------------------------------------------------------- pure shape (edge cases)
s = Image.new("RGB", (600, 600), "#202020")
ds = ImageDraw.Draw(s)
ds.ellipse([120, 120, 480, 480], fill="#E03A3A")
s.save(os.path.join(OUT, "shape_circle.png"))

# ------------------------------------------------- product-shot gate fixtures
# Built so every fidelity_check branch has a known-correct answer:
#   gate_shot.png              -> seamless bg, hue-correct beads, two sharp metal parts
#   gate_shot_tone.png         -> same hue, ~9 L* darker (lighting/exposure, NOT a material error)
#   gate_shot_wronghue.png     -> hue way off (material error)
#   gate_shot_gradient_bg.png  -> warm lit backdrop, not seamless
#   gate_shot_blur.png         -> bottom metal part defocused, top one sharp
import numpy as np

GW = GH = 480
BG = (242, 239, 233)          # #F2EFE9, the declared background target
BEAD = (18, 141, 151)         # #128D97
METAL = (185, 192, 198)       # #B9C0C6
rng = np.random.default_rng(11)


def base_canvas(bg=BG, noise=1.0):
    arr = np.ones((GH, GW, 3), dtype=np.float32) * np.array(bg, dtype=np.float32)
    arr += rng.uniform(-noise, noise, size=(GH, GW, 1))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def draw_bead(im, cx, cy, radius, base):
    dr = ImageDraw.Draw(im)
    steps = 18
    for i in range(steps):
        t = i / (steps - 1)
        k = (t - 0.5) * 14.0        # ±7 per channel: a soft tonal ramp, no specular blowout
        col = tuple(int(np.clip(c + k, 0, 255)) for c in base)
        r = radius * (1 - i / steps)
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)


def draw_ring(im, cx, cy, r, width, col=METAL):
    dr = ImageDraw.Draw(im)
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=width)


def product_shot(bead_col=BEAD, bg=BG, bead_scale=1.0):
    im = base_canvas(bg=bg)
    draw_bead(im, 150, 240, 34, tuple(int(c * bead_scale) for c in bead_col))
    draw_bead(im, 330, 240, 34, tuple(int(c * bead_scale) for c in bead_col))
    draw_ring(im, 240, 120, 16, 4)      # top fastener — stays sharp
    draw_ring(im, 240, 380, 16, 4)      # bottom fastener
    return im


product_shot().save(os.path.join(OUT, "gate_shot.png"))
product_shot(bead_scale=0.78).save(os.path.join(OUT, "gate_shot_tone.png"))
product_shot(bead_col=(160, 90, 24)).save(os.path.join(OUT, "gate_shot_wronghue.png"))

# warm lit backdrop: same idea as the real Eddy v5 shot (ΔE ~20+, vertical gradient)
grad = np.zeros((GH, GW, 3), dtype=np.float32)
top, bottom = np.array((201, 179, 160), np.float32), np.array((218, 202, 190), np.float32)
for y in range(GH):
    grad[y] = top + (bottom - top) * (y / (GH - 1))
grad += rng.uniform(-1, 1, size=(GH, GW, 1))
gim = Image.fromarray(np.clip(grad, 0, 255).astype(np.uint8), "RGB")
# a cast changes the BACKGROUND and the PRODUCT alike (multiplicative lighting), otherwise the
# fixture is physically impossible and would wrongly look like "material correct, colour wrong".
cast_ratio = (np.array((210, 190, 175), np.float32) / np.array(BG, np.float32))
lit_bead = tuple(int(c) for c in np.clip(np.array(BEAD, np.float32) * cast_ratio, 0, 255))
draw_bead(gim, 150, 240, 34, lit_bead)
draw_bead(gim, 330, 240, 34, lit_bead)
draw_ring(gim, 240, 120, 16, 4)
draw_ring(gim, 240, 380, 16, 4)
gim.save(os.path.join(OUT, "gate_shot_gradient_bg.png"))

# defocus the lower half only
sharp = product_shot()
blurred = sharp.copy()
bottom = sharp.crop((0, 300, GW, GH)).filter(ImageFilter.GaussianBlur(1.6))
blurred.paste(bottom, (0, 300))
blurred.save(os.path.join(OUT, "gate_shot_blur.png"))


# ---------------------------------------------------------- makeup render fixtures
# A synthetic "face" is enough here. makeup_check.py never finds faces — it does
# arithmetic inside rectangles you declare. These fixtures prove the arithmetic reacts
# to hue drift / left-right asymmetry / over-smoothing / lip-colour bleed. They are not
# meant to look like a person, and no claim about real faces rests on them.
MK_W, MK_H = 800, 600
SKIN = np.array((200, 155, 130), np.float32)
mk_rng = np.random.default_rng(7)
yy, xx = np.mgrid[0:MK_H, 0:MK_W]
skin_base = np.zeros((MK_H, MK_W, 3), np.float32)
for i, c in enumerate(SKIN):
    skin_base[..., i] = c * (0.92 + 0.12 * (1 - yy / MK_H)) + mk_rng.normal(0, 5.5, (MK_H, MK_W))
skin_base = np.clip(skin_base, 0, 255)

MAKEUP_EYES = {"left": [190, 230, 120, 80], "right": [490, 230, 120, 80]}
MAKEUP_LIPS = [345, 420, 110, 50]
MAKEUP_SKIN_SAMPLE = [400, 120, 24]   # [x, y, r] on the forehead


def _makeup_patch(im, roi, color, alpha=0.8, feather=2.0):
    x, y, w, h = roi
    lay = Image.new("RGB", (w, h), color)
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse([2, 2, w - 3, h - 3], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(feather)).point(lambda v: int(v * alpha))
    im.paste(lay, (x, y), m)


def makeup_face(eye_col, eye_alpha=0.78, lip_col="#8A4B55", lip_alpha=0.85,
                bleed=False, blur=0.0):
    im = Image.fromarray(skin_base.astype(np.uint8), "RGB")
    for roi in MAKEUP_EYES.values():
        _makeup_patch(im, roi, eye_col, eye_alpha, 2.0)
    _makeup_patch(im, MAKEUP_LIPS, lip_col, lip_alpha, 1.5)
    if bleed:
        _makeup_patch(im, [345, 468, 110, 26], lip_col, 0.45, 4.0)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return im


makeup_face("#2E8B97").save(os.path.join(OUT, "makeup_face_teal.png"))
makeup_face("#C86A5A").save(os.path.join(OUT, "makeup_face_coral.png"))

_asym = makeup_face("#2E8B97", eye_alpha=0.85)
_makeup_patch(_asym, MAKEUP_EYES["right"], "#C86A5A", 0.5, 2.0)
_asym.save(os.path.join(OUT, "makeup_face_asym.png"))

makeup_face("#2E8B97", blur=2.2).save(os.path.join(OUT, "makeup_face_smooth.png"))
makeup_face("#2E8B97", bleed=True).save(os.path.join(OUT, "makeup_face_bleed.png"))

# ---- eyelid coverage / lower-lid fixtures -------------------------------------------------
# The lid box spans brow side -> past the lash line. `coverage` is the fraction of that
# height, measured from the lash side, that carries shadow. That is exactly what
# check_eyelid() measures, so these fixtures can assert on the number, not just on a label.
MK_LID = {"left": [190, 230, 120, 80], "right": [490, 230, 120, 80]}
MK_UNDER = {"left": [190, 318, 120, 16], "right": [490, 318, 120, 16]}

# 平坦肤色：上面那块 skin_base 带纵向明度渐变，会让"离肤色多远"的判据把整块皮肤算成
# 有妆。眼睑检查要测的是覆盖率算法本身，所以这里必须让基线肤色在整张图上成立。
_lid_rng = np.random.default_rng(11)
lid_base = np.clip(SKIN[None, None, :] + _lid_rng.normal(0, 2.0, (MK_H, MK_W, 3)), 0, 255)


def makeup_lids(coverage=0.45, sides=("left", "right"), specular=False, alpha=1.0):
    im = Image.fromarray(lid_base.astype(np.uint8), "RGB")
    for side in sides:
        x, y, w, h = MK_LID[side]
        ch = max(5, int(round(h * coverage)))
        _makeup_patch(im, [x + 8, y + h - ch, w - 16, ch], "#9FB8B6", alpha, 1.2)
    if specular:
        for side in ("left", "right"):
            x, y, w, h = MK_UNDER[side]
            _makeup_patch(im, [x + 12, y, w - 24, 6], "#F4F6FA", 0.95, 0.8)
    return im


makeup_lids(0.45).save(os.path.join(OUT, "makeup_lids_sym.png"))
makeup_lids(0.98).save(os.path.join(OUT, "makeup_lids_full.png"))
_lidasym = makeup_lids(0.30, sides=("left",))
_makeup_patch(_lidasym, [498, 230 + 80 - int(80 * 0.90), 104, int(80 * 0.90)],
              "#9FB8B6", 1.0, 1.2)
_lidasym.save(os.path.join(OUT, "makeup_lids_asym.png"))
makeup_lids(0.45, specular=True).save(os.path.join(OUT, "makeup_under_spec.png"))

print(json.dumps({"fixtures": sorted(os.listdir(OUT))}, ensure_ascii=False))
