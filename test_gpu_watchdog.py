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


async def _immediate_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


class TestGpuWatchdog(unittest.IsolatedAsyncioTestCase):
    def _settings_patches(self):
        return (
            patch("gpu_watchdog.asyncio.to_thread", _immediate_to_thread),
            patch("gpu_watchdog.settings.ILO_HOST", "192.168.8.35"),
            patch("gpu_watchdog.settings.ILO_USER", "Administrator"),
            patch("gpu_watchdog.settings.ILO_PASSWORD", "secret"),
            patch("gpu_watchdog.settings.WATCHDOG_ENABLED", True),
            patch("gpu_watchdog.settings.WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS", 20.0),
            patch("gpu_watchdog.settings.WATCHDOG_FAILSAFE_FAN_MIN_XX", 190),
            patch("gpu_watchdog.settings.WATCHDOG_POLL_SECONDS", 5.0),
            patch("gpu_watchdog.settings.WATCHDOG_TELEMETRY_STALE_SECONDS", 15.0),
            patch("gpu_watchdog.settings.WATCHDOG_LOG_TRANSITIONS_ONLY", True),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_TARGET_TEMP_C", 72.0),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_MIN_FAN_XX", 40),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_MAX_FAN_XX", 230),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_PI_KP", 14.0),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_PI_KI", 0.08),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_PI_INTEGRAL_CLAMP", 800.0),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_SMOOTHING_ALPHA", 0.25),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_COMMAND_MIN_DELTA_XX", 5),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_MAX_STEP_UP_XX", 20),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_MAX_STEP_DOWN_XX", 10),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_EMERGENCY_TEMP_C", 84.0),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_EMERGENCY_FAN_XX", 230),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_OFF_IDLE_ENABLED", True),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_OFF_FAN_MIN_XX", 50),
            patch("gpu_watchdog.settings.GPU_WATCHDOG_VM_STARTUP_GRACE_SECONDS", 30.0),
        )

    async def test_pi_target_rises_above_target_temp(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        calls = []
        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(75.0),
            fan_setter=lambda xx: calls.append(xx) or {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
        )
        await svc.step_once()

        status = svc.get_status()
        self.assertEqual(status["mode"], MODE_AUTO)
        self.assertEqual(status["controller"], "pi")
        self.assertGreater(status["desired_fan_xx"], 40)
        self.assertEqual(calls, [83])

    async def test_pi_integral_accumulates_and_is_clamped(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        with patch("gpu_watchdog.settings.GPU_WATCHDOG_PI_INTEGRAL_CLAMP", 20.0):
            svc = GPUWatchdogService(
                telemetry_getter=lambda: _telemetry_sample(82.0),
                fan_setter=lambda xx: {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
                vm_state_getter=lambda: "running",
                monotonic_fn=lambda: 1000.0,
            )
            await svc.step_once()
            await svc.step_once()
            await svc.step_once()

        self.assertEqual(svc.get_status()["pi_integral"], 20.0)

    async def test_pi_integral_decays_below_target_temp(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        with patch("gpu_watchdog.settings.GPU_WATCHDOG_SMOOTHING_ALPHA", 1.0):
            temps = {"value": 82.0}
            now_value = {"t": 0.0}
            svc = GPUWatchdogService(
                telemetry_getter=lambda: _telemetry_sample(temps["value"]),
                fan_setter=lambda xx: {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
                vm_state_getter=lambda: "running",
                monotonic_fn=lambda: now_value["t"],
            )
            await svc.step_once()
            high_integral = svc.get_status()["pi_integral"]

            temps["value"] = 50.0
            now_value["t"] = 25.0
            await svc.step_once()

        self.assertLess(svc.get_status()["pi_integral"], high_integral)

    async def test_smoothing_reduces_short_spike_impact(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        temps = {"value": 60.0}
        now_value = {"t": 0.0}
        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(temps["value"]),
            fan_setter=lambda xx: {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
            monotonic_fn=lambda: now_value["t"],
        )
        await svc.step_once()

        temps["value"] = 80.0
        now_value["t"] = 25.0
        await svc.step_once()

        status = svc.get_status()
        self.assertAlmostEqual(status["smoothed_temp_c"], 65.0)
        self.assertLess(status["desired_fan_xx"], 80)

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

    async def test_command_interval_blocks_repeated_commands(self):
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
        self.assertEqual(calls, [83])
        self.assertEqual(svc.get_status()["mode"], MODE_AUTO)

        temps["value"] = 82.0
        await svc.step_once()
        self.assertEqual(calls, [83])

        now_value["t"] = 21.0
        await svc.step_once()
        self.assertEqual(calls, [83, 103])

    async def test_command_delta_suppresses_tiny_changes(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        temps = {"value": 75.0}
        calls = []
        now_value = {"t": 0.0}
        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(temps["value"]),
            fan_setter=lambda xx: calls.append(xx) or {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
            monotonic_fn=lambda: now_value["t"],
        )
        await svc.step_once()

        temps["value"] = 75.1
        now_value["t"] = 25.0
        await svc.step_once()

        self.assertEqual(calls, [83])

    async def test_max_step_down_limits_normal_decrease(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        temps = {"value": 82.0}
        calls = []
        now_value = {"t": 0.0}
        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(temps["value"]),
            fan_setter=lambda xx: calls.append(xx) or {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
            monotonic_fn=lambda: now_value["t"],
        )
        await svc.step_once()
        self.assertEqual(calls, [184])

        temps["value"] = 50.0
        now_value["t"] = 25.0
        await svc.step_once()

        self.assertEqual(calls, [184, 174])

    async def test_emergency_temp_bypasses_ramp_and_interval(self):
        patches = self._settings_patches()
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

        temps = {"value": 75.0}
        calls = []
        now_value = {"t": 0.0}
        svc = GPUWatchdogService(
            telemetry_getter=lambda: _telemetry_sample(temps["value"]),
            fan_setter=lambda xx: calls.append(xx) or {"ok": True, "timestamp": "2026-01-01T00:00:01Z"},
            vm_state_getter=lambda: "running",
            monotonic_fn=lambda: now_value["t"],
        )
        await svc.step_once()

        temps["value"] = 86.0
        await svc.step_once()

        self.assertEqual(calls, [83, 230])

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
