# llm_server.py
from __future__ import annotations

import time
import datetime
import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import requests

from config import settings
import llama_cpp_provider
from proxmox import get_vm_status, start_vm, shutdown_vm
from state import get_maintenance_mode


logger = logging.getLogger("llm-agent.llm-idle")

_last_activity = datetime.datetime.utcnow()
_ollama_was_up = False  # Track Ollama state transitions for auto-sync
_last_cpu_total: Optional[float] = None
_slot_activity: Dict[str, Any] = {
    "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
    "profile_id": None,
    "served_model_id": None,
    "active_slots": 0,
    "total_slots": 0,
    "error": "slot activity has not been polled",
    "checked_at_monotonic": 0.0,
}

_SLOT_STALE_SECONDS = max(30.0, float(settings.CPU_POLL_INTERVAL_SECONDS) * 3.0)
_SHUTDOWN_SAFE_SLOT_STATES = {
    llama_cpp_provider.SLOT_STATE_IDLE,
    llama_cpp_provider.SLOT_STATE_NO_SERVER,
}
_SHUTDOWN_BLOCKING_SLOT_STATES = {
    llama_cpp_provider.SLOT_STATE_BUSY,
    llama_cpp_provider.SLOT_STATE_LOADING,
    llama_cpp_provider.SLOT_STATE_UNKNOWN,
}


def touch_activity() -> None:
    """Merkitse, että LLM:ää juuri käytettiin."""
    global _last_activity
    _last_activity = datetime.datetime.utcnow()


def get_last_activity() -> datetime.datetime:
    return _last_activity


def get_llama_cpp_activity_state() -> Dict[str, Any]:
    """Return the most recently observed managed llama.cpp slot state."""
    return dict(_slot_activity)


def _record_slot_activity(result: Dict[str, Any], checked_at: Optional[float] = None) -> Dict[str, Any]:
    """Store a slot probe result and log meaningful state/error transitions."""
    global _slot_activity

    valid_states = _SHUTDOWN_SAFE_SLOT_STATES | _SHUTDOWN_BLOCKING_SLOT_STATES
    state = result.get("state")
    if state not in valid_states:
        result = {
            **result,
            "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
            "error": result.get("error") or f"invalid slot state: {state!r}",
        }

    previous = _slot_activity
    snapshot = {
        "state": result.get("state"),
        "profile_id": result.get("profile_id"),
        "served_model_id": result.get("served_model_id"),
        "active_slots": int(result.get("active_slots") or 0),
        "total_slots": int(result.get("total_slots") or 0),
        "error": result.get("error"),
        "checked_at_monotonic": time.monotonic() if checked_at is None else float(checked_at),
    }
    _slot_activity = snapshot

    transitioned = (
        previous.get("state") != snapshot["state"]
        or previous.get("profile_id") != snapshot["profile_id"]
        or previous.get("active_slots") != snapshot["active_slots"]
    )
    if transitioned:
        logger.info(
            "llama.cpp slot state transition profile=%s model=%s state=%s active_slots=%d total_slots=%d",
            snapshot["profile_id"] or "none",
            snapshot["served_model_id"] or "none",
            snapshot["state"],
            snapshot["active_slots"],
            snapshot["total_slots"],
        )
    if snapshot.get("error") and (transitioned or previous.get("error") != snapshot["error"]):
        logger.warning(
            "llama.cpp slot probe error profile=%s state=%s active_slots=%d total_slots=%d error=%s",
            snapshot["profile_id"] or "none",
            snapshot["state"],
            snapshot["active_slots"],
            snapshot["total_slots"],
            snapshot["error"],
        )
    return dict(snapshot)


def _slot_activity_is_stale(
    snapshot: Optional[Dict[str, Any]] = None,
    now_monotonic: Optional[float] = None,
) -> bool:
    current = snapshot or _slot_activity
    checked_at = float(current.get("checked_at_monotonic") or 0.0)
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    return checked_at <= 0.0 or now - checked_at > _SLOT_STALE_SECONDS


def _slot_activity_blocks_shutdown(
    snapshot: Optional[Dict[str, Any]] = None,
    now_monotonic: Optional[float] = None,
) -> bool:
    current = snapshot or _slot_activity
    return (
        _slot_activity_is_stale(current, now_monotonic)
        or current.get("state") not in _SHUTDOWN_SAFE_SLOT_STATES
    )


def _format_cpu(total: Optional[float]) -> str:
    return "unknown" if total is None else f"{total:.1f}"


def _log_shutdown_decision(
    decision: str,
    reason: str,
    *,
    snapshot: Optional[Dict[str, Any]] = None,
    idle_seconds: Optional[float] = None,
    cpu_total: Optional[float] = None,
    lease_count: Optional[int] = None,
    maintenance: Optional[bool] = None,
) -> None:
    current = snapshot or _slot_activity
    stale = _slot_activity_is_stale(current)
    logger.info(
        "LLM VM shutdown decision=%s reason=%s profile=%s model=%s slot_state=%s "
        "active_slots=%d total_slots=%d slot_stale=%s idle_seconds=%.1f cpu_total=%s "
        "lease_state=%s maintenance=%s",
        decision,
        reason,
        current.get("profile_id") or "none",
        current.get("served_model_id") or "none",
        current.get("state") or llama_cpp_provider.SLOT_STATE_UNKNOWN,
        int(current.get("active_slots") or 0),
        int(current.get("total_slots") or 0),
        stale,
        max(0.0, idle_seconds or 0.0),
        _format_cpu(cpu_total),
        "unknown" if lease_count is None else f"active:{lease_count}",
        "unknown" if maintenance is None else maintenance,
    )


def detect_ollama_online_transition() -> bool:
    """
    Tarkista, siirtymä Ollama olemaan online (offline -> online).
    Käytä model_meta.json synkronointiin.
    
    Returns:
        True jos Ollama juuri meni online (siirtymä offline->online), False muuten
    """
    global _ollama_was_up
    current_state = llm_server_up()
    
    if not _ollama_was_up and current_state:
        # Siirtymä: False -> True (offline -> online)
        _ollama_was_up = True
        return True
    
    _ollama_was_up = current_state
    return False


def llm_server_up() -> bool:
    """Tarkista vastaako Ollama /api/tags:iin."""
    try:
        r = requests.get(
            f"http://{settings.LLM_HOST}:{settings.LLM_PORT}/api/tags",
            timeout=1.5,
        )
        return r.ok
    except Exception:
        return False


def is_llm_ready() -> bool:
    """
    Check if LLM server is ready by querying the readiness endpoint.
    This is used by lease endpoints to confirm the LLM is operational.
    """
    try:
        url = f"{settings.LLM_BASE_URL}{settings.LLM_READINESS_PATH}"
        r = requests.get(url, timeout=2.0)
        return r.ok
    except Exception:
        return False


async def wait_for_llm_ready(timeout: int | None = None) -> bool:
    """
    Wait for LLM readiness endpoint to respond.
    Uses exponential backoff (start at 0.5s, max 3s between attempts).

    Args:
        timeout: Maximum time to wait in seconds

    Returns:
        True if LLM became ready, False if timeout
    """
    if timeout is None:
        timeout = settings.LLM_READINESS_TIMEOUT

    loop = asyncio.get_running_loop()
    start = loop.time()
    backoff = 0.5

    while loop.time() - start < timeout:
        ready = await loop.run_in_executor(None, is_llm_ready)
        if ready:
            touch_activity()
            return True

        wait_time = min(backoff, 3.0)
        await asyncio.sleep(wait_time)
        backoff *= 1.5

    return False


def get_llm_server_cpu_total() -> Optional[float]:
    """
    Palauttaa llm-serverin kokonais-CPU-käytön prosentteina (0-100),
    tai None jos lukemaa ei saatu.
    """
    try:
        resp = requests.get(f"{settings.GLANCES_API_BASE}/cpu", timeout=1.0)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total")
        if total is None:
            return None
        return float(total)
    except Exception:
        return None


def is_llm_server_busy(threshold: Optional[float] = None) -> bool:
    """True jos LLM-serverin CPU on yli rajan."""
    if threshold is None:
        threshold = settings.CPU_BUSY_THRESHOLD_FOR_IDLE
    total = get_llm_server_cpu_total()
    if total is None:
        # jos emme saa lukemaa, oletetaan ettei ole kiireellinen estää
        return False
    return total >= threshold


def ensure_llm_running_with_reason() -> Tuple[bool, str]:
    """
    Varmista, että LLM-VM + Ollama on käynnissä.
    - Jos Ollama on jo UP, palauttaa (True, ...)
    - Muuten käynnistää Proxmoxista LLM-VM:n ja odottaa että /api/tags vastaa
    - Jos EXCLUSIVE_VMS on päällä ja Windows-VM on käynnissä, ei käynnistä.
    """
    if llm_server_up():
        # Ollama on jo käynnissä - tarkista onko se juuri tullut online (siirtymä)
        if detect_ollama_online_transition():
            # Ollama juuri meni online - synkronoi model_meta.json
            try:
                from models import sync_model_meta_with_ollama
                sync_model_meta_with_ollama()
            except Exception:
                # Ei pysäytä operaatiota jos sync epäonnistuu
                pass
        return True, "Ollama on jo käynnissä."

    # GPU-exclusivity
    if settings.ENFORCE_EXCLUSIVE_VMS:
        try:
            win_status = get_vm_status(settings.WINDOWS_VM_ID)
        except Exception as e:
            return False, f"Windows-VM statusta ei saatu: {e}"
        if win_status == "running":
            return False, "Windows-VM on käynnissä. Sammuta Windows-VM ennen LLM-VM:n käynnistystä."

    # Start LLM VM if needed
    try:
        st = get_vm_status(settings.LLM_VM_ID)
    except Exception as e:
        return False, f"LLM-VM statusta ei saatu: {e}"

    if st != "running":
        ok, msg = start_vm(settings.LLM_VM_ID, wait_running=True, timeout_s=90)
        if not ok:
            return False, f"LLM-VM start epäonnistui: {msg}"

    # Wait for Ollama API
    deadline = time.time() + settings.LLM_BOOT_TIMEOUT
    while time.time() < deadline:
        if llm_server_up():
            touch_activity()
            # Ollama juuri tuli online - synkronoi model_meta.json
            if detect_ollama_online_transition():
                try:
                    from models import sync_model_meta_with_ollama
                    sync_model_meta_with_ollama()
                except Exception:
                    # Ei pysäytä operaatiota jos sync epäonnistuu
                    pass
            return True, "LLM on käynnissä ja valmis."
        time.sleep(settings.LLM_POLL_INTERVAL)

    return False, "LLM käynnistys aikakatkaistiin (Ollama ei vastannut /api/tags)."


def ensure_llm_running() -> bool:
    ok, _ = ensure_llm_running_with_reason()
    return ok


async def ensure_llm_running_and_ready(timeout: int | None = None) -> bool:
    """Async-versio: käynnistää LLM:n threadissä ja odottaa, että /api/tags vastaa."""
    if timeout is None:
        timeout = settings.LLM_BOOT_TIMEOUT

    loop = asyncio.get_running_loop()
    start = loop.time()

    ok, _ = await loop.run_in_executor(None, ensure_llm_running_with_reason)
    if not ok:
        return False

    while loop.time() - start < timeout:
        up = await loop.run_in_executor(None, llm_server_up)
        if up:
            touch_activity()
            return True
        await asyncio.sleep(3)

    return False


async def poll_llm_activity_once() -> Dict[str, Any]:
    """Poll managed llama.cpp slots first and aggregate CPU as a supplement."""
    global _last_cpu_total

    try:
        result = await asyncio.to_thread(llama_cpp_provider.probe_active_profile_slots, 2.0)
    except Exception as exc:
        result = {
            "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
            "profile_id": None,
            "served_model_id": None,
            "active_slots": 0,
            "total_slots": 0,
            "error": f"slot activity poll failed: {exc}",
        }
    if not isinstance(result, dict):
        result = {
            "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
            "profile_id": None,
            "served_model_id": None,
            "active_slots": 0,
            "total_slots": 0,
            "error": "slot activity poll returned a non-object result",
        }
    snapshot = _record_slot_activity(result)

    try:
        _last_cpu_total = await asyncio.to_thread(get_llm_server_cpu_total)
    except Exception:
        _last_cpu_total = None

    # Loading is active profile-switch/startup work. Unknown is fail-safe blocked
    # without moving the timer, so shutdown resumes only after a valid probe.
    if snapshot["state"] in {
        llama_cpp_provider.SLOT_STATE_BUSY,
        llama_cpp_provider.SLOT_STATE_LOADING,
    }:
        touch_activity()
    if _last_cpu_total is not None and _last_cpu_total >= settings.CPU_BUSY_THRESHOLD_FOR_IDLE:
        touch_activity()

    return snapshot


async def llm_activity_poller() -> None:
    """Continuously combine authoritative slot activity with supplemental CPU."""
    while True:
        try:
            await poll_llm_activity_once()
        except Exception as exc:
            # Keep polling so an indeterminate state can recover on a later pass.
            logger.exception("LLM activity poll failed; preserving fail-safe slot state")
            try:
                _record_slot_activity(
                    {
                        "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
                        "profile_id": _slot_activity.get("profile_id"),
                        "served_model_id": _slot_activity.get("served_model_id"),
                        "active_slots": 0,
                        "total_slots": 0,
                        "error": f"activity poller failed: {exc}",
                    }
                )
            except Exception:
                logger.exception("Could not record fail-safe LLM slot state")
        await asyncio.sleep(settings.CPU_POLL_INTERVAL_SECONDS)


async def run_idle_shutdown_check() -> bool:
    """Evaluate and, when every fail-safe permits it, shut down the LLM VM."""
    global _last_cpu_total

    # Import here to avoid circular dependency.
    from lease import get_lease_manager

    idle = (datetime.datetime.utcnow() - _last_activity).total_seconds()
    if idle <= settings.LLM_IDLE_SECONDS:
        return False

    snapshot = get_llama_cpp_activity_state()
    maintenance = get_maintenance_mode()
    lease_count: Optional[int]
    try:
        lease_count = len(get_lease_manager().get_active_leases())
    except Exception as exc:
        lease_count = None
        _log_shutdown_decision(
            "inhibited",
            f"lease state unavailable: {exc}",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    if maintenance:
        _log_shutdown_decision(
            "inhibited",
            "maintenance mode",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    if lease_count:
        _log_shutdown_decision(
            "inhibited",
            "active lease",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    if _slot_activity_blocks_shutdown(snapshot):
        reason = (
            "stale slot state"
            if _slot_activity_is_stale(snapshot)
            else f"slot state {snapshot.get('state') or llama_cpp_provider.SLOT_STATE_UNKNOWN}"
        )
        _log_shutdown_decision(
            "inhibited",
            reason,
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    try:
        vm_status = await asyncio.to_thread(get_vm_status, settings.LLM_VM_ID)
    except Exception as exc:
        _log_shutdown_decision(
            "inhibited",
            f"LLM VM status unavailable: {exc}",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False
    if vm_status != "running":
        _log_shutdown_decision(
            "skipped",
            f"LLM VM is not running (status={vm_status})",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    # CPU can only extend activity. A low or unavailable value never proves idle.
    try:
        _last_cpu_total = await asyncio.to_thread(get_llm_server_cpu_total)
    except Exception:
        _last_cpu_total = None
    if _last_cpu_total is not None and _last_cpu_total >= settings.CPU_BUSY_THRESHOLD_FOR_IDLE:
        touch_activity()
        _log_shutdown_decision(
            "inhibited",
            "supplemental CPU activity",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    # Recheck local holds immediately before the authoritative final slot probe.
    maintenance = get_maintenance_mode()
    try:
        lease_count = len(get_lease_manager().get_active_leases())
    except Exception as exc:
        _log_shutdown_decision(
            "inhibited",
            f"lease state unavailable before final probe: {exc}",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=None,
            maintenance=maintenance,
        )
        return False
    if maintenance or lease_count:
        _log_shutdown_decision(
            "inhibited",
            "maintenance mode" if maintenance else "active lease before final probe",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    try:
        final_result = await asyncio.to_thread(llama_cpp_provider.probe_active_profile_slots, 2.0)
    except Exception as exc:
        final_result = {
            "state": llama_cpp_provider.SLOT_STATE_UNKNOWN,
            "profile_id": snapshot.get("profile_id"),
            "served_model_id": snapshot.get("served_model_id"),
            "active_slots": 0,
            "total_slots": 0,
            "error": f"final slot probe failed: {exc}",
        }
    snapshot = _record_slot_activity(final_result)
    if snapshot["state"] in {
        llama_cpp_provider.SLOT_STATE_BUSY,
        llama_cpp_provider.SLOT_STATE_LOADING,
    }:
        touch_activity()

    idle = (datetime.datetime.utcnow() - _last_activity).total_seconds()
    if _slot_activity_blocks_shutdown(snapshot):
        _log_shutdown_decision(
            "inhibited",
            f"final slot state {snapshot['state']}",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False
    if idle <= settings.LLM_IDLE_SECONDS:
        _log_shutdown_decision(
            "inhibited",
            "activity refreshed during shutdown evaluation",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
        return False

    _log_shutdown_decision(
        "proceeding",
        "definitive idle slot state and idle timeout exceeded",
        snapshot=snapshot,
        idle_seconds=idle,
        cpu_total=_last_cpu_total,
        lease_count=lease_count,
        maintenance=maintenance,
    )
    ok, message = await asyncio.to_thread(shutdown_vm, settings.LLM_VM_ID, wait_stopped=False)
    if not ok:
        _log_shutdown_decision(
            "failed",
            message or "shutdown request failed",
            snapshot=snapshot,
            idle_seconds=idle,
            cpu_total=_last_cpu_total,
            lease_count=lease_count,
            maintenance=maintenance,
        )
    return bool(ok)


async def idle_shutdown_loop() -> None:
    """Run the fail-safe LLM VM idle shutdown evaluation once per minute."""
    while True:
        await asyncio.sleep(60)
        try:
            await run_idle_shutdown_check()
        except Exception:
            logger.exception("LLM VM idle shutdown evaluation failed; preserving VM state")


async def cpu_activity_poller() -> None:
    """Backward-compatible alias for the unified activity poller."""
    await llm_activity_poller()
