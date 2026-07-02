from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from config_store import (
    ConfigStoreError,
    ConfigValidationError,
    effective_gpu_telemetry_values,
    load_local_config,
    update_gpu_telemetry_config,
)


class TestConfigStore(unittest.TestCase):
    def temp_path(self) -> Path:
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        return Path(tempdir.name) / "local.json"

    def test_loads_defaults_when_file_absent(self):
        path = self.temp_path()

        config = load_local_config(path)

        self.assertTrue(config.gpu.telemetry.skip_when_vm_off)
        self.assertEqual(config.gpu.telemetry.log_throttle_seconds, 60.0)
        self.assertEqual(config.gpu.telemetry.glances_timeout_seconds, 2.5)
        self.assertEqual(config.gpu.telemetry.glances_gpu_id, "nvidia0")

    def test_json_overrides_defaults(self):
        path = self.temp_path()
        path.write_text(
            json.dumps(
                {
                    "gpu": {
                        "telemetry": {
                            "skip_when_vm_off": False,
                            "log_throttle_seconds": 12.5,
                            "glances_timeout_seconds": 1.25,
                            "glances_gpu_id": "nvidia1",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        values = effective_gpu_telemetry_values(path=path, environ={})

        self.assertFalse(values["skip_when_vm_off"].value)
        self.assertEqual(values["skip_when_vm_off"].source, "config/local.json")
        self.assertEqual(values["log_throttle_seconds"].value, 12.5)
        self.assertEqual(values["glances_timeout_seconds"].value, 1.25)
        self.assertEqual(values["glances_gpu_id"].value, "nvidia1")

    def test_env_overrides_json(self):
        path = self.temp_path()
        path.write_text(
            json.dumps({"gpu": {"telemetry": {"log_throttle_seconds": 12.5}}}),
            encoding="utf-8",
        )

        values = effective_gpu_telemetry_values(
            path=path,
            environ={"GPU_TELEMETRY_LOG_THROTTLE_SECONDS": "90"},
        )

        self.assertEqual(values["log_throttle_seconds"].value, 90.0)
        self.assertEqual(values["log_throttle_seconds"].source, "env")

    def test_explicit_default_value_reports_json_source(self):
        path = self.temp_path()
        path.write_text(
            json.dumps({"gpu": {"telemetry": {"skip_when_vm_off": True}}}),
            encoding="utf-8",
        )

        values = effective_gpu_telemetry_values(path=path, environ={})

        self.assertTrue(values["skip_when_vm_off"].value)
        self.assertEqual(values["skip_when_vm_off"].source, "config/local.json")

    def test_legacy_secrets_override_env(self):
        path = self.temp_path()
        secrets = SimpleNamespace(GPU_TELEMETRY_SKIP_WHEN_VM_OFF=False)

        values = effective_gpu_telemetry_values(
            path=path,
            environ={"GPU_TELEMETRY_SKIP_WHEN_VM_OFF": "true"},
            secrets_module=secrets,
        )

        self.assertFalse(values["skip_when_vm_off"].value)
        self.assertEqual(values["skip_when_vm_off"].source, "llm_secrets.py")

    def test_invalid_json_reports_error(self):
        path = self.temp_path()
        path.write_text("{ nope", encoding="utf-8")

        with self.assertRaises(ConfigStoreError):
            load_local_config(path)

    def test_update_preserves_unrelated_config(self):
        path = self.temp_path()
        path.write_text(
            json.dumps(
                {
                    "gpu": {"telemetry": {"log_throttle_seconds": 30}},
                    "future": {"kept": True},
                }
            ),
            encoding="utf-8",
        )

        update_gpu_telemetry_config({"skip_when_vm_off": False}, path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(raw["future"], {"kept": True})
        self.assertFalse(raw["gpu"]["telemetry"]["skip_when_vm_off"])
        self.assertEqual(raw["gpu"]["telemetry"]["log_throttle_seconds"], 30.0)

    def test_unknown_update_key_rejected(self):
        path = self.temp_path()

        with self.assertRaises(ConfigValidationError):
            update_gpu_telemetry_config({"not_a_setting": True}, path=path)


if __name__ == "__main__":
    unittest.main()
