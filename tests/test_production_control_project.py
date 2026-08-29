import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402


GATES = ["visual_constitution", "core_assets", "pilot_shots", "picture_lock"]


def valid_plan():
    tasks = [
        {
            "id": "visual",
            "kind": "visual_constitution",
            "stage": "development",
            "depends_on": [],
            "required_gate": None,
            "inputs": {"brief": "story.md"},
            "output_contract": {"media_type": "application/json", "purpose": "视觉宪法"},
        },
        {
            "id": "assets",
            "kind": "core_assets",
            "stage": "assets",
            "depends_on": ["visual"],
            "required_gate": "visual_constitution",
            "inputs": {"characters": ["lead"]},
            "output_contract": {"media_type": "application/json", "purpose": "核心资产清单"},
        },
        {
            "id": "pilot",
            "kind": "pilot_shots",
            "stage": "pilot",
            "depends_on": ["assets"],
            "required_gate": "core_assets",
            "inputs": {"shots": ["S01-01"]},
            "output_contract": {"media_type": "video/mp4", "purpose": "样片"},
        },
        {
            "id": "rough-cut",
            "kind": "rough_cut",
            "stage": "production",
            "depends_on": ["pilot"],
            "required_gate": "pilot_shots",
            "inputs": {"timeline": "main"},
            "output_contract": {"media_type": "video/mp4", "purpose": "粗剪"},
        },
        {
            "id": "final",
            "kind": "final_export",
            "stage": "delivery",
            "depends_on": ["rough-cut"],
            "required_gate": "picture_lock",
            "inputs": {"preset": "master"},
            "output_contract": {"media_type": "video/mp4", "purpose": "母版"},
        },
    ]
    evidence = {
        "visual_constitution": ["visual"],
        "core_assets": ["assets"],
        "pilot_shots": ["pilot"],
        "picture_lock": ["rough-cut"],
    }
    return {
        "schema_version": "1.0",
        "project": {"id": "feature-01", "title": "回声"},
        "continuity_state": "project-state.json",
        "tasks": tasks,
        "gates": [{"id": gate, "evidence_tasks": evidence[gate]} for gate in GATES],
    }


class ProductionProjectTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tempdir.name) / "feature"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_initializes_local_project(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")

        self.assertTrue((self.project_dir / ".production" / "production.db").is_file())
        self.assertEqual(service.status()["project"]["title"], "回声")
        self.assertEqual(service.status()["event_count"], 1)

    def test_create_refuses_existing_project(self):
        ProductionService.create(self.project_dir, "回声", "feature-01")

        with self.assertRaisesRegex(ProductionError, "项目已经初始化"):
            ProductionService.create(self.project_dir, "回声", "feature-01")

    def test_run_cannot_reference_unknown_task(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")

        with self.assertRaises(sqlite3.IntegrityError), service.store.connect() as connection:
            connection.execute(
                "INSERT INTO runs(task_id, attempt, executor, status, input_hash, packet_json, "
                "lease_until, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("missing", 1, "codex", "leased", "hash", "{}", "9999", "now"),
            )

    def test_imports_valid_plan_and_only_first_task_is_ready(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")

        result = service.import_plan(valid_plan())
        status = service.status()

        self.assertEqual(result["task_count"], 5)
        self.assertEqual(status["tasks_by_status"], {"blocked": 4, "ready": 1})
        self.assertEqual(status["ready_tasks"], ["visual"])
        self.assertEqual([gate["id"] for gate in status["gates"]], GATES)

    def test_import_rejects_plan_for_another_project(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["project"]["id"] = "another-feature"

        with self.assertRaisesRegex(ProductionError, "项目 ID"):
            service.import_plan(plan)

        self.assertEqual(service.status()["tasks_by_status"], {})

    def test_import_rejects_unknown_dependency(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["tasks"][1]["depends_on"] = ["missing"]

        with self.assertRaisesRegex(ProductionError, "不存在的依赖"):
            service.import_plan(plan)

    def test_import_rejects_dependency_cycle(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["tasks"][0]["depends_on"] = ["assets"]

        with self.assertRaisesRegex(ProductionError, "循环依赖"):
            service.import_plan(plan)

    def test_import_requires_exactly_four_ordered_gates(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["gates"] = plan["gates"][:3]

        with self.assertRaisesRegex(ProductionError, "四个审批门"):
            service.import_plan(plan)

    def test_import_rejects_malformed_nested_types_as_business_errors(self):
        mutations = [
            lambda plan: plan["tasks"][0].__setitem__("id", []),
            lambda plan: plan["tasks"][0].__setitem__("kind", []),
            lambda plan: plan["tasks"][0].__setitem__("stage", None),
            lambda plan: plan["tasks"][0].__setitem__("depends_on", {}),
            lambda plan: plan["tasks"][0].__setitem__("required_gate", []),
            lambda plan: plan["tasks"][0].__setitem__("output_contract", []),
            lambda plan: plan["gates"][0].__setitem__("evidence_tasks", [[]]),
        ]

        for mutate in mutations:
            with self.subTest(mutation=mutate):
                project_dir = self.project_dir.parent / f"feature-{id(mutate)}"
                service = ProductionService.create(project_dir, "回声", "feature-01")
                plan = valid_plan()
                mutate(plan)

                with self.assertRaises(ProductionError):
                    service.import_plan(plan)

    def test_gate_evidence_cannot_depend_on_the_gate_it_is_meant_to_unlock(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["gates"][1]["evidence_tasks"] = ["pilot"]

        with self.assertRaisesRegex(ProductionError, "前一审批门"):
            service.import_plan(plan)

    def test_failed_import_is_atomic(self):
        service = ProductionService.create(self.project_dir, "回声", "feature-01")
        plan = valid_plan()
        plan["tasks"][2]["depends_on"] = ["missing"]

        with self.assertRaises(ProductionError):
            service.import_plan(plan)

        self.assertEqual(service.status()["tasks_by_status"], {})
        self.assertEqual(service.status()["gates"], [])


if __name__ == "__main__":
    unittest.main()
