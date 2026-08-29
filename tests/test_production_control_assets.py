import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402


class ProductionControlAssetTests(unittest.TestCase):
    def test_skill_routes_one_click_production_to_control_mode(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("制片控制模式（K）", skill)
        self.assertIn("references/production-control-plane.md", skill)
        self.assertIn("assets/production-plan-template.json", skill)
        for command in (
            "init",
            "ingest",
            "plan",
            "next",
            "submit",
            "review",
            "approve-gate",
            "status",
            "recover",
            "export",
        ):
            self.assertIn(f"production_control_cli.py {command}", skill)

    def test_plan_template_is_accepted_by_control_kernel(self):
        plan_path = ROOT / "assets" / "production-plan-template.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service = ProductionService.create(
                project_dir, plan["project"]["title"], plan["project"]["id"]
            )

            result = service.import_plan(plan)

        self.assertGreaterEqual(result["task_count"], 10)
        self.assertEqual(result["gate_count"], 4)

    def test_reference_defines_three_sources_of_truth_and_human_gates(self):
        reference = (ROOT / "references" / "production-control-plane.md").read_text(
            encoding="utf-8"
        )

        for phrase in ("执行账本", "创作连续性", "媒体内容", "视觉宪法", "核心资产", "样片镜头", "画面锁定"):
            self.assertIn(phrase, reference)
        self.assertIn("不能代替用户批准", reference)

    def test_input_ids_are_unique(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service = ProductionService.create(project_dir, "回声", "feature-01")
            source = project_dir / "story.md"
            source.write_text("故事", encoding="utf-8")
            service.ingest_input("story", source, "剧本")

            with self.assertRaisesRegex(ProductionError, "输入 ID 已经存在"):
                service.ingest_input("story", source, "剧本")

    def test_continuity_state_rejects_symbolic_links(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service = ProductionService.create(project_dir, "回声", "feature-01")
            plan = json.loads(
                (ROOT / "assets" / "production-plan-template.json").read_text(encoding="utf-8")
            )
            plan["project"] = {"id": "feature-01", "title": "回声"}
            service.import_plan(plan)
            target = project_dir / "continuity-real.json"
            target.write_text("{}\n", encoding="utf-8")
            (project_dir / plan["continuity_state"]).symlink_to(target.name)

            with self.assertRaisesRegex(ProductionError, "符号链接"):
                service.next_task("codex")


if __name__ == "__main__":
    unittest.main()
