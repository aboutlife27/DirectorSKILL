#!/usr/bin/env python3
"""验证电影项目状态中的结构、继承、破例和镜头连续性。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


Issue = dict[str, str]
TRUSTED_OVERRIDE_REASON_CATEGORIES = frozenset(
    {"narrative_turn", "pov_break", "world_rule_change"}
)


def _issue(severity: str, code: str, path: str, message: str) -> Issue:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
    }


def _validate_overrides(
    scene: dict[str, Any],
    scene_path: str,
    immutable: dict[str, Any],
    scene_ids: list[str],
    scene_lookup: dict[str, dict[str, Any]],
    issues: list[Issue],
) -> list[dict[str, Any]]:
    overrides = scene.get("overrides", [])
    if not isinstance(overrides, list):
        issues.append(
            _issue("error", "invalid_overrides", f"{scene_path}.overrides", "overrides 必须是数组。")
        )
        return []

    valid: list[dict[str, Any]] = []
    scene_id = scene.get("id")
    seen_fields: set[str] = set()
    for index, override in enumerate(overrides):
        path = f"{scene_path}.overrides[{index}]"
        is_valid = True
        if not isinstance(override, dict):
            issues.append(_issue("error", "invalid_override", path, "视觉破例必须是对象。"))
            continue
        missing = [
            key
            for key in ("field", "value", "reason_category", "reason", "scope", "restore_at")
            if key not in override or override[key] in (None, "", [])
        ]
        if "restore_at" in missing:
            issues.append(
                _issue(
                    "error",
                    "override_missing_restore",
                    path,
                    "视觉破例必须声明恢复点 restore_at。",
                )
            )
        other_missing = [key for key in missing if key != "restore_at"]
        if other_missing:
            issues.append(
                _issue(
                    "error",
                    "invalid_override",
                    path,
                    f"视觉破例缺少字段：{', '.join(other_missing)}。",
                )
            )
            continue
        if "restore_at" in missing:
            continue

        field = override["field"]
        if not isinstance(field, str) or field not in immutable:
            issues.append(
                _issue("error", "invalid_override_field", f"{path}.field", "破例字段必须属于不可变核心。")
            )
            continue
        if field in seen_fields:
            issues.append(
                _issue("error", "duplicate_override_field", f"{path}.field", "同一场景的同一字段只能登记一次破例。")
            )
            continue
        seen_fields.add(field)

        category = override["reason_category"]
        if not isinstance(category, str) or category not in TRUSTED_OVERRIDE_REASON_CATEGORIES:
            issues.append(
                _issue(
                    "error",
                    "invalid_override_reason_category",
                    f"{path}.reason_category",
                    "reason_category 只允许 narrative_turn、pov_break 或 world_rule_change。",
                )
            )
            is_valid = False

        reason = override["reason"]
        if not isinstance(reason, str) or not reason.strip():
            issues.append(
                _issue("error", "invalid_override_reason", f"{path}.reason", "reason 必须是非空字符串。")
            )
            is_valid = False

        scope = override["scope"]
        scope_is_structural = (
            isinstance(scope, list)
            and bool(scope)
            and all(isinstance(item, str) and item for item in scope)
        )
        scope_indices = [scene_ids.index(item) for item in scope] if scope_is_structural and all(item in scene_ids for item in scope) else []
        scope_is_contiguous = bool(scope_indices) and scope_indices == list(
            range(scope_indices[0], scope_indices[0] + len(scope_indices))
        )
        if not scope_is_structural or scope[0] != scene_id or not scope_is_contiguous:
            issues.append(
                _issue(
                    "error",
                    "override_scope_mismatch",
                    f"{path}.scope",
                    "scope 必须从当前场景开始，并按项目顺序列出连续且真实存在的场景 ID。",
                )
            )
            is_valid = False

        if scope_is_contiguous:
            declaration_act = scene.get("act")
            if any(scene_lookup[scope_id].get("act") != declaration_act for scope_id in scope):
                issues.append(
                    _issue(
                        "error",
                        "override_scope_crosses_act",
                        f"{path}.scope",
                        "单个视觉破例的 scope 不得跨幕；跨幕后必须重新登记并占用该幕预算。",
                    )
                )
                is_valid = False

        restore_at = override["restore_at"]
        expected_restore_index = scope_indices[-1] + 1 if scope_is_contiguous else None
        restore_is_valid = (
            isinstance(restore_at, str)
            and restore_at in scene_lookup
            and expected_restore_index is not None
            and expected_restore_index < len(scene_ids)
            and scene_ids[expected_restore_index] == restore_at
        )
        if not restore_is_valid:
            issues.append(
                _issue(
                    "error",
                    "invalid_override_restore",
                    f"{path}.restore_at",
                    "restore_at 必须是 scope 后紧邻且真实存在的场景 ID。",
                )
            )
            is_valid = False

        if scope_is_contiguous:
            for scope_id in scope:
                scope_inherit = scene_lookup[scope_id].get("inherit")
                if not isinstance(scope_inherit, dict) or scope_inherit.get(field) != override["value"]:
                    issues.append(
                        _issue(
                            "error",
                            "override_value_mismatch",
                            f"{path}.value",
                            f"{field} 的破例 value 必须等于 scope 内每个场景的实际继承值。",
                        )
                    )
                    is_valid = False
                    break

        if restore_is_valid:
            restore_inherit = scene_lookup[restore_at].get("inherit")
            if not isinstance(restore_inherit, dict) or restore_inherit.get(field) != immutable[field]:
                issues.append(
                    _issue(
                        "error",
                        "override_not_restored",
                        f"{path}.restore_at",
                        f"{restore_at} 必须显式把 {field} 恢复为视觉宪法值。",
                    )
                )
                is_valid = False

        if is_valid:
            valid.append(override)
    return valid


def _apply_state_resets(
    scene: dict[str, Any], scene_path: str, lineage: dict[str, Any], issues: list[Issue]
) -> dict[str, Any]:
    current = dict(lineage)
    resets = scene.get("state_resets", [])
    if not isinstance(resets, list):
        issues.append(
            _issue("error", "invalid_state_resets", f"{scene_path}.state_resets", "state_resets 必须是数组。")
        )
        return current

    for index, reset in enumerate(resets):
        path = f"{scene_path}.state_resets[{index}]"
        if not isinstance(reset, dict):
            issues.append(_issue("error", "invalid_state_reset", path, "场间状态重置必须是对象。"))
            continue
        field = reset.get("field")
        reason = reset.get("reason")
        missing = [key for key in ("field", "value", "reason") if key not in reset or reset[key] == ""]
        malformed = not isinstance(field, str) or not field.strip() or not isinstance(reason, str) or not reason.strip()
        if missing or malformed:
            issues.append(
                _issue(
                    "error",
                    "invalid_state_reset",
                    path,
                    "场间状态重置必须包含非空字符串 field、任意类型 value 和非空字符串 reason。",
                )
            )
            continue
        current[field] = reset["value"]
    return current


def _validate_shot_continuity(
    scene: dict[str, Any],
    scene_path: str,
    lineage: dict[str, Any],
    issues: list[Issue],
) -> dict[str, Any]:
    shots = scene.get("shots", [])
    if not isinstance(shots, list):
        issues.append(_issue("error", "invalid_shots", f"{scene_path}.shots", "shots 必须是数组。"))
        return lineage

    current = dict(lineage)
    for index, shot in enumerate(shots):
        path = f"{scene_path}.shots[{index}]"
        if not isinstance(shot, dict):
            issues.append(_issue("error", "invalid_shot", path, "镜头必须是对象。"))
            continue

        status = shot.get("status")
        if not isinstance(status, str) or status not in {"planned", "accepted", "rejected", "repair"}:
            issues.append(
                _issue(
                    "error",
                    "invalid_shot_status",
                    f"{path}.status",
                    "镜头状态必须是 planned、accepted、rejected 或 repair。",
                )
            )
            continue

        required_states = ["planned_start", "planned_end"]
        if status == "accepted":
            required_states.extend(["observed_start", "observed_end"])
        invalid_state = False
        for field in required_states:
            if not isinstance(shot.get(field), dict) or not shot.get(field):
                code = (
                    "accepted_shot_missing_observation"
                    if status == "accepted" and field.startswith("observed_")
                    else "invalid_shot_state"
                )
                issues.append(
                    _issue(
                        "error",
                        code,
                        f"{path}.{field}",
                        f"{field} 必须是非空对象。",
                    )
                )
                invalid_state = True

        starts = [("planned_start", shot.get("planned_start"))]
        if status == "accepted":
            starts.append(("observed_start", shot.get("observed_start")))
        for start_field, start in starts:
            if isinstance(start, dict) and current:
                missing = [key for key in current if key not in start]
                changed = [key for key, value in current.items() if key in start and start[key] != value]
                mismatches = sorted(set(missing + changed))
            else:
                mismatches = []
            if mismatches:
                fields = ", ".join(mismatches)
                issues.append(
                    _issue(
                        "error",
                        "shot_state_discontinuity",
                        f"{path}.{start_field}",
                        f"{start_field} 没有继承最近已验收实际终点：{fields}。",
                    )
                )
        if status == "accepted" and not invalid_state:
            current.update(shot["observed_end"])

    return current


def validate_project_state(state: Any) -> list[Issue]:
    """返回按严重度分类的问题；不修改输入状态。"""
    issues: list[Issue] = []
    if not isinstance(state, dict):
        return [_issue("error", "invalid_root", "$", "项目状态根节点必须是对象。")]

    constitution = state.get("visual_constitution")
    if not isinstance(constitution, dict):
        return [
            _issue(
                "error",
                "missing_visual_constitution",
                "$.visual_constitution",
                "长片项目必须有唯一视觉宪法。",
            )
        ]

    immutable = constitution.get("immutable_core", {})
    if not isinstance(immutable, dict) or not immutable:
        issues.append(
            _issue(
                "error",
                "missing_immutable_core",
                "$.visual_constitution.immutable_core",
                "视觉宪法必须声明不可变核心。",
            )
        )
        immutable = {}

    arc = constitution.get("arc", [])
    if not isinstance(arc, list):
        issues.append(
            _issue("error", "invalid_arc", "$.visual_constitution.arc", "视觉弧 arc 必须是数组。")
        )
        arc = []
    arc_ids = {
        item.get("id") for item in arc if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for phase in ("open", "rise", "turn", "climax", "release"):
        if phase not in arc_ids:
            issues.append(
                _issue(
                    "error",
                    "missing_arc_phase",
                    "$.visual_constitution.arc",
                    f"视觉弧缺少 {phase} 阶段。",
                )
            )

    exception_budget = constitution.get("exception_budget", {})
    if not isinstance(exception_budget, dict):
        issues.append(
            _issue(
                "error",
                "invalid_exception_budget",
                "$.visual_constitution.exception_budget",
                "exception_budget 必须是对象。",
            )
        )
        exception_budget = {}
    per_act = exception_budget.get("per_act", 1)
    if isinstance(per_act, bool) or not isinstance(per_act, int) or per_act < 0:
        issues.append(
            _issue(
                "error",
                "invalid_exception_budget",
                "$.visual_constitution.exception_budget.per_act",
                "per_act 必须是大于等于 0 的整数。",
            )
        )
        per_act = 1
    configured_categories = exception_budget.get(
        "allowed_reason_categories",
        sorted(TRUSTED_OVERRIDE_REASON_CATEGORIES),
    )
    if (
        not isinstance(configured_categories, list)
        or not configured_categories
        or any(not isinstance(item, str) or not item for item in configured_categories)
    ):
        issues.append(
            _issue(
                "error",
                "invalid_override_reason_categories",
                "$.visual_constitution.exception_budget.allowed_reason_categories",
                "allowed_reason_categories 必须是非空字符串数组。",
            )
        )
        configured_categories = sorted(TRUSTED_OVERRIDE_REASON_CATEGORIES)
    if set(configured_categories) != TRUSTED_OVERRIDE_REASON_CATEGORIES:
        issues.append(
            _issue(
                "error",
                "untrusted_override_reason_categories",
                "$.visual_constitution.exception_budget.allowed_reason_categories",
                "允许的破例理由由验证器契约固定，项目状态不能自行扩张或缩减。",
            )
        )

    scenes = state.get("scenes", [])
    if not isinstance(scenes, list):
        issues.append(_issue("error", "invalid_scenes", "$.scenes", "scenes 必须是数组。"))
        return issues

    scene_ids: list[str] = []
    scene_lookup: dict[str, dict[str, Any]] = {}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id:
            issues.append(
                _issue("error", "invalid_scene_id", f"$.scenes[{index}].id", "场景 ID 必须是非空字符串。")
            )
            continue
        if scene_id in scene_lookup:
            issues.append(
                _issue("error", "duplicate_scene_id", f"$.scenes[{index}].id", "场景 ID 必须唯一。")
            )
            continue
        scene_ids.append(scene_id)
        scene_lookup[scene_id] = scene

    exception_counts: dict[Any, int] = {}
    overrides_by_scene: dict[str, list[dict[str, Any]]] = {}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict) or scene.get("id") not in scene_lookup:
            continue
        path = f"$.scenes[{index}]"
        valid_overrides = _validate_overrides(
            scene, path, immutable, scene_ids, scene_lookup, issues
        )
        act = scene.get("act", "unknown")
        budget_act = act if isinstance(act, (str, int)) and not isinstance(act, bool) else f"unknown-{index}"
        exception_counts[budget_act] = exception_counts.get(budget_act, 0) + len(valid_overrides)
        for override in valid_overrides:
            field = override["field"]
            for scope_id in override["scope"]:
                existing_fields = {item["field"] for item in overrides_by_scene.get(scope_id, [])}
                if field in existing_fields:
                    issues.append(
                        _issue(
                            "error",
                            "overlapping_override_field",
                            path,
                            f"{scope_id} 的 {field} 同时被多个破例覆盖。",
                        )
                    )
                    continue
                overrides_by_scene.setdefault(scope_id, []).append(override)

    lineage: dict[str, Any] = {}
    for index, scene in enumerate(scenes):
        path = f"$.scenes[{index}]"
        if not isinstance(scene, dict):
            issues.append(_issue("error", "invalid_scene", path, "场景必须是对象。"))
            continue

        valid_overrides = overrides_by_scene.get(scene.get("id"), [])
        act = scene.get("act", "unknown")
        if isinstance(act, bool) or not isinstance(act, (str, int)):
            issues.append(_issue("error", "invalid_act", f"{path}.act", "act 必须是字符串或整数。"))
            act = f"unknown-{index}"

        inherit = scene.get("inherit", {})
        if not isinstance(inherit, dict):
            issues.append(
                _issue("error", "invalid_inherit", f"{path}.inherit", "inherit 必须是对象。")
            )
        else:
            missing_inherit = [field for field in immutable if field not in inherit]
            if missing_inherit:
                issues.append(
                    _issue(
                        "error",
                        "missing_immutable_inheritance",
                        f"{path}.inherit",
                        f"场景必须显式继承全部不可变核心字段：{', '.join(missing_inherit)}。",
                    )
                )
            overrides_by_field = {
                item["field"]: item
                for item in valid_overrides
                if isinstance(item.get("field"), str)
            }
            for field, value in inherit.items():
                if field not in immutable or value == immutable[field]:
                    continue
                override = overrides_by_field.get(field)
                if override is None:
                    issues.append(
                        _issue(
                            "error",
                            "immutable_core_drift",
                            f"{path}.inherit.{field}",
                            f"{field} 偏离视觉宪法，且没有登记视觉破例。",
                        )
                    )
                elif override.get("value") != value:
                    issues.append(
                        _issue(
                            "error",
                            "override_value_mismatch",
                            f"{path}.overrides",
                            f"{field} 的破例 value 必须等于场景实际继承值。",
                        )
                    )

        lineage = _apply_state_resets(scene, path, lineage, issues)
        lineage = _validate_shot_continuity(scene, path, lineage, issues)

    for act, count in exception_counts.items():
        if isinstance(per_act, int) and count > per_act:
            issues.append(
                _issue(
                    "error",
                    "exception_budget_exceeded",
                    "$.scenes",
                    f"第 {act} 幕使用 {count} 次视觉破例，超过预算 {per_act}。",
                )
            )

    return issues


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法：validate_project_state.py <project-state.json>", file=sys.stderr)
        return 2

    path = Path(argv[1])
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2

    issues = validate_project_state(state)
    print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))
    return 1 if any(item["severity"] == "error" for item in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
