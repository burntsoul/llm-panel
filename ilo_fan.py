from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
import shlex
from typing import Any, Dict, Optional

from config import settings


logger = logging.getLogger(__name__)

_last_result: Optional[Dict[str, Any]] = None


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _sanitize_text(text: Optional[str], password: str, max_len: int = 300) -> Optional[str]:
    if text is None:
        return None
    value = str(text)
    if password:
        value = value.replace(password, "***")
    value = value.strip()
    if len(value) > max_len:
        return value[:max_len] + "...(truncated)"
    return value


def _classify_ssh_error(stderr: str, stdout: str) -> str:
    merged = f"{stderr}\n{stdout}".lower()
    if (
        "host key verification failed" in merged
        or "authenticity of host" in merged
        or "are you sure you want to continue connecting" in merged
    ):
        return "host_key_verification"
    if "permission denied" in merged or "authentication failed" in merged:
        return "auth_failure"
    if "connection refused" in merged:
        return "timeout_or_refused"
    if "timed out" in merged or "operation timed out" in merged:
        return "timeout_or_refused"
    return "ssh_command_failed"


def _build_result(
    ok: bool,
    xx: Optional[int],
    command: str,
    error: Optional[str],
    *,
    error_type: Optional[str] = None,
    exit_code: Optional[int] = None,
    stderr: Optional[str] = None,
    stdout: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": ok,
        "xx": xx,
        "command": command,
        "error": error,
        "error_type": error_type,
        "exit_code": exit_code,
        "stderr": stderr,
        "stdout": stdout,
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
        return _build_result(
            False,
            xx_int,
            "",
            "ILO_HOST/ILO_USER/ILO_PASSWORD not configured",
            error_type="config_missing",
        )

    if not shutil.which("sshpass"):
        return _build_result(
            False,
            xx_int,
            "",
            "sshpass not found in PATH",
            error_type="sshpass_missing",
        )

    remote_cmd = f"fan p {idx} min {xx_int}"
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
    ]
    if not settings.ILO_SSH_STRICT_HOSTKEY:
        cmd.extend(
            [
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
            ]
        )
    cmd.extend(
        [
            f"{user}@{host}",
            remote_cmd,
        ]
    )
    masked_cmd = " ".join(shlex.quote(part) for part in cmd)

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
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("iLO fan command timed out")
        return _build_result(
            False,
            xx_int,
            masked_cmd,
            "SSH command timed out",
            error_type="timeout_or_refused",
            exit_code=None,
            stderr=_sanitize_text(getattr(exc, "stderr", None), password),
            stdout=_sanitize_text(getattr(exc, "stdout", None), password),
        )
    except Exception as exc:
        logger.warning("iLO fan command execution failed: %s", exc)
        return _build_result(
            False,
            xx_int,
            masked_cmd,
            str(exc),
            error_type="execution_failed",
        )

    if result.returncode == 0:
        return _build_result(
            True,
            xx_int,
            masked_cmd,
            None,
            exit_code=0,
            stderr=_sanitize_text(result.stderr, password),
            stdout=_sanitize_text(result.stdout, password),
        )

    safe_stderr = _sanitize_text(result.stderr, password) or ""
    safe_stdout = _sanitize_text(result.stdout, password) or ""
    error_type = _classify_ssh_error(safe_stderr, safe_stdout)
    error = safe_stderr or safe_stdout or "iLO SSH command failed"
    logger.warning("iLO fan command failed: %s", error)
    return _build_result(
        False,
        xx_int,
        masked_cmd,
        error,
        error_type=error_type,
        exit_code=result.returncode,
        stderr=safe_stderr or None,
        stdout=safe_stdout or None,
    )
