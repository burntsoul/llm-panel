# config.py
from __future__ import annotations

import os
from pathlib import Path

try:
    import llm_secrets as secrets  # type: ignore
except Exception:
    secrets = None  # type: ignore


def _secret(name: str, default: str = "") -> str:
    """Read a secret value from secrets.py first, then env vars, then default."""
    if secrets is not None and hasattr(secrets, name):
        v = getattr(secrets, name)
        if v is None:
            return default
        return str(v)
    return os.getenv(name, default)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


class Settings:
    """
    Yksi paikka kaikille asetuksille.

    - Ei-salaiset arvot: config.py (oletukset + env override)
    - Salaisuudet: secrets.py (ensin), muuten env
    """

    def __init__(self) -> None:
        # -------- Infra (uusi verkko / VM-jako) --------
        self.PROXMOX_HOST = _env("PROXMOX_HOST", "192.168.8.31")
        self.PROXMOX_PORT = _env_int("PROXMOX_PORT", 8006)
        self.PROXMOX_NODE = _env("PROXMOX_NODE", "proxmox")
        self.PROXMOX_VERIFY_SSL = _env_bool("PROXMOX_VERIFY_SSL", False)

        # Proxmox API token (salaisuudet)
        self.PROXMOX_TOKEN_ID = _secret("PROXMOX_TOKEN_ID", "")
        self.PROXMOX_TOKEN_SECRET = _secret("PROXMOX_TOKEN_SECRET", "")

        # VMID:t (screenshotin mukaan)
        self.HA_VM_ID = _env_int("HA_VM_ID", 100)
        self.LLM_VM_ID = _env_int("LLM_VM_ID", 101)          # llm-server VM
        self.AGENT_VM_ID = _env_int("AGENT_VM_ID", 102)      # llm-agent VM
        self.WINDOWS_VM_ID = _env_int("WINDOWS_VM_ID", 103)  # windows11 VM

        # IP:t (informatiivisia; VM ohjaus tapahtuu Proxmox API:lla)
        self.PROXMOX_IP = _env("PROXMOX_IP", "192.168.8.31")
        self.LLM_AGENT_IP = _env("LLM_AGENT_IP", "192.168.8.32")
        self.LLM_HOST = _env("LLM_HOST", "192.168.8.33")        # llm-server (Ollama)
        self.WINDOWS_VM_IP = _env("WINDOWS_VM_IP", "192.168.8.34")

        # Varmistus: älä anna LLM-VM:n käynnistyä, jos Windows-VM on päällä (GPU)
        self.ENFORCE_EXCLUSIVE_VMS = _env_bool("ENFORCE_EXCLUSIVE_VMS", True)

        # -------- iLO / IPMI (vain status, ei pakko) --------
        # (täytä secrets.py:hin jos haluat sensorit mukaan)
        self.ILO_IP = _secret("ILO_IP", _env("ILO_IP", ""))
        self.ILO_USER = _secret("ILO_USER", _env("ILO_USER", ""))
        self.ILO_PASS = _secret("ILO_PASS", _env("ILO_PASS", ""))

        # -------- LLM-palvelin (Ollama) --------
        self.LLM_PORT = _env_int("LLM_PORT", 11434)

        # Glances-API llm-serverillä
        self.GLANCES_API_BASE = (
            _env("GLANCES_API_BASE", "")
            or f"http://{self.LLM_HOST}:61208/api/3"
        )

        # Idle-logiikka
        self.CPU_BUSY_THRESHOLD_FOR_IDLE = _env_float("CPU_BUSY_THRESHOLD_FOR_IDLE", 20.0)  # %
        self.CPU_POLL_INTERVAL_SECONDS = _env_float("CPU_POLL_INTERVAL_SECONDS", 10.0)      # s

        # Boot & idle timeoutit
        self.LLM_BOOT_TIMEOUT = _env_int("LLM_BOOT_TIMEOUT", 180)
        self.LLM_POLL_INTERVAL = _env_float("LLM_POLL_INTERVAL", 5.0)
        self.LLM_IDLE_SECONDS = _env_int("LLM_IDLE_SECONDS", 3600)

        # Huoltotila (ajastukset pois)
        self.STATE_PATH = _env("STATE_PATH", str(Path(__file__).with_name("state.json")))
        self.MAINTENANCE_DEFAULT = _env_bool("MAINTENANCE_DEFAULT", False)

        # Mitkä mallit näkyvät, vaikka LLM olisi down
        default_models_raw = _env(
            "DEFAULT_MODELS",
            "deepseek-coder:1.3b,deepseek-coder:6.7b",
        )
        self.DEFAULT_MODELS = [m.strip() for m in default_models_raw.split(",") if m.strip()]

        # OpenAI-yhteensopiva base-URL (sama host/port kuin Ollama)
        self.LLM_SERVER_BASE = _env(
            "LLM_SERVER_BASE",
            f"http://{self.LLM_HOST}:{self.LLM_PORT}",
        )

        # -------- Embeddings --------
        # Oletus embedding-malli (jos ei ole määritelty)
        self.DEFAULT_EMBEDDING_MODEL = _env(
            "DEFAULT_EMBEDDING_MODEL",
            "nomic-embed-text:latest",
        )

        # Embedding cache TTL (sekunteina)
        self.EMBEDDING_CACHE_TTL = _env_int("EMBEDDING_CACHE_TTL", 3600)  # 1 hour

        # Max embedding batch size (kuinka monta tekstiä kerralla)
        self.EMBEDDING_MAX_BATCH_SIZE = _env_int("EMBEDDING_MAX_BATCH_SIZE", 32)

        # -------- Lease & Proxy API --------
        # Shared secret token for lease/proxy endpoints (required for auth)
        self.LLM_AGENT_TOKEN = _secret("LLM_AGENT_TOKEN", "")

        # LLM base URL (internal, used by proxy and readiness checks)
        self.LLM_BASE_URL = _env(
            "LLM_BASE_URL",
            f"http://{self.LLM_HOST}:{self.LLM_PORT}",
        )

        # Readiness check endpoint (relative to LLM_BASE_URL)
        # For Ollama, /api/tags or /api/version work well
        self.LLM_READINESS_PATH = _env("LLM_READINESS_PATH", "/api/tags")

        # Default lease TTL (seconds)
        self.LEASE_DEFAULT_TTL = _env_int("LEASE_DEFAULT_TTL", 3600)  # 1 hour

        # Readiness check timeout (seconds)
        self.LLM_READINESS_TIMEOUT = _env_int("LLM_READINESS_TIMEOUT", 120)

        # Readiness check polling interval (seconds)
        self.LLM_READINESS_POLL_INTERVAL = _env_float("LLM_READINESS_POLL_INTERVAL", 2.0)

        # Power modes for idle shutdown:
        # "Off" (immediate), "Medium" (2h), "High" (30min)
        self.POWER_MODE = _env("POWER_MODE", "Medium")
        power_mode_timeouts = {
            "Off": 0,
            "Medium": 7200,  # 2 hours
            "High": 1800,  # 30 minutes
        }
        self.POWER_MODE_IDLE_TIMEOUT = power_mode_timeouts.get(
            self.POWER_MODE, 7200
        )


settings = Settings()
