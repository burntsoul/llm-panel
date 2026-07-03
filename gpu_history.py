from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional


DATA_DIR = Path(__file__).with_name("data")
DEFAULT_DB_PATH = DATA_DIR / "gpu_status.sqlite3"
RETENTION_DAYS = 7

SERIES = ["gpu_temp_c", "gpu_util_percent", "gpu_mem_util_percent", "last_target_xx", "last_applied_xx"]
NUMERIC_FIELDS = [
    "gpu_temp_c",
    "gpu_util_percent",
    "gpu_mem_util_percent",
    "smoothed_temp_c",
    "target_temp_c",
    "desired_fan_xx",
    "rate_limited_target_xx",
    "last_target_xx",
    "last_applied_xx",
]
STATUS_FIELDS = [
    "telemetry_ok",
    "telemetry_applicable",
    "vm_state",
    "watchdog_mode",
    "mode_reason",
    "gpu_id",
    "gpu_name",
    "last_command_ok",
    "last_error",
]

WINDOWS = {
    "5m": {"seconds": 5 * 60, "bucket_seconds": 0},
    "15m": {"seconds": 15 * 60, "bucket_seconds": 0},
    "1h": {"seconds": 60 * 60, "bucket_seconds": 15},
    "6h": {"seconds": 6 * 60 * 60, "bucket_seconds": 60},
    "24h": {"seconds": 24 * 60 * 60, "bucket_seconds": 300},
}


class GpuHistoryError(ValueError):
    pass


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _iso_z(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _int_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _bool_or_none(value: Any) -> Optional[int]:
    if value is None:
        return None
    return 1 if bool(value) else 0


def _connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_gpu_history_db(path: Path = DEFAULT_DB_PATH) -> None:
    with _connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gpu_status_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_epoch REAL NOT NULL,
                ts TEXT NOT NULL,
                telemetry_ok INTEGER,
                telemetry_applicable INTEGER,
                vm_state TEXT,
                watchdog_mode TEXT,
                mode_reason TEXT,
                gpu_id TEXT,
                gpu_name TEXT,
                gpu_temp_c REAL,
                gpu_util_percent REAL,
                gpu_mem_util_percent REAL,
                smoothed_temp_c REAL,
                target_temp_c REAL,
                desired_fan_xx REAL,
                rate_limited_target_xx REAL,
                last_target_xx REAL,
                last_applied_xx REAL,
                last_command_ok INTEGER,
                last_error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gpu_status_samples_ts_epoch ON gpu_status_samples(ts_epoch)")


def prune_gpu_history(path: Path = DEFAULT_DB_PATH, retention_days: int = RETENTION_DAYS, now: Optional[datetime.datetime] = None) -> None:
    init_gpu_history_db(path)
    current = now or _utc_now()
    cutoff = current.timestamp() - (retention_days * 24 * 60 * 60)
    with _connect(path) as conn:
        conn.execute("DELETE FROM gpu_status_samples WHERE ts_epoch < ?", (cutoff,))


def record_gpu_status_sample(
    status: dict[str, Any],
    path: Path = DEFAULT_DB_PATH,
    retention_days: int = RETENTION_DAYS,
    now: Optional[datetime.datetime] = None,
) -> None:
    init_gpu_history_db(path)
    current = _parse_iso(status.get("updated_at")) or now or _utc_now()
    sample = {
        "ts_epoch": current.timestamp(),
        "ts": _iso_z(current),
        "telemetry_ok": _bool_or_none(status.get("telemetry_ok")),
        "telemetry_applicable": _bool_or_none(status.get("telemetry_applicable")),
        "vm_state": status.get("vm_state"),
        "watchdog_mode": status.get("mode"),
        "mode_reason": status.get("mode_reason"),
        "gpu_id": status.get("gpu_id"),
        "gpu_name": status.get("gpu_name"),
        "gpu_temp_c": _float_or_none(status.get("gpu_temp_c")),
        "gpu_util_percent": _float_or_none(status.get("gpu_util_percent")),
        "gpu_mem_util_percent": _float_or_none(status.get("gpu_mem_util_percent")),
        "smoothed_temp_c": _float_or_none(status.get("smoothed_temp_c")),
        "target_temp_c": _float_or_none(status.get("target_temp_c")),
        "desired_fan_xx": _float_or_none(status.get("desired_fan_xx")),
        "rate_limited_target_xx": _float_or_none(status.get("rate_limited_target_xx")),
        "last_target_xx": _float_or_none(status.get("last_target_xx")),
        "last_applied_xx": _float_or_none(status.get("last_applied_xx")),
        "last_command_ok": _bool_or_none(status.get("last_command_ok")),
        "last_error": status.get("last_error"),
    }
    columns = ", ".join(sample)
    placeholders = ", ".join("?" for _ in sample)
    with _connect(path) as conn:
        conn.execute(
            f"INSERT INTO gpu_status_samples ({columns}) VALUES ({placeholders})",
            tuple(sample.values()),
        )

    prune_gpu_history(path=path, retention_days=retention_days, now=current)


def _row_to_point(row: sqlite3.Row) -> dict[str, Any]:
    point: dict[str, Any] = {"ts": row["ts"]}
    for field in NUMERIC_FIELDS:
        point[field] = row[field]
    for field in STATUS_FIELDS:
        value = row[field]
        if field in ("telemetry_ok", "telemetry_applicable", "last_command_ok"):
            point[field] = None if value is None else bool(value)
        else:
            point[field] = value
    return point


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _downsample_rows(rows: list[sqlite3.Row], bucket_seconds: int) -> list[dict[str, Any]]:
    if bucket_seconds <= 0:
        return [_row_to_point(row) for row in rows]

    buckets: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        bucket = int(row["ts_epoch"] // bucket_seconds) * bucket_seconds
        buckets.setdefault(bucket, []).append(row)

    points: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        bucket_rows = buckets[bucket]
        latest = max(bucket_rows, key=lambda row: row["ts_epoch"])
        point: dict[str, Any] = {"ts": _iso_z(datetime.datetime.fromtimestamp(bucket, tz=datetime.timezone.utc))}
        for field in NUMERIC_FIELDS:
            point[field] = _average(row[field] for row in bucket_rows)
        for field in STATUS_FIELDS:
            value = latest[field]
            if field in ("telemetry_ok", "telemetry_applicable", "last_command_ok"):
                point[field] = None if value is None else bool(value)
            else:
                point[field] = value
        points.append(point)
    return points


def get_gpu_history(
    window: str = "15m",
    path: Path = DEFAULT_DB_PATH,
    now: Optional[datetime.datetime] = None,
) -> dict[str, Any]:
    if window not in WINDOWS:
        raise GpuHistoryError(f"unsupported window: {window}")

    init_gpu_history_db(path)
    spec = WINDOWS[window]
    current = now or _utc_now()
    cutoff = current.timestamp() - float(spec["seconds"])
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM gpu_status_samples WHERE ts_epoch >= ? ORDER BY ts_epoch ASC",
            (cutoff,),
        ).fetchall()

    bucket_seconds = int(spec["bucket_seconds"])
    return {
        "ok": True,
        "window": window,
        "bucket_seconds": bucket_seconds,
        "series": list(SERIES),
        "points": _downsample_rows(rows, bucket_seconds),
    }
