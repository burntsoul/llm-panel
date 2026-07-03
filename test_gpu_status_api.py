from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

import app as app_module
from gpu_history import record_gpu_status_sample


class TestGpuStatusApi(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.db_path = Path(self.tempdir.name) / "gpu_status.sqlite3"
        app_module.app.state.gpu_history_db_path = self.db_path

    def tearDown(self):
        if hasattr(app_module.app.state, "gpu_history_db_path"):
            delattr(app_module.app.state, "gpu_history_db_path")

    def test_history_empty_db_returns_empty_points(self):
        payload = app_module.api_gpu_status_history(window="15m")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["points"], [])

    def test_history_returns_expected_shape(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        record_gpu_status_sample(
            {
                "updated_at": now.isoformat().replace("+00:00", "Z"),
                "telemetry_ok": True,
                "telemetry_applicable": True,
                "mode": "auto",
                "gpu_temp_c": 61.0,
                "gpu_util_percent": 80.0,
                "gpu_mem_util_percent": 42.0,
                "last_target_xx": 120,
                "last_applied_xx": 110,
            },
            path=self.db_path,
        )

        payload = app_module.api_gpu_status_history(window="15m")

        self.assertEqual(payload["window"], "15m")
        self.assertEqual(payload["series"], ["gpu_temp_c", "gpu_util_percent", "gpu_mem_util_percent", "last_target_xx", "last_applied_xx"])
        self.assertEqual(len(payload["points"]), 1)
        self.assertEqual(payload["points"][0]["gpu_temp_c"], 61.0)
        self.assertEqual(payload["points"][0]["watchdog_mode"], "auto")

    def test_history_invalid_window_returns_400(self):
        with self.assertRaises(HTTPException) as captured:
            app_module.api_gpu_status_history(window="2d")

        self.assertEqual(captured.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
