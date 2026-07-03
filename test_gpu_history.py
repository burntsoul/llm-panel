from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path

from gpu_history import GpuHistoryError, get_gpu_history, init_gpu_history_db, record_gpu_status_sample


def _sample(ts: datetime.datetime, temp: float = 60.0, mode: str = "auto") -> dict:
    return {
        "updated_at": ts.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "telemetry_ok": True,
        "telemetry_applicable": True,
        "vm_state": "running",
        "mode": mode,
        "mode_reason": "pi_controller",
        "gpu_id": "nvidia0",
        "gpu_name": "Tesla P40",
        "gpu_temp_c": temp,
        "gpu_util_percent": 50.0,
        "gpu_mem_util_percent": 25.0,
        "smoothed_temp_c": temp - 1.0,
        "target_temp_c": 72.0,
        "desired_fan_xx": 100,
        "rate_limited_target_xx": 90,
        "last_target_xx": 90,
        "last_applied_xx": 80,
        "last_command_ok": True,
        "last_error": None,
    }


class TestGpuHistory(unittest.TestCase):
    def temp_path(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return Path(tempdir.name) / "gpu_status.sqlite3"

    def test_creates_schema_and_empty_history(self):
        path = self.temp_path()

        init_gpu_history_db(path)
        result = get_gpu_history("15m", path=path)

        self.assertTrue(result["ok"])
        self.assertEqual(result["points"], [])

    def test_inserts_and_queries_supported_windows(self):
        path = self.temp_path()
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)

        record_gpu_status_sample(_sample(now, temp=61.0), path=path, now=now)

        for window in ("5m", "15m", "1h", "6h", "24h"):
            result = get_gpu_history(window, path=path, now=now)
            self.assertEqual(result["window"], window)
            self.assertEqual(len(result["points"]), 1)
            self.assertEqual(result["points"][0]["gpu_temp_c"], 61.0)

    def test_rejects_invalid_window(self):
        path = self.temp_path()

        with self.assertRaises(GpuHistoryError):
            get_gpu_history("2d", path=path)

    def test_downsampling_averages_numeric_values_and_keeps_latest_status(self):
        path = self.temp_path()
        now = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=datetime.timezone.utc)
        record_gpu_status_sample(_sample(now - datetime.timedelta(seconds=12), temp=60.0, mode="auto"), path=path)
        record_gpu_status_sample(_sample(now - datetime.timedelta(seconds=10), temp=70.0, mode="failsafe"), path=path)

        result = get_gpu_history("1h", path=path, now=now)

        self.assertEqual(result["bucket_seconds"], 15)
        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["gpu_temp_c"], 65.0)
        self.assertEqual(result["points"][0]["watchdog_mode"], "failsafe")

    def test_retention_prunes_old_samples(self):
        path = self.temp_path()
        now = datetime.datetime(2026, 7, 10, 12, 0, tzinfo=datetime.timezone.utc)
        old = now - datetime.timedelta(days=8)

        record_gpu_status_sample(_sample(old, temp=50.0), path=path, now=old)
        record_gpu_status_sample(_sample(now, temp=65.0), path=path, now=now)
        result = get_gpu_history("24h", path=path, now=now)

        self.assertEqual(len(result["points"]), 1)
        self.assertEqual(result["points"][0]["gpu_temp_c"], 65.0)


if __name__ == "__main__":
    unittest.main()
