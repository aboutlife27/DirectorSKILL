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
