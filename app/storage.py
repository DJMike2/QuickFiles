"""SQLite operation journal, with one connection per instance and thread."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JournalOp:
    batch_id: int
    op_order: int
    op_type: str
    src_abs: str
    dst_abs: str
    src_existed: bool = True
    dst_existed_pre: bool = False
    succeeded: bool = False
    error_msg: str = ""
    id: int = 0
    undone: bool = False
    sha256: str = ""


@dataclass
class JournalBatch:
    id: int
    batch_uuid: str
    root: str
    applied_at: float
    action_count: int
    undone: bool
    ops: list[JournalOp] = field(default_factory=list)


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connection()
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS journal_batch (
                id INTEGER PRIMARY KEY, batch_uuid TEXT NOT NULL UNIQUE,
                root TEXT NOT NULL, applied_at REAL NOT NULL,
                action_count INTEGER DEFAULT 0, undone INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS journal_op (
                id INTEGER PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES journal_batch(id),
                op_order INTEGER NOT NULL, op_type TEXT NOT NULL,
                src_abs TEXT NOT NULL, dst_abs TEXT NOT NULL,
                src_existed INTEGER DEFAULT 1, dst_existed_pre INTEGER DEFAULT 0,
                succeeded INTEGER DEFAULT 0, error_msg TEXT DEFAULT '',
                undone INTEGER DEFAULT 0
            );
        """)
        # Older journals lacked a per-operation undo flag.
        columns = {row[1] for row in connection.execute("PRAGMA table_info(journal_op)")}
        if "undone" not in columns:
            connection.execute("ALTER TABLE journal_op ADD COLUMN undone INTEGER DEFAULT 0")
        if "sha256" not in columns:
            connection.execute("ALTER TABLE journal_op ADD COLUMN sha256 TEXT DEFAULT ''")
        connection.commit()

    def connection(self):
        if getattr(self._local, "connection", None) is None:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._local.connection = connection
        return self._local.connection

    def close(self):
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            self._local.connection = None

    def _write(self, sql, parameters=()):
        connection = self.connection()
        with connection:
            return connection.execute(sql, parameters).lastrowid

    def begin_batch(self, root, action_count):
        return self._write(
            "INSERT INTO journal_batch(batch_uuid,root,applied_at,action_count) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), root, time.time(), action_count),
        )

    def record_op(self, op: JournalOp):
        # Commit before changing the filesystem, rather than at the end of a batch.
        return self._write(
            """INSERT INTO journal_op(batch_id,op_order,op_type,src_abs,dst_abs,
               src_existed,dst_existed_pre,error_msg,sha256) VALUES(?,?,?,?,?,?,?,?,?)""",
            (op.batch_id, op.op_order, op.op_type, op.src_abs, op.dst_abs,
             op.src_existed, op.dst_existed_pre, "pending", op.sha256),
        )

    def mark_op_success(self, op_id):
        self._write("UPDATE journal_op SET succeeded=1,error_msg='' WHERE id=?", (op_id,))

    def mark_op_failed(self, op_id, message):
        self._write("UPDATE journal_op SET succeeded=0,error_msg=? WHERE id=?", (message, op_id))

    def mark_op_verification_failed(self, op_id, message):
        # The move happened and must remain visible to undo/recovery.
        self._write("UPDATE journal_op SET succeeded=1,error_msg=? WHERE id=?", (message, op_id))

    def mark_op_undone(self, op_id):
        self._write("UPDATE journal_op SET undone=1 WHERE id=?", (op_id,))

    def mark_batch_undone(self, batch_id):
        self._write("UPDATE journal_batch SET undone=1 WHERE id=?", (batch_id,))

    def get_latest_batch(self):
        row = self.connection().execute(
            "SELECT id FROM journal_batch WHERE undone=0 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return self.get_batch_by_id(row["id"]) if row else None

    def get_batch_by_id(self, batch_id):
        row = self.connection().execute(
            "SELECT * FROM journal_batch WHERE id=?", (batch_id,)
        ).fetchone()
        if row is None:
            return None
        records = self.connection().execute(
            "SELECT * FROM journal_op WHERE batch_id=? ORDER BY op_order,id", (batch_id,)
        ).fetchall()
        return JournalBatch(**dict(row), ops=[JournalOp(**dict(record)) for record in records])
