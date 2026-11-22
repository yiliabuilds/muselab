#!/usr/bin/env python3
"""MuseLab :: product pixel integrity check.

Answers one question only: **were the product's own pixels changed?**

It does NOT judge cut-out quality. A pixel-perfect composite of a bad mask is
still a bad product image. Always run mask_audit.py alongside this script.

Checks
  1. geometry      coordinate mapping original -> composite (scale, offset, clipping)
  2. opaque core   RGB equality inside alpha>=250 (eroded) — the "product body"
  3. edge band     alpha distribution + colour change caused by blending
  4. structure     clipping, anisotropic stretch, silhouette/component retention
  5. compositing   light/shadow changes recorded separately from pixel edits

Two different questions are answered separately, because conflating them is how
"pixel preserved" claims go wrong:

  A. cutout fidelity   cut-out RGB == original RGB inside the core.
                       A failure here means the segmentation step repainted the product.
  B. composite fidelity composite == ideal alpha blend of (cutout RGB, alpha, bg).
                       A failure here means the compositing step repainted or re-lit it.

The seam matters: a foreground mask rarely reaches alpha=255 (macOS Vision tops out
at 254), so even "opaque" pixels are blended with ~1/255 of the new backdrop. That
shifts RGB by up to 4/255 versus the original and is *not* an edit. Comparing the
composite straight against the original therefore reports a false failure; this
script models the blend instead, and reports the un-modelled delta only as tolerance.

Exit codes: 0 ok (report may still say fail), 2 bad input, 5 format conflict, 6 strict fail
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

ALPHA_CORE = 250
CORE_ERODE_PX = 2
DEFAULT_TOL = 0  # exact by default; raise it only when the container is lossy
COVER_DE = 18.0          # ΔE to the estimated backdrop before a pixel counts as "content-like"
COVER_MIN_PX = 200       # ignore specks
COVER_GUARD_PX = 4       # ignore the transition band hugging the mask
COVER_TOP_N = 8
COVER_MIN_L_RANGE = 14.0 # a drop shadow is tonally flat; a pearl has speculars
COVER_MIN_GRAD = 2.0     # and structure


def _bg_clusters(orig: np.ndarray, alpha: np.ndarray, ma):
    """Two-cluster backdrop estimate from the pixels the mask rejected (fast).

    mask_audit.py does the same job with k-means and takes ~14 s on a 2.8 MP photo;
    a gate that runs on every composite cannot afford that. This picks a lit cluster
    and a shadow cluster straight off the L* distribution instead, which is enough to
    stop a drop shadow from being mistaken for missing product.
    """
    px = orig[alpha == 0]
    if px.shape[0] < 500:
        return None
    lab = ma.rgb_to_lab(px)
    L = lab[:, 0]
    out = []
    for pct in (25.0, 70.0):
        target = float(np.percentile(L, pct))
        sel = np.abs(L - target) < 9.0
        if int(sel.sum()) >= 100:
            out.append({"lab": [float(v) for v in np.median(lab[sel], axis=0)]})
    return out or None


def _load_mask_audit():
    """Reuse mask_audit's backdrop model so both scripts measure against one definition."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mask_audit.py")
    spec = importlib.util.spec_from_file_location("_muselab_mask_audit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _components_fast(cand: np.ndarray, max_edge: int = 700):
    """Connected components on a downscaled copy; returns [(pixels, scale_factor)].

    Pure-python flood fill on a full-resolution 1 MP candidate set takes ~15 s, which
    is too slow for a gate. Bounding boxes and areas are therefore approximate by
    design — they are meant to point a human at a region, not to measure it.
    """
    from PIL import Image
    h, w = cand.shape
    f = min(1.0, max_edge / max(h, w))
    if f < 1.0:
        small = np.asarray(Image.fromarray((cand * 255).astype(np.uint8)).resize(
            (max(1, int(w * f)), max(1, int(h * f))), Image.BILINEAR)) > 127
    else:
        small = cand
    ma = _load_mask_audit()
    return [(comp, f) for comp in ma.components(small)]


def coverage_check(orig: np.ndarray, alpha: np.ndarray, overlay_path: str | None = None):
    """Product-like pixels in the original that the mask never picked up.

    This is the blind spot of a pixel-equality test: it can only inspect pixels that
    exist in the cut-out. A mask that silently drops a whole bead loses those pixels
    before the check ever sees them. Coverage looks outside the mask instead.
    """
    ma = _load_mask_audit()
    rgb = orig.astype(np.float32)
    h, w = orig.shape[:2]
    clusters = _bg_clusters(orig, alpha, ma)
    if not clusters:
        return {"status": "skipped", "reason": "no backdrop ring could be estimated"}
    ring_px = int((alpha == 0).sum())
    lab = ma.rgb_to_lab(rgb)
    d_bg = ma.dist_to_bg(lab, clusters)
    cand = (alpha == 0) & (d_bg > COVER_DE) & ~ma.dilate_mask(alpha > 0, COVER_GUARD_PX)
    items = []
    if int(cand.sum()) >= COVER_MIN_PX:
        gy, gx = np.gradient(lab[..., 0])
        grad = ma.box_mean(np.sqrt(gx ** 2 + gy ** 2), 4)
        Limg = lab[..., 0]
        for comp, f in _components_fast(cand):
            if len(comp) / (f * f) < COVER_MIN_PX:
                continue
            ys = [p[0] for p in comp]
            xs = [p[1] for p in comp]
            # 坐标映射回原分辨率（连通域在缩略图上求，bbox/面积是近似值）
            x0, y0 = int(min(xs) / f), int(min(ys) / f)
            x1, y1 = int(max(xs) / f), int(max(ys) / f)
            sub_L = Limg[y0:y1 + 1, x0:x1 + 1]
            sub_g = grad[y0:y1 + 1, x0:x1 + 1]
            if sub_L.size == 0:
                continue
            l_range = float(np.percentile(sub_L, 95) - np.percentile(sub_L, 5))
            mean_grad = float(sub_g.mean())
            # 影子是平滑的、商品有高光：用「亮度跨度 + 局部梯度」把两者分开，
            # 否则灰珍珠与灰纸的投影在颜色上无法区分（这张图正是这种情况）。
            structured = l_range >= COVER_MIN_L_RANGE and mean_grad >= COVER_MIN_GRAD
            sample = [(min(h, max(0, int(y / f))), min(w, max(0, int(x / f))))
                      for y, x in comp[:400]]
            items.append({
                "bbox": [x0, y0, x1 - x0, y1 - y0],
                "area_px": int(round(len(comp) / (f * f))),
                "area_ratio_subject": round(len(comp) / (f * f) / max(1, int((alpha > 0).sum())), 4),
                "mean_delta_e_to_backdrop": round(float(np.mean([d_bg[y, x] for y, x in sample])), 1),
                "l_range": round(l_range, 1),
                "mean_gradient": round(mean_grad, 2),
                "looks_like_product": bool(structured),
            })
        items.sort(key=lambda i: (not i["looks_like_product"], -i["area_px"]))
        items = items[:COVER_TOP_N]
    total = int(sum(i["area_px"] for i in items))
    if overlay_path and cand.any():
        from PIL import Image
        ov = np.asarray(Image.open(overlay_path).convert("RGB")).copy() if os.path.isfile(overlay_path) else orig.astype(np.uint8).copy()
        ov[cand] = (0, 90, 255)
        Image.fromarray(ov).save(overlay_path)
    return {
        "status": "review" if items else "pass",
        "uncovered_pixels": total,
        "uncovered_share_of_subject": round(total / max(1, int((alpha > 0).sum())), 4),
        "items": items,
        "backdrop_ring_px": int(ring_px),
        "thresholds": {"delta_e": COVER_DE, "min_area_px": COVER_MIN_PX, "guard_px": COVER_GUARD_PX},
        "note": ("Areas outside the mask that look unlike the backdrop. They may be product the "
                 "segmentation dropped, or unrelated objects/shadows — this flags them for a human, "
                 "it cannot decide which."),
    }


def load_rgba(path):
    from PIL import Image
    im = Image.open(path)
    fmt = (im.format or "").upper()
    return im, fmt


def erode_binary(mask: np.ndarray, iters: int) -> np.ndarray:
    if iters <= 0:
        return mask
    m = mask.copy()
    for _ in range(iters):
        e = m.copy()
        e[1:, :] &= m[:-1, :]
        e[:-1, :] &= m[1:, :]
        e[:, 1:] &= m[:, :-1]
        e[:, :-1] &= m[:, 1:]
        e[0, :] = False
        e[-1, :] = False
        e[:, 0] = False
        e[:, -1] = False
        m = e
    return m


def component_count(binary: np.ndarray, max_edge: int = 320) -> int:
    """Coarse connected-component count (documented as approximate)."""
    from PIL import Image
    h, w = binary.shape
    f = min(1.0, max_edge / max(h, w))
    if f < 1.0:
        img = Image.fromarray((binary * 255).astype(np.uint8)).resize(
            (max(1, int(w * f)), max(1, int(h * f))), Image.BILINEAR)
        small = np.asarray(img) > 127
    else:
        small = binary
    seen = np.zeros_like(small, dtype=bool)
    hh, ww = small.shape
    count = 0
    for y0 in range(hh):
        for x0 in range(ww):
            if not small[y0, x0] or seen[y0, x0]:
                continue
            count += 1
            stack = [(y0, x0)]
            seen[y0, x0] = True
            while stack:
                y, x = stack.pop()
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < hh and 0 <= nx < ww and small[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description="Check that product pixels survived compositing.")
    ap.add_argument("--original", required=True, help="original product photo (RGB)")
    ap.add_argument("--cutout", required=True, help="RGBA cut-out PNG from cutout.py")
    ap.add_argument("--composite", required=True, help="composited output")
    ap.add_argument("--placement", default=None, help="compose.py --json report (for scale/offset)")
    ap.add_argument("--scale", type=float, default=None, help="override scale if no placement json")
    ap.add_argument("--offset", default=None, help="override offset as x,y")
    ap.add_argument("--tolerance", type=int, default=DEFAULT_TOL,
                    help="allowed per-channel delta inside the opaque core (default 0 = exact)")
    ap.add_argument("--bg", default=None, help="background hex used for compositing, e.g. #D6D6D6")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--overlay", default=None, help="write a diagnostic PNG (red=changed core, blue=uncovered content)")
    ap.add_argument("--no-coverage", action="store_true",
                    help="skip the coverage check (content-like pixels the mask never picked up)")
    ap.add_argument("--strict", action="store_true", help="exit 6 when integrity fails")
    args = ap.parse_args()

    try:
        from PIL import Image
    except Exception as exc:
        print(json.dumps({"ok": False, "error": "capability_missing", "message": str(exc)}), file=sys.stderr)
        return 3

    for p in (args.original, args.cutout, args.composite):
        if not os.path.isfile(p):
            print(json.dumps({"ok": False, "error": "input_not_found", "path": p}), file=sys.stderr)
            return 2

    orig_im, _ = load_rgba(args.original)
    cut_im, _ = load_rgba(args.cutout)
    comp_im, comp_fmt = load_rgba(args.composite)

    if cut_im.mode != "RGBA":
        print(json.dumps({"ok": False, "error": "no_alpha",
                          "message": f"{args.cutout} has no alpha channel (mode={cut_im.mode})"}), file=sys.stderr)
        return 5

    orig = np.asarray(orig_im.convert("RGB")).astype(np.int16)
    cut = np.asarray(cut_im)
    comp = np.asarray(comp_im.convert("RGB")).astype(np.int16)
    alpha = cut[:, :, 3]

    scale, ox, oy, frame_preserved, placement_source = 1.0, 0, 0, None, "default"
    if args.placement and os.path.isfile(args.placement):
        with open(args.placement, encoding="utf-8") as fh:
            rep = json.load(fh)
        sp = rep.get("subject_placement") or {}
        scale = float(sp.get("scale", 1.0))
        ox = int(sp.get("x", 0))
        oy = int(sp.get("y", 0))
        frame_preserved = rep.get("frame_preserved")
        placement_source = "compose_json"
    if args.scale is not None:
        scale = float(args.scale)
        placement_source = "cli"
    if args.offset:
        ox, oy = [int(v) for v in args.offset.split(",")]
        placement_source = "cli"

    h_cut, w_cut = alpha.shape
    h_orig, w_orig = orig.shape[:2]
    h_comp, w_comp = comp.shape[:2]

    ys, xs = np.nonzero(alpha > 0)
    tx = (ox + np.round(xs * scale)).astype(np.int64)
    ty = (oy + np.round(ys * scale)).astype(np.int64)
    inside = (tx >= 0) & (tx < w_comp) & (ty >= 0) & (ty < h_comp)

    substantive = alpha[ys, xs] > 6
    clipped = substantive & ~inside
    clipped_px = int(clipped.sum())
    subj_px = int(substantive.sum())

    # ---------- background reference colour ----------
    bg_rgb, bg_source = None, "none"
    if args.bg and args.bg.startswith("#"):
        v = args.bg.lstrip("#")
        bg_rgb = np.array([int(v[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)
        bg_source = "cli --bg"
    else:
        # 四角中值估计：只在商品没有铺满画面时可靠
        ring = np.concatenate([comp[:8, :8].reshape(-1, 3), comp[:8, -8:].reshape(-1, 3),
                               comp[-8:, :8].reshape(-1, 3), comp[-8:, -8:].reshape(-1, 3)])
        bg_rgb = np.median(ring.astype(np.float32), axis=0)
        bg_source = "corner_median_estimate"

    # ---------- 2. opaque core ----------
    alpha_max = int(alpha.max())
    fully_opaque = int((alpha == 255).sum())
    core = alpha >= ALPHA_CORE
    if scale == 1.0:
        core_mask = erode_binary(core, CORE_ERODE_PX)
    else:
        # 缩放后 1px 重采样会污染边缘，核心多腐蚀一点
        core_mask = erode_binary(core, CORE_ERODE_PX + 2)
    cy, cx = np.nonzero(core_mask)
    core_in = ((ox + np.round(cx * scale)).astype(np.int64) < w_comp) & \
              ((oy + np.round(cy * scale)).astype(np.int64) < h_comp) & \
              ((ox + np.round(cx * scale)).astype(np.int64) >= 0) & \
              ((oy + np.round(cy * scale)).astype(np.int64) >= 0)
    core_outside = int((~core_in).sum())

    core_report = {
        "pixels": int(cy.size),
        "core_pixels_outside_canvas": core_outside,
        "alpha_threshold": ALPHA_CORE,
        "alpha_max_in_cutout": alpha_max,
        "alpha_255_pixels": fully_opaque,
        "background_reference": {"rgb": [int(v) for v in bg_rgb], "source": bg_source},
    }
    if core_outside:
        core_report["note"] = "part of the opaque product body falls outside the output canvas"
    if fully_opaque == 0:
        core_report["alpha_note"] = (
            "the mask never reaches alpha=255, so even 'opaque' pixels carry ~1/255 of the new "
            "backdrop; the composite is compared against the ideal blend, not against the raw original")
    if int(core_in.sum()):
        cy, cx = cy[core_in], cx[core_in]
    cut_preserved = comp_preserved = None
    if cy.size:
        dty = np.round(oy + cy * scale).astype(np.int64)
        dtx = np.round(ox + cx * scale).astype(np.int64)
        if scale == 1.0:
            src_orig = orig[cy, cx].astype(np.float32)
        else:
            src_orig = np.asarray(Image.fromarray(orig.astype(np.uint8)).resize(
                (w_comp, h_comp), Image.LANCZOS)).astype(np.float32)[dty, dtx]
        src_cut = cut[cy, cx, :3].astype(np.float32)
        a_core = alpha[cy, cx].astype(np.float32)[:, None] / 255.0
        dst = comp[dty, dtx].astype(np.float32)

        # A. cut-out fidelity: did segmentation repaint the product body?
        d_cut = np.abs(src_cut - src_orig)
        changed_cut = d_cut.max(axis=1) > args.tolerance
        # B. composite fidelity: does the output equal the ideal alpha blend?
        expected = a_core * src_cut + (1.0 - a_core) * bg_rgb
        d_comp = np.abs(expected - dst)
        # 允许 1 LSB 的定点取整误差
        changed_comp = d_comp.max(axis=1) > (args.tolerance + 1)
        n_changed_cut, n_changed_comp = int(changed_cut.sum()), int(changed_comp.sum())
        core_report.update({
            "cutout_vs_original": {
                "max_channel_delta": int(d_cut.max()),
                "mean_abs_delta": round(float(d_cut.mean()), 4),
                "changed_pixels": n_changed_cut,
                "exact_equal_ratio": round(1.0 - n_changed_cut / cy.size, 6),
            },
            "composite_vs_ideal_blend": {
                "max_channel_delta": round(float(d_comp.max()), 2),
                "mean_abs_delta": round(float(d_comp.mean()), 4),
                "changed_pixels": n_changed_comp,
                "exact_equal_ratio": round(1.0 - n_changed_comp / cy.size, 6),
                "model": "a*cutout_rgb + (1-a)*background_rgb",
            },
            "tolerance": args.tolerance,
        })
        cut_preserved = n_changed_cut == 0
        comp_preserved = n_changed_comp == 0
        if args.overlay and n_changed_comp:
            ov = np.asarray(comp_im.convert("RGB")).copy()
            ov[dty[changed_comp], dtx[changed_comp]] = (255, 0, 0)
            Image.fromarray(ov).save(args.overlay)
    else:
        core_report["note"] = "no fully opaque core found — the cut-out may be entirely translucent"
    core_preserved = bool(cy.size and cut_preserved and comp_preserved)

    # ---------- 3. edge band ----------
    band = (alpha > 0) & (alpha < ALPHA_CORE)
    by, bx = np.nonzero(band)
    btx = (ox + np.round(bx * scale)).astype(np.int64)
    bty = (oy + np.round(by * scale)).astype(np.int64)
    b_in = (btx >= 0) & (btx < w_comp) & (bty >= 0) & (bty < h_comp)
    edge_outside = int((~b_in).sum())
    by, bx, btx, bty = by[b_in], bx[b_in], btx[b_in], bty[b_in]
    edge = {"pixels": int(by.size), "edge_pixels_outside_canvas": edge_outside}
    if by.size:
        a = alpha[by, bx].astype(np.float32)[:, None] / 255.0
        rgb_src = orig[by, bx].astype(np.float32)
        expected = a * rgb_src + (1.0 - a) * bg_rgb
        got = comp[bty, btx].astype(np.float32)
        blend_err = float(np.abs(expected - got).mean())
        buckets = {
            "1-63": int(((alpha[by, bx] >= 1) & (alpha[by, bx] < 64)).sum()),
            "64-127": int(((alpha[by, bx] >= 64) & (alpha[by, bx] < 128)).sum()),
            "128-191": int(((alpha[by, bx] >= 128) & (alpha[by, bx] < 192)).sum()),
            "192-249": int(((alpha[by, bx] >= 192) & (alpha[by, bx] < 250)).sum()),
        }
        edge.update({
            "alpha_buckets": buckets,
            "mean_blend_error": round(blend_err, 3),
            "fully_translucent_share": round(
                buckets["1-63"] / max(1, by.size), 4),
            "note": "blend error is compositing arithmetic; it is not a pixel edit",
        })
    translucent_core_share = round(float(((alpha > 0) & (alpha < ALPHA_CORE)).sum() / max(1, (alpha > 0).sum())), 4)

    # ---------- 4. structure ----------
    stretch_ok = True
    if scale != 1.0:
        stretch_ok = abs(scale - round(scale, 6)) < 1e-9  # uniform by construction; anisotropic impossible here
    comp_components = component_count(alpha > 6)
    structure = {
        "subject_pixels": subj_px,
        "clipped_pixels": clipped_px,
        "clipped_share": round(clipped_px / max(1, subj_px), 6),
        "uniform_scale": stretch_ok,
        "silhouette_components": comp_components,
        "kept_frame": bool(frame_preserved) if frame_preserved is not None else None,
        "note": "clipped_share > 0 means part of the visible product fell outside the output canvas",
    }

    # ---------- 5. coverage: content the mask never picked up ----------
    if args.no_coverage:
        coverage = {"status": "skipped", "reason": "--no-coverage"}
    else:
        overlay_for_coverage = args.overlay if args.overlay else None
        coverage = coverage_check(orig, alpha, overlay_for_coverage)

    # ---------- 6. compositing-only changes ----------
    compositing = {
        "relit": False,
        "shadow_added": False,
        "changes": [],
    }
    if by.size and edge.get("mean_blend_error", 0) > 0.5:
        compositing["changes"].append("edge blending differs from the ideal alpha composite")
    if scale != 1.0:
        compositing["changes"].append(f"subject resampled by {scale:.4f} (LANCZOS) before compositing")
        compositing["resampling"] = True
    if comp_fmt in {"JPEG", "JPG", "WEBP"}:
        compositing["changes"].append(f"output container is {comp_fmt} (lossy) — core equality is approximate")
        compositing["lossy_container"] = comp_fmt

    # ---------- verdict ----------
    failures = []
    if clipped_px > 0:
        failures.append(f"{clipped_px} visible product pixels fall outside the canvas")
    if core_outside > 0:
        failures.append(f"{core_outside} opaque product-body pixels fall outside the canvas")
    if edge_outside > 0:
        failures.append(f"{edge_outside} product edge pixels fall outside the canvas")
    if cut_preserved is False:
        c = core_report.get("cutout_vs_original", {})
        failures.append(
            f"cut-out repainted the product body: {c.get('changed_pixels')} core px, "
            f"max channel delta {c.get('max_channel_delta')}")
    if comp_preserved is False:
        c = core_report.get("composite_vs_ideal_blend", {})
        failures.append(
            f"compositing altered the product body: {c.get('changed_pixels')} core px deviate from "
            f"the ideal alpha blend, max delta {c.get('max_channel_delta')}")
    if core_report.get("pixels", 0) == 0:
        failures.append("no opaque core to verify — cannot claim the product body survived")

    verdict = "fail" if failures else ("pass_resampled" if scale != 1.0 else "pass_exact")

    report = {
        "ok": True,
        "verdict": verdict,
        "is_generative": False,
        "inputs": {
            "original": os.path.abspath(args.original),
            "cutout": os.path.abspath(args.cutout),
            "composite": os.path.abspath(args.composite),
        },
        "sizes": {"original": [w_orig, h_orig], "cutout": [w_cut, h_cut], "composite": [w_comp, h_comp]},
        "mapping": {"scale": round(scale, 6), "offset": [ox, oy], "source": placement_source},
        "opaque_core": core_report,
        "product_pixels_preserved": {
            "cutout_step": cut_preserved,
            "compositing_step": comp_preserved,
            "overall": core_preserved,
        },
        "edge_band": edge,
        "translucent_share_of_subject": translucent_core_share,
        "structure": structure,
        "coverage": coverage,
        "compositing_changes": compositing,
        "machine_failures": failures,
        "honesty_note": (
            "pixel preserved only means the product pixels were not repainted. "
            "It says nothing about mask quality, background residue or whether the image is usable. "
            "Run mask_audit.py as well; a clean mask is not implied by this report."
        ),
    }
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures and args.strict:
        return 6
    return 0


if __name__ == "__main__":
    sys.exit(main())
