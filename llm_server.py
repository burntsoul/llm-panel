# llm_server.py
from __future__ import annotations

import time
import datetime
import asyncio
from typing import Optional, Tuple

import requests

from config import settings
from proxmox import get_vm_status, start_vm, shutdown_vm
from state import get_maintenance_mode


_last_activity = datetime.datetime.utcnow()


def touch_activity() -> None:
    """Merkitse, että LLM:ää juuri käytettiin."""
    global _last_activity
    _last_activity = datetime.datetime.utcnow()


def get_last_activity() -> datetime.datetime:
    return _last_activity


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


async def idle_shutdown_loop() -> None:
    """
    Taustasäie, joka tarkkailee LLM:n käyttöä ja sammuttaa LLM-VM:n
    kun sitä ei ole käytetty pitkään aikaan.
    
    Respects active leases:
    - If any active leases exist, VM stays ON regardless of idle time
    - If no leases, falls back to traditional idle timeout logic
    - Maintenance mode disables shutdown
    """
    global _last_activity
    
    # Import here to avoid circular dependency
    from lease import get_lease_manager
    
    while True:
        await asyncio.sleep(60)

        if get_maintenance_mode():
            continue

        if not llm_server_up():
            continue

        # Check for active leases
        lease_mgr = get_lease_manager()
        has_leases = lease_mgr.has_active_leases()
        
        if has_leases:
            # Keep VM running if there are active leases
            continue

        # No leases; use traditional idle timeout
        idle = (datetime.datetime.utcnow() - _last_activity).total_seconds()
        if idle > settings.LLM_IDLE_SECONDS:
            # käytä lempeää shutdownia (ei force stop)
            shutdown_vm(settings.LLM_VM_ID, wait_stopped=False)
            # halutessa voisi lisätä varmistus-stopin myöhemmin


async def cpu_activity_poller() -> None:
    """
    Pollaa llm-serverin CPU-kuormaa säännöllisesti ja
    kutsuu touch_activity(), jos kuorma on selvästi ei-idle.
    Näin idle_shutdown_loop ei laukea, kun LLM:ää käytetään
    suoraan esimerkiksi VS Codesta.

    (Huoltotilassa ei tarvitse tehdä mitään, mutta poller ei haittaa.)
    """
    while True:
        try:
            total = await asyncio.to_thread(get_llm_server_cpu_total)
            if total is not None and total >= settings.CPU_BUSY_THRESHOLD_FOR_IDLE:
                touch_activity()
        except Exception:
            pass

        await asyncio.sleep(settings.CPU_POLL_INTERVAL_SECONDS)
