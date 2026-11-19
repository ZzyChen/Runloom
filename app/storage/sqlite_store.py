from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]

class SQLiteStore:
    def __init__(self,db_path: str|Path|None = None) -> None:
        if db_path is None:
            db_path = PROJECT_ROOT/"db"/"runloom.sqlite3"
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
    def create_run(self,run_id: str, user_input: str):
        conn = self._connect()
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """
                    INSERT INTO runs(run_id, user_input, status, created_at)
                    VALUES(?,?,?,?)
                """,(run_id,user_input,"running",now)
            )
            conn.execute(
                """
                    INSERT INTO events(run_id,sequence_no, event_type, payload_json,created_at)
                    VALUES(?,?,?,?,?)
                """,(run_id,1,"run_started",json.dumps({},ensure_ascii=False),now)
                        )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    def append_event(self,run_id:str, event_type:str,payload:dict[str,Any]|None = None) -> int:
        payload_json = json.dumps(
        payload if payload is not None else {},
        ensure_ascii=False,
        )
        created_at = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            # 先取得写锁，再计算序号，避免并发写入时分配到相同序号
            conn.execute("BEGIN IMMEDIATE")

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

            conn.commit()
            return sequence_no
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()