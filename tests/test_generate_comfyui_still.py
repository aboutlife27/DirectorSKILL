import importlib.util
import json
import tempfile
import unittest
import urllib.error
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate_comfyui_still.py"
SPEC = importlib.util.spec_from_file_location("generate_comfyui_still", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GenerateComfyStillTests(unittest.TestCase):
    def asset(self):
        return {
            "id": "character-qixia-v001",
            "prompt": "角色三视图",
            "width": 1536,
            "height": 1024,
            "seed": 27001,
        }

    def test_graph_uses_manifest_controls(self):
        graph = MODULE.build_graph(self.asset(), {})

        self.assertEqual(graph["4"]["inputs"]["text"], "角色三视图")
        self.assertEqual(graph["6"]["inputs"]["width"], 1536)
        self.assertEqual(graph["7"]["inputs"]["seed"], 27001)
        self.assertEqual(graph["9"]["inputs"]["images"], ["8", 0])

    def test_invalid_dimensions_are_rejected(self):
        asset = self.asset()
        asset["width"] = 1001

        with self.assertRaisesRegex(ValueError, "8 的倍数"):
            MODULE.build_graph(asset, {})

    def test_manifest_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps({"assets": [self.asset(), self.asset()]}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "不得重复"):
                MODULE.load_manifest(path)

    def test_manifest_rejects_non_object_asset_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps({"assets": [1]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "必须是对象"):
                MODULE.load_manifest(path)

            path.write_text(json.dumps({"assets": [{"id": "incomplete"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "缺少字段"):
                MODULE.load_manifest(path)

    def test_manifest_rejects_path_traversal_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            asset = {**self.asset(), "filename": "../escape.png"}
            path.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "输出目录"):
                MODULE.load_manifest(path)

    def test_manifest_rejects_reserved_report_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            asset = {**self.asset(), "filename": "generation-report.json"}
            path.write_text(json.dumps({"assets": [asset]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "保留文件名"):
                MODULE.load_manifest(path)

    def test_safe_output_path_rejects_reserved_report_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "保留文件名"):
                MODULE.safe_output_path(Path(directory), "generation-report.json")

    def test_manifest_rejects_normalized_filename_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            first = {**self.asset(), "id": "first", "filename": "a/x.png"}
            second = {**self.asset(), "id": "second", "filename": "a/./x.png"}
            path.write_text(json.dumps({"assets": [first, second]}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不得重复"):
                MODULE.load_manifest(path)

    def test_symlink_escape_is_rejected_before_download(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            output_dir = Path(directory)
            (output_dir / "characters").symlink_to(Path(outside), target_is_directory=True)
            asset = {**self.asset(), "filename": "characters/qixia.png"}

            with self.assertRaisesRegex(ValueError, "输出目录"):
                MODULE.generate(
                    manifest={"assets": [asset]},
                    client=mock.Mock(),
                    output_dir=output_dir,
                    only=set(),
                    force=False,
                    timeout=1,
                    poll_seconds=0,
                )

    def test_connection_error_is_wrapped_as_runtime_error(self):
        client = MODULE.ComfyClient("http://127.0.0.1:1", 1)
        with mock.patch.object(
            MODULE.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaisesRegex(RuntimeError, "网络请求失败"):
                client.request_json("/history/test")

    def test_existing_asset_is_reused_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            output = output_dir / "character-qixia-v001.png"
            output.write_bytes(b"image")
            client = mock.Mock()

            results = MODULE.generate(
                manifest={"assets": [self.asset()]},
                client=client,
                output_dir=output_dir,
                only=set(),
                force=False,
                timeout=1,
                poll_seconds=0,
            )

            self.assertEqual(results[0]["status"], "reused")
            client.submit.assert_not_called()

    def test_nested_asset_directory_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            asset = {**self.asset(), "filename": "characters/qixia.png"}
            client = mock.Mock()
            client.submit.return_value = "prompt-1"
            client.wait.return_value = {
                "outputs": {"9": {"images": [{"filename": "remote.png"}]}}
            }
            client.download.side_effect = (
                lambda descriptor, destination: destination.write_bytes(b"image")
            )

            results = MODULE.generate(
                manifest={"assets": [asset]},
                client=client,
                output_dir=output_dir,
                only=set(),
                force=False,
                timeout=1,
                poll_seconds=0,
            )

            self.assertEqual(results[0]["status"], "generated")
            self.assertTrue((output_dir / "characters" / "qixia.png").is_file())

    def test_find_image_reads_save_output(self):
        record = {"outputs": {"9": {"images": [{"filename": "a.png"}]}}}

        self.assertEqual(MODULE.find_image(record)["filename"], "a.png")


if __name__ == "__main__":
    unittest.main()
