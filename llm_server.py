# llm_server.py
import time
import datetime
import asyncio
import requests

from config import settings
from lo100 import lo100_power


_last_activity = datetime.datetime.utcnow()


def touch_activity():
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


def get_llm_server_cpu_total():
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


def is_llm_server_busy(threshold: float | None = None) -> bool:
    """
    Palauttaa True jos llm-serverin CPU-kuorma ylittää threshold-%.
    Virhetilanteessa palauttaa True (fail safe, ei sammuteta sokkona).
    """
    if threshold is None:
        threshold = settings.CPU_BUSY_THRESHOLD_FOR_IDLE

    total = get_llm_server_cpu_total()
    if total is None:
        return True
    return total >= threshold


def ensure_llm_running() -> bool:
    """
    Varmista, että LLM-palvelin on käynnissä.
    - Jos on jo UP, palauttaa True.
    - Muuten lähettää LO100:lle 'power on' ja odottaa, kunnes /api/tags vastaa
      tai timeout.
    """
    if llm_server_up():
        return True

    lo100_power("on")

    deadline = time.time() + settings.LLM_BOOT_TIMEOUT
    while time.time() < deadline:
        if llm_server_up():
            return True
        time.sleep(settings.LLM_POLL_INTERVAL)

    return llm_server_up()


async def ensure_llm_running_and_ready(timeout: int | None = None) -> bool:
    """
    Async-versio: käynnistää LLM:n threadissä ja odottaa, että /api/tags vastaa.
    """
    if timeout is None:
        timeout = settings.LLM_BOOT_TIMEOUT

    loop = asyncio.get_running_loop()
    start = loop.time()

    ok = await loop.run_in_executor(None, ensure_llm_running)
    if not ok:
        return False

    while loop.time() - start < timeout:
        up = await loop.run_in_executor(None, llm_server_up)
        if up:
            touch_activity()
            return True
        await asyncio.sleep(3)

    return False


async def idle_shutdown_loop():
    """
    Taustasäie, joka tarkkailee LLM:n käyttöä ja sammuttaa sen
    kun sitä ei ole käytetty pitkään aikaan.
    """
    global _last_activity
    while True:
        await asyncio.sleep(60)
        if not llm_server_up():
            continue

        idle = (datetime.datetime.utcnow() - _last_activity).total_seconds()
        if idle > settings.LLM_IDLE_SECONDS:
            lo100_power("soft")
            # halutessa voisi lisätä vielä varmistus-offin


async def cpu_activity_poller():
    """
    Pollaa llm-serverin CPU-kuormaa säännöllisesti ja
    kutsuu touch_activity(), jos kuorma on selvästi ei-idle.
    Näin idle_shutdown_loop ei laukea, kun LLM:ää käytetään
    suoraan esimerkiksi VS Codesta.
    """
    while True:
        try:
            total = await asyncio.to_thread(get_llm_server_cpu_total)
            if total is not None and total >= settings.CPU_BUSY_THRESHOLD_FOR_IDLE:
                touch_activity()
        except Exception:
            pass

        await asyncio.sleep(settings.CPU_POLL_INTERVAL_SECONDS)
