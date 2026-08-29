import tempfile
import unittest
from pathlib import Path

from tests.test_production_control_project import valid_plan

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402


def submit_and_accept(service, project_dir, task_id, content=None):
    packet = service.next_task("codex")
    if packet["task"]["id"] != task_id:
        raise AssertionError(f"预期任务 {task_id}，实际为 {packet['task']['id']}")
    artifact = project_dir / f"{task_id}.bin"
    artifact.write_bytes(content or task_id.encode("utf-8"))
    candidate = service.submit_candidate(
        packet["run_id"],
        artifact,
        {"model": "test-model", "prompt": f"生成 {task_id}", "seed": 7},
    )
    service.review_candidate(candidate["candidate_id"], "approve", "导演", "符合当前阶段目标")
    return candidate


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tempdir.name) / "feature"
        self.service = ProductionService.create(self.project_dir, "回声", "feature-01")
        self.service.import_plan(valid_plan())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_gate_cannot_be_approved_before_evidence_is_accepted(self):
        with self.assertRaisesRegex(ProductionError, "证据任务尚未完成"):
            self.service.approve_gate(
                "visual_constitution", "导演", "", human_confirmed=True
            )

    def test_gate_cannot_be_approved_out_of_order(self):
        with self.assertRaisesRegex(ProductionError, "前序审批门"):
            self.service.approve_gate("core_assets", "导演", "", human_confirmed=True)

    def test_approval_unlocks_only_the_next_interval(self):
        submit_and_accept(self.service, self.project_dir, "visual")

        result = self.service.approve_gate(
            "visual_constitution", "导演", "视觉原则已锁定", human_confirmed=True
        )
        status = self.service.status()

        self.assertEqual(result["status"], "approved")
        self.assertEqual(status["ready_tasks"], ["assets"])
        self.assertEqual(status["tasks_by_status"], {"blocked": 3, "completed": 1, "ready": 1})
        self.assertEqual(status["gates"][0]["reviewer"], "导演")
        self.assertTrue(status["gates"][0]["evidence_hash"])

    def test_approved_gate_cannot_be_approved_twice(self):
        submit_and_accept(self.service, self.project_dir, "visual")
        self.service.approve_gate(
            "visual_constitution", "导演", "首次批准", human_confirmed=True
        )

        with self.assertRaisesRegex(ProductionError, "已经批准"):
            self.service.approve_gate(
                "visual_constitution", "导演", "重复批准", human_confirmed=True
            )

    def test_gate_requires_explicit_human_confirmation(self):
        submit_and_accept(self.service, self.project_dir, "visual")

        for confirmation in (False, "false", 1, None):
            with self.subTest(confirmation=confirmation):
                with self.assertRaisesRegex(ProductionError, "人工确认"):
                    self.service.approve_gate(
                        "visual_constitution",
                        "导演",
                        "自动批准",
                        human_confirmed=confirmation,
                    )

    def test_revised_evidence_invalidates_gate_and_downstream(self):
        submit_and_accept(self.service, self.project_dir, "visual", b"visual-v1")
        self.service.approve_gate("visual_constitution", "导演", "v1", human_confirmed=True)
        submit_and_accept(self.service, self.project_dir, "assets", b"assets-v1")
        self.service.approve_gate("core_assets", "导演", "v1", human_confirmed=True)

        self.service.retry_task("visual", "视觉方向调整")
        submit_and_accept(self.service, self.project_dir, "visual", b"visual-v2")
        status = self.service.status()

        self.assertEqual(status["gates"][0]["status"], "invalidated")
        self.assertEqual(status["gates"][1]["status"], "invalidated")
        self.assertEqual(status["task_statuses"]["assets"], "stale")
        self.assertEqual(status["task_statuses"]["pilot"], "stale")

    def test_revised_indirect_dependency_invalidates_approved_gate(self):
        plan = valid_plan()
        plan["tasks"].insert(
            1,
            {
                "id": "character",
                "kind": "character_asset",
                "stage": "assets",
                "depends_on": ["visual"],
                "required_gate": "visual_constitution",
                "inputs": {"character": "lead"},
                "output_contract": {"media_type": "application/json", "purpose": "角色资产"},
            },
        )
        plan["tasks"][2]["depends_on"] = ["character"]
        other_dir = Path(self.tempdir.name) / "indirect"
        service = ProductionService.create(other_dir, "回声", "feature-01")
        service.import_plan(plan)
        submit_and_accept(service, other_dir, "visual")
        service.approve_gate("visual_constitution", "导演", "v1", human_confirmed=True)
        submit_and_accept(service, other_dir, "character", b"character-v1")
        submit_and_accept(service, other_dir, "assets", b"assets-v1")
        service.approve_gate("core_assets", "导演", "v1", human_confirmed=True)

        service.retry_task("character", "角色资产调整")
        submit_and_accept(service, other_dir, "character", b"character-v2")

        status = service.status()
        self.assertEqual(status["gates"][1]["status"], "invalidated")
        self.assertEqual(status["task_statuses"]["assets"], "stale")

    def test_stale_pending_candidate_cannot_be_approved(self):
        submit_and_accept(self.service, self.project_dir, "visual", b"visual-v1")
        self.service.approve_gate(
            "visual_constitution", "导演", "v1", human_confirmed=True
        )
        packet = self.service.next_task("codex")
        artifact = self.project_dir / "assets-pending.bin"
        artifact.write_bytes(b"assets-v1")
        candidate = self.service.submit_candidate(packet["run_id"], artifact, {"model": "test"})

        self.service.retry_task("visual", "视觉调整")
        submit_and_accept(self.service, self.project_dir, "visual", b"visual-v2")

        with self.assertRaisesRegex(ProductionError, "输入|失效|状态"):
            self.service.review_candidate(candidate["candidate_id"], "approve", "导演")
        self.assertEqual(self.service.status()["task_statuses"]["assets"], "stale")


if __name__ == "__main__":
    unittest.main()
