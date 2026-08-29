import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    continuity_state TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    stage TEXT NOT NULL,
    depends_on_json TEXT NOT NULL,
    required_gate TEXT,
    inputs_json TEXT NOT NULL,
    output_contract_json TEXT NOT NULL,
    status TEXT NOT NULL,
    accepted_candidate_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE gates (
    id TEXT PRIMARY KEY,
    position INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,
    evidence_tasks_json TEXT NOT NULL,
    approved_at TEXT,
    reviewer TEXT,
    notes TEXT,
    evidence_hash TEXT
);

CREATE TABLE gate_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id TEXT NOT NULL REFERENCES gates(id),
    reviewer TEXT NOT NULL,
    notes TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    human_confirmed INTEGER NOT NULL CHECK(human_confirmed = 1),
    created_at TEXT NOT NULL
);

CREATE TABLE input_artifacts (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    object_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    media_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt INTEGER NOT NULL,
    executor TEXT NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    packet_json TEXT NOT NULL,
    lease_until TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT,
    UNIQUE(task_id, attempt)
);

CREATE TABLE candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    run_id INTEGER NOT NULL REFERENCES runs(id),
    object_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    media_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_runs_task ON runs(task_id);
CREATE INDEX idx_candidates_task ON candidates(task_id);
CREATE INDEX idx_gate_decisions_gate ON gate_decisions(gate_id);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encode_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, database_path):
        self.database_path = Path(database_path)

    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def transaction(self, immediate=False):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def append_event(connection, event_type, entity_type, entity_id, payload):
        connection.execute(
            "INSERT INTO events(type, entity_type, entity_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_type, entity_type, str(entity_id), encode_json(payload), utc_now()),
        )
