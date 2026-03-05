import unittest
from unittest.mock import patch
import datetime

from gpu_watchdog import (
    GPUWatchdogService,
    MODE_AUTO,
    MODE_FAILSAFE,
    MODE_VM_OFF_IDLE,
    parse_watchdog_control_payload,
)


def _telemetry_sample(temp_c: float):
    now = datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    return {
        "telemetry_ok": True,
        "source": "remote_glances",
        "gpu_id": "nvidia0",
        "gpu_name": "Tesla P40",
        "gpu_temp_c": temp_c,
        "gpu_util_percent": 5.0,
        "gpu_mem_util_percent": 50.0,
        "error": None,
        "updated_at": now,
    }


class TestGpuWatchdog(unittest.IsolatedAsyncioTestCase):
    def _settings_patches(self):
        return (
            patch("gpu_watchdog.settings.ILO_HOST", "192.168.8.35"),
            patch("gpu_watchdog.settings.ILO_USER", "Administrator"),
            patch("gpu_watchdog.settings.ILO_PASSWORD", "secret"),
            patch("gpu_watchdog.settings.WATCHDOG_ENABLED", True),
            patch("gpu_watchdog.settings.WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS", 20.0),
            patch("gpu_watchdog.settings.WATCHDOG_HYSTERESIS_C", 4.0),
            patch("gpu_watchdog.settings.WATCHDOG_FAILSAFE_FAN_MIN_XX", 190),
            patch("gpu_watchdog.settings.WATCHDOG_POLL_SECONDS", 5.0),
            patch("gpu_watchdog.settings.WATCHDOG_TELEMETRY_STALE_SECONDS", 15.0),
            patch("gpu_watchdog.settings.WATCHDOG_LOG_TRANSITIONS_ONLY", True),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_OFF_IDLE_ENABLED", True),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_OFF_FAN_MIN_XX", 50),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_STARTUP_GRACE_SECONDS", 30.0),
        )

    async def test_temp_mapping_and_hysteresis(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(65.0),
            fan_setter=lambda xx: {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
        )
        self.assertEqual(svc.target_xx_for_temp(59.0, 110), 90)
        self.assertEqual(svc.target_xx_for_temp(55.0, 90), 90)
        self.assertEqual(svc.target_xx_for_temp(81.0, 190), 230)

    async def test_failsafe_on_telemetry_error(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        calls = []

        def fan_setter(xx: int):
            calls.append(xx)
            return {"ok": True, "timestamp": "2026-01-01T00:00:01Z"}

        telemetry = {
            "telemetry_ok": False,
            "source": "remote_glances",
            "error": "timeout",
            "updated_at": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
        svc = GPUWatchdogService(
            telemetry_getter=lambda: telemetry,
            fan_setter=fan_setter,
            vm_state_getter=lambda: "running",
        )
        await svc.step_once()

        status = svc.get_status()
        self.assertEqual(status["mode"], MODE_FAILSAFE)
        self.assertEqual(status["last_target_xx"], 190)
        self.assertEqual(calls, [190])

    async def test_rate_limiting(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        temps = {"value": 75.0}
        calls = []
        now_value = {"t": 0.0}

        def telemetry_getter():
            return _telemetry_sample(temps["value"])

        def fan_setter(xx: int):
            calls.append(xx)
            return {"ok": True, "timestamp": "2026-01-01T00:00:01Z"}

        svc = GPUWatchdogService(
            telemetry_getter=telemetry_getter,
            fan_setter=fan_setter,
            vm_state_getter=lambda: "running",
            monotonic_fn=lambda: now_value["t"],
        )

        await svc.step_once()
        self.assertEqual(calls, [150])
        self.assertEqual(svc.get_status()["mode"], MODE_AUTO)

        temps["value"] = 82.0
        await svc.step_once()
        self.assertEqual(calls, [150])

        now_value["t"] = 21.0
        await svc.step_once()
        self.assertEqual(calls, [150, 230])

    async def test_vm_stopped_uses_vm_off_idle_mode(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        calls = []

        def fan_setter(xx: int):
            calls.append(xx)
            return {"ok": True, "timestamp": "2026-01-01T00:00:01Z"}

        svc = GPUWatchdogService(
            telemetry_getter=lambda: {"telemetry_ok": False, "error": "timeout"},
            fan_setter=fan_setter,
            vm_state_getter=lambda: "stopped",
        )
        await svc.step_once()

        status = svc.get_status()
        self.assertEqual(status["mode"], MODE_VM_OFF_IDLE)
        self.assertEqual(status["mode_reason"], "vm_stopped")
        self.assertFalse(status["telemetry_applicable"])
        self.assertEqual(calls, [50])
        self.assertEqual(status["last_target_xx"], 50)

    async def test_vm_state_unknown_uses_conservative_failsafe(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        calls = []

        def fan_setter(xx: int):
            calls.append(xx)
            return {"ok": True, "timestamp": "2026-01-01T00:00:01Z"}

        svc = GPUWatchdogService(
            telemetry_getter=lambda: {"telemetry_ok": False, "error": "timeout"},
            fan_setter=fan_setter,
            vm_state_getter=lambda: (_ for _ in ()).throw(RuntimeError("proxmox unavailable")),
        )
        await svc.step_once()

        status = svc.get_status()
        self.assertEqual(status["mode"], MODE_FAILSAFE)
        self.assertTrue(status["telemetry_applicable"])
        self.assertEqual(calls, [190])


class TestGpuWatchdogControlPayload(unittest.TestCase):
    def test_control_payload_enabled(self):
        enabled, reset, err = parse_watchdog_control_payload({"enabled": True})
        self.assertIsNone(err)
        self.assertTrue(enabled)
        self.assertFalse(reset)

    def test_control_payload_invalid(self):
        enabled, reset, err = parse_watchdog_control_payload({"enabled": "yes"})
        self.assertIsNotNone(err)
        self.assertIsNone(enabled)
        self.assertFalse(reset)


if __name__ == "__main__":
    unittest.main()
