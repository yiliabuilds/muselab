#!/usr/bin/env python3
"""MuseLab :: makeup spec audit — 档案自洽性检查（纯字符串/集合运算）。

它解决的问题：schema 只能保证"字段齐全、类型对"，不能保证**同一份档案里的几处说法互相不矛盾**。
真实踩过的坑：
  - 改了眼妆层和步骤，但 generation.prompt 还是旧描述；
  - 色板只写了 6 个色，而 layers 与 application_steps 实际用了 9 个色；
  - 顶层 concept 还留着 v1 浓眼妆的"眼尾 1/3 与下眼睑后段"，而 layers 早已是单色日常版。
这类矛盾肉眼很难发现，且会直接污染出图。

检查项：
  1. palette_undeclared          — 除 palette 外出现的色值不在 palette.designed 里           [error]
  2. prompt_colour_drift         — generation.prompt 里引用了未申报的色值                    [error]
  3. measured_without_source     — 声称没做取色却带着实测色板                                 [error]
  4. image_prompt_not_synced     — 顶层 image_prompt 与权威提示词 generation.prompt 不一致     [error]
  5. concept_undeclared_colour   — concept 里的色值既不在 designed 也不在 measured            [error]
  6. concept_contradicts_layer   — concept 描述了某一层的做法，而该层声明"不做"                [error]
     prompt_contradicts_layer / image_prompt_contradicts_layer
                                 — 同一规则作用于提示词（同步时查权威字段，否则查镜像字段）   [error]
  7. step_not_in_prompt          — 有上妆步骤，但提示词里完全没提到该部位                      [warn]
  8. prompt_claims_without_step  — 提示词让画某部位，但步骤里没有                              [warn]
  9. palette_unused              — 声明了色值却没被引用                                       [warn]
 10. step_unknown_area           — application_steps 的 area 不在九层结构里                   [warn]
 11. layer_without_step          — 某层写了内容，却没有对应步骤（用户照着做会漏）              [warn]
 12. base_not_first / finishing_not_last — 步骤顺序明显不合理                                [warn]
 13. unauthorized_change         — --since N 时，超出 --authorized 范围的字段被改动            [warn]

明确不做（区分机器检查与人工/模型复核）：
  - 不做语义理解：concept / prompt 的自然语言是否"读起来像同一套妆"，只有视觉模型或人能判。
    第 4/5/6/7/8 项是**词典 + 结构比对**：concept 说"下眼睑压色"而层里写"省略"这种冲突能抓，
    "concept 写得很飘但不算矛盾"抓不到。
  - 不判断这套妆好不好看、适不适合用户。
  - 不判断颜色能不能上脸（那要看产品与肤色）。
  - 不改写档案：发现不一致只报告，绝不自动"修好"。同步由 `muselab_state.py prompt-sync` 负责，
    因为改档案必须产生新版本、必须留下 change_request。

Usage:
  python3 scripts/makeup_spec_audit.py --project <dir> [--design ID] [--json out.json] [--strict]
  python3 scripts/makeup_spec_audit.py --spec design.json [--strict]
  python3 scripts/makeup_spec_audit.py --project <dir> --design ID --since 11 \
      --authorized concept,prompt,negative_prompt,notes

Exit: 0 跑完了（看 JSON 的 ok / errors），2 输入有误，7 --strict 且有 error。
"""
import argparse
import json
import os
import re
import sys

HEX = re.compile(r"#[0-9A-Fa-f]{6}")

LAYERS = ["base", "brows", "eyeshadow", "eyeliner", "lashes",
          "blush", "lips", "highlight_contour", "finishing"]
# 步骤里的 area 允许比层更细：highlight_contour 会拆成 contour / highlight 两步写
EXTRA_STEP_AREAS = {"contour", "highlight"}
EMPTY_MARKERS = ("省略", "none", "无", "不使用", "不适用", "")
# 某层的某个键取这些值时，表示"这一层故意不做"，不算漏写步骤。
# 注意：这个元组只给 layer_active 用，别往里加 main/deepen 之类的细项——
# 那会让"眼影层"因为一个子键被标成不做，整层都被跳过。
DISABLED_KEYS = ("type", "position", "product", "product_type", "color")

# 部位 → 提示词里应出现的词。用于"提示词与步骤是否说到同一件事"的结构比对。
AREA_TERMS = {
    "base": ("底妆", "粉底"),
    "brows": ("眉",),
    "eyeshadow": ("眼影",),
    "eyeliner": ("眼线",),
    "lashes": ("睫毛",),
    "blush": ("腮红",),
    "lips": ("唇",),
    "highlight_contour": ("修容", "提亮", "高光"),
    "contour": ("修容",),
    "highlight": ("提亮", "高光"),
    "finishing": ("定妆", "喷雾"),
}

# concept 里"做了某事"的说法，与层里"明确不做"直接冲突。只看能被词典判定的组合。
# 判定方式：找到部位名词 → 在 ±窗口内找上妆动词 → 动词前若有否定词，说明是"声明不做"。
# 真实踩过的写法：v1 的 "只把这一条线压到眼尾 1/3 与下眼睑后段"（动词在名词前），
# 所以不能只匹配"名词 + 动词"的正序。
NEGATION_WORDS = re.compile(r"[不无未没禁]$|不得|避免|不要")
PLACEMENT_VERBS = re.compile(r"压|扫|画|晕|铺|带|点缀")
DEEPEN_VERBS = re.compile(r"加深|压深|叠深|晕深|加宽|拉长|烟熏|叠色")
TAIL_VERBS = re.compile(r"外拉|上挑|拉长|飞出|上扬")
CLAUSE_WINDOW = 14

# 每条规则只认自己的那类动词："压到眼尾 1/3" 是位置，不是加深；把它算成加深会误杀
# 那些只是描述落点、并且与 layers 一致的档案。
CONTRAST_RULES = [
    {
        "id": "lower_lid",
        "noun": re.compile(r"下眼睑|下眼"),
        "verbs": PLACEMENT_VERBS,
        "layer": "eyeshadow", "keys": ("lower_lid",),
        "message": "concept 说下眼睑上色，但 layers.eyeshadow.lower_lid 标为省略",
    },
    {
        "id": "deepen",
        "noun": re.compile(r"眼尾"),
        "verbs": DEEPEN_VERBS,
        "layer": "eyeshadow", "keys": ("deepen",),
        "message": "concept 说眼尾加深/加宽，但 layers.eyeshadow.deepen 标为省略",
    },
    {
        "id": "liner_tail",
        "noun": re.compile(r"眼线"),
        "verbs": TAIL_VERBS,
        "layer": "eyeliner", "keys": ("tail_mm",),
        "message": "concept 说眼线外拉/上挑，但 layers.eyeliner.tail_mm 为 0",
    },
]


CLAUSE_BOUNDARY = re.compile(r"[，。；;、\n：:,]")


def _negated_before(text: str, pos: int, back: int = 40) -> bool:
    """动词是否处在被否定的子句里。

    不能只看紧邻的几个字："不叠加深色" 里 "加深" 前面紧挨的是"加"，但整个子句
    是被"不"否定的；只看 6 个字会把这种写法误判成"声称加深"（v12 的提示词就踩了这个）。
    所以按标点切子句：看动词所在子句（从上一个标点到动词）里有没有否定词。
    这样"眼尾不加宽、不拉长"各自成立，而"眼尾不加宽，可略微加深"仍会被点出来。
    """
    seg = text[max(0, pos - back):pos]
    cuts = list(CLAUSE_BOUNDARY.finditer(seg))
    if cuts:
        seg = seg[cuts[-1].end():]
    return bool(re.search(r"[不无未没禁]|避免|切勿|禁止", seg))


def find_applied_claim(text: str, noun_re, verbs, window: int = CLAUSE_WINDOW):
    """返回第一处"声称做了"的片段；全是"不做"的表述则返回 None。"""
    for m in noun_re.finditer(text):
        seg_start = max(0, m.start() - window)
        seg = text[seg_start:m.end() + window]
        for v in verbs.finditer(seg):
            abs_pos = seg_start + v.start()
            if _negated_before(text, abs_pos):
                continue
            return text[max(0, abs_pos - 6): min(len(text), m.end() + 8)]
    return None


def die(msg: str, code: int = 2, **extra):
    print(json.dumps({"ok": False, "error": msg, **extra}, ensure_ascii=False, indent=2))
    sys.exit(code)


def walk_strings(node, path=""):
    """产出 (路径, 字符串)，用于扫出所有内嵌色值。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield (path, node)


def hexes_in(node, skip_prefixes=()):
    out = []
    for path, text in walk_strings(node):
        if any(path.startswith(p) for p in skip_prefixes):
            continue
        for h in HEX.findall(text):
            out.append((path, h.upper()))
    return out


def has_content(value) -> bool:
    """这一层算不算"写了东西"（'省略' / 'none' / 空 都不算）。"""
    if isinstance(value, dict):
        return any(has_content(v) for v in value.values())
    if isinstance(value, list):
        return any(has_content(v) for v in value)
    if isinstance(value, str):
        return value.strip().lower() not in [m.lower() for m in EMPTY_MARKERS]
    return value is not None


def layer_active(layer) -> bool:
    """这一层是否真的要做。

    `{"type": "none"}` 和 `{"position": "不使用"}` 是**明确声明不做**，不是漏写；
    如果把它们当缺失，就会报一堆假警告，把真问题淹掉。
    """
    if not isinstance(layer, dict):
        return has_content(layer)
    for key in DISABLED_KEYS:
        v = layer.get(key)
        if isinstance(v, str) and v.strip().lower() in ("none", "不使用", "不适用"):
            return False
    return has_content(layer)


def is_disabled(value) -> bool:
    """这一层/这个键是否被明确声明为"不做"。

    真实的档案里写的是"省略：日常版不再压深青"——带解释，不是干净的 "省略"。
    所以按前缀判，否则真冲突会被放过。0 也算（tail_mm: 0 = 不拉眼尾）。
    """
    if value is None:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) == 0.0
    if isinstance(value, str):
        s = value.strip().lower()
        if s == "" or s == "无":
            return True
        return any(s.startswith(m) for m in ("省略", "none", "不使用", "不适用", "不做"))
    if isinstance(value, (list, dict)):
        return not has_content(value)
    return False


def audit(spec: dict, payload: dict | None = None) -> dict:
    if payload is None:
        payload = spec.get("module_payload") if isinstance(spec.get("module_payload"), dict) else spec
    concept = str(spec.get("concept") or "")
    errors, warnings = [], []
    palette = payload.get("palette") or {}
    designed = palette.get("designed") or []
    measured = palette.get("measured") or []
    layers = payload.get("layers") or {}
    steps = payload.get("application_steps") or []
    generation = payload.get("generation") or {}
    insp = payload.get("inspiration_source") or {}
    prompt = str(generation.get("prompt") or "")
    # 顶层 image_prompt 是第二个"自称生成提示词"的字段（妆容的权威提示词是
    # module_payload.generation.prompt，但 image_prompt 一样会被拿去出图）。
    # 自 1.3.1 起它改为**派生字段**：design-add / revise 由程序写入，不允许独立维护。
    shell_prompt = str(spec.get("image_prompt") or "")
    prompt_synced = bool(prompt) and shell_prompt == prompt

    declared = {}
    for e in designed:
        declared.setdefault(str(e.get("hex", "")).upper(), []).append(e.get("area"))

    # 1. 未申报色值（palette 自身当然"包含"自己，跳过）
    used = hexes_in(payload, skip_prefixes=("palette.",))
    undeclared = {}
    for path, h in used:
        if h not in declared:
            undeclared.setdefault(h, []).append(path)
    for h, paths in sorted(undeclared.items()):
        errors.append({
            "code": "palette_undeclared", "hex": h, "paths": paths,
            "fix": "在 palette.designed 里补一条（area/hex/reason），或把它从这些位置删掉",
        })

    # 2. prompt 专用：出图提示词里的色值必须都申报过——它直接决定生成结果
    prompt_drift = {h: p for h, p in undeclared.items()
                    if any(x.startswith("generation.prompt") for x in p)}
    if prompt_drift:
        errors.append({
            "code": "prompt_colour_drift",
            "hexes": sorted(prompt_drift),
            "note": "generation.prompt 引用了色板里没有的色值：提示词与设计档案对不上，出图会跑偏",
        })

    # 3. 声称没取色，却带着实测色板 —— 这是诚实性问题，不是格式问题
    method = str(insp.get("extraction_method") or "")
    if method in ("none", "visual_estimate") and measured:
        errors.append({
            "code": "measured_without_source", "extraction_method": method,
            "measured_count": len(measured),
            "note": "extraction_method 表明未做像素取色，但 palette.measured 非空；"
                    "要么补真实取色命令，要么清空 measured 并把颜色标为估算",
        })

    # 4. 提示词有两个来源，只能认一个 —— 不同步就说明有人在状态机外手改了 JSON
    if prompt and not prompt_synced:
        errors.append({
            "code": "image_prompt_not_synced",
            "authoritative": "module_payload.generation.prompt",
            "mirror": "image_prompt",
            "authoritative_len": len(prompt), "mirror_len": len(shell_prompt),
            "fix": "muselab_state.py prompt-sync --dir <项目> --id <设计> "
                   "（会生成新版本，不会改写历史版本）",
            "note": "顶层 image_prompt 自 1.3.1 起是派生字段，必须与 generation.prompt 逐字相同。"
                    "两份提示词各写一套，出图时用哪份都算'符合档案'，这正是 v8–v12 一直画旧眼妆的原因",
        })

    # 5/6. 描述性字段：concept 与提示词。
    #
    # 提示词只查一次：同步时查权威字段 generation.prompt；未同步时查那份独立维护的
    # image_prompt（它已经自成一个必须自证清白的版本）。两处都查会让同一段文字
    # 报两遍（prompt_colour_drift + image_prompt_undeclared_colour），噪音盖掉信号。
    allowed_colours = set(declared) | {str(m.get("hex", "")).upper() for m in measured}
    describe = [("concept", concept)]
    if shell_prompt:
        describe.append(("prompt" if prompt_synced else "image_prompt", shell_prompt))
    for field, text in describe:
        if not text:
            continue
        # prompt 的色值已由第 2 项 prompt_colour_drift 覆盖，这里不重复报
        if field != "prompt":
            stray = sorted({h.upper() for h in HEX.findall(text)} - allowed_colours)
            if stray:
                errors.append({
                    "code": "concept_undeclared_colour" if field == "concept"
                            else "image_prompt_undeclared_colour",
                    "field": field, "hexes": stray,
                    "note": f"{field} 引用的色值既不在 palette.designed，也不在 palette.measured；"
                            "说明这段描述没跟着色板一起改",
                })
        for rule in CONTRAST_RULES:
            excerpt = find_applied_claim(text, rule["noun"], rule["verbs"])
            if not excerpt:
                continue
            layer = layers.get(rule["layer"]) or {}
            vals = [layer.get(k) for k in rule["keys"]]
            if vals and all(is_disabled(v) for v in vals):
                errors.append({
                    "code": "concept_contradicts_layer" if field == "concept"
                            else f"{field}_contradicts_layer",
                    "field": field, "rule": rule["id"],
                    "layer": rule["layer"], "keys": list(rule["keys"]),
                    "excerpt": excerpt,
                    "note": rule["message"].replace("concept ", f"{field} ") +
                            f"；{field} 与 layers 说的不是同一套妆",
                })

    # 6/7. prompt 与步骤是否说到同一件事（词典 + 结构，不做语义理解）
    step_areas = [str(s.get("area") or "") for s in steps]
    norm_area = lambda a: "highlight_contour" if a in ("contour", "highlight") else a
    step_areas_norm = {norm_area(a) for a in step_areas if a}
    if prompt:
        for area in sorted(step_areas_norm):
            terms = AREA_TERMS.get(area)
            if terms and not any(t in prompt for t in terms):
                warnings.append({
                    "code": "step_not_in_prompt", "area": area, "expected_terms": list(terms),
                    "note": "有这一步上妆操作，但提示词完全没提到该部位——出图时模型不会画它",
                })
        for area, terms in AREA_TERMS.items():
            if area in step_areas_norm:
                continue
            # highlight_contour 的步骤会拆成 contour / highlight 两步写，两边都算数
            if area in ("contour", "highlight") and "highlight_contour" in step_areas_norm:
                continue
            if any(t in prompt for t in terms):
                warnings.append({
                    "code": "prompt_claims_without_step", "area": area,
                    "matched_terms": [t for t in terms if t in prompt],
                    "note": "提示词让模型画这个部位，但步骤里没有它——用户照着做会漏",
                })

    # 8. 声明了却没人用
    referenced = {h for _, h in used}
    for h, areas in sorted(declared.items()):
        if h not in referenced:
            warnings.append({"code": "palette_unused", "hex": h, "areas": areas,
                             "note": "色板列了这个色，但没有层或步骤引用它"})

    # 9. 步骤里出现未知区域
    known = set(LAYERS) | EXTRA_STEP_AREAS
    for i, s in enumerate(steps):
        area = str(s.get("area") or "")
        if area and area not in known:
            warnings.append({"code": "step_unknown_area", "step_index": i, "area": area,
                             "allowed": sorted(known)})

    # 10. 层有内容但没有对应步骤
    for name in LAYERS:
        if name in layers and layer_active(layers[name]) and name not in step_areas_norm:
            warnings.append({"code": "layer_without_step", "layer": name,
                             "note": "这一层写了做法，但 application_steps 里没有对应步骤"})

    # 11. 顺序：base 不应晚于任何上色步骤；finishing 应在最后
    order = [str(s.get("area") or "") for s in steps]
    colour_areas = {"brows", "eyeshadow", "eyeliner", "lashes", "blush", "lips",
                    "contour", "highlight"}
    first_colour = next((i for i, a in enumerate(order) if a in colour_areas), None)
    last_base = max((i for i, a in enumerate(order) if a == "base"), default=None)
    if first_colour is not None and last_base is not None and last_base > first_colour:
        warnings.append({"code": "base_not_first", "note": "有底妆步骤排在上色步骤之后",
                         "first_colour_index": first_colour, "last_base_index": last_base})
    if "finishing" in order and order[-1] != "finishing":
        warnings.append({"code": "finishing_not_last", "last_area": order[-1],
                         "note": "定妆/收尾步骤不在最后一步"})

    return {
        "makeup_name_zh": payload.get("makeup_name_zh"),
        "designed_colours": len(designed),
        "distinct_colours_used": len(referenced),
        "concept_checked": bool(concept),
        "image_prompt_checked": bool(shell_prompt),
        "prompt_authoritative": "module_payload.generation.prompt" if prompt else None,
        "prompt_synced": prompt_synced if prompt else None,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


def load_payload(a) -> tuple:
    """返回 (spec, payload, design_id, version)。支持 project 目录或单份 spec 文件。"""
    if a.spec:
        if not os.path.isfile(a.spec):
            die("spec_not_found", path=a.spec)
        data = json.load(open(a.spec, "r", encoding="utf-8"))
        spec = data if isinstance(data.get("module_payload"), dict) else {"module_payload": data}
        payload = spec.get("module_payload", data)
        return spec, payload, data.get("design_id") or os.path.basename(a.spec), data.get("version")
    if not a.project:
        die("missing_input", hint="pass --project <dir> or --spec <file>")
    path = os.path.join(a.project, "project.json")
    if not os.path.isfile(path):
        die("project_not_found", path=path)
    state = json.load(open(path, "r", encoding="utf-8"))
    designs = state.get("designs") or {}
    if not designs:
        die("no_designs", path=path)
    did = a.design
    if not did:
        # 没指定就只看妆容类设计
        makeup = [k for k, v in designs.items()
                  if any(ver["spec"].get("design_category") == "makeup" for ver in v.get("versions", []))]
        if not makeup:
            die("no_makeup_design", hint="这份项目里没有 design_category=makeup 的设计")
        did = makeup[0] if len(makeup) == 1 else None
        if did is None:
            die("design_required", available=makeup,
                hint="有多套妆容，请用 --design 指定其中一套")
    entry = designs.get(did)
    if not entry:
        die("design_not_found", design=did, available=list(designs))
    cur = entry.get("current_version")
    ver = next((v for v in entry["versions"] if v["version"] == cur), None)
    if not ver:
        die("version_not_found", design=did, version=cur)
    return ver["spec"], (ver["spec"].get("module_payload") or {}), did, cur


# ---------------------------------------------------------------- version diff
META_KEYS = {"version", "parent_version", "created_at", "change_request", "approval_status",
             "design_id"}

# 派生单元：由程序从别的字段生成，不构成"独立改动"。
# 授权 prompt 就等于授权它的镜像 image_prompt——否则每次改提示词都会被自己的派生
# 逻辑判成越界，护栏变成噪音。但只在**来源单元确实也变了**时才放宽：只改了
# image_prompt 而 prompt 没动，说明有人手改 JSON，那正是要抓的越界。
DERIVED_UNITS = {"image_prompt": "prompt"}


def unit_of(path: str) -> str:
    """把叶子路径归到一个"授权单元"，方便说"这次只许动眼影"。"""
    if path in META_KEYS:
        return "meta"
    if path == "concept":
        return "concept"
    if path.startswith("module_payload.layers."):
        parts = path.split(".")
        return "layer." + (parts[2] if len(parts) > 2 else "?")
    if path.startswith("module_payload.application_steps"):
        return "steps"
    if path.startswith("module_payload.palette"):
        return "palette"
    if path.startswith("module_payload.generation.prompt"):
        return "prompt"
    if path.startswith("module_payload.generation.negative_prompt"):
        return "negative_prompt"
    if path.startswith("module_payload.generation"):
        return "generation_other"
    if path.startswith("module_payload.notes"):
        return "notes"
    if path.startswith("module_payload."):
        return "payload." + path.split(".")[1]
    return path.split(".")[0]


def flatten(node, prefix="") -> dict:
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = node
    return out


def diff_units(old_spec: dict, new_spec: dict) -> dict:
    a, b = flatten(old_spec), flatten(new_spec)
    changed = [k for k in sorted(set(a) | set(b)) if a.get(k, "<missing>") != b.get(k, "<missing>")]
    units = {}
    for k in changed:
        units.setdefault(unit_of(k), []).append(k)
    return units


def load_version_spec(project_dir: str, design_id: str, version: int):
    path = os.path.join(project_dir, "project.json")
    state = json.load(open(path, "r", encoding="utf-8"))
    entry = (state.get("designs") or {}).get(design_id)
    if not entry:
        die("design_not_found", design=design_id)
    ver = next((v for v in entry["versions"] if v["version"] == version), None)
    if not ver:
        die("version_not_found", design=design_id, version=version,
            available=[v["version"] for v in entry["versions"]])
    return ver["spec"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Makeup spec self-consistency audit")
    ap.add_argument("--project", default=None, help="项目目录（含 project.json）")
    ap.add_argument("--design", default=None, help="设计 ID；只有一套妆容时可省略")
    ap.add_argument("--spec", default=None, help="直接审一份 design JSON")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--strict", action="store_true", help="有 error 时 exit 7")
    ap.add_argument("--since", type=int, default=None,
                    help="与指定历史版本比对，列出被改动的单元（需配合 --project）")
    ap.add_argument("--authorized", default=None,
                    help="本次授权改动的单元，逗号分隔；单元有 concept / image_prompt / prompt / "
                         "negative_prompt / notes / steps / palette / meta / layer.<名>（可简写）。"
                         "image_prompt 是 prompt 的镜像：来源也变了时自动视为已授权")
    a = ap.parse_args()

    spec, payload, did, version = load_payload(a)
    if not isinstance(payload, dict) or not payload:
        die("empty_payload", design=did, hint="该设计没有 module_payload，不是妆容设计？")

    report = {"ok": True, "design_id": did, "version": version, **audit(spec, payload),
              "automation_boundary": {
                  "machine_checked": [
                      "色值是否都在 palette 内（layers / steps / generation.prompt / concept）",
                      "image_prompt 是否与权威提示词 generation.prompt 逐字一致（逐字比对）",
                      "concept 与提示词是否描述了某层明确声明『不做』的动作（词典匹配）",
                      "generation.prompt 与 application_steps 是否覆盖同一批部位（关键词典比对）",
                      "步骤顺序与层-步骤对应关系",
                      "与指定历史版本相比，哪些单元被改动（结构 diff）",
                  ],
                  "needs_human_or_vision": [
                      "concept / prompt 的自然语言是否真的在描述这套妆（语义等价）",
                      "旧版描述是否以本脚本词典没覆盖的措辞残留",
                      "改动的单元是否在创意上可接受",
                  ],
                  "note": "第 5/6/7/8 项是词典 + 结构比对，能抓『说同一件事却互相矛盾』，"
                          "抓不到『措辞完全不同但意思一致』。不要把它当成语义审计。",
              }}

    if a.since is not None:
        if not a.project:
            die("since_requires_project", hint="--since 需要 --project 才能读到历史版本")
        old = load_version_spec(a.project, did, a.since)
        units = diff_units(old, spec)
        authorized = set()
        if a.authorized:
            for raw in a.authorized.split(","):
                u = raw.strip()
                if not u:
                    continue
                authorized.add(u if "." in u else
                               (f"layer.{u}" if u in LAYERS else u))
        authorized.discard("meta")
        # image_prompt 是 generation.prompt 的镜像：来源也变了才算"隐式授权"
        implicit = {u for u in units
                    if u in DERIVED_UNITS and DERIVED_UNITS[u] in authorized
                    and DERIVED_UNITS[u] in units}
        unauthorized = {u: paths for u, paths in units.items()
                        if u not in authorized and u not in implicit and u != "meta"}
        report["version_diff"] = {
            "since": a.since, "to": version,
            "changed_units": sorted(units),
            "changed_leaf_count": sum(len(v) for v in units.values()),
            "authorized": sorted(authorized) if authorized else None,
            "derived_units": sorted(implicit),
            "unauthorized_changes": sorted(unauthorized),
        }
        if a.authorized and unauthorized:
            for u, paths in sorted(unauthorized.items()):
                report["warnings"].append({
                    "code": "unauthorized_change", "unit": u,
                    "paths": paths[:8],
                    "note": "这个单元不在本次授权范围内，却被改动了——核对是否越界",
                })

    report["not_checked"] = [
        "concept / generation.prompt 的文字是否与 layers 语义等价（本脚本只做词典与结构比对）",
        "是否有本脚本词典没覆盖的旧版措辞残留",
        "这套妆是否好看、是否适合这位用户",
        "颜色能否真的上脸（取决于产品与肤色）",
        "图层与步骤在现实中是否可执行（需人复核）",
    ]
    report["error_count"] = len(report["errors"])
    report["warning_count"] = len(report["warnings"])

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(a.json_out)), exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    if a.strict and report["errors"]:
        return 7
    return 0


if __name__ == "__main__":
    sys.exit(main())
