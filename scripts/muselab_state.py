#!/usr/bin/env python3
"""MuseLab :: project state, design versions, and image tasks.

Why this exists: a multi-turn creative session cannot rely on the conversation
remembering a bead count. Everything that later steps must not silently change
lives here, in one atomic JSON file per project.

Guarantees
  1. An approved design is frozen. It can be referenced, exported and drawn on,
     but not mutated — unless the user explicitly asks (--consent) and a NEW
     version is created. History is never overwritten.
  2. Status transitions are whitelisted. draft -> approved must pass through
     selected, so nothing gets "finished" by accident.
  3. Image tasks record their real outcome: succeeded / failed / skipped_no_tool.
     A task with no tool can never carry an output_path.

Usage
  muselab_state.py init        --dir P --title "海洋系列" [--project-id slug]
  muselab_state.py profile-add --dir P --file profile.json
  muselab_state.py design-add  --dir P --file design.json
  muselab_state.py design-list --dir P
  muselab_state.py design-show --dir P --id jw_a --version 2
  muselab_state.py status      --dir P --id jw_a --set selected [--note "..."]
  muselab_state.py revise      --dir P --id jw_a --file patch.json [--consent]
  muselab_state.py task-add    --dir P --design-id jw_a --file task.json
  muselab_state.py task-done   --dir P --task-id img_1 --path out.png [--tool ImageGen] [--fidelity-notes "..."]
  muselab_state.py task-fail   --dir P --task-id img_1 --reason "no image tool" [--no-tool]
  muselab_state.py export      --dir P [--design-id jw_a]
  muselab_state.py verify      --dir P
  muselab_state.py link-report --dir P --design-id jw_a --report cutout.json --image out.png

Exit codes: 0 ok, 2 usage/data error, 6 state rule violation (frozen / bad transition)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.environ.get("MUSELAB_SCHEMA_DIR") or os.path.join(os.path.dirname(HERE), "schemas")

STATUSES = ["draft", "selected", "approved", "revision_requested", "generated", "failed"]
ALLOWED_TRANSITIONS = {
    "draft": {"selected", "revision_requested", "failed"},
    "selected": {"approved", "revision_requested", "draft"},
    "revision_requested": {"draft", "selected"},
    "approved": {"revision_requested"},  # and only through `revise --consent`
    "generated": {"selected", "approved", "revision_requested"},
    "failed": {"draft", "revision_requested"},
}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def die(msg: str, code: int = 2, **extra) -> "NoReturn":  # noqa: F821
    payload = {"ok": False, "error": msg}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(code)


def load_json(path: str):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ schema check
def validate(record: dict, schema_name: str) -> list[str]:
    """Validate against schemas/ if jsonschema is installed; otherwise check required keys."""
    path = os.path.join(SCHEMA_DIR, schema_name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            schema = json.load(fh)
    except FileNotFoundError:
        return []
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(schema)
        return [f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
                for e in sorted(validator.iter_errors(record), key=lambda e: list(e.path))]
    except ImportError:
        errors = []
        for key in schema.get("required", []):
            if key not in record:
                errors.append(f"missing required field: {key}")
        props = schema.get("properties", {})
        for key, spec in props.items():
            if key in record and spec.get("enum") and record[key] not in spec["enum"]:
                errors.append(f"{key}: '{record[key]}' not in {spec['enum']}")
        return errors


# 模块专有载荷：design_category -> 额外 schema。新增模块只在这里加一行，
# 不去改首饰字段的含义（见 references/module-extension.md）。
PAYLOAD_SCHEMA = {"makeup": "makeup-spec.schema.json"}


def validate_design(spec: dict) -> list[str]:
    """Validate the shared envelope, then the module payload when the category needs one."""
    errors = validate(spec, "design-spec.schema.json")
    category = str(spec.get("design_category") or "")
    # `structure` describes a bead strand (archetype / length / sequence). A makeup look has
    # no strand, so it is required for every other category but not for makeup.
    if category != "makeup" and not spec.get("structure"):
        errors.append(f"structure: required for design_category={category or '?'}")
    schema_name = PAYLOAD_SCHEMA.get(category)
    if schema_name:
        payload = spec.get("module_payload")
        if not isinstance(payload, dict) or not payload:
            errors.append(
                f"module_payload: required for design_category={spec.get('design_category')} "
                f"(see schemas/{schema_name})")
        else:
            errors += [f"module_payload/{e}" for e in validate(payload, schema_name)]
    return errors


def _list_paths(obj, prefix: str = "") -> list[str]:
    """Paths of every list inside a patch. Lists are replaced wholesale, so a partial
    list silently deletes the entries you did not resend — worth telling the caller."""
    out: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            out += _list_paths(value, f"{prefix}{key}.")
    elif isinstance(obj, list):
        out.append(prefix.rstrip("."))
    return out


def _deep_merge(base: dict, patch: dict) -> dict:
    """Recursive patch: dicts merge, everything else replaces.

    Deep rather than shallow so a makeup revision can touch only `eyeshadow`
    without wiping sibling layers inside `module_payload`. Lists are replaced
    wholesale — there is no identity to merge array items by.
    """
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ------------------------------------------------------- prompt single source
def authoritative_prompt(spec: dict) -> str | None:
    """The prompt generation actually uses, or None for specs without a payload prompt.

    `module_payload.generation.prompt` is the single source of truth for any module
    that ships a payload (makeup does). Jewellery designs have no payload prompt and
    keep `image_prompt` as their only prompt field — nothing is derived there.
    """
    payload = spec.get("module_payload")
    if not isinstance(payload, dict):
        return None
    generation = payload.get("generation")
    if not isinstance(generation, dict):
        return None
    prompt = generation.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    return prompt


def derive_image_prompt(spec: dict) -> bool:
    """Mirror the authoritative prompt into `image_prompt`. Returns True if it changed.

    `image_prompt` is required by design-spec.schema.json and is what several call
    sites read, but for payload modules it is a *copy*. Copying by hand is exactly
    how mk_beach_tidal kept rendering the v1 look from v8 to v12: the envelope field
    was never re-synced. So the caller's value is never trusted — it is overwritten.
    """
    prompt = authoritative_prompt(spec)
    if prompt is None:
        return False
    changed = spec.get("image_prompt") != prompt
    spec["image_prompt"] = prompt
    return changed


def prompt_drift(spec: dict) -> dict:
    """What `prompt-sync` needs to report for one version, without touching it."""
    prompt = authoritative_prompt(spec)
    mirror = spec.get("image_prompt")
    return {
        "authoritative_len": len(prompt) if prompt else 0,
        "image_prompt_len": len(mirror) if isinstance(mirror, str) else 0,
        "in_sync": bool(prompt) and mirror == prompt,
        "derived": prompt is not None,
    }


# -------------------------------------------------------------------- project io
def project_path(workspace: str) -> str:
    return os.path.join(workspace, "project.json")


def load_project(workspace: str, must_exist: bool = True) -> dict:
    path = project_path(workspace)
    if not os.path.isfile(path):
        if must_exist:
            die("project_not_found", hint=f"Run: muselab_state.py init --dir {workspace} --title ...")
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_project(workspace: str, state: dict) -> None:
    """Write project.json in place.

    Deliberately not temp-file + rename: some sandboxes forbid the unlink that
    rename-based atomicity relies on, and a half-written state is recoverable
    (the JSON is small and schema-checked by `verify`), whereas a hard crash on
    every save is not.
    """
    state["updated_at"] = now()
    os.makedirs(workspace, exist_ok=True)
    with open(project_path(workspace), "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def slug(text: str, fallback: str = "project") -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower()
    return s[:40] or fallback


# ---------------------------------------------------------------------- commands
def cmd_init(a) -> None:
    workspace = os.path.abspath(a.dir)
    existing = load_project(workspace, must_exist=False)
    if existing and not a.force:
        die("project_exists", workspace=workspace,
            hint="Refusing to overwrite. Pass --force only if the user confirmed.")
    pid = a.project_id or slug(a.title)
    state = {
        "project_id": pid,
        "title": a.title,
        "created_at": existing.get("created_at", now()) if existing else now(),
        "updated_at": now(),
        "workspace_dir": workspace,
        "status": "draft",
        "profiles": {},
        "designs": {},
        "image_tasks": {},
        "modules_used": [],
        "assumptions": [],
        "notes": [],
    }
    save_project(workspace, state)
    for sub in ("images", "exports"):
        os.makedirs(os.path.join(workspace, sub), exist_ok=True)
    dump({"ok": True, "project": project_path(workspace), "state": state})


def cmd_profile_add(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    profile = load_json(a.file)
    errors = validate(profile, "visual-profile.schema.json")
    if errors:
        die("invalid_visual_profile", errors=errors)
    pid = profile.get("profile_id") or f"vp_{uuid.uuid4().hex[:8]}"
    profile["profile_id"] = pid
    profile.setdefault("created_at", now())
    state.setdefault("profiles", {})[pid] = profile
    if "visual_analysis" not in state["modules_used"]:
        state["modules_used"].append("visual_analysis")
    save_project(workspace, state)
    measured = profile.get("extraction_method") == "pixel_kmeans_lab"
    dump({"ok": True, "profile_id": pid,
          "colors_are_measured": measured,
          "reminder": "Report colours as measured" if measured else "Report colours as visual estimates"})


def cmd_design_add(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    spec = load_json(a.file)
    spec.setdefault("project_id", state["project_id"])
    did = spec.get("design_id")
    if not did:
        die("missing_design_id")
    if did in state["designs"]:
        die("design_exists", design_id=did,
            hint="Use `revise` to create a new version; never overwrite an existing design.")
    spec["version"] = 1
    spec.setdefault("approval_status", "draft")
    spec.setdefault("created_at", now())
    # 信封字段 image_prompt 由载荷里的权威提示词派生；调用方写什么都覆盖。
    synced = derive_image_prompt(spec)
    errors = validate_design(spec)
    if errors:
        die("invalid_design_spec", errors=errors)
    state["designs"][did] = {
        "design_id": did,
        "current_version": 1,
        "frozen": spec["approval_status"] == "approved",
        "versions": [{
            "version": 1,
            "status": spec["approval_status"],
            "spec": spec,
            "created_at": spec["created_at"],
            "image_task_ids": [],
        }],
    }
    if "design" not in state["modules_used"]:
        state["modules_used"].append("design")
    save_project(workspace, state)
    dump({"ok": True, "design_id": did, "version": 1, "status": spec["approval_status"],
          "image_prompt_derived": synced})


def cmd_design_list(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    rows = []
    for did, rec in state["designs"].items():
        cur = rec["versions"][-1]
        spec = cur["spec"]
        rows.append({
            "design_id": did,
            "version": cur["version"],
            "status": cur["status"],
            "frozen": rec.get("frozen", False),
            "name": spec.get("design_name"),
            "category": spec.get("design_category"),
            "structure": (spec.get("structure") or {}).get("archetype"),
            "image_tasks": len(cur.get("image_task_ids", [])),
        })
    dump({"ok": True, "project_id": state["project_id"], "count": len(rows), "designs": rows})


def cmd_design_show(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    rec = state["designs"].get(a.id)
    if not rec:
        die("design_not_found", design_id=a.id, available=list(state["designs"]))
    if a.version:
        for v in rec["versions"]:
            if v["version"] == a.version:
                dump({"ok": True, "design": rec["design_id"], "frozen": rec.get("frozen", False), **v})
                return
        die("version_not_found", design_id=a.id, version=a.version)
    dump({"ok": True, "design": rec["design_id"], "current_version": rec["current_version"],
          "frozen": rec.get("frozen", False), **rec["versions"][-1]})


def _set_status(state: dict, did: str, new_status: str, note: str | None) -> dict:
    rec = state["designs"][did]
    cur = rec["versions"][-1]
    old = cur["status"]
    if old == new_status:
        return {"ok": True, "design_id": did, "version": cur["version"], "status": old,
                "unchanged": True}
    if old == "approved" and new_status != "revision_requested":
        die("design_is_frozen", design_id=did,
            hint=("Approved designs are reference-only. To change one, run `revise --consent` "
                  "after the user explicitly asks for a modification."), code=6)
    if new_status not in ALLOWED_TRANSITIONS.get(old, set()):
        die("invalid_transition", design_id=did, **{"from": old, "to": new_status},
            allowed=sorted(ALLOWED_TRANSITIONS.get(old, [])), code=6)
    cur["status"] = new_status
    if note:
        cur["note"] = note
    cur["spec"]["approval_status"] = new_status
    rec["frozen"] = cur["status"] == "approved"
    return {"ok": True, "design_id": did, "version": cur["version"], "from": old, "status": new_status}


def cmd_status(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    if a.id not in state["designs"]:
        die("design_not_found", design_id=a.id, available=list(state["designs"]))
    if a.set not in STATUSES:
        die("unknown_status", provided=a.set, allowed=STATUSES)
    result = _set_status(state, a.id, a.set, a.note)
    save_project(workspace, state)
    dump(result)


def _apply_revision(state: dict, did: str, patch: dict, change: str,
                    consent: bool) -> tuple[dict, dict, dict, int, bool]:
    """Append a new version built from `patch`; never touches existing versions.

    Shared by `revise` and `prompt-sync` so both obey the same rules: frozen designs
    need --consent, the derived prompt is applied before validation, and the old
    version keeps its own status forever.
    """
    rec = state["designs"].get(did)
    if not rec:
        die("design_not_found", design_id=did, available=list(state["designs"]))
    cur = rec["versions"][-1]
    if cur["status"] == "approved" and not consent:
        die("consent_required", design_id=did,
            hint=("This design is approved. Ask the user '确认要修改已定稿的第 N 版吗?' and re-run with "
                  "--consent. The approved version stays in history."), code=6)

    new_spec = json.loads(json.dumps(cur["spec"]))  # deep copy
    _deep_merge(new_spec, patch)
    new_version = cur["version"] + 1
    new_spec["version"] = new_version
    new_spec["parent_version"] = cur["version"]
    new_spec["approval_status"] = "draft"
    new_spec["change_request"] = change
    new_spec["design_id"] = did
    new_spec["created_at"] = now()
    # 派生必须在 schema 校验之前：image_prompt 是必填字段，由载荷提示词填。
    synced = derive_image_prompt(new_spec)

    errors = validate_design(new_spec)
    if errors:
        die("invalid_design_spec", errors=errors)

    cur["superseded_by"] = new_version
    cur["change_request"] = change
    cur["revision_requested_at"] = new_spec["created_at"]
    # History is append-only: the approved version keeps its own status forever.
    rec["versions"].append({
        "version": new_version,
        "status": "draft",
        "spec": new_spec,
        "created_at": new_spec["created_at"],
        "change_request": change,
        "image_task_ids": [],
    })
    rec["current_version"] = new_version
    rec["frozen"] = False
    return rec, cur, new_spec, new_version, synced


def cmd_revise(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    patch = load_json(a.file)
    change = patch.pop("change_request", None) or a.change_request or "user requested revision"

    rec, cur, new_spec, new_version, synced = _apply_revision(
        state, a.id, patch, change, a.consent)
    save_project(workspace, state)
    replaced = _list_paths(patch)
    result = {"ok": True, "design_id": a.id, "new_version": new_version,
              "previous_version_kept": cur["version"], "status": "draft",
              "changed_fields": sorted(patch.keys()), "change_request": change,
              "image_prompt_derived": synced}
    if replaced:
        result["replaced_lists"] = replaced
        result["replaced_lists_warning"] = (
            "这些列表被整体替换：补丁里没重发的条目已经不存在。"
            "改局部时请把完整列表一起提交，或确认删除是有意的。")
    dump(result)


def cmd_prompt_sync(a) -> None:
    """Report or repair drift between `image_prompt` and the authoritative prompt.

    `--check` never writes: exit 6 when the *current* version has drifted (that is
    the release gate). Historical versions are listed separately — they are frozen
    evidence of what was actually rendered, so rewriting them would be a lie.
    Without `--check` each drifted design gets a new version whose only change is
    the mirror field.
    """
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    ids = [a.id] if a.id else sorted(state["designs"])
    rows, drifted, history = [], [], {}
    for did in ids:
        rec = state["designs"].get(did)
        if not rec:
            die("design_not_found", design_id=did, available=list(state["designs"]))
        versions = []
        for v in rec["versions"]:
            info = prompt_drift(v["spec"])
            info.update({"version": v["version"], "status": v["status"]})
            versions.append(info)
        cur_info = versions[-1]
        stale = [x["version"] for x in versions
                 if x["derived"] and not x["in_sync"]]
        if stale:
            history[did] = stale
        rows.append({"design_id": did, **cur_info, "versions": versions})
        if cur_info["derived"] and not cur_info["in_sync"]:
            drifted.append(did)

    report = {
        "ok": not drifted,
        "authoritative_prompt": "module_payload.generation.prompt",
        "mirror_field": "image_prompt",
        "checked": len(rows),
        "current_drift": drifted,
        "historical_drift": history,
        "designs": rows,
        "note": ("历史版本是「当时实际拿去出图」的证据，不同步也不改写；"
                 "只有当前版本参与发布门禁。" if history else
                 "所有版本均已同步。"),
    }

    if a.check:
        dump(report)
        if drifted:
            sys.exit(6)
        return

    if not drifted:
        report["action"] = "none"
        dump(report)
        return

    applied = []
    for did in drifted:
        rec = state["designs"][did]
        cur = rec["versions"][-1]
        target = authoritative_prompt(cur["spec"])
        _, old, _, new_version, _ = _apply_revision(
            state, did, {"image_prompt": target},
            "同步顶层 image_prompt 至权威提示词 generation.prompt（派生字段，设计内容零改动）",
            a.consent)
        applied.append({"design_id": did, "from_version": old["version"],
                        "new_version": new_version,
                        "previous_version_kept": old["version"]})
    save_project(workspace, state)
    report.update({"ok": True, "current_drift": [], "action": "synced", "applied": applied})
    dump(report)


def cmd_task_add(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    rec = state["designs"].get(a.design_id)
    if not rec:
        die("design_not_found", design_id=a.design_id, available=list(state["designs"]))
    task = load_json(a.file)
    tid = task.get("task_id") or f"img_{uuid.uuid4().hex[:8]}"
    task["task_id"] = tid
    task["design_id"] = a.design_id
    task.setdefault("design_version", rec["current_version"])
    task.setdefault("execution_status", "pending")
    task.setdefault("output_path", None)
    task.setdefault("tool_used", "none")
    task["created_at"] = now()
    errors = validate(task, "image-task.schema.json")
    if errors:
        die("invalid_image_task", errors=errors)
    state["image_tasks"][tid] = task
    cur = rec["versions"][-1]
    cur.setdefault("image_task_ids", []).append(tid)
    save_project(workspace, state)
    dump({"ok": True, "task_id": tid, "design_id": a.design_id,
          "design_version": task["design_version"], "status": task["execution_status"]})


def cmd_task_done(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    task = state["image_tasks"].get(a.task_id)
    if not task:
        die("task_not_found", task_id=a.task_id, available=list(state["image_tasks"]))
    if not os.path.isfile(a.path):
        die("output_missing", path=a.path,
            hint="Do not record a path for an image that does not exist.")
    task["execution_status"] = "succeeded"
    task["output_path"] = os.path.abspath(a.path)
    task["tool_used"] = a.tool or task.get("tool_used", "other")
    task["completed_at"] = now()
    if a.fidelity_notes:
        task["fidelity_notes"] = a.fidelity_notes
    task["is_generative"] = task["tool_used"] in {"ImageGen"}
    rec = state["designs"].get(task["design_id"])
    # An approved spec stays approved: producing a render does not upgrade or downgrade it.
    if rec and rec["versions"][-1]["status"] in {"draft", "selected"}:
        rec["versions"][-1]["status"] = "generated"
        rec["versions"][-1]["spec"]["approval_status"] = "generated"
        rec["frozen"] = False
    save_project(workspace, state)
    dump({"ok": True, "task_id": a.task_id, "output_path": task["output_path"],
          "tool_used": task["tool_used"], "is_generative": task["is_generative"]})


def cmd_task_fail(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    task = state["image_tasks"].get(a.task_id)
    if not task:
        die("task_not_found", task_id=a.task_id, available=list(state["image_tasks"]))
    task["execution_status"] = "skipped_no_tool" if a.no_tool else "failed"
    task["output_path"] = None
    task["tool_used"] = "none"
    task["fidelity_notes"] = a.reason
    task["completed_at"] = now()
    save_project(workspace, state)
    dump({"ok": True, "task_id": a.task_id, "execution_status": task["execution_status"],
          "reason": a.reason, "output_path": None,
          "reminder": "Tell the user the image was not produced and deliver the prompt instead."})


def cmd_link_report(a) -> None:
    """Attach a local image-processing report (cutout/composite/colour) to a design."""
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    rec = state["designs"].get(a.design_id)
    if not rec:
        die("design_not_found", design_id=a.design_id)
    report = load_json(a.report)
    entry = {
        "report": report,
        "image": os.path.abspath(a.image) if a.image else None,
        "linked_at": now(),
    }
    rec["versions"][-1].setdefault("local_reports", []).append(entry)
    save_project(workspace, state)
    dump({"ok": True, "design_id": a.design_id, "linked": entry["image"],
          "method": report.get("method") or report.get("extraction_method")})


def cmd_export(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    if a.design_id:
        rec = state["designs"].get(a.design_id)
        if not rec:
            die("design_not_found", design_id=a.design_id)
        tasks = {t: v for t, v in state["image_tasks"].items() if v["design_id"] == a.design_id}
        dump({"ok": True, "project_id": state["project_id"], "design": rec, "image_tasks": tasks})
        return
    dump({"ok": True, "state": state})


def cmd_verify(a) -> None:
    workspace = os.path.abspath(a.dir)
    state = load_project(workspace)
    problems = []
    for did, rec in state["designs"].items():
        if not rec["versions"]:
            problems.append(f"{did}: no versions")
            continue
        nums = [v["version"] for v in rec["versions"]]
        if nums != sorted(nums) or len(set(nums)) != len(nums):
            problems.append(f"{did}: version history broken {nums}")
        for v in rec["versions"]:
            if v["status"] not in STATUSES:
                problems.append(f"{did} v{v['version']}: bad status {v['status']}")
    for tid, task in state["image_tasks"].items():
        if task["execution_status"] == "succeeded" and not task.get("output_path"):
            problems.append(f"{tid}: succeeded without output_path")
        if task.get("output_path") and not os.path.isfile(task["output_path"]):
            problems.append(f"{tid}: output file missing at {task['output_path']}")
        if task["execution_status"] in {"failed", "skipped_no_tool"} and task.get("output_path"):
            problems.append(f"{tid}: failed task carries an output_path")
    errors = validate(state, "project-state.schema.json")
    problems.extend(f"schema: {e}" for e in errors)
    # 提示词漂移不列为 problem：历史项目里存在合法的旧版本，会把 verify 变成永久的红。
    # 它由 `prompt-sync --check` 单独把关，这里只提示。
    drift = []
    for did, rec in state["designs"].items():
        if not rec.get("versions"):
            continue
        info = prompt_drift(rec["versions"][-1]["spec"])
        if info["derived"] and not info["in_sync"]:
            drift.append(did)
    dump({"ok": not problems, "problems": problems,
          "designs": len(state["designs"]), "image_tasks": len(state["image_tasks"]),
          "prompt_drift": drift,
          **({"prompt_drift_note": "当前版本 image_prompt 与 generation.prompt 不一致，"
                                   "跑 prompt-sync --check 查看详情"} if drift else {})})
    if problems:
        sys.exit(6)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="MuseLab project state manager")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--dir", required=True); p.add_argument("--title", required=True)
    p.add_argument("--project-id"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_init)

    p = sub.add_parser("profile-add"); p.add_argument("--dir", required=True); p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_profile_add)

    p = sub.add_parser("design-add"); p.add_argument("--dir", required=True); p.add_argument("--file", required=True)
    p.set_defaults(fn=cmd_design_add)

    p = sub.add_parser("design-list"); p.add_argument("--dir", required=True); p.set_defaults(fn=cmd_design_list)

    p = sub.add_parser("design-show"); p.add_argument("--dir", required=True); p.add_argument("--id", required=True)
    p.add_argument("--version", type=int); p.set_defaults(fn=cmd_design_show)

    p = sub.add_parser("status"); p.add_argument("--dir", required=True); p.add_argument("--id", required=True)
    p.add_argument("--set", required=True); p.add_argument("--note"); p.set_defaults(fn=cmd_status)

    p = sub.add_parser("revise"); p.add_argument("--dir", required=True); p.add_argument("--id", required=True)
    p.add_argument("--file", required=True); p.add_argument("--change-request")
    p.add_argument("--consent", action="store_true", help="user explicitly approved changing a frozen design")
    p.set_defaults(fn=cmd_revise)

    p = sub.add_parser("prompt-sync", help="report/repair image_prompt drift against the payload prompt")
    p.add_argument("--dir", required=True); p.add_argument("--id")
    p.add_argument("--check", action="store_true", help="只报告不写；当前版本不同步时 exit 6")
    p.add_argument("--consent", action="store_true", help="允许为已定稿设计生成同步版本")
    p.set_defaults(fn=cmd_prompt_sync)

    p = sub.add_parser("task-add"); p.add_argument("--dir", required=True); p.add_argument("--design-id", required=True)
    p.add_argument("--file", required=True); p.set_defaults(fn=cmd_task_add)

    p = sub.add_parser("task-done"); p.add_argument("--dir", required=True); p.add_argument("--task-id", required=True)
    p.add_argument("--path", required=True); p.add_argument("--tool"); p.add_argument("--fidelity-notes")
    p.set_defaults(fn=cmd_task_done)

    p = sub.add_parser("task-fail"); p.add_argument("--dir", required=True); p.add_argument("--task-id", required=True)
    p.add_argument("--reason", required=True); p.add_argument("--no-tool", action="store_true")
    p.set_defaults(fn=cmd_task_fail)

    p = sub.add_parser("link-report"); p.add_argument("--dir", required=True); p.add_argument("--design-id", required=True)
    p.add_argument("--report", required=True); p.add_argument("--image"); p.set_defaults(fn=cmd_link_report)

    p = sub.add_parser("export"); p.add_argument("--dir", required=True); p.add_argument("--design-id")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("verify"); p.add_argument("--dir", required=True); p.set_defaults(fn=cmd_verify)
    return ap


if __name__ == "__main__":
    args = build_parser().parse_args()
    args.fn(args)
