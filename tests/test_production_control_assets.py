import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from production_control import ProductionError, ProductionService  # noqa: E402


class ProductionControlAssetTests(unittest.TestCase):
    @staticmethod
    def _sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _canonical_sha256(value):
        return hashlib.sha256(
            json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

    def _create_knowledge_ready_report(self, project_dir, corpus_sha256=None):
        if corpus_sha256 is None:
            corpus_sha256 = json.loads(
                (project_dir / "source-manifest.json").read_text(encoding="utf-8")
            )["corpus_sha256"]
        chapter_path = project_dir / "source/private/snapshot/chapters/CH0001.md"
        chapter_content = chapter_path.read_text(encoding="utf-8")
        knowledge_dir = project_dir / "knowledge-base"
        graph_dir = project_dir / "graphify-out"
        memory_dir = project_dir / ".agent-memory" / "meta"
        knowledge_dir.mkdir(parents=True)
        graph_dir.mkdir(parents=True)
        memory_dir.mkdir(parents=True)

        database_path = knowledge_dir / "knowledge.db"
        with sqlite3.connect(database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE chapters(
                    chapter_id TEXT PRIMARY KEY, ordinal INTEGER, title TEXT, source_file TEXT,
                    file_sha256 TEXT, content_sha256 TEXT, reliable INTEGER, content TEXT
                );
                CREATE TABLE entities(
                    entity_id TEXT PRIMARY KEY, entity_type TEXT, canonical_name TEXT,
                    status TEXT, source_file TEXT, source_record_sha256 TEXT
                );
                CREATE TABLE entity_aliases(entity_id TEXT, alias TEXT);
                CREATE TABLE mentions(
                    entity_id TEXT, chapter_id TEXT, mention_count INTEGER,
                    first_offset INTEGER, confidence TEXT
                );
                CREATE TABLE documents(
                    document_id TEXT PRIMARY KEY, title TEXT, source_file TEXT
                );
                CREATE TABLE document_mentions(
                    entity_id TEXT, document_id TEXT, mention_count INTEGER,
                    confidence TEXT
                );
                CREATE VIRTUAL TABLE chapter_fts USING fts5(chapter_id, title, content);
                """
            )
            connection.executemany(
                "INSERT INTO metadata VALUES(?, ?)",
                (
                    ("corpus_sha256", json.dumps(corpus_sha256)),
                    ("reliable_end", json.dumps(1)),
                ),
            )
            connection.execute(
                "INSERT INTO chapters VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "CH0001",
                    1,
                    "第一章",
                    "source/private/snapshot/chapters/CH0001.md",
                    self._sha256(chapter_path),
                    hashlib.sha256(chapter_content.encode("utf-8")).hexdigest(),
                    1,
                    chapter_content,
                ),
            )
            connection.execute(
                "INSERT INTO chapter_fts VALUES('CH0001', '第一章', ?)",
                (chapter_content,),
            )
            connection.execute(
                "INSERT INTO entities VALUES("
                "'character:1', 'character', '齐夏', 'candidate', "
                "'imports/candidates.json', 'record-1')"
            )
            connection.execute(
                "INSERT INTO mentions VALUES('character:1', 'CH0001', 1, ?, 'EXTRACTED')",
                (chapter_content.find("齐夏"),),
            )

        graph_path = graph_dir / "graph.json"
        graph_path.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": "chapter:CH0001",
                            "label": "CH0001 第一章",
                            "file_type": "document",
                            "node_kind": "chapter",
                            "source_file": "source/private/snapshot/chapters/CH0001.md",
                            "source_location": "CH0001",
                        },
                        {
                            "id": "character:1",
                            "label": "齐夏",
                            "file_type": "concept",
                            "node_kind": "character",
                            "source_file": "imports/candidates.json",
                            "source_location": "record-1",
                            "status": "candidate",
                        },
                    ],
                    "links": [
                        {
                            "source": "character:1",
                            "target": "chapter:CH0001",
                            "relation": "mentioned_in",
                            "confidence": "EXTRACTED",
                            "confidence_score": 1.0,
                            "weight": 1,
                            "source_file": "source/private/snapshot/chapters/CH0001.md",
                            "source_location": str(chapter_content.find("齐夏")),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        memory_path = memory_dir / "index.sqlite"
        with sqlite3.connect(memory_path) as connection:
            connection.execute("CREATE TABLE memory_docs(id TEXT PRIMARY KEY)")

        report = {
            "schema_version": "1.0",
            "validator": "cinematic-director/narrative-knowledge-base@1",
            "status": "ready",
            "corpus_sha256": corpus_sha256,
            "reliable_end": 1,
            "checks": {
                name: True
                for name in (
                    "source_hashes",
                    "stable_ids",
                    "reliable_scope_isolated",
                    "search",
                    "entity",
                    "related",
                    "database_integrity",
                    "memory_health",
                )
            },
            "artifacts": {
                "knowledge_db": {
                    "path": "knowledge-base/knowledge.db",
                    "sha256": self._sha256(database_path),
                },
                "graph": {
                    "path": "graphify-out/graph.json",
                    "sha256": self._sha256(graph_path),
                },
                "memory_index": {
                    "path": ".agent-memory/meta/index.sqlite",
                    "sha256": self._sha256(memory_path),
                },
            },
        }
        report_path = knowledge_dir / "knowledge-ready-report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return report_path

    def _lease_knowledge_task(self, project_dir):
        plan = json.loads(
            (ROOT / "assets" / "production-plan-template.json").read_text(encoding="utf-8")
        )
        service = ProductionService.create(
            project_dir, plan["project"]["title"], plan["project"]["id"]
        )
        service.import_plan(plan)
        source = project_dir / "complete-script-or-novel.txt"
        source.write_text("完整原文", encoding="utf-8")
        service.ingest_input("complete-script-or-novel", source, "完整原文")
        chapter = project_dir / "source/private/snapshot/chapters/CH0001.md"
        chapter.parent.mkdir(parents=True)
        chapter.write_text("齐夏在密室醒来。", encoding="utf-8")
        content_hash = hashlib.sha256(chapter.read_text(encoding="utf-8").encode()).hexdigest()
        corpus_record = {
            "stable_id": "CH0001",
            "source_episode_id": 1,
            "episode_number": 1,
            "original_title": "第一章",
            "content_sha256": content_hash,
        }
        manifest = project_dir / "source-manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "corpus_sha256": self._canonical_sha256([corpus_record]),
                    "snapshot_path": "source/private/snapshot",
                    "chapters": [
                        {
                            **corpus_record,
                            "file": "chapters/CH0001.md",
                            "file_sha256": self._sha256(chapter),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        service.ingest_input("source-manifest", manifest, "来源清单")
        return service, service.next_task("codex")

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

        self.assertGreaterEqual(result["task_count"], 11)
        self.assertEqual(result["gate_count"], 4)

    def test_plan_template_enforces_knowledge_ready_before_visual_and_assets(self):
        plan = json.loads(
            (ROOT / "assets" / "production-plan-template.json").read_text(encoding="utf-8")
        )
        tasks = {task["id"]: task for task in plan["tasks"]}
        gates = {gate["id"]: gate for gate in plan["gates"]}

        self.assertEqual([], tasks["knowledge-foundation"]["depends_on"])
        self.assertEqual(
            ["knowledge-foundation"], tasks["visual-constitution"]["depends_on"]
        )
        self.assertIn(
            "knowledge-foundation", gates["visual_constitution"]["evidence_tasks"]
        )
        for task_id in ("character-core", "scene-core", "prop-core"):
            self.assertIn("visual-constitution", tasks[task_id]["depends_on"])

    def test_knowledge_task_rejects_unverified_ready_json(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report = project_dir / "fake-ready.json"
            report.write_text('{"status":"ready"}\n', encoding="utf-8")

            with self.assertRaisesRegex(ProductionError, "Knowledge Ready"):
                service.submit_candidate(packet["run_id"], report, {"model": "fake"})

    def test_knowledge_task_accepts_semantically_verified_report(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report = self._create_knowledge_ready_report(project_dir)

            result = service.submit_candidate(
                packet["run_id"], report, {"model": "local-validator"}
            )

        self.assertEqual("application/json", result["media_type"])

    def test_knowledge_task_rejects_report_for_different_source_manifest(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report = self._create_knowledge_ready_report(project_dir, corpus_sha256="b" * 64)

            with self.assertRaisesRegex(ProductionError, "来源清单"):
                service.submit_candidate(
                    packet["run_id"], report, {"model": "local-validator"}
                )

    def test_knowledge_task_rejects_self_consistent_database_not_built_from_source(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report_path = self._create_knowledge_ready_report(project_dir)
            database_path = project_dir / "knowledge-base/knowledge.db"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "UPDATE chapters SET content = '伪造正文', content_sha256 = ? "
                    "WHERE chapter_id = 'CH0001'",
                    (hashlib.sha256("伪造正文".encode("utf-8")).hexdigest(),),
                )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["artifacts"]["knowledge_db"]["sha256"] = self._sha256(database_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(ProductionError, "当前来源清单"):
                service.submit_candidate(
                    packet["run_id"], report_path, {"model": "forged-builder"}
                )

    def test_knowledge_task_rejects_graph_not_rebuilt_from_database_evidence(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report_path = self._create_knowledge_ready_report(project_dir)
            graph_path = project_dir / "graphify-out/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["nodes"].append({"id": "character:fake", "node_kind": "character"})
            graph["links"].append(
                {
                    "source": "character:fake",
                    "target": "chapter:CH0001",
                    "relation": "mentioned_in",
                    "confidence": "EXTRACTED",
                    "weight": 1,
                    "source_file": "source/private/snapshot/chapters/CH0001.md",
                    "source_location": "0",
                }
            )
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["artifacts"]["graph"]["sha256"] = self._sha256(graph_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(ProductionError, "图谱"):
                service.submit_candidate(
                    packet["run_id"], report_path, {"model": "forged-builder"}
                )

    def test_knowledge_task_rejects_forged_graph_entity_status(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report_path = self._create_knowledge_ready_report(project_dir)
            graph_path = project_dir / "graphify-out/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            character = next(
                node for node in graph["nodes"] if node["id"] == "character:1"
            )
            character["status"] = "accepted"
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["artifacts"]["graph"]["sha256"] = self._sha256(graph_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(ProductionError, "图谱"):
                service.submit_candidate(
                    packet["run_id"], report_path, {"model": "forged-builder"}
                )

    def test_knowledge_task_rejects_forged_graph_confidence_score(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report_path = self._create_knowledge_ready_report(project_dir)
            graph_path = project_dir / "graphify-out/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["links"][0]["confidence_score"] = 0.55
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["artifacts"]["graph"]["sha256"] = self._sha256(graph_path)
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(ProductionError, "图谱"):
                service.submit_candidate(
                    packet["run_id"], report_path, {"model": "forged-builder"}
                )

    def test_knowledge_task_rejects_symbolic_link_report(self):
        with tempfile.TemporaryDirectory() as tempdir:
            project_dir = Path(tempdir) / "feature"
            service, packet = self._lease_knowledge_task(project_dir)
            report = self._create_knowledge_ready_report(project_dir)
            linked_report = project_dir / "linked-ready.json"
            linked_report.symlink_to(report.relative_to(project_dir))

            with self.assertRaisesRegex(ProductionError, "符号链接"):
                service.submit_candidate(
                    packet["run_id"], linked_report, {"model": "local-validator"}
                )

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
