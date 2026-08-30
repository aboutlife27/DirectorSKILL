import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "import_huobao_novel.py"


def load_importer():
    spec = importlib.util.spec_from_file_location("import_huobao_novel", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fixture(path, chapters=None, title="测试剧"):
    chapters = chapters or [
        (101, 1, "第一章", "第一章正文"),
        (102, 2, "第二章", "第二章正文"),
        (103, 3, "第三章", "第三章正文"),
    ]
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dramas (id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE episodes (
            id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL, title TEXT NOT NULL,
            content TEXT, script_content TEXT
        );
        CREATE TABLE characters (
            id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL, name TEXT NOT NULL,
            role TEXT, description TEXT, appearance TEXT, personality TEXT,
            voice_style TEXT, sort_order INTEGER, local_path TEXT,
            voice_provider TEXT, bio TEXT, traits TEXT, anchor_front TEXT,
            anchor_side TEXT, anchor_full TEXT, anchor_prompt TEXT,
            visual_spec TEXT, voice_spec TEXT, behavior_spec TEXT,
            consistency_anchors TEXT, image_prompt TEXT, anchor_details TEXT,
            scope TEXT, source_episode_id INTEGER, deleted_at TEXT
        );
        CREATE TABLE scenes (
            id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL, episode_id INTEGER,
            location TEXT, time TEXT, prompt TEXT, storyboard_count INTEGER,
            status TEXT, master_prompt TEXT, visual_spec TEXT,
            consistency_anchors TEXT, image_prompt TEXT, anchor_details TEXT,
            scope TEXT, source_episode_id INTEGER, local_path TEXT
        );
        CREATE TABLE props (
            id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL, name TEXT,
            type TEXT, description TEXT, prompt TEXT, anchor_front TEXT,
            anchor_side TEXT, anchor_top TEXT, anchor_structure TEXT,
            anchor_prompt TEXT, anchor_details TEXT, visual_spec TEXT,
            scope TEXT, source_episode_id INTEGER, local_path TEXT
        );
        CREATE TABLE storyboards (
            id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL, scene_id INTEGER,
            storyboard_number INTEGER, title TEXT, location TEXT, time TEXT,
            shot_type TEXT, angle TEXT, movement TEXT, action TEXT, result TEXT,
            atmosphere TEXT, image_prompt TEXT, video_prompt TEXT, bgm_prompt TEXT,
            sound_effect TEXT, dialogue TEXT, description TEXT, duration INTEGER,
            status TEXT, video_url TEXT
        );
        CREATE TABLE ai_service_configs (
            id INTEGER PRIMARY KEY, api_key TEXT NOT NULL
        );
        """
    )
    connection.execute("INSERT INTO dramas VALUES (?, ?)", (2, title))
    connection.executemany(
        "INSERT INTO episodes VALUES (?, 2, ?, ?, ?, ?)",
        [(row_id, number, chapter_title, content, "已有剧本" if number == 1 else None)
         for row_id, number, chapter_title, content in chapters],
    )
    connection.execute(
        "INSERT INTO characters VALUES (1, 2, '甲', '主角', '描述', '黑发', '克制', '低声', 1, '/private/character.png', 'secret-provider', '传记', '[\"敏锐\"]', NULL, NULL, NULL, NULL, '{\"age\":30}', NULL, NULL, '{bad json', 'portrait', NULL, 'global', 101, NULL)"
    )
    connection.execute(
        "INSERT INTO characters VALUES (2, 2, '已删除角色', NULL, NULL, NULL, NULL, NULL, 2, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'global', NULL, '2026-01-01')"
    )
    connection.execute(
        "INSERT INTO scenes VALUES (1, 2, 101, '空屋', '未知', '压抑空屋', 1, 'pending', NULL, NULL, NULL, NULL, NULL, 'episode', 101, '/private/scene.png')"
    )
    connection.execute(
        "INSERT INTO props VALUES (1, 2, '钨丝灯', '灯具', '老旧', '灯光闪烁', NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'episode', 101, '/private/prop.png')"
    )
    connection.execute(
        "INSERT INTO storyboards VALUES (1, 101, 1, 1, '灯光', '空屋', '未知', 'ECU', 'LOW', 'STATIC', '闪烁', '亮起', '压抑', 'image', 'video', 'bgm', 'buzz', NULL, '开场', 5, 'pending', 'https://private.invalid/video')"
    )
    connection.execute("INSERT INTO ai_service_configs VALUES (1, 'sk-test-must-not-export')")
    connection.commit()
    connection.close()


class HuobaoNovelImporterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name).resolve()
        self.db = self.root / "huobao.db"
        self.output = self.root / "project"
        create_fixture(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_imports_ordered_chapters_and_manifest(self):
        importer = load_importer()
        result = importer.import_project(self.db, 2, self.output, "测试剧")

        manifest = json.loads(
            (self.output / result["manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertEqual("created", result["status"])
        self.assertEqual(3, result["chapter_count"])
        self.assertEqual({"min": 1, "max": 3, "continuous": True}, manifest["sequence"])
        self.assertEqual(["CH0001", "CH0002", "CH0003"], [row["stable_id"] for row in manifest["chapters"]])
        snapshot = self.output / result["snapshot_path"]
        self.assertTrue((snapshot / "compiled-novel.md").is_file())
        self.assertIn("第一章正文", (snapshot / "chapters/CH0001.md").read_text(encoding="utf-8"))

    def test_rejects_title_mismatch(self):
        importer = load_importer()
        with self.assertRaises(importer.ImportFailure) as caught:
            importer.import_project(self.db, 2, self.output, "另一部剧")
        self.assertEqual("title_mismatch", caught.exception.code)

    def test_rejects_invalid_chapter_sequences(self):
        importer = load_importer()
        cases = {
            "empty": [(101, 1, "第一章", "")],
            "duplicate": [(101, 1, "第一章", "甲"), (102, 1, "又一章", "乙")],
            "gap": [(101, 1, "第一章", "甲"), (103, 3, "第三章", "丙")],
        }
        expected = {
            "empty": "empty_chapter",
            "duplicate": "duplicate_episode_number",
            "gap": "episode_gap",
        }
        for name, chapters in cases.items():
            with self.subTest(name=name):
                db = self.root / f"{name}.db"
                create_fixture(db, chapters=chapters)
                with self.assertRaises(importer.ImportFailure) as caught:
                    importer.import_project(db, 2, self.root / name, "测试剧")
                self.assertEqual(expected[name], caught.exception.code)
                self.assertFalse((self.root / name / "source/manifests/current.json").exists())

    def test_exports_only_candidate_whitelists(self):
        importer = load_importer()
        result = importer.import_project(self.db, 2, self.output, "测试剧")

        characters = json.loads(
            (self.output / result["candidate_files"]["characters"]).read_text(encoding="utf-8")
        )
        record = characters["records"][0]
        self.assertEqual("candidate", record["import_status"])
        self.assertEqual(1, result["candidate_counts"]["characters"])
        self.assertEqual(["甲"], [item["name"] for item in characters["records"]])
        self.assertNotIn("local_path", record)
        self.assertNotIn("voice_provider", record)
        all_exports = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.output / "imports").rglob("*.json")
        )
        self.assertNotIn("sk-test-must-not-export", all_exports)
        self.assertIn("consistency_anchors", record["parse_errors"])

    def test_same_corpus_is_unchanged(self):
        importer = load_importer()
        first = importer.import_project(self.db, 2, self.output, "测试剧")
        second = importer.import_project(self.db, 2, self.output, "测试剧")

        self.assertEqual("unchanged", second["status"])
        self.assertEqual(first["corpus_sha256"], second["corpus_sha256"])
        self.assertEqual(first["snapshot_path"], second["snapshot_path"])

    def test_candidate_change_creates_new_state_for_same_corpus(self):
        importer = load_importer()
        first = importer.import_project(self.db, 2, self.output, "测试剧")
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE characters SET personality = '果断' WHERE id = 1")

        second = importer.import_project(self.db, 2, self.output, "测试剧")

        self.assertEqual("created", second["status"])
        self.assertEqual(first["corpus_sha256"], second["corpus_sha256"])
        self.assertNotEqual(first["candidate_set_sha256"], second["candidate_set_sha256"])
        self.assertNotEqual(first["manifest_path"], second["manifest_path"])

    def test_verify_only_detects_tampered_chapter(self):
        importer = load_importer()
        result = importer.import_project(self.db, 2, self.output, "测试剧")
        chapter = self.output / result["snapshot_path"] / "chapters/CH0002.md"
        chapter.write_text(chapter.read_text(encoding="utf-8") + "篡改", encoding="utf-8")

        with self.assertRaises(importer.ImportFailure) as caught:
            importer.verify_project(self.output)
        self.assertEqual("hash_mismatch", caught.exception.code)

    def test_verify_rejects_manifest_path_escape_and_symlink(self):
        importer = load_importer()
        result = importer.import_project(self.db, 2, self.output, "测试剧")
        current_path = self.output / "source/manifests/current.json"
        current = json.loads(current_path.read_text(encoding="utf-8"))
        manifest_path = self.output / current["manifest_file"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["compiled_novel"]["file"] = "../../outside.md"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        with self.assertRaises(importer.ImportFailure) as caught:
            importer.verify_project(self.output)
        self.assertEqual("unsafe_manifest_path", caught.exception.code)

        manifest["compiled_novel"]["file"] = (
            Path(result["snapshot_path"]) / "compiled-novel.md"
        ).as_posix()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        real_imports = self.output / "real-imports"
        (self.output / "imports").rename(real_imports)
        (self.output / "imports").symlink_to(real_imports, target_is_directory=True)
        with self.assertRaises(importer.ImportFailure) as caught:
            importer.verify_project(self.output)
        self.assertEqual("unsafe_manifest_path", caught.exception.code)

    def test_verify_rejects_invalid_candidate_record_hash(self):
        importer = load_importer()
        result = importer.import_project(self.db, 2, self.output, "测试剧")
        manifest = json.loads(
            (self.output / result["manifest_path"]).read_text(encoding="utf-8")
        )
        descriptor = manifest["candidate_exports"]["characters"]
        candidate_path = self.output / descriptor["file"]
        payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        payload["records"][0]["record_sha256"] = "0" * 64
        candidate_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        descriptor["sha256"] = importer.sha256_file(candidate_path)
        (self.output / result["manifest_path"]).write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(importer.ImportFailure) as caught:
            importer.verify_project(self.output)
        self.assertEqual("invalid_candidate_record", caught.exception.code)

    def test_publish_failure_does_not_switch_current_state(self):
        importer = load_importer()
        first = importer.import_project(self.db, 2, self.output, "测试剧")
        current_path = self.output / "source/manifests/current.json"
        old_current = current_path.read_bytes()
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE episodes SET content = '修订正文' WHERE id = 101")

        original_replace = os.replace

        def fail_current_switch(source, destination):
            if Path(destination) == current_path:
                raise OSError("模拟 current 指针切换失败")
            return original_replace(source, destination)

        with mock.patch.object(importer.os, "replace", side_effect=fail_current_switch):
            with self.assertRaises(OSError):
                importer.import_project(self.db, 2, self.output, "测试剧")

        self.assertEqual(old_current, current_path.read_bytes())
        verified = importer.verify_project(self.output)
        self.assertEqual(first["corpus_sha256"], verified["corpus_sha256"])

    def test_cli_outputs_machine_readable_error(self):
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--db",
                str(self.db),
                "--drama-id",
                "999",
                "--output",
                str(self.output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertEqual("drama_not_found", payload["error"]["code"])
        self.assertNotIn("第一章正文", result.stdout)

    def test_cli_outputs_json_for_structurally_invalid_manifest(self):
        importer = load_importer()
        importer.import_project(self.db, 2, self.output, "测试剧")
        current_path = self.output / "source/manifests/current.json"
        current_path.write_text('{"schema_version":"2.0"}\n', encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(CLI), "--verify-only", "--output", str(self.output)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(1, result.returncode)
        self.assertEqual("invalid_manifest", payload["error"]["code"])
        self.assertEqual("", result.stderr)

    def test_cli_outputs_json_for_invalid_arguments(self):
        result = subprocess.run(
            [sys.executable, str(CLI), "--verify-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        payload = json.loads(result.stdout)
        self.assertEqual(2, result.returncode)
        self.assertEqual("invalid_input", payload["error"]["code"])
        self.assertEqual("", result.stderr)

    def test_cli_does_not_expose_missing_database_path(self):
        missing = self.root / "private-secret-name.db"
        result = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--db",
                str(missing),
                "--drama-id",
                "2",
                "--output",
                str(self.output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        payload = json.loads(result.stdout)
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn(str(missing), result.stdout)
        self.assertNotIn("private-secret-name.db", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
