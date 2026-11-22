#!/usr/bin/env python3
"""MuseLab :: makeup render gate (the part that can actually be measured).

What this script CAN decide from pixels — and only inside rectangles you give it:
  1. eyeshadow hue drift + chroma ratio vs the designed colour, per eye
  2. eyelid coverage: how much of the declared lid band carries pigment, how far it
     climbs from the lash line, and whether the upper half stayed clean
  3. left/right balance by colour AND by coverage AND by extent — the median colour
     alone is NOT enough (two lids can share a median while one is painted 3× larger)
  4. lower lid: whether pigment or a specular band sits below the lash line
  5. lip colour drift (soft: lip pigment and lighting move it, so it never hard-fails)
  6. lip-boundary colour bleed into the skin just outside the lip ROI
  7. skin texture retention vs a reference face photo (Laplacian energy ratio)

What it CANNOT decide, ever, from pixels:
  - "is this line sitting on the lash line" (needs eyelid anatomy)
  - "does this brow / lash look deformed"
  - "is the blush / contour in a plausible place"
  - "are there extra eyes / lashes / brows"
  - "does the lip colour stay inside the real lip edge" (only bleed, not contour match)
  - "is this still the same person"  ← identity cannot be judged by a script, period
  - "could a makeup artist reproduce this on a real face"

The ROI has to come from a vision model or a human. This script does arithmetic on
a region, it does not find faces, eyes or lips. There is no landmark detector in the
target environment (no cv2 / mediapipe / dlib / face_recognition): every box below is
declared by hand, and `roi_provenance` in the config records who declared it.

Eyelid boxes are *relative* on purpose: `lid` spans from the brow side down past the
lash line, and the script normalises everything by the box height, so the verdict
survives different face sizes and crops. `lash_edge: "bottom"` says which side of the
box the lashes are on.

Every run therefore prints `not_code_testable` alongside the measurements, and
`verdict=pass_code_checks` means "no measurable drift in the regions you declared" —
NOT "this makeup image is correct".

Usage:
  python3 scripts/makeup_check.py --image shot.png --config gate.json
      [--json out.json] [--overlay qa.png] [--strict]
  python3 scripts/makeup_check.py --list-checks

Exit: 0 ran (read the JSON), 2 bad input, 3 capability missing,
      7 --strict and a machine-checkable check failed.

Config:
{
  "image": "shot.png",
  "reference_image": "face.jpg",
  "skin_baseline": [x, y, r],
  "roi_provenance": "who declared these boxes",
  "eyes": {
    "left":  {"roi": [x, y, w, h], "expected_hex": "#2E8B97",
              "lid":   [x, y, w, h],      // brow side down past the lash line
              "under": [x, y, w, h]},     // band below the lower lashes
    "right": {"roi": [x, y, w, h], "expected_hex": "#2E8B97",
              "lid": [x, y, w, h], "under": [x, y, w, h]}
  },
  "lash_edge": "bottom",                  // which side of the lid box holds the lashes
  "lips": {"roi": [x, y, w, h], "expected_hex": "#8C4A52", "surround": [x, y, w, h]},
  "skin_texture": {"roi": [x, y, w, h]},
  "thresholds": {"hue_deg": 20, "lr_delta_e": 8, "lr_hue_deg": 15, "texture_min_ratio": 0.45}
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
    "hue_deg": 20.0,              # eyeshadow hue drift still explainable by skin blending + light
    "min_chroma_for_hue": 4.0,    # below this the "expected" colour has no meaningful hue
    # A pixel counts as "makeup, not skin" when its a*/b* sit this far from the skin baseline.
    # Chroma alone cannot separate them: tan skin (C≈25) and teal shadow (C≈22) have the same
    # chroma but opposite hue, so the selector has to be a distance from skin, not a threshold.
    "makeup_delta_ab": 8.0,
    "min_makeup_share": 0.03,     # less than 3% of the ROI differing from skin = no detectable makeup
    "specular_keep_pct": 82.0,    # drop the brightest 18% (highlights) before taking a median
    "lr_delta_e": 8.0,
    "lr_hue_deg": 15.0,
    # ---- eyelid coverage. All heights are fractions of the declared lid box, never px:
    # a 100px lid in a close-up and a 40px lid in a 3/4 shot must give the same number.
    "lid_row_min_share": 0.22,    # a row "carries pigment" when this share of it is off-skin colour
    "lid_delta_ab": 5.0,          # looser than makeup_delta_ab: catches the sheer fading edge
    "lid_coverage_min": 0.04,     # < 4% of the lid box = no shadow at all
    "lid_extent_max": 0.62,       # design says "lower half of the visible lid"; 0.62 allows blur
    "lid_upper_max_share": 0.18,  # upper half of the lid box should stay mostly clean skin
    "lid_asym_coverage": 0.12,    # absolute difference in coverage share between the two lids
    "lid_asym_extent": 0.25,      # absolute difference in climb height, as a fraction
    "lid_asym_delta_e": 8.0,      # colour difference between the two lids
    # ---- lower lid
    "under_specular_delta_l": 15.0,  # L* above the band's own median = a specular streak
    "under_specular_share": 0.02,    # more than 2% of the band = a visible bright band
    "under_delta_ab": 10.0,          # stricter than the lid cut: a cheek is warmer than a forehead
    "under_pigment_share": 0.06,     # off-skin pigment under the lashes
    "under_pigment_fail": 0.15,      # this much, in the shadow's own hue, is drawn makeup
    "lip_hue_warn": 22.0,         # soft only: lip pigment + lighting shift it legitimately
    "lip_chroma_ratio_low": 0.45,
    "bleed_hue_deg": 20.0,
    "bleed_mix_fraction": 0.20,   # 20% of the way from skin to lip colour outside the lip = suspicious
    "bleed_max_perp": 0.55,       # how far off that skin→lip line still counts as "the same hue family"
    "texture_min_ratio": 0.45,    # < 45% of the reference's high-frequency energy = over-smoothed
    "texture_max_ratio": 1.80,    # > 180% = texture added / sharpening halo
}

# The ten items a buyer-facing review needs. Only some are measurable; say which.
CHECKLIST = [
    (1, "眼影颜色是否符合设计方向", ["code", "vision"],
     "脚本只比 ROI 内色相与饱和度；'这块是不是眼影'仍需视觉"),
    (2, "左右眼妆是否协调", ["code"],
     "覆盖面积 + 自睫毛根爬升高度 + 颜色三项独立比对；两个 ROI 中位色接近不算通过"),
    (3, "眼线位置与眼部结构是否自然", ["vision"],
     "需要判断睑缘位置，代码不定位解剖结构"),
    (4, "眉毛与睫毛是否畸形", ["vision"],
     "形态完整性判断"),
    (5, "腮红与修容位置是否合理", ["vision", "human"],
     "需要面部解剖参照"),
    (6, "唇线与真实唇缘是否匹配", ["vision", "code(partial)"],
     "代码只能测唇缘外侧有没有溢色，不能确认唇形对不对"),
    (7, "是否出现额外眼睛/睫毛/眉毛/五官", ["vision"],
     "计数与形态判断"),
    (8, "面部皮肤是否过度磨皮", ["code", "vision"],
     "需同一 ROI 的参考图；无参考图只能视觉判"),
    (9, "是否擅自改变人物面部特征", ["vision", "human"],
     "身份保真无法用脚本判定，必须人看"),
    (10, "能否用真实化妆方法近似完成", ["human"],
     "需要化妆经验"),
]

NOT_CODE_TESTABLE = [
    {"item": "眼线位置是否符合眼部结构", "by": "vision",
     "why": "脚本不定位睑缘，无法判断线的落点"},
    {"item": "眉毛/睫毛是否畸形", "by": "vision", "why": "形态完整性不是颜色统计能回答的"},
    {"item": "腮红/修容位置是否合理", "by": "vision+human", "why": "需要解剖参照与审美判断"},
    {"item": "是否出现额外面部特征", "by": "vision", "why": "需要计数与识别"},
    {"item": "唇形是否与真实唇缘匹配", "by": "vision",
     "why": "本脚本只测唇缘外侧溢色，不测轮廓吻合"},
    {"item": "人物身份/五官比例是否被改变", "by": "human",
     "why": "没有可靠的身份度量；任何自动断言都是虚构"},
    {"item": "能否真实化出", "by": "human", "why": "需要化妆经验与产品知识"},
]


# ------------------------------------------------------------------ small helpers
def die(msg: str, code: int = 2, **extra):
    payload = {"ok": False, "error": msg}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(code)


def load_rgb(path: str) -> np.ndarray:
    try:
        from PIL import Image
    except ImportError:
        die("Pillow is required", code=3)
    if not os.path.isfile(path):
        die("image_not_found", path=path)
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float32)


def roi_box(roi, w: int, h: int):
    """[x, y, w, h] -> (x0, y0, x1, y1), clamped to the canvas."""
    if not (isinstance(roi, (list, tuple)) and len(roi) == 4):
        die("bad_roi", roi=roi, hint="roi must be [x, y, w, h]")
    x, y, rw, rh = [int(v) for v in roi]
    x0, y0 = max(0, min(x, w - 1)), max(0, min(y, h - 1))
    x1, y1 = max(x0 + 1, min(x + rw, w)), max(y0 + 1, min(y + rh, h))
    return x0, y0, x1, y1


def median_lab(pixels_rgb: np.ndarray, keep_pct: float) -> np.ndarray:
    """Median Lab after dropping the brightest (1 - keep_pct)% — highlights are not colour."""
    lab = rgb_to_lab(pixels_rgb)
    L = lab[:, 0]
    keep = L <= np.percentile(L, keep_pct)
    return np.median(lab[keep], axis=0)


def chroma_hue(lab) -> tuple[float, float]:
    C = float(math.hypot(lab[1], lab[2]))
    h = (math.degrees(math.atan2(lab[2], lab[1])) + 360.0) % 360.0
    return C, h


def hue_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def delta_e76(a, b) -> float:
    return float(np.linalg.norm(np.asarray(a[:3], dtype=np.float64) - np.asarray(b[:3], dtype=np.float64)))


def laplacian_var(gray: np.ndarray) -> float:
    """4-neighbour Laplacian variance — a proxy for high-frequency (texture) energy."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = (-4.0 * gray[1:-1, 1:-1] + gray[:-2, 1:-1] + gray[2:, 1:-1]
           + gray[1:-1, :-2] + gray[1:-1, 2:])
    return float(lap.var())


def probe_landmark_tools() -> dict:
    """Is there anything here that could find a face on its own?

    If not, every ROI must come from a human/vision model, and the report has to say so
    instead of implying the boxes are anatomical truth.
    """
    found = {}
    for mod in ("cv2", "mediapipe", "dlib", "face_recognition"):
        try:
            __import__(mod)
            found[mod] = True
        except Exception:
            found[mod] = False
    available = [k for k, v in found.items() if v]
    return {"available": available, "probed": found,
            "roi_source": "human_or_vision_declared" if not available else "landmark_assisted_possible",
            "note": ("没有可用的人脸关键点库：脚本内的 ROI 全部由人工/视觉模型声明，"
                     "不得把固定坐标当作通用识别算法" if not available else
                     "环境里有关键点库，但仍需自行验证其在本图上的可靠性再用")}


# ---------------------------------------------------------------------- the checks
def check_eyeshadow(img: np.ndarray, roi, expected_hex: str, skin_lab: np.ndarray | None, th: dict):
    """Hue/saturation drift of the makeup colour inside one eye ROI."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(roi, w, h)
    patch = img[y0:y1, x0:x1].reshape(-1, 3)
    if patch.shape[0] < 50:
        return {"status": "bad_sample", "reason": "ROI smaller than 50 px"}

    exp_lab = rgb_to_lab(np.asarray([hex_to_rgb(expected_hex)], dtype=np.float32))[0]
    C_exp, h_exp = chroma_hue(exp_lab)

    lab = rgb_to_lab(patch)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    C = np.hypot(a, b)
    out = {
        "roi": [x0, y0, x1 - x0, y1 - y0],
        "expected": {"hex": expected_hex.upper(),
                     "lab": [round(float(v), 1) for v in exp_lab],
                     "chroma": round(C_exp, 1), "hue_deg": round(h_exp, 1)},
        "roi_median": None, "makeup_share": 0.0,
        "source": "roi_pixels_chroma_filtered",
    }

    if skin_lab is None:
        out.update({"status": "inconclusive_no_skin_baseline",
                    "reason": "no skin_baseline ROI: cannot separate makeup from skin"})
        return out

    C_skin = float(math.hypot(skin_lab[1], skin_lab[2]))
    d_ab = np.hypot(a - float(skin_lab[1]), b - float(skin_lab[2]))
    sel = d_ab > th["makeup_delta_ab"]
    share = float(sel.sum()) / max(1, C.size)
    out["makeup_share"] = round(share, 4)
    out["skin_baseline"] = {"lab": [round(float(v), 1) for v in skin_lab],
                            "chroma": round(C_skin, 1),
                            "selector": f"a*/b* distance from skin > {th['makeup_delta_ab']} (L ignored: "
                                        "shadow and highlight are not makeup)"}

    if C_exp < th["min_chroma_for_hue"]:
        out.update({"status": "inconclusive_neutral_design",
                    "reason": f"designed colour chroma {C_exp:.1f} < {th['min_chroma_for_hue']}: "
                              "hue is not a meaningful target for a neutral/beige look"})
        return out

    if share < th["min_makeup_share"]:
        out.update({"status": "inconclusive_no_makeup_detected",
                    "reason": f"only {share:.1%} of ROI pixels are more chromatic than skin "
                              f"(threshold {th['min_makeup_share']:.0%}); either no eye makeup in this "
                              "ROI, the ROI is misplaced, or the design is very sheer"})
        return out

    m_lab = np.median(lab[sel], axis=0)
    C_m, h_m = chroma_hue(m_lab)
    d_h = hue_diff(h_m, h_exp)
    ratio = C_m / C_exp if C_exp > 0 else float("nan")
    out["roi_median"] = {"lab": [round(float(v), 1) for v in m_lab],
                         "hex": lab_to_hex(m_lab),
                         "chroma": round(C_m, 1), "hue_deg": round(h_m, 1)}
    out.update({"delta_hue_deg": round(d_h, 1), "chroma_ratio": round(ratio, 2),
                "thresholds": {"hue_deg": th["hue_deg"]}})

    if d_h <= th["hue_deg"] and 0.35 <= ratio <= 2.6:
        out["status"] = "consistent"
    elif d_h <= th["hue_deg"]:
        out["status"] = "consistent_hue_desaturated" if ratio < 0.35 else "consistent_hue_oversaturated"
        out["reason"] = (f"hue agrees ({d_h:.1f}°) but chroma is {ratio:.2f}× the design — "
                         "blending, layering or light can do this; check with the design before failing")
    else:
        out["status"] = "divergent"
        out["reason"] = f"hue drift {d_h:.1f}° > {th['hue_deg']}° — this is a different colour family"
    return out


def check_left_right(left: dict, right: dict, th: dict):
    """Two eyes should not disagree with each other more than they disagree with the design."""
    if left.get("roi_median") is None or right.get("roi_median") is None:
        return {"status": "skipped", "reason": "both eye ROIs need a measurable makeup colour"}
    L = np.array(left["roi_median"]["lab"], dtype=np.float64)
    R = np.array(right["roi_median"]["lab"], dtype=np.float64)
    d_e = delta_e76(L, R)
    _, hL = chroma_hue(L)
    _, hR = chroma_hue(R)
    d_h = hue_diff(hL, hR)
    status = "balanced"
    if d_e > th["lr_delta_e"] or d_h > th["lr_hue_deg"]:
        status = "asymmetric"
    return {"status": status, "delta_e": round(d_e, 2), "delta_hue_deg": round(d_h, 1),
            "thresholds": {"delta_e": th["lr_delta_e"], "delta_hue_deg": th["lr_hue_deg"]},
            "note": "ROI placement error produces the same signal as real asymmetry — confirm before failing"}


def _offskin_mask(lab: np.ndarray, skin_lab: np.ndarray, th: dict,
                  thr: float | None = None) -> np.ndarray:
    """a*/b* distance from the skin baseline. L* is ignored on purpose: lid shadow and
    highlight both change L*, and neither is makeup.

    `thr` lets the eyelid coverage check use a looser cut than the colour check: a sheer
    wash fades out before it crosses the stricter 'this is pigment' line, and we want the
    visible extent, not just the saturated core.
    """
    limit = th["makeup_delta_ab"] if thr is None else thr
    d_ab = np.hypot(lab[..., 1] - float(skin_lab[1]), lab[..., 2] - float(skin_lab[2]))
    return d_ab > limit


def _smooth(values: np.ndarray, window: int = 3) -> np.ndarray:
    if values.size < window:
        return values
    return np.convolve(values, np.ones(window) / window, mode="same")


def _climb_fraction(row_share: np.ndarray, row_min: float, edge_rows: int = 2):
    """How far pigment climbs from the lash side, as a fraction of the band height.

    The first rows next to the lashes can be a feathered edge (a blended-out shadow ends
    slightly before the box edge), so up to `edge_rows` below-threshold rows are skipped
    rather than treated as "no makeup". After that, two sub-threshold rows in a row stop
    the climb — one alone can be a crease line.
    """
    H = int(row_share.size)
    if H == 0:
        return 0.0, 0
    start = 0
    while start < H and start < edge_rows and row_share[start] < row_min:
        start += 1
    covered, dips = 0, 0
    for i in range(start, H):
        if row_share[i] >= row_min:
            covered, dips = i + 1, 0
        else:
            dips += 1
            if dips >= 2:
                break
    return (covered / float(H)), covered


def check_eyelid(img: np.ndarray, lid_roi, expected_hex: str, skin_lab: np.ndarray | None,
                 th: dict, lash_edge: str = "bottom"):
    """Coverage and vertical extent of eyeshadow inside a declared lid band.

    Why this exists: two ROIs can have the *same median colour* while one lid carries a
    wide wash and the other a thin line at the lashes — a median cannot see that. So this
    measures per-row pigment share, how far it climbs from the lash line (as a fraction of
    the box height, not pixels), and whether the half nearer the brow stayed clean.
    """
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(lid_roi, w, h)
    bh, bw = y1 - y0, x1 - x0
    if bh < 8 or bw < 8:
        return {"status": "bad_sample", "reason": "lid ROI smaller than 8×8 px"}
    lab = rgb_to_lab(img[y0:y1, x0:x1].reshape(-1, 3)).reshape(bh, bw, 3)
    out = {"roi": [x0, y0, bw, bh], "lash_edge": lash_edge}

    if skin_lab is None:
        out.update({"status": "inconclusive_no_skin_baseline",
                    "reason": "no skin_baseline ROI: pigment cannot be separated from skin"})
        return out

    mask = _offskin_mask(lab, skin_lab, th, thr=th["lid_delta_ab"])
    row_share = _smooth(mask.mean(axis=1).astype(np.float64))
    coverage = float(mask.mean())

    ordered = row_share[::-1] if lash_edge == "bottom" else row_share
    extent, climbed_rows = _climb_fraction(ordered, th["lid_row_min_share"])

    if lash_edge == "bottom":
        near_brow = row_share[: bh // 2].mean() if bh >= 2 else 0.0
    else:
        near_brow = row_share[bh // 2:].mean() if bh >= 2 else 0.0

    out.update({
        "coverage_share": round(coverage, 4),
        "climb_fraction": round(extent, 3),
        "climb_rows": climbed_rows,
        "band_height_px": bh,
        "share_near_brow_half": round(float(near_brow), 4),
        "profile_lash_to_brow": [round(float(v), 3) for v in ordered],
        "selector": (f"a*/b* distance from skin > {th['lid_delta_ab']} for coverage/extent "
                     f"(looser than the colour check's {th['makeup_delta_ab']}: a sheer wash "
                     "fades out before it reaches full pigment)"),
    })

    strict = _offskin_mask(lab, skin_lab, th)   # colour is measured on the stricter cut
    if strict.any():
        m_lab = np.median(lab[strict], axis=0)
        C_m, h_m = chroma_hue(m_lab)
        out["pigment_colour"] = {"hex": lab_to_hex(m_lab),
                                 "lab": [round(float(v), 1) for v in m_lab],
                                 "chroma": round(C_m, 1), "hue_deg": round(h_m, 1)}
        out["contrast_vs_skin_delta_e"] = round(delta_e76(m_lab, skin_lab), 1)
        exp_lab = rgb_to_lab(np.asarray([hex_to_rgb(expected_hex)], dtype=np.float32))[0]
        C_e, h_e = chroma_hue(exp_lab)
        if C_m >= th["min_chroma_for_hue"]:
            out["delta_hue_vs_design_deg"] = round(hue_diff(h_m, h_e), 1)
            out["chroma_ratio"] = round(C_m / C_e, 2) if C_e else None

    if coverage < th["lid_coverage_min"]:
        out["status"] = "no_pigment"
        out["reason"] = (f"only {coverage:.1%} of the lid band differs from skin "
                         f"(threshold {th['lid_coverage_min']:.0%}) — no eyeshadow detected here")
    elif extent > th["lid_extent_max"]:
        out["status"] = "full_lid"
        out["reason"] = (f"pigment climbs {extent:.0%} of the band from the lash line "
                         f"(limit {th['lid_extent_max']:.0%}) — it covers the lid, not the lower half")
    elif near_brow > th["lid_upper_max_share"]:
        out["status"] = "reaches_upper_lid"
        out["reason"] = (f"the half nearer the brow is {near_brow:.0%} pigmented "
                         f"(limit {th['lid_upper_max_share']:.0%}) — the wash runs too high")
    else:
        out["status"] = "within_band"
    return out


def check_eyelid_balance(left: dict, right: dict, th: dict):
    """Compare two lids on coverage, climb height and colour — three independent signals.

    A single signal can be a placement error, so `asymmetric` needs at least two to agree.
    One lid with no pigment at all next to a pigmented one counts as two signals.
    """
    miss = [s for s, c in (("left", left), ("right", right))
            if c.get("status") in ("bad_sample", "inconclusive_no_skin_baseline")]
    if miss:
        return {"status": "skipped", "reason": f"{miss} lid ROI unusable"}

    cov_l, cov_r = left.get("coverage_share", 0.0), right.get("coverage_share", 0.0)
    ext_l, ext_r = left.get("climb_fraction", 0.0), right.get("climb_fraction", 0.0)
    signals, detail = [], {}

    d_cov = abs(cov_l - cov_r)
    detail["delta_coverage"] = round(d_cov, 4)
    if d_cov > th["lid_asym_coverage"]:
        signals.append("coverage")

    d_ext = abs(ext_l - ext_r)
    detail["delta_climb"] = round(d_ext, 3)
    if d_ext > th["lid_asym_extent"]:
        signals.append("climb_height")

    if left.get("pigment_colour") and right.get("pigment_colour"):
        L = np.array(left["pigment_colour"]["lab"], dtype=np.float64)
        R = np.array(right["pigment_colour"]["lab"], dtype=np.float64)
        d_e = delta_e76(L, R)
        _, hL = chroma_hue(L)
        _, hR = chroma_hue(R)
        detail["delta_e"] = round(d_e, 2)
        detail["delta_hue_deg"] = round(hue_diff(hL, hR), 1)
        if d_e > th["lid_asym_delta_e"]:
            signals.append("colour")

    bare = [s for s, c in (("left", left), ("right", right)) if c.get("status") == "no_pigment"]
    if len(bare) == 1:
        other = cov_r if bare[0] == "left" else cov_l
        if other >= th["lid_coverage_min"] * 2:
            signals += ["bare_" + bare[0]]
            detail["one_lid_bare"] = bare[0]

    status = "balanced"
    if len(signals) >= 2:
        status = "asymmetric"
    elif len(signals) == 1:
        status = "minor_difference"

    return {"status": status, "signals_exceeded": signals, **detail,
            "coverage": {"left": cov_l, "right": cov_r},
            "climb_fraction": {"left": ext_l, "right": ext_r},
            "thresholds": {"coverage": th["lid_asym_coverage"], "climb": th["lid_asym_extent"],
                           "delta_e": th["lid_asym_delta_e"]},
            "note": ("needs ≥2 independent signals to call it asymmetric; "
                     "a misplaced box can fake one signal, not two")}


def check_under_lid(img: np.ndarray, under_roi, skin_lab: np.ndarray | None,
                    expected_hex: str, th: dict):
    """Anything sitting below the lower lashes: pigment, a specular band, or both.

    The design in question says "no lower-lid makeup". A specular streak cannot be told
    apart from a natural 卧蚕 highlight or dry skin, so that one is always an observation.
    Pigment in the eyeshadow's own hue, wide enough to be intentional, is a hard failure.
    """
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(under_roi, w, h)
    patch = img[y0:y1, x0:x1].reshape(-1, 3)
    if patch.shape[0] < 50:
        return {"status": "bad_sample", "reason": "under-lid ROI smaller than 50 px"}
    lab = rgb_to_lab(patch)
    L = lab[:, 0]
    band_median_L = float(np.median(L))
    out = {"roi": [x0, y0, x1 - x0, y1 - y0],
           "band_median_L": round(band_median_L, 1)}

    pigment_share = 0.0
    if skin_lab is not None:
        mask = _offskin_mask(lab, skin_lab, th, thr=th["under_delta_ab"])
        pigment_share = float(mask.mean())
        out["pigment_share"] = round(pigment_share, 4)
        if mask.any():
            m_lab = np.median(lab[mask], axis=0)
            C_m, h_m = chroma_hue(m_lab)
            out["pigment_colour"] = {"hex": lab_to_hex(m_lab), "chroma": round(C_m, 1),
                                     "hue_deg": round(h_m, 1)}
            exp_lab = rgb_to_lab(np.asarray([hex_to_rgb(expected_hex)], dtype=np.float32))[0]
            C_e, h_e = chroma_hue(exp_lab)
            out["delta_hue_vs_design_deg"] = round(hue_diff(h_m, h_e), 1)
            out["same_hue_family_as_shadow"] = bool(
                C_m >= th["min_chroma_for_hue"] and hue_diff(h_m, h_e) <= th["hue_deg"])

    spec = float((L > band_median_L + th["under_specular_delta_l"]).mean())
    out["specular_share"] = round(spec, 4)
    out["specular_delta_l"] = th["under_specular_delta_l"]

    has_pig = pigment_share >= th["under_pigment_share"]
    has_spec = spec >= th["under_specular_share"]
    same_family = bool(out.get("same_hue_family_as_shadow"))
    if has_pig and same_family and has_spec:
        out["status"] = "pigment_and_specular"
    elif has_pig and same_family:
        out["status"] = "pigment_below_lid"
    elif has_spec:
        out["status"] = "specular_below_lid"
        if has_pig:
            out["note_other_pigment"] = ("band also carries off-skin pigment, but in another hue "
                                         "(warm cheek / crease), not the eyeshadow colour")
    elif has_pig:
        out["status"] = "offskin_pigment_other_hue"
    else:
        out["status"] = "clean"

    out["hard_fail"] = bool(
        pigment_share >= th["under_pigment_fail"] and same_family)
    out["reason"] = ("pigment in the shadow's hue family, wide enough to be drawn rather than a "
                     "natural crease" if out["hard_fail"] else
                     "specular streaks are indistinguishable from a natural under-eye highlight — "
                     "vision confirms" if has_spec else
                     "off-skin pigment present but in another hue family — usually cheek colour, "
                     "not lower-lid makeup" if has_pig else "")
    return out


def check_lips(img: np.ndarray, roi, expected_hex: str, th: dict):
    """Soft colour comparison: lip pigment, gloss and light all shift the rendered colour."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(roi, w, h)
    patch = img[y0:y1, x0:x1].reshape(-1, 3)
    if patch.shape[0] < 50:
        return {"status": "bad_sample", "reason": "ROI smaller than 50 px"}
    m = median_lab(patch, th["specular_keep_pct"])
    C_m, h_m = chroma_hue(m)
    exp_lab = rgb_to_lab(np.asarray([hex_to_rgb(expected_hex)], dtype=np.float32))[0]
    C_e, h_e = chroma_hue(exp_lab)
    d_h = hue_diff(h_m, h_e)
    ratio = C_m / C_e if C_e > 0 else float("nan")
    status = "consistent"
    if d_h > th["lip_hue_warn"] or ratio < th["lip_chroma_ratio_low"]:
        status = "divergent_soft"
    return {"status": status, "roi_median": {"hex": lab_to_hex(m),
                                             "lab": [round(float(v), 1) for v in m],
                                             "chroma": round(C_m, 1), "hue_deg": round(h_m, 1)},
            "expected": {"hex": expected_hex.upper(), "chroma": round(C_e, 1), "hue_deg": round(h_e, 1)},
            "delta_hue_deg": round(d_h, 1), "chroma_ratio": round(ratio, 2),
            "hard_fail": False,
            "reason": "soft only — a matte/glossy/hydrated lip shifts hue and chroma legitimately"}


def check_lip_bleed(img: np.ndarray, surround_roi, skin_lab: np.ndarray | None,
                    lip: dict | None, th: dict):
    """Colour leaking into the skin just outside the lip ROI.

    Chroma gain alone is not a usable signal: on tan skin a mauve lip bleed can *lower*
    the chroma. So project the surround colour onto the skin→lip axis and report how far
    it has travelled (`mix_fraction`). 0 = clean skin, 1 = full lip colour.
    """
    if skin_lab is None:
        return {"status": "skipped", "reason": "no skin_baseline ROI"}
    if not lip or lip.get("roi_median") is None:
        return {"status": "skipped", "reason": "no measurable lip colour"}
    lip_lab = np.array(lip["roi_median"]["lab"], dtype=np.float64)
    axis = lip_lab[1:3] - skin_lab[1:3]
    axis_len = float(np.linalg.norm(axis))
    if axis_len < 6.0:
        return {"status": "skipped",
                "reason": f"lip colour is only {axis_len:.1f} a*/b* away from skin — a bleed would be "
                          "undetectable in this pairing"}

    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(surround_roi, w, h)
    patch = img[y0:y1, x0:x1].reshape(-1, 3)
    if patch.shape[0] < 50:
        return {"status": "bad_sample", "reason": "surround ROI smaller than 50 px"}
    m = median_lab(patch, th["specular_keep_pct"])
    C_m, h_m = chroma_hue(m)
    _, h_lip = chroma_hue(lip_lab)
    C_skin, _ = chroma_hue(skin_lab)

    mix = float(np.dot(m[1:3] - skin_lab[1:3], axis) / (axis_len ** 2))
    # A bleed sits ON the skin→lip line; a blush sits off it. Perpendicular distance, in
    # units of the axis length, keeps unrelated warm patches from triggering this.
    perp = float(np.linalg.norm((m[1:3] - skin_lab[1:3]) - mix * axis) / axis_len)
    d_h_lip = hue_diff(h_m, h_lip)
    suspicious = mix > th["bleed_mix_fraction"] and perp < th["bleed_max_perp"]
    return {"status": "possible_bleed" if suspicious else "clean_edge",
            "surround_median": {"lab": [round(float(v), 1) for v in m], "chroma": round(C_m, 1),
                                "hue_deg": round(h_m, 1)},
            "mix_fraction_skin_to_lip": round(mix, 3),
            "perp_distance": round(perp, 3),
            "chroma_gain_vs_skin": round(C_m - C_skin, 1),
            "hue_vs_lip_deg": round(d_h_lip, 1),
            "thresholds": {"mix_fraction": th["bleed_mix_fraction"], "hue_deg": th["bleed_hue_deg"]},
            "hard_fail": False,
            "note": "a smile line, blush or shadow can produce the same signal — human confirms"}


def check_skin_texture(img: np.ndarray, ref: np.ndarray | None, roi, th: dict):
    """High-frequency energy in a skin ROI, relative to the same ROI of the reference photo."""
    if ref is None:
        return {"status": "skipped", "reason": "no reference_image: over-smoothing can only be judged visually"}
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi_box(roi, w, h)
    crop = img[y0:y1, x0:x1]
    gray = (0.299 * crop[..., 0] + 0.587 * crop[..., 1] + 0.114 * crop[..., 2]).astype(np.float64)
    v_img = laplacian_var(gray)

    rh, rw = ref.shape[:2]
    rx0, ry0, rx1, ry1 = roi_box(roi, rw, rh)
    rcrop = ref[ry0:ry1, rx0:rx1]
    from PIL import Image as _Image
    if rcrop.shape[:2] != crop.shape[:2]:
        rcrop = np.asarray(_Image.fromarray(rcrop.astype(np.uint8)).resize(
            (crop.shape[1], crop.shape[0]), _Image.LANCZOS)).astype(np.float32)
    rgray = (0.299 * rcrop[..., 0] + 0.587 * rcrop[..., 1] + 0.114 * rcrop[..., 2]).astype(np.float64)
    v_ref = laplacian_var(rgray)
    if v_ref <= 1e-6:
        return {"status": "inconclusive_flat_reference",
                "reason": "reference ROI has no texture energy (blurred or flat patch)"}
    ratio = v_img / v_ref
    status = "retained"
    if ratio < th["texture_min_ratio"]:
        status = "over_smoothed"
    elif ratio > th["texture_max_ratio"]:
        status = "texture_added"
    return {"status": status, "roi": [x0, y0, x1 - x0, y1 - y0],
            "laplacian_var_image": round(v_img, 1), "laplacian_var_reference": round(v_ref, 1),
            "ratio": round(ratio, 3),
            "reference_note": ("same ROI box applied to the reference; resized to the ROI size. "
                               "Scale/crop differences bias this ratio — treat 0.45–1.8 as a wide band, "
                               "not an exact budget")}


# ---------------------------------------------------------------------------- CLI
def build_report(cfg: dict, img: np.ndarray, ref: np.ndarray | None) -> dict:
    th = {**DEFAULTS, **(cfg.get("thresholds") or {})}
    checks: dict = {}

    skin_lab = None
    skin_roi = cfg.get("skin_baseline")
    if skin_roi:
        if len(skin_roi) == 3:  # [x, y, r] disc, same convention as fidelity_check samples
            x, y, r = [int(v) for v in skin_roi]
            h, w = img.shape[:2]
            ys, xs = np.mgrid[max(0, y - r):min(h, y + r), max(0, x - r):min(w, x + r)]
            disc = (xs - x) ** 2 + (ys - y) ** 2 <= r * r
            px = img[ys[disc], xs[disc]]
            if px.size:
                skin_lab = median_lab(px, th["specular_keep_pct"])
        else:
            x0, y0, x1, y1 = roi_box(skin_roi, img.shape[1], img.shape[0])
            skin_lab = median_lab(img[y0:y1, x0:x1].reshape(-1, 3), th["specular_keep_pct"])

    eyes_cfg = cfg.get("eyes") or {}
    lash_edge = cfg.get("lash_edge", "bottom")
    for side in ("left", "right"):
        spec = eyes_cfg.get(side)
        if not spec:
            continue
        if not spec.get("expected_hex"):
            die("missing_expected_hex", side=side)
        checks[f"eyeshadow_{side}"] = check_eyeshadow(
            img, spec["roi"], spec["expected_hex"], skin_lab, th)
        if spec.get("lid"):
            checks[f"eyelid_{side}"] = check_eyelid(
                img, spec["lid"], spec["expected_hex"], skin_lab, th, lash_edge)
        if spec.get("under"):
            checks[f"under_lid_{side}"] = check_under_lid(
                img, spec["under"], skin_lab, spec["expected_hex"], th)

    if "eyeshadow_left" in checks and "eyeshadow_right" in checks:
        checks["left_right_balance"] = check_left_right(
            checks["eyeshadow_left"], checks["eyeshadow_right"], th)
    if "eyelid_left" in checks and "eyelid_right" in checks:
        checks["eyelid_balance"] = check_eyelid_balance(
            checks["eyelid_left"], checks["eyelid_right"], th)

    lips_cfg = cfg.get("lips")
    if lips_cfg:
        checks["lips"] = check_lips(img, lips_cfg["roi"], lips_cfg["expected_hex"], th)
        if lips_cfg.get("surround"):
            checks["lip_boundary"] = check_lip_bleed(
                img, lips_cfg["surround"], skin_lab, checks["lips"], th)

    tex_cfg = cfg.get("skin_texture")
    if tex_cfg:
        checks["skin_texture"] = check_skin_texture(img, ref, tex_cfg["roi"], th)

    # ---- verdict: only eyeshadow divergence on both eyes, L/R asymmetry and smoothing hard-fail
    failures: list[str] = []
    observations: list[str] = []

    diverged = [k for k in ("eyeshadow_left", "eyeshadow_right")
                if checks.get(k, {}).get("status") == "divergent"]
    if len(diverged) == 2:
        failures.append(
            f"eyeshadow hue diverges on both eyes ({', '.join(diverged)}) — systematic, not ROI noise")
    elif len(diverged) == 1:
        observations.append(
            f"{diverged[0]} diverges but the other eye does not — check ROI placement before judging")

    lr = checks.get("left_right_balance", {})
    if lr.get("status") == "asymmetric":
        failures.append(f"left/right eye imbalance: ΔE {lr['delta_e']}, Δhue {lr['delta_hue_deg']}°")

    # Eyelid balance is the stronger signal: it compares coverage and climb height too,
    # so a shared median colour no longer hides "one lid painted twice as wide".
    lrb = checks.get("eyelid_balance", {})
    if lrb.get("status") == "asymmetric":
        failures.append(
            "eyelid imbalance on " + ", ".join(lrb.get("signals_exceeded", []))
            + f" (coverage {lrb['coverage']['left']:.2f} vs {lrb['coverage']['right']:.2f}, "
              f"climb {lrb['climb_fraction']['left']:.2f} vs {lrb['climb_fraction']['right']:.2f})")
    elif lrb.get("status") == "minor_difference":
        observations.append(
            "eyelid_balance: one signal over threshold only ("
            + ", ".join(lrb.get("signals_exceeded", [])) + ") — confirm with vision before failing")

    for side in ("left", "right"):
        lid = checks.get(f"eyelid_{side}", {})
        if lid.get("status") in ("full_lid", "reaches_upper_lid"):
            failures.append(
                f"eyelid_{side}: {lid['status']} — climb {lid.get('climb_fraction')}, "
                f"near-brow half {lid.get('share_near_brow_half')}")
        elif lid.get("status") == "no_pigment":
            observations.append(f"eyelid_{side}: no_pigment — {lid.get('reason')}")

        under = checks.get(f"under_lid_{side}", {})
        if under.get("hard_fail"):
            failures.append(
                f"under_lid_{side}: pigment below the lashes "
                f"({under.get('pigment_share'):.1%}, same hue family as the shadow)")
        elif under.get("status") in ("specular_below_lid", "pigment_below_lid",
                                     "pigment_and_specular", "offskin_pigment_other_hue"):
            observations.append(f"under_lid_{side}: {under['status']} — {under.get('reason')}")

    tex = checks.get("skin_texture", {})
    if tex.get("status") == "over_smoothed":
        failures.append(f"skin over-smoothed: texture energy {tex['ratio']:.2f}× the reference")

    for key, entry in checks.items():
        st = entry.get("status")
        if st in ("inconclusive_no_skin_baseline", "inconclusive_no_makeup_detected",
                  "inconclusive_neutral_design", "bad_sample", "skipped"):
            observations.append(f"{key}: {st} — {entry.get('reason', '')}")
        if st == "possible_bleed":
            observations.append(f"{key}: possible colour bleed outside the lip ROI — human confirms")
        if st in ("consistent_hue_desaturated", "consistent_hue_oversaturated", "divergent_soft",
                  "texture_added"):
            observations.append(f"{key}: {st} — {entry.get('reason', '')}")

    if failures:
        verdict = "fail"
    elif observations:
        verdict = "review"
    else:
        verdict = "pass_code_checks"

    return {
        "ok": True,
        "image": cfg.get("image"),
        "reference_image": cfg.get("reference_image"),
        "roi_provenance": cfg.get("roi_provenance"),
        "landmark_capability": probe_landmark_tools(),
        "verdict": verdict,
        "machine_failures": failures,
        "observations": observations,
        "checks": checks,
        "code_checks": [c for c in CHECKLIST if any(x.startswith("code") for x in c[2])],
        "checklist": [{"item": n, "name": name, "by": by, "note": note} for n, name, by, note in CHECKLIST],
        "not_code_testable": NOT_CODE_TESTABLE,
        "honesty_note": (
            "pass_code_checks = 你声明的 ROI 内没有可测的色相/平衡/质感漂移。"
            "它不代表眼线位置对了、没有多余五官、或还是同一个人。"
            "第 3/4/5/6/7/9/10 项必须由视觉模型或人来看，本脚本不会替它们下结论。"),
    }


def draw_overlay(path: str, img: np.ndarray, cfg: dict) -> None:
    from PIL import Image, ImageDraw
    im = Image.fromarray(img.astype(np.uint8)).convert("RGB")
    d = ImageDraw.Draw(im)
    h, w = img.shape[:2]
    boxes = []
    if cfg.get("eyes"):
        for side in ("left", "right"):
            if cfg["eyes"].get(side):
                boxes.append((f"eye_{side}", cfg["eyes"][side]["roi"], (255, 40, 40)))
                if cfg["eyes"][side].get("lid"):
                    boxes.append((f"lid_{side}", cfg["eyes"][side]["lid"], (255, 140, 0)))
                if cfg["eyes"][side].get("under"):
                    boxes.append((f"under_{side}", cfg["eyes"][side]["under"], (200, 0, 200)))
    if cfg.get("lips"):
        boxes.append(("lips", cfg["lips"]["roi"], (40, 160, 255)))
        if cfg["lips"].get("surround"):
            boxes.append(("lip_surround", cfg["lips"]["surround"], (40, 160, 255)))
    if cfg.get("skin_texture"):
        boxes.append(("skin", cfg["skin_texture"]["roi"], (30, 200, 120)))
    for name, roi, col in boxes:
        x0, y0, x1, y1 = roi_box(roi, w, h)
        d.rectangle([x0, y0, x1 - 1, y1 - 1], outline=col, width=2)
        d.text((x0 + 3, max(0, y0 - 11)), name, fill=col)
    if cfg.get("skin_baseline") and len(cfg["skin_baseline"]) == 3:
        x, y, r = [int(v) for v in cfg["skin_baseline"]]
        d.ellipse([x - r, y - r, x + r, y + r], outline=(240, 200, 0), width=2)
        d.text((x + r + 2, y - 5), "skin", fill=(240, 200, 0))
    im.save(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Makeup render gate (ROI arithmetic only)")
    ap.add_argument("--image", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--overlay", default=None)
    ap.add_argument("--strict", action="store_true", help="exit 7 when a machine-checkable check fails")
    ap.add_argument("--list-checks", action="store_true")
    ap.add_argument("--capabilities", action="store_true",
                    help="report whether any face-landmark library is available")
    a = ap.parse_args()

    if a.capabilities:
        print(json.dumps({"ok": True, **probe_landmark_tools()}, ensure_ascii=False, indent=2))
        return 0

    if a.list_checks:
        print(json.dumps({"ok": True, "checks": [
            {"id": "eyeshadow_hue", "by": "code",
             "measures": "ROI 内高于肤色彩度的像素，其色相与饱和 vs 设计色",
             "needs": "眼影 ROI + skin_baseline ROI + 设计色 HEX"},
            {"id": "eyelid_coverage", "by": "code",
             "measures": "上睑带内逐行'有妆'占比：覆盖率、自睫毛根爬升高度（占带高比例）、靠眉半区是否干净",
             "needs": "上睑带 ROI（含眉侧到睫根）+ lash_edge"},
            {"id": "eyelid_balance", "by": "code",
             "measures": "左右两睑在覆盖率、爬升高度、颜色三项上的差；需≥2 项同时超阈值才判不对称",
             "needs": "两侧上睑带 ROI"},
            {"id": "under_lid", "by": "code",
             "measures": "下睫毛下方是否出现肤色之外的色素、或高于带内中位 L 15 的镜面亮带",
             "needs": "下睑带 ROI"},
            {"id": "left_right_balance", "by": "code",
             "measures": "左右眼 ROI 的 ΔE / Δhue（只看颜色，不看面积，不要单独依赖它）",
             "needs": "两个眼影 ROI"},
            {"id": "lip_color", "by": "code(soft)",
             "measures": "唇 ROI 中值色的色相与饱和 vs 设计唇色（不硬判）", "needs": "唇部 ROI"},
            {"id": "lip_boundary_bleed", "by": "code(soft)",
             "measures": "唇缘外侧 ROI 相对肤色是否出现同色相、高彩度渗出", "needs": "唇缘外侧 ROI"},
            {"id": "skin_texture_retention", "by": "code",
             "measures": "皮肤 ROI 的 Laplacian 能量 vs 参考照片同一 ROI",
             "needs": "参考人物照片 + 皮肤 ROI"},
        ] + [{"id": f"item_{i}", "by": "+".join(by), "measures": item, "needs": "视觉模型或人"}
             for i, (n, item, by, _) in enumerate(CHECKLIST, 1)
             if not any(x.startswith("code") for x in by)] + NOT_CODE_TESTABLE},
            ensure_ascii=False, indent=2))
        return 0

    if not a.config:
        die("missing --config")
    if not os.path.isfile(a.config):
        die("config_not_found", path=a.config)
    with open(a.config, "r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    img_path = a.image or cfg.get("image")
    if not img_path:
        die("missing_image", hint="pass --image or set 'image' in the config")
    if not os.path.isabs(img_path):
        img_path = os.path.join(os.path.dirname(os.path.abspath(a.config)), img_path)
        if not os.path.isfile(img_path):
            img_path = cfg.get("image")
    if not os.path.isfile(img_path):
        die("image_not_found", path=img_path)

    img = load_rgb(img_path)
    ref = None
    ref_path = cfg.get("reference_image")
    if ref_path and not os.path.isabs(ref_path):
        cand = os.path.join(os.path.dirname(os.path.abspath(a.config)), ref_path)
        ref_path = cand if os.path.isfile(cand) else ref_path
    if ref_path and os.path.isfile(ref_path):
        ref = load_rgb(ref_path)

    report = build_report(cfg, img, ref)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)) or ".", exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if a.overlay:
        os.makedirs(os.path.dirname(os.path.abspath(a.overlay)) or ".", exist_ok=True)
        draw_overlay(a.overlay, img, cfg)
    if a.strict and report["verdict"] == "fail":
        return 7
    return 0


if __name__ == "__main__":
    sys.exit(main())
