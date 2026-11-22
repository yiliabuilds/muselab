#!/usr/bin/env python3
"""MuseLab :: end-to-end demo (real pipeline, no network, no image-generation credits).

Runs the whole local chain on a generated test photo so you can see what each
script actually produces:

    fixtures -> capability probe -> measured palette -> real cutout -> white shot
             -> design record -> image task (no tool) -> verification

Usage:
    python3 scripts/example.py [--out demo_out]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable


def call(script: str, *args: str) -> tuple[int, dict | None]:
    proc = subprocess.run([PY, os.path.join(HERE, script), *args], capture_output=True, text=True)
    payload = None
    if proc.stdout.strip().startswith("{"):
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    if proc.returncode not in (0,):
        print(f"  ! {script} exit={proc.returncode} {(proc.stderr or '').strip()[:160]}")
    return proc.returncode, payload


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "demo_out"))
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    print("=" * 68)
    print("MuseLab demo — 真实执行，无网络、无出图额度")
    print("=" * 68)

    print("\n[1/6] 能力探测")
    code, caps = call("check_env.py", "--imagegen", "no", "--websearch", "no")
    if caps:
        v = caps["capabilities"]["vision_foreground_mask"]
        print(f"  Vision 前景分割: {v['available']} — {v['detail']}")
        print(f"  整体模式: {caps['overall']}")

    print("\n[2/6] 生成测试素材")
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):  # make_fixtures prints its own JSON
            import make_fixtures  # noqa: F401  (runs on import)
        src = os.path.join(ROOT, "tests", "fixtures", "product_beads.png")
    except Exception as exc:
        print(f"  ! fixture 生成失败: {exc}")
        return 2
    print(f"  素材: {src}")

    print("\n[3/6] 实测色板")
    code, pal = call("color_extract.py", src, "--mode", "mood", "--max-colors", "5",
                     "--swatch", os.path.join(out, "palette.png"))
    if pal and pal.get("ok"):
        print(f"  method={pal['extraction_method']}  实测={pal['is_measured']}")
        for p in pal["color_palette"]:
            print(f"    {p['hex']}  {p['name_zh']:<6} {p['role']:<9} {p['area_ratio']*100:5.1f}%")
        print(f"  氛围: {' / '.join(pal['atmosphere'].get('mood_words', []))}")

    print("\n[4/6] 真实抠图（真 alpha）")
    cut = os.path.join(out, "cutout.png")
    code, rep = call("cutout.py", src, "--out", cut, "--despill")
    if code == 0 and rep:
        print(f"  method={rep['method']}  generative={rep['is_generative']}")
        print(f"  bbox={rep['subject_bbox']}  area={rep['subject_area_ratio']:.3f}  "
              f"instances={rep['instance_count']}")
        print(f"  alpha: 透明 {rep['alpha_stats']['transparent_ratio']:.3f} / "
              f"不透明 {rep['alpha_stats']['opaque_ratio']:.3f} / "
              f"半透明 {rep['alpha_stats']['semi_transparent_ratio']:.3f}")
        print(f"  颜色未改动: {rep['color_preserved']}")
    else:
        print("  抠图不可用 → 此步骤降级为 Mode B（说明限制，不产出假图）")

    print("\n[5/6] 纯白商品图")
    if os.path.isfile(cut):
        code, comp = call("compose.py", cut, "--out", os.path.join(out, "white.png"),
                          "--bg", "white", "--size", "1600", "--aspect", "1:1")
        if comp and comp.get("ok"):
            print(f"  输出 {comp['output']}  {comp['output_size']}  纯白={comp['white_is_pure']}")
    else:
        print("  跳过（无抠图结果）")

    print("\n[6/6] 设计档案 + 任务登记 + 校验")
    proj = os.path.join(out, "project")
    call("muselab_state.py", "init", "--dir", proj, "--title", "Demo 海洋系列", "--force")
    design = {
        "design_id": "jw_demo_A",
        "design_category": "beaded_bracelet",
        "design_name": "潮汐三段",
        "concept": "把测试图中的三段珠色转成疏密结构",
        "structure": {"archetype": "half_bead_half_chain", "length_mm": 160,
                      "sequence": ["龙虾扣", "延长链5cm", "银隔珠", "木珠6mm×8", "O字链4cm", "木珠6mm×4"]},
        "materials": [{"name": "木珠", "spec": "6mm", "quantity": "12", "role": "main"},
                      {"name": "925银隔珠", "spec": "3mm", "quantity": "2", "role": "metal"}],
        "colors": {"primary": "#7A5C43", "accent": "#C9A227"},
        "image_prompt": "白底商业珠宝摄影，6mm 木珠三段手链",
        "negative_prompt": "水印，文字，多余珠",
        "approval_status": "draft",
    }
    dpath = os.path.join(out, "design.json")
    with open(dpath, "w", encoding="utf-8") as fh:
        json.dump(design, fh, ensure_ascii=False, indent=2)
    call("muselab_state.py", "design-add", "--dir", proj, "--file", dpath)
    task = {"task_id": "img_demo_1", "design_id": "jw_demo_A", "image_type": "product_shot",
            "prompt": "白底商业珠宝摄影，6mm 木珠三段手链", "negative_prompt": "水印",
            "aspect_ratio": "1:1", "tool_used": "none"}
    tpath = os.path.join(out, "task.json")
    with open(tpath, "w", encoding="utf-8") as fh:
        json.dump(task, fh, ensure_ascii=False, indent=2)
    call("muselab_state.py", "task-add", "--dir", proj, "--design-id", "jw_demo_A", "--file", tpath)
    code, fail = call("muselab_state.py", "task-fail", "--dir", proj, "--task-id", "img_demo_1",
                      "--reason", "demo: 未调用图像生成工具", "--no-tool")
    if fail:
        print(f"  出图任务: {fail['execution_status']}  output_path={fail['output_path']}（不虚构路径）")
    code, ver = call("muselab_state.py", "verify", "--dir", proj)
    if ver:
        print(f"  完整性校验: ok={ver['ok']}  designs={ver['designs']} tasks={ver['image_tasks']}")

    print("\n" + "=" * 68)
    print(f"产物目录: {out}")
    print("提示：出图任务被标记为 skipped_no_tool 且 output_path=null ——")
    print("      这就是「没有工具就不假装出了图」在数据结构上的保证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
