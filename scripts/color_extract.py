#!/usr/bin/env python3
"""MuseLab :: colour extraction from real pixels.

Two honest modes:
  --sample pixels   (default) k-means in CIELAB over the actual pixels -> measured values
  --no-pixels       emit an empty, clearly-labelled skeleton for a vision-only estimate

The output always states which one was used (`extraction_method`), so a measured
palette can never be mistaken for a guess, or the reverse.

Exit codes: 0 ok, 2 bad input, 3 capability missing
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

# --------------------------------------------------------------------- colour math
def srgb_to_linear(c: np.ndarray) -> np.ndarray:
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """rgb: (...,3) uint8/float in 0..255 -> CIE Lab (D65)."""
    lin = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    xyz = xyz / white
    eps = 216 / 24389
    kappa = 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def delta_e76(lab1, lab2) -> float:
    return float(np.sqrt(np.sum((np.asarray(lab1) - np.asarray(lab2)) ** 2)))


def lab_to_hex(lab) -> str:
    """Rounded representative sRGB hex for a Lab centroid."""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[0] + 16) / 116
    fx = fy + lab[1] / 500
    fz = fy - lab[2] / 200
    eps = 216 / 24389
    kappa = 24389 / 27
    xr = np.where(fx ** 3 > eps, fx ** 3, (116 * fx - 16) / kappa)
    yr = np.where(lab[0] > kappa * eps, fy ** 3, lab[0] / kappa)
    zr = np.where(fz ** 3 > eps, fz ** 3, (116 * fz - 16) / kappa)
    xyz = np.array([xr, yr, zr]) * np.array([0.95047, 1.0, 1.08883])
    m_inv = np.array([[3.2404542, -1.5371385, -0.4985314],
                      [-0.9692660, 1.8760108, 0.0415560],
                      [0.0556434, -0.2040259, 1.0572252]])
    lin = m_inv @ xyz
    lin = np.clip(lin, 0, 1)
    srgb = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * lin ** (1 / 2.4) - 0.055)
    rgb = np.clip(np.round(srgb * 255), 0, 255).astype(int)
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def hex_to_rgb(hexv: str) -> tuple[int, int, int]:
    v = hexv.strip().lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def wcag_contrast(rgb1, rgb2) -> float:
    def rel(rgb):
        c = srgb_to_linear(np.asarray(rgb, dtype=np.float64))
        return float(0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])
    l1, l2 = sorted([rel(rgb1), rel(rgb2)], reverse=True)
    return round((l1 + 0.05) / (l2 + 0.05), 2)


# ------------------------------------------------------------------- colour naming
HUE_FAMILIES = [
    # boundaries are CIELAB hue angles (atan2(b*, a*)), not HSL hues:
    # red~40, orange~70, yellow~100, green~135, cyan~200, blue~300, purple~320
    (20, "洋红"), (45, "红"), (75, "橙"), (105, "黄"), (140, "黄绿"), (175, "绿"),
    (215, "青"), (250, "青蓝"), (300, "蓝"), (325, "蓝紫"), (350, "紫"), (361, "洋红"),
]
LIGHT_ZH = [(18, "墨"), (32, "深"), (48, "暗"), (68, ""), (84, "浅"), (96, "粉"), (101, "纯")]
NEUTRAL_NAMES = [(6, "墨黑"), (18, "炭黑"), (38, "深灰"), (62, "中灰"), (82, "浅灰"),
                 (94, "灰白"), (99, "象牙白"), (101, "纯白")]


def hue_family(h: float) -> str:
    for bound, name in HUE_FAMILIES:
        if h < bound:
            return name
    return "红"


def name_zh(lab) -> str:
    L, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    chroma = float(np.hypot(a, b))
    if chroma < 9:
        for bound, nm in NEUTRAL_NAMES:
            if L < bound:
                return nm
        return "纯白"
    h = (np.degrees(np.arctan2(b, a)) + 360) % 360
    fam = hue_family(h)
    lw = ""
    for bound, word in LIGHT_ZH:
        if L < bound:
            lw = word
            break
    if chroma < 22:  # muted / desaturated -> "灰蓝" style names
        muted_prefix = {"墨": "暗", "深": "暗", "暗": "暗", "浅": "淡", "粉": "淡"}.get(lw, "")
        return f"{muted_prefix}灰{fam}" if muted_prefix else f"灰{fam}"
    return f"{lw}{fam}" if lw else f"{fam}"


# ------------------------------------------------------------------------ k-means
def kmeans_lab(pixels_lab: np.ndarray, k: int, seed: int = 7, iters: int = 24):
    rng = np.random.default_rng(seed)
    n = len(pixels_lab)
    # k-means++ initialisation
    centers = [pixels_lab[rng.integers(n)]]
    for _ in range(k - 1):
        d = np.min(((pixels_lab[:, None, :] - np.array(centers)[None, :, :]) ** 2).sum(-1), axis=1)
        probs = np.maximum(d, 0) + 1e-12
        probs = probs / probs.sum()
        centers.append(pixels_lab[rng.choice(n, p=probs)])
    centers = np.array(centers)
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        d = ((pixels_lab[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        new_labels = d.argmin(1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for j in range(k):
            m = labels == j
            if m.any():
                centers[j] = pixels_lab[m].mean(0)
    counts = np.bincount(labels, minlength=k).astype(float)
    return centers, counts / counts.sum()


def merge_close(colors, weights, threshold=6.0, max_colors=7, min_ratio=0.02):
    items = sorted(zip(colors, weights), key=lambda t: -t[1])
    merged: list[list] = []
    for lab, w in items:
        for entry in merged:
            if delta_e76(entry[0], lab) < threshold:
                total = entry[1] + w
                entry[0] = (entry[0] * entry[1] + lab * w) / total
                entry[1] = total
                break
        else:
            merged.append([np.array(lab, dtype=float), w])
    merged.sort(key=lambda e: -e[1])
    kept = [e for e in merged if e[1] >= min_ratio][:max_colors]
    if len(kept) < 3:
        kept = merged[:3]
    total = sum(e[1] for e in kept)
    return [(e[0], e[1] / total) for e in kept]


def assign_roles(entries):
    """primary / secondary / accent, based on area share and chroma."""
    out = []
    for i, (lab, ratio) in enumerate(entries):
        chroma = float(np.hypot(lab[1], lab[2]))
        if i == 0:
            role = "primary"
        elif ratio >= 0.10 and i <= 2:
            role = "secondary"
        else:
            role = "accent"
        out.append((lab, ratio, role, chroma))
    # a strongly chromatic small cluster is a better accent than a muddy mid-grey
    if len(out) >= 3:
        accent_idx = max(range(2, len(out)), key=lambda i: out[i][3] * (out[i][1] ** 0.5), default=None)
        if accent_idx is not None and out[accent_idx][3] > 25 and out[accent_idx][2] != "primary":
            lab, ratio, _, chroma = out[accent_idx]
            out[accent_idx] = (lab, ratio, "accent", chroma)
    return out


# ------------------------------------------------------------------------- report
def mood_metrics(pixels_rgb: np.ndarray) -> dict:
    lab = rgb_to_lab(pixels_rgb)
    L = lab[:, 0]
    a = lab[:, 1]
    b = lab[:, 2]
    chroma = np.hypot(a, b)
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    chromatic = chroma > 10
    warm = chromatic & ((hue < 110) | (hue >= 315))
    cool = chromatic & (hue >= 180) & (hue < 300)
    neutral = ~chromatic
    denom = max(int(chromatic.sum()), 1)
    return {
        "mean_lightness": round(float(L.mean()), 1),
        "lightness_range": [round(float(np.percentile(L, 5)), 1), round(float(np.percentile(L, 95)), 1)],
        "mean_chroma": round(float(chroma.mean()), 1),
        "max_chroma": round(float(np.percentile(chroma, 98)), 1),
        "warm_ratio": round(float(warm.sum() / denom), 3),
        "cool_ratio": round(float(cool.sum() / denom), 3),
        "neutral_ratio": round(float(neutral.mean()), 3),
        "temperature": "warm" if warm.sum() > cool.sum() * 1.2 else ("cool" if cool.sum() > warm.sum() * 1.2 else "balanced"),
        "contrast_spread": round(float(np.percentile(L, 95) - np.percentile(L, 5)), 1),
        "mood_words": [],
    }


def mood_words(m: dict) -> list[str]:
    words = []
    if m["mean_lightness"] > 68:
        words.append("明亮")
    elif m["mean_lightness"] < 38:
        words.append("低沉")
    else:
        words.append("中调")
    if m["mean_chroma"] < 12:
        words.append("素净")
    elif m["mean_chroma"] > 34:
        words.append("浓郁")
    else:
        words.append("柔和")
    if m["contrast_spread"] > 62:
        words.append("对比强烈")
    elif m["contrast_spread"] < 30:
        words.append("层次平缓")
    if m["temperature"] == "warm":
        words.append("偏暖")
    elif m["temperature"] == "cool":
        words.append("偏冷")
    return words


CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]


def load_font(size: int):
    from PIL import ImageFont
    for path in CJK_FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def swatch_image(entries, out_path, width=900, height=170):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), "#FFFFFF")
    d = ImageDraw.Draw(img)
    font = load_font(16)
    font_small = load_font(13)
    n = len(entries)
    pad = 12
    cw = (width - pad * (n + 1)) / n
    for i, e in enumerate(entries):
        x0 = pad + i * (cw + pad)
        x1 = x0 + cw
        d.rectangle([x0, pad, x1, height - 46], fill=e["hex"])
        d.text((x0 + 2, height - 40), e["hex"], fill="#111111", font=font)
        role_mark = {"primary": "主", "secondary": "辅", "accent": "点"}[e["role"]]
        d.text((x0 + 2, height - 21), f"{e['name_zh']}·{role_mark} {e['area_ratio']*100:.0f}%",
               fill="#444444", font=font_small)
    img.save(out_path, "PNG")
    return os.path.abspath(out_path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure a palette from real image pixels.")
    ap.add_argument("image")
    ap.add_argument("--mode", default="color", choices=["color", "mood"],
                    help="color = representative colours; mood = colours + atmosphere")
    ap.add_argument("--max-colors", type=int, default=7)
    ap.add_argument("--min-colors", type=int, default=3)
    ap.add_argument("--mask", default=None,
                    help="optional grayscale/alpha mask PNG: only sample where mask > 127")
    ap.add_argument("--crop", default=None, help="x,y,w,h in pixels")
    ap.add_argument("--no-pixels", action="store_true",
                    help="emit a labelled skeleton for a vision-only estimate (no measurement)")
    ap.add_argument("--swatch", default=None, help="write a palette swatch PNG here")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        print(json.dumps({"ok": False, "error": "input_not_found", "path": args.image}), file=sys.stderr)
        return 2

    if args.no_pixels:
        result = {
            "ok": True,
            "extraction_method": "visual_estimate",
            "is_measured": False,
            "source_image": os.path.abspath(args.image),
            "color_palette": [],
            "warning": ("No pixel access was used. Colour values are approximate and MUST be labelled "
                        "as estimates in the reply. Run without --no-pixels to measure real values."),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    from PIL import Image

    img = Image.open(args.image)
    if img.mode in {"RGBA", "LA", "P"}:
        img = img.convert("RGBA")
        alpha = np.asarray(img.getchannel("A"))
        rgb_img = img.convert("RGB")
    else:
        rgb_img = img.convert("RGB")
        alpha = None

    if args.crop:
        x, y, w, h = [int(v) for v in args.crop.split(",")]
        rgb_img = rgb_img.crop((x, y, x + w, y + h))
        if alpha is not None:
            alpha = alpha[y:y + h, x:x + w]

    arr = np.asarray(rgb_img, dtype=np.uint8)
    mask_arr = None
    if args.mask:
        m = Image.open(args.mask)
        if m.mode not in {"L", "1"}:
            m = m.convert("L") if m.mode != "RGBA" else m.getchannel("A")
        m = m.resize((arr.shape[1], arr.shape[0]), Image.NEAREST)
        mask_arr = np.asarray(m.convert("L"))

    flat = arr.reshape(-1, 3)
    keep = np.ones(len(flat), dtype=bool)
    if alpha is not None:
        keep &= alpha.reshape(-1) > 16
    if mask_arr is not None:
        keep &= mask_arr.reshape(-1) > 127
    if keep.sum() < 50:
        print(json.dumps({"ok": False, "error": "empty_sample",
                          "message": "Fewer than 50 usable pixels after masking."}), file=sys.stderr)
        return 2

    sample = flat[keep]
    step = max(1, len(sample) // 60000)
    sample = sample[::step]
    lab = rgb_to_lab(sample)

    k = max(6, min(10, args.max_colors + 2))
    centers, weights = kmeans_lab(lab, k)
    entries = merge_close(centers, weights, threshold=6.0, max_colors=args.max_colors)
    entries = entries[:max(args.min_colors, min(len(entries), args.max_colors))]
    roles = assign_roles(entries)

    palette = []
    for lab_c, ratio, role, chroma in roles:
        hexv = lab_to_hex(lab_c)
        rgb = hex_to_rgb(hexv)
        palette.append({
            "name_zh": name_zh(lab_c),
            "hex": hexv,
            "rgb": list(rgb),
            "lab": [round(float(v), 1) for v in lab_c],
            "chroma": round(chroma, 1),
            "area_ratio": round(float(ratio), 4),
            "role": role,
            "design_use": {
                "primary": "主体材质色 / 大面积色块 / 主产品主色",
                "secondary": "过渡材质 / 次要结构 / 叠戴搭配色",
                "accent": "金属件 / 单颗点缀珠 / 视觉重点",
            }[role],
        })

    contrast_pairs = []
    for i in range(len(palette)):
        for j in range(i + 1, len(palette)):
            contrast_pairs.append({
                "pair": [palette[i]["hex"], palette[j]["hex"]],
                "wcag_contrast": wcag_contrast(palette[i]["rgb"], palette[j]["rgb"]),
                "delta_e": round(delta_e76(palette[i]["lab"], palette[j]["lab"]), 1),
            })
    contrast_pairs.sort(key=lambda p: -p["delta_e"])

    mood = mood_metrics(sample)
    mood["mood_words"] = mood_words(mood)

    result = {
        "ok": True,
        "extraction_method": "pixel_kmeans_lab",
        "is_measured": True,
        "source_image": os.path.abspath(args.image),
        "image_size": [int(arr.shape[1]), int(arr.shape[0])],
        "sample_pixels": int(len(sample)),
        "mode": args.mode,
        "color_palette": palette,
        "dominant_colors": [p["hex"] for p in palette if p["role"] == "primary"],
        "atmosphere": mood if args.mode == "mood" else {"mood_words": mood["mood_words"],
                                                        "temperature": mood["temperature"]},
        "relationships": {
            "highest_contrast_pairs": contrast_pairs[:3],
            "palette_span_delta_e": round(max([p["delta_e"] for p in contrast_pairs], default=0.0), 1),
        },
        "confidence_notes": [
            "Values are measured from pixels (CIELAB k-means), not guessed; per-swatch rounding to sRGB is ±1.",
            "Area ratios are pixel shares within the analysed region, not design intent.",
            "Colour names are heuristic labels generated from hue/lightness/chroma.",
        ],
    }

    if args.swatch:
        result["swatch_image"] = swatch_image(palette, args.swatch)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
