#!/usr/bin/env python3
"""递增探测 ComfyUI MiniMax H3 单次生成的可用时长。"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid
from pathlib import Path

import validate_comfyui_h3_reference as h3


def retain_verified_runs(
    runs: list[dict[str, object]],
) -> tuple[list[dict[str, object]], set[int]]:
    retained: list[dict[str, object]] = []
    completed: set[int] = set()
    for item in runs:
        if item.get("status") != "completed":
            retained.append(item)
            continue
        result = item.get("result")
        reusable = h3.reusable_checkpoint_result(
            {"completed": True, "result": result}, require_input_evidence=True
        )
        if reusable is None:
            continue
        retained.append(item)
        completed.add(int(item["requested_frames"]))
    return retained, completed


def media_info(path: Path) -> dict[str, object]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-count_frames",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,nb_read_frames,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe 读取失败")
    return json.loads(result.stdout)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.1.188:8189")
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--lengths", type=int, nargs="+", default=[362, 481])
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--width", type=int, default=608)
    parser.add_argument("--height", type=int, default=352)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=7200.0)
    parser.add_argument(
        "--prompt",
        default=(
            "Single continuous live-action portrait shot. The woman remains seated, "
            "breathes naturally, and slowly lowers her chin once. Locked camera, neutral "
            "soft light, unchanged face, hair, clothing, and background. Quiet room tone."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.image_name.strip():
        raise SystemExit("图片文件名不能为空。")
    if any(length < 5 for length in args.lengths):
        raise SystemExit("帧数不能小于 5。")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "duration-probe.json"
    report = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.exists()
        else {"parameters": {}, "runs": []}
    )
    report["runs"], completed = retain_verified_runs(report["runs"])
    client = h3.ComfyClient(args.base_url, timeout=60.0)
    image_name, image_sha256 = client.create_content_addressed_input(args.image_name)
    image_pixels = h3.decoded_image_bytes_hash(client.input_bytes(image_name))
    if not image_pixels["sha256"]:
        raise SystemExit(f"首帧像素解码失败：{image_pixels['error']}")
    parameters = {
        "base_url": args.base_url.rstrip("/"),
        "source_image_name": args.image_name,
        "image_name": image_name,
        "image_sha256": image_sha256,
        "image_pixel_sha256": image_pixels["sha256"],
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "steps": args.steps,
        "fps": 24,
        "prompt": args.prompt,
    }
    if report["parameters"] and report["parameters"] != parameters:
        raise SystemExit("已有探测结果与当前参数不匹配；请使用新的输出目录。")
    report["parameters"] = parameters
    h3.write_json(result_path, report)

    for length in args.lengths:
        if length in completed:
            continue
        run_dir = args.output_dir / f"frames-{length}"
        run_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = run_dir / "checkpoint.json"
        checkpoint = (
            json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint_path.exists()
            else {"client_id": str(uuid.uuid4()), "variants": {}}
        )
        graph = h3.build_graph(
            variant=f"duration-{length}",
            mode="first-frame",
            image_name=image_name,
            prompt=args.prompt,
            seed=args.seed,
            width=args.width,
            height=args.height,
            length=length,
            steps=args.steps,
        )
        h3.write_json(run_dir / "graph.json", graph)
        started = time.monotonic()
        try:
            generated = h3.run_variant(
                variant=f"duration-{length}",
                graph=graph,
                client=client,
                output_dir=run_dir,
                checkpoint=checkpoint,
                deadline=started + args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                image_guard=(image_name, image_sha256),
                expected_image_pixels=str(image_pixels["sha256"]),
            )
            run = {
                "requested_frames": length,
                "requested_seconds": length / 24,
                "status": "completed",
                "elapsed_seconds": time.monotonic() - started,
                "media": media_info(Path(str(generated["file"]))),
                "result": generated,
            }
        except Exception as error:
            run = {
                "requested_frames": length,
                "requested_seconds": length / 24,
                "status": "failed",
                "elapsed_seconds": time.monotonic() - started,
                "error": str(error),
            }
            report["runs"].append(run)
            h3.write_json(result_path, report)
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 2
        report["runs"].append(run)
        h3.write_json(result_path, report)
        print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
