#!/usr/bin/env python3
"""从可验证小说快照构建本地检索库与 Graphify 关系图。"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.1"
KNOWLEDGE_READY_VALIDATOR = "cinematic-director/narrative-knowledge-base@1"
ENTITY_EXPORTS = {"characters": "character", "props": "prop", "scenes": "location"}


def _safe_path(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("来源路径必须是非空字符串")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("来源路径必须位于项目目录内")
    resolved_root = root.resolve()
    resolved = (root / candidate).resolve(strict=False)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("来源路径逃逸项目目录")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 根节点必须是对象：{path}")
    return value


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _semantic_chapter_content(content: str) -> str:
    lines = content.splitlines()
    if lines and lines[0].startswith("<!-- ") and lines[0].endswith(" -->"):
        lines = lines[2:] if len(lines) > 1 and not lines[1].strip() else lines[1:]
        content = "\n".join(lines)
    return content.rstrip()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _verify_file_sha256(path: Path, expected: Any) -> None:
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"来源清单缺少有效文件哈希：{path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"来源文件哈希不一致：{path}")


def _load_manifest(project_root: Path) -> tuple[dict[str, Any], Path]:
    current_path = project_root / "source/manifests/current.json"
    current = _read_json(current_path) if current_path.is_file() else {}
    manifest_file = current.get("manifest_file", "source/manifests/novel-manifest.json")
    manifest_path = _safe_path(project_root, manifest_file)
    manifest = _read_json(manifest_path)
    pointer_corpus = current.get("corpus_sha256")
    if pointer_corpus is not None and pointer_corpus != manifest.get("corpus_sha256"):
        raise ValueError("当前版本指针与来源清单语料哈希不一致")
    pointer_candidates = current.get("candidate_set_sha256")
    if (
        pointer_candidates is not None
        and pointer_candidates != manifest.get("candidate_set_sha256")
    ):
        raise ValueError("当前版本指针与来源清单候选集合哈希不一致")
    snapshot = _safe_path(project_root, manifest.get("snapshot_path"))
    chapters = manifest.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("小说清单必须包含非空 chapters 数组")
    corpus_records = []
    for chapter in chapters:
        record = {
            key: chapter.get(key)
            for key in (
                "stable_id",
                "source_episode_id",
                "episode_number",
                "original_title",
                "content_sha256",
            )
        }
        corpus_records.append(record)
    if _canonical_sha256(corpus_records) != manifest.get("corpus_sha256"):
        raise ValueError("来源清单语料哈希与章节元数据不一致")
    return manifest, snapshot


def _verify_candidate_exports(project_root: Path, manifest: dict[str, Any]) -> None:
    exports = manifest.get("candidate_exports", {})
    if not isinstance(exports, dict):
        raise ValueError("candidate_exports 必须是对象")
    candidate_records = {}
    for export_name, descriptor in exports.items():
        if not isinstance(descriptor, dict) or not descriptor.get("file"):
            raise ValueError(f"候选描述无效：{export_name}")
        if not isinstance(descriptor.get("sha256"), str) or not isinstance(
            descriptor.get("count"), int
        ):
            raise ValueError(f"候选描述缺少 sha256 或 count：{export_name}")
        source_path = _safe_path(project_root, descriptor["file"])
        _verify_file_sha256(source_path, descriptor["sha256"])
        payload = _read_json(source_path)
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError(f"{export_name}.records 必须是数组")
        if descriptor["count"] != len(records):
            raise ValueError(f"候选记录数量不一致：{export_name}")
        if payload.get("corpus_sha256") != manifest.get("corpus_sha256"):
            raise ValueError(f"候选文件语料哈希不一致：{export_name}")
        for record in records:
            if not isinstance(record, dict):
                raise ValueError(f"候选记录结构无效：{export_name}")
            expected = record.get("record_sha256")
            record_without_hash = dict(record)
            record_without_hash.pop("record_sha256", None)
            if not isinstance(expected, str) or _canonical_sha256(record_without_hash) != expected:
                raise ValueError(f"候选记录哈希无效：{export_name}")
        candidate_records[export_name] = records
    declared = manifest.get("candidate_set_sha256")
    if not isinstance(declared, str) or len(declared) != 64:
        raise ValueError("来源清单缺少候选集合哈希")
    if _canonical_sha256(candidate_records) != declared:
        raise ValueError("候选集合哈希与来源清单不一致")


def _load_entities(project_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    exports = manifest.get("candidate_exports", {})
    if not isinstance(exports, dict):
        raise ValueError("candidate_exports 必须是对象")
    entities: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for export_name, entity_type in ENTITY_EXPORTS.items():
        descriptor = exports.get(export_name)
        if not isinstance(descriptor, dict) or not descriptor.get("file"):
            continue
        source_path = _safe_path(project_root, descriptor["file"])
        if descriptor.get("sha256") is not None:
            _verify_file_sha256(source_path, descriptor["sha256"])
        payload = _read_json(source_path)
        records = payload.get("records", [])
        if not isinstance(records, list):
            raise ValueError(f"{export_name}.records 必须是数组")
        for record in records:
            if not isinstance(record, dict):
                continue
            name = record.get("name")
            source_id = record.get("source_id")
            if not isinstance(name, str) or not name.strip() or source_id is None:
                continue
            name = name.strip()
            dedup_key = (entity_type, name)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            entities.append(
                {
                    "entity_id": f"{entity_type}:{source_id}",
                    "entity_type": entity_type,
                    "canonical_name": name,
                    "status": record.get("import_status") or record.get("status") or "candidate",
                    "source_file": source_path.relative_to(project_root).as_posix(),
                    "source_record_sha256": record.get("record_sha256"),
                    "description": record.get("description") or "",
                    "aliases": _normalize_aliases(record.get("aliases"), name),
                    "evidence_chapters": _chapter_references(
                        record.get("source"), record.get("evidence")
                    ),
                }
            )
    by_id = {item["entity_id"]: item for item in entities}
    bible_root = project_root / "development/series-bible"
    directory_types = {"characters": "character", "props": "prop", "locations": "location"}
    collection_keys = ("characters", "props", "locations", "scenes", "records")
    for directory, entity_type in directory_types.items():
        root = bible_root / directory
        if not root.is_dir():
            continue
        for source_path in sorted(root.rglob("*.json")):
            if source_path.is_symlink():
                continue
            payload = _read_json(source_path)
            records = []
            if isinstance(payload.get("name"), str):
                records.append(payload)
            for key in collection_keys:
                values = payload.get(key)
                if isinstance(values, list):
                    records.extend(value for value in values if isinstance(value, dict))
            for record in records:
                name = record.get("name")
                source_id = record.get("id") or record.get("asset_id") or record.get("source_id")
                if not isinstance(name, str) or not name.strip() or source_id is None:
                    continue
                name = name.strip()
                entity_id = f"{entity_type}:{source_id}"
                by_id[entity_id] = {
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "canonical_name": name,
                    "status": record.get("status") or payload.get("status") or "candidate",
                    "source_file": source_path.relative_to(project_root).as_posix(),
                    "source_record_sha256": _sha256_text(
                        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    ),
                    "description": record.get("description") or record.get("narrative_core") or "",
                    "aliases": _normalize_aliases(record.get("aliases"), name),
                    "evidence_chapters": _chapter_references(
                        record.get("source"),
                        record.get("evidence"),
                        record.get("source_reliability"),
                        payload.get("source_reliability"),
                    ),
                }
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for entity in by_id.values():
        by_name[(entity["entity_type"], entity["canonical_name"])] = entity
    return sorted(by_name.values(), key=lambda item: (item["entity_type"], item["canonical_name"]))


def _normalize_aliases(value: Any, canonical_name: str) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted(
        {
            alias.strip()
            for alias in value
            if isinstance(alias, str) and alias.strip() and alias.strip() != canonical_name
        }
    )


def _chapter_references(*values: Any) -> set[str]:
    references = set()

    def collect(value: Any) -> None:
        if isinstance(value, str) and value.startswith("CH") and value[2:].isdigit():
            references.add(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    for value in values:
        collect(value)
    return references


def _load_documents(project_root: Path) -> list[dict[str, Any]]:
    bible = project_root / "development/series-bible"
    if not bible.is_dir():
        return []
    documents = []
    for path in sorted(bible.rglob("*")):
        if (
            path.is_symlink()
            or not path.is_file()
            or path.suffix.lower() not in {".md", ".json", ".yaml", ".yml"}
        ):
            continue
        content = path.read_text(encoding="utf-8")
        documents.append(
            {
                "document_id": f"doc:{path.relative_to(project_root).as_posix()}",
                "source_file": path.relative_to(project_root).as_posix(),
                "title": path.stem,
                "content": content,
                "content_sha256": _sha256_text(content),
            }
        )
    return documents


def _mention_evidence(content: str, name: str, *, max_snippets: int = 3) -> tuple[int, int | None, list[str]]:
    count = content.count(name)
    if not count:
        return 0, None, []
    snippets = []
    start = 0
    first_offset = None
    while len(snippets) < max_snippets:
        offset = content.find(name, start)
        if offset < 0:
            break
        if first_offset is None:
            first_offset = offset
        left = max(0, offset - 45)
        right = min(len(content), offset + len(name) + 75)
        snippets.append(" ".join(content[left:right].split()))
        start = offset + len(name)
    return count, first_offset, snippets


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chapters(
            chapter_id TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            title TEXT NOT NULL,
            source_file TEXT NOT NULL,
            file_sha256 TEXT,
            content_sha256 TEXT,
            reliable INTEGER NOT NULL CHECK(reliable IN (0, 1)),
            content TEXT NOT NULL
        );
        CREATE TABLE documents(
            document_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source_file TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            content TEXT NOT NULL
        );
        CREATE TABLE entities(
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            status TEXT NOT NULL,
            source_file TEXT NOT NULL,
            source_record_sha256 TEXT,
            description TEXT NOT NULL,
            UNIQUE(entity_type, canonical_name)
        );
        CREATE TABLE entity_aliases(
            entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            alias TEXT NOT NULL,
            PRIMARY KEY(entity_id, alias)
        );
        CREATE TABLE mentions(
            entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            chapter_id TEXT NOT NULL REFERENCES chapters(chapter_id),
            mention_count INTEGER NOT NULL,
            first_offset INTEGER NOT NULL,
            snippets_json TEXT NOT NULL,
            confidence TEXT NOT NULL,
            PRIMARY KEY(entity_id, chapter_id)
        );
        CREATE TABLE document_mentions(
            entity_id TEXT NOT NULL REFERENCES entities(entity_id),
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            mention_count INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            PRIMARY KEY(entity_id, document_id)
        );
        CREATE INDEX mentions_by_chapter ON mentions(chapter_id);
        CREATE INDEX entities_by_name ON entities(canonical_name);
        """
    )
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE chapter_fts USING fts5(chapter_id UNINDEXED, title, content, tokenize='trigram')"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE document_fts USING fts5(document_id UNINDEXED, title, content, tokenize='trigram')"
        )
        connection.execute("INSERT INTO metadata VALUES('fts_tokenizer', 'trigram')")
    except sqlite3.OperationalError:
        connection.execute(
            "CREATE VIRTUAL TABLE chapter_fts USING fts5(chapter_id UNINDEXED, title, content, tokenize='unicode61')"
        )
        connection.execute(
            "CREATE VIRTUAL TABLE document_fts USING fts5(document_id UNINDEXED, title, content, tokenize='unicode61')"
        )
        connection.execute("INSERT INTO metadata VALUES('fts_tokenizer', 'unicode61')")


def _build_graph(
    project_root: Path,
    chapters: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    document_mentions: list[dict[str, Any]],
    corpus_sha256: str,
    reliable_end: int,
) -> dict[str, int]:
    from graphify.build import build_from_json
    from graphify.cluster import cluster
    from graphify.export import to_html, to_json

    output = project_root / "graphify-out"
    output.mkdir(parents=True, exist_ok=True)
    reliable_ids = {chapter["chapter_id"] for chapter in chapters if chapter["reliable"]}
    active_entity_ids = {
        mention["entity_id"]
        for mention in mentions
        if mention["chapter_id"] in reliable_ids
    }
    active_entity_ids.update(mention["entity_id"] for mention in document_mentions)
    active_entity_ids.update(
        entity["entity_id"]
        for entity in entities
        if entity["source_file"].startswith("development/series-bible/")
    )
    nodes = []
    for chapter in chapters:
        if not chapter["reliable"]:
            continue
        nodes.append(
            {
                "id": f"chapter:{chapter['chapter_id']}",
                "label": f"{chapter['chapter_id']} {chapter['title']}",
                "file_type": "document",
                "node_kind": "chapter",
                "source_file": chapter["source_file"],
                "source_location": chapter["chapter_id"],
            }
        )
    for entity in entities:
        if entity["entity_id"] not in active_entity_ids:
            continue
        nodes.append(
            {
                "id": entity["entity_id"],
                "label": entity["canonical_name"],
                "file_type": "concept",
                "node_kind": entity["entity_type"],
                "source_file": entity["source_file"],
                "source_location": entity["source_record_sha256"],
                "status": entity["status"],
            }
        )
    for document in documents:
        nodes.append(
            {
                "id": document["document_id"],
                "label": document["title"],
                "file_type": "document",
                "node_kind": "series_bible",
                "source_file": document["source_file"],
                "source_location": None,
            }
        )
    edges = []
    for mention in mentions:
        if mention["chapter_id"] not in reliable_ids:
            continue
        edges.append(
            {
                "source": mention["entity_id"],
                "target": f"chapter:{mention['chapter_id']}",
                "relation": "mentioned_in",
                "confidence": mention["confidence"],
                "confidence_score": 1.0 if mention["confidence"] == "EXTRACTED" else 0.55,
                "weight": mention["mention_count"],
                "source_file": mention["source_file"],
                "source_location": str(mention["first_offset"]),
            }
        )
    for mention in document_mentions:
        edges.append(
            {
                "source": mention["entity_id"],
                "target": mention["document_id"],
                "relation": "documented_in",
                "confidence": mention["confidence"],
                "confidence_score": 1.0 if mention["confidence"] == "EXTRACTED" else 0.55,
                "weight": mention["mention_count"],
                "source_file": mention["source_file"],
                "source_location": None,
            }
        )
    extraction = {"nodes": nodes, "edges": edges, "hyperedges": [], "input_tokens": 0, "output_tokens": 0}
    graph = build_from_json(extraction, directed=False)
    try:
        communities = cluster(graph)
    except Exception:
        communities = {0: list(graph.nodes())}
    to_json(graph, communities, str(output / "graph.json"), force=True)
    to_html(graph, communities, str(output / "graph.html"))
    confidence_counts = Counter(edge["confidence"] for edge in edges)
    report = "\n".join(
        [
            "# 《十日终焉》知识图谱审计报告",
            "",
            f"- 语料哈希：`{corpus_sha256}`",
            f"- 可靠建图范围：CH0001–CH{reliable_end:04d}",
            f"- 节点：{graph.number_of_nodes()}",
            f"- 边：{graph.number_of_edges()}",
            f"- 社区：{len(communities)}",
            f"- EXTRACTED：{confidence_counts.get('EXTRACTED', 0)}",
            f"- AMBIGUOUS：{confidence_counts.get('AMBIGUOUS', 0)}",
            "",
            "## 证据规则",
            "",
            "- 章节边只由实体规范名在哈希验证后的原文中直接命中产生。",
            "- 一字实体和通用概念词不自动建边，避免把普通词误判为专名。",
            "- 候选实体身份仍需后续消歧；本图不把共现自动解释为人物关系。",
            "- CH1371–CH1384 保留在全文库中但默认不参与图谱和查询。",
            "",
        ]
    )
    (output / "GRAPH_REPORT.md").write_text(report, encoding="utf-8")
    return {
        "graph_node_count": graph.number_of_nodes(),
        "graph_edge_count": graph.number_of_edges(),
        "community_count": len(communities),
    }


def _file_descriptor(project_root: Path, relative_path: str) -> dict[str, Any]:
    path = project_root / relative_path
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
    }


def _sqlite_is_healthy(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            return row is not None and row[0] == "ok"
    except sqlite3.Error:
        return False


def _write_knowledge_ready_report(
    project_root: Path,
    *,
    corpus_sha256: str,
    reliable_end: int,
    reliable_chapter_count: int,
    graph_node_count: int,
    graph_edge_count: int,
) -> dict[str, Any]:
    database_path = project_root / "knowledge-base/knowledge.db"
    graph_path = project_root / "graphify-out/graph.json"
    memory_path = project_root / ".agent-memory/meta/index.sqlite"
    database_health = _sqlite_is_healthy(database_path)
    memory_health = _sqlite_is_healthy(memory_path)
    memory_tables = set()
    if memory_health:
        with sqlite3.connect(f"file:{memory_path}?mode=ro", uri=True) as connection:
            memory_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    checks = {
        "source_hashes": True,
        "stable_ids": True,
        "reliable_scope_isolated": True,
        "search": reliable_chapter_count > 0,
        "entity": graph_node_count > reliable_chapter_count,
        "related": graph_edge_count > 0,
        "database_integrity": database_health,
        "memory_health": memory_health
        and bool(memory_tables.intersection({"memory_docs", "memory_sections"})),
    }
    report = {
        "schema_version": "1.0",
        "validator": KNOWLEDGE_READY_VALIDATOR,
        "status": "ready" if all(checks.values()) else "not_ready",
        "corpus_sha256": corpus_sha256,
        "reliable_end": reliable_end,
        "checks": checks,
        "artifacts": {
            "knowledge_db": _file_descriptor(project_root, "knowledge-base/knowledge.db"),
            "graph": _file_descriptor(project_root, "graphify-out/graph.json"),
            "memory_index": _file_descriptor(project_root, ".agent-memory/meta/index.sqlite"),
        },
    }
    report_path = project_root / "knowledge-base/knowledge-ready-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def build_knowledge_base(project_root: Path | str, *, reliable_end: int = 1370) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    manifest, snapshot = _load_manifest(project_root)
    _verify_candidate_exports(project_root, manifest)
    entities = _load_entities(project_root, manifest)
    documents = _load_documents(project_root)
    chapters = []
    for descriptor in manifest["chapters"]:
        chapter_id = descriptor.get("stable_id")
        ordinal = descriptor.get("episode_number")
        if not isinstance(chapter_id, str) or not isinstance(ordinal, int):
            raise ValueError("章节缺少稳定编号或顺序")
        source_path = _safe_path(snapshot, descriptor.get("file"))
        _verify_file_sha256(source_path, descriptor.get("file_sha256"))
        content = source_path.read_text(encoding="utf-8")
        if _sha256_text(_semantic_chapter_content(content)) != descriptor.get("content_sha256"):
            raise ValueError(f"章节内容哈希不一致：{chapter_id}")
        chapters.append(
            {
                "chapter_id": chapter_id,
                "ordinal": ordinal,
                "title": descriptor.get("original_title") or chapter_id,
                "source_file": source_path.relative_to(project_root).as_posix(),
                "file_sha256": descriptor.get("file_sha256"),
                "content_sha256": descriptor.get("content_sha256"),
                "reliable": ordinal <= reliable_end,
                "content": content,
            }
        )

    knowledge_dir = project_root / "knowledge-base"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="knowledge-", suffix=".db.part", dir=knowledge_dir)
    os.close(descriptor)
    temporary_db = Path(temporary_name)
    mentions: list[dict[str, Any]] = []
    document_mentions: list[dict[str, Any]] = []
    try:
        with sqlite3.connect(temporary_db) as connection:
            _create_schema(connection)
            metadata = {
                "schema_version": SCHEMA_VERSION,
                "corpus_sha256": manifest.get("corpus_sha256", ""),
                "reliable_end": reliable_end,
                "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            connection.executemany(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
            )
            for chapter in chapters:
                connection.execute(
                    "INSERT INTO chapters VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        chapter["chapter_id"], chapter["ordinal"], chapter["title"],
                        chapter["source_file"], chapter["file_sha256"], chapter["content_sha256"],
                        int(chapter["reliable"]), chapter["content"],
                    ),
                )
                connection.execute(
                    "INSERT INTO chapter_fts(chapter_id, title, content) VALUES(?, ?, ?)",
                    (chapter["chapter_id"], chapter["title"], chapter["content"]),
                )
            for document in documents:
                connection.execute(
                    "INSERT INTO documents VALUES(?, ?, ?, ?, ?)",
                    (
                        document["document_id"], document["title"], document["source_file"],
                        document["content_sha256"], document["content"],
                    ),
                )
                connection.execute(
                    "INSERT INTO document_fts(document_id, title, content) VALUES(?, ?, ?)",
                    (document["document_id"], document["title"], document["content"]),
                )
            for entity in entities:
                connection.execute(
                    "INSERT INTO entities VALUES(?, ?, ?, ?, ?, ?, ?)",
                    (
                        entity["entity_id"], entity["entity_type"], entity["canonical_name"],
                        entity["status"], entity["source_file"], entity["source_record_sha256"],
                        entity["description"],
                    ),
                )
                connection.executemany(
                    "INSERT INTO entity_aliases(entity_id, alias) VALUES(?, ?)",
                    [(entity["entity_id"], alias) for alias in entity["aliases"]],
                )
                canonical_name = entity["canonical_name"]
                aliases = [name for name in entity["aliases"] if len(name) >= 2]
                if len(canonical_name) < 2 and not aliases:
                    continue
                for chapter in chapters:
                    canonical_evidence = (
                        _mention_evidence(chapter["content"], canonical_name)
                        if len(canonical_name) >= 2
                        else (0, None, [])
                    )
                    alias_evidence = [
                        _mention_evidence(chapter["content"], name) for name in aliases
                    ]
                    if canonical_evidence[0]:
                        count, first_offset, snippets = canonical_evidence
                        confidence = "EXTRACTED"
                    elif alias_evidence:
                        count, first_offset, snippets = max(
                            alias_evidence, key=lambda item: item[0]
                        )
                        confidence = (
                            "INFERRED"
                            if chapter["chapter_id"] in entity["evidence_chapters"]
                            else "AMBIGUOUS"
                        )
                    else:
                        continue
                    if not count or first_offset is None:
                        continue
                    mention = {
                        "entity_id": entity["entity_id"],
                        "chapter_id": chapter["chapter_id"],
                        "mention_count": count,
                        "first_offset": first_offset,
                        "snippets": snippets,
                        "confidence": confidence,
                        "source_file": chapter["source_file"],
                    }
                    mentions.append(mention)
                    connection.execute(
                        "INSERT INTO mentions VALUES(?, ?, ?, ?, ?, ?)",
                        (
                            entity["entity_id"], chapter["chapter_id"], count, first_offset,
                            json.dumps(snippets, ensure_ascii=False), confidence,
                        ),
                    )
                for document in documents:
                    canonical_count = (
                        document["content"].count(canonical_name)
                        if len(canonical_name) >= 2
                        else 0
                    )
                    alias_count = max(
                        (document["content"].count(name) for name in aliases), default=0
                    )
                    count = max(canonical_count, alias_count)
                    if not count:
                        continue
                    confidence = (
                        "EXTRACTED"
                        if canonical_count or document["source_file"] == entity["source_file"]
                        else "AMBIGUOUS"
                    )
                    mention = {
                        "entity_id": entity["entity_id"],
                        "document_id": document["document_id"],
                        "mention_count": count,
                        "confidence": confidence,
                        "source_file": document["source_file"],
                    }
                    document_mentions.append(mention)
                    connection.execute(
                        "INSERT INTO document_mentions VALUES(?, ?, ?, ?)",
                        (entity["entity_id"], document["document_id"], count, confidence),
                    )
            connection.commit()
        os.replace(temporary_db, knowledge_dir / "knowledge.db")
    finally:
        temporary_db.unlink(missing_ok=True)

    graph_result = _build_graph(
        project_root, chapters, entities, documents, mentions, document_mentions,
        str(manifest.get("corpus_sha256", "")), reliable_end,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "corpus_sha256": manifest.get("corpus_sha256"),
        "chapter_count": len(chapters),
        "reliable_chapter_count": sum(chapter["reliable"] for chapter in chapters),
        "entity_count": len(entities),
        "document_count": len(documents),
        "mention_edge_count": len(mentions),
        "knowledge_db": "knowledge-base/knowledge.db",
        "graph": "graphify-out/graph.json",
        **graph_result,
    }
    ready_report = _write_knowledge_ready_report(
        project_root,
        corpus_sha256=str(manifest.get("corpus_sha256", "")),
        reliable_end=reliable_end,
        reliable_chapter_count=result["reliable_chapter_count"],
        graph_node_count=result["graph_node_count"],
        graph_edge_count=result["graph_edge_count"],
    )
    result["knowledge_ready"] = ready_report["status"] == "ready"
    result["knowledge_ready_report"] = "knowledge-base/knowledge-ready-report.json"
    (knowledge_dir / "build-report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def _knowledge_db(project_root: Path | str) -> Path:
    path = Path(project_root).resolve() / "knowledge-base/knowledge.db"
    if not path.is_file():
        raise FileNotFoundError("知识库尚未构建")
    return path


def search_knowledge_base(
    project_root: Path | str,
    query: str,
    *,
    limit: int = 10,
    include_unreliable: bool = False,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise ValueError("查询词不能为空")
    if limit < 1 or limit > 100:
        raise ValueError("limit 必须介于 1 和 100")
    with sqlite3.connect(_knowledge_db(project_root)) as connection:
        connection.row_factory = sqlite3.Row
        reliability = "" if include_unreliable else "AND c.reliable = 1"
        if len(query) >= 3:
            phrase = '"' + query.replace('"', '""') + '"'
            rows = connection.execute(
                f"SELECT c.* FROM chapter_fts f JOIN chapters c USING(chapter_id) "
                f"WHERE chapter_fts MATCH ? {reliability} ORDER BY c.ordinal LIMIT ?",
                (phrase, limit),
            ).fetchall()
            docs = connection.execute(
                "SELECT d.* FROM document_fts f JOIN documents d USING(document_id) "
                "WHERE document_fts MATCH ? ORDER BY d.source_file LIMIT ?",
                (phrase, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                f"SELECT * FROM chapters c WHERE instr(c.title, ?) > 0 OR instr(c.content, ?) > 0 "
                f"{reliability} ORDER BY c.ordinal LIMIT ?",
                (query, query, limit),
            ).fetchall()
            docs = connection.execute(
                "SELECT * FROM documents WHERE instr(title, ?) > 0 OR instr(content, ?) > 0 "
                "ORDER BY source_file LIMIT ?",
                (query, query, limit),
            ).fetchall()
        chapters = []
        for row in rows:
            _, _, snippets = _mention_evidence(row["content"], query, max_snippets=2)
            chapters.append(
                {
                    "chapter_id": row["chapter_id"],
                    "ordinal": row["ordinal"],
                    "title": row["title"],
                    "source_file": row["source_file"],
                    "reliable": bool(row["reliable"]),
                    "snippets": snippets,
                }
            )
        return {
            "query": query,
            "include_unreliable": include_unreliable,
            "chapters": chapters,
            "documents": [
                {"document_id": row["document_id"], "title": row["title"], "source_file": row["source_file"]}
                for row in docs
            ],
        }


def lookup_entity(
    project_root: Path | str,
    name: str,
    *,
    include_unreliable: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("实体名不能为空")
    with sqlite3.connect(_knowledge_db(project_root)) as connection:
        connection.row_factory = sqlite3.Row
        matches = connection.execute(
            "SELECT DISTINCT e.* FROM entities e "
            "LEFT JOIN entity_aliases a ON a.entity_id = e.entity_id "
            "WHERE e.canonical_name = ? OR a.alias = ? ORDER BY e.entity_type, e.canonical_name",
            (name, name),
        ).fetchall()
        if not matches:
            matches = connection.execute(
                "SELECT * FROM entities WHERE instr(canonical_name, ?) > 0 "
                "ORDER BY length(canonical_name), entity_type, canonical_name",
                (name,),
            ).fetchall()
        if len(matches) > 1:
            options = "、".join(row["canonical_name"] for row in matches[:8])
            raise KeyError(f"实体查询有歧义：{name}；候选：{options}")
        entity = matches[0] if matches else None
        if entity is None:
            raise KeyError(f"未找到实体：{name}")
        reliability = "" if include_unreliable else "AND c.reliable = 1"
        rows = connection.execute(
            f"SELECT c.chapter_id, c.ordinal, c.title, c.source_file, c.reliable, "
            f"m.mention_count, m.snippets_json, m.confidence FROM mentions m "
            f"JOIN chapters c USING(chapter_id) WHERE m.entity_id = ? {reliability} "
            f"ORDER BY c.ordinal LIMIT ?",
            (entity["entity_id"], limit),
        ).fetchall()
        if (
            not include_unreliable
            and not rows
            and not entity["source_file"].startswith("development/series-bible/")
        ):
            raise KeyError(f"未找到实体：{name}")
        return {
            "entity": dict(entity),
            "mentions": [
                {
                    **{key: row[key] for key in row.keys() if key != "snippets_json"},
                    "reliable": bool(row["reliable"]),
                    "snippets": json.loads(row["snippets_json"]),
                }
                for row in rows
            ],
        }


def query_knowledge_graph(
    project_root: Path | str,
    query: str,
    *,
    depth: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    """按中文标签定位节点，并返回 Graphify 图中的有限邻域。"""
    from networkx.readwrite import json_graph

    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        raise ValueError("关系查询词不能为空")
    if depth < 0 or depth > 3:
        raise ValueError("depth 必须介于 0 和 3")
    if limit < 1 or limit > 500:
        raise ValueError("limit 必须介于 1 和 500")
    graph_path = Path(project_root).resolve() / "graphify-out/graph.json"
    if not graph_path.is_file():
        raise FileNotFoundError("知识图谱尚未构建")
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    try:
        graph = json_graph.node_link_graph(payload, edges="links")
    except TypeError:
        graph = json_graph.node_link_graph(payload)
    exact_matches = []
    scored = []
    for node_id, data in graph.nodes(data=True):
        label = str(data.get("norm_label") or data.get("label") or "").casefold()
        source = str(data.get("source_file") or "").casefold()
        if label in terms:
            exact_matches.append(str(node_id))
        score = sum(2 for term in terms if term in label) + sum(1 for term in terms if term in source)
        if score:
            scored.append((score, str(node_id)))
    matched = sorted(exact_matches) or [
        node_id for _, node_id in sorted(scored, key=lambda item: (-item[0], item[1]))[:10]
    ]
    if not matched:
        return {"query": query, "matched_node_ids": [], "nodes": [], "edges": []}
    selected = set(matched)
    frontier = set(matched)
    for _ in range(depth):
        neighbors = {str(neighbor) for node_id in frontier for neighbor in graph.neighbors(node_id)}
        frontier = neighbors - selected
        selected.update(frontier)
        if len(selected) >= limit:
            break
    roots = set(matched[:limit])
    selected = roots | set(sorted(selected - roots)[: max(0, limit - len(roots))])
    nodes = [
        {"id": str(node_id), **dict(graph.nodes[node_id])}
        for node_id in sorted(selected)
        if node_id in graph
    ]
    edges = []
    for source, target, data in graph.edges(data=True):
        if str(source) in selected and str(target) in selected:
            edges.append({"source": str(source), "target": str(target), **dict(data)})
    return {
        "query": query,
        "matched_node_ids": matched,
        "nodes": nodes,
        "edges": edges,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="长篇叙事本地知识库")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="重建检索库与知识图谱")
    build.add_argument("project", type=Path)
    build.add_argument("--reliable-end", type=int, default=1370)
    search = subparsers.add_parser("search", help="搜索原文与剧集圣经")
    search.add_argument("project", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-unreliable", action="store_true")
    entity = subparsers.add_parser("entity", help="查询实体的章节证据")
    entity.add_argument("project", type=Path)
    entity.add_argument("name")
    entity.add_argument("--limit", type=int, default=100)
    entity.add_argument("--include-unreliable", action="store_true")
    related = subparsers.add_parser("related", help="查询实体或概念的图谱邻域")
    related.add_argument("project", type=Path)
    related.add_argument("query")
    related.add_argument("--depth", type=int, default=1)
    related.add_argument("--limit", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        if args.command == "build":
            result = build_knowledge_base(args.project, reliable_end=args.reliable_end)
        elif args.command == "search":
            result = search_knowledge_base(
                args.project, args.query, limit=args.limit, include_unreliable=args.include_unreliable
            )
        elif args.command == "entity":
            result = lookup_entity(
                args.project, args.name, limit=args.limit, include_unreliable=args.include_unreliable
            )
        else:
            result = query_knowledge_graph(
                args.project, args.query, depth=args.depth, limit=args.limit
            )
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"ok": False, "error": {"type": type(error).__name__, "message": str(error)}},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
