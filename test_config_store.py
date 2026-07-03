from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from config_store import (
    ConfigStoreError,
    ConfigValidationError,
    effective_gpu_fan_control_values,
    effective_gpu_telemetry_values,
    effective_gpu_watchdog_curve_values,
    gpu_fan_control_fields_for_api,
    gpu_watchdog_curve_fields_for_api,
    load_local_config,
    update_gpu_fan_control_config,
    update_gpu_telemetry_config,
    update_gpu_watchdog_curve_config,
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
        self.assertEqual(config.gpu.fan_control.ilo_ssh_port, 22)
        self.assertEqual(config.gpu.fan_control.ilo_fan_patch_index, 3)
        self.assertEqual(config.gpu.fan_control.ilo_sshpass_path, "sshpass")
        self.assertEqual(config.gpu.watchdog_curve.target_temp_c, 72.0)
        self.assertEqual(config.gpu.watchdog_curve.over_target_kp, 8.0)
        self.assertEqual(config.gpu.watchdog_curve.derivative_lookahead_seconds, 20.0)
        self.assertEqual(config.gpu.watchdog_curve.derivative_smoothing_alpha, 0.35)
        self.assertEqual(config.gpu.watchdog_curve.cooldown_release_below_target_c, 3.0)
        self.assertEqual(config.gpu.watchdog_curve.cooldown_release_gpu_util_percent, 10.0)
        self.assertEqual(config.gpu.watchdog_curve.command_min_delta_xx, 5)
        self.assertEqual(config.gpu.watchdog_curve.emergency_temp_c, 84.0)

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

    def test_gpu_fan_control_json_overrides_defaults(self):
        path = self.temp_path()
        path.write_text(
            json.dumps(
                {
                    "gpu": {
                        "fan_control": {
                            "ilo_host": "192.168.8.35",
                            "ilo_user": "Administrator",
                            "ilo_ssh_port": 2222,
                            "ilo_fan_patch_index": 4,
                            "ilo_ssh_timeout_seconds": 8.5,
                            "ilo_ssh_strict_hostkey": False,
                            "ilo_sshpass_path": "/usr/bin/sshpass",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        values = effective_gpu_fan_control_values(path=path, environ={})

        self.assertEqual(values["ilo_host"].value, "192.168.8.35")
        self.assertEqual(values["ilo_host"].source, "config/local.json")
        self.assertEqual(values["ilo_user"].value, "Administrator")
        self.assertEqual(values["ilo_ssh_port"].value, 2222)
        self.assertEqual(values["ilo_fan_patch_index"].value, 4)
        self.assertEqual(values["ilo_ssh_timeout_seconds"].value, 8.5)
        self.assertFalse(values["ilo_ssh_strict_hostkey"].value)
        self.assertEqual(values["ilo_sshpass_path"].value, "/usr/bin/sshpass")

    def test_gpu_fan_control_env_overrides_json(self):
        path = self.temp_path()
        path.write_text(
            json.dumps({"gpu": {"fan_control": {"ilo_ssh_port": 2222}}}),
            encoding="utf-8",
        )

        values = effective_gpu_fan_control_values(path=path, environ={"ILO_SSH_PORT": "2200"})

        self.assertEqual(values["ilo_ssh_port"].value, 2200)
        self.assertEqual(values["ilo_ssh_port"].source, "env")

    def test_gpu_fan_control_legacy_secrets_override_env(self):
        path = self.temp_path()
        secrets = SimpleNamespace(ILO_HOST="192.168.8.35")

        values = effective_gpu_fan_control_values(
            path=path,
            environ={"ILO_HOST": "192.168.8.36"},
            secrets_module=secrets,
        )

        self.assertEqual(values["ilo_host"].value, "192.168.8.35")
        self.assertEqual(values["ilo_host"].source, "llm_secrets.py")

    def test_update_gpu_fan_control_preserves_unrelated_config(self):
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

        update_gpu_fan_control_config({"ilo_ssh_strict_hostkey": False}, path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(raw["future"], {"kept": True})
        self.assertEqual(raw["gpu"]["telemetry"], {"log_throttle_seconds": 30})
        self.assertFalse(raw["gpu"]["fan_control"]["ilo_ssh_strict_hostkey"])

    def test_gpu_fan_control_secret_status_does_not_expose_password(self):
        fields = gpu_fan_control_fields_for_api(
            secrets_module=SimpleNamespace(ILO_PASSWORD="super-secret-password"),
            environ={},
            path=self.temp_path(),
        )
        encoded = json.dumps(fields)

        self.assertIn("configured", encoded)
        self.assertNotIn("super-secret-password", encoded)

    def test_gpu_watchdog_curve_json_overrides_defaults(self):
        path = self.temp_path()
        path.write_text(
            json.dumps(
                {
                    "gpu": {
                        "watchdog_curve": {
                            "poll_seconds": 3.0,
                            "target_temp_c": 70.0,
                            "command_min_delta_xx": 8,
                            "max_step_up_xx": 15,
                            "failsafe_fan_xx": 200,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        values = effective_gpu_watchdog_curve_values(path=path, environ={})

        self.assertEqual(values["poll_seconds"].value, 3.0)
        self.assertEqual(values["poll_seconds"].source, "config/local.json")
        self.assertEqual(values["target_temp_c"].value, 70.0)
        self.assertEqual(values["command_min_delta_xx"].value, 8)
        self.assertEqual(values["max_step_up_xx"].value, 15)
        self.assertEqual(values["failsafe_fan_xx"].value, 200)

    def test_gpu_watchdog_curve_env_overrides_json_with_legacy_name(self):
        path = self.temp_path()
        path.write_text(
            json.dumps({"gpu": {"watchdog_curve": {"poll_seconds": 3.0}}}),
            encoding="utf-8",
        )

        values = effective_gpu_watchdog_curve_values(path=path, environ={"WATCHDOG_POLL_SECONDS": "7"})

        self.assertEqual(values["poll_seconds"].value, 7.0)
        self.assertEqual(values["poll_seconds"].source, "env")

    def test_update_gpu_watchdog_curve_preserves_unrelated_config(self):
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

        update_gpu_watchdog_curve_config({"target_temp_c": 70.0}, path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(raw["future"], {"kept": True})
        self.assertEqual(raw["gpu"]["telemetry"], {"log_throttle_seconds": 30})
        self.assertEqual(raw["gpu"]["watchdog_curve"]["target_temp_c"], 70.0)

    def test_invalid_gpu_watchdog_curve_range_rejected(self):
        path = self.temp_path()

        with self.assertRaises(ConfigValidationError):
            update_gpu_watchdog_curve_config({"min_fan_xx": 200, "max_fan_xx": 100}, path=path)

    def test_gpu_watchdog_curve_fields_for_api(self):
        fields = gpu_watchdog_curve_fields_for_api(environ={}, path=self.temp_path())

        encoded = json.dumps(fields)
        self.assertIn("target_temp_c", encoded)
        self.assertIn("command_min_delta_xx", encoded)
        self.assertIn("help", encoded)
        self.assertIn("Above-target gain", encoded)


if __name__ == "__main__":
    unittest.main()
