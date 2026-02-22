from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
from typing import Any, Dict, Optional

from config import settings


logger = logging.getLogger(__name__)

_last_result: Optional[Dict[str, Any]] = None


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _build_result(ok: bool, xx: Optional[int], command: str, error: Optional[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": ok,
        "xx": xx,
        "command": command,
        "error": error,
        "timestamp": _utc_now_iso(),
    }
    global _last_result
    _last_result = result
    return result


def get_last_fan_command_result() -> Optional[Dict[str, Any]]:
    return _last_result


def set_ilo_fan_min(xx: int, patch_index: Optional[int] = None) -> Dict[str, Any]:
    try:
        xx_int = int(xx)
    except Exception:
        return _build_result(False, None, "", "xx must be an integer")

    if xx_int < 0 or xx_int > 255:
        return _build_result(False, xx_int, "", "xx must be between 0 and 255")

    host = (settings.ILO_HOST or "").strip()
    user = (settings.ILO_USER or "").strip()
    password = settings.ILO_PASSWORD or ""
    port = int(settings.ILO_SSH_PORT)
    timeout = float(settings.ILO_SSH_TIMEOUT_SECONDS)
    idx = int(settings.ILO_FAN_PATCH_INDEX if patch_index is None else patch_index)

    if not host or not user or not password:
        return _build_result(False, xx_int, "", "ILO_HOST/ILO_USER/ILO_PASSWORD not configured")

    if not shutil.which("sshpass"):
        return _build_result(False, xx_int, "", "sshpass not found in PATH")

    remote_cmd = f"fan p {idx} min {xx_int}"
    masked_cmd = (
        f"sshpass -e ssh -p {port} "
        "-o KexAlgorithms=+diffie-hellman-group14-sha1 "
        "-o HostKeyAlgorithms=+ssh-rsa "
        "-o PubkeyAcceptedAlgorithms=+ssh-rsa "
        "-o PreferredAuthentications=password "
        "-o PubkeyAuthentication=no "
        f"{user}@{host} \"{remote_cmd}\""
    )

    cmd = [
        "sshpass",
        "-e",
        "ssh",
        "-p",
        str(port),
        "-o",
        "KexAlgorithms=+diffie-hellman-group14-sha1",
        "-o",
        "HostKeyAlgorithms=+ssh-rsa",
        "-o",
        "PubkeyAcceptedAlgorithms=+ssh-rsa",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        f"{user}@{host}",
        remote_cmd,
    ]
    env = dict(os.environ)
    env["SSHPASS"] = password

    logger.info("Executing iLO fan command: %s", remote_cmd)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except Exception as exc:
        logger.warning("iLO fan command execution failed: %s", exc)
        return _build_result(False, xx_int, masked_cmd, str(exc))

    if result.returncode == 0:
        return _build_result(True, xx_int, masked_cmd, None)

    error = (result.stderr or "").strip() or (result.stdout or "").strip() or "unknown iLO SSH error"
    logger.warning("iLO fan command failed: %s", error)
    return _build_result(False, xx_int, masked_cmd, error)
