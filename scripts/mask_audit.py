#!/usr/bin/env python3
"""MuseLab :: audit a cut-out mask for the four defects that actually happen.

Read-only. This script never modifies an image; it measures and classifies.

Checks
  1. background_residue   a large, smooth, low-contrast region of background
                          (typically a cast shadow) welded onto the subject
  2. translucent_core     a body that is solid in reality but whose alpha dropped,
                          because it shares its colour with the backdrop
                          (white pearl on a white tray is the classic case)
  3. edge_contamination   edge pixels closer to the background colour than to the
                          product colour = white/背景 fringe
  4. (implicit) structure  every measurement is per connected component, so the
                          report can say "this is product, do not touch"

Everything is relative to a background colour model estimated from the pixels
just outside the mask, so no colour threshold is hard-coded for a specific photo.

Exit codes
  0  audit produced (whatever the verdict)
  2  bad input / mask and image do not match
  3  capability missing (Pillow / numpy)

Limits are reported in the JSON under `limits` and are not papered over.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

try:  # shared colour math, keeps ΔE identical to color_extract.py
    from color_extract import rgb_to_lab, delta_e76, lab_to_hex
except Exception:  # pragma: no cover - only if the script is moved alone
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from color_extract import rgb_to_lab, delta_e76, lab_to_hex  # type: ignore

AUDIT_VERSION = "1.0"

# ---------------------------------------------------------------- numeric helpers


def erode_once(m: np.ndarray) -> np.ndarray:
    """3x3 cross erosion, one step. Border pixels erode (outside = background)."""
    e = m.copy()
    e[1:, :] &= m[:-1, :]
    e[:-1, :] &= m[1:, :]
    e[:, 1:] &= m[:, :-1]
    e[:, :-1] &= m[:, 1:]
    return e


def erode(m: np.ndarray, iters: int) -> np.ndarray:
    out = m.copy()
    for _ in range(max(0, iters)):
        if not out.any():
            break
        out = erode_once(out)
    return out


def thickness_map(m: np.ndarray, max_iter: int = 48) -> np.ndarray:
    """How many 3x3 erosions each pixel survives -> half-width in px (approx)."""
    depth = np.zeros(m.shape, dtype=np.int16)
    cur = m.copy()
    i = 0
    while i < max_iter and cur.any():
        depth[cur] = i + 1
        cur = erode_once(cur)
        i += 1
    return depth


def box_mean(a: np.ndarray, r: int) -> np.ndarray:
    """Mean over a (2r+1)^2 window, via summed-area table."""
    if r <= 0:
        return a.astype(np.float32)
    p = np.pad(a.astype(np.float64), ((r + 1, r), (r + 1, r)), mode="edge")
    s = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    out = (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)
    return out.astype(np.float32)


def dilate_mask(m: np.ndarray, r: int) -> np.ndarray:
    img = Image.fromarray((m.astype(np.uint8) * 255), "L")
    return np.asarray(img.filter(ImageFilter.MaxFilter(2 * r + 1))) > 127


def components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """4-connected components of a boolean mask (pure python, small pixel counts)."""
    ys, xs = np.nonzero(mask)
    pool = set(zip(ys.tolist(), xs.tolist()))
    out: list[list[tuple[int, int]]] = []
    while pool:
        seed = pool.pop()
        stack = [seed]
        comp = [seed]
        while stack:
            y, x = stack.pop()
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if (ny, nx) in pool:
                    pool.discard((ny, nx))
                    stack.append((ny, nx))
                    comp.append((ny, nx))
        out.append(comp)
    return out


def pct(v: np.ndarray, q: float) -> float:
    return float(np.percentile(v, q)) if v.size else 0.0


# ------------------------------------------------------------- background model


def background_model(rgb: np.ndarray, alpha: np.ndarray, ring_px: int = 26):
    """Colour clusters of the pixels immediately outside the subject."""
    subj = alpha > 8
    if not subj.any():
        return None, 0
    ring = dilate_mask(subj, ring_px) & (alpha <= 8)
    if ring.sum() < 200:
        ring = alpha <= 8
    lab = rgb_to_lab(rgb[ring].astype(np.float32))
    if len(lab) > 20000:
        lab = lab[np.random.default_rng(3).choice(len(lab), 20000, replace=False)]
    # tiny k-means, k adaptive on spread
    spread = float(np.sqrt(((lab - lab.mean(0)) ** 2).sum(1)).mean())
    k = 3 if spread > 6 else 2
    rng = np.random.default_rng(7)
    centers = [lab[rng.integers(len(lab))]]
    for _ in range(k - 1):
        d = np.min(((lab[:, None, :] - np.array(centers)[None, :, :]) ** 2).sum(-1), axis=1)
        p = np.maximum(d, 0) + 1e-12
        centers.append(lab[rng.choice(len(lab), p=p / p.sum())])
    centers = np.array(centers)
    labels = np.zeros(len(lab), int)
    for _ in range(20):
        d = ((lab[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        nl = d.argmin(1)
        if np.array_equal(nl, labels):
            break
        labels = nl
        for j in range(k):
            if (labels == j).any():
                centers[j] = lab[labels == j].mean(0)
    counts = np.bincount(labels, minlength=k).astype(float)
    weights = counts / counts.sum()
    order = np.argsort(-weights)
    clusters = [{"lab": [round(float(v), 2) for v in centers[i]],
                 "hex": lab_to_hex(centers[i]),
                 "weight": round(float(weights[i]), 4)} for i in order]
    return {"clusters": clusters, "ring_px": ring_px, "samples": int(ring.sum())}, int(ring.sum())


def dist_to_bg(lab_img: np.ndarray, clusters: list[dict]) -> np.ndarray:
    best = None
    for c in clusters:
        c_lab = np.array(c["lab"], dtype=np.float32)
        d = np.sqrt(((lab_img - c_lab) ** 2).sum(-1))
        best = d if best is None else np.minimum(best, d)
    return best


# ------------------------------------------------------------------- the checks


def check_background_residue(rgb, alpha, lab_img, d_bg, grad, thick, img_area, cfg, mask_out=None):
    """Smooth + thick + background-coloured + low internal contrast = cast shadow."""
    subj = alpha > 16
    cand = subj & (d_bg < cfg["bg_de_max"]) & (grad < cfg["grad_max"]) & (thick >= cfg["thick_min"])
    items = []
    for comp in components(cand):
        ys = np.array([p[0] for p in comp])
        xs = np.array([p[1] for p in comp])
        area = len(comp)
        if area < cfg["min_area_px"] or area / img_area < cfg["min_area_ratio"]:
            continue
        L = lab_img[ys, xs, 0]
        chroma = np.sqrt(lab_img[ys, xs, 1] ** 2 + lab_img[ys, xs, 2] ** 2)
        l_range = pct(L, 95) - pct(L, 5)
        specular = float((L >= 250).mean())
        de = float(d_bg[ys, xs].mean())
        g = float(grad[ys, xs].mean())
        t = int(thick[ys, xs].max())
        # A body with a specular highlight and a wide tonal range is product-like.
        product_like = specular > 0.002 or l_range > cfg["product_l_range"]
        confident = (de <= cfg["auto_de_max"] and l_range <= cfg["auto_l_range"]
                     and area / img_area >= cfg["auto_area_ratio"] and not product_like)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
        items.append({
            "kind": "background_residue",
            "bbox": bbox,
            "area_px": int(area),
            "area_ratio_image": round(area / img_area, 5),
            "area_ratio_subject": round(area / max(int(subj.sum()), 1), 4),
            "metrics": {
                "mean_delta_e_to_background": round(de, 2),
                "l_p5": round(pct(L, 5), 1), "l_p95": round(pct(L, 95), 1),
                "l_range": round(l_range, 1),
                "mean_l_chroma": round(float(chroma.mean()), 1),
                "specular_share": round(specular, 4),
                "max_thickness_px": t,
                "mean_gradient": round(g, 2),
            },
            "class": "auto" if confident else "human",
            "reason": ("smooth low-contrast patch matching the background colour"
                       if confident else
                       "looks background-ish but has structure/highlights — could be product, confirm before removing"),
        })
        if mask_out is not None:
            m = np.zeros(alpha.shape, bool)
            m[ys, xs] = True
            mask_out.append({"kind": "background_residue", "bbox": bbox,
                             "class": items[-1]["class"], "mask": m})
    status = "pass"
    if items:
        status = "fail" if any(i["class"] == "auto" for i in items) else "warn"
    return {"status": status, "items": items,
            "auto_area_ratio_image": round(sum(i["area_px"] for i in items if i["class"] == "auto") / img_area, 5),
            "thresholds": cfg}, cand


def check_translucent_core(lab_img, alpha, img_area, cfg, mask_out=None):
    """Translucent pixels in the middle of a body that should be solid."""
    solid = alpha >= cfg["solid_alpha"]
    core = erode(solid, cfg["core_erode"])
    cand = core & (alpha < cfg["solid_alpha"])
    items = []
    for comp in components(cand):
        ys = np.array([p[0] for p in comp])
        xs = np.array([p[1] for p in comp])
        area = len(comp)
        if area < cfg["min_area_px"]:
            continue
        m = np.zeros_like(cand)
        m[ys, xs] = True
        ring = dilate_mask(m, 2) & ~m
        enclosed = float((alpha[ring] >= cfg["solid_alpha"]).mean()) if ring.any() else 0.0
        bbox = [int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)]
        items.append({
            "kind": "translucent_core",
            "bbox": bbox,
            "area_px": int(area),
            "area_ratio_image": round(area / img_area, 5),
            "metrics": {
                "mean_alpha": round(float(alpha[ys, xs].mean()), 1),
                "min_alpha": int(alpha[ys, xs].min()),
                "enclosed_by_opaque_ratio": round(enclosed, 3),
            },
            "class": "auto" if enclosed >= 0.97 else "human",
            "reason": ("fully surrounded by opaque pixels — safe to seal" if enclosed >= 0.97
                       else "touches the outer contour — sealing it changes the silhouette, confirm first"),
        })
        if mask_out is not None:
            mask_out.append({"kind": "translucent_core", "bbox": bbox,
                             "class": items[-1]["class"], "mask": m})
    status = "pass" if not items else ("warn" if all(i["class"] == "human" for i in items) else "fail")
    return {"status": status, "items": items, "thresholds": cfg}


def check_edge_contamination(rgb, alpha, lab_img, bg_lab_list, thick, img_area, cfg):
    """Edge pixels that look more like the backdrop than like the product."""
    edge = (alpha > cfg["edge_lo"]) & (alpha < cfg["edge_hi"])
    core = alpha >= cfg["solid_alpha"]
    if edge.sum() < 50 or core.sum() < 50:
        return {"status": "pass", "items": [], "metrics": {"edge_pixels": int(edge.sum())},
                "thresholds": cfg}
    # local mean of the opaque core colour = what the edge *should* look like
    cnt = box_mean(core.astype(np.float32), cfg["core_radius"])
    local = np.zeros_like(rgb, dtype=np.float32)
    for c in range(3):
        local[..., c] = box_mean(rgb[..., c] * core, cfg["core_radius"])
    ok = cnt > 1e-4
    for c in range(3):
        local[..., c] = np.where(ok, local[..., c] / np.maximum(cnt, 1e-4),
                                 float(rgb[..., c][core].mean()) if core.any() else 128.0)
    lab_core = rgb_to_lab(local)
    d_core = np.sqrt(((lab_img - lab_core) ** 2).sum(-1))
    best_bg = np.full(alpha.shape, 1e9, dtype=np.float32)
    for cl in bg_lab_list:
        c_lab = np.array(cl["lab"], dtype=np.float32)
        best_bg = np.minimum(best_bg, np.sqrt(((lab_img - c_lab) ** 2).sum(-1)))
    closer_to_bg = edge & (best_bg < d_core)
    ratio = float(closer_to_bg.sum() / max(int(edge.sum()), 1))
    # pixels that are essentially the backdrop colour = the visible "white fringe"
    near_bg = edge & (best_bg < cfg["fringe_de_max"])
    status = "pass"
    if ratio > cfg["contam_warn"]:
        status = "warn"
    if ratio > cfg["contam_fail"]:
        status = "fail"
    item = {
        "kind": "edge_contamination",
        "area_px": int(closer_to_bg.sum()),
        "area_ratio_image": round(int(closer_to_bg.sum()) / img_area, 5),
        "metrics": {
            "edge_pixels": int(edge.sum()),
            "contaminated_ratio": round(ratio, 3),
            "near_background_pixels": int(near_bg.sum()),
            "near_background_ratio": round(float(near_bg.sum() / max(int(edge.sum()), 1)), 3),
            "mean_contaminated_delta_e": round(float(d_core[closer_to_bg].mean()), 1) if closer_to_bg.any() else 0.0,
        },
        "class": "human",
        "reason": ("edge pixels resemble the backdrop; despill/re-matte recommended, "
                   "automatic recolouring risks shifting the product colour"),
    }
    return {"status": status, "items": [item] if ratio > 0 else [], "metrics": item["metrics"],
            "thresholds": cfg}


# ------------------------------------------------------------------------ driver


DEFAULT_CFG = {
    "bg_de_max": 18.0,        # ΔE to background for "looks like background"
    "grad_max": 1.2,          # mean |∇L| per px inside the candidate window
    "thick_min": 5,           # px of half-width; thin chains never qualify
    "min_area_px": 400,
    "min_area_ratio": 0.0008,
    "auto_de_max": 12.0,
    "auto_l_range": 24.0,
    "auto_area_ratio": 0.002,
    "product_l_range": 32.0,
    "solid_alpha": 250,
    "core_erode": 4,
    "edge_lo": 8,
    "edge_hi": 240,
    "core_radius": 7,
    "fringe_de_max": 6.0,
    "contam_warn": 0.15,
    "contam_fail": 0.35,
}


def audit_with_masks(image_path: str, cutout_path: str, cfg: dict | None = None):
    """Same as audit(), plus the boolean component masks keyed like the items."""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    rgb_img = Image.open(image_path).convert("RGB")
    cut = Image.open(cutout_path).convert("RGBA")
    if cut.size != rgb_img.size:
        raise ValueError(f"size mismatch: image {rgb_img.size} vs cutout {cut.size}")
    rgb = np.asarray(rgb_img, dtype=np.float32)
    arr = np.asarray(cut)
    alpha = arr[..., 3].astype(np.int16)
    img_area = alpha.size

    lab_img = rgb_to_lab(rgb)
    # luminance gradient, smoothed so a single sparkle does not look like "structure"
    gy, gx = np.gradient(lab_img[..., 0])
    grad = box_mean(np.sqrt(gx ** 2 + gy ** 2), 4)
    thick = thickness_map(alpha > 16, 48)

    bg, ring_n = background_model(rgb, alpha)
    if bg is None:
        return {"ok": False, "error": "empty_mask", "message": "cutout has no visible pixels"}, []
    d_bg = dist_to_bg(lab_img, bg["clusters"])

    item_masks: list[dict] = []
    residue, _ = check_background_residue(rgb, alpha, lab_img, d_bg, grad, thick, img_area, cfg, item_masks)
    transl = check_translucent_core(lab_img, alpha, img_area, cfg, item_masks)
    edge = check_edge_contamination(rgb, alpha, lab_img, bg["clusters"], thick, img_area, cfg)

    auto = [i for c in (residue, transl, edge) for i in c["items"] if i["class"] == "auto"]
    human = [i for c in (residue, transl, edge) for i in c["items"] if i["class"] == "human"]
    if any(c["status"] == "fail" for c in (residue, transl)):
        overall = "needs_repair"
    elif any(c["status"] != "pass" for c in (residue, transl, edge)):
        overall = "review"
    else:
        overall = "clean"

    report = {
        "ok": True,
        "tool": "mask_audit",
        "audit_version": AUDIT_VERSION,
        "is_generative": False,
        "inputs": {"image": os.path.abspath(image_path), "cutout": os.path.abspath(cutout_path)},
        "image_size": [int(rgb_img.width), int(rgb_img.height)],
        "subject": {
            "visible_px": int((alpha > 16).sum()),
            "opaque_px": int((alpha >= cfg["solid_alpha"]).sum()),
            "visible_ratio_image": round(float((alpha > 16).mean()), 4),
        },
        "background_model": bg,
        "checks": {
            "background_residue": {k: v for k, v in residue.items()},
            "translucent_core": transl,
            "edge_contamination": edge,
        },
        "verdict": {
            "overall": overall,
            "auto_fixable": auto,
            "needs_human": human,
            "preserve_original": not auto,
            "structure_guard": "no audit result authorises moving, adding or reshaping product pixels",
        },
        "limits": [
            "Estimates the backdrop from the ring just outside the mask; a subject that fills the frame has no ring and the model degrades to a global estimate.",
            "A white/silver product on a white backdrop is colour-ambiguous — the structural tests (smoothness, thickness, tonal range) are what separate product from shadow, and they still misfire on featureless metal.",
            "Translucent cores touching the silhouette are reported for human confirmation; sealing them changes the outline.",
            "Edge contamination is measured, not fixed: recolouring edges automatically can shift real product colour.",
            "Thresholds are relative to the estimated background but still assume the product has some tonal or texture contrast against it.",
        ],
        "thresholds": cfg,
    }
    return report, item_masks


def audit(image_path: str, cutout_path: str, cfg: dict | None = None) -> dict:
    """Report-only wrapper (the common case)."""
    report, _ = audit_with_masks(image_path, cutout_path, cfg)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit a cut-out mask for background residue / translucency / edge fringe.")
    ap.add_argument("--image", required=True, help="original image")
    ap.add_argument("--cutout", required=True, help="RGBA cut-out PNG")
    ap.add_argument("--json", dest="json_out", default=None, help="write report here")
    ap.add_argument("--overlay", default=None, help="write a colour-coded diagnostic PNG here")
    ap.add_argument("--auto-only", action="store_true", help="exit 1 unless the mask is clean")
    args = ap.parse_args()

    for p in (args.image, args.cutout):
        if not os.path.isfile(p):
            print(json.dumps({"ok": False, "error": "input_not_found", "path": p}), file=sys.stderr)
            return 2
    try:
        rep, item_masks = audit_with_masks(args.image, args.cutout)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": "size_mismatch", "message": str(exc)}), file=sys.stderr)
        return 2
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": "capability_missing", "message": str(exc)}), file=sys.stderr)
        return 3

    if args.overlay:
        rgb = np.asarray(Image.open(args.image).convert("RGB"))
        cut = np.asarray(Image.open(args.cutout).convert("RGBA"))
        vis = (rgb.astype(np.float32) * 0.22 + 255 * 0.78).astype(np.uint8)
        cyan = cut[..., 3] > 16
        vis[cyan] = (rgb[cyan].astype(np.float32) * 0.5 + np.array([0, 120, 160]) * 0.5).astype(np.uint8)
        for m in item_masks:
            if m["kind"] == "background_residue":
                col = np.array([230, 40, 40]) if m["class"] == "auto" else np.array([242, 152, 24])
            else:
                col = np.array([20, 120, 240]) if m["class"] == "auto" else np.array([190, 40, 200])
            sel = m["mask"]
            vis[sel] = (vis[sel] * 0.35 + col * 0.65).astype(np.uint8)
        Image.fromarray(vis).save(args.overlay)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
    print(json.dumps(rep, ensure_ascii=False, indent=2))

    if args.auto_only and rep["verdict"]["overall"] != "clean":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
