#!/usr/bin/env python3
"""MuseLab :: product-fidelity gate (code-checkable part only).

What this script CAN decide from pixels:
  - background purity / uniformity / gradient (seamless vs lit backdrop)
  - per-material colour drift, split into lighting-plausible vs material mismatch,
    using the background as an illuminant reference and de-casting a*/b*
  - per-ROI focus (blurred accessory = structure not verifiable)
  - foreground presence inside a declared accessory ROI

What it CANNOT decide (must be a vision model or a human):
  - "is this shape a lobster clasp" / "is the pearl bezel-set"
  - bead counting, sequence order, "does it look like the same product"
  - whether an extra decoration is a defect or a design choice

So the report always separates `code_checks`, `requires_vision`,
`requires_human`, and never turns "the script exited 0" into "the image passed".

Colour rule (this is the point of the script):
  ΔE 不能直接和 HEX 比。要先把画面光照色偏估出来，再判断残差是
  「光照」还是「材质」——
    设计色 (flat_fill)   : 允许 ΔE ≤ 4，超了就是印错色
    材质本色 (gemstone/pearl) : 允许明度与饱和度随光照变化，只卡色相与饱和度总量
    金属 (metal)         : 只看色相（银中性 / 金 ≈ 85°），明度与饱和由高光决定
  高镜面区域不参与判色，直接标 inconclusive，交人工。

Usage:
  python3 scripts/fidelity_check.py --config gate.json [--json out.json] [--overlay o.png] [--strict]
Exit: 0 check ran (read JSON), 2 bad input, 3 capability missing,
      7 --strict and at least one machine-checkable gate failed.

Config:
{
  "image": "shot.png",
  "expected_background": "#F2EFE9",
  "background": {"mode": "seamless", "max_delta_e": 6, "max_patch_std": 3.0, "max_patch_spread_de": 3.0},
  "materials": [
    {"name": "深青玉髓", "expected": "#128D97", "kind": "gemstone", "sample": [430, 505, 22]},
    {"name": "925 银", "expected": "#C8CDD2", "kind": "metal", "sample": [560, 470, 10]}
  ],
  "accessory_rois": [
    {"name": "龙虾扣", "roi": [120, 220, 160, 90], "required": true}
  ]
}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from color_extract import hex_to_rgb, lab_to_hex, rgb_to_lab  # noqa: E402

DEFAULTS = {
    "background": {"mode": "seamless", "max_delta_e": 6.0, "max_patch_std": 3.0, "max_patch_spread_de": 3.0},
    # material tolerance: hue is the material fingerprint, L/C are lighting-sensitive
    "tolerances": {
        # l_tol = how much residual lightness error is still explainable by exposure/shadow
        #         after removing the global exposure offset. metal: L is pure highlight, never judged.
        "flat_fill": {"max_de": 4.0, "max_hue": 3.0, "chroma_ratio": [-0.10, 0.10], "l_tol": 4.0},
        "gemstone": {"max_de": 12.0, "max_hue": 10.0, "chroma_ratio": [-0.35, 0.25], "l_tol": 8.0},
        "pearl":    {"max_de": 14.0, "max_hue": 14.0, "chroma_ratio": [-0.55, 0.45], "l_tol": 10.0},
        "metal":    {"max_de": 20.0, "max_hue": 10.0, "chroma_ratio": [-0.60, 0.60], "l_tol": 999.0},
    },
    "sample": {"max_background_share": 0.30, "reject_background_share": 0.70, "structure_l_range": 10.0},
    "focus": {"min_ratio_to_reference": 0.25, "block": 32},
    "accessory": {"min_foreground_ratio": 0.01},
}

CHECKS = [
    ("code", "background: 边缘均一性 / 色偏 / 渐变（ΔE、patch std、跨区 spread）"),
    ("code", "material: 去光照色偏后的色相与饱和度残差（分设计色/材质本色/金属三档）"),
    ("code", "focus: ROI 内 Laplacian 方差 vs 全图参考锐度 → 该配件是否虚到无法核对"),
    ("code", "presence: ROI 内前景占比 → 声明位置的配件是否根本不在画面里"),
    ("code", "specular: 高光过强区域自动拒判色（inconclusive，不硬判）"),
    ("vision", "配件身份：这里的形状是不是龙虾扣 / 延长链 / 吊坠固定结构"),
    ("vision", "珠数与排列顺序是否与 spec 的 sequence 一致"),
    ("vision", "是否多了装饰、是否穿模、金属反光方向是否一致"),
    ("human", "材质真伪：染色优化 vs 天然、巴洛克珍珠的虹彩倾向"),
    ("human", "该图能否商用（是否需标注 AI 模拟、是否需要补拍）"),
]


# ------------------------------------------------------------------ helpers
def circ_diff(h1: float, h2: float) -> float:
    d = abs((h1 - h2) % 360.0)
    return min(d, 360.0 - d)


def lch(lab: np.ndarray) -> tuple[float, float, float]:
    L, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    return L, math.hypot(a, b), (math.degrees(math.atan2(b, a)) % 360.0)


def laplacian_var(gray: np.ndarray) -> float:
    g = gray.astype(np.float32)
    lap = (-4.0 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def border_patches(lab: np.ndarray, n: int = 8, size: int = 48) -> list[dict]:
    H, W = lab.shape[:2]
    s = min(size, H // 6, W // 6)
    boxes = {
        "TL": (8, 8), "TC": (W // 2 - s // 2, 8), "TR": (W - s - 8, 8),
        "ML": (8, H // 2 - s // 2), "MR": (W - s - 8, H // 2 - s // 2),
        "BL": (8, H - s - 8), "BC": (W // 2 - s // 2, H - s - 8), "BR": (W - s - 8, H - s - 8),
    }
    out = []
    for name, (x, y) in boxes.items():
        patch = lab[y:y + s, x:x + s]
        mean = patch.reshape(-1, 3).mean(axis=0)
        out.append({"where": name, "patch": [int(x), int(y), int(s), int(s)],
                    "lab": [round(float(v), 2) for v in mean],
                    "l_std": round(float(patch[..., 0].std()), 2)})
    return out


def body_pixels(rgb_patch: np.ndarray, lab_patch: np.ndarray) -> tuple[np.ndarray, dict]:
    """Median body colour: drop the brightest 15% (specular) and darkest 15% (shadow)."""
    L = lab_patch[..., 0].ravel()
    lo, hi = np.percentile(L, 15), np.percentile(L, 85)
    mask = (L >= lo) & (L <= hi)
    if mask.sum() < 12:  # tiny / very uniform patch — keep everything
        mask = np.ones_like(L, dtype=bool)
    med = np.median(lab_patch.reshape(-1, 3)[mask], axis=0)
    stats = {
        "l_p15": round(float(lo), 1), "l_p85": round(float(hi), 1),
        "l_range": round(float(np.percentile(L, 95) - np.percentile(L, 5)), 1),
        "pixels_used": int(mask.sum()),
    }
    return med, stats


# ------------------------------------------------------------------ checks
def check_background(lab, cfg) -> dict:
    patches = border_patches(lab)
    exp = cfg.get("expected_background")
    items = []
    for p in patches:
        p["delta_e"] = round(float(np.sqrt(np.sum((np.array(p["lab"]) - np.array(cfg["_exp_lab"])) ** 2))), 2) if exp else None
        items.append(p)
    des = [p["delta_e"] for p in items if p["delta_e"] is not None]
    st = [p["l_std"] for p in items]
    spread = round(max(des) - min(des), 2) if des else 0.0
    tol = cfg["background"]

    if not exp:
        verdict = "no_reference"      # cannot judge purity, only uniformity
    elif max(des) > tol["max_delta_e"]:
        verdict = "off_spec"          # 边缘色与目标差太多（布景色 ≠ 无缝纯色）
    elif spread > tol["max_patch_spread_de"] or max(st) > tol["max_patch_std"]:
        verdict = "lit_backdrop"      # 色相大致对，但有打光渐变/布纹 → 商品图需重打光
    else:
        verdict = "pass_seamless"

    return {
        "status": verdict,
        "max_delta_e": round(max(des), 2) if des else None,
        "min_delta_e": round(min(des), 2) if des else None,
        "spread_delta_e": spread,
        "max_patch_std": round(max(st), 2),
        "patches": items,
        "note": {
            "pass_seamless": "边缘均一且接近目标色，可作无缝背景",
            "lit_backdrop": "平均色接近目标，但存在肉眼可见的明暗/纹理渐变；不是无缝纯色背景",
            "off_spec": "边缘色明显偏离目标；若目标是「无缝纯色」，此图不满足",
            "no_reference": "未给 expected_background，只报告均一性，不判纯度",
        }[verdict],
        "thresholds": tol,
    }


def estimate_cast(bg: dict, cfg) -> dict:
    """Illuminant cast from the known background target — only valid if the target is a priori known."""
    if not cfg.get("expected_background") or bg["status"] == "no_reference":
        return {"available": False, "reason": "no known background target; cannot separate lighting from material"}
    # use the least-contaminated (brightest, most uniform) patches
    patches = sorted(bg["patches"], key=lambda p: p["delta_e"])
    use = patches[:4]
    mean = np.mean([p["lab"] for p in use], axis=0)
    exp = np.array(cfg["_exp_lab"])
    cast = mean - exp
    return {
        "available": True,
        "patches_used": [p["where"] for p in use],
        "cast_lab": [round(float(v), 2) for v in cast],
        "cast_dl": round(float(cast[0]), 2),
        "cast_da": round(float(cast[1]), 2),
        "cast_db": round(float(cast[2]), 2),
        "cast_chroma": round(float(math.hypot(cast[1], cast[2])), 2),
        "note": "背景是已知目标色时，其 a*/b* 偏移即画面光照色偏；比较材质色前先扣掉这部分。",
    }


def check_material(m: dict, rgb, lab, cast, cfg) -> dict:
    x, y, r = m["sample"]
    H, W = lab.shape[:2]
    x0, x1 = max(0, x - r), min(W, x + r)
    y0, y1 = max(0, y - r), min(H, y + r)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return {"name": m.get("name"), "status": "bad_sample", "reason": "sampling window outside image or too small"}
    lp = lab[y0:y1, x0:x1]
    rp = rgb[y0:y1, x0:x1]
    # circular window
    yy, xx = np.mgrid[y0:y1, x0:x1]
    disc = ((xx - x) ** 2 + (yy - y) ** 2) <= max(r - 2, 2) ** 2
    if disc.sum() < 20:
        return {"name": m.get("name"), "status": "bad_sample", "reason": "disc too small"}
    lp = lp[disc]
    med, stats = body_pixels(rp[disc].reshape(-1, 1, 3), lp.reshape(-1, 1, 3))

    # sample sanity: a well-placed disc sits on the product, not on the backdrop
    if cfg.get("_meas_bg_lab") is not None:
        de_bg = np.sqrt(((lp - np.array(cfg["_meas_bg_lab"])) ** 2).sum(axis=-1))
        bg_share = round(float((de_bg < 10).mean()), 3)
    else:
        bg_share = None
    if bg_share is not None and bg_share > cfg["sample"]["max_background_share"]:
        # Colour alone cannot separate "misplaced circle" from "light product on a light backdrop",
        # so look for surface structure: a real object has a tonal ramp, a backdrop does not.
        structured = stats["l_range"] > cfg["sample"]["structure_l_range"]
        if bg_share > cfg["sample"]["reject_background_share"] and not structured:
            return {"name": m.get("name"), "kind": m.get("kind"),
                    "status": "inconclusive_low_separation",
                    "reason": (f"{int(bg_share*100)}% of the disc is within ΔE 10 of the backdrop and the disc is flat "
                               f"(L range {stats['l_range']}). Either the sampling circle is misplaced, or the product is "
                               f"nearly the same colour as the backdrop. Both make a colour verdict unreliable — "
                               f"fix the sample or fix the lighting, then re-run."),
                    "background_share": bg_share, "sample": [x, y, r],
                    "l_range": stats["l_range"]}
        # 30–70% background-like, or a structured object: keep the verdict at low confidence.
        sample_warning = (f"{int(bg_share*100)}% of the disc is within ΔE 10 of the backdrop — "
                          f"product and background are close in colour, verdict is low-confidence")
    else:
        sample_warning = None

    exp_rgb = hex_to_rgb(m["expected"])
    exp_lab = rgb_to_lab(np.array(exp_rgb, dtype=np.float32))
    kind = m.get("kind", "gemstone")
    tol = {**cfg["tolerances"]["gemstone"], **cfg["tolerances"].get(kind, {})}

    L_m, C_m, h_m = lch(med)
    L_e, C_e, h_e = lch(exp_lab)

    # de-cast a*/b* only. Lightness is legitimately set by the lighting, so it is judged separately.
    if cast["available"]:
        a_c = med[1] - cast["cast_da"]
        b_c = med[2] - cast["cast_db"]
    else:
        a_c, b_c = med[1], med[2]
    L_c, C_c, h_c = L_m, math.hypot(a_c, b_c), (math.degrees(math.atan2(b_c, a_c)) % 360.0)

    d_hue = round(circ_diff(h_c, h_e), 1) if C_e > 2 and C_c > 2 else 0.0
    chroma_ratio = round((C_c - C_e) / C_e, 3) if C_e > 2 else 0.0
    d_e_raw = round(float(np.sqrt(np.sum((med - exp_lab) ** 2))), 2)
    d_l = round(L_m - L_e, 1)
    # exposure offset is global (measured on the known background), so remove it before blaming the material
    cast_dl = cast.get("cast_dl", 0.0) if cast["available"] else 0.0
    d_l_residual = round(d_l - cast_dl, 1)
    d_e_dec = round(float(np.sqrt((d_l_residual) ** 2 + (a_c - exp_lab[1]) ** 2 + (b_c - exp_lab[2]) ** 2)), 2)

    # specular guard: a mirror-like surface has no stable body colour
    specular = stats["l_range"] > 55 or stats["l_p85"] > 96
    if specular:
        verdict = "inconclusive_specular"
    elif d_hue > tol["max_hue"] * 2 or not (tol["chroma_ratio"][0] <= chroma_ratio <= tol["chroma_ratio"][1]):
        # wrong hue or chroma collapsed → the material itself is not the declared one
        verdict = "material_mismatch"
    elif d_e_dec > tol["max_de"] and abs(d_l_residual) <= tol.get("l_tol", 8.0):
        verdict = "material_mismatch"
    elif abs(d_l_residual) > tol.get("l_tol", 8.0):
        # hue + chroma match, only tone is off → exposure/shadow/post, correctable, not a material error
        verdict = "tone_shift"
    else:
        verdict = "pass_within_lighting"

    return {
        "name": m.get("name"), "kind": kind,
        "status": verdict,
        "expected_hex": m["expected"],
        "measured_hex_body": lab_to_hex(med),
        "measured_lch": [round(L_m, 1), round(C_c, 1), round(h_c, 1)],
        "expected_lch": [round(L_e, 1), round(C_e, 1), round(h_e, 1)],
        "d_l": d_l, "d_l_residual_after_exposure": d_l_residual,
        "d_hue_deg": d_hue, "chroma_ratio": chroma_ratio,
        "delta_e_raw": d_e_raw, "delta_e_after_decast": d_e_dec,
        "delta_e_is_lighting_only": bool(verdict == "pass_within_lighting" and d_e_raw > tol["max_de"]),
        "background_share_in_disc": bg_share,
        "sample_warning": sample_warning,
        "body_stats": stats,
        "thresholds": tol,
        "sample": [x, y, r],
    }


def check_focus(gray: np.ndarray, rois: list[dict], cfg) -> tuple[list[dict], dict]:
    b = cfg["focus"]["block"]
    H, W = gray.shape
    blocks = []
    for y in range(0, H - b, b):
        for x in range(0, W - b, b):
            blocks.append(laplacian_var(gray[y:y + b, x:x + b]))
    p90 = float(np.percentile(blocks, 90)) if blocks else 0.0

    raw = []
    for roi in rois:
        x, y, w, h = roi["roi"]
        sub = gray[max(0, y):y + h, max(0, x):x + w]
        raw.append(float(laplacian_var(sub)) if sub.size else None)

    # Reference: the sharpest declared accessory ROI. Sharpness here is a RELATIVE question
    # ("is the far side much softer than the near side?"), and a global p90 is dominated by
    # flat backdrop on sparse product shots. Fall back to p90 only with a single ROI.
    valid = [v for v in raw if v is not None]
    if len(valid) >= 2 and max(valid) > 0:
        ref, ref_src = max(valid), "sharpest_declared_roi"
    else:
        ref, ref_src = p90, "global_p90"
    global_ref = {"reference_sharpness": round(ref, 2), "reference_source": ref_src,
                  "global_p90": round(p90, 2), "block": b}

    out = []
    for roi, v in zip(rois, raw):
        if v is None:
            out.append({"name": roi.get("name"), "roi": roi["roi"], "required": roi.get("required", True),
                        "laplacian_var": None, "ratio_to_reference": None, "status": "bad_roi"})
            continue
        ratio = round(v / ref, 3) if ref > 0 else None
        ok = ratio is not None and ratio >= cfg["focus"]["min_ratio_to_reference"]
        out.append({
            "name": roi.get("name"), "roi": roi["roi"], "required": roi.get("required", True),
            "laplacian_var": round(v, 2), "ratio_to_reference": ratio,
            "status": "sharp_enough_to_verify" if ok else "out_of_focus_unverifiable",
        })
    return out, global_ref


def check_presence(rgb, lab, rois, cfg) -> list[dict]:
    # reference = the background AS PHOTOGRAPHED, not the target colour:
    # an off-spec backdrop must not make every pixel look like a product.
    bg_lab = np.array(cfg["_meas_bg_lab"]) if cfg.get("_meas_bg_lab") is not None else None
    out = []
    for roi in rois:
        x, y, w, h = roi["roi"]
        sub = lab[max(0, y):y + h, max(0, x):x + w]
        if sub.size == 0:
            out.append({"name": roi.get("name"), "foreground_ratio": None, "status": "bad_roi"})
            continue
        ref = bg_lab if bg_lab is not None else np.median(np.concatenate(
            [lab[:20].reshape(-1, 3), lab[-20:].reshape(-1, 3)]), axis=0)
        de = np.sqrt(((sub - ref) ** 2).sum(axis=-1))
        fg = float((de > 10).mean())
        out.append({
            "name": roi.get("name"), "roi": roi["roi"],
            "foreground_ratio": round(fg, 4),
            "status": "present" if fg >= cfg["accessory"]["min_foreground_ratio"] else "absent_at_roi",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=False, help="gate config JSON")
    ap.add_argument("--image")
    ap.add_argument("--json")
    ap.add_argument("--overlay")
    ap.add_argument("--strict", action="store_true", help="exit 7 when a machine-checkable gate fails")
    ap.add_argument("--list-checks", action="store_true")
    a = ap.parse_args()

    if a.list_checks:
        print(json.dumps({"ok": True, "checks": [{"by": k, "check": c} for k, c in CHECKS]},
                         ensure_ascii=False, indent=2))
        return 0

    cfg = {}
    if a.config:
        if not os.path.isfile(a.config):
            print(json.dumps({"ok": False, "error": "config_not_found", "path": a.config}, ensure_ascii=False), file=sys.stderr)
            return 2
        cfg = json.load(open(a.config, encoding="utf-8"))
    if a.image:
        cfg["image"] = a.image
    if not cfg.get("image"):
        print(json.dumps({"ok": False, "error": "missing_image"}, ensure_ascii=False), file=sys.stderr)
        return 2
    # relative image paths resolve against the config file first, then the cwd
    if not os.path.isabs(cfg["image"]):
        cands = []
        if a.config:
            cands.append(os.path.join(os.path.dirname(os.path.abspath(a.config)), cfg["image"]))
        cands.append(os.path.abspath(cfg["image"]))
        hit = next((c for c in cands if os.path.isfile(c)), None)
        if hit:
            cfg["image"] = hit

    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": "capability_missing", "message": f"Pillow: {exc}"}, ensure_ascii=False), file=sys.stderr)
        return 3
    if not os.path.isfile(cfg["image"]):
        print(json.dumps({"ok": False, "error": "image_not_found", "path": cfg["image"]}, ensure_ascii=False), file=sys.stderr)
        return 2

    for k, v in DEFAULTS.items():
        if k in ("background", "focus", "accessory", "sample", "tolerances"):
            cfg[k] = {**v, **cfg.get(k, {})}
            if k == "tolerances":
                cfg[k] = {kk: {**vv, **cfg[k].get(kk, {})} for kk, vv in v.items()}

    img = Image.open(cfg["image"]).convert("RGB")
    rgb = np.asarray(img, dtype=np.float32)
    lab = rgb_to_lab(rgb)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    cfg["_exp_lab"] = list(rgb_to_lab(np.array(hex_to_rgb(cfg["expected_background"]), dtype=np.float32))) if cfg.get("expected_background") else None

    bg = check_background(lab, cfg)
    cast = estimate_cast(bg, cfg)
    # background as photographed — used for foreground detection and sample sanity
    cfg["_meas_bg_lab"] = [float(v) for v in np.mean([p["lab"] for p in bg["patches"]], axis=0)]

    materials = [check_material(m, rgb, lab, cast, cfg) for m in cfg.get("materials", [])]
    rois = cfg.get("accessory_rois", [])
    focus, fref = check_focus(gray, rois, cfg)
    presence = check_presence(rgb, lab, rois, cfg)

    # join focus + presence per roi
    accessories = []
    for roi in rois:
        f = next((x for x in focus if x.get("name") == roi.get("name")), {})
        p = next((x for x in presence if x.get("name") == roi.get("name")), {})
        if p.get("status") == "absent_at_roi":
            st, why = "absent_at_roi", "declared position contains almost no foreground — the part is missing or moved"
        elif f.get("status") == "out_of_focus_unverifiable":
            st, why = "unverifiable_blur", "area is too soft to judge the part; cannot be signed off from this image"
        elif f.get("status") == "sharp_enough_to_verify":
            st, why = "ready_for_vision_review", "part is present and sharp — identity still needs a vision model or a human"
        else:
            st, why = "bad_roi", "roi unusable"
        accessories.append({"name": roi.get("name"), "required": roi.get("required", True),
                            "roi": roi["roi"], "status": st, "reason": why,
                            "foreground_ratio": p.get("foreground_ratio"),
                            "focus_ratio_to_reference": f.get("ratio_to_reference")})

    machine_fail = []
    correctable = []
    if bg["status"] == "off_spec":
        machine_fail.append(f"background off spec (max ΔE {bg['max_delta_e']} > {cfg['background']['max_delta_e']})")
    if bg["status"] == "lit_backdrop":
        machine_fail.append(f"background is a lit backdrop, not seamless (spread ΔE {bg['spread_delta_e']}, patch std {bg['max_patch_std']})")
    for m in materials:
        if m["status"] == "material_mismatch":
            machine_fail.append(
                f"{m['name']}: ΔE(decast) {m['delta_e_after_decast']} / hue drift {m['d_hue_deg']}° / chroma {m['chroma_ratio']:+.2f}")
        elif m["status"] == "tone_shift":
            correctable.append(
                f"{m['name']}: 色相与饱和度对得上（drift {m['d_hue_deg']}°, chroma {m['chroma_ratio']:+.2f}），"
                f"只有明度偏 {m['d_l_residual_after_exposure']:+} L*（已扣除整体曝光位移）→ 后期可修正，不算材质错")
    for acc in accessories:
        if acc["required"] and acc["status"] == "absent_at_roi":
            machine_fail.append(f"{acc['name']}: absent at declared ROI (foreground {acc['foreground_ratio']})")

    needs_human = [x["name"] for x in accessories if x["status"] == "unverifiable_blur"]
    needs_human += [m["name"] for m in materials if m["status"] in ("inconclusive_specular", "bad_sample", "inconclusive_low_separation")]

    verdict = "fail" if machine_fail else ("pass_needs_review" if (needs_human or correctable) else "pass_code_checks")

    report = {
        "ok": True,
        "image": os.path.abspath(cfg["image"]),
        "size": list(img.size),
        "verdict": verdict,
        "machine_failures": machine_fail,
        "correctable_in_post": correctable,
        "needs_human_or_vision": needs_human,
        "background": bg,
        "illuminant_cast": cast,
        "materials": materials,
        "accessory_focus_reference": fref,
        "accessories": accessories,
        "code_checks": [c for k, c in CHECKS if k == "code"],
        "requires_vision": [c for k, c in CHECKS if k == "vision"],
        "requires_human": [c for k, c in CHECKS if k == "human"],
        "honesty_note": "本脚本只判定可测项。verdict=pass 不代表「商品图合格」——配件身份、珠数、排列仍需视觉模型或人工核对。",
    }

    if a.overlay:
        try:
            from PIL import ImageDraw
            ov = img.copy()
            d = ImageDraw.Draw(ov)
            for p in bg["patches"]:
                x, y, s, _ = p["patch"]
                d.rectangle([x, y, x + s, y + s], outline=(0, 160, 200), width=2)
            for m in materials:
                x, y, r = m["sample"]
                col = (30, 170, 60) if m["status"].startswith("pass") else ((230, 60, 40) if m["status"] == "material_mismatch" else (200, 140, 20))
                d.ellipse([x - r, y - r, x + r, y + r], outline=col, width=3)
            for acc in accessories:
                x, y, w, h = acc["roi"]
                col = {"ready_for_vision_review": (30, 140, 230), "absent_at_roi": (230, 40, 40)}.get(acc["status"], (220, 150, 20))
                d.rectangle([x, y, x + w, y + h], outline=col, width=3)
            ov.save(a.overlay)
            report["overlay"] = os.path.abspath(a.overlay)
        except Exception as exc:
            report["overlay_error"] = str(exc)

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(text)
    if a.strict and machine_fail:
        return 7
    return 0


if __name__ == "__main__":
    sys.exit(main())
