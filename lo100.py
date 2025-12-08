# lo100.py
import subprocess
from config import settings


def lo100_power_status() -> str:
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.LO100_IP,
        "-U", settings.LO100_USER,
        "-P", settings.LO100_PASS,
        "chassis", "power", "status",
    ]
    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            timeout=5,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def lo100_power(action: str) -> str:
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.LO100_IP,
        "-U", settings.LO100_USER,
        "-P", settings.LO100_PASS,
        "chassis", "power", action,
    ]
    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            timeout=10,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_lo100_health_and_temp():
    """
    Palauttaa (system_health, cpu_temp) LO100:n sensoreista.
    system_health: 'ok', 'warning', 'critical' tai 'unknown'
    cpu_temp: esim. '30.0 °C' tai None
    """
    cpu_temp = None
    worst_level = 0  # 0=unknown, 1=ok, 2=warning, 3=critical

    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", settings.LO100_IP,
        "-U", settings.LO100_USER,
        "-P", settings.LO100_PASS,
        "sensor",
    ]

    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            timeout=15,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return "unknown", cpu_temp

    for line in out.splitlines():
        if not line.strip():
            continue
        if line.startswith("Get HPM.x Capabilities"):
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            continue

        name = parts[0]
        reading = parts[1]
        units = parts[2]
        status = parts[3].lower()

        name_l = name.lower()
        reading_l = reading.lower()
        units_l = units.lower()

        # CPU-lämpötila
        if "cpu0 dmn0 temp" in name_l and reading_l not in ("na", "unavailable"):
            if "degrees" in units_l and reading.replace(".", "", 1).isdigit():
                try:
                    temp_val = float(reading)
                    cpu_temp = f"{temp_val:.1f} °C"
                except ValueError:
                    cpu_temp = f"{reading} {units}"
            else:
                cpu_temp = f"{reading} {units}"

        # health-luokittelu
        if reading_l in ("na", "unavailable"):
            continue
        if status in ("na", "ns", "n/a", "unavailable"):
            continue
        if status.startswith("0x"):
            continue

        level = 0
        if any(word in status for word in ("critical", "non-recoverable", "unrecoverable", "fail", "fault")):
            level = 3
        elif any(word in status for word in ("warning", "non-critical")):
            level = 2
        elif status.startswith("ok") or "normal operating range" in status:
            level = 1

        if level > worst_level:
            worst_level = level

    if worst_level == 0:
        health = "unknown"
    elif worst_level == 1:
        health = "ok"
    elif worst_level == 2:
        health = "warning"
    else:
        health = "critical"

    return health, cpu_temp
