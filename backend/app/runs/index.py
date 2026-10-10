"""SQLite index of recorded runs (the data itself stays in runs/<run_id>/)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from twinpilot_sim.recorder import META, read_meta

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT,
    level TEXT,
    scenario TEXT,
    seed INTEGER,
    duration REAL,
    faults TEXT,
    result TEXT,
    path TEXT
)
"""


class RunIndex:
    def __init__(self, db_path: Path, runs_dir: Path):
        self.db_path = Path(db_path)
        self.runs_dir = Path(runs_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert(self, run_dir: Path) -> None:
        m = read_meta(run_dir)
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    m.run_id,
                    m.created_at,
                    m.level,
                    m.scenario,
                    m.seed,
                    m.duration,
                    json.dumps([f.model_dump(mode="json") for f in m.faults]),
                    json.dumps(m.result),
                    str(run_dir),
                ),
            )

    def sync(self) -> int:
        """Index every run directory found on disk (e.g. created by the CLI)."""
        n = 0
        with self._conn() as c:  # drop rows whose run directory was deleted
            for row in c.execute("SELECT run_id, path FROM runs").fetchall():
                if not (Path(row["path"]) / META).exists():
                    c.execute("DELETE FROM runs WHERE run_id=?", (row["run_id"],))
        if not self.runs_dir.exists():
            return 0
        for meta in self.runs_dir.glob(f"*/{META}"):
            try:
                self.upsert(meta.parent)
                n += 1
            except Exception:  # noqa: BLE001 - skip half-written runs
                continue
        return n

    def list(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM runs ORDER BY created_at DESC, run_id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["faults"] = json.loads(d["faults"] or "[]")
            d["result"] = json.loads(d["result"] or "{}")
            out.append(d)
        return out

    def path(self, run_id: str) -> Path | None:
        with self._conn() as c:
            row = c.execute("SELECT path FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row and Path(row["path"]).exists():
            return Path(row["path"])
        candidate = self.runs_dir / run_id
        return candidate if (candidate / META).exists() else None
