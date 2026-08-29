import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_production_control_project import valid_plan


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "production_control_cli.py"


class ProductionCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.project = self.root / "feature"

    def tearDown(self):
        self.tempdir.cleanup()

    def run_cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, str(CLI), *map(str, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, expected, msg=result.stderr or result.stdout)
        output = result.stdout if result.stdout else result.stderr
        return json.loads(output)

    def test_codex_can_complete_first_gate_using_json_commands(self):
        created = self.run_cli("init", self.project, "--title", "回声", "--project-id", "feature-01")
        self.assertTrue(created["ok"])

        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(valid_plan(), ensure_ascii=False), encoding="utf-8")
        self.run_cli("plan", self.project, plan_path)
        packet = self.run_cli("next", self.project, "--executor", "codex")["result"]

        artifact = self.root / "visual.json"
        artifact.write_text('{"palette":"cyan-gray"}', encoding="utf-8")
        submitted = self.run_cli(
            "submit",
            self.project,
            "--run-id",
            packet["run_id"],
            "--artifact",
            artifact,
            "--metadata",
            '{"model":"test","prompt":"视觉宪法","seed":1}',
        )["result"]
        self.run_cli(
            "review",
            self.project,
            "--candidate-id",
            submitted["candidate_id"],
            "--decision",
            "approve",
            "--reviewer",
            "导演",
        )
        self.run_cli(
            "approve-gate",
            self.project,
            "visual_constitution",
            "--reviewer",
            "导演",
            "--human-confirmed",
        )
        status = self.run_cli("status", self.project)["result"]

        self.assertEqual(status["ready_tasks"], ["assets"])

    def test_business_error_is_machine_readable(self):
        self.run_cli("init", self.project, "--title", "回声", "--project-id", "feature-01")

        error = self.run_cli("next", self.project, "--executor", "codex", expected=1)

        self.assertFalse(error["ok"])
        self.assertEqual(error["error"]["code"], "no_ready_task")

    def test_malformed_plan_is_a_machine_readable_business_error(self):
        self.run_cli("init", self.project, "--title", "回声", "--project-id", "feature-01")
        plan_path = self.root / "malformed-plan.json"
        plan = valid_plan()
        plan["project"] = None
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        error = self.run_cli("plan", self.project, plan_path, expected=1)

        self.assertEqual(error["error"]["code"], "invalid_plan")


if __name__ == "__main__":
    unittest.main()
