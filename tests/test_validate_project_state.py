import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_project_state import validate_project_state  # noqa: E402


def valid_state():
    return {
        "schema_version": "1.0",
        "project": {"id": "feature-01", "title": "回声", "format": "feature"},
        "visual_constitution": {
            "immutable_core": {
                "aspect_ratio": "2.39:1",
                "lens_family": [32, 50, 75],
                "camera_support": ["tripod", "dolly"],
                "framing_rule": "解释权决定中心权",
                "palette_roles": {
                    "base": "低饱和青灰",
                    "accent": "警示琥珀",
                },
                "lighting_logic": "单侧动机光，暗侧不主动补平",
                "texture": "自然高光与细颗粒",
                "spatial_rule": "权力优先在同框空间表达",
                "performance_register": "内收",
                "edit_rule": "硬切，按信息变化切",
                "sound_rule": "连续环境底",
            },
            "arc": [
                {"id": "open", "position": 0.0},
                {"id": "rise", "position": 0.25},
                {"id": "turn", "position": 0.5},
                {"id": "climax", "position": 0.75},
                {"id": "release", "position": 1.0},
            ],
                "exception_budget": {
                    "per_act": 1,
                    "required_fields": [
                        "field",
                        "value",
                        "reason_category",
                        "reason",
                        "scope",
                        "restore_at",
                    ],
                    "allowed_reason_categories": ["narrative_turn", "pov_break", "world_rule_change"],
                },
        },
        "scenes": [
            {
                "id": "S01",
                "act": 1,
                "arc_position": 0.1,
                "inherit": {
                    "aspect_ratio": "2.39:1",
                    "lens_family": [32, 50, 75],
                    "camera_support": ["tripod", "dolly"],
                    "framing_rule": "解释权决定中心权",
                    "palette_roles": {"base": "低饱和青灰", "accent": "警示琥珀"},
                    "lighting_logic": "单侧动机光，暗侧不主动补平",
                    "texture": "自然高光与细颗粒",
                    "spatial_rule": "权力优先在同框空间表达",
                    "performance_register": "内收",
                    "edit_rule": "硬切，按信息变化切",
                    "sound_rule": "连续环境底",
                },
                "overrides": [],
                "shots": [
                    {
                        "id": "S01-01",
                        "status": "accepted",
                        "planned_start": {"prop": "信封在右手"},
                        "planned_end": {"prop": "信封在桌上"},
                        "observed_start": {"prop": "信封在右手"},
                        "observed_end": {"prop": "信封在桌上"},
                    },
                    {
                        "id": "S01-02",
                        "status": "planned",
                        "planned_start": {"prop": "信封在桌上"},
                        "planned_end": {"prop": "信封被父亲拿起"},
                    },
                ],
            }
        ],
    }


def contract_shape(value):
    if isinstance(value, dict):
        return {key: contract_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [contract_shape(item) for item in value]
    return type(value).__name__


def append_scene(state, scene_id):
    scene = copy.deepcopy(state["scenes"][0])
    scene["id"] = scene_id
    scene["shots"] = []
    scene["overrides"] = []
    state["scenes"].append(scene)
    return scene


class ValidateProjectStateTests(unittest.TestCase):
    def test_json_template_mirrors_yaml_visual_constitution_contract(self):
        template = json.loads((ROOT / "assets" / "project-state-template.json").read_text(encoding="utf-8"))
        source = yaml.safe_load(
            (ROOT / "assets" / "film-visual-constitution.yaml").read_text(encoding="utf-8")
        )
        template_constitution = template["visual_constitution"]
        source_constitution = source["visual_constitution"]

        self.assertEqual(contract_shape(template_constitution), contract_shape(source_constitution))
        self.assertEqual(template_constitution["immutable_core"], source_constitution["immutable_core"])
        self.assertEqual(template_constitution["exception_budget"], source_constitution["exception_budget"])

    def test_valid_feature_state_has_no_errors(self):
        issues = validate_project_state(valid_state())
        self.assertEqual([i for i in issues if i["severity"] == "error"], [])

    def test_missing_visual_constitution_is_an_error(self):
        state = valid_state()
        del state["visual_constitution"]

        issues = validate_project_state(state)

        self.assertIn("missing_visual_constitution", {i["code"] for i in issues})

    def test_exception_without_restore_point_is_an_error(self):
        state = valid_state()
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "身份真相使秩序崩解",
                "scope": ["S01"],
            }
        ]

        issues = validate_project_state(state)

        self.assertIn("override_missing_restore", {i["code"] for i in issues})

    def test_scene_cannot_omit_immutable_inheritance(self):
        state = valid_state()
        state["scenes"][0]["inherit"] = {}

        issues = validate_project_state(state)

        self.assertIn("missing_immutable_inheritance", {i["code"] for i in issues})

    def test_override_value_must_match_effective_scene_value(self):
        state = valid_state()
        append_scene(state, "S02")
        state["scenes"][0]["inherit"]["lens_family"] = [18, 24]
        state["scenes"][0]["overrides"] = [
            {
                "field": "lens_family",
                "value": [32, 50, 75],
                "reason_category": "narrative_turn",
                "reason": "认知翻转改变空间压缩",
                "scope": ["S01"],
                "restore_at": "S02",
            }
        ]

        issues = validate_project_state(state)

        self.assertIn("override_value_mismatch", {i["code"] for i in issues})

    def test_valid_override_must_restore_at_the_next_real_scene(self):
        state = valid_state()
        append_scene(state, "S02")
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "身份真相使秩序崩解",
                "scope": ["S01"],
                "restore_at": "S02",
            }
        ]

        issues = validate_project_state(state)

        self.assertEqual([], [i for i in issues if i["severity"] == "error"])

    def test_valid_override_can_cover_multiple_contiguous_scenes(self):
        state = valid_state()
        second = append_scene(state, "S02")
        append_scene(state, "S03")
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        second["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "身份真相持续破坏控制感",
                "scope": ["S01", "S02"],
                "restore_at": "S03",
            }
        ]

        issues = validate_project_state(state)

        self.assertEqual([], [i for i in issues if i["severity"] == "error"])

    def test_override_scope_cannot_cross_act(self):
        state = valid_state()
        second = append_scene(state, "S02")
        second["act"] = 2
        append_scene(state, "S03")["act"] = 2
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        second["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "主观失控跨越幕边界",
                "scope": ["S01", "S02"],
                "restore_at": "S03",
            }
        ]

        issues = validate_project_state(state)

        self.assertIn("override_scope_crosses_act", {i["code"] for i in issues})

    def test_state_cannot_define_its_own_override_reason_category(self):
        state = valid_state()
        append_scene(state, "S02")
        state["visual_constitution"]["exception_budget"]["allowed_reason_categories"] = ["anything"]
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "anything",
                "reason": "任意理由",
                "scope": ["S01"],
                "restore_at": "S02",
            }
        ]

        codes = {i["code"] for i in validate_project_state(state)}

        self.assertIn("untrusted_override_reason_categories", codes)
        self.assertIn("invalid_override_reason_category", codes)

    def test_override_scope_must_use_existing_contiguous_scenes(self):
        state = valid_state()
        append_scene(state, "S02")
        append_scene(state, "S03")
        append_scene(state, "S04")
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][2]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "身份真相使秩序崩解",
                "scope": ["S01", "S03"],
                "restore_at": "S04",
            }
        ]

        codes = {i["code"] for i in validate_project_state(state)}

        self.assertIn("override_scope_mismatch", codes)

    def test_override_restore_scene_must_exist_and_restore_canonical_value(self):
        state = valid_state()
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "pov_break",
                "reason": "身份真相使秩序崩解",
                "scope": ["S01"],
                "restore_at": "S99",
            }
        ]

        codes = {i["code"] for i in validate_project_state(state)}
        self.assertIn("invalid_override_restore", codes)

        restore_scene = append_scene(state, "S99")
        restore_scene["inherit"]["camera_support"] = ["handheld"]
        codes = {i["code"] for i in validate_project_state(state)}
        self.assertIn("override_not_restored", codes)

    def test_override_requires_allowed_reason_category_and_current_scope(self):
        state = valid_state()
        state["scenes"][0]["inherit"]["camera_support"] = ["handheld"]
        state["scenes"][0]["overrides"] = [
            {
                "field": "camera_support",
                "value": ["handheld"],
                "reason_category": "looks_cool",
                "reason": "更有冲击力",
                "scope": ["S99"],
                "restore_at": "S99",
            }
        ]

        issues = validate_project_state(state)
        codes = {i["code"] for i in issues}

        self.assertIn("invalid_override_reason_category", codes)
        self.assertIn("override_scope_mismatch", codes)
        self.assertIn("invalid_override_restore", codes)

    def test_next_shot_must_start_from_last_accepted_observed_end(self):
        state = valid_state()
        state["scenes"][0]["shots"][1]["planned_start"] = {"prop": "信封仍在右手"}

        issues = validate_project_state(state)

        self.assertIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_accepted_shot_plan_must_start_from_last_accepted_observed_end(self):
        state = valid_state()
        state["scenes"][0]["shots"][1] = {
            "id": "S01-02",
            "status": "accepted",
            "planned_start": {"prop": "信封仍在右手"},
            "planned_end": {"prop": "信封被父亲拿起"},
            "observed_start": {"prop": "信封在桌上"},
            "observed_end": {"prop": "信封被父亲拿起"},
        }

        issues = validate_project_state(state)

        discontinuities = [i for i in issues if i["code"] == "shot_state_discontinuity"]
        self.assertEqual(1, len(discontinuities))
        self.assertTrue(discontinuities[0]["path"].endswith(".planned_start"))

    def test_next_shot_cannot_omit_a_carried_state_field(self):
        state = valid_state()
        state["scenes"][0]["shots"][1]["planned_start"] = {}

        issues = validate_project_state(state)

        self.assertIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_rejected_shot_does_not_become_a_continuity_ancestor(self):
        state = valid_state()
        scene = state["scenes"][0]
        scene["shots"].insert(
            1,
            {
                "id": "S01-R1",
                "status": "rejected",
                "planned_start": {"prop": "信封在桌上"},
                "planned_end": {"prop": "信封掉在地上"},
            },
        )

        issues = validate_project_state(state)

        self.assertNotIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_inheriting_a_rejected_shot_end_is_a_discontinuity(self):
        state = valid_state()
        scene = state["scenes"][0]
        scene["shots"].insert(
            1,
            {
                "id": "S01-R1",
                "status": "rejected",
                "planned_start": {"prop": "信封在桌上"},
                "planned_end": {"prop": "信封掉在地上"},
            },
        )
        scene["shots"][2]["planned_start"] = {"prop": "信封掉在地上"}

        issues = validate_project_state(state)

        self.assertIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_state_carries_across_scene_boundaries(self):
        state = valid_state()
        state["scenes"].append(
            {
                "id": "S02",
                "act": 1,
                "arc_position": 0.2,
                "inherit": dict(state["visual_constitution"]["immutable_core"]),
                "overrides": [],
                "state_resets": [],
                "shots": [
                    {
                        "id": "S02-01",
                        "status": "planned",
                        "planned_start": {"prop": "信封仍在右手"},
                        "planned_end": {"prop": "信封被撕开"},
                    }
                ],
            }
        )

        issues = validate_project_state(state)

        self.assertIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_explicit_state_reset_allows_a_new_scene_state(self):
        state = valid_state()
        state["scenes"].append(
            {
                "id": "S02",
                "act": 1,
                "arc_position": 0.2,
                "inherit": dict(state["visual_constitution"]["immutable_core"]),
                "overrides": [],
                "state_resets": [
                    {"field": "prop", "value": "信封已归档", "reason": "场间经过一天"}
                ],
                "shots": [
                    {
                        "id": "S02-01",
                        "status": "planned",
                        "planned_start": {"prop": "信封已归档"},
                        "planned_end": {"prop": "信封已归档"},
                    }
                ],
            }
        )

        issues = validate_project_state(state)

        self.assertNotIn("shot_state_discontinuity", {i["code"] for i in issues})

    def test_accepted_shot_requires_observed_start_and_end(self):
        state = valid_state()
        del state["scenes"][0]["shots"][0]["observed_end"]

        issues = validate_project_state(state)

        self.assertIn("accepted_shot_missing_observation", {i["code"] for i in issues})

    def test_malformed_overrides_and_shots_return_issues_instead_of_crashing(self):
        state = valid_state()
        state["scenes"][0]["overrides"] = ["bad"]
        state["scenes"][0]["shots"] = ["bad"]

        issues = validate_project_state(state)

        codes = {i["code"] for i in issues}
        self.assertIn("invalid_override", codes)
        self.assertIn("invalid_shot", codes)

    def test_all_structural_fields_reject_malformed_types(self):
        mutations = [
            ("invalid_exception_budget", lambda state: state["visual_constitution"].update(exception_budget=[])),
            ("invalid_act", lambda state: state["scenes"][0].update(act=[])),
            ("invalid_inherit", lambda state: state["scenes"][0].update(inherit=[])),
            (
                "invalid_state_reset",
                lambda state: state["scenes"][0].update(
                    state_resets=[{"field": [], "value": "x", "reason": "跨日"}]
                ),
            ),
            (
                "invalid_shot_status",
                lambda state: state["scenes"][0]["shots"][0].update(status=[]),
            ),
        ]

        for expected_code, mutate in mutations:
            with self.subTest(expected_code=expected_code):
                state = valid_state()
                mutate(state)
                issues = validate_project_state(state)
                self.assertIn(expected_code, {i["code"] for i in issues})

    def test_visual_constitution_requires_five_arc_phases(self):
        state = valid_state()
        state["visual_constitution"]["arc"] = [
            item for item in state["visual_constitution"]["arc"] if item["id"] != "climax"
        ]

        issues = validate_project_state(state)

        self.assertIn("missing_arc_phase", {i["code"] for i in issues})

    def test_immutable_core_drift_requires_a_logged_override(self):
        state = valid_state()
        state["scenes"][0]["inherit"]["lens_family"] = [18, 24]

        issues = validate_project_state(state)

        self.assertIn("immutable_core_drift", {i["code"] for i in issues})

    def test_cli_returns_one_when_errors_exist(self):
        state = valid_state()
        del state["visual_constitution"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.json"
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_project_state.py"), str(path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing_visual_constitution", result.stdout)


if __name__ == "__main__":
    unittest.main()
