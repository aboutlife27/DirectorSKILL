import os
import hashlib
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from tests.test_production_control_project import valid_plan

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402
from production_control.media import import_artifact as real_import_artifact  # noqa: E402


class ProductionRunTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.tempdir.name) / "feature"
        self.service = ProductionService.create(self.project_dir, "回声", "feature-01")
        (self.project_dir / "project-state.json").write_text('{"version":1}', encoding="utf-8")
        self.service.import_plan(valid_plan())

    def tearDown(self):
        self.tempdir.cleanup()

    def test_next_returns_complete_codex_packet_and_leases_atomically(self):
        packet = self.service.next_task("codex", lease_seconds=600)

        self.assertEqual(packet["task"]["id"], "visual")
        self.assertEqual(packet["task"]["output_contract"]["purpose"], "视觉宪法")
        self.assertEqual(packet["executor"], "codex")
        self.assertEqual(len(packet["input_hash"]), 64)
        self.assertIn("lease_until", packet)
        with self.assertRaisesRegex(ProductionError, "没有可领取任务"):
            self.service.next_task("another")

    def test_concurrent_executors_cannot_claim_the_same_task(self):
        barrier = Barrier(2)

        def claim(executor):
            service = ProductionService(self.project_dir)
            barrier.wait()
            try:
                return service.next_task(executor)["task"]["id"]
            except ProductionError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, ("codex-a", "codex-b")))

        self.assertEqual(results.count("visual"), 1)
        self.assertEqual(results.count("no_ready_task"), 1)

    def test_continuity_state_cannot_escape_project_directory(self):
        other_project = Path(self.tempdir.name) / "unsafe-path"
        service = ProductionService.create(other_project, "回声", "feature-02")
        plan = valid_plan()
        plan["project"]["id"] = "feature-02"
        plan["continuity_state"] = "../outside.json"
        (Path(self.tempdir.name) / "outside.json").write_text("{}", encoding="utf-8")
        service.import_plan(plan)

        with self.assertRaisesRegex(ProductionError, "越出项目目录"):
            service.next_task("codex")

    def test_ingested_input_is_included_in_task_packet_and_input_hash(self):
        source = Path(self.tempdir.name) / "story.md"
        source.write_text("一个关于记忆的故事", encoding="utf-8")
        self.service.ingest_input("script", source, "screenplay")
        plan = valid_plan()

        other_project = Path(self.tempdir.name) / "with-input"
        service = ProductionService.create(other_project, "回声", "feature-02")
        service.ingest_input("script", source, "screenplay")
        plan["project"]["id"] = "feature-02"
        plan["tasks"][0]["inputs"] = {"artifacts": ["script"]}
        service.import_plan(plan)

        packet = service.next_task("codex")

        self.assertEqual(packet["references"]["input_artifacts"][0]["id"], "script")
        self.assertEqual(len(packet["references"]["input_artifacts"][0]["content_hash"]), 64)

    def test_missing_ingested_input_blocks_task_lease(self):
        other_project = Path(self.tempdir.name) / "missing-input"
        service = ProductionService.create(other_project, "回声", "feature-02")
        plan = valid_plan()
        plan["project"]["id"] = "feature-02"
        plan["tasks"][0]["inputs"] = {"artifacts": ["script"]}
        service.import_plan(plan)

        with self.assertRaisesRegex(ProductionError, "输入尚未登记"):
            service.next_task("codex")

    def test_submit_copies_artifact_by_hash_and_records_reproducibility_metadata(self):
        packet = self.service.next_task("codex")
        artifact = Path(self.tempdir.name) / "visual.json"
        artifact.write_text('{"look":"cold"}', encoding="utf-8")

        result = self.service.submit_candidate(
            packet["run_id"],
            artifact,
            {"model": "image-model", "prompt": "冷峻但保留肤色", "seed": 42},
        )

        object_path = self.project_dir / result["object_path"]
        self.assertTrue(object_path.is_file())
        self.assertEqual(object_path.read_bytes(), artifact.read_bytes())
        self.assertEqual(len(result["content_hash"]), 64)
        self.assertEqual(self.service.status()["task_statuses"]["visual"], "submitted")

    def test_submit_rejects_changed_input_snapshot(self):
        packet = self.service.next_task("codex")
        (self.project_dir / "project-state.json").write_text('{"version":2}', encoding="utf-8")
        artifact = Path(self.tempdir.name) / "visual.json"
        artifact.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(ProductionError, "输入已经变化"):
            self.service.submit_candidate(packet["run_id"], artifact, {"model": "test"})

        self.assertEqual(self.service.status()["task_statuses"]["visual"], "ready")

    def test_submit_rechecks_snapshot_after_artifact_import(self):
        packet = self.service.next_task("codex")
        artifact = Path(self.tempdir.name) / "visual.json"
        artifact.write_text("{}", encoding="utf-8")

        def import_then_change_state(*args, **kwargs):
            imported = real_import_artifact(*args, **kwargs)
            (self.project_dir / "project-state.json").write_text(
                '{"version":2}', encoding="utf-8"
            )
            return imported

        with patch("production_control.service.import_artifact", side_effect=import_then_change_state):
            with self.assertRaisesRegex(ProductionError, "输入已经变化"):
                self.service.submit_candidate(packet["run_id"], artifact, {"model": "test"})

        self.assertEqual(self.service.status()["task_statuses"]["visual"], "ready")

    def test_submit_rejects_symbolic_link(self):
        packet = self.service.next_task("codex")
        artifact = Path(self.tempdir.name) / "real.bin"
        artifact.write_bytes(b"asset")
        link = Path(self.tempdir.name) / "link.bin"
        os.symlink(artifact, link)

        with self.assertRaisesRegex(ProductionError, "符号链接"):
            self.service.submit_candidate(packet["run_id"], link, {"model": "test"})

    def test_submit_rejects_symbolic_link_in_object_store_path(self):
        packet = self.service.next_task("codex")
        artifact = Path(self.tempdir.name) / "candidate.bin"
        artifact.write_bytes(b"candidate")
        objects = self.project_dir / "media" / "objects"
        objects.rmdir()
        outside = Path(self.tempdir.name) / "outside-objects"
        outside.mkdir()
        os.symlink(outside, objects)

        with self.assertRaisesRegex(ProductionError, "素材库路径"):
            self.service.submit_candidate(packet["run_id"], artifact, {"model": "test"})

        self.assertEqual(list(outside.iterdir()), [])

    def test_interrupted_object_copy_leaves_no_partial_object(self):
        artifact = Path(self.tempdir.name) / "candidate.bin"
        artifact.write_bytes(b"candidate")
        content_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()

        with patch("production_control.media.shutil.copyfileobj", side_effect=OSError("中断")):
            with self.assertRaisesRegex(ProductionError, "不可写"):
                real_import_artifact(self.project_dir, artifact)

        prefix = self.project_dir / "media" / "objects" / content_hash[:2]
        self.assertEqual(list(prefix.iterdir()), [])

    def test_source_open_failure_closes_temporary_descriptor(self):
        artifact = Path(self.tempdir.name) / "candidate.bin"
        artifact.write_bytes(b"candidate")
        content_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        original_os_open = os.open
        temporary_descriptors = []

        def track_temporary_open(*args, **kwargs):
            descriptor = original_os_open(*args, **kwargs)
            flags = args[1] if len(args) > 1 else kwargs["flags"]
            if flags & os.O_CREAT:
                temporary_descriptors.append(descriptor)
            return descriptor

        with patch("production_control.media.os.open", side_effect=track_temporary_open):
            with patch("production_control.media.sha256_file", return_value=content_hash):
                with patch.object(type(artifact), "open", side_effect=OSError("源文件已消失")):
                    with self.assertRaisesRegex(ProductionError, "不可写"):
                        real_import_artifact(self.project_dir, artifact)

        prefix = self.project_dir / "media" / "objects" / content_hash[:2]
        self.assertEqual(len(temporary_descriptors), 1)
        try:
            os.fstat(temporary_descriptors[0])
        except OSError:
            descriptor_closed = True
        else:
            descriptor_closed = False
            os.close(temporary_descriptors[0])
        self.assertTrue(descriptor_closed)
        self.assertEqual(list(prefix.iterdir()), [])

    def test_concurrent_imports_publish_one_complete_object(self):
        artifact = Path(self.tempdir.name) / "candidate.bin"
        artifact.write_bytes(b"candidate" * 1024)
        barrier = Barrier(2)

        def import_once(_):
            barrier.wait()
            return real_import_artifact(self.project_dir, artifact)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(import_once, range(2)))

        self.assertEqual(results[0]["content_hash"], results[1]["content_hash"])
        object_path = self.project_dir / results[0]["object_path"]
        self.assertEqual(object_path.read_bytes(), artifact.read_bytes())
        self.assertEqual([path.name for path in object_path.parent.iterdir()], [object_path.name])

    def test_rejecting_only_candidate_returns_task_to_ready(self):
        packet = self.service.next_task("codex")
        artifact = Path(self.tempdir.name) / "candidate.bin"
        artifact.write_bytes(b"candidate")
        candidate = self.service.submit_candidate(packet["run_id"], artifact, {"model": "test"})

        self.service.review_candidate(candidate["candidate_id"], "reject", "导演", "构图偏离")

        self.assertEqual(self.service.status()["task_statuses"]["visual"], "ready")
        self.assertEqual(self.service.status()["candidate_counts"], {"rejected": 1})


if __name__ == "__main__":
    unittest.main()
