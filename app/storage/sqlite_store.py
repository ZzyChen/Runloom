from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SQLiteStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = PROJECT_ROOT / "db" / "runloom.sqlite3"
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    user_input TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('running', 'succeeded', 'failed')),
                    final_output TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id)
                        REFERENCES runs(run_id)
                        ON DELETE CASCADE,
                    UNIQUE (run_id, sequence_no)
                )
            """)

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def create_run(self, run_id: str, user_input: str):
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """
                    INSERT INTO runs(run_id, user_input, status, created_at)
                    VALUES(?,?,?,?)
                """,
                (run_id, user_input, "running", now),
            )
            conn.execute(
                """
                    INSERT INTO events(run_id,sequence_no, event_type, payload_json,created_at)
                    VALUES(?,?,?,?,?)
                """,
                (run_id, 1, "run_started", json.dumps({}, ensure_ascii=False), now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _append_event(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ):
        payload_json = json.dumps(
            payload if payload is not None else {}, ensure_ascii=False
        )
        created_at = datetime.now(timezone.utc).isoformat()

        row = conn.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
            FROM events
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        sequence_no = row["next_sequence"]

        conn.execute(
            """
            INSERT INTO events (
                run_id,
                sequence_no,
                event_type,
                payload_json,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, sequence_no, event_type, payload_json, created_at),
        )
        return sequence_no

    def append_event(
        self, run_id: str, event_type: str, payload: dict[str, Any] | None = None
    ) -> int:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            sequence_no = self._append_event(conn, run_id, event_type, payload)
            conn.commit()
            return sequence_no
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def finish_run(
        self,
        run_id: str,
        final_output: str | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        status = "succeeded" if error_message is None else "failed"
        try:
            cur = conn.execute(
                """
                    UPDATE runs
                    SET status=?,final_output=?,error_message=?,finished_at=?
                    WHERE run_id = ? AND status = 'running'
                """,
                (status, final_output, error_message, now, run_id),
            )

            if cur.rowcount == 0:
                # run 不存在，或已经不是 running（重复 finish）
                raise ValueError(f"run {run_id!r} 不存在或已结束，无法 finish")

            self._append_event(
                conn,
                run_id,
                "run_finished" if status == "succeeded" else "run_failed",
                {"status": status, "error": error_message},
            )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_events(
        self, run_id: str, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE run_id = ? AND sequence_no > ?
                ORDER BY sequence_no ASC
                LIMIT ?
                """,
                (run_id, after_sequence, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
