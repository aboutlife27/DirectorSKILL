#!/usr/bin/env python3
"""验证 ComfyUI MiniMax H3 的 API 图像条件是否真正生效。"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def build_graph(
    *,
    variant: str,
    mode: str = "reference",
    image_name: str | None,
    prompt: str,
    seed: int,
    width: int,
    height: int,
    length: int,
    steps: int,
) -> dict[str, dict[str, Any]]:
    if mode not in {"reference", "first-frame"}:
        raise ValueError(f"不支持的验证模式: {mode}")
    is_reference = mode == "reference"
    conditioning_class = (
        "MiniMaxH3ReferenceToVideo" if is_reference else "MiniMaxH3ImageToVideo"
    )
    conditioning_inputs: dict[str, Any] = {
        "clip": ["2", 0],
        "vae": ["3", 0],
        "prompt": prompt,
        "width": width,
        "height": height,
        "length": length,
    }
    if is_reference:
        conditioning_inputs.update(
            {"audio_vae": ["4", 0], "ref_image_size": "match"}
        )
    graph: dict[str, dict[str, Any]] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": (
                    "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
                    if is_reference
                    else "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
                ),
                "weight_dtype": "default",
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
                "type": "minimax",
                "device": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "minimax_h3_video_vae_fp16.safetensors"},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "minimax_h3_audio_vae_fp32.safetensors"},
        },
        "6": {
            "class_type": conditioning_class,
            "inputs": conditioning_inputs,
        },
        "7": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["1", 0], "conditioning": ["6", 0]},
        },
        "8": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "res_multistep"},
        },
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["1", 0],
                "scheduler": "simple",
                "steps": steps,
                "denoise": 1.0,
            },
        },
        "10": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "11": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["10", 0],
                "guider": ["7", 0],
                "sampler": ["8", 0],
                "sigmas": ["9", 0],
                "latent_image": ["6", 1],
            },
        },
        "12": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
        },
        "13": {
            "class_type": "VAEDecodeAudio",
            "inputs": {"samples": ["11", 0], "vae": ["4", 0]},
        },
        "14": {
            "class_type": "CreateVideo",
            "inputs": {
                "images": ["12", 0],
                "audio": ["13", 0],
                "fps": 24.0,
                "bit_depth": 8,
            },
        },
        "15": {
            "class_type": "SaveVideo",
            "inputs": {
                "video": ["14", 0],
                "filename_prefix": "video/H3_image_condition_validation",
                "format": "mp4",
                "codec": "auto",
            },
        },
    }
    if image_name:
        graph["21"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        graph["22"] = {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["21", 0],
                "filename_prefix": "h3_validation/actual_loaded_input",
            },
        }
        if is_reference:
            graph["6"]["inputs"]["ref_images"] = {"ref_image_0": ["21", 0]}
        else:
            graph["6"]["inputs"]["first_frame"] = ["21", 0]
    return graph


class ComfyClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> Any:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {error.code} {path}: {detail}") from error

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        response = self.request_json(
            "/prompt", method="POST", payload={"prompt": graph, "client_id": client_id}
        )
        if response.get("node_errors"):
            raise RuntimeError(f"工作流校验失败: {json.dumps(response, ensure_ascii=False)}")
        return str(response["prompt_id"])

    def wait_for_history(self, prompt_id: str, deadline: float, poll_seconds: float) -> dict[str, Any]:
        while time.monotonic() < deadline:
            history = self.request_json(f"/history/{urllib.parse.quote(prompt_id)}")
            if prompt_id in history:
                record = history[prompt_id]
                status = record.get("status", {})
                if status.get("status_str") == "error" or status.get("completed") is False:
                    raise RuntimeError(
                        f"生成失败: {json.dumps(status, ensure_ascii=False)}"
                    )
                return record
            time.sleep(poll_seconds)
        raise TimeoutError(f"等待任务 {prompt_id} 超时")

    def download_output(self, descriptor: dict[str, Any], destination: Path) -> None:
        query = urllib.parse.urlencode(
            {
                "filename": descriptor["filename"],
                "subfolder": descriptor.get("subfolder", ""),
                "type": descriptor.get("type", "output"),
            }
        )
        request = urllib.request.Request(f"{self.base_url}/view?{query}")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            destination.write_bytes(response.read())

    def input_sha256(self, image_name: str) -> str:
        return hashlib.sha256(self.input_bytes(image_name)).hexdigest()

    def input_bytes(self, image_name: str) -> bytes:
        query = urllib.parse.urlencode({"filename": image_name, "type": "input"})
        request = urllib.request.Request(f"{self.base_url}/view?{query}")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def create_content_addressed_input(self, source_name: str) -> tuple[str, str]:
        content = self.input_bytes(source_name)
        digest = hashlib.sha256(content).hexdigest()
        suffix = Path(source_name).suffix.lower() or ".png"
        target_name = f"h3-validation-{digest}{suffix}"
        try:
            existing_digest = self.input_sha256(target_name)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        else:
            if existing_digest != digest:
                raise RuntimeError("内容寻址首帧名称已存在，但内容哈希不一致。")
            return target_name, digest
        boundary = f"----CodexH3{uuid.uuid4().hex}"
        body = bytearray()

        def add_field(name: str, value: str) -> None:
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            )
            body.extend(value.encode())
            body.extend(b"\r\n")

        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                'Content-Disposition: form-data; name="image"; '
                f'filename="{target_name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        body.extend(content)
        body.extend(b"\r\n")
        add_field("overwrite", "false")
        body.extend(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"{self.base_url}/upload/image",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            uploaded = json.loads(response.read().decode("utf-8"))
        resolved_name = str(uploaded.get("name", target_name))
        if self.input_sha256(resolved_name) != digest:
            raise RuntimeError("内容寻址首帧上传后哈希不一致。")
        return resolved_name, digest


def find_media_descriptors(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if isinstance(value.get("filename"), str):
            found.append(value)
        for child in value.values():
            found.extend(find_media_descriptors(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(find_media_descriptors(child))
    return found


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decoded_hash(path: Path, stream: str) -> dict[str, str | None]:
    mapping = "0:v:0" if stream == "video" else "0:a:0"
    command = ["ffmpeg", "-v", "error", "-i", str(path), "-map", mapping]
    if stream == "video":
        command.extend(["-c:v", "rawvideo", "-pix_fmt", "rgb24"])
    else:
        command.extend(["-c:a", "pcm_s16le"])
    command.extend(["-f", "hash", "-hash", "sha256", "-"])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {"sha256": None, "error": result.stderr.strip() or "ffmpeg 解码失败"}
    value = result.stdout.strip().partition("=")[2]
    if not value:
        return {"sha256": None, "error": "ffmpeg 未返回流哈希"}
    return {"sha256": value, "error": None}


def decoded_image_bytes_hash(content: bytes) -> dict[str, str | None]:
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        "pipe:0",
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-c:v",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-f",
        "hash",
        "-hash",
        "sha256",
        "-",
    ]
    result = subprocess.run(command, input=content, capture_output=True, check=False)
    if result.returncode != 0:
        return {
            "sha256": None,
            "error": result.stderr.decode(errors="replace").strip()
            or "ffmpeg 图像解码失败",
        }
    value = result.stdout.decode().strip().partition("=")[2]
    if not value:
        return {"sha256": None, "error": "ffmpeg 未返回图像哈希"}
    return {"sha256": value, "error": None}


def compare_results(
    with_reference: dict[str, Any],
    without_reference: dict[str, Any],
    control_repeat: dict[str, Any] | None = None,
    conditioned_repeat: dict[str, Any] | None = None,
) -> str:
    keys = ("decoded_video", "decoded_audio")
    for result in (
        with_reference,
        without_reference,
        control_repeat,
        conditioned_repeat,
    ):
        if result is None:
            continue
        if any(not result[key]["sha256"] for key in keys):
            return "inconclusive_decode_failed"
    same_pair = all(
        with_reference[key]["sha256"] == without_reference[key]["sha256"]
        for key in keys
    )
    if same_pair:
        return "reference_ignored"
    if control_repeat is None or conditioned_repeat is None:
        return "needs_both_repeats"
    stable_control = all(
        without_reference[key]["sha256"] == control_repeat[key]["sha256"]
        for key in keys
    )
    stable_conditioned = all(
        with_reference[key]["sha256"] == conditioned_repeat[key]["sha256"]
        for key in keys
    )
    return (
        "reference_effect_detected"
        if stable_control and stable_conditioned
        else "inconclusive_nondeterministic"
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reusable_checkpoint_result(
    state: dict[str, Any], *, require_input_evidence: bool = False
) -> dict[str, Any] | None:
    result = state.get("result")
    if not state.get("completed") or not isinstance(result, dict):
        return None
    try:
        path = Path(result["file"])
        expected_file = result["sha256"]
        expected_video = result["decoded_video"]["sha256"]
        expected_audio = result["decoded_audio"]["sha256"]
    except (KeyError, TypeError):
        return None
    if not path.is_file() or not expected_video or not expected_audio:
        return None
    if sha256_file(path) != expected_file:
        return None
    if decoded_hash(path, "video")["sha256"] != expected_video:
        return None
    if decoded_hash(path, "audio")["sha256"] != expected_audio:
        return None
    evidence = result.get("input_evidence")
    if require_input_evidence and not isinstance(evidence, dict):
        return None
    if isinstance(evidence, dict):
        try:
            evidence_path = Path(evidence["file"])
            evidence_file_hash = evidence["sha256"]
            evidence_pixel_hash = evidence["decoded_pixels"]["sha256"]
        except (KeyError, TypeError):
            return None
        if not evidence_path.is_file() or not evidence_pixel_hash:
            return None
        if sha256_file(evidence_path) != evidence_file_hash:
            return None
        if decoded_hash(evidence_path, "video")["sha256"] != evidence_pixel_hash:
            return None
    return result


def assert_input_integrity(
    client: ComfyClient, image_guard: tuple[str, str] | None
) -> None:
    if image_guard is None:
        return
    image_name, expected_sha256 = image_guard
    if client.input_sha256(image_name) != expected_sha256:
        raise RuntimeError(
            f"服务器输入图像 {image_name} 已变化；停止实验，避免污染结论。"
        )


def run_variant(
    *,
    variant: str,
    graph: dict[str, Any],
    client: ComfyClient,
    output_dir: Path,
    checkpoint: dict[str, Any],
    deadline: float,
    poll_seconds: float,
    image_guard: tuple[str, str] | None = None,
    expected_image_pixels: str | None = None,
) -> dict[str, Any]:
    assert_input_integrity(client, image_guard)
    state = checkpoint.setdefault("variants", {}).setdefault(variant, {})
    reusable = reusable_checkpoint_result(
        state, require_input_evidence=expected_image_pixels is not None
    )
    if reusable is not None:
        return reusable
    if state.get("completed"):
        state.clear()
    if "prompt_id" not in state:
        state["prompt_id"] = client.submit(graph, checkpoint["client_id"])
        write_json(output_dir / "checkpoint.json", checkpoint)

    record = client.wait_for_history(state["prompt_id"], deadline, poll_seconds)
    assert_input_integrity(client, image_guard)
    write_json(output_dir / f"history-{variant}.json", record)
    descriptors = find_media_descriptors(record.get("outputs", {}))
    if not descriptors:
        raise RuntimeError(f"任务 {state['prompt_id']} 完成但没有媒体输出")
    descriptor = next(
        (item for item in descriptors if str(item["filename"]).lower().endswith(".mp4")),
        descriptors[0],
    )
    destination = output_dir / f"{variant}{Path(descriptor['filename']).suffix or '.mp4'}"
    client.download_output(descriptor, destination)
    input_evidence = None
    if expected_image_pixels is not None:
        image_descriptor = next(
            (
                item
                for item in descriptors
                if str(item["filename"]).lower().endswith((".png", ".jpg", ".jpeg"))
            ),
            None,
        )
        if image_descriptor is None:
            raise RuntimeError("任务未返回实际加载首帧的像素证据。")
        evidence_path = output_dir / f"{variant}-actual-input.png"
        client.download_output(image_descriptor, evidence_path)
        decoded_pixels = decoded_hash(evidence_path, "video")
        if decoded_pixels["sha256"] != expected_image_pixels:
            raise RuntimeError("模型实际加载的首帧像素与实验绑定素材不一致。")
        input_evidence = {
            "server_output": image_descriptor,
            "file": str(evidence_path),
            "sha256": sha256_file(evidence_path),
            "decoded_pixels": decoded_pixels,
        }
    result = {
        "prompt_id": state["prompt_id"],
        "server_output": descriptor,
        "file": str(destination),
        "sha256": sha256_file(destination),
        "decoded_video": decoded_hash(destination, "video"),
        "decoded_audio": decoded_hash(destination, "audio"),
    }
    if input_evidence is not None:
        result["input_evidence"] = input_evidence
    state["completed"] = True
    state["result"] = result
    write_json(output_dir / "checkpoint.json", checkpoint)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.1.188:8189")
    parser.add_argument(
        "--mode",
        choices=("reference", "first-frame"),
        default="reference",
        help="验证 Reference-to-Video 参考图或 Image-to-Video 首帧",
    )
    parser.add_argument("--image-name", required=True, help="ComfyUI input 目录中的输入图文件名")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument("--width", type=int, default=608)
    parser.add_argument("--height", type=int, default=352)
    parser.add_argument("--length", type=int, default=124)
    parser.add_argument("--steps", type=int, default=25)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=3600.0)
    parser.add_argument(
        "--prompt",
        default=(
            "Single continuous live-action portrait shot. <Picture 1> is the exact identity "
            "reference. The subject faces camera, breathes naturally, and slowly turns their "
            "eyes to screen left. Locked camera, neutral soft light, unchanged face, hair, "
            "clothing, and background throughout. Audio: quiet room tone, no music."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.image_name.strip():
        raise SystemExit("图片文件名不能为空。")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = ComfyClient(args.base_url, timeout=60.0)
    experiment_image_name, image_sha256 = client.create_content_addressed_input(
        args.image_name
    )
    image_pixels = decoded_image_bytes_hash(
        client.input_bytes(experiment_image_name)
    )
    if not image_pixels["sha256"]:
        raise SystemExit(f"首帧像素解码失败：{image_pixels['error']}")
    common = {
        "prompt": args.prompt,
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "length": args.length,
        "steps": args.steps,
    }
    experiment = {
        "protocol": 6,
        "base_url": args.base_url.rstrip("/"),
        "mode": args.mode,
        "source_image_name": args.image_name,
        "image_name": experiment_image_name,
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
        write_json(checkpoint_path, checkpoint)

    graphs = {
        "with-reference": build_graph(
            variant="with-reference",
            mode=args.mode,
            image_name=experiment_image_name,
            **common,
        ),
        "without-reference": build_graph(
            variant="without-reference", mode=args.mode, image_name=None, **common
        ),
        "without-reference-repeat": build_graph(
            variant="without-reference-repeat", mode=args.mode, image_name=None, **common
        ),
        "with-reference-repeat": build_graph(
            variant="with-reference-repeat",
            mode=args.mode,
            image_name=experiment_image_name,
            **common,
        ),
    }
    write_json(args.output_dir / "graph-with-reference.json", graphs["with-reference"])
    write_json(args.output_dir / "graph-without-reference.json", graphs["without-reference"])

    deadline = time.monotonic() + args.timeout_seconds
    try:
        results = {}
        for variant in ("with-reference", "without-reference"):
            results[variant] = run_variant(
                variant=variant,
                graph=graphs[variant],
                client=client,
                output_dir=args.output_dir,
                checkpoint=checkpoint,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                image_guard=(experiment_image_name, image_sha256),
                expected_image_pixels=(
                    str(image_pixels["sha256"]) if "21" in graphs[variant] else None
                ),
            )
        verdict = compare_results(
            results["with-reference"], results["without-reference"]
        )
        if verdict == "needs_both_repeats":
            results["without-reference-repeat"] = run_variant(
                variant="without-reference-repeat",
                graph=graphs["without-reference-repeat"],
                client=client,
                output_dir=args.output_dir,
                checkpoint=checkpoint,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                image_guard=(experiment_image_name, image_sha256),
                expected_image_pixels=None,
            )
            results["with-reference-repeat"] = run_variant(
                variant="with-reference-repeat",
                graph=graphs["with-reference-repeat"],
                client=client,
                output_dir=args.output_dir,
                checkpoint=checkpoint,
                deadline=deadline,
                poll_seconds=args.poll_seconds,
                image_guard=(experiment_image_name, image_sha256),
                expected_image_pixels=str(image_pixels["sha256"]),
            )
            verdict = compare_results(
                results["with-reference"],
                results["without-reference"],
                results["without-reference-repeat"],
                results["with-reference-repeat"],
            )
        report = {
            "ok": verdict == "reference_effect_detected",
            "verdict": verdict,
            "parameters": {
                "base_url": args.base_url,
                "mode": args.mode,
                "source_image_name": args.image_name,
                "image_name": experiment_image_name,
                "image_sha256": image_sha256,
                "image_pixel_sha256": image_pixels["sha256"],
                **common,
            },
            "results": results,
        }
        write_json(args.output_dir / "result.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    except Exception as error:
        failure = {"ok": False, "verdict": "execution_failed", "error": str(error)}
        write_json(args.output_dir / "result.json", failure)
        print(json.dumps(failure, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
