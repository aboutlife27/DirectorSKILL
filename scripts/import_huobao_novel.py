#!/usr/bin/env python3
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
from pathlib import Path


REQUIRED_COLUMNS = {
    "dramas": {"id", "title"},
    "episodes": {"id", "drama_id", "episode_number", "title", "content", "script_content"},
    "characters": {"id", "drama_id", "name"},
    "scenes": {"id", "drama_id", "episode_id", "location"},
    "props": {"id", "drama_id", "name"},
    "storyboards": {"id", "episode_id"},
}

CANDIDATE_EXPORTS = {
    "characters": {
        "table": "characters",
        "fields": (
            "name", "role", "description", "appearance", "personality", "voice_style",
            "sort_order", "bio", "traits", "anchor_front", "anchor_side", "anchor_full",
            "anchor_prompt", "visual_spec", "voice_spec", "behavior_spec",
            "consistency_anchors", "image_prompt", "anchor_details", "scope",
            "source_episode_id",
        ),
        "json_fields": {"traits", "visual_spec", "voice_spec", "behavior_spec", "consistency_anchors", "anchor_details"},
    },
    "scenes": {
        "table": "scenes",
        "fields": (
            "episode_id", "location", "time", "prompt", "storyboard_count", "status",
            "master_prompt", "visual_spec", "consistency_anchors", "image_prompt",
            "anchor_details", "scope", "source_episode_id",
        ),
        "json_fields": {"visual_spec", "consistency_anchors", "anchor_details"},
    },
    "props": {
        "table": "props",
        "fields": (
            "name", "type", "description", "prompt", "anchor_front", "anchor_side",
            "anchor_top", "anchor_structure", "anchor_prompt", "anchor_details",
            "visual_spec", "scope", "source_episode_id",
        ),
        "json_fields": {"anchor_details", "visual_spec"},
    },
}

STORYBOARD_FIELDS = (
    "scene_id", "storyboard_number", "title", "location", "time", "shot_type",
    "angle", "movement", "action", "result", "atmosphere", "image_prompt",
    "video_prompt", "bgm_prompt", "sound_effect", "dialogue", "description",
    "duration", "status",
)


class ImportFailure(RuntimeError):
    def __init__(self, code, stage, message):
        self.code = code
        self.stage = stage
        super().__init__(message)


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_corpus_quality(chapters):
    """识别明显抓取污染，只报警，不改写来源正文。"""
    issues = []
    for chapter in chapters:
        chapter_id = chapter["stable_id"]
        content = chapter["content"]
        normalized = unicodedata.normalize("NFKC", content).lower()
        compact = re.sub(r"[\W_]+", "", normalized)
        nonempty_lines = [line for line in content.splitlines() if line.strip()]
        sentences = [
            re.sub(r"\s+", "", sentence)
            for sentence in re.split(r"[。！？!?]+", normalized)
            if len(re.sub(r"\s+", "", sentence)) >= 8
        ]
        duplicate_ratio = (
            1 - len(set(sentences)) / len(sentences) if sentences else 0.0
        )
        metrics = {
            "character_count": len(content),
            "nonempty_line_count": len(nonempty_lines),
            "sentence_count": len(sentences),
            "duplicate_sentence_ratio": round(duplicate_ratio, 4),
        }
        if "hetushu" in compact or "和图书" in compact:
            issues.append({"chapter_id": chapter_id, "code": "watermark_detected", "metrics": metrics})
        if len(content) >= 6000 and len(nonempty_lines) <= 2:
            issues.append({"chapter_id": chapter_id, "code": "single_line_outlier", "metrics": metrics})
        if len(sentences) >= 20 and duplicate_ratio >= 0.25:
            issues.append({"chapter_id": chapter_id, "code": "repeated_content", "metrics": metrics})
    return {
        "status": "warning" if issues else "passed",
        "policy": "report_only_no_source_rewrite",
        "issues": issues,
    }


def build_verification_report(manifest, semantic_quality, verified_at=None):
    status = "warning" if semantic_quality["status"] == "warning" else "passed"
    return {
        "schema_version": "2.1",
        "corpus_sha256": manifest["corpus_sha256"],
        "candidate_set_sha256": manifest.get("candidate_set_sha256"),
        "state_sha256": manifest.get("state_sha256"),
        "verified_at": verified_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": status,
        "structural_status": "passed",
        "semantic_quality": semantic_quality,
        "checks": ["chapter_files", "compiled_novel", "candidate_exports", "semantic_quality"],
    }


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def atomic_write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def table_columns(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def validate_schema(connection):
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    for table, required in REQUIRED_COLUMNS.items():
        if table not in tables:
            raise ImportFailure("missing_table", "validate_source", f"源数据库缺少必需表：{table}")
        missing = required - table_columns(connection, table)
        if missing:
            names = ", ".join(sorted(missing))
            raise ImportFailure("missing_column", "validate_source", f"表 {table} 缺少必需字段：{names}")


def readonly_snapshot(db_path, temporary_directory):
    try:
        source_path = Path(db_path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ImportFailure("invalid_database", "open_source", "无法访问源数据库") from exc
    if not source_path.is_file():
        raise ImportFailure("invalid_database", "open_source", "源数据库不是普通文件")
    snapshot_path = Path(temporary_directory) / "source.db"
    source = None
    target = None
    try:
        source = sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(snapshot_path)
        with target:
            source.backup(target)
    except sqlite3.Error as exc:
        raise ImportFailure("invalid_database", "open_source", "无法读取源数据库") from exc
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    return source_path, snapshot_path


def load_source(connection, drama_id, expected_title):
    validate_schema(connection)
    drama = connection.execute(
        "SELECT id, title FROM dramas WHERE id = ?", (drama_id,)
    ).fetchone()
    if drama is None:
        raise ImportFailure("drama_not_found", "validate_source", "未找到指定剧目")
    title = drama[1]
    if expected_title is not None and title != expected_title:
        raise ImportFailure("title_mismatch", "validate_source", "剧目标题与预期不一致")

    rows = connection.execute(
        "SELECT id, episode_number, title, content FROM episodes "
        "WHERE drama_id = ? ORDER BY episode_number, id",
        (drama_id,),
    ).fetchall()
    if not rows:
        raise ImportFailure("no_chapters", "validate_source", "剧目没有章节")
    numbers = [row[1] for row in rows]
    if len(numbers) != len(set(numbers)):
        raise ImportFailure("duplicate_episode_number", "validate_source", "章节序号存在重复")
    empty = [number for _, number, _, content in rows if content is None or not content.strip()]
    if empty:
        raise ImportFailure("empty_chapter", "validate_source", "章节正文为空")
    expected = list(range(numbers[0], numbers[-1] + 1))
    if numbers != expected:
        raise ImportFailure("episode_gap", "validate_source", "章节序号不连续")
    return title, rows


def normalize_json_fields(payload, json_fields):
    parse_errors = []
    for field in json_fields:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            payload[field] = json.loads(value)
        except json.JSONDecodeError:
            parse_errors.append(field)
    if parse_errors:
        payload["parse_errors"] = sorted(parse_errors)


def candidate_record(row, columns, table, drama_id, json_fields=()):
    source = dict(zip(columns, row))
    source_id = source.pop("id")
    payload = {key: value for key, value in source.items()}
    normalize_json_fields(payload, json_fields)
    payload.update(
        {
            "source_table": table,
            "source_id": source_id,
            "source_drama_id": drama_id,
            "source_episode_id": payload.get("source_episode_id") or payload.get("episode_id"),
            "import_status": "candidate",
        }
    )
    payload["record_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def query_candidates(connection, drama_id):
    exports = {}
    for name, config in CANDIDATE_EXPORTS.items():
        table = config["table"]
        available = table_columns(connection, table)
        fields = [field for field in config["fields"] if field in available]
        columns = ["id", *fields]
        active_clause = " AND deleted_at IS NULL" if "deleted_at" in available else ""
        sql = (
            f"SELECT {', '.join(columns)} FROM {table} "
            f"WHERE drama_id = ?{active_clause} ORDER BY id"
        )
        exports[name] = [
            candidate_record(row, columns, table, drama_id, config["json_fields"])
            for row in connection.execute(sql, (drama_id,))
        ]

    episode_active_clause = (
        " AND deleted_at IS NULL" if "deleted_at" in table_columns(connection, "episodes") else ""
    )
    scripts = connection.execute(
        "SELECT id, episode_number, title, script_content FROM episodes "
        "WHERE drama_id = ? AND script_content IS NOT NULL AND trim(script_content) <> '' "
        f"{episode_active_clause} ORDER BY episode_number, id",
        (drama_id,),
    ).fetchall()
    script_columns = ["id", "episode_number", "title", "script_content"]
    exports["existing-scripts"] = [
        candidate_record(row, script_columns, "episodes", drama_id)
        for row in scripts
    ]

    available = table_columns(connection, "storyboards")
    fields = [field for field in STORYBOARD_FIELDS if field in available]
    columns = ["id", "episode_id", *fields]
    storyboard_active_clause = " AND s.deleted_at IS NULL" if "deleted_at" in available else ""
    episode_join_active_clause = (
        " AND e.deleted_at IS NULL" if "deleted_at" in table_columns(connection, "episodes") else ""
    )
    sql = (
        f"SELECT {', '.join('s.' + field for field in columns)} FROM storyboards s "
        "JOIN episodes e ON e.id = s.episode_id WHERE e.drama_id = ?"
        f"{storyboard_active_clause}{episode_join_active_clause} ORDER BY e.episode_number, s.id"
    )
    exports["existing-storyboards"] = [
        candidate_record(row, columns, "storyboards", drama_id)
        for row in connection.execute(sql, (drama_id,))
    ]
    return exports


def build_chapter(stable_id, source_id, number, title, content):
    metadata = {
        "stable_id": stable_id,
        "source_episode_id": source_id,
        "episode_number": number,
        "original_title": title,
    }
    return f"<!-- {canonical_json(metadata).decode('utf-8')} -->\n\n{content}\n"


def ensure_safe_output(output):
    output = Path(output).expanduser().absolute()
    cursor = Path(output.anchor)
    for part in output.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise ImportFailure("unsafe_output", "prepare_output", "输出路径不能经过符号链接")
    if output.exists() and not output.is_dir():
        raise ImportFailure("unsafe_output", "prepare_output", "输出路径必须是目录")
    return output


def safe_join(root, relative, stage="verify"):
    if not isinstance(relative, str) or not relative:
        raise ImportFailure("invalid_manifest", stage, "清单路径必须是非空字符串")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ImportFailure("unsafe_manifest_path", stage, "清单路径不得逃逸项目目录")
    root = Path(root).absolute()
    candidate = root
    for part in relative_path.parts:
        candidate /= part
        if candidate.is_symlink():
            raise ImportFailure("unsafe_manifest_path", stage, "清单路径不得经过符号链接")
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError as exc:
        raise ImportFailure("unsafe_manifest_path", stage, "清单路径不得逃逸项目目录") from exc
    return candidate


def publish_directory(staged, destination):
    if destination.is_symlink():
        raise ImportFailure("unsafe_output", "publish", "发布路径不能是符号链接")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_dir():
            raise ImportFailure("snapshot_conflict", "publish", "发布目标不是目录")
        return False
    os.replace(staged, destination)
    return True


def import_project(db_path, drama_id, output, expected_title=None):
    output = ensure_safe_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="huobao-read-") as read_temp:
        source_path, database_snapshot = readonly_snapshot(db_path, read_temp)
        connection = sqlite3.connect(database_snapshot)
        try:
            title, chapters = load_source(connection, drama_id, expected_title)
            candidate_exports = query_candidates(connection, drama_id)
        finally:
            connection.close()

    chapter_manifest = []
    corpus_records = []
    for source_id, number, chapter_title, content in chapters:
        stable_id = f"CH{number:04d}"
        content_sha256 = sha256_bytes(content.encode("utf-8"))
        corpus_record = {
            "stable_id": stable_id,
            "source_episode_id": source_id,
            "episode_number": number,
            "original_title": chapter_title,
            "content_sha256": content_sha256,
        }
        corpus_records.append(corpus_record)
    corpus_sha256 = sha256_bytes(canonical_json(corpus_records))
    semantic_quality = audit_corpus_quality(
        [
            {"stable_id": record["stable_id"], "content": row[3]}
            for row, record in zip(chapters, corpus_records)
        ]
    )

    candidate_set_sha256 = sha256_bytes(canonical_json(candidate_exports))
    state_sha256 = sha256_bytes(
        canonical_json(
            {
                "schema_version": "2.0",
                "corpus_sha256": corpus_sha256,
                "candidate_set_sha256": candidate_set_sha256,
            }
        )
    )

    snapshot_relative = Path("source/private/snapshots") / corpus_sha256
    import_relative = Path("imports/huobao") / corpus_sha256 / candidate_set_sha256
    version_relative = Path("source/manifests/versions") / state_sha256
    snapshot_destination = safe_join(output, snapshot_relative.as_posix(), "publish")
    import_destination = safe_join(output, import_relative.as_posix(), "publish")
    version_destination = safe_join(output, version_relative.as_posix(), "publish")
    current_path = safe_join(output, "source/manifests/current.json", "publish")
    if current_path.exists():
        verified = verify_project(output)
        if (
            verified["corpus_sha256"] == corpus_sha256
            and verified["candidate_set_sha256"] == candidate_set_sha256
        ):
            verified["status"] = "unchanged"
            return verified

    stage_root = Path(tempfile.mkdtemp(prefix=f".{output.name}-import-", dir=output.parent))
    try:
        staged_snapshot = stage_root / snapshot_relative
        chapter_dir = staged_snapshot / "chapters"
        chapter_dir.mkdir(parents=True)
        compiled_parts = []
        for (source_id, number, chapter_title, content), corpus_record in zip(chapters, corpus_records):
            stable_id = corpus_record["stable_id"]
            chapter_text = build_chapter(stable_id, source_id, number, chapter_title, content)
            chapter_path = chapter_dir / f"{stable_id}.md"
            chapter_path.write_text(chapter_text, encoding="utf-8")
            compiled_parts.append(chapter_text)
            chapter_manifest.append(
                {
                    **corpus_record,
                    "character_count": len(content),
                    "file": f"chapters/{stable_id}.md",
                    "file_sha256": sha256_file(chapter_path),
                }
            )
        compiled_path = staged_snapshot / "compiled-novel.md"
        compiled_path.write_text("\n\n".join(compiled_parts), encoding="utf-8")
        imported_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(
            staged_snapshot / "source-metadata.json",
            {
                "schema_version": "1.0",
                "source_database": str(source_path),
                "source_database_sha256": sha256_file(source_path),
                "drama_id": drama_id,
                "title": title,
                "corpus_sha256": corpus_sha256,
                "imported_at": imported_at,
                "chapter_count": len(chapters),
            },
        )

        staged_import = stage_root / import_relative
        candidate_files = {}
        candidate_counts = {}
        candidate_descriptors = {}
        for name, records in candidate_exports.items():
            path = staged_import / f"{name}.json"
            write_json(path, {"schema_version": "1.0", "corpus_sha256": corpus_sha256, "records": records})
            relative = import_relative / path.name
            candidate_files[name] = relative.as_posix()
            candidate_counts[name] = len(records)
            candidate_descriptors[name] = {
                "file": relative.as_posix(),
                "count": len(records),
                "sha256": sha256_file(path),
            }

        manifest = {
            "schema_version": "2.0",
            "drama_id": drama_id,
            "title": title,
            "corpus_sha256": corpus_sha256,
            "candidate_set_sha256": candidate_set_sha256,
            "state_sha256": state_sha256,
            "chapter_count": len(chapters),
            "total_character_count": sum(row["character_count"] for row in chapter_manifest),
            "sequence": {"min": chapters[0][1], "max": chapters[-1][1], "continuous": True},
            "empty_chapter_count": 0,
            "duplicate_episode_number_count": 0,
            "snapshot_path": snapshot_relative.as_posix(),
            "compiled_novel": {
                "file": (snapshot_relative / "compiled-novel.md").as_posix(),
                "sha256": sha256_file(compiled_path),
            },
            "chapters": chapter_manifest,
            "candidate_exports": candidate_descriptors,
            "semantic_quality": semantic_quality,
        }
        staged_version = stage_root / version_relative
        staged_manifest = staged_version / "novel-manifest.json"
        write_json(staged_manifest, manifest)
        write_json(
            staged_version / "verification-report.json",
            build_verification_report(manifest, semantic_quality, imported_at),
        )

        output.mkdir(parents=True, exist_ok=True)
        publish_directory(staged_snapshot, snapshot_destination)
        publish_directory(staged_import, import_destination)
        publish_directory(staged_version, version_destination)
        _verify_manifest(output, manifest)
        atomic_write_json(
            current_path,
            {
                "schema_version": "2.0",
                "corpus_sha256": corpus_sha256,
                "candidate_set_sha256": candidate_set_sha256,
                "state_sha256": state_sha256,
                "manifest_file": (version_relative / "novel-manifest.json").as_posix(),
                "verification_report_file": (
                    version_relative / "verification-report.json"
                ).as_posix(),
            },
        )
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise
    else:
        shutil.rmtree(stage_root, ignore_errors=True)

    result = verify_project(output)
    result["status"] = "created"
    return result


def verify_project(output):
    output = ensure_safe_output(output)
    try:
        current_path = safe_join(output, "source/manifests/current.json")
        current = json.loads(current_path.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            raise TypeError("current 不是对象")
        corpus_sha256 = require_hash(current.get("corpus_sha256"), "current.corpus_sha256")
        manifest_file = current.get("manifest_file", "source/manifests/novel-manifest.json")
        if "manifest_file" not in current and set(current) <= {"schema_version"}:
            raise KeyError("current.corpus_sha256")
        manifest_path = safe_join(output, manifest_file)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = _verify_manifest(output, manifest)
        if result["corpus_sha256"] != corpus_sha256:
            raise ImportFailure("manifest_mismatch", "verify", "当前指针与来源清单不一致")
        pointer_candidate_hash = current.get("candidate_set_sha256")
        if pointer_candidate_hash is not None:
            require_hash(pointer_candidate_hash, "current.candidate_set_sha256")
            if result["candidate_set_sha256"] != pointer_candidate_hash:
                raise ImportFailure("manifest_mismatch", "verify", "当前指针与候选清单不一致")
        result["manifest_path"] = manifest_file
        report_file = current.get(
            "verification_report_file", "source/manifests/verification-report.json"
        )
        report_path = safe_join(output, report_file)
        report_manifest = dict(manifest)
        report_manifest["candidate_set_sha256"] = result["candidate_set_sha256"]
        atomic_write_json(
            report_path,
            build_verification_report(report_manifest, result["semantic_quality"]),
        )
        result["verification_report_path"] = report_file
        return result
    except ImportFailure:
        raise
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ImportFailure("invalid_manifest", "verify", "无法读取当前来源清单") from exc


def require_hash(value, label):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise TypeError(f"{label} 不是 SHA-256")
    return value


def _verify_manifest(output, manifest):
    if not isinstance(manifest, dict):
        raise TypeError("manifest 不是对象")
    corpus_sha256 = require_hash(manifest["corpus_sha256"], "manifest.corpus_sha256")
    snapshot_relative = manifest["snapshot_path"]
    snapshot = safe_join(output, snapshot_relative)
    chapters = manifest["chapters"]
    if not isinstance(chapters, list) or manifest["chapter_count"] != len(chapters):
        raise TypeError("章节清单数量不一致")
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise TypeError("章节清单项不是对象")
        stable_id = chapter["stable_id"]
        if not isinstance(stable_id, str) or not stable_id:
            raise TypeError("章节稳定标识无效")
        expected_hash = require_hash(chapter["file_sha256"], "chapter.file_sha256")
        path = safe_join(snapshot, chapter["file"])
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ImportFailure("hash_mismatch", "verify", f"章节文件校验失败：{stable_id}")

    semantic_quality = audit_corpus_quality(
        [
            {
                "stable_id": chapter["stable_id"],
                "content": safe_join(snapshot, chapter["file"]).read_text(encoding="utf-8"),
            }
            for chapter in chapters
        ]
    )

    compiled_descriptor = manifest["compiled_novel"]
    if not isinstance(compiled_descriptor, dict):
        raise TypeError("合订本描述不是对象")
    compiled = safe_join(output, compiled_descriptor["file"])
    compiled_hash = require_hash(compiled_descriptor["sha256"], "compiled_novel.sha256")
    if not compiled.is_file() or sha256_file(compiled) != compiled_hash:
        raise ImportFailure("hash_mismatch", "verify", "合订本文件校验失败")

    candidate_files = {}
    candidate_counts = {}
    candidate_records = {}
    descriptors = manifest["candidate_exports"]
    if not isinstance(descriptors, dict):
        raise TypeError("候选清单不是对象")
    for name, descriptor in descriptors.items():
        if not isinstance(name, str) or not isinstance(descriptor, dict):
            raise TypeError("候选描述无效")
        path = safe_join(output, descriptor["file"])
        expected_hash = require_hash(descriptor["sha256"], "candidate.sha256")
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ImportFailure("hash_mismatch", "verify", f"候选文件校验失败：{name}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ImportFailure("invalid_candidate_file", "verify", f"候选文件结构无效：{name}") from exc
        if not isinstance(payload, dict) or payload.get("corpus_sha256") != corpus_sha256:
            raise ImportFailure("invalid_candidate_file", "verify", f"候选文件语料标识无效：{name}")
        records = payload.get("records")
        if not isinstance(records, list) or descriptor["count"] != len(records):
            raise ImportFailure("invalid_candidate_file", "verify", f"候选记录数量无效：{name}")
        for record in records:
            if not isinstance(record, dict):
                raise ImportFailure("invalid_candidate_record", "verify", f"候选记录结构无效：{name}")
            record_hash = record.get("record_sha256")
            record_without_hash = dict(record)
            record_without_hash.pop("record_sha256", None)
            if (
                not isinstance(record_hash, str)
                or sha256_bytes(canonical_json(record_without_hash)) != record_hash
            ):
                raise ImportFailure("invalid_candidate_record", "verify", f"候选记录哈希无效：{name}")
        candidate_files[name] = descriptor["file"]
        candidate_counts[name] = descriptor["count"]
        candidate_records[name] = records
    candidate_set_sha256 = sha256_bytes(canonical_json(candidate_records))
    declared_candidate_hash = manifest.get("candidate_set_sha256")
    if declared_candidate_hash is not None:
        require_hash(declared_candidate_hash, "manifest.candidate_set_sha256")
        if candidate_set_sha256 != declared_candidate_hash:
            raise ImportFailure("manifest_mismatch", "verify", "候选集合哈希与来源清单不一致")
    return {
        "status": "verified",
        "corpus_sha256": corpus_sha256,
        "candidate_set_sha256": candidate_set_sha256,
        "chapter_count": manifest["chapter_count"],
        "snapshot_path": snapshot_relative,
        "candidate_files": candidate_files,
        "candidate_counts": candidate_counts,
        "semantic_quality": semantic_quality,
    }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def build_parser():
    parser = JsonArgumentParser(description="Huobao 长篇小说可验证导入器")
    parser.add_argument("--db")
    parser.add_argument("--drama-id", type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--expected-title")
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.verify_only:
            result = verify_project(args.output)
        else:
            if not args.db or args.drama_id is None:
                raise ValueError("导入模式必须同时提供 --db 和 --drama-id")
            result = import_project(args.db, args.drama_id, args.output, args.expected_title)
    except ImportFailure as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "stage": exc.stage, "message": str(exc)}}, ensure_ascii=False))
        return 1
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": {"code": "invalid_input", "stage": "cli", "message": str(exc)}}, ensure_ascii=False))
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"ok": False, "error": {"code": "io_failure", "stage": "cli", "message": "无法完成本地文件或数据库操作"}}, ensure_ascii=False))
        return 2
    except Exception:
        print(json.dumps({"ok": False, "error": {"code": "internal_error", "stage": "cli", "message": "执行失败，请检查输入与文件状态"}}, ensure_ascii=False))
        return 3
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
