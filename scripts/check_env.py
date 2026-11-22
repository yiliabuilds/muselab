#!/usr/bin/env python3
"""MuseLab :: capability probe.

Run this FIRST in any session. It decides whether MuseLab may claim a real image
operation (Mode A) or must fall back to prompt/instruction delivery (Mode B).

It only inspects this machine. Two capabilities live on the agent side and cannot be
detected here, so they are declared by the caller:
  --imagegen yes|no      an image generation / image editing tool is callable
  --websearch yes|no     a live web search tool is callable

Example:
  python3 scripts/check_env.py --imagegen yes --websearch yes
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys


def probe() -> dict:
    caps: dict = {}
    notes: list[str] = []

    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    caps["python"] = {"available": sys.version_info >= (3, 9), "detail": py,
                      "limitation": None if sys.version_info >= (3, 9) else "Python 3.9+ required"}

    try:
        import PIL
        from PIL import Image  # noqa: F401
        caps["pillow"] = {"available": True, "detail": PIL.__version__, "limitation": None}
    except Exception as exc:
        caps["pillow"] = {"available": False, "detail": str(exc),
                          "limitation": "No image I/O. Colour measurement and compositing are unavailable."}

    try:
        import numpy
        caps["numpy"] = {"available": True, "detail": numpy.__version__, "limitation": None}
    except Exception as exc:
        caps["numpy"] = {"available": False, "detail": str(exc),
                         "limitation": "No array math. Measured colour extraction is unavailable."}

    try:
        import jsonschema
        caps["jsonschema"] = {"available": True, "detail": jsonschema.__version__, "limitation": None}
    except Exception:
        caps["jsonschema"] = {"available": False, "detail": "not installed",
                              "limitation": "Schema validation degrades to required-field checks."}

    mac_ver = platform.mac_ver()[0]
    vision_ok = False
    vision_detail = "not macOS"
    if sys.platform == "darwin":
        try:
            import Vision  # noqa: F401
            if hasattr(Vision, "VNGenerateForegroundInstanceMaskRequest"):
                vision_ok = True
                vision_detail = f"macOS {mac_ver} + pyobjc Vision (foreground instance mask)"
            else:
                vision_detail = f"macOS {mac_ver}: foreground instance mask request not present"
        except Exception as exc:
            vision_detail = f"pyobjc-framework-Vision missing ({type(exc).__name__})"
    caps["vision_foreground_mask"] = {
        "available": vision_ok,
        "detail": vision_detail,
        "limitation": None if vision_ok else
        "Real (non-generative) cutout is NOT possible. Never claim a cutout; offer a redraw or Mode B.",
    }

    caps["ffmpeg"] = {"available": shutil.which("ffmpeg") is not None,
                      "detail": shutil.which("ffmpeg") or "not installed",
                      "limitation": "Only needed for video; MuseLab V1 does not use it."}

    if not vision_ok:
        notes.append("Cutout must be declared unavailable, or offered as a generative approximation "
                     "explicitly labelled as such.")
    return {"python_caps": caps, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--imagegen", choices=["yes", "no", "unknown"], default="unknown",
                    help="is an image generation/editing tool callable right now?")
    ap.add_argument("--websearch", choices=["yes", "no", "unknown"], default="unknown")
    ap.add_argument("--json", dest="json_out")
    a = ap.parse_args()

    local = probe()
    caps = local["python_caps"]

    def mode_for(key: str) -> str:
        return "A" if caps.get(key, {}).get("available") else "B"

    matrix = {
        "background_removal": {
            "mode": mode_for("vision_foreground_mask"),
            "primary": "macOS Vision foreground instance mask (true alpha)",
            "no_tool": "Declare cutout impossible; offer a generative redraw labelled as approximation",
        },
        "color_extraction": {
            "mode": mode_for("numpy") if caps["numpy"]["available"] and caps["pillow"]["available"] else "B",
            "primary": "scripts/color_extract.py (CIELAB k-means on real pixels)",
            "no_tool": "Visual estimate only; label values as approximate",
        },
        "background_composite": {
            "mode": mode_for("pillow"),
            "primary": "scripts/compose.py (alpha composite, pure #FFFFFF option)",
            "no_tool": "Provide instructions, not an image",
        },
        "image_generation": {
            "mode": "A" if a.imagegen == "yes" else ("B" if a.imagegen == "no" else "unknown"),
            "primary": "agent image-generation tool with prompt + reference image",
            "no_tool": "Deliver full prompt, negative prompt, aspect ratio; say the image was not generated",
        },
        "news_search": {
            "mode": "A" if a.websearch == "yes" else ("B" if a.websearch == "no" else "unknown"),
            "primary": "agent live web search",
            "no_tool": "Ask the user for news links/text; never invent headlines, dates or URLs",
        },
        "design_state": {
            "mode": mode_for("pillow") if caps["pillow"]["available"] else "A",
            "primary": "scripts/muselab_state.py (versions, freeze, image tasks)",
            "no_tool": "Return the design JSON inline so the user can keep it",
        },
    }

    overall = "A" if all(v["mode"] != "B" for v in matrix.values()) else "partial"
    print(json.dumps({
        "ok": True,
        "platform": f"{platform.system()} {platform.release()} / {platform.machine()}",
        "capabilities": caps,
        "mode_matrix": matrix,
        "overall": overall,
        "notes": local["notes"] + [
            "Mode A = real tool execution. Mode B = analysis + copy-ready prompts.",
            "Never describe Mode B output as a finished image.",
            "Agent-side flags used: imagegen=%s websearch=%s" % (a.imagegen, a.websearch),
        ],
    }, ensure_ascii=False, indent=2))

    if a.json_out:
        import os
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump({"capabilities": caps, "mode_matrix": matrix}, fh, ensure_ascii=False, indent=2)
        print(f"\nsaved -> {os.path.abspath(a.json_out)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
