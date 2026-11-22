#!/usr/bin/env python3
"""MuseLab :: real (non-generative) subject cutout using macOS Vision foreground masks.

This produces a TRUE alpha channel from the platform's instance-segmentation model.
It does not redraw, repaint or hallucinate the subject.

Platform: macOS 14+ (VNDetectForegroundInstanceMaskRequest family, available 13+/14+).
Requires: pyobjc-framework-Vision, pyobjc-framework-Quartz, Pillow, numpy.

Exit codes:
  0  success
  3  capability missing (Vision / pyobjc not available) -> caller must degrade to Mode B
  4  no foreground subject found -> caller must ask the user or degrade
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys

def _fail_capability(msg: str, detail: str = "") -> "NoReturn":  # noqa: F821
    print(json.dumps({
        "ok": False,
        "error": "capability_missing",
        "message": msg,
        "detail": detail,
        "degrade": "Mode B (prompt / instruction mode). Do not claim a cutout was produced.",
    }, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(3)


def _fail_no_subject(msg: str) -> "NoReturn":  # noqa: F821
    print(json.dumps({
        "ok": False,
        "error": "no_subject",
        "message": msg,
        "degrade": "Ask the user which object to keep, or degrade to Mode B.",
    }, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(4)


def vision_mask(image_path: str, single_index: int | None = None):
    """Return (alpha_float32 HxW in 0..1, meta dict)."""
    try:
        import Vision
        import Quartz
        from Foundation import NSURL
    except Exception as exc:  # pragma: no cover - platform dependent
        _fail_capability(
            "macOS Vision bindings unavailable (pyobjc-framework-Vision missing?).",
            f"{type(exc).__name__}: {exc}",
        )

    if not hasattr(Vision, "VNGenerateForegroundInstanceMaskRequest"):
        _fail_capability("This macOS build has no VNGenerateForegroundInstanceMaskRequest.")

    url = NSURL.fileURLWithPath_(os.path.abspath(image_path))
    options = {Vision.VNImageOptionCameraIntrinsics: None}
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    request = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        _fail_capability("Vision request failed.", str(err))

    results = request.results()
    if not results or len(results) == 0:
        _fail_no_subject("Vision returned no observation for this image.")
    obs = results[0]

    instances = obs.allInstances()
    count = int(instances.count()) if instances is not None else 0
    if count == 0:
        _fail_no_subject("No foreground instance detected. The subject may be too small or the image may be a flat texture.")

    if single_index is not None:
        from Foundation import NSIndexSet
        instances = NSIndexSet.indexSetWithIndex_(single_index)

    mask, m_err = obs.generateScaledMaskForImageForInstances_fromRequestHandler_error_(
        instances, handler, None
    )
    if mask is None:
        _fail_capability("generateScaledMaskForImage returned no buffer.", str(m_err))

    import numpy as np
    import Quartz as Q

    Q.CVPixelBufferLockBaseAddress(mask, 1)  # read-only lock
    try:
        width = int(Q.CVPixelBufferGetWidth(mask))
        height = int(Q.CVPixelBufferGetHeight(mask))
        bpr = int(Q.CVPixelBufferGetBytesPerRow(mask))
        fmt = int(Q.CVPixelBufferGetPixelFormatType(mask))
        base = Q.CVPixelBufferGetBaseAddress(mask)
        if base is None:
            _fail_capability("Mask base address is null.")
        # pyobjc hands back a buffer-protocol object (objc.varlist) for CVPixelBuffer data.
        if hasattr(base, "as_buffer"):
            raw = base.as_buffer(bpr * height)
        elif hasattr(base, "as_buffer_"):
            raw = base.as_buffer_(bpr * height)
        else:
            addr = ctypes.cast(base, ctypes.c_void_p).value
            raw = ctypes.string_at(addr, bpr * height)
    finally:
        Q.CVPixelBufferUnlockBaseAddress(mask, 1)

    # 'L008' = OneComponent8 (0x4C303038), 'L00f' = OneComponent32Float (0x4C303066)
    if fmt == 0x4C303038:
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(height, bpr)[:, :width].astype(np.float32) / 255.0
    elif fmt == 0x4C303066:
        arr = np.frombuffer(raw, dtype=np.float32).reshape(height, bpr // 4)[:, :width]
    elif fmt == 0x4C303068:  # OneComponent16
        arr = np.frombuffer(raw, dtype=np.uint16).reshape(height, bpr // 2)[:, :width].astype(np.float32) / 65535.0
    else:
        _fail_capability(f"Unsupported mask pixel format 0x{fmt:X}.")

    if arr.max() > 1.0:
        arr = arr / 255.0
    return arr, {"instance_count": count, "mask_shape": [int(height), int(width)],
                 "pixel_format": f"0x{fmt:X}", "revision": int(request.revision()) if hasattr(request, "revision") else None}


def main() -> int:
    ap = argparse.ArgumentParser(description="Real subject cutout with true alpha (macOS Vision).")
    ap.add_argument("image", help="input image path")
    ap.add_argument("--out", required=True, help="output RGBA PNG path")
    ap.add_argument("--min-alpha", type=float, default=0.02,
                    help="alpha values below this are clamped to 0 (kills faint halo)")
    ap.add_argument("--feather", type=float, default=0.0,
                    help="gaussian blur sigma on the alpha edge, in px (0 = off)")
    ap.add_argument("--despill", action="store_true",
                    help="colour-decontaminate 1px edge pixels to remove white/背景 fringe")
    ap.add_argument("--instance", type=int, default=None,
                    help="keep only this instance index when several subjects exist")
    ap.add_argument("--json", dest="json_out", default=None, help="write report JSON here")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        print(json.dumps({"ok": False, "error": "input_not_found", "path": args.image}), file=sys.stderr)
        return 2

    try:
        from PIL import Image, ImageFilter
        import numpy as np
    except Exception as exc:
        _fail_capability("Pillow/numpy unavailable.", str(exc))

    alpha, meta = vision_mask(args.image, args.instance)

    img = Image.open(args.image)
    src_mode = img.mode
    img = img.convert("RGBA")
    if alpha.shape != (img.height, img.width):
        a_img = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), mode="L")
        a_img = a_img.resize((img.width, img.height), Image.BILINEAR)
        alpha = np.asarray(a_img).astype(np.float32) / 255.0

    alpha = np.clip(alpha, 0.0, 1.0)
    alpha[alpha < args.min_alpha] = 0.0

    if args.feather > 0:
        a_img = Image.fromarray((alpha * 255).astype(np.uint8), mode="L").filter(
            ImageFilter.GaussianBlur(args.feather))
        alpha = np.asarray(a_img).astype(np.float32) / 255.0

    arr = np.asarray(img).astype(np.uint8).copy()
    rgb = arr[..., :3].astype(np.float32)

    if args.despill:
        # Un-premultiply against an estimated background colour along the 1px edge ring.
        edge = (alpha > 0.05) & (alpha < 0.95)
        if edge.any():
            core = alpha >= 0.95
            if core.any():
                mean_core = rgb[core].mean(axis=0)
                # push edge colours toward the opaque core colour, scaled by alpha
                for c in range(3):
                    rgb[..., c] = np.where(
                        edge,
                        np.clip((rgb[..., c] - (1.0 - alpha) * 255.0) / np.maximum(alpha, 0.35), 0, 255),
                        rgb[..., c],
                    )
                del mean_core

    out = np.dstack([rgb.astype(np.uint8), (alpha * 255).astype(np.uint8)])
    Image.fromarray(out, mode="RGBA").save(args.out, "PNG", optimize=True)

    ys, xs = np.where(alpha > 0.5)
    if len(xs) == 0:
        _fail_no_subject("Mask was empty after thresholding.")
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]
    report = {
        "ok": True,
        "method": "vision_foreground_instance_mask",
        "method_detail": "macOS Vision VNGenerateForegroundInstanceMaskRequest — real segmentation, true alpha channel",
        "is_generative": False,
        "input": os.path.abspath(args.image),
        "input_mode": src_mode,
        "output": os.path.abspath(args.out),
        "output_size": [img.width, img.height],
        **meta,
        "subject_bbox": bbox,
        "subject_area_ratio": round(float((alpha > 0.5).mean()), 4),
        "alpha_stats": {
            "transparent_ratio": round(float((alpha <= 0.02).mean()), 4),
            "opaque_ratio": round(float((alpha >= 0.98).mean()), 4),
            "semi_transparent_ratio": round(float(((alpha > 0.02) & (alpha < 0.98)).mean()), 4),
        },
        "color_preserved": True,
        "notes": [
            "Pixels keep their original RGB values; only the alpha channel is added.",
            "If instance_count > 1, several subjects were kept. Re-run with --instance N or ask the user.",
        ],
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
