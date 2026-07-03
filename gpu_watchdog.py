from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from config import settings
from gpu_telemetry import get_gpu_telemetry
from ilo_fan import set_ilo_fan_min
from proxmox import get_vm_status


logger = logging.getLogger(__name__)


MODE_DISABLED = "disabled"
MODE_AUTO = "auto"
MODE_VM_OFF_IDLE = "vm_off_idle"
MODE_FAILSAFE = "failsafe"

CONTROLLER_PI = "pid_predictive"


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _parse_iso_timestamp(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def parse_watchdog_control_payload(payload: Any) -> Tuple[Optional[bool], bool, Optional[str]]:
    if not isinstance(payload, dict):
        return None, False, "invalid JSON object"

    has_enabled = "enabled" in payload
    has_reset = "reset_error" in payload
    if not has_enabled and not has_reset:
        return None, False, "payload must include 'enabled' or 'reset_error'"

    enabled_value: Optional[bool] = None
    if has_enabled:
        value = payload.get("enabled")
        if not isinstance(value, bool):
            return None, False, "'enabled' must be boolean"
        enabled_value = value

    reset_error = False
    if has_reset:
        value = payload.get("reset_error")
        if not isinstance(value, bool):
            return None, False, "'reset_error' must be boolean"
        reset_error = value

    return enabled_value, reset_error, None


class GPUWatchdogService:
    def __init__(
        self,
        telemetry_getter: Callable[[], Dict[str, Any]] = get_gpu_telemetry,
        fan_setter: Callable[[int], Dict[str, Any]] = set_ilo_fan_min,
        vm_state_getter: Optional[Callable[[], str]] = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sample_recorder: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._telemetry_getter = telemetry_getter
        self._fan_setter = fan_setter
        self._vm_state_getter = vm_state_getter or (lambda: get_vm_status(settings.LLM_VM_ID))
        self._monotonic = monotonic_fn
        self._sample_recorder = sample_recorder
        self._task: Optional[asyncio.Task] = None

        self._vm_off_idle_enabled = bool(settings.GPU_WATCHDOG_VM_OFF_IDLE_ENABLED)
        self._vm_off_fan_min_xx = int(settings.GPU_WATCHDOG_VM_OFF_FAN_MIN_XX)
        self._vm_startup_grace_seconds = float(settings.GPU_WATCHDOG_VM_STARTUP_GRACE_SECONDS)
        self._log_transitions_only = bool(settings.WATCHDOG_LOG_TRANSITIONS_ONLY)
        self._load_curve_settings()

        self._enabled = bool(settings.WATCHDOG_ENABLED)
        self._mode = MODE_DISABLED
        self._last_transition_mode = MODE_DISABLED
        self._last_target_xx: Optional[int] = None
        self._last_applied_xx: Optional[int] = None
        self._last_apply_monotonic: Optional[float] = None
        self._last_command_ok: Optional[bool] = None
        self._last_command_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._status_updated_at = _utc_now_iso()
        self._mode_reason = "watchdog_disabled"
        self._vm_state: Optional[str] = None
        self._telemetry_applicable = True

        self._telemetry_source = "remote_glances"
        self._telemetry_ok = False
        self._gpu_name: Optional[str] = None
        self._gpu_id: Optional[str] = None
        self._gpu_temp_c: Optional[float] = None
        self._gpu_util_percent: Optional[float] = None
        self._gpu_mem_util_percent: Optional[float] = None
        self._smoothed_temp_c: Optional[float] = None
        self._pi_error_c: Optional[float] = None
        self._pi_integral = 0.0
        self._temp_rate_c_per_s: Optional[float] = None
        self._projected_temp_c: Optional[float] = None
        self._projected_error_c: Optional[float] = None
        self._control_error_c: Optional[float] = None
        self._cooldown_release_active = False
        self._last_temp_sample_monotonic: Optional[float] = None
        self._desired_fan_xx: Optional[int] = None
        self._rate_limited_target_xx: Optional[int] = None

        self._config_error = self._validate_runtime_config()
        if self._config_error:
            self._enabled = False
            self._last_error = self._config_error

    def _load_curve_settings(self) -> None:
        self._poll_seconds = float(settings.WATCHDOG_POLL_SECONDS)
        self._target_temp_c = float(settings.GPU_WATCHDOG_TARGET_TEMP_C)
        self._min_fan_xx = int(settings.GPU_WATCHDOG_MIN_FAN_XX)
        self._max_fan_xx = int(settings.GPU_WATCHDOG_MAX_FAN_XX)
        self._kp = float(settings.GPU_WATCHDOG_PI_KP)
        self._over_target_kp = float(settings.GPU_WATCHDOG_OVER_TARGET_KP)
        self._ki = float(settings.GPU_WATCHDOG_PI_KI)
        self._integral_clamp = float(settings.GPU_WATCHDOG_PI_INTEGRAL_CLAMP)
        self._smoothing_alpha = float(settings.GPU_WATCHDOG_SMOOTHING_ALPHA)
        self._derivative_lookahead_seconds = float(settings.GPU_WATCHDOG_DERIVATIVE_LOOKAHEAD_SECONDS)
        self._derivative_smoothing_alpha = float(settings.GPU_WATCHDOG_DERIVATIVE_SMOOTHING_ALPHA)
        self._cooldown_release_below_target_c = float(settings.GPU_WATCHDOG_COOLDOWN_RELEASE_BELOW_TARGET_C)
        self._cooldown_release_gpu_util_percent = float(settings.GPU_WATCHDOG_COOLDOWN_RELEASE_GPU_UTIL_PERCENT)
        self._min_change_interval_seconds = float(settings.WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS)
        self._command_min_delta_xx = int(settings.GPU_WATCHDOG_COMMAND_MIN_DELTA_XX)
        self._max_step_up_xx = int(settings.GPU_WATCHDOG_MAX_STEP_UP_XX)
        self._max_step_down_xx = int(settings.GPU_WATCHDOG_MAX_STEP_DOWN_XX)
        self._emergency_temp_c = float(settings.GPU_WATCHDOG_EMERGENCY_TEMP_C)
        self._emergency_fan_xx = int(settings.GPU_WATCHDOG_EMERGENCY_FAN_XX)
        self._failsafe_fan_min_xx = int(settings.WATCHDOG_FAILSAFE_FAN_MIN_XX)
        self._telemetry_stale_seconds = float(settings.WATCHDOG_TELEMETRY_STALE_SECONDS)

    def reload_config(self, reset_controller: bool = False) -> None:
        self._load_curve_settings()
        self._config_error = self._validate_runtime_config()
        if self._config_error:
            self._enabled = False
            self._last_error = self._config_error
        if reset_controller:
            self._smoothed_temp_c = None
            self._pi_error_c = None
            self._pi_integral = 0.0
            self._temp_rate_c_per_s = None
            self._projected_temp_c = None
            self._projected_error_c = None
            self._control_error_c = None
            self._cooldown_release_active = False
            self._last_temp_sample_monotonic = None
            self._desired_fan_xx = None
            self._rate_limited_target_xx = None
        self._status_updated_at = _utc_now_iso()

    def _validate_runtime_config(self) -> Optional[str]:
        if not settings.ILO_HOST or not settings.ILO_USER or not settings.ILO_PASSWORD:
            return "watchdog disabled: ILO_HOST/ILO_USER/ILO_PASSWORD not configured"
        return None

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "mode_reason": self._mode_reason,
            "vm_state": self._vm_state,
            "telemetry_applicable": self._telemetry_applicable,
            "telemetry_source": self._telemetry_source,
            "telemetry_ok": self._telemetry_ok,
            "gpu_name": self._gpu_name,
            "gpu_id": self._gpu_id,
            "gpu_temp_c": self._gpu_temp_c,
            "gpu_util_percent": self._gpu_util_percent,
            "gpu_mem_util_percent": self._gpu_mem_util_percent,
            "last_target_xx": self._last_target_xx,
            "last_applied_xx": self._last_applied_xx,
            "last_applied_fan_xx": self._last_applied_xx,
            "last_command_ok": self._last_command_ok,
            "last_command_at": self._last_command_at,
            "last_error": self._last_error,
            "updated_at": self._status_updated_at,
            "controller": CONTROLLER_PI,
            "poll_seconds": self._poll_seconds,
            "target_temp_c": self._target_temp_c,
            "smoothed_temp_c": self._smoothed_temp_c,
            "pi_error_c": self._pi_error_c,
            "pi_integral": self._pi_integral,
            "temp_rate_c_per_s": self._temp_rate_c_per_s,
            "projected_temp_c": self._projected_temp_c,
            "projected_error_c": self._projected_error_c,
            "control_error_c": self._control_error_c,
            "cooldown_release_active": self._cooldown_release_active,
            "cooldown_release_below_target_c": self._cooldown_release_below_target_c,
            "cooldown_release_gpu_util_percent": self._cooldown_release_gpu_util_percent,
            "derivative_lookahead_seconds": self._derivative_lookahead_seconds,
            "derivative_smoothing_alpha": self._derivative_smoothing_alpha,
            "over_target_kp": self._over_target_kp,
            "desired_fan_xx": self._desired_fan_xx,
            "rate_limited_target_xx": self._rate_limited_target_xx,
            "min_fan_xx": self._min_fan_xx,
            "max_fan_xx": self._max_fan_xx,
            "kp": self._kp,
            "ki": self._ki,
            "integral_clamp": self._integral_clamp,
            "min_change_interval_seconds": self._min_change_interval_seconds,
            "command_min_delta_xx": self._command_min_delta_xx,
            "max_step_up_xx": self._max_step_up_xx,
            "max_step_down_xx": self._max_step_down_xx,
            "emergency_temp_c": self._emergency_temp_c,
            "emergency_fan_xx": self._emergency_fan_xx,
            "failsafe_fan_min_xx": self._failsafe_fan_min_xx,
            "vm_off_idle_enabled": self._vm_off_idle_enabled,
            "vm_off_fan_min_xx": self._vm_off_fan_min_xx,
            "vm_startup_grace_seconds": self._vm_startup_grace_seconds,
        }

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._mode = MODE_DISABLED
            self._mode_reason = "watchdog_disabled"
        self._status_updated_at = _utc_now_iso()
        if self._log_transitions_only:
            logger.info("GPU watchdog enabled=%s", self._enabled)

    def reset_error(self) -> None:
        self._last_error = None
        self._status_updated_at = _utc_now_iso()

    def _record_history_sample(self) -> None:
        if self._sample_recorder is None:
            return
        try:
            self._sample_recorder(self.get_status())
        except Exception as exc:
            logger.warning("GPU history sample recording failed: %s", exc)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self.run_loop(), name="gpu_watchdog_loop")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def _telemetry_is_stale(self, telemetry: Dict[str, Any]) -> bool:
        dt = _parse_iso_timestamp(telemetry.get("updated_at"))
        if dt is None:
            return False
        age = (datetime.datetime.utcnow() - dt).total_seconds()
        return age > self._telemetry_stale_seconds

    def _read_vm_state(self) -> Optional[str]:
        try:
            raw = self._vm_state_getter()
        except Exception as exc:
            self._vm_state = f"ERROR: {exc}"
            return None
        if raw is None:
            self._vm_state = None
            return None
        value = str(raw).strip()
        self._vm_state = value or None
        return self._vm_state

    @staticmethod
    def _is_vm_running(vm_state: Optional[str]) -> bool:
        return isinstance(vm_state, str) and vm_state.strip().lower() == "running"

    @staticmethod
    def _is_vm_known_not_running(vm_state: Optional[str]) -> bool:
        if not isinstance(vm_state, str):
            return False
        lowered = vm_state.strip().lower()
        if not lowered or lowered.startswith("error"):
            return False
        return lowered != "running"

    def _rate_limit_target(self, desired_xx: int) -> int:
        if self._last_applied_xx is None:
            return desired_xx
        if desired_xx > self._last_applied_xx:
            step_xx = max(self._max_step_up_xx, self._command_min_delta_xx)
            return min(desired_xx, self._last_applied_xx + step_xx)
        if desired_xx < self._last_applied_xx:
            step_xx = max(self._max_step_down_xx, self._command_min_delta_xx)
            return max(desired_xx, self._last_applied_xx - step_xx)
        return desired_xx

    def _pi_target_for_temp(self, raw_temp_c: float) -> tuple[int, bool]:
        emergency = raw_temp_c >= self._emergency_temp_c
        if emergency:
            self._pi_error_c = raw_temp_c - self._target_temp_c
            self._projected_temp_c = raw_temp_c
            self._projected_error_c = self._pi_error_c
            self._control_error_c = max(self._pi_error_c, 0.0)
            self._cooldown_release_active = False
            self._desired_fan_xx = int(_clamp(self._emergency_fan_xx, self._min_fan_xx, self._max_fan_xx))
            self._rate_limited_target_xx = self._desired_fan_xx
            return self._desired_fan_xx, True

        alpha = _clamp(self._smoothing_alpha, 0.0, 1.0)
        previous_smoothed_temp = self._smoothed_temp_c
        if self._smoothed_temp_c is None:
            self._smoothed_temp_c = raw_temp_c
        else:
            self._smoothed_temp_c = (alpha * raw_temp_c) + ((1.0 - alpha) * self._smoothed_temp_c)

        now = self._monotonic()
        if previous_smoothed_temp is not None and self._last_temp_sample_monotonic is not None:
            elapsed = now - self._last_temp_sample_monotonic
            if elapsed < 0.5:
                elapsed = 0.0
        else:
            elapsed = 0.0

        if previous_smoothed_temp is not None and elapsed > 0.0:
            observed_rate = (self._smoothed_temp_c - previous_smoothed_temp) / elapsed
            if observed_rate <= 0.0:
                self._temp_rate_c_per_s = 0.0
            else:
                rate_alpha = _clamp(self._derivative_smoothing_alpha, 0.0, 1.0)
                if self._temp_rate_c_per_s is None:
                    self._temp_rate_c_per_s = observed_rate
                else:
                    self._temp_rate_c_per_s = (rate_alpha * observed_rate) + ((1.0 - rate_alpha) * self._temp_rate_c_per_s)
        self._last_temp_sample_monotonic = now

        error = self._smoothed_temp_c - self._target_temp_c
        self._pi_error_c = error
        integral_delta = error * self._poll_seconds
        self._pi_integral = _clamp(self._pi_integral + integral_delta, 0.0, self._integral_clamp)

        gpu_util_percent = self._gpu_util_percent
        low_gpu_load = not isinstance(gpu_util_percent, (int, float)) or gpu_util_percent <= self._cooldown_release_gpu_util_percent
        raw_below_release = raw_temp_c <= (self._target_temp_c - self._cooldown_release_below_target_c)
        self._cooldown_release_active = bool(raw_below_release and low_gpu_load)
        if self._cooldown_release_active:
            self._temp_rate_c_per_s = 0.0
            self._projected_temp_c = self._smoothed_temp_c
            self._projected_error_c = self._projected_temp_c - self._target_temp_c
            self._control_error_c = 0.0
            self._pi_integral = 0.0
            self._desired_fan_xx = self._min_fan_xx
            self._rate_limited_target_xx = self._rate_limit_target(self._desired_fan_xx)
            return self._rate_limited_target_xx, False

        positive_rate = max(self._temp_rate_c_per_s or 0.0, 0.0)
        self._projected_temp_c = self._smoothed_temp_c + (positive_rate * self._derivative_lookahead_seconds)
        self._projected_error_c = self._projected_temp_c - self._target_temp_c
        self._control_error_c = max(error, self._projected_error_c, 0.0)

        over_target_error = max(error, 0.0)
        desired = (
            self._min_fan_xx
            + (self._kp * self._control_error_c)
            + (self._over_target_kp * over_target_error)
            + (self._ki * self._pi_integral)
        )
        self._desired_fan_xx = int(round(_clamp(desired, self._min_fan_xx, self._max_fan_xx)))
        self._rate_limited_target_xx = self._rate_limit_target(self._desired_fan_xx)
        return self._rate_limited_target_xx, False

    async def _apply_target_if_needed(self, target_xx: int, *, force: bool = False) -> None:
        self._last_target_xx = target_xx
        now = self._monotonic()
        if self._last_applied_xx == target_xx:
            return
        if not force and self._last_applied_xx is not None and abs(target_xx - self._last_applied_xx) < self._command_min_delta_xx:
            return
        if not force and self._last_apply_monotonic is not None:
            elapsed = now - self._last_apply_monotonic
            if elapsed < self._min_change_interval_seconds:
                return

        cmd_result = await asyncio.to_thread(self._fan_setter, target_xx)
        self._last_command_ok = bool(cmd_result.get("ok"))
        self._last_command_at = cmd_result.get("timestamp") or _utc_now_iso()

        if self._last_command_ok:
            self._last_applied_xx = target_xx
            self._last_apply_monotonic = now
            if not self._log_transitions_only:
                logger.info("GPU watchdog applied fan min xx=%s", target_xx)
            return

        err = cmd_result.get("error") or "failed to apply fan command"
        self._last_error = f"fan command failed: {err}"
        logger.warning("GPU watchdog fan command failed: %s", err)

    async def step_once(self) -> None:
        try:
            self._status_updated_at = _utc_now_iso()

            if self._config_error:
                self._mode = MODE_DISABLED
                self._mode_reason = "config_error"
                self._telemetry_applicable = False
                self._last_error = self._config_error
                return

            if not self._enabled:
                self._mode = MODE_DISABLED
                self._mode_reason = "watchdog_disabled"
                self._telemetry_applicable = False
                self._last_error = None
                return

            vm_state = self._read_vm_state()
            vm_running = self._is_vm_running(vm_state)
            vm_known_not_running = self._is_vm_known_not_running(vm_state)

            if self._vm_off_idle_enabled and vm_known_not_running:
                self._mode = MODE_VM_OFF_IDLE
                self._mode_reason = f"vm_{str(vm_state).strip().lower()}"
                self._telemetry_applicable = False
                self._telemetry_ok = False
                self._gpu_name = None
                self._gpu_id = None
                self._gpu_temp_c = None
                self._gpu_util_percent = None
                self._gpu_mem_util_percent = None
                self._last_error = None
                self._smoothed_temp_c = None
                self._pi_error_c = None
                self._pi_integral = 0.0
                self._temp_rate_c_per_s = None
                self._projected_temp_c = None
                self._projected_error_c = None
                self._control_error_c = None
                self._cooldown_release_active = False
                self._last_temp_sample_monotonic = None
                await self._apply_target_if_needed(self._vm_off_fan_min_xx)
                if self._last_transition_mode != self._mode:
                    logger.info("GPU watchdog mode transition: %s -> %s", self._last_transition_mode, self._mode)
                    self._last_transition_mode = self._mode
                return

            self._telemetry_applicable = True
            telemetry: Dict[str, Any]
            try:
                telemetry = await asyncio.to_thread(self._telemetry_getter)
            except Exception as exc:
                telemetry = {
                    "telemetry_ok": False,
                    "error": f"telemetry exception: {exc}",
                    "source": "remote_glances",
                    "updated_at": _utc_now_iso(),
                }

            self._telemetry_source = telemetry.get("source") or self._telemetry_source
            self._telemetry_ok = bool(telemetry.get("telemetry_ok"))
            self._gpu_name = telemetry.get("gpu_name")
            self._gpu_id = telemetry.get("gpu_id")
            self._gpu_temp_c = telemetry.get("gpu_temp_c")
            self._gpu_util_percent = telemetry.get("gpu_util_percent")
            self._gpu_mem_util_percent = telemetry.get("gpu_mem_util_percent")

            stale = self._telemetry_is_stale(telemetry)
            telemetry_error = telemetry.get("error")
            healthy = self._telemetry_ok and self._gpu_temp_c is not None and not stale

            if not healthy:
                self._mode = MODE_FAILSAFE
                self._mode_reason = "telemetry_invalid"
                if stale:
                    self._last_error = "telemetry stale"
                    self._mode_reason = "telemetry_stale"
                elif telemetry_error:
                    self._last_error = f"telemetry error: {telemetry_error}"
                    self._mode_reason = "telemetry_error"
                else:
                    self._last_error = "telemetry invalid"
                if vm_running:
                    self._mode_reason = f"{self._mode_reason}_vm_running"
                elif vm_state is None:
                    self._mode_reason = f"{self._mode_reason}_vm_unknown"
                await self._apply_target_if_needed(self._failsafe_fan_min_xx)
            else:
                if self._mode == MODE_FAILSAFE:
                    logger.info("GPU watchdog leaving failsafe mode (telemetry healthy)")
                self._mode = MODE_AUTO
                self._mode_reason = "pi_controller"
                self._last_error = None
                target, emergency = self._pi_target_for_temp(float(self._gpu_temp_c))
                await self._apply_target_if_needed(target, force=emergency)

            if self._last_transition_mode != self._mode:
                logger.info("GPU watchdog mode transition: %s -> %s", self._last_transition_mode, self._mode)
                self._last_transition_mode = self._mode
        finally:
            self._record_history_sample()

    async def run_loop(self) -> None:
        logger.info("GPU watchdog loop started")
        try:
            while True:
                await self.step_once()
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            logger.info("GPU watchdog loop stopped")
            raise
