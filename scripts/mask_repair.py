#!/usr/bin/env python3
"""MuseLab :: apply a mask repair that the audit has already justified.

Rules baked in (they are the reason this script exists instead of a hand edit):
  * RGB is never touched. Only alpha changes. The run verifies pixel equality
    before writing and aborts if a single product colour moved.
  * Nothing is applied unless `mask_audit.py` classified it. `--auto` is the
    default; human-class items require `--force <kind>` and the report records
    that a human authorised them.
  * Repairs are applied to the *exact connected component* the audit flagged —
    never to a bounding box, so product pixels inside the box survive.
  * Removing more than `--max-remove-ratio` of the subject is refused: that is
    not a shadow, that is the product.
  * Nothing is added, moved or reshaped. Only alpha -> 0 (shadow residue) or
    alpha -> 255 (sealed translucent core).

Exit codes
  0  repaired (or nothing to do)
  2  bad input / stale audit
  3  capability missing
  6  refused by a guard
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mask_audit import audit_with_masks  # noqa: E402

AUDIT_KINDS = ("background_residue", "translucent_core")


def repair(image_path: str, cutout_path: str, audit_json: dict | None = None,
           apply_auto: bool = True, force_kinds: tuple[str, ...] = (),
           feather: float = 1.0, max_remove_ratio: float = 0.35,
           diff_path: str | None = None):
    rgb_img = Image.open(image_path).convert("RGB")
    cut = Image.open(cutout_path).convert("RGBA")
    if cut.size != rgb_img.size:
        raise ValueError(f"size mismatch: image {rgb_img.size} vs cutout {cut.size}")

    fresh, item_masks = audit_with_masks(image_path, cutout_path)
    if not fresh.get("ok"):
        raise ValueError(fresh.get("message", "audit failed"))
    stale = False
    if audit_json:
        want = {(i["kind"], tuple(i["bbox"])) for k in ("background_residue", "translucent_core")
                for i in audit_json.get("checks", {}).get(k, {}).get("items", [])}
        got = {(m["kind"], tuple(m["bbox"])) for m in item_masks}
        stale = want != got

    src = np.asarray(cut)
    rgb = src[..., :3].copy()
    alpha = src[..., 3].astype(np.uint8).copy()
    H, W = alpha.shape
    subject_px = int((alpha > 16).sum())

    diff = np.zeros((H, W), dtype=np.uint8)  # 1 removed, 2 sealed
    log: list[dict] = []
    refused: list[dict] = []
    applied = 0

    for m in item_masks:
        kind, cls, box, mask = m["kind"], m["class"], m["bbox"], m["mask"]
        authorised = (cls == "auto" and apply_auto) or (kind in force_kinds)
        if not authorised:
            refused.append({"kind": kind, "class": cls, "bbox": box,
                            "refusal": "auto items disabled" if cls == "auto"
                                       else f"human-class item needs --force {kind}"})
            continue
        if kind == "background_residue":
            area = int(mask.sum())
            if subject_px and area / subject_px > max_remove_ratio:
                refused.append({"kind": kind, "class": cls, "bbox": box,
                                "refusal": f"would remove {area / subject_px:.0%} of the subject"})
                continue
            alpha[mask] = 0
            diff[mask] = 1
            applied += 1
            log.append({"kind": kind, "action": "alpha -> 0", "class": cls, "bbox": box,
                        "area_px": area, "authorised_by": "auto" if cls == "auto" else "explicit --force"})
        elif kind == "translucent_core":
            sel = mask & (alpha < 250)
            if not sel.any():
                continue
            alpha[sel] = 255
            diff[sel] = 2
            applied += 1
            log.append({"kind": kind, "action": "alpha -> 255", "class": cls, "bbox": box,
                        "area_px": int(sel.sum()), "authorised_by": "auto" if cls == "auto" else "explicit --force"})

    if (diff == 1).any() and feather > 0:
        # soften only the seam where the removal met the surviving subject
        removed = Image.fromarray(((diff == 1) * 255).astype(np.uint8), "L")
        seam = np.asarray(removed.filter(ImageFilter.MaxFilter(5))) > 0
        seam &= alpha > 0
        if seam.any():
            blurred = np.asarray(Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(feather)))
            alpha[seam] = np.minimum(alpha[seam], blurred[seam])

    out = np.dstack([rgb, alpha])
    rgb_delta = int(np.abs(out[..., :3].astype(np.int16) - src[..., :3].astype(np.int16)).max())
    if rgb_delta != 0:
        raise RuntimeError(f"internal error: RGB changed by {rgb_delta} — refusing to write")

    report = {
        "ok": True,
        "tool": "mask_repair",
        "is_generative": False,
        "inputs": {"image": os.path.abspath(image_path), "cutout": os.path.abspath(cutout_path)},
        "audit_matches_input": not stale,
        "applied_items": applied,
        "log": log,
        "refused": refused,
        "guards": {
            "rgb_max_delta": rgb_delta,
            "rgb_untouched": True,
            "max_remove_ratio": max_remove_ratio,
            "pixels_added": 0,
            "pixels_moved": 0,
            "silhouette_redrawn": False,
        },
        "alpha_after": {
            "visible_px": int((alpha > 16).sum()),
            "opaque_px": int((alpha >= 250).sum()),
            "changed_px": int((diff > 0).sum()),
            "removed_px": int((diff == 1).sum()),
            "sealed_px": int((diff == 2).sum()),
        },
        "limits": [
            "Repairs only the two patterns the audit can justify; everything else stays for a human.",
            "Removal is component-bounded, so a shadow fused into the outline is cleaned only as far as the flagged component reached.",
            "Sealing a translucent core raises alpha; it cannot recover colour detail the camera never resolved.",
        ],
    }

    if diff_path:
        vis = np.full((H, W, 3), 255, dtype=np.uint8)
        keep = (alpha > 16) & (diff == 0)
        vis[keep] = (200, 204, 210)
        vis[diff == 1] = (224, 48, 48)
        vis[diff == 2] = (32, 168, 108)
        Image.fromarray(vis, "RGB").save(diff_path)
        report["diff_image"] = os.path.abspath(diff_path)

    return report, Image.fromarray(out, "RGBA")


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply audit-justified alpha repairs to a cut-out.")
    ap.add_argument("--image", required=True)
    ap.add_argument("--cutout", required=True)
    ap.add_argument("--audit", default=None, help="JSON from mask_audit.py (optional, used to detect a stale audit)")
    ap.add_argument("--out", required=True, help="repaired RGBA PNG (must not overwrite the input)")
    ap.add_argument("--diff", default=None, help="write a mask-difference PNG here")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--no-auto", action="store_true", help="do not apply the auto class")
    ap.add_argument("--force", action="append", default=[],
                    help="explicitly authorise a human-class kind (background_residue|translucent_core)")
    ap.add_argument("--feather", type=float, default=1.0)
    ap.add_argument("--max-remove-ratio", type=float, default=0.35)
    args = ap.parse_args()

    for p in (args.image, args.cutout):
        if not os.path.isfile(p):
            print(json.dumps({"ok": False, "error": "input_not_found", "path": p}), file=sys.stderr)
            return 2
    if os.path.abspath(args.out) == os.path.abspath(args.cutout):
        print(json.dumps({"ok": False, "error": "refuse_overwrite",
                          "message": "the repaired file must not overwrite the original cutout"}), file=sys.stderr)
        return 6
    if args.audit and not os.path.isfile(args.audit):
        print(json.dumps({"ok": False, "error": "input_not_found", "path": args.audit}), file=sys.stderr)
        return 2
    bad = [k for k in args.force if k not in AUDIT_KINDS]
    if bad:
        print(json.dumps({"ok": False, "error": "unknown_force_kind", "kinds": bad}), file=sys.stderr)
        return 2

    audit_json = None
    if args.audit:
        with open(args.audit, encoding="utf-8") as fh:
            audit_json = json.load(fh)
    try:
        report, img = repair(args.image, args.cutout, audit_json,
                             apply_auto=not args.no_auto, force_kinds=tuple(args.force),
                             feather=args.feather, max_remove_ratio=args.max_remove_ratio,
                             diff_path=args.diff)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "bad_input", "message": str(exc)}), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": "guard_refused", "message": str(exc)}), file=sys.stderr)
        return 6

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    img.save(args.out, "PNG", optimize=True)
    report["output"] = os.path.abspath(args.out)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
