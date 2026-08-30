#!/usr/bin/env python3
"""验证 ComfyUI MiniMax H3 的文本提示条件是否真正生效。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

import validate_comfyui_h3_reference as h3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.1.188:8189")
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prompt-a", required=True)
    parser.add_argument("--prompt-b", required=True)
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--width", type=int, default=608)
    parser.add_argument("--height", type=int, default=352)
    parser.add_argument("--length", type=int, default=124)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    return parser.parse_args()


def prompt_verdict(
    prompt_a: dict[str, object],
    prompt_b: dict[str, object],
    prompt_b_repeat: dict[str, object] | None = None,
    prompt_a_repeat: dict[str, object] | None = None,
) -> str:
    raw = h3.compare_results(
        prompt_a, prompt_b, prompt_b_repeat, prompt_a_repeat
    )
    return {
        "reference_ignored": "prompt_ignored",
        "needs_both_repeats": "needs_both_repeats",
        "reference_effect_detected": "prompt_effect_detected",
        "inconclusive_decode_failed": "inconclusive_decode_failed",
        "inconclusive_nondeterministic": "inconclusive_nondeterministic",
    }[raw]


def main() -> int:
    args = parse_args()
    if not args.image_name.strip():
        raise SystemExit("图片文件名不能为空。")
    if args.prompt_a == args.prompt_b:
        raise SystemExit("两个提示词必须不同。")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = h3.ComfyClient(args.base_url, timeout=60.0)
    experiment_image_name, image_sha256 = client.create_content_addressed_input(
        args.image_name
    )
    image_pixels = h3.decoded_image_bytes_hash(
        client.input_bytes(experiment_image_name)
    )
    if not image_pixels["sha256"]:
        raise SystemExit(f"首帧像素解码失败：{image_pixels['error']}")
    common = {
        "mode": "first-frame",
        "image_name": experiment_image_name,
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "length": args.length,
        "steps": args.steps,
    }
    experiment = {
        "protocol": 3,
        "base_url": args.base_url.rstrip("/"),
        "source_image_name": args.image_name,
        "prompt_a": args.prompt_a,
        "prompt_b": args.prompt_b,
        "image_sha256": image_sha256,
        "image_pixel_sha256": image_pixels["sha256"],
        **common,
    }
    fingerprint = hashlib.sha256(
        json.dumps(experiment, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    checkpoint_path = args.output_dir / "checkpoint.json"
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("experiment_fingerprint") != fingerprint:
            raise SystemExit("断点与当前实验参数不匹配；请使用新的输出目录。")
    else:
        checkpoint = {
            "client_id": str(uuid.uuid4()),
            "experiment_fingerprint": fingerprint,
            "experiment": experiment,
            "variants": {},
        }
        h3.write_json(checkpoint_path, checkpoint)

    prompts = {
        "prompt-a": args.prompt_a,
        "prompt-b": args.prompt_b,
        "prompt-b-repeat": args.prompt_b,
        "prompt-a-repeat": args.prompt_a,
    }
    graphs = {
        name: h3.build_graph(variant=name, prompt=prompt, **common)
        for name, prompt in prompts.items()
    }
    h3.write_json(args.output_dir / "graph-prompt-a.json", graphs["prompt-a"])
    h3.write_json(args.output_dir / "graph-prompt-b.json", graphs["prompt-b"])

    deadline = time.monotonic() + args.timeout_seconds
    try:
        results = {}
        for variant in ("prompt-a", "prompt-b"):
            results[variant] = h3.run_variant(
                variant=variant,
                graph=graphs[variant],
                client=client,
                output_dir=args.output_dir,
                checkpoint=checkpoint,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                image_guard=(experiment_image_name, image_sha256),
                expected_image_pixels=str(image_pixels["sha256"]),
            )
        verdict = prompt_verdict(results["prompt-a"], results["prompt-b"])
        if verdict == "needs_both_repeats":
            for variant in ("prompt-b-repeat", "prompt-a-repeat"):
                results[variant] = h3.run_variant(
                    variant=variant,
                    graph=graphs[variant],
                    client=client,
                    output_dir=args.output_dir,
                    checkpoint=checkpoint,
                    deadline=deadline,
                    poll_seconds=args.poll_seconds,
                    image_guard=(experiment_image_name, image_sha256),
                    expected_image_pixels=str(image_pixels["sha256"]),
                )
            verdict = prompt_verdict(
                results["prompt-a"],
                results["prompt-b"],
                results["prompt-b-repeat"],
                results["prompt-a-repeat"],
            )
        report = {
            "ok": verdict == "prompt_effect_detected",
            "verdict": verdict,
            "parameters": experiment,
            "results": results,
        }
        h3.write_json(args.output_dir / "result.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    except Exception as error:
        failure = {"ok": False, "verdict": "execution_failed", "error": str(error)}
        h3.write_json(args.output_dir / "result.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
