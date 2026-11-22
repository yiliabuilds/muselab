#!/usr/bin/env python3
"""MuseLab :: compose a cut-out subject onto a real background.

Uses the true alpha channel from cutout.py. Subject pixels are copied, never
repainted, so the product itself is preserved exactly.

Modes:
  transparent  keep alpha, PNG only
  white / color  solid backdrop (white = RGB 255,255,255)
  image        photographic backdrop (cover-cropped), optional contact shadow

Exit codes: 0 ok, 2 bad input, 3 capability missing, 5 unsupported combination
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def parse_hex(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        raise ValueError(f"invalid hex colour: {value}")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def parse_aspect(value: str) -> tuple[int, int]:
    if ":" in value:
        a, b = value.split(":", 1)
        return int(a), int(b)
    if value in {"square", "1x1"}:
        return 1, 1
    raise ValueError(f"invalid aspect: {value}")


def cover_resize(img, tw: int, th: int):
    from PIL import Image
    ratio = max(tw / img.width, th / img.height)
    nw, nh = max(1, round(img.width * ratio)), max(1, round(img.height * ratio))
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def main() -> int:
    ap = argparse.ArgumentParser(description="Composite a transparent PNG onto a background.")
    ap.add_argument("subject", help="RGBA cutout PNG (from cutout.py)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--bg", default="white", help="white | transparent | #RRGGBB | path to a background image")
    ap.add_argument("--size", type=int, default=1600, help="long edge of the output canvas, px")
    ap.add_argument("--aspect", default="1:1", help="W:H, e.g. 1:1, 4:5, 3:2, 16:9")
    ap.add_argument("--margin", type=float, default=0.08, help="empty border as a fraction of the canvas")
    ap.add_argument("--format", dest="fmt", default=None, choices=["png", "jpg", "jpeg", "webp"])
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--shadow", action="store_true", help="add a synthetic contact shadow (photographic bg only)")
    ap.add_argument("--shadow-strength", type=float, default=0.28)
    ap.add_argument("--keep-frame", action="store_true",
                    help="product mode: keep the cut-out's own canvas and 1:1 pixel placement "
                         "(no bbox crop, no rescale, no recentre). --size/--aspect/--margin are ignored.")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    try:
        from PIL import Image, ImageDraw, ImageFilter
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "capability_missing", "message": str(exc)}), file=sys.stderr)
        return 3

    if not os.path.isfile(args.subject):
        print(json.dumps({"ok": False, "error": "input_not_found", "path": args.subject}), file=sys.stderr)
        return 2

    subj = Image.open(args.subject)
    if subj.mode != "RGBA":
        print(json.dumps({"ok": False, "error": "no_alpha",
                          "message": f"{args.subject} has no alpha channel (mode={subj.mode}). "
                                     "A real cutout is required; do not fake transparency with a checkerboard."}),
              file=sys.stderr)
        return 5

    aw, ah = parse_aspect(args.aspect)
    if args.keep_frame:
        cw, ch = subj.size
    elif aw >= ah:
        cw = args.size
        ch = round(args.size * ah / aw)
    else:
        ch = args.size
        cw = round(args.size * aw / ah)

    if args.keep_frame:
        # 商品模式：不裁 bbox、不缩放、不居中。原有画外关系与像素坐标必须原样保留。
        bbox = (0, 0, subj.width, subj.height)
    else:
        bbox = subj.getchannel("A").point(lambda v: 255 if v > 6 else 0).getbbox()
        if bbox is None:
            print(json.dumps({"ok": False, "error": "empty_alpha",
                              "message": "The alpha channel is fully transparent."}), file=sys.stderr)
            return 5
    if bbox != (0, 0, subj.width, subj.height):
        subj = subj.crop(bbox)

    if args.keep_frame:
        nw, nh, scale = subj.width, subj.height, 1.0
    else:
        usable_w = cw * (1 - 2 * args.margin)
        usable_h = ch * (1 - 2 * args.margin)
        scale = min(usable_w / subj.width, usable_h / subj.height)
        nw, nh = max(1, round(subj.width * scale)), max(1, round(subj.height * scale))
        subj = subj.resize((nw, nh), Image.LANCZOS)

    bg_desc = args.bg
    bg_image_path = None
    if args.bg in {"white", "transparent"}:
        canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 255) if args.bg == "white" else (0, 0, 0, 0))
    elif args.bg.startswith("#"):
        canvas = Image.new("RGBA", (cw, ch), parse_hex(args.bg) + (255,))
    elif os.path.isfile(args.bg):
        bg_image_path = os.path.abspath(args.bg)
        base = cover_resize(Image.open(args.bg).convert("RGB"), cw, ch)
        canvas = base.convert("RGBA")
    else:
        print(json.dumps({"ok": False, "error": "bad_bg",
                          "message": f"--bg accepts white | transparent | #RRGGBB | image path, got {args.bg}"}),
              file=sys.stderr)
        return 2

    if args.keep_frame:
        ox = oy = 0
    else:
        ox = (cw - nw) // 2
        oy = round(ch * args.margin) + (round(ch * (1 - 2 * args.margin)) - nh) // 2

    if args.shadow and bg_image_path:
        alpha = subj.getchannel("A")
        shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh = alpha.filter(ImageFilter.GaussianBlur(max(6, round(min(nw, nh) * 0.035))))
        black = Image.new("RGBA", sh.size, (0, 0, 0, int(255 * args.shadow_strength)))
        shadow.paste(black, (ox, oy + round(nh * 0.02)), sh)
        canvas = Image.alpha_composite(canvas, shadow)

    canvas.alpha_composite(subj, (ox, oy))

    fmt = (args.fmt or ("png" if args.bg == "transparent" else "png")).lower()
    if args.bg == "transparent" and fmt in {"jpg", "jpeg"}:
        print(json.dumps({"ok": False, "error": "unsupported_combination",
                          "message": "JPEG cannot store transparency. Use PNG for transparent output."}),
              file=sys.stderr)
        return 5

    out = args.out
    if fmt in {"jpg", "jpeg"}:
        canvas.convert("RGB").save(out, "JPEG", quality=args.quality, subsampling=0, optimize=True)
    elif fmt == "webp":
        canvas.save(out, "WEBP", quality=args.quality, method=6)
    else:
        canvas.save(out, "PNG", optimize=True)

    report = {
        "ok": True,
        "method": "alpha_composite",
        "is_generative": False,
        "subject_source": os.path.abspath(args.subject),
        "background": bg_desc,
        "background_image": bg_image_path,
        "output": os.path.abspath(out),
        "output_size": [cw, ch],
        "format": fmt,
        "subject_placement": {"x": ox, "y": oy, "w": nw, "h": nh, "scale": round(scale, 4)},
        "frame_preserved": bool(args.keep_frame),
        "has_alpha": args.bg == "transparent",
        "white_is_pure": args.bg == "white",
        "shadow": bool(args.shadow and bg_image_path),
        "notes": [
            "Subject pixels are copied from the cut-out, not regenerated.",
            "A synthetic contact shadow is a visual aid, not a measured light simulation.",
            "keep_frame=true means the subject canvas, placement and any off-frame crop are preserved pixel-for-pixel.",
        ],
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
