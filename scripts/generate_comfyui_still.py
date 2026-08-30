#!/usr/bin/env python3
"""按资产清单调用 ComfyUI 生成可复现的静态图候选。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DEFAULT_MODEL = {
    "unet_name": "boogu\\boogu_image_turbo_fp8_scaled.safetensors",
    "clip_name": "boogu\\qwen3vl_8b_fp8_scaled.safetensors",
    "clip_type": "boogu",
    "vae_name": "flux1_vae_bf16.safetensors",
    "steps": 4,
    "cfg": 1.0,
    "sampler_name": "lcm",
    "scheduler": "sgm_uniform",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_graph(asset: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    width = int(asset["width"])
    height = int(asset["height"])
    if width <= 0 or height <= 0 or width % 8 or height % 8:
        raise ValueError(f"{asset['id']} 的宽高必须是正数且为 8 的倍数")
    settings = {**DEFAULT_MODEL, **model}
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": settings["unet_name"],
                "weight_dtype": settings.get("weight_dtype", "default"),
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": settings["clip_name"],
                "type": settings["clip_type"],
                "device": settings.get("clip_device", "default"),
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": settings["vae_name"]},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"clip": ["2", 0], "text": asset["prompt"]},
        },
        "5": {
            "class_type": "ConditioningZeroOut",
            "inputs": {"conditioning": ["4", 0]},
        },
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "seed": int(asset["seed"]),
                "steps": int(settings["steps"]),
                "cfg": float(settings["cfg"]),
                "sampler_name": settings["sampler_name"],
                "scheduler": settings["scheduler"],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["7", 0], "vae": ["3", 0]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": f"codex-assets/{asset['id']}",
            },
        },
    }


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
        except urllib.error.URLError as error:
            raise RuntimeError(f"网络请求失败 {path}: {error.reason}") from error

    def submit(self, graph: dict[str, Any], client_id: str) -> str:
        response = self.request_json(
            "/prompt", method="POST", payload={"prompt": graph, "client_id": client_id}
        )
        if response.get("node_errors"):
            raise RuntimeError(
                f"工作流校验失败: {json.dumps(response, ensure_ascii=False)}"
            )
        return str(response["prompt_id"])

    def wait(self, prompt_id: str, deadline: float, poll_seconds: float) -> dict[str, Any]:
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

    def download(self, descriptor: dict[str, Any], destination: Path) -> None:
        query = urllib.parse.urlencode(
            {
                "filename": descriptor["filename"],
                "subfolder": descriptor.get("subfolder", ""),
                "type": descriptor.get("type", "output"),
            }
        )
        request = urllib.request.Request(f"{self.base_url}/view?{query}")
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                temporary.write_bytes(response.read())
            temporary.replace(destination)
        except urllib.error.URLError as error:
            raise RuntimeError(f"图片下载失败: {error.reason}") from error
        finally:
            temporary.unlink(missing_ok=True)


def find_image(record: dict[str, Any]) -> dict[str, Any]:
    outputs = record.get("outputs", {})
    for node in outputs.values():
        images = node.get("images", []) if isinstance(node, dict) else []
        if images:
            return images[0]
    raise RuntimeError("历史记录中没有图片输出")


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("资产清单根节点必须是对象")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("资产清单必须包含非空 assets 数组")
    if any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("assets 中的每个资产必须是对象")
    model = manifest.get("model", {})
    if not isinstance(model, dict):
        raise ValueError("model 必须是对象")
    required = ("id", "prompt", "width", "height", "seed")
    for index, asset in enumerate(assets):
        missing = [field for field in required if field not in asset]
        if missing:
            raise ValueError(f"assets[{index}] 缺少字段: {', '.join(missing)}")
        if not isinstance(asset["id"], str) or not re.fullmatch(r"[A-Za-z0-9._-]+", asset["id"]):
            raise ValueError(f"assets[{index}].id 只允许字母、数字、点、下划线和连字符")
        if not isinstance(asset["prompt"], str) or not asset["prompt"].strip():
            raise ValueError(f"assets[{index}].prompt 必须是非空字符串")
        if any(isinstance(asset[field], bool) or not isinstance(asset[field], int) for field in ("width", "height", "seed")):
            raise ValueError(f"assets[{index}] 的 width、height、seed 必须是整数")
        filename = _validate_relative_output(asset.get("filename", f"{asset['id']}.png")).as_posix()
        asset["filename"] = filename
    ids = [asset.get("id") for asset in assets]
    if len(ids) != len(set(ids)):
        raise ValueError("资产 id 不得重复")
    filenames = [asset["filename"] for asset in assets]
    if len(filenames) != len(set(filenames)):
        raise ValueError("资产输出文件名不得重复")
    return manifest


def _validate_relative_output(filename: Any) -> Path:
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("资产文件名必须是非空字符串")
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("资产文件必须位于输出目录内")
    if relative.as_posix() == "generation-report.json":
        raise ValueError("generation-report.json 是保留文件名")
    return relative


def safe_output_path(output_dir: Path, filename: Any) -> Path:
    root = output_dir.resolve()
    destination = (output_dir / _validate_relative_output(filename)).resolve(strict=False)
    if not destination.is_relative_to(root) or destination == root:
        raise ValueError("资产文件必须位于输出目录内，且不得通过符号链接逃逸")
    return destination


def generate(
    *,
    manifest: dict[str, Any],
    client: ComfyClient,
    output_dir: Path,
    only: set[str],
    force: bool,
    timeout: float,
    poll_seconds: float,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    model = manifest.get("model", {})
    for asset in manifest["assets"]:
        asset_id = asset["id"]
        if only and asset_id not in only:
            continue
        destination = safe_output_path(
            output_dir, asset.get("filename", f"{asset_id}.png")
        )
        if destination.exists() and not force:
            results.append(
                {
                    "id": asset_id,
                    "status": "reused",
                    "file": str(destination),
                    "sha256": sha256_file(destination),
                }
            )
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        graph = build_graph(asset, model)
        started = time.monotonic()
        prompt_id = client.submit(graph, str(uuid.uuid4()))
        record = client.wait(prompt_id, started + timeout, poll_seconds)
        client.download(find_image(record), destination)
        results.append(
            {
                "id": asset_id,
                "status": "generated",
                "file": str(destination),
                "sha256": sha256_file(destination),
                "prompt_id": prompt_id,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "seed": int(asset["seed"]),
            }
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        results = generate(
            manifest=manifest,
            client=ComfyClient(args.server, min(args.timeout, 60.0)),
            output_dir=args.output_dir,
            only=set(args.only),
            force=args.force,
            timeout=args.timeout,
            poll_seconds=args.poll_seconds,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    report = {"ok": True, "results": results}
    report_path = args.output_dir / "generation-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
