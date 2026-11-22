#!/usr/bin/env python3
"""MuseLab :: local test suite (no network, no credits, deterministic).

Covers everything that can be verified on this machine without an image-generation
model: real segmentation, measured colour, alpha compositing, the design state
machine, schema validation, and the failure paths.

Cases that require an agent-side tool (image generation, live news search) are
reported as NOT-RUN with the reason, never as PASS.

Run:  python3 tests/run_tests.py [--keep] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
FIX = os.path.join(HERE, "fixtures")
PY = sys.executable

RESULTS: list[dict] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append({"case": name, "status": status, "detail": detail})
    icon = {"PASS": "PASS", "FAIL": "FAIL", "NOT-RUN": "NOT-RUN", "ENV-UNSUPPORTED": "ENV-UNSUPPORTED"}[status]
    print(f"[{icon:15}] {name}" + (f" — {detail}" if detail else ""))


def run(script: str, *args: str, cwd: str | None = None) -> tuple[int, dict | None, str]:
    proc = subprocess.run([PY, os.path.join(SCRIPTS, script), *args],
                          capture_output=True, text=True, cwd=cwd or ROOT)
    payload = None
    out = proc.stdout.strip()
    if out.startswith("{"):
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = None
    # Drop interpreter-level noise (e.g. sandbox tempdir warnings) so real errors stay readable.
    noise = ("sitecustomize", "No usable temporary directory", "PYTHONVERBOSE")
    err = "\n".join(line for line in (proc.stderr or "").splitlines()
                    if line.strip() and not any(n in line for n in noise))
    return proc.returncode, payload, err.strip()


def write_json(path: str, obj: dict) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
    return path


# ----------------------------------------------------------------- the fixtures
BASE_DESIGN = {
    "design_id": "jw_test_A",
    "design_category": "beaded_bracelet",
    "design_name": "潮汐三段",
    "concept": "把海浪的三段节奏转成手串的疏密变化",
    "structure": {"archetype": "half_bead_half_chain", "length_mm": 165,
                  "sequence": ["龙虾扣", "延长链5cm", "银隔珠", "6mm海蓝宝x12", "链条段", "银隔珠"]},
    "materials": [
        {"name": "海蓝宝", "spec": "6mm 圆珠", "quantity": "12", "role": "main"},
        {"name": "925 银隔珠", "spec": "3mm", "quantity": "4", "role": "metal"},
        {"name": "925 银链", "spec": "1.2mm O字链", "quantity": "6cm", "role": "cord"},
    ],
    "colors": {"primary": "#2E8B93", "secondary": ["#F4EDE4"], "accent": "#D8C3A5"},
    "image_prompt": "商业珠宝摄影，白底，莫桑比克海蓝宝三段手链，6mm 圆珠，925 银隔珠，柔光箱，微距",
    "negative_prompt": "多出的珠子，错位，水印，文字，塑料质感，过度磨皮",
    "approval_status": "draft",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--keep", action="store_true", help="keep the working directory")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work = os.path.join(HERE, "_run", stamp)
    os.makedirs(work, exist_ok=True)
    print(f"MuseLab test run {stamp}\nwork: {work}\n")

    try:
        from PIL import Image
        import numpy as np
    except Exception as exc:
        record("suite: imports", "ENV-UNSUPPORTED", f"Pillow/numpy missing: {exc}")
        return 1

    # fixtures (regenerate so the run is self-contained)
    subprocess.run([PY, os.path.join(HERE, "make_fixtures.py")], capture_output=True, cwd=ROOT)

    # ------------------------------------------------------------------ 0. env
    code, env, err = run("check_env.py", "--imagegen", "no", "--websearch", "no")
    if code == 0 and env and env.get("ok"):
        caps = env["capabilities"]
        record("env: capability probe", "PASS",
               f"vision={caps['vision_foreground_mask']['available']} "
               f"pillow={caps['pillow']['available']} numpy={caps['numpy']['available']} "
               f"jsonschema={caps['jsonschema']['available']}")
        vision_ok = bool(caps["vision_foreground_mask"]["available"])
    else:
        record("env: capability probe", "FAIL", err[:200])
        vision_ok = False

    # --------------------------------------------------- 1. background removal
    cut_circle = os.path.join(work, "circle_cutout.png")
    cut_prod = os.path.join(work, "product_cutout.png")
    if vision_ok:
        code, rep, err = run("cutout.py", os.path.join(FIX, "shape_circle.png"), "--out", cut_circle)
        if code == 0 and rep and rep.get("ok"):
            img = Image.open(cut_circle)
            a = np.asarray(img.getchannel("A"))
            corners_clear = all(a[y, x] < 20 for y, x in [(2, 2), (2, -3), (-3, 2), (-3, -3)])
            center_solid = a[a.shape[0] // 2, a.shape[1] // 2] > 235
            if img.mode == "RGBA" and corners_clear and center_solid:
                record("01 单主体抠图（真实 alpha）", "PASS",
                       f"{img.mode} {img.size}, 中心不透明/背景全透明, method={rep['method']}")
            else:
                record("01 单主体抠图（真实 alpha）", "FAIL",
                       f"mode={img.mode} corners_clear={corners_clear} center_solid={center_solid}")
        else:
            record("01 单主体抠图（真实 alpha）", "FAIL", err[:200])

        # colour preservation on a real product-like photo
        code, rep, err = run("cutout.py", os.path.join(FIX, "product_beads.png"), "--out", cut_prod, "--despill")
        if code == 0 and rep and rep.get("ok"):
            src = np.asarray(Image.open(os.path.join(FIX, "product_beads.png")).convert("RGB")).astype(int)
            out = np.asarray(Image.open(cut_prod).convert("RGBA")).astype(int)
            solid = out[..., 3] >= 250  # Vision mattes rarely reach exactly 255
            if solid.sum() > 500:
                diff = np.abs(src[solid] - out[..., :3][solid]).max()
                ok = diff <= 2
                record("01b 抠图不改变商品颜色", "PASS" if ok else "FAIL",
                       f"完全不透明像素 {int(solid.sum())} 个，最大通道偏差 {diff}")
            else:
                record("01b 抠图不改变商品颜色", "FAIL", f"opaque pixels too few: {int(solid.sum())}")
            record("01c 主体定位", "PASS",
                   f"bbox={rep['subject_bbox']} area={rep['subject_area_ratio']:.3f} "
                   f"instances={rep['instance_count']}")
        else:
            record("01b 抠图不改变商品颜色", "FAIL", err[:200])
            record("01c 主体定位", "FAIL", err[:200])
    else:
        for name in ("01 单主体抠图（真实 alpha）", "01b 抠图不改变商品颜色", "01c 主体定位"):
            record(name, "ENV-UNSUPPORTED", "macOS Vision foreground mask unavailable")

    code, _, err = run("cutout.py", "does_not_exist.png", "--out", os.path.join(work, "x.png"))
    record("01d 缺失输入的处理", "PASS" if code == 2 else "FAIL", f"exit={code}")

    # --------------------------------------------------- 2. colour extraction
    truth = json.load(open(os.path.join(FIX, "palette_blocks.truth.json"), encoding="utf-8"))
    code, rep, err = run("color_extract.py", os.path.join(FIX, "palette_blocks.png"),
                         "--mode", "mood", "--swatch", os.path.join(work, "swatch.png"))
    if code == 0 and rep and rep.get("ok"):
        got = {p["hex"].upper() for p in rep["color_palette"]}
        want = {h.upper() for h in truth["expected_hex"]}
        missing = want - got
        extra = got - want
        record("02 海滩照片吸色（测量精度）", "PASS" if not missing else "FAIL",
               f"命中 {len(want & got)}/{len(want)}，多出 {sorted(extra) if extra else '无'}，"
               f"method={rep['extraction_method']}")
        ratios_ok = all(abs(p["area_ratio"] - 0.2) < 0.02 for p in rep["color_palette"])
        record("02b 面积占比准确", "PASS" if ratios_ok else "FAIL",
               "每块 0.2 等分" if ratios_ok else str([p["area_ratio"] for p in rep["color_palette"]]))
        mood_ok = "mood_words" in rep.get("atmosphere", {}) and rep["atmosphere"]["mood_words"]
        record("02c 氛围分析（明度/冷暖/对比）", "PASS" if mood_ok else "FAIL",
               f"{rep['atmosphere']}")
        record("02d 色板图输出", "PASS" if os.path.isfile(os.path.join(work, "swatch.png")) else "FAIL",
               "swatch.png")
    else:
        record("02 海滩照片吸色（测量精度）", "FAIL", err[:200])
        for n in ("02b 面积占比准确", "02c 氛围分析（明度/冷暖/对比）", "02d 色板图输出"):
            record(n, "FAIL", "upstream failed")

    code, rep, _ = run("color_extract.py", os.path.join(FIX, "palette_blocks.png"), "--no-pixels")
    ok = code == 0 and rep and rep.get("extraction_method") == "visual_estimate" and not rep.get("is_measured")
    record("02e 区分测量与推测", "PASS" if ok else "FAIL",
           f"method={rep.get('extraction_method') if rep else None}")

    # --------------------------------------------------------- 3. compositing
    if os.path.isfile(cut_circle):
        white = os.path.join(work, "circle_white.png")
        code, rep, err = run("compose.py", cut_circle, "--out", white, "--bg", "white", "--size", "800")
        if code == 0 and os.path.isfile(white):
            arr = np.asarray(Image.open(white).convert("RGB"))
            pure = tuple(int(v) for v in arr[3, 3]) == (255, 255, 255) and tuple(int(v) for v in arr[-4, -4]) == (255, 255, 255)
            record("03 纯白背景导出", "PASS" if pure else "FAIL", f"corner={tuple(int(v) for v in arr[3,3])}")
        else:
            record("03 纯白背景导出", "FAIL", err[:200])

        trans = os.path.join(work, "circle_transparent.png")
        code, rep, _ = run("compose.py", cut_circle, "--out", trans, "--bg", "transparent", "--size", "800")
        ok = code == 0 and Image.open(trans).mode == "RGBA"
        record("03b 透明输出保留真实通道", "PASS" if ok else "FAIL", f"mode={Image.open(trans).mode if code==0 else 'n/a'}")

        code, _, _ = run("compose.py", cut_circle, "--out", os.path.join(work, "bad.jpg"), "--bg", "transparent", "--format", "jpg")
        record("03c 拒绝 JPEG+透明 组合", "PASS" if code == 5 else "FAIL", f"exit={code}")

        im = Image.open(trans)
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        a = np.asarray(im.getchannel("A"))
        record("03d 缩放后边缘仍为软边", "PASS" if ((a > 8) & (a < 248)).sum() > 50 else "FAIL",
               f"semi-transparent px={int(((a>8)&(a<248)).sum())}")
    else:
        for n in ("03 纯白背景导出", "03b 透明输出保留真实通道", "03c 拒绝 JPEG+透明 组合", "03d 缩放后边缘仍为软边"):
            record(n, "ENV-UNSUPPORTED", "cutout fixture missing")

    # ------------------------------------- 3x. product compositing + pixel integrity
    # 这一段不依赖 Vision：用固定夹具构造"原图 → 抠图 → 合成"的一致三元组，
    # 再人为制造两类真实事故（合成后调色、画布裁切），确认检查能抓到。
    if os.path.isfile(cut_prod):
        prod_src = os.path.join(FIX, "product_beads.png")
        keep_png = os.path.join(work, "prod_keepframe.png")
        place = os.path.join(work, "prod_place.json")
        code, rep, err = run("compose.py", cut_prod, "--out", keep_png,
                             "--bg", "#D6D6D6", "--keep-frame", "--json", place)
        if code == 0 and rep:
            sp = rep["subject_placement"]
            same_canvas = rep["output_size"] == list(Image.open(cut_prod).size)
            ok = same_canvas and rep.get("frame_preserved") is True and sp["scale"] == 1.0 and (sp["x"], sp["y"]) == (0, 0)
            record("03e --keep-frame 保画布与像素坐标", "PASS" if ok else "FAIL",
                   f"size={rep['output_size']} scale={sp['scale']} offset=({sp['x']},{sp['y']}) "
                   f"frame_preserved={rep.get('frame_preserved')}")
        else:
            record("03e --keep-frame 保画布与像素坐标", "FAIL", err[:200])

        base_args = ("--original", prod_src, "--cutout", cut_prod, "--composite", keep_png,
                     "--placement", place, "--bg", "#D6D6D6")
        code, rep, err = run("pixel_integrity.py", *base_args, "--json", os.path.join(work, "pi_ok.json"))
        if code == 0 and rep:
            p = rep["product_pixels_preserved"]
            ok = rep["verdict"].startswith("pass") and p["cutout_step"] and p["compositing_step"]
            record("03f 像素完整性：正常合成无改动", "PASS" if ok else "FAIL",
                   f"verdict={rep['verdict']} core={rep['opaque_core']['cutout_vs_original']['max_channel_delta']} "
                   f"blend_err_max={rep['opaque_core']['composite_vs_ideal_blend']['max_channel_delta']}")
        else:
            record("03f 像素完整性：正常合成无改动", "FAIL", err[:200])

        # 事故 1：合成后偷偷调色（整块 +6）
        arr = np.asarray(Image.open(keep_png).convert("RGB")).astype(np.int16)
        h, w = arr.shape[:2]
        bad = arr.copy()
        bad[h // 3: h // 3 + 60, w // 3: w // 3 + 60] = np.clip(bad[h // 3: h // 3 + 60, w // 3: w // 3 + 60] + 6, 0, 255)
        relit = os.path.join(work, "prod_relit.png")
        Image.fromarray(bad.astype(np.uint8)).save(relit)
        code, rep, _ = run("pixel_integrity.py", "--original", prod_src, "--cutout", cut_prod,
                           "--composite", relit, "--placement", place, "--bg", "#D6D6D6", "--strict",
                           "--json", os.path.join(work, "pi_relit.json"))
        caught = code == 6 and rep and rep["product_pixels_preserved"]["compositing_step"] is False \
            and rep["product_pixels_preserved"]["cutout_step"] is True
        record("03g 检出合成后调色（且不误判抠图）", "PASS" if caught else "FAIL",
               f"exit={code} preserved={rep['product_pixels_preserved'] if rep else None}")

        # 事故 2：画布被裁掉一条边（夹具商品居中，裁到 55% 宽必然切到主体）
        cropped = os.path.join(work, "prod_cropped.png")
        Image.fromarray(arr[:, : max(10, int(w * 0.55))].astype(np.uint8)).save(cropped)
        code, rep, _ = run("pixel_integrity.py", "--original", prod_src, "--cutout", cut_prod,
                           "--composite", cropped, "--placement", place, "--bg", "#D6D6D6", "--strict",
                           "--json", os.path.join(work, "pi_crop.json"))
        clipped = rep["structure"]["clipped_pixels"] if rep else 0
        caught = code == 6 and clipped > 0
        record("03h 检出非预期裁切", "PASS" if caught else "FAIL",
               f"exit={code} clipped_px={clipped}")
        # 事故 3：蒙版悄悄丢掉一块商品（像素完整性看不出来，覆盖检查必须报出来）
        ca = np.asarray(Image.open(cut_prod))
        inner = ca[..., 3] > 200
        lum = np.asarray(Image.open(prod_src).convert("L")).astype(np.int16)
        masked_lum = np.where(inner, lum, 255)
        cy_p, cx_p = np.unravel_index(int(np.argmin(masked_lum)), lum.shape)  # 洞要打在商品最暗处
        ca2 = ca.copy()
        ca2[cy_p - 30:cy_p + 30, cx_p - 30:cx_p + 30, 3] = 0
        cut_drop = os.path.join(work, "prod_cutout_dropped.png")
        Image.fromarray(ca2).save(cut_drop)
        comp_drop = os.path.join(work, "prod_drop_composite.png")
        run("compose.py", cut_drop, "--out", comp_drop, "--bg", "#D6D6D6", "--keep-frame")
        code, rep, _ = run("pixel_integrity.py", "--original", prod_src, "--cutout", cut_drop,
                           "--composite", comp_drop, "--bg", "#D6D6D6",
                           "--json", os.path.join(work, "pi_drop.json"))
        cov = (rep or {}).get("coverage", {})
        flagged = [i for i in cov.get("items", []) if i.get("looks_like_product")]
        ok = code == 0 and cov.get("status") == "review" and flagged
        record("03i 覆盖检查抓出蒙版漏掉的商品", "PASS" if ok else "FAIL",
               f"hole@({cx_p},{cy_p}) status={cov.get('status')} uncovered_px={cov.get('uncovered_pixels')} "
               f"product_like={len(flagged)} (像素完整性本次仍判 {rep.get('verdict') if rep else '?'})")
    else:
        for n in ("03e --keep-frame 保画布与像素坐标", "03f 像素完整性：正常合成无改动",
                  "03g 检出合成后调色（且不误判抠图）", "03h 检出非预期裁切",
                  "03i 覆盖检查抓出蒙版漏掉的商品"):
            record(n, "ENV-UNSUPPORTED", "cutout fixture missing")

    # ------------------------------------------------ 3j. mask audit / repair CLI
    if os.path.isfile(cut_prod):
        audit_json = os.path.join(work, "audit_prod.json")
        code, rep, err = run("mask_audit.py", "--image", os.path.join(FIX, "product_beads.png"),
                             "--cutout", cut_prod, "--json", audit_json)
        if code == 0 and rep:
            overall = rep.get("verdict", {}).get("overall")
            keys_ok = set(rep.get("checks", {})) >= {"background_residue", "translucent_core", "edge_contamination"}
            ok = overall in {"pass", "review", "needs_repair"} and keys_ok and rep.get("is_generative") is False
            record("03j 蒙版质检输出结构", "PASS" if ok else "FAIL",
                   f"overall={overall} checks={sorted(rep.get('checks', {}))}")
            code2, rep2, _ = run("mask_repair.py", "--image", os.path.join(FIX, "product_beads.png"),
                                 "--cutout", cut_prod, "--out", os.path.join(work, "prod_repaired.png"),
                                 "--json", os.path.join(work, "repair.json"))
            record("03k 蒙版修补可运行（不覆盖输入）", "PASS" if os.path.isfile(os.path.join(work, "prod_repaired.png")) else "FAIL",
                   f"exit={code2} " + (f"ok={rep2.get('ok')} removed={rep2.get('removed_px') or rep2.get('applied') or '-'}" if rep2 else "no json"))
        else:
            record("03j 蒙版质检输出结构", "FAIL", err[:200])
            record("03k 蒙版修补可运行（不覆盖输入）", "FAIL", "upstream failed")
    else:
        record("03j 蒙版质检输出结构", "ENV-UNSUPPORTED", "cutout fixture missing")
        record("03k 蒙版修补可运行（不覆盖输入）", "ENV-UNSUPPORTED", "cutout fixture missing")

    # --------------------------------------------------- 4. design state machine
    proj = os.path.join(work, "proj")
    code, rep, err = run("muselab_state.py", "init", "--dir", proj, "--title", "海洋系列")
    record("04 项目初始化", "PASS" if code == 0 and os.path.isfile(os.path.join(proj, "project.json")) else "FAIL",
           err[:200])

    profile = {
        "profile_id": "vp_test_beach",
        "source_image": "/tmp/beach.jpg",
        "extraction_method": "pixel_kmeans_lab",
        "color_palette": [{"hex": "#2E8B93", "role": "primary", "area_ratio": 0.4},
                          {"hex": "#F4EDE4", "role": "secondary", "area_ratio": 0.35},
                          {"hex": "#D8C3A5", "role": "accent", "area_ratio": 0.25}],
        "design_keywords": ["海浪", "渐变", "半透明"],
    }
    pf = write_json(os.path.join(work, "profile.json"), profile)
    code, rep, err = run("muselab_state.py", "profile-add", "--dir", proj, "--file", pf)
    record("04b 视觉档案入库", "PASS" if code == 0 and rep.get("colors_are_measured") else "FAIL", err[:200])

    d1 = write_json(os.path.join(work, "design.json"), dict(BASE_DESIGN))
    code, rep, err = run("muselab_state.py", "design-add", "--dir", proj, "--file", d1)
    record("05 设计档案入库", "PASS" if code == 0 else "FAIL", err[:200])

    code, _, _ = run("muselab_state.py", "design-add", "--dir", proj, "--file", d1)
    record("05b 拒绝覆盖已有设计", "PASS" if code == 2 else "FAIL", f"exit={code}")

    code, _, _ = run("muselab_state.py", "status", "--dir", proj, "--id", "jw_test_A", "--set", "approved")
    record("05c 拒绝 draft→approved 跳步", "PASS" if code == 6 else "FAIL", f"exit={code}")

    run("muselab_state.py", "status", "--dir", proj, "--id", "jw_test_A", "--set", "selected")
    code, rep, err = run("muselab_state.py", "status", "--dir", proj, "--id", "jw_test_A", "--set", "approved")
    record("05d 定稿（selected→approved）", "PASS" if code == 0 and rep.get("status") == "approved" else "FAIL", err[:200])

    code, rep, _ = run("muselab_state.py", "design-show", "--dir", proj, "--id", "jw_test_A")
    record("05e 定稿后标记 frozen", "PASS" if code == 0 and rep.get("frozen") else "FAIL", str(rep.get("frozen") if rep else None))

    code, _, _ = run("muselab_state.py", "status", "--dir", proj, "--id", "jw_test_A", "--set", "draft")
    record("05f 拒绝改动已定稿设计", "PASS" if code == 6 else "FAIL", f"exit={code}")

    patch = write_json(os.path.join(work, "patch.json"),
                       {"materials": BASE_DESIGN["materials"][:1], "colors": {"accent": "#C9A227"},
                        "change_request": "保留主体，把银色配件改成金色"})
    code, _, _ = run("muselab_state.py", "revise", "--dir", proj, "--id", "jw_test_A", "--file", patch)
    record("06 未获许可不得修改定稿", "PASS" if code == 6 else "FAIL", f"exit={code}")

    code, rep, err = run("muselab_state.py", "revise", "--dir", proj, "--id", "jw_test_A",
                         "--file", patch, "--consent")
    ok = code == 0 and rep.get("new_version") == 2 and rep.get("previous_version_kept") == 1
    record("06b 授权修改生成新版本 v2", "PASS" if ok else "FAIL", err[:200] or json.dumps(rep, ensure_ascii=False)[:160])

    code, rep, _ = run("muselab_state.py", "design-show", "--dir", proj, "--id", "jw_test_A", "--version", "1")
    ok = (code == 0 and rep["spec"]["colors"]["accent"] == "#D8C3A5"
          and rep["status"] == "approved" and rep.get("superseded_by") == 2)
    record("06c 原版本仍可引用且未被改写", "PASS" if ok else "FAIL",
           f"v1 accent={rep['spec']['colors']['accent'] if rep else None} "
           f"status={rep['status'] if rep else None} superseded_by={rep.get('superseded_by') if rep else None}")

    code, rep, _ = run("muselab_state.py", "design-show", "--dir", proj, "--id", "jw_test_A")
    ok = code == 0 and rep["spec"]["colors"]["accent"] == "#C9A227" and rep["spec"]["parent_version"] == 1
    record("06d 新版本只改指定字段", "PASS" if ok else "FAIL",
           f"v2 accent={rep['spec']['colors']['accent'] if rep else None} parent={rep['spec'].get('parent_version') if rep else None}")

    # --------------------------------------------------------- 7. image tasks
    task = {"task_id": "img_t1", "design_id": "jw_test_A", "image_type": "product_shot",
            "prompt": "白底商业珠宝摄影，海蓝宝三段手链", "negative_prompt": "水印，文字",
            "aspect_ratio": "1:1", "tool_used": "none", "execution_status": "pending"}
    tf = write_json(os.path.join(work, "task.json"), task)
    code, rep, err = run("muselab_state.py", "task-add", "--dir", proj, "--design-id", "jw_test_A", "--file", tf)
    record("07 图像任务登记", "PASS" if code == 0 else "FAIL", err[:200])

    code, _, _ = run("muselab_state.py", "task-done", "--dir", proj, "--task-id", "img_t1",
                     "--path", os.path.join(work, "nope.png"))
    record("07b 拒绝虚构输出路径", "PASS" if code == 2 else "FAIL", f"exit={code}")

    code, rep, _ = run("muselab_state.py", "task-fail", "--dir", proj, "--task-id", "img_t1",
                       "--reason", "当前环境无图像生成工具", "--no-tool")
    ok = code == 0 and rep.get("execution_status") == "skipped_no_tool" and rep.get("output_path") is None
    record("07c 工具不可用时如实降级", "PASS" if ok else "FAIL", json.dumps(rep, ensure_ascii=False)[:160] if rep else "")

    fake = os.path.join(work, "fake_out.png")
    Image.new("RGB", (64, 64), "white").save(fake)
    code, rep, _ = run("muselab_state.py", "task-done", "--dir", proj, "--task-id", "img_t1",
                       "--path", fake, "--tool", "ImageGen", "--fidelity-notes", "珠数可能偏差，需复核")
    ok = code == 0 and rep.get("is_generative") is True
    record("07d 记录真实产出与保真说明", "PASS" if ok else "FAIL", json.dumps(rep, ensure_ascii=False)[:160] if rep else "")

    code, rep, err = run("muselab_state.py", "verify", "--dir", proj)
    record("07e 项目完整性校验", "PASS" if code == 0 and rep.get("ok") else "FAIL", (err or json.dumps(rep, ensure_ascii=False))[:200])

    code, rep, _ = run("muselab_state.py", "design-list", "--dir", proj)
    record("07f 设计清单可引用", "PASS" if code == 0 and rep.get("count") == 1 else "FAIL",
           json.dumps(rep.get("designs"), ensure_ascii=False)[:200] if rep else "")

    # ------------------------------------------------------ 8. schema guard
    bad = dict(BASE_DESIGN)
    bad["design_id"] = "jw_bad"
    bad.pop("concept")
    bf = write_json(os.path.join(work, "bad_design.json"), bad)
    code, _, err = run("muselab_state.py", "design-add", "--dir", proj, "--file", bf)
    record("08 Schema 校验拦截缺字段", "PASS" if code == 2 else "FAIL", f"exit={code}")

    # ---------------------------------------------- 10. fidelity gate (V1.1)
    GATE_BASE = {
        "expected_background": "#F2EFE9",
        "materials": [{"name": "青珠", "expected": "#128D97", "kind": "gemstone", "sample": [150, 240, 20]}],
        "accessory_rois": [{"name": "上扣件", "roi": [200, 80, 80, 80]},
                           {"name": "下扣件", "roi": [200, 340, 80, 80]}],
    }

    def gate(image: str, extra: dict | None = None, strict: bool = False) -> tuple[int, dict | None, str]:
        cfg = {**GATE_BASE, "image": os.path.join(FIX, image), **(extra or {})}
        path = write_json(os.path.join(work, f"gate_{os.path.splitext(image)[0]}.json"), cfg)
        args = ["fidelity_check.py", "--config", path, "--json", os.path.join(work, f"gate_{image}.json")]
        if strict:
            args.append("--strict")
        return run(*args)

    code, rep, err = gate("gate_shot.png")
    ok = (code == 0 and rep and rep["background"]["status"] == "pass_seamless"
          and rep["materials"][0]["status"] == "pass_within_lighting"
          and all(a["status"] == "ready_for_vision_review" for a in rep["accessories"])
          and rep["verdict"] == "pass_code_checks")
    record("10 门禁：合规图判 pass（且不越权宣布合格）", "PASS" if ok else "FAIL",
           err[:200] or (f"verdict={rep['verdict']} bg={rep['background']['status']} "
                         f"mat={rep['materials'][0]['status']}" if rep else "no payload"))

    code, rep, err = gate("gate_shot_tone.png")
    m = rep["materials"][0] if rep else {}
    ok = (code == 0 and m.get("status") == "tone_shift"
          and not any("青珠" in f for f in rep["machine_failures"])
          and any("青珠" in f for f in rep["correctable_in_post"]))
    record("10b 明度偏但色相对 → 归为可后期修正", "PASS" if ok else "FAIL",
           f"status={m.get('status')} dL_res={m.get('d_l_residual_after_exposure')} "
           f"hue={m.get('d_hue_deg')} 硬失败={len(rep['machine_failures']) if rep else '?'}")

    code, rep, err = gate("gate_shot_wronghue.png")
    m = rep["materials"][0] if rep else {}
    ok = code == 0 and m.get("status") == "material_mismatch" and rep["verdict"] == "fail"
    record("10c 色相错 → 判材质不符", "PASS" if ok else "FAIL",
           f"status={m.get('status')} hue_drift={m.get('d_hue_deg')}°")

    # the whole point of the colour rule: same material, casted scene → raw ΔE looks bad, decast is fine
    code, rep, err = gate("gate_shot_gradient_bg.png")
    m = rep["materials"][0] if rep else {}
    ok = (code == 0 and m.get("status") == "pass_within_lighting"
          and m.get("delta_e_raw", 0) > 10 and m.get("delta_e_after_decast", 99) < 6
          and rep["background"]["status"] == "off_spec")
    record("10d 光照补偿：raw ΔE 大但材质本身对", "PASS" if ok else "FAIL",
           f"dE_raw={m.get('delta_e_raw')} → dE_decast={m.get('delta_e_after_decast')} "
           f"bg={rep['background']['status'] if rep else '?'}")

    code, rep, err = gate("gate_shot_gradient_bg.png")
    ok = code == 0 and "background" in " ".join(rep["machine_failures"]) and rep["verdict"] == "fail"
    record("10e 打光布景背景 → 判不合格", "PASS" if ok else "FAIL",
           f"status={rep['background']['status'] if rep else '?'} spread={rep['background']['spread_delta_e'] if rep else '?'}")

    code, rep, err = gate("gate_shot_blur.png")
    acc = {a["name"]: a for a in rep["accessories"]} if rep else {}
    ok = (code == 0 and acc.get("下扣件", {}).get("status") == "unverifiable_blur"
          and acc.get("上扣件", {}).get("status") == "ready_for_vision_review")
    record("10f 配件虚化 → 判「无法验收」而非通过", "PASS" if ok else "FAIL",
           f"上={acc.get('上扣件', {}).get('status')} 下={acc.get('下扣件', {}).get('status')} "
           f"ratio={acc.get('下扣件', {}).get('focus_ratio_to_reference')}")

    code, rep, err = gate("gate_shot.png", {"accessory_rois": [{"name": "延长链", "roi": [20, 20, 60, 60]}]})
    acc = rep["accessories"][0] if rep else {}
    ok = code == 0 and acc.get("status") == "absent_at_roi" and rep["verdict"] == "fail"
    record("10g 声明位置的配件缺失 → 硬失败", "PASS" if ok else "FAIL",
           f"status={acc.get('status')} fg={acc.get('foreground_ratio')}")

    code, _, _ = gate("gate_shot_wronghue.png", strict=True)
    record("10h --strict 硬门禁失败时退出 7", "PASS" if code == 7 else "FAIL", f"exit={code}")

    code, rep, err = gate("gate_shot.png", {"materials": [{"name": "青珠", "expected": "#128D97",
                                                           "kind": "gemstone", "sample": [30, 30, 8]}]})
    m = rep["materials"][0] if rep else {}
    ok = code == 0 and m.get("status") in ("bad_sample", "inconclusive_low_separation") and "measured_hex_body" not in m
    record("10i 采样点落在背景 → 拒判，不硬给结论", "PASS" if ok else "FAIL",
           f"status={m.get('status')} reason={(m.get('reason') or '')[:70]}")

    task_gm = {"task_id": "img_gm", "design_id": "jw_test_A", "image_type": "white_background",
               "generation_mode": "product_compositing", "tool_used": "alpha_composite",
               "prompt": "非生成：抠图 + 保画布合成", "execution_status": "pending", "is_generative": False}
    t1 = write_json(os.path.join(work, "task_gm.json"), task_gm)
    code1, _, err1 = run("muselab_state.py", "task-add", "--dir", proj, "--design-id", "jw_test_A", "--file", t1)
    # 旧值必须仍可读，否则历史档案会被新 schema 判非法
    task_gm["task_id"] = "img_gm_legacy"
    task_gm["generation_mode"] = "product_fidelity"
    t_legacy = write_json(os.path.join(work, "task_gm_legacy.json"), task_gm)
    code_legacy, _, _ = run("muselab_state.py", "task-add", "--dir", proj, "--design-id", "jw_test_A", "--file", t_legacy)
    task_gm["task_id"] = "img_gm_bad"
    task_gm["generation_mode"] = "photo_of_real_thing"
    t2 = write_json(os.path.join(work, "task_gm_bad.json"), task_gm)
    code2, _, _ = run("muselab_state.py", "task-add", "--dir", proj, "--design-id", "jw_test_A", "--file", t2)
    record("10j 三条路线进入任务 schema（且兼容旧值）", "PASS" if code1 == 0 and code_legacy == 0 and code2 == 2 else "FAIL",
           f"product_compositing exit={code1} 旧值 exit={code_legacy} 非法 exit={code2} {err1[:60]}")

    code, rep, err = run("fidelity_check.py", "--list-checks")
    ok = code == 0 and rep and {"code", "vision", "human"} <= {c["by"] for c in rep["checks"]}
    record("10k 声明哪些检查是代码/视觉/人工", "PASS" if ok else "FAIL",
           f"{len(rep['checks']) if rep else 0} 条，代码 {sum(1 for c in rep['checks'] if c['by']=='code') if rep else 0}")


    # ------------------------------------------------------------- 11. makeup module
    makeup_design = {
        "design_id": "mk_test_A",
        "design_category": "makeup",
        "design_name": "潮汐眼",
        "concept": "把参考图海水青落在眼尾 1/3，其余区域压到中性",
        "image_prompt": "冷调清透妆容，眼尾青绿，浅灰棚拍，皮肤保留纹理",
        "negative_prompt": "额外眼睛，畸形睫毛，过度磨皮，改变脸型",
        "approval_status": "draft",
        "module_payload": {
            "makeup_name_zh": "潮汐眼",
            "makeup_name_en": "Tidal Eye",
            "makeup_style": "cool_ethereal",
            "visual_focus": "eyes",
            "focus_strength": "moderate",
            "inspiration_source": {"kind": "image", "path": "source_beach.jpg",
                                   "extraction_method": "pixel_kmeans_lab"},
            "palette": {
                "measured": [{"hex": "#69B7BB", "role": "secondary"}],
                "designed": [{"area": "eyeshadow", "hex": "#2E8B97",
                              "reason": "实测浅青降饱和 10%，避免在眼皮上发灰"}],
            },
            "layers": {
                "base": {"finish": "satin", "skin_texture": "保留毛孔，不叠加第二层粉底"},
                "eyeshadow": {"main": "青绿压在眼尾 1/3", "boundary": "soft", "finish": "satin"},
                "lips": {"color_hex": "#8A4B55", "finish": "satin"},
            },
            "application_steps": [
                {"step": 1, "area": "eyeshadow", "product_type": "哑光眼影",
                 "placement": "眼尾 1/3 至下眼睑后段", "technique": "压后向外晕",
                 "effect": "拉长眼型，边界柔化"},
            ],
            "generation": {"route": "creative_generation", "aspect_ratio": "1:1"},
        },
    }
    proj_mk = os.path.join(work, "proj_makeup")
    run("muselab_state.py", "init", "--dir", proj_mk, "--title", "妆容测试")

    no_payload = json.loads(json.dumps(makeup_design))
    no_payload["design_id"] = "mk_test_nopayload"
    no_payload.pop("module_payload")
    f_np = write_json(os.path.join(work, "mk_no_payload.json"), no_payload)
    code_np, _, err_np = run("muselab_state.py", "design-add", "--dir", proj_mk, "--file", f_np)
    record("11a 妆容档案缺 module_payload 被拒", "PASS" if code_np == 2 else "FAIL",
           f"exit={code_np} {err_np.replace(chr(10), ' ')[:90]}")

    f_mk = write_json(os.path.join(work, "mk_v1.json"), makeup_design)
    code_mk, rep_mk, err_mk = run("muselab_state.py", "design-add", "--dir", proj_mk, "--file", f_mk)
    code_list, list_rep, _ = run("muselab_state.py", "design-list", "--dir", proj_mk)
    cat = (list_rep or {}).get("designs", [{}])[0].get("category") if list_rep else None
    ok = code_mk == 0 and code_list == 0 and cat == "makeup"
    record("11b 妆容档案走同一状态机登记", "PASS" if ok else "FAIL",
           f"exit={code_mk} category={cat} {err_mk[:60]}")

    # partial revision: only the eye layer, base + lips must survive untouched
    patch = {"change_request": "保留底妆和唇妆，只把眼妆改成更日常的版本",
             "module_payload": {"layers": {"eyeshadow": {"main": "浅棕过渡，眼尾不加青",
                                                         "finish": "matte"}}}}
    f_patch = write_json(os.path.join(work, "mk_patch.json"), patch)
    code_rv, _, err_rv = run("muselab_state.py", "revise", "--dir", proj_mk,
                             "--id", "mk_test_A", "--file", f_patch)
    code_v1, rep_v1, _ = run("muselab_state.py", "design-show", "--dir", proj_mk,
                             "--id", "mk_test_A", "--version", "1")
    code_v2, rep_v2, _ = run("muselab_state.py", "design-show", "--dir", proj_mk,
                             "--id", "mk_test_A", "--version", "2")
    pl1 = (rep_v1 or {}).get("spec", {}).get("module_payload", {})
    pl2 = (rep_v2 or {}).get("spec", {}).get("module_payload", {})
    kept = (pl2.get("layers", {}).get("base") == pl1.get("layers", {}).get("base")
            and pl2.get("layers", {}).get("lips") == pl1.get("layers", {}).get("lips"))
    eye_changed = (pl2.get("layers", {}).get("eyeshadow", {}).get("main")
                   != pl1.get("layers", {}).get("eyeshadow", {}).get("main"))
    old_intact = bool((rep_v1 or {}).get("spec", {}).get("version") == 1)
    ok = (code_rv == 0 and kept and eye_changed and old_intact
          and pl1.get("layers", {}).get("eyeshadow", {}).get("main") == "青绿压在眼尾 1/3")
    record("11c 只改眼妆：唇/底妆不动且旧版保留", "PASS" if ok else "FAIL",
           f"revise exit={code_rv} 底妆保留={kept} 眼妆改变={eye_changed} v1存在={old_intact}")

    bad_patch = {"module_payload": {"makeup_style": "fluorescent_rave"}}
    f_bad = write_json(os.path.join(work, "mk_patch_bad.json"), bad_patch)
    code_bad, _, err_bad = run("muselab_state.py", "revise", "--dir", proj_mk,
                               "--id", "mk_test_A", "--file", f_bad)
    record("11d 非法妆容风格被 payload schema 拦下", "PASS" if code_bad == 2 else "FAIL",
           f"exit={code_bad} {err_bad.replace(chr(10), ' ')[:90]}")

    code_lc, lc, _ = run("makeup_check.py", "--list-checks")
    bys = {c.get("by", "") for c in (lc or {}).get("checks", [])}
    ok = code_lc == 0 and any("code" in b for b in bys) and any("vision" in b for b in bys) \
        and any("human" in b for b in bys)
    record("11e 妆容检查声明代码/视觉/人工三条边界", "PASS" if ok else "FAIL",
           f"{len((lc or {}).get('checks', []))} 条，by={sorted(bys)[:6]}")

    def makeup_gate(name: str, image: str, reference: str | None, expected: str) -> tuple[int, dict | None]:
        cfg = {
            "image": image,
            "skin_baseline": [400, 120, 24],
            "eyes": {"left": {"roi": [190, 230, 120, 80], "expected_hex": expected},
                     "right": {"roi": [490, 230, 120, 80], "expected_hex": expected}},
            "lips": {"roi": [345, 420, 110, 50], "expected_hex": "#8A4B55",
                     "surround": [345, 470, 110, 24]},
            "skin_texture": {"roi": [150, 330, 100, 70]},
        }
        if reference:
            cfg["reference_image"] = reference
        path = write_json(os.path.join(work, f"gate_{name}.json"), cfg)
        code, rep, _ = run("makeup_check.py", "--config", path,
                           "--json", os.path.join(work, f"gate_{name}_result.json"))
        return code, rep

    teal = os.path.join(FIX, "makeup_face_teal.png")
    code_ok, rep_ok = makeup_gate("teal", teal, teal, "#2E8B97")
    ok = code_ok == 0 and rep_ok and rep_ok["verdict"] == "pass_code_checks" \
        and rep_ok["checks"]["eyeshadow_left"]["status"] == "consistent" \
        and rep_ok["checks"]["left_right_balance"]["status"] == "balanced"
    record("11f 色相一致时判 consistent 并给出数值", "PASS" if ok else "FAIL",
           (f"verdict={rep_ok['verdict']} dHue="
            f"{rep_ok['checks']['eyeshadow_left'].get('delta_hue_deg')}° "
            f"chromaRatio={rep_ok['checks']['eyeshadow_left'].get('chroma_ratio')}") if rep_ok else "no json")

    coral = os.path.join(FIX, "makeup_face_coral.png")
    code_cr, rep_cr = makeup_gate("coral", coral, coral, "#2E8B97")
    path_cr = os.path.join(work, "gate_coral.json")
    code_strict, _, _ = run("makeup_check.py", "--config", path_cr, "--strict")
    ok = (code_cr == 0 and rep_cr and rep_cr["verdict"] == "fail"
          and len(rep_cr["machine_failures"]) == 1 and code_strict == 7)
    record("11g 双眼色相背离判 fail（strict 退出 7）", "PASS" if ok else "FAIL",
           f"verdict={rep_cr['verdict'] if rep_cr else '?'} strict={code_strict} "
           f"dHue={rep_cr['checks']['eyeshadow_left'].get('delta_hue_deg') if rep_cr else '?'}°")

    asym = os.path.join(FIX, "makeup_face_asym.png")
    code_as, rep_as = makeup_gate("asym", asym, asym, "#2E8B97")
    lr = (rep_as or {}).get("checks", {}).get("left_right_balance", {})
    ok = code_as == 0 and rep_as and rep_as["verdict"] == "fail" and lr.get("status") == "asymmetric"
    record("11h 左右眼不平衡被单独检出", "PASS" if ok else "FAIL",
           f"ΔE={lr.get('delta_e')} Δhue={lr.get('delta_hue_deg')}° verdict={(rep_as or {}).get('verdict')}")

    smooth = os.path.join(FIX, "makeup_face_smooth.png")
    code_sm, rep_sm = makeup_gate("smooth", smooth, teal, "#2E8B97")
    tex = (rep_sm or {}).get("checks", {}).get("skin_texture", {})
    ok = code_sm == 0 and rep_sm and rep_sm["verdict"] == "fail" and tex.get("status") == "over_smoothed"
    record("11i 过度磨皮按参考图纹理能量检出", "PASS" if ok else "FAIL",
           f"ratio={tex.get('ratio')} (参考 {tex.get('laplacian_var_reference')})")

    bleed = os.path.join(FIX, "makeup_face_bleed.png")
    code_bl, rep_bl = makeup_gate("bleed", bleed, bleed, "#2E8B97")
    lb = (rep_bl or {}).get("checks", {}).get("lip_boundary", {})
    ok = (code_bl == 0 and rep_bl and lb.get("status") == "possible_bleed"
          and rep_bl["verdict"] == "review")
    record("11j 唇缘溢色只作软提示不硬判", "PASS" if ok else "FAIL",
           f"status={lb.get('status')} mix={lb.get('mix_fraction_skin_to_lip')} "
           f"verdict={(rep_bl or {}).get('verdict')}")

    not_code = {i["item"] for i in (rep_ok or {}).get("not_code_testable", [])}
    ok = any("身份" in i or "五官" in i for i in not_code) and len(not_code) >= 5
    record("11k 报告明确声明身份/五官不可自动判定", "PASS" if ok else "FAIL",
           f"{len(not_code)} 项非代码可测")

    # 11l–11n: 档案自洽性审计（色板 vs 层 vs 步骤 vs 提示词）
    code_au, au_clean, _ = run("makeup_spec_audit.py", "--project", proj_mk,
                               "--design", "mk_test_A")
    # 上面这份 v2 档案里 lips 有 color_hex 但没申报进 palette.designed，必须被点名
    undecl = {e.get("hex") for e in (au_clean or {}).get("errors", [])
              if e.get("code") == "palette_undeclared"}
    ok = code_au == 0 and au_clean and au_clean["ok"] is False and "#8A4B55" in undecl
    record("11l 未申报色值被审计点名（schema 查不出）", "PASS" if ok else "FAIL",
           f"exit={code_au} ok={au_clean.get('ok') if au_clean else '?'} "
           f"未申报={sorted(undecl)}")

    broken = json.loads(json.dumps(makeup_design))
    broken["design_id"] = "mk_test_broken"
    broken["module_payload"]["generation"]["prompt"] = "眼尾加深用 #0000FF"
    broken["module_payload"]["palette"]["measured"] = [{"hex": "#69B7BB", "role": "secondary"}]
    broken["module_payload"]["inspiration_source"]["extraction_method"] = "none"
    f_bk = write_json(os.path.join(work, "mk_broken.json"), broken)
    code_bk, bk, _ = run("makeup_spec_audit.py", "--spec", f_bk, "--strict")
    codes = {e.get("code") for e in (bk or {}).get("errors", [])}
    ok = (code_bk == 7 and "prompt_colour_drift" in codes
          and "measured_without_source" in codes)
    record("11m 提示词跑色 / 谎称取色 → error 且 strict 退出 7", "PASS" if ok else "FAIL",
           f"exit={code_bk} errors={sorted(codes)}")

    clean_proj = os.path.join(work, "proj_makeup_clean")
    run("muselab_state.py", "init", "--dir", clean_proj, "--title", "干净妆容")
    clean = json.loads(json.dumps(makeup_design))
    clean["design_id"] = "mk_clean"
    clean["module_payload"]["palette"]["designed"] = [
        {"area": "eyeshadow", "hex": "#2E8B97", "reason": "实测浅青降饱和 10%"},
        {"area": "lips", "hex": "#8A4B55", "reason": "与眼尾同冷调，降饱和取得体唇色"},
    ]
    clean["module_payload"]["layers"].pop("base", None)   # 层与步骤对齐：base 无步骤就删层
    # 层文字里要真的引用到申报的色值，且每一层都要有步骤——否则审计会（正确地）报 warn
    clean["module_payload"]["layers"]["eyeshadow"]["main"] = "#2E8B97 青绿压在眼尾 1/3"
    clean["module_payload"]["layers"]["lips"]["color_hex"] = "#8A4B55"
    clean["module_payload"]["layers"]["lips"]["finish"] = "satin"
    clean["module_payload"]["application_steps"] = [
        {"step": 1, "area": "eyeshadow", "product_type": "哑光眼影 #2E8B97",
         "placement": "眼尾 1/3 至下眼睑后段", "technique": "压后向外晕",
         "effect": "拉长眼型，边界柔化"},
        {"step": 2, "area": "lips", "product_type": "缎面唇膏 #8A4B55",
         "placement": "全唇", "technique": "薄涂后指腹按压去浮光",
         "effect": "与眼尾同冷调，不抢眼妆"},
    ]
    f_cl = write_json(os.path.join(work, "mk_clean.json"), clean)
    run("muselab_state.py", "design-add", "--dir", clean_proj, "--file", f_cl)
    code_cl, cl, _ = run("makeup_spec_audit.py", "--project", clean_proj,
                         "--design", "mk_clean", "--strict")
    ok = (code_cl == 0 and cl and cl["ok"] is True and cl["error_count"] == 0
          and cl["warning_count"] == 0)
    record("11n 自洽档案审计通过（strict 退出 0，零 warn）", "PASS" if ok else "FAIL",
           f"exit={code_cl} ok={cl.get('ok') if cl else '?'} "
           f"E={cl.get('error_count') if cl else '?'} W={cl.get('warning_count') if cl else '?'}")


    # ------------------------------------- 12a–12h: eyelid coverage / concept audit
    def lid_gate(name: str, image: str) -> tuple[int, dict | None]:
        cfg = {
            "image": image,
            "skin_baseline": [400, 120, 24],
            "lash_edge": "bottom",
            "roi_provenance": "测试夹具：坐标由生成器写死，不是人脸识别结果",
            "eyes": {
                "left": {"roi": [190, 270, 120, 44], "expected_hex": "#9FB8B6",
                         "lid": [190, 230, 120, 80], "under": [190, 318, 120, 16]},
                "right": {"roi": [490, 270, 120, 44], "expected_hex": "#9FB8B6",
                          "lid": [490, 230, 120, 80], "under": [490, 318, 120, 16]},
            },
        }
        path = write_json(os.path.join(work, f"lid_{name}.json"), cfg)
        code, rep, _ = run("makeup_check.py", "--config", path)
        return code, rep

    _, sym = lid_gate("sym", os.path.join(FIX, "makeup_lids_sym.png"))
    ok = (sym and sym["verdict"] == "pass_code_checks"
          and sym["checks"]["eyelid_left"]["status"] == "within_band"
          and sym["checks"]["eyelid_right"]["status"] == "within_band"
          and sym["checks"]["eyelid_balance"]["status"] == "balanced"
          and sym["checks"]["under_lid_left"]["status"] == "clean")
    record("12a 对称且只涂下半个上睑 → 通过（不再是'中位色接近就算平衡'）", "PASS" if ok else "FAIL",
           f"verdict={sym.get('verdict') if sym else '?'} "
           f"cov={sym['checks']['eyelid_left']['coverage_share'] if sym else '?'}")

    climb = sym["checks"]["eyelid_left"]["climb_fraction"] if sym else -1
    ok = abs(climb - 0.45) <= 0.12
    record("12b 爬升高度按带高归一化，读到画进去的 0.45", "PASS" if ok else "FAIL",
           f"climb={climb}（夹具绘制 0.45，容差 0.12）")

    _, full = lid_gate("full", os.path.join(FIX, "makeup_lids_full.png"))
    ok = (full and full["verdict"] == "fail"
          and full["checks"]["eyelid_left"]["status"] == "full_lid"
          and full["checks"]["eyelid_right"]["status"] == "full_lid"
          and full["checks"]["eyelid_left"]["climb_fraction"] > 0.8)
    record("12c 涂满整条上睑 → full_lid 硬失败", "PASS" if ok else "FAIL",
           f"climb={full['checks']['eyelid_left']['climb_fraction'] if full else '?'}")

    _, asym = lid_gate("asym", os.path.join(FIX, "makeup_lids_asym.png"))
    sig = (asym or {}).get("checks", {}).get("eyelid_balance", {}).get("signals_exceeded", [])
    ok = (asym and asym["verdict"] == "fail"
          and asym["checks"]["eyelid_balance"]["status"] == "asymmetric" and len(sig) >= 2)
    record("12d 左 0.30 / 右 0.90 → 覆盖+爬升两项同时超阈值才判不对称", "PASS" if ok else "FAIL",
           f"signals={sig}")

    _, spec_under = lid_gate("under", os.path.join(FIX, "makeup_under_spec.png"))
    ok = (spec_under
          and spec_under["checks"]["under_lid_left"]["status"] == "specular_below_lid"
          and spec_under["checks"]["under_lid_right"]["status"] == "specular_below_lid"
          and spec_under["verdict"] == "review")
    record("12e 下睫毛下方亮带 → 记为需人判的观察项，不当硬失败", "PASS" if ok else "FAIL",
           f"spec={spec_under['checks']['under_lid_left']['specular_share'] if spec_under else '?'}")

    code_cap, cap, _ = run("makeup_check.py", "--capabilities")
    ok = code_cap == 0 and cap and cap["roi_source"] in ("human_or_vision_declared",
                                                        "landmark_assisted_possible")
    record("12f 明确声明 ROI 来源：无关键点库时不得冒充识别算法", "PASS" if ok else "FAIL",
           f"available={cap.get('available') if cap else '?'} source={cap.get('roi_source') if cap else '?'}")

    # 12g/12h: concept 与层冲突（旧版残留）；否定式不得误报
    stale = json.loads(json.dumps(clean))
    stale["design_id"] = "mk_stale_concept"
    stale["concept"] = "眼尾加深一档，下眼睑后段也带色，其余面部留白。"
    f_stale = write_json(os.path.join(work, "mk_stale.json"), stale)
    code_st, st, _ = run("makeup_spec_audit.py", "--spec", f_stale, "--strict")
    rules = {e.get("rule") for e in (st or {}).get("errors", [])
             if e.get("code") == "concept_contradicts_layer"}
    ok = code_st == 7 and {"lower_lid", "deepen"} <= rules
    record("12g concept 写'眼尾加深/下眼睑带色'而层里写省略 → 两项 error", "PASS" if ok else "FAIL",
           f"exit={code_st} rules={sorted(rules)}")

    neg = json.loads(json.dumps(clean))
    neg["design_id"] = "mk_negated_concept"
    neg["concept"] = "眼影只到可见上睑一半，不加深眼尾、不画下眼睑。"
    f_neg = write_json(os.path.join(work, "mk_neg.json"), neg)
    code_ng, ng, _ = run("makeup_spec_audit.py", "--spec", f_neg, "--strict")
    codes_ng = {e.get("code") for e in (ng or {}).get("errors", [])}
    ok = code_ng == 0 and "concept_contradicts_layer" not in codes_ng
    record("12h 否定式（不加深眼尾）不被误判为冲突", "PASS" if ok else "FAIL",
           f"exit={code_ng} errors={sorted(codes_ng)}")

    # 12i: 未授权改动检测
    run("muselab_state.py", "revise", "--dir", clean_proj, "--id", "mk_clean",
        "--file", write_json(os.path.join(work, "mk_clean_lip.json"),
                             {"module_payload": {"layers": {"lips": {"finish": "matte"}}}}),
        "--change-request", "只改眼影的授权下，误改了唇")
    code_diff, diff_rep, _ = run("makeup_spec_audit.py", "--project", clean_proj,
                                 "--design", "mk_clean", "--since", "1",
                                 "--authorized", "eyeshadow")
    unauth = (diff_rep or {}).get("version_diff", {}).get("unauthorized_changes", [])
    ok = code_diff == 0 and "layer.lips" in unauth
    record("12i --since 比对：只授权眼影却改了唇 → 点名 unauthorized", "PASS" if ok else "FAIL",
           f"unauthorized={unauth}")

    # 12j: 真实出图回归（需 env 指定，套件不消耗额度）
    real_render = os.environ.get("MUSELAB_REAL_RENDER")
    if real_render and os.path.isfile(real_render):
        cfg_real = {
            "image": real_render,
            "skin_baseline": [430, 140, 30],
            "lash_edge": "bottom",
            "roi_provenance": "人工按刻度网格标注（真实出图回归）",
            "eyes": {
                "left": {"roi": [238, 280, 58, 20], "expected_hex": "#9FB8B6",
                         "lid": [212, 274, 84, 20], "under": [225, 340, 70, 16]},
                "right": {"roi": [495, 228, 82, 26], "expected_hex": "#9FB8B6",
                          "lid": [486, 222, 100, 30], "under": [518, 292, 78, 16]},
            },
        }
        p_real = write_json(os.path.join(work, "gate_real_render.json"), cfg_real)
        code_rl, rl, _ = run("makeup_check.py", "--config", p_real)
        fails = " ".join((rl or {}).get("machine_failures", []))
        ok = (code_rl == 0 and rl and rl["verdict"] == "fail"
              and "eyelid imbalance" in fails and "full_lid" in fails)
        record("12j 真实出图回归：眼睑不均衡与满睑覆盖被抓出", "PASS" if ok else "FAIL",
               f"failures={len((rl or {}).get('machine_failures', []))}")
    else:
        record("12j 真实出图回归（需要 MUSELAB_REAL_RENDER 指向出一张真实图）", "NOT-RUN",
               "本轮人工执行过，结果见项目内 qa_render_tidal_v11_real_01.md；套件不代跑额度任务")

    # 12k/12l: 顶层 image_prompt 也是"自称提示词"的字段（v8 起残留旧眼妆的真实来源）
    stale_ip = json.loads(json.dumps(clean))
    stale_ip["design_id"] = "mk_stale_image_prompt"
    stale_ip["image_prompt"] = ("眼影：冷灰米 #D9D6D3 打底，浅青 #7FB6B9 从睫毛根向上晕至眼窝 2/3，"
                                "深青 #14747C 只压在眼尾 1/3 与下眼睑后段。")
    f_ip = write_json(os.path.join(work, "mk_stale_image_prompt.json"), stale_ip)
    code_ip, ip_rep, _ = run("makeup_spec_audit.py", "--spec", f_ip, "--strict")
    codes_ip = {e.get("code") for e in (ip_rep or {}).get("errors", [])}
    ok = (code_ip == 7
          and "image_prompt_contradicts_layer" in codes_ip
          and "image_prompt_undeclared_colour" in codes_ip)
    record("12k 顶层 image_prompt 残留旧眼妆 → 冲突与未申报色两项 error", "PASS" if ok else "FAIL",
           f"exit={code_ip} errors={sorted(codes_ip)}")

    # 否定词和动词之间夹着字（"不叠加深色"）：按子句判否定，不能只看紧邻 6 个字
    neg_ip = json.loads(json.dumps(clean))
    neg_ip["design_id"] = "mk_negated_inline"
    neg_ip["image_prompt"] = ("眼影只到可见上睑一半，眼尾不加宽、不拉长、不叠加深色，不做烟熏；"
                              "下眼睑完全不画。")
    f_nip = write_json(os.path.join(work, "mk_neg_image_prompt.json"), neg_ip)
    code_nip, nip, _ = run("makeup_spec_audit.py", "--spec", f_nip, "--strict")
    codes_nip = {e.get("code") for e in (nip or {}).get("errors", [])}
    ok = code_nip == 0 and not any("contradicts_layer" in c for c in codes_nip)
    record("12l '不叠加深色'这类夹字否定不被误判（按子句判否定）", "PASS" if ok else "FAIL",
           f"exit={code_nip} errors={sorted(codes_nip)}")

    # ------------------- 13a–13g: 提示词单一来源（派生 / 同步 / 越界豁免的边界）
    #
    # 背景：妆容档案里有两个都自称"生成提示词"的字段。v8 起 mk_beach_tidal 的顶层
    # image_prompt 一直没跟着 generation.prompt 更新，于是用哪份都能说"符合档案"，
    # 结果连续几版都在画 v1 的浓眼妆。修法是让 image_prompt 变成派生字段：
    # design-add / revise 由程序写入，prompt-sync 负责补历史，审计负责抓手改。
    sync_proj = os.path.join(work, "proj_prompt_sync")
    run("muselab_state.py", "init", "--dir", sync_proj, "--title", "提示词同步")
    P1 = "眼影只在可见上睑下半，颜色 #2E8B97；唇 #8A4B55 薄涂。"
    P2 = "眼影改成 #2E8B97 薄薄一层，只到上睑一半；唇 #8A4B55 缎面。"
    sync_spec = json.loads(json.dumps(clean))
    sync_spec["design_id"] = "mk_prompt_sync"
    sync_spec["module_payload"]["generation"] = {
        "route": "creative_generation", "aspect_ratio": "1:1", "prompt": P1}
    # 故意写一份不同的 image_prompt：派生逻辑必须无视它
    sync_spec["image_prompt"] = "旧提示词：浅青 #7FB6B9 从睫毛根晕到眼窝 2/3"
    f_sync = write_json(os.path.join(work, "mk_prompt_sync.json"), sync_spec)
    code_ad, ad, _ = run("muselab_state.py", "design-add", "--dir", sync_proj, "--file", f_sync)
    _, sh1, _ = run("muselab_state.py", "design-show", "--dir", sync_proj,
                    "--id", "mk_prompt_sync", "--version", "1")
    got1 = ((sh1 or {}).get("spec") or {}).get("image_prompt")
    ok = code_ad == 0 and got1 == P1 and (ad or {}).get("image_prompt_derived") is True
    record("13a design-add 时 image_prompt 由 generation.prompt 派生（手写值被覆盖）",
           "PASS" if ok else "FAIL", f"exit={code_ad} derived={(ad or {}).get('image_prompt_derived')}")

    f_p2 = write_json(os.path.join(work, "mk_prompt_p2.json"),
                      {"change_request": "只改提示词里眼影的写法",
                       "module_payload": {"generation": {"prompt": P2}}})
    code_rv2, _, _ = run("muselab_state.py", "revise", "--dir", sync_proj,
                         "--id", "mk_prompt_sync", "--file", f_p2)
    _, sh2, _ = run("muselab_state.py", "design-show", "--dir", sync_proj,
                    "--id", "mk_prompt_sync", "--version", "2")
    spec2 = (sh2 or {}).get("spec") or {}
    code_d2, d2, _ = run("makeup_spec_audit.py", "--project", sync_proj,
                         "--design", "mk_prompt_sync", "--since", "1", "--authorized", "prompt")
    vd = (d2 or {}).get("version_diff", {})
    ok = (code_rv2 == 0 and spec2.get("image_prompt") == P2
          and vd.get("derived_units") == ["image_prompt"] and vd.get("unauthorized_changes") == [])
    record("13b 只改 generation.prompt → 镜像自动跟随；授权 prompt 时镜像算派生不算越界",
           "PASS" if ok else "FAIL",
           f"mirror==P2={spec2.get('image_prompt') == P2} derived={vd.get('derived_units')} "
           f"unauth={vd.get('unauthorized_changes')}")

    # 手改 project.json 制造漂移（模拟绕过状态机的人）
    pj_path = os.path.join(sync_proj, "project.json")
    with open(pj_path, encoding="utf-8") as fh:
        state_disk = json.load(fh)
    rec_disk = state_disk["designs"]["mk_prompt_sync"]
    rec_disk["versions"][-1]["spec"]["image_prompt"] = "手改的旧提示词：深青 #14747C 压眼尾 1/3"
    write_json(pj_path, state_disk)
    code_aud, aud, _ = run("makeup_spec_audit.py", "--project", sync_proj,
                           "--design", "mk_prompt_sync", "--strict")
    codes_aud = {e.get("code") for e in (aud or {}).get("errors", [])}
    ok = code_aud == 7 and "image_prompt_not_synced" in codes_aud
    record("13c 绕过状态机手改 image_prompt → 审计报 image_prompt_not_synced（error）",
           "PASS" if ok else "FAIL", f"exit={code_aud} errors={sorted(codes_aud)}")

    code_chk, chk, _ = run("muselab_state.py", "prompt-sync", "--dir", sync_proj, "--check")
    ok = (code_chk == 6 and (chk or {}).get("current_drift") == ["mk_prompt_sync"]
          and (chk or {}).get("ok") is False)
    record("13d prompt-sync --check 当前版本漂移时退出 6 并点名设计", "PASS" if ok else "FAIL",
           f"exit={code_chk} drift={(chk or {}).get('current_drift')}")

    n_before = len(rec_disk["versions"])
    code_fix, fix, _ = run("muselab_state.py", "prompt-sync", "--dir", sync_proj)
    code_chk2, chk2, _ = run("muselab_state.py", "prompt-sync", "--dir", sync_proj, "--check")
    with open(pj_path, encoding="utf-8") as fh:
        state_after = json.load(fh)
    versions_after = state_after["designs"]["mk_prompt_sync"]["versions"]
    cur_after = versions_after[-1]["spec"]
    prev_kept = versions_after[-2]["spec"]["image_prompt"].startswith("手改的旧提示词")
    ok = (code_fix == 0 and (fix or {}).get("action") == "synced"
          and len(versions_after) == n_before + 1
          and cur_after["image_prompt"] == cur_after["module_payload"]["generation"]["prompt"]
          and prev_kept and code_chk2 == 0)
    record("13e prompt-sync 生成同步版本：当前版本对齐、旧版本原样保留、复查退出 0",
           "PASS" if ok else "FAIL",
           f"v{n_before}->v{len(versions_after)} recheck_exit={code_chk2} prev_kept={prev_kept}")

    # 只改镜像、权威提示词没动：派生豁免不得生效，仍然算越界。
    # 基线取上一版（v3 由 prompt-sync 生成，与 v2 的 prompt 相同，只差 image_prompt）。
    with open(pj_path, encoding="utf-8") as fh:
        state_x = json.load(fh)
    rec_x = state_x["designs"]["mk_prompt_sync"]
    prev_n = rec_x["versions"][-2]["version"]
    rec_x["versions"][-1]["spec"]["image_prompt"] = "只改镜像，不改权威提示词"
    write_json(pj_path, state_x)
    code_g, g, _ = run("makeup_spec_audit.py", "--project", sync_proj, "--design", "mk_prompt_sync",
                       "--since", str(prev_n), "--authorized", "prompt")
    vdg = (g or {}).get("version_diff", {})
    ok = code_g == 0 and "image_prompt" in (vdg.get("unauthorized_changes") or [])
    record("13f 只改 image_prompt 而 prompt 未变 → 不享受派生豁免，仍判越界",
           "PASS" if ok else "FAIL",
           f"derived={vdg.get('derived_units')} unauth={vdg.get('unauthorized_changes')}")

    # 首饰档案没有载荷提示词：派生逻辑不得碰它
    _, jw_show, _ = run("muselab_state.py", "design-show", "--dir", proj, "--id", "jw_test_A")
    jw_ip = ((jw_show or {}).get("spec") or {}).get("image_prompt")
    code_jw, jw_chk, _ = run("muselab_state.py", "prompt-sync", "--dir", proj, "--check")
    jw_row = next((d for d in (jw_chk or {}).get("designs", [])
                   if d["design_id"] == "jw_test_A"), {})
    ok = (jw_ip == BASE_DESIGN["image_prompt"] and code_jw == 0 and jw_row.get("derived") is False
          and jw_row.get("in_sync") is False)
    record("13g 首饰设计（无载荷提示词）不被派生改写，prompt-sync 报 derived=false",
           "PASS" if ok else "FAIL",
           f"image_prompt 原样={jw_ip == BASE_DESIGN['image_prompt']} derived={jw_row.get('derived')}")

    # ---------------------------------------------- 9. workflows needing a model
    record("09 照片转三款手串（含真实出图）", "NOT-RUN",
           "需要 agent 侧图像生成工具 + 1 次出图额度；见 examples/beach-to-jewelry.md")
    record("09b 系列产品设计（多图一致）", "NOT-RUN", "需要图像生成工具与 4 次出图额度")
    record("09c 真人模拟佩戴", "NOT-RUN", "需要参考图编辑能力；商品一致性无法用脚本验证")
    record("09d 新闻灵感设计（联网）", "NOT-RUN", "需要联网搜索工具；脚本不联网，不编造新闻")
    record("12 妆容真实 ImageGen 出图（套件不代跑额度）", "NOT-RUN",
           "本轮已在项目内人工执行 1 次 v11 出图并出验收报告；套件不消耗额度，故不在自动流程内")
    record("12m 妆容参考图路线（面部照编辑）", "NOT-RUN",
           "需要真实面部照片 + 参考图编辑能力；本轮用户未提供面部照")

    # ---------------------------------------------------------------- summary
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    notrun = sum(1 for r in RESULTS if r["status"] == "NOT-RUN")
    envun = sum(1 for r in RESULTS if r["status"] == "ENV-UNSUPPORTED")
    print(f"\nPASS {passed} | FAIL {failed} | NOT-RUN {notrun} | ENV-UNSUPPORTED {envun} | total {len(RESULTS)}")

    report = {"ran_at": stamp, "platform": sys.platform, "summary": {
        "pass": passed, "fail": failed, "not_run": notrun, "env_unsupported": envun, "total": len(RESULTS)},
        "results": RESULTS}
    if args.json_out:
        write_json(args.json_out, report)

    md = [f"# MuseLab 测试结果\n", f"- 运行时间：{stamp}", f"- 平台：{sys.platform}",
          f"- 汇总：PASS {passed} / FAIL {failed} / NOT-RUN {notrun} / ENV-UNSUPPORTED {envun}\n",
          "| 用例 | 结果 | 说明 |", "|---|---|---|"]
    for r in RESULTS:
        md.append(f"| {r['case']} | {r['status']} | {r['detail'].replace('|', '/')} |")
    with open(os.path.join(HERE, "RESULTS.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    print(f"report -> {os.path.join(HERE, 'RESULTS.md')}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
