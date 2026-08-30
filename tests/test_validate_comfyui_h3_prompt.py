import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "validate_comfyui_h3_prompt.py"
SPEC = importlib.util.spec_from_file_location("validate_comfyui_h3_prompt", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def result(video: str, audio: str):
    return {
        "decoded_video": {"sha256": video, "error": None},
        "decoded_audio": {"sha256": audio, "error": None},
    }


class ValidateComfyH3PromptTests(unittest.TestCase):
    def test_identical_streams_mean_prompt_is_ignored(self):
        same = result("video", "audio")
        self.assertEqual(MODULE.prompt_verdict(same, same), "prompt_ignored")

    def test_difference_requires_repeatable_control(self):
        prompt_a = result("video-a", "audio-a")
        prompt_b = result("video-b", "audio-b")
        self.assertEqual(
            MODULE.prompt_verdict(prompt_a, prompt_b), "needs_both_repeats"
        )
        self.assertEqual(
            MODULE.prompt_verdict(prompt_a, prompt_b, prompt_b, prompt_a),
            "prompt_effect_detected",
        )

    def test_nondeterministic_control_is_inconclusive(self):
        self.assertEqual(
            MODULE.prompt_verdict(
                result("a", "a"),
                result("b", "b"),
                result("c", "c"),
                result("a", "a"),
            ),
            "inconclusive_nondeterministic",
        )


if __name__ == "__main__":
    unittest.main()
