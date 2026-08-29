import json
import tempfile
import unittest
from pathlib import Path

from tests.test_production_control_gates import submit_and_accept
from tests.test_production_control_project import GATES, valid_plan

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402


class ProductionRecoveryExportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tempdir.name) / "feature"
        self.service = ProductionService.create(self.project_dir, "回声", "feature-01")
        self.service.import_plan(valid_plan())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_recover_releases_only_expired_runs(self):
        self.service.next_task("codex", lease_seconds=3600)
        self.assertEqual(self.service.recover("2000-01-01T00:00:00+00:00")["recovered"], 0)

        result = self.service.recover("9999-01-01T00:00:00+00:00")

        self.assertEqual(result["recovered"], 1)
        self.assertEqual(self.service.status()["task_statuses"]["visual"], "ready")
        self.assertEqual(self.service.status()["run_counts"], {"interrupted": 1})

    def test_export_is_blocked_until_all_gates_and_tasks_complete(self):
        with self.assertRaisesRegex(ProductionError, "尚未完成"):
            self.service.export_delivery()

    def test_complete_project_exports_traceable_manifest(self):
        tasks = ["visual", "assets", "pilot", "rough-cut"]
        for task_id, gate in zip(tasks, GATES):
            submit_and_accept(self.service, self.project_dir, task_id)
            self.service.approve_gate(gate, "导演", f"批准 {gate}", human_confirmed=True)
        submit_and_accept(self.service, self.project_dir, "final")

        result = self.service.export_delivery()
        manifest = json.loads((self.project_dir / result["manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual([item["id"] for item in manifest["gates"]], GATES)
        self.assertEqual(len(manifest["accepted_candidates"]), 5)
        self.assertGreaterEqual(manifest["event_count"], 20)
        self.assertEqual(manifest["project"]["id"], "feature-01")
        self.assertTrue(manifest["manifest_hash"])


if __name__ == "__main__":
    unittest.main()
