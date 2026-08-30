import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SCRIPT = SCRIPTS / "probe_comfyui_h3_duration.py"
SPEC = importlib.util.spec_from_file_location("probe_comfyui_h3_duration", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class ProbeComfyH3DurationTests(unittest.TestCase):
    def test_only_verified_completed_runs_are_skipped(self):
        runs = [
            {"requested_frames": 124, "status": "completed", "result": {"id": 1}},
            {"requested_frames": 362, "status": "completed", "result": {"id": 2}},
            {"requested_frames": 481, "status": "failed", "error": "oom"},
        ]

        with mock.patch.object(
            MODULE.h3,
            "reusable_checkpoint_result",
            side_effect=[{"id": 1}, None],
        ) as reusable:
            retained, completed = MODULE.retain_verified_runs(runs)

        self.assertEqual(completed, {124})
        self.assertEqual(retained, [runs[0], runs[2]])
        self.assertEqual(reusable.call_count, 2)
        for call in reusable.call_args_list:
            self.assertTrue(call.kwargs["require_input_evidence"])


if __name__ == "__main__":
    unittest.main()
