import json
import re
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
STAGE_FIELDS = {
    "id",
    "name",
    "trigger",
    "decision",
    "constraints",
    "actions",
    "observations",
    "rollback",
    "gate",
    "artifacts",
}


class FeatureWorkflowAssetsTest(unittest.TestCase):
    def test_hell_grind_audit_separates_evidence_and_transfer_limits(self):
        path = ROOT / "references" / "hell-grind-workflow.md"
        text = path.read_text(encoding="utf-8")

        for heading in (
            "## 证据分级",
            "## 已核验的公开工作流",
            "## 15 段镜头提示骨架",
            "## 可迁移机制",
            "## 不直接继承的做法",
            "## 局限与未知",
            "## 来源",
        ):
            self.assertIn(heading, text)
        self.assertIn("2026-08-29", text)
        self.assertIn("Marché du Film", text)
        self.assertIn("生成窗口", text)
        self.assertIn("不等同于端到端制作周期", text)
        for source_id in ("A-HF-PAGE", "A-CINEDANCE", "A-ACTING", "A-LIRA", "B-NEBIUS", "C-CINED"):
            self.assertIn(source_id, text)

        transfer_section = text.split("## 可迁移机制", 1)[1].split("## 不直接继承的做法", 1)[0]
        transfer_rows = [line for line in transfer_section.splitlines() if line.startswith("|")][2:]
        self.assertGreaterEqual(len(transfer_rows), 8)
        for row in transfer_rows:
            self.assertRegex(row, r"\[(?:A|B|C|D)-[A-Z-]+\]", row)

        for start, end in (
            ("### 项目边界", "### 公开附件"),
            ("### 从资产到镜头", "### 表演、声音与后期"),
            ("### 表演、声音与后期", "## 15 段镜头提示骨架"),
            ("## 不直接继承的做法", "## 局限与未知"),
            ("## 局限与未知", "## 来源"),
        ):
            section = text.split(start, 1)[1].split(end, 1)[0]
            claims = [line for line in section.splitlines() if re.match(r"^(?:-|\d+\.) ", line)]
            self.assertTrue(claims, start)
            for claim in claims:
                self.assertRegex(claim, r"^(?:-|\d+\.) `\[(?:A|B|C|D|U)(?:-[A-Z-]+)?\]", claim)
        for claim in (
            "`[A-HF-PAGE]` 项目页在核验时提供三类独立附件",
            "`[A-HF-PAGE][U]` 官方页还提到制作简报",
            "`[A-CINEDANCE][D-TRANSFER]` 顺序的价值",
        ):
            self.assertIn(claim, text)

    def test_playbook_has_eleven_closed_loop_stages(self):
        path = ROOT / "references" / "ai-feature-production-playbook.md"
        text = path.read_text(encoding="utf-8")
        stage_parts = re.split(r"(?=^## 阶段 \d+：)", text, flags=re.MULTILINE)[1:]
        stages = [part.splitlines()[0] for part in stage_parts]

        self.assertEqual(11, len(stages))
        self.assertEqual([f"## 阶段 {index}：" for index in range(11)], [stage.split("：", 1)[0] + "：" for stage in stages])
        for label in ("触发", "判定", "约束", "执行", "观测", "回滚", "阶段门", "产物"):
            for index, stage in enumerate(stage_parts):
                self.assertEqual(1, stage.count(f"**{label}：**"), f"阶段 {index} / {label}")
        self.assertIn("不可变核心", text)
        self.assertIn("已验收实际终点", text)
        self.assertIn("单变量", text)
        self.assertIn("连续 10-15 次", text)
        self.assertIn("阶段 0-5 必须在集中生成窗口开始前通过", text)
        self.assertIn("两周生成窗口从阶段 6 的首镜证明开始", text)
        self.assertIn("主要覆盖阶段 6-8", text)
        self.assertIn("若窗口已开始而阶段 0-5 未通过，立即降低交付等级并补齐前置门", text)

    def test_runbook_is_machine_readable_and_complete(self):
        path = ROOT / "assets" / "ai-feature-production-runbook.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))

        self.assertEqual("2.2.0", data["schema_version"])
        self.assertEqual(11, len(data["stages"]))
        self.assertIn("evidence_policy", data)
        self.assertIn("consistency_contract", data)
        self.assertIn("iteration_policy", data)
        self.assertIn("daily_metrics", data)
        window = data["project"]["generation_window_policy"]
        self.assertEqual([f"S{index:02d}" for index in range(6)], window["prerequisites"])
        self.assertEqual("S06", window["starts_at"])
        self.assertEqual(["S06", "S07", "S08"], window["primary_scope"])
        self.assertIn("S00-S05 未通过时不得宣布集中生成窗口开始", window["rule"])
        self.assertIn("降低交付等级并补齐前置门", window["rule"])
        self.assertEqual([f"S{index:02d}" for index in range(11)], [stage["id"] for stage in data["stages"]])
        self.assertEqual(11, len({stage["id"] for stage in data["stages"]}))
        for stage in data["stages"]:
            self.assertTrue(STAGE_FIELDS.issubset(stage), stage.get("id"))
            for field in STAGE_FIELDS - {"id"}:
                self.assertTrue(stage[field], f"{stage['id']} / {field}")

    def test_runbook_uses_the_canonical_project_state_contract(self):
        runbook = yaml.safe_load(
            (ROOT / "assets" / "ai-feature-production-runbook.yaml").read_text(encoding="utf-8")
        )
        state = json.loads(
            (ROOT / "assets" / "project-state-template.json").read_text(encoding="utf-8")
        )

        self.assertEqual("1.0", runbook["consistency_contract"]["state_schema_version"])
        self.assertEqual(
            state["visual_constitution"]["immutable_core"],
            runbook["consistency_contract"]["immutable_core"],
        )

    def test_skill_routes_case_audit_and_feature_execution(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("references/hell-grind-workflow.md", text)
        self.assertIn("references/ai-feature-production-playbook.md", text)
        self.assertIn("assets/ai-feature-production-runbook.yaml", text)
        self.assertIn("Hell Grind", text)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for asset in (
            "ai-feature-production-runbook.yaml",
            "film-visual-constitution.yaml",
            "project-state-template.json",
        ):
            self.assertIn(asset, readme)

    def test_skill_routes_private_source_ingestion(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        workflow = ROOT / "references" / "source-ingestion-workflow.md"

        self.assertTrue(workflow.is_file())
        self.assertIn("references/source-ingestion-workflow.md", skill)
        self.assertIn("来源继承", skill)
        text = workflow.read_text(encoding="utf-8")
        for heading in (
            "## 道：来源不是正史",
            "## 法：四层来源协议",
            "## 术：可验证导入循环",
            "## 器：Huobao 导入命令",
            "## 候选晋升规则",
            "## 私有边界",
        ):
            self.assertIn(heading, text)
        self.assertIn("--verify-only", text)
        self.assertIn("import_status: candidate", text)

    def test_all_director_templates_use_current_pipeline_steps(self):
        style_files = sorted((ROOT / "references" / "director_styles").glob("[0-9][0-9]_*.md"))
        self.assertEqual(20, len(style_files))
        old_labels = ("Step 5 导演本", "Step 7 分镜", "Step 8，接图像模型", "Step 10，接关键帧")
        new_labels = ("Step 7 导演本", "Step 9 分镜", "Step 10，接图像模型", "Step 12，接关键帧")
        for path in style_files:
            text = path.read_text(encoding="utf-8")
            for label in old_labels:
                self.assertNotIn(label, text, f"{path.name} / {label}")
            for label in new_labels:
                self.assertIn(label, text, f"{path.name} / {label}")

    def test_evals_cover_audit_and_runbook(self):
        data = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        ids = {case["id"] for case in data["evals"]}

        self.assertEqual("2.3.0", data["version"])
        self.assertIn("hell-grind-workflow-audit", ids)
        self.assertIn("feature-film-production-runbook", ids)
        self.assertIn("codex-production-control", ids)
        cases = {case["id"]: case for case in data["evals"]}
        self.assertEqual(["references/hell-grind-workflow.md"], cases["hell-grind-workflow-audit"]["loads"])
        self.assertIn("assets/ai-feature-production-runbook.yaml", cases["feature-film-production-runbook"]["loads"])
        for case_id in ("hell-grind-workflow-audit", "feature-film-production-runbook"):
            self.assertTrue(cases[case_id]["prompt"])
            self.assertTrue(cases[case_id]["expected_output"])
            self.assertGreaterEqual(len(cases[case_id]["assertions"]), 6)
            self.assertTrue(all(cases[case_id]["assertions"]))


if __name__ == "__main__":
    unittest.main()
