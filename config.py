# config.py
from __future__ import annotations

import os
from pathlib import Path

try:
    import llm_secrets as secrets  # type: ignore
except Exception:
    secrets = None  # type: ignore


def _secret(name: str, default: str = "") -> str:
    """Read a secret value from llm_secrets.py first, then env vars, then default."""
    if secrets is not None and hasattr(secrets, name):
        v = getattr(secrets, name)
        if v is None:
            return default
        return str(v)
    return os.getenv(name, default)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _conf(name: str, default: str = "") -> str:
    if secrets is not None and hasattr(secrets, name):
        v = getattr(secrets, name)
        if v is None:
            return default
        return str(v)
    return os.getenv(name, default)


def _conf_int(name: str, default: int) -> int:
    raw = _conf(name, str(default))
    try:
        return int(raw)
    except Exception:
        return default


def _conf_float(name: str, default: float) -> float:
    raw = _conf(name, str(default))
    try:
        return float(raw)
    except Exception:
        return default


def _conf_bool(name: str, default: bool = False) -> bool:
    if secrets is not None and hasattr(secrets, name):
        v = getattr(secrets, name)
        if isinstance(v, bool):
            return v
        if v is None:
            return default
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


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
    - Salaisuudet: llm_secrets.py (ensin), muuten env
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
        self.LLM_AGENT_IP = _env("LLM_AGENT_IP", "192.168.8.36")
        self.LLM_HOST = _env("LLM_HOST", "192.168.8.33")        # llm-server (Ollama)
        self.WINDOWS_VM_IP = _env("WINDOWS_VM_IP", "192.168.8.34")

        # Varmistus: älä anna LLM-VM:n käynnistyä, jos Windows-VM on päällä (GPU)
        self.ENFORCE_EXCLUSIVE_VMS = _env_bool("ENFORCE_EXCLUSIVE_VMS", True)

        # -------- iLO / IPMI (vain status, ei pakko) --------
        # (täytä llm_secrets.py:hin jos haluat sensorit mukaan)
        self.ILO_HOST = _secret("ILO_HOST", _secret("ILO_IP", _env("ILO_HOST", _env("ILO_IP", ""))))
        self.ILO_USER = _secret("ILO_USER", _env("ILO_USER", ""))
        self.ILO_PASSWORD = _secret(
            "ILO_PASSWORD",
            _secret("ILO_PASS", _env("ILO_PASSWORD", _env("ILO_PASS", ""))),
        )
        self.ILO_SSH_PORT = _env_int("ILO_SSH_PORT", 22)
        self.ILO_FAN_PATCH_INDEX = _env_int("ILO_FAN_PATCH_INDEX", 3)
        self.ILO_SSH_TIMEOUT_SECONDS = _env_float("ILO_SSH_TIMEOUT_SECONDS", 5.0)
        self.ILO_SSH_STRICT_HOSTKEY = _env_bool("ILO_SSH_STRICT_HOSTKEY", True)

        # Backward-compatible aliases for existing IPMI health code
        self.ILO_IP = self.ILO_HOST
        self.ILO_PASS = self.ILO_PASSWORD

        # -------- LLM-palvelin (Ollama) --------
        self.LLM_PORT = _env_int("LLM_PORT", 11434)

        # Glances-API llm-serverillä
        self.GLANCES_API_BASE = (
            _env("GLANCES_API_BASE", "")
            or f"http://{self.LLM_HOST}:61208/api/3"
        )
        self.GPU_TELEMETRY_PROVIDER = _env("GPU_TELEMETRY_PROVIDER", "remote_glances")
        self.GLANCES_API_BASE_V4 = _env(
            "GLANCES_API_BASE_V4",
            f"http://{self.LLM_HOST}:61208/api/4",
        )
        self.GLANCES_GPU_ID = _env("GLANCES_GPU_ID", "nvidia0")
        self.GLANCES_TIMEOUT_SECONDS = _env_float("GLANCES_TIMEOUT_SECONDS", 2.5)
        self.WATCHDOG_ENABLED = _env_bool("WATCHDOG_ENABLED", False)
        self.WATCHDOG_POLL_SECONDS = _env_float("WATCHDOG_POLL_SECONDS", 5.0)
        self.WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS = _env_float(
            "WATCHDOG_MIN_CHANGE_INTERVAL_SECONDS",
            20.0,
        )
        self.WATCHDOG_FAILSAFE_FAN_MIN_XX = _env_int("WATCHDOG_FAILSAFE_FAN_MIN_XX", 190)
        self.WATCHDOG_HYSTERESIS_C = _env_float("WATCHDOG_HYSTERESIS_C", 4.0)
        self.WATCHDOG_TELEMETRY_STALE_SECONDS = _env_float(
            "WATCHDOG_TELEMETRY_STALE_SECONDS",
            15.0,
        )
        self.WATCHDOG_LOG_TRANSITIONS_ONLY = _env_bool("WATCHDOG_LOG_TRANSITIONS_ONLY", True)

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

        # -------- ComfyUI (image generation) --------
        self.COMFYUI_BASE_URL = _conf(
            "COMFYUI_BASE_URL",
            f"http://{self.LLM_HOST}:8188",
        )
        self.COMFYUI_READY_PATH = _conf("COMFYUI_READY_PATH", "/system_stats")
        self.COMFYUI_READY_TIMEOUT = _conf_int("COMFYUI_READY_TIMEOUT", 120)
        self.COMFYUI_POLL_INTERVAL = _conf_float("COMFYUI_POLL_INTERVAL", 2.0)
        self.COMFYUI_HTTP_TIMEOUT = _conf_float("COMFYUI_HTTP_TIMEOUT", 300.0)
        self.COMFYUI_GENERATION_TIMEOUT = _conf_int("COMFYUI_GENERATION_TIMEOUT", 300)
        self.COMFYUI_IDLE_SECONDS = _conf_int("COMFYUI_IDLE_SECONDS", 600)
        self.COMFYUI_MAX_BATCH_SIZE = _conf_int("COMFYUI_MAX_BATCH_SIZE", 4)

        self.COMFYUI_DEFAULT_CHECKPOINT = _conf("COMFYUI_DEFAULT_CHECKPOINT", "")
        self.COMFYUI_DEFAULT_STEPS = _conf_int("COMFYUI_DEFAULT_STEPS", 20)
        self.COMFYUI_DEFAULT_CFG_SCALE = _conf_float("COMFYUI_DEFAULT_CFG_SCALE", 7.0)
        self.COMFYUI_DEFAULT_SAMPLER = _conf("COMFYUI_DEFAULT_SAMPLER", "euler")
        self.COMFYUI_DEFAULT_SCHEDULER = _conf("COMFYUI_DEFAULT_SCHEDULER", "normal")

        self.COMFYUI_WORKFLOW_PATH = _conf(
            "COMFYUI_WORKFLOW_PATH",
            str(Path(__file__).with_name("assets") / "comfyui_txt2img.json"),
        )
        self.COMFYUI_EDIT_WORKFLOW_PATH = _conf(
            "COMFYUI_EDIT_WORKFLOW_PATH",
            str(Path(__file__).with_name("assets") / "comfyui_img2img.json"),
        )
        self.COMFYUI_INPAINT_WORKFLOW_PATH = _conf(
            "COMFYUI_INPAINT_WORKFLOW_PATH",
            str(Path(__file__).with_name("assets") / "comfyui_inpaint.json"),
        )
        self.COMFYUI_NODE_CHECKPOINT = _conf("COMFYUI_NODE_CHECKPOINT", "1")
        self.COMFYUI_NODE_POSITIVE = _conf("COMFYUI_NODE_POSITIVE", "2")
        self.COMFYUI_NODE_NEGATIVE = _conf("COMFYUI_NODE_NEGATIVE", "3")
        self.COMFYUI_NODE_LATENT = _conf("COMFYUI_NODE_LATENT", "4")
        self.COMFYUI_NODE_SAMPLER = _conf("COMFYUI_NODE_SAMPLER", "5")
        self.COMFYUI_NODE_IMG2IMG_IMAGE = _conf("COMFYUI_NODE_IMG2IMG_IMAGE", "4")
        self.COMFYUI_NODE_IMG2IMG_VAE_ENCODE = _conf("COMFYUI_NODE_IMG2IMG_VAE_ENCODE", "5")
        self.COMFYUI_NODE_IMG2IMG_SAMPLER = _conf("COMFYUI_NODE_IMG2IMG_SAMPLER", "6")

        self.COMFYUI_EDIT_DENOISE = _conf_float("COMFYUI_EDIT_DENOISE", 0.35)
        self.COMFYUI_NODE_INPAINT_IMAGE = _conf("COMFYUI_NODE_INPAINT_IMAGE", "4")
        self.COMFYUI_NODE_INPAINT_MASK = _conf("COMFYUI_NODE_INPAINT_MASK", "5")
        self.COMFYUI_NODE_INPAINT_VAE_ENCODE = _conf("COMFYUI_NODE_INPAINT_VAE_ENCODE", "8")
        self.COMFYUI_NODE_INPAINT_SAMPLER = _conf("COMFYUI_NODE_INPAINT_SAMPLER", "9")
        self.COMFYUI_NODE_INPAINT_VAE_LOADER = _conf("COMFYUI_NODE_INPAINT_VAE_LOADER", "13")
        self.COMFYUI_INPAINT_VAE_NAME = _conf("COMFYUI_INPAINT_VAE_NAME", "sdxl_vae.safetensors")
        self.COMFYUI_NODE_INPAINT_REFINER_CHECKPOINT = _conf("COMFYUI_NODE_INPAINT_REFINER_CHECKPOINT", "10")
        self.COMFYUI_NODE_INPAINT_REFINER_SAMPLER = _conf("COMFYUI_NODE_INPAINT_REFINER_SAMPLER", "11")
        self.COMFYUI_NODE_INPAINT_REFINER_POSITIVE = _conf("COMFYUI_NODE_INPAINT_REFINER_POSITIVE", "15")
        self.COMFYUI_NODE_INPAINT_REFINER_NEGATIVE = _conf("COMFYUI_NODE_INPAINT_REFINER_NEGATIVE", "16")
        self.COMFYUI_INPAINT_REFINER_NAME = _conf("COMFYUI_INPAINT_REFINER_NAME", "sd_xl_refiner_1.0.safetensors")
        self.COMFYUI_INPAINT_REFINER_DENOISE = _conf_float("COMFYUI_INPAINT_REFINER_DENOISE", 0.2)
        self.COMFYUI_INPAINT_REFINER_STEPS = _conf_int("COMFYUI_INPAINT_REFINER_STEPS", 15)

        self.COMFYUI_SSH_ENABLED = _conf_bool("COMFYUI_SSH_ENABLED", False)
        self.COMFYUI_SSH_HOST = _conf("COMFYUI_SSH_HOST", self.LLM_HOST)
        self.COMFYUI_SSH_USER = _conf("COMFYUI_SSH_USER", "")
        self.COMFYUI_SSH_PORT = _conf_int("COMFYUI_SSH_PORT", 22)
        self.COMFYUI_SSH_KEY = _conf("COMFYUI_SSH_KEY", "")
        self.COMFYUI_SSH_STRICT_HOST_KEY = _conf_bool(
            "COMFYUI_SSH_STRICT_HOST_KEY",
            True,
        )
        self.COMFYUI_SSH_TIMEOUT = _conf_int("COMFYUI_SSH_TIMEOUT", 20)
        self.COMFYUI_SERVICE_NAME = _conf("COMFYUI_SERVICE_NAME", "comfyui.service")
        self.COMFYUI_SSH_USE_SUDO = _conf_bool("COMFYUI_SSH_USE_SUDO", False)
        self.COMFYUI_SYSTEMCTL_PATH = _conf("COMFYUI_SYSTEMCTL_PATH", "/usr/bin/systemctl")

        # -------- Lease & Proxy API --------
        # Shared secret token for lease/proxy endpoints (required for auth)
        self.LLM_AGENT_TOKEN = _secret("LLM_AGENT_TOKEN", "")

        # -------- Logging --------
        self.LOG_LEVEL = _conf("LOG_LEVEL", "INFO")
        self.LOG_FILE = _conf(
            "LOG_FILE",
            str(Path(__file__).with_name("logs") / "llm-agent.log"),
        )

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
