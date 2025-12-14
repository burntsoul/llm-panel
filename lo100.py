# lo100.py  (legacy name)
# HUOM: Uudessa infrassa LLM-agent EI enää käynnistä/sammuta koko palvelinta IPMI:llä.
# Tätä moduulia käytetään vain iLO/IPMI *status*-tiedon hakemiseen (health/temps).
# IPMI power-komennot ovat oletuksena DISABLOITU.

from __future__ import annotations

import os
import subprocess
from typing import Tuple, Optional

from config import settings


def _ipmi_enabled() -> bool:
    return bool(settings.ILO_IP and settings.ILO_USER and settings.ILO_PASS)


def lo100_power_status() -> str:
    """Palauta chassis power status iLO/IPMI:ltä (jos konffattu)."""
    if not _ipmi_enabled():
        return "N/A (iLO/IPMI ei konffattu)"
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.ILO_IP,
        "-U", settings.ILO_USER,
        "-P", settings.ILO_PASS,
        "chassis", "power", "status",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=10, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def lo100_power(action: str) -> str:
    """Legacy: IPMI power control (DISABLED by default)."""
    if os.getenv("ALLOW_IPMI_POWER", "").strip().lower() not in ("1","true","yes","on"):
        return "IPMI power ohjaus on disabloitu (aseta ALLOW_IPMI_POWER=1 jos tiedät mitä teet)."

    if not _ipmi_enabled():
        return "iLO/IPMI ei ole konffattu (ILO_IP/ILO_USER/ILO_PASS)."

    action_map = {
        "on": "on",
        "off": "off",
        "soft": "soft",
        "cycle": "cycle",
        "reset": "reset",
    }
    if action not in action_map:
        return f"Tuntematon action: {action}"

    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.ILO_IP,
        "-U", settings.ILO_USER,
        "-P", settings.ILO_PASS,
        "chassis", "power", action_map[action],
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=15, stderr=subprocess.DEVNULL)
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_lo100_health_and_temp() -> Tuple[str, Optional[float]]:
    """
    Palauttaa (system_health, cpu_temp) iLO/IPMI sensoreista.
    system_health: 'ok', 'warning', 'critical' tai 'unknown'
    cpu_temp: float (C) jos löytyy, muuten None
    """
    if not _ipmi_enabled():
        return "unknown", None

    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.ILO_IP,
        "-U", settings.ILO_USER,
        "-P", settings.ILO_PASS,
        "sdr",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=15, stderr=subprocess.DEVNULL)
    except Exception:
        return "unknown", None

    health = "ok"
    cpu0_temp = None

    # Etsi tyypilliset hälytykset ja CPU0-temp
    for line in out.splitlines():
        lower = line.lower()
        if "critical" in lower:
            health = "critical"
        elif "warning" in lower and health != "critical":
            health = "warning"

        # HPE/ILO sensorien nimet vaihtelevat: etsitään "cpu" + "temp"
        if cpu0_temp is None and "cpu" in lower and "temp" in lower:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2:
                # usein "xx degrees C"
                m = None
                try:
                    import re
                    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*degrees", parts[1], re.IGNORECASE)
                except Exception:
                    m = None
                if m:
                    try:
                        cpu0_temp = float(m.group(1))
                    except Exception:
                        cpu0_temp = None

    return health, cpu0_temp
