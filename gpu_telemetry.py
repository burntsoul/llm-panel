from __future__ import annotations

import datetime
import logging
import time
from typing import Any, Dict, Optional

import requests

from config import settings
from proxmox import get_vm_status


logger = logging.getLogger(__name__)
_last_warning_at: Dict[str, float] = {}


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _to_float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_percent(value: Any) -> Optional[float]:
    """
    Normalize percentage-like values.
    - 0..1 is treated as a fraction and converted to 0..100
    - 0..100 is treated as percent
    - Values outside that range are clamped to 0..100
    """
    parsed = _to_float_or_none(value)
    if parsed is None:
        return None

    if 0.0 <= parsed <= 1.0:
        parsed = parsed * 100.0

    if parsed < 0.0:
        return 0.0
    if parsed > 100.0:
        return 100.0
    return parsed


def _base_result() -> Dict[str, Any]:
    return {
        "telemetry_ok": False,
        "telemetry_applicable": True,
        "source": "remote_glances",
        "vm_state": None,
        "gpu_id": None,
        "gpu_name": None,
        "gpu_temp_c": None,
        "gpu_util_percent": None,
        "gpu_mem_util_percent": None,
        "error": None,
        "updated_at": _utc_now_iso(),
    }


def _is_vm_known_not_running(vm_state: Optional[str]) -> bool:
    if not isinstance(vm_state, str):
        return False
    lowered = vm_state.strip().lower()
    if not lowered or lowered.startswith("error"):
        return False
    return lowered != "running"


def _warning_throttled(key: str, message: str, *args: Any) -> None:
    interval = max(0.0, float(settings.GPU_TELEMETRY_LOG_THROTTLE_SECONDS))
    now = time.monotonic()
    previous = _last_warning_at.get(key)
    if previous is not None and now - previous < interval:
        return
    _last_warning_at[key] = now
    logger.warning(message, *args)


def _maybe_skip_when_vm_off(result: Dict[str, Any]) -> bool:
    if not settings.GPU_TELEMETRY_SKIP_WHEN_VM_OFF:
        return False

    try:
        vm_state = get_vm_status(settings.LLM_VM_ID)
    except Exception as exc:
        result["vm_state"] = f"ERROR: {exc}"
        _warning_throttled("vm_state", "GPU telemetry VM state check failed: %s", exc)
        return False

    result["vm_state"] = vm_state
    if not _is_vm_known_not_running(vm_state):
        return False

    result["telemetry_applicable"] = False
    result["error"] = f"LLM VM is {str(vm_state).strip().lower()}; GPU telemetry skipped"
    return True


def normalize_glances_gpu_payload(
    payload: Any,
    glances_gpu_id: Optional[str] = None,
    include_raw: bool = False,
) -> Dict[str, Any]:
    result = _base_result()

    if not isinstance(payload, list):
        result["error"] = "invalid GPU payload type (expected list)"
        return result

    if not payload:
        result["error"] = "GPU payload empty"
        return result

    selected = None
    requested_id = (glances_gpu_id or "").strip()
    if requested_id:
        for sample in payload:
            if isinstance(sample, dict) and str(sample.get("gpu_id")) == requested_id:
                selected = sample
                break
        if selected is None:
            result["error"] = f"configured GPU not found: {requested_id}"
            return result
    else:
        first = payload[0]
        if isinstance(first, dict):
            selected = first
        else:
            result["error"] = "invalid GPU sample type (expected object)"
            return result

    result["gpu_id"] = selected.get("gpu_id")
    result["gpu_name"] = selected.get("name")
    result["gpu_temp_c"] = _to_float_or_none(selected.get("temperature"))
    result["gpu_util_percent"] = _normalize_percent(selected.get("proc"))
    result["gpu_mem_util_percent"] = _normalize_percent(selected.get("mem"))
    result["telemetry_ok"] = True

    if include_raw:
        result["raw_sample"] = selected

    return result


def get_remote_glances_gpu_telemetry(include_raw: bool = False) -> Dict[str, Any]:
    result = _base_result()
    if _maybe_skip_when_vm_off(result):
        return result

    url = f"{settings.GLANCES_API_BASE_V4.rstrip('/')}/gpu"

    try:
        response = requests.get(url, timeout=settings.GLANCES_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout:
        result["error"] = "GPU telemetry request timed out"
        _warning_throttled("timeout", "GPU telemetry timeout from remote Glances")
        return result
    except Exception as exc:
        result["error"] = f"GPU telemetry fetch failed: {exc}"
        _warning_throttled("fetch_failed", "GPU telemetry fetch failed: %s", exc)
        return result

    normalized = normalize_glances_gpu_payload(
        payload=payload,
        glances_gpu_id=settings.GLANCES_GPU_ID,
        include_raw=include_raw,
    )
    normalized["vm_state"] = result.get("vm_state")
    return normalized


def get_gpu_telemetry(include_raw: bool = False) -> Dict[str, Any]:
    provider = (settings.GPU_TELEMETRY_PROVIDER or "").strip().lower()
    if provider == "remote_glances":
        return get_remote_glances_gpu_telemetry(include_raw=include_raw)

    result = _base_result()
    result["error"] = f"unsupported GPU telemetry provider: {provider or 'unset'}"
    logger.warning("Unsupported GPU telemetry provider: %s", provider or "unset")
    return result
