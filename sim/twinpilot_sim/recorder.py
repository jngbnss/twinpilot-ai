"""Run recording: telemetry JSONL, events JSONL, run metadata, Parquet export."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from twinpilot_sim.schema import RunMetadata, TelemetryRecord

TELEMETRY = "telemetry.jsonl"
EVENTS = "events.jsonl"
META = "meta.json"
PARQUET = "telemetry.parquet"


class RunRecorder:
    def __init__(self, root: Path | str, meta: RunMetadata):
        self.meta = meta
        self.dir = Path(root) / meta.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        # long-lived handles, closed in close()
        self._tel = open(self.dir / TELEMETRY, "w", encoding="utf-8", newline="\n")  # noqa: SIM115
        self._ev = open(self.dir / EVENTS, "w", encoding="utf-8", newline="\n")  # noqa: SIM115
        self.count = 0
        self._write_meta()

    def _write_meta(self) -> None:
        (self.dir / META).write_text(self.meta.model_dump_json(indent=2), encoding="utf-8")

    def write(self, records: Iterable[TelemetryRecord]) -> None:
        for r in records:
            self._tel.write(r.model_dump_json())
            self._tel.write("\n")
            self.count += 1

    def log_event(self, event: dict[str, Any]) -> None:
        self._ev.write(json.dumps(event, sort_keys=True))
        self._ev.write("\n")

    def close(self, result: dict[str, Any] | None = None, duration: float | None = None) -> Path:
        self._tel.close()
        self._ev.close()
        if result is not None:
            self.meta.result = result
        if duration is not None:
            self.meta.duration = duration
        self._write_meta()
        return self.dir


def read_records(run_dir: Path | str) -> Iterator[TelemetryRecord]:
    with open(Path(run_dir) / TELEMETRY, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield TelemetryRecord.model_validate_json(line)


def read_events(run_dir: Path | str) -> list[dict[str, Any]]:
    path = Path(run_dir) / EVENTS
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def read_meta(run_dir: Path | str) -> RunMetadata:
    return RunMetadata.model_validate_json((Path(run_dir) / META).read_text(encoding="utf-8"))


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def telemetry_hash(run_dir: Path | str) -> str:
    return file_sha256(Path(run_dir) / TELEMETRY)


def to_parquet(run_dir: Path | str) -> Path:
    run_dir = Path(run_dir)
    cols: dict[str, list] = {k: [] for k in TelemetryRecord.model_fields}
    for r in read_records(run_dir):
        for k in cols:
            v = getattr(r, k)
            cols[k].append(v.value if hasattr(v, "value") else v)
    out = run_dir / PARQUET
    pq.write_table(pa.table(cols), out, compression="zstd")
    return out
