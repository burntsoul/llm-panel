from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Callable, Dict, Optional, Tuple

from config import settings
from gpu_telemetry import get_gpu_telemetry
from ilo_fan import set_ilo_fan_min


logger = logging.getLogger(__name__)


MODE_DISABLED = "disabled"
MODE_AUTO = "auto"
MODE_FAILSAFE = "failsafe"

DEFAULT_POLICY = (
    {"name": "lt60", "min_temp": None, "max_temp": 59.999, "xx": 80},
    {"name": "60-67", "min_temp": 60.0, "max_temp": 67.999, "xx": 110},
    {"name": "68-73", "min_temp": 68.0, "max_temp": 73.999, "xx": 150},
    {"name": "74-79", "min_temp": 74.0, "max_temp": 79.999, "xx": 190},
    {"name": "ge80", "min_temp": 80.0, "max_temp": None, "xx": 230},
)


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
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._telemetry_getter = telemetry_getter
        self._fan_setter = fan_setter
        self._monotonic = monotonic_fn
        self._task: Optional[asyncio.Task] = None

        self._policy = tuple(DEFAULT_POLICY)
        self._poll_seconds = float(settings.WATCHDOG_POLL_SECONDS)
        self._min_change_interval_seconds = float(settings.WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS)
        self._failsafe_fan_min_xx = int(settings.WATCHDOG_FAILSAFE_FAN_MIN_XX)
        self._hysteresis_c = float(settings.WATCHDOG_HYSTERESIS_C)
        self._telemetry_stale_seconds = float(settings.WATCHDOG_TELEMETRY_STALE_SECONDS)
        self._log_transitions_only = bool(settings.WATCHDOG_LOG_TRANSITIONS_ONLY)

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

        self._telemetry_source = "remote_glances"
        self._telemetry_ok = False
        self._gpu_name: Optional[str] = None
        self._gpu_id: Optional[str] = None
        self._gpu_temp_c: Optional[float] = None
        self._gpu_util_percent: Optional[float] = None
        self._gpu_mem_util_percent: Optional[float] = None

        self._config_error = self._validate_runtime_config()
        if self._config_error:
            self._enabled = False
            self._last_error = self._config_error

    def _validate_runtime_config(self) -> Optional[str]:
        if not settings.ILO_HOST or not settings.ILO_USER or not settings.ILO_PASSWORD:
            return "watchdog disabled: ILO_HOST/ILO_USER/ILO_PASSWORD not configured"
        return None

    def _policy_summary(self) -> str:
        return ",".join(f"{row['name']}={row['xx']}" for row in self._policy)

    def get_status(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "telemetry_source": self._telemetry_source,
            "telemetry_ok": self._telemetry_ok,
            "gpu_name": self._gpu_name,
            "gpu_id": self._gpu_id,
            "gpu_temp_c": self._gpu_temp_c,
            "gpu_util_percent": self._gpu_util_percent,
            "gpu_mem_util_percent": self._gpu_mem_util_percent,
            "last_target_xx": self._last_target_xx,
            "last_applied_xx": self._last_applied_xx,
            "last_command_ok": self._last_command_ok,
            "last_command_at": self._last_command_at,
            "last_error": self._last_error,
            "updated_at": self._status_updated_at,
            "poll_seconds": self._poll_seconds,
            "min_change_interval_seconds": self._min_change_interval_seconds,
            "failsafe_fan_min_xx": self._failsafe_fan_min_xx,
            "hysteresis_c": self._hysteresis_c,
            "thresholds": self._policy_summary(),
        }

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._mode = MODE_DISABLED
        self._status_updated_at = _utc_now_iso()
        if self._log_transitions_only:
            logger.info("GPU watchdog enabled=%s", self._enabled)

    def reset_error(self) -> None:
        self._last_error = None
        self._status_updated_at = _utc_now_iso()

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

    def _band_index_for_temp(self, temp_c: float) -> int:
        for idx, row in enumerate(self._policy):
            min_t = row["min_temp"]
            max_t = row["max_temp"]
            if min_t is not None and temp_c < min_t:
                continue
            if max_t is not None and temp_c > max_t:
                continue
            return idx
        return len(self._policy) - 1

    def _band_index_for_target(self, xx: Optional[int]) -> Optional[int]:
        if xx is None:
            return None
        for idx, row in enumerate(self._policy):
            if int(row["xx"]) == int(xx):
                return idx
        return None

    def _apply_hysteresis(self, temp_c: float, base_idx: int, current_idx: Optional[int]) -> int:
        if current_idx is None:
            return base_idx
        if base_idx >= current_idx:
            return base_idx

        allowed_idx = current_idx
        while allowed_idx > base_idx:
            lower_idx = allowed_idx - 1
            lower_max = self._policy[lower_idx]["max_temp"]
            if lower_max is None:
                allowed_idx = lower_idx
                continue
            if temp_c <= (float(lower_max) - self._hysteresis_c):
                allowed_idx = lower_idx
                continue
            break
        return allowed_idx

    def target_xx_for_temp(self, temp_c: float, current_target_xx: Optional[int]) -> int:
        base_idx = self._band_index_for_temp(temp_c)
        current_idx = self._band_index_for_target(current_target_xx)
        final_idx = self._apply_hysteresis(temp_c, base_idx, current_idx)
        return int(self._policy[final_idx]["xx"])

    def _telemetry_is_stale(self, telemetry: Dict[str, Any]) -> bool:
        dt = _parse_iso_timestamp(telemetry.get("updated_at"))
        if dt is None:
            return False
        age = (datetime.datetime.utcnow() - dt).total_seconds()
        return age > self._telemetry_stale_seconds

    async def _apply_target_if_needed(self, target_xx: int) -> None:
        self._last_target_xx = target_xx
        now = self._monotonic()
        if self._last_applied_xx == target_xx:
            return
        if self._last_apply_monotonic is not None:
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
        self._status_updated_at = _utc_now_iso()

        if self._config_error:
            self._mode = MODE_DISABLED
            self._last_error = self._config_error
            return

        if not self._enabled:
            self._mode = MODE_DISABLED
            return

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
            if stale:
                self._last_error = "telemetry stale"
            elif telemetry_error:
                self._last_error = f"telemetry error: {telemetry_error}"
            else:
                self._last_error = "telemetry invalid"
            await self._apply_target_if_needed(self._failsafe_fan_min_xx)
        else:
            if self._mode == MODE_FAILSAFE:
                logger.info("GPU watchdog leaving failsafe mode (telemetry healthy)")
            self._mode = MODE_AUTO
            self._last_error = None
            target = self.target_xx_for_temp(float(self._gpu_temp_c), self._last_target_xx)
            await self._apply_target_if_needed(target)

        if self._last_transition_mode != self._mode:
            logger.info("GPU watchdog mode transition: %s -> %s", self._last_transition_mode, self._mode)
            self._last_transition_mode = self._mode

    async def run_loop(self) -> None:
        logger.info("GPU watchdog loop started")
        try:
            while True:
                await self.step_once()
                await asyncio.sleep(self._poll_seconds)
        except asyncio.CancelledError:
            logger.info("GPU watchdog loop stopped")
            raise

