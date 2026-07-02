from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field, ValidationError


CONFIG_DIR = Path(__file__).with_name("config")
LOCAL_CONFIG_PATH = CONFIG_DIR / "local.json"


class ConfigStoreError(RuntimeError):
    pass


class ConfigValidationError(ConfigStoreError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("config validation failed")
        self.errors = errors


class GpuTelemetryConfig(BaseModel):
    skip_when_vm_off: bool = True
    log_throttle_seconds: float = Field(default=60.0, ge=0.0)
    glances_timeout_seconds: float = Field(default=2.5, gt=0.0)
    glances_gpu_id: str = "nvidia0"


class GpuFanControlConfig(BaseModel):
    ilo_host: str = ""
    ilo_user: str = ""
    ilo_ssh_port: int = Field(default=22, ge=1, le=65535)
    ilo_fan_patch_index: int = Field(default=3, ge=0)
    ilo_ssh_timeout_seconds: float = Field(default=5.0, gt=0.0)
    ilo_ssh_strict_hostkey: bool = True
    ilo_sshpass_path: str = "sshpass"


class GpuConfig(BaseModel):
    telemetry: GpuTelemetryConfig = Field(default_factory=GpuTelemetryConfig)
    fan_control: GpuFanControlConfig = Field(default_factory=GpuFanControlConfig)


class LocalConfig(BaseModel):
    gpu: GpuConfig = Field(default_factory=GpuConfig)


@dataclass(frozen=True)
class SettingValue:
    key: str
    value: Any
    source: str


def _legacy_has(secrets_module: Any, name: str) -> bool:
    return secrets_module is not None and hasattr(secrets_module, name)


def _legacy_value(secrets_module: Any, name: str) -> Any:
    return getattr(secrets_module, name)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        raise ValueError("expected boolean")
    lowered = str(value).strip().lower()
    if lowered in ("1", "true", "yes", "y", "on"):
        return True
    if lowered in ("0", "false", "no", "n", "off"):
        return False
    raise ValueError("expected boolean")


def _coerce_float(value: Any) -> float:
    return float(value)


def _coerce_int(value: Any) -> int:
    return int(value)


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


GPU_TELEMETRY_FIELDS = {
    "skip_when_vm_off": {
        "legacy_name": "GPU_TELEMETRY_SKIP_WHEN_VM_OFF",
        "env_name": "GPU_TELEMETRY_SKIP_WHEN_VM_OFF",
        "coerce": _coerce_bool,
        "label": "Skip when VM off",
        "type": "boolean",
    },
    "log_throttle_seconds": {
        "legacy_name": "GPU_TELEMETRY_LOG_THROTTLE_SECONDS",
        "env_name": "GPU_TELEMETRY_LOG_THROTTLE_SECONDS",
        "coerce": _coerce_float,
        "label": "Log throttle",
        "type": "number",
        "unit": "s",
    },
    "glances_timeout_seconds": {
        "legacy_name": "GLANCES_TIMEOUT_SECONDS",
        "env_name": "GLANCES_TIMEOUT_SECONDS",
        "coerce": _coerce_float,
        "label": "Glances timeout",
        "type": "number",
        "unit": "s",
    },
    "glances_gpu_id": {
        "legacy_name": "GLANCES_GPU_ID",
        "env_name": "GLANCES_GPU_ID",
        "coerce": _coerce_str,
        "label": "Glances GPU ID",
        "type": "text",
    },
}


GPU_FAN_CONTROL_FIELDS = {
    "ilo_host": {
        "legacy_names": ("ILO_HOST", "ILO_IP"),
        "env_names": ("ILO_HOST", "ILO_IP"),
        "coerce": _coerce_str,
        "label": "iLO host",
        "type": "text",
    },
    "ilo_user": {
        "legacy_names": ("ILO_USER",),
        "env_names": ("ILO_USER",),
        "coerce": _coerce_str,
        "label": "iLO user",
        "type": "text",
    },
    "ilo_ssh_port": {
        "legacy_names": ("ILO_SSH_PORT",),
        "env_names": ("ILO_SSH_PORT",),
        "coerce": _coerce_int,
        "label": "SSH port",
        "type": "number",
    },
    "ilo_fan_patch_index": {
        "legacy_names": ("ILO_FAN_PATCH_INDEX",),
        "env_names": ("ILO_FAN_PATCH_INDEX",),
        "coerce": _coerce_int,
        "label": "Fan patch index",
        "type": "number",
    },
    "ilo_ssh_timeout_seconds": {
        "legacy_names": ("ILO_SSH_TIMEOUT_SECONDS",),
        "env_names": ("ILO_SSH_TIMEOUT_SECONDS",),
        "coerce": _coerce_float,
        "label": "SSH timeout",
        "type": "number",
        "unit": "s",
    },
    "ilo_ssh_strict_hostkey": {
        "legacy_names": ("ILO_SSH_STRICT_HOSTKEY",),
        "env_names": ("ILO_SSH_STRICT_HOSTKEY",),
        "coerce": _coerce_bool,
        "label": "Strict host key",
        "type": "boolean",
    },
    "ilo_sshpass_path": {
        "legacy_names": ("ILO_SSHPASS_PATH",),
        "env_names": ("ILO_SSHPASS_PATH",),
        "coerce": _coerce_str,
        "label": "sshpass path",
        "type": "text",
    },
}


def load_local_config(path: Path = LOCAL_CONFIG_PATH) -> LocalConfig:
    raw = _read_local_raw(path)

    try:
        return LocalConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigValidationError(exc.errors()) from exc


def _read_local_raw(path: Path = LOCAL_CONFIG_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigStoreError(f"invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigStoreError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigStoreError(f"{path} must contain a JSON object")
    return raw


def _dump_local_raw(data: Mapping[str, Any], path: Path = LOCAL_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"

    fd, tmp_name = tempfile.mkstemp(prefix=".local.", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def update_gpu_telemetry_config(
    updates: Mapping[str, Any],
    path: Path = LOCAL_CONFIG_PATH,
) -> GpuTelemetryConfig:
    allowed = set(GPU_TELEMETRY_FIELDS)
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ConfigValidationError(
            [{"loc": ("gpu", "telemetry", key), "msg": "unknown setting", "type": "value_error.unknown"} for key in unknown]
        )

    raw = _read_local_raw(path)
    current = LocalConfig.model_validate(raw)
    merged = current.gpu.telemetry.model_dump()
    merged.update(dict(updates))

    try:
        telemetry = GpuTelemetryConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigValidationError(exc.errors()) from exc

    raw.setdefault("gpu", {})
    if not isinstance(raw["gpu"], dict):
        raw["gpu"] = {}
    raw["gpu"]["telemetry"] = telemetry.model_dump(mode="json")
    _dump_local_raw(raw, path)
    return telemetry


def update_gpu_fan_control_config(
    updates: Mapping[str, Any],
    path: Path = LOCAL_CONFIG_PATH,
) -> GpuFanControlConfig:
    allowed = set(GPU_FAN_CONTROL_FIELDS)
    unknown = sorted(set(updates) - allowed)
    if unknown:
        raise ConfigValidationError(
            [{"loc": ("gpu", "fan_control", key), "msg": "unknown setting", "type": "value_error.unknown"} for key in unknown]
        )

    raw = _read_local_raw(path)
    current = LocalConfig.model_validate(raw)
    merged = current.gpu.fan_control.model_dump()
    merged.update(dict(updates))

    try:
        fan_control = GpuFanControlConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigValidationError(exc.errors()) from exc

    raw.setdefault("gpu", {})
    if not isinstance(raw["gpu"], dict):
        raw["gpu"] = {}
    raw["gpu"]["fan_control"] = fan_control.model_dump(mode="json")
    _dump_local_raw(raw, path)
    return fan_control


def _resolve_gpu_telemetry_field(
    field_name: str,
    local: GpuTelemetryConfig,
    local_has_field: bool,
    secrets_module: Any,
    environ: Mapping[str, str],
) -> SettingValue:
    meta = GPU_TELEMETRY_FIELDS[field_name]
    legacy_name = meta["legacy_name"]
    env_name = meta["env_name"]
    coerce = meta["coerce"]

    value = getattr(GpuTelemetryConfig(), field_name)
    source = "default"

    if local_has_field:
        value = getattr(local, field_name)
        source = "config/local.json"

    if env_name in environ:
        value = coerce(environ[env_name])
        source = "env"

    if _legacy_has(secrets_module, legacy_name):
        legacy_raw = _legacy_value(secrets_module, legacy_name)
        value = coerce(legacy_raw)
        source = "llm_secrets.py"

    return SettingValue(key=legacy_name, value=value, source=source)


def _first_existing_legacy(secrets_module: Any, names: tuple[str, ...]) -> tuple[bool, str, Any]:
    for name in names:
        if _legacy_has(secrets_module, name):
            return True, name, _legacy_value(secrets_module, name)
    return False, "", None


def _first_existing_env(environ: Mapping[str, str], names: tuple[str, ...]) -> tuple[bool, str, str]:
    for name in names:
        if name in environ:
            return True, name, environ[name]
    return False, "", ""


def _resolve_gpu_fan_control_field(
    field_name: str,
    local: GpuFanControlConfig,
    local_has_field: bool,
    secrets_module: Any,
    environ: Mapping[str, str],
) -> SettingValue:
    meta = GPU_FAN_CONTROL_FIELDS[field_name]
    legacy_names = meta["legacy_names"]
    env_names = meta["env_names"]
    coerce = meta["coerce"]

    value = getattr(GpuFanControlConfig(), field_name)
    source = "default"

    if local_has_field:
        value = getattr(local, field_name)
        source = "config/local.json"

    has_env, env_name, env_raw = _first_existing_env(environ, env_names)
    if has_env:
        value = coerce(env_raw)
        source = "env"

    has_legacy, legacy_name, legacy_raw = _first_existing_legacy(secrets_module, legacy_names)
    if has_legacy:
        value = coerce(legacy_raw)
        source = "llm_secrets.py"

    return SettingValue(key=legacy_name or env_name or legacy_names[0], value=value, source=source)


def effective_gpu_telemetry_values(
    secrets_module: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    path: Path = LOCAL_CONFIG_PATH,
    ignore_local_errors: bool = False,
) -> dict[str, SettingValue]:
    env = environ if environ is not None else os.environ
    try:
        raw = _read_local_raw(path)
    except ConfigStoreError:
        if not ignore_local_errors:
            raise
        raw = {}
    local = LocalConfig.model_validate(raw).gpu.telemetry
    raw_gpu = raw.get("gpu", {})
    raw_telemetry = raw_gpu.get("telemetry", {}) if isinstance(raw_gpu, dict) else {}
    if not isinstance(raw_telemetry, dict):
        raw_telemetry = {}
    return {
        field_name: _resolve_gpu_telemetry_field(
            field_name,
            local,
            field_name in raw_telemetry,
            secrets_module,
            env,
        )
        for field_name in GPU_TELEMETRY_FIELDS
    }


def effective_gpu_fan_control_values(
    secrets_module: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    path: Path = LOCAL_CONFIG_PATH,
    ignore_local_errors: bool = False,
) -> dict[str, SettingValue]:
    env = environ if environ is not None else os.environ
    try:
        raw = _read_local_raw(path)
    except ConfigStoreError:
        if not ignore_local_errors:
            raise
        raw = {}
    local = LocalConfig.model_validate(raw).gpu.fan_control
    raw_gpu = raw.get("gpu", {})
    raw_fan_control = raw_gpu.get("fan_control", {}) if isinstance(raw_gpu, dict) else {}
    if not isinstance(raw_fan_control, dict):
        raw_fan_control = {}
    return {
        field_name: _resolve_gpu_fan_control_field(
            field_name,
            local,
            field_name in raw_fan_control,
            secrets_module,
            env,
        )
        for field_name in GPU_FAN_CONTROL_FIELDS
    }


def gpu_telemetry_fields_for_api(
    secrets_module: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    path: Path = LOCAL_CONFIG_PATH,
) -> list[dict[str, Any]]:
    values = effective_gpu_telemetry_values(secrets_module=secrets_module, environ=environ, path=path)
    fields: list[dict[str, Any]] = []
    for field_name, meta in GPU_TELEMETRY_FIELDS.items():
        resolved = values[field_name]
        field = {
            "key": resolved.key,
            "config_key": field_name,
            "label": meta["label"],
            "value": resolved.value,
            "type": meta["type"],
            "source": resolved.source,
            "editable": resolved.source in ("default", "config/local.json"),
        }
        if "unit" in meta:
            field["unit"] = meta["unit"]
        fields.append(field)
    return fields


def gpu_fan_control_fields_for_api(
    secrets_module: Any = None,
    environ: Optional[Mapping[str, str]] = None,
    path: Path = LOCAL_CONFIG_PATH,
) -> list[dict[str, Any]]:
    values = effective_gpu_fan_control_values(secrets_module=secrets_module, environ=environ, path=path)
    fields: list[dict[str, Any]] = []
    for field_name, meta in GPU_FAN_CONTROL_FIELDS.items():
        resolved = values[field_name]
        field = {
            "key": resolved.key,
            "config_key": field_name,
            "label": meta["label"],
            "value": resolved.value,
            "type": meta["type"],
            "source": resolved.source,
            "editable": resolved.source in ("default", "config/local.json"),
        }
        if "unit" in meta:
            field["unit"] = meta["unit"]
        fields.append(field)

    fields.append(
        {
            "key": "ILO_PASSWORD",
            "label": "iLO password",
            "value": "configured"
            if _legacy_has(secrets_module, "ILO_PASSWORD")
            or _legacy_has(secrets_module, "ILO_PASS")
            or "ILO_PASSWORD" in (environ if environ is not None else os.environ)
            or "ILO_PASS" in (environ if environ is not None else os.environ)
            else "missing",
            "type": "secret-status",
            "source": "llm_secrets.py/env",
            "editable": False,
        }
    )
    return fields
