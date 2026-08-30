import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_comfyui_h3_reference.py"
SPEC = importlib.util.spec_from_file_location("validate_comfyui_h3_reference", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ValidateComfyH3ReferenceTests(unittest.TestCase):
    def graph(self, image_name, variant="test", mode="reference"):
        return MODULE.build_graph(
            variant=variant,
            mode=mode,
            image_name=image_name,
            prompt="<Picture 1> 是身份参考",
            seed=7,
            width=608,
            height=352,
            length=124,
            steps=25,
        )

    def test_reference_graph_uses_nested_autogrow_input(self):
        graph = self.graph("character.png")

        self.assertEqual(graph["21"]["inputs"]["image"], "character.png")
        self.assertEqual(graph["22"]["inputs"]["images"], ["21", 0])
        self.assertEqual(
            graph["6"]["inputs"]["ref_images"],
            {"ref_image_0": ["21", 0]},
        )

    def test_control_graph_only_removes_reference_path(self):
        with_reference = self.graph("character.png", "with-reference")
        without_reference = self.graph(None, "without-reference")

        self.assertNotIn("21", without_reference)
        self.assertNotIn("22", without_reference)
        self.assertNotIn("ref_images", without_reference["6"]["inputs"])
        del with_reference["21"]
        del with_reference["22"]
        del with_reference["6"]["inputs"]["ref_images"]
        self.assertEqual(with_reference, without_reference)

    def test_first_frame_graph_uses_fl2va_and_direct_image_input(self):
        graph = self.graph("character.png", mode="first-frame")

        self.assertEqual(
            graph["1"]["inputs"]["unet_name"],
            "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        )
        self.assertEqual(graph["6"]["class_type"], "MiniMaxH3ImageToVideo")
        self.assertEqual(graph["6"]["inputs"]["first_frame"], ["21", 0])
        self.assertNotIn("audio_vae", graph["6"]["inputs"])
        self.assertNotIn("ref_images", graph["6"]["inputs"])

    def test_first_frame_control_only_removes_image_path(self):
        with_image = self.graph("character.png", mode="first-frame")
        without_image = self.graph(None, mode="first-frame")

        self.assertNotIn("21", without_image)
        self.assertNotIn("22", without_image)
        self.assertNotIn("first_frame", without_image["6"]["inputs"])
        del with_image["21"]
        del with_image["22"]
        del with_image["6"]["inputs"]["first_frame"]
        self.assertEqual(with_image, without_image)

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "不支持的验证模式"):
            self.graph("character.png", mode="unknown")

    def test_missing_decode_hash_is_inconclusive(self):
        failed = {
            "decoded_video": {"sha256": None, "error": "失败"},
            "decoded_audio": {"sha256": None, "error": "失败"},
        }

        self.assertEqual(
            MODULE.compare_results(failed, failed),
            "inconclusive_decode_failed",
        )

    def test_difference_requires_stable_repeats_on_both_branches(self):
        reference = {
            "decoded_video": {"sha256": "video-ref", "error": None},
            "decoded_audio": {"sha256": "audio-ref", "error": None},
        }
        control = {
            "decoded_video": {"sha256": "video-control", "error": None},
            "decoded_audio": {"sha256": "audio-control", "error": None},
        }

        self.assertEqual(
            MODULE.compare_results(reference, control),
            "needs_both_repeats",
        )
        self.assertEqual(
            MODULE.compare_results(reference, control, control, reference),
            "reference_effect_detected",
        )

    def test_unstable_conditioned_branch_is_inconclusive(self):
        reference = {
            "decoded_video": {"sha256": "video-ref", "error": None},
            "decoded_audio": {"sha256": "audio-ref", "error": None},
        }
        changed_reference = {
            "decoded_video": {"sha256": "video-other", "error": None},
            "decoded_audio": {"sha256": "audio-other", "error": None},
        }
        control = {
            "decoded_video": {"sha256": "video-control", "error": None},
            "decoded_audio": {"sha256": "audio-control", "error": None},
        }

        self.assertEqual(
            MODULE.compare_results(reference, control, control, changed_reference),
            "inconclusive_nondeterministic",
        )

    def test_media_descriptors_are_found_recursively(self):
        value = {"15": {"video": [{"filename": "clip.mp4", "type": "output"}]}}

        self.assertEqual(
            MODULE.find_media_descriptors(value),
            [{"filename": "clip.mp4", "type": "output"}],
        )

    def test_file_hash_is_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bin"
            path.write_bytes(b"same")

            self.assertEqual(MODULE.sha256_file(path), MODULE.sha256_file(path))

    def test_changed_server_input_is_rejected(self):
        client = mock.Mock()
        client.input_sha256.return_value = "changed"

        with self.assertRaisesRegex(RuntimeError, "已变化"):
            MODULE.assert_input_integrity(client, ("fixed.png", "expected"))

    def test_completed_checkpoint_reuses_verified_local_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(b"media")
            result = {
                "file": str(path),
                "sha256": MODULE.sha256_file(path),
                "decoded_video": {"sha256": "video", "error": None},
                "decoded_audio": {"sha256": "audio", "error": None},
            }
            checkpoint = {
                "variants": {"done": {"completed": True, "result": result}}
            }
            client = mock.Mock()

            def fake_decoded_hash(_path, stream):
                return {"sha256": stream, "error": None}

            with mock.patch.object(
                MODULE, "decoded_hash", side_effect=fake_decoded_hash
            ):
                actual = MODULE.run_variant(
                    variant="done",
                    graph={},
                    client=client,
                    output_dir=Path(directory),
                    checkpoint=checkpoint,
                    deadline=0,
                    poll_seconds=0,
                )

            self.assertEqual(actual, result)
            client.submit.assert_not_called()
            client.wait_for_history.assert_not_called()

    def test_invalid_completed_checkpoint_is_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = {
                "client_id": "client",
                "variants": {
                    "stale": {
                        "prompt_id": "old",
                        "completed": True,
                        "result": {"file": str(Path(directory) / "missing.mp4")},
                    }
                },
            }
            client = mock.Mock()
            client.submit.return_value = "new"
            client.wait_for_history.side_effect = RuntimeError("停止到历史读取")

            with self.assertRaisesRegex(RuntimeError, "停止到历史读取"):
                MODULE.run_variant(
                    variant="stale",
                    graph={"1": {}},
                    client=client,
                    output_dir=Path(directory),
                    checkpoint=checkpoint,
                    deadline=0,
                    poll_seconds=0,
                )

            client.submit.assert_called_once_with({"1": {}}, "client")
            self.assertEqual(
                checkpoint["variants"]["stale"]["prompt_id"], "new"
            )


if __name__ == "__main__":
    unittest.main()
