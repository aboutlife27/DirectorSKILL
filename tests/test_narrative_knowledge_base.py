import importlib.util
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "narrative_knowledge_base.py"


def load_module():
    spec = importlib.util.spec_from_file_location("narrative_knowledge_base", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def canonical_sha256(value):
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class NarrativeKnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        snapshot = self.root / "source/private/snapshots/corpus/chapters"
        snapshot.mkdir(parents=True)
        chapters = [
            ("CH0001", 1, "空屋", "齐夏在密室醒来，看到圆桌。"),
            ("CH0002", 2, "说谎", "齐夏和乔家劲围绕圆桌交谈。"),
            ("CH0003", 3, "污染", "齐夏 hetushu 重复污染。"),
        ]
        manifest_chapters = []
        corpus_records = []
        for chapter_id, number, title, content in chapters:
            relative = f"chapters/{chapter_id}.md"
            chapter_path = snapshot / f"{chapter_id}.md"
            chapter_path.write_text(content, encoding="utf-8")
            corpus_record = {
                "stable_id": chapter_id,
                "source_episode_id": number,
                "episode_number": number,
                "original_title": title,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            corpus_records.append(corpus_record)
            manifest_chapters.append(
                {
                    **corpus_record,
                    "file": relative,
                    "file_sha256": hashlib.sha256(chapter_path.read_bytes()).hexdigest(),
                }
            )

        imports = self.root / "imports/huobao/corpus"
        imports.mkdir(parents=True)
        characters = {
            "records": [
                {"source_id": 1, "name": "齐夏", "record_sha256": "qixia", "import_status": "candidate"},
                {"source_id": 2, "name": "乔家劲", "record_sha256": "qiao", "import_status": "candidate"},
            ]
        }
        props = {
            "records": [
                {"source_id": 3, "name": "圆桌", "record_sha256": "table", "import_status": "candidate"}
            ]
        }
        (imports / "characters.json").write_text(json.dumps(characters), encoding="utf-8")
        (imports / "props.json").write_text(json.dumps(props), encoding="utf-8")

        manifests = self.root / "source/manifests"
        manifests.mkdir(parents=True)
        manifest = {
            "schema_version": "fixture",
            "corpus_sha256": canonical_sha256(corpus_records),
            "snapshot_path": "source/private/snapshots/corpus",
            "chapters": manifest_chapters,
            "candidate_exports": {
                "characters": {"file": "imports/huobao/corpus/characters.json"},
                "props": {"file": "imports/huobao/corpus/props.json"},
            },
        }
        (manifests / "novel-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.manifest_path = manifests / "novel-manifest.json"
        self._resign_candidates()
        bible = self.root / "development/series-bible"
        bible.mkdir(parents=True)
        (bible / "world-bible.md").write_text("密室与圆桌会跨循环复现。", encoding="utf-8")
        memory_index = self.root / ".agent-memory/meta/index.sqlite"
        memory_index.parent.mkdir(parents=True)
        with sqlite3.connect(memory_index) as connection:
            connection.execute("CREATE TABLE memory_docs(id TEXT PRIMARY KEY)")

    def tearDown(self):
        self.temp.cleanup()

    def _resign_candidates(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        candidate_records = {}
        for name, descriptor in manifest["candidate_exports"].items():
            path = self.root / descriptor["file"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["corpus_sha256"] = manifest["corpus_sha256"]
            for record in payload["records"]:
                unsigned = dict(record)
                unsigned.pop("record_sha256", None)
                record["record_sha256"] = canonical_sha256(unsigned)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            descriptor["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            descriptor["count"] = len(payload["records"])
            candidate_records[name] = payload["records"]
        manifest["candidate_set_sha256"] = canonical_sha256(candidate_records)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def test_builds_searchable_evidence_and_graph_with_reliable_scope(self):
        module = load_module()

        result = module.build_knowledge_base(self.root, reliable_end=2)

        self.assertEqual(3, result["chapter_count"])
        self.assertEqual(2, result["reliable_chapter_count"])
        self.assertEqual(3, result["entity_count"])
        self.assertTrue((self.root / "knowledge-base/knowledge.db").is_file())
        self.assertTrue((self.root / "graphify-out/graph.json").is_file())
        self.assertTrue((self.root / "graphify-out/graph.html").is_file())
        self.assertTrue((self.root / "graphify-out/GRAPH_REPORT.md").is_file())
        ready_report = json.loads(
            (self.root / "knowledge-base/knowledge-ready-report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertTrue(result["knowledge_ready"])
        self.assertEqual("ready", ready_report["status"])
        self.assertTrue(all(ready_report["checks"].values()))

        with sqlite3.connect(self.root / "knowledge-base/knowledge.db") as connection:
            mentions = connection.execute(
                "SELECT chapter_id, mention_count FROM mentions "
                "WHERE entity_id = 'character:1' ORDER BY chapter_id"
            ).fetchall()
            self.assertEqual([("CH0001", 1), ("CH0002", 1), ("CH0003", 1)], mentions)

        search = module.search_knowledge_base(self.root, "齐夏", limit=10)
        self.assertEqual(["CH0001", "CH0002"], [item["chapter_id"] for item in search["chapters"]])
        polluted = module.search_knowledge_base(
            self.root, "齐夏", limit=10, include_unreliable=True
        )
        self.assertEqual(["CH0001", "CH0002", "CH0003"], [item["chapter_id"] for item in polluted["chapters"]])

        entity = module.lookup_entity(self.root, "乔家劲")
        self.assertEqual("character:2", entity["entity"]["entity_id"])
        self.assertEqual(["CH0002"], [item["chapter_id"] for item in entity["mentions"]])

        related = module.query_knowledge_graph(self.root, "齐夏", depth=1)
        self.assertEqual(["character:1"], related["matched_node_ids"])
        self.assertIn("chapter:CH0001", {node["id"] for node in related["nodes"]})

        graph = json.loads((self.root / "graphify-out/graph.json").read_text(encoding="utf-8"))
        node_ids = {node["id"] for node in graph["nodes"]}
        self.assertIn("character:1", node_ids)
        self.assertIn("chapter:CH0002", node_ids)
        self.assertNotIn("chapter:CH0003", node_ids)

    def test_rejects_chapter_when_snapshot_hash_no_longer_matches_manifest(self):
        module = load_module()
        chapter = self.root / "source/private/snapshots/corpus/chapters/CH0001.md"
        chapter.write_text("被替换的章节", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "哈希不一致"):
            module.build_knowledge_base(self.root, reliable_end=2)

    def test_series_bible_registry_writes_back_new_entities_and_supports_short_query(self):
        module = load_module()
        props_path = self.root / "imports/huobao/corpus/props.json"
        props_path.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "source_id": 99,
                            "name": "斑驳圆桌/九桌板系统",
                            "record_sha256": "legacy-table",
                            "import_status": "candidate",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._resign_candidates()
        registry = self.root / "development/series-bible/props/prop-registry.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(
            json.dumps(
                {
                    "status": "candidate",
                    "props": [
                        {
                            "id": "PROP-ROUND-TABLE-01",
                            "name": "斑驳圆桌/九桌板系统",
                            "aliases": ["圆桌", "九桌板"],
                            "status": "spec_candidate",
                            "source": ["CH0001"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        result = module.build_knowledge_base(self.root, reliable_end=2)
        entity = module.lookup_entity(self.root, "圆桌")

        self.assertEqual(3, result["entity_count"])
        self.assertEqual("prop:PROP-ROUND-TABLE-01", entity["entity"]["entity_id"])
        self.assertEqual("spec_candidate", entity["entity"]["status"])
        self.assertEqual(
            "development/series-bible/props/prop-registry.json",
            entity["entity"]["source_file"],
        )
        self.assertEqual(["CH0001", "CH0002"], [item["chapter_id"] for item in entity["mentions"]])
        self.assertEqual(
            ["INFERRED", "AMBIGUOUS"],
            [item["confidence"] for item in entity["mentions"]],
        )

    def test_document_evidence_activates_entity_and_graph_relation(self):
        module = load_module()
        props_path = self.root / "imports/huobao/corpus/props.json"
        payload = json.loads(props_path.read_text(encoding="utf-8"))
        payload["records"].append(
            {
                "source_id": 4,
                "name": "文档专用道具",
                "record_sha256": "document-only",
                "import_status": "candidate",
            }
        )
        props_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self._resign_candidates()
        bible_path = self.root / "development/series-bible/world-bible.md"
        bible_path.write_text("密室与圆桌会跨循环复现，文档专用道具只在设定集记录。", encoding="utf-8")

        module.build_knowledge_base(self.root, reliable_end=2)

        graph = json.loads(
            (self.root / "graphify-out/graph.json").read_text(encoding="utf-8")
        )
        self.assertIn("prop:4", {node["id"] for node in graph["nodes"]})
        self.assertTrue(
            any(
                {link["source"], link["target"]}
                == {"prop:4", "doc:development/series-bible/world-bible.md"}
                and link["relation"] == "documented_in"
                for link in graph["links"]
            )
        )

    def test_resolves_versioned_manifest_from_current_pointer(self):
        module = load_module()
        versioned = self.root / "source/manifests/versions/state/novel-manifest.json"
        versioned.parent.mkdir(parents=True)
        self.manifest_path.replace(versioned)
        manifest = json.loads(versioned.read_text(encoding="utf-8"))
        (self.root / "source/manifests/current.json").write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "corpus_sha256": manifest["corpus_sha256"],
                    "candidate_set_sha256": manifest["candidate_set_sha256"],
                    "manifest_file": "source/manifests/versions/state/novel-manifest.json",
                }
            ),
            encoding="utf-8",
        )

        result = module.build_knowledge_base(self.root, reliable_end=2)

        self.assertEqual(manifest["corpus_sha256"], result["corpus_sha256"])

    def test_rejects_tampered_candidate_record_even_when_descriptor_hash_is_updated(self):
        module = load_module()
        candidate = self.root / "imports/huobao/corpus/characters.json"
        payload = json.loads(candidate.read_text(encoding="utf-8"))
        for record in payload["records"]:
            record_without_hash = dict(record)
            record_without_hash.pop("record_sha256", None)
            record["record_sha256"] = canonical_sha256(record_without_hash)
        payload["corpus_sha256"] = json.loads(
            self.manifest_path.read_text(encoding="utf-8")
        )["corpus_sha256"]
        payload["records"][0]["name"] = "被篡改的齐夏"
        candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["candidate_exports"]["characters"].update(
            {
                "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                "count": len(payload["records"]),
            }
        )
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "候选记录哈希"):
            module.build_knowledge_base(self.root, reliable_end=2)

    def test_rejects_candidate_export_when_required_hash_is_missing(self):
        module = load_module()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        del manifest["candidate_exports"]["characters"]["sha256"]
        self.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "候选描述缺少"):
            module.build_knowledge_base(self.root, reliable_end=2)

    def test_entity_only_in_quarantined_chapter_is_hidden_by_default(self):
        module = load_module()
        props = self.root / "imports/huobao/corpus/props.json"
        payload = json.loads(props.read_text(encoding="utf-8"))
        payload["records"].append(
            {
                "source_id": 4,
                "name": "重复污染",
                "record_sha256": "polluted",
                "import_status": "candidate",
            }
        )
        props.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self._resign_candidates()
        module.build_knowledge_base(self.root, reliable_end=2)

        with self.assertRaisesRegex(KeyError, "未找到实体"):
            module.lookup_entity(self.root, "重复污染")
        entity = module.lookup_entity(self.root, "重复污染", include_unreliable=True)
        self.assertEqual("prop:4", entity["entity"]["entity_id"])


if __name__ == "__main__":
    unittest.main()
