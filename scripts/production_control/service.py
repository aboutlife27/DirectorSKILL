import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import ProductionError
from .media import import_artifact, sha256_file
from .store import Store, encode_json, utc_now


GATE_ORDER = ["visual_constitution", "core_assets", "pilot_shots", "picture_lock"]
REQUIRED_TASK_FIELDS = {
    "id",
    "kind",
    "stage",
    "depends_on",
    "required_gate",
    "inputs",
    "output_contract",
}
KNOWLEDGE_READY_VALIDATOR = "cinematic-director/narrative-knowledge-base@1"
KNOWLEDGE_READY_CHECKS = {
    "source_hashes",
    "stable_ids",
    "reliable_scope_isolated",
    "search",
    "entity",
    "related",
    "database_integrity",
    "memory_health",
}


class ProductionService:
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir).resolve()
        self.database_path = self.project_dir / ".production" / "production.db"
        if not self.database_path.is_file():
            raise ProductionError("项目尚未初始化", "project_not_initialized")
        self.store = Store(self.database_path)

    @classmethod
    def create(cls, project_dir, title, project_id):
        project_dir = Path(project_dir).resolve()
        database_path = project_dir / ".production" / "production.db"
        if database_path.exists():
            raise ProductionError("项目已经初始化", "project_exists")
        (project_dir / ".production").mkdir(parents=True, exist_ok=True)
        (project_dir / "media" / "objects").mkdir(parents=True, exist_ok=True)
        (project_dir / "exports").mkdir(parents=True, exist_ok=True)
        store = Store(database_path)
        store.initialize()
        with store.transaction() as connection:
            connection.execute(
                "INSERT INTO project(id, title, continuity_state, created_at) VALUES (?, ?, ?, ?)",
                (project_id, title, None, utc_now()),
            )
            store.append_event(
                connection,
                "project.created",
                "project",
                project_id,
                {"title": title},
            )
        return cls(project_dir)

    def import_plan(self, plan):
        self._validate_plan(plan)
        now = utc_now()
        with self.store.transaction() as connection:
            project = connection.execute("SELECT id FROM project").fetchone()
            if project is None or plan["project"]["id"] != project["id"]:
                raise ProductionError("制片计划的项目 ID 与当前项目不一致", "project_id_mismatch")
            existing = connection.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()["count"]
            if existing:
                raise ProductionError("项目已经导入制片计划", "plan_exists")
            connection.execute(
                "UPDATE project SET continuity_state = ?",
                (plan.get("continuity_state"),),
            )
            for position, gate in enumerate(plan["gates"]):
                connection.execute(
                    "INSERT INTO gates(id, position, status, evidence_tasks_json) VALUES (?, ?, ?, ?)",
                    (gate["id"], position, "pending", encode_json(gate["evidence_tasks"])),
                )
            for task in plan["tasks"]:
                status = "ready" if not task["depends_on"] and task["required_gate"] is None else "blocked"
                connection.execute(
                    "INSERT INTO tasks(id, kind, stage, depends_on_json, required_gate, inputs_json, "
                    "output_contract_json, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task["id"],
                        task["kind"],
                        task["stage"],
                        encode_json(task["depends_on"]),
                        task["required_gate"],
                        encode_json(task["inputs"]),
                        encode_json(task["output_contract"]),
                        status,
                        now,
                        now,
                    ),
                )
            self.store.append_event(
                connection,
                "plan.imported",
                "project",
                plan["project"]["id"],
                {"schema_version": plan["schema_version"], "task_count": len(plan["tasks"])},
            )
        return {"task_count": len(plan["tasks"]), "gate_count": len(plan["gates"])}

    def ingest_input(self, input_id, source_path, role, metadata=None):
        if not input_id or not role:
            raise ProductionError("输入 ID 和用途不能为空", "invalid_input_artifact")
        artifact = import_artifact(self.project_dir, source_path)
        try:
            with self.store.transaction() as connection:
                connection.execute(
                    "INSERT INTO input_artifacts(id, role, object_path, content_hash, media_type, "
                    "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        input_id,
                        role,
                        artifact["object_path"],
                        artifact["content_hash"],
                        artifact["media_type"],
                        encode_json(metadata or {}),
                        utc_now(),
                    ),
                )
                self.store.append_event(
                    connection,
                    "input.ingested",
                    "input_artifact",
                    input_id,
                    {"role": role, "content_hash": artifact["content_hash"]},
                )
        except sqlite3.IntegrityError as exc:
            raise ProductionError("输入 ID 已经存在", "input_exists") from exc
        return {"id": input_id, "role": role, **artifact}

    def next_task(self, executor, lease_seconds=900):
        if not executor:
            raise ProductionError("执行器名称不能为空", "invalid_executor")
        if lease_seconds <= 0:
            raise ProductionError("租约时长必须大于零", "invalid_lease")
        with self.store.transaction(immediate=True) as connection:
            self._refresh_blocked_tasks(connection)
            task = connection.execute(
                "SELECT rowid, * FROM tasks WHERE status = 'ready' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if task is None:
                raise ProductionError("当前没有可领取任务", "no_ready_task")
            attempt = connection.execute(
                "SELECT COUNT(*) AS count FROM runs WHERE task_id = ?", (task["id"],)
            ).fetchone()["count"] + 1
            input_hash, references = self._input_snapshot(connection, task)
            now = datetime.now(timezone.utc)
            lease_until = (now + timedelta(seconds=lease_seconds)).isoformat()
            packet = {
                "task": {
                    "id": task["id"],
                    "kind": task["kind"],
                    "stage": task["stage"],
                    "inputs": json.loads(task["inputs_json"]),
                    "output_contract": json.loads(task["output_contract_json"]),
                    "required_gate": task["required_gate"],
                },
                "references": references,
                "executor": executor,
                "input_hash": input_hash,
                "lease_until": lease_until,
            }
            changed = connection.execute(
                "UPDATE tasks SET status = 'leased', updated_at = ? WHERE id = ? AND status = 'ready'",
                (utc_now(), task["id"]),
            ).rowcount
            if changed != 1:
                raise ProductionError("任务已被其他执行器领取", "lease_conflict")
            cursor = connection.execute(
                "INSERT INTO runs(task_id, attempt, executor, status, input_hash, packet_json, "
                "lease_until, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task["id"],
                    attempt,
                    executor,
                    "leased",
                    input_hash,
                    encode_json(packet),
                    lease_until,
                    now.isoformat(),
                ),
            )
            run_id = cursor.lastrowid
            self.store.append_event(
                connection,
                "run.leased",
                "run",
                run_id,
                {"task_id": task["id"], "executor": executor, "input_hash": input_hash},
            )
        return {"run_id": run_id, **packet}

    def submit_candidate(self, run_id, artifact_path, metadata):
        if not isinstance(metadata, dict) or not metadata.get("model"):
            raise ProductionError("候选元数据必须包含 model", "invalid_metadata")
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT runs.*, tasks.output_contract_json FROM runs "
                "JOIN tasks ON tasks.id = runs.task_id WHERE runs.id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ProductionError("运行记录不存在", "run_not_found")
            if row["status"] not in {"leased", "submitted"}:
                raise ProductionError("当前运行不接受候选", "invalid_run_status")
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (row["task_id"],)).fetchone()
            current_hash, _ = self._input_snapshot(connection, task)
        if current_hash != row["input_hash"]:
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE runs SET status = 'stale_input', finished_at = ? WHERE id = ?",
                    (utc_now(), run_id),
                )
                connection.execute(
                    "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                    (utc_now(), row["task_id"]),
                )
                self.store.append_event(
                    connection,
                    "run.stale_input",
                    "run",
                    run_id,
                    {"task_id": row["task_id"]},
                )
            raise ProductionError("任务输入已经变化，请重新领取", "stale_input")

        if task["kind"] == "narrative_knowledge_foundation":
            self._validate_knowledge_ready_report(artifact_path, task)

        contract = json.loads(row["output_contract_json"])
        artifact = import_artifact(self.project_dir, artifact_path, contract.get("media_type"))
        stale_input = False
        with self.store.transaction(immediate=True) as connection:
            run = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if run is None or run["status"] not in {"leased", "submitted"}:
                raise ProductionError("当前运行不接受候选", "invalid_run_status")
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (run["task_id"],)).fetchone()
            current_hash, _ = self._input_snapshot(connection, task)
            if current_hash != run["input_hash"]:
                self._mark_run_stale(connection, run_id, run["task_id"])
                stale_input = True
            else:
                cursor = connection.execute(
                    "INSERT INTO candidates(task_id, run_id, object_path, content_hash, media_type, "
                    "metadata_json, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run["task_id"],
                        run_id,
                        artifact["object_path"],
                        artifact["content_hash"],
                        artifact["media_type"],
                        encode_json(metadata),
                        "pending",
                        utc_now(),
                    ),
                )
                candidate_id = cursor.lastrowid
                connection.execute("UPDATE runs SET status = 'submitted' WHERE id = ?", (run_id,))
                connection.execute(
                    "UPDATE tasks SET status = 'submitted', updated_at = ? WHERE id = ?",
                    (utc_now(), run["task_id"]),
                )
                self.store.append_event(
                    connection,
                    "candidate.submitted",
                    "candidate",
                    candidate_id,
                    {"task_id": run["task_id"], "content_hash": artifact["content_hash"]},
                )
        if stale_input:
            raise ProductionError("任务输入已经变化，请重新领取", "stale_input")
        return {"candidate_id": candidate_id, **artifact}

    def _project_artifact(self, relative_path):
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise ValueError("资产路径必须是非空字符串")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("资产路径必须位于项目目录内")
        candidate = self.project_dir / relative
        current = self.project_dir
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Knowledge Ready 资产不得使用符号链接：{relative_path}")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.project_dir) or not resolved.is_file():
            raise ValueError(f"Knowledge Ready 资产不存在：{relative_path}")
        return resolved

    @staticmethod
    def _sqlite_integrity(path):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                raise ValueError(f"SQLite 完整性检查失败：{path.name}")

    @staticmethod
    def _canonical_sha256(value):
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _semantic_chapter_content(content):
        lines = content.splitlines()
        if lines and lines[0].startswith("<!-- ") and lines[0].endswith(" -->"):
            lines = lines[2:] if len(lines) > 1 and not lines[1].strip() else lines[1:]
            content = "\n".join(lines)
        return content.rstrip()

    def _validate_source_binding(
        self, source_manifest, knowledge_db, corpus_sha256, reliable_end
    ):
        chapters = source_manifest.get("chapters")
        snapshot_path = source_manifest.get("snapshot_path")
        if not isinstance(chapters, list) or not chapters or not isinstance(snapshot_path, str):
            raise ValueError("来源清单缺少完整章节或快照路径")
        corpus_records = [
            {
                key: chapter.get(key)
                for key in (
                    "stable_id",
                    "source_episode_id",
                    "episode_number",
                    "original_title",
                    "content_sha256",
                )
            }
            for chapter in chapters
        ]
        if self._canonical_sha256(corpus_records) != corpus_sha256:
            raise ValueError("来源清单章节元数据无法重建报告语料哈希")

        source_rows = {}
        for chapter in chapters:
            chapter_id = chapter.get("stable_id")
            ordinal = chapter.get("episode_number")
            relative_file = chapter.get("file")
            if not isinstance(chapter_id, str) or not isinstance(ordinal, int):
                raise ValueError("来源清单章节缺少稳定 ID 或顺序")
            combined = Path(snapshot_path) / str(relative_file)
            source_path = self._project_artifact(combined.as_posix())
            file_hash = sha256_file(source_path)
            content = source_path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(
                self._semantic_chapter_content(content).encode("utf-8")
            ).hexdigest()
            if (
                file_hash != chapter.get("file_sha256")
                or content_hash != chapter.get("content_sha256")
            ):
                raise ValueError(f"来源章节哈希不一致：{chapter_id}")
            if chapter_id in source_rows:
                raise ValueError(f"来源章节稳定 ID 重复：{chapter_id}")
            source_rows[chapter_id] = {
                "ordinal": ordinal,
                "title": chapter.get("original_title") or chapter_id,
                "source_file": source_path.relative_to(self.project_dir).as_posix(),
                "file_sha256": file_hash,
                "content_sha256": chapter.get("content_sha256"),
                "reliable": int(ordinal <= reliable_end),
                "content": content,
            }

        with sqlite3.connect(f"file:{knowledge_db}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            database_rows = {
                row["chapter_id"]: {
                    key: row[key]
                    for key in (
                        "ordinal",
                        "title",
                        "source_file",
                        "file_sha256",
                        "content_sha256",
                        "reliable",
                        "content",
                    )
                }
                for row in connection.execute(
                    "SELECT chapter_id, ordinal, title, source_file, file_sha256, "
                    "content_sha256, reliable, content FROM chapters"
                )
            }
            if database_rows != source_rows:
                raise ValueError("知识库章节内容并非由当前来源清单重建")
            fts_count = connection.execute("SELECT COUNT(*) FROM chapter_fts").fetchone()[0]
            if fts_count != len(source_rows):
                raise ValueError("全文搜索索引与来源章节数量不一致")
            aliases = {}
            for entity_id, canonical_name in connection.execute(
                "SELECT entity_id, canonical_name FROM entities"
            ):
                aliases[entity_id] = [canonical_name]
            for entity_id, alias in connection.execute(
                "SELECT entity_id, alias FROM entity_aliases"
            ):
                aliases.setdefault(entity_id, []).append(alias)
            mention_rows = connection.execute(
                "SELECT entity_id, chapter_id, mention_count, first_offset, confidence "
                "FROM mentions"
            ).fetchall()
            for mention in mention_rows:
                content = source_rows[mention["chapter_id"]]["content"]
                matches = [
                    (content.count(term), content.find(term))
                    for term in aliases.get(mention["entity_id"], [])
                    if len(term) >= 2 and term in content
                ]
                if (
                    not matches
                    or (mention["mention_count"], mention["first_offset"]) not in matches
                    or mention["confidence"] not in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
                ):
                    raise ValueError("实体证据无法从来源章节复现")

    @staticmethod
    def _validate_graph_binding(graph, knowledge_db):
        with sqlite3.connect(f"file:{knowledge_db}?mode=ro", uri=True) as connection:
            reliable_chapters = {
                f"chapter:{row[0]}": {
                    "label": f"{row[0]} {row[1]}",
                    "file_type": "document",
                    "node_kind": "chapter",
                    "source_file": row[2],
                    "source_location": row[0],
                    "status": None,
                }
                for row in connection.execute(
                    "SELECT chapter_id, title, source_file FROM chapters WHERE reliable = 1"
                )
            }
            active_entities = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT m.entity_id FROM mentions m "
                    "JOIN chapters c USING(chapter_id) WHERE c.reliable = 1 "
                    "UNION SELECT entity_id FROM document_mentions "
                    "UNION SELECT entity_id FROM entities "
                    "WHERE source_file LIKE 'development/series-bible/%'"
                )
            }
            entity_nodes = {
                row[0]: {
                    "label": row[2],
                    "file_type": "concept",
                    "node_kind": row[1],
                    "source_file": row[4],
                    "source_location": row[5],
                    "status": row[3],
                }
                for row in connection.execute(
                    "SELECT entity_id, entity_type, canonical_name, status, source_file, "
                    "source_record_sha256 FROM entities"
                )
                if row[0] in active_entities
            }
            document_nodes = {
                row[0]: {
                    "label": row[1],
                    "file_type": "document",
                    "node_kind": "series_bible",
                    "source_file": row[2],
                    "source_location": None,
                    "status": None,
                }
                for row in connection.execute(
                    "SELECT document_id, title, source_file FROM documents"
                )
            }
            expected_nodes = {**reliable_chapters, **entity_nodes, **document_nodes}
            expected_edges = set()
            for row in connection.execute(
                "SELECT m.entity_id, m.chapter_id, m.confidence, m.mention_count, "
                "c.source_file, m.first_offset FROM mentions m "
                "JOIN chapters c USING(chapter_id) WHERE c.reliable = 1"
            ):
                expected_edges.add(
                    (
                        tuple(sorted((row[0], f"chapter:{row[1]}"))),
                        "mentioned_in",
                        row[2],
                        1.0 if row[2] == "EXTRACTED" else 0.55,
                        row[3],
                        row[4],
                        str(row[5]),
                    )
                )
            for row in connection.execute(
                "SELECT dm.entity_id, dm.document_id, dm.confidence, dm.mention_count, "
                "d.source_file FROM document_mentions dm "
                "JOIN documents d USING(document_id)"
            ):
                expected_edges.add(
                    (
                        tuple(sorted((row[0], row[1]))),
                        "documented_in",
                        row[2],
                        1.0 if row[2] == "EXTRACTED" else 0.55,
                        row[3],
                        row[4],
                        None,
                    )
                )

        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        links = graph.get("links") if isinstance(graph, dict) else None
        if not isinstance(nodes, list) or not isinstance(links, list):
            raise ValueError("知识图谱节点或关系结构无效")
        semantic_keys = (
            "label",
            "file_type",
            "node_kind",
            "source_file",
            "source_location",
            "status",
        )
        actual_nodes = {
            node.get("id"): {key: node.get(key) for key in semantic_keys}
            for node in nodes
            if isinstance(node, dict) and node.get("id")
        }
        if len(actual_nodes) != len(nodes) or actual_nodes != expected_nodes:
            raise ValueError("知识图谱节点身份属性与知识库证据不一致")
        actual_edges = {
            (
                tuple(sorted((link.get("source"), link.get("target")))),
                link.get("relation"),
                link.get("confidence"),
                link.get("confidence_score"),
                link.get("weight"),
                link.get("source_file"),
                link.get("source_location"),
            )
            for link in links
            if isinstance(link, dict)
            and isinstance(link.get("source"), str)
            and isinstance(link.get("target"), str)
        }
        if len(actual_edges) != len(links) or actual_edges != expected_edges:
            raise ValueError("知识图谱关系无法由知识库证据重建")

    def _validate_knowledge_ready_report(self, report_path, task):
        try:
            raw_report = Path(report_path)
            if raw_report.is_symlink():
                raise ValueError("Knowledge Ready 报告不得使用符号链接")
            for parent in raw_report.absolute().parents:
                if parent.resolve(strict=False) == self.project_dir:
                    break
                if parent.is_symlink() and parent.resolve(strict=False).is_relative_to(
                    self.project_dir
                ):
                    raise ValueError("Knowledge Ready 报告不得位于符号链接目录")
            resolved_report = raw_report.resolve(strict=True)
            if not resolved_report.is_relative_to(self.project_dir):
                raise ValueError("Knowledge Ready 报告必须位于项目目录内")
            report_relative = resolved_report.relative_to(self.project_dir)
            report_file = self._project_artifact(report_relative.as_posix())
            report = json.loads(report_file.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("报告根节点必须是对象")
            if report.get("validator") != KNOWLEDGE_READY_VALIDATOR:
                raise ValueError("报告验证器标识无效")
            if report.get("status") != "ready":
                raise ValueError("知识底座尚未就绪")
            checks = report.get("checks")
            if not isinstance(checks, dict) or any(
                checks.get(name) is not True for name in KNOWLEDGE_READY_CHECKS
            ):
                raise ValueError("报告检查项未全部通过")
            corpus_sha256 = report.get("corpus_sha256")
            reliable_end = report.get("reliable_end")
            if (
                not isinstance(corpus_sha256, str)
                or len(corpus_sha256) != 64
                or any(character not in "0123456789abcdef" for character in corpus_sha256)
                or not isinstance(reliable_end, int)
                or reliable_end < 1
            ):
                raise ValueError("报告语料哈希或可靠范围无效")
            input_ids = json.loads(task["inputs_json"]).get("artifacts", [])
            if "source-manifest" not in input_ids:
                raise ValueError("知识底座任务未绑定来源清单")
            with self.store.connect() as connection:
                manifest_artifact = connection.execute(
                    "SELECT object_path FROM input_artifacts WHERE id = 'source-manifest'"
                ).fetchone()
            if manifest_artifact is None:
                raise ValueError("来源清单输入不存在")
            manifest_path = self._project_artifact(manifest_artifact["object_path"])
            source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                not isinstance(source_manifest, dict)
                or source_manifest.get("corpus_sha256") != corpus_sha256
            ):
                raise ValueError("报告语料哈希与任务来源清单不一致")

            artifacts = report.get("artifacts")
            if not isinstance(artifacts, dict):
                raise ValueError("报告缺少资产清单")
            verified = {}
            expected_paths = {
                "knowledge_db": "knowledge-base/knowledge.db",
                "graph": "graphify-out/graph.json",
                "memory_index": ".agent-memory/meta/index.sqlite",
            }
            for name, expected_path in expected_paths.items():
                descriptor = artifacts.get(name)
                if not isinstance(descriptor, dict):
                    raise ValueError(f"报告缺少资产：{name}")
                if descriptor.get("path") != expected_path:
                    raise ValueError(f"Knowledge Ready 资产路径不符合合同：{name}")
                path = self._project_artifact(descriptor.get("path"))
                expected_hash = descriptor.get("sha256")
                if not isinstance(expected_hash, str) or sha256_file(path) != expected_hash:
                    raise ValueError(f"Knowledge Ready 资产哈希不一致：{name}")
                verified[name] = path

            knowledge_db = verified["knowledge_db"]
            self._sqlite_integrity(knowledge_db)
            with sqlite3.connect(f"file:{knowledge_db}?mode=ro", uri=True) as connection:
                metadata = {
                    key: json.loads(value)
                    for key, value in connection.execute("SELECT key, value FROM metadata")
                }
                if metadata.get("corpus_sha256") != corpus_sha256:
                    raise ValueError("报告与知识库语料哈希不一致")
                if metadata.get("reliable_end") != reliable_end:
                    raise ValueError("报告与知识库可靠范围不一致")
                reliable_ids = {
                    row[0]
                    for row in connection.execute(
                        "SELECT chapter_id FROM chapters WHERE reliable = 1"
                    )
                }
                entity_count = connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
                mention_count = connection.execute(
                    "SELECT COUNT(*) FROM mentions m JOIN chapters c USING(chapter_id) "
                    "WHERE c.reliable = 1"
                ).fetchone()[0]
            if not reliable_ids or entity_count < 1 or mention_count < 1:
                raise ValueError("知识库缺少可用章节、实体或可靠证据")
            self._validate_source_binding(
                source_manifest, knowledge_db, corpus_sha256, reliable_end
            )

            graph = json.loads(verified["graph"].read_text(encoding="utf-8"))
            nodes = graph.get("nodes") if isinstance(graph, dict) else None
            links = graph.get("links") if isinstance(graph, dict) else None
            if not isinstance(nodes, list) or not nodes or not isinstance(links, list) or not links:
                raise ValueError("知识图谱没有可遍历节点或关系")
            node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
            if (
                len(node_ids) != len(nodes)
                or any(not isinstance(node_id, str) or not node_id for node_id in node_ids)
                or len(set(node_ids)) != len(node_ids)
            ):
                raise ValueError("知识图谱节点 ID 不稳定或重复")
            node_id_set = set(node_ids)
            for link in links:
                if (
                    not isinstance(link, dict)
                    or link.get("source") not in node_id_set
                    or link.get("target") not in node_id_set
                ):
                    raise ValueError("知识图谱存在悬空关系")
            graph_chapters = {
                node.get("id", "").removeprefix("chapter:")
                for node in nodes
                if isinstance(node, dict) and node.get("node_kind") == "chapter"
            }
            if not graph_chapters or not graph_chapters.issubset(reliable_ids):
                raise ValueError("知识图谱包含隔离范围或缺少可靠章节")
            self._validate_graph_binding(graph, knowledge_db)

            memory_index = verified["memory_index"]
            self._sqlite_integrity(memory_index)
            with sqlite3.connect(f"file:{memory_index}?mode=ro", uri=True) as connection:
                memory_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
            if not memory_tables.intersection({"memory_docs", "memory_sections"}):
                raise ValueError("长期记忆索引结构无效")
        except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
            raise ProductionError(
                f"Knowledge Ready 语义校验失败：{exc}", "invalid_knowledge_ready"
            ) from exc

    def review_candidate(self, candidate_id, decision, reviewer, notes=""):
        if decision not in {"approve", "reject"}:
            raise ProductionError("评审决定只能是 approve 或 reject", "invalid_review")
        if not reviewer:
            raise ProductionError("评审人不能为空", "invalid_reviewer")
        stale_input = False
        result = None
        with self.store.transaction(immediate=True) as connection:
            candidate = connection.execute(
                "SELECT * FROM candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise ProductionError("候选不存在", "candidate_not_found")
            if candidate["status"] == "stale_input":
                raise ProductionError("候选已因输入变化失效", "stale_input")
            if candidate["status"] != "pending":
                raise ProductionError("候选已经完成评审", "candidate_already_reviewed")
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (candidate["task_id"],)).fetchone()
            run = connection.execute("SELECT * FROM runs WHERE id = ?", (candidate["run_id"],)).fetchone()
            if task["status"] != "submitted" or run is None or run["status"] != "submitted":
                raise ProductionError("候选所属任务或运行状态已经失效", "invalid_candidate_state")
            current_hash, _ = self._input_snapshot(connection, task)
            if current_hash != run["input_hash"]:
                connection.execute(
                    "UPDATE candidates SET status = 'stale_input' WHERE id = ?", (candidate_id,)
                )
                self._mark_run_stale(connection, run["id"], task["id"])
                stale_input = True
            else:
                connection.execute(
                    "INSERT INTO reviews(candidate_id, decision, reviewer, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                    (candidate_id, decision, reviewer, notes, utc_now()),
                )
                if decision == "approve":
                    previous = task["accepted_candidate_id"]
                    connection.execute(
                        "UPDATE candidates SET status = 'accepted' WHERE id = ?", (candidate_id,)
                    )
                    connection.execute(
                        "UPDATE candidates SET status = 'superseded' "
                        "WHERE task_id = ? AND id <> ? AND status = 'pending'",
                        (task["id"], candidate_id),
                    )
                    connection.execute(
                        "UPDATE tasks SET status = 'completed', accepted_candidate_id = ?, "
                        "updated_at = ? WHERE id = ?",
                        (candidate_id, utc_now(), task["id"]),
                    )
                    connection.execute(
                        "UPDATE runs SET status = 'completed', finished_at = ? WHERE id = ?",
                        (utc_now(), candidate["run_id"]),
                    )
                    if previous is not None and previous != candidate_id:
                        connection.execute(
                            "UPDATE candidates SET status = 'superseded' WHERE id = ?", (previous,)
                        )
                        self._invalidate_after_task(connection, task["id"])
                    event_type = "candidate.approved"
                else:
                    connection.execute(
                        "UPDATE candidates SET status = 'rejected' WHERE id = ?", (candidate_id,)
                    )
                    pending = connection.execute(
                        "SELECT COUNT(*) AS count FROM candidates "
                        "WHERE task_id = ? AND status = 'pending'",
                        (task["id"],),
                    ).fetchone()["count"]
                    if pending == 0:
                        connection.execute(
                            "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                            (utc_now(), task["id"]),
                        )
                        connection.execute(
                            "UPDATE runs SET status = 'rejected', finished_at = ? WHERE id = ?",
                            (utc_now(), candidate["run_id"]),
                        )
                    event_type = "candidate.rejected"
                self.store.append_event(
                    connection,
                    event_type,
                    "candidate",
                    candidate_id,
                    {"task_id": task["id"], "reviewer": reviewer, "notes": notes},
                )
                self._refresh_blocked_tasks(connection)
                result = {"candidate_id": candidate_id, "decision": decision}
        if stale_input:
            raise ProductionError("候选输入已经变化，不能评审", "stale_input")
        return result

    def approve_gate(self, gate_id, reviewer, notes="", human_confirmed=False):
        if gate_id not in GATE_ORDER:
            raise ProductionError("审批门不存在", "gate_not_found")
        if human_confirmed is not True:
            raise ProductionError("审批门必须记录用户的明确人工确认", "human_confirmation_required")
        if not reviewer or reviewer.strip().lower() in {"codex", "ai", "agent", "assistant"}:
            raise ProductionError("审批人必须是明确命名的人类责任人", "invalid_gate_reviewer")
        with self.store.transaction(immediate=True) as connection:
            gate = connection.execute("SELECT * FROM gates WHERE id = ?", (gate_id,)).fetchone()
            if gate is None:
                raise ProductionError("尚未导入审批门", "gate_not_found")
            if gate["status"] == "approved":
                raise ProductionError("审批门已经批准，不能重复批准", "gate_already_approved")
            earlier = connection.execute(
                "SELECT id FROM gates WHERE position < ? AND status <> 'approved' ORDER BY position",
                (gate["position"],),
            ).fetchall()
            if earlier:
                raise ProductionError("前序审批门尚未通过", "previous_gate_pending")
            evidence_ids = json.loads(gate["evidence_tasks_json"])
            placeholders = ",".join("?" for _ in evidence_ids)
            evidence = connection.execute(
                f"SELECT id, status, accepted_candidate_id FROM tasks WHERE id IN ({placeholders})",
                evidence_ids,
            ).fetchall()
            incomplete = sorted(row["id"] for row in evidence if row["status"] != "completed")
            if incomplete:
                raise ProductionError(
                    f"审批门证据任务尚未完成：{','.join(incomplete)}",
                    "gate_evidence_incomplete",
                )
            hashes = []
            for row in sorted(evidence, key=lambda item: item["id"]):
                candidate = connection.execute(
                    "SELECT content_hash FROM candidates WHERE id = ?", (row["accepted_candidate_id"],)
                ).fetchone()
                hashes.append(f"{row['id']}:{candidate['content_hash']}")
            evidence_hash = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
            connection.execute(
                "UPDATE gates SET status = 'approved', approved_at = ?, reviewer = ?, notes = ?, "
                "evidence_hash = ? WHERE id = ?",
                (utc_now(), reviewer, notes, evidence_hash, gate_id),
            )
            connection.execute(
                "INSERT INTO gate_decisions(gate_id, reviewer, notes, evidence_hash, "
                "human_confirmed, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                (gate_id, reviewer, notes, evidence_hash, utc_now()),
            )
            self.store.append_event(
                connection,
                "gate.approved",
                "gate",
                gate_id,
                {"reviewer": reviewer, "notes": notes, "evidence_hash": evidence_hash},
            )
            self._refresh_blocked_tasks(connection)
        return {"id": gate_id, "status": "approved", "evidence_hash": evidence_hash}

    def retry_task(self, task_id, reason):
        with self.store.transaction(immediate=True) as connection:
            task = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ProductionError("任务不存在", "task_not_found")
            if task["status"] not in {"completed", "stale", "failed", "rejected"}:
                raise ProductionError("当前任务状态不能重试", "invalid_retry_status")
            if not self._prerequisites_met(connection, task):
                raise ProductionError("任务前置条件尚未满足", "retry_blocked")
            connection.execute(
                "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                (utc_now(), task_id),
            )
            self.store.append_event(
                connection, "task.retry_requested", "task", task_id, {"reason": reason}
            )
        return {"task_id": task_id, "status": "ready"}

    def recover(self, now=None):
        current = now or utc_now()
        with self.store.transaction(immediate=True) as connection:
            expired = connection.execute(
                "SELECT id, task_id FROM runs WHERE status = 'leased' AND lease_until < ?",
                (current,),
            ).fetchall()
            for run in expired:
                connection.execute(
                    "UPDATE runs SET status = 'interrupted', finished_at = ?, error = ? WHERE id = ?",
                    (current, "租约过期", run["id"]),
                )
                connection.execute(
                    "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ? AND status = 'leased'",
                    (current, run["task_id"]),
                )
                self.store.append_event(
                    connection,
                    "run.interrupted",
                    "run",
                    run["id"],
                    {"task_id": run["task_id"], "reason": "lease_expired"},
                )
        return {"recovered": len(expired)}

    def export_delivery(self):
        with self.store.connect() as connection:
            project = dict(connection.execute("SELECT * FROM project").fetchone())
            incomplete_gates = connection.execute(
                "SELECT id FROM gates WHERE status <> 'approved' ORDER BY position"
            ).fetchall()
            incomplete_tasks = connection.execute(
                "SELECT id, status FROM tasks WHERE status <> 'completed' ORDER BY rowid"
            ).fetchall()
            if incomplete_gates or incomplete_tasks:
                raise ProductionError("项目尚未完成，不能导出最终交付", "delivery_incomplete")
            gates = [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "reviewer": row["reviewer"],
                    "notes": row["notes"],
                    "evidence_hash": row["evidence_hash"],
                    "approved_at": row["approved_at"],
                }
                for row in connection.execute("SELECT * FROM gates ORDER BY position")
            ]
            accepted = [
                {
                    "task_id": row["task_id"],
                    "candidate_id": row["id"],
                    "content_hash": row["content_hash"],
                    "object_path": row["object_path"],
                    "media_type": row["media_type"],
                    "metadata": json.loads(row["metadata_json"]),
                }
                for row in connection.execute(
                    "SELECT * FROM candidates WHERE status = 'accepted' ORDER BY task_id"
                )
            ]
            gate_decisions = [
                {
                    "id": row["id"],
                    "gate_id": row["gate_id"],
                    "reviewer": row["reviewer"],
                    "notes": row["notes"],
                    "evidence_hash": row["evidence_hash"],
                    "human_confirmed": bool(row["human_confirmed"]),
                    "created_at": row["created_at"],
                }
                for row in connection.execute("SELECT * FROM gate_decisions ORDER BY id")
            ]
            event_count = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
        continuity_path = self._continuity_path(project.get("continuity_state"))
        manifest = {
            "schema_version": "1.0",
            "project": {"id": project["id"], "title": project["title"]},
            "gates": gates,
            "gate_decisions": gate_decisions,
            "accepted_candidates": accepted,
            "continuity_state": {
                "path": project.get("continuity_state"),
                "content_hash": sha256_file(continuity_path) if continuity_path else None,
            },
            "event_count": event_count,
            "exported_at": utc_now(),
        }
        manifest["manifest_hash"] = hashlib.sha256(encode_json(manifest).encode("utf-8")).hexdigest()
        relative = Path("exports") / "delivery-manifest.json"
        (self.project_dir / relative).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.store.transaction() as connection:
            self.store.append_event(
                connection,
                "delivery.exported",
                "project",
                project["id"],
                {"manifest_path": relative.as_posix(), "manifest_hash": manifest["manifest_hash"]},
            )
        return {"manifest_path": relative.as_posix(), "manifest_hash": manifest["manifest_hash"]}

    def status(self):
        with self.store.connect() as connection:
            project = dict(connection.execute("SELECT * FROM project").fetchone())
            task_rows = connection.execute("SELECT id, status FROM tasks ORDER BY id").fetchall()
            counts = Counter(row["status"] for row in task_rows)
            gates = [
                {
                    "id": row["id"],
                    "status": row["status"],
                    "evidence_tasks": json.loads(row["evidence_tasks_json"]),
                    "reviewer": row["reviewer"],
                    "evidence_hash": row["evidence_hash"],
                }
                for row in connection.execute("SELECT * FROM gates ORDER BY position")
            ]
            event_count = connection.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
            run_counts = Counter(
                row["status"] for row in connection.execute("SELECT status FROM runs")
            )
            candidate_counts = Counter(
                row["status"] for row in connection.execute("SELECT status FROM candidates")
            )
            input_count = connection.execute(
                "SELECT COUNT(*) AS count FROM input_artifacts"
            ).fetchone()["count"]
        return {
            "project": {"id": project["id"], "title": project["title"]},
            "tasks_by_status": dict(sorted(counts.items())),
            "ready_tasks": [row["id"] for row in task_rows if row["status"] == "ready"],
            "task_statuses": {row["id"]: row["status"] for row in task_rows},
            "gates": gates,
            "event_count": event_count,
            "run_counts": dict(sorted(run_counts.items())),
            "candidate_counts": dict(sorted(candidate_counts.items())),
            "input_count": input_count,
        }

    def _input_snapshot(self, connection, task):
        dependencies = []
        for dependency_id in json.loads(task["depends_on_json"]):
            row = connection.execute(
                "SELECT tasks.id, candidates.id AS candidate_id, candidates.content_hash "
                "FROM tasks LEFT JOIN candidates ON candidates.id = tasks.accepted_candidate_id "
                "WHERE tasks.id = ?",
                (dependency_id,),
            ).fetchone()
            dependencies.append(
                {
                    "task_id": row["id"],
                    "candidate_id": row["candidate_id"],
                    "content_hash": row["content_hash"],
                }
            )
        project = connection.execute("SELECT continuity_state FROM project").fetchone()
        continuity_path = self._continuity_path(project["continuity_state"])
        snapshot = {
            "task_id": task["id"],
            "inputs": json.loads(task["inputs_json"]),
            "dependencies": dependencies,
            "continuity_state_hash": sha256_file(continuity_path) if continuity_path else None,
            "input_artifacts": [],
        }
        for input_id in snapshot["inputs"].get("artifacts", []):
            artifact = connection.execute(
                "SELECT id, role, content_hash FROM input_artifacts WHERE id = ?", (input_id,)
            ).fetchone()
            if artifact is None:
                raise ProductionError(f"任务引用的输入尚未登记：{input_id}", "missing_input_artifact")
            snapshot["input_artifacts"].append(dict(artifact))
        references = {
            "dependencies": dependencies,
            "input_artifacts": snapshot["input_artifacts"],
            "continuity_state": {
                "path": project["continuity_state"],
                "content_hash": snapshot["continuity_state_hash"],
            },
        }
        return hashlib.sha256(encode_json(snapshot).encode("utf-8")).hexdigest(), references

    def _continuity_path(self, relative_path):
        if not relative_path:
            return None
        unresolved = self.project_dir / relative_path
        if unresolved.is_symlink():
            raise ProductionError("连续性状态不能是符号链接", "unsafe_continuity_path")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(self.project_dir)
        except ValueError as exc:
            raise ProductionError("连续性状态路径越出项目目录", "unsafe_continuity_path") from exc
        return candidate if candidate.is_file() else None

    def _refresh_blocked_tasks(self, connection):
        changed = True
        while changed:
            changed = False
            for task in connection.execute("SELECT * FROM tasks WHERE status = 'blocked'").fetchall():
                if self._prerequisites_met(connection, task):
                    connection.execute(
                        "UPDATE tasks SET status = 'ready', updated_at = ? WHERE id = ?",
                        (utc_now(), task["id"]),
                    )
                    changed = True

    @staticmethod
    def _prerequisites_met(connection, task):
        dependencies = json.loads(task["depends_on_json"])
        for dependency in dependencies:
            row = connection.execute("SELECT status FROM tasks WHERE id = ?", (dependency,)).fetchone()
            if row is None or row["status"] != "completed":
                return False
        if task["required_gate"] is not None:
            gate = connection.execute(
                "SELECT status FROM gates WHERE id = ?", (task["required_gate"],)
            ).fetchone()
            if gate is None or gate["status"] != "approved":
                return False
        return True

    def _invalidate_after_task(self, connection, task_id):
        rows = connection.execute("SELECT id, depends_on_json, status FROM tasks").fetchall()
        descendants = set()
        frontier = {task_id}
        while frontier:
            parent = frontier.pop()
            for row in rows:
                if row["id"] not in descendants and parent in json.loads(row["depends_on_json"]):
                    descendants.add(row["id"])
                    frontier.add(row["id"])
        for descendant in descendants:
            row = next(item for item in rows if item["id"] == descendant)
            if row["status"] in {"completed", "ready", "leased", "submitted"}:
                connection.execute(
                    "UPDATE tasks SET status = 'stale', updated_at = ? WHERE id = ?",
                    (utc_now(), descendant),
                )
            connection.execute(
                "UPDATE candidates SET status = 'stale_input' "
                "WHERE task_id = ? AND status = 'pending'",
                (descendant,),
            )
            connection.execute(
                "UPDATE runs SET status = 'stale_input', finished_at = ? "
                "WHERE task_id = ? AND status IN ('leased', 'submitted')",
                (utc_now(), descendant),
            )
        affected_position = None
        for gate in connection.execute("SELECT * FROM gates ORDER BY position").fetchall():
            if ({task_id} | descendants).intersection(json.loads(gate["evidence_tasks_json"])):
                affected_position = gate["position"]
                break
        if affected_position is not None:
            connection.execute(
                "UPDATE gates SET status = 'invalidated' WHERE position >= ? AND status = 'approved'",
                (affected_position,),
            )
        self.store.append_event(
            connection,
            "lineage.invalidated",
            "task",
            task_id,
            {"descendants": sorted(descendants), "gate_position": affected_position},
        )

    def _mark_run_stale(self, connection, run_id, task_id):
        now = utc_now()
        connection.execute(
            "UPDATE runs SET status = 'stale_input', finished_at = ? WHERE id = ?",
            (now, run_id),
        )
        connection.execute(
            "UPDATE tasks SET status = 'ready', updated_at = ? "
            "WHERE id = ? AND status IN ('leased', 'submitted')",
            (now, task_id),
        )
        self.store.append_event(
            connection, "run.stale_input", "run", run_id, {"task_id": task_id}
        )

    @staticmethod
    def _validate_plan(plan):
        if not isinstance(plan, dict):
            raise ProductionError("制片计划必须是 JSON 对象", "invalid_plan")
        for field in ("schema_version", "project", "tasks", "gates"):
            if field not in plan:
                raise ProductionError(f"制片计划缺少字段：{field}", "invalid_plan")
        if not isinstance(plan["schema_version"], str) or not plan["schema_version"]:
            raise ProductionError("schema_version 必须是非空字符串", "invalid_plan")
        project = plan["project"]
        if not isinstance(project, dict) or any(
            not isinstance(project.get(field), str) or not project.get(field)
            for field in ("id", "title")
        ):
            raise ProductionError("project 必须包含非空的 id 和 title", "invalid_plan")
        if not isinstance(plan["gates"], list) or any(
            not isinstance(gate, dict) for gate in plan["gates"]
        ):
            raise ProductionError("gates 必须是对象数组", "invalid_plan")
        if [gate.get("id") for gate in plan["gates"]] != GATE_ORDER:
            raise ProductionError("制片计划必须包含固定顺序的四个审批门", "invalid_gates")

        tasks = plan["tasks"]
        if not isinstance(tasks, list) or not tasks:
            raise ProductionError("制片计划至少需要一个任务", "invalid_plan")
        if any(not isinstance(task, dict) for task in tasks):
            raise ProductionError("tasks 必须是对象数组", "invalid_plan")
        ids = [task.get("id") for task in tasks]
        if any(not isinstance(task_id, str) or not task_id for task_id in ids):
            raise ProductionError("任务 ID 必须是非空字符串", "invalid_task_id")
        if len(ids) != len(set(ids)):
            raise ProductionError("任务 ID 必须存在且唯一", "invalid_task_id")
        id_set = set(ids)
        gate_set = set(GATE_ORDER)
        for task in tasks:
            missing = REQUIRED_TASK_FIELDS - set(task)
            if missing:
                raise ProductionError(
                    f"任务 {task.get('id', '<unknown>')} 缺少字段：{','.join(sorted(missing))}",
                    "invalid_task",
                )
            if any(
                not isinstance(task[field], str) or not task[field]
                for field in ("kind", "stage")
            ):
                raise ProductionError(
                    f"任务 {task['id']} 的 kind 和 stage 必须是非空字符串", "invalid_task"
                )
            if not isinstance(task["depends_on"], list) or any(
                not isinstance(item, str) or not item for item in task["depends_on"]
            ):
                raise ProductionError(f"任务 {task['id']} 的 depends_on 必须是字符串数组", "invalid_task")
            if not isinstance(task["inputs"], dict):
                raise ProductionError(f"任务 {task['id']} 的 inputs 必须是对象", "invalid_task")
            if not isinstance(task["output_contract"], dict) or any(
                not isinstance(task["output_contract"].get(field), str)
                or not task["output_contract"].get(field)
                for field in ("media_type", "purpose")
            ):
                raise ProductionError(
                    f"任务 {task['id']} 的 output_contract 必须包含 media_type 和 purpose",
                    "invalid_task",
                )
            unknown = set(task["depends_on"]) - id_set
            if unknown:
                raise ProductionError(
                    f"任务 {task['id']} 引用了不存在的依赖：{','.join(sorted(unknown))}",
                    "unknown_dependency",
                )
            if task["required_gate"] is not None and not isinstance(
                task["required_gate"], str
            ):
                raise ProductionError(
                    f"任务 {task['id']} 的 required_gate 必须是字符串或 null", "invalid_task"
                )
            if task["required_gate"] is not None and task["required_gate"] not in gate_set:
                raise ProductionError(f"任务 {task['id']} 引用了未知审批门", "unknown_gate")

        tasks_by_id = {task["id"]: task for task in tasks}
        for position, gate in enumerate(plan["gates"]):
            evidence = gate.get("evidence_tasks")
            if (
                not isinstance(evidence, list)
                or not evidence
                or any(not isinstance(task_id, str) or not task_id for task_id in evidence)
            ):
                raise ProductionError(f"审批门 {gate['id']} 缺少证据任务", "invalid_gate_evidence")
            unknown = set(evidence) - id_set
            if unknown:
                raise ProductionError(
                    f"审批门 {gate['id']} 引用了不存在的证据任务：{','.join(sorted(unknown))}",
                    "unknown_gate_evidence",
                )
            expected_gate = None if position == 0 else GATE_ORDER[position - 1]
            unreachable = [
                task_id
                for task_id in evidence
                if tasks_by_id[task_id]["required_gate"] != expected_gate
            ]
            if unreachable:
                expected = expected_gate or "无审批门"
                raise ProductionError(
                    f"审批门 {gate['id']} 的证据任务必须由前一审批门 {expected} 解锁："
                    f"{','.join(sorted(unreachable))}",
                    "unreachable_gate_evidence",
                )
        ProductionService._assert_acyclic(tasks)

    @staticmethod
    def _assert_acyclic(tasks):
        graph = {task["id"]: task["depends_on"] for task in tasks}
        visiting = set()
        visited = set()

        def visit(task_id):
            if task_id in visiting:
                raise ProductionError("任务图存在循环依赖", "dependency_cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)
